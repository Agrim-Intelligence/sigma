"""#2222 T2: first_added_blob, the acceptance guard's committed baseline (real git, no mocks)."""
import importlib.util
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'
REL = '.sdlc/acceptance/9.md'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args, when=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True, env=env)


def write_commit(repo, text, msg='c', when=None):
    f = repo / REL
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', msg, when=when)


def make(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    (repo / 'README').write_text('x')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    return repo


def test_acceptance_baseline_returns_first_commit_blob_not_tip(tmp_path):
    repo = make(tmp_path)
    write_commit(repo, 'one\n')
    write_commit(repo, 'two\n')
    blob, why = load('review_context').first_added_blob(repo, ['main'], REL)
    assert blob == b'one\n' and 'first added' in why


def test_acceptance_baseline_tampered_blob_committed_first_second_commit_still_returns_first(tmp_path):
    repo = make(tmp_path)
    write_commit(repo, 'TAMPERED\n')
    write_commit(repo, 'edited again\n')
    blob, _ = load('review_context').first_added_blob(repo, ['main'], REL)
    assert blob == b'TAMPERED\n'                    # the tip is not the baseline; the first blob is


def test_acceptance_baseline_none_when_file_never_added(tmp_path):
    repo = make(tmp_path)
    blob, why = load('review_context').first_added_blob(repo, ['main'], REL)
    assert blob is None and 'never added' in why


def test_acceptance_baseline_none_and_reason_when_git_unreadable(tmp_path, monkeypatch):
    rc = load('review_context')
    repo = make(tmp_path)
    write_commit(repo, 'one\n')
    blob, why = rc.first_added_blob(tmp_path / 'not-a-repo', ['main'], REL)
    assert blob is None and why
    monkeypatch.setenv('PATH', str(tmp_path / 'empty'))      # git missing from PATH
    blob, why = rc.first_added_blob(repo, ['main'], REL)
    assert blob is None and why


def test_acceptance_baseline_file_deleted_and_readded_returns_earliest(tmp_path):
    repo = make(tmp_path)
    write_commit(repo, 'first\n')
    git(repo, 'rm', '-q', REL)
    git(repo, 'commit', '-qm', 'rm')
    write_commit(repo, 'second add\n')
    blob, _ = load('review_context').first_added_blob(repo, ['main'], REL)
    assert blob == b'first\n'


def test_acceptance_baseline_falls_back_to_remote_base_when_landed(tmp_path):
    repo = make(tmp_path)
    write_commit(repo, 'landed\n')
    blob, _ = load('review_context').first_added_blob(repo, ['sdlc/9', 'origin/sdlc/9', 'main'], REL)
    assert blob == b'landed\n'


def test_acceptance_baseline_earliest_added_across_refs_not_first_listed(tmp_path):
    repo = make(tmp_path)
    git(repo, 'checkout', '-q', '-b', 'early')
    write_commit(repo, 'early\n', when='2020-01-01T00:00:00Z')
    git(repo, 'checkout', '-q', 'main')
    git(repo, 'checkout', '-q', '-b', 'late')
    write_commit(repo, 'late\n', when='2024-01-01T00:00:00Z')
    blob, _ = load('review_context').first_added_blob(repo, ['late', 'early'], REL)
    assert blob == b'early\n'


def test_acceptance_baseline_branch_doc_unchanged():
    """`_branch_doc` still serves the TIP (a different seam); first_added_blob is a separate function."""
    rc = load('review_context')
    assert callable(rc._branch_doc) and rc._branch_doc is not rc.first_added_blob
    src = (SCRIPTS / 'review_context.py').read_text()
    body = src.split('def _branch_doc', 1)[1].split('\ndef ', 1)[0]
    assert '--reverse' not in body and 'diff-filter' not in body


def test_acceptance_baseline_git_show_failure_returns_reason_not_exception(tmp_path, monkeypatch):
    rc = load('review_context')
    repo = make(tmp_path)
    write_commit(repo, 'one\n')
    real = rc.subprocess.run

    def flaky(argv, *a, **k):
        if 'show' in argv:
            raise OSError('simulated wedge')
        return real(argv, *a, **k)
    monkeypatch.setattr(rc.subprocess, 'run', flaky)
    blob, why = rc.first_added_blob(repo, ['main'], REL)
    assert blob is None and 'simulated wedge' in why
