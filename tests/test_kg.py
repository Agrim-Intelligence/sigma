"""The knowledge-graph helper is the deterministic side of the optional KG feature: read config,
locate the corpus, compute the build plan per scope. Disabled by default; the builder (graphify) is
a soft dep driven by the /sigma-kg skill, not by this helper."""
import json, pathlib, importlib.util, tempfile

KG = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-kg" / "scripts" / "kg.py"


def _kg():
    spec = importlib.util.spec_from_file_location("kg", KG)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(d, kg_block):
    base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
    cfg = {"budget": {}}
    if kg_block is not None:
        cfg["knowledge_graph"] = kg_block
    (base / "config.json").write_text(json.dumps(cfg))
    return str(base)


def test_disabled_by_default_when_no_block():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.load_config(base)["enabled"] is False
        assert kg.build_plan(base) is None              # disabled -> no plan


def test_disabled_explicitly():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": False, "scope": "full"})
        assert kg.build_plan(base) is None


def test_enabled_full_scope_includes_code():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "scope": "full"})
        plan = kg.build_plan(base, repo_root=d)
        assert plan["include_code"] is True and plan["code_root"] is not None
        assert plan["builder"] == "graphify" and plan["corpus"].endswith("knowledge")


def test_enabled_research_scope_skips_code():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "scope": "research"})
        plan = kg.build_plan(base, repo_root=d)
        assert plan["include_code"] is False and plan["code_root"] is None


def test_builder_and_auto_refresh_configurable():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "builder": "mygraph", "auto_refresh": True})
        plan = kg.build_plan(base)
        assert plan["builder"] == "mygraph" and plan["auto_refresh"] is True


def test_enabled_must_be_strict_true():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": "true"})        # stringy -> treated as disabled
        assert kg.load_config(base)["enabled"] is False and kg.build_plan(base) is None


def test_status_graph_built_follows_builder_output_dir():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "builder": "mygraph"})
        out = pathlib.Path(d) / "mygraph-out"; out.mkdir(); (out / "graph.json").write_text("{}")
        assert kg.status(base)["graph_built"] is True   # not hardcoded to graphify-out


def test_gap_log_appends_and_lists():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_list(base) == []                       # nothing logged yet
        assert kg.gap_log(base, "how does auth refresh work?") is True
        assert kg.gap_log(base, "where is the retry budget set?") is True
        assert kg.gap_list(base) == ["how does auth refresh work?",
                                     "where is the retry budget set?"]


def test_gap_log_dedups_so_it_cannot_bloat():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_log(base, "same question") is True
        assert kg.gap_log(base, "same question") is False         # exact dup skipped
        assert kg.gap_log(base, "  same question  ") is False     # whitespace-normalized dup
        assert kg.gap_list(base) == ["same question"]


def test_gap_log_ignores_empty():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.gap_log(base, "   ") is False
        assert kg.gap_list(base) == []


def test_main_gap_log_and_list():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.main(["kg.py", "gap", "log", "what calls _set_board_status?", base]) == 0
        assert kg.main(["kg.py", "gap", "list", base]) == 0
        assert kg.gap_list(base) == ["what calls _set_board_status?"]   # logged + listable via CLI


def test_gap_resolve_closes_an_open_gap():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "q-one"); kg.gap_log(base, "q-two")
        assert kg.gap_resolve(base, "q-one") is True
        assert kg.gap_list(base) == ["q-two"]              # resolved drops out of the open list


def test_gap_resolve_unknown_is_noop():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "real")
        assert kg.gap_resolve(base, "never logged") is False
        assert kg.gap_list(base) == ["real"]               # untouched


def test_resolved_gap_reopens_if_missed_again():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "flaky"); kg.gap_resolve(base, "flaky")
        assert kg.gap_list(base) == []                     # closed
        assert kg.gap_log(base, "flaky") is True           # missed again -> re-opens
        assert kg.gap_list(base) == ["flaky"]


def test_main_gap_resolve():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        kg.gap_log(base, "via cli")
        assert kg.main(["kg.py", "gap", "resolve", "via cli", base]) == 0
        assert kg.gap_list(base) == []


def test_maintain_flags_stale_source_notes():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        (pathlib.Path(d) / "src").mkdir()
        (pathlib.Path(d) / "src" / "live.py").write_text("x")     # exists under repo_root
        an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
        (an / "fresh.md").write_text("documents `src/live.py` which still exists")
        (an / "stale.md").write_text("documents `backend/gone.py` long deleted")
        stale = kg.maintain_report(base, repo_root=d)["stale"]
        assert [s["note"] for s in stale] == ["analysis/stale.md"]   # only the dead-path note
        assert stale[0]["missing"] == ["backend/gone.py"]


