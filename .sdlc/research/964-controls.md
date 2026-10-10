# #964 AC-4 controls (implementer, P5)

Host: Darwin (macOS), run inside Claude Code (CLAUDE_CODE_SESSION_ID set, env left as is). Worktree .sdlc/work/964.
Six-id command (cwd worktree): python3 -m pytest tests/test_preflight.py::test_ssh_alias_remote_is_resolved_through_ssh_G tests/test_preflight.py::test_unresolvable_ssh_alias_is_cannot_verify_never_a_login tests/test_preflight.py::test_alias_resolving_to_gitlab_is_non_github tests/test_model_predict.py::test_codex_model_override_routes_goal_and_step_without_changing_claude tests/test_loop.py::test_codex_only_model_override_reaches_pick_time_without_changing_claude -q -p no:cacheprovider

1. Baseline, before edits: 5 failed, 1 passed in 1.90s
   (the gitlab alias test passes unfixed; it was the sixth id in the plan but is not red on its own)
2. After edits: 6 passed in 2.71s; tests/test_preflight.py + tests/test_model_predict.py: 187 passed; test_loop -k "codex_only_model_override or codex_raw_token or invalid_codex_mapping": 4 passed
3. Control: git diff -- tests > 964.patch; git checkout -- tests; six ids -> 5 failed, 1 passed in 1.90s (RED)
   git apply 964.patch; six ids -> 6 passed in 2.71s (GREEN)
   git diff --stat identical before and after: 3 files, 17 insertions(+), 3 deletions(-)

## Correction (orchestrator, P5): the implementer's id list wrongly held test_alias_resolving_to_gitlab_is_non_github (not one of the six) and omitted test_ghe_fqdn_is_still_checked_as_itself.
Re-ran on macOS inside Claude Code, env as is, with the issue's actual six (ssh_G, unresolvable x2 params, ghe_fqdn, model_predict codex, loop codex-only):
- fix reverted (git checkout -- tests): 6 failed in 2.43s (RED)
- patch re-applied: 6 passed in 4.28s (GREEN); git diff --stat 3 files, 17 insertions(+), 3 deletions(-)
