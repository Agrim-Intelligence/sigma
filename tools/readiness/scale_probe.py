#!/usr/bin/env python3
"""Cheap 1x / 10x / 100x timings for two per-item Sigma paths (D5).

Usage:
    scale_probe.py --workdir DIR --json OUT

Probes, each a real Sigma CLI against a synthetic scratch ``.sdlc`` (nothing real is touched):
  * ``sigma-log slots``: one action-log file per goal, 85 / 850 / 8,500 goals x 14 events.
  * ``liveness_prune.py sweep --dry-run --limit 0``: 1,000 / 10,000 / 100,000 aged ``claims/*.lock``.
Wall time, median of 3.  One machine, warm cache; the host is recorded, nothing else is claimed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"
SCHEMA = "readiness-scale-probe/v1"


def _median_wall(argv, runs=3):
    walls = []
    for _ in range(runs):
        t = time.monotonic()
        p = subprocess.run(argv, capture_output=True, text=True)
        walls.append(time.monotonic() - t)
        if p.returncode not in (0,):
            raise SystemExit("probe command failed rc=%d: %s" % (p.returncode, p.stderr[-300:]))
    return round(statistics.median(walls), 3)


def probe_slots(workdir, goals):
    sdlc = workdir / ("slots-%d" % goals) / ".sdlc"
    (sdlc).mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps({"action_log": {"enabled": True}}))
    spec = importlib.util.spec_from_file_location("actionlog_probe", LOOP / "actionlog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(LOOP))
    spec.loader.exec_module(mod)
    for g in range(1, goals + 1):
        for _ in range(14):
            mod.append(sdlc, str(g), "claimed", "probe")
    files = len(list((sdlc / "state" / "log").glob("*")))
    wall = _median_wall([sys.executable, str(ROOT / "skills" / "sigma-log" / "scripts" / "log.py"), "slots", str(sdlc)])
    return {"items": goals, "files": files, "wall_s": wall}


def probe_claims(workdir, locks):
    sdlc = workdir / ("claims-%d" % locks) / ".sdlc"
    claims = sdlc / "state" / "claims"
    claims.mkdir(parents=True)
    (sdlc / "config.json").write_text("{}")
    old = time.time() - 40 * 86400
    for i in range(1, locks + 1):
        f = claims / ("%d.lock" % i)
        f.write_bytes(b"")
        os.utime(f, (old, old))
    wall = _median_wall([sys.executable, str(LOOP / "liveness_prune.py"), "sweep", str(sdlc), "--dry-run", "--limit", "0"])
    return {"items": locks, "files": locks, "wall_s": wall}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--json", type=Path, required=True)
    args = ap.parse_args(argv[1:])
    if args.workdir.exists() and any(args.workdir.iterdir()):
        print("scale_probe: workdir exists and is not empty", file=sys.stderr)
        return 2
    args.workdir.mkdir(parents=True, exist_ok=True)
    out = {
        "schema": SCHEMA, "platform": sys.platform, "cpus": os.cpu_count(),
        "commit": subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "sigma_log_slots": [probe_slots(args.workdir, n) for n in (85, 850, 8500)],
        "liveness_prune_claims_dry_run": [probe_claims(args.workdir, n) for n in (1000, 10000, 100000)],
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
