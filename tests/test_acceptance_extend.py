"""#2222 T9/T10: `acceptance.py extend` (the only verb that adds) and how review sees added criteria."""
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
BODY = '## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n'


def load(name):
    spec = importlib.util.spec_from_file_location(name + '_ext', SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acc = load('acceptance')


class Source:
    def __init__(self, fail_post=False):
        self.comments, self.fail_post, self.posts = [], fail_post, 0

    def fetch_title_body(self, goal):
        return {'title': 'Goal', 'body': BODY}

    def fetch_comments_strict(self, goal):
        return {'comments': self.comments}

    def note(self, goal, text):
        if self.fail_post:
            raise RuntimeError('post failed')
        self.posts += 1
        self.comments.append({'body': text})


@pytest.fixture
def rec(tmp_path):
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir()
    file = acc.record(sdlc, '42', source=Source(), config={})
    return sdlc, file


def extend(sdlc, source=None, **kw):
    args = dict(slice_ref='42b', reason='review found the gap', criteria_list=['Fourth holds.'])
    args.update(kw)
    return acc.extend(sdlc, '42', args['slice_ref'], args['reason'], args['criteria_list'], config={}, source=source)


def test_acceptance_extend_requires_slice_and_reason(rec):
    sdlc, file = rec
    before = file.read_bytes()
    for kw in ({'slice_ref': None}, {'slice_ref': ''}, {'reason': None}):
        with pytest.raises(ValueError, match='non-blank'):
            extend(sdlc, **kw)
    with pytest.raises(ValueError, match='at least one'):
        extend(sdlc, criteria_list=[])
    assert file.read_bytes() == before


def test_acceptance_extend_rejects_empty_or_blank_reason(rec):
    sdlc, file = rec
    for reason in ('', '   ', '\t\n'):
        with pytest.raises(ValueError):
            extend(sdlc, reason=reason)
    with pytest.raises(ValueError):
        extend(sdlc, slice_ref='a;b')
    assert acc.read(sdlc, '42')['added'] == []


def test_acceptance_extend_appends_with_visible_suffix(rec):
    sdlc, file = rec
    extend(sdlc)
    text = file.read_text()
    assert text.endswith('## Added criteria\n- [ ] Fourth holds. (added: 42b; reason: review found the gap)\n')
    assert acc.read(sdlc, '42')['added'] == [{'text': 'Fourth holds.', 'slice': '42b', 'reason': 'review found the gap'}]
    extend(sdlc, slice_ref='42c', reason='another', criteria_list=['Fifth holds.'])
    assert [x['text'] for x in acc.read(sdlc, '42')['added']] == ['Fourth holds.', 'Fifth holds.']


def test_acceptance_extend_leaves_original_block_byte_identical(rec):
    sdlc, file = rec
    before = file.read_text()
    extend(sdlc)
    after = file.read_text()
    assert after.startswith(before.rstrip('\n'))
    assert acc.read(sdlc, '42')['criteria'] == ['First works.', 'Second refuses bad input.', 'Third recovers.']
    assert acc.read(sdlc, '42')['criteria_sha256'] == acc.criteria_hash(acc.read(sdlc, '42')['criteria'])


def test_acceptance_extend_posts_comment_with_extend_marker_on_issue(rec):
    sdlc, _ = rec
    src = Source()
    extend(sdlc, source=src)
    marker = acc.extend_marker('Fourth holds.', '42b', 'review found the gap')
    assert src.posts == 1 and marker in src.comments[0]['body']
    assert 'Fourth holds.' in src.comments[0]['body'] and '42b' in src.comments[0]['body']
    assert acc.extend_comment_refusal(sdlc, '42', src) is None          # exactly what `work.py pr` will look for


def test_acceptance_extend_comment_retry_is_idempotent(rec):
    sdlc, file = rec
    src = Source()
    marker = acc.extend_marker('Fourth holds.', '42b', 'review found the gap')
    src.comments.append({'body': marker + '\nalready posted by a crashed earlier run'})
    extend(sdlc, source=src)
    assert src.posts == 0 and acc.read(sdlc, '42')['added'][0]['text'] == 'Fourth holds.'


def test_acceptance_extend_post_failure_writes_nothing(rec):
    sdlc, file = rec
    before = file.read_bytes()
    with pytest.raises(RuntimeError):
        extend(sdlc, source=Source(fail_post=True))
    assert file.read_bytes() == before


def test_acceptance_extend_syncs_worktree_copy(rec, tmp_path):
    sdlc, file = rec
    wt = tmp_path / 'wt'
    target = wt / '.sdlc/acceptance/42.md'
    target.parent.mkdir(parents=True)
    target.write_bytes(file.read_bytes())
    work = sdlc / 'state/work'
    work.mkdir(parents=True)
    (work / '42.json').write_text(json.dumps({'worktree': str(wt)}))
    extend(sdlc)
    assert target.read_bytes() == file.read_bytes() and 'Fourth holds.' in target.read_text()


def test_acceptance_extend_refuses_when_original_already_tampered(rec):
    sdlc, file = rec
    text = file.read_text().replace('Second refuses bad input.', 'Second refuses nothing.')
    file.write_text(text)                                   # hash now disagrees with the block (clause H)
    before = file.read_bytes()
    with pytest.raises(ValueError, match='criteria_sha256'):
        extend(sdlc)
    assert file.read_bytes() == before


def test_acceptance_extend_refuses_eleventh_added_criterion(rec):
    sdlc, file = rec
    for i in range(10):
        extend(sdlc, criteria_list=['Added %d.' % i])
    before = file.read_bytes()
    with pytest.raises(ValueError, match='new goal'):
        extend(sdlc, criteria_list=['One too many.'])
    assert file.read_bytes() == before and len(acc.read(sdlc, '42')['added']) == 10


def test_acceptance_extend_invalidates_verify_evidence_by_design(rec):
    sdlc, file = rec
    state = load('state')
    old_digest = acc.digest(sdlc, '42')
    evidence = state.evidence_path(sdlc, '42')
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({'acceptance_sha256': old_digest}))
    extend(sdlc)
    assert evidence.exists() and acc.digest(sdlc, '42') != old_digest      # the evidence now binds stale bytes


