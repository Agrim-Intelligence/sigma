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
    f.parks = []
    monkeypatch.setattr(work, '_park_goal', lambda sdlc_dir, source, goal, detail: f.parks.append((goal, detail)))
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
    """Review fix M1: the divergence refusal records the park itself (D4), so exit 4 means the goal already left
    the claimed state, and the text says not to `record`."""
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = fx.pr()
    assert refused(out) and 'issue' in out and '(nothing pushed)' in out
    assert 'Parked it' in out and 'do not `record`' in out
    assert len(fx.parks) == 1 and fx.parks[0][0] == '42' and 'Done when' in fx.parks[0][1]


def test_acceptance_commit_parks_when_issue_criteria_diverge_from_record(fx):
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = fx.commit()
    assert refused(out) and 'Parked it' in out and staged(fx) == '' and len(fx.parks) == 1


def test_acceptance_divergence_park_failure_says_still_claimed(fx, monkeypatch):
    def boom(*a):
        raise RuntimeError('ledger down')
    monkeypatch.setattr(work, '_park_goal', boom)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = fx.pr()
    assert refused(out) and 'STILL CLAIMED' in out and 'parked' in out


def test_acceptance_transient_refusals_do_not_park_and_tamper_refusals_do(fx):
    """Third review S2: exit 4 from a finding means the goal already left the claimed state; a read failure is
    transient (the next tick retries) and leaves it claimed."""
    fx.source.fail = True
    assert refused(fx.pr()) and fx.parks == []                  # transient: the next tick retries
    fx.source.fail = False
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    out = fx.pr()
    assert refused(out) and 'Parked it' in out and 'do not `record`' in out and 'append-only' in out
    assert len(fx.parks) == 1 and fx.parks[0][0] == '42' and 'slice and reason' in fx.parks[0][1]


def test_acceptance_divergence_runs_for_drafted_record_when_issue_has_a_section(fx):
    """Review fix B2: `drafted: true` is no switch-off; a drafted record only legitimately arises with no section."""
    text = fx.root_file.read_text().replace('drafted: false', 'drafted: true')
    fx.root_file.write_text(text)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    assert refused(fx.pr())


def test_acceptance_divergence_skipped_for_drafted_record_when_issue_has_no_section(fx):
    text = fx.root_file.read_text().replace('drafted: false', 'drafted: true')
    fx.root_file.write_text(text)
    fx.source.body = 'Prose only; the criteria were drafted.'
    assert not refused(fx.pr())


def test_acceptance_divergence_runs_for_record_without_hash_when_issue_has_a_section(fx):
    """Review fix B2: deleting the `criteria_sha256` line is no switch-off either."""
    legacy(fx.root_file)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    assert refused(fx.pr())


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
    out = work._acceptance_issue_refusal(fx.sdlc, {}, fx.rec, 'g.md', '(x)', closed=True, comments=True)
    assert out is None and 'extend comment: skipped (local goal)' in capsys.readouterr().err


def test_acceptance_source_is_none_unless_the_backlog_is_github(tmp_path):
    """No issue to compare with on a local-goals backlog: the check is skipped and says so (default source)."""
    fresh = load('work')
    assert fresh._acceptance_source(tmp_path / '.sdlc', {}) is None
    assert fresh._acceptance_source(tmp_path / '.sdlc', {'discovery': {'source': 'local-goals'}}) is None


# ---- review fix B1: a deletion about to be staged is judged, not the committed tip ---------------------------

def test_acceptance_commit_refuses_deleting_both_copies_after_baseline(fx):
    """Clause D at `commit`: with a committed baseline, neither copy on disk is the deletion about to be staged."""
    first_commit(fx)
    fx.root_file.unlink()
    fx.wt_file.unlink()
    out = fx.commit()
    assert refused(out) and 'missing' in out and '(nothing committed)' in out
    assert staged(fx) == ''
    assert '.sdlc/acceptance/42.md' in git(fx.wt, 'ls-tree', '-r', '--name-only', 'HEAD')


def test_acceptance_pr_refuses_deleting_both_copies_after_baseline_pushed(fx):
    """Clause D at `pr`: the tip on origin still holds the record, but what `pr` pushes is the local deletion."""
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    fx.root_file.unlink()
    fx.wt_file.unlink()
    git(fx.wt, 'add', '-A')
    git(fx.wt, 'commit', '-qm', 'drop the record')     # a raw commit past the guarded `work.py commit`
    out = fx.pr()
    assert refused(out) and 'missing' in out and '(nothing pushed)' in out


