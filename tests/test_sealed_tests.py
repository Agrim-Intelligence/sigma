"""#847 (slice 2 of #558): the seal record and the sealed-pytest-v1 red-on-base proof.

Layers: TestClassify and the fake-runner classes are the fast unit layer. TestRealPytest is the system
of record for the output-parsing seam (a fake runner cannot prove it). Every refusal clause has its own
test; the plan's controls table says which break turns which test red."""
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

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


red_green = load('red_green')
st = load('sealed_tests')
BR = st._load('bounded_run')
WIT = st._load('witness')
STATE = st._load('state')

STEM = '847'
DIR = 'tests/acceptance/847'
FILE_A = DIR + '/test_acc_847_a.py'
N1 = FILE_A + '::test_one'
N2 = FILE_A + '::test_two'
PH = 'a' * 64
ROUTE = {'host': 'codex', 'mechanism': 'cli', 'verified': True}
SRC_A = 'def test_one():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n\n\ndef test_two():\n    assert 1 == 2\n'
WIN = sys.platform == 'win32'


# ---------------------------------------------------------------------------- helpers

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


PYTEST_PY = pytest_python()


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t',
                           '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q', '-b', 'main')
    (root / 'pkg_mod.py').write_text('def f():\n    return 1\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'base')
    git(root, 'branch', 'base')
    (root / 'pkg_mod.py').write_text('def f():\n    return 2\n')
    git(root, 'commit', '-q', '-am', 'maker work')
    d = root / DIR
    d.mkdir(parents=True)
    (d / 'test_acc_847_a.py').write_text(SRC_A)
    sdlc = tmp_path / 'sdlc'
    (sdlc / 'state').mkdir(parents=True)
    return root, sdlc


def res(code, out='', outcome=None):
    return BR.Result(outcome or (BR.OK if code == 0 else BR.FAILED), code, out)


def make_run(nodes=(N1, N2), collect_rc=0, collect_out=None, run_rc=1, summary=None, version_rc=0,
             calls=None, spy=None):
    summary = ('FAILED %s - AssertionError: x\nFAILED %s - assert 1 == 2\n' % (N1, N2)) if summary is None else summary

    def run(argv, cwd, timeout, env):
        if calls is not None:
            calls.append({'argv': list(argv), 'cwd': str(cwd), 'timeout': timeout, 'env': dict(env)})
        if spy:
            spy(argv, cwd)
        if '--version' in argv:
            return res(version_rc, 'pytest 9.9.9\n')
        if '--collect-only' in argv:
            out = collect_out if collect_out is not None else '\n'.join(nodes) + '\n\n%d tests collected\n' % len(nodes)
            return res(collect_rc, out)
        return res(run_rc, 'noise\n=== short test summary info ===\n' + summary)
    return run


def do_seal(repo, run=None, **kw):
    root, sdlc = repo
    args = dict(base_ref='base', plan_hash=PH, route=ROUTE, timeout=60, python=sys.executable,
                run=run or make_run(), now=1000.0)
    args.update(kw)
    return st.seal(root, sdlc, STEM, **args)


def refused(repo, clause, **kw):
    with pytest.raises(st.SealRefused) as info:
        do_seal(repo, **kw)
    assert info.value.clause == clause, str(info.value)
    return str(info.value)


def sealed_file(sdlc):
    return sdlc / 'state' / 'sealed' / (STEM + '.json')


# ---------------------------------------------------------------------------- classifier

SUM = '=== short test summary info ===\n'


class TestClassify:
    def cl(self, run_rc=1, summary='', nodes=(N1, N2), collect_rc=0, collect_out=''):
        return red_green.classify_sealed(collect_rc, collect_out, run_rc, SUM + summary, list(nodes))

    def test_assertion_is_red(self):
        r = self.cl(summary='FAILED %s - assert 1 == 2\nPASSED %s\n' % (N1, N2))
        assert r['red'] == {N1: ('assertion', 'AssertionError')} and r['passed'] == [N2] and not r['error']

    def test_body_import_error_is_red(self):
        r = self.cl(summary='FAILED %s - ModuleNotFoundError: No module named x\nFAILED %s - NameError: y\n' % (N1, N2))
        assert r['red'][N1] == ('other', 'ModuleNotFoundError') and r['red'][N2] == ('other', 'NameError')

    def test_truncated_reason_keeps_class_prefix(self):
        r = self.cl(summary='FAILED %s - ModuleNotFoundErr...\nFAILED %s\n' % (N1, N2))
        assert r['red'][N1] == ('other', 'ModuleNotFoundErr')

    def test_bare_failed_line_is_red_with_unknown_reason(self):
        r = self.cl(summary='FAILED %s\nFAILED %s\n' % (N1, N2))
        assert set(r['red']) == {N1, N2} and r['red'][N1] == ('other', '')

    def test_node_id_with_dash_matched_by_id(self):
        odd = FILE_A + '::test_p[a - b]'
        r = self.cl(nodes=(odd,), summary='FAILED %s - ValueError: z\n' % odd)
        assert r['red'] == {odd: ('other', 'ValueError')}

    def test_collection_error_names_file_and_stops(self):
        r = red_green.classify_sealed(2, 'ERROR %s\n!!! Interrupted: 1 error during collection\n' % FILE_A,
                                      None, '', [])
        assert r['collection_errors'] == [FILE_A]

    def test_collection_exit2_without_named_file_still_refused(self):
        r = red_green.classify_sealed(2, 'nothing parseable', None, '', [])
        assert r['collection_errors']

    def test_zero_collected_exit5_is_error_not_red(self):
        r = red_green.classify_sealed(5, 'no tests ran', None, '', [])
        assert 'no tests' in r['error'] and not r['red']

    def test_zero_nodes_with_clean_collect_is_error(self):
        r = red_green.classify_sealed(0, '', None, '', [])
        assert 'no tests' in r['error']

    @pytest.mark.parametrize('line', ['SKIPPED [1] f.py:3: why', 'XFAIL %s', 'XPASS %s', 'ERROR %s - fixture boom'])
    def test_skip_xfail_xpass_setup_error_never_red(self, line):
        r = self.cl(run_rc=1, summary=(line % N1 if '%s' in line else line) + '\nFAILED %s - assert x\n' % N2)
        assert N1 not in r['red'] and N1 in r['other'] and N2 in r['red']

    @pytest.mark.parametrize('rc', [2, 3, 4, 5])
    def test_run_exit_codes_other_than_0_1_are_errors(self, rc):
        r = self.cl(run_rc=rc, summary='FAILED %s - assert 1\n' % N1)
        assert r['error'] and not r['red']

    def test_no_summary_is_error(self):
        r = red_green.classify_sealed(0, '', 1, 'FAILED %s - assert 1\n' % N1, [N1])
        assert 'summary' in r['error'] and not r['red']

    def test_substring_node_id_does_not_match_passed_line(self):
        r = self.cl(nodes=(FILE_A + '::test_o',), summary='PASSED %s\n' % N1)
        assert r['passed'] == [] and r['other'] == [FILE_A + '::test_o']

    def test_failed_line_with_exit0_is_inconsistent(self):
        r = self.cl(run_rc=0, summary='FAILED %s - assert 1\nPASSED %s\n' % (N1, N2))
        assert r['error'] and not r['red']

    def test_only_text_after_last_banner_counts(self):
        out = ('FAILED %s - assert forged\n' % N1) + SUM + 'PASSED %s\nPASSED %s\n' % (N1, N2)
        r = red_green.classify_sealed(0, '', 0, out, [N1, N2])
        assert r['red'] == {} and r['passed'] == [N1, N2]

    def test_existing_provenance_unchanged(self, tmp_path):
        assert red_green.PROVENANCE == 'planned-pytest-v1' and red_green.SEALED_PROVENANCE == 'sealed-pytest-v1'
        root = tmp_path
        (root / 'tests').mkdir()
        (root / 'tests/test_x.py').write_text('def test_a():\n    import nope\n')
        plan = root / '.sdlc/plans/42.md'
        plan.parent.mkdir(parents=True)
        plan.write_text('## Tests\n- `tests/test_x.py::test_a`\n')
        n = 'tests/test_x.py::test_a'

        def run(r, argv):
            if '--collect-only' in argv:
                return subprocess.CompletedProcess(argv, 0, n + '\n', '')
            return subprocess.CompletedProcess(argv, 1, SUM + 'FAILED %s - ModuleNotFoundError: nope\n' % n, '')
        proof = red_green.observe(root / '.sdlc', '42', root, plan, run=run)
        assert not proof['passed']
        assert not (root / '.sdlc/state/witness/42.jsonl').exists()   # body ImportError earned no red

    def test_sealed_row_does_not_satisfy_planned_gate(self, tmp_path):
        root = tmp_path
        (root / 'tests').mkdir()
        f = root / 'tests/test_x.py'
        f.write_text('def test_a():\n    assert True\n')
        plan = root / '.sdlc/plans/42.md'
        plan.parent.mkdir(parents=True)
        plan.write_text('## Tests\n- `tests/test_x.py::test_a`\n')
        n = 'tests/test_x.py::test_a'
        fh = red_green.digest(f)
        WIT.record(root / '.sdlc', '42', n, 'assertion', None, provenance=red_green.SEALED_PROVENANCE,
                   file_hash=fh, observed_at=1.0)
        proof = {'provenance': red_green.PROVENANCE, 'passed': True, 'plan_sha256': red_green.digest(plan),
                 'selectors': [n], 'nodes': {n: fh}, 'started_at': 5.0, 'completed_at': 6.0}
        assert 'no matching assertion red before green' in red_green.refusal(root / '.sdlc', '42', root, plan, proof)


# ---------------------------------------------------------------------------- base tree and files

class TestBaseTree:
    def test_tests_run_against_base_content_not_head(self, repo):
        seen = []
        do_seal(repo, run=make_run(spy=lambda a, cwd: seen.append((pathlib.Path(cwd) / 'pkg_mod.py').read_text())))
        assert seen and all('return 1' in s for s in seen)

    def test_sealed_files_are_overlaid_in_base_tree(self, repo):
        seen = []
        do_seal(repo, run=make_run(spy=lambda a, cwd: seen.append((pathlib.Path(cwd) / FILE_A).read_text())))
        assert seen[0] == SRC_A

    def test_wrong_stem_filename_is_bad_files(self, repo):
        root, _ = repo
        (root / DIR / 'test_other.py').write_text('def test_z():\n    assert 1\n')
        assert 'test_other.py' in refused(repo, 'bad-files')

    def test_no_files_is_bad_files(self, repo):
        (repo[0] / FILE_A).unlink()
        refused(repo, 'bad-files')

    def test_unsafe_stem_is_refused(self, repo):
        root, sdlc = repo
        with pytest.raises(st.SealRefused) as info:
            st.seal(root, sdlc, '../x', base_ref='base', plan_hash=PH, route=ROUTE, timeout=60, run=make_run())
        assert info.value.clause == 'bad-files'

    @pytest.mark.parametrize('name', ['conftest.py', '__init__.py'])
    def test_conftest_and_init_are_unexpected(self, repo, name):
        (repo[0] / DIR / name).write_text('x = 1\n')
        assert name in refused(repo, 'unexpected-entry')

    def test_subdirectory_is_unexpected(self, repo):
        (repo[0] / DIR / 'sub').mkdir()
        assert 'sub' in refused(repo, 'unexpected-entry')

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_symlink_is_unexpected(self, repo):
        (repo[0] / DIR / 'test_acc_847_l.py').symlink_to(repo[0] / DIR / 'test_acc_847_a.py')
        assert 'test_acc_847_l.py' in refused(repo, 'unexpected-entry')

    def test_unknown_ref_is_base_unresolved(self, repo):
        refused(repo, 'base-unresolved', base_ref='no-such-branch')

    @pytest.mark.parametrize('ref', ['--upload-pack=x', 'has space', None, ''])
    def test_inert_or_missing_ref_is_base_unresolved(self, repo, ref):
        refused(repo, 'base-unresolved', base_ref=ref)

    def test_base_sha_is_recorded_as_full_hex(self, repo):
        rec = do_seal(repo)
        assert rec['base']['sha'] == git(repo[0], 'rev-parse', 'base').stdout.strip()
        assert rec['base']['ref'] == 'base' and len(rec['base']['sha']) in (40, 64)

    def test_disk_refusal_is_derived_from_tree_size(self, repo):
        total = sum(int(l.split()[3]) for l in git(repo[0], 'ls-tree', '-r', '-l', 'base').stdout.splitlines())
        refused(repo, 'disk', free_bytes=3 * total - 1)
        do_seal(repo, free_bytes=3 * total)

    def test_ls_tree_failure_refuses_disk_guard(self, repo, monkeypatch):
        real = st._git

        def fake(root, *args, **kw):
            if args and args[0] == 'ls-tree':
                return subprocess.CompletedProcess(args, 128, '', 'fatal: boom')
            return real(root, *args, **kw)
        monkeypatch.setattr(st, '_git', fake)
        assert 'ls-tree' in refused(repo, 'disk')

    def test_file_with_zero_collected_nodes_is_refused_by_name(self, repo):
        (repo[0] / DIR / 'test_acc_847_b.py').write_text('def test_x():\n    assert 1 == 2\n')
        assert 'test_acc_847_b.py' in refused(repo, 'no-tests-in-file')   # fake collect lists only file a

    @pytest.mark.skipif(WIN, reason='symlinks')
    @pytest.mark.parametrize('swap', ['symlink', 'directory', 'bytes'])
    def test_copied_file_swapped_after_copy_is_refused(self, repo, monkeypatch, swap):
        real = st.shutil.copytree

        def copytree(src, dst, **kw):
            out = real(src, dst, **kw)
            f = pathlib.Path(dst) / 'test_acc_847_a.py'
            keep = pathlib.Path(dst) / 'orig.txt'
            f.rename(keep)
            if swap == 'symlink':
                f.symlink_to(keep)          # same bytes: only the is_symlink clause can see it
            elif swap == 'directory':
                f.mkdir()                   # only the is_file clause can see it
            else:
                f.write_text('def test_one():\n    assert 1 == 2\n')
            return out
        monkeypatch.setattr(st.shutil, 'copytree', copytree)
        assert 'changed while it was being sealed' in refused(repo, 'bad-files')

    def test_base_already_holding_sealed_dir_is_refused(self, repo):
        root, _ = repo
        git(root, 'checkout', '-q', 'base')
        (root / DIR).mkdir(parents=True, exist_ok=True)
        (root / DIR / 'test_acc_847_a.py').write_text(SRC_A)
        git(root, 'add', '-f', DIR)
        git(root, 'commit', '-q', '-m', 'base with sealed dir')
        git(root, 'branch', '-f', 'polluted')
        assert 'base' in refused(repo, 'unexpected-entry', base_ref='polluted')


# ---------------------------------------------------------------------------- runner

class TestRun:
    def test_runner_unusable_when_pytest_version_fails(self, repo):
        refused(repo, 'runner-unusable', run=make_run(version_rc=1))

    def test_refused_platform_is_runner_unusable(self, repo):
        def run(argv, cwd, timeout, env):
            return BR.Result(BR.REFUSED, None, detail='no process group')
        assert 'no process group' in refused(repo, 'runner-unusable', run=run)

    def test_timeout_outcome_is_timeout_refusal(self, repo):
        def run(argv, cwd, timeout, env):
            if '--version' in argv:
                return res(0, 'pytest 9\n')
            return BR.Result(BR.TIMEOUT, None, detail='stopped')
        refused(repo, 'timeout', run=run)

    @pytest.mark.parametrize('bad', [0, -1, None, True, 'x', float('nan')])
    def test_timeout_is_required_and_validated(self, repo, bad):
        refused(repo, 'timeout', timeout=bad)

    def test_budget_is_shared_and_never_grows(self, repo):
        calls = []
        do_seal(repo, run=make_run(calls=calls), timeout=100)
        ts = [c['timeout'] for c in calls]
        assert len(ts) == 3 and ts[0] <= 100 and ts == sorted(ts, reverse=True)

    def test_exhausted_budget_is_timeout(self, repo, monkeypatch):
        clock = iter([0.0, 0.0, 500.0, 500.0, 500.0, 500.0, 500.0])
        monkeypatch.setattr(st.time, 'monotonic', lambda: next(clock, 500.0))
        refused(repo, 'timeout', timeout=10)

    def test_explicit_pytest_argv(self, repo):
        calls = []
        do_seal(repo, run=make_run(calls=calls))
        run_call = calls[-1]['argv']
        for flag in ('-q', '-rA', '--tb=short', '--color=no', '-p', 'no:cacheprovider', '-o', 'addopts='):
            assert flag in run_call, flag
        assert any(a.startswith('--rootdir=') for a in run_call)
        assert run_call[:3] == [sys.executable, '-m', 'pytest']
        assert N1 in run_call and N2 in run_call
        assert '--collect-only' in calls[1]['argv'] and DIR in calls[1]['argv']

    def test_env_is_scrubbed_and_home_is_distinct_subdir(self, repo, monkeypatch):
        for k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'GIT_DIR'):
            monkeypatch.setenv(k, '/evil')
        calls = []
        do_seal(repo, run=make_run(calls=calls))
        for c in calls:
            env = c['env']
            for k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS', 'GIT_DIR'):
                assert k not in env, k
            assert env['PYTHONDONTWRITEBYTECODE'] == '1'
            assert env['GIT_TERMINAL_PROMPT'] == '0'
            home = pathlib.Path(env['HOME'])
            assert home != pathlib.Path(c['cwd']) and home.name.startswith('home')
            assert pathlib.Path(c['cwd']).parent == home.parent      # both inside the one temp dir

    def test_temp_tree_is_removed(self, repo):
        calls = []
        do_seal(repo, run=make_run(calls=calls))
        assert not pathlib.Path(calls[0]['cwd']).exists()

    def test_collection_error_refusal_names_file(self, repo):
        run = make_run(collect_rc=2, collect_out='ERROR %s\n' % FILE_A)
        assert FILE_A in refused(repo, 'collection-error', run=run)

    def test_zero_collected_refused(self, repo):
        refused(repo, 'collection-error', run=make_run(nodes=(), collect_rc=5, collect_out='no tests ran\n'))

    def test_pass_on_base_names_test(self, repo):
        run = make_run(summary='FAILED %s - assert 1 == 2\nPASSED %s\n' % (N1, N2))
        assert N2 in refused(repo, 'pass-on-base', run=run)

    def test_skipped_test_is_not_red_and_not_a_pass(self, repo):
        run = make_run(summary='FAILED %s - assert 1 == 2\nSKIPPED [1] %s:3: why\n' % (N1, FILE_A))
        assert N2 in refused(repo, 'not-red', run=run)

    def test_internalerror_exit3_is_runner_unusable(self, repo):
        refused(repo, 'runner-unusable', run=make_run(run_rc=3, summary=''))

    def test_exit1_with_no_failed_nodes_is_not_a_seal(self, repo):
        # exit 1 alone ("pytest not installed" also exits 1) must not read as red
        refused(repo, 'pass-on-base', run=make_run(run_rc=1, summary='PASSED %s\nPASSED %s\n' % (N1, N2)))

    def test_body_import_error_is_red_in_the_record(self, repo):
        run = make_run(summary='FAILED %s - ModuleNotFoundError: x\nFAILED %s - assert 1\n' % (N1, N2))
        rec = do_seal(repo, run=run)
        assert {r['test']: (r['kind'], r['reason']) for r in rec['red']} == {
            N1: ('other', 'ModuleNotFoundError'), N2: ('assertion', 'AssertionError')}


