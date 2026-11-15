"""#2222 T7/T8: `goal_readiness` (off | warn | require): setting, pick check, start check and the promote seam."""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name + '_gr', SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ib = load('issue_body')
GOOD = 'Do it.\n\n## Done when\n- [ ] One.\n- [ ] Two.\n- [ ] Three.\n'
BAD = 'Do it, no criteria at all.\n'


class Src:
    def __init__(self, body=BAD, fail=False):
        self.body, self.fail = body, fail

    def fetch_title_body(self, goal):
        if self.fail:
            raise RuntimeError('boom')
        return {'title': 'T', 'body': self.body}


def check(config, body=BAD, fail=False, monkeypatch=None):
    loop = load('loop')
    parked = []
    loop._record = lambda sdlc, source, goal, kind, detail, **kw: parked.append((kind, detail))
    return loop.readiness_check('.sdlc', '42', config, Src(body, fail)), parked


def test_goal_readiness_default_is_warn():
    assert ib.readiness_mode({}) == 'warn'
    assert ib.readiness_mode({'goal_readiness': {}}) == 'warn'
    out, parked = check({})
    assert out.startswith('WARN') and parked == []


def test_goal_readiness_off_checks_nothing():
    out, parked = check({'goal_readiness': {'mode': 'off'}})
    assert out == 'OFF' and parked == []


def test_goal_readiness_warn_proceeds_with_message_naming_missing_sections():
    out, parked = check({'goal_readiness': {'mode': 'warn'}})
    assert out.startswith('WARN') and '`## Done when`' in out and parked == []


def test_goal_readiness_require_parks_with_stated_reason():
    out, parked = check({'goal_readiness': {'mode': 'require'}})
    assert out.startswith('PARKED') and '`## Done when`' in out
    assert parked == [('parked', out[len('PARKED '):])]


def test_goal_readiness_required_sections_configurable():
    cfg = {'goal_readiness': {'mode': 'require', 'required_sections': ['Rollback plan']}}
    out, _ = check(cfg, body=GOOD)
    assert out.startswith('PARKED') and 'Rollback plan' in out
    out, _ = check(cfg, body=GOOD + '\n## Rollback plan\nrevert\n')
    assert out == 'PROCEED'


def test_goal_readiness_verification_command_required_only_when_known():
    assert ib.readiness_required({}) == ['Done when']
    assert ib.readiness_required({'verify': {'command': '  '}}) == ['Done when']
    assert ib.readiness_required({'verify': {'command': 'pytest -q'}}) == ['Done when', 'Verification command']
    out, _ = check({'verify': {'command': 'pytest -q'}}, body=GOOD)
    assert out.startswith('WARN') and 'Verification command' in out
    assert check({}, body=GOOD)[0] == 'PROCEED'


def test_goal_readiness_invalid_mode_refused_not_defaulted_silently(tmp_path):
    with pytest.raises(ValueError, match='mode must be one of'):
        ib.readiness_mode({'goal_readiness': {'mode': 'requre'}})
    with pytest.raises(ValueError):
        ib.readiness_mode({'goal_readiness': 'require'})
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir()
    (sdlc / 'config.json').write_text(json.dumps({'goal_readiness': {'mode': 'requre'}}))
    proc = subprocess.run([sys.executable, str(SCRIPTS / 'loop.py'), 'readiness-check', str(sdlc), '42'],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and 'REFUSED' in proc.stderr and proc.stdout == ''


def test_goal_readiness_pick_verb_first_word_is_proceed_warn_parked_or_off():
    words = {check(c, b)[0].split()[0] for c, b in [
        ({}, GOOD), ({}, BAD), ({'goal_readiness': {'mode': 'require'}}, BAD), ({'goal_readiness': {'mode': 'off'}}, BAD)]}
    assert words == {'PROCEED', 'WARN', 'PARKED', 'OFF'}
    assert check({'goal_readiness': {'mode': 'require'}}, fail=True)[0] == 'PROCEED'      # fail-open on a fetch error


def test_goal_readiness_template_block_defaults():
    text = (ROOT / 'skills/sigma-init/templates/config.json.tmpl').read_text()
    cfg = json.loads(text)
    assert cfg['goal_readiness'] == {'mode': 'warn'} and '_goal_readiness' in cfg
    assert ib.readiness_mode(cfg) == 'warn'


def test_goal_readiness_setting_never_reaches_append_only_guard():
    acc_src = (SCRIPTS / 'acceptance.py').read_text()
    assert 'goal_readiness' not in acc_src and 'readiness' not in acc_src.split('def prefix_check', 1)[1].split('def _guard', 1)[0]
    # behaviourally: mode off leaves a tampered record refused (the guard has no config input at all)
    acc = load('acceptance')
    import inspect
    assert 'config' not in inspect.signature(acc.prefix_check).parameters


def test_goal_readiness_start_refuses_under_require_and_warns_otherwise(tmp_path, monkeypatch, capsys):
    work = load('work')
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc, cfg: Src(BAD))
    refusal = work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'require'}}, '42')
    assert refusal.startswith('READINESS REFUSED:') and '(nothing started)' in refusal
    assert work._readiness_refusal(tmp_path / '.sdlc', {}, '42') is None
    assert 'warning' in capsys.readouterr().err
    assert work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'off'}}, '42') is None
    assert work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'nope'}}, '42').startswith('READINESS REFUSED')


