# #895 slice 5 retro: GraphQL-unavailable board gate (advisory)

Grade: achieved (with the residuals below; refs #895, does not close it).

## Shipped vs plan
- `GitHubSource.board_active` in sources.py: false when the board is config-off; true when GraphQL is
  available; otherwise skip plus one stderr notice (once-flag on `sys`, shared across loaded copies).
  Fails open if the availability check raises.
- Raw `project_enabled` reads swapped onto the gate in `_ready_lane`, `_archive_card`, `_set_board_status`,
  `_apply_custom_fields`, `set_board_phase`, `_ensure_board`, `_board_queue`, and the promote / unpark /
  auto_unpark readers (so no false "board move FAILED").
- Doctor fix text and queue row, status.py segment, CHANGELOG, docs/cloud-sessions.md. Write-surface left alone.
- tests/test_board_gate.py: 12 tests (AST rule, no-board-call, argv-unchanged, one-notice, fail-open,
  label-queue fallback, promote/unpark, auto_unpark).

## Deviations
- The runtime test does not go red from removing a single inner gate, because the outer gates
  (`_ensure_board`, `_set_board_status`) backstop each other. The AST tests
  (`test_every_board_call_function_is_gated`, `test_project_enabled_read_only_inside_board_active`) are the
  control that does catch a single removed gate.
- Plan review twice rewrote the plan: wrong function names, then a once-flag that was not process-wide.

## Residual debt
- Ungated operator tools: board_setup, board_migrate, board_layout (deliberate, documented).
- `_graphql` label path (`_swap_labels`, `_label_node_ids`, `_issue_node_id`, reconcile timeline) still uses
  GraphQL: other slices' business.
- The notice is once per process, so in cloud (a fresh process per phase boundary) it can print per boundary.
- Nothing tested live in a cloud session; evidence is the recording-fake test only.

## Durable lessons
- In the plan phase, verify every function name and line number with grep before writing it down.
- A once-flag must live where every loaded copy of the module sees it; `spec_from_file_location` gives each
  consumer its own module copy.
- A runtime test cannot prove each of several redundant gates; pair it with a structural (AST) rule.
