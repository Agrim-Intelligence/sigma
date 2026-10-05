Found live during the 2026-08-07 overnight dogfood drain — confirmed across all three goals worked that night (#475, #476, #488): every one is `state: CLOSED` with `sdlc:goal` + `sdlc:in-progress` BOTH still present.

`grep -n 'in_progress_label' skills/sigma-loop/scripts/sources.py` shows exactly one hit outside its own definition/config-read: `sources.py:296`, an `--add-label`. There is no corresponding `--remove-label self.in_progress_label` anywhere in the file — not in the done/`complete()` path, not in `_offboard()`'s park path.

**Impact is cosmetic, not unsafe:** a DONE goal closes the issue, and `next_pending()` filters on `state: open`, so a stale `sdlc:in-progress` label on a closed issue can never cause a re-pick. A PARKED goal's `sdlc:goal` removal (which `_offboard()` does do) is what actually excludes it from `next_pending()` — the stale `sdlc:in-progress` label sitting alongside `sdlc:parked` is misleading to a human glancing at the issue (looks "in progress" when it's actually parked awaiting a human), but not functionally broken.

**Fix direction:** wherever the done-path (`complete()`) and `_offboard()` currently add/remove their own labels, also remove `in_progress_label` — mirrors how `_offboard()` already removes `goal_label` explicitly rather than leaving it to chance.

**Severity:** P3, hygiene only — no data loss, no re-pick risk, no audit-trail loss (unlike #505, a related-looking but more serious finding from the same drain). Bundle with #505 if convenient (same drain, same-ish area of `sources.py`/`complete()`), or fix independently.

model:bulk — small, mechanical, once the exact done-path label-mutation call sites are identified.
