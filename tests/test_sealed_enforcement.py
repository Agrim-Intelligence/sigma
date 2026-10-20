"""#850 (slice 3 of #558): the seal is ENFORCED at `work.py commit` and `work.py pr`.

Layers: TestWrappers drives the two gate functions in-process against a REAL repo, linked worktree and bare
remote (git is the thing under test, so it is not faked; only the pytest runner is, where a test is about
ordering). TestDocumentedGesture runs the gesture the docs give: `python work.py commit|pr .sdlc <goal>`
with cwd = the repo root and the RELATIVE `.sdlc`, asserting stdout and the exit code (4).
The plan's controls table names which break turns which test red."""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pytest_python():
    """First interpreter that runs `-m pytest --version` in the scrubbed env seal() uses (None if none does)."""
    env = {k: v for k, v in load('bounded_run').unattended_env().items()
           if k not in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS')}
    home = tempfile.mkdtemp(prefix='home-')            # seal() runs the runner with a throwaway HOME
    env.update(PYTHONDONTWRITEBYTECODE='1', COLUMNS='240', HOME=home)
    try:
        for cand in dict.fromkeys(c for c in (sys.executable, shutil.which('python3'), shutil.which('python'),
                                           os.path.expanduser('~/.sigma-venv312/bin/python')) if c and os.path.exists(c)):
            try:
                if subprocess.run([cand, '-m', 'pytest', '--version'], capture_output=True, env=env).returncode == 0:
                    return cand
            except OSError:
                pass
        return None
    finally:
        shutil.rmtree(home, ignore_errors=True)


work = load('work')
loop_mod = load('loop')

GOAL = '850'
DIR = 'tests/acceptance/850'
FILE = DIR + '/test_acc_850_a.py'
SRC = 'def test_one():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n'
NODE = FILE + '::test_one'
WIN = sys.platform == 'win32'


def git(path, *args, check=True):
    return subprocess.run(['git', '-C', str(path), '-c', 'user.email=t@t', '-c', 'user.name=t',
                           '-c', 'commit.gpgsign=false', *args], check=check, capture_output=True, text=True)


class World:
    """repo (main checkout) + bare remote + linked worktree on sdlc/850 + `.sdlc` with a seal and work record."""

    def __init__(self, tmp_path, sealed=True):
        self.tmp = tmp_path
        self.proj = tmp_path / 'proj'
        self.proj.mkdir()
        self.remote = tmp_path / 'remote.git'
        self.wt = tmp_path / 'wt850'
        self.sdlc = self.proj / '.sdlc'
        git(self.proj, 'init', '-q', '-b', 'main')
        git(self.proj, 'config', '--local', 'user.email', 't@t')      # the gesture child strips GIT_*: identity must be repo-local
        git(self.proj, 'config', '--local', 'user.name', 't')
        git(self.proj, 'config', '--local', 'commit.gpgsign', 'false')
        (self.proj / 'pkg_mod.py').write_text('def f():\n    return 1\n')
        (self.proj / '.gitignore').write_text('.sdlc/\n')
        git(self.proj, 'add', '-A')
        git(self.proj, 'commit', '-q', '-m', 'base')
        subprocess.run(['git', 'init', '-q', '--bare', str(self.remote)], check=True, capture_output=True)
        git(self.proj, 'remote', 'add', 'origin', str(self.remote))
        git(self.proj, 'push', '-q', 'origin', 'main')
        git(self.proj, 'worktree', 'add', '-q', str(self.wt), '-b', 'sdlc/850')
        (self.wt / 'pkg_mod.py').write_text('def f():\n    return 2\n')
        (self.wt / DIR).mkdir(parents=True)
        (self.wt / FILE).write_text(SRC)
        git(self.wt, 'add', '-A')
        git(self.wt, 'commit', '-q', '-m', 'maker work + sealed tests')
        (self.sdlc / 'state' / 'work').mkdir(parents=True)
        (self.sdlc / 'state' / 'sealed').mkdir(parents=True)
        (self.sdlc / 'config.json').write_text(json.dumps({'work': {'enabled': True}}))
        work._save(str(self.sdlc), GOAL, {'worktree': str(self.wt), 'branch': 'sdlc/850', 'base': 'main',
                                          'remote': 'origin'})
        if sealed:
            self.write_record()

    def write_record(self, **over):
        data = SRC.encode()
        rec = {'schema': 'sigma-sealed/1', 'goal': GOAL, 'generation': 1, 'plan_hash': 'a' * 64,
               'files': {FILE: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}},
               'red': [{'test': NODE, 'kind': 'assertion', 'reason': 'AssertionError', 'evidence_id': 'x'}],
               'proof': {'python': pytest_python() or sys.executable}}
        rec.update(over)
        (self.sdlc / 'state' / 'sealed' / (GOAL + '.json')).write_text(json.dumps(rec))

    @property
    def rec(self):
        return work._record(str(self.sdlc), GOAL)

    def opt_in(self):
        git(self.proj, 'config', '--local', 'sigma.allowRepositoryShellCommands', 'true')

    def stage_all(self):
        git(self.wt, 'add', '-A')

    def staged(self):
        return git(self.wt, 'diff', '--cached', '--name-only').stdout.split()


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def recorder(real=None, fail=None):
    calls = []
    real = real or work._run

    def run(cwd, argv):
        calls.append(list(argv))
        if fail and fail in ' '.join(argv):
            raise RuntimeError('boom')
        return real(cwd, argv)
    run.calls = calls
    return run


