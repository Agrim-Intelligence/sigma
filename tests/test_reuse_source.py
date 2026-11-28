"""#2280: `reuse.py source` says whether the reuse search uses the knowledge graph or `git grep`, and
`check-research` checks the dossier's `**Source:**` line against it.

`source()` reaches `kg.py freshness` and `git diff` only through the injected runner, so no real graph is
needed; the ancestor rule is exercised against a real temporary git repository. Every refusal asserts the exact
error list, so an extra or a missing error is red. The graph path (mode `graph`) is exercised only with an
injected runner: it was never run against a built graph here."""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
REUSE = HERE.parent / "skills" / "sigma-loop" / "scripts" / "reuse.py"


def _fixtures():
    """tests/test_reuse.py's builders (`_project`, `_dossier`, `_run`, `_git`, ...), loaded by path."""
    spec = importlib.util.spec_from_file_location("_reuse_fixtures", HERE / "test_reuse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = _fixtures()
C40 = "ab" * 20
C64 = "cd" * 32
GONE = object()                                # a key the fake helper leaves out
NO_BUILD = "cannot list the files changed since the graph build"
HELPER = "knowledge graph helper unavailable"
SOURCE_USAGE = "reuse.py source <sdlc_dir>"
MISSING_SOURCE = "Source: missing; write **Source:** graph@<commit> or grep (<why>)"
BAD_FORM = "Source: not graph@<7 to 64 hexadecimal characters> or grep, optionally followed by (<text>)"


def _reuse():
    return T._reuse()


def _kg(**over):
    """A freshness answer for a usable graph, with `over` applied; `GONE` removes the key."""
    fresh = {"enabled": True, "built": True, "holds_code": True, "built_at_commit": C40, "ancestor": True,
             "commits_behind": 3, "changed_files": 2}
    fresh.update(over)
    return {k: v for k, v in fresh.items() if v is not GONE}


def _proc(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")


def _runner(fresh, diff=("a.py",)):
    """(run, calls): a fake for both subprocess calls. `fresh` is a dict (printed as JSON), a CompletedProcess,
    or an exception to raise; `diff` is a list of lines, a CompletedProcess or an exception. Every argv is kept."""
    calls = []

    def run(argv):
        calls.append(list(argv))
        what = fresh if "freshness" in argv else diff
        if isinstance(what, BaseException):
            raise what
        if isinstance(what, subprocess.CompletedProcess):
            return what
        return _proc(json.dumps(what) if "freshness" in argv else "".join(line + "\n" for line in what))
    return run, calls


def _source(tmp_path, fresh, diff=("a.py",)):
    run, calls = _runner(fresh, diff)
    return _reuse().source(str(tmp_path / ".sdlc"), run=run), calls


def _grep(reason):
    return {"mode": "grep", "graph_commit": None, "reason": reason, "delta_files": []}


# -- source(): the condition table, one clause per test ---------------------------------------------------------

@pytest.mark.parametrize("value", [False, None, "true", 1, GONE], ids=["false", "null", "string", "one", "missing"])
def test_source_is_grep_when_the_graph_is_disabled(tmp_path, value):
    assert _source(tmp_path, _kg(enabled=value))[0] == _grep("knowledge graph disabled")


@pytest.mark.parametrize("value", [False, None, "true", 1, GONE], ids=["false", "null", "string", "one", "missing"])
def test_source_is_grep_when_the_graph_is_not_built(tmp_path, value):
    assert _source(tmp_path, _kg(built=value))[0] == _grep("graph not built")


@pytest.mark.parametrize("value", [None, False, "true", 1, GONE], ids=["null", "false", "string", "one", "missing"])
def test_source_is_grep_when_holds_code_is_not_true(tmp_path, value):
    assert _source(tmp_path, _kg(holds_code=value))[0] == _grep("cannot confirm the graph holds code")


@pytest.mark.parametrize("value", [None, GONE, "not-hex", C40[:12], C40 + "a", C40.upper(), "--output=x", 7, "", 10 ** 39],
                         ids=["null", "missing", "not-hex", "prefix12", "hex41", "upper", "flag-like", "number",
                              "empty", "forty-digit-number"])
def test_source_is_grep_when_there_is_no_build_commit(tmp_path, value):
    """A build commit that is not 40 or 64 lowercase hex is refused BEFORE any git call: only the helper's call."""
    result, calls = _source(tmp_path, _kg(built_at_commit=value))
    assert result == _grep("graph has no build commit")
    assert len(calls) == 1 and "freshness" in calls[0]


@pytest.mark.parametrize("value", [False, None, "yes", 1, GONE], ids=["false", "null", "string", "one", "missing"])
def test_source_is_grep_when_the_commit_is_not_an_ancestor(tmp_path, value):
    assert _source(tmp_path, _kg(ancestor=value))[0] == _grep("graph commit is not in this history")


def test_source_is_grep_when_the_graph_is_more_than_500_files_behind(tmp_path):
    result, calls = _source(tmp_path, _kg(changed_files=501))
    assert result == _grep("graph is 501 files behind (limit 500)")
    assert len(calls) == 1


def test_source_is_graph_at_exactly_500_files_behind(tmp_path):
    result, _ = _source(tmp_path, _kg(changed_files=500))
    assert result["mode"] == "graph" and result["reason"] == ""


@pytest.mark.parametrize("value", [None, True, "3", 1.5, GONE], ids=["null", "true", "string", "float", "missing"])
def test_source_is_grep_when_changed_files_is_not_a_number(tmp_path, value):
    """`true` is not a number: a bool must not read as one file behind."""
    result, calls = _source(tmp_path, _kg(changed_files=value))
    assert result == _grep("graph is an unknown number of files behind (limit 500)")
    assert len(calls) == 1


def test_source_reports_the_first_failing_condition_in_table_order(tmp_path):
    every = {"enabled": False, "built": False, "holds_code": None, "built_at_commit": None, "ancestor": False,
             "changed_files": 501}
    reasons = ["knowledge graph disabled", "graph not built", "cannot confirm the graph holds code",
               "graph has no build commit", "graph commit is not in this history",
               "graph is 501 files behind (limit 500)"]
    good = {"enabled": True, "built": True, "holds_code": True, "built_at_commit": C40, "ancestor": True}
    for k, reason in enumerate(reasons):
        failing = dict(good, changed_files=2)
        failing.update({key: every[key] for key in list(every)[k:]})     # every condition from row k on fails
        assert _source(tmp_path, _kg(**failing))[0]["reason"] == reason, reason


def test_source_is_graph_and_lists_the_changed_files_sorted(tmp_path):
    for commit in (C40, C64):
        result, calls = _source(tmp_path, _kg(built_at_commit=commit), diff=["z.py", "a/b.py", "m.md", "a/b.py", "z.py"])
        assert result == {"mode": "graph", "graph_commit": commit, "reason": "",
                          "delta_files": ["a/b.py", "m.md", "z.py"]}
        assert len(calls) == 2


def test_the_diff_commit_is_passed_behind_end_of_options(tmp_path):
    _, calls = _source(tmp_path, _kg())
    root = str(pathlib.Path(tmp_path / ".sdlc").resolve().parent)
    assert calls[1] == ["git", "-C", root, "diff", "--name-only", "--end-of-options", C40 + "..HEAD"]


@pytest.mark.parametrize("diff", [_proc("", 1), _proc("a.py\n", 128), OSError("no git"),
                                  subprocess.TimeoutExpired("git", 60)], ids=["exit1", "exit128", "oserror", "timeout"])
def test_source_is_grep_when_the_diff_fails(tmp_path, diff):
    """Never graph mode with an empty delta: that would hide new code behind a stale graph."""
    assert _source(tmp_path, _kg(), diff=diff)[0] == _grep(NO_BUILD)


@pytest.mark.parametrize("fresh", [
    subprocess.TimeoutExpired("kg.py", 60), OSError("no interpreter"), _proc(json.dumps(_kg()), 1),
    _proc(""), _proc("not json"), _proc("[1, 2]"), _proc("3"), _proc("null"),
], ids=["timeout", "oserror", "exit1", "empty", "garbage", "list", "number", "null"])
def test_source_is_grep_when_the_helper_fails_or_prints_garbage(tmp_path, fresh):
    result, calls = _source(tmp_path, fresh)
    assert result == _grep(HELPER)
    assert len(calls) == 1


def test_source_is_grep_when_the_helper_prints_absurdly_nested_json(tmp_path):
    """json.loads raises RecursionError on this, not ValueError: the verb must still never raise."""
    result, _ = _source(tmp_path, _proc("[" * 200000))
    assert result == _grep(HELPER)


def test_source_reaches_kg_through_a_subprocess_not_an_import(tmp_path):
    before = "kg" in sys.modules
    result, calls = _source(tmp_path, _kg(enabled=False))
    sdlc = tmp_path / ".sdlc"
    assert result["mode"] == "grep"
    assert len(calls) == 1 and calls[0][0] == sys.executable
    assert calls[0][1].endswith("sigma-kg/scripts/kg.py") and pathlib.Path(calls[0][1]).is_file()
    assert calls[0][2:] == ["freshness", str(sdlc)]
    assert ("kg" in sys.modules) == before


def test_source_line_text_for_both_modes():
    m = _reuse()
    assert m.source_line({"mode": "graph", "graph_commit": C40, "reason": "", "delta_files": []}) == "graph@" + C40[:12]
    assert m.source_line({"mode": "graph", "graph_commit": C64, "reason": "", "delta_files": []}) == "graph@" + C64[:12]
    assert m.source_line(_grep("graph not built")) == "grep (graph not built)"


def test_source_uses_no_git_in_grep_mode(tmp_path):
    _, calls = _source(tmp_path, _kg(built=False))
    assert len(calls) == 1


def test_the_real_runner_gives_the_helper_a_bounded_text_call(tmp_path, monkeypatch):
    """No `run`: the default runner is text mode, 60 s, git told not to fetch lazily and not to leave `-C`'s repo."""
    seen = {}

    def spy(argv, *a, **kw):
        seen.update(kw)
        return _proc(json.dumps(_kg(enabled=False)))
    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    assert _reuse().source(str(tmp_path / ".sdlc"))["mode"] == "grep"
    assert (seen["text"], seen["capture_output"], seen["timeout"]) == (True, True, 60)
    assert seen["env"]["GIT_NO_LAZY_FETCH"] == "1" and "GIT_DIR" not in seen["env"]


# -- the CLI verb -----------------------------------------------------------------------------------------------

def _cli(*args, cwd=None):
    return subprocess.run([sys.executable, str(REUSE), *args], capture_output=True, text=True, cwd=cwd)


def test_cli_source_prints_one_json_line(tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    proc = _cli("source", str(sdlc))
    assert proc.returncode == 0 and proc.stderr == ""
    assert proc.stdout == json.dumps(_grep("knowledge graph disabled"), sort_keys=True) + "\n"
    assert set(json.loads(proc.stdout)) == {"mode", "graph_commit", "reason", "delta_files"}


@pytest.mark.parametrize("args", [["source"], ["source", "a", "b"], ["source", "a", "--at-research"],
                                  ["source", "--at-research"]], ids=["no-dir", "extra", "flag", "flag-only"])
def test_cli_source_rejects_other_shapes(tmp_path, args):
    proc = _cli(*args)
    assert (proc.returncode, proc.stdout) == (2, "")
    assert proc.stderr.startswith("usage: reuse.py check-research ") and SOURCE_USAGE in proc.stderr


# -- the form rule ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, ""], ids=["absent", "empty"])
def test_a_missing_source_line_is_refused(tmp_path, value):
    assert T._run(tmp_path, T._dossier(source=value)) == (
        [MISSING_SOURCE], ["Searched at: commit 0123456 is not on this machine"])


MALFORMED = ["graphite", "graph@abc", "graph@" + "a" * 6, "graph@" + "a" * 65, "graph@ABCDEF1", "grep(x)", "grep (",
             "grep ()", "grep extra", "graph", "graph@", "--output=x", "graph@--output=x", "grep (x) y"]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_source_line_is_refused_with_or_without_the_flag(tmp_path, monkeypatch, value):
    _, _, head = T._project(tmp_path, git=True)
    m = _reuse()
    calls = T._spy(monkeypatch, m)
    asked = []
    monkeypatch.setattr(m, "source", lambda *a, **k: asked.append(1) or _grep("x"))
    text = T._dossier(source=value, searched_at=head)
    assert T._run(tmp_path, text, m=m) == ([BAD_FORM], [])
    del calls[:]
    assert T._run(tmp_path, text, m=m, at_research=True) == ([BAD_FORM], [])
    # with the flag: the Searched-at existence check ran, and nothing else reached git or the helper
    assert [c for c in calls if "merge-base" in c] == [] and asked == []
    assert len([c for c in calls if "rev-parse" in c]) == 1


def test_valid_source_forms_pass_the_form_rule(tmp_path):
    for value in ("graph@" + "a" * 7, "graph@" + "a" * 12, "graph@" + "a" * 40, "graph@" + "b" * 64, "grep",
                  "grep (graph not built)"):
        errors, _ = T._run(tmp_path, T._dossier(source=value))
        assert errors == [], value


# -- the at-research rules: the ancestor rule (real git) ---------------------------------------------------------

def _line(tmp_path):
    """(sdlc, root, [A, B, C], O): a repo on `main` with commits A, B, C on a line and an unrelated orphan commit O."""
    sdlc, root, a = T._project(tmp_path, git=True)
    ids = [a]
    for name in ("b", "c"):
        (root / (name + ".txt")).write_text(name, encoding="utf-8")
        T._git(root, "add", name + ".txt")
        T._git(root, "commit", "-qm", name)
        ids.append(T._git(root, "rev-parse", "HEAD"))
    T._git(root, "checkout", "-q", "--orphan", "other")
    T._git(root, "commit", "-q", "--allow-empty", "-m", "orphan")
    orphan = T._git(root, "rev-parse", "HEAD")
    T._git(root, "checkout", "-qf", "main")
    return sdlc, root, ids, orphan


def _agree(monkeypatch, m, commit):
    """Make the live `source()` a graph at `commit`, so only the ancestor rule is under test."""
    monkeypatch.setattr(m, "source", lambda sdlc_dir, run=None: {
        "mode": "graph", "graph_commit": commit, "reason": "", "delta_files": []})


def _not_ancestor(x):
    return "Source: graph commit %s is not an ancestor of the searched-at commit" % x


def _ancestor_run(tmp_path, monkeypatch, graph, searched, full=None, **kw):
    m = _reuse()
    _agree(monkeypatch, m, full or (graph + "0" * 40)[:40])
    return T._run(tmp_path, T._dossier(source="graph@" + graph, searched_at=searched), m=m, at_research=True, **kw)


def test_at_research_a_graph_source_must_be_an_ancestor_of_the_searched_at_commit(tmp_path, monkeypatch):
    _, _, (a, b, c), orphan = _line(tmp_path)
    assert _ancestor_run(tmp_path, monkeypatch, a[:12], b, a) == ([], [])
    assert _ancestor_run(tmp_path, monkeypatch, b[:12], b, b) == ([], [])          # a commit is its own ancestor
    assert _ancestor_run(tmp_path, monkeypatch, a[:7], b, a) == ([], [])           # an abbreviation resolves
    assert _ancestor_run(tmp_path, monkeypatch, c[:12], b, c) == ([_not_ancestor(c[:12])], [])
    assert _ancestor_run(tmp_path, monkeypatch, orphan[:12], b, orphan) == ([_not_ancestor(orphan[:12])], [])
    assert _ancestor_run(tmp_path, monkeypatch, "deadbee", b, "deadbee" + "0" * 33) == ([_not_ancestor("deadbee")], [])


def test_a_searched_at_commit_absent_from_the_machine_is_never_success(tmp_path, monkeypatch):
    _, _, (a, b, c), _ = _line(tmp_path)
    errors, _ = _ancestor_run(tmp_path, monkeypatch, a[:12], "a" * 40, a)
    assert errors == ["Searched at: commit aaaaaaa is not on this machine", _not_ancestor(a[:12])]


def test_the_ancestor_rule_compares_with_the_searched_at_commit_not_head(tmp_path, monkeypatch):
    """graph@B is an ancestor of HEAD (C) but not of the searched-at commit A: refused."""
    _, _, (a, b, c), _ = _line(tmp_path)
    assert T._git(tmp_path / "repo", "rev-parse", "HEAD") == c
    assert _ancestor_run(tmp_path, monkeypatch, b[:12], a, b) == ([_not_ancestor(b[:12])], [])


def test_without_at_research_the_ancestor_rule_does_not_run(tmp_path, monkeypatch):
    sdlc, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    calls = T._spy(monkeypatch, m)
    monkeypatch.setattr(m, "source", lambda *a_, **k: pytest.fail("source() ran without --at-research"))
    text = T._dossier(source="graph@" + c[:12], searched_at=b)
    assert T._run(tmp_path, text, m=m) == ([], [])
    assert [call for call in calls if "merge-base" in call] == []
    # the gesture the docs give: no flag at all
    proc = _cli("check-research", str(sdlc), "7")
    assert proc.returncode == 0, proc.stderr


def test_at_research_ancestor_rule_is_skipped_when_searched_at_is_missing_or_not_hex(tmp_path, monkeypatch):
    _, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    calls = T._spy(monkeypatch, m)
    _agree(monkeypatch, m, a)
    assert T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=None), m=m, at_research=True) == (
        [T.MISSING], [])
    assert T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at="not-hex"), m=m, at_research=True) == (
        [T.NOT_FULL], [])
    assert T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=""), m=m, at_research=True) == (
        [T.NOT_FULL], [])
    assert calls == []


