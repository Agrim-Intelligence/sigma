# Docs behavioural claim sweep

Dimension D8. Measured commit: `793a1ed612f6`, 2026-10-13, local run, no network except the issue filing.
This is the second D8 sweep. The first (`docs-claim-sweep-f627d06ad3f6.md`) read for stated numbers and defaults.
This one reads for BEHAVIOURAL claims: "X does Y", "X refuses Z", "the loop does W when V", exit codes, defaults,
"command C exists", "file F holds ...", and checks each against the code.

## Method

1. Scope: every page under `docs/` outside `docs/launch/` (frozen evidence records): the 42 top-level pages and the
   four pages under `docs/bench/`. 46 pages. The README and `CONTRIBUTING.md` were covered by the first sweep.
2. Eight readers, each given a fixed brief, read their pages in full and extracted the checkable claims. The long
   pages were split: `branching-model.md` into two halves, and `label-model.md` with its two companions, the two
   Dossier pages, and the cloud/backlog/upgrading/board group had a reader each.
3. Each claim was checked by grep and by reading the script, constant, error code or test that pins it. Where the
   page names a command, a harmless `--help` or a pure function (`render.py`, `features.parse_body`,
   `decision_rule.py --check`, `bench_tasks.py check --frozen`, `scrub.commit_secret_hits`, the
   `enforcement_table.py` generator diffed against `docs/enforcement.md`) was run. No test suite and no live GitHub
   call was made by the readers.
4. Classification: TRUE, FALSE (the page says something the code does not do), STALE (a number or state that moved),
   UNVERIFIABLE (needs live GitHub, a live host, a one-off measurement, or an external tool, with the reason stated).
5. Limits of this method, stated plainly: the claim counts are the readers' own counts (one claim per checkable
   sentence or table row; dense rows counted as several), so they are approximate. Each defect below was reported by
   a reader with a code location. The author of this record re-checked the line-number citations in
   `docs/threat-model.md` and `docs/privacy.md` against the files, and ran the build-scale measurement; the other
   defects were taken from the readers' code citations without being re-derived one by one.

## N, counted

