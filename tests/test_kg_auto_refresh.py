"""Refreshing the knowledge graph is CODE, not a sentence in someone else's prompt.

Issue #1562. `knowledge_graph.auto_refresh` was documented in three places as rebuilding the graph
at the end of every Retrospective — `skills/sigma-kg/SKILL.md`, `README.md`, and
`skills/sigma-retro/SKILL.md`, whose §4 pointed at "this skill's own step 3 above" for a call that
step 3 never made. No code path anywhere invoked a builder: `build_plan()["auto_refresh"]` had zero
consumers. Measured on this repo 2026-09-02: `auto_refresh=True`, a 526-document corpus, and
`graph: not built` — with no error, no warning and no doctor row anywhere to say so.

THE GESTURE UNDER TEST IS THE ONE THE DOCS GIVE, not a stronger one. `/sigma-loop` SKILL.md step 6
and `/sigma-goal` SKILL.md step 4 both end a goal with `loop.py record <goal> done --retro-grade
<grade>`, so that is what these tests call — through the real `_record()`, the real `subprocess`
hop into the sibling `kg.py` CLI, and a real builder executable on PATH. Nothing is monkeypatched.

WHAT THEY ASSERT ON. `<builder>-out/graph.json` ON DISK — the exact file `kg.py::status()` reads to
decide `graph_built`, and the exact path `/sigma-context`'s gate and `graphify query` both depend on.
Never `_record`'s return value or a captured log line: those are the code's own account of itself
and would read correctly for a version that invoked nothing, which is precisely the bug being fixed.

The stub builder writes the file the real one would. That boundary is deliberate: Sigma's
responsibility is the TRIGGER (does the builder get invoked, with the right arguments, at the right
moment, and only then), and the extraction itself is graphify's. On this machine the real builder
cannot run at all — no `GEMINI_API_KEY`/`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`OLLAMA_BASE_URL`, no
ollama, and `claude -p` returns "OAuth session expired" — which is the same reason nobody noticed
the trigger was missing.
"""
import importlib.util
import json
import os
import pathlib
import stat

from journal_events import journal_events

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


loop = _load("loop")
state = _load("state")

GOAL = "0001-x.md"
BUILDER = "stubgraph"          # not "graphify": proves the configured builder is the one invoked


class _Source:
    """Minimal backlog source, same shape as test_finish_on_done.py's own double."""

    def __init__(self):
        self.completed, self.parked = [], []

    def complete(self, goal):
        self.completed.append(goal)

    def park(self, goal, reason, **kw):
        self.parked.append((goal, reason))


def _stub_builder(tmp_path):
    """A REAL executable on PATH that does what the real builder does to the filesystem: honour
    `--out <dir>` by writing `<dir>/<builder>-out/graph.json`. It also records its own argv, so a
    test can prove the invocation was shaped correctly rather than merely that something ran."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / BUILDER
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json, pathlib\n"
        "argv = sys.argv[1:]\n"
        "out = pathlib.Path(argv[argv.index('--out') + 1]) if '--out' in argv else pathlib.Path('.')\n"
        f"d = out / '{BUILDER}-out'\n"
        "d.mkdir(parents=True, exist_ok=True)\n"
        "(d / 'graph.json').write_text(json.dumps({'nodes': [], 'argv': argv}))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _repo(tmp_path, kg_block):
    """A repo root with a real .sdlc, a real (non-empty) corpus, and the given knowledge_graph
    config. Returns (sdlc_dir, repo_root)."""
    root = tmp_path / "repo"
    d = root / ".sdlc"
    (d / "state").mkdir(parents=True)
    # #707: a custom builder needs the operator's Git-local opt-in; this is the operator's own repo.
    import subprocess
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "--local", "sigma.allowRepositoryShellCommands", "true"], check=True)
    # ledger+journal on, matching test_loop.py's own `_telemetry_base`: the fail-open test below
    # reads back the `retro` event to prove the goal's bookkeeping survived a broken builder.
    cfg = {"budget": {}, "ledger": {"enabled": True, "actor": "rae"}, "journal": {"enabled": True}}
    if kg_block is not None:
        cfg["knowledge_graph"] = kg_block
    (d / "config.json").write_text(json.dumps(cfg))
    (d / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    analysis = d / "knowledge" / "analysis"
    analysis.mkdir(parents=True)
    (analysis / "issue-1.md").write_text("# Issue #1: a retro note\n")
    state.start_run(str(d))
    return str(d), root


def _graph(root):
    return root / f"{BUILDER}-out" / "graph.json"


def _on(**over):
    block = {"enabled": True, "scope": "research", "builder": BUILDER, "auto_refresh": True}
    block.update(over)
    return block


def test_recording_a_goal_with_a_retro_grade_refreshes_the_graph(tmp_path, monkeypatch):
    """THE REGRESSION. This is the issue's own done_when: a completed goal with auto_refresh: true
    produces the builder's graph.json with no manual /sigma-kg invocation anywhere."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert _graph(root).exists(), "a goal completed with auto_refresh: true did not refresh the graph"


