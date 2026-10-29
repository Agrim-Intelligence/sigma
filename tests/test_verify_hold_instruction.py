"""#2109: a verify that holds mid-goal (exit 5 with `RESOURCE-HOLD` on stderr) has an instruction.

The skills used to say what to do with a HOLD at the pick and nothing about `loop.py verify` holding later,
so a phase agent could read the exit 5 as a red verify (edit code), a flaky suite (park) or a reason to
retry in a tight loop. These tests pin the one shared instruction, `skills/sigma-loop/references/verify-hold.md`,
and the three places that point at it.

A substring proves a sentence is in a file, never that an agent obeys it: so each test also re-derives what
the prose CITES from the source it lives in (the prefix from `loop.py`, the wait flags from `resource_lease.py`,
the park kind from `qkind.py`, the counter from `work.py`), and the wait gesture is run once as written.
No network, no model call; the lease state folder is always a temp one.

Each clause was broken on its own and seen red (see the plan's controls).
"""
import os
import pathlib
import re
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
HOLD_PATH = ROOT / "skills" / "sigma-loop" / "references" / "verify-hold.md"
HOLD = HOLD_PATH.read_text(encoding="utf-8") if HOLD_PATH.is_file() else ""
LAND = (ROOT / "skills" / "sigma-goal" / "references" / "land.md").read_text(encoding="utf-8")
LANDING = (ROOT / "skills" / "sigma-loop" / "references" / "landing.md").read_text(encoding="utf-8")
LOOP_SKILL = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text(encoding="utf-8")
LOOP_PY = (SCRIPTS / "loop.py").read_text(encoding="utf-8")
WORK_PY = (SCRIPTS / "work.py").read_text(encoding="utf-8")

#: The bound on consecutive holds of one goal's verify before the run parks. PROVISIONAL (owner to ratify):
#: not derived from a measured hold duration. Changing it means changing this line and the reference together.
BOUND = 6
#: The wait gesture, exactly as the reference gives it (the `<s>` stands for retry_after, else 540).
WAIT = 'resource_lease.py" wait --kind verify --max'


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_the_reference_exists_and_names_the_hold_by_its_exit_and_stderr_prefix():
    assert HOLD, "skills/sigma-loop/references/verify-hold.md is missing"
    flat = _flat(HOLD)
    assert "exit 5" in flat and "`RESOURCE-HOLD`" in flat
    # the line to read is the one loop.py really prints, so the doc cannot drift from the code
    assert 'print(f"RESOURCE-HOLD exit=5 -- ' in LOOP_PY and "retry_after=" in LOOP_PY
    assert "Read stderr: a line starting `RESOURCE-HOLD exit=5 -- ... retry_after=<s>`" in flat


def test_exit_5_without_the_prefix_keeps_its_unverified_meaning():
    flat = _flat(HOLD)
    m = re.search(r"[^.]*without the prefix[^.]*\.", flat)
    assert m, "the reference must say what exit 5 without the prefix means"
    assert "`UNVERIFIED`" in m.group(0), m.group(0)
    assert "Exit 5 without the prefix keeps its `UNVERIFIED` meaning" in flat


def test_the_wait_gesture_is_given_with_the_fallback_and_it_runs():
    """The documented gesture, copied out of the reference and run: it parses and exits 0 on an empty state folder."""
    flat = _flat(HOLD)
    assert WAIT in flat, "the reference must give the verify-kind wait"
    assert "retry_after, else 540" in flat, "the wait bound is retry_after, else 540"
    m = re.search(r"python3 \"\$\{CLAUDE_SKILL_DIR\}/scripts/(resource_lease\.py)\" (wait --kind verify --max) <s>", HOLD)
    assert m, "the reference must show the full command with `<s>` for the seconds"
    gesture = [sys.executable, str(SCRIPTS / m.group(1))] + shlex.split(m.group(2)) + ["0"]
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, SIGMA_RESOURCE_DIR=tmp)
        env.pop("SIGMA_RESOURCE_MODE", None)
        done = subprocess.run(gesture, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, (gesture, done.stdout, done.stderr)


def test_after_the_wait_the_verify_is_run_again_by_the_loops_own_command():
    flat = _flat(HOLD)
    assert "`loop.py\" verify .sdlc \"$goal\"`" in flat or 'loop.py" verify .sdlc "$goal"' in HOLD
    after = flat.split(WAIT, 1)[1]
    assert "verify" in after[:400] and "again" in after[:400], "the wait must be followed by a fresh verify"


def test_a_held_verify_is_never_a_code_fix_a_review_or_a_cycle():
    flat = _flat(HOLD)
    assert re.search(r"never edit (the )?code", flat, re.I), "a hold is never sent to a code fix"
    assert "`work.max_review_cycles`" in flat and re.search(r"never count[^.]*`work\.max_review_cycles`", flat)
    assert "`post-review`" in flat, "a hold never becomes a review verdict"
    assert "never park it as flaky" in flat or "not a flaky suite" in flat


def test_the_review_counter_is_written_only_by_a_block_verdict_and_never_by_loop_py():
    """Structural pin for AC-2: a hold cannot be counted because nothing outside post-review's block writes it."""
    assert "review_cycles" not in LOOP_PY
    lines = WORK_PY.splitlines()
    writes = [i for i, line in enumerate(lines) if re.search(r'\["review_cycles"\]\s*=', line)]
    assert writes, "work.py no longer writes review_cycles under that spelling: re-derive this pin"
    for i in writes:
        assert 'verdict") == "block"' in " ".join(lines[max(0, i - 2):i]), lines[i]


def test_the_bound_ends_in_a_stated_park_with_a_kind_that_exists():
    flat = _flat(HOLD)
    assert f"{BOUND} holds in a row" in flat, "the bound must be stated as a number of holds in a row"
    assert 'record .sdlc "$goal" parked' in HOLD and "--qkind no_evidence" in HOLD
    sys.path.insert(0, str(SCRIPTS))
    try:
        import qkind
    finally:
        sys.path.remove(str(SCRIPTS))
    assert "no_evidence" in qkind.QKINDS
    assert "the goal is not failed" in flat, "the park reason must say the goal is not failed"


def test_each_entry_point_links_the_reference_and_the_link_resolves():
    for name, text, base in (("land.md", LAND, ROOT / "skills" / "sigma-goal" / "references"),
                             ("landing.md", LANDING, ROOT / "skills" / "sigma-loop" / "references"),
                             ("sigma-loop SKILL.md", LOOP_SKILL, ROOT / "skills" / "sigma-loop")):
        links = re.findall(r"\]\(([^)]*verify-hold\.md)\)", text)
        assert links, f"{name} must link verify-hold.md"
        for link in links:
            assert (base / link).resolve() == HOLD_PATH.resolve(), (name, link)


def test_land_md_does_not_count_a_verify_hold_as_an_ending():
    flat = _flat(LAND)
    m = re.search(r"Not endings:([^\n]*(?:\n[^\n]+){0,3})", LAND)
    assert m, "land.md lost its Not endings paragraph"
    assert "`RESOURCE-HOLD`" in _flat(m.group(1)), "a verify RESOURCE-HOLD is not an ending"
    assert "](../../sigma-loop/references/verify-hold.md)" in _flat(m.group(1)), "the Not endings entry must link the instruction"