def test_goal_readiness_start_fails_open_when_the_body_cannot_be_read(tmp_path, monkeypatch):
    work = load('work')
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc, cfg: Src(BAD, fail=True))
    assert work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'require'}}, '42') is None


def test_goal_readiness_start_reads_a_local_goal_file(tmp_path):
    work = load('work')
    goal = tmp_path / 'g.md'
    goal.write_text(BAD)
    assert work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'require'}}, str(goal)).startswith('READINESS')
    goal.write_text(GOOD)
    assert work._readiness_refusal(tmp_path / '.sdlc', {'goal_readiness': {'mode': 'require'}}, str(goal)) is None


def test_goal_readiness_start_calls_the_readiness_check_before_cutting_anything(tmp_path, monkeypatch):
    """`start` itself must run it (host-agnostic, not only the prose pick step): a refusal is returned first."""
    work = load('work')
    monkeypatch.setattr(work, '_dirty_root_refusal', lambda *a, **k: None)
    monkeypatch.setattr(work, '_readiness_refusal', lambda sdlc, cfg, goal: 'READINESS REFUSED: marker (nothing started)')
    real_load = work._load
    monkeypatch.setattr(work, '_load', lambda name: _FakeLoop() if name == 'loop' else real_load(name))
    calls = []
    out = work.start(tmp_path / '.sdlc', {}, '42', run=lambda cwd, argv: calls.append(argv) or '')
    assert out.startswith('READINESS REFUSED: marker') and calls == []


class _FakeLoop:
    """Stands in for `loop.py` inside `work.start`: records the transition `start` makes, touches nothing real."""
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail
        self.sources = self

    def get_source(self, sdlc_dir, config):
        return 'SRC'

    def _record(self, sdlc_dir, source, goal, result, detail='', **kw):
        if self.fail:
            raise RuntimeError('ledger down')
        self.calls.append(('record', goal, result, detail, kw.get('qkind')))

    def _release(self, sdlc_dir, source, goal, reason='', **kw):
        self.calls.append(('release', goal, reason))
        return True


def _start_with(monkeypatch, tmp_path, config, fake, body=BAD):
    work = load('work')
    real_load = work._load
    monkeypatch.setattr(work, '_load', lambda name: fake if name == 'loop' else real_load(name))
    monkeypatch.setattr(work, '_dirty_root_refusal', lambda *a, **k: None)
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc, cfg: Src(body))
    calls = []
    out = work.start(tmp_path / '.sdlc', config, '42', run=lambda cwd, argv: calls.append(argv) or '')
    return out, calls


def test_goal_readiness_start_require_parks_the_goal_it_refuses(tmp_path, monkeypatch):
    """Review fix M2 (AC-3 on the Python path): exit 4 from `start` means the goal already left the claimed state,
    so a `require` refusal at start records the park with the missing sections named, and nothing is cut."""
    fake = _FakeLoop()
    out, calls = _start_with(monkeypatch, tmp_path, {'goal_readiness': {'mode': 'require'}}, fake)
    assert out.startswith('READINESS REFUSED:') and 'Parked it' in out and calls == []
    assert fake.calls and fake.calls[0][:3] == ('record', '42', 'parked')
    assert '## Done when' in fake.calls[0][3] and fake.calls[0][4] == 'needs_decision'


def test_goal_readiness_start_invalid_mode_releases_the_claim(tmp_path, monkeypatch):
    fake = _FakeLoop()
    out, _ = _start_with(monkeypatch, tmp_path, {'goal_readiness': {'mode': 'nope'}}, fake)
    assert out.startswith('READINESS REFUSED:') and fake.calls and fake.calls[0][:2] == ('release', '42')


def test_goal_readiness_start_says_still_claimed_when_the_park_fails(tmp_path, monkeypatch):
    out, _ = _start_with(monkeypatch, tmp_path, {'goal_readiness': {'mode': 'require'}}, _FakeLoop(fail=True))
    assert out.startswith('READINESS REFUSED:') and 'STILL CLAIMED' in out and 'parked' in out


