#!/usr/bin/env python3
"""#2311 (slice 1 of Epic #2310, design #2289): the passive drift watcher's wall-clock
heartbeat, threaded into `watch_daemon.py`'s own tick sequence exactly the way `#2294`'s
`reconcile_tick.py` already is (design `### 1`, BR-3) -- same
`tick(sdlc_dir, config=None, run=None, now=None)` -> one-line-summary-or-`""` contract, same
`main(argv)` CLI convention, same non-fatal failure posture (a watcher tick must never crash the
`watch_daemon.py` loop -- AGENTS.md's RESILIENCY bar).

Thin wiring, on purpose: `tick()` calls `drift_watch.sweep(...)` directly rather than re-deriving
ANY of its own gates here (the `ledger.enabled`-composed `drift_watch.enabled`, the TTL watermark,
the ledger-backed dedup) -- reusing the already-gated sweep function, rather than hand-rolling a
second copy of the same gates, is what makes "the same config an operator already understands
governs both the loop-driven and the `watch_daemon.py`-driven trigger" true by construction, the identical
reasoning `reconcile_tick.py`'s own docstring gives for reaching into `loop._reconcile_sweep`.

    python3 drift_tick.py <sdlc_dir>
"""
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
drift_watch = _load("drift_watch")


def tick(sdlc_dir, config=None, run=None, now=None):
    """-> a one-line summary for `watch_daemon.py`'s log, or `""` when nothing needed anyone. Mirrors
    `reconcile_tick.py`'s `tick()` shape exactly.

    `config=None` reads `config.json` fresh via `ledger._config` (same idiom as every sibling
    watch-tick script), so a config edit between two ticks is picked up on the very next one."""
    config = config if config is not None else ledger._config(sdlc_dir)
    return drift_watch.sweep(sdlc_dir, config=config, run=run, now=now) or ""


USAGE = "usage: drift_tick.py [sdlc_dir]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"drift_tick: tick failed (non-fatal): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
