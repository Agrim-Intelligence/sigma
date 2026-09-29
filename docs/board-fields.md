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
- **An unlabelled goal's Priority stays blank. It is not set to P3.** The issue asked for P3. The
  kit's rule is that "no priority" sorts after P4 (`discovery.UNPRIORITISED`). Writing P3 would move
  every unlabelled goal ahead of the P4s in the board queue.
- A status write takes Priority from the labels that the same run's backlog sync already read, so it
  costs no extra call. A phase start takes it from the issue's own labels, returned by its one read.
  A card moved to Done gets no Priority write: the goal is finished.
- Status is never written at a phase start, and a goal with no card is never carded there. Carding
  it would leave a card with a blank Status.

## Who writes Priority: Sigma's board, or yours

The `priority:*` label is the source of truth. Whether the loop may also treat the board's Priority
column as a writer depends on whose column it is.

**Sigma's Priority field.** That is a board the loop created in this run, the board
`board_setup.py create` created (`project.setup_created` equals the pinned `project.number`), or any
pinned board once you set **`project.mirror_priority: true`**. There the loop creates the field when
it is missing, and keeps the #719 rule: a recognised field value wins and the label is corrected
(with a message on stderr); a blank field is filled from the label.

**Any other board (one you made or adopted).**

- The loop **never creates** a Priority field on it.
- If the board already has one, the loop fills a **blank** Priority from the label. It never rewrites
  a label from the field, and never overwrites a Priority value somebody set. A person who changes
  `priority:P1` to `priority:P0` is never reverted.
- One exception predates #233 and is unchanged: the backlog sync (`_sync_backlog`, on every status
  write) has always applied the #719 field-wins rule to a column named exactly `priority_field`
  (default `Priority`) on any board. A case-only variant (`PRIORITY`) is adopted by the same name
  rule as the phase path, but label-to-field only. To make the field the writer on your own board,
  opt in with `project.mirror_priority: true`.

To add the column to your own board, either set `project.mirror_priority: true` (the next phase
start creates it), or run `board_setup.py create <sdlc> --number <N> --yes`, which adds the fields
the board lacks (see `skills/agrim-init/references/board.md`).

## Missing fields, and names that differ in case

The phase-start path runs only when `project.enabled` is on and `project.number` is pinned. The pin
is the operator's explicit choice of board.

- **Phase is absent.** It is created once on the pinned board, as a single-select with the seven
  tokens. Phase is Sigma's own column: no board had one before #233, so nothing it could overwrite
  existed. **Priority is absent:** created only on Sigma's board (above). The create is
  `createProjectV2Field`, every literal JSON-escaped the same way #235's `_options_mutation` does it,
  and it returns the new option ids, so no re-read follows.
- **One field differs only in case** (`phase`, `PRIORITY`). It is adopted as it is: never renamed,
  and no duplicate is created. The sync and the phase path share one rule (`_match_field`).
- **Several fields differ only in case.** An exact name wins, and the run prints one note naming the
  others. With no exact name, nothing is guessed: that field is skipped with one warning. The sync
  reads cards through `gh project item-list`, which files every field under its lowercased name, so
  two case-variants of Priority overwrite each other there. The sync then mirrors no Priority at all
  and says so once.
- **The field has extra options** (a `Triage` lane, a `Someday` priority). They are kept.
  Options are matched exactly first, then by case (`p2 research` counts as `P2 RESEARCH`).
- **An option is missing.** The loop never appends it. An option rewrite replaces the field's whole
  option list, and #235's rule is to refuse rather than risk resetting colours, descriptions or ids.
  The run prints one warning naming the option. Add it on the board by hand.
- **A same-named field is not single-select.** It is skipped with one warning.

## Which card

A card is matched by **repository and issue number**, never by number alone. A board can hold cards
from several repos, and `acme/other#11` is not `acme/widget#11`. The phase path reads the issue by
repository, and the status path skips `item-list` rows whose `content.repository` names another repo.
The phase path also requires the card's board to be the pinned number under the configured owner. A
board that merely has the expected title never stands in for a pin that cannot be read.

