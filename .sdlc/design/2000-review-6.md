# goal-review round 6: Dossier #2000, revision 5 ("lean goals")

**Reviewer:** fresh, independent subagent (Stage 2), mechanism `subagent`. I formed this view from
the code before reading `2000-review-5.md`. I wrote only this file: no labels, comments, Epic,
`compile_plan.py` or design edits.
**Tree verified:** `origin/main` at `504de5c1` (plugin 1.0.20), exported read-only. Every `file:line`
is against that tree.
**Owner rulings R1-R13:** treated as decided. Every finding is about whether the design implements
them.

**Verdict I would record: REJECT.** It would produce exactly two things:
- `work.py close-design .sdlc 2000 --comment "goal-review: REJECTED"`;
- `loop.py note .sdlc 2000 "goal-review: REJECTED -- N1..N5 below -- design PR: <result>"`.

There would be no `sdlc:designed`, no Epic, no children and no board move.

## Step 1: schema and script checks

- `goal_design.py requirements`: `ok: true`, 0 violations, 0 warnings. FR-31 to FR-73 are each in exactly one slice row.
- `goal_design.py quality`: `ok: true` (lane large), 0 violations. One row is open: `data_retention_privacy` (D-33).
- `goal_design.py sweep-budget .sdlc --mode lane --lane large`: `rounds: 3`, which matches `**Budget**: 3`.
- Sweep `capped`. 1 seed is unswept (S-41), which matches the banner. Round 7 still found new
  in-scope sites, and this review finds more (below).
- Phase-prose headroom, re-measured with `measure_all`, matches PC-34 exactly. In tokens:
  implement 0, review-post-pr 3, plan-review 8, research 13, review-pre-pr 14, goal-slot 21,
  plan 21, orchestrator 22, retro 48.

## Findings, ranked

### N1 (blocking, slices 4 and 6): another machine cannot resume a slice, because the gates' evidence is local

The design moves the *visibility* of between-slices onto GitHub. It does not move the *evidence*
that the gates need in order to let slice 2 run on a different machine.
- The plan-review record is `<sdlc>/state/gates/<stem>.json` (`work.py:1515-1521`). That path is
  gitignored (`.gitignore:12`). Both `_hard_plan_gate_refusal` (`work.py:6127-6175`) and
  `_plan_review_refusal` (`work.py:6221-6260`) read only that local file.
- So machine B's `pr` for slice 2 is refused unless plan-review runs again, which FR-42 forbids.
- The phase-record gate reads "the goal's own action-log file" (`phase_gate.py:1-30`), which is
  also local. Machine B's `record done` for the final slice has no research, plan or plan-review
  rows, so it is refused.