# ---- review fix B2: hash deletion and a drafted flip are not switch-offs -------------------------------------

def test_acceptance_commit_refuses_hash_deleted_and_criteria_edited_before_first_commit(fx):
    """No baseline yet: the issue's Done when is the only witness, and it runs although the hash line is gone."""
    tamper(fx.root_file, EDITED)
    legacy(fx.root_file)
    out = fx.commit()
    assert refused(out) and 'issue' in out and staged(fx) == ''


def test_acceptance_commit_refuses_drafted_flip_and_criteria_edited_before_first_commit(fx):
    tamper(fx.root_file, EDITED)                    # hash re-computed, so clause H is silent
    fx.root_file.write_text(fx.root_file.read_text().replace('drafted: false', 'drafted: true'))
    out = fx.commit()
    assert refused(out) and 'issue' in out and staged(fx) == ''


def test_acceptance_commit_refuses_hash_line_dropped_after_baseline(fx):
    """Clause B: the criteria are unchanged (so the issue witness is silent) but the hash the baseline had is gone."""
    first_commit(fx)
    legacy(fx.root_file)
    legacy(fx.wt_file)
    out = fx.commit()
    assert refused(out) and 'criteria_sha256' in out and staged(fx) == ''


def test_acceptance_commit_refuses_drafted_flip_after_baseline(fx):
    first_commit(fx)
    for f in (fx.root_file, fx.wt_file):
        f.write_text(f.read_text().replace('drafted: false', 'drafted: true'))
    out = fx.commit()
    assert refused(out) and 'drafted' in out and staged(fx) == ''


def test_acceptance_commit_refuses_worktree_only_drafted_flip(fx):
    """m1: the `drafted` equality in `_acceptance_is_extension` is what sees a worktree-only flip at commit, before
    clause B (which judges the worktree copy against the baseline) gets a turn."""
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added=GOOD_ADDED)
    fx.wt_file.write_text(fx.wt_file.read_text().replace('drafted: false', 'drafted: true'))
    out = fx.commit()
    assert refused(out) and 'worktree copy and the root copy' in out and staged(fx) == ''


# ---- review fix B3: a backdated local rewrite cannot replace the pushed baseline ------------------------------

def test_acceptance_commit_refuses_backdated_local_rewrite_after_baseline_pushed(fx):
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')                      # the pushed original is the baseline
    git(fx.wt, 'reset', '-q', '--hard', 'main')                         # rewrite the local branch from scratch
    fx.source.body = BODY_EDITED
    tamper(fx.root_file, EDITED)
    fx.wt_file.parent.mkdir(parents=True, exist_ok=True)
    tamper_text = fx.root_file.read_text()
    fx.wt_file.write_text(tamper_text)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t',
               GIT_AUTHOR_DATE='2001-01-01T00:00:00Z', GIT_COMMITTER_DATE='2001-01-01T00:00:00Z')
    subprocess.run(['git', '-C', str(fx.wt), 'add', '-A'], check=True, env=env)
    subprocess.run(['git', '-C', str(fx.wt), 'commit', '-qm', 'forged first add'], check=True, env=env)
    (fx.wt / 'more.txt').write_text('x')                                # something for `commit` to stage
    out = fx.commit()
    assert refused(out) and 'ordered prefix' in out and staged(fx) == ''
    out = fx.pr()
    assert refused(out) and 'ordered prefix' in out


# ---- m4: a source that cannot be built is an unreadable issue, not a traceback -------------------------------

def _no_source(sdlc_dir, config):
    raise RuntimeError('gh missing')


def test_acceptance_pr_refuses_when_the_issue_source_cannot_be_built(fx, monkeypatch):
    monkeypatch.setattr(work, '_acceptance_source', _no_source)
    out = fx.pr()
    assert refused(out) and 'could not read the issue' in out and 'gh missing' in out


def test_acceptance_commit_proceeds_when_the_issue_source_cannot_be_built(fx, monkeypatch, capsys):
    monkeypatch.setattr(work, '_acceptance_source', _no_source)
    out = fx.commit()
    assert not refused(out or '') and 'issue unreadable' in capsys.readouterr().err


# ---- second review F1: `work.py rebase` force-pushes local HEAD, so it runs the guard before any push --------

