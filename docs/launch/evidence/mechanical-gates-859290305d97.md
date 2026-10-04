# Mechanical gates at the re-frozen commit 859290305d97 (review steps S1 and S2, #580)

Evidence only. **No dimension is scored here** and `docs/launch/scorecard.json` is untouched; scoring is a later step the owner gates.
The machine-readable record is [mechanical-gates-859290305d97.json](mechanical-gates-859290305d97.json) (every command, exit code, wall time and
result) and the regenerated [inventory-859290305d97.json](inventory-859290305d97.json). Nothing below is a live run: every gate here is a hermetic
check on a clone of the code, on one macOS machine with Python 3.12 that was also running other work.

## S1: the new freeze

- Frozen commit: `859290305d977dc9119a81305e88025cba61e761`, 63 commits after the previous freeze `a5c615062313`.
- Made with `python3 tools/readiness/baseline.py snapshot <source-repo> --sha <sha> --dest <frozen-clone>` (1 s): a detached clone with
  `origin` removed and a pre-push hook that refuses. Every gate below ran there, with two stated exceptions (the goal worktree at the
  same commit, for the three that need an `origin`: G13b, G18, E3).
- Inventory regenerated: 106,764 Python lines outside tests, 181,179 test lines, 42 skills (was 92,979 and 158,788 at the old freeze).
- **The review units are stale and could not be re-cut.** `docs/launch/review-units.json` was produced by
  `tools/readiness/review_units.py --sha <sha> --json <path>`. At this commit the tool exits 2: Tier A names `install.sh`, which was
  deleted from main after the old freeze, and the tool refuses a missing named path by design. It was not edited and the units file
  was not touched. The old units were compared with the new commit by diff instead: **23 of 27 units contain a file changed since the old freeze**,
  so the old list does not describe the code at this commit. S4 must re-review A01, A02, A03, A04, A05, A06, A07, A08, A09, A10, A11, A12, A13, A14, A15, A16, A17, B01, B02, B03, B04, B05, B06; only B07, B08, B09, B10 are unchanged. Three more facts:
  Tier A loses 1 file (`install.sh`, 32 lines) and gains none; the Tier B population grows by 47 files; the old ranges of A12 and A14
  now end beyond their file. A scratch run of the tool with the deleted path removed from its list (not committed, only to size S4) gives
  16 Tier A units of 36,519 lines (was 17 of 36,018) and a Tier B population of 71,855 lines (was 58,577); a new sha gives a new seed, so its sample is a different draw. The tool defect is filed as #581 (D1, `launch:next`).

## S2: gates run once at the frozen commit

Result vocabulary: `pass`, `fail`, `not-run` (always with its reason), `verdict` (the checker ran and gave its documented answer).
D0, D3, D6 and D9 appear as extra tags on a few rows; they are outside this issue's scope and nothing is scored.
Each command is the documented gesture, with host paths replaced by placeholders (`<python3.12>`, `<scratch>`, `<frozen-clone>`).

| Id | Gate | Result | Exit | Wall | Dimensions |
|---|---|---|---:|---:|---|
| G01 | configured full verify (first run, launched with nohup) | fail | 1 | 774.5 s | D1, D2, D5, D8, D10, D11 |
| G01b | configured full verify (re-run once, no nohup) | pass | 0 | 676.6 s | D1, D2, D5, D8, D10, D11 |
| G02 | documented-gesture test | pass | 0 | 2.9 s | D2 |
| G03 | doc-claims test | pass | 0 | 0.2 s | D8 |
| G04 | known-false-claims test | pass | 0 | 0.1 s | D8 |
| G05 | doc link and anchor test | pass | 0 | 0.4 s | D8 |
| G06 | release-consistency test | pass | 0 | 0.1 s | D8, D11 |
| G07 | skill budget and structure test | pass | 0 | 2.9 s | D2 |
| G08 | instruction-bill ratchet (phase context budget) | pass | 0 | 2.5 s | D2 |
| G09 | script --help allowlist test | pass | 0 | 5.1 s | D2 |
| G10 | write-surface inventory ratchet | pass | 0 | 1.7 s | D5, D10 |
| G11 | growth-audit gesture (regenerates the audit) | pass | 0 | 5.0 s | D5 |
| G12 | shared .sdlc paths check | pass | 0 | 8.1 s | D9 |
| G13 | leak scan, tracked tree, frozen clone | pass | 0 | 6.3 s | D10 |
| G13b | leak scan, tracked tree, goal worktree (origin present) | pass | 0 | 6.7 s | D10 |
| G14 | launch-definition test | pass | 0 | 0.2 s | D0, D11 |
| G15 | launch CI cells test | pass | 0 | 0.2 s | D0, D6, D11 |
| G16 | review-plan consistency test | pass | 0 | 0.2 s | D0, D3, D11 |
| G17 | decide.py checker test | pass | 0 | 45.4 s | D0, D11 |
| G18 | decide.py verdict | verdict | 1 | 1.3 s | D0, D11 |
| G19 | doctor check | pass | 0 | 0.5 s | D5 |
| G20 | doctor hygiene | pass | 0 | 0.1 s | D5, D11 |
| G21 | quality gate, Tier-1 behavioral drift (evals/run.py) | pass | 0 | 0.5 s | D1, D2 |
| G22 | decision gate validate and check | pass | 0 | 0.0 s | D1 |
| G23 | SKILL.md frontmatter YAML test | pass | 0 | 0.1 s | D2 |
| G24 | no-private-names test | pass | 0 | 12.3 s | D10 |
| G25 | threat-model and hostile-input tests | pass | 0 | 2.2 s | D10 |
| G26 | session-start and doctor tests (dead-vs-idle) | pass | 0 | 40.7 s | D5 |
| G27 | self-contained tree test | pass | 0 | 0.6 s | D10, D11 |
| G28 | repository settings check (live GitHub state of the private source repository, not the frozen commit; read-only GET) | fail | 1 | 2.4 s | D11 |
| E1 | exposure scan, history mode (every blob reachable from every ref of the frozen clone) | fail | 1 | 24.3 s | D10 |
| E2 | exposure scan, tracked mode (HEAD) | fail | 1 | 12.1 s | D10 |
| E3 | exposure scan, refs mode | pass | 0 | 0.7 s | D10 |

