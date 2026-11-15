"""#2222 T1: alias heading, two-pass criteria(), baseline hash, `## Added criteria` section."""
import hashlib
import importlib.util
import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/sigma-loop/scripts'


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ITEMS = ['First works.', 'Second refuses bad input.', 'Third recovers.']
CANON = '## Done when\n' + ''.join('- [ ] %s\n' % i for i in ITEMS)
ALIAS = '## Acceptance criteria\n' + ''.join('- [ ] %s\n' % i for i in ITEMS)


class Source:
    def __init__(self, body):
        self.body, self.comments = body, []

    def fetch_title_body(self, goal):
        return {'title': 'Goal', 'body': self.body}

    def fetch_comments_strict(self, goal):
        return {'comments': self.comments}

    def note(self, goal, text):
        self.comments.append({'body': text})


def record(tmp_path, body, goal='42'):
    sdlc = tmp_path / '.sdlc'
    sdlc.mkdir(exist_ok=True)
    a = load('acceptance')
    file = a.record(sdlc, goal, source=Source(body), config={})
    return a, sdlc, file


def test_acceptance_alias_heading_recorded_as_done_when(tmp_path):
    a, sdlc, file = record(tmp_path, ALIAS)
    text = file.read_text()
    assert '## Done when\n' in text and 'Acceptance criteria' not in text
    assert a.read(sdlc, '42')['criteria'] == ITEMS


def test_acceptance_body_with_neither_heading_still_refused(tmp_path):
    with pytest.raises(ValueError):
        record(tmp_path, '## Summary\nnothing here\n')


def test_acceptance_both_headings_canonical_wins(tmp_path):
    other = '## Acceptance criteria\n- [ ] A.\n- [ ] B.\n- [ ] C.\n'
    a, sdlc, _ = record(tmp_path, CANON + '\n' + other)
    assert a.read(sdlc, '42')['criteria'] == ITEMS


def test_acceptance_alias_before_canonical_canonical_still_wins(tmp_path):
    other = '## Acceptance criteria\n- [ ] A.\n- [ ] B.\n- [ ] C.\n\n'
    a, sdlc, _ = record(tmp_path, other + CANON)
    assert a.read(sdlc, '42')['criteria'] == ITEMS


def test_acceptance_record_stores_criteria_sha256(tmp_path):
    a, sdlc, _ = record(tmp_path, CANON)
    want = a.criteria_hash(ITEMS)
    assert a.read(sdlc, '42')['criteria_sha256'] == want
    assert re.fullmatch(r'[0-9a-f]{64}', want)


ADDED = ('## Done when\n- [ ] One.\n- [ ] Two.\n- [ ] Three.\n\n## Added criteria\n'
         '- [ ] Four holds. (added: 42b; reason: review found it)\n'
         '- [x] Five holds too. (added: 42c; reason: scope; with a semicolon)\n')


def _parse(text, goal='42'):
    a = load('acceptance')
    return a.parse('---\nkind: "acceptance"\ngoal: "%s"\nverify_command: null\n---\n%s' % (goal, text), goal)


def test_acceptance_added_section_parses_slice_and_reason():
    rec = _parse(ADDED)
    assert rec['criteria'] == ['One.', 'Two.', 'Three.']
    assert rec['added'] == [
        {'text': 'Four holds.', 'slice': '42b', 'reason': 'review found it'},
        {'text': 'Five holds too.', 'slice': '42c', 'reason': 'scope; with a semicolon'}]


def test_acceptance_reasonless_added_line_parses_with_reason_none():
    rec = _parse('## Done when\n- [ ] One.\n- [ ] Two.\n- [ ] Three.\n\n## Added criteria\n- [ ] Naked line.\n')
    assert rec['added'] == [{'text': 'Naked line.', 'slice': None, 'reason': None}]


def test_acceptance_added_ceiling_ten():
    body = '## Done when\n- [ ] One.\n- [ ] Two.\n- [ ] Three.\n\n## Added criteria\n'
    ok = body + ''.join('- [ ] A%d. (added: s; reason: r)\n' % i for i in range(10))
    assert len(_parse(ok)['added']) == 10
    over = body + ''.join('- [ ] A%d. (added: s; reason: r)\n' % i for i in range(11))
    with pytest.raises(ValueError, match='at most 10'):
        _parse(over)


def test_acceptance_old_record_without_hash_still_parses():
    rec = _parse(CANON)
    assert rec['criteria_sha256'] is None and rec['added'] == []


def test_acceptance_record_with_added_stays_under_max_bytes():
    a = load('acceptance')
    item = 'x' * 900
    suffix = ' (added: s; reason: %s)' % ('r' * 100)
    body = CANON + '\n## Added criteria\n' + ''.join('- [ ] %s%s\n' % (item, suffix) for _ in range(10))
    assert len(body.encode()) < a.MAX_BYTES
    assert len(_parse(body)['added']) == 10




def _old_criteria(text):
    """Frozen copy of the pre-#2222 single-pass criteria(): the semantics an OLD installed parser has."""
    found, fenced, lines, separated, footer = False, None, [], False, False
    for line in text.splitlines():
        fence = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            char = fence.group(1)[0]
            fenced = None if fenced == char else char if fenced is None else fenced
            continue
        if fenced:
            continue
        if re.match(r'^##\s+', line):
            if found:
                break
            found = bool(re.fullmatch(r'##\s+Done when\s*', line, re.I))
            continue
        if found and lines and not line.strip():
            separated = True
        if found and line.strip():
            item = re.fullmatch(r'\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*', line)
            if not item:
                if lines and separated:
                    footer = True
                    continue
                raise ValueError('x')
            lines.append(item.group(1))
            separated = False
    return lines if found and 3 <= len(lines) <= 7 else None


def test_acceptance_old_parser_ignores_added_section_golden():
    gold = ROOT / 'contract/golden/acceptance'
    head = subprocess.run(['git', '-C', str(ROOT), 'show', 'HEAD:contract/golden/acceptance/42.md'],
                          capture_output=True, check=True).stdout
    assert (gold / '42.md').read_bytes() == head          # the original golden is untouched
    a = load('acceptance')
    for name in ('42.md', '42-extended.md'):
        body = (gold / name).read_text().split('\n---\n', 1)[1]
        assert _old_criteria(body) == a.criteria(body)
        assert len(a.criteria(body)) == 3
    ext = a.parse((gold / '42-extended.md').read_text(), '42-extended')
    assert len(ext['added']) == 1 and ext['criteria_sha256'] == a.criteria_hash(ext['criteria'])
