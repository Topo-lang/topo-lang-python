#!/usr/bin/env python3
# Minimal Python span-emitter for the topo-profile demo.
#
# Mirrors topo-lang-cpp/topo-profile/test/fixtures/spans_demo.cpp: emits
# three NDJSON span records on stdout following libtopo-observe's wire
# shape, so topo-profile (host-agnostic — only consumes the NDJSON
# contract) re-emits them under the Topo profile schema with
# `backend: "python"`.
#
# Wire shape (one line per span):
#   {"name":"pipeline::demo::stageN","duration_ns":<ns>,
#    "thread_id":<u64>,"ts_ns":<ns since epoch>}
#
# Names follow `pipeline::<name>::stage<N>` so parseStagePipeline() in
# topo-profile recovers stage / pipeline fields. Pure stdlib — no
# debugpy / observe runtime; the fixture is just a span-shaped print
# generator.
import json
import sys
import threading
import time


def busy(us: int) -> None:
    deadline_ns = time.monotonic_ns() + us * 1000
    acc = 0
    while time.monotonic_ns() < deadline_ns:
        for i in range(1000):
            acc ^= i
    # Defeat any tracer optimisation.
    if acc == -1:
        sys.stderr.write("unreachable\n")


def emit_span(name: str, duration_ns: int) -> None:
    record = {
        "name": name,
        "duration_ns": duration_ns,
        "thread_id": threading.get_ident(),
        "ts_ns": time.time_ns(),
    }
    # Compact JSON, single line, key order matches libtopo-observe.
    sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def timed_span(name: str, busy_us: int) -> int:
    start_ns = time.monotonic_ns()
    busy(busy_us)
    duration_ns = time.monotonic_ns() - start_ns
    emit_span(name, duration_ns)
    return duration_ns


def main() -> None:
    timed_span("pipeline::demo::stage0", 5000)
    timed_span("pipeline::demo::stage1", 5000)
    timed_span("pipeline::demo::stage2", 5000)


if __name__ == "__main__":
    main()
