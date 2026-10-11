"""#375: the opt-in post guard. With SIGMA_LEAK_PATTERNS exported, text bound for GitHub (or a commit)
that matches a pattern is refused before any process is spawned. The slug below is synthetic and built
at run time so this file never holds a real name."""
import importlib.util
import os
import pathlib
import re
import stat
import subprocess
import sys
import textwrap
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"
SLUG = "acme" + "-ghost-" + "repo"          # synthetic, built at run time
LEAK = "see https://github.com/" + SLUG + "/pull/9 for context"


def _load(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.fixture
def pg():
    return _load("post_guard")


@pytest.fixture
def armed(tmp_path, monkeypatch):
    pat = tmp_path / "patterns.txt"
    pat.write_text("# synthetic\n" + re.escape(SLUG) + "\n", encoding="utf-8")
    monkeypatch.setenv("SIGMA_LEAK_PATTERNS", str(pat))
    return pat


WRITES = [
    ["issue", "comment", "5", "--repo", "o/r", "--body", LEAK],
    ["gh", "issue", "comment", "5", "-b", LEAK],
    ["issue", "create", "--title", LEAK, "--body", "ok"],
    ["issue", "edit", "5", "--body=" + LEAK],
    ["pr", "create", "--title", "t", "--body", LEAK],
    ["gh", "pr", "comment", "5", "--body", LEAK],
    ["pr", "close", "5", "--comment", LEAK],
    ["git", "commit", "-m", LEAK],
    ["gh", "api", "-X", "PUT", "repos/o/r/contents/x", "-f", "message=" + LEAK],
    ["api", "-X", "POST", "repos/o/r/issues", "-f", "title=ok", "-f", "body=" + LEAK],
    ["api", "repos/o/r/issues/5/comments", "-f", "body=" + LEAK],        # fields, no method: gh POSTs
    ["api", "graphql", "-f", "query=mutation { addComment(input:{body:\"" + LEAK + "\"}) { id } }"],
    ["git", "commit", "-am", LEAK],
    ["git", "commit", "-m" + LEAK],
    ["git", "-C", "/tmp/x", "commit", "--amend", "-m", LEAK],
    ["gh", "pr", "create", "-b" + LEAK],
    ["gh", "pr", "create", "-b=" + LEAK],
    ["gh", "-R", "o/r", "issue", "comment", "5", "--body", LEAK],
    ["gh", "api", "repos/o/r/issues", "-fbody=" + LEAK],
    ["gh", "api", "-XPOST", "repos/o/r/issues", "-fbody=" + LEAK],
    ["gh", "api", "-XPOST", "-fbody=" + LEAK, "repos/o/r/issues"],
    ["gh", "release", "create", "v1", "--notes", LEAK],
    ["gh", "release", "create", "v1", "-n", LEAK],
    ["gh", "release", "create", "v1", "Title " + LEAK],
    ["gh", "pr", "reopen", "5", "--comment", LEAK],
    ["gh", "pr", "ready", "5", "-b", LEAK],
    ["gh", "issue", "lock", "5", "--comment", LEAK],
    ["gh", "issue", "reopen", "5", "--comment", LEAK],
    ["gh", "issue", "unlock", "5", "--comment", LEAK],
    ["git", "tag", "-a", "v1", "-m", LEAK],
    ["git", "merge", "-m", LEAK, "topic"],
    ["git", "notes", "add", "-m", LEAK],
    ["git", "commit", "-s", "-m", LEAK],
    ["git", "commit", "-m", "-" + LEAK],
    ["/opt/homebrew/bin/gh", "issue", "comment", "5", "--body", LEAK],
    ["/usr/bin/git", "commit", "-m", LEAK],
]


@pytest.mark.parametrize("argv", WRITES)
def test_write_with_a_hit_is_refused_without_echoing_it(pg, armed, argv):
    with pytest.raises(pg.PostRefused) as ei:
        pg.check(argv)
    msg = str(ei.value)
    assert "pattern 2" in msg and SLUG not in msg and "github.com" not in msg and "#" not in msg


@pytest.mark.parametrize("argv", WRITES)
def test_unset_variable_changes_nothing(pg, monkeypatch, argv):
    monkeypatch.delenv("SIGMA_LEAK_PATTERNS", raising=False)
    pg.check(argv)


def test_clean_text_and_read_verbs_pass(pg, armed):
    pg.check(["issue", "comment", "5", "--body", "all good, see #12"])
    pg.check(["issue", "list", "--search", SLUG])            # a read is never inspected
    pg.check(["pr", "view", "5", "--json", "title"])
    pg.check(["api", "repos/o/r/issues", "--method", "GET", "-f", "state=" + LEAK])   # GET: query params
    pg.check(["api", "graphql", "-f", "query={ repository { issue(number: 5) { title } } }"])
    pg.check(["api", "repos/o/r/issues/5"])


def test_reads_survive_a_broken_patterns_file(pg, tmp_path, monkeypatch):
    monkeypatch.setenv("SIGMA_LEAK_PATTERNS", str(tmp_path / "missing.txt"))
    pg.check(["issue", "view", "5"])                          # lazy: only a write loads patterns
    pg.check(["api", "graphql", "-f", "query={ viewer { login } }"])   # a graphql read, same
    with pytest.raises(pg.PostRefused) as ei:
        pg.check(["issue", "comment", "5", "--body", "fine"])
    assert "patterns" in str(ei.value)


def test_patterns_file_inside_a_work_tree_refuses(pg, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path / "w")], check=True)
    f = tmp_path / "w" / "p.txt"
    f.write_text("zzz\n")
    monkeypatch.setenv("SIGMA_LEAK_PATTERNS", str(f))
    with pytest.raises(pg.PostRefused):
        pg.check(["issue", "comment", "5", "--body", "fine"])


