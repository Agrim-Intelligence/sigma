import json, os, subprocess, sys, pathlib, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_install_copies_hook_and_prints_snippet():
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "SDLC_KIT_SKILLS_DIR": tmp}
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert (pathlib.Path(tmp) / "sigma" / "hooks" / "agrim_gate.sh").exists()
        # the spine's skills must actually land, not just the hook
        assert (pathlib.Path(tmp) / "sigma" / "skills" / "agrim-loop" / "scripts" / "loop.py").exists()
        # prints the wiring snippet (does NOT edit settings.json itself)
        assert "UserPromptSubmit" in proc.stdout
        assert "agrim_gate.sh" in proc.stdout


def test_install_fails_loudly_when_a_copy_fails():
    # A real copy failure must abort (set -e), not be swallowed by `|| true` while the
    # success banner still prints. Force the skills copy to fail by planting a FILE where
    # the skills dir must go (deterministic + root-safe: no permission bits involved).
    with tempfile.TemporaryDirectory() as tmp:
        dest = pathlib.Path(tmp) / "sigma"; dest.mkdir()
        (dest / "skills").write_text("blocker")          # cp -R <dir> onto a file -> error
        env = {**os.environ, "SDLC_KIT_SKILLS_DIR": tmp}
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, env=env,
                              capture_output=True, text=True)
        assert proc.returncode != 0, "install.sh swallowed a copy failure and exited 0"


def test_installed_tree_actually_runs_a_documented_gesture():
    """The assertions above prove files LAND. This one proves one of them RUNS (#2643).

    #2573 was invisible to every one of them: `install.sh:13` is `cp -R skills` and the rate card
    lived outside `skills/`, so every install.sh user's `phase_report.py end` raised
    FileNotFoundError and lost both the Block B cost line and the phase's ledger event -- while
    `loop.py`'s `.exists()` stayed green the whole time. The gesture was already right; the
    assertion was decoration.

    So this runs the bracket the docs prescribe verbatim (`skills/agrim-loop/SKILL.md`,
    `skills/agrim-goal/SKILL.md`: `phase_report.py start ... --model <tier>` then
    `phase_report.py end ... --agent-id <id>`) against the INSTALLED tree, and asserts the cost
    reaches stdout. Its repo-tree twin is `tests/test_phase_report.py`'s
    `test_cli_end_with_real_transcript_prints_cost_and_writes_ledger_event`; the only delta here is
    which tree runs.

    Three fixture details are load-bearing, each measured, not assumed:

    * The transcript is pinned to the CLOSED 2026-08-24 rate vintage. The card's current
      claude-sonnet-5 rows carry an empty `effective_to`, so a transcript stamped "now" prices
      against whatever vintage is open and the next price revision would turn a healthy install
      red. A closed vintage never moves. (Safe against the phase marker: the `--agent-id` path
      calls `price_transcript(..., rates=rates)` with no `since_ts`.)
    * `CLAUDE_CODE_SESSION_ID` is set explicitly. Inherited from a live session it resolves to
      "no per-turn usage source found"; unset, as in CI, it falls through to the Codex branch.
      Both are red for a reason that is not the defect, which is worthless as evidence.
    * All five token rate-kinds are populated. `price_turn` is all-or-nothing, so a usage dict
      missing `cache_read_input_tokens` or either `cache_creation` key prices as "model not in
      rate card" -- green-for-the-wrong-reason, blind to the very defect this targets.

    CONTROL, run 2026-09-24: moving `skills/agrim-loop/rates/` to a top-level `rates/` -- #2573's
    actual shape, where `cp -R skills` genuinely misses it -- turns the `$7.00` assertion below
    red while BOTH `.exists()` tests above stay green. Note what it does NOT do: `end` still
    exits 0, because `load_rate_rows()` returns `[]` rather than raising since #2573. A
    returncode-only assertion could not see this. The teeth are in stdout.
    """
    usage = {"input_tokens": 1_000_000, "output_tokens": 500_000,
             "cache_read_input_tokens": 0,
             "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                "ephemeral_1h_input_tokens": 0}}
    turn = {"type": "assistant", "timestamp": "2026-08-24T00:00:00Z",
            "message": {"id": "msg_install_1", "role": "assistant",
                        "model": "claude-sonnet-5", "usage": usage}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        # `.sdlc`, not `sdlc`: `timing_store._find_sdlc` only accepts an argv path whose
        # name is exactly ".sdlc" and otherwise falls back to os.getcwd(), which would
        # record a phantom `sess-install` session in the developer's live timing store.
        # It is also what makes the gesture below the docs' own, byte for byte.
        dest, home, sdlc = tmp / "skills", tmp / "home", tmp / ".sdlc"
        proc = subprocess.run(["bash", "install.sh"], cwd=ROOT, capture_output=True, text=True,
                              env={**os.environ, "SDLC_KIT_SKILLS_DIR": str(dest)})
        assert proc.returncode == 0, proc.stderr

        (sdlc / "state").mkdir(parents=True)
        (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        (sdlc / "config.json").write_text('{"journal": {"enabled": true}}')
        subagents = home / ".claude" / "projects" / "-slug" / "sess-install" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-abc.jsonl").write_text(json.dumps(turn) + "\n")

        report = dest / "sigma" / "skills" / "agrim-loop" / "scripts" / "phase_report.py"
        env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "sess-install",
               "CODEX_THREAD_ID": "", "CODEX_SESSION_ID": ""}
        started = subprocess.run(
            [sys.executable, str(report), "start", str(sdlc), "42", "research", "--model", "sonnet"],
            capture_output=True, text=True, env=env)
        assert started.returncode == 0, started.stderr
        ended = subprocess.run(
            [sys.executable, str(report), "end", str(sdlc), "42", "research", "--agent-id", "abc"],
            capture_output=True, text=True, env=env)

        assert ended.returncode == 0, ended.stderr
        assert "cost: unavailable on this host" not in ended.stdout, ended.stdout
        # $2.00/Mtok in + $10.00/Mtok out, frozen 2026-08-24 vintage.
        assert "$7.00" in ended.stdout, ended.stdout
