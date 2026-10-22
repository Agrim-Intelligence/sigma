# What is new in 1.0.20

This page lists every change in this release in grouped, readable form, each with its issue number. The same
changes are in the `1.0.20` section of the [CHANGELOG](../CHANGELOG.md) in the order a maintainer reads them.
Start with [Upgrading from 1.0.19](#upgrading-from-1019) if you only want to know what changes for you.

This release is about reading the dependency map on a real backlog. The pages now show who holds each member and
the work that waits on a goal; the map is complete after one rebuild; pages get titles without a hand-made file;
and `deps.py` gains a review sheet for a whole priority band, a way to take a wrong `Blocked by` line back, and a
two-way-wait flag that reads which way a wait runs.

Every entry was proven with fakes first. The map, page and sheet entries were then run read-only against one private
product repository's backlog (482 map nodes, 63 open P0 and P1 goals); those numbers are quoted below. Nothing in that
check wrote to the repository.

## Upgrading from 1.0.19

Step 1 applies to everyone. The rest apply only if you use the feature named.

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`,
   then remove the file and start the watcher again.
2. **If you use `/sigma-define`,** the unit label is now listed and created on `discovery.github.repo` (#1983),
   not on gh's default repository, and the label step refuses, naming that key, when it is unset.
3. **If you use `deps.py mark` on issues Sigma did not create or label,** it now prints `REFUSED` and writes
   nothing (#1984); before, the hard-stop guard could park the issue. Write the line by hand or give the issue a
   Sigma label first.
4. **If you plan chains from the map with an assignee filter,** the filter is now applied from the map's stored
   assignees (#1993). A member with none (an outside node, or a map written before assignees existed) counts as not
   yours, and the plan says how many. Only a map with no assignees on any member still falls back to a live read.
   Run `dag.py rebuild` once so every node carries its assignees.
5. **`dag.py rebuild` reads up to 500 outside numbers in one run (#1988)**, where 1.0.19 read only part of them per
   run; `--outside-max N` changes the number (hard cap 2,000). The periodic refresh still reads 25.

What stays unchanged:

- Your `.sdlc/config.json` is never rewritten by an update.
- `dag.py html` still makes no GitHub call; titles come from a file `dag.py titles` writes.
- `deps.py report` without `--sheet` prints the same JSON lines, with new fields added.

## The map's pages

- **Assignees on the pages, and an index you can sort and filter (#1982, #1985).** The map keeps each member's
  assignees from the listings the refresh already reads, at no extra call. Chain pages show them on each node; the
  index carries each chain's top priority and its assignees, sorts by priority, open members or number, and filters by
  assignee (`unassigned` and `unknown` included) and priority. A map written before this release reads as "assignee
  unknown", never "unassigned". On the private backlog, every listed issue (399 of 482 nodes) carried its assignees.
- **A goal's page draws the work that waits on it (#1986).** Its open dependents are drawn to its right, muted, at
  most 40, with the count in the heading. On the private backlog, a stuck root's page said 19 open issues wait on it,
  matching `dag.py stuck`.
- **Titles without a hand-made file (#1987).** `dag.py titles` lists open titles over REST (one call per 100 issues,
  at most 50 calls) into `.sdlc/state/dag-titles.json`; `dag.py html` reads it and says when it is older than the
  map. On the private backlog: 399 titles in 5 calls, 6 s.
- **A page tells the truth about the assignee filter (#1997).** A page is drawn for no runner, so it says the plan
  applies no assignee filter for that reason, rather than claiming the map holds no assignees.

## The map itself

- **One rebuild reads every outside number (#1988).** The report prints `unread N` and, when N is not 0, the exact
  command to run. On the private backlog: `unread 0` after one rebuild (83 outside reads, 64 s), where 1.0.19 needed
  four runs.
- **An open outside issue keeps its assignees, priority and life (#1994)** from the one read the rebuild already
  makes, so a checkpoint issue with no Sigma label is drawn under its real assignee.
- **A chain plan read from the map applies the assignee filter from the map (#1993).** See upgrade step 4.

## Reviewing dependencies with `deps.py`

- **A review sheet for a whole band (#1991).** `deps.py report .sdlc --label <band> --label <band> --sheet` reads the
  union of open members once and prints one markdown sheet: each goal's comment edges, weak refs and mentions with
  their snippets, then a tally. It is a review aid and writes nothing. On the private backlog: all 63 open P0 and P1
  goals in one command, 257 s.
- **A two-way wait is flagged, by direction (#1990, #1995).** Each blocking ref is read once more. A ref whose own text
  says it waits on the goal is `reverse` on an edge, and `likely-dependent` on a weak ref, where the weak read is
  probably the wrong way round. Closed refs are skipped and counted. A body edge that only quotes another issue's
  marker is `quoted`. On the private backlog, the four refs the first version flagged `reverse` all read
  `likely-dependent`, and nothing was flagged two-way wrongly. A wait on one stage of another issue ("stays a
  stage-2 prerequisite") is not flagged: that one still needs a person to read it.
- **`deps.py unmark` takes a wrong line back (#1989).** It removes the `**Blocked by:** #N` line, drops the stored map
  edge and leaves a note with the reason. A wait written in prose is reported and left alone.
- **`deps.py mark` refuses a foreign issue before any write (#1984).** See upgrade step 3.

## Also in this release

- **`define.py open` uses the configured repository for the unit label (#1983).** See upgrade step 2.
- **goal-review runs the design quality check and rejects on it (#1781).** Prose only: the reject depends on the
  reviewing model following it.
- **The goal-review feature stamp runs inside the filing scope (#1992),** so with hard stops on, an epic-only ticket is
  stamped instead of raising.
- **Registries, the skill card and the changelog are complete for the new writers (#1996).**

## Not in this release

- Measurements of the pages, titles and sheet on a second repository, and of the index at 1,000 chains or more on a
  real board (synthetic rows only: 1,000 chains is 222 KB).
- A dependency written only in prose about one stage of another issue is still left to a person.