def _forge_local_first_add(f, tip_items=None):
    """Rewrite the local goal branch so its FIRST add of the record holds EDITED criteria (the pushed original is the
    baseline), optionally followed by a commit that puts ``tip_items`` back at the tip."""
    git(f.wt, 'reset', '-q', '--hard', 'main')
    f.wt_file.parent.mkdir(parents=True, exist_ok=True)
    f.wt_file.write_text(f.root_file.read_text())
    tamper(f.wt_file, EDITED)
    git(f.wt, 'add', '-A')
    git(f.wt, 'commit', '-qm', 'forged first add')
    if tip_items is not None:
        tamper(f.wt_file, tip_items)
        git(f.wt, 'add', '-A')
        git(f.wt, 'commit', '-qm', 'restore the tip')


def _remote_tip(f):
    return git(f.wt, 'ls-remote', 'origin', 'refs/heads/sdlc/42').split()[0]


def test_acceptance_rebase_refuses_force_pushing_a_rewritten_first_add(fx):
    """F1: commit, push, rewrite local history so the first add holds edited criteria, then `rebase` (the force-push
    `_reconcile_behind` and feature upkeep call unprompted): refused before any push, remote baseline unchanged."""
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    before = _remote_tip(fx)
    _forge_local_first_add(fx)
    out = fx_rebase(fx)
    assert refused(out) and '(nothing pushed)' in out, out
    assert _remote_tip(fx) == before


def test_acceptance_rebase_refuses_a_rewritten_first_add_even_when_the_tip_is_restored(fx):
    """F1, the first-add clause on its own: the tip carries the original criteria (so the tip clause is silent) but
    the pushed history's first add would hold the edited ones and become the next baseline."""
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    before = _remote_tip(fx)
    _forge_local_first_add(fx, tip_items=ORIG)
    out = fx_rebase(fx)
    assert refused(out) and 'first add' in out, out
    assert _remote_tip(fx) == before


def test_acceptance_rebase_refuses_pushing_a_tip_edit_over_an_intact_first_add(fx):
    """F1, the tip clause on its own: history is not rewritten (the first add is the pushed original), but a raw
    commit past the guarded `work.py commit` edits the criteria at HEAD, and `rebase` would push it."""
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    before = _remote_tip(fx)
    tamper(fx.wt_file, EDITED)
    git(fx.wt, 'commit', '-qam', 'edit the criteria')
    out = fx_rebase(fx)
    assert refused(out) and 'ordered prefix' in out and 'first add' not in out, out
    assert _remote_tip(fx) == before


def test_acceptance_rebase_refuses_pushing_a_deleted_record_while_the_root_copy_is_intact(fx):
    """F1: the bytes judged are HEAD's, never the root copy: a raw commit deleting the record is refused at rebase."""
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    git(fx.wt, 'rm', '-q', '.sdlc/acceptance/42.md')
    git(fx.wt, 'commit', '-qm', 'drop the record')
    out = fx_rebase(fx)
    assert refused(out) and 'missing' in out, out


def test_acceptance_rebase_still_pushes_an_intact_branch(fx):
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    out = fx_rebase(fx)
    assert out.startswith('rebased'), out


def test_acceptance_rebase_refusal_exits_4_through_work_main(fx):
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    _forge_local_first_add(fx)
    cfg = fx.sdlc / 'config.json'
    cfg.write_text('{"work": {"enabled": true}}')
    assert work.main(['work.py', 'rebase', str(fx.sdlc), '42']) == 4


def fx_rebase(f):
    return work.rebase(f.sdlc, {}, '42')


# ---- second review F1 (merge half): merge() runs the divergence check, fail-closed like its other checks -------

def test_acceptance_merge_parks_when_issue_criteria_diverge_from_record(fx):
    first_commit(fx)
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    calls = []
    out = work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: calls.append(argv) or '')
    assert out.startswith('PARK: ACCEPTANCE REFUSED') and 'issue' in out and '(nothing merged)' in out, out
    assert not calls and fx.parks == []            # merge's caller records the PARK line; the guard does not park twice


def test_acceptance_merge_parks_when_the_issue_is_unreadable(fx):
    first_commit(fx)
    fx.source.fail = True
    out = work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: '')
    assert out.startswith('PARK: ACCEPTANCE REFUSED') and 'could not read the issue' in out, out