- The work record, the red/green proofs and the reviewer receipts are local too.
- Because of this, the reliability quality row ("delete the owner's local state, then another
  machine resumes slice 2") cannot pass as designed.

**Change.** Choose one of these and make it an FR with a control:
- (a) Put the approving plan-review verdict, the plan sha256 and the manifest scope fingerprint in
  a GitHub-durable carrier (a timeline marker). Lean goals' plan gates and phase gate then accept
  the goal-level records from it.
- (b) Resume only on the owning machine. Adopting the goal on another machine means one recorded
  re-plan-review, and the 72-hour mark becomes a visible park with that lever.

### N2 (blocking, slice 4): in a mixed fleet, a 1.0.x machine picks a between-slices goal and closes it after one slice

- The design releases `sdlc:in-progress` between slices (Detailed design, step 5).
- 1.0.x knows nothing of `sdlc:between-slices`. Its eligibility rule is
  `not_eligible_labels` (`sources.py:2172-2183`), so the goal is pickable there.
- A 1.0.x machine then runs the full SDLC on the whole goal. It writes `Closes #N` on a default
  base (`work.py:4326-4327`), or Sigma's own close fires on any base (`work.py:7795-7800`). The
  issue closes with slices 3..n never built.
- D-42 scopes the mixed-fleet risk to filings only. There is no repository-level version floor in
  the code (searched).
- AGENTS.md says Sigma is installed across whole organisations, so mixed versions during rollout
  are certain.

**Change.** Keep a label that every 1.0.x machine already excludes, for example: keep
`sdlc:in-progress` through between-slices. The 2.0 picker then explicitly queries
`sdlc:between-slices` and adopts the goal when GitHub shows a merged slice and no open slice PR.
Control: a fixture running the 1.0.x eligibility rule against a between-slices goal must not pick it.

### N3 (blocking, slice 4): the new overlay's eligibility and board column are unspecified, and the obvious reading contradicts FR-64

- `docs/label-model.md` §2 calls every overlay "orthogonal" and lists them. `overlay_labels()`
  (`sources.py:2147-2170`) is "THE CHOKEPOINT". Registering the new label there makes it
  *not eligible* (`sources.py:2182-2183`), so it would never be picked, which contradicts FR-64.
  Not registering it leaves `triage`, `status._github_counts` and census parity to drift (#1393).
- Under the shipped default `queue_source: "status"` (`config.json.tmpl:66`), only `Ready` cards
  are picked. The uncarded fallback drops carded issues (`sources.py:2452-2458`), and `await_merge`
  moves the card to QC (`sources.py:3243-3256`). So a between-slices goal is unpickable on the
  default board config. The design never names a column.
- The label-creation path is missing from the blast radius:
  - `_LABEL_COLORS` (`sources.py:966`), plus `docs/label-model.md` §12 ("seeds exactly ten"), plus
    the tests that pin it (`tests/test_setup.py`, `tests/test_sources.py`);
  - `complete()` stripping the label;
  - reconciler case (a), which strips activity overlays on closed issues.
- BR-59 says only "documented beside `sdlc:in-progress`".

**Change.** Add an FR for each of these:
- the eligibility decision (an explicit lane, not `overlay_labels`);
- the board column on release;
- seeding;
- strip-on-close.

Add a control for each.

### N4 (high, slice 4): resuming duplicates a slice, and "dead worker" is undefined across machines

- FR-64 and FR-66 resume "at the first slice without a merged pull request". A slice whose PR is
  *open* has no merged PR, so it is run again and a second PR is opened.
- FR-38 releases "a claim held by a dead worker" after 72 hours. Another machine has no way to
  know that. `loop.py:1320-1333` says that without the ledger (off by default), nothing "can tell
  crashed from still being worked". `record review` also ends the lease (`loop.py:4720-4735`).
  So the 72-hour release can take a goal that is legitimately waiting on a human reviewer.

**Change.**
- Resume at the first slice with neither a merged nor an open PR.
- Define "dead" from GitHub facts, or drop the release in favour of a visible stall plus an owner
  lever.
- Add controls for both.

### N5 (high): D-41's recovery claim is false, and dependents can be held forever

- D-41 says a slice PR "merged outside Sigma is observed by the reconcile pass when a machine next
  runs".
- That pass reads only local work-record flags (`loop.py:4869-4900`, `work.py:4997-5012`). Only
  the owning machine can observe the merge.
- The case that breaks: the owner is dead, and a human merges the final slice PR. The issue never
  closes.
- `Blocked by` dependents are held while the blocker is open (`docs/branching-model.md` §13a,
  "This is what releases a dependent goal"). They wait forever.
- For default-branch repos this is a regression. Today GitHub's `Closes` covers a human merge, and
  FR-36 removes it everywhere. Nothing else in the code relies on closing keywords; I grepped for
  `closingIssuesReferences` and found nothing, so the other D-41 sites are fine. Those sites are
  the dependency gate, `complete()` (`sources.py:3258-3290`), the board and the ledger.

**Change.**
- Add a GitHub-derived merge observation for lean goals, for example over overlay or claimed goals
  with slice PRs, bounded per pass.
- Add a control: delete the owner's state, merge the final slice PR by hand, and another machine
  must close the issue.

### M1: the filing-route marker does not exist, and as filer text it is forgeable

- FR-71 keys the bypass reconcile on "the filing-route marker".
- `handoff.py` stamps only `UPKEEP_FINDING_MARKER` (`handoff.py:88`). No FR or slice creates the
  route marker.
- A raw `gh issue create` can paste the marker into its body and escape reconcile. That
  contradicts the design's own security row: "0 ... exemptions granted from filer text alone".

**Change.**
- Make the routed set Python-recorded, for example issue numbers logged by `create_tracked_issue`,
  or bind the marker to that record.
- Assign it to slice 7.

### M2: where a discovered slice is written while a PR is open or awaiting merge

- D-43 says discovered slices are "committed on the slice branch in the goal worktree".
- At "awaiting merge" no session is running.
- A commit onto an open, reviewed PR changes the reviewed and verified diff.
- FR-71's watch-tick conversion has no worktree at all.
- The design never says who writes the manifest in these states, or where.

### M3: the run-the-control table misses guards

These guards have no break-and-red row:
- FR-33 (legacy cutover; break: classify a mid-implement legacy goal as lean);
- FR-38;
- FR-43 (delta-only review gate);
- FR-44 (cap park);
- FR-48 (bare-number marker; break: write `#N` and a phantom blocker appears);
- FR-65 (marker spoofing; break: accept the marker from any merged PR);
- FR-67.

### M4: FR-42's trigger is still incoherent (residue of review-5 M3)

"WHEN a slice ... starts, THE phase gate SHALL require ... code review verdict" asks for records
that exist only later. FR-70 added the right enforcement point, but FR-42 still states the
impossible one.

### M5: the landing is one large merge, and its cost is unstated

- Main churn over the last 14 days: 219 commits in total, including 39 on `work.py`, 27 on
  `loop.py` and 25 on `sources.py`. These are the unit's core files.
- Upkeep is off by default (`docs/branching-model.md` §13d), and landing is human-only (§13).
- **Change:** state the upkeep cadence, who resolves conflicts (`/sigma-rebase`), and a serial
  full-suite verify of the landing tip.
- Slice 12 cannot itself perform FR-72's landing. Use the existing `sdlc:checkpoint` overlay
  ("met by a unit landing").

### Low

- **L1.** FR-64's order relative to the `sdlc:blocking` lane is unstated.
- **L2.** `newest_label_event` returns None past 1000 events (`gh_api.py:995-1005`), which silently
  disables the stalled mark on long timelines.
- **L3.** `docs/branching-model.md` §13a and the header text at `work.py:4329` describe `Closes` on
  main, and both need the lean wording.
- **L4.** D-40's "same login" boundary is nearly vacuous where the operator and the loop share one
  login, as they do here.
- **L5.** The slice-state marker should be bound to `work.py`-created head branches, not to PR body
  text (FR-65).

## Adjudication (2a)

| ID | Bucket | Note |
| --- | --- | --- |
| D-1, D-2, D-3, D-20, D-27, D-30, D-31, D-32, D-35, D-36, D-37 | resolved | answered by rulings or by round 7 and they hold. D-32's "release a dead worker's claim" is wrong as built (N4) |
| D-15, D-17, D-24, D-28, D-29, D-33, D-38, D-39, D-44 | open, not blocking | every slice can start whichever way each is decided |
| D-34 / S-41 | open, not blocking | slice 3 owns the deletions; it can start, and the ratchet catches failure |
| D-21, D-40 | open, not blocking | owner boundary question; M1 must be fixed first |
| D-41 | **blocking** (slice 4) | N5: the stated recovery does not exist |
| D-42 | **blocking** (slice 4) | N2: it is scoped to filings and misses picking |
| D-43 | open, not blocking | M2 |
| B-3 | open, not blocking | no slice waits on it |
| X-1..X-3, X-7, X-11..X-14, X-18, X-21, X-23, X-26..X-30 | resolved | exclusions hold |
| PC-26, PC-27 | open, not blocking | X-21 and D-20 cover them |

## Status of review-5's findings

| Finding | Status |
| --- | --- |
| H1 | **fixed**: FR-63, BR-53, and a control row |
| H2 | **fixed**: FR-36 (Refs always) and FR-67, with a control. Edge: a filing between the final merge's close and `done` is orphaned (low) |
| H3 | **partly fixed**: the age and overlay are visible on GitHub (FR-37, FR-38, BR-62), but resume is unbuildable (N1, N3, N4) and not mixed-fleet safe (N2) |
| H4 | **fixed**: FR-68 is caller- and marker-based. Verified at `loop.py:5612-5624,5786-5791`, `decompose_goal.py:70-80` and `goal_size.py:52` |
| H5 | **fixed**: urgent bucket plus Python disjointness (FR-49). `file_triage.py:34` is confirmed |
| H6 | **fixed**: the route is set at classification (FR-34), with BR-55 |
| H7 | **fixed**: feature unit (FR-72). Landing cost is in M5 |
| M1 | **resolved by R10**, with the FR-55 net |
| M2 | **fixed**: FR-55 fails closed and FR-69 handles class gaming |
| M3 | **fixed in FR-70**, but FR-42's text remains (M4 here) |
| M4 | **reworded, not fixed**: FR-66 has the open-PR duplicate (N4) |
| M5 | **mostly fixed** by FR-65. The discovered-slice write location is open (M2 here) |
| M6 | **fixed** (D-38, cost table) |
| M7 | **partly fixed**: the `test-review-pre` budget phase was added; S-41 is still unswept |
| M8 | **fixed as measurement** (FR-58). The picking half of mixed fleets is N2 |
| L1-L3 | handled by X-30, D-44 and D-33 |

## Owner aims

1. **Armed count: mostly met.** The holes are a forgeable route marker (M1) and 1.0.x filings,
   which are counted but not stopped.
2. **Slices skip research, plan and plan-review: met on one machine.** It breaks across machines (N1).
3. **Small goals by judgment with a PR-time check: met** (R10, FR-55, FR-69).
4. **Cost and speed: unmeasured and honestly framed.** Per-slice review, CI and verify multiply
   per-PR fixed costs, and that is unmeasured.
5. **Seamless upgrade, core loop intact: not met.** See N1, N2, N3 and N5.
6. **Host parity: met in mechanism** (Python reconcile in `_next` and the tick), apart from M1.

## What I could not verify

- The live GitHub numbers (215 armed; 107 created versus 22 closed).
- The cost baseline (n=79).
- Whether `sigma-implement` has deletable prose worth at least one pointer line (S-41).
- The DAG renderer's handling of the new overlay.
- That no adopter relies on GitHub-linked issues outside this repo.
- No end-to-end loop run on any host.