## Fail-open, and the time bound

A board write never fails a pick or a phase. `phase_report.py start` does the write after its
marker, its ledger event and its banner, so its exit code and stdout are the same whatever the
board does. Each run prints at most one line on stderr, whatever goes wrong:
`sigma: board Phase/Priority not written - <reason>. The goal continues unaffected; ...`.

- Each `gh` call has `BOARD_CALL_TIMEOUT_S` (20s). The whole write stops making calls once
  `BOARD_BUDGET_S` (45s) is spent.
- A call that overruns is **killed with everything it spawned**. On POSIX, `gh` starts in a session
  of its own and its process group gets SIGKILL. On Windows, `taskkill /T /F` kills the tree, with a
  `BOARD_REAP_S` (5s) bound of its own. Collecting a killed call's output is bounded by
  `BOARD_REAP_S` on every platform: a pipe that is still held open is abandoned, not waited on.
- A killed call is **never retried**. `gh`'s own timeout text says "timed out", which the board
  layer's transient-error list matches, so the runner marks it `no_retry`. The boundary then skips
  every remaining board write, prints its one warning, and the next boundary tries again.

## Cost per phase boundary

Call counts are measured against `tests/boardfake.py`
(`tests/test_board_phase.py::test_calls_per_boundary_are_bounded`):

- **Steady state:** **1 read** (a single GraphQL query for this issue: its labels, its cards, the
  pinned board's fields and the card's values) plus at most 1 Phase write and at most 1 Priority
  write. There are 7 boundaries per goal.
- **First boundary on a board missing Phase (and, on Sigma's board, Priority):** add 1 create per
  missing field. No re-read.

**Timing (measured, read-only, board #17, 246 cards, 3 runs):** the one-card GraphQL read took
0.66–0.72s. The version this replaced read `project list` (2.6s), `field-list` (2.5s) and
`item-list --limit 5000` (6.5s): about 11.6s per boundary, or about 80s per goal. The writes were
not timed against live GitHub (goal #233 was not allowed to mutate it).

**Scale.** The read does not depend on the board's card count: 10x or 100x the cards costs the same
query. Its limits are fixed page sizes. It sees the first 50 boards an issue is carded on
(`_CARD_BOARDS`); an issue on more than 50 boards may read as uncarded and gets one warning. It reads
50 fields (`_CARD_FIELDS`; GitHub allows 50 fields per project), 50 of the card's field values, and
100 labels. Each read costs 1 GraphQL rate-limit point. This path makes no backlog read and no
whole-board read (`_sync_backlog` does not run here).

## Config

`discovery.github.project`:

- `phase_field`: the Phase column's name. Default `"Phase"`. Set it to `false` to disable Phase;
  Priority is unaffected.
- `priority_field`: the Priority column's name. Default `"Priority"`. Set it to `false` to disable
  Priority.
- `mirror_priority`: `true` makes the pinned board's Priority column Sigma's (created if missing;
  #719 field-wins). Default off. Only the literal `true` turns it on.
- `setup_created`: written by `board_setup.py create` when it created the board. Not for hand
  editing.
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
   that the card's Phase reads `P2 RESEARCH` .. `P7 RETRO`. The first run creates the Phase field.
   Priority reads `P1` if the board has a Priority column (it is created only on Sigma's board or
   with `project.mirror_priority: true`).
3. **Label wins on your board.** On a board you made, change the goal's label from `priority:P1` to
   `priority:P0` and run the next `start`. The label must stay `priority:P0`.
4. Repeat the last `start`. Nothing should change on the card and nothing should print on stderr.
5. **Control.** Delete the Phase field on the board. Then revoke the scope (`gh auth refresh
   --remove-scopes project`) or use a token without write access, and run a pick (`loop.py next
   .sdlc`) and one `start`. Both must succeed (exit 0, banner printed). The `start` must print
   exactly one `sigma: board Phase/Priority not written` line. The pick prints its own
   missing-scope note, as it did before #233. Restore the scope afterwards.
6. With the scope back, run one `start`. The Phase field is recreated once, and the card shows the
   phase again.