def commit_gate(w, run=None):
    return work._sealed_commit_refusal(str(w.sdlc), w.rec, GOAL, run or work._run)


def pr_gate(w, config=None):
    return work._sealed_pr_refusal(str(w.sdlc), config if config is not None else {'work': {'enabled': True}},
                                   w.rec, GOAL)


@pytest.fixture
def spy_green(monkeypatch):
    seen = []

    def fake(root, sdlc_dir, stem, *, timeout, run=None):
        seen.append({'root': root, 'timeout': timeout})
        return {'green': True, 'failing': [], 'detail': ''}
    monkeypatch.setattr(work.sealed_tests, 'run_green', fake)
    return seen


class TestWrappers:
    # ------------------------------------------------------------------------------ commit
    def test_no_seal_record_is_a_passthrough_with_zero_git_calls(self, tmp_path):
        w = World(tmp_path, sealed=False)
        run = recorder()
        assert commit_gate(w, run) == '' and run.calls == []

    def test_a_clean_sealed_tree_passes(self, w):
        w.stage_all()
        assert commit_gate(w) == ''

    def test_a_bad_goal_stem_is_a_passthrough(self, w):
        assert work._sealed_commit_refusal(str(w.sdlc), w.rec, '../x', work._run) == ''

    def test_drift_is_refused_naming_path_and_kind_and_the_index_is_reset(self, w):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        w.stage_all()
        assert FILE in w.staged()
        out = commit_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'differs ' + FILE in out
        assert 'Nothing was committed. Only the index entries under tests/acceptance/850/ were reset' in out
        assert 'other staged paths remain staged' in out
        assert w.staged() == []

    def test_the_unstage_is_the_targeted_literal_reset(self, w):
        (w.wt / FILE).write_text(SRC + '# x\n')
        w.stage_all()
        run = recorder()
        commit_gate(w, run)
        assert ['git', 'reset', '-q', '--', ':(literal)' + DIR] in run.calls

    def test_a_failed_unstage_says_still_staged(self, w):
        (w.wt / FILE).write_text(SRC + '# x\n')
        w.stage_all()
        out = commit_gate(w, recorder(fail='reset'))
        assert out.startswith('SEALED REFUSED:') and 'STILL STAGED' in out

    def test_rename_out_of_a_sealed_file_is_caught_as_missing(self, w):
        git(w.wt, 'mv', FILE, 'moved_away.py')
        out = commit_gate(w)
        assert 'missing ' + FILE in out
        assert 'moved_away.py' in w.staged()          # outside the sealed dir: not ours to unstage

    def test_a_new_file_in_the_sealed_dir_is_unexpected(self, w):
        (w.wt / DIR / 'extra.py').write_text('x = 1\n')
        w.stage_all()
        out = commit_gate(w)
        assert 'unexpected ' + DIR + '/extra.py' in out and w.staged() == []

    def test_staged_paths_outside_the_sealed_dir_do_not_trigger_it(self, w):
        (w.wt / 'other.py').write_text('x = 1\n')
        w.stage_all()
        assert commit_gate(w) == ''

    @pytest.mark.parametrize('text', ['{nope', '[]', '{"schema": "other/1"}'])
    def test_a_corrupt_or_foreign_record_refuses_never_passes(self, w, text):
        (w.sdlc / 'state' / 'sealed' / (GOAL + '.json')).write_text(text)
        w.stage_all()
        out = commit_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'record' in out and 'park' in out

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_a_symlinked_sealed_directory_refuses(self, w):
        import shutil
        d = w.wt / DIR
        shutil.copytree(d, w.tmp / 'elsewhere')
        shutil.rmtree(d)
        d.symlink_to(w.tmp / 'elsewhere')
        w.stage_all()
        out = commit_gate(w)
        assert out and DIR in out

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_a_symlinked_record_refuses(self, w):
        real = w.sdlc / 'state' / 'sealed' / (GOAL + '.json')
        moved = w.sdlc / 'state' / 'sealed' / 'real.json'
        real.rename(moved)
        real.symlink_to(moved)
        assert commit_gate(w).startswith('SEALED REFUSED:')

    def test_a_raising_check_is_a_refusal_not_an_exception_and_still_unstages(self, w, monkeypatch):
        (w.wt / FILE).write_text(SRC + '# x\n')
        w.stage_all()

        def boom(*a, **k):
            raise ValueError('unexpected shape')
        monkeypatch.setattr(work.sealed_tests, 'check', boom)
        out = commit_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'unexpected shape' in out
        assert w.staged() == []

    def test_each_kind_gets_its_own_remedy_text(self, w):
        (w.wt / FILE).write_text(SRC + '# x\n')
        (w.wt / DIR / 'extra.py').write_text('x = 1\n')
        w.stage_all()
        out = commit_gate(w)
        assert 'restore --source=' in out and 'rm -f' in out

    # ------------------------------------------------------------------------------ pr
    def test_pr_no_record_is_a_passthrough_that_never_looks(self, tmp_path, monkeypatch):
        w = World(tmp_path, sealed=False)
        monkeypatch.setattr(work.sealed_tests, 'check_tree', lambda *a, **k: pytest.fail('looked'))
        monkeypatch.setattr(work.sealed_tests, 'run_green', lambda *a, **k: pytest.fail('ran'))
        assert pr_gate(w) == ''

    def test_pr_clean_and_green_passes(self, w, spy_green):
        w.opt_in()
        assert pr_gate(w) == '' and len(spy_green) == 1

    def test_pr_catches_a_raw_git_commit_bypass_naming_each_path(self, w, spy_green):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        (w.wt / DIR / 'extra.py').write_text('x = 1\n')
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw git bypass')
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED:') and out.endswith('(nothing pushed)')
        assert 'differs ' + FILE in out and 'unexpected ' + DIR + '/extra.py' in out
        assert 'restore --source=' in out
        assert spy_green == []                         # tree-compare comes BEFORE the green run

    def test_pr_reads_head_not_the_working_tree(self, w, spy_green):
        git(w.wt, 'rm', '-q', FILE)
        git(w.wt, 'commit', '-q', '-m', 'rm')
        (w.wt / DIR).mkdir(parents=True, exist_ok=True)
        (w.wt / FILE).write_text(SRC)                  # back on disk, untracked: the working tree looks fine
        out = pr_gate(w)
        assert 'missing ' + FILE in out and spy_green == []

    def test_pr_working_tree_guard_refuses_an_ignored_conftest(self, w, spy_green):
        w.opt_in()
        (w.proj / '.git' / 'info').mkdir(exist_ok=True)
        (w.proj / '.git' / 'info' / 'exclude').write_text('conftest.py\n')
        (w.wt / DIR / 'conftest.py').write_text('import os\n')
        assert git(w.wt, 'status', '--porcelain').stdout == ''     # invisible to porcelain, as designed
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'unexpected ' + DIR + '/conftest.py' in out
        assert spy_green == []

    def test_pr_requires_the_shell_policy_opt_in_and_names_it_not_parking(self, w, spy_green):
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED: shell-policy opt-in missing')
        assert 'sigma.allowRepositoryShellCommands' in out and 'NOT parking' in out
        assert spy_green == []

    def test_pr_names_the_failing_test(self, w, monkeypatch):
        w.opt_in()
        monkeypatch.setattr(work.sealed_tests, 'run_green', lambda *a, **k: {
            'green': False, 'failing': [{'test': NODE, 'why': 'AssertionError'}], 'detail': ''})
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'FAILED ' + NODE + ' - AssertionError' in out
        assert out.endswith('(nothing pushed)')

    def test_pr_a_runner_refusal_is_a_sealed_refusal(self, w, monkeypatch):
        w.opt_in()

        def refuse(*a, **k):
            raise work.sealed_tests.SealRefused('timeout', 'the budget ran out')
        monkeypatch.setattr(work.sealed_tests, 'run_green', refuse)
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'budget' in out

    def test_pr_a_raising_check_tree_is_a_refusal_not_an_exception(self, w, monkeypatch):
        def boom(*a, **k):
            raise ValueError('weird')
        monkeypatch.setattr(work.sealed_tests, 'check_tree', boom)
        out = pr_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'weird' in out

    def test_pr_a_corrupt_record_refuses(self, w, spy_green):
        (w.sdlc / 'state' / 'sealed' / (GOAL + '.json')).write_text('{nope')
        assert pr_gate(w).startswith('SEALED REFUSED:') and spy_green == []

    def test_commit_and_pr_say_the_same_thing_about_the_same_problem(self, w, spy_green):
        (w.wt / FILE).write_text(SRC + '# x\n')
        git(w.wt, 'add', '-A')
        c = commit_gate(w).splitlines()
        git(w.wt, 'add', '-A')                         # the gate reset the index; the raw bypass stages again
        git(w.wt, 'commit', '-q', '-m', 'raw')
        p = pr_gate(w).splitlines()
        line = [ln for ln in c if ln.strip().startswith('differs ')]
        assert line and line == [ln for ln in p if ln.strip().startswith('differs ')]

    def test_every_problem_shares_one_probe_budget(self, w, spy_green, monkeypatch):
        (w.wt / FILE).write_text(SRC + '# x\n')
        (w.wt / DIR / 'extra_a.py').write_text('x = 1\n')
        (w.wt / DIR / 'extra_b.py').write_text('x = 1\n')
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw')
        budgets = []
        real = work.sealed_tests.remedy
        monkeypatch.setattr(work.sealed_tests, 'remedy',
                            lambda problem, root, rec, budget=None: (budgets.append(budget), real(problem, root, rec, budget))[1])
        pr_gate(w)
        assert len(budgets) == 3 and all(b is budgets[0] and isinstance(b, dict) for b in budgets)

    def test_the_timeout_is_derived_from_config(self, w, spy_green):
        w.opt_in()
        pr_gate(w, {'work': {'enabled': True}, 'verify': {'timeout_minutes': 5}})
        assert spy_green[-1]['timeout'] == 300

    @pytest.mark.parametrize('config', [{}, {'verify': {'timeout_minutes': 5}}, {'verify': {'timeout_minutes': 0}},
                                        {'verify': {'timeout_minutes': 1441}}, {'verify': {'timeout_minutes': True}},
                                        {'verify': {'timeout_minutes': '9'}}, {'verify': {'timeout_minutes': 1440}}])
    def test_the_timeout_rule_equals_the_verify_rule(self, config):
        assert work._sealed_timeout_seconds(config) == loop_mod._verify_timeout_seconds(config)


