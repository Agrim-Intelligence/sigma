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

Closed in the second pass at the end of this record (2026-10-13); the list is kept as the first pass left it.

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

## Second pass (2026-10-13)

Goal: close the coverage gaps named above and settle every unverifiable claim that live GitHub state can settle.
Docs only, plus three passages of skill prose that repeated doc claims. Local work and GitHub reads only: every GitHub
read was a `gh api` REST GET (no GraphQL, no write), except filing the two issues below. `OWNER/NAME` is this repository;
"the source repository" is the private repository this one was exported from, whose issue numbers the older pages still use.
No pytest file was run by the readers; the author ran `--collect-only` once on `tests/test_onboarding_control.py`.

### Part 1: coverage gaps closed

Three readers took the gaps; each claim was checked against the code, a constant, an error code or the test that pins it.
Counts are the readers' own and approximate. Some of these claims were partly covered by the first pass, so a few overlap it.

| Gap | TRUE | FALSE or STALE | UNVERIFIABLE |
|-----|-----:|---------------:|-------------:|
| `features.md` rows 12, 17, 33, 38, 39 and the `sigma-scope`, `sigma-triage`, `sigma-wizard` prose | 88 | 5 | 2 |
| `uninstall.md` (re-read in full after #1260) | 31 | 0 | 2 |
| `upgrading.md` plugin verbs | 14 | 0 | 3 |
| `output-contract-detail.md` host-model mapping | 28 | 0 | 2 |
| `publish-runbook.md` per-item ceilings | 45 | 0 | 2 |
| `branching-model.md` loss guard | 40 | 5 | 3 |
| `branching-model.md` ownership gate | 17 | 3 | 1 |
| `branching-model.md` chat landing | 5 | 1 | 0 |
| `branching-model.md` "eight causes" | 0 | 1 | 0 |
| `label-model.md` sections 2b-ii, 2b-iii, 2b | 15 | 2 | 3 |
| `cloud-sessions.md` mapping tables, write-site counts | 66 | 9 | 3 |
| Total | 349 | 26 | 21 |

Corrections made (docs unless noted):

- `features.md` row 33 (and the same sentence in `skills/sigma-loop/references/landing.md`): `GitHubSource.complete()` on an
  open issue dispatches six `gh` commands, not five (the REST completion comment was missing); measured with an injected runner.
- `features.md` row 39: `goal_decompose.max_children` is only written into the meta-goal's instructions; no code enforces
  it. Filed as #1283.
- `skills/sigma-scope/SKILL.md`: step 7 offers the active members only when CODEOWNERS resolves no owner; the Caveats
  paragraph said filed issues are "proposed, not actionable", which is false under the default `ai_filed.triage.enabled`
  (they are armed at filing).
- `branching-model.md`: the "eight causes" of `sibling_gate` was a miscount (about 13 refusal sites; now listed by name;
  the stale docstring is #1284); the #1146 carve-out refuses an inherited file reverted to an OLDER version and excuses only a
  deletion, reads up to three guarded commands not two, and the manual-recovery push does not recover the `pull (finish)` and
  `rebase (continue) (finish)` reflog shapes; the ownership gate skips goal-less upkeep findings, writes the ledger entry only
  after the hold landed, and reads the author also when a unit has no owner yet; `--unsafe-merge` still runs
  `verify.command` first when upkeep is off.
- `label-model.md`: a gate hold writes TWO issue comments (the park comment with the machine line and the flag comment), and the
  ledger entry only when the ledger is on; `sdlc:blocking` re-ranking is on by default (`blocking_priority_override`) and only a
  literal `false` turns it off.
- `cloud-sessions.md`: "about 230 worst-case merges per hour" contradicted its own 66 calls per merge (about 75); eight
  line-number groups in the ratchet-site table were re-read from `scan()` (36 sites in 8 files, equal to `BASELINE`).

Verified without change: the 12-row `PR_FIELDS` table, the 5-row `mergeable` and rollup table, the 11-row `WRITE_POLICY`
table, every named page cap and breaker constant, the write-site history 101 down to 36, `uninstall.md` end to end, the
`claude plugin` and `codex plugin` verb grammar (against the copy of the host help text pinned in
`tests/test_readme_first_run.py`), the Codex `host-model` mapping through `predict.py resolve_host_model`, and the
`publish-runbook.md` ceilings against `tools/leak_refs.py` (100 revisions, titles and reviews; 1,000 pages; pacing; 400 writes).

### Part 2: claims that live GitHub state can settle

Each row: the doc, the claim, the read and its result on 2026-10-13. All reads are REST GETs on `repos/OWNER/NAME/...`
(this repository), the source repository, or the named upstream projects.

| Doc | Claim | Read and result | Verdict |
|-----|-------|-----------------|---------|
| `how-branching-works.md` | #1571 and #1564 are open adoption-readiness items | `issues/1571` and `issues/1564` in the source repository: closed 2026-09-26, each with a "moved to" comment; the re-filed issues are #13 and #9 here, both open. The fix for #13 is in `work.py` (`_declaration_moves_the_base`). Page rewritten with the public numbers | STALE, corrected |
| `branching-model.md` | "Nothing in the kit creates `.sdlc/features/` (#1576)" | #1576 is now #18 (open). `define.py _step_registry` creates the directory as the last step of `/sigma-define`. Reworded | FALSE, corrected |
| `onboarding-control.md` | five CI wall times 58.6 to 95.1 s, mean 74.3 s, run ids in `budget.json` | `actions/jobs/<id>` for all five jobs: each exists, success, onboarding step 95, 82, 58, 76, 61 s (second resolution) and planted-test step 263, 279, 181, 254, 228 s, equal to the file | TRUE |
| `onboarding-control.md` | the file "has since grown to 76 tests" | `pytest --collect-only -q tests/test_onboarding_control.py`: 76 collected | TRUE |
| `public-snapshot.md` | one CI run is about 129 runner-minutes, 34 minutes wall, run 36952374676 | `actions/runs/36952374676` and its jobs: five jobs summing to 128.6 minutes, 34.1 minutes wall, success | TRUE |
| `platform-support.md` | "9+ merged PRs showing the `sigma:approve` comment then same-account merge, as of 2026-08-06" | search for PRs merged by 2026-08-06 in the source repository (223), comments read on each: 97 carry an approving independent-review comment and all 97 were merged by an account that posted it. 78 carry the approve marker under its pre-rename prefix and 19 a bare `**APPROVE**` heading (second count by the independent review); none contains the current `sigma:approve` spelling | count TRUE (97 is at least 9); marker wording STALE, corrected |
| `ownership.md` | no branch protection rule requires CODEOWNERS approval | `rulesets` and `rules/branches/main`: one approving review and the `test` status check are required; `require_code_owner_review` is false; classic protection is a 404. Page clarified | TRUE for code-owner review; clarified (STALE as worded) |
| `cloud-sessions.md` | classic protection is a 404 here, rulesets enforce | `branches/main/protection` answers 404 "Branch not protected"; `rules/branches/main` lists the pull-request and required-check rules | TRUE |
| `cloud-sessions.md` | `issues/928` is closed, `state_reason` null, `merged_at` 2026-10-09T17:46:12Z | read again: identical | TRUE |
| `cloud-sessions.md` | this repository has no bot-authored issue or comment | every issue and pull request (`issues?state=all`) and every comment (`issues/comments`) filtered on `user.type == Bot`: none | TRUE |
| `cloud-sessions.md`, `running-unattended.md`, `bench/preregistration.md`, `onboarding-control.md` | #895, #721, #410 open; #304 closed | `issues/895`, `721`, `410` open; `304` closed 2026-09-29 | TRUE (4) |
| `label-model.md` | issue #226 carried `sdlc:goal` and `sdlc:parked`, parked 45.8 hours after the goal label | `issues/226/events` in the source repository: goal labelled 2026-08-11 12:55:35, in-progress removed and parked added 2026-08-13 10:45:14 and :15, goal label removed only 2026-08-20 | TRUE |
| `label-model.md` | story #2017 and epic #2020 labelled in-progress 2 s before each comment | events and comments in the source repository: label 11:34:41, comment 11:34:43; label 11:35:02, comment 11:35:04 | TRUE (2) |
| `label-model.md` | "57 of the 89 pickable goals", "126 of 605 issues" | point-in-time backlog counts, not reproducible from today's backlog. Restated in the page as dated figures from before 2026-09-29 | still UNVERIFIABLE; page no longer claims them as current |
| `dossier-pipeline.md` | the 2026-09-01 run wrote `priority: P2` into all six children | the six issues after epic #2020 (#2021 to #2026) each have one `priority:P2` labelled event, 11:33:01 to 11:33:13 | TRUE |
| `how-the-dossier-pipeline-works.md` | the throwaway repository `dossier-e2e-v2-2026-08-28` and its issue numbers | its `repos/` read answers 404; page now says so | still UNVERIFIABLE (repository gone or not visible) |
| `dossier-pipeline.md` | `git check-ignore` exits 0 for ignored, 1 for not ignored with no output | run locally on this checkout: `.sdlc/design/1.md` exit 1 and silent, `.sdlc/state/x.json` exit 0 with the matching rule | TRUE |
| `bench/task-sourcing.md` | eight tasks: licence, merged PR, PR creation date; seven rejected-row figures | `repos/<repo>`, `pulls/<n>`, `issues/<n>` on the upstream projects: all eight licences, merged states and PR creation dates match; the nox (2,165 added), omegaconf (2,282), pip-tools (167 + 52 = 219), semantic-release (239 + 27 = 266), starlette (6), tiktoken (created 2025) and more-itertools (two merged PRs) figures match. Stars moved by up to 10 since 2026-10-04 (dated column); non-test line counts not re-derived | TRUE; note added |
| `board-fields.md` | an organisation's 14 boards on 2026-09-29, 4 with the default `Todo` lane and a lowercase `In progress`, 3 with `In Progress` | `orgs/OWNER/projectsV2` and each board's fields: 11 boards now, 4 with `Todo` and lowercase `In progress` (one with the duplicated lane), none with the capital P (two of those three were untitled web-UI boards that are gone). Note added | TRUE for the 4; the rest is history; clarified (STALE as worded) |
| `release.md` | at the probes (2026-10-02) the public repository and its tag did not exist; "not re-probed" | the named public repository now exists, is private, lists no tags or releases; this repository lists six release tags. Installation was not re-probed | STALE, corrected |
| `dossier-pipeline.md` | the plugin payload is "26M" | consistent with the 26.6 MiB export measured above; not re-derived | consistent |

### Counts

"Claim" is the readers' unit (a checkable sentence or table cell); all counts are approximate.

| | Claims | TRUE | FALSE or STALE | UNVERIFIABLE |
|---|-------:|-----:|---------------:|-------------:|
| After the first pass (46 pages) | 1,509 | 1,375 | 73 (33 FALSE, 40 STALE) | 61 |
| New claims, coverage gaps (8 pages, 3 skill files) | 396 | 349 | 26 | 21 |
| New claims, GitHub reads (10 pages) | 21 | 20 | 1 | 0 |
| First-pass UNVERIFIABLE claims resolved by the GitHub reads | 0 | +16 | +4 | -20 |
| After the second pass | 1,926 | 1,760 | 104 | 62 |

The Part 2 table has six STALE or FALSE rows (how-branching-works, branching-model, platform-support, ownership, board-fields, release). The counts credit them as follows: how-branching-works was already counted in the first pass's 73 (the held item); branching-model is the one new site; the other four were first-pass UNVERIFIABLE claims, hence the +4. So the six rows reconcile as 1 + 1 + 4.

The 104 FALSE or STALE sites are all corrected in the tree or on main: the 72 corrected in the first pass (2 of them by #1260),
the held `how-branching-works.md` item (corrected in this pass), and the 31 found in this pass. The first pass's "1 left open"
is now 0. Filed this pass: #1283 and #1284, both `launch:next` and `priority:P3`. Pages edited this pass: 14 under `docs/` and
2 skill files.

### What still cannot be verified, and why (about 62)

By cause, approximate:

- Host CLIs not installed here (`claude`, `codex`, `graphify`): the host help text itself, what `claude plugin disable
  --scope local` writes, the real plugin cache layout after an uninstall, the Codex runs, the marketplace URL test of
  2026-10-08, host behaviour of skill overrides (about 8).
- Live host, model or cloud-session runs and hosted validations: the 25-PR GraphQL-versus-REST parity run, the cloud-session
  claims, the hosted validation of 2026-10-10 and the hosted upkeep run (both on a private throwaway repository), the live
  board validator run of 2026-09-11, subagent banner visibility (about 12).
- Private or deleted objects: the dossier throwaway repository, the private throwaway boards (about 6).
- One-off past measurements that cannot be rerun: the lost-goal counts, the incident file counts and the real-git reflog
  patterns, the 292 benchmark timings, the 2026-10-02 scan counts, the token-budget session totals and rate windows, the
  57-of-89 and 126-of-605 backlog counts, the onboarding timings and the planning measurements in the publish runbook
  (about 24).
- Not checkable without GraphQL or a write, both excluded from this pass: the board schema introspection of 2026-09-29, the
  `copyProjectV2` survival, the atomicity of the aliased label mutation, host refusal of branch names starting with `refs/`,
  live `publish-runbook` `--apply` behaviour (about 8).
- Judgement or policy rather than fact: whether `watch_daemon.py:906` counts as "the watcher loop", the S1 response policy,
  the issue-number provenance of three output-contract items (about 4).

### Result against the D8 threshold

"0 unresolvable claims" is NOT met as a plain count: about 62 claims remain unverifiable, for the causes above. What this
second pass supports: every coverage gap named in the first record has been read against the code; every claim that live
GitHub state could settle was confirmed, corrected or restated as a dated figure; no FALSE or STALE site is known to be open.
The bar can be read as met only "for claims checkable from the repository and GitHub", with the remaining claims
unverifiable by nature (a host CLI, a live model session, a deleted or private object, a one-off measurement). Whether that
reading satisfies the threshold is left to the owner.

## Third pass (2026-10-14)

Goal: for every claim still listed UNVERIFIABLE, decide whether it can be settled now with what exists on this machine.
Docs and two skill files only. No model call was made, no network write, and no GraphQL. `OWNER/NAME` is this repository.

### What exists here, and how it was used

| Tool | On this machine | Use |
|------|-----------------|-----|
| `claude` | on PATH | `--help`, `plugin ... --help`, and local `plugin` verbs run with `CLAUDE_CONFIG_DIR` pointed at a scratch directory under the session's scratchpad, from a scratch project directory. The marketplaces added were this checkout and a two-plugin fixture, both by directory path, so nothing was fetched. The real profile was not written: its marketplace record and settings file hold no reference to the scratch paths |
| `graphify` | on PATH (installed as a `uv` tool; the sweep recorded it as not installed) | `graphify --help`, one `graphify --mcp` call in an empty scratch directory (it only printed an error), and a read of the installed package source. No build, no extraction, no backend key |
| `gh` | on PATH | no new read was needed: every claim a REST GET could answer was settled in the second pass |
| `codex` | NOT installed: not on PATH, and not at the ChatGPT app-bundle path `tools/onboarding_control.py` looks in | tool not installed here; every Codex claim stays as it was |

Real help text, quoted from `claude plugin --help` and its verbs (read-only, 2026-10-14):

```
Commands:
  disable [options] [plugin]           Disable an enabled plugin
  enable [options] <plugin>            Enable a disabled plugin
  install|i [options] <plugin>         Install a plugin from available
                                       marketplaces (use plugin@marketplace for
                                       specific marketplace)
  list [options]                       List installed plugins
  marketplace                          Manage Claude Code marketplaces
  uninstall|remove [options] <plugin>  Uninstall an installed plugin
  update [options] <plugin>            Update a plugin to the latest version
                                       (restart required to apply)

install:  -s, --scope <scope>  Installation scope: user, project, or local
                               (default: "user")
disable:  -s, --scope <scope>  Installation scope: user, project, local (default:
                               auto-detect)
list:     --json               Output as JSON
marketplace:
  add [options] <source>      Add a marketplace from a URL, path, or GitHub repo
  remove|rm [options] <name>  Remove a configured marketplace
  update [options] [name]     Update marketplace(s) from their source - updates
                              all if no name specified
```

and from `claude --help` (the flags `docs/running-unattended.md` lists as unprobed):

```
--max-budget-usd <amount>   Maximum dollar amount to spend on API calls (only works with --print)
--permission-mode <mode>    Permission mode to use for the session (choices: "acceptEdits", "auto",
                            "bypassPermissions", "manual", "dontAsk", "plan")
--plugin-dir <path>         Load a plugin from a directory or .zip for this session only; ...
--settings <file-or-json>   Path to a settings JSON file or a JSON string to load additional settings from
--setting-sources <sources> Comma-separated list of setting sources to load (user, project, local).
--allowedTools, --allowed-tools <tools...>      --disallowedTools, --disallowed-tools <tools...>
--tools <tools...>   --strict-mcp-config   --no-session-persistence   --add-dir <directories...>
--bare   --append-system-prompt <prompt>   -p, --print   --output-format <format>   --model <model>
```

### Disposition, claim group by claim group

"Settled TRUE", "settled FALSE (fixed)" and "still unverifiable" are this pass's outcomes. The group names are those of the
table above.

| Group | Claim | Outcome and the evidence |
|-------|-------|--------------------------|
| `agent-rules-detail.md` | the host release resolves the bare plugin id | settled TRUE: `claude plugin update <name>` without `@marketplace` resolved and answered "already at the latest version" (and, against the fixture after its listing moved, "updated"). That older releases failed is history and stays untested |
| `agent-rules-detail.md` | `marketplace update` does not upgrade an installed plugin; `install` on an installed plugin is a no-op | settled TRUE, both: against the fixture, after its listing moved ahead, `marketplace update` left the installed version where it was, `install` answered "already installed" and printed the `update` command to run, and only `update` moved it. Directory marketplace only; a GitHub-fetched marketplace was not tried |
| `agent-rules-detail.md` | the live-board validator run of 2026-09-11 | still unverifiable: a live board and a model call |
| `upgrading.md` | the `claude plugin` verbs of the pre-launch sequence on a marketplace shared with other plugins | settled TRUE for uninstall and marketplace removal, and the result added a fact to the page: `marketplace remove` also uninstalls every other plugin installed from that marketplace, so the page's "only if no other plugin comes from it" guard is load-bearing. Run against a stand-in fixture, not a real pre-launch install (stated in the page) |
| `upgrading.md`, `sigma-doctor` | `disable --scope local` writes `"<id>": false` under `enabledPlugins` in the project's `.claude/settings.local.json` | settled TRUE: the file held exactly that entry; `enable --scope local` set it to true. Whether that entry wins over project and user settings at session load needs a session, so that clause stays unverified |
| `sigma-doctor` | a project-scope install carries a `projectPath` and `uninstall --scope project` removes it | settled TRUE at the `plugin list --json` level (a second record with `scope` `project` and `projectPath`; gone after the uninstall). Shadowing at session load is not testable without a session |
| `uninstall.md` | `claude plugin uninstall` leaves the plugin directory under the profile; `leftovers.py` reports two rows; `CLAUDE_CONFIG_DIR` is the profile | settled TRUE, three claims: after user-scope and project-scope uninstalls the cache directory stayed, `leftovers.py` printed exactly the cache directory and its child, and both lived under the scratch `CLAUDE_CONFIG_DIR`. Also found and added to the page: the marketplace declaration stays too |
| `running-unattended.md` | the design flags are UNVERIFIED; the variable that moves the configuration directory | settled for existence, TRUE: all twelve flags, plus `-p`, `--output-format` and `--model`, are in `claude --help`. Settled TRUE: `CLAUDE_CONFIG_DIR` moved plugin installs, marketplace records and the settings file. Behaviour of the flags, and the transcript's place and shape, stay unverified (a model call). The launcher is code and was not changed |
| host help text, `onboarding-control.md`, README | the verb grammar of `claude plugin` | settled TRUE: the pinned grammar in `tests/test_readme_first_run.py` was compared with the live help and `test_the_plugin_grammar_matches_the_cli_where_it_is_installed` ran for the claude parameter (it is skipped on CI) and passed. The Codex parameter skipped: tool not installed here |
| `onboarding-control.md` | `claude plugin details` lists 42 skills and 5 hooks | settled: 5 hook events TRUE; the skill count is now 43, so the 42 is a dated figure. STALE as a current figure, fixed by adding the current reading beside the dated one |
| `onboarding-control.md` | the dated install timings of 2026-09-29 and 2026-10-05 | still unverifiable as re-measurements; they stay dated historical figures (one-off past measurements) |
| `branching-model.md` | the 4-call to 17-call count | settled TRUE: `tests/test_docs.py::test_section_6e_still_counts_the_whole_pick_and_not_just_the_part_that_was_cheap` drives `work.start()` with a recording runner and passed (one test of that file, run alone) |
| `branching-model.md` | lost-goal counts, 40-of-40, incident file count, reflog patterns; the per-row transport and cost columns | still unverifiable: one-off past measurements, and the per-row trace was not redone |
| `branching-model.md` | hosted-run results, branch-protection bypass behaviour, host refusal of `refs/` names | still unverifiable: observing a refusal needs a write to a hosted repository; a REST read cannot show it |
| `threat-model.md` | the refusal rates for an unbroken run placed as a getenv default | settled TRUE: re-derived with `scrub.commit_secret_hits` on 2,000 draws per length with a fresh generator seeded 961 per length, one letter or digit at a time, key name `api_key`: 64.0, 74.2, 91.4, 96.8, 100, 100, 100 percent at 6, 8, 14, 20, 24, 32, 64. Exact match. (A key named `key` is never judged, which the page's wording does not say: the figure depends on the key name.) |
| `threat-model.md` | the passphrase, symbol-value and 500-run figures; whether `watch_daemon.py:906` is "the watcher loop" | still unverifiable: the word list and the exact generators are not recorded; the second is a judgement |
| `knowledge-graph.md`, `sigma-kg`, `sigma-context` | graphify installs with `pip install graphifyy` | settled TRUE: the installed distribution is named `graphifyy` (a `uv` tool environment) |
| `knowledge-graph.md`, `sigma-kg` | the backend environment variables | settled TRUE: the package's own "no LLM backend configured" text names the same six (and Azure's), and its backend table reads those keys |
| `knowledge-graph.md`, `sigma-kg` | graphify keeps a per-file cache and `extract <path> --out DIR` writes `DIR/graphify-out/` | settled TRUE: `graphify --help` ("default: <path>; writes <DIR>/graphify-out/") and the package's cache module |
| `knowledge-graph.md`, `sigma-context` | "run `graphify --mcp`" | settled FALSE, fixed: `graphify --mcp` prints `unknown command '--mcp'`. The server is `python -m graphify.serve <graph.json>`, or the `/graphify <path> --mcp` skill flag. Pages corrected; `tests/test_packaging_slice4.py` only asserts the text `--mcp` is present, which it still is |
| `knowledge-graph.md`, `sigma-kg` | "querying via graphify saves the answer back into the graph" | settled STALE, fixed: `graphify query` only prints; the saving is `graphify save-result` into `graphify-out/memory/`, which the `/graphify` skill's query flow calls and the next build reads |
| `sigma-kg` | "graphify's cache keys on content, so a rename is not re-billed" | settled FALSE, fixed: the cache key is a hash of the content plus the path relative to the root, so a moved file is a new key and is re-billed once |
| `codex-autonomy.md`, `platform-support.md`, `output-contract-detail.md`, `upgrading.md` | everything that needs the `codex` CLI: the Codex runs, the 2026-10-08 marketplace test, `codex plugin remove`, the host model mapping at runtime | still unverifiable: tool not installed here |
| `companions.md` | `skillOverrides` does not affect plugin skills | still unverifiable: needs a session, which means a model call |
| `output-contract-detail.md` | the subagent banner is "not verified end to end" | still unverifiable: a live host session |
| `label-model.md`, `board.md`, `board-fields.md`, `publish-runbook.md` | aliased-mutation atomicity, `copyProjectV2` survival, board timings, live `--apply` behaviour | still unverifiable: GraphQL or a write, both excluded |
| `cloud-sessions.md`, `upkeep-part-b.md`, `running-unattended.md` | the GraphQL versus REST parity run, the hosted validation of 2026-10-10, the drill record | still unverifiable: live cloud session, a private throwaway repository, a past run |
| `how-the-dossier-pipeline-works.md`, `dossier-pipeline.md` | the throwaway repository's issue numbers and counts | still unverifiable: the repository is gone or not visible |
| `upgrading.md`, `public-snapshot.md`, `bench/token-budget.md`, `bench/task-sourcing.md`, `label-model.md`, `onboarding-control.md` | per-pass token figures, 2026-10-02 scan counts, session totals and rate-window readings, backlog counts, search counts | still unverifiable: one-off past measurements or logs of another repository. Kept as dated historical figures |
| `platform-support.md`, `ownership.md`, `parallelism.md`, `invocation-adapter.md`, `release.md` | the coverage figure, the entry-file audit, per-authorize timing, the S1 response policy | still unverifiable: a heavy measurement the serial-run rule excludes, a measurement with no record, and a policy call |

### Counts

Same unit as the second pass (a checkable sentence or table cell), approximate. All 21 settled claims were drawn from the 62
that were unverifiable, so the total does not change. The unit here is this pass's own: the three claims under one
`uninstall.md` row are counted as three, the flag list as one.

| | Claims | TRUE | FALSE or STALE | UNVERIFIABLE |
|---|-------:|-----:|---------------:|-------------:|
| After the second pass | 1,926 | 1,760 | 104 | 62 |
| Settled by this pass | 0 | +17 | +4 (2 FALSE, 2 STALE) | -21 |
| After the third pass | 1,926 | 1,777 | 108 | 41 |

The four FALSE or STALE sites are corrected in this change: `graphify --mcp` (two pages), the save-back wording (two pages),
the rename claim, and the dated skill count. One further page gap was found and fixed that is not counted as a claim: the
uninstall page said nothing about the marketplace declaration surviving the uninstall.

### What still cannot be verified, and why (about 41)

By cause, re-estimated rather than subtracted, approximate:

- Codex CLI not installed here: about 4.
- Needs a live host session, a model call or a cloud session: about 8 (the flags' behaviour and the transcript shape,
  `skillOverrides`, the subagent banner, the hosted validations, the board validator run).
- Private or deleted objects: about 6.
- One-off past measurements, kept as dated historical figures: about 13.
- Not checkable without GraphQL or a write: about 6.
- Judgement or policy: about 4.

### Result against the D8 threshold

"0 unresolvable claims" is NOT met as a plain count: about 41 claims remain unverifiable, down from about 62, for the causes
above. Every claim about the Claude Code CLI or graphify that this machine could settle was settled, and the four that were
false or stale in the pages are corrected. No FALSE or STALE site is known to be open. The reading "met for claims checkable
from the repository and GitHub" remains the owner's call; this pass does not move it. No code defect was found, so no
`launch:next` issue was filed.
