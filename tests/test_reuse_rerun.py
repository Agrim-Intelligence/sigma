"""#2281: `reuse.py rerun` re-runs a goal's recorded research queries at a commit and reports the hits new since
Research. Real temporary git repositories (a bare remote, a clone, a linked worktree) back the behaviour tests; an
injected `run(argv, timeout=...)` backs the ones that need to see the exact command, force a timeout or a
clock jump. Every refusal test asserts the whole output, so an extra or a missing line is red."""
import importlib.util
import os
import pathlib
import subprocess
import sys
import time
import types

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
REUSE = S / "reuse.py"
_LOCATION = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")
OLD_A = "def old_helper():\n    return 'needle'\n"
NEW_B = "def new_helper():\n    return 'needle'\n"
FETCHED = "; remote refs last fetched "


def _reuse():
    spec = importlib.util.spec_from_file_location("reuse", REUSE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if k not in _LOCATION}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _dossier_text(searched_at, queries):
    lines = ["# Research: x", "**Risk**: none", "", "## Reuse", "**Source:** grep"]
    if searched_at is not None:
        lines.append("**Searched at:** " + searched_at)
    lines.append("**Queries:**")
    lines += [q if isinstance(q, str) else "- pattern: `%s` paths: `%s`" % q for q in queries]
    return "\n".join(lines) + "\n"


def _write_dossier(w, searched_at=..., queries=None, goal="1"):
    text = _dossier_text(w.A if searched_at is ... else searched_at, [("needle", ".")] if queries is None else queries)
    (w.proj / ".sdlc" / "research").mkdir(parents=True, exist_ok=True)
    (w.proj / ".sdlc" / "research" / (goal + ".md")).write_text(text, encoding="utf-8")


def _world(tmp, record=True):
    """Remote bare repo, clone `proj` on main, commit A (old_helper.py), goal branch sdlc/1 at A, then commit B on
    main (old_helper.py gets two lines in front of its code, new_helper.py is added) pushed and fetched; the
    dossier searched at A. `proj/.sdlc` is untracked."""
    rem, proj = tmp / "remote.git", tmp / "proj"
    rem.mkdir()
    proj.mkdir()
    _git(rem, "init", "-q", "--bare", "-b", "main")
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "remote", "add", "origin", str(rem))
    (proj / "old_helper.py").write_text(OLD_A, encoding="utf-8")
    _git(proj, "add", "old_helper.py")
    _git(proj, "commit", "-qm", "A")
    a = _git(proj, "rev-parse", "HEAD")
    _git(proj, "push", "-q", "-u", "origin", "main")
    _git(proj, "branch", "sdlc/1")
    (proj / "old_helper.py").write_text("# moved\n" + OLD_A, encoding="utf-8")
    (proj / "new_helper.py").write_text(NEW_B, encoding="utf-8")
    _git(proj, "add", "old_helper.py", "new_helper.py")
    _git(proj, "commit", "-qm", "B")
    b = _git(proj, "rev-parse", "HEAD")
    _git(proj, "push", "-q", "origin", "main")
    _git(proj, "fetch", "-q", "origin")
    _git(proj, "remote", "set-head", "origin", "main")
    w = types.SimpleNamespace(tmp=tmp, proj=proj, rem=rem, A=a, B=b, sdlc=str(proj / ".sdlc"), m=_reuse())
    _write_dossier(w)
    if record:
        (proj / ".sdlc" / "state" / "work").mkdir(parents=True)
        (proj / ".sdlc" / "state" / "work" / "1.json").write_text('{"remote": "origin", "base": "main"}')
    return w


def _spy(m, on_call=None):
    """A `run(argv, timeout=...)` that records every call and delegates to the real runner, unless `on_call`
    returns something (a CompletedProcess to hand back; an exception is raised)."""
    calls = []

    def run(argv, timeout=None):
        calls.append((list(argv), timeout))
        if on_call:
            result = on_call(list(argv), timeout)
            if isinstance(result, BaseException):
                raise result
            if result is not None:
                return result
        return (m._run_bounded if "grep" in argv else m._run_git)(argv, timeout=timeout)
    return run, calls


def _proc(argv=(), rc=0, out=b"", err=b""):
    return subprocess.CompletedProcess(list(argv), rc, out, err)


def _is_grep(argv):
    return "grep" in argv


NEEDLE_NEW = "    return 'needle'"


# ----------------------------------------------------------------------------------------------- resolve_commit

