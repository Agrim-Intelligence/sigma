"""#2702: bound the knowledge corpus. Web captures (`.sdlc/knowledge/research/web/`, local and
machine-derived) get an OPT-IN retention pass that archives by age and by a byte bound DERIVED from the
disk the corpus sits on — archive, never delete, count reported, idempotent. The shared analysis notes
stay propose-only: `kg.py maintain` prints the exact archive command per proposal and applies nothing.

Every test here is a control: each was run red against a broken or absent seam before it went green
(the PR body records the runs). Absence assertions carry a non-vacuity check first."""
import collections, importlib.util, json, math, os, pathlib, shlex, subprocess, time

import pytest

KG = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-kg" / "scripts" / "kg.py"
_DU = collections.namedtuple("usage", "total used free")
DAY = 86400


def _kg():
    spec = importlib.util.spec_from_file_location("kg_retention_under_test", KG)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _sdlc(tmp_path, kg_block):
    base = tmp_path / ".sdlc"; base.mkdir()
    (base / "config.json").write_text(json.dumps({"knowledge_graph": kg_block}))
    return str(base)


def _web(base):
    w = pathlib.Path(base) / "knowledge" / "research" / "web"; w.mkdir(parents=True, exist_ok=True); return w


def _capture(web, name, age_days, size=600):
    p = web / f"{name}.md"; p.write_bytes(b"x" * size)
    t = time.time() - age_days * DAY; os.utime(p, (t, t)); return p


def _archive(base):
    return pathlib.Path(base) / "knowledge" / "archive" / "research" / "web"


def _big_disk(monkeypatch, kg, total=10**12):
    monkeypatch.setattr(kg.shutil, "disk_usage", lambda p: _DU(total, 0, total))


ON = {"enabled": True, "web_retention_enabled": True}


# --- automatic web-capture retention ------------------------------------------------------------

def test_c1_retention_is_off_by_default_and_moves_nothing(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True})                     # KG on, retention key ABSENT
    web = _web(base); old = [_capture(web, f"old{i}", 400) for i in range(5)]
    assert all(p.exists() for p in old)                            # non-vacuity: they are there to lose
    res = kg.retain_web(base)
    assert res["ran"] is False and res["archived"] == 0
    assert all(p.exists() for p in old) and not _archive(base).exists()


def test_c2_age_bound_archives_old_captures_and_reports_the_count(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    old = [_capture(web, f"old{i}", 200) for i in range(5)]
    fresh = [_capture(web, f"new{i}", 1) for i in range(3)]
    assert len(list(web.glob("*.md"))) == 8
    res = kg.retain_web(base)
    assert res["ok"] and res["ran"] and res["archived"] == 5 and res["remaining"] == 3
    assert sorted(p.name for p in web.glob("*.md")) == sorted(p.name for p in fresh)
    assert sorted(p.name for p in _archive(base).glob("*.md")) == sorted(p.name for p in old)
    assert len(list(web.glob("*.md"))) + len(list(_archive(base).glob("*.md"))) == 8   # nothing deleted


def test_c3_the_byte_bound_is_derived_from_the_disk_and_moves_with_it(tmp_path, monkeypatch):
    kg = _kg()
    base = _sdlc(tmp_path, ON); _web(base)
    _big_disk(monkeypatch, kg, total=100 * 10**9)
    small = kg.web_bound(base)
    _big_disk(monkeypatch, kg, total=10**12)
    large = kg.web_bound(base)
    assert small == int(100 * 10**9 * 0.0001) == 10_000_000
    assert large == int(10**12 * 0.0001) == 100_000_000 and large == 10 * small


def test_c3_over_the_derived_bound_the_oldest_go_first_until_under(tmp_path, monkeypatch):
    kg = _kg()
    base = _sdlc(tmp_path, {**ON, "web_retention_days": 0})    # age criterion OFF: bytes alone decide
    web = _web(base)
    files = [_capture(web, f"c{i}", 10 - i) for i in range(10)]     # c0 oldest ... c9 newest, 600 B each
    st = files[0].stat()
    occ = st.st_blocks * 512 if st.st_blocks else st.st_size        # the literal occupancy, NOT the function under test
    _big_disk(monkeypatch, kg, total=int(occ * 4 / 0.0001))         # bound = exactly 4 files' occupancy
    res = kg.retain_web(base)
    assert res["bound_bytes"] == occ * 4
    assert res["archived"] == 6 and res["remaining"] == 4
    assert sorted(p.name for p in web.glob("*.md")) == ["c6.md", "c7.md", "c8.md", "c9.md"]
    assert sum(kg._occupancy(p.stat()) for p in web.glob("*.md")) <= res["bound_bytes"]


@pytest.mark.parametrize("bad", [{"web_retention_disk_share": 0}, {"web_retention_disk_share": "abc"},
                                 {"web_retention_disk_share": 2}, {"web_retention_days": "x"},
                                 {"web_retention_days": -1}, {"web_retention_days": float("nan")},
                                 {"web_retention_disk_share": float("nan")}, {"web_retention_days": True},
                                 {"web_retention_disk_share": False}, {"web_retention_days": float("inf")}])
def test_c3b_a_malformed_limit_refuses_and_moves_nothing(tmp_path, monkeypatch, bad):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {**ON, **bad}); web = _web(base)
    old = [_capture(web, f"old{i}", 400) for i in range(3)]
    assert all(p.exists() for p in old)
    res = kg.retain_web(base)
    assert res["ok"] is False and res["ran"] is False and res["archived"] == 0
    assert next(iter(bad)) in res["detail"]                          # names the offending key
    assert all(p.exists() for p in old) and not _archive(base).exists()


