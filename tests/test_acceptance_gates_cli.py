"""#2222 T4: the append-only guard at `work.py commit`, `pr` and `merge()`, the issue divergence check and the
extend-comment check. Real git (a bare origin, a root checkout and a goal worktree); only the issue source is a fake."""
import importlib.util
import json
import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'
BODY = '## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n'
ORIG = ['First works.', 'Second refuses bad input.', 'Third recovers.']
EDITED = ['First works.', 'Second refuses nothing.', 'Third recovers.']
# a careful tamperer edits the issue too, so the divergence witness stays silent and only the append-only guard speaks
BODY_EDITED = BODY.replace('Second refuses bad input.', 'Second refuses nothing.')


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


work = load('work')
acc = load('acceptance')


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout


class Source:
    def __init__(self, body=BODY, fail=False):
        self.body, self.comments, self.fail = body, [], fail

    def fetch_title_body(self, goal):
        if self.fail:
            raise RuntimeError('network down')
        return {'title': 'Goal', 'body': self.body}

    def fetch_comments_strict(self, goal):
        if self.fail:
            raise RuntimeError('network down')
        return {'comments': self.comments}

    def note(self, goal, text):
        self.comments.append({'body': text})


class Fx:
    pass


@pytest.fixture
def fx(tmp_path, monkeypatch):
    f = Fx()
    f.source = Source()
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc_dir, config: f.source)
    origin = tmp_path / 'origin.git'
    subprocess.run(['git', 'init', '-q', '--bare', '-b', 'main', str(origin)], check=True)
    f.repo = tmp_path / 'repo'
    f.repo.mkdir()
    git(f.repo, 'init', '-q', '-b', 'main')
    (f.repo / 'README').write_text('x')
    (f.repo / '.gitignore').write_text('.sdlc/state/\n')
    git(f.repo, 'add', '-A')
    git(f.repo, 'commit', '-qm', 'init')
    git(f.repo, 'remote', 'add', 'origin', str(origin))
    git(f.repo, 'push', '-q', 'origin', 'main')
    f.wt = tmp_path / 'wt'
    git(f.repo, 'worktree', 'add', '-q', '-b', 'sdlc/42', str(f.wt), 'main')
    f.sdlc = f.repo / '.sdlc'
    f.sdlc.mkdir()
    f.root_file = acc.record(f.sdlc, '42', source=Source(), config={})
    f.wt_file = f.wt / '.sdlc/acceptance/42.md'
    f.rec = {'worktree': str(f.wt), 'remote': 'origin', 'base': 'main', 'branch': 'sdlc/42', 'pr': '7'}
    work._save(f.sdlc, '42', f.rec)
    f.commit = lambda: work.commit(f.sdlc, {}, '42', message='sdlc: 42')
    f.pr = lambda cfg=None: work.pr(f.sdlc, cfg or {}, '42')
    return f


def first_commit(f):
    out = f.commit()
    assert 'ACCEPTANCE' not in out, out
    assert f.wt_file.is_file()
    return out


def tamper(file, items, added='', fix_hash=True):
    text = file.read_text()
    head = text.split('\n---\n', 1)[0]
    if fix_hash and 'criteria_sha256' in head:
        head = re.sub(r'criteria_sha256: ".*"', 'criteria_sha256: "%s"' % acc.criteria_hash(items), head)
    body = '## Done when\n' + ''.join('- [ ] %s\n' % i for i in items)
    if added:
        body += '\n## Added criteria\n' + added
    file.write_text(head + '\n---\n' + body)


def legacy(file):
    file.write_text(''.join(l for l in file.read_text().splitlines(True) if not l.startswith('criteria_sha256')))


def refused(out):
    return 'ACCEPTANCE REFUSED' in out


GOOD_ADDED = '- [ ] Fourth holds. (added: 42b; reason: review found it)\n'


def append_both(f, added=GOOD_ADDED):
    tamper(f.root_file, ORIG, added=added)
    tamper(f.wt_file, ORIG, added=added)


def comment_for(f, text='Fourth holds.', slice_='42b', reason='review found it'):
    f.source.comments.append({'body': acc.extend_marker(text, slice_, reason) + '\n' + text})