# ---------------------------------------------------------------------------- exemptions

PASS2 = 'FAILED %s - assert 1 == 2\nPASSED %s\n'


class TestExemptions:
    def run2(self):
        return make_run(summary=PASS2 % (N1, N2))

    def test_exempt_pass_is_recorded_with_reason(self, repo):
        rec = do_seal(repo, run=self.run2(), exemptions={N2: '  documents existing behaviour  '})
        assert rec['exemptions'] == [{'test': N2, 'reason': 'documents existing behaviour'}]
        assert [r['test'] for r in rec['red']] == [N1]

    @pytest.mark.parametrize('reason', ['', '   ', None, 5])
    def test_blank_or_non_string_reason_refused(self, repo, reason):
        assert N2 in refused(repo, 'exemption-reason', run=self.run2(), exemptions={N2: reason})

    def test_stale_exemption_refused(self, repo):
        assert N1 in refused(repo, 'exemption-stale', run=make_run(), exemptions={N1: 'why'})

    def test_unknown_exemption_refused(self, repo):
        assert 'nope' in refused(repo, 'exemption-unknown', exemptions={FILE_A + '::nope': 'why'})

    def test_all_exempt_refused(self, repo):
        refused(repo, 'all-exempt', run=make_run(summary='PASSED %s\nPASSED %s\n' % (N1, N2)),
                exemptions={N1: 'a', N2: 'b'})

    def test_prefix_rule_covers_parametrized_cases(self, repo):
        p1, p2 = FILE_A + '::test_p[1]', FILE_A + '::test_p[2]'
        run = make_run(nodes=(N1, p1, p2), summary='FAILED %s - assert 1\nPASSED %s\nPASSED %s\n' % (N1, p1, p2))
        rec = do_seal(repo, run=run, exemptions={FILE_A + '::test_p': 'param table'})
        assert [r['test'] for r in rec['red']] == [N1]

    def test_unexempted_pass_still_refused_when_other_is_exempt(self, repo):
        run = make_run(nodes=(N1, N2, FILE_A + '::test_three'),
                       summary='FAILED %s - assert 1\nPASSED %s\nPASSED %s::test_three\n' % (N1, N2, FILE_A))
        assert 'test_three' in refused(repo, 'pass-on-base', run=run, exemptions={N2: 'why'})


