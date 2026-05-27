#!/usr/bin/env python3
# End-to-end driver for the live --mode=sys-monitoring path.
#
# Only registered as a CTest when configure-time finds Python >= 3.12
# (PEP 669). Steps:
#   1. run `python -m topo_profile_python.sys_monitoring_harness <demo.py>`
#      capturing the PY_START/PY_RETURN NDJSON to a temp file
#   2. run `<topo-profile> trace --mode sys-monitoring
#          --sys-monitoring-input <temp> --backend python`
#   3. assert the trace JSON has source_format == "py_sys_monitoring",
#      a non-empty sampling.events array, and that a declared stage
#      function name (stage_load / stage_transform) joined into a stack
#
# argv: <topo-profile-bin> <repo-src-root>
# exit 0 on success, 1 on any failure (CTest reads the exit code).

import json
import os
import subprocess
import sys
import tempfile


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write("usage: driver <topo-profile-bin> <src-root>\n")
        return 1
    topo_profile, src_root = sys.argv[1], sys.argv[2]

    pkg_src = os.path.join(src_root, "topo-lang-python", "topo-profile", "src")
    demo = os.path.join(src_root, "topo-lang-python", "topo-profile",
                        "test", "fixtures", "sys_monitoring_demo.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_src + os.pathsep + env.get("PYTHONPATH", "")

    with tempfile.TemporaryDirectory() as td:
        ndjson = os.path.join(td, "events.ndjson")
        with open(ndjson, "w", encoding="utf-8") as fh:
            h = subprocess.run(
                [sys.executable, "-m",
                 "topo_profile_python.sys_monitoring_harness", demo],
                stdout=fh, stderr=subprocess.PIPE, env=env, timeout=60)
        if h.returncode != 0:
            sys.stderr.write(
                "harness failed: " + h.stderr.decode(errors="replace") + "\n")
            return 1
        if os.path.getsize(ndjson) == 0:
            sys.stderr.write("harness produced no NDJSON events\n")
            return 1

        p = subprocess.run(
            [topo_profile, "trace", "--mode", "sys-monitoring",
             "--sys-monitoring-input", ndjson, "--backend", "python"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        # exit 0 (clean) or 1 (target-failed partial) both still emit JSON;
        # a usage/parse error is exit 2 with no JSON.
        if p.returncode not in (0, 1):
            sys.stderr.write(
                "topo-profile failed (rc=%d): %s\n"
                % (p.returncode, p.stderr.decode(errors="replace")))
            return 1
        try:
            doc = json.loads(p.stdout.decode())
        except Exception as e:
            sys.stderr.write("trace output not valid JSON: %s\n" % e)
            return 1

    s = doc.get("sampling", {})
    if s.get("source_format") != "py_sys_monitoring":
        sys.stderr.write("source_format != py_sys_monitoring: %r\n"
                         % s.get("source_format"))
        return 1
    events = s.get("events", [])
    if not events:
        sys.stderr.write("sampling.events is empty\n")
        return 1
    joined = any(
        any("stage_load" in f or "stage_transform" in f
            for f in ev.get("stack", []))
        for ev in events)
    if not joined:
        sys.stderr.write(
            "no declared stage function (stage_load/stage_transform) "
            "joined into any sample stack\n")
        return 1
    sys.stdout.write(
        "OK: %d samples, source_format=py_sys_monitoring, "
        "declaration join verified\n" % len(events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
