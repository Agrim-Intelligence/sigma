"""#2222 T10 / D11: a reasonless added criterion blocks an approving REVIEW verdict, with review.independent on or off."""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'
BODY = '## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n'


def load(name):
    spec = importlib.util.spec_from_file_location(name + '_rg', SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reviewer = load('reviewer')
acc = load('acceptance')


class Source:
    def fetch_title_body(self, goal):
        return {'title': 'G', 'body': BODY}

    def fetch_comments_strict(self, goal):
        return {'comments': []}

    def note(self, goal, text):
        pass


def make(tmp_path, added='', independent=False, config_text=None):
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir()
    file = acc.record(sdlc, '42', source=Source(), config={})
    if added:
        file.write_text(file.read_text() + '\n## Added criteria\n' + added)
    cfg = config_text if config_text is not None else json.dumps({'review': {'independent': independent}})
    (sdlc / 'config.json').write_text(cfg)
    return sdlc


REASONLESS = '- [ ] Naked line.\n'
REASONED = '- [ ] Fourth holds. (added: 42b; reason: found in review)\n'


@pytest.mark.parametrize('independent', [False, True])
def test_acceptance_reviewer_gate_refuses_approving_review_with_reasonless_added_criterion(tmp_path, independent):
    sdlc = make(tmp_path, REASONLESS, independent=independent)
    out = reviewer.independent_run_refusal(sdlc, '42', 'review')
    assert out and 'Naked line.' in out


def test_acceptance_reviewer_gate_refusal_names_the_extend_gesture(tmp_path):
    out = reviewer.independent_run_refusal(make(tmp_path, REASONLESS), '42', 'review')
    assert 'acceptance.py extend' in out and '--slice' in out and '--reason' in out and '/sigma-unpark' in out


def test_acceptance_reviewer_gate_passes_when_every_added_criterion_has_a_reason(tmp_path):
    assert reviewer.independent_run_refusal(make(tmp_path, REASONED), '42', 'review') is None


def test_acceptance_reviewer_gate_passes_when_no_added_section(tmp_path):
    assert reviewer.independent_run_refusal(make(tmp_path), '42', 'review') is None


def test_acceptance_reviewer_gate_applies_to_review_phase_only(tmp_path):
    sdlc = make(tmp_path, REASONLESS)
    assert reviewer.independent_run_refusal(sdlc, '42', 'plan_review') is None


def test_acceptance_reviewer_gate_does_not_read_config_for_the_decision(tmp_path):
    """Damaged config: the generic path refuses with its own text; the reasonless clause fires FIRST and its own
    text wins, so its decision cannot depend on what the config says."""
    damaged = make(tmp_path, REASONLESS, config_text='{ not json')
    out = reviewer.independent_run_refusal(damaged, '42', 'review')
    assert 'Naked line.' in out and 'unreadable' not in out
    clean = tmp_path / 'b'
    clean.mkdir()
    other = make(clean, REASONED, config_text='{ not json')
    assert 'unreadable' in reviewer.independent_run_refusal(other, '42', 'review')      # generic path still there