@pytest.mark.parametrize("ref", ["--output=x", "-n"])
def test_a_ref_starting_with_a_dash_is_refused(tmp_path, ref, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert w.m.rerun(w.sdlc, "1", ref=ref) == ["rerun: ref %s does not resolve to a commit" % ref]
    assert w.m.main(["rerun", w.sdlc, "1", "--ref", ref]) == 0
    assert not (tmp_path / "x").exists() and not (w.proj / "x").exists()


def test_a_ref_that_does_not_resolve_is_refused(tmp_path):
    w = _world(tmp_path)
    assert w.m.rerun(w.sdlc, "1", ref="no-such-branch") == ["rerun: ref no-such-branch does not resolve to a commit"]


def test_resolve_commit_returns_a_full_commit_id_for_a_branch_and_a_tag(tmp_path):
    w = _world(tmp_path)
    _git(w.proj, "tag", "v1", w.A)
    assert w.m.resolve_commit(w.proj, "sdlc/1") == (w.A, None)
    assert w.m.resolve_commit(w.proj, "v1") == (w.A, None)
    assert w.m.resolve_commit(w.proj, "origin/main") == (w.B, None)


def test_resolve_commit_refuses_a_blob_and_a_tree(tmp_path):
    w = _world(tmp_path)
    for ref in ("origin/main:old_helper.py", "origin/main^{tree}"):
        commit, error = w.m.resolve_commit(w.proj, ref)
        assert commit is None and error == "ref %s does not resolve to a commit" % ref


@pytest.mark.parametrize("ref", ["a\nb", "a\x00b", "", None, 7])
def test_resolve_commit_refuses_control_characters_without_calling_git(tmp_path, ref):
    m = _reuse()
    run, calls = _spy(m)
    commit, error = m.resolve_commit(tmp_path, ref, run=run)
    assert commit is None and error and calls == []
    assert "\n" not in error and "\x00" not in error


def test_resolve_commit_asks_git_behind_end_of_options_and_returns_only_hex():
    m = _reuse()
    run, calls = _spy(m, lambda argv, t: _proc(argv, 0, b"not hex at all\n"))
    commit, error = m.resolve_commit("/r", "main", run=run)
    assert commit is None and error == "ref main does not resolve to a commit"
    assert calls == [(["git", "-C", "/r", "rev-parse", "--verify", "--end-of-options", "main^{commit}"], m.GIT_TIMEOUT)]


def test_resolve_commit_reports_a_git_that_cannot_run_or_answer():
    m = _reuse()
    for exc, text in ((FileNotFoundError(2, "no git"), "git could not run"),
                      (subprocess.TimeoutExpired("git", 10), "git did not answer in 10 seconds")):
        run, _ = _spy(m, lambda argv, t, exc=exc: exc)
        assert m.resolve_commit("/r", "main", run=run) == (None, text)


# -------------------------------------------------------------------------------------------------- git_grep

def test_rerun_never_uses_a_shell(tmp_path):
    """Every call, whole verb included, gets a list and a timeout and nothing else; the recorded pattern is exactly
    one item right after `-e`, never joined into another argument."""
    pattern = "a b;$(touch pwned) | x"
    w = _world(tmp_path)
    _write_dossier(w, queries=[(pattern, ". sdlc")])
    run, calls = _spy(w.m)
    w.m.rerun(w.sdlc, "1", new_since_research=True, run=run)
    greps = [argv for argv, _ in calls if _is_grep(argv)]
    assert len(greps) == 2
    assert all(isinstance(argv, list) and all(isinstance(a, str) for a in argv) for argv, _ in calls)
    for argv in greps:
        assert argv[argv.index("-e") + 1] == pattern and argv.count(pattern) == 1
        assert argv[:2] == ["git", "--literal-pathspecs"] and argv[argv.index("--") + 1:] == [".", "sdlc"]


def test_git_grep_command_is_exactly_the_issue_list():
    m = _reuse()
    run, calls = _spy(m, lambda argv, t: _proc(argv, 1))
    ref = "a" * 40
    assert m.git_grep("/r", "pat", ["p1", "p2"], ref=ref, run=run) == ("ok", [])
    assert m.git_grep("/r", "pat", ["p1"], run=run) == ("ok", [])
    assert calls[0] == (["git", "--literal-pathspecs", "-C", "/r", "grep", "-n", "-I", "-E", "-z", "-e", "pat", ref,
                         "--", "p1", "p2"], m.QUERY_TIMEOUT)
    assert calls[1][0] == ["git", "--literal-pathspecs", "-C", "/r", "grep", "-n", "-I", "-E", "-z", "-e", "pat",
                           "--", "p1"]


def test_a_file_name_with_a_colon_is_parsed_correctly(tmp_path):
    w = _world(tmp_path)
    (w.proj / "x:y.txt").write_text("one needle\n")
    _git(w.proj, "add", "x:y.txt")
    _git(w.proj, "commit", "-qm", "colon")
    commit = _git(w.proj, "rev-parse", "HEAD")
    assert w.m.git_grep(w.proj, "needle", ["x:y.txt"], ref=commit) == ("ok", [("x:y.txt", 1, "one needle")])
    assert w.m.git_grep(w.proj, "needle", ["x:y.txt"]) == ("ok", [("x:y.txt", 1, "one needle")])


def test_rerun_at_a_ref_strips_the_ref_prefix(tmp_path):
    w = _world(tmp_path)
    lines = w.m.rerun(w.sdlc, "1", ref="sdlc/1")
    assert lines == ["Q1 old_helper.py:2: " + NEEDLE_NEW,
                     "rerun: 1 hit(s) for 1 query(ies) at sdlc/1 (%s)" % w.A[:7]]


def test_a_path_is_never_treated_as_a_pattern_or_magic(tmp_path):
    w = _world(tmp_path)
    (w.proj / "*.py").write_text("a needle\n")
    (w.proj / "plain.py").write_text("a needle\n")
    _git(w.proj, "add", "*.py", "plain.py")
    _git(w.proj, "commit", "-qm", "glob")
    commit = _git(w.proj, "rev-parse", "HEAD")
    assert w.m.git_grep(w.proj, "needle", ["*.py"], ref=commit) == ("ok", [("*.py", 1, "a needle")])
    for path in (":(top)old_helper.py", "-x"):
        assert w.m.git_grep(w.proj, "needle", [path], ref=commit) == ("ok", [])


def test_a_pattern_with_shell_syntax_runs_nothing(tmp_path, monkeypatch):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("$(touch pwned)", "."), (";touch pwned", "."), ("`touch pwned`", ".")])
    monkeypatch.chdir(tmp_path)
    lines = w.m.rerun(w.sdlc, "1", ref="origin/main")
    assert lines[-1].startswith("rerun: ")
    assert [p for p in tmp_path.rglob("pwned")] == []


