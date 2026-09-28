Slice 1 of 4 of the reviewed section-8 v2 design ("decomposition as a goal, not a step"). The architecture is settled by an independent APPROVE-WITH-CHANGES plan review; this issue is implementation only. Model: daily — implementation against a settled, reviewed spec.

## Context

Oversized goals (epics) picked by the loop should be detected deterministically and, depending on config, annotated or parked — never silently implemented as one giant goal. The pick-path never decomposes anything itself: the actual decomposition runs later as its own normal goal (the meta-goal filing branch ships in a later slice; NOT in scope here). This slice ships the classifier and the `log`/`park` rungs of the mode ladder only — zero filing code.

## Scope (8a)

- New `skills/agrim-loop/scripts/goal_size.py`: deterministic classifier, sibling of `predict.py` — ordered signals, first match wins, zero LLM, zero latency. v1 signals: body word/line count over thresholds; >=3 independent `##` sections; >=4 top-level checkboxes naming distinct deliverables; explicit Phase-1/Phase-2 structure. Returns `(flagged: bool, reason: str)`. Thresholds are module constants, documented in the tmpl explainer.
- `loop.py`: new `decompose-check <sdlc_dir> <issue>` verb; dispatch mirrors `precheck`'s (`load_config` / `get_source` / print). Behavior:
  - `goal_decompose.enabled` is not `True` -> print `OFF`
  - read title+body via `gh issue view --json title,body` (direct read, no mutation)
  - refuse-to-flag guards -> `PROCEED`: first line contains `sigma:decomposed-from=` (it IS a child; depth limit 1 by construction) or `sigma:decompose-of=` (it IS a meta-goal)
  - not flagged -> `PROCEED`
  - unrecognized `mode` string -> stderr warning, treat as `log` (must never fall off the branch chain and print `None` — output stays inside the `OFF | PARKED | PROCEED` vocabulary SKILL.md parses)
  - mode `log` -> action-log internal kind `decompose_check` `{verdict: "flagged", reason, mode}`; print `PROCEED (flagged: <reason>)` (same annotation precedent as `PROCEED (advisory)`)
  - mode `park` -> `_record(parked, "too large per goal_size (<reason>) — needs manual decomposition")`; print `PARKED ...`
  - mode `file` -> behaves as `park` in this slice (the file branch ships later; the operator opted INTO mutation, so degrading to the safe visible action is correct — hard-`OFF` would silently implement oversized goals, inverting their intent)
  - outer try/except -> `PROCEED` (precheck's fail-open idiom; nothing above the mode branches mutates anything)
- `actionlog.py`: new internal kind `decompose_check` with fields `{verdict, reason, mode}` (not an overload of `note`).
- `skills/agrim-init/templates/config.json.tmpl`: add `"goal_decompose": { "enabled": false, "mode": "log", "max_children": 8 }` plus a `_goal_decompose` explainer (SKILL-driven like precheck — `run_loop` never calls it; `log` -> classify + annotate only, zero mutation ever; `park` -> park oversized goals for a human to split; `file` -> park AND file one "Decompose #N" meta-goal; deterministic classifier; off by default; absent -> nothing runs).
- `SKILL.md`: one new step-3 bullet mirroring precheck's wording, placed AFTER precheck's bullet (a duplicate should park as a duplicate, not get decomposed).
- CHANGELOG entry under `## Unreleased`.

## Acceptance criteria (tests — hermetic, non-vacuous: simulate the break -> observe the exact predicted failure -> restore -> pass)

- classifier verdicts on fixture bodies, both directions (the full real corpus lands in the follow-up slice)
- `OFF` when disabled / key absent / malformed config
- `PROCEED` for unflagged goals
- first-line child/meta refusal — and NOT refused when the marker appears mid-body only (anchoring test)
- log-mode zero-mutation proof: a recording fake source asserts NO mutating verb (`issue edit/comment/close`, `create_dependency`, `append_to_body`) is ever invoked
- park-mode single-`_record` contract
- unrecognized-mode -> log behavior + stderr warning, never `None` output
- config-tmpl discoverability test pinning the new key (the existing discoverability test is gates-scoped and would NOT auto-catch this)
- feature invisible unless enabled; zero behavior change with default config

## Verification

```
python3 -m pytest tests/ -q --cov=skills --cov=hooks --cov-fail-under=85 && python3 evals/run.py
```