def test_maintain_finds_exact_dups():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
        (an / "a.md").write_text("identical body")
        (an / "b.md").write_text("identical body")
        (an / "c.md").write_text("different")
        assert kg.maintain_report(base, repo_root=d)["dups"] == [["analysis/a.md", "analysis/b.md"]]


def test_maintain_clean_corpus_reports_nothing():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        rep = kg.maintain_report(base, repo_root=d)               # empty corpus
        assert rep["stale"] == [] and rep["dups"] == []
        assert rep["counts"] == {"research": 0, "analysis": 0, "gaps": 0}
        assert rep["over_threshold"] is False


def test_main_maintain_runs():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.main(["kg.py", "maintain", base, d]) == 0       # report-only, exits clean


def test_load_config_tolerates_missing_or_garbage_config():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        # no .sdlc/config.json at all -> safe defaults, disabled
        assert kg.load_config(str(pathlib.Path(d) / ".sdlc"))["enabled"] is False


def test_load_config_handles_literal_null_json():
    """Regression (#475): config.json containing the valid JSON value `null` parses successfully
    but produces a NoneType object. Without a guard, the next line's cfg.get() would crash with
    a raw AttributeError. This test verifies that null config -> safe defaults (disabled), not
    a crash."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        # Overwrite the config with literal null
        (pathlib.Path(base) / "config.json").write_text("null")
        result = kg.load_config(base)
        assert result["enabled"] is False  # null config -> defaults, disabled
        assert result["scope"] == "full"   # all defaults apply
        assert result["builder"] == "graphify"


def _sdlc_discovery(d, kg_block, discovery_source=None):
    """Like _sdlc, but also lets a test set discovery.source (issue #1050's note_id() needs it)."""
    base = pathlib.Path(d) / ".sdlc"; base.mkdir(parents=True)
    cfg = {"budget": {}}
    if kg_block is not None:
        cfg["knowledge_graph"] = kg_block
    if discovery_source is not None:
        cfg["discovery"] = {"source": discovery_source}
    (base / "config.json").write_text(json.dumps(cfg))
    return str(base)


def test_note_id_github_mode():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, None, discovery_source="github")
        assert kg.note_id(base, "1050") == "issue-1050"


def test_note_id_local_mode_from_full_path():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, None, discovery_source="local-goals")
        assert kg.note_id(base, ".sdlc/goals/0004-foo.md") == "goal-0004"


def test_note_id_local_mode_from_bare_id():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, None, discovery_source="local-goals")
        assert kg.note_id(base, "7") == "goal-0007"


def test_note_id_defaults_to_local_when_source_unset():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)      # no discovery block at all
        assert kg.note_id(base, ".sdlc/goals/0002-bar.md") == "goal-0002"


def test_note_id_local_mode_no_digits_falls_back_verbatim():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, None, discovery_source="local-goals")
        assert kg.note_id(base, "no-digits-here") == "goal-no-digits-here"


def test_write_note_noop_missing_block():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)
        assert kg.write_note(base, "issue-1", "content") is False
        assert not (pathlib.Path(base) / "knowledge" / "analysis" / "issue-1.md").exists()


def test_write_note_noop_explicit_false():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": False})
        assert kg.write_note(base, "issue-1", "content") is False
        assert not (pathlib.Path(base) / "knowledge" / "analysis" / "issue-1.md").exists()


def test_write_note_noop_stringy_true():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": "true"})
        assert kg.write_note(base, "issue-1", "content") is False
        assert not (pathlib.Path(base) / "knowledge" / "analysis" / "issue-1.md").exists()


def test_write_note_writes_when_enabled():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True})
        assert kg.write_note(base, "issue-1050", "# Issue #1050: Title\n\nBody text.") is True
        f = pathlib.Path(base) / "knowledge" / "analysis" / "issue-1050.md"
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "# Issue #1050: Title\n\nBody text.\n"   # trailing \n normalized


def test_main_note_cli_writes_via_stdin(monkeypatch, capsys):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, {"enabled": True}, discovery_source="github")
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("# Issue #42: Something\n\nSummary.\n"))
        rc = kg.main(["kg.py", "note", base, "42"])
        assert rc == 0
        f = pathlib.Path(base) / "knowledge" / "analysis" / "issue-42.md"
        assert f.exists()
        assert "Summary." in f.read_text(encoding="utf-8")


