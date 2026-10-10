# #881 controls: every guard broken once, seen red, restored green

Phase P5 IMPLEMENT. Goal worktree `.sdlc/work/881` (left DIRTY; nothing staged or committed there).

## How these were run (read first)
- **Files were tracked at that point.** The repo guards read `git ls-files`, so untracked files are invisible and a guard run in the goal worktree would be vacuously green. Every guard run below was done in a SCRATCH git clone of the worktree HEAD (5f25b99) with the changed files copied in and COMMITTED THERE (`scratchpad/g881`, `scratchpad/final881`), never in the goal worktree.
- **Gesture.** Every control runs the documented gesture `python3 evals/golden/verify.py` (no flags, the README line copied verbatim) with `cwd` at a planted root that holds a copy of verify.py, `tools/readiness/bench_tasks.py`, the bench harness and task `t1`. The same break is also run through the pytest node that guards it. Note the gesture `python3` here is the system Python 3.9.6; the pytest nodes run under the venv312 Python 3.12.
- Method per guard: apply the break to the scratch copy of verify.py, run the planted tree and the test (red), restore, run both again (green). The driver is a scratch script (not shipped).

## Deliberate deviations from the acceptance wording
1. **Zero write-surface entries, by design.** The acceptance lists "write-surface entries"; verify.py uses only `tempfile.TemporaryDirectory`, `shutil.copytree` and `subprocess` (no `rmtree`, `os.remove`, `write_text`, `mkdir`), so `python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json` is green with `docs/launch/write-surface.json` untouched. Control (below): adding `shutil.rmtree` to verify.py turns the ratchet red (`new write site evals/golden/verify.py:... fs-rmtree`), proving the scan covers the file.
2. **No documented-gestures allowlist row; a plain doc line instead.** `tests/fixtures/documented_gestures_allowlist.json` stays `[]`. `evals/README.md` is not a scanned source, so one plain line carrying `python3 evals/golden/verify.py` was added to `docs/agent-rules-detail.md`; the guard resolves and `--help`-runs it. Control (below): pointing that line at `verifyx.py` turns `tests/test_documented_gestures.py` red (`missing script verifyx.py`).
3. `docs/enforcement.md` is generated and untouched; `--json OUT` was skipped (would cost a write-surface entry).
4. Beyond the plan's list, `symlink` is red anywhere under `repo/`, `reference/`, `hidden/` (refinement 3), and `verify.json` must be a `python -m pytest` argv (refinement 5).

## Controls: guard | broken by | red evidence | restored
### origin check skipped (missing)
- broken by: `red("origin", "missing")` -> `pass`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (3.2s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_missing_origin_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.60s); gesture exit 1: 'RED t1 origin: missing\ngolden: 1 task(s), 1 red'

