# #895 slice 3a controls (part 1: Task A gh_api write core, Task C wrappers/shim)

Gesture: `env -i HOME=$HOME PATH=/opt/homebrew/bin:/usr/bin:/bin $HOME/.sigma-venv312/bin/python -m pytest <ids> -q -p no:cacheprovider` from the worktree. Each break is applied, the ids run (red), reverted, ids run again (green).
Initial red-before-implement: step 1 (3 policy tests failed on stub), step 2 (5 failed on stub), step 3 (3 failed), steps 4-5 (87 failed), step 6 sources wrappers (7 failed with the wrapper block removed; restored).

### C1: server_5xx non-idempotent flag -> True
- break: skills/sigma-loop/scripts/gh_api.py: '("server_5xx", "HTTP 500-599: the write may have committed, so idempot' -> '("server_5xx", "HTTP 500-599: the write may have committed, so idempot'
- red: 3 failed, 223 passed in 1.26s
  - FAILED tests/test_gh_api.py::test_write_policy_table - assert (True, True) ==...
  - FAILED tests/test_gh_api.py::test_comment_5xx_never_falls_back - AssertionErr...
  - FAILED tests/test_gh_api.py::test_create_5xx_never_falls_back - AssertionErro...
- reverted, green: 226 passed in 1.10s

### C3: transport_ambiguous idempotent flag -> True
- break: skills/sigma-loop/scripts/gh_api.py: '("transport_ambiguous", "timeout / connection failure after the reques' -> '("transport_ambiguous", "timeout / connection failure after the reques'
- red: 7 failed, 219 passed in 1.31s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: transp...
  - FAILED tests/test_gh_api.py::test_real_transport_shapes_are_no_fallback_for_every_write
  - FAILED tests/test_gh_api.py::test_transport_timeout_never_falls_back_for_any_write[add_labels]
  - FAILED tests/test_gh_api.py::test_transport_timeout_never_falls_back_for_any_write[assign]
  - FAILED tests/test_gh_api.py::test_transport_timeout_never_falls_back_for_any_write[close]
  - FAILED tests/test_gh_api.py::test_transport_timeout_never_falls_back_for_any_write[edit]
- reverted, green: 226 passed in 1.15s

### C4: remove _is_secondary (secondary treated as primary)
- break: skills/sigma-loop/scripts/gh_api.py: 'def _is_secondary(text):\n    return bool(_SECONDARY_RE.search(text or ' -> 'def _is_secondary(text):\n    return False'
- red: 9 failed, 217 passed in 1.40s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: assert...
  - FAILED tests/test_gh_api.py::test_secondary_rate_limit_never_falls_back - Ass...
  - FAILED tests/test_gh_api.py::test_secondary_rate_limit_never_falls_back_per_op[add_labels]
  - FAILED tests/test_gh_api.py::test_secondary_rate_limit_never_falls_back_per_op[assign]
  - FAILED tests/test_gh_api.py::test_secondary_rate_limit_never_falls_back_per_op[close]
  - FAILED tests/test_gh_api.py::test_secondary_rate_limit_never_falls_back_per_op[comment]
- reverted, green: 226 passed in 1.04s

### C5: invalid_422 flags -> True/True
- break: skills/sigma-loop/scripts/gh_api.py: '("invalid_422", "HTTP 422 (also a dropped assignee, a team slug)", Fal' -> '("invalid_422", "HTTP 422 (also a dropped assignee, a team slug)", Tru'
- red: 8 failed, 218 passed in 1.24s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: invali...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_422[add_labels] - AssertionE...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_422[assign] - AssertionError...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_422[close] - AssertionError:...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_422[comment] - AssertionErro...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_422[create] - AssertionError...
- reverted, green: 226 passed in 1.14s

### C6: not_found_404 flags -> True/True
- break: skills/sigma-loop/scripts/gh_api.py: '("not_found_404", "HTTP 404", False, False),\n' -> '("not_found_404", "HTTP 404", True, True),\n'
- red: 8 failed, 218 passed in 1.30s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: not_fo...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_404[add_labels] - AssertionE...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_404[assign] - AssertionError...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_404[close] - AssertionError:...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_404[comment] - AssertionErro...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_404[create] - AssertionError...
- reverted, green: 226 passed in 1.09s

