# topo-debug-python — stdlib `pdb` fallback.
#
# `topo-debug-python` normally drives the target through debugpy's DAP
# server. When `debugpy` is not importable (minimal/locked-down Python
# environments), a graceful degradation is required: `topo debug` emits
# an explicit degradation notice and the pdb subprocess still reads out
# basic variables for CLI text output (the Web Render path is not
# available in degraded mode).
#
# pdb is stdlib (always available). It has no DAP, so this path supports
# only *basic variable reads* for the CLI text output: list[float] →
# f64 row vector, list[int] → i64 row vector — the same basic subset
# the DAP path materialises. Snapshot / replay / Web Render are NOT
# available here (the caller advertises that in its degradation notice).
#
# Mechanism: run `python -m pdb <target>` with a command script on stdin:
#   tbreak <abs_site>:<line>   (one-shot breakpoint)
#   continue
#   !<introspect+pack one-liner that prints a sentinel-delimited record>
#   continue
#   quit
# pdb executes `!` lines as Python in the stopped frame. The injected
# line writes `<<TOPOVAR>>name|dtype|len|hexbytes<<END>>` to stdout; we
# scan it out of pdb's combined output (prompt/source chatter and any
# target prints are ignored by anchoring on the sentinels).

import os
import re
import subprocess

# One self-contained expression per variable. Kept as a single physical
# line because pdb's `!` consumes exactly one line. `__import__` avoids a
# separate import command. Empty list → f64/[] (neutral, matches the DAP
# path). Unsupported element types emit dtype `ERR` so the caller can map
# it to the same exit-4 contract as the DAP path.
_PROBE = (
    "!__v={var}; __t=type(__v).__name__; "
    "__e=(0 if (__t!='list' or len(__v)==0) else "
    "(1 if isinstance(__v[0],float) else (2 if isinstance(__v[0],int) "
    "and not isinstance(__v[0],bool) else 3))); "
    "__d=('f64' if __t=='list' and __e in (0,1) else "
    "'i64' if __t=='list' and __e==2 else 'ERR'); "
    "__h=(__import__('struct').pack('<%dd'%len(__v),*__v).hex() "
    "if __d=='f64' and len(__v)>0 else "
    "__import__('struct').pack('<%dq'%len(__v),*__v).hex() "
    "if __d=='i64' else ''); "
    "__import__('sys').stdout.write("
    "'<<TOPOVAR>>{var}|'+__d+'|'+str(len(__v) if __t=='list' else -1)"
    "+'|'+__h+'<<END>>\\n'); "
    "__import__('sys').stdout.flush()"
)

_REC = re.compile(r"<<TOPOVAR>>(.*?)<<END>>", re.S)


