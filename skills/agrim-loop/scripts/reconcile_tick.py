#!/usr/bin/env python3
"""#2294: give `reconcile.py`'s AUTOMATIC-tier sweep a wall-clock heartbeat independent of whether
anyone is actively driving `/agrim-loop`.

PC-4 (`.sdlc/design/2287.md`): `loop.py`'s `_reconcile_sweep` is already the correct, fully-gated
entry point for this -- `discovery.reconcile.mode` (default 'off'), then a TTL watermark
(`discovery.reconcile.ttl_minutes`, default 60), then `reconcile.sweep_reconcile` itself. It is
only ever CALLED from inside `next_batch`/`_next` (loop.py:1828-1829, 2060), so an idle repo --
nobody running `/agrim-loop` -- gets zero board sanitation no matter how the TTL is set, because
nothing calls the function that checks it. This script is that second caller: `watch_daemon.py`'s own
tick sequence (BR-13) and the `AUTOWATCH.md` Desktop/CLI adapter pattern (BR-14) are both already
periodic and host-agnostic, so the fix is one more line in a loop that already exists, not a new
watcher class.

Thin wiring, mirroring `agent_watch.py`/`comment_watch.py`'s own shape exactly: same
`tick(sdlc_dir, config=None, ...)` -> one-line-summary-or-"" contract, same
`main(argv)` CLI convention, same non-fatal failure posture (a sweep failure must never crash the
watch_daemon.py loop -- AGENTS.md's RESILIENCY bar).

NO new config key, NO new gate. `tick()` calls `loop._reconcile_sweep(sdlc_dir, config, ...)`
directly rather than re-deriving `discovery.reconcile.mode`/`ttl_minutes`/the TTL watermark here --
that function is ALREADY exactly this: opt-in gated, TTL-throttled, and fail-open (never raises).
Reusing it, rather than hand-rolling a second copy of the same three gates, is what makes "the same
config an operator already understands governs both the loop-driven and the watch_daemon.py-driven
trigger" true by construction, not by two independently-maintained copies staying in sync by hand --
and it inherits `_reconcile_sweep`'s own, already-tested idempotency for free: two calls inside the
same TTL window cost one `gh` census, whichever caller gets there first.

Deliberately reaches into `loop.py`'s underscore-prefixed `_reconcile_sweep` -- Python enforces no
real privacy boundary between sibling scripts in this skill, and `autowatch.py`'s own
`_check_no_concurrent_session` already sets this precedent for `loop._goal_has_registered_worker`.

    python3 reconcile_tick.py <sdlc_dir>
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
loop = _load("loop")


def tick(sdlc_dir, config=None, run=None, now=None):
    """-> a one-line summary for watch_daemon.py's log, or "" when nothing needed anyone. Mirrors
    agent_watch.py/comment_watch.py's tick() shape exactly.

    `config=None` reads `config.json` fresh via `ledger._config` (same idiom as both siblings), so
    a config edit between two ticks is picked up on the very next one, matching every other
    watch-tick script in this kit."""
    config = config if config is not None else ledger._config(sdlc_dir)
    swept = loop._reconcile_sweep(sdlc_dir, config, run=run, now=now)
    # #232: the second, UNGATED duty of this tick -- close goals `record review` left waiting whose
    # PR has since merged. Not behind `discovery.reconcile.mode` (default off): done-means-merged is
    # the shipped behaviour, so its close must not depend on an opt-in. Bounded and throttled inside
    # `_reconcile_awaiting_merges` itself; zero `gh` calls when nothing is awaiting.
    merged = loop._reconcile_awaiting_merges(sdlc_dir, config, run=run, now=now)
    parts = []
    if swept:
        parts.append("%d issue(s) reconciled — %s" % (
            len(swept), ", ".join("#%s" % issue for issue in sorted(swept))))
    if merged:
        parts.append("%d awaiting-merge goal(s) recorded — %s" % (
            len(merged), ", ".join("%s %s" % (goal, outcome) for goal, outcome, _pr in merged)))
    return "; ".join(parts)


USAGE = "usage: reconcile_tick.py [sdlc_dir]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"reconcile_tick: tick failed (non-fatal): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
