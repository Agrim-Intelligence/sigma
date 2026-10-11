# 1046 retro (advisory)

Grade: **achieved** (done_when met; pin quality is the soft spot).

## Intent vs shipped
- 24 prose-only cards, `kind: pinned`, written to `evals/skills/cards/`: achieved. Plan-review and release-check carry two pins; the plan-review ordering pin is carded.
- Real-tree wiring: 3 new pytests plus the prose-only and ordering tests in `tests/test_skill_smoke.py`: achieved.
- Bare `python3 evals/skills/smoke.py` re-run here: 43 skills, 0 findings, exit 0, 0.14s.
- Probe proof: tested via a tmp copy in pytest; the literal scratch-clone control is a PR-body item.

## Residual debt
- About 5 pins are weak (single generic phrase; a reword that keeps the meaning still goes red, one that drops it can stay green).
- Pin maintenance cost (D-5): two files per reworded sentence, still unmeasured.
- Budget and wall time deferred to slice 4.
- Process: the implement phase marker started before the plan-review verdict was recorded.
- Scope: a docstring edit in `evals/skills/smoke.py` falls outside the plan's "not edited" list. It is comment-only, but unplanned.
