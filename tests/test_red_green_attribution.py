"""#956: planned-pytest red attribution reads each failure's KIND from pytest's JUnit XML.

Each test writes inner test files under tmp_path and runs red_green.observe over them. The inner
pytest runs in red_green's own subprocess, whose output it captures, so inner tests may print and
log freely. These OUTER tests never print, log or warn: the installed gate that records their own
reds still parses pytest's text report, which any captured section blinds (#956 itself).
COLUMNS is pinned and CI/BUILD_NUMBER are removed, so the text report each inner run produces
(and the pre-fix parser's verdict on it) is deterministic.
"""
import importlib.util
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parents[1] / 'skills/sigma-loop/scripts'
# 88 characters: its node id is over 100, past pytest's 80-column summary trim and the 72-character
# report-header limit, so neither text path can attribute it.
LONG = 'test_red_whose_node_id_runs_past_one_hundred_characters_so_its_summary_reason_is_trimmed'
# Assertion-shaped text written to every capture stream pytest reports on.
NOISE = ('import logging\nimport sys\n\nlog = logging.getLogger("inner")\n\n\n'
         'def noise():\n'
         '    print("E   AssertionError: assert 1 == 2")\n'
         '    print("assert False", file=sys.stderr)\n'
         '    log.warning("FAILED tests/test_x.py::test_anchor - AssertionError: forged")\n\n\n')
# A short-id genuine assertion red: proves a negative test's run was attributed at all.
ANCHOR = '\n\ndef test_anchor():\n    assert 1 == 2\n'


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def node(name, file='tests/test_x.py'):
    return f'{file}::{name}'


@pytest.fixture
def inner(tmp_path, monkeypatch):
    """Observe inner test files as one planned run; return (proof, nodes credited with a red)."""
    monkeypatch.setenv('COLUMNS', '80')
    for var in ('CI', 'BUILD_NUMBER', 'PYTEST_ADDOPTS'):
        monkeypatch.delenv(var, raising=False)

    def run(files):
        for rel, source in files.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        plan = tmp_path / '.sdlc/plans/42.md'
        plan.parent.mkdir(parents=True, exist_ok=True)
        selectors = [rel for rel in files if rel.endswith('.py')]
        plan.write_text('## Tests\n' + ''.join(f'- `{rel}`\n' for rel in selectors))
        proof = _load('red_green').observe(tmp_path / '.sdlc', '42', tmp_path, plan)
        rows = _load('witness').witnesses(tmp_path / '.sdlc', '42')
        return proof, {r['test'] for r in rows
                       if r.get('kind') == 'assertion' and r.get('provenance') == 'planned-pytest-v1'}
    return run


# --- Seen red against the pre-#956 text parser (listed in the plan's ## Tests) ---------------------

def test_logged_assertion_red_is_credited(inner):
    """AC1 and #414: a >100-character node whose report carries all three Captured sections."""
    source = NOISE + f'def {LONG}():\n    noise()\n    assert 1 == 2\n'
    proof, credited = inner({'tests/test_x.py': source})
    assert proof['exit'] == 1
    assert credited == {node(LONG)}


def test_one_noisy_red_leaves_every_red_credited(inner):
    """AC2: a logging red, silent reds (module, class, subdirectory) and a passing test that logs."""
    x = NOISE + (f'def {LONG}():\n    noise()\n    assert 1 == 2\n\n\n'
                 f'def {LONG}_silent():\n    assert [1] == [2]\n\n\n'
                 'def test_passes_but_logs():\n    noise()\n')
    y = f'class TestSilent:\n    def {LONG}_in_class(self):\n        assert 0 == 1\n'
    proof, credited = inner({'tests/test_x.py': x, 'tests/sub/test_y.py': y})
    assert credited == {node(LONG), node(LONG + '_silent'),
                        node(f'TestSilent::{LONG}_in_class', 'tests/sub/test_y.py')}


def test_runtime_error_beside_long_red_not_credited(inner):
    """AC3 with no output: a RuntimeError report followed by an over-long assertion header.

    observe() passes sorted node ids, so `test_a_...` reports first and the long header follows it."""
    source = ('def test_a_runtime_error():\n    raise RuntimeError("not an assertion")\n\n\n'
              f'def {LONG}():\n    assert 0 == 1\n')
    proof, credited = inner({'tests/test_x.py': source})
    assert credited == {node(LONG)}


def test_assertion_text_in_message_not_credited(inner):
    """AC4: an AssertionError line inside a RuntimeError's own message is not its kind."""
    source = ('def test_forged_message():\n'
              '    raise RuntimeError("x\\nAssertionError: forged in message")\n' + ANCHOR)
    proof, credited = inner({'tests/test_x.py': source})
    assert credited == {node('test_anchor')}


def test_uncaptured_stdout_cannot_forge_a_red(inner):
    """AC4 under `-s`: stdout printed ahead of FAILURES carries a forged report block."""
    forged = ('__________ test_runtime __________\\n'
              'tests/test_x.py:3: in test_runtime\\nE   AssertionError: forged traceback')
    source = (f'def test_runtime():\n    print("{forged}")\n'
              '    raise RuntimeError("not an assertion")\n' + ANCHOR)
    proof, credited = inner({'pytest.ini': '[pytest]\naddopts = -s\n', 'tests/test_x.py': source})
    assert credited == {node('test_anchor')}