def test_body_file_is_read_and_stdin_is_refused(pg, armed, tmp_path):
    f = tmp_path / "b.md"
    f.write_text(LEAK)
    with pytest.raises(pg.PostRefused):
        pg.check(["pr", "create", "--body-file", str(f)])
    f.write_text("clean")
    pg.check(["pr", "create", "--body-file", str(f)])
    with pytest.raises(pg.PostRefused) as ei:
        pg.check(["pr", "create", "--body-file", "-"])
    assert "stdin" in str(ei.value)


def test_message_never_names_text_with_many_patterns(pg, tmp_path, monkeypatch):
    f = tmp_path / "p.txt"
    f.write_text("nomatch-one\n" + re.escape(SLUG) + "\n")
    monkeypatch.setenv("SIGMA_LEAK_PATTERNS", str(f))
    with pytest.raises(pg.PostRefused) as ei:
        pg.check(["issue", "comment", "1", "--body", "x\n" + LEAK])
    assert "pattern 2" in str(ei.value) and "--body" in str(ei.value)


def test_joined_body_file_and_input_files(pg, armed, tmp_path):
    f = tmp_path / "b.md"
    f.write_text(LEAK)
    with pytest.raises(pg.PostRefused):
        pg.check(["gh", "pr", "create", "-F" + str(f)])
    q = tmp_path / "q.json"
    q.write_text('{"query": "mutation { addComment(input:{body:\\"' + LEAK + '\\"}) { id } }"}')
    with pytest.raises(pg.PostRefused):
        pg.check(["gh", "api", "graphql", "--input", str(q)])
    q.write_text('{"query": "{ viewer { login } }"}')
    pg.check(["gh", "api", "graphql", "--input", str(q)])          # a query is a read
    with pytest.raises(pg.PostRefused) as ei:
        pg.check(["gh", "api", "graphql", "--input", "-"])
    assert "stdin" in str(ei.value)
    with pytest.raises(pg.PostRefused):
        pg.check(["gh", "api", "repos/o/r/issues", "--input", "-"])


ZW = "\u200b"
OBFUSCATED = [
    LEAK.replace("ghost", "gh" + ZW + "ost"),                       # zero-width space inside the slug
    LEAK.replace("ghost", "gh\u00adost"),                           # soft hyphen
    LEAK.replace("acme", "\uff41\uff43\uff4d\uff45"),            # fullwidth letters
]