class TestPlantedBytecode:
    """A gitignored, header-matching pytest rewrite cache in the sealed dir must not turn a failing test green."""

    @pytest.mark.skipif(WIN, reason='posix fixtures')
    def test_a_planted_always_pass_pyc_does_not_make_a_failing_sealed_test_green(self, w):
        import importlib.util as iu
        import marshal
        import struct
        from _pytest.assertion.rewrite import PYTEST_TAG
        w.opt_in()
        (w.wt / 'pkg_mod.py').write_text('def f():\n    return 1\n')           # the code under test is wrong
        git(w.wt, 'commit', '-q', '-am', 'regress')
        (w.proj / '.git' / 'info' / 'exclude').write_text('__pycache__/\n')
        cfg = {'work': {'enabled': True}}
        before = pr_gate(w, cfg)
        assert before.startswith('SEALED REFUSED:') and 'FAILED ' + NODE in before     # refused with no cache
        src = w.wt / FILE
        info = src.stat()
        code = compile('def test_one():\n    pass\n', str(src), 'exec')
        pyc = src.parent / '__pycache__' / ('%s.%s.pyc' % (src.stem, PYTEST_TAG))
        pyc.parent.mkdir()
        pyc.write_bytes(iu.MAGIC_NUMBER + b'\0\0\0\0' + struct.pack('<LL', int(info.st_mtime) & 0xFFFFFFFF,
                                                                   info.st_size & 0xFFFFFFFF) + marshal.dumps(code))
        after = pr_gate(w, cfg)
        assert after.startswith('SEALED REFUSED:') and 'FAILED ' + NODE in after