def test_at_research_ancestor_git_timeout_is_one_error(tmp_path, monkeypatch):
    _, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    _agree(monkeypatch, m, a)
    real = m._run_git

    def slow(argv):
        if "merge-base" in argv:
            raise subprocess.TimeoutExpired("git", 10)
        return real(argv)
    monkeypatch.setattr(m, "_run_git", slow)
    errors, _ = T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=b), m=m, at_research=True)
    assert errors == ["Source: graph commit %s could not be checked: git did not answer in 10 seconds" % a[:12]]


def test_at_research_ancestor_git_that_cannot_run_is_one_error(tmp_path, monkeypatch):
    _, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    _agree(monkeypatch, m, a)
    real = m._run_git

    def broken(argv):
        if "^{commit}" in argv[-1] and a[:12] in argv[-1]:
            raise FileNotFoundError(2, "No such file or directory", "git")
        return real(argv)
    monkeypatch.setattr(m, "_run_git", broken)
    errors, _ = T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=b), m=m, at_research=True)
    assert errors == [_not_ancestor(a[:12])]


def test_the_ancestor_rule_passes_both_commits_behind_end_of_options(tmp_path, monkeypatch):
    sdlc, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    _agree(monkeypatch, m, a)
    calls = T._spy(monkeypatch, m)
    errors, _ = T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=b), m=m, at_research=True)
    assert errors == []
    root = str(pathlib.Path(sdlc).resolve().parent)
    assert ["git", "-C", root, "rev-parse", "--verify", "--end-of-options", a[:12] + "^{commit}"] in calls
    assert ["git", "-C", root, "merge-base", "--is-ancestor", "--end-of-options", a, b] in calls


