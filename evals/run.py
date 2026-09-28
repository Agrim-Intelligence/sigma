#!/usr/bin/env python3
"""Sigma quality evals — the integration/quality gate. Run on every change to catch quality drift.

Two tiers:
  Tier 1 (default, deterministic, FREE, runs in CI): the intent hook is a deterministic proxy for
    "the agent gets the right discipline signal". Run it over the behavioral corpus (fixtures.json),
    score it, and FAIL if the score drops below the committed baseline (baseline.json) — that drop
    IS the drift signal. No LLM, no cost, same result every run.
  Tier 2 (--live, opt-in, needs an LLM + API budget): run the agent on each fixture goal and have an
    LLM judge score the output against the fixture's rubric. PARKED pending a spend decision — the
    runner and its injectable agent/judge seam live here and are tested; only the real LLM wiring is
    withheld so nothing spends unattended.

  python3 evals/run.py           # Tier 1: score the corpus, gate on drift vs baseline
  python3 evals/run.py --live    # + Tier 2 (prints the parked notice until an LLM is wired)
"""
import os, sys, json, pathlib, subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
HOOK = ROOT / "hooks" / "agrim_gate.sh"
FIXTURES = HERE / "fixtures.json"
BASELINE = HERE / "baseline.json"

# The marker the hook injects for each intent class (see hooks/agrim_gate.sh).
_MARKER = {"code": "CODE CHANGE", "ask": "READ-ONLY", "standard": "GOAL-BASED SDLC"}


def _hook_run(prompt):
    """Inject one prompt through the real hook, return its stdout (the JSON envelope).

    The evals measure the intent CLASSIFIER, which only runs in an adopted repo
    (per-repo scoping, 0.6) — so pin the global escape hatch to score it
    hermetically, independent of the runner's cwd."""
    return subprocess.run(["bash", str(HOOK)], input=json.dumps({"prompt": prompt}),
                          capture_output=True, text=True,
                          env={**os.environ, "SIGMA_GATE_GLOBAL": "1"}).stdout


def hook_directive(prompt, run=None):
    """The additionalContext the hook injects for a prompt. `run` is injectable for hermetic tests."""
    return json.loads((run or _hook_run)(prompt))["hookSpecificOutput"]["additionalContext"]


def load_fixtures():
    return json.loads(FIXTURES.read_text())


def run_tier1(fixtures, run=None):
    """Score the corpus against the deterministic hook. Returns (score 0..1, [misses])."""
    misses = []
    for fx in fixtures:
        ctx = hook_directive(fx["prompt"], run=run)
        if fx["expect"] == "standard":
            # base policy present, neither the code nor the read-only banner
            ok = "GOAL-BASED SDLC" in ctx and "CODE CHANGE" not in ctx and "READ-ONLY" not in ctx
        else:
            ok = _MARKER[fx["expect"]] in ctx
        if not ok:
            misses.append({"id": fx["id"], "prompt": fx["prompt"], "expect": fx["expect"]})
    return (len(fixtures) - len(misses)) / len(fixtures), misses


def run_tier2(fixtures, agent, judge):
    """Tier-2 shape, hermetic via injected callables: agent(prompt)->output, judge(output, rubric)->0..1.
    The real agent/judge are LLMs wired only under --live; tests inject fakes to exercise this seam."""
    return [{"id": fx["id"], "score": judge(agent(fx["prompt"]), fx.get("tier2_rubric", []))}
            for fx in fixtures]


def _parked(*_a, **_k):
    raise SystemExit("Tier-2 LLM judge is parked pending an API-budget decision — see evals/README.md")


def main(argv):
    fixtures = load_fixtures()
    score, misses = run_tier1(fixtures)
    floor = json.loads(BASELINE.read_text())["tier1_min"]
    print(f"Tier-1 behavioral gate: {score:.3f} over {len(fixtures)} fixtures (baseline {floor:.3f})")
    for m in misses:
        print(f"  DRIFT: {m['id']} — {m['prompt']!r} expected {m['expect']}")
    if "--live" in argv:
        run_tier2(fixtures, _parked, _parked)   # raises the parked notice (nothing spends unattended)
    if score < floor:
        print(f"\nQUALITY DRIFT: {score:.3f} < baseline {floor:.3f}. A discipline signal regressed — "
              "fix the classifier or justify + lower the baseline.")
        return 1
    tail = "" if score <= floor else f" (baseline can be raised to {score:.3f})"
    print("no drift." + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
