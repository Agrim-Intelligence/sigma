# #429 controls and gestures

## What was measured

The commit that will carry this file does not exist when it is written, so this record pins
content: each value is the git object id `git rev-parse <commit>:<path>` prints on any commit whose
file is byte-identical to the one measured.

| Object | Id |
|---|---|
| `origin/main` when measured (the base) | `a3c913c95803a360d7f4882ab1762d6adfe2faf8` |
| blob `tools/readiness/decide.py` | `92e5ed09b848f56b4ed88e56cd48ba01f79f0c43` |
| blob `tests/test_readiness_decide.py` | `75192637d5566582008af384b9b00b382585c3b8` |
| blob `docs/launch/decision-rule.md` | `f5162f7222abcb83c35c3c6c059989dc363fde1d` |
| blob `docs/launch/scorecard.json` | `a9642c419a1aece5078c43a109b8e87bfc90400f` |

Check a commit with:

```
git rev-parse <commit>:tools/readiness/decide.py <commit>:tests/test_readiness_decide.py \
  <commit>:docs/launch/decision-rule.md <commit>:docs/launch/scorecard.json
```

If any id differs, what was merged is not what was measured: rerun the gestures and the controls.

After the measurements, two edits changed bytes but not behaviour, so the ids above are the final
ones: the test file's placeholder token became a named constant built from two literals (the commit
gate's credential-assignment scan), and the page and this record name the program before each
version string (`tests/test_readme_first_run.py`'s release-name guard). The full test file was rerun
green and every planned node rerun red on the #331 checker after both edits.

Measured 2026-10-02 on macOS (Darwin 25.6), branch `sdlc/429`. Python: the system `python3`
Python 3.9.6 and Python 3.12.13. git: the one `PATH` resolves first is Homebrew's git 2.55.0 (CI's legs also run
git 2.55.0); the whole test file was also run once with Apple's git 2.39.5 first on `PATH`. gh 2.98.0, only
for its `config get` behaviour (unset prints nothing, exit 0; set prints the path; unreadable YAML
exits 1) and, for the pre-PR review's B1, for read-only REST GETs (`repos/cli/cli/git/ref/heads/trunk`
and this repository's own `main` and issue list; nothing was written to GitHub); every test and the
other gestures below stub `gh`. CI runs Linux with Python 3.10, 3.11, 3.12 and
3.13 and macOS with Python 3.12; those legs were not run here, and the pull request's checks are
their record. Nothing was run on Windows; the checker refuses to run there (exit 2).

## Test counts and runtime

