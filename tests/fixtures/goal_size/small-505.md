Found live during the 2026-08-07 overnight dogfood drain (#488's fix), reproduced directly.

`sources.py::complete()` runs `gh issue close <goal> --repo ... --comment "Completed by the Sigma SDLC loop."` as part of recording a goal `done`. When the merged PR's body/commit contains a GitHub auto-close keyword (`Fixes #N` / `Closes #N` — the norm for this codebase's own PRs), GitHub closes the issue automatically at merge time, BEFORE `complete()`'s own `gh issue close` call runs. `gh issue close` on an already-closed issue prints `! Issue ... is already closed` and exits 0 — but the `--comment` text is silently never posted.

**Impact:** since "Fixes #N" is this repo's own normal PR convention (used throughout the whole arc), this likely drops the completion comment on MOST loop-driven goals, not an edge case. The comment is meant to be part of the durable audit trail on the issue — losing it silently is the same *class* of concern (durable trace vanishing under a code path that "succeeds" without doing what it claims) that this arc's other hardening work has been fixing elsewhere.

**Fix direction (not yet designed):** `complete()` needs to detect the "already closed" case (exit 0 but the specific stderr text, or check the issue's state first) and post the comment via a separate `gh issue comment` call when close-with-comment silently skipped it — rather than assuming `gh issue close --comment` always posts.

**Severity:** not data-loss, not unsafe (fail-open in the safe direction — nothing crashes, nothing mis-records), but a real, common, silent gap in the audit trail. Not urgent enough for an overnight unattended fix without its own review, but should not sit unfiled.

model:bulk — likely a small, mechanical fix (detect the case, add a fallback comment call) once someone looks at `complete()`'s exact current code, but needs its own quick verification of `gh issue close`'s exact exit code / stderr shape before implementing blind.