def test_main_note_cli_disabled_prints_skip_not_error(monkeypatch, capsys):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, None, discovery_source="github")   # no knowledge_graph block -> disabled
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("some content\n"))
        rc = kg.main(["kg.py", "note", base, "42"])
        assert rc == 0                                                # fail-open: never a hard error
        assert not (pathlib.Path(base) / "knowledge" / "analysis" / "issue-42.md").exists()
        out = capsys.readouterr().out.lower()
        assert "disabled" in out or "skip" in out


def test_main_note_cli_empty_stdin_is_usage_error(monkeypatch, capsys):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc_discovery(d, {"enabled": True}, discovery_source="github")
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO("   \n"))
        rc = kg.main(["kg.py", "note", base, "42"])
        assert rc == 2
        assert not (pathlib.Path(base) / "knowledge" / "analysis" / "issue-42.md").exists()


def test_load_config_handles_valid_json_non_dict():
    """Extended regression (#475): not just `null`, but ANY valid JSON that isn't a dict
    (a list, string, number) should be treated the same way: defaults apply, disabled."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, None)

        # Test with a list
        (pathlib.Path(base) / "config.json").write_text("[]")
        result = kg.load_config(base)
        assert result["enabled"] is False

        # Test with a string
        (pathlib.Path(base) / "config.json").write_text('"not a dict"')
        result = kg.load_config(base)
        assert result["enabled"] is False

        # Test with a number
        (pathlib.Path(base) / "config.json").write_text("42")
        result = kg.load_config(base)
        assert result["enabled"] is False


# --------------------------------------------------------------------------- issue #1562: refresh()
# `knowledge_graph.auto_refresh` was documented in three places as rebuilding the graph at the end
# of every Retrospective, and NOTHING read it: `build_plan()["auto_refresh"]` had zero consumers and
# `kg.py` had no builder invocation at all. These tests pin the gate in both directions -- the
# positive case would pass just as happily against a function that always builds, so
# `test_refresh_does_not_invoke_the_builder_when_auto_refresh_is_*` are what make the gate real.
#
# THE ASSERTION IS ON `ran`, NOT ON `detail`. `detail` is the function's own account of itself and
# would read correctly for an implementation that never invoked anything -- the same mistake
# tests/test_finish_on_done.py's docstring records for `finish`'s return string.


def _recorder():
    """A fake builder runner. Records every argv it is asked to run and reports success, so a test
    can assert on WHAT was invoked -- or, in the negative controls, that nothing was."""
    calls = []

    def run(argv, timeout=None):
        calls.append(argv)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    return calls, run


def _corpus(base):
    """The corpus dir has to exist for refresh() to have anything to build from."""
    (pathlib.Path(base) / "knowledge" / "analysis").mkdir(parents=True, exist_ok=True)
    (pathlib.Path(base) / "knowledge" / "analysis" / "issue-1.md").write_text("# note\n")
    return base


def test_refresh_invokes_the_builder_when_auto_refresh_is_on():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": True}))
        calls, run = _recorder()
        result = kg.refresh(base, runner=run)
        assert result["ran"] is True and result["ok"] is True
        assert len(calls) == 1, "the builder was not invoked exactly once"
        assert calls[0][0] == "graphify" and calls[0][1] == "extract"


def test_refresh_points_the_builder_output_at_the_repo_root_where_status_looks():
    """The second half of #1562. `graphify extract <corpus>` writes `<corpus>/graphify-out/`, but
    `status()` reads `<repo-root>/<builder>-out/graph.json` -- so a fully SUCCESSFUL build used to
    land where `status`, `/sigma-context`'s gate and `graphify query`'s default path all structurally
    could not see it. This repo still carries the evidence: an empty `.sdlc/knowledge/graphify-out/`
    from a hand-run on 2026-08-24, with `status` reporting `graph: not built` ever since."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": True}))
        calls, run = _recorder()
        kg.refresh(base, runner=run)
        argv = calls[0]
        assert "--out" in argv, "no --out: the graph lands next to the corpus, where nothing looks"
        out = argv[argv.index("--out") + 1]
        # the same dir status() derives graph_built from: the parent of .sdlc
        assert pathlib.Path(out).resolve() == pathlib.Path(base).resolve().parent


