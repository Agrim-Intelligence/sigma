# Measured results

Written 2026-10-12. Every number below is a local or CI measurement made by the maintainers, on their own
machines and their own repository. Nothing here comes from an outside user, and nothing here compares Sigma
with any other tool. Each figure names the file it was copied from and the date of that file's commit; where
a thing was not measured, this page says "not measured". Figures are quoted as the source files print them.
Nothing is rounded up, re-derived or extrapolated, except where a line says "read from the table".

This page stands in for a benchmark results file at launch. The pre-registered three-arm comparative
benchmark (#275) moved past launch; no arm of it has been run. See "What this document is not" at the end.

Commits the numbers were recorded on (they differ, and most are older than current main):

| Section | Recorded on | Date |
|---|---|---|
| Live drills | plugin exported from `03fa8189` and `cc92a834`, plus the installed marketplace copy | 2026-10-12 |
| Validation sessions | sessions of 2026-10-06; Sigma commit not recorded in the file | 2026-10-06 |
| Cost calibration | frozen `a5c615062313`; meter at `e48420fa97f6` | 2026-10-04 |
| Code review S4 | units cut at `c3faf6f23e12` | 2026-10-05 |
| Code review S5 and red team | `7997e29e4270` | 2026-10-07 |
| Mechanical gates | `859290305d97` | 2026-10-04 |
| Mutation | `70c2c6e96136` | 2026-10-04 |
| Flake census | `c82e3dfa7420` | 2026-10-12 |
| Coverage | `a23f6c6` | 2026-10-03 |
| Hermetic drills | `cde9869d0cf8` | 2026-10-01 |
| Blast radius | `859290305d97` | 2026-10-04 |
| Export rehearsal | `f92a7528a26b` | 2026-10-12 |
| Nightly CI | the head of main on each night (named below) | 2026-10-10 to 2026-10-12 |

## 1. Cost per goal and per phase, from live drills

Source: [live-onboarding-2026-10-12.md](live-onboarding-2026-10-12.md), 2026-10-12. One run each, one machine
(macOS), Claude Code 2.1.284, model `claude-sonnet-5-5` (one run also used `claude-haiku-4-5-20251001`), a
subscription login with no API key. The dollar figures are the host's own `total_cost_usd` at list price on a
subscription login: an estimate, not billing. The first three runs loaded the plugin from files exported from
main with `--plugin-dir` (the third from a later main commit, `cc92a834`); only the fourth used the marketplace
copy already installed in the owner's profile, which was an older release than main at the time.

| Run | Goals | Result | Whole loop session, host list-price estimate | Sum of the `phase_report.py end` lines | Agent wall time |
|---|---|---|---|---|---|
| Local mode, scratch repo whose own `make test` needed a file the demo goal does not make | 2 | 1 done, 1 failed (the demo goal; filed as #1196) | $2.018 | $0.82 (12 lines) | 22 s init + 286 s loop (a 17 s first attempt did nothing) |
| GitHub mode, private throwaway repository | 1 | goal reached `review`; PR opened and left for a human merge | $1.480 | $0.31 (8 lines, review and retro run twice) | 32 s init + 440 s loop |
| Local mode, vanilla repo (`test` target is `true`) | 1 | 1 done | $1.244 | $0.53 (8 lines, review and retro run twice) | 19 s init + 160 s loop |
| Local mode, marketplace-installed plugin | 2 | 2 done in one loop session | $1.873 | $0.76 (14 lines) | 25 s init + 240 s loop |

- The `/sigma-init --demo` call alone cost $0.127, $0.130, $0.127 and $0.148 in the four runs.
- Per phase: each phase line in these drills reads between $0.02 and $0.11; the file lists every line.
- The phase lines do not add up to the session total. The file states that the remainder (about $1.20, $0.71
  and $1.11 in the three local runs; not stated for the GitHub run) is the orchestrating session, which no
  phase line attributes. So the per-phase numbers are a lower bound on the cost of a goal, not the cost.
- These goals were one-line tasks (create a text file). They say what the loop's fixed overhead looked like on
  a trivial goal. They do not say what a real goal costs.
- Not measured: Linux, Codex, Cursor, a person outside the team, a continuous install-to-merged-PR clock, the
  `gh` call count of the live run, billing, and the current main in a live session.
- Every run printed refusals on the way (missing "Done when" section, missing research artifact, review verdict
  recorded after retro, test-first gate). The file quotes them and the issues filed (#1196, #1197, #1199,
  #1206). A permission mode of `acceptEdits` denied the loop's first command and the loop did nothing (#721).

## 2. Validation sessions and their cost

These are validation sessions, not a benchmark. Sources: [token-budget.md](../../bench/token-budget.md)
(2026-10-06) and [cost-calibration.md](cost-calibration.md) (2026-10-04).

**22 full Sigma loop sessions** on `claude-sonnet-5-5`, from the owner's two validation runs of 2026-10-06, each
on a real post-cutoff bug. Totals are the host's own end-of-run `modelUsage` figures, all four token kinds,
subagents included. They are repeats of about four distinct bugs, so the file says the effective n is about 4.

| | n | min | median | max |
|---|---|---|---|---|
| Total tokens per session | 22 | 1,113,647 | 3,981,203 | 5,236,384 |

- 92 percent of the loop sessions' tokens were cache reads (the file's own figure).
- Indicative dollars for the same sessions, read from the table (list rates, not billing): lowest $0.5990
  (S06), highest $2.2862 (S07). The file prints no median in dollars and none is given here.
- The rate-limit readings taken during these sessions had a resolution of 0.01 and were taken while the owner
  was also using the account. The five-hour window moved +0.01 in 13 of 16 sessions with two or more readings
  and +0.02 in 3; the seven-day window moved 0.00 in 12 and +0.01 in 4.

**Context figure, not a comparison.** The same file lists 2 plain agent runs (n = 2) on the same kind of bug:
142,165 and 293,055 total tokens, $0.0998 and $0.1445 indicative. They are given only so the Sigma figures have
a scale. They are not a measure of whether Sigma's extra spend bought anything: the file does not record
whether those plain runs solved their bugs to the same standard, n is 2, the bugs are about four, and no
statistical test was run. No claim follows from them.

**Review-pass costs** (cost-calibration, frozen `a5c615062313`, one run per unit, Sonnet-tier reviewers asked
for medium effort in the prompt and not verified): a review of one 2,267-line high-risk unit processed
734,530 tokens ($0.628, 208 s); verifying its 21 findings processed 932,251 ($0.415, 65 s). A 2,975-line
sampled unit: review 451,179 tokens ($0.502), verification of 18 findings 505,555 ($0.271). The file says unit
cost differs by up to 2.8x per line, and that variance was not measured.

Not measured, per those files: one live goal's recorded phase costs at that date (superseded by section 1),
the benchmark itself (no arm run), whether a token-authenticated `claude -p` writes the same usage records as
the interactive logins the table came from, and any dollars-per-task figure.

## 3. Code review and red team

**S4 high-risk review**, [review-s4-c3faf6f23e12.md](review-s4-c3faf6f23e12.md), 2026-10-05.

- 16 of 16 Tier A units reviewed (36,519 lines); the Tier B sample was not reviewed.
- 82 findings reported, 69 verified by a different fresh reviewer, 13 dropped.
- Cost, measured with the repository's meter: all reviewers and verifiers 24,845,157 processed tokens
  ($12.37); the file's own per-line figure is 680.3 tokens per line for review plus verification.
- Four `launch:blocker` candidates were filed (#589, #625, #629, #635); the issue tracker shows all four
  closed as of 2026-10-12.
- Not covered: one shallow pass per unit, no seeded defects (recall unmeasured), no second reviewer vendor,
  medium effort requested and not verified.

**S5 phase-skill review**, [review-s5-7997e29e4270.md](review-s5-7997e29e4270.md), 2026-10-07.

- 10 skills; 35 findings reported, 11 verified by a verifier told to refute, 24 dropped.
- Verified by class: 5 gate-skip, 3 injection, 3 prose-vs-code. The file says its own threshold of zero
  gate-skip ambiguities was not met at that commit.
- 31,882,801 processed tokens in all; verifiers cost about 405k processed tokens each, mostly cache reads.
- Filed: #713 and #714 (both closed as of 2026-10-12) and #715 (open as of 2026-10-12).
- Not covered: single shallow pass, recall unmeasured, medium effort requested not verified, a model verifier
  reading prose gives a judgement, not a proof.

**Red team**, [redteam-7997e29e4270.md](redteam-7997e29e4270.md), 2026-10-07, against the shipped surface.

- Three passes of a hostile outsider with fakes at the code's own seams: about 245 attack cases (A), about
  150 probe inputs in about 28 classes (B), 101 assertions in 15 scripts (C); five verifiers re-reproduced
  every proposed blocker.
- Four `launch:blocker` issues were filed at that commit (#707 to #710); all four are closed as of
  2026-10-12. The file records negative results (attacks that were refused) alongside.
- The file states the token cap for this work was exceeded: 63,527,346 processed tokens ($17.82) against a
  10M request, mostly cache reads.
- Not run: the model-level injection drill (whether a model obeys hostile text is unmeasured), merge
  head-pinning, and independent re-verification of several non-blocker reproductions.

## 4. Mutation, flake census, mechanical gates, coverage

**Mutation kill rates**, [mutation-70c2c6e96136.json](mutation-70c2c6e96136.json), 2026-10-04. `complete: false`.
14 of 33 listed files were measured. Of the other 19, 7 are not Python, 10 timed out, and 2 were not run for
budget (15 minutes per module, 3 hours overall, on a shared machine). The rate is killed over killed plus
survived, on lines the scoped tests execute, so it measures how strongly those tests check that module, not
how well it is tested overall.

| Module (file basename) | Kill rate | Killed / survived |
|---|---|---|
| `setup_wizard.py` | 0.8364 | 138 / 27 |
| `time_track.py` | 0.8261 | 114 / 24 |
| `issue_field_gate.py` | 0.8085 | 38 / 9 |
| `scrub.py` | 0.8026 | 61 / 15 |
| `state.py` | 0.7447 | 245 / 84 |
| `decision_gate.py` | 0.7286 | 247 / 92 |
| `blockers.py` | 0.7089 | 151 / 62 |
| `research_capture.py` | 0.7 | 63 / 27 |
| `board_setup.py` | 0.6945 | 557 / 245 |
| `channel_notify.py` | 0.6718 | 88 / 43 |
| `run_with_timeout.py` | 0.6667 | 22 / 11 |
| `upstream.py` | 0.6601 | 371 / 191 |
| `slack_client.py` | 0.6238 | 63 / 38 |
| `gh_session.py` | 0.6 | 3 / 2 |

Four modules are at or above 0.8, ten are below. The file notes survivors include equivalent mutants and that
a rate over few mutants (`gh_session.py`: 5) is thin. The large loop, work and ledger modules are among the
19 with no number. Not measured: those 19, and any later commit.

**Flake census**, [flake-c82e3dfa7420.json](flake-c82e3dfa7420.json), 2026-10-12, Python 3.12 only. 10 runs on
a hosted Linux runner (12,170 tests per run, 0 failed, 38 skipped) and 10 runs on a local macOS machine
(12,170 tests, 0 failed, 31 skipped, 4 parallel workers): 0 flaky in either. The file's own caveats: ten runs
observe instability and cannot prove zero flaky tests (by arithmetic a test failing 1 run in 20 passes all ten
about 60 percent of the time); Python 3.10, 3.11 and 3.13 were not run; measured at that commit, not later.
Section 6 shows failures on those other interpreters in the nightly runs.

**Mechanical gates**, [mechanical-gates-859290305d97.md](mechanical-gates-859290305d97.md), 2026-10-04, in a
frozen clone with the origin removed, on one macOS machine with Python 3.12. 33 gate rows: 28 pass, 4 fail,
1 verdict.

- The full configured verify failed once (a signal test that fails under `nohup`) and passed on re-run:
  12,263 passed, 22 skipped, 9 xfailed (wall time 676.6 s). Both runs are recorded.
- The other failures: a check of the private source repository's live settings (main unprotected there), and
  two exposure scans over history and the tracked tree (235 and 62 findings, triaged by the file as fixtures,
  detectors and placeholders; no secret and no real private reference at HEAD). The file also records one real
  developer host path in git history (not at HEAD), routed to #400, now closed.
- Three gates exit 0 but the file says they checked almost nothing in a frozen clone, and a pass there is not
  evidence. Ten checks were not run, each with its reason (including the planted-defect recall and the
  model-level injection drill).

**Coverage**, [../coverage.md](../coverage.md), 2026-10-03, local macOS, Python 3.12, line coverage not branch:
90.91 percent (37,806 statements, 3,436 missed); 11,569 passed, 24 skipped, 2 xfailed in 2,489 s. The file says
this understates (some subprocess code is not traced) and that no Linux percentage exists, because the one
Linux trial stopped before printing a total. CI does not measure or enforce coverage.

## 5. Hermetic drills and blast radius

**Crash and restore drills**, [drills-cde9869d0cf8.json](drills-cde9869d0cf8.json), 2026-10-01, macOS only,
hermetic fakes (no live model, no real GitHub). 4 drills, 5 seeded runs each, 20 runs.

- D1 (kill at a checkpoint; recorded runs cover three checkpoints) 5 of 5 runs passed all invariants; D2 5 of 5;
  D3 5 of 5.
- D4 is a control: its two reporting invariants (doctor and session-start reporting an explicit stop file) are
  recorded as failing by design in all 5 runs ("control_failed_and_recorded"), and the file marks them as the
  expected failure states. They are not passes and are not counted as passes here. The invariants that the
  daemon exits for the stop file and that the stop file is preserved passed in all 5.
- Not measured: Linux, Windows, a model-driven run.

**Blast radius**, [blast-radius-2026-10-04.json](blast-radius-2026-10-04.json) and
[../blast-radius.md](../blast-radius.md), 2026-10-04, commit `859290305d97`. The scripted end-to-end flow was run
against a private throwaway repository with no branch protection, once with auto-merge on and once off. Five
assertions (default branch moved only through the goal's merged PR; no force-push; only `sdlc/*` branches
deleted; an unrelated issue untouched; every `gh` and `git` launch matches an inventory rule) passed in both
runs, and the control (inventory with the PR rules removed) made the fifth assertion fail as it should. The
file states a scripted flow is not a model-driven run, the capture sees Python subprocess launches only, and
the drill tooling was uncommitted when measured.

## 6. CI and export rehearsal

**Nightly full matrix**, read from the GitHub Actions REST API on 2026-10-12. The latest completed scheduled
runs (only three scheduled runs were listed):

| Night (UTC) | Run | Head | macOS 3.12 | Ubuntu 3.10 | 3.11 | 3.12 | 3.13 |
|---|---|---|---|---|---|---|---|
| 2026-10-12 | 38208647371 | `b13a0ea2ef02` | pass | FAIL | FAIL | pass | pass |
| 2026-10-11 | 38114872169 | `abf91edf7787` | pass | FAIL | FAIL | FAIL | FAIL |
| 2026-10-10 | 38027579304 | `b9d1baaec222` | pass | pass | pass | pass | pass |

The 2026-10-12 run's `regression-control` job also passed.

Failing tests, from the failed jobs' logs:

- 2026-10-12, Python 3.10 and 3.11 (1 failed each, 16,792 and 16,802 passed):
  `test_the_documented_install_and_diagnose_gesture_works_as_written`. The rehearsal below saw the same test
  fail on those two interpreters in a container, and its cause is not isolated.
- 2026-10-11, Python 3.11, 3.12 and 3.13 (3 failed each): `test_split_mode_peak_memory_under_a_flood_is_bounded`,
  `test_overrun_stops_the_whole_group` and `test_term_comes_before_kill`. Python 3.10 failed the first and the
  third (2 failed).
- Whether the 2026-10-11 failures were fixed, flaky or environmental was not investigated for this page.
  The red legs are reported as red. Pull requests and merges to main run only one macOS 3.12 leg (the
  required check); the other four legs run in this nightly and on demand.

**Export rehearsal**, [rehearsal-f92a7528a26b.md](rehearsal-f92a7528a26b.md), 2026-10-12, commit
`f92a7528a26b`. The file's first line says it is local evidence only, not GitHub CI.

- The public-tree export built twice with an identical tree and export commit (about 34 s each); 1,229 files;
  the leak and reference scans and the builder's own exposure pass found 0 hits on the export. The standalone
  `exposure_scan.py tracked` run inside the export exited 1 with 4 `stale-allowlist` rows (allowlist rows for the
  excluded `.sdlc`, not hits; #1203, since addressed by #1215 and not re-run for this page). A planted fake private reference was
  rejected (exit 1) and appeared in none of the outputs.
- The CI-equivalent commands on the exported tree were red: on macOS 3.12, 8 failed, 16,523 passed; the Linux
  containers gave 8 to 10 failures per leg across Python 3.10 to 3.13 and an Ubuntu image. The file traces 8
  of them on every leg to one cause (the default export removes `.sdlc/`), filed as #1203. A later commit on
  main (`7360ea44`) is titled as a fix for #1203; this page did not re-run the export after it.
- The isolated-profile install control passed (all six modes green, 137.5 s, real profile untouched); it ran
  no model session.
- Not run: the GitHub CI legs on the export (spend not approved at the time), the doctor from an installed
  copy.

## What this document is not

- It is not a comparative benchmark. No arm of the pre-registered three-arm benchmark (#275) has been run, and
  no number here supports a claim that Sigma is better, cheaper or more accurate than any other tool or than
  an agent used without it.
- It is not an outsider's result. Every figure was produced by the maintainers. No person outside the team has
  run Sigma for any figure on this page.
- It is not one build. The figures were recorded on the different commits listed at the top, most of them
  older than current main, and the code has moved since.
- It is not a broad platform result. The live drills and most local measurements are one machine class
  (macOS, Python 3.12). Linux appears only in the CI runs of section 6 and the flake census; Codex and Cursor
  appear nowhere here.
- It is not billing. Dollar figures are host list-price estimates on a subscription login.
- It is not a measure of review recall or of a model's resistance to hostile text: both are listed above as
  not measured.