### C7: permission_403 flags -> True/True
- break: skills/sigma-loop/scripts/gh_api.py: '("permission_403", "HTTP 403 that is not a rate limit", False, False),' -> '("permission_403", "HTTP 403 that is not a rate limit", True, True),\n'
- red: 9 failed, 217 passed in 1.36s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: permis...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_permission_403[add_labels]
  - FAILED tests/test_gh_api.py::test_no_fallback_on_permission_403[assign] - Ass...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_permission_403[close] - Asse...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_permission_403[comment] - As...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_permission_403[create] - Ass...
- reverted, green: 226 passed in 1.15s

### C8: proxy_block flags -> True/True
- break: skills/sigma-loop/scripts/gh_api.py: '("proxy_block", "cloud proxy / GraphQL-unavailable text", False, False' -> '("proxy_block", "cloud proxy / GraphQL-unavailable text", True, True),'
- red: 8 failed, 218 passed in 1.37s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: proxy_...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_proxy_block[add_labels] - As...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_proxy_block[assign] - Assert...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_proxy_block[close] - Asserti...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_proxy_block[comment] - Asser...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_proxy_block[create] - Assert...
- reverted, green: 226 passed in 1.16s

### C9: auth_401 flags -> True/True
- break: skills/sigma-loop/scripts/gh_api.py: '("auth_401", "HTTP 401", False, False),\n' -> '("auth_401", "HTTP 401", True, True),\n'
- red: 8 failed, 218 passed in 1.40s
  - FAILED tests/test_gh_api.py::test_write_policy_table - AssertionError: auth_401
  - FAILED tests/test_gh_api.py::test_no_fallback_on_401[add_labels] - AssertionE...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_401[assign] - AssertionError...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_401[close] - AssertionError:...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_401[comment] - AssertionErro...
  - FAILED tests/test_gh_api.py::test_no_fallback_on_401[create] - AssertionError...
- reverted, green: 226 passed in 1.18s

### C10: primary_rate_limit non-idempotent flag -> False
- break: skills/sigma-loop/scripts/gh_api.py: '     True, True),\n    ("secondary' -> '     True, False),\n    ("secondary'
- red: 4 failed, 222 passed in 1.17s
  - FAILED tests/test_gh_api.py::test_write_policy_table - assert (True, False) =...
  - FAILED tests/test_gh_api.py::test_primary_rate_limit_falls_back_for_every_write[comment]
  - FAILED tests/test_gh_api.py::test_primary_rate_limit_falls_back_for_every_write[create]
  - FAILED tests/test_gh_api.py::test_primary_429_create_falls_back_once - Assert...
- reverted, green: 226 passed in 1.05s

### C12: retry the fallback once more (two fallback attempts)
- break: skills/sigma-loop/scripts/gh_api.py: '        return fallback()\n\n\ndef _log_write' -> '        try:\n            return fallback()\n        except Exception:\n '
- red: 2 failed, 224 passed in 1.15s
  - FAILED tests/test_gh_api.py::test_exactly_one_fallback_then_propagate - Asser...
  - FAILED tests/test_gh_api.py::test_fallback_failure_propagates_as_gh_api_error_without_retry
- reverted, green: 226 passed in 1.68s

### C13: _rest_write touches the read breaker
- break: skills/sigma-loop/scripts/gh_api.py: '        env = os.environ if env is None else env\n        ok = graphql_' -> '        env = os.environ if env is None else env\n        _save(sdlc_di'
- red: 1 failed, 225 passed in 2.03s
  - FAILED tests/test_gh_api.py::test_write_never_touches_read_breaker - Assertio...
- reverted, green: 226 passed in 2.37s

### C11: skip the graphql_available check in _rest_write (cloud session)
- break: skills/sigma-loop/scripts/gh_api.py: '        env = os.environ if env is None else env\n        ok = graphql_' -> '        env = os.environ if env is None else env\n        ok = True'
- red: 9 failed, 217 passed in 1.60s
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back - AssertionE...
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back_per_op[add_labels]
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back_per_op[assign]
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back_per_op[close]
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back_per_op[comment]
  - FAILED tests/test_gh_api.py::test_cloud_session_never_falls_back_per_op[create]