def test_acceptance_extend_local_goal_has_no_comment(tmp_path):
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir()
    goal = tmp_path / 'g.md'
    goal.write_text('---\n---\n' + BODY)
    acc.record(sdlc, str(goal), source=Source(), config={})
    src = Source()
    acc.extend(sdlc, str(goal), '1', 'because', ['Local extra.'], config={}, source=src)
    assert src.posts == 0 and acc.read(sdlc, str(goal))['added'][0]['text'] == 'Local extra.'


def test_acceptance_extend_no_way_to_replace_a_criterion():
    verbs = [n for n in dir(acc) if not n.startswith('_') and callable(getattr(acc, n))]
    assert 'extend' in verbs and not [v for v in verbs if re.search(r'replace|remove|edit|delete|amend', v)]
    import inspect
    assert not [p for p in inspect.signature(acc.extend).parameters if re.search(r'force|replace|index|remove', p)]


def test_acceptance_extend_write_is_atomic_when_replace_fails(rec, monkeypatch):
    """Deterministic in-process control at the exact seam (AGENTS.md): the final rename fails, so the old record
    must survive whole and no temp file may be left behind."""
    sdlc, file = rec
    before = file.read_bytes()

    def boom(src, dst):
        raise OSError('simulated replace failure')
    monkeypatch.setattr(os, 'replace', boom)
    with pytest.raises(OSError):
        extend(sdlc)
    monkeypatch.undo()
    assert file.read_bytes() == before
    assert [p.name for p in file.parent.iterdir()] == ['42.md']


def test_acceptance_extend_cli_gesture_is_the_documented_one(rec):
    sdlc, file = rec
    proc = subprocess.run([sys.executable, str(SCRIPTS / 'acceptance.py'), 'extend', str(sdlc), '42', '--slice', '42b',
                           '--reason', 'review found the gap', '--criterion', 'Fourth holds.', '--criterion', 'Fifth holds.'],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert [x['text'] for x in acc.read(sdlc, '42')['added']] == ['Fourth holds.', 'Fifth holds.']
    bad = subprocess.run([sys.executable, str(SCRIPTS / 'acceptance.py'), 'extend', str(sdlc), '42', '--criterion', 'x'],
                         capture_output=True, text=True)
    assert bad.returncode == 2 and 'REFUSED' in bad.stderr


# ---- T10: review surfaces every added criterion ---------------------------------------------------------------

rc = load('review_context')


def brief(sdlc, tmp_path):
    return rc.brief(str(sdlc), '42', 'pr-review', artifact='42', repo_root=str(tmp_path), source=Source())


def test_acceptance_review_brief_lists_every_added_criterion_with_slice_and_reason(rec, tmp_path):
    sdlc, _ = rec
    extend(sdlc)
    extend(sdlc, slice_ref='42c', reason='second gap', criteria_list=['Fifth holds.'])
    out = brief(sdlc, tmp_path)
    assert '- Fourth holds. — slice 42b; reason: review found the gap' in out
    assert '- Fifth holds. — slice 42c; reason: second gap' in out
    assert 'Reasonless added criteria: 0' in out


def test_acceptance_review_brief_marks_reasonless_added_criterion_as_block(rec, tmp_path):
    sdlc, file = rec
    file.write_text(file.read_text() + '\n## Added criteria\n- [ ] Naked line.\n')
    out = brief(sdlc, tmp_path)
    assert '- Naked line. — NO SLICE OR REASON (a block)' in out
    assert 'Reasonless added criteria: 1 (above zero is a block' in out


def test_acceptance_review_brief_unchanged_when_nothing_added(rec, tmp_path):
    sdlc, _ = rec
    assert rc.added_criteria_block(sdlc, '42') == ''
    out = brief(sdlc, tmp_path)
    assert 'Added criteria' not in out and 'Reasonless added' not in out
    assert '## Recorded acceptance (P1 intent)' in out


def test_acceptance_review_brief_names_original_criteria_unchanged_check(rec, tmp_path):
    sdlc, file = rec
    extend(sdlc)
    assert 'Original criteria unchanged since the first record (`acceptance.py check`): yes' in brief(sdlc, tmp_path)
    file.write_text(file.read_text().replace('Second refuses bad input.', 'Second refuses nothing.'))
    assert 'unchanged since the first record (`acceptance.py check`): NO, ' in brief(sdlc, tmp_path)
