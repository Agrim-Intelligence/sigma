# Write-surface inventory

The `check` command ratchets tracked Python and shell write sites. Control: in a temporary tracked shell file add `gh label delete legacy`, run `python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json`, and see it fail as a new `gh-label` site; remove the line before the green run.

| Path | Function | Rule | Count | Gate | Risk |
|---|---|---|---:|---|---|
| hooks/gate_state.py | _open_child | fs-write | 1 | ungated | medium |
| hooks/gate_state.py | _prune | fs-remove | 1 | ungated | high |
| hooks/gate_state.py | _stripe_lock | fs-remove | 2 | ungated | high |
| hooks/gate_state.py | _stripe_lock | fs-write | 1 | ungated | medium |
| hooks/research_capture.py | main | fs-write | 2 | ungated | medium |
| hooks/time_track.py | handle | fs-write | 2 | ungated | medium |
| skills/agrim-define/scripts/define.py | _step_registry | fs-write | 1 | ungated | medium |
| skills/agrim-define/scripts/define.py | main | fs-write | 1 | ungated | medium |
| skills/agrim-doctor/scripts/board_migrate.py | seed_ready | gh-project | 1 | ungated | medium |
| skills/agrim-doctor/scripts/migrate.py | _write | fs-remove | 2 | ungated | high |
| skills/agrim-dossier/scripts/dossier.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-init/scripts/init_flow.py | _write_json | fs-write | 1 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_codex | fs-remove | 2 | ungated | high |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_cursor_rules | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_demo | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_extras | fs-write | 1 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_github | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/sdlc_init.py | scaffold_vision | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/setup_wizard.py | _write_cache | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/setup_wizard.py | write_dismissed | fs-write | 2 | ungated | medium |
| skills/agrim-init/scripts/verify_detect.py | _atomic_write_json | fs-remove | 2 | ungated | high |
| skills/agrim-kg/scripts/kg.py | _write_refresh_record | fs-write | 2 | ungated | medium |
| skills/agrim-kg/scripts/kg.py | gap_log | fs-write | 1 | ungated | medium |
| skills/agrim-kg/scripts/kg.py | gap_resolve | fs-write | 1 | ungated | medium |
| skills/agrim-kg/scripts/kg.py | retain_web | fs-write | 1 | ungated | medium |
| skills/agrim-kg/scripts/kg.py | warn | fs-write | 2 | ungated | medium |
| skills/agrim-kg/scripts/kg.py | write_note | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/acceptance.py | record | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/acceptance.py | record | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/actionlog.py | append | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/agent_watch.py | _save_cursor | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/autowatch.py | _record_spend | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/backlog_check.py | _dense_channel | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/blockers.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/channel_notify.py | _save_cursor | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/coexist.py | _mark | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/coexist.py | backup_features | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/coexist.py | backup_features | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/coexist.py | backup_features | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/coexist.py | clear_watch_owner | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/coexist.py | write_owner | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/coexist.py | write_owner | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/coexist.py | write_watch_owner | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/comment_watch.py | _save_cursor | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/cross_repo.py | _record | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | cleanup | git-destructive | 1 | ungated | high |
| skills/agrim-loop/scripts/diff_revert.py | run | fs-write | 3 | ungated | medium |
| skills/agrim-loop/scripts/drift_watch.py | _stamp | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/feature_doc.py | _atomic_write_bytes | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/feature_doc.py | _atomic_write_bytes | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/feature_judge.py | _acquire_spend_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/feature_judge.py | _record_spend | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/feature_propagate.py | _store | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/feature_propagate.py | _write_remote | gh-api-write | 1 | granted verdict | high |
| skills/agrim-loop/scripts/feature_rebase.py | _clear_blocked | fs-remove | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _drop_worktree | fs-rmtree | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _drop_worktree | git-destructive | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _mark_blocked | fs-write | 2 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | _pushed | git-destructive | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _pushed | git-push | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_rebase.py | _rebase_feature | fs-write | 1 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | _remember | fs-write | 2 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | _upkeep | fs-write | 1 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | ack | fs-write | 2 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | mark_push_refused | fs-write | 2 | work.rebase_upkeep | medium |
| skills/agrim-loop/scripts/feature_rebase.py | push_refused | fs-remove | 1 | work.rebase_upkeep | high |
| skills/agrim-loop/scripts/feature_registry.py | _atomic_write_text | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/feature_registry.py | _atomic_write_text | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/feature_sync.py | _acquire | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/ledger.py | _maybe_prune_journal | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/ledger.py | append | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/ledger.py | main | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/ledger.py | prune_journal | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _end_if_owner | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _ensure_claimed | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _ensure_ledger_delivery | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _ensure_unit_tracking | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _merge_reconcile_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _phase_marker_end | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _prune | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _prune_dead_session_entries | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _reconcile_stamp | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _reserve_goal_slot | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _session_claim | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _session_locked | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _session_write | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _try_acquire_claim_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | _write | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/loop.py | _write | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | agent_end | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | agent_reclaim | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | agent_start | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | reclaim_stale_claim_lock | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/loop.py | session_start | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | verify_goal | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/loop.py | write_session_heartbeat | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/loop.py | write_session_heartbeat | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/merge_observation.py | materialize_snapshot | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/merge_observation.py | write_immutable | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/merge_observation.py | write_immutable | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/mirror.py | _write | fs-write | 3 | ungated | medium |
| skills/agrim-loop/scripts/phase_report.py | write_marker | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/pipeline.py | main | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/pipeline.py | propose_from_discovery | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/pipeline.py | propose_goals | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/promote.py | promote | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/reconcile.py | _write | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/reconcile.py | _write | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/release_manifest.py | publish_to_ledger_branch | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/release_manifest.py | write_once | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/release_manifest.py | write_once | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/review_context.py | _atomic_bytes | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/review_context.py | _atomic_bytes | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | _atomic_write_text | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/slack_commands_listen.py | _atomic_write_text | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | _log | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | acquire_single_instance | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/slack_commands_listen.py | acquire_single_instance | fs-write | 3 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | cut_worktree | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/slack_commands_listen.py | release_single_instance | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/sources.py | _apply_custom_fields | gh-project | 2 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _archive_card | graphql-mutation | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _create_issue | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_board | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_labels | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_priority_field | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _ensure_status_field | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _gh_json | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _graphql | graphql-mutation | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _run_gh | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _set_board_status | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _swap_labels | graphql-mutation | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _sync_backlog | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_board_phase | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_priority_field | gh-project | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | _write_priority_label | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | append_to_body | fs-write | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | append_to_body | gh-issue | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | complete | gh-issue | 1 | discovery.source == github; board writes require project.enabled | high |
| skills/agrim-loop/scripts/sources.py | create_dependency | fs-write | 2 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | create_dependency | gh-issue | 2 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | create_dependency | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | ensure_labels_report | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | fetch_issues_rest | gh-label | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | note | fs-write | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | note | gh-api-write | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | note | gh-issue | 1 | discovery.source == github; board writes require project.enabled | medium |
| skills/agrim-loop/scripts/sources.py | release | gh-issue | 2 | discovery.source == github; board writes require project.enabled | high |
| skills/agrim-loop/scripts/state.py | _cursor_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/state.py | _patch_cursor | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/state.py | _queue | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/state.py | _set_status | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/state.py | _state_file | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/state.py | claim_run_stop | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/state.py | phase_end_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/state.py | reanchor_content | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/supervise_daemon.py | main | fs-write | 4 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _ensure_gitattributes | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _ensure_lines | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _knowledge_lock | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _prune | fs-remove | 3 | ungated | high |
| skills/agrim-loop/scripts/sync.py | _publish_knowledge | git-destructive | 1 | ungated | high |
| skills/agrim-loop/scripts/sync.py | _push_with_retry | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/sync.py | _union_note | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _write_knowledge_gitignore | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | _write_team | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | bootstrap | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | init | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/sync.py | init | git-destructive | 1 | ungated | high |
| skills/agrim-loop/scripts/sync.py | publish_receipt | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/tier_escalation.py | write_floor | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/tier_escalation.py | write_floor | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/timing_store.py | _sweep | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/timing_store.py | append | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/timing_store.py | append_session | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/timing_store.py | maybe_prune | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/triage.py | _atomic_write_text | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/triage.py | _atomic_write_text | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/triage.py | _ensure_arbitrary_labels | gh-label | 1 | ungated | medium |
| skills/agrim-loop/scripts/triage.py | _execute_action | gh-issue | 3 | ungated | medium |
| skills/agrim-loop/scripts/unpark.py | _append_block | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/unpark.py | _comment | gh-issue | 1 | ungated | medium |
| skills/agrim-loop/scripts/upstream.py | _file_upstream | gh-api-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/upstream.py | _remember | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/upstream.py | _spill | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/watch.py | clear_inbox | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/watch.py | tick | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/watch_classify.py | save_cursor | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/watch_daemon.py | _run | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/watch_daemon.py | acquire_decision_mutex | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/watch_daemon.py | acquire_decision_mutex | fs-write | 3 | ungated | medium |
| skills/agrim-loop/scripts/watch_daemon.py | cleanup | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/watch_daemon.py | release_decision_mutex | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/watch_daemon.py | rotate_log | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/watch_daemon.py | take_over | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/watch_daemon.py | take_over | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/watch_daemon.py | touch_heartbeat | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/witness.py | record | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _clear_completed_merge_deliveries | fs-remove | 1 | ungated | high |
| skills/agrim-loop/scripts/work.py | _close_issue_the_base_cannot | gh-api-write | 1 | work.enabled; base branch cannot close the issue | high |
| skills/agrim-loop/scripts/work.py | _create_exclusive | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _delete_remote_branch | gh-api-write | 1 | work.enabled; merged PR cleanup | high |
| skills/agrim-loop/scripts/work.py | _repair_review_post_effects | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _save | fs-write | 2 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _try_union_changelog | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | _write_merge_delivery | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/work.py | _write_merge_delivery | fs-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | close_design | gh-pr | 1 | ungated | high |
| skills/agrim-loop/scripts/work.py | finish | fs-remove | 2 | ungated | high |
| skills/agrim-loop/scripts/work.py | finish | fs-rmtree | 1 | ungated | high |
| skills/agrim-loop/scripts/work.py | finish | gh-pr | 1 | work.enabled; confirmed merged PR | high |
| skills/agrim-loop/scripts/work.py | finish | git-destructive | 1 | ungated | high |
| skills/agrim-loop/scripts/work.py | merge | gh-pr | 2 | work.enabled; work.auto_merge != off; merge rights; fresh verify evidence and CLEAN PR | high |
| skills/agrim-loop/scripts/work.py | merge_design | gh-pr | 1 | work.enabled | high |
| skills/agrim-loop/scripts/work.py | post_review | gh-pr | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | pr | gh-api-write | 1 | ungated | medium |
| skills/agrim-loop/scripts/work.py | pr | git-push | 1 | ungated | high |
| skills/agrim-loop/scripts/work.py | rebase | git-destructive | 3 | ungated | high |
| skills/agrim-loop/scripts/work.py | rebase | git-push | 2 | ungated | high |
| skills/agrim-loop/scripts/work.py | review_evidence | fs-write | 1 | ungated | medium |
| skills/agrim-radar/scripts/radar.py | record | fs-write | 1 | ungated | medium |
| skills/agrim-rebase/scripts/conflict_walk.py | _resolve_to_stage | git-destructive | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | _write_context_store | fs-remove | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | _write_context_store | fs-write | 1 | ungated | medium |
| skills/agrim-rebase/scripts/rebase_brief.py | attempt_rebase | git-destructive | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | clear_context_snapshots | fs-remove | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | push_branch | git-destructive | 1 | ungated | high |
| skills/agrim-rebase/scripts/rebase_brief.py | push_branch | git-push | 1 | ungated | high |
| skills/agrim-rebase/scripts/verify_merge.py | _write_delivery | fs-remove | 1 | human input() confirmation | high |
| skills/agrim-rebase/scripts/verify_merge.py | _write_delivery | fs-write | 1 | human input() confirmation | medium |
| skills/agrim-rebase/scripts/verify_merge.py | ensure_landing_pr | gh-pr | 2 | human input() confirmation | medium |
| skills/agrim-rebase/scripts/verify_merge.py | merge_pr | gh-pr | 1 | human input() confirmation | high |
| skills/agrim-scope/scripts/assign.py | _apply_assignment | gh-issue | 1 | ungated | medium |
| skills/agrim-scope/scripts/assign.py | execute | fs-write | 2 | ungated | medium |
| skills/agrim-scope/scripts/scope.py | main | fs-write | 2 | ungated | medium |
| skills/agrim-setup/scripts/setup.py | ensure_ignore | fs-write | 2 | ungated | medium |
| skills/agrim-setup/scripts/setup.py | write_cfg | fs-write | 1 | ungated | medium |
| skills/agrim-status/scripts/merge_queue_enable.py | create_merge_queue_ruleset | gh-api-write | 1 | exact --yes-enable-merge-queue admin consent | high |
| skills/agrim-status/scripts/merge_queue_enable.py | patch_auto_merge | gh-api-write | 1 | exact --yes-enable-merge-queue admin consent | high |
| tools/kg_control.py | _repo | fs-write | 7 | ungated | medium |
| tools/kg_control.py | _write_builder | fs-write | 2 | ungated | medium |
| tools/kg_control.py | main | fs-write | 2 | ungated | medium |
| tools/kg_control.py | run | fs-write | 3 | ungated | medium |
| tools/onboarding_control.py | _drive_local_goal | fs-write | 2 | ungated | medium |
| tools/onboarding_control.py | _env | fs-write | 1 | ungated | medium |
| tools/onboarding_control.py | _fresh_files | fs-write | 2 | ungated | medium |
| tools/onboarding_control.py | _local_goal_work | fs-write | 2 | ungated | medium |
| tools/onboarding_control.py | _stub_gh | fs-write | 1 | ungated | medium |
| tools/onboarding_control.py | host_install | fs-write | 2 | ungated | medium |
| tools/onboarding_control.py | main | fs-rmtree | 1 | ungated | high |
| tools/onboarding_control.py | main | fs-write | 1 | ungated | medium |
| tools/onboarding_control.py | run_github | fs-write | 7 | ungated | medium |
| tools/onboarding_control.py | run_github | git-push | 1 | ungated | high |
| tools/onboarding_control.py | run_local | fs-write | 3 | ungated | medium |
| tools/readiness/write_surface.py | main | fs-write | 1 | ungated | medium |