def test_git_grep_status_values():
    m = _reuse()
    one = b"f.py\x003\x00text\n"
    cases = [(_proc(rc=0, out=one), ("ok", [("f.py", 3, "text")])),
             (_proc(rc=1), ("ok", [])),
             (_proc(rc=128, err=b"fatal: bad thing\nsecond line\n"), ("error: fatal: bad thing", [])),
             (subprocess.TimeoutExpired("git", 30), ("timeout", [])),
             (FileNotFoundError(2, "no git"), ("error: git could not run", []))]
    for result, expected in cases:
        run, _ = _spy(m, lambda argv, t, result=result: result)
        assert m.git_grep("/r", "x", ["."], run=run) == expected


def test_git_grep_cuts_long_text_and_caps_output():
    m = _reuse()
    big = b"f.py\x001\x00" + b"a" * (1 << 20) + b"\n"
    run, _ = _spy(m, lambda argv, t: _proc(rc=0, out=big))
    assert m.git_grep("/r", "a", ["."], run=run) == ("ok", [("f.py", 1, "a" * m.MAX_LINE_CHARS + "…")])
    over = _spy(m, lambda argv, t: _proc(rc=0, out=b"x" * (m.MAX_GREP_BYTES + 1)))[0]
    status, hits = m.git_grep("/r", "a", ["."], run=over)
    assert status.startswith("error: output over") and not hits


def test_git_grep_skips_binary_files(tmp_path):
    w = _world(tmp_path)
    (w.proj / "blob.bin").write_bytes(b"needle\x00binary\n")
    _git(w.proj, "add", "blob.bin")
    _git(w.proj, "commit", "-qm", "bin")
    commit = _git(w.proj, "rev-parse", "HEAD")
    assert [name for name, _, _ in w.m.git_grep(w.proj, "needle", ["."], ref=commit)[1]] == [
        "new_helper.py", "old_helper.py"]


def test_git_grep_refuses_an_unresolved_ref():
    m = _reuse()
    run, calls = _spy(m)
    for ref in ("main", "--output=x", "A" * 40):
        with pytest.raises(ValueError):
            m.git_grep("/r", "x", ["."], ref=ref, run=run)
    assert calls == []


