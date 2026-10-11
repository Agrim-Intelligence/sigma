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
    vd = _mod("skills/sigma-init/scripts/verify_detect.py", "verify_detect")
    assert len(ident.split(":")[1]) == vd._ID_HEX, "the example id must be as long as the ids init prints"


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


# ---- the prose is held to the code it describes (not just to substrings) ----------------------

def _mod(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_only_waivable_phases_are_the_ones_the_readme_names():
    waivable = _mod("skills/sigma-loop/scripts/phase_gate.py", "phase_gate").WAIVABLE
    assert tuple(waivable) == ("research", "retro")
    assert "research,retro" in README


def test_every_runtime_dir_the_readme_names_is_one_init_really_ignores():
    ignores = _mod("skills/sigma-setup/scripts/setup.py", "sigma_setup").RUNTIME_IGNORES
    named = set(re.findall(r"`(\.sdlc/[a-z]+/)`", README.split("What init leaves")[1].split("###")[0]))
    runtime = {d for d in named if d != ".sdlc/goals/"}
    assert runtime and runtime <= set(ignores), (runtime, ignores)


def test_the_documented_reconcile_line_is_the_one_loop_py_prints():
    src = (ROOT / "skills/sigma-loop/scripts/loop.py").read_text(encoding="utf-8")
    assert '(PR #{pr} {what})' in src and '"merged" if outcome == "done"' in src


def test_the_readme_does_not_promise_a_review_the_default_does_not_require():
    section = README.split("Before you start `/sigma-loop`")[1][:900]
    assert "work.require_review" in section and "default" in section
    assert "every PR the loop opens needs" not in README.lower()


def test_the_skill_instructions_carry_the_same_gestures_as_the_readme():
    init = " ".join((ROOT / "skills/sigma-init/SKILL.md").read_text(encoding="utf-8").split())
    loop = " ".join((ROOT / "skills/sigma-loop/SKILL.md").read_text(encoding="utf-8").split())
    ident = _verify_example(README)
    assert f"`/sigma-init --verify {ident}`" in init and "SECOND invocation" in init
    assert "ignore `.sdlc/` as a whole" in init
    running = " ".join((ROOT / "skills/sigma-loop/references/running.md").read_text(encoding="utf-8").split())
    assert "host refusal" in running.lower() and "never retried or routed around" in running
