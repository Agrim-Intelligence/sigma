# The GitHub Project board (#235)

`scripts/board_setup.py create <.sdlc> [--owner O] [--title T] [--template N|OWNER/N] [--number N] [--yes]`

It creates the board the loop mirrors onto, or finishes or adopts one, and pins it in
`discovery.github.project.number` / `.owner`. Nothing is created without `--yes`.

| Step | What | On failure |
|---|---|---|
| auth + scopes | `gh auth status`, preflight's `repo` + `project` check | REFUSED, exit 2, per-host fix lines |
| resolve | the owner's boards over REST. A pinned number is reused; a same-titled board is refused | REFUSED, exit 2, runbook below |
| create | `createProjectV2` (links the repo in the same call), or `copyProjectV2` + `linkProjectV2ToRepository` | exit 1, resume command |
| pin | `project.number` + `project.owner` only, atomic, written before anything else can fail | `[FAIL]`, the value to set by hand |
| fields | Status options from `project.columns`, id-preserving. Only on a board created or copied in this same run are GitHub's `Todo` / `In progress` renamed to Backlog / In Progress. Priority gets `P0`..`P4` | `[FAIL]`, exit 1, resume command; `[REFUSED]`, exit 2 (below) |
| verify | reads the fields back; one GraphQL read for the repo link and workflows | `[FAIL]` / `[manual]` |

Re-running is safe. It finds its board by the pin and makes no mutation when nothing is missing.

## Adopting a board a human built (`--number N`, or a pin)

Nothing on it is renamed, recoloured or deleted. The field's options are read over REST (every
page), and each existing option is sent back with its own id, name, colour and description; only
the missing options are added. An option id left out of that write would delete the lane, wipe
every card's value in it, and turn off any workflow that targets it, so none is ever left out.

- **Case-only variant.** If the board has `In progress` where the loop wants `In Progress`, the
  board's spelling is written to `project.columns.in_progress` in config and used as-is. Adding
  `In Progress` next to it would make two lanes that read the same (#1492), and renaming it would
  change what the team sees, so neither is done.
- **Refusals (exit 2, no resume command, since a re-run cannot help until the thing is fixed by
  hand).** A `Status` or `Priority` field that exists but is not single-select; a Priority option
  spelled differently only by case (`p0`); options whose colour or description could not be read,
  because rewriting them would reset them.
- **Resume after a partial first run is an adoption too.** If the first run created the board but
  failed before setting Status, the resume adds Backlog and the rest and leaves GitHub's `Todo` as
  an extra lane. That is cosmetic; delete it on the board if you do not want it.

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
- **copyProjectV2.** Its input is `projectId, ownerId, title, includeDraftIssues`. Whether a copy
  keeps views and workflows could not be checked without a mutation. So the template path sets the
  Status and Priority fields afterwards anyway and reads the workflow state back.

### Manual check for the owner (5 steps)

1. `gh auth refresh -s project`. In the UI, create a throwaway board with one extra view and "Item closed" ON, and note its number T.
2. In a throwaway repository whose `.sdlc` pins no board: `python3 <sigma>/skills/agrim-init/scripts/board_setup.py create .sdlc --owner <you> --title "copy-probe" --template T --yes`
3. On the new board, open `/views/1` and `/workflows` and note which views and workflows came across, and whether they are enabled.
4. Record the answer on issue #235.
5. Delete both throwaway boards (Settings → Delete project) and remove the pin board_setup wrote.

## Cost

A fresh create is 3 GraphQL mutations plus 1 GraphQL read, and 3 + ceil(boards/100) +
2 x ceil(fields/100) REST reads (all core quota; a board has at most 50 fields, so that is 5 +
ceil(boards/100)) plus one `gh auth status`. Re-running on a finished board is 1 GraphQL read.
`/agrim-init` itself makes no call: it prints the offer. In the loop's steady state the only extra call is one `gh project view`,
made when a pinned number is not in the first page of 100 boards.