def test_a_resolve_that_prints_a_flag_never_reaches_merge_base(tmp_path, monkeypatch):
    """The resolved id is hex-checked before it is handed to merge-base, whatever git printed."""
    sdlc, _, (a, b, c), _ = _line(tmp_path)
    m = _reuse()
    _agree(monkeypatch, m, a)
    calls, real = [], m._run_git

    def spy(argv):
        calls.append(list(argv))
        if argv[-1] == a[:12] + "^{commit}":
            return subprocess.CompletedProcess(argv, 0, stdout=b"--output=x\n", stderr=b"")
        return real(argv)
    monkeypatch.setattr(m, "_run_git", spy)
    errors, _ = T._run(tmp_path, T._dossier(source="graph@" + a[:12], searched_at=b), m=m, at_research=True)
    assert errors == [_not_ancestor(a[:12])]
    assert [c_ for c_ in calls if "merge-base" in c_] == []


# -- the at-research rules: agreement with the live source() -------------------------------------------------------

def _agreement(tmp_path, monkeypatch, dossier_source, result, at_research=True):
    _, _, head = T._project(tmp_path, git=True)
    m = _reuse()
    seen = []

    def fake(sdlc_dir, run=None):
        seen.append(str(sdlc_dir))
        return result
    monkeypatch.setattr(m, "source", fake)
    out = T._run(tmp_path, T._dossier(source=dossier_source.replace("HEAD", head[:12]), searched_at=head), m=m,
                 at_research=at_research)
    return out, seen


