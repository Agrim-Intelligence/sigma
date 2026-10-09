# #895 slice 2c retro (P7 RETRO, advisory)

## What went to plan
- All 10 `gh issue list` sites moved to `gh_api.list_issues_gh` (REST first, one fallback, none in a cloud session).
- `read_issue`'s breaker/log/fallback body was factored into `_rest_first`, with the existing test_gh_api tests passing.
- Ratchet baseline lowered 84 -> 74. 27 deliberate-break controls were run red then green (895-controls.md).
- Diff: 18 files, +1404/-319, one PR, no out-of-scope writes.

## What review caught
- Plan-review 1: doctor `sdlc_dir` contradiction (breaker/log sharing needs a dir doctor may not have).
- Plan-review 2: empty output read as an empty list (an outage would look like an empty census); the fetch re-exec.
- Code review: the non-blocking cost wording was wrong; the breaker is shared across read_issue and list calls.
- Review-fix controls 26 and 27 cover the comment tail bound and the `#42` ref form.

## Residual debt
- `status.py:174` stays on `gh issue list` by design (out of scope).
- `_LIST_FETCH` is a module-level cache, and doctor reloads gh_api on every call.
- Assign on PR-heavy repos: REST /issues includes PRs, so the request ceiling is higher (cap 200 can need more pages).
- Census wording in docs/doctor should state what was measured, not "complete".

## Lessons
- Fake runners that return empty make fail-open tests pass for the wrong reason. Assert the REST argv was actually requested.
- Classify at the call seam. Do not infer failure from output shape.

## Honesty
- Cloud-session behaviour is unmeasured; only the fake-runner path was exercised.
- The full-suite result comes from the implementer only and has not been independently re-run.

grade: partial