# ---------------------------------------------------------------------------- record

class TestRecord:
    def test_record_has_every_field(self, repo):
        rec = do_seal(repo)
        root, sdlc = repo
        assert json.loads(sealed_file(sdlc).read_text()) == rec
        assert rec['schema'] == 'sigma-sealed/1' and rec['goal'] == STEM and rec['generation'] == 1
        assert rec['plan_hash'] == PH and rec['route'] == ROUTE and rec['dir'] == DIR + '/'
        assert rec['files'] == {FILE_A: {'sha256': hashlib.sha256(SRC_A.encode()).hexdigest(), 'bytes': len(SRC_A)}}
        assert rec['exemptions'] == []
        assert rec['proof'] == {'provenance': 'sealed-pytest-v1', 'python': sys.executable,
                                'pytest': '9.9.9', 'exit': 1}
        assert {r['test'] for r in rec['red']} == {N1, N2}
        assert rec['sealed_at']

    def test_witness_rows_match_evidence_ids_and_cannot_verify(self, repo):
        rec = do_seal(repo)
        root, sdlc = repo
        rows = WIT.witnesses(sdlc, STEM)
        assert len(rows) == 2 and all(r['provenance'] == 'sealed-pytest-v1' for r in rows)
        fh = rec['files'][FILE_A]['sha256']
        for row in rows:
            canon = json.dumps({'test': row['test'], 'file_hash': row['file_hash'],
                                'observed_at': row['observed_at'], 'provenance': row['provenance']}, sort_keys=True)
            ident = hashlib.sha256(canon.encode()).hexdigest()[:16]
            assert row['file_hash'] == fh
            assert ident in {r['evidence_id'] for r in rec['red']}
        # cross-contamination: a sealed row must never count STRONG in witness.verdict
        v = WIT.verdict(sdlc, STEM, [N1, N2], root=root)
        assert v['verified'] == []

    @pytest.mark.parametrize('bad', ['', 'a' * 63, 'A' * 64, 'g' * 64, None, 12, 'a' * 65])
    def test_bad_plan_hash(self, repo, bad):
        refused(repo, 'bad-plan-hash', plan_hash=bad)

    def test_unpersisted_evidence_refused_and_no_record(self, repo, monkeypatch):
        monkeypatch.setattr(WIT, 'record', lambda *a, **k: None)
        refused(repo, 'evidence-unpersisted')
        assert not sealed_file(repo[1]).exists()

    def test_crash_at_witness_append_leaves_no_record_and_rerun_succeeds(self, repo, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError('crash')
        with monkeypatch.context() as m:
            m.setattr(WIT, 'record', boom)
            with pytest.raises(RuntimeError):
                do_seal(repo)
        assert not sealed_file(repo[1]).exists()
        assert do_seal(repo)['generation'] == 1

    def test_crash_at_final_replace_leaves_no_record_and_rerun_succeeds(self, repo, monkeypatch):
        def boom(path, text):
            raise OSError('disk full')
        with monkeypatch.context() as m:
            m.setattr(STATE, 'atomic_write_text', boom)
            with pytest.raises(st.SealRefused) as info:
                do_seal(repo)
            assert info.value.clause == 'evidence-unpersisted'
        assert not sealed_file(repo[1]).exists()
        assert do_seal(repo)['generation'] == 1

    def test_record_is_written_last_after_witness_rows(self, repo, monkeypatch):
        order = []
        real_rec, real_write = WIT.record, STATE.atomic_write_text
        monkeypatch.setattr(WIT, 'record', lambda *a, **k: order.append('witness') or real_rec(*a, **k))
        monkeypatch.setattr(STATE, 'atomic_write_text', lambda p, t: order.append('record') or real_write(p, t))
        do_seal(repo)
        assert order == ['witness', 'witness', 'record']

    def test_record_is_written_through_atomic_writer(self, repo, monkeypatch):
        used = []
        real = STATE.atomic_write_text
        monkeypatch.setattr(STATE, 'atomic_write_text', lambda p, t: used.append(str(p)) or real(p, t))
        do_seal(repo)
        assert used == [str(sealed_file(repo[1]))]

    def test_identical_rerun_returns_existing_without_rewriting(self, repo):
        first = do_seal(repo)
        f = sealed_file(repo[1])
        before = (f.read_bytes(), f.stat().st_mtime_ns)
        rows = len(WIT.witnesses(repo[1], STEM))
        calls = []
        second = do_seal(repo, run=make_run(calls=calls), now=2000.0)
        assert second == first and second['generation'] == 1
        assert (f.read_bytes(), f.stat().st_mtime_ns) == before
        assert len(WIT.witnesses(repo[1], STEM)) == rows and calls == []

    def test_new_plan_hash_bumps_generation(self, repo):
        do_seal(repo)
        assert do_seal(repo, plan_hash='b' * 64)['generation'] == 2

    def test_new_base_sha_bumps_generation(self, repo):
        do_seal(repo)
        git(repo[0], 'branch', 'base2', 'main')           # a different commit, same files
        rec = do_seal(repo, base_ref='base2')
        assert rec['generation'] == 2 and rec['base']['sha'] != git(repo[0], 'rev-parse', 'base').stdout.strip()

    def test_changed_file_bumps_generation(self, repo):
        do_seal(repo)
        (repo[0] / FILE_A).write_text(SRC_A + '\n\ndef test_three():\n    assert 3 == 4\n')
        run = make_run(nodes=(N1, N2, FILE_A + '::test_three'),
                       summary='FAILED %s - assert 1\nFAILED %s - assert 1\nFAILED %s::test_three - assert 1\n' % (N1, N2, FILE_A))
        assert do_seal(repo, run=run)['generation'] == 2

    def test_changed_route_or_exemptions_bump_generation(self, repo):
        do_seal(repo, run=make_run(summary=PASS2 % (N1, N2)), exemptions={N2: 'a'})
        assert do_seal(repo, run=make_run(summary=PASS2 % (N1, N2)), exemptions={N2: 'b'})['generation'] == 2
        assert do_seal(repo, run=make_run(summary=PASS2 % (N1, N2)), exemptions={N2: 'b'},
                       route={'host': 'other'})['generation'] == 3

    def test_unparseable_existing_record_is_replaced(self, repo):
        f = sealed_file(repo[1])
        f.parent.mkdir(parents=True)
        f.write_text('{not json')
        assert do_seal(repo)['generation'] == 1

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_symlinked_state_dir_refused(self, repo, tmp_path):
        root, sdlc = repo
        elsewhere = tmp_path / 'elsewhere'
        elsewhere.mkdir()
        (sdlc / 'state' / 'sealed').symlink_to(elsewhere)
        with pytest.raises(st.SealRefused):
            do_seal(repo)
        assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------------------- read function

def tree_snapshot(path):
    out = {}
    for p in sorted(pathlib.Path(path).rglob('*')):
        s = p.lstat()
        out[str(p)] = (s.st_mtime_ns, s.st_size)
    return out


class TestCheck:
    def sealed(self, repo):
        do_seal(repo)
        return repo

    def check(self, repo):
        return st.check(repo[0], repo[1], STEM)

    def test_unchanged_is_sealed(self, repo):
        self.sealed(repo)
        assert self.check(repo) == {'sealed': True, 'problems': []}

    def test_differs(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 3'))
        assert self.check(repo) == {'sealed': False, 'problems': [{'path': FILE_A, 'kind': 'differs'}]}

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_symlinked_sealed_directory_differs(self, repo):
        import shutil
        self.sealed(repo)
        d = repo[0] / DIR
        shutil.copytree(d, repo[0].parent / 'elsewhere')
        shutil.rmtree(d)
        d.symlink_to(repo[0].parent / 'elsewhere')
        assert self.check(repo) == {'sealed': False, 'problems': [{'path': DIR, 'kind': 'differs'}]}

    def test_same_size_different_content_differs(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))
        assert (repo[0] / FILE_A).stat().st_size == len(SRC_A)
        assert self.check(repo)['problems'] == [{'path': FILE_A, 'kind': 'differs'}]

    def test_missing(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).unlink()
        assert self.check(repo)['problems'] == [{'path': FILE_A, 'kind': 'missing'}]

    def test_unexpected_extra_file_and_subdir(self, repo):
        self.sealed(repo)
        (repo[0] / DIR / 'conftest.py').write_text('x=1\n')
        (repo[0] / DIR / 'sub').mkdir()
        got = self.check(repo)
        assert not got['sealed']
        assert {(p['path'], p['kind']) for p in got['problems']} == {
            (DIR + '/conftest.py', 'unexpected'), (DIR + '/sub', 'unexpected')}

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_symlink_replacing_sealed_file_is_reported(self, repo):
        self.sealed(repo)
        target = repo[0] / 'elsewhere.py'
        target.write_text(SRC_A)                       # identical bytes behind a link must still not pass
        (repo[0] / FILE_A).unlink()
        (repo[0] / FILE_A).symlink_to(target)
        assert self.check(repo)['problems'] == [{'path': FILE_A, 'kind': 'differs'}]

    def test_absent_record_is_not_sealed(self, repo):
        got = self.check(repo)
        assert got['sealed'] is False and got['problems'] and got['problems'][0]['kind'] == 'record'

    @pytest.mark.parametrize('text', ['{nope', '[]', '{"schema": "other/1"}', '{"schema": "sigma-sealed/1"}'])
    def test_unparseable_or_foreign_record_is_not_sealed(self, repo, text):
        f = sealed_file(repo[1])
        f.parent.mkdir(parents=True)
        f.write_text(text)
        got = self.check(repo)
        assert got['sealed'] is False and got['problems'][0]['kind'] == 'record'

    def test_record_naming_a_path_outside_the_dir_is_not_sealed(self, repo):
        self.sealed(repo)
        f = sealed_file(repo[1])
        rec = json.loads(f.read_text())
        rec['files']['../../etc/passwd'] = {'sha256': 'x', 'bytes': 1}
        f.write_text(json.dumps(rec))
        assert not self.check(repo)['sealed']

    def test_check_writes_nothing(self, repo):
        self.sealed(repo)
        (repo[0] / DIR / 'extra.py').write_text('x=1\n')
        before = (tree_snapshot(repo[1]), tree_snapshot(repo[0] / DIR))
        self.check(repo)
        assert (tree_snapshot(repo[1]), tree_snapshot(repo[0] / DIR)) == before


class TestDebrisAbsRoute:
    """Round 3: verify-created cache debris, relative sdlc_dir, unserialisable route."""

    def debris(self, repo):
        pc = repo[0] / DIR / '__pycache__'
        pc.mkdir()
        (pc / 'test_acc_847_a.cpython-312.pyc').write_bytes(b'\x00\x01')
        (repo[0] / DIR / '.pytest_cache').mkdir()
        (repo[0] / DIR / '.pytest_cache' / 'README.md').write_text('x')

    def test_debris_ignored_by_check_and_reseal(self, repo):
        do_seal(repo)
        self.debris(repo)
        assert st.check(repo[0], repo[1], STEM) == {'sealed': True, 'problems': []}
        assert do_seal(repo)['generation'] == 1      # idempotent re-seal, not refused

    def test_seal_with_debris_present(self, repo):
        self.debris(repo)
        assert do_seal(repo)['generation'] == 1

    def test_real_extra_py_still_reported(self, repo):
        do_seal(repo)
        self.debris(repo)
        (repo[0] / DIR / 'extra.py').write_text('x=1\n')
        assert st.check(repo[0], repo[1], STEM)['problems'] == [{'path': DIR + '/extra.py', 'kind': 'unexpected'}]

    def test_non_pyc_file_in_pycache_is_unexpected(self, repo):
        do_seal(repo)
        pc = repo[0] / DIR / '__pycache__'
        pc.mkdir()
        (pc / 'evil.py').write_text('x=1\n')
        assert st.check(repo[0], repo[1], STEM)['problems'] == [{'path': DIR + '/__pycache__', 'kind': 'unexpected'}]
        refused(repo, 'unexpected-entry', plan_hash='b' * 64)

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_symlink_named_pycache_is_unexpected(self, repo):
        do_seal(repo)
        (repo[0] / 'emptydir').mkdir()               # an empty target, so only the link itself can fail it
        (repo[0] / DIR / '__pycache__').symlink_to(repo[0] / 'emptydir')
        assert st.check(repo[0], repo[1], STEM)['problems'] == [{'path': DIR + '/__pycache__', 'kind': 'unexpected'}]

    def test_relative_sdlc_dir(self, repo, monkeypatch):
        monkeypatch.chdir(repo[1].parent)
        rel = repo[1].relative_to(repo[1].parent)
        rec = st.seal(repo[0], rel, STEM, base_ref='base', plan_hash=PH, route=ROUTE, timeout=60,
                      python=sys.executable, run=make_run(), now=1000.0)
        assert rec['generation'] == 1 and sealed_file(repo[1]).exists()
        assert not (repo[1] / 'sdlc').exists()
        assert st.check(repo[0], rel, STEM)['sealed'] is True

    @pytest.mark.parametrize('kw', [{'route': {'x': {1, 2}}}, {'exemptions': {N1: 'r'}, 'route': object()}])
    def test_unserialisable_route_refused_before_side_effects(self, repo, kw):
        refused(repo, 'bad-route', **kw)
        assert WIT.witnesses(repo[1], STEM) == [] and not sealed_file(repo[1]).exists()


# ---------------------------------------------------------------------------- real pytest

@pytest.mark.skipif(WIN, reason='bounded_run refuses without a process group (loud refusal covered above)')
class TestRealPytest:
    """The system of record for the output-parsing seam: real `python -m pytest` in a real base tree."""

    def put(self, repo, **files):
        d = repo[0] / DIR
        for p in d.iterdir():
            p.unlink()
        for name, src in files.items():
            (d / ('test_acc_847_%s.py' % name)).write_text(src)

    def real_seal(self, repo, **kw):
        return st.seal(repo[0], repo[1], STEM, base_ref='base', plan_hash=PH, route=ROUTE, timeout=120,
                        **dict(dict(python=PYTEST_PY), **kw))

    def test_body_import_error_and_assertion_are_both_red(self, repo):
        self.put(repo,
                 imp='def test_imp():\n    from no_such_pkg_xyz import thing\n    assert thing\n',
                 asr='def test_asr():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n')
        rec = self.real_seal(repo)
        kinds = {r['test'].split('::')[-1]: (r['kind'], r['reason']) for r in rec['red']}
        assert kinds['test_imp'][1] == 'ModuleNotFoundError' and kinds['test_imp'][0] == 'other'
        assert kinds['test_asr'][0] == 'assertion'
        assert rec['proof']['exit'] == 1 and rec['proof']['pytest']

    def test_passing_on_base_is_refused_naming_test(self, repo):
        self.put(repo, ok='def test_passes_on_base():\n    import pkg_mod\n    assert pkg_mod.f() == 1\n')
        with pytest.raises(st.SealRefused) as info:
            self.real_seal(repo)
        assert info.value.clause == 'pass-on-base' and 'test_passes_on_base' in str(info.value)

    def test_exemption_lets_a_passing_test_through(self, repo):
        self.put(repo, ok='def test_passes_on_base():\n    import pkg_mod\n    assert pkg_mod.f() == 1\n',
                 bad='def test_red():\n    assert False\n')
        node = DIR + '/test_acc_847_ok.py::test_passes_on_base'
        rec = self.real_seal(repo, exemptions={node: 'regression guard'})
        assert rec['exemptions'] == [{'test': node, 'reason': 'regression guard'}]

    def test_module_top_failure_is_collection_error_naming_file(self, repo):
        self.put(repo, boom='raise RuntimeError("top")\n\n\ndef test_x():\n    assert 1\n',
                 fine='def test_y():\n    assert False\n')
        with pytest.raises(st.SealRefused) as info:
            self.real_seal(repo)
        assert info.value.clause == 'collection-error' and 'test_acc_847_boom.py' in str(info.value)

    def test_skip_in_body_is_not_red(self, repo):
        self.put(repo, sk='def test_s():\n    import pytest\n    pytest.skip("no")\n',
                 bad='def test_red():\n    assert False\n')
        with pytest.raises(st.SealRefused) as info:
            self.real_seal(repo)
        assert info.value.clause == 'not-red' and 'test_s' in str(info.value)

    def test_repo_addopts_is_overridden_not_obeyed(self, repo):
        (repo[0] / 'pytest.ini').write_text('[pytest]\naddopts = --no-such-flag-xyz\n')
        git(repo[0], 'add', 'pytest.ini')
        git(repo[0], 'commit', '-q', '-m', 'ini')
        git(repo[0], 'branch', '-f', 'base', 'main')
        self.put(repo, bad='def test_red():\n    assert False\n')
        assert self.real_seal(repo)['red']

    def test_pytest_addopts_env_is_not_inherited(self, repo, monkeypatch):
        monkeypatch.setenv('PYTEST_ADDOPTS', '--no-such-flag-xyz')
        self.put(repo, bad='def test_red():\n    assert False\n')
        assert self.real_seal(repo)['red']

    def test_not_a_python_with_pytest_is_runner_unusable(self, repo):
        with pytest.raises(st.SealRefused) as info:
            self.real_seal(repo, python='/usr/bin/false')
        assert info.value.clause == 'runner-unusable'


class TestSafeExtractSpecialMembers:
    """Without tarfile.data_filter the fallback must itself refuse device/FIFO members."""

    @pytest.mark.parametrize('kind', ['FIFOTYPE', 'CHRTYPE', 'BLKTYPE'])
    def test_special_member_refused_in_fallback(self, tmp_path, monkeypatch, kind):
        import io
        import tarfile
        monkeypatch.delattr(tarfile, 'data_filter', raising=False)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w') as tw:
            info = tarfile.TarInfo('dev_node')
            info.type = getattr(tarfile, kind)
            tw.addfile(info)
        buf.seek(0)
        dest = tmp_path / 'tree'
        dest.mkdir()
        with tarfile.open(fileobj=buf) as tr:
            with pytest.raises(st.SealRefused) as exc:
                st._safe_extract(tr, str(dest))
        assert exc.value.clause == 'base-unresolved'
        assert not (dest / 'dev_node').exists()


# ---------------------------------------------------------------------------- #850: committed-tree compare

def commit_all(repo, msg='sealed tests'):
    git(repo[0], 'add', '-A')
    git(repo[0], 'commit', '-q', '-m', msg)


def kinds(got):
    return sorted((p['path'], p['kind']) for p in got['problems'])


class TestDebrisName:
    """One pure name rule, shared by the working-tree `is_debris` and the committed-tree `check_tree`."""

    @pytest.mark.parametrize('parts,debris', [
        ((), False),
        (('.pytest_cache',), True),
        (('.pytest_cache', 'v', 'cache', 'lastfailed'), True),
        (('__pycache__', 'a.cpython-312.pyc'), True),
        (('__pycache__', 'a.txt'), False),
        (('__pycache__', 'sub', 'a.pyc'), False),
        (('__pycache__',), False),
        (('sub', '__pycache__', 'a.pyc'), False),
        (('test_acc_847_a.py',), False),
    ])
    def test_table(self, parts, debris):
        assert st.is_debris_name(parts) is debris

    def test_is_debris_goes_through_the_shared_rule(self, tmp_path, monkeypatch):
        (tmp_path / '__pycache__').mkdir()
        (tmp_path / '__pycache__' / 'a.pyc').write_bytes(b'x')
        assert st.is_debris(tmp_path, '__pycache__') is True
        monkeypatch.setattr(st, 'is_debris_name', lambda parts: False)
        assert st.is_debris(tmp_path, '__pycache__') is False


class TestIsSealed:
    def test_absent_present_and_unsafe(self, repo):
        assert st.is_sealed(repo[1], STEM) is False
        do_seal(repo)
        assert st.is_sealed(repo[1], STEM) is True
        assert st.is_sealed(repo[1], '../x') is False

    def test_relative_sdlc_dir_uses_the_current_directory_once(self, repo, monkeypatch):
        do_seal(repo)
        monkeypatch.chdir(repo[1].parent)
        assert st.is_sealed('sdlc', STEM) is True
        monkeypatch.chdir(repo[0])
        assert st.is_sealed(os.path.relpath(repo[1], repo[0]), STEM) is True


class TestCheckTree:
    """Reads the COMMITTED tree (`git ls-tree` + `git cat-file`), never the working tree."""

    def sealed(self, repo, commit=True):
        do_seal(repo)
        if commit:
            commit_all(repo)
        return repo

    def tree(self, repo, **kw):
        return st.check_tree(repo[0], repo[1], STEM, **kw)

    def test_clean(self, repo):
        self.sealed(repo)
        assert self.tree(repo) == {'sealed': True, 'problems': []}

    def test_differs_when_committed_bytes_change(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))      # same size
        commit_all(repo, 'tamper')
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]

    def test_missing_after_git_rm(self, repo):
        self.sealed(repo)
        git(repo[0], 'rm', '-q', FILE_A)
        git(repo[0], 'commit', '-q', '-m', 'rm')
        assert kinds(self.tree(repo)) == [(FILE_A, 'missing')]

    def test_unexpected_extra_committed_file(self, repo):
        self.sealed(repo)
        (repo[0] / DIR / 'conftest.py').write_text('x = 1\n')
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(DIR + '/conftest.py', 'unexpected')]

    def test_gitignored_sealed_file_on_disk_but_not_in_head_is_missing(self, repo):
        do_seal(repo)
        (repo[0] / '.gitignore').write_text('test_acc_847_a.py\n')
        commit_all(repo)
        assert st.check(repo[0], repo[1], STEM)['sealed'] is True       # the working tree looks fine
        assert kinds(self.tree(repo)) == [(FILE_A, 'missing')]          # HEAD does not hold it

    def test_committed_clean_then_modified_uncommitted_is_clean_at_head(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A + '# edit\n')
        assert st.check(repo[0], repo[1], STEM)['sealed'] is False
        assert self.tree(repo)['sealed'] is True

    def test_rev_selects_the_tree(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))
        commit_all(repo, 'tamper')
        assert self.tree(repo, rev='HEAD~1')['sealed'] is True
        assert self.tree(repo, rev='HEAD')['sealed'] is False

    def test_trailing_newline_is_part_of_the_bytes(self, repo):
        self.sealed(repo)
        assert SRC_A.endswith('\n') and self.tree(repo)['sealed'] is True      # a stripped read would not match
        (repo[0] / FILE_A).write_text(SRC_A[:-1] + ' ')                       # same size, last byte differs
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]

    @pytest.mark.skipif(WIN, reason='symlinks and modes')
    def test_symlink_and_exec_bit_are_differs(self, repo):
        self.sealed(repo)
        target = repo[0] / 'elsewhere.py'
        target.write_text(SRC_A)
        (repo[0] / FILE_A).unlink()
        (repo[0] / FILE_A).symlink_to(target)
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]
        (repo[0] / FILE_A).unlink()
        (repo[0] / FILE_A).write_text(SRC_A)
        os.chmod(repo[0] / FILE_A, 0o755)
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]

    @pytest.mark.skipif(WIN, reason='modes')
    def test_check_and_check_tree_agree_on_an_exec_bit_only_change(self, repo):
        self.sealed(repo)
        os.chmod(repo[0] / FILE_A, 0o755)
        assert kinds(st.check(repo[0], repo[1], STEM)) == [(FILE_A, 'differs')]
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]

    def test_submodule_entry_has_no_size_and_does_not_crash(self, repo):
        self.sealed(repo)
        head = git(repo[0], 'rev-parse', 'HEAD').stdout.strip()
        git(repo[0], 'rm', '-q', '--cached', FILE_A)
        git(repo[0], 'update-index', '--add', '--cacheinfo', '160000,%s,%s' % (head, FILE_A))
        git(repo[0], 'update-index', '--add', '--cacheinfo', '160000,%s,%s' % (head, DIR + '/sub'))
        git(repo[0], 'commit', '-q', '-m', 'gitlinks')
        assert kinds(self.tree(repo)) == [(DIR + '/sub', 'unexpected'), (FILE_A, 'differs')]

    def test_size_mismatch_is_differs_without_reading_the_blob(self, repo, monkeypatch):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A + '# more\n')
        commit_all(repo)
        seen = []
        real = st._git_bytes

        def spy(root, *args, **kw):
            seen.append(args[0])
            return real(root, *args, **kw)
        monkeypatch.setattr(st, '_git_bytes', spy)
        assert kinds(self.tree(repo)) == [(FILE_A, 'differs')]
        assert 'cat-file' not in seen and 'ls-tree' in seen

    def test_pycache_pyc_and_pytest_cache_committed_are_ignored(self, repo):
        self.sealed(repo, commit=False)
        (repo[0] / DIR / '__pycache__').mkdir()
        (repo[0] / DIR / '__pycache__' / 'a.cpython-312.pyc').write_bytes(b'\0')
        (repo[0] / DIR / '.pytest_cache' / 'v').mkdir(parents=True)
        (repo[0] / DIR / '.pytest_cache' / 'v' / 'lastfailed').write_text('{}')
        commit_all(repo)
        assert self.tree(repo) == {'sealed': True, 'problems': []}

    def test_nested_or_non_pyc_under_pycache_is_unexpected(self, repo):
        self.sealed(repo, commit=False)
        (repo[0] / DIR / '__pycache__' / 'sub').mkdir(parents=True)
        (repo[0] / DIR / '__pycache__' / 'sub' / 'x.pyc').write_bytes(b'\0')
        (repo[0] / DIR / '__pycache__' / 'x.txt').write_text('x')
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(DIR + '/__pycache__/sub/x.pyc', 'unexpected'),
                                          (DIR + '/__pycache__/x.txt', 'unexpected')]

    @pytest.mark.skipif(WIN, reason='symlinks')
    @pytest.mark.parametrize('how', ['symlink', 'blob'])
    def test_the_sealed_dir_itself_being_a_symlink_or_blob_is_differs(self, repo, how):
        self.sealed(repo)
        git(repo[0], 'rm', '-rq', DIR)
        (repo[0] / DIR).parent.mkdir(parents=True, exist_ok=True)
        if how == 'symlink':
            (repo[0] / DIR).symlink_to(repo[0] / 'pkg_mod.py')
        else:
            (repo[0] / DIR).write_text('not a directory\n')
        commit_all(repo)
        got = kinds(self.tree(repo))
        assert (DIR, 'differs') in got and (FILE_A, 'missing') in got

    def test_absent_record_is_a_record_problem(self, repo):
        got = self.tree(repo)
        assert got['sealed'] is False and got['problems'][0]['kind'] == 'record'

    @pytest.mark.parametrize('text', ['{nope', '[]', '{"schema": "other/1"}'])
    def test_corrupt_or_foreign_record_is_a_record_problem(self, repo, text):
        f = sealed_file(repo[1])
        f.parent.mkdir(parents=True)
        f.write_text(text)
        got = self.tree(repo)
        assert got['sealed'] is False and got['problems'][0]['kind'] == 'record'

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_a_symlinked_record_is_a_record_problem(self, repo):
        do_seal(repo)
        real = sealed_file(repo[1])
        moved = repo[1] / 'state' / 'sealed' / 'real.json'
        real.rename(moved)
        real.symlink_to(moved)
        assert self.tree(repo)['problems'][0]['kind'] == 'record'
        assert st.check(repo[0], repo[1], STEM)['problems'][0]['kind'] == 'record'

    def test_every_problem_path_is_named(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text('x = 1\n')
        (repo[0] / DIR / 'extra_a.py').write_text('x = 1\n')
        (repo[0] / DIR / 'extra_b.py').write_text('x = 1\n')
        commit_all(repo)
        assert kinds(self.tree(repo)) == [(DIR + '/extra_a.py', 'unexpected'), (DIR + '/extra_b.py', 'unexpected'),
                                          (FILE_A, 'differs')]

    def test_check_tree_writes_nothing(self, repo):
        self.sealed(repo)
        before = (tree_snapshot(repo[1]), tree_snapshot(repo[0] / DIR), git(repo[0], 'status', '--porcelain').stdout)
        self.tree(repo)
        assert (tree_snapshot(repo[1]), tree_snapshot(repo[0] / DIR),
                git(repo[0], 'status', '--porcelain').stdout) == before

    # ---- filters: only a MISMATCH is explained; a clean compare is never refused up front
    def test_text_auto_with_lf_content_passes(self, repo):
        do_seal(repo)
        (repo[0] / '.gitattributes').write_text('* text=auto\n')
        commit_all(repo)
        assert self.tree(repo) == {'sealed': True, 'problems': []}

    def test_autocrlf_input_with_lf_content_passes(self, repo):
        do_seal(repo)
        git(repo[0], 'config', 'core.autocrlf', 'input')
        commit_all(repo)
        assert self.tree(repo) == {'sealed': True, 'problems': []}

    def crlf_sealed(self, repo, attrs):
        do_seal(repo)
        crlf = SRC_A.replace('\n', '\r\n').encode()
        (repo[0] / FILE_A).write_bytes(crlf)
        rec = json.loads(sealed_file(repo[1]).read_text())
        rec['files'][FILE_A] = {'sha256': hashlib.sha256(crlf).hexdigest(), 'bytes': len(crlf)}
        sealed_file(repo[1]).write_text(json.dumps(rec))
        (repo[0] / '.gitattributes').write_text(attrs)
        commit_all(repo)

    def test_a_converted_blob_is_differs_with_the_explanation(self, repo):
        self.crlf_sealed(repo, '* text eol=crlf\n')
        got = self.tree(repo)
        assert kinds(got) == [(FILE_A, 'differs')]
        assert st.check(repo[0], repo[1], STEM)['sealed'] is True            # the working bytes are the sealed ones
        why = got['problems'][0].get('why', '')
        assert 'text/eol/filter attributes or core.autocrlf' in why and '-text' in why and 'park' in why

    def test_a_plain_mismatch_gets_no_filter_explanation(self, repo):
        self.sealed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))
        commit_all(repo)
        assert 'why' not in self.tree(repo)['problems'][0]