@pytest.mark.parametrize("text", OBFUSCATED)
def test_zero_width_and_fullwidth_forms_are_caught(pg, armed, text):
    assert text != LEAK
    with pytest.raises(pg.PostRefused):
        pg.check(["issue", "comment", "5", "--body", text])


def test_homoglyphs_are_documented_as_not_caught(pg, armed):
    cyr = LEAK.replace("a", "\u0430", 1)            # Cyrillic a: NFKC does not fold it
    pg.check(["issue", "comment", "5", "--body", cyr])
    assert "homoglyph" in (ROOT / "docs" / "publish-runbook.md").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_variable_counts_as_unset(pg, monkeypatch, value):
    monkeypatch.setenv("SIGMA_LEAK_PATTERNS", value)
    pg.check(["issue", "comment", "5", "--body", LEAK])
    assert "blank" in (ROOT / "docs" / "publish-runbook.md").read_text(encoding="utf-8").lower()


def test_leak_refs_ships_where_the_guard_loads_it(pg, armed, tmp_path):
    """Packaging (#375 review 8): the plugin source is `./`, so the tracked tree ships, and the public
    export carries tools/ (docs/public-snapshot.md). Prove it two ways: the file is tracked and no
    default export exclusion drops it, and a bare copy of ONLY the two files works as an install."""
    tracked = subprocess.run(["git", "ls-files", "tools/leak_refs.py", "skills/sigma-loop/scripts/post_guard.py"],
                             cwd=str(ROOT), capture_output=True, text=True).stdout.split()
    assert sorted(tracked) == ["skills/sigma-loop/scripts/post_guard.py", "tools/leak_refs.py"]
    btp = _load_tool("build_public_tree")
    assert "tools/leak_refs.py" in btp.TOOL_PATHS and btp.SDLC_DEFAULT == "exclude"   # only .sdlc/ is dropped
    inst = tmp_path / "plugin"
    (inst / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    (inst / "tools").mkdir()
    for rel in ("skills/sigma-loop/scripts/post_guard.py", "tools/leak_refs.py"):
        (inst / rel).write_bytes((ROOT / rel).read_bytes())
    code = ("import sys; sys.path.insert(0, %r); import post_guard as g\n"
            "try:\n g.check(['issue','comment','1','--body',%r])\nexcept g.PostRefused as e: print(e)\n"
            % (str(inst / "skills" / "sigma-loop" / "scripts"), LEAK))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=dict(os.environ))
    assert "[leak]" in r.stdout, r.stderr
    (inst / "tools" / "leak_refs.py").unlink()
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "[no-leak-refs]" in r.stdout          # absent => every write refused, loudly, never a silent pass


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- the two chokepoints and the two call-site hooks -------------------------------------------

def test_sources_run_refuses_before_gh(armed):
    sources = _load("sources")
    calls = []
    src = sources.GitHubSource.__new__(sources.GitHubSource)
    src._raw_run = lambda args: calls.append(args) or ""
    with pytest.raises(RuntimeError):
        src._run(["issue", "comment", "5", "--body", LEAK])
    assert calls == []
    src._run(["issue", "comment", "5", "--body", "clean"])
    assert len(calls) == 1


def test_work_run_never_spawns_on_a_hit(armed, tmp_path, monkeypatch):
    work = _load("work")
    spawned = []

    def fake(argv, *a, **k):
        if "rev-parse" in argv:          # the patterns-file work-tree probe: answer "not a git repository"
            return types.SimpleNamespace(returncode=128, stdout="", stderr=(
                "fatal: not a git repository (or any of the parent directories): .git"))
        spawned.append(argv)
        return types.SimpleNamespace(returncode=0, stdout="ok", stderr="")
    monkeypatch.setattr(work.subprocess, "run", fake)
    for argv in (["git", "commit", "-m", LEAK], ["gh", "pr", "comment", "5", "--body", LEAK],
                 ["/opt/homebrew/bin/gh", "issue", "comment", "5", "-b" + LEAK]):
        with pytest.raises(RuntimeError):
            work._run(tmp_path, argv)
    assert spawned == []
    assert work._run(tmp_path, ["gh", "pr", "comment", "5", "--body", "clean"]) == "ok"
    assert len(spawned) == 1


