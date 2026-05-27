#!/usr/bin/env python3
# cProfile NDJSON harness for `topo profile trace --mode=sampling
# --cprofile-input <ndjson>`.
#
# Runs a target Python script under `cProfile.Profile`, walks the resulting
# `pstats` entries, and writes one NDJSON line per recorded call edge to
# stdout. The line shape mirrors the JFR-NDJSON intermediate so the
# downstream C++ converter (CProfileNdjsonConverter) can fold all sampling
# source-formats into the same Topo profile sampling sub-schema:
#
#     {"event_type":"py_call",
#      "ts_ns":<int>,
#      "thread":{"id":<int>,"name":"<str>"},
#      "stack":[{"module":"<str>","function":"<str>","line":<int>}, ...],
#      "duration_ns":<int>}
#
# cProfile is *deterministic*, not sampling — it instruments every Python
# call. We still route through the sampling sub-schema because the converter
# already exists, and the duration field carries the per-call signal that a
# real sampler would approximate by repetition. `ts_ns` is synthesized as a
# monotonically increasing cursor (accumulated cumulative time) so the
# resulting event stream is ordered; cProfile itself does not preserve
# wall-clock timestamps per call edge.
#
# Stack convention: root → leaf. For each (caller, callee) edge produced by
# `getstats()`, emit one event with `stack=[caller, callee]`. For functions
# that have no caller in the recorded profile (entry points), emit a single-
# frame stack `[callee]`.
#
# CLI:
#     python -m topo_profile_python.cprofile_harness [--target-stdout <path>]
#         <target.py> [args...]
#
# Output:
#     stdout — NDJSON lines (one per recorded call edge)
#     stderr — harness diagnostics only; the target script's own stderr is
#              suppressed unless --target-stdout is given, in which case
#              stderr is folded into the same file (the file is the target
#              process's combined output sink).
#
# Exit codes:
#     0  — success
#     2  — harness error (target not found, CLI misuse, profile failure)
#
# Pure stdlib (cProfile, pstats, threading, time, runpy). No pip deps.

import argparse
import cProfile
import io
import json
import os
import runpy
import sys
import threading
import time


def _emit(line: dict) -> None:
    # Compact JSON, one record per line. `separators` matches the JFR-NDJSON
    # fixture style and keeps the wire small.
    sys.stdout.write(json.dumps(line, separators=(",", ":")))
    sys.stdout.write("\n")


def _frame(code) -> dict:
    """Flatten a `code` object (from cProfile entries) to the wire shape.

    `code` here is either a real CPython code object (`co_filename`,
    `co_name`, `co_firstlineno`) or a `pstats`-synthesised string like
    `"<built-in method builtins.len>"`. For the string case we fall back
    to module="<builtin>" and function=<the string>.
    """
    if hasattr(code, "co_name"):
        module = os.path.basename(getattr(code, "co_filename", "") or "")
        return {
            "module": module,
            "function": code.co_name,
            "line": int(getattr(code, "co_firstlineno", 0) or 0),
        }
    # Built-in / synthesised entry — `code` is a string.
    return {"module": "<builtin>", "function": str(code), "line": 0}


def _seconds_to_ns(t: float) -> int:
    # cProfile stores time in seconds (float, sub-microsecond resolution).
    # Round to the nearest ns; negative inputs are clamped to 0 (defensive —
    # pstats has been observed to produce tiny negatives on some platforms).
    if t <= 0.0:
        return 0
    return int(round(t * 1_000_000_000.0))


def _run_target(target: str, target_args: list, target_stdout_path: str | None) -> cProfile.Profile:
    """Execute `target` (a path to a .py file) under cProfile and return the
    profile object. Captures the target's stdout/stderr per the contract.

    Raises:
        FileNotFoundError if `target` doesn't exist (caller maps to exit 2).
    """
    if not os.path.exists(target):
        raise FileNotFoundError(target)

    # The target script sees its own sys.argv[0] as the script path and
    # sys.argv[1:] as the forwarded args — matches `python target.py args`.
    saved_argv = sys.argv[:]
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr
    sys.argv = [target] + list(target_args)

    sink = None
    try:
        if target_stdout_path is not None:
            sink = open(target_stdout_path, "w", encoding="utf-8")
            sys.stdout = sink
            sys.stderr = sink
        else:
            # Default: suppress the target's output entirely so it does not
            # contaminate our NDJSON wire stream on stdout.
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()

        profile = cProfile.Profile()
        profile.enable()
        try:
            try:
                runpy.run_path(target, run_name="__main__")
            except SystemExit as se:
                # Target called sys.exit(); zero-or-None means clean finish,
                # non-zero re-raises so the caller can surface it as a
                # target failure.
                code = (se.code if isinstance(se.code, int)
                        else (0 if se.code is None else 1))
                if code != 0:
                    raise
        finally:
            profile.disable()
        return profile
    finally:
        sys.argv = saved_argv
        sys.stdout = saved_stdout
        sys.stderr = saved_stderr
        if sink is not None:
            sink.close()