def test_goal_readiness_start_warn_records_nothing(tmp_path, monkeypatch):
    fake = _FakeLoop()
    _start_with(monkeypatch, tmp_path, {}, fake, body=GOOD)
    _start_with(monkeypatch, tmp_path, {}, fake)
    assert fake.calls == []


def test_goal_readiness_and_acceptance_refusals_exit_4_through_main(monkeypatch, capsys):
    """m3: the exit-4 mapping of both new prefixes, through `work.main` (the documented gesture's entry point)."""
    work = load('work')

    class _Quiet:
        def __getattr__(self, name):
            return lambda *a, **k: None
    real_load = work._load
    monkeypatch.setattr(work, '_load', lambda name: _Quiet() if name == 'loop' else real_load(name))
    monkeypatch.setattr(work.state, 'load_config', lambda sdlc: {'work': {'enabled': True}})
    monkeypatch.setattr(work, '_record', lambda sdlc, goal: {'worktree': '/nonexistent'})
    for verb, prefix in (('start', 'READINESS REFUSED: x'), ('commit', 'ACCEPTANCE REFUSED: x'),
                         ('pr', 'ACCEPTANCE REFUSED: x')):
        monkeypatch.setitem(work._COMMANDS, verb, lambda *a, _p=prefix, **k: _p)
        argv = ['work.py', verb, '.sdlc', '42'] + (['--message', 'm'] if verb == 'commit' else [])
        assert work.main(argv) == 4, verb
        monkeypatch.setitem(work._COMMANDS, verb, lambda *a, **k: 'fine')
        assert work.main(argv) == 0, verb
    capsys.readouterr()


# ---- T8: the promote seam ---------------------------------------------------------------------------------------

sys.path.insert(0, str(ROOT / 'tests'))
import test_promote as tp  # noqa: E402  (the real GitHubSource over an injectable runner; no network)

NO_CRIT_BODY = 'Just prose, nothing else.\n'


def _cfg(mode=None):
    cfg = tp._config()
    if mode:
        cfg['goal_readiness'] = {'mode': mode}
    return cfg


def _promote(mode, body, fail_read=False):
    p = tp._mod('promote')
    views = {'5': tp._view('sdlc:needs-confirmation', body=body)}
    run = tp._runner(views=views, fail_on=('issues/5',) if fail_read else ())
    return p.promote('.sdlc', _cfg(mode), ['5'], run=run), run


def test_goal_readiness_promote_warn_promotes_and_prints_message():
    result, run = _promote(None, NO_CRIT_BODY)
    row = result['results'][0]
    assert row['outcome'] == 'promoted' and 'not agent-ready' in row['detail'] and '`## Done when`' in row['detail']
    assert run.gql_calls or run.rest_writes             # the swap really went out


def test_goal_readiness_promote_require_refuses_and_leaves_labels_unchanged():
    result, run = _promote('require', NO_CRIT_BODY)
    row = result['results'][0]
    assert row['outcome'] == 'skipped' and '`## Done when`' in row['detail'] and 'labels unchanged' in row['detail']
    assert not run.gql_calls and not run.rest_writes    # nothing was written at all
    ok, run2 = _promote('require', 'Do it.\n\n## Done when\n- [ ] A.\n- [ ] B.\n- [ ] C.\n')
    assert ok['results'][0]['outcome'] == 'promoted'


def test_goal_readiness_promote_off_is_unchanged():
    result, _ = _promote('off', NO_CRIT_BODY)
    row = result['results'][0]
    assert row['outcome'] == 'promoted' and row['detail'] == '+sdlc:goal -sdlc:needs-confirmation'


def test_goal_readiness_promote_fetch_failure_does_not_promote_under_require():
    result, run = _promote('require', NO_CRIT_BODY, fail_read=True)
    assert result['results'][0]['outcome'] == 'failed'
    assert not run.gql_calls and not run.rest_writes


def test_goal_readiness_promote_invalid_mode_refuses_rather_than_defaulting():
    result, run = _promote('requre', NO_CRIT_BODY)
    assert result['results'][0]['outcome'] == 'skipped' and 'invalid goal_readiness' in result['results'][0]['detail']
    assert not run.gql_calls and not run.rest_writes


def test_goal_readiness_local_goal_has_only_pick_time_check():
    """D8: promote.py is GitHub-only; a local-file goal is covered by the pick verb and `work.py start`."""
    p = tp._mod('promote')
    out = p.promote('.sdlc', {'goal_readiness': {'mode': 'require'}}, ['5'], source=object())
    assert out['results'][0]['outcome'] == 'skipped' and 'github' in out['results'][0]['detail']
    assert 'D8' in (SCRIPTS / 'promote.py').read_text() or 'local-file goal has no promote gesture' in (SCRIPTS / 'promote.py').read_text()