def test_acceptance_merge_divergence_reads_the_branch_blob_when_both_copies_are_gone(fx):
    first_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    fx.root_file.unlink()
    fx.wt_file.unlink()
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = work.merge(fx.sdlc, {}, '42', run=lambda cwd, argv: '')
    assert out.startswith('PARK: ACCEPTANCE REFUSED') and 'issue' in out, out


# ---- second review F6: `pr` refuses a GitHub goal whose issue has criteria but which has no record -------------

def test_acceptance_pr_refuses_when_no_record_exists_but_the_issue_has_criteria(fx):
    fx.root_file.unlink()                          # before the first commit: no baseline, no copy anywhere
    assert not refused(fx.commit() or '')          # commit stays as it was (the residual is documented)
    out = fx.pr()
    assert refused(out) and 'no acceptance record' in out and '(nothing pushed)' in out, out
    assert fx.parks == []


def test_acceptance_pr_without_record_passes_when_the_issue_has_no_criteria_section(fx):
    fx.root_file.unlink()
    fx.source.body = 'Just prose, no checklist.\n'
    assert not refused(fx.pr())


# ---- second review F8: a goal already parked is not parked (and commented on) again --------------------------

class LabelledSource(Source):
    parked_label = 'sdlc:parked'

    def __init__(self, labels, **kw):
        super().__init__(**kw)
        self.labels = labels

    def fetch_body_labels_rest(self, goal):
        return {'body': self.body, 'labels': [{'name': n} for n in self.labels]}


def test_acceptance_divergence_does_not_repark_a_goal_already_parked(fx):
    fx.source = LabelledSource(['sdlc:parked'], body=BODY.replace('Third recovers.', 'Third explodes.'))
    out = fx.pr()
    assert refused(out) and 'already parked' in out and fx.parks == [], out


def test_acceptance_divergence_parks_a_goal_not_yet_parked(fx):
    fx.source = LabelledSource(['sdlc:goal'], body=BODY.replace('Third recovers.', 'Third explodes.'))
    out = fx.pr()
    assert refused(out) and 'Parked it' in out and len(fx.parks) == 1, out


# ---- second review F9: the `pr` divergence refusal exits 4 through `work.main` --------------------------------

def test_acceptance_pr_divergence_exits_4_through_work_main(fx):
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    (fx.sdlc / 'config.json').write_text('{"work": {"enabled": true}}')
    assert work.main(['work.py', 'pr', str(fx.sdlc), '42']) == 4
    assert len(fx.parks) == 1


# ---- second review F3: the divergence refusal names the lever that exists, not a re-record through unpark -------

def test_acceptance_divergence_refusal_names_restore_or_new_goal_not_rerecord(fx):
    fx.source.body = BODY.replace('Third recovers.', 'Third explodes.')
    out = fx.pr()
    assert refused(out) and "restores the issue's `## Done when` to match the record" in out, out
    assert 'closes this goal and files a new one' in out and 'agent never edits' in out
    assert 're-record' not in out and 'they resolve it with' not in out
    assert 'never edits' in fx.parks[0][1]                      # the park comment carries the same lever


def test_acceptance_rerecord_after_baseline_is_refused_so_it_is_not_a_lever(fx):
    """The probe behind F3: after a committed baseline, deleting both copies and re-recording from an edited issue is
    refused by clause B, which is why the documented lever is restore-the-issue or a new goal."""
    first_commit(fx)
    fx.root_file.unlink()
    fx.wt_file.unlink()
    fx.source.body = BODY_EDITED
    acc.record(fx.sdlc, '42', source=fx.source, config={})
    out = fx.commit()
    assert refused(out) and 'ordered prefix' in out and staged(fx) == '', out


# ---- second review F4: the divergence comparison is ORDERED; a reorder in the issue is a divergence ------------

SWAPPED = '## Done when\n- [ ] Second refuses bad input.\n- [ ] First works.\n- [ ] Third recovers.\n'


def test_acceptance_divergence_issue_reordering_criteria_is_a_divergence(fx):
    assert acc.issue_divergence(fx.root_file.read_text(), '42', SWAPPED)[0] == 'diverge'
    fx.source.body = SWAPPED
    out = fx.pr()
    assert refused(out) and 'no longer matches' in out and len(fx.parks) == 1, out


# ---- third review BLOCKING-1: the branch-tip baseline (an added criterion, once committed, is append-only too) --