`python3 -m pytest tests/test_readiness_decide.py -q`, serial, on this machine while other work
shared it (the wall time moves with the load; the plan's budget is 60 s serial on 3.12):

After the pre-PR review fixes (269 tests: the 259 below plus 10 new nodes), git 2.55.0, at a load
average near 40:

| Python | git | Result | Wall time |
|---|---|---|---|
| Python 3.9.6 | git 2.55.0 | 269 passed | 157 s |
| Python 3.12.13 | git 2.55.0 | 269 passed | 125 s |

Before those fixes (git 2.39.5 was not re-run after them):

| Python | git | Result | Wall time |
|---|---|---|---|
| Python 3.9.6 | git 2.55.0 | 259 passed | 50 s; 150 s at a load average near 30 |
| Python 3.9.6 | git 2.39.5 | 259 passed | 149 s at a load average near 30 (no quiet run) |
| Python 3.12.13 | git 2.55.0 | 259 passed | 48 s (258 tests, before the last regression test was added); 120 s at a load average of 15 |
| Python 3.12.13 | git 2.39.5 | 259 passed | 81 s at a load average near 30 (no quiet run) |

The 23 tests the plan names (69 nodes with their parameters) were each seen fail
by assertion on the #331 checker (`origin/sdlc/331`'s `tools/readiness/decide.py`, the test file
unchanged) and pass on this one: against the #331 checker, 69 failed by assertion on Python 3.9.6 and
69 failed by assertion on Python 3.12.13 (no error, no skip); against this one, 69 passed
and 69 passed. The run is red_green.py's own invocation (`python3 -m pytest -q -rA
--tb=short --color=no`) with `COLUMNS=300`: at pytest's default 80 columns a piped run drops the
failure reason from summary lines this long. Re-run with the final test file after the pre-PR
review fixes: 69 failed, all by `AssertionError`, on Python 3.9.6 and on Python 3.12.13; the 10 nodes added for
those fixes also fail, all by `AssertionError`, on the #331 checker and on the reviewed checker.

## The seven defects

Each reproduction was run with the gesture the page gives, from the fixture's root, with the #331
checker and then this one. Defects 1–5 end NO-GO (exit 1) for the reason the real `main` gives:
the ambient state is ignored, not detected, so it cannot reach the verdict. Every offline run also
carries the offline reason, because a file cannot establish that no blocker is open; each test
asserts the specific reason line, and each reproduction was also run live (stub `gh`), where the
#331 checker printed GO as well.

| # | Reproduction | #331 checker | This checker | Tests (red on #331, green here) |
|---|---|---|---|---|
| 1 | `git branch origin/main HEAD`, `git tag origin/main HEAD`, `git update-ref refs/origin/main HEAD` (main lacks the benchmark; HEAD has it) | GO, exit 0 | NO-GO, exit 1, `benchmark results: ... is not tracked at origin/main` | `test_local_ref_named_origin_main_cannot_stand_in_for_the_remote` (3) |
| 2 | `GIT_DIR` naming another checkout whose main has the benchmark, with and without `GIT_WORK_TREE` | GO, exit 0 | NO-GO, exit 1, same reason | `test_inherited_git_dir_cannot_redirect_main` (2), `test_documented_gesture_ignores_an_inherited_git_dir` (2) |
| 3 | `GIT_DIR`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `~/.gitconfig`, `$XDG_CONFIG_HOME/git/config`, a global `include.path`, a local `insteadOf`, a local `include.path`; victim/widget has open blocker #7 | GO, exit 0; `gh` asked for attacker/widget | NO-GO, exit 1, `open launch:blocker: #7`; `gh` asked for victim/widget only | `test_git_environment_or_config_cannot_redirect_the_blocker_read` (10), `test_gh_runs_without_any_git_environment` |
| 4 | `git replace <origin/main> HEAD`; `GIT_REPLACE_REF_BASE=refs/heads/` with a branch named like main's sha | GO, exit 0 | NO-GO, exit 1, the benchmark reason | `test_replace_ref_cannot_stand_in_for_origin_main` (2) |
| 5 | main unscored with no (or a proposed) definition; the working tree scored and signed | GO, exit 0 | NO-GO, exit 1, `D1 Correctness: not scored ...` and `definition missing: ...` / `definition not signed: status is 'proposed'` | `test_working_tree_scorecard_and_definition_cannot_steer_the_verdict` (2), `test_working_tree_is_never_read` (5) |
| 6 | the docstring claimed Linux, macOS and Windows; no CI leg runs Windows | the claim | the sentence CI's legs prove, built from `.github/workflows/ci.yml` by the test; Windows refused, exit 2 | `test_platform_claims_match_the_ci_legs`, `test_windows_is_refused_before_anything_runs` |
| 7 | none of the six regression tests the original fix plan named existed | 0 of 6 collected (`ERROR: not found`, exit 4) | all six present, 20 nodes collected | the six above |

Beyond the issue, measured GO on the #331 checker and refused here: `refs/remotes/origin/main`
moved by hand, a symbolic `refs/remotes/origin/main`, and a stale fetch (the remote's `main` moved
on after this checkout fetched) — `test_local_main_that_differs_from_the_remote_main_is_refused`,
`test_symbolic_origin_main_is_refused`.

## Pre-PR review #1: two findings

A fresh, author-blind review of this diff before the pull request blocked it on two findings, both
reproduced here first and each now covered by tests seen red, by assertion, on the reviewed checker
and on the #331 checker, and green on this one.

**B1. A pager could forge every REST read.** `gh` ran with the caller's environment minus `GIT_*`,
`GH_REPO` and `GH_HOST`. With `GH_FORCE_TTY` set, gh treats its piped stdout as a terminal and runs
`gh api` output through a pager, and the checker parsed whatever the pager printed. Measured with
gh 2.98.0 on `gh api repos/cli/cli/git/ref/heads/trunk` (a read-only public GET), the parent's
environment as given, then through the checker's own `_gh_env()`:

| Parent environment | gh as called before | gh via `_gh_env()` now |
|---|---|---|
| none of the below | the real JSON | the real JSON |
| `GH_FORCE_TTY=1 GH_PAGER=<forger>` | `X-FORGED` | the real JSON |
| `GH_FORCE_TTY=1 PAGER=<forger>` | `X-FORGED` | the real JSON |
| `GH_FORCE_TTY=1`, a scratch `GH_CONFIG_DIR` whose `config.yml` sets `pager: <forger>` | `X-FORGED` | the real JSON |
| `GH_FORCE_TTY=1 GH_PAGER=<forger> PAGER=<forger> CLICOLOR_FORCE=1` | `X-FORGED` | the real JSON |

What each pin does, measured one at a time on raw gh: `GH_PAGER=""` alone switches the pager off
under `GH_FORCE_TTY=1`, whether the pager came from `PAGER` or from gh config's `pager` (the output
is then coloured JSON, which fails to parse: exit 2, not a forgery); without `GH_FORCE_TTY` no
pager runs at all (piped stdout is not a terminal); `NO_COLOR=1` removes the colour under a forced
terminal; `CLICOLOR_FORCE=1` colours the JSON even without a terminal and even with `NO_COLOR=1`,
so it is removed, not countered. `PAGER=""` is belt and braces: `GH_PAGER=""` already wins over it.
The forger was `sh -c 'cat >/dev/null; printf X-FORGED'`.

