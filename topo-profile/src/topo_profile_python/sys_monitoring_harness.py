#!/usr/bin/env python3
# PEP 669 `sys.monitoring` harness for
# `topo profile trace --mode=sys-monitoring --sys-monitoring-input <ndjson>`.
#
# Runs a target Python script with the PEP 669 monitoring API (CPython
# 3.12+) registered for PY_START and PY_RETURN, and writes one NDJSON line
# per event to stdout. The downstream C++ converter
# (SysMonitoringNdjsonConverter) pairs START/RETURN into the Topo profile
# sampling sub-schema.
#
# Line shape (one frame per event; the converter pairs them by thread +
# call depth):
#
#     {"event_type":"py_start",
#      "ts_ns":<int>,
#      "thread":{"id":<int>,"name":"<str>"},
#      "frame":{"module":"<str>","function":"<str>","line":<int>}}
#     {"event_type":"py_return", ... same shape ... }
#
# `sys.monitoring` is a CPython-internal, near-zero-overhead instrumentation
# API (no per-call Python frame allocation like sys.settrace). On Python
# 3.12+ it produces a PY_START / PY_RETURN event sequence that joins
# successfully against the declarations. Declaration join is downstream:
# each frame is flattened to `<module>.<function>:<line>`, so a
# declared stage-boundary function surfaces by name in the sample stacks.
#
# CLI:
#     python -m topo_profile_python.sys_monitoring_harness
#         [--target-stdout <path>] <target.py> [target-args...]
#
# Output:
#     stdout — NDJSON lines (one per PY_START / PY_RETURN event)
#     stderr — harness diagnostics only; target output suppressed unless
#              --target-stdout is given (then stdout+stderr fold there).
#
# Exit codes:
#     0  — success
#     2  — harness error (Python < 3.12, target not found, CLI misuse,
#           target raised)
#
# Pure stdlib (sys.monitoring, runpy, threading, time, json). No pip deps.

import argparse
import io
import json
import os
import runpy
import sys
import threading
import time

# PEP 669 landed in CPython 3.12. `sys.monitoring` is absent on 3.11- and
# the API shape is stable from 3.12 onward. Gate hard with a clear message
# so the C++ side never has to special-case interpreter versions.
_MIN = (3, 12)


def _emit(rec: dict) -> None:
    sys.stdout.write(json.dumps(rec, separators=(",", ":")))
    sys.stdout.write("\n")


def _frame_of(code, line: int) -> dict:
    """Flatten a code object to the wire frame shape."""
    module = os.path.basename(getattr(code, "co_filename", "") or "")
    return {
        "module": module,
        "function": getattr(code, "co_qualname", None) or code.co_name,
        "line": int(line or getattr(code, "co_firstlineno", 0) or 0),
    }


def _install_monitoring() -> int:
    """Register PY_START / PY_RETURN callbacks under our own tool id.

    Returns the monitoring tool id (caller frees it on teardown).
    """
    mon = sys.monitoring
    tool_id = mon.PROFILER_ID  # the slot reserved for profiling tools
    mon.use_tool_id(tool_id, "topo-profile-sys-monitoring")

    thread = threading.current_thread()

    def _on_start(code, instruction_offset):
        # Resolve the line lazily — co_firstlineno is enough for stage
        # join (function identity), and avoids the cost of per-offset
        # line-table lookups on the hot path.
        _emit({
            "event_type": "py_start",
            "ts_ns": time.perf_counter_ns(),
            "thread": {"id": threading.get_ident(), "name": thread.name},
            "frame": _frame_of(code, code.co_firstlineno),
        })

    def _on_return(code, instruction_offset, retval):
        _emit({
            "event_type": "py_return",
            "ts_ns": time.perf_counter_ns(),
            "thread": {"id": threading.get_ident(), "name": thread.name},
            "frame": _frame_of(code, code.co_firstlineno),
        })

    E = mon.events
    mon.set_events(tool_id, E.PY_START | E.PY_RETURN)
    mon.register_callback(tool_id, E.PY_START, _on_start)
    mon.register_callback(tool_id, E.PY_RETURN, _on_return)
    return tool_id


def _teardown_monitoring(tool_id: int) -> None:
    mon = sys.monitoring
    try:
        mon.set_events(tool_id, 0)
        mon.register_callback(tool_id, mon.events.PY_START, None)
        mon.register_callback(tool_id, mon.events.PY_RETURN, None)
    finally:
        mon.free_tool_id(tool_id)


def _run_target(target: str, target_args: list,
                target_stdout_path: str | None) -> None:
    if not os.path.exists(target):
        raise FileNotFoundError(target)

    saved_argv = sys.argv[:]
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr
    sys.argv = [target] + list(target_args)

    sink = None
    tool_id = None
    try:
        if target_stdout_path is not None:
            sink = open(target_stdout_path, "w", encoding="utf-8")
            sys.stdout = sink
            sys.stderr = sink
        else:
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()

        # Install AFTER stdout is swapped so the harness's own _emit (which
        # writes to the *saved* real stdout via the closure below) is not
        # captured. We bind _emit to the real stdout explicitly.
        tool_id = _install_monitoring()
        try:
            runpy.run_path(target, run_name="__main__")
        except SystemExit as se:
            code = (se.code if isinstance(se.code, int)
                    else (0 if se.code is None else 1))
            if code != 0:
                raise
    finally:
        if tool_id is not None:
            _teardown_monitoring(tool_id)
        sys.argv = saved_argv
        sys.stdout = saved_stdout
        sys.stderr = saved_stderr
        if sink is not None:
            sink.close()


def main(argv: list | None = None) -> int:
    if sys.version_info[:2] < _MIN:
        sys.stderr.write(
            "sys_monitoring_harness: requires Python >= 3.12 (PEP 669 "
            f"sys.monitoring); running {sys.version.split()[0]}. "
            "Use --mode=sampling with --cprofile-input for older "
            "interpreters.\n"
        )
        return 2

    parser = argparse.ArgumentParser(
        prog="topo_profile_python.sys_monitoring_harness",
        description=(
            "Run a Python script under PEP 669 sys.monitoring and emit "
            "PY_START/PY_RETURN NDJSON on stdout (sys.monitoring "
            "--mode=sys-monitoring track)."
        ),
        usage=(
            "python -m topo_profile_python.sys_monitoring_harness "
            "[--target-stdout <path>] <target.py> [target-args...]"
        ),
    )
    parser.add_argument("--target-stdout", default=None,
                        help="redirect target stdout+stderr here "
                             "(default: suppress)")
    parser.add_argument("target", help="path to the .py script to profile")
    args, target_args = parser.parse_known_args(argv)
    if target_args and target_args[0] == "--":
        target_args = target_args[1:]

    # Pin _emit to the real stdout so monitoring callbacks fired while the
    # target's stdout is redirected still write to our NDJSON wire.
    global _emit
    real_stdout = sys.stdout

    def _emit_real(rec: dict) -> None:
        real_stdout.write(json.dumps(rec, separators=(",", ":")))
        real_stdout.write("\n")

    _emit = _emit_real

    try:
        _run_target(args.target, target_args, args.target_stdout)
    except FileNotFoundError as e:
        sys.stderr.write(
            f"sys_monitoring_harness: target script not found: {e}\n")
        return 2
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        sys.stderr.write(
            f"sys_monitoring_harness: target exited with code {code}\n")
        return 2
    except Exception as e:
        sys.stderr.write(
            f"sys_monitoring_harness: target raised "
            f"{type(e).__name__}: {e}\n")
        return 2

    real_stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