def test_the_real_runner_keeps_at_most_the_cap_and_kills_a_flood(monkeypatch):
    m = _reuse()
    started = time.monotonic()
    done = m._run_bounded([sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x'*65536)"],
                          timeout=20, max_bytes=1000)
    assert len(done.stdout) == 1001 and time.monotonic() - started < 15


def test_the_real_runner_kills_a_command_that_runs_over_its_timeout():
    m = _reuse()
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        m._run_bounded([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.5)
    assert time.monotonic() - started < 10


def test_a_flood_of_hits_from_real_git_is_an_error_not_unbounded_memory(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_GREP_BYTES", 100)
    status, hits = w.m.git_grep(w.proj, "def|return", ["."], ref=w.B)
    assert status.startswith("error: output over") and not hits


# ------------------------------------------------------------------------------- queries, base_ref, fetch_age

def test_a_legacy_goal_has_no_queries(tmp_path, capsys):
    w = _world(tmp_path)
    (w.proj / ".sdlc" / "research" / "1.md").write_text("# Research\n\n## Goal\nold\n")
    assert w.m.rerun(w.sdlc, "1") == ["rerun: no recorded queries for goal 1"]
    assert w.m.main(["rerun", w.sdlc, "1"]) == 0
    assert capsys.readouterr().out == "rerun: no recorded queries for goal 1\n"
    assert w.m.main(["rerun", w.sdlc, "99"]) == 0
    assert capsys.readouterr().out == "rerun: no recorded queries for goal 99\n"
    assert w.m.queries_for(w.sdlc, "99") == []


def test_queries_for_returns_the_parsed_queries(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("a|b", "x y"), "- not a query"])
    assert w.m.queries_for(w.sdlc, "1") == [("a|b", ["x", "y"])]


def test_rerun_without_a_work_record_names_the_base_it_used(tmp_path):
    w = _world(tmp_path, record=False)
    lines = w.m.rerun(w.sdlc, "1")
    assert lines[0] == "rerun: no work record for goal 1; using origin/main"
    assert lines[-1] == "rerun: 2 hit(s) for 1 query(ies) at origin/main (%s)" % w.B[:7]


def test_base_ref_uses_the_work_record_remote_and_base(tmp_path):
    w = _world(tmp_path)
    (w.proj / ".sdlc" / "state" / "work" / "1.json").write_text('{"remote": "up", "base": "develop"}')
    assert w.m.base_ref(w.sdlc, "1") == ("up/develop", None)


def test_base_ref_with_no_record_and_no_default_branch_is_not_found(tmp_path):
    """No `refs/remotes/origin/HEAD`, no config: HEAD (a real branch here) is never the answer."""
    w = _world(tmp_path, record=False)
    _git(w.proj, "remote", "set-head", "origin", "-d")
    ref, note = w.m.base_ref(w.sdlc, "1")
    assert ref == "origin/" and note == "rerun: no work record for goal 1; using origin/"
    assert w.m.rerun(w.sdlc, "1") == ["rerun: base ref origin not found; fetch it and run again"]


def test_a_missing_base_ref_is_reported_in_one_line(tmp_path, capsys):
    w = _world(tmp_path)
    _git(w.proj, "remote", "remove", "origin")
    assert w.m.rerun(w.sdlc, "1") == ["rerun: base ref origin/main not found; fetch it and run again"]
    assert w.m.main(["rerun", w.sdlc, "1", "--new-since-research"]) == 0
    assert capsys.readouterr().out == "rerun: base ref origin/main not found; fetch it and run again\n"


def test_base_ref_reads_the_configured_work_base(tmp_path):
    w = _world(tmp_path, record=False)
    for configured in ("develop", "origin/develop"):
        (w.proj / ".sdlc" / "config.json").write_text('{"work": {"base": "%s"}}' % configured)
        assert w.m.base_ref(w.sdlc, "1") == ("origin/develop", "rerun: no work record for goal 1; using origin/develop")


def test_base_ref_with_an_option_like_config_value_is_not_found(tmp_path):
    w = _world(tmp_path, record=False)
    (w.proj / ".sdlc" / "config.json").write_text('{"work": {"base": "--output=x"}}')
    assert w.m.rerun(w.sdlc, "1")[0].startswith("rerun: base ref ") and len(w.m.rerun(w.sdlc, "1")) == 1


def test_base_ref_never_asks_git_about_head(tmp_path):
    w = _world(tmp_path, record=False)
    run, calls = _spy(w.m)
    w.m.base_ref(w.sdlc, "1", run=run)
    flat = [arg for argv, _ in calls for arg in argv]
    assert calls and "HEAD" not in flat and "origin/main" not in flat


def test_the_base_option_is_used_as_given_or_as_a_remote_branch(tmp_path):
    w = _world(tmp_path, record=False)
    _git(w.proj, "branch", "develop", w.A)
    _git(w.proj, "push", "-q", "origin", "develop")
    _git(w.proj, "branch", "-D", "develop")
    _git(w.proj, "fetch", "-q", "origin")
    given = w.m.rerun(w.sdlc, "1", base="origin/main")
    assert given[0] == "Q1 new_helper.py:2: " + NEEDLE_NEW         # no "no work record" note when --base is given
    assert given[-1].endswith("at origin/main (%s)" % w.B[:7])
    dev = w.m.rerun(w.sdlc, "1", base="develop")
    assert dev[-1] == "rerun: 1 hit(s) for 1 query(ies) at origin/develop (%s)" % w.A[:7]
    assert w.m.rerun(w.sdlc, "1", base="nowhere") == ["rerun: base ref origin/nowhere not found; fetch it and run again"]


def test_a_base_that_exists_only_as_given_is_used_as_given(tmp_path):
    w = _world(tmp_path, record=False)
    lines = w.m.rerun(w.sdlc, "1", base="sdlc/1")              # a local branch: origin/sdlc/1 does not exist
    assert lines == ["Q1 old_helper.py:2: " + NEEDLE_NEW, "rerun: 1 hit(s) for 1 query(ies) at sdlc/1 (%s)" % w.A[:7]]


def test_a_ref_given_skips_the_base_and_its_note(tmp_path):
    w = _world(tmp_path, record=False)
    run, calls = _spy(w.m)
    lines = w.m.rerun(w.sdlc, "1", ref="sdlc/1", run=run)
    assert not any("no work record" in line for line in lines)
    assert not any("symbolic-ref" in argv for argv, _ in calls)


def test_age_formatting():
    m = _reuse()
    got = [m._age(s) for s in (-5, 0, 59, 60, 119, 3599, 3600, 86399, 86400, 3 * 86400 + 5)]
    assert got == ["0m", "0m", "0m", "1m", "1m", "59m", "1h", "23h", "1d", "3d"]


def test_fetch_age_reads_the_file_and_is_none_when_never_fetched(tmp_path):
    w = _world(tmp_path)
    fetch_head = w.proj / ".git" / "FETCH_HEAD"
    now = time.time()
    os.utime(fetch_head, (now - 7300, now - 7300))
    assert 7290 <= w.m.fetch_age(w.proj, now=lambda: now) <= 7310
    fetch_head.unlink()
    assert w.m.fetch_age(w.proj) is None
    assert w.m.fetch_age(tmp_path / "not-a-repo") is None


def test_fetch_age_in_a_linked_worktree_reads_both_places_git_fetch_writes(tmp_path):
    """MEASURED (git 2.56): a fetch run in the main checkout writes <common>/FETCH_HEAD, one run in a linked
    worktree writes <common>/worktrees/<name>/FETCH_HEAD; remote refs are shared, so the newer of the two is the age."""
    w = _world(tmp_path)
    linked = tmp_path / "linked"
    _git(w.proj, "worktree", "add", "-q", str(linked), "sdlc/1")
    now = time.time()
    common = w.proj / ".git" / "FETCH_HEAD"
    own = w.proj / ".git" / "worktrees" / "linked" / "FETCH_HEAD"
    os.utime(common, (now - 600, now - 600))
    assert 590 <= w.m.fetch_age(linked, now=lambda: now) <= 610                 # only the shared copy exists
    own.write_text("x")
    os.utime(own, (now - 60, now - 60))
    assert 50 <= w.m.fetch_age(linked, now=lambda: now) <= 70                   # the worktree's own copy is newer
    os.utime(own, (now - 6000, now - 6000))
    assert 590 <= w.m.fetch_age(linked, now=lambda: now) <= 610                 # the shared copy is newer


# -------------------------------------------------------------------------------------------- rerun, plain

def test_rerun_prints_hits_per_query(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("needle", "."), ("def ", "."), ("nothing matches this", ".")])
    assert w.m.rerun(w.sdlc, "1") == [
        "Q1 new_helper.py:2: " + NEEDLE_NEW, "Q1 old_helper.py:3: " + NEEDLE_NEW,
        "Q2 new_helper.py:1: def new_helper():", "Q2 old_helper.py:2: def old_helper():",
        "rerun: 4 hit(s) for 3 query(ies) at origin/main (%s)" % w.B[:7]]


def test_rerun_with_no_ref_searches_the_base_tip_not_head(tmp_path):
    w = _world(tmp_path)
    expected = w.m.rerun(w.sdlc, "1")
    _git(w.proj, "checkout", "-q", "-b", "other", w.A)
    (w.proj / "other.py").write_text("needle in other\n")
    _git(w.proj, "add", "other.py")
    _git(w.proj, "commit", "-qm", "other")
    run, calls = _spy(w.m)
    assert w.m.rerun(w.sdlc, "1", run=run) == expected
    assert "HEAD" not in [arg for argv, _ in calls for arg in argv]
    assert not any(argv[3:5] == ["rev-parse", "HEAD"] for argv, _ in calls)


def test_a_timed_out_query_is_reported_and_the_run_continues(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("needle", "."), ("def ", ".")])
    seen = []

    def first_grep_times_out(argv, timeout):
        if _is_grep(argv):
            seen.append(timeout)
            if len(seen) == 1:
                return subprocess.TimeoutExpired(argv, timeout)

    run, _ = _spy(w.m, first_grep_times_out)
    assert w.m.rerun(w.sdlc, "1", run=run) == [
        "TIMEOUT Q1", "Q2 new_helper.py:1: def new_helper():", "Q2 old_helper.py:2: def old_helper():",
        "rerun: 2 hit(s) for 2 query(ies) at origin/main (%s)" % w.B[:7]]
    assert seen[0] == w.m.QUERY_TIMEOUT == 30


