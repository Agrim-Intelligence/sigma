"""#2222 T3: the append-only guard (acceptance.prefix_check / `acceptance.py check`), driven through the CLI."""
import ast
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'
ACC = SCRIPTS / 'acceptance.py'
BODY = '## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Source:
    def fetch_title_body(self, goal):
        return {'title': 'Goal', 'body': BODY}

    def fetch_comments_strict(self, goal):
        return {'comments': []}

    def note(self, goal, text):
        pass


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True, env=env)


def cli(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    return subprocess.run([sys.executable, str(ACC), *args], capture_output=True, text=True, cwd=repo, env=env)


def check(repo):
    return cli(repo, 'check', '.sdlc', '42')


def make(tmp_path, legacy=False, commit=True):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'README').write_text('x')
    git(repo, 'add', 'README')
    git(repo, 'commit', '-qm', 'init')
    sdlc = repo / '.sdlc'
    sdlc.mkdir()
    acc = load('acceptance')
    file = acc.record(sdlc, '42', source=Source(), config={})
    if legacy:
        text = ''.join(l for l in file.read_text().splitlines(True) if not l.startswith('criteria_sha256'))
        file.write_text(text)
    work = sdlc / 'state' / 'work'
    work.mkdir(parents=True)
    (work / '42.json').write_text(json.dumps({'branch': 'main', 'remote': 'origin', 'base': 'main'}))
    if commit:
        git(repo, 'add', '-A')
        git(repo, 'commit', '-qm', 'record')
    return repo, sdlc, file, acc


def rewrite(file, acc, items, added='', keep_hash=False, fix_hash=True):
    """Rewrite the record's block; recompute the stored hash unless told not to (a tamperer who is careful)."""
    text = file.read_text()
    head = text.split('\n---\n', 1)[0]
    if fix_hash and 'criteria_sha256' in head:
        head = re.sub(r'criteria_sha256: ".*"', 'criteria_sha256: "%s"' % acc.criteria_hash(items), head)
    body = '## Done when\n' + ''.join('- [ ] %s\n' % i for i in items)
    if added:
        body += '\n## Added criteria\n' + added
    file.write_text(head + '\n---\n' + body)


def refused(proc):
    return proc.returncode == 2 and 'REFUSED' in proc.stderr


ORIG = ['First works.', 'Second refuses bad input.', 'Third recovers.']


def test_acceptance_append_only_intact_record_passes_and_prints_baseline(tmp_path):
    repo, *_ = make(tmp_path)
    proc = check(repo)
    assert proc.returncode == 0, proc.stderr
    assert 'baseline: first added' in proc.stdout


@pytest.mark.parametrize('legacy', [True, False])
def test_acceptance_append_only_edited_criterion_refused(tmp_path, legacy):
    repo, _, file, acc = make(tmp_path, legacy=legacy)
    rewrite(file, acc, ['First works.', 'Second refuses nothing.', 'Third recovers.'])
    assert refused(check(repo))


@pytest.mark.parametrize('legacy', [True, False])
def test_acceptance_append_only_removed_criterion_refused(tmp_path, legacy):
    repo, _, file, acc = make(tmp_path, legacy=legacy)
    rewrite(file, acc, ORIG[:2] + ['Fourth.'])            # the third is gone, a new one stands in
    assert refused(check(repo))


@pytest.mark.parametrize('legacy', [True, False])
def test_acceptance_append_only_reordered_criteria_refused(tmp_path, legacy):
    repo, _, file, acc = make(tmp_path, legacy=legacy)
    rewrite(file, acc, [ORIG[1], ORIG[0], ORIG[2]])
    assert refused(check(repo))


@pytest.mark.parametrize('legacy', [True, False])
def test_acceptance_append_only_weakened_criterion_refused(tmp_path, legacy):
    repo, _, file, acc = make(tmp_path, legacy=legacy)
    rewrite(file, acc, ['First works.', 'Second refuses input.', 'Third recovers.'])     # a word dropped
    assert refused(check(repo))


