# #895 slice 4b-3 controls (each guard broken once and seen red)

Gesture (the documented one): `$HOME/.sigma-venv312/bin/python -m pytest tests/test_work.py tests/test_gh_api.py -k "design or close_pr_gh"`.

With controls 1 and 2 applied together, 24 tests were red (21 in tests/test_work.py, 3 in tests/test_gh_api.py);
restored, all green.

1. `_design_pr_state` made to answer "MERGED" for an unreadable or unknown re-read (the old "no proof needed"
   posture): red, among them every `test_merge_and_close_design_never_count_a_degraded_re_read_as_proof[*]`
   (blank, spaces, null, empty-object, list, truncated, unknown-state, null-state, html, unreadable) and
   `test_merge_design_never_retries_an_ambiguous_merge`.
2. `gh_api.close_pr_gh` made to accept any 2xx body (the `state != "closed"` check disabled): 3
   `test_close_pr_gh_a_2xx_without_state_closed_is_never_success` cases red, plus
   `test_close_design_a_degraded_2xx_patch_reply_is_never_success[empty-object, still-open]`.
3. Ratchet: the baseline of work.py left at 5 while the tree is at 3 fails `test_no_stale_baseline_entry` (seen
   before the baseline was lowered); write-surface `check` failed with the two new `gh-api-write` rows missing
   until the inventory was updated.
