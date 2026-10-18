"""#847 (slice 2 of #558): the seal record and the sealed-pytest-v1 red-on-base proof.

Layers: TestClassify and the fake-runner classes are the fast unit layer. TestRealPytest is the system
of record for the output-parsing seam (a fake runner cannot prove it). Every refusal clause has its own
test; the plan's controls table says which break turns which test red."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

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
        return st.seal(repo[0], repo[1], STEM, base_ref='base', plan_hash=PH, route=ROUTE, timeout=120, **kw)

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