@pytest.mark.skipif(WIN, reason='modes')
def test_seal_refuses_an_executable_sealed_file(repo):
    """The exec-bit obligation sits at SEAL time: a 0755 file must never be sealed to fail the first gate."""
    os.chmod(repo[0] / FILE_A, 0o755)
    msg = refused(repo, 'unexpected-entry')
    assert 'chmod 644' in msg and 'test_acc_847_a.py' in msg


# ---------------------------------------------------------------------------- #850: sealed-green

PASSING = 'PASSED %s\nPASSED %s\n' % (N1, N2)


@pytest.fixture
def wt(repo, tmp_path):
    """A REAL linked worktree of the repo (`checkout_kind` is "linked" there; a bare tmp dir would say None)."""
    root, _ = repo
    path = tmp_path / 'wt'
    git(root, 'worktree', 'add', '-q', str(path), '-b', 'sdlc/847')
    return path


def green_run(**kw):
    kw.setdefault('run_rc', 0)
    kw.setdefault('summary', PASSING)
    return make_run(**kw)


class TestRunGreen:
    def go(self, repo, wt, run=None, **kw):
        do_seal(repo)
        args = dict(timeout=60, run=run or green_run())
        args.update(kw)
        return st.run_green(wt, repo[1], STEM, **args)

    def refused_green(self, repo, wt, clause, **kw):
        with pytest.raises(st.SealRefused) as info:
            self.go(repo, wt, **kw)
        assert info.value.clause == clause, str(info.value)
        return str(info.value)

    def test_green(self, repo, wt):
        got = self.go(repo, wt)
        assert got['green'] is True and got['failing'] == [] and isinstance(got['detail'], str)

    def test_failing_node_is_named(self, repo, wt):
        got = self.go(repo, wt, run=green_run(run_rc=1, summary='PASSED %s\nFAILED %s - assert 1 == 2\n' % (N1, N2)))
        assert got['green'] is False
        assert [f['test'] for f in got['failing']] == [N2] and 'AssertionError' in got['failing'][0]['why']

    def test_collection_error_names_the_file(self, repo, wt):
        out = 'ERROR %s - ImportError: nope\n' % FILE_A
        got = self.go(repo, wt, run=green_run(collect_rc=2, collect_out=out))
        assert got['green'] is False and FILE_A in [f['test'] for f in got['failing']]

    @pytest.mark.parametrize('line', ['SKIPPED [1] %s:3: nope', 'XFAIL %s', 'XPASS %s', 'ERROR %s - setup blew up'])
    def test_anything_but_passed_is_not_green(self, repo, wt, line):
        text = line % N2 if '%s' in line else line
        got = self.go(repo, wt, run=green_run(summary='PASSED %s\n%s\n' % (N1, text)))
        assert got['green'] is False and N2 in [f['test'] for f in got['failing']]

    def test_an_exempt_on_base_node_that_is_skipped_is_not_green(self, repo, wt):
        """A node the seal did not record red (exempt on base) is caught ONLY by the not-passed clause."""
        n3 = FILE_A + '::test_three'
        summary = 'PASSED %s\nPASSED %s\nSKIPPED [1] %s:9: nope\n' % (N1, N2, FILE_A)
        got = self.go(repo, wt, run=green_run(nodes=(N1, N2, n3), summary=summary))
        assert got['green'] is False and n3 in [f['test'] for f in got['failing']]

    def test_a_deleted_red_test_is_not_green(self, repo, wt):
        got = self.go(repo, wt, run=green_run(nodes=(N1,), summary='PASSED %s\n' % N1))
        assert got['green'] is False and N2 in [f['test'] for f in got['failing']]

    @pytest.mark.parametrize('kw', [dict(run_rc=3), dict(run_rc=4), dict(collect_rc=5, collect_out=''),
                                    dict(run_rc=0, summary='no summary here\n')])
    def test_odd_exit_codes_and_no_summary_are_not_green(self, repo, wt, kw):
        got = self.go(repo, wt, run=green_run(**kw))
        assert got['green'] is False and got['detail']

    def test_timeout_refuses_naming_the_budget(self, repo, wt):
        def run(argv, cwd, timeout, env):
            return res(None, '', outcome=BR.TIMEOUT)
        msg = self.refused_green(repo, wt, 'timeout', run=run)
        assert 'budget' in msg

    def test_one_budget_is_shared_by_version_collect_and_run(self, repo, wt):
        calls = []
        self.go(repo, wt, run=green_run(calls=calls), timeout=60)
        timeouts = [c['timeout'] for c in calls]
        assert len(timeouts) == 3 and max(timeouts) <= 60 and timeouts == sorted(timeouts, reverse=True)

    def test_unsafe_timeout_is_refused(self, repo, wt):
        self.refused_green(repo, wt, 'timeout', timeout=0)

    def test_stale_recorded_python_falls_back_and_says_so(self, repo, wt):
        do_seal(repo)
        f = sealed_file(repo[1])
        rec = json.loads(f.read_text())
        rec['proof']['python'] = '/nonexistent/venv/bin/python'
        f.write_text(json.dumps(rec))
        calls = []
        got = st.run_green(wt, repo[1], STEM, timeout=60, run=green_run(calls=calls))
        assert got['green'] is True and calls[0]['argv'][0] == sys.executable
        assert 'interpreter fallback: /nonexistent/venv/bin/python -> %s' % sys.executable in got['detail']

    def test_neither_interpreter_runs_pytest_is_runner_unusable(self, repo, wt):
        do_seal(repo)
        f = sealed_file(repo[1])
        rec = json.loads(f.read_text())
        rec['proof']['python'] = '/nonexistent/venv/bin/python'
        f.write_text(json.dumps(rec))
        with pytest.raises(st.SealRefused) as info:
            st.run_green(wt, repo[1], STEM, timeout=60, run=green_run(version_rc=1))
        assert info.value.clause == 'runner-unusable'

    def test_the_main_checkout_is_refused_and_nothing_runs(self, repo):
        calls = []
        do_seal(repo)
        with pytest.raises(st.SealRefused) as info:
            st.run_green(repo[0], repo[1], STEM, timeout=60, run=green_run(calls=calls))
        assert info.value.clause == 'main-checkout' and calls == []

    def test_a_directory_git_cannot_read_is_refused_too(self, repo, tmp_path):
        calls = []
        do_seal(repo)
        (tmp_path / 'plain').mkdir()
        with pytest.raises(st.SealRefused) as info:
            st.run_green(tmp_path / 'plain', repo[1], STEM, timeout=60, run=green_run(calls=calls))
        assert info.value.clause == 'main-checkout' and calls == []

    def test_no_record_is_refused(self, repo, wt):
        with pytest.raises(st.SealRefused) as info:
            st.run_green(wt, repo[1], STEM, timeout=60, run=green_run())
        assert info.value.clause == 'record'

    def test_environment_is_scrubbed_and_writes_no_bytecode(self, repo, wt, monkeypatch):
        monkeypatch.setenv('PYTHONPATH', '/evil')
        monkeypatch.setenv('PYTEST_ADDOPTS', '-x')
        calls = []
        self.go(repo, wt, run=green_run(calls=calls))
        env = calls[-1]['env']
        assert env['PYTHONDONTWRITEBYTECODE'] == '1' and 'PYTHONPATH' not in env and 'PYTEST_ADDOPTS' not in env
        assert env['COLUMNS'] == '240' and env['HOME'] != os.environ.get('HOME')
        argv = calls[-1]['argv']
        assert '-p' in argv and 'no:cacheprovider' in argv and '-o' in argv and 'addopts=' in argv

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_a_symlinked_worktree_path_is_realpathed(self, repo, wt, tmp_path):
        link = tmp_path / 'link'
        link.symlink_to(wt)
        calls = []
        self.go(repo, link, run=green_run(calls=calls))
        real = os.path.realpath(wt)
        assert {c['cwd'] for c in calls} == {real}
        assert '--rootdir=' + real in calls[-1]['argv']