def test_a_failing_query_is_reported_and_the_run_continues(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("(", "."), ("def ", ".")])
    lines = w.m.rerun(w.sdlc, "1")
    assert lines[0].startswith("ERROR Q1: ") and len(lines[0]) > len("ERROR Q1: ")
    assert lines[1:] == ["Q2 new_helper.py:1: def new_helper():", "Q2 old_helper.py:2: def old_helper():",
                         "rerun: 2 hit(s) for 2 query(ies) at origin/main (%s)" % w.B[:7]]


def test_rerun_stops_after_the_total_time_limit_and_says_so(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("needle", "."), ("def ", "."), ("return", ".")])
    now = [0.0]

    def clock():
        return now[0]

    def jump(argv, timeout):
        if _is_grep(argv):
            now[0] = 121.0

    run, _ = _spy(w.m, jump)
    assert w.m.rerun(w.sdlc, "1", run=run, clock=clock) == [
        "Q1 new_helper.py:2: " + NEEDLE_NEW, "Q1 old_helper.py:3: " + NEEDLE_NEW,
        "rerun: stopped after 120 seconds; 1 of 3 queries run"]


def test_the_remaining_time_bounds_a_running_query(tmp_path):
    w = _world(tmp_path)
    now = [0.0]

    def late(argv, timeout):
        if "rev-parse" in argv:
            now[0] = 100.0

    run, calls = _spy(w.m, late)
    w.m.rerun(w.sdlc, "1", run=run, clock=lambda: now[0])
    assert [t for argv, t in calls if _is_grep(argv)] == [20.0]


