# Public-snapshot rehearsal: commit f92a7528a26b

**LOCAL EVIDENCE ONLY. THIS IS NOT GITHUB CI.** No GitHub Actions job ran for this rehearsal: the
owner has not approved CI spend, and the export was pushed to a non-default branch of a private
throwaway repository whose workflow does not trigger there. Every pass or fail below was produced on
the operator's own macOS machine or in local Docker containers. It does not satisfy #473's Done-when
item "CI green on each supported leg"; that item stays open.

To run the GitHub CI legs once spend is approved (one line): `git -C <export> push https://github.com/<owner>/<throwaway repo>.git main && gh workflow run ci.yml --repo <owner>/<throwaway repo> --ref main` (details under "What ran where").

Goals served: #397 (builder and verifier, rehearsal step), #473 (owner-side rehearsal evidence), the
agent-doable part of #471. Date: 2026-10-12. Measured commit:
`f92a7528a26bda354583b9ab67974981cb633c88` (origin/main at the start of the run).

## What ran where

| Step | Where | Result |
|------|-------|--------|
| Build the export, twice | local (macOS, python 3.9.6 `python3`) | VERIFIED both times, identical tree and export commit |
| Leak / exposure / commit-time scans on the export | local | 0 hits (one standalone gate exits 1, see Defects) |
| Planted fake private reference | local, scratch clone | REJECTED, exit 1, token in no output |
| Push the export as one orphan commit | GitHub, private throwaway repo, branch `rehearsal` | pushed; round-trip clone has the same commit and tree |
| `verify_public_repo.py` | GitHub REST, read-only | 7 ok / 4 FAIL on the rehearsal shape (all four expected, see below) |
| CI-equivalent commands on the EXPORTED tree, macOS 3.12 | local | red: 8 test failures plus one failing gate (Defects) |
| Linux legs, Docker: python:3.10, 3.11, 3.12, 3.13, ubuntu:24.04 (apt python 3.12) | local Docker, uid 1000, `pytest tests -q -n 4` | red, see table |
| `onboarding_control.py --install claude --from-install` | local, isolated profile, claude CLI 2.1.284 | GREEN, ok true |
| Rename rehearsal (`handover_check.py sequence`) | NOT RUN | obsolete, see below |
| GitHub CI legs (5 jobs) | NOT RUN | spend decision pending |
| Doctor from the installed copy (public-snapshot.md Rehearsal step 7) | NOT RUN | needs an initialised scratch repo; not part of this task |
| `regression-control` job | NOT RUN | not required by the task |

To run the GitHub legs: re-create an EMPTY private throwaway, then the line above
(the `full` job is `workflow_dispatch`, and a dispatch needs the workflow on the default branch, so
the export must be pushed to `main`; that push alone also starts the one-job `test` subset). The
issue estimates about 129 runner-minutes per run; that figure is quoted from #473, not measured here.

## Obsolete: the rename rehearsal

#473 and an older #397 note prescribe `python3 tools/handover_check.py sequence`. That tool no longer
exists on main (`tools/handover_check.py` is absent) and `docs/public-snapshot.md` now says "There is
nothing to repoint": the owner decided the public repository is a NEW repository named
`Agrim-Intelligence/sigmaloop` and the source repository is not renamed, so no clone is ever
repointed. A rename rehearsal has nothing to rehearse. #473's body should drop that sentence.

## 1. Build and determinism

Command, from a clean detached worktree of the commit, exactly as the doc gives it:
`python3 tools/build_public_tree.py f92a7528a26b... --out <dir> --patterns <owner patterns file> --repo <source repository> --allow-public-source`
(`--sdlc` not passed: default `exclude`, recorded as default).

| | Build 1 | Build 2 |
|-|---------|---------|
| verdict | VERIFIED | VERIFIED |
| tree | `91edef64bd7ad311a6b70499f2a0bda035779efa` | same |
| export-commit | `67dc17198b4e06708f4244c8869b211d39385429` | same |
| scans line | `builder=0 leak_refs=0/0 exposure=0/0+0 leak_scan=0` | same |
| wall (shell `time`) | 35.5 s | 33.5 s |
| report timings (scans / total) | 30.87 s / 35.45 s | 28.87 s / 33.41 s |

These are a RE-RUN on 2026-10-12 of both builds from the same detached worktree of the same commit
(`f92a7528a26b`; main has since moved, the measured commit did not), with `--report-dir` set to the
kept directory: files `build1.stdout`, `build2.stdout`, `*.stderr` and the four report files there. The
tree and export commit are identical to the first run's, so nothing differed because main moved. The
first run's wall times (49.1 s and 55.0 s; host load was not measured) were not kept as raw logs and are
superseded by these. Source: builder stdout and its JSON reports (`files 1229`, `bytes 24177780`,
planned git tree = python tree = disk tree = export tree). Excluded: `.sdlc`
607 files, 3,952,574 bytes, `references_remaining` 73. Review: requested and reached `pr-merged`
(pull 1185), `independent: true`, branch protection `none`, `public_source_override: true`. Patterns
file: 2 patterns (count only; content never read into this record). Dispositions: exposure allowlist
67 entries (4 moot), public-tree dispositions 7 entries (0 moot). Doctor slug and issue-template slug
both equal `public_repo` (`Agrim-Intelligence/sigmaloop`). Placeholders in `SECURITY.md` and
`CODE_OF_CONDUCT.md`: the builder scan reports 0 findings, so none remain at this commit (a real
build, not a grep).

