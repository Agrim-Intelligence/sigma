"""#2005 (lean goals slice 3; design 2000.md FR-61, D-34, BR-16/48/67/71): `loop.py next-step`.

`loop.py next-step <dir> <goal>` prints a goal's route, its recorded phases (`loop.py phases` verbatim),
and -- until implement has a recorded end -- the step-banner command and the slice-wave procedure that
used to live in `sigma-implement`'s `## Announce each step` and `running.md` 3b. It is read-only.
The prose it replaces left the phase skills (one pointer line each); the three moved sections are
checked here for the move itself, and the budget for its size (tests/test_phase_context_budget.py).

Every gesture is run as the real CLI, and the documented pointer gestures are run as the docs write them."""
import functools
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_lean_route_classification as lr  # noqa: E402
import test_phase_record_gate as g  # noqa: E402
import upgrade_fixtures as u  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
S = SKILLS / "sigma-loop" / "scripts"
CLASSIFY = 'loop.py" classify'
BANNER = 'phase_report.py" step'
WAVE = 'slices.py" plan'


@functools.lru_cache(maxsize=None)
def _usage():
    return subprocess.run([sys.executable, str(S / "loop.py"), "--help"], capture_output=True, text=True).stdout


def _seam():
    assert "next-step <dir> <goal>" in _usage(), "loop.py has no next-step verb"


def nxt(p, goal=g.GOAL, sdlc=".sdlc"):
    _seam()
    return g.run(p, "loop.py", "next-step", sdlc, goal)


def _out(p, goal=g.GOAL):
    r = nxt(p, goal)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _shell(p, line, env=None):
    return subprocess.run(["bash", "-c", line], cwd=p, capture_output=True, text=True,
                          env={**g.ENV, **(env or {})})


def _line(out, needle):
    hits = [x for x in out.splitlines() if needle in x]
    assert hits, f"no line with {needle!r} in:\n{out}"
    return hits[0]


def _wave_project(tmp_path, work=True, parallel=True, manifest=True):
    p = g._project(tmp_path, gate=None, extra={"work": {"enabled": work}, "parallel": {"enabled": parallel}})
    if manifest:
        (p / ".sdlc" / "plans" / "0001-x.slices.json").write_text(json.dumps({"slices": []}))
    return p


def _set_cfg(p, **blocks):
    cfg = json.loads((p / ".sdlc" / "config.json").read_text())
    cfg.update(blocks)
    lr._cfg(p, cfg)


def _append_row(p, row):
    f = p / ".sdlc" / "state" / "log" / "0001-x.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as handle:
        handle.write(json.dumps({"ts": "2026-10-25T00:00:00.000Z", "actor": "loop", "thread": "main", **row}) + "\n")


# --------------------------------------------------------------------------- the verb

def test_usage_lists_next_step_verb():
    _seam()


def test_unclassified_goal_is_told_to_run_classify(tmp_path):
    p = g._project(tmp_path, gate=None)
    out = _out(p)
    line = _line(out, "route: unclassified")
    assert CLASSIFY in line and '"trivial|small|standard|heavy" --rationale' in line, line   # quoted: shell-safe
    gesture = line.split("classify it now: ", 1)[1].replace('"trivial|small|standard|heavy"', '"small"')
    r = _shell(p, gesture.replace('"<why>"', '"one file"'))
    assert r.returncode == 0, r.stderr
    assert lr.work.route_of(str(p / ".sdlc"), g.GOAL)["profile"] == "small"


def test_unclassified_goal_with_no_route_store_says_nothing_can_hold_it(tmp_path):
    p = g._project(tmp_path, gate=None, action_log=False)
    out = _out(p)
    assert "route: unclassified" in out and "nothing can hold the route" in out, out
    assert CLASSIFY not in out
    lr._work_record(p / ".sdlc")                    # a work record CAN hold it: the prompt comes back
    assert CLASSIFY in _out(p)


def test_unclassified_goal_past_pick_gets_no_classify_prompt(tmp_path):
    """Refinement 6: the prompt never names a classify that `work.classify` would refuse."""
    for kind in ("recorded", "merge_armed"):
        p = g._project(tmp_path / kind, gate=None)
        _append_row(p, {"kind": kind})
        out = _out(p)
        assert "route: unclassified" in out and f"`{kind}`" in out, out
        assert CLASSIFY not in out