def test_upstream_file_refuses_without_calling_run(armed):
    upstream = _load("upstream")
    calls = []
    out = upstream._file_upstream("o/r", "t", LEAK, "", lambda a: calls.append(a) or (0, "{}", ""))
    assert calls == [] and out["issue"] is None and out["error"] and SLUG not in out["error"]


def test_feature_propagate_write_refuses(armed, tmp_path):
    fp = _load("feature_propagate")
    calls = []
    with pytest.raises(RuntimeError):
        fp._write_remote(lambda cwd, a: calls.append(a) or "", tmp_path, "o/r", "p.md", "clean", "", LEAK)
    assert calls == []


# ---- the documented gesture: export the variable, no flag, a fake gh on PATH --------------------

def test_exported_variable_gesture_never_reaches_gh(armed, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\necho called >> '%s'\nexit 0\n" % log)
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    code = textwrap.dedent("""
        import sys; sys.path.insert(0, %r)
        import sources
        s = sources.GitHubSource.__new__(sources.GitHubSource)
        s._raw_run = sources._run_gh
        try:
            s._run(["issue", "comment", "5", "--body", sys.argv[1]])
        except RuntimeError as e:
            print("REFUSED"); sys.exit(3)
    """ % str(SCRIPTS))
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    r = subprocess.run([sys.executable, "-c", code, LEAK], env=env, capture_output=True, text=True)
    assert r.returncode == 3 and "REFUSED" in r.stdout and not log.exists()
    r = subprocess.run([sys.executable, "-c", code, "clean text"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and log.exists()          # the fake gh WAS reached when text is clean


# ---- structural: a new gh write path outside the known routes fails the suite -------------------

_WRITE_SITE = re.compile(r'"(?:issue|pr)",\s*"(?:comment|create|edit|close|review)"'
                         r'|"pr",\s*"create"|"api",\s*"-X",\s*"(?:POST|PUT|PATCH)"')
#: file -> how its writes are covered. "guarded" = through a guarded chokepoint or call-site hook.
KNOWN_WRITE_FILES = {
    "skills/sigma-loop/scripts/sources.py": "guarded: GitHubSource._run",
    "skills/sigma-loop/scripts/blockers.py": "guarded: source._run",
    "skills/sigma-loop/scripts/promote.py": "guarded: source._run",
    "skills/sigma-loop/scripts/unpark.py": "guarded: source._run",
    "skills/sigma-loop/scripts/triage.py": "guarded: source._run",
    "skills/sigma-dossier/scripts/dossier.py": "guarded: source._run",
    "skills/sigma-scope/scripts/assign.py": "guarded: source._run",
    "skills/sigma-loop/scripts/work.py": "guarded: work._run",
    "skills/sigma-loop/scripts/gh_api.py": "guarded: _default_run",
    "skills/sigma-loop/scripts/upstream.py": "guarded: _file_upstream hook",
    "skills/sigma-loop/scripts/feature_propagate.py": "guarded: _write_remote hook",
    "skills/sigma-rebase/scripts/verify_merge.py": "gap: --fill takes the guarded commit messages",
    "skills/sigma-status/scripts/merge_queue_enable.py": "gap: sets a repo flag, no free text",
}


def test_every_gh_write_site_is_a_known_route():
    seen = set()
    for p in (ROOT / "skills").rglob("*.py"):
        if "/tests/" in str(p) or p.name.startswith("test_"):
            continue
        if _WRITE_SITE.search(p.read_text(encoding="utf-8", errors="replace")):
            seen.add(str(p.relative_to(ROOT)))
    assert sorted(seen - set(KNOWN_WRITE_FILES)) == [], "a new gh write path: guard it and list it"


def test_gh_api_default_runner_refuses_before_gh(armed, tmp_path, monkeypatch):
    gh_api = _load("gh_api")
    calls = []
    monkeypatch.setattr(gh_api, "subprocess", types.SimpleNamespace(run=lambda *a, **k: calls.append(a)))
    with pytest.raises(RuntimeError):
        gh_api._default_run(["api", "-X", "POST", "repos/o/r/issues/5/comments", "-f", "body=" + LEAK])
    assert calls == []