## 2. Scans on the export, and the planted control

On the export (1229 files, 1 commit): `tools/leak_refs.py tree` 0 hits; `tools/leak_scan.py` 0
findings; the builder's own exposure pass 0 findings. The standalone gesture
`python3 tools/readiness/exposure_scan.py tracked .` run inside the export exits 1 with 4
`stale-allowlist` rows (rows for the excluded `.sdlc`); see Defects, #1203.

Planted control, in a scratch clone (the source repository untouched): a throwaway patterns file with
ONE invented pattern, and a file containing a string built at runtime that matches it. Same commit
without the plant: VERIFIED (exit 0). With the plant (one new commit, `--review-level clean`): exit 1
REJECTED, `scans: builder=0 leak_refs=1/1 exposure=1/1+0 leak_scan=0`, an `OUT.rejected` directory
with 0 refs (nothing pushable), and the planted string appears in 0 of stdout, stderr, the JSON
report and the `.md` twin (grep count 0 each).

## 3. Throwaway repository, push, verifier

Created `<throwaway repo>` (private, in the owner's organisation) with `auto_init` (so `main` held a README
commit), pushed the export from `main` to branch `rehearsal` only: one orphan commit
`67dc17198b4e` (same id as the build). A fresh clone of `rehearsal` returned commit `67dc17198b4e`
and tree `91edef64bd7a`. GitHub Actions: `GET repos/<throwaway>/actions/runs` returned
`total_count 0` about 8 seconds after the push (single measurement); `ci.yml` triggers only on push to
`main`, pull request and schedule, so none was expected. At the end of the session the throwaway returned 404 (this run never deleted it and the
token cannot; the cause was not investigated), so that count could not be re-read.

