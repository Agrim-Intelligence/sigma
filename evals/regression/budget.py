#!/usr/bin/env python3
"""Run the onboarding control's PLAIN gesture and hold its wall time to evals/regression/budget.json (#879).

    python3 evals/regression/budget.py [--budget FILE] [-- COMMAND ...]

Without a command it runs `python3 tools/onboarding_control.py`, the gesture docs/onboarding-control.md
gives. Prints `onboarding-control wall time: Xs, ceiling Ys`. Exit: the command's own nonzero code; 1 when
the wall time is over `ceiling_seconds`; 2 when the budget file is unreadable or malformed. A null
ceiling means unset: the time is printed and not enforced (how a ceiling is first measured).

Read-only: no file writes, no network, no model. Cost: one control run (about 2 minutes on a laptop).
"""
import argparse
import json
import math
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent


def ceiling(path):
    """ceiling_seconds as a float, None when unset; ValueError when the file is not a usable budget."""
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get("ceiling_seconds")
    except (OSError, ValueError, AttributeError) as exc:
        raise ValueError(f"budget {path}: {exc}") from None
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"budget {path}: ceiling_seconds must be a positive number or null")
    return float(value)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget", default=str(HERE / "budget.json"))
    ap.add_argument("command", nargs="*", help="after `--`: a command to run instead of the plain gesture")
    args = ap.parse_args(argv)
    try:
        limit = ceiling(args.budget)
    except ValueError as exc:
        print(f"budget.py: {exc}", file=sys.stderr)
        return 2
    command = args.command or [sys.executable, str(REPO / "tools" / "onboarding_control.py")]
    start = time.monotonic()
    code = subprocess.run(command, cwd=str(REPO)).returncode
    wall = time.monotonic() - start
    print(f"onboarding-control wall time: {wall:.1f}s, ceiling {'unset' if limit is None else f'{limit:.1f}s'}")
    if code:
        return code
    if limit is not None and wall > limit:
        print(f"FINDING wall time {wall:.1f}s exceeds the {limit:.1f}s ceiling in {args.budget}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