def test_acceptance_append_only_hash_mismatch_with_block_refused(tmp_path):
    """Clause H alone: no commit exists, so B cannot run; the block changed and the stored hash did not."""
    repo, _, file, acc = make(tmp_path, commit=False)
    rewrite(file, acc, ['First works.', 'Second refuses nothing.', 'Third recovers.'], fix_hash=False)
    proc = check(repo)
    assert refused(proc) and 'criteria_sha256' in proc.stderr and 'baseline: unavailable' in proc.stdout


def test_acceptance_append_only_criteria_sha256_changed_with_block_refused_against_first_blob(tmp_path):
    """Clause B's hash equality. The FIRST committed record carries a hash that is not the current block's
    (as if the hash had been rewritten after the first commit); the criteria text itself is an ordered prefix, so
    only the baseline-hash comparison can refuse, and clause H (current block vs current hash) is satisfied."""
    repo, _, file, acc = make(tmp_path, commit=False)
    good = file.read_text()
    file.write_text(re.sub(r'criteria_sha256: ".*"', 'criteria_sha256: "%s"' % ('a' * 64), good))
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'first record')
    file.write_text(good)                                     # current: hash consistent with its own block
    proc = check(repo)
    assert refused(proc) and 'first committed record' in proc.stderr


def test_acceptance_append_only_baseline_not_ordered_prefix_refused(tmp_path):
    """LEGACY record on purpose: with no stored hash only the ordered-prefix test can see the reorder (a hash
    would mask a membership-only weakening, because the reordered block hashes differently)."""
    repo, _, file, acc = make(tmp_path, legacy=True)
    rewrite(file, acc, ORIG, added='')                                         # intact
    assert check(repo).returncode == 0
    rewrite(file, acc, [ORIG[0], ORIG[2], ORIG[1]])                           # all members present, order broken
    assert refused(check(repo))


def test_acceptance_append_only_appended_criterion_accepted(tmp_path):
    repo, _, file, acc = make(tmp_path)
    rewrite(file, acc, ORIG, added='- [ ] Fourth holds. (added: 42b; reason: review found it)\n')
    proc = check(repo)
    assert proc.returncode == 0, proc.stderr


def test_acceptance_append_only_reasonless_added_criterion_refused(tmp_path):
    repo, _, file, acc = make(tmp_path)
    rewrite(file, acc, ORIG, added='- [ ] Naked line.\n')
    proc = check(repo)
    assert refused(proc) and 'without slice and reason' in proc.stderr
    assert acc.reasonless_added(repo / '.sdlc', '42') == ['Naked line.']


def test_acceptance_append_only_metadata_only_verify_command_change_accepted(tmp_path):
    repo, _, file, _ = make(tmp_path)
    proc = cli(repo, 'set-verify-command', '.sdlc', '42', '--verify-command', 'python3 -m pytest -q')
    assert proc.returncode == 0, proc.stderr
    assert check(repo).returncode == 0


def test_acceptance_append_only_set_verify_command_cannot_alter_criteria(tmp_path):
    repo, _, file, acc = make(tmp_path)
    rewrite(file, acc, ['First works.', 'Second refuses nothing.', 'Third recovers.'])
    before = file.read_text()
    proc = cli(repo, 'set-verify-command', '.sdlc', '42', '--verify-command', 'python3 -m pytest -q')
    assert refused(proc)
    assert file.read_text() == before                    # nothing written on a refusal


def test_acceptance_append_only_record_on_existing_tampered_record_refused(tmp_path):
    repo, _, file, acc = make(tmp_path)
    rewrite(file, acc, ['First works.', 'Second refuses nothing.', 'Third recovers.'])
    assert refused(cli(repo, 'record', '.sdlc', '42'))


def test_acceptance_append_only_legacy_record_unchanged_passes(tmp_path):
    for commit in (False, True):
        sub = tmp_path / str(commit)
        sub.mkdir()
        repo, *_ = make(sub, legacy=True, commit=commit)
        assert check(repo).returncode == 0


def test_acceptance_append_only_legacy_record_pre_commit_guard_is_vacuous_and_says_so(tmp_path):
    """The stated residual (D1): before the first commit a legacy record has no witness, so a rewrite passes."""
    repo, _, file, acc = make(tmp_path, legacy=True, commit=False)
    rewrite(file, acc, ['Something else.', 'Entirely.', 'Different.'])
    proc = check(repo)
    assert proc.returncode == 0 and 'baseline: unavailable' in proc.stdout