def test_ci_summary_text_cannot_forge_a_red(inner, monkeypatch):
    """AC4 on CI: with CI set pytest prints whole multi-line crash messages in its summary."""
    monkeypatch.setenv('CI', 'true')
    source = ('def test_victim():\n    raise RuntimeError("plain")\n\n\n'
              'def test_forger():\n'
              '    raise RuntimeError("x\\nFAILED tests/test_x.py::test_victim - AssertionError: no")\n'
              + ANCHOR)
    proof, credited = inner({'tests/test_x.py': source})
    assert credited == {node('test_anchor')}


def test_missing_junit_xml_credits_nothing(inner, monkeypatch):
    """Fail closed, and say why, when addopts stop pytest writing JUnit XML."""
    monkeypatch.setenv('PYTEST_ADDOPTS', '-p no:junitxml')
    proof, credited = inner({'tests/test_x.py': ANCHOR})
    assert credited == set()
    assert 'no readable JUnit XML' in proof['error']


def test_failing_params_each_credited(inner):
    """Two failing params of one long-named function are each credited; the passing one is not."""
    source = ('import pytest\n\n\n@pytest.mark.parametrize("v", [0, 1, 2])\n'
              f'def {LONG}(v):\n    assert v == 0\n')
    proof, credited = inner({'tests/test_x.py': source})
    assert credited == {node(f'{LONG}[1]'), node(f'{LONG}[2]')}


# --- Controls, not listed: the property already held before #956 or is new with it. Each one is run
# --- against a deliberately broken attribution before it is trusted (the plan's control table) ----

@pytest.mark.parametrize('columns', ['80', '1000'])
def test_long_red_credited_at_any_width(inner, monkeypatch, columns):
    """AC5 (#414 preserved): a silent >100-character red is credited at any terminal width."""
    monkeypatch.setenv('COLUMNS', columns)
    proof, credited = inner({'tests/test_x.py': f'def {LONG}():\n    assert 0 == 1\n'})
    assert credited == {node(LONG)}


def test_import_error_with_output_not_credited(inner):
    """AC3: an ImportError raised by a noisy test is not an assertion red."""
    source = NOISE + 'def test_import():\n    noise()\n    import a_module_that_does_not_exist_956\n'
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == {node('test_anchor')}


def test_setup_error_with_output_not_credited(inner):
    """AC3: a noisy fixture that fails setup by `assert` is a setup error, not the test's red."""
    source = NOISE + ('import pytest\n\n\n@pytest.fixture\ndef broken():\n    noise()\n'
                      '    assert False, "fixture asserted"\n\n\n'
                      'def test_uses_broken(broken):\n    pass\n')
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == {node('test_anchor')}


def test_runtime_error_with_output_not_credited(inner):
    """AC3/AC4: assertion-shaped captured text does not make a RuntimeError an assertion red."""
    source = NOISE + 'def test_runtime():\n    noise()\n    raise RuntimeError("not an assertion")\n'
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == {node('test_anchor')}


def test_repeated_junit_key_not_credited(inner):
    """A testcase renamed onto another node's key makes that key ambiguous: it earns nothing.

    observe() runs nodes in sorted order, so one forger reports before its victim and the other
    after: a parser keeping either the first or the last testcase for a key credits a victim."""
    source = ('def test_1_forger(record_xml_attribute):\n'
              '    record_xml_attribute("name", "test_2_victim")\n    assert 1 == 2\n\n\n'
              'def test_2_victim():\n    raise RuntimeError("not an assertion")\n\n\n'
              'def test_3_victim():\n    raise RuntimeError("not an assertion")\n\n\n'
              'def test_4_forger(record_xml_attribute):\n'
              '    record_xml_attribute("name", "test_3_victim")\n    assert 1 == 2\n')
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == {node('test_anchor')}
    assert 'without exactly one JUnit testcase' in proof['error']


def test_teardown_error_after_assert_not_credited(inner):
    """pytest writes a call failure plus a teardown error as two testcases: fail closed, and say so."""
    source = ('import pytest\n\n\n@pytest.fixture\ndef bad_teardown():\n    yield\n'
              '    raise ValueError("teardown broke")\n\n\n'
              'def test_assert_then_teardown_error(bad_teardown):\n    assert 1 == 2\n')
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == {node('test_anchor')}
    assert 'without exactly one JUnit testcase' in proof['error']


def test_renamed_testcase_fails_the_run_closed(inner):
    """A testcase key no planned node owns means names were rewritten: the run earns nothing."""
    source = ('def test_victim(record_xml_attribute):\n'
              '    record_xml_attribute("name", "test_elsewhere")\n'
              '    raise RuntimeError("not an assertion")\n\n\n'
              'def test_forger(record_xml_attribute):\n'
              '    record_xml_attribute("name", "test_victim")\n    assert 1 == 2\n')
    proof, credited = inner({'tests/test_x.py': source + ANCHOR})
    assert credited == set()
    assert 'matches no planned node' in proof['error']