def test_at_research_refuses_grep_when_the_graph_is_usable(tmp_path, monkeypatch):
    result = {"mode": "graph", "graph_commit": C40, "reason": "", "delta_files": ["a", "b", "c"]}
    out, _ = _agreement(tmp_path, monkeypatch, "grep (knowledge graph disabled)", result)
    assert out == (["Source: the dossier says grep but the knowledge graph is usable (graph@%s): search the graph, "
                    "then the 3 changed files" % C40[:12]], [])


def test_at_research_refuses_graph_when_the_graph_is_not_usable(tmp_path, monkeypatch):
    out, _ = _agreement(tmp_path, monkeypatch, "graph@HEAD", _grep("graph not built"))
    assert out == (["Source: the dossier says graph but graph not built"], [])


def test_at_research_refuses_a_graph_commit_that_is_not_a_prefix_of_the_usable_graph(tmp_path, monkeypatch):
    result = {"mode": "graph", "graph_commit": C40, "reason": "", "delta_files": []}
    _, _, head = T._project(tmp_path, git=True)
    m = _reuse()
    monkeypatch.setattr(m, "source", lambda sdlc_dir, run=None: result)
    out = T._run(tmp_path, T._dossier(source="graph@" + head[:12], searched_at=head), m=m, at_research=True)
    assert out == (["Source: the dossier names graph@%s but the usable graph was built at %s" % (head[:12], C40[:12])],
                   [])