Notes that change how a row reads:

- **G01 failed, G01b passed.** The first full verify was launched with `nohup` and failed one test, the Slack listener's
  signal-seam test, because `nohup` makes SIGHUP ignored and the code under test respects that. Alone, with `nohup` it fails and
  without it passes. The re-run without `nohup` is the clean result: 12,263 passed, 22 skipped, 9 xfailed. So the full configured
  verify ran twice, not once; the cause is filed as #581. Neither row is a flaky-census claim.
- G13 and G13b are one leak scan in two places: the frozen clone has no `origin`, so its `origin-owner-url` rule is skipped there; G13b
  runs it with the rule active.
- G18 is `decide.py`'s documented NO-GO, expected while nothing is scored and the definition is `proposed`; it is not counted as a pass.
- G19, G20 and G22 exited 0 but checked almost nothing in a frozen clone: the doctor found no project layer and no standing documents
  to scan, and no decisions registry is tracked, so the decision gate is off. A pass there is not evidence.
- G28 judges the live settings of the private source repository, not this commit and not the public repository; main is unprotected
  there, which is the owner's launch action (`docs/launch/repo-settings.md`).
- G11 regenerates the growth audit and the clone's file was restored afterwards.

Not run, with the reason (never counted as passing):

| Id | Gate | Dimension | Reason |
|---|---|---|---|
| N01 | mutation sample | D1 | Not re-run, by instruction: it is recorded at its own commit 70c2c6e96136 in docs/launch/evidence/mutation-70c2c6e96136.json (#360). That file is incomplete: 14 modules measured, 19 absent (timeout, not-python, not-run-budget). |
| N02 | flake census | D1 | Not re-run, by instruction: recorded at commit c82e3dfa7420 in docs/launch/evidence/flake-c82e3dfa7420.json (#337): 10 runs on each of Linux and macOS, 0 flaky, measured at that commit, not this one. |
| N03 | crash and restore drills | D5 | Not re-run: recorded at cde9869d0cf8 in docs/launch/evidence/drills-cde9869d0cf8.json; hermetic fakes, macOS only. |
| N04 | egress capture | D10 | Not re-run: recorded at a9747324eee1 in docs/launch/evidence/egress-full-suite-a9747324eee1.json. |
| N05 | model-level injection drill (tools/readiness/injection_drill.py) | D10 | Needs a throwaway repository and a live model; owner-gated, never run. |
| N06 | planted-defect recall (tools/readiness/seed_defects.py) | D1 | Needs a real seed set written by a person who will not review; only three made-up examples exist. |
| N07 | review-units re-cut (tools/readiness/review_units.py) | D1 | The tool exits 2 at this commit: Tier A path install.sh is not tracked (deleted by #408). Not edited here; filed as #581. The old units are compared against this commit by diff instead (see units). |
| N08 | claim check over all docs beyond the named false claims | D8 | No such gate exists on main. |
| N09 | CHANGELOG against git history | D8 | No such gate exists on main. |
| N10 | coverage run | D1 | Not a gate: CI neither measures nor enforces coverage (docs/launch/coverage.md); the recorded figure is from another commit. |

## Exposure scans (D10)

`tools/readiness/exposure_scan.py` in modes `history` (every blob reachable from every ref of the frozen clone), `tracked` (HEAD) and
`refs`, with a private-pattern file supplied from outside the repository (2 patterns; they matched nothing). Nothing printed or
recorded here is a matched value: the record holds rules, path classes and counts.

| Mode | Findings | Stale allowlist entries | Exit |
|---|---:|---:|---:|
| history | 235 | 0 | 1 |
| tracked | 62 | 4 | 1 |
| refs | 9 remote refs, 1 local-only tag | n/a | 0 |

Exit 1 means findings, not a failed run. **Triage result: no secret and no real private reference in the tree at HEAD, and no finding in a
blocker class (B2), so no `launch:blocker` was filed.** Every finding is a test fixture, a detector or its docstring describing a pattern, or a
placeholder. History holds one real developer host path, in `.sdlc/plans/258.md` line 803, already in the earlier scan and routed to #400
(closed); this repository's history is not what a visibility change would expose, because the public repository is a fresh snapshot
(`docs/launch/definition.md`), so what visibility would expose is the tracked tree. The tracked scan cannot exit 0 on a later commit,
because its allowlist is keyed to exact blobs and 4 entries went stale; filed as #583 (D10, `launch:next`). The earlier scan, at
`8aee0c74c526`, had 233 history and 70 tracked findings of the same classes.

## What the gates establish, and what they do not, per gating dimension

| Dimension | What these gates now establish at 859290305d97 | What they do not establish |
|---|---|---|
| D1 Correctness | The full suite passes (12,263 tests, G01b), the Tier-1 behavioral drift gate is 1.000 over 8 fixtures (G21). The mutation and flake artifacts are cited at their own commits: flake census 0 flaky in 10 runs on each of Linux and macOS at `c82e3dfa7420`; mutation kill rates for 14 of 33 modules at `70c2c6e96136`, the rest timed out or did not run. | Nothing about this commit's flakiness or kill rate (both measured elsewhere), no planted-defect recall, no line-by-line review (S4), no second-vendor pass, and the review units are stale (23 of 27 changed). A green hermetic suite is not correctness. |
| D2 Skills | Skill budget and instruction-bill ratchets, documented Python gestures, the `--help` allowlist and the frontmatter test pass (G02, G07, G08, G09, G23). | Verbs and flags that are not copyable Python gestures are not resolved by any gate; no model review of the phase skills (S5); no count of gate-skip ambiguities. |
| D5 Five properties | The write-surface ratchet and the session-start and doctor tests (the dead-vs-idle staleness check) pass (G10, G26). The growth audit regenerates with 0 unresolved store patterns, but the committed audit is stale against it (#582). | No drill against a real host or on Linux, no recorded live control of dead-vs-idle on a really stopped watcher, no 10x and 100x table. The doctor check and hygiene gates were vacuous here (G19, G20). |
| D8 Docs | The doc-claims, known-false-claims, link and anchor, documented-gesture and release-consistency tests pass (G02 to G06). | Only the named false claims and relative links are checked; there is no claim check over all docs and no CHANGELOG-against-git check (both not-run: no such gate exists). A passing link test says nothing about whether a claim is true. |
| D10 Security, privacy, exposure | The leak scan is clean on the tracked tree (G13, G13b); the write-surface ratchet, threat-model and hostile-input tests and the no-private-names test pass; the exposure scans found no secret or real private reference at HEAD (above). | No model-level injection drill, no red team, no blast-radius drill, no history and private-reference scan of the public tree (#397 is separate), and the tracked exposure scan cannot show clean until #583 lands. A static scan does not see run-time-built paths or secrets in formats it has no rule for. |
| D11 Operations | The launch-definition, CI-cells and release-consistency tests pass; `SECURITY.md` and the runbook exist (cited by the plan). | Nothing about CI on the launch commit (five legs not read here), no rollback run, and the settings check fails on the source repository as expected. |

## Findings filed

All are queued and `launch:next`; none is in a blocker class. One issue per dimension: #581 (D1), #582 (D5), #583 (D10). The
related-issue check (`dedup.py`) returned only related, non-duplicate candidates for each.

## Cost of S1 plus S2

Metered with `python3 evals/bench/meter.py --paths` on this slot's own transcript and the two fresh plan-review subagents, at the
time this page was written: **13,415,484 processed tokens, $4.63** (3 transcripts, 0 unpriced turns), made of
190 input, 68,805 output, 12,792,247 cache-read and 554,242 cache-write tokens. That is about
4.5 times the issue's unmeasured estimate of under 3M. 95% of it is cache reads: every turn of a long session re-reads its context, and the plan's unit counts cache reads. The gates themselves cost no tokens (they are scripts); the session turns that ran and read them do.

**Not included** in that figure: the pre-PR code review, the post-PR review, CI polling and the merge. Those are metered at the end and
posted as a comment on #580; the counters should take that larger final figure.

| Counter | Before this run | Subtract (measured so far) | Remaining |
|---|---:|---:|---:|
| 40M checkpoint | 36,112,807 | 13,415,484 | 22,697,323 |
| 75M ceiling | 71,112,807 | 13,415,484 | 57,697,323 |

The pilots' 3,887,193 are already inside the counters (owner decision of 2026-10-04); this run is the first step after them. The plan's
other steps are still unmeasured, and the plan's S1 plus S2 estimate (1M to 2.5M) is now measured at several times that.