- reverted, green: 226 passed in 1.15s

### C14: _write_case classifies via str(exc) (hint falls back to the whole message)
- break: skills/sigma-loop/scripts/gh_api.py: '    h = getattr(exc, "hint", None)\n    return h if isinstance(h, str) ' -> '    return str(exc)'
- red: 3 failed, 223 passed in 1.38s
  - FAILED tests/test_gh_api.py::test_label_exists_404_is_false_other_failures_raise
  - FAILED tests/test_gh_api.py::test_body_wording_does_not_drive_write_classification
  - FAILED tests/test_gh_api.py::test_remove_label_matches_structured_404_only - ...
- reverted, green: 226 passed in 1.19s

### C15a: remove layer-1 guard in add_labels
- break: skills/sigma-loop/scripts/gh_api.py: '    _refuse_feature_labels(labels, feature_labels_exist)\n    args = ["' -> '    args = ["api", _endpoint(repo, "issues/%d/labels"'
- red: 1 failed, 225 passed in 1.17s
  - FAILED tests/test_gh_api.py::test_add_labels_refuses_feature_label_before_any_call
- reverted, green: 226 passed in 1.20s

### C15b: remove layer-1 guard in create_issue
- break: skills/sigma-loop/scripts/gh_api.py: '    _refuse_feature_labels(labels, feature_labels_exist)\n    args = ["' -> '    args = ["api", _endpoint(repo, "issues"), "--method"'
- red: 1 failed, 225 passed in 1.22s
  - FAILED tests/test_gh_api.py::test_create_issue_refuses_feature_label_before_any_call
- reverted, green: 226 passed in 1.13s

### C16a: layer 2 bypassed in _issue_create (verified=True) -- layer 1 is then also bypassed by feature_labels_exist=True
- break: skills/sigma-loop/scripts/sources.py: '        verified = self._require_feature_labels(labels)\n        return' -> '        verified = True\n        return gh_api.create_issue'
- red: 2 failed, 1 passed in 0.47s
  - FAILED tests/test_sources.py::test_issue_create_refuses_absent_feature_label_with_zero_writes
  - FAILED tests/test_sources.py::test_feature_label_lookup_failure_fails_closed
- reverted, green: 3 passed in 0.14s

### C16b: layer 2 bypassed in _issue_add_labels
- break: skills/sigma-loop/scripts/sources.py: '        verified = self._require_feature_labels(labels)\n        return' -> '        verified = True\n        return gh_api.add_labels'
- red: 2 failed, 1 passed in 0.22s
  - FAILED tests/test_sources.py::test_issue_add_labels_refuses_absent_feature_label_with_zero_writes
  - FAILED tests/test_sources.py::test_feature_label_lookup_failure_fails_closed
- reverted, green: 3 passed in 0.10s

### C17: remove_label: restore blanket '404' in str(exc)
- break: skills/sigma-loop/scripts/gh_api.py: '    except GhApiError as exc:\n        if _hint_404(exc):\n            r' -> '    except GhApiError as exc:\n        if "404" in str(exc):\n          '
- red: 1 failed, 225 passed in 1.29s
  - FAILED tests/test_gh_api.py::test_remove_label_matches_structured_404_only - ...
- reverted, green: 226 passed in 1.12s

### C18a: add_assignees: drop the response-login check
- break: skills/sigma-loop/scripts/gh_api.py: '        if missing:\n            raise GhApiError("GitHub did not assig' -> '        if False:\n            raise GhApiError("GitHub did not assign'
- red: 1 failed, 225 passed in 1.16s
  - FAILED tests/test_gh_api.py::test_add_assignees_dropped_login_is_invalid - Fa...
- reverted, green: 226 passed in 1.02s

### C18b: add_assignees: drop @me resolution
- break: skills/sigma-loop/scripts/gh_api.py: '            if name.lower() == "me":' -> '            if False:'
- red: 3 failed, 223 passed in 1.27s
  - FAILED tests/test_gh_api.py::test_add_assignees_resolves_me - gh_api.GhApiErr...
  - FAILED tests/test_gh_api.py::test_add_assignees_get_user_403_raises_without_post_or_fallback
  - FAILED tests/test_gh_api.py::test_add_assignees_get_user_5xx_follows_the_idempotent_table
