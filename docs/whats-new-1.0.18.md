# What is new in 1.0.18

This page lists every change in this release in grouped, readable form, each with its issue number. The same
changes are in the `1.0.18` section of the [CHANGELOG](../CHANGELOG.md) in the order a maintainer reads them.
Start with [Upgrading from 1.0.17](#upgrading-from-1017) if you only want to know what changes for you.

The first theme is Sigma Chain doing what its name promises on a tangled backlog: `/sigma-chain <N>` now asks every
question the chain behind goal N needs, deep members included, on the first sheet, before anything is armed, and each
answer changes what `arm` and `run` do. The second is a page per chain: `chain.py html` draws the whole plan with every
question pinned to the member it is about, and `dag.py html` draws the stored map one chain at a time. The third is a
fix to triage that could leave an issue with two priority labels.

Everything in the first two themes was proven against an offline replay of a tangled backlog with a fake `gh`, not
against a live repository. Where an entry was not measured live, it says so, and the last section lists what is open.

## Upgrading from 1.0.17

Steps 1 and 2 apply to everyone. Steps 3 and 4 apply only if you use Chain. Script paths are under
`skills/sigma-loop/scripts/`.

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`,
   wait for it to exit, remove the file, then start the watcher again.
2. **Run `/sigma-doctor` once.** A new warn-only row lists open issues that carry two priority labels. Keep the one
   you mean and remove the other; triage `enact` now replaces an old priority label itself (#1920).
3. **Re-plan a chain you planned on 1.0.17.** The sheet asks new questions (`park:`, `wait:`, `elsewhere:` and, if
   you opt in, `irreversible:` for a paid label), and `chain.py answer` and `approve` no longer write to GitHub:
   every answer is enacted by `arm`. Run `chain.py plan <sdlc_dir> <N>` and `sheet` again before you answer.
4. **If paid work is marked by a label on your repository,** list it under `chain.paid_labels` in
   `.sdlc/config.json` (default: none). A member carrying one is asked the spend question up front.

What stays unchanged:

- Your `.sdlc/config.json` is never rewritten by an update.
- Nothing starts by itself: Chain is run by hand, one cycle per `run` call.
- The gate, the stored map and label drains read dependencies as before; prose waits are read by Chain's plan only.

## Sigma Chain asks everything up front

- **Every question on the first sheet, every answer enacted.** The sheet used to leave four things to the run, which
  then stopped on them with nothing asked. Now:
  - `park:<n>` says why a member is parked (its recorded park question, else the first line of its note, cut short)
    and asks release it or keep it parked; release goes through the existing unpark gesture at `arm`.
  - `wait:<a>-<b>` asks confirm or dismiss for a wait read from prose whose blocker is open; confirm writes the
    marker `deps.py mark` writes, dismiss writes the dismissal marker.
  - `elsewhere:<n>` asks wait for them or take it over for a member somebody else holds; take over assigns you and
    removes the old assignee (one assignee per issue). A member in progress is offered wait only.
  - `irreversible:<n>` is also asked for a member carrying a label in the new opt-in `chain.paid_labels`.
  `answer` and `approve` write nothing to GitHub; `arm` enacts the answers first, idempotently, and a failed step
  leaves the chain unarmed until `arm` is run again. The cap and a cross-unit "stop here" are now enforced; they were
  recorded only. The forecast is computed from the answers, `approve` prints it, and it equals the run.
- **The run picks what can move and the stop names what holds the chain.** A sentence that says a goal does not wait
  ("does not wait for #N") is no longer read as a wait; six more wording classes are read; a body is read to its full
  length. The stop line names the members nothing else waits behind, and every other one on its own line.
- **A cycle reads each issue once.** About 560 to 680 GitHub calls per run cycle on the offline replay, against about
  800 to 1,130 before. Not measured on a live repository.
- **New sheet structure and verdicts (#1902–#1909).** Waits written in prose or a comment become `unconfirmed` edges;
  a blocking-only issue is `not-a-goal`; a checkpoint (`sdlc:checkpoint`) is never worked; a follow-up awaiting its
  owner is never armed by approve; a closed prerequisite with something still open is asked about; the sheet opens
  with the forecast and groups what a person can act on; a public regression scenario guards it.

How it was checked: a private replay of an 81-issue backlog through a fake `gh`, with a scripted owner, checked
against readers' notes. A new criterion, "asked up front", passes in all 42 runs of the final matrix: every
question is on the first sheet, no run stops on anything the sheet did not ask, and deep members are covered. It
fails on 1.0.17 in every run. Runs where the scripted owner releases, confirms and approves everything finish the
goals the readers expect with no question at run time.

## A page per chain

- **`chain.py html <sdlc_dir> <N>` (#1924).** Draws the stored plan: every member, deep ones read from prose
  included, unconfirmed waits dashed, a colour per state (checkpoint, not a goal, awaiting owner, paid gate,
  parked), done prerequisites behind a toggle. A panel lists "N questions before anything is armed" in the sheet's
  own numbering and wording; each has a badge on its member, and clicking either highlights the other. The sheet's
  last line names the command. No network, no library, no GitHub call.
- **`dag.py html --goal N | --all` (#1924).** One offline page per chain from the stored map, never a whole
  connected group drawn as one, plus an index with `--all`. It says that prose waits are not in the map and names
  `chain.py html` for the full plan. Default output `.sdlc/state/dag-html/`; an `--out` that would dirty a checkout
  is refused.

## One priority label per issue

- **Triage replaces the old priority label (#1920).** `enact` used to add the plan's priority beside an existing one,
  so an issue could end with two and every reader took the better rank. It now swaps in one step, cleans up a double
  the issue already carries, and does nothing when the label is already right. Filing through
  `handoff.create_tracked_issue` drops a caller's extra priority label. The doctor lists open issues with two.
  Measured on a private scratch repository: the old code reproduced the double; the new code swapped, healed and was
  idempotent.

## Also in this release

- **The verify worker grant reaches vitest** (`VITEST_MAX_FORKS`, `VITEST_MAX_THREADS`).
- **Plan-review asks which force an abstraction answers; review reports `missing structure` (#1543).**

## Not in this release

- A dependency named only by a slice label ("S2a") and never by issue number is not read; the plan says so.
- "In progress in a live session" is read from the in-progress label alone, with no lease check.
- Two answers enact nothing different from their alternative: "treat it as a checkpoint" on a not-a-goal member,
  and "plan its slices instead" on an upper-tier end goal.
- Nothing in Chain or the pages is measured on a live repository yet; very deep chains open small on the page.
