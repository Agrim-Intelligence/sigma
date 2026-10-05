#!/usr/bin/env python3
"""Measure each Sigma phase agent's instruction bill and enforce down-only ceilings (#262).

The table measures what the phase is instructed to load, rather than the repository's entire
prompt corpus.  The explicit mapping is deliberately data, not a broad glob: an instruction-file
change is visible in review and an unrelated new skill cannot silently inflate an agent's bill.

`evals/phase_context_budget.json` is a measurement record.  A ceiling may equal the measurement or
be at most five percent above it (rounding room for small formatting changes), and the measurement
may never exceed that ceiling.  Those two directions make the record a down-only ratchet.

Usage:
    python3 evals/phase_context_budget.py          # table + gate
    python3 evals/phase_context_budget.py --table  # table only
    python3 evals/phase_context_budget.py --json   # current measurements, no writes
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import subprocess
import sys


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BUDGET_FILE = HERE / "phase_context_budget.json"
HELLO = ROOT / "examples" / "hello-sdlc"
MAX_HEADROOM = 0.05

# Each entry lists the skill and reference files its own procedure directs this agent to load.  The
# review phases additionally render the pack that `sigma-review` directs a fresh reviewer to read.
PHASES = {
    "orchestrator": {
        "files": (
            "skills/sigma-loop/SKILL.md",
            "skills/sigma-loop/references/selection.md",
            "skills/sigma-loop/references/picking.md",
            "skills/sigma-loop/references/running.md",
            "skills/sigma-loop/references/filing.md",
            "skills/sigma-loop/references/progress.md",
            "skills/sigma-loop/references/landing.md",
            "skills/sigma-loop/references/stopping.md",
        ),
    },
    "goal-slot": {
        "files": (
            "skills/sigma-goal/SKILL.md",
            "skills/sigma-goal/references/selection.md",
            "skills/sigma-loop/references/running.md",
        ),
    },
    "research": {"files": ("skills/sigma-research/SKILL.md", "skills/sigma-research/references/selection.md")},
    "plan": {"files": ("skills/sigma-plan/SKILL.md", "skills/sigma-plan/references/selection.md")},
    "plan-review": {
        "files": ("skills/sigma-plan-review/SKILL.md", "skills/sigma-plan-review/references/selection.md"),
        "brief": "plan-review",
    },
    "implement": {"files": ("skills/sigma-implement/SKILL.md", "skills/sigma-implement/references/selection.md")},
    "review-pre-pr": {
        "files": (
            "skills/sigma-review/SKILL.md",
            "skills/sigma-review/references/selection.md",
            "skills/sigma-review/references/axes.md",
        ),
        "brief": "code-review",
    },
    "review-post-pr": {
        "files": (
            "skills/sigma-review/SKILL.md",
            "skills/sigma-review/references/selection.md",
            "skills/sigma-review/references/axes.md",
        ),
        "brief": "pr-review",
    },
    "retro": {
        "files": ("skills/sigma-retro/SKILL.md", "skills/sigma-retro/references/selection.md"),
        "brief": "retro",
    },
}


def _load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _word_count(text: str) -> int:
    return len(text.split())


def _commit(root: pathlib.Path) -> str:
    """The exact tree identity the committed record was measured against, or an honest fallback."""
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def _review_brief(root: pathlib.Path, phase: str) -> str:
    """Render the real pack locally; never fetch an issue or depend on a live project."""
    review_context = _load_module("phase_context_review_context",
                                  root / "skills/sigma-loop/scripts/review_context.py")
    # Pass stable, repository-relative prose/pointers to the rendered payload.  Absolute checkout
    # paths would make a ceiling vary merely because a user cloned Sigma under a longer directory.
    goal = root / "examples/hello-sdlc/.sdlc/goals/0001-add-exclaim.md"
    return review_context.brief(str(root / "examples/hello-sdlc/.sdlc"), str(goal), phase,
                                artifact="examples/hello-sdlc/greeter.py",
                                repo_root=str(root / "examples/hello-sdlc"))


def measure_phase(name: str, root: pathlib.Path = ROOT) -> dict:
    """Resolve and measure one explicit phase payload, including its rendered review pack."""
    definition = PHASES[name]
    payloads = []
    labels = []
    for rel in definition["files"]:
        path = root / rel
        payloads.append(path.read_text(encoding="utf-8"))
        labels.append(rel)
    if phase := definition.get("brief"):
        payloads.append(_review_brief(root, phase))
        labels.append("rendered:review_context.py --for %s (examples/hello-sdlc/)" % phase)
    text = "\n\n".join(payloads)
    skill_structure = _load_module("phase_context_skill_structure", root / "evals/skill_structure.py")
    return {
        "files": labels,
        "words": _word_count(text),
        "est_tokens": skill_structure.estimate_tokens(text),
    }


def measure_all(root: pathlib.Path = ROOT) -> dict:
    return {name: measure_phase(name, root) for name in PHASES}


def load_budget(path: pathlib.Path = BUDGET_FILE) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def findings(root: pathlib.Path = ROOT, budget_path: pathlib.Path = BUDGET_FILE) -> list[str]:
    """Return every stale, expanded, or malformed ceiling.  Empty is a passing gate."""
    try:
        budget = load_budget(budget_path)
    except (OSError, ValueError) as exc:
        return ["cannot read budget record %s: %s" % (budget_path, exc)]
    measured = measure_all(root)
    recorded = budget.get("phases")
    if not isinstance(recorded, dict):
        return ["budget record has no phases object"]
    out = []
    if set(recorded) != set(PHASES):
        out.append("budget phases differ: expected %s, got %s" % (sorted(PHASES), sorted(recorded)))
    for name, row in measured.items():
        ceiling = (recorded.get(name) or {}).get("ceiling")
        if not isinstance(ceiling, dict):
            out.append("%s: missing ceiling" % name)
            continue
        for metric in ("words", "est_tokens"):
            limit = ceiling.get(metric)
            if not isinstance(limit, int) or limit < 0:
                out.append("%s: ceiling %s must be a non-negative integer" % (name, metric))
                continue
            actual = row[metric]
            if actual > limit:
                out.append("%s: measured %s %s exceeds ceiling %s" % (name, metric, actual, limit))
            if limit > actual * (1 + MAX_HEADROOM):
                out.append("%s: ceiling %s %s is more than 5%% above measured %s" %
                           (name, metric, limit, actual))
    return out


def render_table(measured: dict, budget: dict | None) -> str:
    """Human-readable, committed-record-aware output; its figures make no performance claim."""
    date = (budget or {}).get("measured_at", "unrecorded")
    commit = (budget or {}).get("commit", "unrecorded")
    lines = ["phase context budget", "measurement date: %s" % date,
             "measurement commit: %s" % commit,
             "current commit: %s" % _commit(ROOT),
             "", "phase              words  approx-tokens  files"]
    for name, row in measured.items():
        lines.append("%-18s %6d %14d  %d" % (name, row["words"], row["est_tokens"], len(row["files"])))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", action="store_true", help="report only; do not enforce ceilings")
    parser.add_argument("--json", action="store_true", help="print current measurements as JSON")
    args = parser.parse_args(argv)
    measured = measure_all(ROOT)
    budget = None
    try:
        budget = load_budget(BUDGET_FILE)
    except (OSError, ValueError):
        pass
    if args.json:
        print(json.dumps({"measured_at": dt.date.today().isoformat(), "commit": _commit(ROOT),
                          "phases": measured}, indent=2, sort_keys=True))
        return 0
    print(render_table(measured, budget))
    if args.table:
        return 0
    problems = findings(ROOT, BUDGET_FILE)
    if problems:
        print("\nphase context budget: FAILED")
        print("\n".join("- " + problem for problem in problems))
        return 1
    print("\nphase context budget: clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