def test_lean_goal_prints_route_and_no_classify_gesture(tmp_path):
    p = g._project(tmp_path, gate=None)
    assert lr._classify(p, "small").returncode == 0
    out = _out(p)
    assert "route: lean/1 profile=small" in out, out
    assert CLASSIFY not in out


def test_legacy_goal_prints_legacy_and_no_classify_gesture(tmp_path):
    by_verdict = u.mid_implement_goal(tmp_path / "verdict")
    by_gates = g._project(tmp_path / "gates", gate=None)
    lr._gates(by_gates / ".sdlc", {"verdict": "warn", "plan_hash": "a" * 64})
    for p in (by_verdict, by_gates):
        out = _out(p)
        assert "route: legacy" in out and "FR-33" in out, out
        assert CLASSIFY not in out


def test_phases_block_is_phase_gate_status_verbatim(tmp_path):
    p = g._project(tmp_path, gate=None)
    g.phase(p, "research", "a1")
    out = _out(p)
    phases = g.run(p, "loop.py", "phases", ".sdlc", g.GOAL)
    assert phases.returncode == 0 and "NEXT:" in phases.stdout
    assert phases.stdout.strip() in out


def test_step_banner_printed_until_implement_ends_for_lean_and_legacy(tmp_path):
    lean = g._project(tmp_path / "lean", gate=None)
    assert lr._classify(lean).returncode == 0
    # explicit action_log: a 1.0.x repo with the key absent logs nothing for a legacy goal (FR-96), so no record
    # could show implement's end and the banner rightly stays.
    legacy = u.mid_implement_goal(tmp_path / "legacy", final_cfg={**u.omega_shaped_config(),
                                                                    "action_log": {"enabled": True}})
    for p in (lean, legacy):
        assert BANNER in _out(p)
        if p is lean:
            assert g.run(p, "phase_report.py", "start", ".sdlc", g.GOAL, "implement", "--model", "sonnet").returncode == 0
            assert BANNER in _out(p)                # started is not ended
        assert g.run(p, "phase_report.py", "end", ".sdlc", g.GOAL, "implement").returncode == 0
        assert BANNER not in _out(p)


def test_step_banner_returns_when_implement_restarts_after_send_back(tmp_path):
    """Implement is ended only while its LATEST start/end row is an end: a send-back re-start brings the
    banner and the slice-wave procedure back."""
    p = _wave_project(tmp_path)
    assert BANNER in _out(p) and WAVE in _out(p)
    assert lr._classify(p).returncode == 0
    impl = lambda verb, *a: g.run(p, "phase_report.py", verb, ".sdlc", g.GOAL, "implement", *a)
    assert impl("start", "--model", "sonnet").returncode == 0
    assert impl("end").returncode == 0
    out = _out(p)
    assert BANNER not in out and "slices:" not in out, out
    assert impl("start", "--model", "sonnet").returncode == 0      # sent back from review
    out = _out(p)
    assert BANNER in out and WAVE in out and "slices: run the wave" in out, out


def test_printed_step_banner_command_runs(tmp_path):
    p = g._project(tmp_path, gate=None)
    line = _line(_out(p), BANNER).strip()
    cmd = line.replace("<n>", "1").replace("<m>", "2").replace("<what>", "write the test")
    r = _shell(p, cmd)
    assert r.returncode == 0, r.stderr
    assert "STEP 1/2" in r.stdout


def test_slice_wave_block_needs_manifest_work_and_parallel(tmp_path):
    p = _wave_project(tmp_path)
    out = _out(p)
    assert WAVE in out and "agent_dispatch --thread <slice-id> --role slice --phase implement --model <tier>" in out
    assert "agent-start" in out and "agent_done" in out and "claude -p" in out and "dispatch: session" in out
    manifest = p / ".sdlc" / "plans" / "0001-x.slices.json"
    manifest.unlink()
    out = _out(p)
    assert WAVE not in out and "slices: none declared" in out, out
    manifest.write_text("{}")
    _set_cfg(p, parallel={"enabled": False})
    out = _out(p)
    assert WAVE not in out and "slices: none declared" in out, out


def test_work_off_says_run_one_unit(tmp_path):
    p = _wave_project(tmp_path, work=False)
    out = _out(p)
    assert "slices: work.enabled is off" in out, out
    assert WAVE not in out


