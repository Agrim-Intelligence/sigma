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
| fields | Status options from `project.columns`, id-preserving. GitHub's `Todo` / `In progress` are renamed to Backlog / In Progress. Priority gets `P0`..`P4` | `[FAIL]`, exit 1, resume command |
| verify | reads the fields back; one GraphQL read for the repo link and workflows | `[FAIL]` / `[manual]` |

Re-running is safe. It finds its board by the pin and makes no mutation when nothing is missing.

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

A fresh create is 3 GraphQL mutations plus 1 GraphQL read, and 5 + ceil(boards/100) REST reads
(all core quota) plus one `gh auth status`. Re-running on a finished board is 1 GraphQL read.
`/agrim-init` itself makes no call: it prints the offer. In the loop's steady state the only extra call is one `gh project view`,
made when a pinned number is not in the first page of 100 boards.