def staged(f):
    return git(f.wt, 'diff', '--cached', '--name-only').strip()


# ---- the unconditional guard -------------------------------------------------------------------------------

def test_acceptance_commit_refuses_tampered_record_unconditionally(fx):
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.root_file, EDITED)
    tamper(fx.wt_file, EDITED)
    out = fx.commit()                          # verify.enforce is off ({} config)
    assert refused(out) and '(nothing committed)' in out
    assert staged(fx) == ''


def test_acceptance_pr_refuses_tampered_record_unconditionally(fx):
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.wt_file, EDITED)
    out = fx.pr()
    assert refused(out) and '(nothing pushed)' in out


def test_acceptance_merge_refuses_tampered_record_unconditionally(fx):
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.wt_file, EDITED)
    calls = []
    out = work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: calls.append(argv) or '')
    assert out.startswith('PARK: ACCEPTANCE REFUSED') and not calls


def test_acceptance_merge_reads_the_branch_blob_when_the_root_copy_is_already_removed(fx):
    """#1474 cleanup removes the byte-identical root copy after a confirmed merge: absence is not a tamper."""
    first_commit(fx)
    fx.root_file.unlink()
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    fx.wt_file.unlink()                                  # neither copy on disk: read the committed tip
    calls = []
    out = work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: calls.append(argv) or '')
    assert 'ACCEPTANCE REFUSED' not in out


def test_acceptance_pr_refuses_reasonless_added_criterion(fx):
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    assert refused(fx.pr())


def test_acceptance_merge_refuses_reasonless_added_criterion(fx):
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    assert work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: '').startswith('PARK: ACCEPTANCE REFUSED')


def test_acceptance_commit_and_pr_accept_appended_record(fx):
    first_commit(fx)
    append_both(fx)
    comment_for(fx)
    out = fx.commit()
    assert not refused(out), out
    assert not refused(fx.pr())


def test_acceptance_pr_presence_check_still_enforce_gated(fx):
    fx.root_file.unlink()
    assert 'acceptance record missing or invalid' not in fx.pr({})
    assert 'acceptance record missing or invalid' in fx.pr({'verify': {'enforce': True}})


# ---- which copy ----------------------------------------------------------------------------------------------

def test_acceptance_commit_refuses_worktree_only_edit(fx):
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.wt_file, EDITED)                      # root untouched; the create-only copy would never overwrite it
    out = fx.commit()
    assert refused(out) and staged(fx) == ''


def test_acceptance_commit_refuses_root_only_edit(fx):
    first_commit(fx)
    tamper(fx.root_file, EDITED)                    # the worktree copy is stale (still the original)
    out = fx.commit()
    assert refused(out) and staged(fx) == ''


def test_acceptance_commit_accepts_differing_copies_when_worktree_is_legitimate_extension(fx):
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added=GOOD_ADDED)      # worktree = root plus added criteria
    assert fx.wt_file.read_text() != fx.root_file.read_text()
    assert not refused(fx.commit())


def test_acceptance_commit_refuses_stale_worktree_copy_when_root_was_extended(fx):
    """The worktree copy passes the prefix guard on its own (it is the committed original) and so does the root
    copy; only the 'differ by more than appended criteria' clause can see that the copy about to be staged would
    drop the root's added criterion."""
    first_commit(fx)
    tamper(fx.root_file, ORIG, added=GOOD_ADDED)
    out = fx.commit()
    assert refused(out) and 'differ' in out and staged(fx) == ''


def test_acceptance_commit_checks_bytes_about_to_be_staged_not_only_root(fx):
    """Root is pristine and a root-only check would pass; `pr` has no root check at all and still refuses."""
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.wt_file, EDITED)
    assert acc.prefix_refusal(fx.sdlc, '42') is None            # the root copy alone is clean
    assert refused(fx.pr())


# ---- rollout: legacy records ---------------------------------------------------------------------------------

def test_acceptance_rollout_legacy_record_unchanged_passes_at_commit(fx):
    legacy(fx.root_file)
    first_commit(fx)
    assert not refused(fx.commit() or '')


def test_acceptance_rollout_legacy_record_unchanged_passes_at_pr(fx):
    legacy(fx.root_file)
    first_commit(fx)
    assert not refused(fx.pr())