def test_the_refreshed_graph_lands_where_status_actually_looks(tmp_path, monkeypatch):
    """The second half of #1562. Left to itself the builder writes `<corpus>/<builder>-out/`, which
    `kg.py::status()` — which reads `<repo-root>/<builder>-out/graph.json` — structurally cannot
    see. This repo still holds the evidence: an empty `.sdlc/knowledge/graphify-out/` from a hand-run
    on 2026-08-24, and `status` has reported `graph: not built` ever since. Asserting on
    `status()['graph_built']` pins the whole chain, not just that a file exists somewhere."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    kg_spec = importlib.util.spec_from_file_location(
        "kg", pathlib.Path(__file__).resolve().parent.parent
        / "skills" / "sigma-kg" / "scripts" / "kg.py")
    kg = importlib.util.module_from_spec(kg_spec); kg_spec.loader.exec_module(kg)
    assert kg.status(d)["graph_built"] is True
    assert not (pathlib.Path(d) / "knowledge" / f"{BUILDER}-out").exists(), \
        "the graph was written next to the corpus, where nothing downstream reads it"


def test_auto_refresh_false_does_not_refresh(tmp_path, monkeypatch):
    """NEGATIVE CONTROL, and the issue's own done_when. Without it the two tests above pass just as
    happily for a version that rebuilds unconditionally — which would spend a user's LLM quota on
    every goal they ever complete, having never asked for it."""
    d, root = _repo(tmp_path, _on(auto_refresh=False))
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert not _graph(root).exists(), "auto_refresh: false still rebuilt the graph"


def test_auto_refresh_absent_does_not_refresh(tmp_path, monkeypatch):
    """NEGATIVE CONTROL, absent-key form — the shape every repo scaffolded from
    skills/sigma-init/templates/config.json.tmpl actually ships with."""
    d, root = _repo(tmp_path, {"enabled": True, "scope": "research", "builder": BUILDER})
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert not _graph(root).exists()


def test_the_graph_being_disabled_does_not_refresh(tmp_path, monkeypatch):
    """NEGATIVE CONTROL. The graph is opt-in and off by default; a repo that never enabled it must
    never have a builder spawned on its behalf, whatever auto_refresh happens to say."""
    d, root = _repo(tmp_path, {"enabled": False, "builder": BUILDER, "auto_refresh": True})
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert not _graph(root).exists()


def test_a_goal_that_never_reached_retrospective_does_not_refresh(tmp_path, monkeypatch):
    """NEGATIVE CONTROL on the TRIGGER MOMENT, not the config. The docs place the refresh "at the
    end of the Retrospective phase", and both SKILL.md files say to omit --retro-grade entirely on a
    goal that never got there (a pre-work park, a failed pick). No retrospective, no new corpus note,
    nothing to refresh from — so refreshing anyway would be a rebuild the docs never promised."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "parked", "blocked on a decision")     # no retro_grade

    assert not _graph(root).exists(), "a goal that never ran Retrospective still rebuilt the graph"


