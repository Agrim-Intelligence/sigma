Blocked by #520

Slice 3 of 4 of the reviewed section-8 v2 design — `file` mode must not ship before the corpus slice has validated the classifier. Model: daily — the riskiest slice, but every step is specified by the reviewed plan.

## Scope (8c)

**`loop.py` `decompose-check`: implement the `file` branch.** Steps 1-4 sit inside an INNER try/except whose only exits are one `_record(parked)` attempt and, failing that, printing `PARKED <reason>` — there is NO path from "flagged in file mode" back to a bare `PROCEED`:

1. **Idempotency read:** `gh issue view <parent> --json comments`; any comment containing `sigma:decompose-filed` -> `_record(parked, "decomposition already filed — see comments")`, print `PARKED ...`. **The read itself failing fails CLOSED:** `_record(parked, "could not confirm whether a decomposition was already filed — check comments")` — never treat an unreadable timeline as "no marker" (that would duplicate the meta-issue), never fall through to the outer catch's `PROCEED`. Direct read of the one issue's own timeline — never a search-API query (eventually consistent).
2. **Create exactly ONE meta-issue** via `create_tracked_issue(goal=<parent>, area=<parent's area:* label, else config default>, why="oversized goal — needs decomposition before implementation", same_area=True, immediately_actionable=True, blocks_goal=False, priority=<parent's priority:* label, else default>, title="Decompose #<parent>: <parent title, truncated>", body=<template below>, extra_labels=["sdlc:decompose", "model:daily"], source=source)`. `report["issue"]` is `None` -> `_record(parked, "too large — failed to file decomposition goal: <warnings> — needs a human")`. `last_assignee_applied` is `False` -> the park detail must say "filed as #M but unassigned — a human must assign it before any loop can see it".
3. **Marker comment on the parent** (`source.note` is UNGUARDED — wrap the call): `"Too large to implement as one goal — decomposition filed as #M. <!-- sigma:decompose-filed=#M -->"`. Failure -> fold a warning into the park detail and proceed (the marker only matters if the park below then ALSO fails).
4. **Park the parent:** `_record(parked, "too large per goal_size (<reason>) — decomposition filed as #M<, plus any warnings>")`, print `PARKED ...`.

**Meta-goal body template** (module constant or `templates/decompose-goal.md.tmpl`). First line: `<!-- sigma:decompose-of=#N -->`, then, in substance:

- Header: decompose #N into independently implementable sub-issues; this goal creates ISSUES only — never implement any of #N's actual work here.
- Step 0, reconcile first (every check a DIRECT timeline read, never search): #N closed -> obsolete: comment, `loop.py verify`, `record done`, stop. #N's comments contain a "decomposed into ..." summary, or this goal's / a sibling decompose-goal's timeline shows children already created (each `track` call posts its narrative on the filing goal's timeline — that IS the child ledger) -> already done: same exit. Another OPEN `sdlc:decompose` issue targets #N (label-scoped list, then read each candidate's first line for `decompose-of=#N`) with a LOWER number -> defer to it: same exit. **Lower-number-wins is the tie-break** — without it two concurrent duplicates each see the other and both abort (mutual-abort deadlock); the lowest-numbered open decompose-goal always proceeds.
- Step 1 research #N fully; step 2 plan the split (2..max_children children, each independently implementable AND verifiable, distinct specific titles, dependency edges only where one child genuinely cannot start first; plan-review applies as normal — the independent reviewer judges the split itself).
- Step 3 implement = create each child in dependency order (blockers first so their numbers exist) via `handoff.py track <sdlc_dir> <THIS issue> --area <#N's area> --why "<one line>" --queue actionable --assignee same-area --blocks no --priority <#N's priority> --label model:<predicted tier> --title "<child title>" --body-file <tmp>`. Child body: FIRST line `<!-- sigma:decomposed-from=#N -->`; near the top a `Blocked by #<sibling>` line per real dependency; then content. Never `gh issue create` directly.
- Step 4 verify (outcome-check): every child appears in `gh issue list --label sdlc:goal --assignee <configured assignee> --state open`; any missing/unassigned -> fix or `record parked` saying exactly what failed. Then comment on #N: "decomposed into #A, #B, #C — see this goal for the plan." Run `loop.py verify` before `record done`.
- No code changes -> no worktree/branch/PR; skip `work.py`, record directly.

**`handoff.py`:** `track` gains `--body-file <path>` — verbatim file contents as `body=`; additive, nothing else changes.

**`SKILL.md`:** one sentence noting the file-mode outcome. **CHANGELOG** entry under `## Unreleased`.

## Acceptance criteria (tests — hermetic, non-vacuous)

- idempotency-hit: marker present -> park; no create call ever issued
- idempotency-read-fails -> STRICT park, never `PROCEED`, no create call (fail-closed pin)
- create-fail -> park with warnings; no marker attempt
- marker-fail -> still parks; detail carries the warning
- happy path -> exactly one create + one note + one `_record`, in that order (recording fake source pins the sequence)
- unassigned-surface: `last_assignee_applied=False` -> park detail says so
- inner-wrapper regression: break the wrapper in a scratch copy -> the test predicts and observes the bare-`PROCEED` -> restore -> pass
- `--body-file` round-trip + missing-file error
- full gate green

## Verification

```
python3 -m pytest tests/ -q --cov=skills --cov=hooks --cov-fail-under=85 && python3 evals/run.py
```