End to end, with the real gh and this repository's own `origin` URL on a fixture checkout whose
`main` is not the real one (control: refused, sha mismatch), and a pager that rewrote the ref JSON
to the fixture's sha and every list to `[]`:

```
# the reviewed checker
$ GH_FORCE_TTY=1 GH_PAGER=<pager> python3 tools/readiness/decide.py .
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at <fixture sha>
info: blockers read over REST from github.com/<OWNER>/<NAME>
GO
(exit 0)

# this checker
$ GH_FORCE_TTY=1 GH_PAGER=<pager> python3 tools/readiness/decide.py .
decide.py: REFUSED: main of github.com/<OWNER>/<NAME> is <real sha> over REST, but this checkout's refs/remotes/origin/main is <fixture sha>; git fetch origin, then rerun
(exit 2)   the pager never ran
```

Fixed: every gh call runs with `GH_FORCE_TTY` and `CLICOLOR_FORCE` removed as well, and with
`GH_PAGER` and `PAGER` set to `""` and `NO_COLOR=1`. Tests: `test_no_gh_call_can_be_paged_or_coloured`
(in-process, every gh call's environment) and `test_documented_gesture_cannot_be_forged_by_a_pager`
(2: the page's gesture through a stub gh that pages as the table above measured, with `GH_PAGER`
or `PAGER` as the forger; victim/widget's open #7 must stay NO-GO). The stub does not read gh
config, so the config `pager` row is covered by the measurement, not by a test. The docstring's
"nobody can steer the outcome by ... setting an environment variable" and the CHANGELOG's "Nothing
outside `main` can steer the verdict" were false; both now enumerate what is removed or pinned and
the residual trust roots, and `test_docs_make_no_absolute_steering_claim` keeps them so.

**B2. `--repo` accepted a same-commit fork.** The REST check compares commits only, so a fork or
mirror whose `main` is the same commit passed it, and its (empty) blocker list was used. Live, stub
gh, victim/widget has open #7 and every repository's `main` is the fixture's commit:

```
# the reviewed checker (the #331 checker prints GO too)
$ python3 tools/readiness/decide.py . --repo attacker/widget
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 80a3e6083d340fe0394d04c2c816be9fddc17a8f
info: blockers read over REST from github.com/attacker/widget
GO
(exit 0)   gh was asked for: repos/attacker/widget/git/ref/heads/main, repos/attacker/widget/issues?state=open&labels=launch:blocker&per_page=100

# this checker
$ python3 tools/readiness/decide.py . --repo attacker/widget
decide.py: REFUSED: --repo names github.com/attacker/widget, but this checkout's only remote, origin, names github.com/victim/widget; a fork or mirror whose main is the same commit has its own blocker list. Pass --repo naming origin's repository, or omit it
(exit 2)   no gh call
```

When `origin` cannot name a repository, `--repo` is accepted as the operator's assertion and said
so (this checker; two remotes):

```
$ python3 tools/readiness/decide.py . --repo victim/widget
open launch:blocker: #7
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 603cec97226c2da98df7e997c2b5448124c83e41
info: --repo victim/widget is the operator's assertion: the checkout's remotes are ['origin', 'upstream'] (need exactly one, named origin), so origin cannot name the repository whose blockers count
info: blockers read over REST from github.com/victim/widget
NO-GO
(exit 1)
```

Fixed: when `origin` names a repository, `--repo` must be that one (host `github.com`, OWNER/NAME
case-insensitive; origin's spelling is then used), else refused naming both. Tests:
`test_repo_that_is_not_origins_repository_is_refused` (2: a same-commit fork, an origin on another
host), `test_repo_that_names_origins_repository_is_used` (`Victim/Widget` reads victim/widget's
#7), `test_repo_when_origin_cannot_name_the_repository_is_the_operators_assertion` (3: two remotes,
two URLs, a URL of no known shape). The test fixtures' default `origin` moved from
`example.invalid` to `github.com/acme/widget`, so the existing `--repo acme/widget` tests name
origin's own repository; `test_valid_repo_argument_is_used_verbatim` now runs with two remotes, the
assertion path.

**Non-blocking: the commit-graph.** A forged `.git/objects/info/commit-graph` could in principle make
git read another root tree for the same commit id. Every git call now carries `-c
core.commitGraph=false`, which on the command line beats the checkout's own config and, per
git-config(1), stops git reading the commit-graph file. Reasoned, not tested: no forged-graph
fixture was built. `test_every_git_call_is_scrubbed_pinned_and_config_free` pins the flag.

## Gesture transcripts

Every run: system `python3`, the fixture's root as the working directory, a stub `gh` first on
`PATH` (it answers `gh config get` with nothing, the REST read of `main` with the fixture's origin
`main`, victim/widget's issues with the fixture's blocker list and anything else with `[]`), every
`GIT_*` variable removed and an empty `HOME`. Commit ids vary per fixture.

Defect 1, `git branch origin/main HEAD`:

```
# the #331 checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
info: D12 Market is informational (score 3); it blocks only through B3
GO
(exit 0)

# this checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
benchmark results: docs/launch/evidence/benchmark-results.json is not tracked at origin/main
blocker list read offline from 'blockers.json'; only the live REST read can establish that no open launch:blocker issue exists
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at b1c0d24abac72256b648e9e4c1507a49bbb8c6b1
info: blockers read offline from 'blockers.json'
NO-GO
(exit 1)
```

Defect 2, `GIT_DIR` naming the other checkout:

```
# the #331 checker
$ GIT_DIR=../../other/repo/.git python3 tools/readiness/decide.py . --blockers-json blockers.json
info: D12 Market is informational (score 3); it blocks only through B3
GO
(exit 0)

# this checker
$ GIT_DIR=../../other/repo/.git python3 tools/readiness/decide.py . --blockers-json blockers.json
benchmark results: docs/launch/evidence/benchmark-results.json is not tracked at origin/main
blocker list read offline from 'blockers.json'; only the live REST read can establish that no open launch:blocker issue exists
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 0d2602c97d659f0f6b39ace2ea80b96a11dcc737
info: blockers read offline from 'blockers.json'
NO-GO
(exit 1)
```

Defect 3, a `GIT_CONFIG_GLOBAL` file rewriting victim to attacker with `insteadOf` (live):

```
# the #331 checker
$ GIT_CONFIG_GLOBAL=../../insteadof.cfg python3 tools/readiness/decide.py .
info: D12 Market is informational (score 3); it blocks only through B3
GO
(exit 0)   gh was asked for: repos/attacker/widget/issues?state=open&labels=launch:blocker&per_page=100

# this checker
$ GIT_CONFIG_GLOBAL=../../insteadof.cfg python3 tools/readiness/decide.py .
open launch:blocker: #7
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 1c56c12c72fde9cd4b19c21725e6edf706a6b5e7
info: blockers read over REST from github.com/victim/widget
NO-GO
(exit 1)   gh was asked for: repos/victim/widget/git/ref/heads/main, repos/victim/widget/issues?state=open&labels=launch:blocker&per_page=100
```

Defect 4, `git replace <origin/main> HEAD`:

```
# the #331 checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
info: D12 Market is informational (score 3); it blocks only through B3
GO
(exit 0)

# this checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
benchmark results: docs/launch/evidence/benchmark-results.json is not tracked at origin/main
blocker list read offline from 'blockers.json'; only the live REST read can establish that no open launch:blocker issue exists
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 8eb943a257a334b303f557d4465d4af3d072bc7c
info: blockers read offline from 'blockers.json'
NO-GO
(exit 1)
```

Defect 5, main without a definition; a scored card and an untracked signed definition in the
working tree (live):

```
# the #331 checker
$ python3 tools/readiness/decide.py .
info: D12 Market is informational (score 3); it blocks only through B3
GO
(exit 0)   gh was asked for: repos/victim/widget/issues?state=open&labels=launch:blocker&per_page=100

# this checker
$ python3 tools/readiness/decide.py .
definition missing: docs/launch/definition.json is not tracked at origin/main
D1 Correctness: not scored (gating; needs >= 3 with evidence)
D2 Skills: not scored (gating; needs >= 3 with evidence)
D3 Outcomes and cost: not scored (gating; needs >= 3 with evidence)
D5 Five properties: not scored (gating; needs >= 3 with evidence)
D6 Platform: not scored (gating; needs >= 3 with evidence)
D7 Onboarding: not scored (gating; needs >= 3 with evidence)
D8 Docs: not scored (gating; needs >= 3 with evidence)
D9 Upgrade, coexistence, uninstall: not scored (gating; needs >= 3 with evidence)
D10 Security, privacy, exposure: not scored (gating; needs >= 3 with evidence)
D11 Operations: not scored (gating; needs >= 3 with evidence)
D13 Legal and naming: not scored (gating; needs >= 3 with evidence)
benchmark results: not named in the scorecard
info: D12 Market is informational (unscored); it blocks only through B3
info: main is refs/remotes/origin/main at 891ae898c19b685a56b9b5c60ed18364e2f23d1a
info: blockers read over REST from github.com/victim/widget
NO-GO
(exit 1)   gh was asked for: repos/victim/widget/git/ref/heads/main, repos/victim/widget/issues?state=open&labels=launch:blocker&per_page=100
```

Beyond the issue, `refs/remotes/origin/main` moved by hand, and made symbolic (this checker):

```
# this checker
$ python3 tools/readiness/decide.py .
decide.py: REFUSED: main of github.com/victim/widget is 3444c73a9abe6e4ce860bebf4a72368cf6ee3ea2 over REST, but this checkout's refs/remotes/origin/main is 1aa15861b60ad3ed355fc43b7558b7c1f5498963; git fetch origin, then rerun
(exit 2)   gh was asked for: repos/victim/widget/git/ref/heads/main

# this checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
decide.py: REFUSED: refs/remotes/origin/main is a symbolic ref (to refs/heads/main); the checker reads only a ref that git fetch wrote
(exit 2)
```

The page's two gestures on a passing fixture (this checker): live GO, exit 0; offline NO-GO, exit
1, the offline reason its only reason.

```
# this checker
$ python3 tools/readiness/decide.py .
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 17ba39bd377a1d655b060b1cf27cc5623d187b1b
info: blockers read over REST from github.com/victim/widget
GO
(exit 0)   gh was asked for: repos/victim/widget/git/ref/heads/main, repos/victim/widget/issues?state=open&labels=launch:blocker&per_page=100

# this checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
blocker list read offline from 'blockers.json'; only the live REST read can establish that no open launch:blocker issue exists
info: D12 Market is informational (score 3); it blocks only through B3
info: main is refs/remotes/origin/main at 17ba39bd377a1d655b060b1cf27cc5623d187b1b
info: blockers read offline from 'blockers.json'
NO-GO
(exit 1)
```

#331's acceptance item 3, the shipped scorecard and definition committed on a fixture `main`
(offline, this checker):

```
# this checker
$ python3 tools/readiness/decide.py . --blockers-json blockers.json
definition not signed: status is 'proposed'
D1 Correctness: not scored (gating; needs >= 3 with evidence)
D2 Skills: not scored (gating; needs >= 3 with evidence)
D3 Outcomes and cost: not scored (gating; needs >= 3 with evidence)
D5 Five properties: not scored (gating; needs >= 3 with evidence)
D6 Platform: not scored (gating; needs >= 3 with evidence)
D7 Onboarding: not scored (gating; needs >= 3 with evidence)
D8 Docs: not scored (gating; needs >= 3 with evidence)
D9 Upgrade, coexistence, uninstall: not scored (gating; needs >= 3 with evidence)
D10 Security, privacy, exposure: not scored (gating; needs >= 3 with evidence)
D11 Operations: not scored (gating; needs >= 3 with evidence)
D13 Legal and naming: not scored (gating; needs >= 3 with evidence)
benchmark results: not named in the scorecard
blocker list read offline from 'blockers.json'; only the live REST read can establish that no open launch:blocker issue exists
info: D12 Market is informational (unscored); it blocks only through B3
info: main is refs/remotes/origin/main at 6d09bd3d8446dfbeb68a53e7b44c52aa42e4c07d
info: blockers read offline from 'blockers.json'
NO-GO
(exit 1)
```

On this repository before the merge, `main` has no scorecard yet, so both gestures print
`decide.py: MALFORMED: scorecard not found at refs/remotes/origin/main (<sha>)`, exit 2, nothing
on stdout and no `gh` call (measured with a recording `gh` stub first on `PATH`).

## Sensitivity controls

Each guard was broken once in a copy of `tools/readiness/decide.py` (one breakage at a time, in a
scratch tree holding the unchanged test file and docs), and all of `tests/test_readiness_decide.py`
was run on Python 3.9.6 and Python 3.12.13. "red" means the run failed AND every test the row names failed.

| Breakage (one at a time) | Must go red | Python 3.9.6 | Python 3.12.13 |
|---|---|---|---|
| `GIT_*` scrub: `_git_env` keeps `os.environ` | `test_inherited_git_dir_cannot_redirect_main`, `test_documented_gesture_ignores_an_inherited_git_dir`, `test_git_environment_or_config_cannot_redirect_the_blocker_read[git-dir]`, `test_every_git_call_is_scrubbed_pinned_and_config_free`, `test_inherited_git_trace_writes_nothing` | red, 7 failed | red, 7 failed |
| `GIT_*` scrub and raw URL: both breakages at once | `test_git_environment_or_config_cannot_redirect_the_blocker_read[config-count]`, `test_git_environment_or_config_cannot_redirect_the_blocker_read[config-parameters]` | red, 11 failed | red, 11 failed |
| config off: `GIT_CONFIG_NOSYSTEM` and `GIT_CONFIG_GLOBAL` pins dropped | `test_global_remote_entries_cannot_cause_a_false_refusal`, `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 4 failed | red, 4 failed |
| raw URL: back to `remote get-url --all origin` | `test_git_environment_or_config_cannot_redirect_the_blocker_read[local-insteadof]` | red, 3 failed | red, 3 failed |
| exact ref, short: `rev-parse --verify -q origin/main^{commit}` | `test_local_ref_named_origin_main_cannot_stand_in_for_the_remote` | red, 5 failed | red, 5 failed |
| exact ref, qualified: `rev-parse --verify -q refs/remotes/origin/main^{commit}` | `test_main_that_cannot_be_read_is_refused_never_a_verdict[heads-lookalike]`, `test_main_that_cannot_be_read_is_refused_never_a_verdict[tags-lookalike]`, `test_main_that_cannot_be_read_is_refused_never_a_verdict[refs-lookalike]` | red, 5 failed | red, 5 failed |
| symref refusal deleted | `test_symbolic_origin_main_is_refused` | red, 1 failed | red, 1 failed |
| replace off: `--no-replace-objects` dropped | `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 1 failed | red, 1 failed |
| replace off: `GIT_NO_REPLACE_OBJECTS` pin dropped | `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 1 failed | red, 1 failed |
| replace off: flag and env pin both dropped | `test_replace_ref_cannot_stand_in_for_origin_main[git-replace]`, `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 2 failed | red, 2 failed |
| committed tree: scorecard read from the working tree | `test_working_tree_scorecard_and_definition_cannot_steer_the_verdict`, `test_working_tree_is_never_read[scorecard-deleted]` | red, 7 failed | red, 7 failed |
| committed tree: definition read from the working tree | `test_working_tree_scorecard_and_definition_cannot_steer_the_verdict`, `test_working_tree_is_never_read[definition-deleted]` | red, 6 failed | red, 6 failed |
| size cap: `2**40` | `test_scorecard_or_definition_at_main_that_is_not_a_small_regular_file_is_malformed[card-oversized]`, `test_scorecard_or_definition_at_main_that_is_not_a_small_regular_file_is_malformed[def-oversized]` | red, 2 failed | red, 2 failed |
| REST main: sha compare skipped | `test_local_main_that_differs_from_the_remote_main_is_refused`, `test_rest_main_read_that_fails_or_is_malformed_is_refused[sha-mismatch]` | red, 3 failed | red, 3 failed |
| REST main: shape checks skipped (plain `json.loads`, no type or sha-shape check) | `test_rest_main_read_that_fails_or_is_malformed_is_refused[duplicate-key]`, `test_rest_main_read_that_fails_or_is_malformed_is_refused[array]`, `test_rest_main_read_that_fails_or_is_malformed_is_refused[wrong-type]` | red, 3 failed | red, 3 failed |
| unix socket: check is a no-op | `test_gh_unix_socket_is_refused` | red, 4 failed | red, 4 failed |
| unix socket: the `gh config get -h <host>` call dropped | `test_gh_unix_socket_is_refused[per-host]` | red, 2 failed | red, 2 failed |
| lazy fetch: `protocol.allow=never`, the six per-protocol pins and `GIT_NO_LAZY_FETCH` dropped | `test_partial_clone_main_read_never_fetches`, `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 3 failed | red, 3 failed |
| offline never GO: offline reason dropped | `test_offline_blocker_list_is_never_go` | red, 1 failed | red, 1 failed |
| Windows refusal deleted | `test_windows_is_refused_before_anything_runs` | red, 1 failed | red, 1 failed |
| version gate floor `(2, 0)` | `test_old_git_is_refused` | red, 1 failed | red, 1 failed |
| gh scrub: gh keeps `GIT_*` | `test_gh_runs_without_any_git_environment` | red, 1 failed | red, 1 failed |
| NUL/control character allowed in an evidence path | `test_evidence_path_with_a_control_character_is_nogo` | red, 4 failed | red, 4 failed |
| missing blob's size taken as present | `test_partial_clone_missing_benchmark_blob_is_refused` | red, 1 failed | red, 1 failed |
| peel to a commit skipped (main may be a tree) | `test_main_that_cannot_be_read_is_refused_never_a_verdict[not-a-commit]` | red, 1 failed | red, 1 failed |
| root below the top level accepted | `test_main_that_cannot_be_read_is_refused_never_a_verdict[below-top-level]` | red, 1 failed | red, 1 failed |
| no origin/main falls back to HEAD | `test_main_that_cannot_be_read_is_refused_never_a_verdict[no-origin-main]` | red, 5 failed | red, 5 failed |
| dubious ownership: global-config explanation dropped | `test_foreign_owned_checkout_is_refused_naming_dubious_ownership` | red, 1 failed | red, 1 failed |

The shape-check row names which of `test_rest_main_read_that_fails_or_is_malformed_is_refused`'s
cases it reddens; the others (`nonzero`, `empty`, `unparsable`, `truncated`) are failures of the
read itself, and `no-sha` stays refused by the sha comparison.

The two tables above were run on the checker the pre-PR review saw; its fixes do not touch those
guards. The fixes' own guards, broken the same way (one breakage at a time, the whole final test
file):

| Breakage (one at a time) | Must go red | Python 3.9.6 | Python 3.12.13 |
|---|---|---|---|
| gh: GH_FORCE_TTY kept | `test_no_gh_call_can_be_paged_or_coloured` | red, 1 failed | red, 1 failed |
| gh: CLICOLOR_FORCE kept | `test_no_gh_call_can_be_paged_or_coloured` | red, 1 failed | red, 1 failed |
| gh: GH_PAGER pin dropped | `test_no_gh_call_can_be_paged_or_coloured` | red, 1 failed | red, 1 failed |
| gh: PAGER pin dropped | `test_no_gh_call_can_be_paged_or_coloured` | red, 1 failed | red, 1 failed |
| gh: NO_COLOR pin dropped | `test_no_gh_call_can_be_paged_or_coloured` | red, 1 failed | red, 1 failed |
| gh: all B1 removals and pins dropped (the reviewed env) | `test_no_gh_call_can_be_paged_or_coloured`, `test_documented_gesture_cannot_be_forged_by_a_pager[GH_PAGER]`, `test_documented_gesture_cannot_be_forged_by_a_pager[PAGER]` | red, 3 failed | red, 3 failed |
| --repo: origin match skipped | `test_repo_that_is_not_origins_repository_is_refused[fork-same-main]`, `test_repo_that_is_not_origins_repository_is_refused[other-host]` | red, 3 failed | red, 3 failed |
| --repo: host not compared | `test_repo_that_is_not_origins_repository_is_refused[other-host]` | red, 1 failed | red, 1 failed |
| --repo: compared case-sensitively | `test_repo_that_names_origins_repository_is_used` | red, 1 failed | red, 1 failed |
| --repo: operator's-assertion line dropped | `test_repo_when_origin_cannot_name_the_repository_is_the_operators_assertion[two-remotes]`, `test_repo_when_origin_cannot_name_the_repository_is_the_operators_assertion[two-urls]`, `test_repo_when_origin_cannot_name_the_repository_is_the_operators_assertion[unparseable-url]` | red, 3 failed | red, 3 failed |
| git: core.commitGraph=false dropped | `test_every_git_call_is_scrubbed_pinned_and_config_free` | red, 1 failed | red, 1 failed |

#331's controls, re-run on this checker (the acceptance control first):

| #331 control, re-run on this checker | Must go red | Python 3.9.6 | Python 3.12.13 |
|---|---|---|---|
| #331 acceptance: blockers list ignored | `test_one_open_blocker_is_nogo_naming_its_number`, `test_bad_blocker_list_is_malformed`, `test_blocker_entry_of_the_wrong_shape_is_malformed`, `test_blocker_state_matches_case_insensitively`, `test_blocker_with_unknown_or_missing_state_is_malformed`, `test_gh_path_uses_rest_paginated_and_decodes_every_page` | red, 27 failed | red, 27 failed |
| #331 evidence validity skipped | `test_evidence_entry_that_is_not_a_link_is_nogo_naming_it`, `test_empty_evidence_file_is_nogo`, `test_one_bad_entry_beside_a_good_one_is_still_nogo`, `test_untracked_evidence_file_is_nogo`, `test_symlink_evidence_escaping_the_repo_is_nogo`, `test_directory_evidence_is_not_a_file` | red, 26 failed | red, 26 failed |
| #331 URL host not required | `test_evidence_entry_that_is_not_a_link_is_nogo_naming_it` | red, 1 failed | red, 1 failed |
| #331 directory/symlink accepted as a regular file at main (also: symlink blob at main) | `test_directory_evidence_is_not_a_file`, `test_symlink_evidence_escaping_the_repo_is_nogo`, `test_benchmark_committed_as_a_symlink_is_nogo` | red, 4 failed | red, 4 failed |
| #331 unnamed benchmark passes | `test_unnamed_benchmark_file_is_nogo` | red, 4 failed | red, 4 failed |
| #331 path outside evidence/ accepted | `test_steering_or_bad_scorecard_is_malformed` | red, 1 failed | red, 1 failed |
| #331 untracked file accepted | `test_untracked_benchmark_is_nogo`, `test_untracked_evidence_file_is_nogo`, `test_missing_benchmark_file_is_nogo` | red, 18 failed | red, 18 failed |
| #331 empty-at-main accepted | `test_empty_benchmark_at_origin_main_is_nogo`, `test_empty_evidence_file_is_nogo` | red, 3 failed | red, 3 failed |
| #331 unknown/missing state dropped | `test_blocker_with_unknown_or_missing_state_is_malformed` | red, 3 failed | red, 3 failed |
| #331 gh empty output read as zero | `test_gh_exit_0_with_empty_output_is_malformed` | red, 2 failed | red, 2 failed |
| #331 several remotes accepted | `test_default_repo_needs_exactly_one_origin_remote` | red, 1 failed | red, 1 failed |
| #331 unknown top-level key ignored | `test_steering_or_bad_scorecard_is_malformed` | red, 1 failed | red, 1 failed |
| #331 unknown dimension key ignored | `test_steering_or_bad_scorecard_is_malformed` | red, 1 failed | red, 1 failed |
| #331 renamed dimension accepted | `test_steering_or_bad_scorecard_is_malformed` | red, 1 failed | red, 1 failed |
| #331 impossible date accepted | `test_signed_without_signer_or_date_is_nogo` | red, 2 failed | red, 2 failed |
| #331 B1 gh left to resolve `{owner}/{repo}` | `test_default_repo_is_derived_from_origins_url`, `test_documented_gesture_with_gh_repo_set_reads_this_repository`, `test_gh_repo_and_gh_host_cannot_redirect_the_read` | red, 131 failed | red, 131 failed |
| #331 B1 `GH_REPO`/`GH_HOST` not scrubbed | `test_documented_gesture_with_gh_repo_set_reads_this_repository`, `test_gh_repo_and_gh_host_cannot_redirect_the_read` | red, 4 failed | red, 4 failed |
| #331 B1 `--hostname` not passed (issues read) | `test_default_repo_is_derived_from_origins_url`, `test_documented_gesture_with_gh_repo_set_reads_this_repository`, `test_gh_path_uses_rest_paginated_and_decodes_every_page`, `test_gh_repo_and_gh_host_cannot_redirect_the_read`, `test_valid_repo_argument_is_used_verbatim` | red, 121 failed | red, 121 failed |
| #331 B1 origin with several URLs accepted | `test_origin_with_two_urls_is_malformed` | red, 1 failed | red, 1 failed |
| #331 B1 origin URL path not validated | `test_origin_url_of_an_unknown_shape_is_malformed` | red, 6 failed | red, 6 failed |
| #331 B2 old `--repo` segment pattern | `test_repo_argument_that_is_not_owner_name_is_malformed` | red, 10 failed | red, 10 failed |
| #331 B2 `--repo` matched with `$`, not `fullmatch` | `test_repo_argument_that_is_not_owner_name_is_malformed` | red, 1 failed | red, 1 failed |
| #331 (a) duplicate keys accepted | `test_duplicate_gating_key_cannot_flip_a_dimension`, `test_duplicate_key_in_blocker_list_is_malformed`, `test_duplicate_key_in_definition_is_malformed`, `test_duplicate_key_in_gh_output_is_malformed`, `test_duplicate_key_in_scorecard_is_malformed` | red, 6 failed | red, 6 failed |
| #331 (b) unparsable evidence URL raises | `test_evidence_entry_that_is_not_a_link_is_nogo_naming_it` | red, 1 failed | red, 1 failed |
| #331 (b) unreadable-input catches removed | `test_unreadable_input_is_malformed_never_a_traceback` | red, 1 failed | red, 1 failed |
| #331 (c) evidence not checked at main | `test_empty_evidence_file_is_nogo`, `test_untracked_evidence_file_is_nogo` | red, 10 failed | red, 10 failed |
| #331 (d) signed_by not a plain login | `test_signed_without_signer_or_date_is_nogo` | red, 7 failed | red, 7 failed |
| #331 (d) future signed_on accepted | `test_signed_without_signer_or_date_is_nogo` | red, 1 failed | red, 1 failed |
| #331 (e) `--help` exits 0 | `test_help_exits_2_never_the_go_code`, `test_help_via_the_documented_gesture_exits_2` | red, 7 failed | red, 7 failed |
| #331 (f) labels not required | `test_blocker_entry_of_the_wrong_shape_is_malformed` | red, 5 failed | red, 5 failed |
| #331 (f) non-object `pull_request` dropped | `test_blocker_entry_of_the_wrong_shape_is_malformed` | red, 2 failed | red, 2 failed |

Retired with the working-tree reads, and where each guard now lives: "symlink/resolve escape not
checked", "dirty benchmark accepted" and "checkout symlink accepted" (the working tree is never
read: `test_working_tree_is_never_read`, and a committed symlink is not a regular file at main:
`test_symlink_evidence_escaping_the_repo_is_nogo`, `test_benchmark_committed_as_a_symlink_is_nogo`);
"unverifiable main passes the benchmark" and "evidence passes when main unreadable" (an unreadable
`main` is now refused before anything is checked: the "no origin/main falls back to HEAD" row and
`test_main_that_cannot_be_read_is_refused_never_a_verdict`).

## Not measured

- A real git older than 2.32 (the test stubs `git --version`), and whether git 2.32–2.38 honour
  `GIT_NO_LAZY_FETCH` (git 2.39.5 and git 2.55.0 do; on a git that does not, the protocol pins alone stop
  a lazy fetch).
- A checkout really owned by another user: the test sets git's own
  `GIT_TEST_ASSUME_DIFFERENT_OWNER` through a stub `git`.
- Windows, and the CI legs (Linux 3.10–3.13, macOS 3.12): the pull request's checks are their
  record.
- A forged commit-graph file: `-c core.commitGraph=false` is per git-config(1) and pinned by a
  test, but no forged-graph fixture was built.
- The paging gesture test's stub models gh's measured `GH_FORCE_TTY` / `GH_PAGER` / `PAGER`
  behaviour but not gh config's `pager`; that path is covered by the real-gh measurement only.
- Other gh variables that could change what gh prints (anything beyond the removed and pinned
  ones) were not surveyed; they are inherited and stated as a trust root.
- `gh` honouring a per-host `http_unix_socket`: gh 2.98.0 reported it with `gh config get -h` but
  its `gh api` call still went to the network; the checker refuses it anyway.