def test_at_research_accepts_agreement_in_both_modes(tmp_path, monkeypatch):
    out, _ = _agreement(tmp_path, monkeypatch, "grep (graph not built)", _grep("graph not built"))
    assert out == ([], [])
    _, _, head = T._project(tmp_path / "g", git=True)
    m = _reuse()
    monkeypatch.setattr(m, "source", lambda sdlc_dir, run=None: {
        "mode": "graph", "graph_commit": head, "reason": "", "delta_files": []})
    for written in (head[:7], head[:12], head):
        assert T._run(tmp_path / "g", T._dossier(source="graph@" + written, searched_at=head), m=m,
                      at_research=True) == ([], [])


def test_without_at_research_a_later_graph_rebuild_does_not_fail_the_check(tmp_path, monkeypatch):
    """The graph may be rebuilt between Research and a later re-check: no flag, no comparison, no helper call."""
    _, _, head = T._project(tmp_path, git=True)
    m = _reuse()
    monkeypatch.setattr(m, "source", lambda *a, **k: pytest.fail("source() ran without --at-research"))
    for written in ("grep (knowledge graph disabled)", "graph@" + head[:12]):
        assert T._run(tmp_path, T._dossier(source=written, searched_at=head), m=m) == ([], [])


def test_at_research_runs_source_exactly_once(tmp_path, monkeypatch):
    out, seen = _agreement(tmp_path, monkeypatch, "grep (graph not built)", _grep("graph not built"))
    assert out == ([], []) and len(seen) == 1
    assert seen[0].endswith(".sdlc")
    out, seen = _agreement(tmp_path / "two", monkeypatch, "graph@HEAD", _grep("graph not built"))
    assert len(seen) == 1
