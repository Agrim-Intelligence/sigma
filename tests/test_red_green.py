"""#2084: a `## Tests` bullet may name a configured runner (`vitest: path > suite > test`), and red/green
proof, the trust gate, the flake check and the witness all cover it.

Written test-first as ONE file and recorded red once on its final bytes (`loop.py red`). Every call to a
target goes through `outcome(lambda: ...)`, so a symbol or parameter that does not exist yet becomes a failed
`assert` (an AssertionError), never an AttributeError or TypeError. Modules load lazily inside the lambdas.

The runner is a `sh` stub: it appends its arguments to `stub.log`, exits 0 when a file `pass` exists, and else
prints a vitest-shaped `AssertionError` line and exits 1. NOTHING here was measured against a real vitest.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import time
import types

import pytest

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'
APPROVAL_ENV = 'SIGMA_TRUSTED_VERIFY_SHA256'
FILE = 'ui/a.test.tsx'
NODE = 'vitest: ui/a.test.tsx > suite > test'
NODE2 = 'vitest: ui/a.test.tsx > suite > other'
PYNODE = 'tests/test_x.py::test_a'
TEMPLATE = 'sh stub.sh {file} -t {name}'
PATTERN = 'AssertionError'
CMD = "sh stub.sh ui/a.test.tsx -t 'suite > test'"

STUB = '#!/bin/sh\necho "$@" >> stub.log\nif [ -e pass ]; then echo ok; exit 0; fi\n' \
       'echo "AssertionError: expected 1 to be 2"\nexit 1\n'
FLAKY = '#!/bin/sh\necho "$@" >> flaky.log\nn=$(wc -l < flaky.log)\nexit $((n % 2))\n'
HANG = '#!/bin/sh\nsleep 30 &\necho $! > child.pid\nwait\n'


def mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def outcome(fn):
    try:
        return 'ok', fn()
    except Exception as exc:                          # noqa: BLE001 - a missing symbol must read as a failed assert
        return 'raised', f'{type(exc).__name__}: {exc}'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def runners(template=TEMPLATE, pattern=PATTERN):
    return {'vitest': {'command': template, 'red_pattern': pattern}}


def refuses(res, *needles):
    kind, text = res
    assert kind == 'raised' and text.startswith('ValueError:'), res
    for needle in needles:
        assert needle in text, (needle, text)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True)


def make(tmp_path, template=TEMPLATE, pattern=PATTERN, grant=True, bullets=(NODE,), runner_config=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    if grant:
        git(root, 'config', '--local', 'sigma.allowRepositoryShellCommands', 'true')
    (root / 'ui').mkdir()
    (root / FILE).write_text('// a vitest-shaped file\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_x.py').write_text('def test_a():\n    assert False\n')
    for name, text in (('stub.sh', STUB), ('flaky.sh', FLAKY), ('hang.sh', HANG)):
        (root / name).write_text(text)
    config = {'verify': {'command': 'true'}, 'resource': {'mode': 'off'}}
    if runner_config:
        config['tests'] = {'runners': {'vitest': template if pattern is None else
                                       {'command': template, 'red_pattern': pattern}}}
    sdlc = root / '.sdlc'
    (sdlc / 'plans').mkdir(parents=True)
    (sdlc / 'config.json').write_text(json.dumps(config))
    plan = sdlc / 'plans/84.md'
    plan.write_text('# Plan\n\n## Tests\n' + ''.join(f'- `{x}`\n' for x in bullets))
    return types.SimpleNamespace(root=root, sdlc=sdlc, plan=plan, config=config, log=root / 'stub.log')


@pytest.fixture
def b(tmp_path):
    return make(tmp_path)


def set_plan(box, *bullets):
    box.plan.write_text('# Plan\n\n## Tests\n' + ''.join(f'- `{x}`\n' for x in bullets))


def ob(box, rn=None, goal='84', **kw):
    kw.setdefault('timeout', 60)
    kind, value = outcome(lambda: mod('red_green').observe(
        box.sdlc, goal, box.root, box.plan, runners=runners() if rn is None else rn, **kw))
    return value if kind == 'ok' else {'passed': False, 'error': value, 'raised': True}


def refusal(box, proof, rn=None, goal='84'):
    kind, value = outcome(lambda: mod('red_green').refusal(
        box.sdlc, goal, box.root, box.plan, proof, runners=runners() if rn is None else rn))
    return value if kind == 'ok' else 'RAISED ' + value


def rows(box, node=NODE, goal='84'):
    return [r for r in mod('witness').witnesses(box.sdlc, goal) if r.get('test') == node]


def invocations(box):
    return len(box.log.read_text().splitlines()) if box.log.exists() else 0


def fake_exec(results, delay=0.0):
    calls = []

    def run(command, cwd, seconds):
        calls.append({'at': time.time(), 'command': command, 'seconds': seconds})
        if delay:
            time.sleep(delay)
        calls[-1]['end'] = time.time()
        return results[min(len(calls), len(results)) - 1]
    run.calls = calls
    return run


def cli(box, *args, approval=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_') and k != APPROVAL_ENV}
    if approval is not None:
        env[APPROVAL_ENV] = approval
    return subprocess.run(['python3', str(S / 'loop.py'), *args], cwd=box.root, env=env,
                          capture_output=True, text=True, timeout=300)


def cli_red(box, **kw):
    return cli(box, 'red', '.sdlc', '84', **kw)


def evidence(box):
    path = mod('state').evidence_path(box.sdlc, '84')
    return json.loads(path.read_text()) if path.exists() else None


def pytest_run(output, code=0, nodes=(PYNODE,)):
    def run(root, argv):
        if '--collect-only' in argv:
            return subprocess.CompletedProcess(argv, 0, '\n'.join(nodes) + '\n', '')
        return subprocess.CompletedProcess(argv, code, '=== short test summary info ===\n' + output, '')
    return run


# ------------------------------------------------------------------ Task 1: parse and render (AC-4)

def test_runner_bullet_accepted_when_configured(b):
    res = outcome(lambda: mod('red_green').selectors(b.plan, runners()))
    assert res == ('ok', [NODE]), res


def test_unknown_runner_names_config_key(b):
    refuses(outcome(lambda: mod('red_green').selectors(b.plan, {'jest': {'command': 'x {file}'}})),
            "unknown test runner 'vitest'", 'tests.runners.vitest')


def test_runner_without_config_block_names_config_key(b):
    refuses(outcome(lambda: mod('red_green').selectors(b.plan)), "unknown test runner 'vitest'",
            'tests.runners.vitest')
    refuses(outcome(lambda: mod('red_green').selectors(b.plan, {})), 'tests.runners.vitest')


@pytest.mark.parametrize('file', ['/abs/a.test.tsx', '../a.test.tsx', 'ui/../../a.test.tsx', '-bad.tsx',
                                  'ui\\a.test.tsx', 'a;b.tsx', 'a|b.tsx', 'a&b.tsx', 'ui/a b.tsx', 'ui/./a.tsx'])
def test_bad_runner_file_rejected(b, file):
    set_plan(b, f'vitest: {file} > suite > test')
    refuses(outcome(lambda: mod('red_green').selectors(b.plan, runners())), 'unsafe runner file')


def test_render_quotes_file_and_name():
    rg = mod('red_green')
    res = outcome(lambda: rg.render_command('run {file} -t {name}', FILE, 'a b > c', 'vitest'))
    assert res == ('ok', "run ui/a.test.tsx -t 'a b > c'"), res
    res = outcome(lambda: rg.render_command('run {file} -t {name}', FILE, 'x; touch PWNED', 'vitest'))
    assert res == ('ok', "run ui/a.test.tsx -t 'x; touch PWNED'"), res
    res = outcome(lambda: rg.render_command('run {file} -t {name}', FILE, "it's", 'vitest'))
    assert res == ('ok', 'run ui/a.test.tsx -t ' + shlex.quote("it's")), res


def test_render_rejects_unknown_placeholder():
    refuses(outcome(lambda: mod('red_green').render_command('run {file} {other}', FILE, 'n', 'vitest')),
            'tests.runners.vitest', 'other')


@pytest.mark.parametrize('template', ['run "{name}"', "run '{name}'", 'run "x {file}"', 'run `{name}`'])
def test_render_rejects_placeholder_inside_quotes(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


@pytest.mark.parametrize('file,name', [(FILE, '-rf'), ('-x.tsx', 'n')])
def test_render_rejects_leading_dash(file, name):
    refuses(outcome(lambda: mod('red_green').render_command('run {file} {name}', file, name, 'vitest')),
            'may not start with')


@pytest.mark.parametrize('file,name', [(FILE, 'a\nb'), (FILE, 'a\x00b'), ('ui/a\n.tsx', 'n'), ('', 'n')])
def test_render_rejects_newline_nul_and_empty_file(file, name):
    refuses(outcome(lambda: mod('red_green').render_command('run {file} -t {name}', file, name, 'vitest')),
            'tests.runners.vitest')


def _injection_attempt(tmp_path, template, name):
    box = make(tmp_path, template=template, bullets=(f'vitest: {FILE} > {name}',))
    proc = cli_red(box)
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert 'tests.runners.vitest' in proc.stderr, proc.stderr
    assert not (box.root / 'PWNED').exists() and not box.log.exists()


def test_double_quoted_template_cannot_inject_via_name(tmp_path):
    _injection_attempt(tmp_path, 'sh stub.sh "{name}"', '$(touch PWNED)')


def test_nested_substitution_template_cannot_inject(tmp_path):
    _injection_attempt(tmp_path, 'sh stub.sh "$(echo "{name}")"', '$(touch PWNED)')


@pytest.mark.parametrize('template', ['run ${name}', 'run $ {name}', 'run $x{name}'])
def test_dollar_brace_and_dollar_space_placeholders_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


def test_backslash_before_placeholder_rejected():
    refuses(outcome(lambda: mod('red_green').render_command('run \\{name}', FILE, 'n', 'vitest')),
            'tests.runners.vitest')


def test_heredoc_template_rejected():
    refuses(outcome(lambda: mod('red_green').render_command('cat <<EOF\n{name}\nEOF', FILE, 'n', 'vitest')),
            'tests.runners.vitest')


def test_ansi_c_quote_dollar_rejected():
    refuses(outcome(lambda: mod('red_green').render_command("run $'{name}'", FILE, 'n', 'vitest')),
            'tests.runners.vitest')


@pytest.mark.parametrize('template', ['run x{name}', 'run a;{name}', 'run {file}{name}', 'run {name}.snap'])
def test_glued_token_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


def test_flag_equals_suffix_accepted():
    res = outcome(lambda: mod('red_green').render_command('run --grep={name} {file}', FILE, 'a b', 'vitest'))
    assert res == ('ok', "run --grep='a b' ui/a.test.tsx"), res


def test_brace_escape_renders_literal_braces():
    res = outcome(lambda: mod('red_green').render_command('echo {{ok}} {file}', FILE, '', 'vitest'))
    assert res == ('ok', 'echo {ok} ui/a.test.tsx'), res


def test_value_containing_double_braces_survives():
    """The `{{`/`}}` escape is undone on the TEMPLATE only, never over the rendered string (a name is data)."""
    res = outcome(lambda: mod('red_green').render_command('run {file} -t {name}', FILE, '{{x}} }}', 'vitest'))
    assert res == ('ok', "run ui/a.test.tsx -t '{{x}} }}'"), res


@pytest.mark.parametrize('template', ['{name} run', 'eval {name}', 'x ; {name}', 'x && {file}', 'sh -c {name}',
                                      'run > {file}', 'run 2>> {file}', 'x | {name}'])
def test_placeholder_in_command_or_redirect_position_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


@pytest.mark.parametrize('template', ['run ` {name} `', 'run ` {file} `', 'run $( {name} )'])
def test_backtick_or_command_substitution_around_a_spaced_placeholder_rejected(template):
    """A backtick or `$(` separated from the placeholder by spaces leaves it a whole word, so only the forbidden
    characters stop `` ` x ` `` from running the quoted value as a command."""
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


def test_file_with_shell_metacharacters_is_quoted(b):
    """`{file}` passes the path checks with `$(`, quotes and globs in it, so quoting it is not decoration."""
    rg = mod('red_green')
    res = outcome(lambda: rg.render_command('run {file}', 'ui/a$(id)*.tsx', '', 'vitest'))
    assert res == ('ok', "run 'ui/a$(id)*.tsx'"), res
    set_plan(b, 'vitest: ui/a$(id)*.tsx > t')
    assert outcome(lambda: rg.selectors(b.plan, runners())) == ('ok', ['vitest: ui/a$(id)*.tsx > t'])


@pytest.mark.parametrize('template', ['echo ok\n{name}', 'a\r{name}', 'echo ok\r\n{file}', 'x\n{name} y'])
def test_newline_or_carriage_return_in_a_template_with_a_placeholder_rejected(template):
    """A bare newline or CR starts a new command for the shell but is plain whitespace to `shlex.split`, so it
    would hide a placeholder in command position from the token rules."""
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


@pytest.mark.parametrize('template', ['bash -lc {name}', 'bash -ec {name}', 'zsh -ic {file}', 'sh -c {name}'])
def test_shell_option_cluster_holding_c_before_a_placeholder_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


@pytest.mark.parametrize('template', ['python3 -c {name}', 'node -e {name}', 'node --eval {name}', 'node -p {name}',
                                      'perl -E {name}', 'ruby -e {name}'])
def test_interpreter_eval_flag_before_a_placeholder_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


@pytest.mark.parametrize('template', ['sh {name}', 'bash -x {name}', 'sh {file}'])
def test_placeholder_as_first_argument_of_a_bare_shell_rejected(template):
    refuses(outcome(lambda: mod('red_green').render_command(template, FILE, 'n', 'vitest')), 'tests.runners.vitest')


def test_script_and_module_forms_with_a_placeholder_still_accepted():
    rg = mod('red_green')
    for template, want in (('sh stub.sh {file} -t {name}', "sh stub.sh ui/a.test.tsx -t n"),
                           ('python3 -m pytest {file}', 'python3 -m pytest ui/a.test.tsx'),
                           ('node run.js {file} {name}', 'node run.js ui/a.test.tsx n')):
        assert outcome(lambda: rg.render_command(template, FILE, 'n', 'vitest')) == ('ok', want), template


@pytest.mark.parametrize('result', [('failed', 1, ''), ('failed', 127, ''), ('timeout', None, ''), ('error', None, '')])
def test_flake_runner_identical_non_ok_runs_are_absent_not_verified(b, result):
    lp = mod('loop')
    got = outcome(lambda: lp._runner_flake(b.root, [NODE], runners(), 60, fake_exec([result])))
    assert got[0] == 'ok' and got[1].get('verdict') == 'absent', got
    ok = outcome(lambda: lp._runner_flake(b.root, [NODE], runners(), 60, fake_exec([('ok', 0, '')])))
    assert ok[0] == 'ok' and ok[1].get('verdict') == 'verified', ok


def test_runner_named_none_rejected():
    refuses(outcome(lambda: mod('state').test_runners({'tests': {'runners': {'none': 'x {file}'}}})),
            'tests.runners.none')


@pytest.mark.parametrize('block,key', [
    ({'vitest': ''}, 'tests.runners.vitest'),
    ({'vitest': 7}, 'tests.runners.vitest'),
    ({'vitest': {'command': ''}}, 'tests.runners.vitest'),
    ({'vitest': {'command': 'x {file}', 'red_pattern': '('}}, 'tests.runners.vitest.red_pattern'),
    ({'vitest': {'command': 'x {file}', 'red_pattern': 5}}, 'tests.runners.vitest.red_pattern'),
    ({'Vi test': 'x {file}'}, 'tests.runners.Vi test'),
])
def test_invalid_runner_config_rejected_naming_key(block, key):
    refuses(outcome(lambda: mod('state').test_runners({'tests': {'runners': block}})), key)


def test_test_runners_returns_validated_json_safe_map():
    st = mod('state')
    assert outcome(lambda: st.test_runners({})) == ('ok', {})
    res = outcome(lambda: st.test_runners({'tests': {'runners': {
        'vitest': 'x {file}', 'jest': {'command': 'y {name}', 'red_pattern': 'Err'}}}}))
    assert res == ('ok', {'vitest': {'command': 'x {file}', 'red_pattern': None},
                          'jest': {'command': 'y {name}', 'red_pattern': 'Err'}}), res


def test_runner_commands_lists_the_rendered_commands(b):
    st = mod('state')
    assert outcome(lambda: st.runner_commands(b.plan, b.config)) == ('ok', [CMD])
    b.plan.write_text('# Plan\n\n## Tests\n- `tests/test_x.py::test_a`\n')
    assert outcome(lambda: st.runner_commands(b.plan, b.config)) == ('ok', [])


def test_test_name_containing_double_colon_is_a_runner_id(b):
    rg, w = mod('red_green'), mod('witness')
    node = 'vitest: ui/a.test.tsx > suite > test::x'
    parsed = outcome(lambda: tuple(rg.parse_runner(node)))
    assert parsed == ('ok', ('vitest', FILE, 'suite > test::x')), parsed
    src = outcome(lambda: w.test_source(node, root=b.root))
    assert src == ('ok', '// a vitest-shaped file\n'), src


def test_name_keeps_gt_in_suite_title():
    rg = mod('red_green')
    assert outcome(lambda: tuple(rg.parse_runner('vitest: ui/a.test.tsx > a > b > c'))) == (
        'ok', ('vitest', FILE, 'a > b > c'))
    assert outcome(lambda: tuple(rg.parse_runner('vitest: ui/a.test.tsx'))) == ('ok', ('vitest', FILE, ''))


# ------------------------------------------------------------------ Task 2: observe and refusal (AC-1, AC-2)

def test_observe_runner_red_with_pattern_records_strong_provenance(b):
    res = ob(b)
    assert res.get('passed') is False and res.get('provenance') == 'planned-pytest-v1', res
    assert res['runners'][NODE]['red'] == 'pattern', res
    got = rows(b)
    assert len(got) == 1, got
    assert (got[0]['provenance'], got[0]['kind']) == ('planned-runner-v1', 'assertion')
    assert got[0]['file_hash'] == hashlib.sha256((b.root / FILE).read_bytes()).hexdigest()
    assert got[0]['observed_at'] == res['completed_at']


def test_observe_runner_red_without_pattern_records_weaker_provenance(b):
    res = ob(b, runners(pattern=None))
    got = rows(b)
    assert len(got) == 1, (res, got)
    assert (got[0]['provenance'], got[0]['kind']) == ('planned-runner-exit-v1', 'other')
    assert res['runners'][NODE]['red'] == 'exit', res


def test_observe_runner_red_pattern_mismatch_records_nothing(b):
    res = ob(b, runners(pattern='NOPE-NOT-IN-OUTPUT'))
    assert res.get('runners', {}).get(NODE, {}).get('exit') == 1, res    # it ran and failed ...
    assert res['runners'][NODE]['red'] is None and rows(b) == [], (res, rows(b))   # ... but earned no red


def test_observe_runner_green_exit_zero(b):
    (b.root / 'pass').write_text('yes')
    res = ob(b)
    assert res.get('passed') is True and res.get('exit') == 0 and res.get('error') == '', res
    assert res['runners'][NODE]['exit'] == 0 and rows(b) == []
    assert res['nodes'] == {NODE: hashlib.sha256((b.root / FILE).read_bytes()).hexdigest()}


@pytest.mark.parametrize('result', [('failed', 126, 'AssertionError: x'), ('failed', 127, 'AssertionError: x'),
                                    ('failed', -9, 'AssertionError: x'), ('timeout', None, 'AssertionError: x'),
                                    ('stopped', None, 'AssertionError: x'), ('refused', None, 'AssertionError: x'),
                                    ('error', None, 'AssertionError: x')])
def test_exit_126_127_and_signal_earn_no_red(b, result):
    fx = fake_exec([result])
    res = ob(b, exec_runner=fx)
    assert len(fx.calls) == 1, (res, fx.calls)                      # the seam was used ...
    assert res.get('passed') is False and res.get('error'), res
    assert rows(b) == [], rows(b)                                   # ... and a harness failure earned no red
    assert res.get('exit'), res                                     # and the aggregate exit is nonzero


def test_mixed_plan_exit_is_first_nonzero(b):
    set_plan(b, PYNODE, NODE)
    red_py = pytest_run(f'FAILED {PYNODE} - assert False\n', 1)
    res = ob(b, run=red_py, exec_runner=fake_exec([('failed', 3, 'AssertionError')]))
    assert res.get('exit') == 1, res                                # the pytest exit comes first
    green_py = pytest_run(f'PASSED {PYNODE}\n', 0)
    res = ob(b, run=green_py, exec_runner=fake_exec([('failed', 3, 'AssertionError')]))
    assert res.get('exit') == 3 and res.get('passed') is False, res  # a mixed plan never reports 0 while one failed


def test_runner_deadline_is_shared_across_runs(b):
    set_plan(b, NODE, NODE2, 'vitest: ui/a.test.tsx > suite > third')
    fx = fake_exec([('timeout', None, '')], delay=0.3)
    res = ob(b, timeout=0.5, exec_runner=fx)
    assert [round(c['seconds'], 1) for c in fx.calls][:1] == [0.5], (fx.calls, res)
    assert len(fx.calls) == 2 and fx.calls[1]['seconds'] < fx.calls[0]['seconds'], fx.calls   # the third never started
    assert res.get('passed') is False


def test_exec_runner_seam_is_separate_from_run(b):
    def boom(root, argv):
        raise AssertionError('pytest must not run for a runner-only plan')
    fx = fake_exec([('failed', 1, 'AssertionError: expected 1 to be 2')])
    res = ob(b, run=boom, exec_runner=fx)
    assert len(fx.calls) == 1 and fx.calls[0]['command'] == CMD, (res, fx.calls)
    assert len(rows(b)) == 1


def test_red_row_binds_command_and_pattern_hash(b):
    res = ob(b)
    want = {'command_sha256': sha(CMD), 'pattern_sha256': sha(PATTERN)}
    got = res.get('runners', {}).get(NODE, {})
    assert {k: got.get(k) for k in want} == want, res
    assert [r['detail'] for r in rows(b)] == ['bound:%s:%s' % (sha(CMD), sha(PATTERN))]


def _red_then_green(b, red_runners=None, green_runners=None):
    ob(b, red_runners)
    (b.root / 'pass').write_text('yes')
    return ob(b, green_runners)


def test_refusal_accepts_runner_proof(b):
    green = _red_then_green(b)
    assert green.get('passed') is True, green
    assert refusal(b, green) == ''


def test_refusal_rejects_template_edited_after_red(b):
    edited = runners(TEMPLATE + ' --edited')
    green = _red_then_green(b, runners(), edited)
    assert green.get('passed') is True, green
    assert 'runner command changed since observed' in refusal(b, green, edited)


def test_refusal_rejects_pattern_edited_after_red(b):
    edited = runners(pattern='Assertion')
    green = _red_then_green(b, runners(), edited)
    assert green.get('passed') is True, green
    assert 'runner command changed since observed' in refusal(b, green, edited)


def test_refusal_rejects_template_edited_after_green(b):
    green = _red_then_green(b)
    assert refusal(b, green) == ''
    assert 'runner command changed since observed' in refusal(b, green, runners(TEMPLATE + ' --edited'))


def test_refusal_rejects_proof_map_with_a_different_command_hash(b):
    green = _red_then_green(b)
    assert refusal(b, green) == ''
    green['runners'][NODE]['command_sha256'] = sha('something else')
    assert 'runner command changed since observed' in refusal(b, green)


def test_refusal_rejects_row_detail_with_a_different_hash(b):
    green = _red_then_green(b)
    assert refusal(b, green) == ''
    w = mod('witness')
    edited = [dict(r, detail='bound:%s:%s' % (sha('other'), sha(PATTERN))) for r in w.witnesses(b.sdlc, '84')]
    w.path(b.sdlc, '84').write_text(''.join(json.dumps(r) + '\n' for r in edited))
    assert 'runner command changed since observed' in refusal(b, green)


def test_refusal_rejects_red_recorded_for_older_file_bytes(b):
    ob(b)                                                           # red on the old bytes
    (b.root / FILE).write_text('// the file changed after red\n')
    (b.root / 'pass').write_text('yes')
    green = ob(b)
    assert green.get('passed') is True, green
    assert 'no matching assertion red before green' in refusal(b, green)


def test_refusal_rejects_runner_proof_when_runner_unconfigured(b):
    green = _red_then_green(b)
    assert refusal(b, green) == ''
    assert 'tests.runners.vitest' in refusal(b, green, {})


def _forged(b, goal, provenance, kind):
    w = mod('witness')
    (b.root / 'pass').write_text('yes')
    bound = 'bound:%s:%s' % (sha(CMD), sha(PATTERN))
    w.record(b.sdlc, goal, NODE, kind, w.source_hash(w.test_source(NODE, root=b.root)), detail=bound,
             provenance=provenance, file_hash=hashlib.sha256((b.root / FILE).read_bytes()).hexdigest(),
             observed_at=time.time() - 5)
    return ob(b, goal=goal)


def test_refusal_rejects_pytest_provenance_for_runner_node(b):
    green = _forged(b, '84', 'planned-pytest-v1', 'assertion')
    assert green.get('passed') is True, green
    assert 'no matching assertion red before green' in refusal(b, green)
    # control: the same row shape with the runner provenance for the node IS accepted
    assert refusal(b, _forged(b, '85', 'planned-runner-v1', 'assertion'), goal='85') == ''


def test_refusal_rejects_exit_row_with_assertion_kind(b):
    green = _forged(b, '84', 'planned-runner-exit-v1', 'assertion')
    assert green.get('passed') is True, green
    assert 'no matching assertion red before green' in refusal(b, green)
    assert refusal(b, _forged(b, '85', 'planned-runner-exit-v1', 'other'), goal='85') == ''   # the legitimate pair


def test_exit_only_red_is_not_strong_in_witness_verdict(tmp_path):
    w = mod('witness')
    weak = make(tmp_path / 'weak')
    ob(weak, runners(pattern=None))
    got = outcome(lambda: w.verdict(weak.sdlc, '84', [NODE], root=weak.root)['verdict'])
    assert got == ('ok', 'unverified'), got
    strong = make(tmp_path / 'strong')
    ob(strong)
    got = outcome(lambda: w.verdict(strong.sdlc, '84', [NODE], root=strong.root)['verdict'])
    assert got == ('ok', 'verified'), got


def test_observe_one_started_completed_pair_brackets_all_runs(b):
    set_plan(b, NODE, NODE2)
    fx = fake_exec([('failed', 1, 'AssertionError')], delay=0.05)
    res = ob(b, exec_runner=fx)
    assert len(fx.calls) == 2, (res, fx.calls)
    assert res['started_at'] <= fx.calls[0]['at'] and res['completed_at'] >= fx.calls[1]['end'], (res, fx.calls)


def test_red_rows_use_the_shared_completed(b):
    set_plan(b, NODE, NODE2)
    res = ob(b, exec_runner=fake_exec([('failed', 1, 'AssertionError')], delay=0.05))
    got = rows(b, NODE) + rows(b, NODE2)
    assert len(got) == 2 and {r['observed_at'] for r in got} == {res.get('completed_at')}, (res, got)


def test_observe_result_is_json_serializable(b):
    res = ob(b)
    assert 'runners' in res, res
    assert outcome(lambda: json.loads(json.dumps(res))) == ('ok', res)


def test_red_pattern_sees_only_last_200k_chars(b):
    early = 'AssertionError' + 'x' * 250000
    ob(b, exec_runner=fake_exec([('failed', 1, early)]))
    assert rows(b) == []
    ob(b, exec_runner=fake_exec([('failed', 1, 'x' * 250000 + 'AssertionError')]))
    assert len(rows(b)) == 1


def test_runner_timeout_kills_group_and_earns_no_red(tmp_path):
    hang = 'sh hang.sh {file} -t {name}'
    box = make(tmp_path, template=hang)
    res = ob(box, runners(hang), timeout=1)
    pid_file = box.root / 'child.pid'
    assert pid_file.exists(), res                                   # the stub started and spawned a child
    pid = int(pid_file.read_text())
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        raise AssertionError('the sleeping child outlived the runner timeout')
    assert res.get('passed') is False and rows(box) == [], (res, rows(box))


def test_runner_file_change_during_observe_discards(b):
    def edit(command, cwd, seconds):
        (b.root / FILE).write_text('// edited while the runner ran\n')
        return 'failed', 1, 'AssertionError'
    res = ob(b, exec_runner=edit)
    assert res.get('passed') is False and 'changed during observation' in res.get('error', ''), res
    assert rows(b) == []
    (b.root / FILE).write_text('// steady\n')
    assert ob(b, exec_runner=fake_exec([('failed', 1, 'AssertionError')])).get('provenance')


def test_mixed_pytest_and_runner_plan(b):
    set_plan(b, PYNODE, NODE)
    ob(b, run=pytest_run(f'FAILED {PYNODE} - assert False\n', 1))
    assert len(rows(b, PYNODE)) == 1 and len(rows(b, NODE)) == 1
    assert rows(b, PYNODE)[0]['provenance'] == 'planned-pytest-v1'
    assert rows(b, NODE)[0]['provenance'] == 'planned-runner-v1'
    (b.root / 'pass').write_text('yes')
    green = ob(b, run=pytest_run(f'PASSED {PYNODE}\n', 0))
    assert green.get('passed') is True and refusal(b, green) == ''


# ------------------------------------------------------------------ Task 3: trust gate and CLI paths

def test_loop_red_runs_stub_under_git_local_grant(b):
    proc = cli_red(b)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert invocations(b) == 1 and 'recorded' in proc.stdout
    assert b.log.read_text().strip() == "ui/a.test.tsx -t suite > test"
    assert len(rows(b)) == 1


def test_loop_verify_records_green_for_runner(b):
    assert cli_red(b).returncode == 0
    (b.root / 'pass').write_text('yes')
    proc = cli(b, 'verify', '.sdlc', '84')
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    ev = evidence(b)
    assert ev and ev['exit'] == 0 and ev['test_first']['passed'] is True, ev
    assert ev['test_first']['runners'][NODE]['exit'] == 0
    assert refusal(b, ev['test_first']) == ''


def test_work_pr_accepts_runner_proof_without_no_tests(b, monkeypatch):
    w = mod('work')
    ob(b)
    (b.root / 'pass').write_text('yes')
    green = ob(b)
    ev_path = w.state.evidence_path(b.sdlc, '84')
    ev_path.parent.mkdir(parents=True, exist_ok=True)
    ev_path.write_text(json.dumps({'root': str(b.root), 'content': {'fingerprint': 'x'}, 'test_first': green}))
    monkeypatch.setattr(w.state, 'done_refusal', lambda *a: None)
    rec = {'worktree': str(b.root), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/84'}
    config = dict(b.config, work={'enabled': True}, verify={'enforce': True, 'command': 'true'})
    got = outcome(lambda: w._test_first_refusal(b.sdlc, config, rec, '84', lambda cwd, argv: ''))
    assert got == ('ok', ''), got
    no_runner = {k: v for k, v in config.items() if k != 'tests'}
    got = outcome(lambda: w._test_first_refusal(b.sdlc, no_runner, rec, '84', lambda cwd, argv: ''))
    assert got[0] == 'ok' and 'tests.runners.vitest' in got[1] and 'TEST-FIRST REFUSED' in got[1], got


def _approved(tmp_path, **kw):
    box = make(tmp_path, grant=False, **kw)
    git(box.root, 'remote', 'add', 'origin', 'https://github.com/Owner/Repo.git')
    return box, mod('shell_policy').approval_value('owner/repo', 'true')


def test_loop_red_refuses_runner_under_environment_approval(tmp_path):
    box, approval = _approved(tmp_path)
    proc = cli_red(box, approval=approval)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert 'does not cover a runner command' in proc.stderr and 'sigma.allowRepositoryShellCommands' in proc.stderr
    assert not box.log.exists() and rows(box) == []


def test_loop_verify_refuses_runner_under_environment_approval(tmp_path):
    box, approval = _approved(tmp_path)
    proc = cli(box, 'verify', '.sdlc', '84', approval=approval)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert 'does not cover a runner command' in proc.stderr and 'sigma.allowRepositoryShellCommands' in proc.stderr
    assert not box.log.exists() and evidence(box) is None


def test_loop_red_unknown_runner_exit_2_names_key(tmp_path):
    box = make(tmp_path, runner_config=False)
    proc = cli_red(box)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "unknown test runner 'vitest'" in proc.stderr and 'tests.runners.vitest' in proc.stderr
    assert not box.log.exists()


def test_invalid_runner_config_exits_2_naming_key(tmp_path):
    for n, pattern in enumerate(('(', 5)):
        box = make(tmp_path / str(n), pattern=pattern)
        proc = cli_red(box)
        assert proc.returncode == 2, (proc.stdout, proc.stderr)
        assert 'tests.runners.vitest.red_pattern' in proc.stderr and not box.log.exists()
    box = make(tmp_path / 'verify', pattern='(')
    proc = cli(box, 'verify', '.sdlc', '84')
    assert proc.returncode == 2 and 'tests.runners.vitest.red_pattern' in proc.stderr, (proc.stdout, proc.stderr)
    assert not box.log.exists() and evidence(box) is None


def test_observe_without_trust_capability_runs_nothing(tmp_path, monkeypatch, capsys):
    """The refusal call is disabled on purpose: `runners` must not reach `observe` unless the trust is git-local."""
    box, approval = _approved(tmp_path)
    monkeypatch.setenv(APPROVAL_ENV, approval)
    lp = mod('loop')
    monkeypatch.setattr(lp, '_runner_trust_refusal', lambda *a, **k: None, raising=False)
    assert lp.red_goal(box.sdlc, '84') == 2
    err = capsys.readouterr().err
    assert "unknown test runner 'vitest'" in err and 'tests.runners.vitest' in err, err
    assert not box.log.exists()


def test_one_config_read_feeds_gate_observe_and_flake(b, monkeypatch):
    lp = mod('loop')
    reads = []

    def counted(config):
        reads.append(1)
        return runners() if len(reads) == 1 else runners('true {file} {name}', None)
    monkeypatch.setattr(lp.state, 'test_runners', counted, raising=False)
    lp.red_goal(b.sdlc, '84')
    assert len(reads) == 1, reads
    del reads[:]
    (b.root / 'pass').write_text('yes')
    lp.verify_goal(b.sdlc, '84')
    assert len(reads) == 1, reads


# ------------------------------------------------------------------ Task 4: flake and witness

def test_flake_runs_runner_three_times_verified(b):
    lp = mod('loop')
    (b.root / 'pass').write_text('yes')
    res = outcome(lambda: lp._flake_verdict(b.sdlc, '84', b.root, True, runners()))
    assert res[0] == 'ok' and res[1].get('verdict') == 'verified', res
    assert invocations(b) == 3, invocations(b)


def test_flake_runner_disagreement_unverified(tmp_path):
    box = make(tmp_path, template='sh flaky.sh {file} -t {name}')
    lp = mod('loop')
    res = outcome(lambda: lp._flake_verdict(box.sdlc, '84', box.root, True, runners('sh flaky.sh {file} -t {name}')))
    assert res[0] == 'ok' and res[1].get('verdict') == 'unverified' and res[1].get('disagreement'), res


def test_witness_hashes_runner_file(b):
    w = mod('witness')
    assert outcome(lambda: w.test_source(NODE, root=b.root)) == ('ok', '// a vitest-shaped file\n')
    before = w.source_hash(w.test_source(NODE, root=b.root))
    (b.root / FILE).write_text('// edited\n')
    after = w.source_hash(w.test_source(NODE, root=b.root))
    assert before and after and before != after


# ------------------------------------------------------------------ Task 5: config template

def test_config_template_documents_tests_runners():
    path = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-init/templates/config.json.tmpl'
    cfg = json.loads(path.read_text())
    assert cfg.get('tests', {}).get('runners') == {} and '_tests' in cfg, cfg.get('tests')
    assert outcome(lambda: mod('state').test_runners(cfg)) == ('ok', {})