class TestRegistry:
    def test_both_gates_are_registered_with_the_right_function(self):
        funcs = {g['function'] for g in work.ENFORCEMENT_GATES}
        assert {'_sealed_commit_refusal', '_sealed_pr_refusal'} <= funcs


# ---------------------------------------------------------------------------------- documented gesture

def gesture(w, verb, *extra, env_extra=None):
    """`python work.py <verb> .sdlc 850` with cwd = repo root and the RELATIVE sdlc dir, as the docs say."""
    bindir = w.tmp / 'bin'
    bindir.mkdir(exist_ok=True)
    fake_gh = bindir / 'gh'
    fake_gh.write_text('#!/bin/sh\nexit 1\n')
    fake_gh.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('GIT_', 'SIGMA_', 'CLAUDE'))}
    env['PATH'] = str(bindir) + os.pathsep + env.get('PATH', '')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env.update(env_extra or {})
    done = subprocess.run([sys.executable, str(SCRIPTS / 'work.py'), verb, '.sdlc', GOAL, *extra],
                          cwd=str(w.proj), capture_output=True, text=True, env=env, timeout=300)
    return done


@pytest.mark.skipif(WIN, reason='posix fixtures')
class TestDocumentedGesture:
    def test_commit_refuses_a_drifted_sealed_file_exit_4_and_resets_the_index(self, w):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        done = gesture(w, 'commit')
        assert done.returncode == 4, done.stderr
        assert 'SEALED REFUSED:' in done.stdout and FILE in done.stdout
        assert w.staged() == []
        assert git(w.wt, 'log', '--oneline').stdout.count('\n') == 2        # nothing was committed

    def test_commit_with_a_clean_sealed_dir_commits_as_before(self, w):
        (w.wt / 'other.py').write_text('x = 1\n')
        done = gesture(w, 'commit', '--message', 'feat: other')
        assert done.returncode == 0 and 'committed on sdlc/850' in done.stdout, done.stderr + done.stdout
        assert 'SEALED REFUSED' not in done.stdout

    def test_commit_for_a_goal_with_no_seal_record_is_untouched(self, tmp_path):
        w = World(tmp_path, sealed=False)
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))              # same edit, but nothing is sealed
        done = gesture(w, 'commit', '--message', 'feat: edit')
        assert done.returncode == 0 and 'committed on sdlc/850' in done.stdout
        assert 'SEALED REFUSED' not in done.stdout

    def test_pr_catches_a_raw_git_commit_exit_4_before_any_push(self, w):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw')
        done = gesture(w, 'pr')
        assert done.returncode == 4, done.stderr + done.stdout
        assert 'SEALED REFUSED:' in done.stdout and 'differs ' + FILE in done.stdout
        assert subprocess.run(['git', '-C', str(w.remote), 'branch', '--list', 'sdlc/850'],
                              capture_output=True, text=True).stdout.strip() == ''     # nothing pushed

    def test_a_no_op_commit_stays_a_no_op_even_when_head_holds_tampered_sealed_bytes(self, w):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw')
        done = gesture(w, 'commit')
        assert done.returncode == 0 and done.stdout.strip() == 'nothing to commit'     # `pr` is what catches HEAD

    def test_pr_with_no_tests_set_still_refuses_a_sealed_goal(self, w):
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw')
        done = gesture(w, 'pr', '--no-tests', 'a reason')
        assert done.returncode == 4 and 'SEALED REFUSED:' in done.stdout

    def test_pr_without_the_shell_policy_opt_in_exit_4_naming_the_opt_in(self, w):
        done = gesture(w, 'pr')
        assert done.returncode == 4, done.stderr + done.stdout
        assert 'shell-policy opt-in missing' in done.stdout and 'NOT parking' in done.stdout

    def test_pr_runs_the_sealed_tests_and_refuses_a_red_one_naming_it(self, w):
        w.opt_in()
        (w.wt / 'pkg_mod.py').write_text('def f():\n    return 1\n')
        git(w.wt, 'commit', '-q', '-am', 'regress')
        done = gesture(w, 'pr')
        assert done.returncode == 4, done.stderr + done.stdout
        assert 'SEALED REFUSED:' in done.stdout and 'FAILED ' + NODE in done.stdout

    def test_pr_green_passes_the_seal_gates_and_leaves_no_debris(self, w):
        w.opt_in()
        done = gesture(w, 'pr')
        assert 'SEALED REFUSED' not in done.stdout + done.stderr
        assert git(w.wt, 'status', '--porcelain').stdout == ''
        assert not list((w.wt / DIR).rglob('__pycache__'))

    def test_pr_for_a_goal_with_no_seal_record_is_untouched(self, tmp_path):
        w = World(tmp_path, sealed=False)
        done = gesture(w, 'pr')
        assert 'SEALED REFUSED' not in done.stdout + done.stderr


