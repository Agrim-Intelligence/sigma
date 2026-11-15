"""#2222 T11: `/sigma-init` scaffolds the goal issue template (never overwriting) and `/sigma-doctor` reports readiness."""
import importlib.util
import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / 'skills/sigma-init/scripts/sdlc_init.py'
DOCTOR = ROOT / 'skills/sigma-doctor/scripts/doctor.py'


def mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def init():
    return mod(INIT, 'sdlc_init_gr')


_DOCTOR = mod(DOCTOR, 'doctor_gr')


def doctor():
    return _DOCTOR           # one instance: `_RawFailure` must be the same class on both sides of the fake


def git_dir(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    return tmp_path


def test_goal_readiness_init_scaffolds_issue_template(tmp_path):
    created, skipped = init().scaffold_github(git_dir(tmp_path))
    assert 'ISSUE_TEMPLATE/goal.md' in created
    assert (tmp_path / '.github/ISSUE_TEMPLATE/goal.md').is_file()


def test_goal_readiness_init_never_overwrites_existing_template(tmp_path):
    git_dir(tmp_path)
    target = tmp_path / '.github/ISSUE_TEMPLATE/goal.md'
    target.parent.mkdir(parents=True)
    target.write_text('MY OWN GOAL TEMPLATE')
    created, skipped = init().scaffold_github(tmp_path)
    assert 'ISSUE_TEMPLATE/goal.md' in skipped and 'ISSUE_TEMPLATE/goal.md' not in created
    assert target.read_text() == 'MY OWN GOAL TEMPLATE'
    init().scaffold_github(tmp_path)                                  # twice: still untouched
    assert target.read_text() == 'MY OWN GOAL TEMPLATE'


def test_goal_readiness_init_template_has_agent_ready_sections():
    text = (ROOT / 'skills/sigma-init/github-templates/ISSUE_TEMPLATE/goal.md.tmpl').read_text()
    assert re.search(r'(?m)^## Done when$', text) and re.search(r'(?m)^## Verification command$', text)
    assert text.startswith('---\n') and 'labels: ["goal"]' in text
    assert 'sdlc:' not in text.split('\n---\n', 1)[0]    # m7: a template never writes loop membership (label model)
    acceptance = mod(ROOT / 'skills/sigma-loop/scripts/acceptance.py', 'acc_gr')
    with pytest.raises(ValueError):                       # an unfilled template is refused, not recorded as criteria
        acceptance.criteria(text)


class Run:
    """A doctor `run(argv)` fake: serves the REST issue list and git's check-ignore answer."""
    def __init__(self, issues, ignored=False, fail=False):
        self.issues, self.ignored, self.fail, self.calls = issues, ignored, fail, []

    def __call__(self, argv):
        self.calls.append(list(argv))
        d = doctor()
        if argv[:2] == ['git', '-C'] and 'check-ignore' in argv:
            return '.gitignore:3:.sdlc/acceptance/\t.sdlc/acceptance/probe.md' if self.ignored else d._RawFailure('')
        if self.fail:
            return d._RawFailure('HTTP 500')
        if argv[0] == 'gh' and argv[1] == 'api':
            fields = dict(str(a).split('=', 1) for a in argv if '=' in str(a))
            return json.dumps(self.issues if fields.get('page', '1') == '1' else [])
        return ''


GOOD = '## Done when\n- [ ] A.\n- [ ] B.\n- [ ] C.\n'
CFG = {'discovery': {'source': 'github', 'github': {'repo': 'acme/widget'}}}


def rows(cfg, run, github=True, tmp=None):
    d = doctor()
    base = (tmp or pathlib.Path('/nonexistent')) / '.sdlc'
    return d._goal_readiness_rows(cfg, base, (cfg.get('discovery') or {}).get('github') or {}, run, github)


def names(rs):
    return [r['name'] for r in rs]


def test_goal_readiness_doctor_reports_mode(tmp_path):
    assert 'goal readiness mode: warn' in names(rows(CFG, Run([]), tmp=tmp_path))
    cfg = {**CFG, 'goal_readiness': {'mode': 'require'}}
    assert 'goal readiness mode: require' in names(rows(cfg, Run([]), tmp=tmp_path))
    bad = rows({**CFG, 'goal_readiness': {'mode': 'nope'}}, Run([]), tmp=tmp_path)
    assert len(bad) == 1 and bad[0]['ok'] is False and 'mode must be one of' in bad[0]['fix']


def test_goal_readiness_doctor_counts_open_goals_lacking_criteria(tmp_path):
    issues = [{'number': 1, 'title': 'a', 'body': GOOD}, {'number': 2, 'title': 'b', 'body': 'prose'},
              {'number': 3, 'title': 'c', 'body': ''}]
    out = names(rows(CFG, Run(issues), tmp=tmp_path))
    assert any('2 of 3 checked open goal(s) lack `## Done when`' in n for n in out), out


def test_goal_readiness_doctor_count_is_bounded_at_fifty_and_says_so(tmp_path):
    issues = [{'number': i, 'title': 't', 'body': 'prose'} for i in range(1, 80)]
    run = Run(issues)
    out = names(rows(CFG, run, tmp=tmp_path))
    assert any('50 of 50 checked' in n and '50+ unchecked' in n for n in out), out
    api = [c for c in run.calls if c[:2] == ['gh', 'api']]
    assert api and all('per_page=50' in c for c in api) and len(api) == 1       # one REST list request, no more


def test_goal_readiness_doctor_unreadable_source_is_reported_not_zero(tmp_path):
    rs = rows(CFG, Run([], fail=True), tmp=tmp_path)
    bad = [r for r in rs if 'open goals checked' in r['name']]
    assert bad and bad[0]['ok'] is False and 'UNKNOWN, not zero' in bad[0]['fix']
    assert not any('0 of 0' in r['name'] for r in rs)


def test_goal_readiness_doctor_surfaces_gitignored_acceptance_dir(tmp_path):
    rs = rows({}, Run([], ignored=True), github=False, tmp=tmp_path)
    row = [r for r in rs if 'acceptance records are tracked' in r['name']]
    assert row and row[0]['ok'] is False and 'baseline: unavailable' in row[0]['fix']
    assert not [r for r in rows({}, Run([], ignored=False), github=False, tmp=tmp_path) if 'tracked' in r['name']]


def test_goal_readiness_doctor_off_reads_no_issues(tmp_path):
    run = Run([{'number': 1, 'title': 'a', 'body': 'x'}])
    out = rows({**CFG, 'goal_readiness': {'mode': 'off'}}, run, tmp=tmp_path)
    assert 'goal readiness mode: off' in names(out) and not [c for c in run.calls if c[:2] == ['gh', 'api']]
