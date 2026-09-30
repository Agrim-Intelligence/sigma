| Path | Function | Rule | Count | Gate | Risk |
|---|---|---|---:|---|---|
| .sdlc/research/292-benchmark.py | main | git-push | 1 | ungated | high |
| skills/agrim-define/scripts/define.py | _already_exists | git-push | 2 | ungated | high |
| skills/agrim-define/scripts/define.py | _label_create_argv | gh-label | 1 | ungated | medium |
| skills/agrim-define/scripts/define.py | _step_branch | git-push | 1 | ungated | high |
| skills/agrim-doctor/scripts/board_migrate.py | build_options_mutation | graphql-mutation | 2 | ungated | medium |
| skills/agrim-doctor/scripts/board_migrate.py | main | graphql-mutation | 1 | ungated | medium |
| skills/agrim-doctor/scripts/board_migrate.py | seed_ready | gh-project | 1 | ungated | medium |
| skills/agrim-dossier/scripts/dossier.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-init/scripts/board_layout.py | _create_field | graphql-mutation | 2 | ungated | medium |
| skills/agrim-init/scripts/board_layout.py | _view_update | graphql-mutation | 1 | ungated | medium |
| skills/agrim-init/scripts/board_layout.py | run_views | graphql-mutation | 1 | ungated | medium |
| skills/agrim-init/scripts/board_setup.py | _create_field | graphql-mutation | 1 | ungated | medium |
| skills/agrim-init/scripts/board_setup.py | _ensure_fields | graphql-mutation | 1 | ungated | medium |
| skills/agrim-init/scripts/board_setup.py | _link_and_workflows | graphql-mutation | 1 | ungated | medium |
| skills/agrim-init/scripts/board_setup.py | create | graphql-mutation | 2 | ungated | medium |
| skills/agrim-loop/scripts/blockers.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/coexist.py | backup_features | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | git-destructive | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/feature_classify.py | <module> | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/feature_labels.py | <module> | gh-api-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/feature_rebase.py | <module> | git-push | 3 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _aftermath | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _drop_worktree | fs-rmtree | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _drop_worktree | git-destructive | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _drop_worktree | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _pushed | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _rebase_feature | git-push | 3 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _replay_goals | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | own_losses | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/loop.py | _agent_marker_path | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | agent_end | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/mirror.py | <module> | git-push | 2 | ungated | high |
| skills/agrim-loop/scripts/mirror.py | main | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/pipeline.py | <module> | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/pipeline.py | main | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/promote.py | promote | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | teardown_worktree | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/sources.py | _apply_custom_fields | gh-project | 2 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _archive_card | graphql-mutation | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _create_field_mutation | graphql-mutation | 2 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_board | gh-project | 2 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_labels | gh-label | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_labels | git-push | 2 | discovery.source == "github"; board writes also project.enabled | high |
| skills/agrim-loop/scripts/sources.py | _ensure_priority_field | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_status_field | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_status_field | graphql-mutation | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _item_id | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _options_mutation | graphql-mutation | 2 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _run | gh-api-write | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _set_board_status | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _swap_labels | graphql-mutation | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _sync_backlog | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_board_phase | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_priority_field | gh-project | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_priority_label | gh-issue | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | append_to_body | gh-issue | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | complete | gh-issue | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | create_dependency | gh-issue | 3 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | create_dependency | gh-label | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | create_dependency | git-push | 1 | discovery.source == "github"; board writes also project.enabled | high |
| skills/agrim-loop/scripts/sources.py | ensure_labels | git-push | 1 | discovery.source == "github"; board writes also project.enabled | high |
| skills/agrim-loop/scripts/sources.py | ensure_labels_report | gh-label | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | ensure_labels_report | git-push | 1 | discovery.source == "github"; board writes also project.enabled | high |
| skills/agrim-loop/scripts/sources.py | mark_needs_triage | git-push | 1 | discovery.source == "github"; board writes also project.enabled | high |
| skills/agrim-loop/scripts/sources.py | note | gh-issue | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | release | gh-issue | 2 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | set_board_phase | graphql-mutation | 1 | discovery.source == "github"; board writes also project.enabled | medium |
| skills/agrim-loop/scripts/state.py | unsafe_goal_reason | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/sync.py | init | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/triage.py | _ensure_arbitrary_labels | gh-label | 1 | ungated | medium |
| skills/agrim-loop/scripts/triage.py | _ensure_arbitrary_labels | git-push | 2 | ungated | high |
| skills/agrim-loop/scripts/triage.py | _execute_action | gh-issue | 3 | ungated | medium |
| skills/agrim-loop/scripts/triage.py | enact | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/unpark.py | _append_block | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/unpark.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _open_pr_refusal | git-push | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | close_design | gh-pr | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | medium |
| skills/agrim-loop/scripts/work.py | finish | fs-rmtree | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | finish | git-destructive | 2 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | finish | git-push | 6 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | main | git-push | 2 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | merge | gh-pr | 2 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | medium |
| skills/agrim-loop/scripts/work.py | merge_design | gh-pr | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | medium |
| skills/agrim-loop/scripts/work.py | post_review | gh-pr | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | medium |
| skills/agrim-loop/scripts/work.py | pr | git-push | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | rebase | git-destructive | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | rebase | git-push | 3 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | start | git-push | 1 | work.enabled; merge paths also require fresh verify evidence and CLEAN PR | high |
| skills/agrim-rebase/scripts/rebase_brief.py | _remote_tip | git-push | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | attempt_rebase | git-destructive | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | push_branch | git-push | 3 | ungated | high |
| skills/agrim-rebase/scripts/verify_merge.py | ensure_landing_pr | gh-pr | 2 | human input() confirmation | medium |
| skills/agrim-rebase/scripts/verify_merge.py | merge_pr | gh-pr | 1 | human input() confirmation | medium |
| skills/agrim-scope/scripts/assign.py | _apply_assignment | gh-issue | 1 | ungated | medium |
| skills/agrim-setup/scripts/setup.py | ensure_core_labels | git-push | 1 | ungated | high |
| tools/leak_refs.py | <module> | git-destructive | 1 | ungated | high |
| tools/leak_refs.py | _validate_edits | gh-pr | 1 | ungated | medium |
| tools/onboarding_control.py | main | fs-rmtree | 1 | ungated | high |
| tools/onboarding_control.py | run_github | gh-issue | 1 | ungated | medium |
| tools/onboarding_control.py | run_github | gh-pr | 3 | ungated | medium |
| tools/readiness/write_surface.py | <module> | git-push | 1 | ungated | high |