@pytest.mark.skipif(WIN, reason='posix fixtures')
class TestControls:
    """#855: clauses the #850 tests did not each cover alone. Every test here names, in its docstring, the single
    clause whose break turns it red; `docs/sealed-tests-controls.md` records the observed red run."""

    def test_sealed_is_checked_before_secrets_and_its_paths_are_unstaged(self, w):
        """Order clause (sealed before `_secret_refusal`): drift AND a secret-shaped file in one commit must report
        the sealed refusal and reset the sealed paths, not stop at the secret refusal with the drift still staged."""
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        (w.wt / '.env').write_text('API_TOKEN=abcdefghijklmnop0123456789\n')
        done = gesture(w, 'commit', '--message', 'feat: both')
        assert done.returncode == 4 and 'SEALED REFUSED:' in done.stdout, done.stdout + done.stderr
        assert FILE not in w.staged()

    def test_a_foreign_schema_with_well_formed_files_is_still_a_record_problem(self, w):
        """`_existing` schema clause alone: a record that parses and carries a right-looking `files` map but
        names another schema must refuse (the `files` shape check would otherwise be the only thing refusing)."""
        path = w.sdlc / 'state' / 'sealed' / (GOAL + '.json')
        rec = json.loads(path.read_text())
        rec['schema'] = 'other/1'
        path.write_text(json.dumps(rec))
        out = commit_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'record' in out

    def test_an_executable_bit_alone_is_refused_on_the_gesture(self, w):
        """`check` exec-bit clause: chmod +x with identical bytes."""
        os.chmod(w.wt / FILE, 0o755)
        done = gesture(w, 'commit', '--message', 'chore: mode')
        assert done.returncode == 4 and 'differs ' + FILE in done.stdout, done.stdout + done.stderr
        assert 'chmod 644' in done.stdout

    def test_verify_debris_in_the_sealed_dir_does_not_refuse(self, w):
        """`is_debris` tolerance clause: `.pytest_cache` and `__pycache__/x.pyc` after a verify run are not unexpected."""
        (w.wt / DIR / '.pytest_cache').mkdir()
        (w.wt / DIR / '.pytest_cache' / 'v').write_text('1')
        (w.wt / DIR / '__pycache__').mkdir()
        (w.wt / DIR / '__pycache__' / 'x.pyc').write_bytes(b'\0')
        w.stage_all()
        assert commit_gate(w) == ''

    def test_a_non_pyc_file_in_pycache_is_unexpected(self, w):
        """`is_debris` strictness clause: only `*.pyc` directly under `__pycache__` is debris."""
        (w.wt / DIR / '__pycache__').mkdir()
        (w.wt / DIR / '__pycache__' / 'notes.txt').write_text('x')
        out = commit_gate(w)
        assert out.startswith('SEALED REFUSED:') and 'unexpected ' + DIR + '/__pycache__' in out

    def test_pr_names_the_committed_tree_first_even_when_every_later_check_would_also_fail(self, w, monkeypatch):
        """Order clauses of `_sealed_pr_refusal` (HEAD tree < working tree < opt-in < green run), each weakened to
        membership by this one case: HEAD tampered, the working tree tampered differently, no opt-in, a red runner.
        The message must be the HEAD-tree one and the runner must never be asked."""
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 3'))
        git(w.wt, 'add', '-A')
        git(w.wt, 'commit', '-q', '-m', 'raw')
        (w.wt / FILE).write_text(SRC.replace('== 2', '== 4'))
        ran = []
        monkeypatch.setattr(work.sealed_tests, 'run_green', lambda *a, **k: ran.append(1) or {
            'green': False, 'failing': [{'test': NODE, 'why': 'x'}], 'detail': ''})
        out = pr_gate(w)
        assert 'the committed tree at HEAD differs' in out and 'working tree' not in out
        assert ran == []

    def test_pr_names_the_working_tree_before_the_opt_in_and_the_opt_in_before_the_green_run(self, w, monkeypatch):
        """Order clauses, second half: working tree differs + no opt-in -> the working-tree message; clean tree + no
        opt-in -> the opt-in message with the runner never asked."""
        ran = []
        monkeypatch.setattr(work.sealed_tests, 'run_green', lambda *a, **k: ran.append(1) or {
            'green': False, 'failing': [{'test': NODE, 'why': 'x'}], 'detail': ''})
        (w.wt / DIR / 'extra.py').write_text('x = 1\n')
        assert 'the working tree (pytest runs it) differs' in pr_gate(w)
        (w.wt / DIR / 'extra.py').unlink()
        assert pr_gate(w).startswith('SEALED REFUSED: shell-policy opt-in missing')
        assert ran == []