def test_codex_slice_dispatch_resolves_host_model(tmp_path):
    p = _wave_project(tmp_path)
    out = _out(p)
    assert "--host codex --goal-worktree <absolute-path> --tier <tier-or-off> --effort <effort>" in out
    line = _line(out, 'predict.py" host-model codex "<tier-or-off>"')
    script = re.search(r'python3 "([^"]+/predict\.py)"', line)
    assert script and pathlib.Path(script.group(1)).is_file(), line
    assert "`work.py start`). Before every Codex" in out


def test_next_step_writes_nothing(tmp_path):
    """No goal state: no file, log row, note or heartbeat. Excluded: `.sdlc/state/time/`, the working-time
    floor `timing_store.timed_main` writes for EVERY loop.py verb (`phases` too), never config-gated."""
    p = _wave_project(tmp_path)
    assert lr._classify(p).returncode == 0
    timing = p / ".sdlc" / "state" / "time"

    def snap():
        return sorted((str(x), x.stat().st_mtime_ns, x.stat().st_size) for x in p.rglob("*")
                      if timing not in (x, *x.parents))
    before = snap()
    _out(p)
    assert snap() == before


def test_unsafe_goal_is_refused_exit_2(tmp_path):
    p = g._project(tmp_path, gate=None)
    r = nxt(p, goal="../x")
    assert r.returncode == 2, r.stdout + r.stderr
    # next-step's own refusal, before any read -- not a downstream reader's incidental one
    assert "loop.py next-step: unsafe goal '../x': " in r.stderr and r.stdout == "", r.stderr


# --------------------------------------------------------------------------- the prose moves (D-34)

_GESTURE = re.compile(r'python3 "\$\{CLAUDE_SKILL_DIR\}([^"]*)/loop\.py" next-step (\S+) ("[^"]+")')
_DOCS = {
    SKILLS / "sigma-implement" / "SKILL.md": SKILLS / "sigma-implement",
    SKILLS / "sigma-loop" / "references" / "running.md": SKILLS / "sigma-loop",
    SKILLS / "sigma-loop" / "SKILL.md": SKILLS / "sigma-loop",
}


def test_documented_next_step_gestures_run(tmp_path):
    """AGENTS.md: run the gesture the docs give. Each doc's pointer, as written, runs and prints the step."""
    p = g._project(tmp_path, gate=None)
    for doc, skill_dir in _DOCS.items():
        found = _GESTURE.search(doc.read_text(encoding="utf-8"))
        assert found, f"{doc} has no runnable next-step pointer"
        _seam()
        gesture = found.group(0).replace('"<goal>"', '"$goal"')
        r = _shell(p, gesture, env={"CLAUDE_SKILL_DIR": str(skill_dir), "goal": g.GOAL})
        assert r.returncode == 0, (doc, r.stderr)
        assert "NEXT:" in r.stdout and "route:" in r.stdout, (doc, r.stdout)


def test_implement_announce_section_replaced_by_pointer():
    text = (SKILLS / "sigma-implement" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Announce each step" not in text
    assert 'loop.py" next-step' in text
    assert "--phase-model" not in text                  # the banner rules now print from next-step
    goal = (SKILLS / "sigma-goal" / "SKILL.md").read_text(encoding="utf-8")
    assert "Announce each step" not in goal and "loop.py next-step" in goal


def test_plan_review_disposition_moved_keeping_pinned_paragraphs():
    base = SKILLS / "sigma-plan-review"
    ref = base / "references" / "disposition.md"
    assert ref.is_file(), ref
    skill, moved = (base / "SKILL.md").read_text(encoding="utf-8"), ref.read_text(encoding="utf-8")
    assert "## 5. Disposition" in skill and "](references/disposition.md)" in skill
    for gone in ("- **Accept**", "**The review can also be wrong.**", "**Regen threshold.**",
                 "Carry the dispositions"):
        assert gone not in skill and gone in moved, gone
    for kept in ("**Structural over patchwork**", 'loop.py" escalate .sdlc', 'loop.py" note .sdlc'):
        assert kept in skill and kept not in moved, kept


def test_counter_review_is_a_linked_reference():
    base = SKILLS / "sigma-review"
    ref = base / "references" / "counter-review.md"
    assert ref.is_file(), ref
    skill = (base / "SKILL.md").read_text(encoding="utf-8")
    assert "## Counter-review" not in skill and "](references/counter-review.md)" in skill
    assert "stronger findings it missed" in ref.read_text(encoding="utf-8")