def test_acceptance_append_only_legacy_in_block_extension_passes_residual(tmp_path):
    """m2 residual: a LEGACY baseline is satisfied by any ordered extension, even inside `## Done when`; only a
    hash-carrying record is protected from it (clause H)."""
    repo, _, file, acc = make(tmp_path, legacy=True)
    rewrite(file, acc, ORIG + ['Slipped in without a reason.'])
    assert check(repo).returncode == 0                                     # documented residual, legacy only
    other = tmp_path / 'b'
    other.mkdir()
    repo2, _, file2, acc2 = make(other, legacy=False)
    rewrite(file2, acc2, ORIG + ['Slipped in without a reason.'], fix_hash=False)
    assert refused(check(repo2))                                           # clause H


def test_acceptance_append_only_deleted_record_with_baseline_refused(tmp_path):
    """Clause D: the record vanished but a first-added baseline exists."""
    repo, _, file, _ = make(tmp_path)
    file.unlink()
    proc = check(repo)
    assert refused(proc) and 'committed baseline exists' in proc.stderr


def test_acceptance_append_only_unparseable_record_with_baseline_refused(tmp_path):
    repo, _, file, _ = make(tmp_path)
    file.write_text('garbage')
    assert refused(check(repo))


def test_acceptance_append_only_missing_record_without_baseline_is_not_a_tamper(tmp_path):
    repo, _, file, _ = make(tmp_path, commit=False)
    file.unlink()
    assert check(repo).returncode == 0


def test_acceptance_append_only_git_unreadable_refuses_only_on_clear_mismatch(tmp_path):
    repo, _, file, acc = make(tmp_path)
    git_dir = repo / '.git'
    git_dir.rename(repo / '.git-gone')                                    # git cannot answer
    proc = check(repo)
    assert proc.returncode == 0 and 'baseline: unavailable' in proc.stdout
    rewrite(file, acc, ['First works.', 'Second refuses nothing.', 'Third recovers.'], fix_hash=False)
    assert refused(check(repo))                                           # H still refuses


def test_acceptance_append_only_extend_marker_constructor():
    acc = load('acceptance')
    marker = acc.extend_marker('t', 's', 'r')
    assert marker.startswith('<!-- sigma:acceptance-extend ') and marker.endswith(' -->')
    assert marker != acc.extend_marker('t', 's', 'r2')


GUARD_FUNCS = {'prefix_check', 'prefix_refusal', 'baseline_refs', 'reasonless_added', 'extend_comment_refusal',
               '_record_text', 'extend_marker'}
FORBIDDEN_NAMES = {'load_config', 'getenv', 'environ'}
FORBIDDEN_OPTIONS = re.compile(r'force|skip|allow|override|no-guard|no_guard', re.I)


def _forbidden_in(tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            hits.append(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                hits.append(node.attr)
            if node.attr == 'get' and isinstance(node.value, ast.Name) and node.value.id == 'config':
                hits.append('config.get')
    return hits


def _guard_sources(text):
    tree = ast.parse(text)
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in GUARD_FUNCS]


def test_acceptance_append_only_no_bypass_surface():
    text = ACC.read_text()
    tree = ast.parse(text)
    options = [a.value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, 'attr', '') == 'add_argument'
               for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    assert options and not [o for o in options if FORBIDDEN_OPTIONS.search(o)], options
    funcs = _guard_sources(text)
    assert {f.name for f in funcs} >= {'prefix_check', 'prefix_refusal', 'baseline_refs', 'reasonless_added'}
    for fn in funcs:
        assert not _forbidden_in(fn), (fn.name, _forbidden_in(fn))
        assert not [a.arg for a in fn.args.args + fn.args.kwonlyargs if FORBIDDEN_OPTIONS.search(a.arg)], fn.name
    rc = ast.parse((SCRIPTS / 'review_context.py').read_text())
    first = [n for n in ast.walk(rc) if isinstance(n, ast.FunctionDef) and n.name == 'first_added_blob']
    assert first and not _forbidden_in(first[0])