def test_a_goal_that_parked_after_retrospective_still_refreshes(tmp_path, monkeypatch):
    """The mirror of the test above, and the reason the gate is `retro_grade`, not `outcome ==
    done`. `_record`'s own comment records that Retrospective always runs before Record for ANY
    terminal outcome, so a goal that completed its retro and then parked (a merge conflict found
    afterwards) has still written its corpus note and still deserves the refresh."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_stub_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    loop._record(d, _Source(), GOAL, "parked", "merge conflict", retro_grade="partial")

    assert _graph(root).exists()


def test_a_missing_builder_never_breaks_the_goal_record(tmp_path):
    """A builder that is not installed — or, as on this machine, installed but with no LLM backend
    it can reach — must cost the goal nothing. Note what actually guarantees this: the refresh runs
    in a CHILD PROCESS, so the builder's failure cannot propagate into `_record` at all. Removing
    the `except` in `_refresh_knowledge_graph` leaves this test green, which is why the test below
    exists as well — this one alone would be decoration for that except block."""
    d, root = _repo(tmp_path, _on(builder="definitely-not-installed-anywhere"))

    outcome = loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert outcome == "done"
    recorded = [e for e in journal_events(loop.ledger, d) if e["kind"] == "retro"]
    assert len(recorded) == 1 and recorded[0]["grade"] == "achieved"


def test_a_refresh_that_raises_never_breaks_the_goal_record(tmp_path, monkeypatch):
    """THE CONTROL FOR THE `except` ITSELF. The process boundary covers a builder that fails; it
    does NOT cover the spawn failing — a `TimeoutExpired` from a builder that ignores its own cap,
    an OSError from an exhausted process table, an unreadable interpreter path. Those raise inside
    `_record`, AFTER the ledger and action-log writes have landed, and would take the goal's whole
    terminal record with them. Only the kg.py hop is made to raise, so everything else in `_record`
    runs exactly as it normally does."""
    d, root = _repo(tmp_path, _on())
    real_run = loop.subprocess.run

    def exploding_run(argv, *a, **kw):
        if any("kg.py" in str(x) for x in argv):
            raise OSError("cannot allocate a process")
        return real_run(argv, *a, **kw)

    monkeypatch.setattr(loop.subprocess, "run", exploding_run)

    outcome = loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert outcome == "done"
    recorded = [e for e in journal_events(loop.ledger, d) if e["kind"] == "retro"]
    assert len(recorded) == 1 and recorded[0]["grade"] == "achieved"


# --------------------------------------------------------------------------- issue #2704
# The failure this file's docstring describes ("the same reason nobody noticed") was still silent
# after #1562: `_record` printed the builder's text once to stderr and it was gone. Now the
# documented `record` gesture leaves the builder's own words in `.sdlc/state/kg-refresh.json`,
# and the documented `next` gesture -- Sigma's own Python, so Cursor and Codex get it too --
# prints one once-a-day warning line on stderr that quotes them.

import subprocess
import sys


def _failing_builder(tmp_path):
    """A REAL executable on PATH that answers `--version` (so doctor's `<builder> installed` row
    and the wizard stay quiet) and otherwise fails the way graphify does with no backend: exit 1,
    the reason on stderr, nothing written anywhere."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / BUILDER
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        f"    print('{BUILDER} 1.0'); sys.exit(0)\n"
        "print('No LLM backend configured. Set one of: GEMINI_API_KEY, ANTHROPIC_API_KEY', "
        "file=sys.stderr)\n"
        "sys.exit(1)\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def test_a_failing_builder_leaves_its_own_words_on_disk_and_the_goal_still_records_done(tmp_path, monkeypatch):
    """THE SWALLOWED ERROR, on the docs' gesture. And the fail-open control in the same test: the
    recorded outcome is still `done`, whatever the builder said."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_failing_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")

    outcome = loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    assert outcome == "done"
    assert not _graph(root).exists()
    rec = json.loads((pathlib.Path(d) / "state" / "kg-refresh.json").read_text())
    assert rec["ok"] is False and rec["ran"] is True
    assert "No LLM backend configured" in rec["detail"]


def _next(d):
    """The documented pick gesture, as a real subprocess: stdout is the goal channel the skill
    parses, stderr is where a human reads warnings."""
    return subprocess.run([sys.executable, str(_SCRIPTS / "loop.py"), "next", d],
                          capture_output=True, text=True, timeout=120)


def test_the_next_pick_warns_once_a_day_on_every_host_and_quotes_the_builder(tmp_path, monkeypatch):
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_failing_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    first = _next(d)
    assert first.returncode == 0, first.stderr
    assert "never been built" in first.stderr and "No LLM backend configured" in first.stderr
    assert "sigma:" not in first.stdout, "the warning leaked into the goal channel"

    second = _next(d)
    assert second.returncode == 0, second.stderr
    assert "never been built" not in second.stderr, "the pick warned twice within a day"


def test_the_next_pick_is_silent_about_the_graph_when_auto_refresh_is_off(tmp_path, monkeypatch):
    """NEGATIVE CONTROL, on the pick gesture: the shipped default and a by-hand repo say nothing."""
    d, root = _repo(tmp_path, _on(auto_refresh=False))
    monkeypatch.setenv("PATH", f"{_failing_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    r = _next(d)
    assert r.returncode == 0, r.stderr
    assert "knowledge graph" not in r.stderr


def test_a_headless_worker_does_not_spend_the_days_warning(tmp_path, monkeypatch):
    """Review round 1, finding 2: the stamp is shared with the session-start hook, so a supervised
    worker (SIGMA_RUN_ID set, nobody reading its stderr) printing the line would keep the
    human's own session start silent all day. Control first: the same repo warns when not headless."""
    d, root = _repo(tmp_path, _on())
    monkeypatch.setenv("PATH", f"{_failing_builder(tmp_path)}{os.pathsep}{os.environ['PATH']}")
    loop._record(d, _Source(), GOAL, "done", "", retro_grade="achieved")

    env = {**os.environ, "SIGMA_RUN_ID": "supervise-1-2-3"}
    headless = subprocess.run([sys.executable, str(_SCRIPTS / "loop.py"), "next", d],
                              capture_output=True, text=True, timeout=120, env=env)
    assert headless.returncode == 0, headless.stderr
    assert "never been built" not in headless.stderr
    assert not (pathlib.Path(d) / "state" / "kg-warned.stamp").exists(), "a headless pick consumed the stamp"

    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    assert "never been built" in _next(d).stderr