@pytest.mark.skipif(PYTEST_PY is None, reason='no interpreter runs pytest in the scrubbed env')
class TestRunGreenRealPytest:
    """The system of record for the green verdict's output parsing: real `python -m pytest` in a real worktree."""

    def setup(self, repo, tmp_path, **files):
        root, sdlc = repo
        for p in (root / DIR).iterdir():
            p.unlink()
        for name, src in files.items():
            (root / DIR / ('test_acc_847_%s.py' % name)).write_text(src)
        st.seal(root, sdlc, STEM, base_ref='base', plan_hash=PH, route=ROUTE, timeout=120, python=PYTEST_PY)
        path = tmp_path / 'wt'
        git(root, 'worktree', 'add', '-q', str(path), '-b', 'sdlc/847')
        shutil_copy(root / DIR, path / DIR)
        return path

    def test_green_and_leaves_no_debris(self, repo, tmp_path):
        # red on base (f() == 1), green on the maker's branch (pkg_mod.f() == 2)
        wt = self.setup(repo, tmp_path, a='def test_one():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n')
        git(wt, 'add', '-A')
        git(wt, 'commit', '-q', '-m', 'sealed')
        got = st.run_green(wt, repo[1], STEM, timeout=120)
        assert got['green'] is True, got
        assert not list((wt / DIR).rglob('__pycache__')) and not list(wt.rglob('.pytest_cache'))
        assert git(wt, 'status', '--porcelain').stdout == ''

    def test_a_failing_node_is_not_green_and_named(self, repo, tmp_path):
        wt = self.setup(repo, tmp_path, a='def test_one():\n    assert False, "boom"\n',
                        b='def test_two():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n')
        got = st.run_green(wt, repo[1], STEM, timeout=120)
        assert got['green'] is False
        assert [f['test'].split('::')[-1] for f in got['failing']] == ['test_one']

    def test_an_exempt_node_turned_into_a_skip_is_not_green(self, repo, tmp_path):
        root, sdlc = repo
        for p in (root / DIR).iterdir():
            p.unlink()
        (root / DIR / 'test_acc_847_a.py').write_text('def test_one():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n')
        (root / DIR / 'test_acc_847_b.py').write_text('def test_keep():\n    assert True\n')
        node = DIR + '/test_acc_847_b.py::test_keep'
        st.seal(root, sdlc, STEM, base_ref='base', plan_hash=PH, route=ROUTE, timeout=120, python=PYTEST_PY,
                exemptions={node: 'regression guard, passes on base'})
        wt = tmp_path / 'wt'
        git(root, 'worktree', 'add', '-q', str(wt), '-b', 'sdlc/847')
        shutil_copy(root / DIR, wt / DIR)
        assert st.run_green(wt, sdlc, STEM, timeout=120)['green'] is True
        (wt / DIR / 'test_acc_847_b.py').write_text('import pytest\n\n\n' + chr(64) + 'pytest.mark.skip\ndef test_keep():\n    assert True\n')
        got = st.run_green(wt, sdlc, STEM, timeout=120)
        assert got['green'] is False and node in [f['test'] for f in got['failing']]

    def test_a_skip_marker_is_not_green(self, repo, tmp_path):
        wt = self.setup(repo, tmp_path, a='def test_one():\n    assert False\n')
        (wt / DIR / 'test_acc_847_a.py').write_text('import pytest\n\n\n' + chr(64) + 'pytest.mark.skip\ndef test_one():\n    assert False\n')
        got = st.run_green(wt, repo[1], STEM, timeout=120)
        assert got['green'] is False and got['failing']

    @pytest.mark.skipif(WIN, reason='symlinks')
    def test_a_symlinked_worktree_path_still_matches_the_recorded_node_ids(self, repo, tmp_path):
        wt = self.setup(repo, tmp_path, a='def test_one():\n    import pkg_mod\n    assert pkg_mod.f() == 2\n')
        link = tmp_path / 'linked-wt'
        link.symlink_to(wt)
        assert st.run_green(link, repo[1], STEM, timeout=120)['green'] is True