### origin check skipped (bad value)
- broken by: `elif not (isinstance(origin, str) and ORIGIN.fullmatch(origin)):` -> `elif False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.8s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_bad_origin_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (5 passed in 2.46s); gesture exit 1: "RED t1 origin: 'other' is not planned or issue:<n>\ngolden: 1 task(s), 1 red"

### hash compare skipped
- broken by: `elif actual[rel] != declared[rel]:` -> `elif False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (3.2s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_hidden_hash_mismatch_names_the_file` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.55s); gesture exit 1: 'RED t1 hidden-sha256: files/test_calc.py: hash mismatch\ngolden: 1 task(s), 1 red'

### hidden-on-start run skipped (accepts exit 0)
- broken by: `attempt("hidden-on-start", d / "repo", bundle, 1, "hidden tests must fail on the start tree")` -> `pass`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (1.4s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_hidden_tests_passing_on_the_start_tree_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.49s); gesture exit 1: 'RED t1 hidden-on-start: hidden tests must fail on the start tree (exit 0): 1 passed in 0.00s\ngolden: 1 task(s), 1 red'

### hidden-on-reference run skipped (accepts non-zero)
- broken by: `attempt("hidden-on-reference", d / "reference", bundle, 0, "hidden tests must pass on the reference tree")` -> `pass`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (1.6s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_hidden_tests_failing_on_the_reference_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.56s); gesture exit 1: 'RED t1 hidden-on-reference: hidden tests must pass on the reference tree (exit 1): 1 failed in 0.03s\ngolden: 1 task(s), 1 red'

### reference diff skipped
- broken by: `_reference_diff(d, red)` -> `pass`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.8s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_reference_change_outside_allowed_paths_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.50s); gesture exit 1: 'RED t1 reference-diff: extra.py added but not in allowed_paths.json\ngolden: 1 task(s), 1 red'

### reference diff ignores deleted paths
- broken by: `if start.get(rel) != reference.get(rel) and not _allowed(rel, allowed):` -> `if start.get(rel) != reference.get(rel) and rel in reference and not _allowed(rel, allowed):`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.9s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_reference_deleting_a_file_outside_allowed_paths_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.52s); gesture exit 1: 'RED t1 reference-diff: keep.py deleted but not in allowed_paths.json\ngolden: 1 task(s), 1 red'

### naive check skipped
- broken by: `if (d / "hidden" / "naive").is_dir():` -> `if False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.8s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_naive_passing_the_hidden_tests_is_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.60s); gesture exit 1: 'RED t1 naive: hidden tests pass on the naive patch (exit 0): 1 passed in 0.00s\ngolden: 1 task(s), 1 red'

### rubric object check skipped
- broken by: `if (d / "rubric.json").is_file() and not isinstance(_json(d / "rubric.json"), dict):` -> `if False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.5s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_rubric_must_be_a_json_object` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.46s); gesture exit 1: 'RED t1 rubric: rubric.json must be a JSON object\ngolden: 1 task(s), 1 red'

### visible_command validation skipped
- broken by: `if "visible_command" in task and not (_strings(command) and command and command[0] in ("python", "python3")):` -> `if False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.6s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_visible_command_is_validated` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (4 passed in 1.62s); gesture exit 1: 'RED t1 visible_command: must be a non-empty list of strings starting with python or python3 (validated, never run)\ngolden: 1 task(s), 1 red'

### whole hidden/ copied incl. naive/
- broken by: `if os.fspath(directory) == os.fspath(hidden) and "naive" in names:` -> `if False:`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 1 (4.3s): 'RED t1 hidden-on-reference: hidden tests must pass on the reference tree (exit 1): 1 failed, 1 passed in 0.03s\ngolden: 1 task(s), 1 red'
- test `test_naive_never_reaches_the_scored_tree` BROKEN: exit 1: E       AssertionError: RED t1 hidden-on-reference: hidden tests must pass on the reference tree (exit 1): 1 failed, 1 passed in 0.01s | E         golden: 1 task(s), 1 red | E
- restored: test exit 0 (1 passed in 0.68s); gesture exit 0: 'ok t1\ngolden: 1 task(s), 0 red'

