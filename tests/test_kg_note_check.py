"""A Retrospective that wrote no knowledge note is SAID SO at record time — on stderr, never refused.

Issue #2701. `sigma-retro` §4 writes the KG analysis note; `loop.py record --retro-grade` then
refreshes the graph. Nothing checked the note existed, so a retro that skipped §4 left the corpus
(and every peer machine, once notes sync) silently missing that goal. Plans and research have a
pre-push guard; the note is written after merge, so its guard belongs at record.

THE GESTURE UNDER TEST IS THE ONE THE DOCS GIVE. `/sigma-loop` and `/sigma-goal` both end a goal with
`loop.py record <goal> <result> --retro-grade <grade>`, so these tests go through the real
`_record()` and the real subprocess hop into the sibling `kg.py`. The "present" case is produced by
the §4 gesture itself, copied VERBATIM from `skills/sigma-retro/SKILL.md` and run through bash — so
the path the guard expects is, by construction, the path the documented writer writes. A companion
assertion checks the copied string is still literally in the doc.

What they assert on: the stderr line (the whole product of this goal), the returned outcome, and the
`retro` ledger event — the two things a warning must never change.

Fail-open is already controlled by
`tests/test_kg_auto_refresh.py::test_a_refresh_that_raises_never_breaks_the_goal_record`, whose
`exploding_run` raises on EVERY `kg.py` argv, this hop included; it is cited here, not duplicated.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

from journal_events import journal_events

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "skills" / "sigma-loop" / "scripts"
_KG = _ROOT / "skills" / "sigma-kg" / "scripts" / "kg.py"
_RETRO_SKILL = _ROOT / "skills" / "sigma-retro"

# skills/sigma-retro/SKILL.md §4, verbatim. `test_the_copied_gesture_is_still_the_documented_one`
# fails if the doc moves away from this text, so the copy cannot drift silently.
GESTURE = 'echo "$note_text" | python3 "${CLAUDE_SKILL_DIR}/../sigma-kg/scripts/kg.py" note .sdlc "$goal"'

MISSING = "knowledge note missing"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


loop = _load("loop", _SCRIPTS / "loop.py")
state = _load("state", _SCRIPTS / "state.py")
kg = _load("kg", _KG)

GOAL = "0001-x.md"                      # local-goals mode -> expected note `goal-0001.md`


class _Source:
    def __init__(self):
        self.completed, self.parked = [], []

    def complete(self, goal):
        self.completed.append(goal)

    def park(self, goal, reason, **kw):
        self.parked.append((goal, reason))


def _repo(tmp_path, kg_block, discovery=None):
    """A repo root with a real .sdlc and the given knowledge_graph block. `auto_refresh` is left at
    its default (off) so no builder is involved: the note check is gated on `enabled`, not on the
    refresh, exactly as §4's own writer is. No note is written here — the tests that need one write
    it with the documented gesture."""
    root = tmp_path / "repo"
    d = root / ".sdlc"
    (d / "state").mkdir(parents=True)
    cfg = {"budget": {}, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}
    if kg_block is not None:
        cfg["knowledge_graph"] = kg_block
    if discovery is not None:
        cfg["discovery"] = {"source": discovery}
    (d / "config.json").write_text(json.dumps(cfg))
    (d / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    state.start_run(str(d))
    return str(d), root


def _missing_lines(err):
    return [ln for ln in err.splitlines() if MISSING in ln]


def _retro_events(d):
    return [e for e in journal_events(loop.ledger, d) if e["kind"] == "retro"]


def _write_note_with_the_documented_gesture(root, goal, text="# Goal 0001: x\n\nShipped.\n"):
    """Run §4's gesture exactly as written, from the repo root (its `.sdlc` is literal), with the
    three variables the doc assumes are in scope."""
    env = {**os.environ, "CLAUDE_SKILL_DIR": str(_RETRO_SKILL), "note_text": text, "goal": goal}
    r = subprocess.run(["bash", "-c", GESTURE], cwd=root, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "wrote" in r.stdout, r.stdout      # rc 0 is also the disabled no-op; `wrote` is not
    return r.stdout


def test_the_copied_gesture_is_still_the_documented_one():
    assert GESTURE in (_RETRO_SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_missing_note_warns_once_on_stderr_naming_the_expected_path(tmp_path, capsys):
    d, root = _repo(tmp_path, {"enabled": True})

    outcome = loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    lines = _missing_lines(capsys.readouterr().err)
    assert len(lines) == 1, lines
    assert "knowledge/analysis/goal-0001.md" in lines[0]
    assert outcome == "done"                          # the warning changed nothing
    ev = _retro_events(d)
    assert len(ev) == 1 and ev[0]["grade"] == "achieved"


def test_a_note_written_by_the_documented_gesture_is_silent(tmp_path, capsys):
    d, root = _repo(tmp_path, {"enabled": True})
    _write_note_with_the_documented_gesture(root, GOAL)
    assert (root / ".sdlc" / "knowledge" / "analysis" / "goal-0001.md").exists()

    outcome = loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert _missing_lines(capsys.readouterr().err) == []
    assert outcome == "done"


def test_graph_off_is_silent_even_with_the_note_missing(tmp_path, capsys):
    """The fresh-install default (no block) and an explicit `false` — §4 writes nothing in either,
    so there is nothing to warn about. Note ABSENT on purpose: with it present this test could not
    tell an `enabled` gate from a bare existence check."""
    for block in (None, {"enabled": False}):
        d, root = _repo(tmp_path / ("a" if block is None else "b"), block)
        assert not (root / ".sdlc" / "knowledge").exists()

        loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

        assert _missing_lines(capsys.readouterr().err) == [], block


def test_no_retro_grade_is_silent(tmp_path, capsys):
    """A goal that never reached Retrospective (a pre-work park) owes no note."""
    d, root = _repo(tmp_path, {"enabled": True})

    loop._record(d, _Source(), GOAL, "parked", "blocked on a decision")     # no retro_grade

    assert _missing_lines(capsys.readouterr().err) == []


def test_github_mode_names_the_issue_note(tmp_path, capsys):
    """The filename comes from `kg.py note_id`, so github discovery expects `issue-<N>.md`."""
    d, root = _repo(tmp_path, {"enabled": True}, discovery="github")

    loop._record(d, _Source(), "2701", "done", "", retro_grade="achieved")

    lines = _missing_lines(capsys.readouterr().err)
    assert len(lines) == 1 and "knowledge/analysis/issue-2701.md" in lines[0], lines


def test_note_check_verb_stream_routing(tmp_path, capsys):
    """The `kg.py note-check` CLI itself: disabled and present go to stdout with rc 0 (so `_record`,
    which forwards only stderr, stays silent); missing goes to stderr with rc 1."""
    off, _ = _repo(tmp_path / "off", None)
    assert kg.main(["kg.py", "note-check", off, GOAL]) == 0
    out, err = capsys.readouterr()
    assert "disabled" in out and err == ""

    on, root = _repo(tmp_path / "on", {"enabled": True})
    assert kg.main(["kg.py", "note-check", on, GOAL]) == 1
    out, err = capsys.readouterr()
    assert out == "" and MISSING in err and f"{on}/knowledge/analysis/goal-0001.md" in err

    _write_note_with_the_documented_gesture(root, GOAL)
    assert kg.main(["kg.py", "note-check", on, GOAL]) == 0
    out, err = capsys.readouterr()
    assert "present" in out and err == ""