def _emit_events(profile: cProfile.Profile) -> None:
    """Walk profile.getstats() and emit one NDJSON event per recorded call
    edge. cProfile.Profile.getstats() returns entries with:

        entry.code         — CPython code object or built-in name string
        entry.callcount    — total number of times this function was called
        entry.reccallcount — recursive call count (subset of callcount)
        entry.totaltime    — cumulative inline time (s)
        entry.inlinetime   — inline time excluding sub-calls (s)
        entry.calls        — list of sub-call entries, each with .code and
                             timing fields; this is the *callee* side, so
                             "this entry called <code>".

    We invert the relation: for each entry E that has subcalls, treat each
    subcall S as the (caller=E, callee=S) edge. For each entry with no
    incoming caller anywhere in the profile, emit a single-frame stack so
    the entry point is represented.

    `ts_ns` is a monotonic cursor — we accumulate `inlinetime` across the
    emitted events to give a deterministic, ordered timeline.
    """
    entries = profile.getstats()

    # Map id(code) → bool: was this code observed as a callee somewhere?
    called_as_subcall: set[int] = set()
    for entry in entries:
        calls = getattr(entry, "calls", None) or []
        for sub in calls:
            called_as_subcall.add(id(sub.code))

    thread_id = threading.get_ident()
    thread_name = threading.current_thread().name
    cursor_ns = 0

    for entry in entries:
        caller_frame = _frame(entry.code)
        calls = getattr(entry, "calls", None) or []

        if calls:
            # One event per (caller, callee) edge.
            for sub in calls:
                # cProfile's subcall entries carry .totaltime as the time
                # spent in the callee when called from this caller. That is
                # the most faithful single-edge duration.
                edge_dur_s = float(getattr(sub, "totaltime", 0.0) or 0.0)
                edge_dur_ns = _seconds_to_ns(edge_dur_s)
                cursor_ns += edge_dur_ns
                _emit({
                    "event_type": "py_call",
                    "ts_ns": cursor_ns,
                    "thread": {"id": thread_id, "name": thread_name},
                    "stack": [caller_frame, _frame(sub.code)],
                    "duration_ns": edge_dur_ns,
                })
        else:
            # Leaf entry with no further sub-calls. If it is also not called
            # by anyone else in the profile, it's a top-level entry point —
            # emit a single-frame stack so it isn't dropped.
            if id(entry.code) not in called_as_subcall:
                inline_dur_ns = _seconds_to_ns(float(entry.inlinetime or 0.0))
                cursor_ns += inline_dur_ns
                _emit({
                    "event_type": "py_call",
                    "ts_ns": cursor_ns,
                    "thread": {"id": thread_id, "name": thread_name},
                    "stack": [caller_frame],
                    "duration_ns": inline_dur_ns,
                })


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="topo_profile_python.cprofile_harness",
        description=(
            "Run a Python script under cProfile and emit NDJSON call-edge "
            "events on stdout (cProfile NDJSON track)."
        ),
        usage=(
            "python -m topo_profile_python.cprofile_harness "
            "[--target-stdout <path>] <target.py> [target-args...]"
        ),
    )
    parser.add_argument(
        "--target-stdout",
        default=None,
        help=(
            "redirect the target script's stdout+stderr to this file. "
            "Default: suppress."
        ),
    )
    parser.add_argument("target", help="path to the .py script to profile")
    # Everything after <target> is forwarded verbatim to the target. We use
    # parse_known_args (instead of an argparse.REMAINDER positional) so that
    # harness flags placed *before* <target> are still recognized — a
    # REMAINDER positional swallows them regardless of leading `--`.
    args, target_args = parser.parse_known_args(argv)

    # A leading `--` is the conventional argument-list separator; strip one
    # so the target sees its own argv cleanly.
    if target_args and target_args[0] == "--":
        target_args = target_args[1:]

    try:
        profile = _run_target(args.target, target_args, args.target_stdout)
    except FileNotFoundError as e:
        sys.stderr.write(
            f"cprofile_harness: target script not found: {e}\n"
        )
        return 2
    except SystemExit as e:
        # Re-raised from `_run_target` only when target's sys.exit() carried
        # a non-zero code. Clean SystemExit(0) is absorbed inside.
        code = e.code if isinstance(e.code, int) else 1
        sys.stderr.write(
            f"cprofile_harness: target exited with code {code}\n"
        )
        return 2
    except Exception as e:
        sys.stderr.write(
            f"cprofile_harness: target raised {type(e).__name__}: {e}\n"
        )
        return 2

    try:
        _emit_events(profile)
    except Exception as e:
        sys.stderr.write(
            f"cprofile_harness: failed to emit events: {type(e).__name__}: {e}\n"
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