def extend_and_commit(f, text='Fourth holds.', slice_='42b', reason='review found it'):
    acc.extend(f.sdlc, '42', slice_, reason, [text], config={}, source=f.source)
    out = f.commit()
    assert not refused(out), out
    return out


def strip_added(file):
    file.write_text(file.read_text().split('\n## Added criteria', 1)[0].rstrip('\n') + '\n')


def test_acceptance_tip_commit_refuses_deleting_committed_added_section(fx):
    first_commit(fx)
    extend_and_commit(fx)
    strip_added(fx.root_file)
    strip_added(fx.wt_file)
    out = fx.commit()
    assert refused(out) and 'committed on the goal branch' in out and staged(fx) == ''


def test_acceptance_tip_pr_refuses_deleting_committed_added_section(fx):
    first_commit(fx)
    extend_and_commit(fx)
    strip_added(fx.wt_file)
    out = fx.pr()
    assert refused(out) and 'committed on the goal branch' in out


def test_acceptance_tip_commit_refuses_rewriting_added_text_keeping_suffix(fx):
    first_commit(fx)
    extend_and_commit(fx)
    for f in (fx.root_file, fx.wt_file):
        f.write_text(f.read_text().replace('Fourth holds.', 'Fourth is optional.'))
    out = fx.commit()
    assert refused(out) and 'committed on the goal branch' in out and staged(fx) == ''


def test_acceptance_tip_pr_refuses_rewrite_for_a_goal_with_no_issue(fx, monkeypatch):
    """A local-file backlog (no issue source, so no extend-comment witness): only the tip baseline can see it."""
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc_dir, config: None)
    first_commit(fx)
    acc.extend(fx.sdlc, '42', '42b', 'review found it', ['Fourth holds.'], config={}, source=None)
    assert not refused(fx.commit())
    fx.wt_file.write_text(fx.wt_file.read_text().replace('Fourth holds.', 'Fourth is optional.'))
    out = fx.pr()
    assert refused(out) and 'committed on the goal branch' in out


def test_acceptance_tip_check_cli_refuses_deleted_added_section(fx):
    first_commit(fx)
    extend_and_commit(fx)
    assert acc.main(['check', str(fx.sdlc), '42']) == 0
    strip_added(fx.root_file)
    assert acc.main(['check', str(fx.sdlc), '42']) == 2


def test_acceptance_tip_remote_tip_is_read_when_the_local_branch_was_reset(fx):
    first_commit(fx)
    extend_and_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    git(fx.wt, 'reset', '-q', '--hard', 'HEAD~1')          # the local tip no longer holds the added criterion
    strip_added(fx.root_file)
    out = fx.commit()
    assert refused(out) and 'committed on the goal branch' in out


def test_acceptance_tip_second_extend_is_accepted_everywhere(fx):
    first_commit(fx)
    extend_and_commit(fx)
    extend_and_commit(fx, text='Fifth holds.', slice_='42c', reason='a second finding')
    assert not refused(fx.pr())
    assert acc.main(['check', str(fx.sdlc), '42']) == 0


def test_acceptance_tip_commit_refuses_rewriting_added_reason_keeping_text(fx):
    first_commit(fx)
    extend_and_commit(fx)
    for f in (fx.root_file, fx.wt_file):
        f.write_text(f.read_text().replace('reason: review found it', 'reason: never mind'))
    out = fx.commit()
    assert refused(out) and 'committed on the goal branch' in out and staged(fx) == ''


def test_acceptance_tip_deletion_refused_when_only_the_tip_is_readable(fx):
    """Clause T's own deletion leg: the first add was unparseable (no first-add baseline, clause D silent), the
    record committed since is valid, and deleting it is still refused."""
    good = fx.root_file.read_text()
    for f in (fx.root_file,):
        f.write_text('not a record\n')
    first_commit(fx)                                       # nothing to compare with: lands
    fx.root_file.write_text(good)
    fx.wt_file.write_text(good)
    assert not refused(fx.commit())
    fx.root_file.unlink()
    fx.wt_file.unlink()
    out = fx.commit()
    assert refused(out) and 'missing' in out and staged(fx) == ''


def test_acceptance_tip_commit_refuses_reordering_committed_added_criteria(fx):
    first_commit(fx)
    acc.extend(fx.sdlc, '42', '42b', 'review found it', ['Fourth holds.', 'Fifth holds.'], config={}, source=fx.source)
    assert not refused(fx.commit())
    for f in (fx.root_file, fx.wt_file):
        head, rows = f.read_text().split('## Added criteria\n', 1)
        lines = rows.splitlines(True)
        f.write_text(head + '## Added criteria\n' + lines[1] + lines[0])
    out = fx.commit()
    assert refused(out) and 'committed on the goal branch' in out and staged(fx) == ''