def test_refresh_does_not_invoke_the_builder_when_auto_refresh_is_false():
    """NEGATIVE CONTROL. Without this the positive test above passes for a no-op gate."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": False}))
        calls, run = _recorder()
        result = kg.refresh(base, runner=run)
        assert result["ran"] is False
        assert calls == [], "auto_refresh: false still invoked the builder"


def test_refresh_does_not_invoke_the_builder_when_auto_refresh_is_absent():
    """NEGATIVE CONTROL, absent-key form -- the shape every repo scaffolded from
    skills/sigma-init/templates/config.json.tmpl that never touched the key actually has."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research"}))
        calls, run = _recorder()
        result = kg.refresh(base, runner=run)
        assert result["ran"] is False
        assert calls == []


def test_refresh_does_not_invoke_the_builder_when_the_graph_is_disabled():
    """NEGATIVE CONTROL. `enabled` gates ahead of `auto_refresh`: a repo that never opted in must
    never spawn a builder even if auto_refresh was left true in a copied config."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": False, "auto_refresh": True}))
        calls, run = _recorder()
        result = kg.refresh(base, runner=run)
        assert result["ran"] is False and result["ok"] is True
        assert calls == []


def test_refresh_declines_scope_full_instead_of_building_the_wrong_thing():
    """`scope: full` needs a separate code-extract + merge-graphs pass. Doing the corpus-only half
    would silently ship a graph missing the code the config asked for -- worse than declining."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "full", "auto_refresh": True}))
        calls, run = _recorder()
        result = kg.refresh(base, repo_root=d, runner=run)
        assert result["ran"] is False and result["ok"] is False
        assert calls == []
        assert "full" in result["detail"]


def test_refresh_is_a_no_op_when_the_corpus_does_not_exist_yet():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": True})
        calls, run = _recorder()
        result = kg.refresh(base, runner=run)
        assert result["ran"] is False and calls == []


def test_refresh_surfaces_the_builders_own_error_text_rather_than_a_generic_failure():
    """The failure a user actually hits (`No LLM backend configured...`) is the builder's, and it
    names the fix. Swallowing it for a generic 'refresh failed' is what made this feature silent."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": True}))

        def run(argv, timeout=None):
            return {"returncode": 1, "stdout": "", "stderr": "No LLM backend configured. Set one of: ..."}

        result = kg.refresh(base, runner=run)
        assert result["ran"] is True and result["ok"] is False
        assert "No LLM backend configured" in result["detail"]


def test_refresh_never_raises_when_the_builder_is_not_installed():
    """Fail-open, like note(). A KG hiccup must never break a goal's completion -- `_record()` calls
    this after the goal is already recorded, and an exception here must not escape."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research", "auto_refresh": True}))

        def run(argv, timeout=None):
            raise FileNotFoundError("graphify")

        result = kg.refresh(base, runner=run)          # must not raise
        assert result["ok"] is False and "graphify" in result["detail"]


def test_refresh_cli_verb_reports_ok_and_exits_zero():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "research"}))   # auto_refresh off
        assert kg.main(["kg.py", "refresh", base]) == 0


def test_refresh_cli_verb_exits_nonzero_on_a_real_failure():
    """A caller (and a human) must be able to tell a gated no-op from a genuine failure."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": True, "scope": "full", "auto_refresh": True}))
        assert kg.main(["kg.py", "refresh", base]) == 1


# --------------------------------------------------------------------------- issue #2704: warn()
# On the maintainer's machine `graphify-out/` had been an empty directory for three weeks with
# `auto_refresh: true`: the builder's own error was printed once per completed goal into the loop's
# stream and then lost, session start said nothing, and the only standing signal lived in
# `/sigma-doctor`. Two root causes, two fixes: `refresh()` now RECORDS its last result, and `warn()`
# composes one once-a-day line from that record plus what the filesystem shows.
import os as _os
import time as _time


def _stub_on_path(tmp, name):
    """A builder executable on PATH that only has to EXIST -- warn() asks `shutil.which`, nothing
    more, so the prerequisite it names is 'not installed' vs 'installed but failing'."""
    b = pathlib.Path(tmp) / "bin"; b.mkdir(exist_ok=True)
    p = b / name; p.write_text("#!/bin/sh\nexit 0\n"); p.chmod(0o755)
    return str(b)


def _on(builder="stubgraph", **over):
    block = {"enabled": True, "scope": "research", "builder": builder, "auto_refresh": True}
    block.update(over)
    return block


def _graph(base, builder="stubgraph", age=0):
    out = pathlib.Path(base).resolve().parent / f"{builder}-out"; out.mkdir(exist_ok=True)
    g = out / "graph.json"; g.write_text("{}")
    if age:
        t = _time.time() - age; _os.utime(g, (t, t))
    return g


def _record_file(base):
    return pathlib.Path(base) / "state" / "kg-refresh.json"


def test_refresh_records_the_builders_failure_so_it_outlives_the_goal():
    """THE SWALLOWED ERROR. `_record` forwards the text to stderr once and it is gone; nothing
    downstream could ever say WHY the graph was never built. Now the record on disk can."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))

        def run(argv, timeout=None):
            return {"returncode": 1, "stdout": "", "stderr": "No LLM backend configured. Set one of: ..."}

        kg.refresh(base, runner=run)
        rec = json.loads(_record_file(base).read_text())
        assert rec["ok"] is False and rec["ran"] is True
        assert "No LLM backend configured" in rec["detail"]
        assert abs(rec["at"] - _time.time()) < 60


