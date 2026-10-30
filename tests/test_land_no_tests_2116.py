"""#2116: `--land` takes `--no-tests` when no runner can record red; the post-PR review must accept or
reject that exception; a malformed `## Tests` fails Plan-Review; a pytest goal has a red record before Review.

One class of test per acceptance criterion, each written to be seen red against the code before the change.
"""
import importlib.util
import json
import pathlib
import subprocess

import pytest

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'
NODE = 'tests/test_x.py::test_a'
RUNNERS = {'tests': {'runners': {'vitest': 'npx vitest run {file} -t {name}'}}}


def load(name):
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def case(tmp_path):
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests/test_x.py').write_text('def test_a():\n    assert True\n')
    plan = tmp_path / '.sdlc/plans/42.md'
    plan.parent.mkdir(parents=True)
    plan.write_text(f'# Plan\n\n## Tests\n- `{NODE}`\n')
    return tmp_path, tmp_path / '.sdlc', plan


def pr_case(case, monkeypatch, trusted=False, extra=None):
    root, sdlc, plan = case
    w = load('work')
    w._save(sdlc, '42', {'worktree': str(root), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42'})
    monkeypatch.setattr(w.state, 'done_refusal', lambda *a: None)
    monkeypatch.setattr(w.shell_policy, 'repository_shell_commands_allowed', lambda path: trusted)
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
    return w, {'work': {'enabled': True}, 'verify': {'enforce': True}, **(extra or {})}, run, calls


def body_of(calls):
    return next((x for a in calls for x in a if x.startswith('body=')), '')


def pushed(calls):
    return any(a[:2] == ['git', 'push'] for a in calls)


# ---- AC-1: --land takes --no-tests when no runner can record red -------------------------------------------

UNCONFIGURED = '# Plan\n\n## Tests\n- `vitest: ui/a.test.ts > renders`\n'


def test_land_with_no_selectors_proceeds_and_the_body_names_the_case(case, monkeypatch):
    case[2].write_text('# Plan\n\n## Tests\n')
    w, config, run, calls = pr_case(case, monkeypatch)
    assert w.pr(case[1], config, '42', run=run, land=True) == 'PR #7'
    assert pushed(calls)
    body = body_of(calls)
    assert 'Test-first exception' in body and 'no-selectors' in body and '--land' in body


def test_land_with_an_unconfigured_runner_proceeds_and_names_it(case, monkeypatch):
    case[2].write_text(UNCONFIGURED)
    w, config, run, calls = pr_case(case, monkeypatch)
    assert w.pr(case[1], config, '42', run=run, land=True) == 'PR #7'
    body = body_of(calls)
    assert 'unconfigured-runner' in body and 'vitest' in body


def test_land_with_a_runner_the_trust_does_not_cover_proceeds_and_names_it(case, monkeypatch):
    case[2].write_text(UNCONFIGURED)
    w, config, run, calls = pr_case(case, monkeypatch, trusted=False, extra=RUNNERS)
    assert w.pr(case[1], config, '42', run=run, land=True) == 'PR #7'
    assert 'runner-untrusted' in body_of(calls)


def test_land_does_not_take_the_exception_when_the_git_local_trust_covers_the_runner(case, monkeypatch):
    """The runner CAN record red here, so the missing red is a real refusal, not an exception."""
    case[2].write_text(UNCONFIGURED)
    w, config, run, calls = pr_case(case, monkeypatch, trusted=True, extra=RUNNERS)
    assert w.pr(case[1], config, '42', run=run, land=True).startswith('TEST-FIRST REFUSED')
    assert not pushed(calls)


def test_land_does_not_take_the_exception_for_pytest_selectors_that_never_went_red(case, monkeypatch):
    w, config, run, calls = pr_case(case, monkeypatch)
    assert w.pr(case[1], config, '42', run=run, land=True).startswith('TEST-FIRST REFUSED')
    assert not pushed(calls)


def test_land_does_not_take_the_exception_for_a_none_plan_that_touches_code(case, monkeypatch):
    case[2].write_text('# Plan\n\n## Tests\nnone: wording only\n')
    w, config, run, calls = pr_case(case, monkeypatch)

    def diff_run(cwd, argv):
        if argv[:3] == ['git', 'diff', '--name-only']:
            return 'skills/x.py'
        return run(cwd, argv)
    assert w.pr(case[1], config, '42', run=diff_run, land=True).startswith('TEST-FIRST REFUSED')


@pytest.mark.parametrize('plan,extra', [('# Plan\n\n## Tests\n', None), (UNCONFIGURED, None), (UNCONFIGURED, RUNNERS)])
def test_outside_land_the_same_plans_are_still_refused(case, monkeypatch, plan, extra):
    case[2].write_text(plan)
    w, config, run, calls = pr_case(case, monkeypatch, extra=extra)
    assert w.pr(case[1], config, '42', run=run).startswith('TEST-FIRST REFUSED')
    assert not pushed(calls)


def test_a_malformed_section_is_not_an_exception_under_land(case, monkeypatch):
    """A bullet that is not a backticked selector is a plan defect (Plan-Review's), not a missing runner."""
    case[2].write_text('# Plan\n\n## Tests\n- tests/test_x.py without backticks\n')
    w, config, run, calls = pr_case(case, monkeypatch)
    assert w.pr(case[1], config, '42', run=run, land=True).startswith('TEST-FIRST REFUSED')


def test_land_cli_flag_reaches_pr(case, monkeypatch):
    w, config, run, calls = pr_case(case, monkeypatch)
    seen = {}
    monkeypatch.setitem(w._COMMANDS, 'pr', lambda sdlc, cfg, goal, **kw: seen.update(kw) or 'PR #7')
    monkeypatch.setattr(w.state, 'load_config', lambda sdlc: config)
    w.main(['work.py', 'pr', str(case[1]), '42', '--land'])
    assert seen.get('land') is True


# ---- AC-2: the post-PR review accepts or rejects the exception ---------------------------------------------

ACCEPT = 'findings: none\nTEST-FIRST-EXCEPTION: accept - vitest cannot run here, the UI diff is covered by review\n'
REJECT = 'TEST-FIRST-EXCEPTION: reject - a pytest test could have covered this\n'


def land_record(case, monkeypatch, reason='--land: no-selectors'):
    w, config, run, calls = pr_case(case, monkeypatch)
    case[2].write_text('# Plan\n\n## Tests\n')
    assert w.pr(case[1], config, '42', run=run, land=True) == 'PR #7'
    return w


def write_result(w, case, tmp_path, verdict, reason, n=[0]):
    n[0] += 1
    resolution = tmp_path / f'res{n[0]}.json'
    resolution.write_text('{}')
    manifest = {'generation_id': f'g{n[0]}', 'brief_sha256': '0' * 64, 'goal': '42'}
    return w._write_review_result(case[1], manifest, str(resolution), verdict, reason)


def test_the_pr_review_brief_requires_an_explicit_accept_or_reject(case, monkeypatch):
    w = land_record(case, monkeypatch)
    rc = load('review_context')
    brief = rc.brief(str(case[1]), '42', 'pr-review', artifact='PR#7', repo_root=str(case[0]))
    assert 'TEST-FIRST-EXCEPTION: accept' in brief and 'TEST-FIRST-EXCEPTION: reject' in brief
    assert 'no-selectors' in brief and 'VERDICT: block' in brief


def test_the_brief_is_unchanged_for_a_goal_with_no_exception(case):
    w = load('work')
    w._save(case[1], '42', {'worktree': str(case[0]), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42'})
    brief = load('review_context').brief(str(case[1]), '42', 'pr-review', artifact='PR#7', repo_root=str(case[0]))
    assert 'TEST-FIRST-EXCEPTION' not in brief


def test_an_approve_that_never_judged_the_exception_is_written_as_block(case, monkeypatch, tmp_path):
    w = land_record(case, monkeypatch)
    for verdict in ('approve', 'unblock'):
        result = write_result(w, case, tmp_path, verdict, 'no blocking findings\nVERDICT: approve')
        assert result['verdict'] == 'block' and 'TEST-FIRST-EXCEPTION' in result['reason'], result


def test_a_rejected_exception_is_written_as_block_even_under_an_approve(case, monkeypatch, tmp_path):
    w = land_record(case, monkeypatch)
    result = write_result(w, case, tmp_path, 'approve', REJECT + 'VERDICT: approve')
    assert result['verdict'] == 'block' and 'rejected' in result['reason']


def test_an_accepted_exception_keeps_the_approve(case, monkeypatch, tmp_path):
    w = land_record(case, monkeypatch)
    result = write_result(w, case, tmp_path, 'approve', ACCEPT + 'VERDICT: approve')
    assert result['verdict'] == 'approve'


def test_accept_and_reject_together_is_a_block(case, monkeypatch, tmp_path):
    w = land_record(case, monkeypatch)
    result = write_result(w, case, tmp_path, 'approve', ACCEPT + REJECT + 'VERDICT: approve')
    assert result['verdict'] == 'block'


def test_a_goal_without_an_exception_is_untouched(case, monkeypatch, tmp_path):
    w, config, run, calls = pr_case(case, monkeypatch)
    w._save(case[1], '42', {'worktree': str(case[0]), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42'})
    result = write_result(w, case, tmp_path, 'approve', 'fine\nVERDICT: approve')
    assert result['verdict'] == 'approve'


def test_an_explicit_no_tests_flag_is_reviewed_the_same_way(case, monkeypatch, tmp_path):
    w, config, run, calls = pr_case(case, monkeypatch)
    assert w.pr(case[1], config, '42', run=run, no_tests='throwaway prototype') == 'PR #7'
    assert write_result(w, case, tmp_path, 'approve', 'fine')['verdict'] == 'block'
    assert write_result(w, case, tmp_path, 'approve', ACCEPT)['verdict'] == 'approve'


# ---- AC-3: a malformed ## Tests fails Plan-Review with the parser's message --------------------------------

def test_check_tests_reports_the_parser_message(case):
    rg = load('red_green')
    case[2].write_text('# Plan\n\n## Tests\n- tests/test_x.py without backticks\n')
    assert rg.tests_section_error(case[2], {}) == '## Tests requires one backticked pytest selector per bullet'
    case[2].write_text('# Plan\n\n## Tests\n- `tests/test_x.py`\n')
    assert rg.tests_section_error(case[2], {}) == ''
    case[2].write_text('# Plan\n\n## Tests\nnone: docs only\n')
    assert rg.tests_section_error(case[2], {}) == ''
    case[2].write_text('# Plan\n\nno tests heading at all\n')
    assert 'requires 1' in rg.tests_section_error(case[2], {})


def test_check_tests_cli_is_the_gesture_plan_runs(case):
    (case[1] / 'config.json').write_text('{}')
    case[2].write_text('# Plan\n\n## Tests\n- `../escape.py`\n')
    bad = subprocess.run(['python3', str(S / 'red_green.py'), 'check-tests', str(case[1]), '42'],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and 'unsafe pytest selector' in bad.stderr, bad
    case[2].write_text(f'# Plan\n\n## Tests\n- `{NODE}`\n')
    good = subprocess.run(['python3', str(S / 'red_green.py'), 'check-tests', str(case[1]), '42'],
                          capture_output=True, text=True)
    assert good.returncode == 0, good


# ---- AC-4: a pytest goal has a red record before Review starts ---------------------------------------------

def witness_red(sdlc, node=NODE, goal='42'):
    rg = load('red_green')
    wit = load('witness')
    wit.record(str(sdlc), goal, node, 'assertion', 'h', provenance=rg.PROVENANCE, file_hash='f',
               observed_at='2026-01-01T00:00:00Z')


def review_refusal(case, config):
    (case[1] / 'config.json').write_text(json.dumps(config))
    return load('phase_gate').start_refusal(str(case[1]), '42', 'review')


ENFORCE = {'verify': {'enforce': True}}


def test_review_may_not_start_without_a_red_record_for_pytest_selectors(case):
    got = review_refusal(case, ENFORCE)
    assert got and 'loop.py red' in got and NODE in got


def test_review_starts_once_every_selector_has_a_red_row(case):
    witness_red(case[1])
    assert review_refusal(case, ENFORCE) is None


def test_a_red_row_for_a_different_test_does_not_satisfy_the_selector(case):
    witness_red(case[1], node='tests/test_other.py::test_z')
    assert review_refusal(case, ENFORCE)


def test_a_non_red_witness_does_not_count_as_a_red_record(case):
    wit = load('witness')
    wit.record(str(case[1]), '42', NODE, 'assertion', 'h', provenance='something-else', file_hash='f',
               observed_at='2026-01-01T00:00:00Z')
    assert review_refusal(case, ENFORCE)


def test_review_start_is_free_without_verify_enforce(case):
    assert review_refusal(case, {}) is None


@pytest.mark.parametrize('plan', ['# Plan\n\n## Tests\nnone: docs only\n', '# Plan\n\nno section\n',
                                  '# Plan\n\n## Tests\n- `vitest: ui/a.test.ts > x`\n'])
def test_review_start_only_checks_goals_that_planned_pytest_selectors(case, plan):
    case[2].write_text(plan)
    assert review_refusal(case, ENFORCE) is None


def test_other_phases_start_freely(case):
    (case[1] / 'config.json').write_text(json.dumps(ENFORCE))
    assert load('phase_gate').start_refusal(str(case[1]), '42', 'retro') is None
