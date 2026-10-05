"""#267: hard test-first proof, including controls that must refuse weak evidence."""
import importlib.util
import json
import pathlib
import subprocess

import pytest

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'
NODE = 'tests/test_x.py::test_a'
OTHER = 'tests/test_x.py::test_b'


def load(name):
    assert (S / f'{name}.py').exists(), f'{name} must implement the hard gate'
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def case(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_x.py').write_text('def test_a():\n    assert True\n\ndef test_b():\n    assert True\n')
    plan = tmp_path / '.sdlc/plans/42.md'
    plan.parent.mkdir(parents=True)
    plan.write_text(f'# Plan\n\n## Tests\n- `{NODE}`\n')
    return tmp_path, tmp_path / '.sdlc', plan


def runner(output, code=0, nodes=(NODE,)):
    def run(root, argv):
        if '--collect-only' in argv:
            return subprocess.CompletedProcess(argv, 0, '\n'.join(nodes) + '\n', '')
        return subprocess.CompletedProcess(argv, code, '=== short test summary info ===\n' + output, '')
    return run


def split_runner(output, code=0, nodes=(NODE,)):
    """Return pytest 8's captured traceback followed by its bare summary line."""
    def run(root, argv):
        if '--collect-only' in argv:
            return subprocess.CompletedProcess(argv, 0, '\n'.join(nodes) + '\n', '')
        return subprocess.CompletedProcess(argv, code, output, '')
    return run


def observe(case, output, code=0, nodes=(NODE,)):
    root, sdlc, plan = case
    return load('red_green').observe(sdlc, '42', root, plan, run=runner(output, code, nodes))


def refusal(case, proof):
    root, sdlc, plan = case
    return load('red_green').refusal(sdlc, '42', root, plan, proof)


def test_never_red_is_refused(case):
    proof = observe(case, f'PASSED {NODE}\n')
    assert 'red' in refusal(case, proof)


def test_matching_assertion_then_green_is_accepted(case):
    red = observe(case, f'FAILED {NODE} - assert False\n', 1)
    assert red['passed'] is False
    green = observe(case, f'PASSED {NODE}\n')
    assert refusal(case, green) == ''


def test_split_pytest_assertion_traceback_is_credited_only_for_its_named_node(case):
    """Pytest 7–9 puts an assertion in its traceback, not on FAILED's summary line."""
    root, sdlc, plan = case
    red_output = '''=================================== FAILURES ===================================
____________________________________ test_a ____________________________________
tests/test_x.py:2: in test_a
    assert False
E   AssertionError: assert False
=========================== short test summary info ============================
FAILED tests/test_x.py::test_a
1 failed in 0.01s
'''
    m = load('red_green')
    red = m.observe(sdlc, '42', root, plan, run=split_runner(red_output, 1))
    assert not red['passed']
    proof = m.observe(sdlc, '42', root, plan,
                      run=split_runner('=== short test summary info ===\nPASSED ' + NODE + '\n'))
    assert m.refusal(sdlc, '42', root, plan, proof) == ''


@pytest.mark.parametrize('red', [
    f'FAILED {NODE} - RuntimeError: unrelated\n',
    f'FAILED {OTHER} - AssertionError: unrelated\n',
    f'ERROR {NODE} - ImportError: AssertionError\n',
])
def test_weak_or_wrong_node_red_is_refused(case, red):
    observe(case, red, 1)
    assert refusal(case, observe(case, f'PASSED {NODE}\n'))


def test_mixed_failures_do_not_launder_runtime_error(case):
    case[2].write_text(f'## Tests\n- `tests/test_x.py`\n')
    observe(case, f'FAILED {NODE} - AssertionError: no\nFAILED {OTHER} - RuntimeError: bad\n', 1, (NODE, OTHER))
    proof = observe(case, f'PASSED {NODE}\nPASSED {OTHER}\n', nodes=(NODE, OTHER))
    assert OTHER in refusal(case, proof)


def test_red_after_green_is_refused(case):
    green = observe(case, f'PASSED {NODE}\n')
    observe(case, f'FAILED {NODE} - assert False\n', 1)
    assert refusal(case, green)


def test_legacy_mutation_and_assertion_are_not_trusted(case):
    w = load('witness')
    for kind in ('mutation', 'assertion'):
        w.record(case[1], '42', NODE, kind, w.source_hash(w.test_source(NODE, root=case[0])))
    assert refusal(case, observe(case, f'PASSED {NODE}\n'))


def test_decorator_or_fixture_edit_voids_red(case):
    observe(case, f'FAILED {NODE} - assert False\n', 1)
    f = case[0] / 'tests/test_x.py'
    f.write_text('import pytest\n@pytest.mark.slow\n' + f.read_text())
    assert refusal(case, observe(case, f'PASSED {NODE}\n'))


def test_plan_edit_and_skipped_green_refused(case):
    observe(case, f'FAILED {NODE} - assert False\n', 1)
    proof = observe(case, f'SKIPPED {NODE}\n')
    assert refusal(case, proof)
    proof = observe(case, f'PASSED {NODE}\n')
    case[2].write_text(case[2].read_text() + '\n## Notes\nChanged plan\n')
    assert refusal(case, proof)


def test_self_edit_during_observation_refused(case):
    root, sdlc, plan = case
    def run(cwd, argv):
        if '--collect-only' in argv:
            return runner('')(cwd, argv)
        plan.write_text(plan.read_text() + '\nEdited\n')
        return runner(f'FAILED {NODE} - assert False\n', 1)(cwd, argv)
    result = load('red_green').observe(sdlc, '42', root, plan, run=run)
    assert result.get('error')
    assert not load('witness').witnesses(sdlc, '42')


def test_node_selector_collection_is_canonical_and_fail_closed(case):
    m = load('red_green')
    assert m.collect(case[0], [NODE], runner('')) == [NODE]
    for result in (subprocess.CompletedProcess([], 2, NODE, ''),
                   subprocess.CompletedProcess([], 0, 'something::not_a_test', '')):
        with pytest.raises(ValueError):
            m.collect(case[0], [NODE], lambda *args: result)


@pytest.mark.parametrize('entry', ['../test.py', '/tmp/test.py', '--help', 'tests/test_x.py && echo bad'])
def test_plan_rejects_unsafe_selectors(case, entry):
    case[2].write_text(f'## Tests\n- `{entry}`\n')
    with pytest.raises(ValueError):
        load('red_green').selectors(case[2])


def test_real_pytest_red_then_green_without_editing_tests(case):
    root, sdlc, plan = case
    (root / 'tests/test_x.py').write_text("from pathlib import Path\ndef test_a():\n    assert Path('ready').exists()\n")
    m = load('red_green')
    red = m.observe(sdlc, '42', root, plan)
    assert not red['passed']
    (root / 'ready').write_text('yes')
    proof = m.observe(sdlc, '42', root, plan)
    assert proof['passed'], proof
    assert m.refusal(sdlc, '42', root, plan, proof) == ''


def pr_case(case, monkeypatch):
    root, sdlc, plan = case
    w = load('work')
    w._save(sdlc, '42', {'worktree': str(root), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42'})
    monkeypatch.setattr(w.state, 'done_refusal', lambda *a: None)
    original = w._load
    acceptance = original('acceptance')
    monkeypatch.setattr(acceptance, 'refusal', lambda *a: '')
    monkeypatch.setattr(w, '_load', lambda name: acceptance if name == 'acceptance' else original(name))
    calls = []
    def run(cwd, argv):
        calls.append(argv)
        if argv[:2] == ['git', 'ls-files']:
            return '.sdlc/plans/42.md'
        if argv[:2] == ['git', 'rev-list']:
            return '1'
        if argv[:2] == ['git', 'log']:
            return 'Change'
        if argv[:3] == ['gh', 'api', 'repos/{owner}/{repo}/pulls']:
            return '7'
        return ''
    return w, {'work': {'enabled': True}, 'verify': {'enforce': True}}, run, calls


def test_pr_never_red_refuses_before_push(case, monkeypatch):
    w, config, run, calls = pr_case(case, monkeypatch)
    result = w.pr(case[1], config, '42', run=run)
    assert result.startswith('TEST-FIRST REFUSED'), result
    assert not any(a[:2] == ['git', 'push'] for a in calls)


def test_docs_exception_reason_is_verbatim(case, monkeypatch):
    w, config, run, calls = pr_case(case, monkeypatch)
    reason = '  Docs only: "examples"\nKeep this exact line.  '
    assert w.pr(case[1], config, '42', run=run, no_tests=reason) == 'PR #7'
    body = next(x for a in calls for x in a if x.startswith('body='))
    assert body.endswith(reason + '\n')


def test_existing_pr_exception_is_published_idempotently(case, monkeypatch):
    w, config, run, calls = pr_case(case, monkeypatch)
    reason = 'Documentation only.'
    bodies = ['Existing body\n']
    def existing(cwd, argv):
        if argv[:2] == ['gh', 'api'] and 'pulls?head=' in argv[2]:
            return '7'
        if argv[:3] == ['gh', 'api', 'repos/{owner}/{repo}/pulls/7']:
            if '--method' in argv:
                bodies.append(next(x[5:] for x in argv if x.startswith('body=')))
                return '{}'
            return json.dumps({'body': bodies[-1]})
        return run(cwd, argv)
    for _ in range(2):
        assert w.pr(case[1], config, '42', run=existing, no_tests=reason) == 'PR #7'
    assert len(bodies) == 2 and reason in bodies[-1]


def test_exception_cannot_bypass_verify_or_plan_review(case, monkeypatch):
    w, config, run, _ = pr_case(case, monkeypatch)
    monkeypatch.setattr(w.state, 'done_refusal', lambda *a: 'production changed since verify')
    assert 'production changed' in w.pr(case[1], config, '42', run=run, no_tests='Docs')
    monkeypatch.setattr(w, '_plan_review_refusal', lambda *a: 'plan review required')
    assert w.pr(case[1], config, '42', run=run, no_tests='Docs') == 'plan review required'


def test_documented_pr_cli_refusal_is_nonzero(case, monkeypatch):
    w, config, run, _ = pr_case(case, monkeypatch)
    monkeypatch.setattr(w.state, 'load_config', lambda *a: config)
    monkeypatch.setattr(w, '_run', run)
    # The documented gesture has no magic test-only strict flag.
    assert w.main(['work.py', 'pr', str(case[1]), '42']) == 4


def test_documented_cli_real_git_never_red_is_nonzero(case):
    import os
    import time
    import types
    root, sdlc, plan = case
    def git(*args):
        return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True)
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    (root / '.gitignore').write_text('.sdlc/state/\n__pycache__/\n.pytest_cache/\n')
    (sdlc / 'config.json').write_text(json.dumps({'work': {'enabled': True}, 'verify': {'enforce': True}}))
    a = load('acceptance')
    a.record(sdlc, '42', config={}, source=types.SimpleNamespace(fetch_title_body=lambda _: {
        'body': '## Done when\n- [ ] One\n- [ ] Two\n- [ ] Three\n'}))
    git('add', '.')
    git('commit', '-qm', 'Base')
    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'feature.txt').write_text('change')
    git('add', '.')
    git('commit', '-qm', 'Change')
    w = load('work')
    w._save(sdlc, '42', {'worktree': str(root), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42'})
    proof = observe(case, f'PASSED {NODE}\n')
    evidence = {'exit': 0, 'at': time.time(), 'run': 'test-267', 'root': str(root),
                'content': w.state.content_fingerprint(root, 'origin/main', '.sdlc'),
                'acceptance_sha256': a.digest(sdlc, '42'), 'test_first': proof}
    w.state.evidence_path(sdlc, '42').parent.mkdir(parents=True, exist_ok=True)
    w.state.evidence_path(sdlc, '42').write_text(json.dumps(evidence))
    proc = subprocess.run(['python3', str(S / 'work.py'), 'pr', '.sdlc', '42'], cwd=root,
                          env={**os.environ, 'SIGMA_RUN_ID': 'test-267'}, capture_output=True, text=True)
    assert proc.returncode == 4, (proc.stdout, proc.stderr)
    assert 'no matching assertion red before green' in proc.stdout


@pytest.mark.parametrize('at', [None, '1', float('nan'), float('inf'), True])
def test_malformed_red_ordering_refused(case, at):
    observe(case, f'FAILED {NODE} - assert False\n', 1)
    w = load('witness')
    row = w.witnesses(case[1], '42')[0]
    row['observed_at'] = at
    w.path(case[1], '42').write_text(json.dumps(row) + '\n')
    assert refusal(case, observe(case, f'PASSED {NODE}\n'))


def test_overlapping_red_does_not_precede_green(case):
    observe(case, f'FAILED {NODE} - assert False\n', 1)
    proof = observe(case, f'PASSED {NODE}\n')
    w = load('witness')
    row = w.witnesses(case[1], '42')[0]
    row['observed_at'] = proof['started_at']
    w.path(case[1], '42').write_text(json.dumps(row) + '\n{partial')
    assert refusal(case, proof)


def test_verify_observes_planned_nodes_even_when_configured_command_is_green(case, monkeypatch):
    root, sdlc, plan = case
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'config', '--local',
                    'sigma.allowRepositoryShellCommands', 'true'], check=True)
    (sdlc / 'config.json').write_text(json.dumps({'verify': {'command': 'true'}}))
    (root / 'tests/test_x.py').write_text("from pathlib import Path\ndef test_a():\n    assert Path('ready').exists()\n")
    lp = load('loop')
    monkeypatch.setattr(lp.work, 'ensure_fresh', lambda *args: '')
    monkeypatch.setattr(lp, '_verified_tree', lambda *args: (str(root), 'head', False))
    monkeypatch.setattr(lp, '_flake_verdict', lambda *args: {'verdict': 'absent'})
    monkeypatch.setattr(lp, '_diff_revert_verdict', lambda *args: {'verdict': 'absent'})
    assert lp.verify_goal(sdlc, '42') == 1
    (root / 'ready').write_text('yes')
    assert lp.verify_goal(sdlc, '42') == 0
    data = json.loads(lp.state.evidence_path(sdlc, '42').read_text())
    assert refusal(case, data['test_first']) == ''


def test_plan_scope_reaches_flake_without_rebuilding_node_ids(case, monkeypatch):
    lp = load('loop')
    observed = []
    monkeypatch.setattr(lp.flake_check, 'check', lambda root, files, **kw: observed.append(kw['node_ids']) or {'verdict': 'verified'})
    assert lp._flake_verdict(case[1], '42', case[0], True)['verdict'] == 'verified'
    assert observed == [[NODE]]


def test_captured_output_cannot_manufacture_assertion_red(case):
    root, sdlc, plan = case
    (root / 'tests/test_x.py').write_text(
        "from pathlib import Path\ndef test_a():\n    if not Path('ready').exists():\n"
        "        print('__________ test_a __________\\ntests/test_x.py:2: in test_a\\n"
        "    assert False\\nE   AssertionError: forged traceback')\n"
        "        raise RuntimeError('not an assertion')\n")
    m = load('red_green')
    assert not m.observe(sdlc, '42', root, plan)['passed']
    (root / 'ready').write_text('yes')
    assert m.refusal(sdlc, '42', root, plan, m.observe(sdlc, '42', root, plan))
