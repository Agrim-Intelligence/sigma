# Board fields: Phase and Priority (#233)

The loop mirrors three things onto a goal's Projects card: **Status** (unchanged, on every status
write), **Priority** and **Phase**. With all three on the card, the board passes the
[output contract](output-contract.md)'s single-glance test: which goal, which phase, what now.

## What is written, and when

| Field | Written when | Value |
|-------|--------------|-------|
| Status | every status write (pick, QC, done, blocked, park) | the configured column; semantics unchanged |
| Priority | every status write, and every phase start | from the `priority:P0`..`P4` label (see below) |
| Phase | every phase start (`phase_report.py start`), and only then | the phase the loop is ENTERING: `P1 GOAL` .. `P7 RETRO` |

- **Phase** tokens are `phase_report.PHASE_TOKENS`, the same closed vocabulary as the output
  contract §2. They are passed to the board code, never copied. A start whose card already reads that
  phase writes nothing, so a repeated `start` costs no write.
- **Priority** keeps the #719 rule: a recognised field value wins and the label is corrected (with a
  message on stderr); a blank field is filled from the label. **An unlabelled goal's Priority stays
  blank. It is not set to P3.** The issue asked for P3. The kit's rule is that "no priority" sorts
  after P4 (`discovery.UNPRIORITISED`). Writing P3 would move every unlabelled goal ahead of the P4s
  in the board queue. The field-wins mirror would then add `priority:P3` labels to all of them.
- A status write takes Priority from the labels that the same run's backlog sync already read, so it
  costs no extra call. A phase start takes it from the card's own labels, which `item-list` returns.
- Status is never written at a phase start, and a goal with no card is never carded there. Carding
  it would leave a card with a blank Status.

## Missing fields, and boards Sigma did not create

The phase-start path provisions both fields, but only when `project.enabled` is on and
`project.number` is pinned. The pin is the operator's explicit choice of board.

- **The field is absent.** It is created once, as a single-select with fixed options: Phase gets the
  seven tokens, Priority gets `P0`..`P4`. The create is `createProjectV2Field`, and every literal is
  JSON-escaped the same way #235's `_options_mutation` does it.
- **The field exists with a different case** (`phase`, `PRIORITY`). It is adopted as it is: never
  renamed, and no duplicate is created.
- **The field has extra options** (a `Triage` lane, a `Someday` priority). They are kept.
  Options are matched exactly first, then by case (`p2 research` counts as `P2 RESEARCH`).
- **An option is missing.** The loop never appends it. `gh project field-list` does not return option
  colours or descriptions, so a rewrite would reset them, and #235's rule is to refuse rather than
  reset. The run prints one warning naming the option. Add it on the board by hand. Nothing is ever
  renamed, recoloured, reordered or dropped, so every option id is preserved.
- **A same-named field is not single-select.** It is skipped with one warning.

A loop tick (a status write) still never creates a Priority field on a board it did not create
(`test_an_adopted_board_gets_no_priority_field_from_the_loop`).

## Fail-open

A board write never fails a pick or a phase. `phase_report.py start` does the write after its
marker, its ledger event and its banner, so its exit code and stdout are the same whatever the
board does. Each run prints at most one line on stderr, whatever goes wrong:
`sigma: board Phase/Priority not written - <reason>. The goal continues unaffected; ...`.
Each `gh` call times out after `BOARD_CALL_TIMEOUT_S` (20s). The whole write stops making calls
once `BOARD_BUDGET_S` (45s) is spent.

## Cost per phase boundary

These counts were measured against `tests/boardfake.py`
(`tests/test_board_phase.py::test_calls_per_boundary_are_bounded`):

- **Steady state:** 3 reads (`project list`, `field-list`, `item-list`) plus at most 1 Phase write
  and at most 1 Priority write. There are 7 boundaries per goal.
- **First boundary on a board missing both fields:** add 2 creates and 1 re-list.
- **Pinned board outside the first 100:** add 1 `project view`.

`item-list` is one CLI call. Its internal paging grows with the board (100 cards per page, capped
at `_BOARD_ITEM_LIMIT` = 5000). A board 10x or 100x larger therefore costs 10x or 100x the pages
inside that one call. Every status write already pays the same read. This path adds no O(board)
write and no backlog read (`_sync_backlog` does not run here). These costs were not measured
against live GitHub.

## Config

`discovery.github.project`:

- `phase_field`: the Phase column's name. Default `"Phase"`. Set it to `false` to disable Phase;
  Priority is unaffected.
- `priority_field`: the Priority column's name. Default `"Priority"`. Set it to `false` to disable
  Priority.
- `enabled` off, or no `number`: nothing runs. A phase start makes zero `gh` calls and does not load
  the board code.

## Owner runbook: acceptance on a live board

The goal that built this (#233) was not allowed to mutate real GitHub. Its acceptance ran against
the in-memory fake (`tests/test_board_phase.py`, evidence in `.sdlc/evidence/233/`). **The run on
board #17 has not been executed.** To run it yourself, on a throwaway goal:

1. Confirm the config has `project.enabled: true`, `project.owner` and `project.number: 17`.
   Confirm `gh auth status` lists the `project` scope.
2. Pick a goal labelled `sdlc:goal` + `priority:P1` whose card is on board #17. For each phase in
   turn, run
   `python3 skills/agrim-loop/scripts/phase_report.py start .sdlc <N> <phase> --model sonnet`,
   using `research`, `plan`, `plan_review`, `implement`, `review`, `retro`. After each one, check
   that the card's Phase reads `P2 RESEARCH` .. `P7 RETRO` and Priority reads `P1`. The first run
   creates the Phase field (and Priority, if missing) on the board.
3. Repeat the last `start`. Nothing should change on the card and nothing should print on stderr.
4. **Control.** Delete the Phase field on the board. Then revoke the scope (`gh auth refresh
   --remove-scopes project`) or use a token without write access, and run a pick (`loop.py next
   .sdlc`) and one `start`. Both must succeed (exit 0, banner printed). The `start` must print
   exactly one `sigma: board Phase/Priority not written` line. The pick prints its own
   missing-scope note, as it did before #233. Restore the scope afterwards.
5. With the scope back, run one `start`. The Phase field is recreated once, and the card shows the
   phase again.
