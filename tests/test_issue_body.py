"""#2222 T5/T6: the shared issue-body helper and every writer that adopts it."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ib = load('issue_body')
acc = load('acceptance')
CRIT = ['First works.', 'Second refuses bad input.', 'Third recovers.']


def test_issue_body_render_emits_done_when_section():
    body = ib.render('Context line.', CRIT)
    assert '## Done when\n- [ ] First works.\n' in body and body.startswith('Context line.')


def test_issue_body_render_refuses_zero_criteria():
    for empty in ([], ['', '  '], None):
        with pytest.raises(ValueError, match='at least one'):
            ib.render('ctx', empty)
    with pytest.raises(ValueError):                       # two is below acceptance's own floor of three
        ib.render('ctx', CRIT[:2])


def test_issue_body_check_lists_missing_sections():
    body = ib.render('ctx', CRIT)
    assert ib.check(body, ['Done when']) == []
    assert ib.check(body, ['Done when', 'Verification command']) == ['Verification command']
    assert ib.check('no sections here', ['Done when']) == ['Done when']
    assert ib.check(ib.render('ctx', CRIT, verify_command='pytest -q'), ['Verification command']) == []


def test_issue_body_check_accepts_alias_heading():
    assert ib.check(ib.render('c', CRIT).replace('## Done when', '## Acceptance criteria'), ['Done when']) == []


def test_issue_body_criteria_from_requirements_served_lines():
    served = ['- R1: First works.', '* [ ] Second refuses bad input.', '3. Third recovers.', '#12', '', '- R1: First works.']
    assert ib.criteria_from_served(served) == CRIT


def test_issue_body_rendered_body_parses_with_acceptance_criteria():
    assert acc.criteria(ib.render('ctx\n\n**Area:** `x`', CRIT, verify_command='pytest -q')) == CRIT


def test_issue_body_handoff_open_has_done_when_section():
    h = load('handoff')
    body = h.issue_body('42', 'billing', 'a schema change', 'https://example/issues/42')
    assert acc.criteria(body) is not None and len(acc.criteria(body)) == 3
    assert '**Blocked goal:** https://example/issues/42' in body and '**Done when:**' not in body


def test_issue_body_handoff_track_has_done_when_section():
    h = load('handoff')
    for blocks in (True, False):
        body = h._tracked_issue_body('42', 'billing', 'a follow-up', blocks)
        assert acc.criteria(body) is not None, blocks


def test_issue_body_handoff_body_file_checked_not_generated(tmp_path, capsys):
    """`track --body-file` never rewrites the caller's body; it reports a missing section."""
    h = load('handoff')
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir()
    (tmp_path / 'b.md').write_text('only prose, no criteria\n')
    src = (SCRIPTS / 'handoff.py').read_text()
    assert 'absent = _issue_body_module().check(body, ["Done when"])' in src
    assert 'handoff.py track: --body-file has no readable `## Done when` section' in src
    # the check is a report, not a rewrite: nothing assigns to `body` after it
    after = src.split('absent = _issue_body_module().check(body', 1)[1].split('config = ledger._config(sdlc_dir)', 1)[0]
    assert 'body =' not in after


# ---- T6: compile_plan, goal-design slices, scope and brainstorm prose ----------------------------------------

import json
import subprocess
import sys

GD_SCRIPT = ROOT / 'skills/sigma-goal-design/scripts/goal_design.py'
FR1 = 'WHEN a user logs in, THE system SHALL show the home page'
FR2 = 'IF the disk is full, THEN THE writer SHALL refuse the write'


def _art():
    return ('# Design: x\n\n## Intent\nsome intent\n\n## Premise check\n- None.\n\n'
            '## Functional requirements\n- **FR-1.** %s\n- **FR-2.** %s\n\n## Queries run\n- `x`\n\n'
            '## Detailed design\ntext\n\n## Slice-count estimate\n| Slice | Covers | Requirements | Depends on |\n'
            '| --- | --- | --- | --- |\n| 1 | a | FR-1 | — |\n| 2 | b | FR-2 | 1 |\n\n## Handoff\n- nothing\n' % (FR1, FR2))


def _gd():
    spec = importlib.util.spec_from_file_location('goal_design_t6', GD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Src:
    def __init__(self):
        self.created = []

    def create_dependency(self, title, body, assignee, labels=None, goal_label=False):
        self.created.append({'title': title, 'body': body})
        return len(self.created)


def test_issue_body_compile_plan_scope_issue_has_criteria():
    cp = load('compile_plan') if (SCRIPTS / 'compile_plan.py').exists() else None
    spec = importlib.util.spec_from_file_location('compile_plan_t6', ROOT / 'skills/sigma-scope/scripts/compile_plan.py')
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    src = _Src()
    plan = {'issues': [{'key': 'a', 'title': 'T', 'body': 'scope text', 'criteria': CRIT}]}
    cp.compile_plan('/x/.sdlc', {}, plan, source=src)
    assert acc.criteria(src.created[0]['body']) == CRIT and src.created[0]['body'].startswith('scope text')


def test_issue_body_compile_plan_refuses_item_without_criteria():
    spec = importlib.util.spec_from_file_location('compile_plan_t6b', ROOT / 'skills/sigma-scope/scripts/compile_plan.py')
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    src = _Src()
    plan = {'issues': [{'key': 'a', 'title': 'ok', 'body': 'x', 'criteria': CRIT}, {'key': 'b', 'title': 'bare', 'body': 'x'}]}
    with pytest.raises(ValueError, match="no acceptance criteria"):
        cp.compile_plan('/x/.sdlc', {}, plan, source=src)
    assert src.created == []                                 # zero side effects: not even the good sibling


def test_issue_body_goal_design_slice_derives_done_when_from_requirements_served():
    gd = _gd()
    result = gd.check_requirements(_art())
    block = gd.done_when_block(result, '1')
    got = acc.criteria(block)
    assert got[0] == FR1 and len(got) == 3 and got[1:] == list(gd._DONE_WHEN_PAD)
    assert gd.done_when_block(result, '2')[:0] == '' and acc.criteria(gd.done_when_block(result, '2'))[0] == FR2


def test_issue_body_goal_design_check_plan_accepts_derived_section(tmp_path):
    gd = _gd()
    result = gd.check_requirements(_art())
    plan = {'issues': []}
    for label in ('1', '2'):
        plan['issues'].append({'key': label, 'title': 't%s' % label,
                               'body': 'scope\n\n' + gd.served_block(result, label) + '\n' + gd.done_when_block(result, label)})
    assert gd.check_plan(result, plan) == []
    art, pl = tmp_path / 'd.md', tmp_path / 'p.json'
    art.write_text(_art())
    pl.write_text(json.dumps(plan))
    out = subprocess.run([sys.executable, str(GD_SCRIPT), 'served', str(art), '--slice', '1', '--done-when'],
                         capture_output=True, text=True)
    assert out.returncode == 0 and '## Requirements served' in out.stdout and '## Done when' in out.stdout


def test_issue_body_scope_and_brainstorm_prose_name_the_helper():
    for rel in ('skills/sigma-scope/SKILL.md', 'skills/sigma-brainstorm/SKILL.md'):
        text = (ROOT / rel).read_text()
        assert 'issue_body.py' in text and 'Done when' in text, rel
