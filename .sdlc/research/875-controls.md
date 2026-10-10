# #875 controls: each new test seen red with the shared fake deliberately broken

Gesture (CONTRIBUTING.md form, env scrubbed of CLAUDE*/ANTHROPIC* variables):

    python3 -m pytest tests/test_public_bootstrap_control.py -k "pr_comment or find_evidence" -q -p no:cacheprovider

New tests (node ids, all in `tests/test_public_bootstrap_control.py`):

- `test_pr_comment_prints_a_parseable_url_with_the_stored_integer_id`
- `test_pr_comment_ids_are_unique_and_increasing_across_prs`
- `test_pr_comments_paged_read_honours_page_and_per_page`
- `test_find_evidence_marker_pages_to_termination_against_the_fake` (extra: drives the real
  `work._find_evidence_marker` against the fake with 250 comments, so paging must terminate)

Method: the tests were first run with the fake unedited (all 4 failed: `pr comment` printed nothing
and the comments endpoint logged `unmodeled api endpoint`). After the edit they passed (4 passed).
Each break below was applied to a copy of the edited file, the gesture run, then the good file was
restored byte for byte and `git diff --stat` compared with the pre-break stat (identical:
1 file changed, 119 insertions(+), 1 deletion(-)). The control patch/backup lived outside the repo.

| Break | Result |
|---|---|
| (i) delete the `print(...)` of the comment URL | 2 failed (`..prints_a_parseable_url..`, `..ids_are_unique..`), 2 passed |
| (ii) paged read ignores `page`/`per_page` (`emit(rows, ...)`) | 2 failed (`..paged_read_honours..`, `..find_evidence_marker_pages..`), 2 passed; the latter fails with `ValueError: review comment history exceeds the bounded recovery scan` |
| (iii) id derived from the one PR's comments, not the global max | 1 failed (`..ids_are_unique..`), 3 passed |
| restored | 4 passed; `git diff --stat` equals the pre-break stat |

Note: break (iii) is not caught by the URL or paging tests by design; only the cross-PR uniqueness
test sees it.

## Control: the comments READ branch must not swallow a field POST (review send-back)

Test: `test_issue_comments_read_does_not_swallow_a_field_post` in
`tests/test_public_bootstrap_control.py`. Gesture: `gh api repos/<repo>/issues/101/comments -f body=x`
(the form `work.py` uses for the closed-by-merge audit note: POST inferred by `-f`, no `-X`).

- RED against the previous fake (read branch `method in ("", "GET")`): 1 failed; the call returned
  `[]` with exit 0 and logged nothing unhandled, shadowing the drills overlay's POST handler.
- Fix: the read branch now applies only when `method == "GET"` or (`method == ""` and no `-f`/`-F`
  fields), mirroring real `gh api`, which switches to POST when fields are given.
- GREEN: the test passes (nonzero exit, exactly one unhandled-log line, as before #875);
  `test_public_bootstrap_control.py` + `test_onboarding_control.py` + `test_readiness_blast_radius.py`
  = 127 passed.