def test_refresh_records_a_success_too():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        _, run = _recorder()
        kg.refresh(base, runner=run)
        rec = json.loads(_record_file(base).read_text())
        assert rec["ok"] is True and rec["ran"] is True


def test_refresh_records_the_scope_full_refusal_because_that_is_a_reason_too():
    """`scope: full` is declined by name; that refusal is exactly the prerequisite warn() should
    surface, so it is recorded like a failed run (ok=False, ran=False)."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on(scope="full")))
        _, run = _recorder()
        kg.refresh(base, repo_root=d, runner=run)
        rec = json.loads(_record_file(base).read_text())
        assert rec["ok"] is False and rec["ran"] is False and "full" in rec["detail"]


def test_a_gated_no_op_records_nothing():
    """NEGATIVE CONTROL: a repo with auto_refresh off must not grow a state file on every goal."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on(auto_refresh=False)))
        _, run = _recorder()
        kg.refresh(base, runner=run)
        assert not _record_file(base).exists()


def test_an_unwritable_state_dir_never_makes_refresh_raise():
    """Fail-open, same posture as refresh() itself: `_record` has already landed the goal."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        (pathlib.Path(base) / "state").write_text("a file where the dir should be")
        _, run = _recorder()
        result = kg.refresh(base, runner=run)          # must not raise
        assert result["ok"] is True and result["ran"] is True


def test_status_measures_the_corpus_in_documents_and_bytes():
    """The first-build guide says: state the measured size, never assert it. This is the measure."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        (pathlib.Path(base) / "knowledge" / "research").mkdir()
        (pathlib.Path(base) / "knowledge" / "research" / "a.md").write_text("x" * 1000)
        s = kg.status(base)
        assert s["corpus_docs"] == 2
        assert s["corpus_bytes"] == 1000 + len("# note\n")


def test_status_cli_prints_the_corpus_size(capsys):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        kg.main(["kg.py", "status", base])
        assert "1 documents" in capsys.readouterr().out


def test_warn_fires_when_auto_refresh_is_on_and_the_graph_was_never_built():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        text = kg.warn(base)
        assert "never been built" in text
        assert "1 documents" in text                          # measured corpus size
        assert "not measured" in text                         # first-build cost, honestly
        assert "/sigma-kg" in text and "/sigma-doctor" in text


def test_warn_fires_when_the_graph_is_older_than_the_newest_corpus_document():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        _graph(base, age=3600)                                # built an hour before the note
        text = kg.warn(base)
        assert "stale" in text and "issue-1.md" in text


def test_warn_names_a_builder_that_is_not_installed(monkeypatch):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on(builder="graphify")))
        # PATH without any graphify: the prerequisite is the install, and for the default builder
        # the exact line doctor already hands out.
        monkeypatch.setenv("PATH", str(pathlib.Path(d) / "nowhere"))
        text = kg.warn(base)
        assert "not on PATH" in text and "pip install graphifyy" in text


def test_warn_quotes_the_last_recorded_failure_verbatim(monkeypatch):
    """The prerequisite is whatever the BUILDER said -- Sigma guesses at no env var."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        monkeypatch.setenv("PATH", _stub_on_path(d, "stubgraph") + _os.pathsep + _os.environ["PATH"])
        kg.refresh(base, runner=lambda argv, timeout=None: {
            "returncode": 1, "stdout": "", "stderr": "No LLM backend configured. Set one of: X, Y"})
        text = kg.warn(base)
        assert "No LLM backend configured. Set one of: X, Y" in text
        assert "ago" in text                                  # age is the tell


def test_warn_says_never_attempted_when_no_refresh_was_ever_recorded(monkeypatch):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        monkeypatch.setenv("PATH", _stub_on_path(d, "stubgraph") + _os.pathsep + _os.environ["PATH"])
        assert "never attempted" in kg.warn(base)


def test_warn_is_silent_when_the_graph_is_fresh():
    """NEGATIVE CONTROL (issue done_when): fresh graph -> no text AND no stamp written."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        _graph(base)                                          # built after the note
        assert kg.warn(base) == ""
        assert not (pathlib.Path(base) / "state" / "kg-warned.stamp").exists()


