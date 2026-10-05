#!/usr/bin/env python3
"""goal_design.py (#2032) -- the engine behind `sigma-goal-design`'s sweep-round budget, and the
verb `sigma-goal-review` re-runs to check it.

WHY THIS SCRIPT EXISTS AT ALL. Before #2032, "the budget is three sweep rounds" was a number
stated only in `SKILL.md` prose -- nothing in Python counted a round, and nothing checked what a
design pass claimed it ran under. A `goal_design.rounds` config key that no code path read would
have been exactly that same silent half-guarantee with an extra name on it, which SAFETY forbids
(`AGENTS.md`) -- and it is why an earlier pass correctly declined to add one. This module is the
fix: `sweep_budget()` is engine-owned data behind a CLI verb, mirroring
`skills/sigma-dossier/scripts/dossier.py`'s `followups()` -- the number a pass obeys is READ BACK
from here, never remembered, and the same call is what `goal-review` re-runs to check the
artifact's `**Budget**` field against what the verb actually returns, closing the hole the #2032
adversarial review found: a pass that skips the verb and writes `3` from memory produced an
artifact indistinguishable from a correct one.

THE SHAPE. `goal_design.rounds` (an optional positive integer under the existing `goal_design`
config block) raises the ceiling a `full`-mode design pass obeys, so an operator who wants a pass
to "scan the whole product" can raise it -- bounded, always a number the operator chose, never an
`"unbounded"` sentinel (each round is real Anthropic spend, and an infinite sweep is not something
anyone can opt into safely). Absent, the ceiling is unchanged from before this issue: 3, so an
install that touches no config gets byte-identical behaviour.

`mode: lane` does NOT read this key at all -- lane budgets are fixed at one round per measured lane
(`small`=1, `medium`=2, `large`=3; only `small` was ever numbered anywhere in `SKILL.md` before
this -- `medium`/`large` are a genuine spec gap this closes, not a new decision dressed as a
restatement). No evidence across the three real design passes on record (#1942, #1973, #2017) ever
needed lane's own ceiling moved, and the live config runs `mode: full` -- so a `rounds` key set
under `mode: lane` is reported back as ignored (`rounds_config_ignored`) rather than silently doing
nothing, with the escape hatch (switch to `mode: full`) named on stderr.

THE BOOL TRAP. `True == 1` and `isinstance(True, int)` are both true in Python (`bool` is an `int`
subclass), so a bare `isinstance(raw, int)` check alone would silently accept a stray
`"rounds": true` in a hand-edited config as `rounds=1`. The explicit `not isinstance(raw, bool)`
guard in `sweep_budget()` below is the only thing standing between that and a config typo passing
as a deliberate override -- see `tests/test_goal_design.py`'s own control, which removes the guard
and watches `True` get silently accepted.

    python3 goal_design.py sweep-budget <sdlc_dir> --mode full|lane --lane small|medium|large
"""
import json, pathlib, sys, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load_loop_script(name):
    """Cross-load a script from the sibling `sigma-loop` skill (skills/sigma-goal-design/scripts/ ->
    skills/sigma-loop/scripts/<name>.py) -- the established, narrow, named exception to "don't reach
    across skill directories" (`dossier.py`'s own `_load_loop_script`, `compile_plan.py`'s,
    `define.py`'s, `brainstorm.py`'s `_load`). `state.py` is where `load_config`/`ConfigMissing`
    already live, and `discovery.py` is where the `LANES`/`DEFAULT_LANE` rubric this module's own
    lane budgets key off of already lives -- reimplementing either here would be exactly the
    hardened-sibling-divergence bug class this plugin's own docs already warn against."""
    path = _HERE.parent.parent / "sigma-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state = _load_loop_script("state")
discovery = _load_loop_script("discovery")

#: The `full`-mode default, unchanged from the fixed constant #2032 replaces ("The budget is three
#: sweep rounds", `SKILL.md` §2, pre-#2032) -- an absent `goal_design.rounds` must resolve here so
#: an install that changes no config gets byte-identical behaviour.
DEFAULT_FULL_ROUNDS = 3

#: `mode: lane` budgets -- fixed, and NOT configurable (#2032's own ruling: no evidence lane's
#: ceiling ever needed moving across #1942/#1973/#2017, none of which ran under lane, and the live
#: config runs `mode: full`). Only `small=1` was ever stated in `SKILL.md` before this issue.
LANE_ROUNDS = {"small": 1, "medium": 2, "large": 3}


def sweep_budget(config, mode, lane):
    """Resolve the sweep-round ceiling for one design pass. `config` is a parsed `config.json`
    dict (`state.load_config`'s return, or `{}` where none exists -- see `main()`). Never raises on
    a malformed `goal_design.rounds`: an invalid override falls back to the unconfigured default
    rather than refusing the whole pass over one bad config value -- the same fail-open posture
    the two private-side daemon starters applied to a malformed `interval`, applied here
    to a malformed `rounds`. (Those two were `loop.py`'s own when this was written; S1-G7 moved them
    out of the core, so that reference is PROVENANCE -- the posture is what was copied, not the
    location, and `loop.py` defines neither today.)"""
    goal_design_cfg = config.get("goal_design") or {}
    raw = goal_design_cfg.get("rounds")
    resolved_lane = lane if lane in discovery.LANES else discovery.DEFAULT_LANE

    if mode == "lane":
        ignored = raw is not None
        if ignored:
            print(
                "goal_design.py sweep-budget: goal_design.rounds is ignored under mode: lane "
                "(lane budgets are fixed at small=1/medium=2/large=3) -- set mode: full to raise "
                "the ceiling instead.",
                file=sys.stderr,
            )
        return {"rounds": LANE_ROUNDS[resolved_lane], "mode": "lane", "lane": resolved_lane,
                "rounds_config_ignored": ignored}

    # mode == "full" (or an already-normalised caller): the config key is live here.
    # `not isinstance(raw, bool)` MUST come before `raw > 0` -- see the module docstring's BOOL
    # TRAP. Order matters for a different reason too: `isinstance(True, int)` is `True`, so
    # checking `isinstance(raw, int)` alone (without the bool exclusion) would let `True` through.
    valid = isinstance(raw, int) and not isinstance(raw, bool) and raw > 0
    rounds = raw if valid else DEFAULT_FULL_ROUNDS
    return {"rounds": rounds, "mode": "full", "lane": resolved_lane,
            "rounds_config_ignored": False}


_USAGE = "usage: goal_design.py sweep-budget <sdlc_dir> --mode full|lane --lane small|medium|large"


def _value(tail, name):
    if name in tail:
        i = tail.index(name)
        return tail[i + 1] if i + 1 < len(tail) else None
    return None


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(argv) >= 3 and argv[1] == "sweep-budget":
        sdlc_dir, tail = argv[2], argv[3:]
        mode = _value(tail, "--mode") or "full"
        lane = _value(tail, "--lane") or discovery.DEFAULT_LANE
        if mode not in ("full", "lane"):
            print("goal_design.py sweep-budget: --mode must be full or lane (got %r)" % mode,
                  file=sys.stderr)
            return 2
        try:
            config = state.load_config(sdlc_dir)
        except state.ConfigMissing:
            # No .sdlc/config.json at all -- there is no configured `goal_design.rounds` to read,
            # the same reading `SKILL.md` §2 already gives this exception for `mode` resolution.
            config = {}
        print(json.dumps(sweep_budget(config, mode, lane), indent=2))
        return 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    sys.exit(_load_loop_script("timing_store").timed_main(main, sys.argv, "goal_design"))