`verify_public_repo.py --repo <throwaway> --report <report> --expect-visibility private`: 7 ok, 4
FAIL (`single-commit`, `branches`, `ci-run`, `ci-legs`). With `--branch rehearsal`: still 7 ok / 4
FAIL (`default-branch`, `branches`, `ci-run`, `ci-legs`) but `single-commit`, `root-commit` and `tree`
(`91edef64...` = the report's) are ok. The failures are the shape of this rehearsal, not tool bugs:
`auto_init` put a second branch and a README commit on `main`, the export sits on a non-default
branch, and no CI ran. A verifier PASS (11 ok) requires an EMPTY throwaway with the export on its
default branch and CI green, i.e. the spend step.

## 4. CI-equivalent commands on the EXPORTED tree

Clean venv, Python 3.12.13, `pip install pytest pytest-xdist` (pytest 9.1.1, xdist 3.8.0), git
identity supplied through a temporary global config, never the operator's. The export tree was
copied first so the pushed export stayed byte-clean.

macOS (the `test` job of `ci.yml` at this commit):

| Command | Result | Wall |
|---------|--------|------|
| `evals/skills/smoke.py` | rc 0 | <1 s |
| `pytest tests/ <SLOW ignores> -rs -q -n auto` | **8 failed**, 16523 passed, 27 skipped, 9 xfailed | 703.8 s |
| `tools/rename_check.py` | rc 0 | 1 s |
| `tools/readiness/write_surface.py check . docs/launch/write-surface.json` | **rc 1** (4 stale rows) | 3 s |
| `evals/run.py` | rc 0, 1.000 over 8 fixtures | 1 s |
| `evals/safety/check.py` | rc 0 | 40 s |
| `hooks/decision_gate.py validate .` and `check .` | rc 0, rc 0 (no registry: gate off) | <1 s |
| `tests/test_skill_frontmatter_yaml.py` (with PyYAML) | 1 passed | 1 s |
| the six SLOW files (`-n auto`), re-run with the log kept (`mac-slow-six.log`) | 189 passed | 700.6 s |
| `evals/regression/budget.py` (onboarding-control job), re-run with the log kept (`mac-budget.log`) | ok, 40.2 s against a 300 s ceiling | 40 s |

The 8 failures all trace to one cause (tests and inventories reading `.sdlc/` paths that the default
export removes); they are listed in #1203. The same cause fails `write_surface.py check`.

Linux in Docker (local; images `python:3.10` to `3.13`, and `ubuntu:24.04` with apt python 3.12.3;
uid 1000 with a passwd entry; export mounted read-only and copied in; Docker VM 12 CPUs, 8 GB; full
suite, not the CI subset, `pytest tests -q -n 4 -p no:cacheprovider`, as `ci.yml`'s `full` job but with
`-n 4`):

| Leg | Result | pytest wall | Leg total |
|-----|--------|-------------|-----------|
| python:3.10 | 9 failed, 16699 passed, 39 skipped, 9 xfailed | 1199.6 s | 1228 s |
| python:3.11 | 9 failed, 16709 passed, 29 skipped, 9 xfailed | 959.4 s | 987 s |
| python:3.12 | 8 failed, 16710 passed, 29 skipped, 9 xfailed | 1702.5 s | 1733 s |
| python:3.13 | 8 failed, 16710 passed, 29 skipped, 9 xfailed | 1702.8 s | 1734 s |
| ubuntu:24.04 (python 3.12.3) | 10 failed, 16708 passed, 29 skipped, 9 xfailed | 947.6 s | 977 s |

The python 3.12 and 3.13 rows are separate runs (separate logs) whose pytest wall times happen to coincide (1702.49 s vs 1702.83 s).

Every leg: `rename_check` rc 0, `write_surface check` rc 1, `evals/run.py` rc 0, `evals/safety/check.py` rc 0,
decision gate rc 0. Wall times differ by up to 1.8x between legs on one machine; the load on the host
during the 3.12 and 3.13 legs was not measured, so the times are not comparable evidence.

Failure breakdown:
- 8 on every leg: the `.sdlc` family above (#1203).
- python:3.10 and 3.11 only: `tests/test_invocation.py::test_the_documented_install_and_diagnose_gesture_works_as_written`
  (`invocation.py diagnose` prints `refused`; 0.2 s, deterministic). It also fails on a copy of the
  private tree on python:3.10 in the same container (100 passed, 1 failed in that file), passes on 3.12,
  3.13 and macOS 3.12, and the scheduled `full` run of 2026-10-10 passed on GitHub's ubuntu 3.10 and
  3.11. So it is specific to this container setup or to those interpreters in it; the cause was not
  isolated and no issue is filed for it.
- ubuntu:24.04 only: `test_without_flock_the_lock_family_refuses_loudly` and
  `test_without_flock_the_sweep_refuses_loudly`, the known pre-existing Linux failures. The known flaky
  Slack race test did not fail in any leg of this run.

A first Linux attempt (python:3.10) ran as uid 1000 with no passwd entry and produced 14 failures,
5 of them `getpwuid(): uid not found`; that was the harness, fixed by mounting a passwd file, and every
number above is from the rerun.

## 5. Fresh-profile install control

A push-disabled clone of the pushed `rehearsal` branch (`origin` removed), Python 3.12, then
`python3 tools/onboarding_control.py --sigma <clone> --install claude --from-install --json <file>`.
No model session ran and no API key was used (the control is script-level by design: its JSON says
`tokens: N/A`); the install ran the real `claude plugin marketplace add` and `plugin install` against
an isolated profile under the control's temp directory, never the operator's. Exit 0; modes
`readme-usage` 1.2 s, `local` 30.2 s, `github` 24.2 s, `local/no-command` 30.0 s, `github/no-command`
22.1 s, `github/process` 26.4 s, all GREEN; `install claude: ok`; `real profile untouched: True`
(surface hash taken before and after); total 137.5 s. Not covered: a GitHub-source marketplace add
(the install is from a local clone, as the doc states), and the Codex install.

## 6. The export's own README

`README.md` lines 3, 85, 86, 92, 93, 106 and 124 name `Agrim-Intelligence/sigmaloop` and no other
repository; the plugin id is `sigmaloop@sigmaloop`. The private source slug does appear elsewhere in
the export (13 files outside the README, none an install instruction); that is #1204.

## Defects found

- #1203: the default export's own suite and gates are red (8 test failures, `write_surface check` and
  standalone `exposure_scan tracked` exit 1), because `.sdlc/` is excluded. Labelled `launch:next`
  because it breaks no B1-B8 class as written; it makes the verifier's `ci-run`/`ci-legs` checks
  unreachable, so the owner may want it promoted.
- #1204: the export names the private source slug in 13 files that no scan flags. `launch:next`.

## For #471 (agent-doable part)

Done: the placeholders at `SECURITY.md:13` and `CODE_OF_CONDUCT.md:39` are confirmed absent by a real
build (builder scan 0 findings); the `public_repo` and doctor and issue-template slugs agree on
`Agrim-Intelligence/sigmaloop` (scan status `equal`); the `.sdlc` default-exclude consequence is
measured (607 files, 73 remaining references) and filed (#1203). Remaining, owner only: the name of
this private repository after go-public; the scratch-clone sweep under the scratch root;
collaborator clones (4 collaborators, 1 team); the decision on #1204's per-file dispositions.