def test_warn_is_silent_when_auto_refresh_is_off():
    """NEGATIVE CONTROL (issue done_when). A repo building by hand has nothing wrong with it."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on(auto_refresh=False)))
        assert kg.warn(base) == ""


def test_warn_is_silent_on_the_shipped_default():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, {"enabled": False, "scope": "full", "builder": "graphify",
                                 "auto_refresh": False}))
        assert kg.warn(base) == ""
        assert not (pathlib.Path(base) / "state").exists()   # writes nothing in a repo that opted out


def test_warn_fires_at_most_once_a_day():
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        assert kg.warn(base) != ""
        assert kg.warn(base) == "", "warned twice within a day"
        stamp = pathlib.Path(base) / "state" / "kg-warned.stamp"
        t = _time.time() - 25 * 3600; _os.utime(stamp, (t, t))
        assert kg.warn(base) != "", "a day-old stamp still suppressed the warning"


def test_warn_cli_prints_the_line_and_exits_zero_either_way(capsys):
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        assert kg.main(["kg.py", "warn", base]) == 0
        assert "never been built" in capsys.readouterr().out
        assert kg.main(["kg.py", "warn", base]) == 0          # throttled: silent, still exit 0
        assert capsys.readouterr().out == ""


# ------------------------------------------------- #2704 review round 1: the stale+ok branch and empties
def test_warn_on_a_stale_graph_after_an_ok_build_tells_the_truth(monkeypatch):
    """BLOCKING finding of the author-blind review: with a built graph, an ok record and a document
    written since (the everyday state -- one `gap log` makes it so), the line used to say "no graph
    is where status() looks ... First-build cost ... build it by hand", all three false. A mutation
    of that branch passed every test, so this is the test."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))
        monkeypatch.setenv("PATH", _stub_on_path(d, "stubgraph") + _os.pathsep + _os.environ["PATH"])
        _graph(base, age=3600)                                # built an hour ago...
        _, run = _recorder()
        kg.refresh(base, runner=run)                          # ...reported ok (the runner "built" it)...
        rec = _record_file(base); t = _time.time() - 1800; _os.utime(rec, (t, t))
        rec.write_text(json.dumps({**json.loads(rec.read_text()), "at": t}))   # ...half an hour ago
        note = pathlib.Path(base) / "knowledge" / "analysis" / "issue-1.md"
        note.write_text("# a note written after that build\n")               # ...and a note since
        text = kg.warn(base)
        assert "stale" in text and "reported ok" in text and "next Retrospective" in text
        assert "no graph is where" not in text
        assert "First-build" not in text and "build it by hand" not in text


def test_warn_on_a_stale_graph_whose_ok_build_did_not_update_it_says_so(monkeypatch):
    """Review round 2, N1: the ok record is newer than every stale document, so that build ran over
    them and left graph.json untouched. "The next Retrospective rebuilds it" would be a daily false
    promise; the line names the real state instead."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _corpus(_sdlc(d, _on()))                       # note written now
        monkeypatch.setenv("PATH", _stub_on_path(d, "stubgraph") + _os.pathsep + _os.environ["PATH"])
        _graph(base, age=3600)                                # graph older than the note
        _, run = _recorder()
        kg.refresh(base, runner=run)                          # ok, AFTER the note, graph untouched
        text = kg.warn(base)
        assert "reported ok, yet" in text and "was not updated by it" in text
        assert "next Retrospective" not in text


def test_warn_is_silent_on_an_empty_corpus():
    """refresh() calls a 0-document corpus "nothing to refresh yet"; warn must not tell the user to
    hand-build nothing."""
    kg = _kg()
    with tempfile.TemporaryDirectory() as d:
        base = _sdlc(d, _on())                                # no corpus at all
        assert kg.warn(base) == ""
        (pathlib.Path(base) / "knowledge").mkdir()            # a corpus dir with no .md in it
        assert kg.warn(base) == ""