def test_acceptance_rollout_set_verify_command_passes_at_commit_and_pr(fx):
    first_commit(fx)
    acc.set_verify_command(fx.sdlc, '42', 'python3 -m pytest -q')
    assert not refused(fx.commit() or '')
    assert not refused(fx.pr())


# ---- issue divergence ----------------------------------------------------------------------------------------

def test_acceptance_pr_parks_when_issue_criteria_diverge_from_record(fx):
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = fx.pr()
    assert refused(out) and 'issue' in out and '(nothing pushed)' in out


def test_acceptance_divergence_skipped_for_drafted_record(fx):
    text = fx.root_file.read_text().replace('drafted: false', 'drafted: true')
    fx.root_file.write_text(text)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    assert not refused(fx.pr())


def test_acceptance_divergence_skipped_for_legacy_record_without_hash(fx):
    legacy(fx.root_file)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    assert not refused(fx.pr())


def test_acceptance_divergence_skipped_when_issue_has_no_criteria_section(fx):
    fx.source.body = 'Just prose, no checklist.'
    assert not refused(fx.pr())


def test_acceptance_divergence_ticked_checkbox_is_not_divergence(fx):
    fx.source.body = BODY.replace('- [ ] First works.', '- [x] First works.')
    assert not refused(fx.pr())


def test_acceptance_divergence_alias_heading_in_issue_is_normalised(fx):
    fx.source.body = BODY.replace('## Done when', '## Acceptance criteria')
    assert not refused(fx.pr())


def test_acceptance_pr_fails_closed_when_issue_fetch_fails(fx):
    fx.source.fail = True
    out = fx.pr()
    assert refused(out) and 'could not read the issue' in out


def test_acceptance_commit_fails_open_with_message_when_issue_fetch_fails(fx, capsys):
    fx.source.fail = True
    out = fx.commit()
    assert not refused(out or '')
    assert 'issue unreadable' in capsys.readouterr().err


def test_acceptance_local_goal_skips_divergence_and_says_so(fx, capsys):
    out = work._acceptance_issue_refusal(fx.sdlc, {}, fx.rec, 'some-goal.md', '(x)', closed=True, comments=True)
    assert out is None and 'skipped (local goal)' in capsys.readouterr().err


# ---- added criteria are in the open --------------------------------------------------------------------------

def test_acceptance_pr_refuses_hand_written_added_criterion_without_extend_comment(fx):
    first_commit(fx)
    append_both(fx)                                  # well-formed suffix, but nobody ran `extend`
    out = fx.pr()
    assert refused(out) and 'extend comment' in out


def test_acceptance_pr_accepts_added_criterion_with_matching_extend_comment(fx):
    first_commit(fx)
    append_both(fx)
    comment_for(fx)
    assert not refused(fx.pr())


def test_acceptance_pr_refuses_comment_with_different_reason(fx):
    first_commit(fx)
    append_both(fx)
    comment_for(fx, reason='some other reason')
    assert refused(fx.pr())


def test_acceptance_pr_refuses_when_comment_history_unreadable(fx):
    first_commit(fx)
    append_both(fx)
    comment_for(fx)

    def boom(goal):
        raise RuntimeError('rate limited')
    fx.source.fetch_comments_strict = boom
    out = fx.pr()
    assert refused(out) and 'comments' in out


def test_acceptance_pr_local_goal_exempt_from_extend_comment_check_and_says_so(fx, capsys):
    text = (BODY + '\n## Added criteria\n' + GOOD_ADDED)
    out = work._acceptance_issue_refusal(fx.sdlc, {}, fx.rec, 'g.md', '(x)', closed=True, comments=True)
    assert out is None and 'extend comment: skipped (local goal)' in capsys.readouterr().err


def test_acceptance_source_is_none_unless_the_backlog_is_github(tmp_path):
    """No issue to compare with on a local-goals backlog: the check is skipped and says so (default source)."""
    fresh = load('work')
    assert fresh._acceptance_source(tmp_path / '.sdlc', {}) is None
    assert fresh._acceptance_source(tmp_path / '.sdlc', {'discovery': {'source': 'local-goals'}}) is None
