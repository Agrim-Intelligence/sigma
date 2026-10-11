"""#612: the first-live-run deviations stay documented, and the documented gestures stay real.

Each check copies the gesture out of the README (not a restated string), so prose that drifts from
the CLI goes red. `_CHECKS` is the table; the control below deletes each anchor and demands red."""
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())


def _init_flow():
    spec = importlib.util.spec_from_file_location(
        "init_flow", ROOT / "skills/sigma-init/scripts/init_flow.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _verify_example(text):
    m = re.search(r"`/sigma-init --verify (\d+:[0-9a-f]+)`", text)
    return m.group(1) if m else None


def _review_before_loop(text):
    i = text.find("independent review")
    j = text.find("## Configuration")
    return 0 <= i < j and all(w in text for w in ("self-approval", "attended", "sigma:approve"))


def _trivial_goal(text):
    return all(w in text for w in (
        "acceptance before", "never after the work", "--no-tests", "not a waiver",
        "waive-phases", "research,retro", "plan-review"))


def _artifacts(text):
    return all(w in text for w in (".gitignore", ".sdlc/goals/", ".sdlc/config.json",
                                   "git status", "ignore `.sdlc/` as a whole"))


def _silent_reconcile(text):
    return "reconcile-merges" in text and "prints nothing" in text and "1 done (PR #" in text


def _phase_lines(text):
    return all(w in text for w in ("phase_report.py start", "phase_report.py end", "cost"))


_CHECKS = {
    "review fallback before the loop": _review_before_loop,
    "trivial goal path": _trivial_goal,
    "init artifacts": _artifacts,
    "silent reconcile": _silent_reconcile,
    "phase report lines": _phase_lines,
}


@pytest.mark.parametrize("name", sorted(_CHECKS))
def test_readme_documents(name):
    assert _CHECKS[name](README), name


def test_the_verify_example_is_a_gesture_init_flow_accepts():
    ident = _verify_example(README)
    assert ident, "README must show a second-invocation `/sigma-init --verify N:ID` example"
    opts, target, err = _init_flow().parse(["--verify", ident])
    assert err is None and opts["verify"] == ident


@pytest.mark.parametrize("name", sorted(_CHECKS))
def test_control_a_drifted_readme_goes_red(name):
    broken = {"review fallback before the loop": "independent review", "trivial goal path": "waive-phases",
              "init artifacts": "git status", "silent reconcile": "prints nothing",
              "phase report lines": "phase_report.py end"}[name]
    assert not _CHECKS[name](README.replace(broken, "x" * len(broken)))


def test_control_a_missing_or_bare_verify_example_is_red():
    assert _verify_example(README.replace("`/sigma-init --verify", "`/sigma-init --verif")) is None
    _, _, err = _init_flow().parse(["--verify", "1"])
    assert err and "N:ID" in err