- reverted, green: 226 passed in 1.17s

### C18c: add_assignees: drop the team-slug refusal
- break: skills/sigma-loop/scripts/gh_api.py: '        if "/" in a:\n            raise GhApiError("gh_api.add_assignee' -> '        if False:\n            raise GhApiError("gh_api.add_assignees'
- red: 1 failed, 225 passed in 1.28s
  - FAILED tests/test_gh_api.py::test_add_assignees_refuses_team_slug - Assertion...
- reverted, green: 226 passed in 1.37s

### C19: add_assignees: fallback gets a stripped/resolved name instead of the original @me
- break: skills/sigma-loop/scripts/gh_api.py: '        fb += ["--add-assignee", a]' -> '        fb += ["--add-assignee", a.lstrip("@")]'
- red: 2 failed, 224 passed in 1.22s
  - FAILED tests/test_gh_api.py::test_add_assignees_fallback_receives_original_me
  - FAILED tests/test_gh_api.py::test_add_assignees_get_user_5xx_follows_the_idempotent_table
- reverted, green: 226 passed in 1.19s

### C23-part1: shim vacuity: comment_issue posts to a wrong path
- break: skills/sigma-loop/scripts/gh_api.py: '"issues/%d/comments" % number), "--method", "POST",\n                  ' -> '"issues/%d/commentz" % number), "--method", "POST",\n                  '
- red: 1 failed in 0.23s
  - FAILED tests/test_sources.py::test_issue_write_wrappers_go_rest_with_the_sources_repo_and_record_legacy_calls
- reverted, green: 1 passed in 0.13s

## Not yet run (later parts): C2 (handoff), C16 handoff id, C20-C22, C24, C23 on dossier/complete.
## Known red after part 1 (expected, plan step 15): tests/test_issue_creation_boundary.py (2 tests) -- gh_api.create_issue's own `["issue","create"...]` fallback literal.

### C2: server_5xx non-idempotent flag -> True (create falls back on 5xx)
- break: skills/sigma-loop/scripts/gh_api.py: '("server_5xx", "HTTP 500-599: the write may have committed, so idempot' -> '("server_5xx", "HTTP 500-599: the write may have committed, so idempot'
- red: 4 failed, 223 passed in 0.95s
  - FAILED tests/test_gh_api.py::test_write_policy_table - assert (True, True) ==...
  - FAILED tests/test_gh_api.py::test_comment_5xx_never_falls_back - AssertionErr...
  - FAILED tests/test_gh_api.py::test_create_5xx_never_falls_back - AssertionErro...
  - FAILED tests/test_handoff.py::test_create_dependency_5xx_makes_exactly_one_create_call
- reverted, green: 227 passed in 0.93s

### C16-handoff: layer 2 bypassed in _issue_create (verified=True)
- break: skills/sigma-loop/scripts/sources.py: '        verified = self._require_feature_labels(labels)\n        return' -> '        verified = True\n        return gh_api.create_issue'
- red: 1 failed in 0.11s
  - FAILED tests/test_handoff.py::test_feature_label_create_refused_without_rest_write
- reverted, green: 1 passed in 0.08s

### C20: _write_priority_label: removes before the add
- break: skills/sigma-loop/scripts/sources.py: '        self._issue_add_labels(n, [self.priority_prefix + canon])\n    ' -> '        for name in stale:\n            self._issue_remove_label(n, nam'
- red: 1 failed in 0.10s
  - FAILED tests/test_github_project.py::test_priority_label_adds_before_removes
- reverted, green: 1 passed in 0.08s