def shutil_copy(src, dst):
    import shutil
    shutil.copytree(src, dst, dirs_exist_ok=True)


# ---------------------------------------------------------------------------- #850: remedy lookup

def head_sha(root, rev='HEAD'):
    return git(root, 'rev-parse', rev).stdout.strip()


class TestSourceCommit:
    """`source_commit` finds a commit whose blob at the path has the SEALED sha256, newest first, within a
    budget. Restoring `--source=HEAD` is wrong when HEAD is the tampered commit or the file is gone there."""

    def sealed_sha(self, repo):
        return json.loads(sealed_file(repo[1]).read_text())['files'][FILE_A]['sha256']

    def setup_committed(self, repo):
        do_seal(repo)
        commit_all(repo)
        return self.sealed_sha(repo), head_sha(repo[0])

    def test_head_is_the_tampered_commit(self, repo):
        sha, good = self.setup_committed(repo)
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))
        commit_all(repo, 'tamper')
        assert st.source_commit(repo[0], FILE_A, sha) == good != head_sha(repo[0])

    def test_missing_at_head(self, repo):
        sha, good = self.setup_committed(repo)
        git(repo[0], 'rm', '-q', FILE_A)
        git(repo[0], 'commit', '-q', '-m', 'rm')
        assert st.source_commit(repo[0], FILE_A, sha) == good

    def test_the_sealed_bytes_were_never_committed(self, repo):
        do_seal(repo)
        sha = self.sealed_sha(repo)
        (repo[0] / FILE_A).write_text('x = 1\n')
        commit_all(repo)
        assert st.source_commit(repo[0], FILE_A, sha) is None

    def test_the_first_commit_is_not_taken_unless_its_blob_matches(self, repo):
        sha, good = self.setup_committed(repo)
        (repo[0] / FILE_A).write_text('x = 1\n')
        commit_all(repo, 'other')
        (repo[0] / FILE_A).write_text('y = 2\n')
        commit_all(repo, 'tamper')
        assert st.source_commit(repo[0], FILE_A, sha) == good

    def test_a_rename_into_the_path_is_not_hidden_by_path_limited_history(self, repo):
        do_seal(repo)
        sha = self.sealed_sha(repo)
        (repo[0] / DIR / 'elsewhere.py').write_text(SRC_A)
        (repo[0] / FILE_A).unlink()
        commit_all(repo, 'other name')
        git(repo[0], 'mv', DIR + '/elsewhere.py', FILE_A)
        git(repo[0], 'commit', '-q', '-m', 'renamed in')
        renamed_in = head_sha(repo[0])
        (repo[0] / FILE_A).write_text('x = 1\n')
        commit_all(repo, 'tamper')
        assert st.source_commit(repo[0], FILE_A, sha) == renamed_in

    def test_a_copy_into_the_path_is_found(self, repo):
        do_seal(repo)
        sha = self.sealed_sha(repo)
        (repo[0] / 'copy_src.py').write_text(SRC_A)
        (repo[0] / FILE_A).unlink()
        commit_all(repo, 'src')
        (repo[0] / FILE_A).write_text(SRC_A)
        git(repo[0], 'add', '-A')
        git(repo[0], 'commit', '-q', '-m', 'copied in')
        copied = head_sha(repo[0])
        (repo[0] / FILE_A).write_text('x = 1\n')
        commit_all(repo, 'tamper')
        assert st.source_commit(repo[0], FILE_A, sha) == copied

    def test_cap_bounds_the_walk_and_spends_the_shared_budget(self, repo):
        sha, good = self.setup_committed(repo)
        for i in range(5):
            (repo[0] / FILE_A).write_text('x = %d\n' % i)
            commit_all(repo, 'edit %d' % i)
        budget = {'left': 3}
        assert st.source_commit(repo[0], FILE_A, sha, budget=budget) is None and budget['left'] == 0
        assert st.source_commit(repo[0], FILE_A, sha, cap=50, budget={'left': 50}) == good

    def test_an_unchanged_oid_is_hashed_once(self, repo, monkeypatch):
        sha, good = self.setup_committed(repo)
        for body in ('x = 1\n', SRC_A, 'x = 1\n', SRC_A, 'z = 3\n'):
            (repo[0] / FILE_A).write_text(body)
            commit_all(repo, 'flip')
        (repo[0] / FILE_A).write_text('final = 0\n')
        commit_all(repo, 'final')
        reads = []
        real = st._git_bytes
        monkeypatch.setattr(st, '_git_bytes', lambda root, *a, **k: (reads.append(a) if a[0] == 'cat-file' else None,
                                                                    real(root, *a, **k))[1])
        assert st.source_commit(repo[0], FILE_A, sha) is not None
        oids = [a[-1] for a in reads]
        assert len(oids) == len(set(oids))