@pytest.mark.skipif(WIN, reason='posix fixtures')
class TestRetireGesture:
    """#855: `work.py seal-retire .sdlc <goal> --reason ...` and the fold it exists for, on the documented gesture."""

    def fold(self, w):
        """The maker's move: the sealed test folded into an owner test file, the sealed dir deleted."""
        git(w.wt, 'rm', '-q', '-r', '--', DIR)
        (w.wt / 'tests').mkdir(exist_ok=True)
        (w.wt / 'tests' / 'test_owner.py').write_text(SRC)

    def test_a_missing_reason_is_a_usage_error_exit_2(self, w):
        done = gesture(w, 'seal-retire')
        assert done.returncode == 2 and 'usage: work.py seal-retire' in done.stderr
        assert (w.sdlc / 'state' / 'sealed' / (GOAL + '.json')).exists()

    def test_an_unsealed_goal_is_refused_exit_4(self, tmp_path):
        w = World(tmp_path, sealed=False)
        done = gesture(w, 'seal-retire', '--reason', 'folded')
        assert done.returncode == 4 and 'seal-retire: REFUSED [not-sealed]' in done.stderr

    def test_a_blank_reason_is_refused_exit_4(self, w):
        done = gesture(w, 'seal-retire', '--reason', '   ')
        assert done.returncode == 4 and 'REFUSED [no-reason]' in done.stderr

    def test_the_happy_path_exit_0_prints_the_tombstone_and_works_with_the_gate_off(self, w):
        assert 'sealed_acceptance' not in (w.sdlc / 'config.json').read_text()          # gate off: cleanup still works
        done = gesture(w, 'seal-retire', '--reason', 'folded into the owner file')
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout)['reason'] == 'folded into the owner file'
        assert not (w.sdlc / 'state' / 'sealed' / (GOAL + '.json')).exists()
        assert (w.sdlc / 'state' / 'sealed' / (GOAL + '.retired.json')).exists()

    def test_it_appends_an_action_log_row_with_the_reason(self, w):
        (w.sdlc / 'config.json').write_text(json.dumps({'work': {'enabled': True}, 'action_log': {'enabled': True}}))
        done = gesture(w, 'seal-retire', '--reason', 'folded for the log')
        assert done.returncode == 0, done.stderr
        log = (w.sdlc / 'state' / 'log' / (GOAL + '.jsonl')).read_text()
        assert 'sealed acceptance retired' in log and 'folded for the log' in log

    def test_the_fold_commit_is_refused_before_retire_and_lands_after_it(self, w):
        self.fold(w)
        before = gesture(w, 'commit', '--message', 'refactor: fold sealed tests')
        assert before.returncode == 4 and 'SEALED REFUSED:' in before.stdout
        done = gesture(w, 'seal-retire', '--reason', 'folded into tests/test_owner.py')
        assert done.returncode == 0, done.stderr
        after = gesture(w, 'commit', '--message', 'refactor: fold sealed tests')
        assert after.returncode == 0 and 'committed on sdlc/850' in after.stdout, after.stdout + after.stderr
        assert 'SEALED REFUSED' not in after.stdout
        assert not (w.wt / DIR).exists() and (w.wt / 'tests' / 'test_owner.py').exists()


def test_review_skill_names_maker_edit_of_sealed_test_as_bug():
    """AC4: the review skill states sealed tests are the acceptance proof the maker must not edit."""
    text = (SCRIPTS.parents[1] / 'sigma-review/SKILL.md').read_text(encoding='utf-8')
    assert 'maker edit to a sealed test (the acceptance proof): **Bug**' in text