def test_acceptance_tip_rebase_pushes_a_branch_whose_added_criterion_was_committed_after_the_first_add(fx):
    """The rebase guard's first-add leg judges HEAD's first add, which predates every extend: it must not be held
    to the tip (``tip=False``), or every extended goal would refuse to rebase."""
    first_commit(fx)
    extend_and_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    out = fx_rebase(fx)
    assert out.startswith('rebased'), out


def test_acceptance_tip_rebase_refuses_a_raw_commit_dropping_a_pushed_added_criterion(fx):
    first_commit(fx)
    extend_and_commit(fx)
    git(fx.wt, 'push', '-q', 'origin', 'sdlc/42')
    before = _remote_tip(fx)
    strip_added(fx.wt_file)
    git(fx.wt, 'commit', '-qam', 'drop the added criterion')
    out = fx_rebase(fx)
    assert refused(out) and 'committed on the goal branch' in out, out
    assert _remote_tip(fx) == before


# ---- third review S2: an append-only refusal parks; a read failure does not ------------------------------------

def test_acceptance_commit_tamper_refusal_parks(fx):
    first_commit(fx)
    fx.source.body = BODY_EDITED
    tamper(fx.root_file, EDITED)
    tamper(fx.wt_file, EDITED)
    out = fx.commit()
    assert refused(out) and 'Parked it' in out and staged(fx) == '' and len(fx.parks) == 1


def test_acceptance_guard_that_could_not_run_does_not_park(fx, monkeypatch):
    first_commit(fx)

    def boom(*a):
        raise OSError('disk gone')
    monkeypatch.setattr(work, '_acceptance_rel', boom)
    out = fx.pr()
    assert refused(out) and 'could not run' in out and 'Parked' not in out and fx.parks == []


def test_acceptance_missing_extend_comment_parks_but_unreadable_history_does_not(fx):
    first_commit(fx)
    append_both(fx)
    out = fx.pr()                                    # well-formed suffix, no extend comment: a finding
    assert refused(out) and 'extend comment' in out and 'Parked it' in out and len(fx.parks) == 1

    def boom(goal):
        raise RuntimeError('rate limited')
    fx.source.fetch_comments_strict = boom
    out = fx.pr()
    assert refused(out) and 'could not read the issue comments' in out and 'Parked' not in out
    assert len(fx.parks) == 1


def test_acceptance_tamper_park_failure_says_still_claimed(fx, monkeypatch):
    def boom(*a):
        raise RuntimeError('ledger down')
    monkeypatch.setattr(work, '_park_goal', boom)
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    out = fx.pr()
    assert refused(out) and 'STILL CLAIMED' in out


def test_acceptance_tamper_not_parked_again_when_already_parked(fx):
    fx.source.fetch_body_labels_rest = lambda goal: {'labels': [{'name': 'sdlc:parked'}]}
    fx.source.parked_label = 'sdlc:parked'
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    out = fx.pr()
    assert refused(out) and 'already parked' in out and fx.parks == []


def test_acceptance_tamper_parks_a_goal_with_no_issue_source(fx, monkeypatch):
    """A local-file backlog has no issue, but it still has a park: the loop's own source records it."""
    monkeypatch.setattr(work, '_acceptance_source', lambda sdlc_dir, config: None)
    sources = []
    monkeypatch.setattr(work, '_park_goal', lambda sdlc_dir, source, goal, detail: sources.append(source))
    first_commit(fx)
    tamper(fx.wt_file, ORIG, added='- [ ] Naked line.\n')
    out = fx.pr()
    assert refused(out) and 'Parked it' in out and len(sources) == 1 and sources[0] is not None


def test_acceptance_commit_refuses_root_only_criteria_edit_with_hash_line_unchanged(fx):
    """Third review m3: the root's criteria edited while its `criteria_sha256` line is left alone. The worktree copy
    is the clean original, so only `_acceptance_is_extension`'s own criteria comparison can see the two differ."""
    first_commit(fx)
    tamper(fx.root_file, EDITED, fix_hash=False)
    out = fx.commit()
    assert refused(out) and 'differ' in out and staged(fx) == ''