def test_rerun_skips_an_unsafe_recorded_query(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("bad\x01", "."), ("", "."), ("x" * 201, "."), ("needle", "../x"),
                               ("needle", "/etc"), ("needle", ""), ("needle", ". :(top)x"), ("needle", ".")])
    lines = w.m.rerun(w.sdlc, "1")
    assert lines[:7] == [
        "ERROR Q1: pattern contains a control character", "ERROR Q2: pattern is empty",
        "ERROR Q3: pattern is 201 characters; 1 to 200 allowed", "ERROR Q4: path '../x' has a '..' segment",
        "ERROR Q5: path '/etc' starts with '/'", "ERROR Q6: path '' is empty", "ERROR Q7: path ':(top)x' starts with ':'"]
    assert lines[7:] == ["Q8 new_helper.py:2: " + NEEDLE_NEW, "Q8 old_helper.py:3: " + NEEDLE_NEW,
                         "rerun: 2 hit(s) for 8 query(ies) at origin/main (%s)" % w.B[:7]]


def test_a_malformed_query_line_keeps_its_position_and_is_reported(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=["- pattern: `needle`", ("needle", "."), "- just words"])
    assert w.m.rerun(w.sdlc, "1") == [
        "ERROR Q1: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`",
        "Q2 new_helper.py:2: " + NEEDLE_NEW, "Q2 old_helper.py:3: " + NEEDLE_NEW,
        "ERROR Q3: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`",
        "rerun: 2 hit(s) for 3 query(ies) at origin/main (%s)" % w.B[:7]]


def test_only_the_first_25_queries_run(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("q%d" % n, ".") for n in range(27)])
    run, calls = _spy(w.m)
    lines = w.m.rerun(w.sdlc, "1", run=run)
    assert lines[0] == "rerun: only the first 25 of 27 queries run"
    assert len([1 for argv, _ in calls if _is_grep(argv)]) == 25
    assert lines[-1].startswith("rerun: 0 hit(s) for 25 query(ies)")


def test_rerun_caps_hits_per_query_and_total_output(tmp_path, monkeypatch):
    w = _world(tmp_path)
    (w.proj / "big.txt").write_text("needle\n" * 250)
    _git(w.proj, "add", "big.txt")
    _git(w.proj, "commit", "-qm", "big")
    lines = w.m.rerun(w.sdlc, "1", ref="HEAD")
    assert len([l for l in lines if l.startswith("Q1 big.txt:")]) == 200
    assert "rerun: Q1 truncated at 200 hits" in lines
    monkeypatch.setattr(w.m, "MAX_OUTPUT_LINES", 5)
    _write_dossier(w, queries=[("needle", "."), ("def ", "."), ("helper", ".")])
    capped = w.m.rerun(w.sdlc, "1", ref="origin/main")
    assert len(capped) == 7 and capped[5] == "rerun: output stopped at 5 lines"
    assert capped[6].startswith("rerun: ")


def test_control_characters_in_a_hit_are_not_printed(tmp_path):
    w = _world(tmp_path)
    (w.proj / "esc.txt").write_text("needle \x1b[31mred\x9b\n")
    _git(w.proj, "add", "esc.txt")
    _git(w.proj, "commit", "-qm", "esc")
    line = [l for l in w.m.rerun(w.sdlc, "1", ref="HEAD") if l.startswith("Q1 esc.txt")][0]
    assert line == "Q1 esc.txt:1: needle ?[31mred?"


