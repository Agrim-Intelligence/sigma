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
`board_setup.py create` created (`project.setup_created` names the pinned `project.number` under
the configured `project.owner`), or any
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
- **A widening, stated plainly.** Before #233 the sync read only a column named *exactly*
  `priority_field`, so a board whose column was spelled `PRIORITY` (or `priority`) got no Priority
  writes at all. It now adopts that lone case-variant, which means the sync **starts filling blank
  cells** in it from the labels on an adopted board where it wrote nothing before. It still never
  overwrites a value somebody set there and never rewrites a label from it. To keep the loop out of
  such a column entirely, set `project.priority_field: false`.

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
  and it returns the new option ids, so no re-read follows. Two first phase starts can race to
  create it: the fake rejects the second create ("Name has already been taken"), as GitHub rejects a
  duplicate field name (not measured on a live board). The loser then re-reads the card once. If the
  field is there, it uses the winner's field and writes its Phase with no warning. Only a field that
  is still absent gets the one warning (#308).
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

## Status options spelled differently (#280)

A card move names a column from `project.columns` (`In Progress` by default). The Status field's
options are matched with the same rule as the fields above, and whitespace is ignored as well:

- **An exact option wins.** A board with both `In Progress` and `In progress` uses `In Progress`.
- **Otherwise one option that differs only in case or spacing is used** under the board's own
  spelling. For example, `in   progress` matches `In Progress`. This happens only in memory, for
  the current run. The board is never renamed and config is never written. `board_setup.py` is
  the place that maps an adopted board's spelling into `project.columns` (#235).
- **Several such options and no exact one** match nothing. The loop does not guess between two lanes.
  Two lanes with the *same* name count as one, as they do in the loop's option map.
- **`Ready` is matched exactly, always.** It decides whether the board or the label queue is used,
  and that is never switched by spelling. A board with a `READY`, `ready` or `Ready ` lane keeps
  the label queue. The run prints **one** notice naming that lane. To use it as the queue, set
  `project.columns.ready` to its exact spelling (an explicit choice, which then wins) or run
  `board_migrate.py`. The loop also never writes a card into such a lane.
- **No match** means the card is not moved. The run prints **one** warning for each column. It
  names the column, its `project.columns.<key>`, and the options the board actually has. Before
  #280 this was a silent no-op. There are two exceptions. A board without `Ready` gets no warning,
  because that is the designed label queue. A park into a missing `Parked` falls back to `Blocked`
  with no warning. A board with neither `Ready` nor `Backlog` also gets one warning, because the
  sync then cannot card new goals.
- **A board the loop creates itself** (`_ensure_board`, when the owner has no boards) renames
  GitHub's defaults `Todo` and `In progress` to `Backlog` and `In Progress`. It does this in the
  same update that preserves option ids, which is the same thing `board_setup.py` does on its fresh
  path. Workflows stay enabled. Before #280 only `Todo` was renamed. The loop's own board kept
  `In progress`, and every pick wrote nothing to it.

GitHub's default spelling, as one read-only GraphQL read of an organisation's 14 boards on
2026-09-29 shows (`.sdlc/research/280.md`). 4 boards still have GitHub's default `Todo` lane and
carry `In progress`, with a lowercase p. One of them has the loop's own default title
`<repo> — SDLC`, and it holds a duplicated `In progress` lane. 3 other `Todo / In Progress / Done`
boards carry `In Progress`. Both spellings are live, so neither one is assumed. What GitHub's API gives a
brand-new board today was not measured, because doing so would mean creating a board on real
GitHub.

`/agrim-doctor` has a row, **board Status options match the loop's columns**. It lists every loop
column that has no matching option on the pinned board. `ready` is never listed. `parked` is not
listed while `blocked` matches. The row uses the loop's own matching function, so the two never
disagree. It shares one read-only `gh project field-list` with the **board custom fields mapped**
row. It runs only when the pinned board is reachable, and it is never run under `cheap_only`.

## Which card

A card is matched by **repository and issue number**, never by number alone. A board can hold cards
from several repos, and `acme/other#11` is not `acme/widget#11`. The phase path reads the issue by
repository, and the status path skips `item-list` rows whose `content.repository` names another repo.
A card names the repo's **current** name. After a rename or transfer, a stale `discovery.github.repo`
still matches its own cards (#308). One read, `gh api repos/<configured repo>`, resolves it. It is
made when the first card names another repository, or before the first `item-add`, whichever comes
first. GitHub answers it with the current `full_name` (measured read-only on a renamed repo,
the evidence recorded on #308). That name counts as ours for as long as the source lives. New cards are
added by an issue URL under it, even on a board that holds none of our cards yet: a URL under the
old name does not resolve. If the read fails, only the configured name counts, so another repo's
card is still never written. A failure is remembered for 5 minutes (`_REPO_RETRY_S`), then the next
need reads again, so a long-lived watcher recovers from one failed read. A board with only our cards
makes no such read unless a card has to be added.
The phase path also requires the card's board to be the pinned number under the configured owner. A
board that merely has the expected title never stands in for a pin that cannot be read.

`project.owner: "@me"` (a supported value: `board_migrate --owner @me`, and what the owner falls back
to when neither `project.owner` nor `discovery.github.repo` is set) is resolved to the token's own
login by the **same** read (`viewer { login }`), and compared like any other owner, case-insensitively.
It is never a wildcard: an org's board #5 is not your board #5. If the login cannot be read, nothing
is written and the run prints its one warning.

## Fail-open, and the time bound

A board write never fails a pick or a phase. `phase_report.py start` does the write after its
marker, its ledger event and its banner, so its exit code and stdout are the same whatever the
board does. Each run prints at most one line on stderr, whatever goes wrong:
`sigma: board Phase/Priority not written - <reason>. The goal continues unaffected; ...`. That
includes failures before the board code runs (it cannot be loaded). A `config.json` that cannot be
parsed prints the line only if its text names a `"project"`; a repo with no board says nothing.

- Each `gh` call has `BOARD_CALL_TIMEOUT_S` (20s). The whole write stops making calls once
  `BOARD_BUDGET_S` (45s) is spent.
- A call that overruns is **killed with everything it spawned**. On POSIX, `gh` starts in a session
  of its own and its process group gets SIGKILL. On Windows, `taskkill /T /F` kills the tree, with a
  `BOARD_REAP_S` (5s) bound of its own. Collecting a killed call's output is bounded by
  `BOARD_REAP_S` on every platform: a pipe that is still held open is abandoned, not waited on.
- **Ctrl-C (or any other interrupt) during a call** kills that call's process group or tree the
  same way before the interrupt goes through. `gh` runs in a session of its own, so the terminal's
  SIGINT never reaches it; without the kill it would be left running with no timeout.
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
- `setup_created`: written by `board_setup.py create` when it created the board, as
  `{"number": N, "owner": "<login>"}`. It counts only while it names the pinned number under the
  pinned owner: a hand-made board of another owner that reuses the number is not Sigma's, and
  re-pinning under another owner or number drops it. The older bare-number form (`"setup_created": 17`)
  cannot tell two owners' boards apart, so it reads as **not** Sigma's board (the label stays the
  one Priority writer), and any `board_setup.py create` re-pin drops it. It is never upgraded: the
  pre-#233 `pin()` kept it when only the owner changed, and so does a hand edit of `project.owner`,
  so the owner beside it cannot vouch for it (#308 review). To make that board's Priority column
  Sigma's, set `mirror_priority: true`. To create a separate board instead, use the recovery
  procedure below. Omitting `--number` alone still reuses `project.number`; it does not create
  a board or restore the marker. Not for hand editing.
- `enabled` off, or no `number`: nothing runs. A phase start makes zero `gh` calls and does not load
  the board code.

## Recovering a dropped board-ownership marker

To keep the existing board and let Sigma manage its Priority field, set
`discovery.github.project.mirror_priority` to `true`. This does not restore `setup_created`
or add a Ready lane; use the board migration procedure to change the queue deliberately.

To create a **separate** board with a full ownership marker, first save the current board's
owner and number so you can re-pin it if creation fails. Stop any loop writing this project's
config while making this change. In `.sdlc/config.json`, remove
`discovery.github.project.number`; keep the other settings. Then run `create` without
`--number`, with a title that does not already exist under that owner. Leaving the pin in place
reuses that board even if `--title` names a different one. Removing only the pin while reusing
the old board's title is refused as a duplicate.

From the project root, set `SIGMA_PLUGIN_ROOT` to the installed Sigma plugin directory. Choose
an unused title in the second command (the example uses `Sigma recovery board`). The first command
writes a temporary sibling and replaces the config only after that write succeeds; `&&` prevents
board setup from running if the config edit fails:

<!-- setup-created-recovery -->
```sh
python3 - <<'PY' &&
import json, os, pathlib, tempfile

p = pathlib.Path(".sdlc/config.json")
c = json.loads(p.read_text(encoding="utf-8"))
c["discovery"]["github"]["project"].pop("number", None)
fd, name = tempfile.mkstemp(dir=p.parent, prefix=".config-recovery-", suffix=".tmp")
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(c, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, p)
finally:
    pathlib.Path(name).unlink(missing_ok=True)
PY
python3 "$SIGMA_PLUGIN_ROOT/skills/agrim-init/scripts/board_setup.py" create .sdlc --title "Sigma recovery board" --yes
```

Successful creation pins the new board and writes `setup_created` with its number and owner.
The existing board and its cards remain where they were; this is not a card migration. A failed
run may already have created and pinned the new board: follow its printed resume instructions
instead of clearing that new pin and creating another board. If no new board was pinned, restore
the saved owner and number to keep using the existing board.

## Owner runbook: acceptance on a live board

The goal that built this (#233) was not allowed to mutate real GitHub. Its acceptance ran against
the in-memory fake (`tests/test_board_phase.py`, evidence recorded on #233). **The run on
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
