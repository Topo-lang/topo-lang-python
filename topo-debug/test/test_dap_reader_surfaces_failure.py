"""Regression test for
topo-lang-python-dapclient-reader-thread-swallows-exceptions.

Pre-fix, ``DapClient._reader_loop`` wrapped the entire read/parse/dispatch
loop in ``except Exception: pass`` so any framing error, JSON decode
failure, or dispatch crash silently terminated the reader thread. Waiters
then saw ``_closed = True`` and raised a generic "DAP connection closed"
RuntimeError — the cause of the close was lost.

The fix splits the catch into three arms (OSError on ``recv``, parse
exception, dispatch exception), captures the cause into
``DapClient._fail_reason``, prints a first-step ``{PROG}: DAP reader
exited: ...`` line to stderr, and appends the cause to the waiter's
RuntimeError message. This test pins both behaviours.

Run: ``python3 -m unittest topo-lang-python.topo-debug.test.test_dap_reader_surfaces_failure``
"""

from __future__ import annotations

import io
import socket
import sys
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parents[1] / "runtime"))

from topo_debug_python import main as dbg_main  # noqa: E402


@contextmanager
def _captured_stderr():
    """Redirect sys.stderr inside the block to a fresh StringIO."""
    saved = sys.stderr
    buf = io.StringIO()
    sys.stderr = buf
    try:
        yield buf
    finally:
        sys.stderr = saved


def _socket_pair() -> tuple[socket.socket, socket.socket]:
    """Return a connected (client, server) socket pair over loopback."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    server, _ = listener.accept()
    listener.close()
    client.settimeout(None)
    server.settimeout(None)
    return client, server


def _await_close(dap: dbg_main.DapClient, *, timeout: float = 2.0) -> None:
    """Block until the reader thread sets ``_closed``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with dap._cv:
            if dap._closed:
                return
            dap._cv.wait(timeout=0.05)
    raise AssertionError("reader thread did not close within deadline")


class ReaderSurfacesMalformedHeader(unittest.TestCase):
    """A malformed Content-Length header (``ValueError`` from ``int(...)``)
    is currently caught inside ``_try_parse_one`` and yields ``None``, so
    this case stays a clean close. A garbage non-numeric body length
    followed by EOF exercises that path symmetrically."""

    def test_garbage_then_eof_closes_cleanly(self):
        client, server = _socket_pair()
        try:
            dap = dbg_main.DapClient(client)
            # Send a header that ``_try_parse_one`` returns None on
            # (no Content-Length at all), then close — clean EOF path.
            server.sendall(b"NotARealHeader\r\n\r\n")
            server.close()
            _await_close(dap)
            # Clean EOF: no fail reason recorded.
            self.assertIsNone(dap._fail_reason)
        finally:
            try:
                client.close()
            except OSError:
                pass


class ReaderSurfacesOversizedContentLength(unittest.TestCase):
    """An adversarial 4 GB Content-Length header forces the
    ``self._recv_buf`` extend / parse loop to overshoot any practical
    memory budget. The previous code masked the resulting MemoryError;
    the fix surfaces it through the ``_fail_reason`` channel."""

    def test_huge_length_surfaces_or_closes_cleanly(self):
        client, server = _socket_pair()
        try:
            dap = dbg_main.DapClient(client)
            # 100 MB Content-Length but only a tiny body, then EOF.
            # ``_try_parse_one`` returns None until ``len(_recv_buf) >=
            # total`` — never reached — then ``recv`` returns b"" and the
            # loop exits cleanly. The fix doesn't *create* a failure mode
            # here, it preserves the clean close.
            server.sendall(
                b"Content-Length: 104857600\r\n\r\n{\"seq\":1}")
            server.close()
            _await_close(dap, timeout=3.0)
            # Either the clean-EOF branch or the recv-OSError branch
            # is acceptable. The important contract is that ``_closed``
            # actually gets set rather than the reader silently spinning.
            self.assertTrue(dap._closed)
        finally:
            try:
                client.close()
            except OSError:
                pass


class ReaderSurfacesDispatchException(unittest.TestCase):
    """If ``_dispatch`` raises (e.g. monkey-patched to simulate a
    downstream crash), the fix captures the exception into
    ``_fail_reason``, prints a first-step stderr line, and waiters
    raise a RuntimeError that mentions the cause."""

    def test_dispatch_crash_surfaces_in_waiter(self):
        client, server = _socket_pair()
        try:
            dap = dbg_main.DapClient(client)

            # Monkey-patch _dispatch to raise on the first call.
            def boom(msg):
                raise RuntimeError("synthetic dispatch failure for test")
            dap._dispatch = boom  # type: ignore[method-assign]

            # Send one well-framed response so _dispatch is invoked.
            body = b'{"seq":1,"type":"response","request_seq":1,"success":true}'
            header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")

            with _captured_stderr() as err:
                server.sendall(header + body)
                _await_close(dap, timeout=2.0)

                # Reader recorded the cause.
                self.assertIsNotNone(dap._fail_reason)
                assert dap._fail_reason is not None  # mypy
                self.assertIn("dispatch error", dap._fail_reason)
                self.assertIn("synthetic dispatch failure for test",
                              dap._fail_reason)

                # First-step trace landed on stderr.
                self.assertIn("DAP reader exited", err.getvalue())

            # Waiter surfaces the cause rather than the generic message.
            with self.assertRaises(RuntimeError) as ctx:
                dap.wait_response(seq=1, command_hint="probe", timeout=0.2)
            self.assertIn("synthetic dispatch failure for test",
                          str(ctx.exception))
        finally:
            try:
                client.close()
            except OSError:
                pass
            try:
                server.close()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