def _tree(root):
    out = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or path.is_dir():
            continue
        st = path.stat()
        out[str(path.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return out


def test_rerun_writes_nothing(tmp_path):
    w = _world(tmp_path)
    before = (_tree(w.proj), _git(w.proj, "status", "--porcelain"), _git(w.proj, "for-each-ref"),
              (w.proj / ".git" / "FETCH_HEAD").stat().st_mtime_ns)
    w.m.rerun(w.sdlc, "1")
    w.m.rerun(w.sdlc, "1", new_since_research=True)
    w.m.rerun(w.sdlc, "1", ref="sdlc/1")
    after = (_tree(w.proj), _git(w.proj, "status", "--porcelain"), _git(w.proj, "for-each-ref"),
             (w.proj / ".git" / "FETCH_HEAD").stat().st_mtime_ns)
    assert before == after


# ---------------------------------------------------------------------------------------- new since research

NEW_LINE = "NEW Q1 new_helper.py:2: " + NEEDLE_NEW


def _summary(w, n, extra=""):
    return "rerun: %d hit(s) new since Research (%s) on origin/main (%s)%s" % (n, w.A[:7], w.B[:7], extra)


def test_new_since_research_shows_only_code_merged_since_research(tmp_path):
    """new_helper.py carries the same TEXT as old_helper.py had at A, so comparing by text alone loses it."""
    w = _world(tmp_path)
    lines = w.m.rerun(w.sdlc, "1", new_since_research=True)
    assert lines[0] == NEW_LINE and len(lines) == 2
    assert lines[1].startswith(_summary(w, 1, FETCHED))


def test_new_since_research_names_both_commits(tmp_path):
    w = _world(tmp_path)
    assert w.m.rerun(w.sdlc, "1", new_since_research=True)[-1].startswith(
        "rerun: 1 hit(s) new since Research (%s) on origin/main (%s)" % (w.A[:7], w.B[:7]))


def test_new_since_research_ignores_code_that_only_moved_lines(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, queries=[("old_helper|return 'needle'", "old_helper.py")])
    lines = w.m.rerun(w.sdlc, "1", new_since_research=True)
    assert len(lines) == 1 and lines[0].startswith(_summary(w, 0, FETCHED))


def test_new_since_research_still_reports_after_the_branch_is_rebased(tmp_path):
    w = _world(tmp_path)
    expected = w.m.rerun(w.sdlc, "1", new_since_research=True)[:1]
    _git(w.proj, "checkout", "-q", "sdlc/1")
    (w.proj / "goal.txt").write_text("work\n")
    _git(w.proj, "add", "goal.txt")
    _git(w.proj, "commit", "-qm", "goal work")
    _git(w.proj, "rebase", "-q", "origin/main")
    assert _git(w.proj, "merge-base", "HEAD", "origin/main") == w.B
    assert w.m.rerun(w.sdlc, "1", new_since_research=True)[:1] == expected == [NEW_LINE]


def test_new_since_research_does_not_depend_on_head(tmp_path):
    w = _world(tmp_path)
    expected = w.m.rerun(w.sdlc, "1", new_since_research=True)
    _git(w.proj, "checkout", "-q", "-b", "other", w.A)
    (w.proj / "other.py").write_text("needle only here\n")
    _git(w.proj, "add", "other.py")
    _git(w.proj, "commit", "-qm", "other")
    assert w.m.rerun(w.sdlc, "1", new_since_research=True)[:1] == expected[:1]


def test_new_since_research_never_calls_merge_base_or_fetch(tmp_path):
    w = _world(tmp_path)
    run, calls = _spy(w.m)
    w.m.rerun(w.sdlc, "1", new_since_research=True, run=run)
    flat = {arg for argv, _ in calls for arg in argv}
    assert not flat & {"merge-base", "fetch", "pull", "HEAD", "push", "checkout"}
    assert len([1 for argv, _ in calls if _is_grep(argv)]) == 2


def test_new_since_research_with_no_record_prints_the_note_first(tmp_path):
    w = _world(tmp_path, record=False)
    lines = w.m.rerun(w.sdlc, "1", new_since_research=True)
    assert lines[0] == "rerun: no work record for goal 1; using origin/main" and lines[1] == NEW_LINE


def test_a_searched_at_commit_missing_here_is_reported_in_one_line(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, searched_at="a" * 40)
    assert w.m.rerun(w.sdlc, "1", new_since_research=True) == [
        "rerun: searched-at commit aaaaaaa is not on this machine; fetch the goal branch and run again"]


def test_a_dossier_without_a_searched_at_line_is_reported_in_one_line(tmp_path):
    w = _world(tmp_path)
    _write_dossier(w, searched_at=None)
    assert w.m.rerun(w.sdlc, "1", new_since_research=True) == ["rerun: the dossier records no searched-at commit"]


@pytest.mark.parametrize("value", ["zzz", "--output=x", "ABCDEF" * 7, "a" * 39, ""])
def test_a_non_hex_searched_at_line_never_reaches_git(tmp_path, value):
    w = _world(tmp_path)
    _write_dossier(w, searched_at=value)
    run, calls = _spy(w.m)
    assert w.m.rerun(w.sdlc, "1", new_since_research=True, run=run) == [
        "rerun: the dossier records no searched-at commit"]
    assert calls == []


def test_new_since_research_reports_never_fetched_when_fetch_head_is_missing(tmp_path):
    w = _world(tmp_path)
    (w.proj / ".git" / "FETCH_HEAD").unlink()
    assert w.m.rerun(w.sdlc, "1", new_since_research=True)[-1] == _summary(w, 1, "; remote refs never fetched")


def test_new_since_research_reports_how_old_the_fetch_is(tmp_path):
    w = _world(tmp_path)
    now = time.time()
    os.utime(w.proj / ".git" / "FETCH_HEAD", (now - 7300, now - 7300))
    assert w.m.rerun(w.sdlc, "1", new_since_research=True)[-1] == _summary(w, 1, FETCHED + "2h")


@pytest.mark.parametrize("queries, searched_at, base_gone, expected", [
    ([], "A", True, "rerun: no recorded queries for goal 1"),
    ([("needle", ".")], None, True, "rerun: the dossier records no searched-at commit"),
    ([("needle", ".")], "b" * 40, True, "rerun: base ref origin/main not found; fetch it and run again"),
    ([("needle", ".")], "b" * 40, False,
     "rerun: searched-at commit bbbbbbb is not on this machine; fetch the goal branch and run again"),
], ids=["no-queries", "no-searched-at", "base-first", "searched-at-missing-last"])
def test_one_line_refusals_come_in_a_fixed_order(tmp_path, queries, searched_at, base_gone, expected):
    w = _world(tmp_path)
    if base_gone:
        _git(w.proj, "remote", "remove", "origin")
    _write_dossier(w, searched_at=w.A if searched_at == "A" else searched_at, queries=queries)
    assert w.m.rerun(w.sdlc, "1", new_since_research=True) == [expected]


# ------------------------------------------------------------------------------------------------ command line

def test_ref_and_new_since_research_together_are_a_usage_error(tmp_path, capsys):
    w = _world(tmp_path)
    assert w.m.main(["rerun", w.sdlc, "1", "--ref", "sdlc/1", "--new-since-research"]) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("usage:") and "rerun" in out.err


@pytest.mark.parametrize("case", ["legacy", "no-searched-at", "bad-ref", "no-base"])
def test_main_rerun_exits_zero_for_every_informational_case(tmp_path, capsys, case):
    w = _world(tmp_path)
    args = ["rerun", w.sdlc, "1"]
    if case == "legacy":
        (w.proj / ".sdlc" / "research" / "1.md").write_text("# old\n")
    elif case == "no-searched-at":
        _write_dossier(w, searched_at=None)
        args.append("--new-since-research")
    elif case == "bad-ref":
        args += ["--ref", "--output=x"]
    else:
        _git(w.proj, "remote", "remove", "origin")
    assert w.m.main(args) == 0
    out = capsys.readouterr()
    assert out.err == "" and len(out.out.splitlines()) == 1 and out.out.startswith("rerun: ")


def test_main_prints_hits_and_exits_zero(tmp_path, capsys):
    w = _world(tmp_path)
    assert w.m.main(["rerun", w.sdlc, "1", "--new-since-research"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == NEW_LINE
    assert w.m.main(["rerun", w.sdlc, "1", "--base", "origin/main"]) == 0
    assert "at origin/main" in capsys.readouterr().out


@pytest.mark.parametrize("args", [
    ["--ref"], ["--base"], ["--bogus"], ["--ref", "a", "--ref", "b"], ["--base", "a", "--base", "b"],
    ["--new-since-research", "--new-since-research"], ["extra"], [],
], ids=["ref-last", "base-last", "unknown", "ref-twice", "base-twice", "flag-twice", "extra-positional", "no-goal"])
def test_main_rerun_rejects_unknown_options_and_missing_values(tmp_path, capsys, args):
    m = _reuse()
    argv = ["rerun", str(tmp_path / ".sdlc")] + (["1"] if args != [] else []) + args
    assert m.main(argv) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err.startswith("usage:")


def test_main_check_research_still_rejects_extra_arguments(capsys):
    m = _reuse()
    for argv in (["check-research", "a", "b", "c"], ["check-research", "a"], ["bogus", "a", "b"], []):
        assert m.main(argv) == 2
        assert capsys.readouterr().err.startswith("usage:")


def test_help_documents_rerun(capsys):
    m = _reuse()
    assert m.main(["--help"]) == 0
    text = capsys.readouterr().out
    for needle in ("rerun", "--new-since-research", "120 seconds", "never fetches", "30 seconds", "judgement"):
        assert needle in text
    assert "rerun" in m.USAGE and "usage" in m.USAGE


def test_the_issue_names_are_all_collected():
    names = {n for n in globals() if n.startswith("test_")}
    issue = """test_rerun_prints_hits_per_query test_rerun_at_a_ref_strips_the_ref_prefix
    test_rerun_with_no_ref_searches_the_base_tip_not_head test_a_searched_at_commit_missing_here_is_reported_in_one_line
    test_new_since_research_shows_only_code_merged_since_research
    test_new_since_research_still_reports_after_the_branch_is_rebased test_new_since_research_does_not_depend_on_head
    test_new_since_research_ignores_code_that_only_moved_lines test_new_since_research_names_both_commits
    test_a_ref_starting_with_a_dash_is_refused test_a_ref_that_does_not_resolve_is_refused
    test_a_missing_base_ref_is_reported_in_one_line test_a_legacy_goal_has_no_queries
    test_rerun_stops_after_the_total_time_limit_and_says_so test_a_file_name_with_a_colon_is_parsed_correctly
    test_a_path_is_never_treated_as_a_pattern_or_magic test_rerun_without_a_work_record_names_the_base_it_used
    test_a_timed_out_query_is_reported_and_the_run_continues test_a_failing_query_is_reported_and_the_run_continues
    test_rerun_never_uses_a_shell test_a_pattern_with_shell_syntax_runs_nothing
    test_ref_and_new_since_research_together_are_a_usage_error test_rerun_writes_nothing""".split()
    assert len(issue) == 23 and not set(issue) - names