def debugpy_importable(python_exe: str) -> bool:
    """True iff `import debugpy` succeeds in the interpreter that would
    run the target. We probe `python_exe` (not the current process) so
    the decision matches the environment the target actually runs in."""
    try:
        r = subprocess.run(
            [python_exe, "-c", "import debugpy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_pdb_fallback(target_path, fwd_args, abs_site, site_line,
                     var_names, python_exe):
    """Drive `target_path` under stdlib pdb, read `var_names` at the
    one-shot breakpoint, and return a list of records:

        [{"var": str, "dtype": str, "length": int, "raw": bytes}, ...]

    Raises RuntimeError on a pdb/process failure and ValueError if a
    requested variable has an unsupported type (caller → exit 4).

    Defence-in-depth gate on ``var_names``: the entry point in
    ``main.py`` already rejects non-identifier names at CLI parse
    time, but this function is also imported directly by tests and
    will eventually be reachable from a non-CLI driver, so the
    expression-injection threat (a hostile var name eval'd into a
    probe template) is reaffirmed here.
    """
    import re as _re
    _SAFE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    for v in var_names:
        if not isinstance(v, str) or not _SAFE.match(v):
            raise ValueError(
                f"pdb fallback: refusing to expand --var entry {v!r} "
                f"that is not a Python identifier")

    script_lines = [f"tbreak {abs_site}:{site_line}", "continue"]
    for v in var_names:
        script_lines.append(_PROBE.format(var=v))
    script_lines += ["continue", "quit"]
    pdb_stdin = ("\n".join(script_lines) + "\n").encode("utf-8")

    # The wall-clock timeout covers the *entire* target run — process
    # spawn, target execution up to the breakpoint, probe evaluation,
    # and the post-breakpoint resume. Targets that take longer than
    # the default (60 s) to reach the breakpoint can override via the
    # `TOPO_DEBUG_PDB_TIMEOUT_SEC` env var; 0 disables the timeout
    # (block forever, useful when debugging a hang interactively).
    timeout_sec: "float | None" = 60.0
    raw_env = os.environ.get("TOPO_DEBUG_PDB_TIMEOUT_SEC")
    if raw_env is not None:
        try:
            v = float(raw_env)
        except ValueError:
            v = -1.0  # leave default in place; do not crash on bad input
        if v == 0:
            timeout_sec = None
        elif v > 0:
            timeout_sec = v

    cmd = [python_exe, "-m", "pdb", target_path, *fwd_args]
    proc: "subprocess.Popen[bytes] | None" = None
    try:
        # Capture stderr (instead of routing to DEVNULL) so a timeout
        # error message can include the tail of the target's own
        # diagnostics — historically a silent timeout left the user
        # with no signal whether the breakpoint was ever reached.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = proc.communicate(input=pdb_stdin, timeout=timeout_sec)

        class _Proc:
            __slots__ = ("returncode", "stdout", "stderr")
            def __init__(self, rc: int, so: bytes, se: bytes) -> None:
                self.returncode = rc
                self.stdout = so
                self.stderr = se

        proc_result = _Proc(proc.returncode, stdout, stderr)
    except OSError as e:
        raise RuntimeError(f"failed to spawn pdb: {e}")
    except subprocess.TimeoutExpired as e:
        # Cleanup-on-cancel: kill the lingering child and drain a
        # small window of its stderr so the user sees the target's
        # tail diagnostics instead of an opaque "timed out".
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                _so, _se = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                _so, _se = b"", b""
            tail = (_se or b"").decode("utf-8", errors="replace")
        else:
            tail = (e.stderr or b"").decode("utf-8", errors="replace") if e.stderr else ""
        secs = (f"{timeout_sec:g}s" if timeout_sec is not None
                else "no timeout configured but communicate() raised")
        msg = (f"pdb fallback timed out ({secs}); the timeout covers the entire "
               f"target run, not just the wait for the breakpoint. Override via "
               f"TOPO_DEBUG_PDB_TIMEOUT_SEC=<seconds> (0 disables).")
        if tail.strip():
            # Trim the tail to a reasonable size for the user-facing
            # message; full text is on stderr anyway when the user
            # re-runs with the timeout raised.
            tail_clip = tail.strip().splitlines()[-20:]
            msg += "\nTarget stderr (last 20 lines):\n" + "\n".join(tail_clip)
        raise RuntimeError(msg)

    out = proc_result.stdout.decode("utf-8", errors="replace")
    found = {}
    for m in _REC.finditer(out):
        body = m.group(1)
        # name|dtype|len|hex
        parts = body.split("|", 3)
        if len(parts) != 4:
            continue
        name, dtype, length_s, hex_s = parts
        found[name] = (dtype, length_s, hex_s)

    records = []
    for v in var_names:
        if v not in found:
            raise RuntimeError(
                f"pdb fallback did not capture variable {v!r} at "
                f"{os.path.basename(abs_site)}:{site_line} "
                f"(breakpoint not hit or variable unbound)")
        dtype, length_s, hex_s = found[v]
        if dtype == "ERR":
            raise ValueError(
                f"variable {v!r} is not a list[int]/list[float] "
                f"(pdb fallback supports only the basic list[int]/"
                f"list[float] subset)")
        try:
            length = int(length_s)
        except ValueError:
            raise RuntimeError(f"pdb fallback: bad length for {v!r}: "
                               f"{length_s!r}")
        try:
            raw = bytes.fromhex(hex_s)
        except ValueError as e:
            raise RuntimeError(
                f"pdb fallback: undecodable bytes for {v!r}: {e}")
        records.append({"var": v, "dtype": dtype,
                        "length": max(length, 0), "raw": raw})
    return records
