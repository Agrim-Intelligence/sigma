# The GitHub Project board (#235)

`scripts/board_setup.py create <.sdlc> [--owner O] [--title T] [--template N|OWNER/N] [--number N] [--yes]`

It creates the board the loop mirrors onto, or finishes or adopts one, and pins it in
`discovery.github.project.number` / `.owner`. Nothing is created without `--yes`.

| Step | What | On failure |
|---|---|---|
| auth + scopes | `gh auth status`, preflight's `repo` + `project` check | REFUSED, exit 2, per-host fix lines |
| resolve | the owner's boards over REST. A pinned number is reused; a same-titled board is refused | REFUSED, exit 2, runbook below |
| create | `createProjectV2` (links the repo in the same call), or `copyProjectV2` + `linkProjectV2ToRepository` | exit 1, resume command |
| pin | `project.number` + `project.owner` (+ `project.setup_created` = `{number, owner}` when this run created the board; dropped when another number or owner is pinned), atomic, written before anything else can fail | `[FAIL]`, the value to set by hand |
| fields | Status options from `project.columns`, id-preserving. Only on OUR EMPTY board (created or copied in this run, or the board an earlier run created that still has no card) are GitHub's `Todo` / `In progress` renamed to Backlog / In Progress and `Ready` added. Priority gets `P0`..`P4` | `[FAIL]`, exit 1, resume command; `[REFUSED]`, exit 2 (below) |
| verify | reads the fields back; one GraphQL read for the repo link and workflows | `[FAIL]` / `[manual]` |

Re-running is safe. It finds its board by the pin and makes no mutation when nothing is missing.

## `Ready` is the loop's queue switch, so it goes only on our empty board

`Ready` decides HOW the loop picks work (`sources._ready_lane`). Without a `Ready` option the loop
picks by the `sdlc:goal` label. With one (and the shipped `queue_source: "status"`) the board IS
the queue: only a card in `Ready` is picked, and the only fallback is an issue with NO card. So
adding `Ready` to a board whose goal cards sit in other lanes (`Todo`, `In progress`, no Status)
strands all of them and the loop reports DONE. Reproduced in review of PR #279: a human board with
goal cards #5/#6/#7 picked `5` before adoption and nothing after.

So board_setup adds `Ready` only where nothing can be stranded: a board it created itself
(`project.setup_created` names it) that has no card yet. Everywhere else it prints
`[skip] Ready lane` with the reason, says the loop keeps picking by label, and prints the explicit
step that changes that:

    python3 <sigma>/skills/agrim-doctor/scripts/board_migrate.py --owner <O> --project <N> --backlog <lane> --apply

`board_migrate.py` adds `Ready` AND moves the open goal cards from `<lane>` into it (run it once per
lane that holds queued goals; without `--apply` it is a dry run). A `Ready` the board already has
is left exactly as it is: that board is already on the status queue, and nothing about it changes.

If a board does end up with an empty `Ready` while goal cards sit elsewhere, the loop says so once
per run on stderr (`the board has a 'Ready' lane but NOTHING in it, while N sdlc:goal card(s) sit
outside it (...)`), naming each lane and the command, instead of reading DONE in silence.

## Adopting a board a human built (`--number N`, or a pin)

Adoption does not change how the loop picks work: no `Ready` is added (above). Nothing on the
board is renamed, recoloured, reordered or deleted. The field's options are read over REST (every
page), and each existing option is sent back with its own id, name, colour and description, in its
own position; only the missing options are appended after them. An option id left out of that write would delete the lane, wipe
every card's value in it, and turn off any workflow that targets it, so none is ever left out.