### C23a: shim vacuity: dossier comment posts to a wrong path (_issue_comment path)
- break: skills/sigma-loop/scripts/gh_api.py: '"issues/%d/comments" % number' -> '"issues/%d/commentz" % number'
- red: 5 failed, 87 passed in 3.90s
  - FAILED tests/test_dossier.py::test_file_posts_the_same_block_as_a_comment - a...
  - FAILED tests/test_dossier.py::test_file_comment_goes_rest_and_a_failing_comment_is_swallowed
  - FAILED tests/test_blockers.py::test_a_routed_blocker_gets_membership_and_a_comment_naming_the_goal
  - FAILED tests/test_blockers.py::test_routing_survives_a_ledger_that_is_switched_off
  - FAILED tests/test_blockers.py::test_the_routing_comment_is_a_rest_post_and_its_failure_is_swallowed
- reverted, green: 92 passed in 3.78s

### C23b: shim vacuity: complete()'s comment path wrong
- break: skills/sigma-loop/scripts/gh_api.py: '"issues/%d/comments" % number' -> '"issues/%d/commentz" % number'
- red: 1 failed in 0.12s
  - FAILED tests/test_sources.py::test_complete_comments_then_closes - assert (0 ...
- reverted, green: 1 passed in 0.08s

## Part 2 (Task D) notes
- Controls first run on C20/C23 were discarded: a stale .pyc (break and revert inside one mtime second) made the "green" run fail, and C23's first break hit the READ endpoint. The harness now sleeps >1s around each write and sets PYTHONDONTWRITEBYTECODE; C23 targets the second occurrence (the POST).
- C21 (boundary tests) and C22 (ratchet baseline) belong to Task E (tests do not exist yet): NOT run. Known red until then: test_issue_creation_boundary.py (2), test_no_direct_gh.py.
- Extra files touched in the step-14 sweep: tests/test_feature_stamp.py, tests/test_loop.py, tests/test_define.py (in the plan's list); tests/test_label_create_no_force.py, tests/test_sdlc_scope_integration.py (run only, unchanged).

### C21a: add a second caller of gh_api.create_issue in skills (assign.py)
- break: skills/sigma-scope/scripts/assign.py
- red: 1 failed in 0.60s
  - FAILED tests/test_issue_creation_boundary.py::test_only_create_dependency_calls_create_issue
- reverted, green: 1 passed in 1.15s

### C21b: drop the gh_api.create_issue allowlist entry
- break: tests/test_issue_creation_boundary.py
- red: 2 failed in 1.66s
  - FAILED tests/test_issue_creation_boundary.py::test_gh_api_create_issue_fallback_argv_is_allowlisted
  - FAILED tests/test_issue_creation_boundary.py::test_skills_and_hooks_only_open_issues_through_create_dependency
- reverted, green: 2 passed in 2.17s

### C22a: BASELINE: raise sources.py entry 20 -> 21 (stale/under arm)
- break: tests/test_no_direct_gh.py
- red: 1 failed, 8 passed in 1.83s
  - FAILED tests/test_no_direct_gh.py::test_no_stale_baseline_entry - AssertionEr...
- reverted, green: 9 passed in 1.91s

### C22b: BASELINE: leave a removed-site entry (dossier.py: 1) in place
- break: tests/test_no_direct_gh.py
- red: 1 failed, 8 passed in 2.00s
  - FAILED tests/test_no_direct_gh.py::test_no_stale_baseline_entry - AssertionEr...
- reverted, green: 9 passed in 1.84s

### C22c: BASELINE: lower sources.py entry 20 -> 19 (over arm)
- break: tests/test_no_direct_gh.py
- red: 1 failed, 8 passed in 1.93s
  - FAILED tests/test_no_direct_gh.py::test_no_baseline_file_gained_sites - Asser...
- reverted, green: 9 passed in 2.32s

### C24: rename a case id in docs/cloud-sessions.md
- break: docs/cloud-sessions.md
- red: 1 failed in 0.17s
  - FAILED tests/test_gh_api.py::test_cloud_sessions_doc_lists_every_write_policy_case
- reverted, green: 1 passed in 0.14s

### C22d: write_surface check with an unrecorded issue-comment literal (documented gesture: check . docs/launch/write-surface.json)
- break: appended `source._run(["issue","comment",...])` in a function of skills/sigma-scope/scripts/assign.py (tracked file)
- red: exit 1 (output above: "new write site ... gh-issue")
- reverted, green: exit 0, no output
