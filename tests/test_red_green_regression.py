"""#2084 characterization: what the pytest-only planned-test proof did BEFORE runner bullets, and must still do.

Green on main by construction, so it is not in the plan's `## Tests` (a green-on-main test can never be
credited red); it is a Definition-of-done file. Each test has its own control (see the plan's Controls).
"""
import importlib.util
import json
import os
import pathlib
import subprocess

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'
APPROVAL_ENV = 'SIGMA_TRUSTED_VERIFY_SHA256'
NODE = 'tests/test_x.py::test_a'


def mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)


def make(tmp_path, grant=True, bullet=NODE):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    if grant:
        git(root, 'config', '--local', 'sigma.allowRepositoryShellCommands', 'true')
    (root / 'tests').mkdir()
    (root / 'tests/test_x.py').write_text('def test_a():\n    assert False\n')
    sdlc = root / '.sdlc'
    (sdlc / 'plans').mkdir(parents=True)
    (sdlc / 'config.json').write_text(json.dumps({'verify': {'command': 'true'}, 'resource': {'mode': 'off'}}))
    plan = sdlc / 'plans/84.md'
    plan.write_text(f'# Plan\n\n## Tests\n- `{bullet}`\n')
    return root, sdlc, plan


def cli_red(root, approval=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != APPROVAL_ENV}
    if approval:
        env[APPROVAL_ENV] = approval
    return subprocess.run(['python3', str(S / 'loop.py'), 'red', '.sdlc', '84'], cwd=root, env=env,
                          capture_output=True, text=True, timeout=300)


def journal(sdlc):
    return mod('witness').witnesses(sdlc, '84')


def test_pytest_selector_with_colon_in_param_not_a_runner(tmp_path):
    root, sdlc, plan = make(tmp_path, bullet='tests/test_x.py::test_a[a:b]')
    assert mod('red_green').selectors(plan) == ['tests/test_x.py::test_a[a:b]']


def test_existing_pytest_messages_unchanged(tmp_path):
    root, sdlc, plan = make(tmp_path)
    rg = mod('red_green')
    for text, message in (('## Tests\n- tests/test_x.py\n', '## Tests requires one backticked pytest selector per bullet'),
                          ('## Tests\n- `/abs/test_x.py`\n', 'unsafe pytest selector: /abs/test_x.py'),
                          ('## Tests\n- `tests/test_x.py::a b`\n', 'unsafe pytest selector: tests/test_x.py::a b'),
                          ('## Tests\n', '## Tests requires 1–25 pytest selectors; use --no-tests, or the line '
                                         '`none: <reason>` for a docs-only change')):
        plan.write_text(text)
        try:
            rg.selectors(plan)
        except ValueError as exc:
            assert str(exc) == message, str(exc)
        else:
            raise AssertionError('expected a ValueError for ' + text)


def test_pytest_only_observe_unchanged(tmp_path):
    root, sdlc, plan = make(tmp_path)
    (root / 'tests/test_x.py').write_text('def test_a():\n    assert True\n')

    def run(cwd, argv):
        if '--collect-only' in argv:
            return subprocess.CompletedProcess(argv, 0, NODE + '\n', '')
        return subprocess.CompletedProcess(argv, 0, '=== short test summary info ===\nPASSED ' + NODE + '\n', '')
    got = mod('red_green').observe(sdlc, '84', root, plan, run=run)
    assert set(got) == {'provenance', 'plan_sha256', 'selectors', 'nodes', 'started_at', 'completed_at', 'passed',
                        'exit', 'error'}, sorted(got)
    assert got['provenance'] == 'planned-pytest-v1' and got['passed'] is True and got['exit'] == 0


def test_pytest_only_goal_still_runs_under_environment_approval(tmp_path):
    root, sdlc, plan = make(tmp_path, grant=False)
    git(root, 'remote', 'add', 'origin', 'https://github.com/Owner/Repo.git')
    proc = cli_red(root, approval=mod('shell_policy').approval_value('owner/repo', 'true'))
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert [r['provenance'] for r in journal(sdlc)] == ['planned-pytest-v1']


def test_flake_pytest_branch_unchanged_when_no_runners(tmp_path, monkeypatch):
    root, sdlc, plan = make(tmp_path)
    lp = mod('loop')
    seen = []
    monkeypatch.setattr(lp.flake_check, 'check',
                        lambda r, files, **kw: seen.append((files, kw['node_ids'])) or {'verdict': 'verified'})
    monkeypatch.setattr(lp, '_planned_scope', lambda *a: ([NODE], [NODE]))
    assert lp._flake_verdict(sdlc, '84', root, True)['verdict'] == 'verified'
    assert seen == [(['tests/test_x.py'], [NODE])]


def test_runnerless_config_changes_nothing(tmp_path):
    root, sdlc, plan = make(tmp_path)
    proc = cli_red(root)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    rows = journal(sdlc)
    assert [(r['test'], r['provenance'], r['kind']) for r in rows] == [(NODE, 'planned-pytest-v1', 'assertion')]