- **Case-only variant.** If the board has `In progress` where the loop wants `In Progress`, the
  board's spelling is written to `project.columns.in_progress` in config and used as-is. Adding
  `In Progress` next to it would make two lanes that read the same (#1492), and renaming it would
  change what the team sees, so neither is done.
- **Refusals (exit 2, no resume command, since a re-run cannot help until the thing is fixed by
  hand).** A `Status` or `Priority` field that exists but is not single-select; a Priority option
  spelled differently only by case (`p0`); options whose colour or description could not be read,
  because rewriting them would reset them.
- **Resume after a partial first run.** While the board the first run created still has no card,
  the resume finishes it exactly as the first run would have (`Todo` / `In progress` renamed, ids
  kept, `Ready` added). One extra REST read tells: `projectsV2/<n>/items?per_page=1`. If a loop tick
  ran in between and carded goals on it (with no Status, since the board had no matching lane), the
  resume is an adoption: nothing renamed, no `Ready`, the `board_migrate.py` line printed. Adding
  `Ready` there would strand exactly those cards. If the card read fails, `Ready` is not added and
  the run exits 1 with the resume command.

## Priority: `P0`..`P4`, not `P0`..`P3`

Issue #235 says `P0`..`P3`. The field gets `P0`..`P4`, because the options come from
`discovery.PRIORITIES`, the same list the loop's queue ranks by and the priority mirror writes. A
four-option field would leave every `P4` goal with no value on the board.

## The resume command on Windows

On Windows the resume command is printed with double quotes, which cmd and PowerShell both accept.
If a value (the title, the owner, the `.sdlc` path) contains `"`, `%`, `$`, a backtick or `!`, no
quoting is safe in both shells, so no command is printed. The message tells you to re-run the
command you ran, adding `--number N`.

## Why the pin matters

The loop finds its board by `project.number`, or else by the title `<repo> — SDLC`. It creates a
board only when the owner has none. With the pin, the board is still found after someone renames it,
and when the owner has more than 100 boards (then the pinned board is read directly). Without the
pin, a renamed board turns mirroring off ("board mirroring OFF this run").

## Workflows and views: what the API can and cannot do

Checked by reading GitHub's GraphQL schema (introspection, no mutation). The evidence is in
`.sdlc/evidence/235/` and `.sdlc/research/235.md`.

- **Workflows.** There is no API to create or enable one. The only workflow mutation is
  `deleteProjectV2Workflow`. When "Item closed" is off, board_setup prints the manual step and the
  deep link: `https://github.com/{orgs|users}/<owner>/projects/<n>/workflows` → Item closed → Edit →
  Status: Done → Save and turn on workflow.
- **Views.** `createProjectV2View` and `updateProjectV2View` exist, but board_setup creates no views.
  The canonical six views and the Phase / Area / Model tier fields are a separate, explicit step,
  `scripts/board_layout.py fields|views|verify` (#234). A view's name, layout, visible columns and
  filter can be set through the API. Its grouping, sort, column field and tab order cannot, and are
  printed as UI steps. The design, the runbook and the evidence are in `docs/board.md`.
- **copyProjectV2.** Its input is `projectId, ownerId, title, includeDraftIssues`. Whether a copy
  keeps views and workflows could not be checked without a mutation. So the template path sets the
  Status and Priority fields afterwards anyway and reads the workflow state back. `docs/board.md`
  control (c) is the owner's measurement: `gh project copy`, then `board_layout.py verify` on the
  copy.

### Manual check for the owner (5 steps)

1. `gh auth refresh -s project`. In the UI, create a throwaway board with one extra view and "Item closed" ON, and note its number T.
2. In a throwaway repository whose `.sdlc` pins no board: `python3 <sigma>/skills/agrim-init/scripts/board_setup.py create .sdlc --owner <you> --title "copy-probe" --template T --yes`
3. On the new board, open `/views/1` and `/workflows` and note which views and workflows came across, and whether they are enabled.
4. Record the answer on issue #235.
5. Delete both throwaway boards (Settings → Delete project) and remove the pin board_setup wrote.

## Cost

A fresh create is 3 GraphQL mutations plus 1 GraphQL read, and 3 + ceil(boards/100) +
2 x ceil(fields/100) REST reads (all core quota; a board has at most 50 fields, so that is 5 +
ceil(boards/100)) plus one `gh auth status`. Re-running on a finished board is 1 GraphQL read. A
resume of our own board that has no `Ready` yet adds one REST read (does any card exist?).
`/agrim-init` itself makes no call: it prints the offer. In the loop's steady state the only extra call is one `gh project view`,
made when a pinned number is not in the first page of 100 boards.
