# Public export verification: commit de188d1fb5dd

**LOCAL RUNS ON macOS. THIS IS NOT GITHUB CI.** Every number below was produced on the operator's own
macOS machine, in a fresh Python 3.12 virtual environment, on a copy of the exported tree. No GitHub
Actions job ran.

**What was NOT run:** the GitHub CI legs (all five); any Linux leg (no Docker, no Ubuntu, no Python 3.10,
3.11 or 3.13); a real push of the export to any repository; `verify_public_repo.py` (it reads a pushed
repository, and nothing was pushed); the `owner-merged` review level; the doctor from an installed copy; the
`regression-control` job. The `pr-merged` review level was run by the two default builds (REST reads of
this repository's pull request 1216, no writes), and NOT by the planted-control builds, which used `clean`.

Every kept log is under `~/.sigma-ops/export-verify-2026-10-12/` (outside every repository); the file
named next to each number is the source of that number. Measured commit:
`de188d1fb5ddb12b8cda407a3c907b43e855cf29` (origin/main at the start of the run; it moved from `7360ea44`
while an earlier run on that commit was in progress, so that run was discarded and kept only under
`superseded-7360ea44/`). Date: 2026-10-12. Goals served: #1214, #1220, #1215 verified on main; #397.

## Result in one table

| Check | Result | Kept log |
|-------|--------|----------|
| Build the export twice | VERIFIED twice, identical tree `269fb8b53b6a67322bcc1d97eb745a8b42a50e25`, 1235 files each, same export commit `89618a74697f3d8556b6a4842b44eb778d8612b0` | `build1.stdout`, `build2.stdout`, `filecount1.txt`, `filecount2.txt` |
| Exported suite, plain whole-suite pytest (NOT the CI `test` job command), macOS Python 3.12 | **1 failed**, 16870 passed, 30 skipped, 9 xfailed, in 5273.44 s | `pytest.log` |
| The five export-transform skips | all five present (`EXPORT-SKIP`, listed below) | `pytest.log` |
| The one failure | deterministic, a real failure (not a load flake): see "The failure" | `pytest.log`, `alone-export.log` |
| Other steps run (a subset of the `test` job's steps; the smoke runner and the safety check were not run) | rename check, write-surface ratchet, quality gate, decision gate validate and check, SKILL.md frontmatter test: all exit 0 | `ci_exits.txt` |
| Planted fake controls | md, py, extensionless file, path: REJECTED; two wrapped forms: NOT caught | `controls/summary.json` |
| Onboarding control, `--install claude --from-install` | GREEN, exit 0, real profile untouched | `onboarding.log`, `onboarding.json` |
| Leak scan of the export tree | 0 hits over 1235 files and 1 commit | `leak_refs_tree.out` |
| Exposure scan of the export | 0 findings, 0 stale allowlist rows | `exposure.json`, `exposure.out` |
| Public identity | README names only the public repository; copyright and author read as required | "Identity" below |

## 1. Build and determinism

Command, from a clean detached worktree of the commit, the documented gesture:
`python3 tools/build_public_tree.py <commit> --out <dir> --patterns <owner patterns file> --repo <source repository> --allow-public-source --report-dir <dir>`.
`--allow-public-source` is needed because the source repository is public (both stderr files carry the
builder's WARNING line, `build1.stderr`, `build2.stderr`).

| | Build 1 | Build 2 |
|-|---------|---------|
| verdict | VERIFIED | VERIFIED |
| tree | `269fb8b53b6a67322bcc1d97eb745a8b42a50e25` | same |
| export commit | `89618a74697f3d8556b6a4842b44eb778d8612b0` | same |
| files | 1235 | 1235 |
| scans line | `builder=0 leak_refs=0/0 exposure=0/0+0 leak_scan=0` | same |
| review level (requested / reached) | pr-merged / pr-merged | same |
| report total time | 39.356 s | 38.128 s |

Sources: `build1.stdout`, `build2.stdout`, `filecount1.txt`, `filecount2.txt`, `report1/`, `report2/`.

## 2. The exported suite (plain whole-suite run, not the CI `test` job)

**Correction of fact.** An earlier wording of this section called these "the CI `test` job commands". That
was wrong. The pytest command actually run is the plain whole-suite command `python -m pytest tests/ -rs -q`:
serial, no `-n auto`, no `$SLOW` ignore list (so the six slow files, including the onboarding-control test,
were included), no `--durations`. The `ci.yml` `test` job (unchanged in these respects from this commit to
today, only its timeout differs) runs `python -m pytest tests/ $SLOW -rs -q -n auto --durations=30` after
installing pytest-xdist, preceded by the per-skill smoke runner and followed by the safety check. This run
resembles neither the `test` job nor the `full` job's command (`pytest tests/ -rs -q -n auto`). It also
ran no smoke runner and no safety check. What it did run, in a copy of the export with a fresh Python 3.12
venv holding only pytest and PyYAML, and a throwaway git identity supplied through `GIT_CONFIG_GLOBAL`
(`ci_job.sh`; exit codes `ci_exits.txt`):

- `python -m pytest tests/ -rs -q`: `1 failed, 16870 passed, 30 skipped, 9 xfailed in 5273.44s (1:27:53)` (`pytest.log`, last line).
- `python3 tools/rename_check.py`: exit 0 (`rename_check.log`).
- `python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json`: exit 0 (`write_surface.log`).
- `python3 evals/run.py`: exit 0, 1.000 over 8 fixtures, no drift (`evals.log`).
- `python3 hooks/decision_gate.py validate .` and `check .`: exit 0 both, but each prints that the gate is off
  because the export has no `.sdlc/decisions.json` (`decision_validate.log`, `decision_check.log`). That is a
  vacuous pass, not a checked one.
- `python -m pytest tests/test_skill_frontmatter_yaml.py -q`: `1 passed` (`frontmatter.log`).

The five pinned skips from the export transform (`EXPORT-SKIP`, `pytest.log`): `tests/test_b6_466.py`,
`tests/test_goal_state_disposition.py`, `tests/test_liveness_disposition.py`,
`tests/test_no_private_sdlc_citations.py`, `tests/test_worktree_disposition.py`. The other 25 skips are
the ordinary ones (no `codex` on PATH, real Windows only, Linux-only behaviour, a frozen commit absent from
a shallow clone, the predecessor clone variable unset, and similar).

### The failure

`tests/test_no_skip_hooks.py::test_the_guard_tests_carry_no_skip_marker_call_or_early_return` fails in the
exported tree. Run alone, on a copy of the export, it fails again (`1 failed, 2 passed`,
`alone-export.log`), and on the source commit it passes (`3 passed`, run in the source worktree; not kept as a log). It is not a timing flake.
Cause, from the assertion text: the export transform makes three of the five guard tests skip by a call to
`skip()` inside the test body (`test_b6_466.py`, `test_goal_state_disposition.py`,
`test_no_private_sdlc_citations.py`), and this guard test accepts the decorator form of the export skip
(`EXPORT_SKIPPED` plus `EXPORT_DECORATOR`) but flags a `skip()` call. So the exported suite is NOT clean:
the expectation "0 failed" does not hold on this commit. This run did not change the code; it needs its own goal.

## 3. Planted-FAKE controls

A runtime-built fake: the script reads the two owner patterns from the private file and plants those
strings (its source, `controls/run_controls.py`, contains none). Each case is a scratch CLONE of the
source worktree with one planted file committed locally, built with `--review-level clean` and the same
`--repo`. The check is that the planted text appears in no stdout, stderr or report file.

| Plant | Exit | Verdict | Scans line | Planted text in any output |
|-------|------|---------|------------|----------------------------|
| in an `.md` | 1 | REJECTED | `leak_refs=1/1 exposure=1/1+0` | no |
| in a `.py` | 1 | REJECTED | `leak_refs=1/1 exposure=1/1+0` | no |
| in an extensionless file | 1 | REJECTED | `leak_refs=1/1 exposure=1/1+0` | no |
| in a path (file name) | 1 | REJECTED | `leak_refs=1/1 exposure=0/0+0` | no |
| wrapped: the hyphen followed by a line break | 0 | **VERIFIED (not caught)** | all 0 | no |
| wrapped: the slash followed by a line break and a quote marker | 0 | **VERIFIED (not caught)** | all 0 | no |
| baseline, no plant | 0 | VERIFIED | all 0 | no |

The baseline shows the REJECTED rows are caused by the plant and not by the scratch setup. An earlier
attempt of mine built the clone without `--repo` and was refused for that reason (`source-slug-unknown`,
exit 2); that is not a control and was redone (`superseded-7360ea44/` holds the same shape on the previous commit).

The two wrapped forms are a real gap: the private patterns are matched per line, so a name split across a
line break passes, and the report's `not_covered` list does not say so. As a measured bound on the real
tree, a separate whitespace- and quote-tolerant search of all 1235 exported paths and files (the pattern
characters separated by any run of whitespace, `>` or `#`) found 0 matches (`wrapped-scan.txt`). That search
is a one-off script and is not part of the builder.

## 4. Onboarding control

`python3 tools/onboarding_control.py --install claude --from-install`, run in a copy of the export with
the Python 3.12 venv first on PATH, no model and no API key (`onboarding.log`):
`readme-usage` GREEN (1.202 s), `local` GREEN (30.169 s), `github` GREEN (24.245 s), `local/no-command`
GREEN (29.937 s), `github/no-command` GREEN (22.957 s), `github/process` GREEN (26.1 s),
`install claude: ok`, `real profile untouched: True`; exit 0, 2:18.57 total wall. The installed copy is
a LOCAL install from the export, not a GitHub marketplace add.

## 5. Leaks and exposure

- `python3 tools/leak_refs.py tree --patterns <owner patterns file>` in the export: 0 hits, 1235 files,
  1 commit (`leak_refs_tree.out`).
- `python3 tools/readiness/exposure_scan.py tracked . --json <outside path> --patterns <owner patterns file>`:
  exit 0, 0 findings, no stale allowlist rows, nothing skipped as binary or oversize (`exposure.json`).
  Its only count is 6266 legacy issue references (`#N` forms).
- No file contains the operator's home user name. Absolute home paths are placeholders only (`/Users/alice`,
  `/Users/bob`, `/Users/x`, `/Users/you`, `/home/u` and similar in tests and docs).
- The export commit's author and committer are `sigma-public-snapshot`.
- Not hidden by these scans: the owner's public GitHub handle appears in five tracked files
  (`.github/CODEOWNERS`, `docs/launch/definition.md`, `docs/launch/definition.json`,
  `docs/launch/evidence/330-control.md`, `tests/test_readiness_decide.py`). It is an owner decision whether
  that is intended; it is not a private slug.

## Identity

- README: every repository slug in it is `Agrim-Intelligence/sigmaloop` (6 mentions) plus the bare
  organisation URL.
- `.claude-plugin/plugin.json` author and `.claude-plugin/marketplace.json` owner: `Agrim Intelligence Private Limited`.
- `LICENSE` line 3 and `NOTICE` line 2: `Copyright (c) 2026 Agrim Intelligence Private Limited`.
- Version in both manifests: `1.0.11`.

## Reproduce

Build twice as in section 1, then `ci_job.sh`-equivalent commands of section 2 in a copy of the output. The
suite took 5273 s (88 minutes) here, so budget two hours.
