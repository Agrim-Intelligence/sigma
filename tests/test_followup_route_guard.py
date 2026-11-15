"""Pure move from tests/test_followup_route.py at 4042e97 (#2224 amendment 2): the guard nodes (pass at base); test bodies byte-identical.

The one routing function for follow-ups (#2224): recorded facts in, `slice` or `separate(<reason>)` out.

Run: python3 -m pytest tests/test_followup_route*.py
(generic form: python -m pytest tests/test_followup_route*.py)

CONTROLS (each clause broken alone and seen red; evidence in .sdlc/evidence/2224-controls.md):
  C1 test_undeclared_item_inherits   C2 test_different_feature   C3 test_slice_cap_at_exactly_cap
  C4 test_different_feature_wins_over_cap   C24 test_non_registered_unit_inherits   C28 test_body_wins_over_label

"""
import importlib.util
import inspect
import itertools
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fr = _mod("followup_route")

CAP = 10


def test_signature_has_no_reason_input_and_no_kwargs():
    params = inspect.signature(fr.route).parameters
    assert list(params) == ["goal_unit", "item_unit", "added", "cap"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and p.default is p.empty for p in params.values())


def test_the_result_does_not_read_the_environment(monkeypatch):
    before = [fr.route(g, i, a, CAP) for g, i, a in itertools.product([None, "A"], [None, "A", "B"], [0, 10])]
    for name in ("SIGMA_FOLLOWUP_REASON", "FOLLOWUP_REASON", "SIGMA_REASON", "SIGMA_FOLLOWUPS_MODE", "SIGMA_X"):
        monkeypatch.setenv(name, "slice-cap")
    after = [fr.route(g, i, a, CAP) for g, i, a in itertools.product([None, "A"], [None, "A", "B"], [0, 10])]
    assert before == after