| Quantity | N |
|----------|--:|
| Pages swept (42 top-level, 4 under `docs/bench/`) | 46 |
| Pages read in full by a reader | 46 |
| Behavioural claims checked (approximate, readers' counts) | about 1,500 |
| TRUE | about 1,375 |
| FALSE | 33 |
| STALE | 40 |
| UNVERIFIABLE (each with a reason, listed below) | about 61 |
| FALSE and STALE sites corrected in this pull request (docs only) | 70 of 73 |
| FALSE sites in `docs/uninstall.md`, corrected on main by #1260 while this sweep ran | 2 |
| FALSE and STALE sites left open | 1 (needs GitHub, see below) |
| Filed as `launch:next` issues | 3 (#1277, #1278, #1279) |
| Code-side findings | 1 defect (the stop file is noticed only at the next tick), 1 design looseness (`render.py` accepts `last` after any phase) |

## Coverage gaps (named)

- `docs/uninstall.md`: read and checked in full at the earlier commit. It was not edited here; the pull request that
  rewrote it (#1260) landed first and corrects both findings (the stop-file wait and the missing branch and cache steps).
  The page was not re-read after that change.
- `docs/features.md` rows 12, 17, 33, 38 and 39 (long table cells) were only partly checked; the `/sigma-scope` and
  `/sigma-triage` skill prose it summarises, and `sigma-wizard` behaviour, were not checked line by line.
- `docs/branching-model.md`: the loss-guard narrative (which revert shapes are caught) was spot-checked through its
  two strict-xfail tests only; the ownership-gate edge cases and the chat-landing wording were checked for the
  existence of the symbols, not traced end to end; the "eight causes" was not counted individually.
- `docs/label-model.md`: the scope and owner hold wording (sections 2b-ii and 2b-iii) was checked for identifiers and
  gate functions, not for the comment and ledger entry reaching the named owner; `sdlc:blocking` re-ranking was not
  checked.
- `docs/cloud-sessions.md`: only the headline constants and shapes of the per-field REST mapping tables, not every
  cell; the write-site counts and the per-slice historical deltas were not recounted.
- `docs/upgrading.md`: the `claude plugin` and `codex plugin` CLI verbs were not checked.
- `docs/output-contract-detail.md`: the Codex `model_selection` mapping through `host-model` was not traced.
- `docs/publish-runbook.md`: the per-item ceilings (revisions, titles or reviews) were not individually grepped.

## Defects found and corrected (grouped by page)

| Page | Defect | Class |
|------|--------|-------|
| `branching-model.md` | the unit `priority` row said nothing reads it for ordering; `sources._unit_priority_rank` does, on by default since #2284 | STALE |
| `branching-model.md` | "nothing merges a feature branch" (two sites): the attended landing engine and the Slack unsafe-merge command can | STALE |
| `branching-model.md` | the delete sites "neither reads `rec["base"]`": both pass it to `_delete_remote_branch` as a refusal guard | FALSE |
| `branching-model.md` | "3 calls ... 15 with it", contradicting the section 6e figure of 4 and 17 | STALE |
| `branching-model.md` | `define.py set-priority` and `bump-priority` omitted the required `<sdlc_dir>` positional | FALSE |
| `branching-model.md` | section 18: "no live judge exists"; one shipped in #2380, opt-in and metered | FALSE |
| `label-model.md` | the same live-judge statement, two more sites | STALE |
| `label-model.md` | `_ensure_labels` "three membership labels and six overlays"; it seeds `sdlc:goal`, `sdlc:parked` and seven overlays | FALSE |
| `label-model.md` | board Status "In review"; the default column is QC | FALSE |
| `label-model.md` | the blocker table listed the PROMOTED and human-proposal rows that `blockers.classify` never returns | STALE |
| `agent-rules-detail.md` | "one commit with no trace and the pass stops" omitted the rebase-acks exit (#2756) | STALE |
| `dossier-pipeline.md` | REJECT "writes one comment and nothing else"; it first closes the Stage-1 design PR (#2482) | FALSE |
| `dossier-pipeline.md` | the CONFIRM sequence lacked the `work.py merge-design` step | FALSE |
| `dossier-pipeline.md` | the accept path lacked its seventh step (feature priority) | FALSE |
| `dossier-pipeline.md` | "eight seeded labels" (nine); `epic` colour (`d4c5f9`); `LocalSource` guard name | STALE (3) |
| `how-the-dossier-pipeline-works.md` | heading "The three extra questions"; reject "creates nothing" omitted the design PR close | STALE (2) |
| `worktrees-and-merging.md` | read-only or fork PR records `done` (it records `review`); BEHIND rebased once (up to 3); failing check records `failed` at once (after CI-fix cycles); lands with `gh pr merge` (REST); review state "fail-open" (fail-closed except the thread count); `work.enabled` default only in a scaffolded config | 3 FALSE, 3 STALE |
| `features.md` | landing "with `gh pr merge`" (REST) | STALE |
| `running-the-loop.md` | "no code reads `gates.irreversible_actions`" (read when hard stops are enabled); `fable` reachable only with a raised max tier | FALSE, STALE |
| `release.md` | the first-release placeholder-date branch (that heading no longer exists); the "tag does not exist" sentence | STALE (2) |
| `architecture.md` | the diagram said "arm GitHub auto-merge" and sent BEHIND to park; "Always-on intent hook" | FALSE (2) |
| `board.md` | the Needs-a-human filter listed the legacy label, which `board_spec.py` dropped | FALSE |
| `backlog.md` | local goals "in filename order"; parked card "Blocked"; `project.enabled` "on by default"; the sweep "once per batch"; priority order lacked the unit rank | 4 FALSE, 1 STALE |
| `cloud-sessions.md` | landing helpers "nothing calls them yet"; stale ratchet line numbers; the `status.py` line; the 47-site snapshot read as current | 1 FALSE, 3 STALE |
| `public-snapshot.md` | the build-scale figures (re-measured, below); "every build is REJECTED until placeholders are filled"; the rename-ordering note said the rename was pending | STALE (3) |
| `onboarding-control.md` | "the only skip is Windows"; "weekly" legs (they are nightly) | FALSE, STALE |
| `action-log-and-ledger.md` | the action log "on by default"; the hard-stop net unconditional; ledger kinds incomplete; ledger "off by default" nuance | FALSE (4) |
| `output-contract.md` | `last` implied only after P7 RETRO; `render.py` accepts it after any phase | FALSE |
| `upgrading.md` | "about 21 `gh` calls an hour" against the code's ~13 per sweep | reconciled |
| `threat-model.md` | eight drifted `file:line` citations | STALE (8) |
| `publish-runbook.md` | a Python 3.9 CI check that does not exist | STALE |
| `privacy.md` | three drifted citations and the SMTP opt-in omitted | STALE (3) |
| `bench/task-sourcing.md` | "all 12 tasks" (15); timings differ from the committed JSON by hundredths of a second | STALE (2) |
| `bench/preregistration.md` | A3 loop bound (`--max-attempts`, default 10); stop code 78 | FALSE, STALE |

## Not corrected here

- `docs/uninstall.md` (2 FALSE at the earlier commit): corrected on main by #1260. The code-side cause (the stop file
  is noticed only at the next tick) is #1278.
- `docs/how-branching-works.md` lines 48 to 50 (STALE, held): lists #1571 as an open adoption-readiness item, while
  `work.py` `_declaration_moves_the_base` carries the fix. Whether the issue is closed needs GitHub.
- Skill prose outside `docs/` that the readers found stale (a regex in a goal-design reference, "rebases once" in the
  landing reference, "seven answers" in the dossier skill): filed as #1279.

## Re-measured: the build scale in `docs/public-snapshot.md` (#1251)

One run of `tools/build_public_tree.py` at `793a1ed6` (`--review-level clean`, the source repository, `--allow-public-source`,
the private patterns file), alone, with 55 percent of memory free beforehand, under `/usr/bin/time -l`: 1282 files,
26.6 MiB exported; 37.3 s wall (scans 25.9 s, materialise 10.1 s, export repository 0.8 s); peak resident set 97 MB, peak
footprint 42 MB. The run ended REJECTED (exit 1) on three `source-slug` findings in launch evidence lines (filed as
#1277), after every scan had run, so the figures are the full cost. The 10x and 100x lines in the page were re-derived
by linear extrapolation from these figures and are labelled as not measured. The scratch export and report were deleted
afterwards.

## UNVERIFIABLE claims, with the reason

| Where | Claim | Why it cannot be checked here |
|-------|-------|-------------------------------|
| `branching-model.md` lines 184, 268-288, 915, 939 | the lost-goal-number counts, the 40-of-40 writes figure, the incident file count, the reflog patterns "against real git" | one-off past measurements |
| `branching-model.md` lines 611-622 | transport and cost columns of the GraphQL, REST and `gh` rows | not traced per row |
| `branching-model.md` | the 4-call to 17-call count | needs a recorded-runner run; a pinned test asserts it |
| `branching-model.md` | hosted-run results, branch-protection bypass behaviour, host refusal of branch names starting `refs/` | live GitHub |
| `agent-rules-detail.md` | the host version that resolves the bare plugin id; a live-board validator run of 2026-09-11 | needs the host CLI and a live board and model call |
| `label-model.md` | "either all of it lands, or none of it does" (atomicity of the aliased mutation); the 57-of-89 and 126-of-605 figures; two issue timestamps | a GitHub server property; live board and history |
| `how-the-dossier-pipeline-works.md`, `dossier-pipeline.md` | the real-run issue numbers and counts; the payload-size figure; `git check-ignore` exit codes | live GitHub or historical artifacts |
| `cloud-sessions.md` | the "taken from the issue, not captured live" wording; GraphQL versus REST parity measurements | live cloud session |
| `board.md`, `board-fields.md` | `copyProjectV2` survival, board timings | live GitHub |
| `upgrading.md` | the per-pass token figures and the verdict counts | logs of another repository |
| `public-snapshot.md` | the runner-minute CI figure, the 2026-10-02 scan counts | live GitHub run data; past run |
| `onboarding-control.md` | the 76-test count and dated timings | needs pytest collection and past runs |
| `output-contract-detail.md` | the Codex model mapping; the subagent banner "not verified end to end" | live host |
| `threat-model.md` | the measured percentages and corpus counts; whether `watch_daemon.py:906` counts as "the watcher loop" | a measurement harness; judgement |
| `publish-runbook.md` | which GitHub surfaces `scan` reads; live `--apply` behaviour; the scale estimates | live GitHub; labelled estimates |
| `platform-support.md`, `worktrees-and-merging.md`, `ownership.md`, `parallelism.md`, `invocation-adapter.md`, `release.md` | merged-PR counts; live Codex runs; the coverage figure; absent CODEOWNERS branch protection; the entry-file audit; per-authorize timing; the S1 response policy | live GitHub or host, a measurement, or policy |
| `codex-autonomy.md`, `running-unattended.md`, `knowledge-graph.md`, `companions.md`, `upkeep-part-b.md` | the `codex` and `graphify` CLIs; the drill record; host behaviour of skill overrides; the hosted validation of 2026-10-10 | external tools not installed; live GitHub |
| `bench/task-sourcing.md`, `bench/token-budget.md` | stars, licences, PR dates, search counts; the session totals and rate-window readings | live GitHub; owner validation runs whose raw data is not in the repository |

Checked and held TRUE on the bench pages: the decision-rule operating characteristics match, the frozen manifest hash
equals the recorded hash, and every row sum and ratio in the token-budget table recomputes.

## Result against the D8 threshold

"0 unresolvable claims" is NOT claimed. About 61 claims are unverifiable from the repository for the stated reasons,
and the coverage gaps above were unread or only partly checked. What this sweep supports: for the 46 pages, the
behavioural claims that could be checked against code were checked, 73 were wrong or stale, 70 of those are corrected here,
72 are corrected (2 of them by #1260), and 1 remains open with a stated reason.