### symlink check skipped
- broken by: `red("symlink", f"{sub}/{rel}")` -> `pass`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (0.1s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_symlinks_are_red` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (1 passed in 0.29s); gesture exit 1: 'RED t1 symlink: repo/link.py\ngolden: 1 task(s), 1 red'

### verify.json -p no:cacheprovider requirement dropped
- broken by: `("-p", "no:cacheprovider") in pairs and` -> ``
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.3s): 'ok t1\ngolden: 1 task(s), 0 red'
- test `test_malformed_verify_command_is_red_verify_json` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (7 passed in 0.60s); gesture exit 1: 'RED t1 verify-json: command must be a python -m pytest argv with -p no:cacheprovider and .sigma-hidden/files\ngolden: 1 task(s), 1 red'

### missing contract-file pre-parse skipped
- broken by: `for rel in ("rubric.json", "allowed_paths.json", "hidden/verify.json"):` -> `for rel in ():`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 1 (0.1s): "bench_tasks: unreadable <tmp> [Errno 2] No such file or directory: '<tmp>'"
- test `test_a_missing_contract_file_is_red_not_a_traceback` BROKEN: exit 1: E       AssertionError: ok t1 | E         golden: 1 task(s), 0 red | E
- restored: test exit 0 (3 passed in 0.32s); gesture exit 1: 'RED t1 hidden/verify.json: missing\ngolden: 1 task(s), 1 red'

### dir without task.json skipped
- broken by: `return [("task-json", "missing")]` -> `return []`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 0 (2.8s): 'ok t1\nok t2\ngolden: 2 task(s), 0 red'
- test `test_a_directory_without_task_json_is_red_and_not_zero_tasks` BROKEN: exit 1: E       AssertionError: ok t1 | E         ok t2 | E         golden: 2 task(s), 0 red
- restored: test exit 0 (1 passed in 0.48s); gesture exit 1: 'ok t1\nRED t2 task-json: missing\ngolden: 2 task(s), 1 red'

### own timeout not passed to the run (timeout=None, line `code, tail = bt._hidden_run(...)` in _runs)
- broken by: `bt._hidden_run(tree, bundle, task, sys.executable, timeout, env)` -> `bt._hidden_run(tree, bundle, task, sys.executable, None, env)`
- gesture `python3 evals/golden/verify.py` on planted tree, BROKEN: exit 1 (27.6s): 'RED t1 hidden-on-start: hidden tests must fail on the start tree (exit 0): 1 passed in 12.01s\ngolden: 1 task(s), 1 red'
- test `test_a_hung_hidden_test_is_killed_and_reported` BROKEN: exit 1: E           subprocess.TimeoutExpired: Command '['<venv>/bin/python', 'evals/golden/verify.py']' timed out after 120 seconds | FAILED tests/test_golden_verify.py::test_a_hung_hidden_test_is_killed_and_reported | 1 failed in 120.18s (0:02:00)
- restored: test exit 0 (1 passed in 4.21s); gesture exit 1: 'RED t1 hidden-on-start: timeout after 2s (killed)\nRED t1 hidden-on-reference: timeout after 2s (killed)\ngolden: 1 task(s), 1 red'


### zero-task line removed
- broken by: `if not dirs: print(f"0 golden tasks found ...")` -> `if False:` (the SECOND `if not dirs:`; a first attempt broke the `--only` branch instead and stayed green, so the target was corrected)
- gesture `python3 evals/golden/verify.py` on a planted root with no task dirs, BROKEN: exit 0: `golden: 0 task(s), 0 red` (no "nothing verified" line)
- test `test_zero_tasks_is_loud_and_not_ok` BROKEN: exit 1: `assert '0 golden tasks found' in 'golden: 0 task(s), 0 red'`
- restored: test passes; gesture exit 0: `0 golden tasks found under <root> (nothing verified)`

## Registration, pin and structural controls (scratch clone `g881`, files committed there)
- **FIXTURE_ROOTS** | planted a throwaway hidden test file under the golden fixture tree importing `pytest`; removed `("evals", "golden")` from `tests/test_third_party_imports.py` | `python -m pytest tests/test_third_party_imports.py`: `1 failed, 10 passed`; `E evals/golden/t1/hidden/files/test_x.py:1: third-party import `pytest` is not in the allowlist` | with the entry: `11 passed`.
- **Pin: isolated_env** | renamed `def isolated_env(` to `isolated_env_RENAMED(` in scratch `evals/bench/arms/common.py` | `test_clean_env_chain_reaches_arms_common_isolated_env`: `AttributeError: module 'arms.common' has no attribute 'isolated_env'` | restored: 1 passed.
- **Pin: `_run`** | renamed `def _run(` in scratch `tools/readiness/bench_tasks.py` | `test_bench_tasks_private_names_verify_uses_exist`: `bench_tasks._run is gone; evals/golden/verify.py depends on it` | restored: 1 passed.
- **Pin: verify.py uses only pinned names** | `bt.file_sha256(path)` -> `bt.bundle_files(path)` | `test_verify_uses_only_pinned_names`: `unpinned bench_tasks names used by verify.py: ['bundle_files']` | restored: 1 passed.
- **CI step** | removed the `golden tasks verify` step from scratch ci.yml | `assert 'golden tasks verify' in '      - name: decision gate'`; changing its `if:` to `'false'` is also red (`assert "if: ${{ env.FULL == 'true' }}" in [...]`) | restored: 1 passed.
- **Documented gesture line** | `docs/agent-rules-detail.md` gesture pointed at `verifyx.py` | `tests/test_documented_gestures.py`: `docs/agent-rules-detail.md:192: missing script verifyx.py` (1 failed, 7 passed) | restored: 8 passed.
- **Write-surface ratchet** | appended a function calling `shutil.rmtree` to scratch verify.py | `python3 tools/readiness/write_surface.py check . docs/launch/write-surface.json`: `new write site evals/golden/verify.py:_x fs-rmtree -- add it to docs/launch/write-surface.json` | restored: exit 0, no output.
- **Naive isolation (plan step 5)** is the "whole hidden/ copied incl. naive/" control above: with the filter off, the hidden `test_no_naive.py` fails on the reference run (`hidden-on-reference ... 1 failed, 1 passed`).
- **Windows refusal**: `test_windows_is_refused_loudly` runs `main` in-process with `sys.platform` patched; exit 2. Not run on a real Windows host.

## Findings worth keeping
- **Pre-parse is load-bearing**: with the missing-file check removed, the gesture exits 1 printing `bench_tasks: unreadable .../bundle/verify.json` and no `RED` line, i.e. bench's `SystemExit(str)` masquerades as a red; the test goes red on the missing task id and property (control above).
- The first attempt at the zero-task control broke the wrong `if not dirs:` (the `--only` branch) and stayed green; corrected to the second occurrence. A control that cannot fail is decoration, so this is recorded.
- The timeout control, with `timeout=None` passed to `_hidden_run` (line in `_runs`), makes a 12 s sleeping hidden test run to completion (27.6 s, no `timeout` red; the test's own 3600 s sleep hit the 120 s subprocess limit and left orphaned pytest children, which were killed by hand afterwards). Restored: both runs killed at 2 s, `RED t1 hidden-on-start: timeout after 2s (killed)`.

## verify.py wall time (measured)
Method: a scratch root (not tracked) with the real verify.py, bench_tasks.py, bench harness and ONE planted task with a `hidden/naive/` patch (3 pytest runs: start, reference, naive); `/usr/bin/time -p python3 evals/golden/verify.py`, five runs, macOS, nothing else running for the second batch.
- Python 3.12 venv (`~/.sigma-venv312/bin/python`): 0.47, 0.45, 0.46, 0.65, 0.63 s -> min 0.45, median 0.47, max 0.65.
- system `python3` 3.9.6 (slower pytest start-up): 3.94, 3.42, 3.88, 3.77, 3.77 s -> min 3.42, median 3.77, max 3.94. (A first batch run while the control driver was busy: 3.91 to 4.53 s.)
- One task only. Slices 2-4 will change it; 10x and 100x are not measured. Runs are sequential, which is the named ceiling.

## Fix round after code review (symlinked roots, crash path, dot-dirs)
- **Symlinked roots/contract files** | in a scratch copy, disabled the `ROOT_LINKS` check (`for rel in ():`); planted `repo` as a symlink to a real tree elsewhere | `python3 evals/golden/verify.py` printed `ok t1`, exit 0 (`AssertionError: ok t1 / assert 0 == 1`): the symlinked root passed | restored: `RED t1 symlink: repo`, exit 1; 12 passed across repo, reference, hidden, hidden/files, hidden/naive, hidden/verify.json, task.json, rubric.json, allowed_paths.json and the task directory.
- **Helper crash path** | in a scratch copy, narrowed the `_runs` guard to `except ZeroDivisionError`; planted `repo/.sigma-hidden/x` | the gesture died with `Traceback ...` and exit 1 and no RED line (`assert 1 == 2`) | restored: exit 2, `verify.py: unreadable input: task t1: could not run hidden tests: ...`, no Traceback, no RED.