class TestRemedy:
    def prep(self, repo):
        do_seal(repo)
        commit_all(repo)
        rec = st.read_record(repo[1], STEM)
        return rec

    def say(self, repo, kind, path, rec=None):
        rec = rec or st.read_record(repo[1], STEM)
        return st.remedy({'kind': kind, 'path': path}, repo[0], rec, budget={'left': 200})

    def test_differs_restores_from_the_matching_commit_not_head(self, repo):
        rec = self.prep(repo)
        good = head_sha(repo[0])
        (repo[0] / FILE_A).write_text(SRC_A.replace('== 2', '== 9'))
        commit_all(repo, 'tamper')
        text = self.say(repo, 'differs', FILE_A, rec)
        assert 'restore --source=%s --staged --worktree -- %s' % (good, FILE_A) in text
        assert '--source=HEAD' not in text and str(repo[0]) in text

    def test_missing_names_the_same_restore(self, repo):
        rec = self.prep(repo)
        good = head_sha(repo[0])
        git(repo[0], 'rm', '-q', FILE_A)
        git(repo[0], 'commit', '-q', '-m', 'rm')
        assert '--source=%s' % good in self.say(repo, 'missing', FILE_A, rec)

    def test_never_committed_names_only_the_park_lever(self, repo):
        do_seal(repo)
        (repo[0] / FILE_A).write_text('x = 1\n')
        commit_all(repo)
        text = self.say(repo, 'differs', FILE_A)
        assert 'never committed' in text and 'park' in text and 'restore' not in text

    def test_budget_exhaustion_is_reported(self, repo):
        self.prep(repo)
        for i in range(3):
            (repo[0] / FILE_A).write_text('x = %d\n' % i)
            commit_all(repo, 'e%d' % i)
        text = st.remedy({'kind': 'differs', 'path': FILE_A}, repo[0], st.read_record(repo[1], STEM), budget={'left': 0})
        assert 'probe budget' in text and 'park' in text

    def test_unexpected_names_deletion(self, repo):
        self.prep(repo)
        text = self.say(repo, 'unexpected', DIR + '/extra.py')
        assert 'rm -f' in text and DIR + '/extra.py' in text and '--source' not in text

    @pytest.mark.skipif(WIN, reason='modes')
    def test_mode_only_names_chmod(self, repo):
        self.prep(repo)
        os.chmod(repo[0] / FILE_A, 0o755)
        text = self.say(repo, 'differs', FILE_A)
        assert 'chmod 644' in text and 'restore' not in text

    def test_record_problem_is_park_only(self, repo):
        self.prep(repo)
        text = self.say(repo, 'record', str(sealed_file(repo[1])))
        assert 'park' in text and 'restore' not in text

    def test_one_global_budget_is_shared_by_every_path(self, repo, monkeypatch):
        rec = self.prep(repo)
        for i in range(4):
            (repo[0] / FILE_A).write_text('x = %d\n' % i)
            commit_all(repo, 'e%d' % i)
        reads = []
        real = st._git_bytes
        monkeypatch.setattr(st, '_git_bytes', lambda root, *a, **k: (reads.append(a) if a[0] == 'cat-file' else None,
                                                                    real(root, *a, **k))[1])
        budget = {'left': 3}
        for _ in range(6):
            st.remedy({'kind': 'differs', 'path': FILE_A}, repo[0], rec, budget=budget)
        assert len(reads) <= 3 and budget['left'] == 0