def test_c4_control_a_corpus_below_the_bound_is_untouched(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    fresh = {p.name: p.read_bytes() for p in (_capture(web, f"n{i}", 2, size=100 + i) for i in range(4))}
    assert len(fresh) == 4                                           # non-vacuity
    res = kg.retain_web(base)
    assert res["ok"] and res["ran"] and res["archived"] == 0 and res["remaining"] == 4
    assert {p.name: p.read_bytes() for p in web.glob("*.md")} == fresh
    assert not _archive(base).exists()


def test_c5_a_second_pass_archives_nothing(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    [_capture(web, f"old{i}", 200) for i in range(4)]; _capture(web, "new", 1)
    first = kg.retain_web(base); second = kg.retain_web(base)
    assert first["archived"] == 4
    assert second["ok"] and second["archived"] == 0 and second["remaining"] == first["remaining"] == 1


def test_c6_a_name_already_in_the_archive_is_skipped_never_overwritten(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    live = _capture(web, "dup", 300); live.write_bytes(b"live")
    t = time.time() - 300 * DAY; os.utime(live, (t, t))              # the write above reset its mtime
    arch = _archive(base); arch.mkdir(parents=True); (arch / "dup.md").write_bytes(b"archived-earlier")
    res = kg.retain_web(base)
    assert res["ok"] and res["archived"] == 0 and res["skipped"] == 1
    assert live.read_bytes() == b"live" and (arch / "dup.md").read_bytes() == b"archived-earlier"


def test_c7a_a_permission_error_is_reported_never_raised(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    old = [_capture(web, f"old{i}", 300) for i in range(2)]

    def boom(self, target): raise PermissionError("read-only volume")
    monkeypatch.setattr(kg.pathlib.Path, "rename", boom)
    res = kg.retain_web(base)
    assert res["ok"] is False and "read-only volume" in res["detail"]
    assert all(p.exists() for p in old)


def test_c7b_a_file_another_worker_already_moved_counts_as_raced_not_failed(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    [_capture(web, f"old{i}", 300) for i in range(3)]
    real = kg.pathlib.Path.rename

    def racy(self, target):
        if self.name == "old1.md":
            raise FileNotFoundError(self)                          # a sibling _record got there first
        return real(self, target)
    monkeypatch.setattr(kg.pathlib.Path, "rename", racy)
    res = kg.retain_web(base)
    assert res["ok"] is True and res["raced"] == 1 and res["archived"] == 2


def test_c8_the_refresh_verb_runs_retention_first_and_reports_it_on_stderr(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    [_capture(web, f"old{i}", 200) for i in range(5)]
    assert kg.main(["kg.py", "refresh", base]) == 0                # auto_refresh off -> gated no-op
    out, err = capsys.readouterr()
    assert "archived 5 web capture" in err                          # a real action reaches the loop's console
    assert "knowledge-graph refresh:" in out                        # the existing gated no-op line, unchanged
    assert len(list(_archive(base).glob("*.md"))) == 5


def test_c8_the_refresh_verb_is_silent_about_retention_when_it_is_off(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); web = _web(base)
    [_capture(web, f"old{i}", 200) for i in range(5)]
    assert kg.main(["kg.py", "refresh", base]) == 0
    out, err = capsys.readouterr()
    assert "knowledge-graph refresh:" in out                        # non-vacuous: the verb DID speak
    assert "archived" not in out + err and "retention" not in err
    assert len(list(web.glob("*.md"))) == 5


def test_c8_the_refresh_verb_exits_one_when_retention_refuses(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {**ON, "web_retention_disk_share": 0}); web = _web(base)
    _capture(web, "old", 200)
    assert kg.main(["kg.py", "refresh", base]) == 1
    assert "web_retention_disk_share" in capsys.readouterr().err


def test_c9_the_retain_verb_is_the_operators_opt_in(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); web = _web(base)   # retention key ABSENT
    [_capture(web, f"old{i}", 200) for i in range(2)]
    assert kg.main(["kg.py", "retain", base]) == 0
    assert "archived 2 web capture" in capsys.readouterr().err
    assert len(list(_archive(base).glob("*.md"))) == 2


def test_c9_the_retain_verb_does_nothing_when_the_graph_is_disabled(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": False, "web_retention_enabled": True}); web = _web(base)
    [_capture(web, f"old{i}", 200) for i in range(2)]
    assert kg.main(["kg.py", "retain", base]) == 0
    out, err = capsys.readouterr()
    assert "disabled" in out and err == ""
    assert len(list(web.glob("*.md"))) == 2 and not _archive(base).exists()


def test_c3c_occupancy_is_block_rounded_disk_usage_not_content_length(tmp_path):
    kg = _kg()
    p = tmp_path / "c.md"; p.write_bytes(b"x" * 600); st = p.stat()
    if not getattr(st, "st_blocks", None):
        pytest.skip("this OS reports no st_blocks; the st_size fallback is covered below")
    assert kg._occupancy(st) == st.st_blocks * 512 and kg._occupancy(st) != st.st_size == 600
    fake = collections.namedtuple("st", "st_size st_blocks")(600, None)      # an OS without st_blocks
    assert kg._occupancy(fake) == 600


def test_c7c_a_file_raced_away_before_stat_does_not_undercount_remaining(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, ON); web = _web(base)
    [_capture(web, f"old{i}", 300) for i in range(2)]; [_capture(web, f"new{i}", 1) for i in range(3)]
    real = kg.pathlib.Path.stat

    def racy(self, *a, **k):
        if self.name == "new0.md":
            raise FileNotFoundError(self)
        return real(self, *a, **k)
    monkeypatch.setattr(kg.pathlib.Path, "stat", racy)
    res = kg.retain_web(base)
    assert res["ok"] and res["archived"] == 2 and res["raced"] == 1 and res["remaining"] == 2


def test_c12b_maintain_survives_a_capture_renamed_mid_glob(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); web = _web(base)
    [_capture(web, f"c{i}", 1) for i in range(3)]
    real = kg.pathlib.Path.stat

    def racy(self, *a, **k):
        if self.name == "c1.md":
            raise FileNotFoundError(self)
        return real(self, *a, **k)
    monkeypatch.setattr(kg.pathlib.Path, "stat", racy)
    assert kg.maintain_report(base, repo_root=str(tmp_path))["web"]["count"] == 2


# --- reviewed analysis trims: maintain proposes a runnable command, applies nothing --------------

def _analysis(tmp_path, base):
    (tmp_path / "src").mkdir(); (tmp_path / "src" / "live.py").write_text("x")
    an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
    (an / "stale.md").write_text("documents `backend/gone.py` long deleted")
    (an / "z b;c.md").write_text("identical body")                 # a name a shell would mangle unquoted
    (an / "b.md").write_text("identical body")
    (an / "ok.md").write_text("documents `src/live.py`")
    return an


def test_c10_maintain_prints_a_runnable_mv_command_per_proposal_and_applies_nothing(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); an = _analysis(tmp_path, base)
    rep = kg.maintain_report(base, repo_root=str(tmp_path))
    assert rep["stale"] and rep["dups"]                              # non-vacuity: there IS something to propose
    assert len(rep["apply"]) == 2
    for a in rep["apply"]:
        words = shlex.split(a["cmd"].replace("&&", " "))
        assert "mv" in words and "-n" in words and "git" not in words
        assert any(w.endswith("archive/analysis") or "archive/analysis/" in w for w in words)
    dup_cmd = next(a["cmd"] for a in rep["apply"] if "dup" in a["reason"])
    assert str(an / "z b;c.md") in shlex.split(dup_cmd.replace("&&", " "))   # quoted: round-trips whole
    assert str(an / "b.md") not in shlex.split(dup_cmd.replace("&&", " "))    # the first (kept) member
    assert kg.main(["kg.py", "maintain", base, str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.count("apply: ") == 2
    assert sorted(p.name for p in an.glob("*.md")) == ["b.md", "ok.md", "stale.md", "z b;c.md"]   # untouched


def test_c11_maintain_uses_git_rm_only_for_tracked_notes_when_analysis_is_a_worktree(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); an = _analysis(tmp_path, base)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q", "--separate-git-dir", str(tmp_path / "gitdir"), str(an)],
                   check=True, env=env, capture_output=True)
    assert (an / ".git").is_file()                                   # #2698's shape: a .git FILE, not a dir
    subprocess.run(["git", "-C", str(an), "add", "stale.md", "ok.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(an), "commit", "-q", "-m", "seed"], check=True, env=env, capture_output=True)
    rep = kg.maintain_report(base, repo_root=str(tmp_path))
    by_reason = {("stale" if "stale" in a["reason"] else "dup"): a["cmd"] for a in rep["apply"]}
    tracked = shlex.split(by_reason["stale"].replace("&&", " "))
    assert tracked[:3] == ["git", "-C", str(an)] and "rm" in tracked and "commit" in tracked and "stale.md" in tracked
    untracked = shlex.split(by_reason["dup"].replace("&&", " "))
    assert "mv" in untracked and "git" not in untracked            # a note git never saw is moved, not git-rm'd
    assert (an / "stale.md").exists() and (an / "b.md").exists()  # proposed, not applied


def test_c12_maintain_reports_the_web_set_against_the_derived_bound_with_the_command(tmp_path, monkeypatch, capsys):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True}); web = _web(base)
    files = [_capture(web, f"c{i}", 1) for i in range(6)]
    arch = _archive(base); arch.mkdir(parents=True); (arch / "gone.md").write_bytes(b"y" * 100)
    occ = kg._occupancy(files[0].stat())
    _big_disk(monkeypatch, kg, total=int(occ * 2 / 0.0001))         # bound = 2 files; 6 live -> over
    rep = kg.maintain_report(base, repo_root=str(tmp_path))
    assert rep["web"]["over"] is True and rep["web"]["count"] == 6 and rep["web"]["retention"] is False
    assert rep["web"]["archived_count"] == 1 and rep["web"]["archived_bytes"] > 0
    assert kg.main(["kg.py", "maintain", base, str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "retain" in out and "web_retention_enabled" in out and "archive" in out
    assert len(list(web.glob("*.md"))) == 6                         # report-only: nothing moved


def test_c11b_one_git_ls_files_per_report_however_many_proposals(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); an = _analysis(tmp_path, base)
    (an / "stale2.md").write_text("cites `nowhere/x.py` too"); (an / ".git").mkdir()
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv); return subprocess.CompletedProcess(argv, 0, stdout="stale.md\0", stderr="")
    monkeypatch.setattr(kg.subprocess, "run", fake_run)
    rep = kg.maintain_report(base, repo_root=str(tmp_path))
    assert len(rep["stale"]) == 2 and len(rep["dups"]) == 1 and len(rep["apply"]) == 3   # non-vacuity
    assert len(calls) == 1 and calls[0][:4] == ["git", "-C", str(an), "ls-files"]


def test_c11c_the_commit_is_limited_to_the_notes_it_removes(tmp_path, monkeypatch):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True}); an = _analysis(tmp_path, base); (an / ".git").mkdir()
    monkeypatch.setattr(kg.subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout="stale.md\0ok.md\0", stderr=""))
    cmd = next(a["cmd"] for a in kg.maintain_report(base, repo_root=str(tmp_path))["apply"] if "stale" in a["reason"])
    commit = cmd.split("&&")[-1]
    assert shlex.split(commit)[-2:] == ["--", "stale.md"]             # pathspec-limited: nothing else staged rides along


def test_c10b_a_note_that_is_both_stale_and_a_duplicate_gets_one_command(tmp_path, monkeypatch, capsys):
    kg = _kg(); _big_disk(monkeypatch, kg)
    base = _sdlc(tmp_path, {"enabled": True})
    an = pathlib.Path(base) / "knowledge" / "analysis"; an.mkdir(parents=True)
    (an / "a.md").write_text("cites `gone/x.py`"); (an / "b.md").write_text("cites `gone/x.py`")   # stale AND identical
    rep = kg.maintain_report(base, repo_root=str(tmp_path))
    assert len(rep["stale"]) == 2 and rep["dups"] == [["analysis/a.md", "analysis/b.md"]]      # non-vacuity
    dup = next(a for a in rep["apply"] if "duplicate" in a["reason"])
    assert dup["cmd"] == ""                                            # b.md already archived by its stale line
    assert kg.main(["kg.py", "maintain", base, str(tmp_path)]) == 0
    assert "already covered" in capsys.readouterr().out
