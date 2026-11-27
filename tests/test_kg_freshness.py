"""#2277: `kg.py freshness` reports which commit the knowledge graph was built from and how far HEAD has
moved since. It reads only the head and tail of the graph file, passes the build commit to git only when
it is full hex and only after `--end-of-options`, and reports every unknown as null rather than a guess."""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading

import pytest

KG = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-kg" / "scripts" / "kg.py"
KEYS = {"enabled", "built", "holds_code", "built_at_commit", "ancestor", "commits_behind", "changed_files"}
SHA_A = hashlib.sha1(b"a").hexdigest()
SHA_B = hashlib.sha1(b"b").hexdigest()
SHA_64 = hashlib.sha256(b"a").hexdigest()
# Variables that would send the fixture's git to another repository than `-C` names.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")


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


def _check(r):
    assert set(r) == KEYS
    return r


def _graph(base, text):
    p = pathlib.Path(base).resolve().parent / "graphify-out" / "graph.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _code_node(**kw):
    return {"id": "app_main", "label": "main()", "file_type": "code", "source_file": "src/app.py",
            "source_location": "L12", **kw}


def _graph_text(nodes, commit=None):
    """The builder's shape: json.dump(indent=2), so a node spans several lines; built_at_commit is the last key."""
    data = {"directed": False, "multigraph": False, "graph": {}, "nodes": nodes, "links": []}
    if commit is not None:
        data["built_at_commit"] = commit
    return json.dumps(data, indent=2)


class _Rec:
    """A recording `run`: answers each git verb (argv[3]) from a table and records every argv."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = {"merge-base": (0, ""), "rev-list": (0, "3\n"), "diff": (0, "a\nb\n"), **(answers or {})}

    def __call__(self, argv):
        self.calls.append(list(argv))
        rc, out = self.answers[argv[3]]
        return subprocess.CompletedProcess(argv, rc, out, "")


def test_freshness_reports_nulls_when_the_graph_is_disabled(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": False})
    _graph(base, _graph_text([_code_node()], SHA_A))
    rec = _Rec()
    r = _check(kg.freshness(base, run=rec))
    assert r["enabled"] is False and r["built"] is True
    assert [r[k] for k in sorted(KEYS - {"enabled", "built"})] == [None] * 5
    assert rec.calls == []


def test_freshness_reports_built_false_when_there_is_no_graph_file(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    r = _check(kg.freshness(base, run=_Rec()))
    assert r["enabled"] is True and r["built"] is False
    assert [r[k] for k in sorted(KEYS - {"enabled", "built"})] == [None] * 5


def _stamped(commit):
    return f'"built_at_commit": "{commit}"}}'


def test_freshness_reads_the_build_commit_from_the_tail(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, "x" * 200_000 + _stamped(SHA_A))
    r = _check(kg.freshness(base, run=_Rec()))
    assert r["built_at_commit"] == SHA_A


def test_freshness_does_not_read_past_the_tail(tmp_path):
    """The control for the 64 KiB bound: the stamp sits in the first 100 bytes of a 200,000-byte file."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    head = _stamped(SHA_A)
    assert len(head) < 100
    _graph(base, head + "x" * (200_000 - len(head)))
    rec = _Rec()
    r = _check(kg.freshness(base, run=rec))
    assert r["built"] is True and r["built_at_commit"] is None
    assert rec.calls == []
    _graph(base, "x" * (200_000 - len(head)) + head)     # the same bytes, stamp at the end: it is read
    assert _check(kg.freshness(base, run=_Rec()))["built_at_commit"] == SHA_A


def test_a_64_character_build_commit_is_accepted(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, _graph_text([], SHA_64))
    r = _check(kg.freshness(base, run=_Rec()))
    assert r["built_at_commit"] == SHA_64


def test_a_build_commit_that_is_not_40_hex_is_ignored(tmp_path):
    """FR-58. The recording-run assertion is the half that proves the pattern: with a loosened pattern the
    bogus value would still sit after --end-of-options, so the no-`pwned`-file half cannot go red alone."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, '{"nodes": [], ' + _stamped("--output=pwned"))
    rec = _Rec()
    r = _check(kg.freshness(base, run=rec))
    assert not any("pwned" in arg for argv in rec.calls for arg in argv)
    assert r["built_at_commit"] is None
    subprocess.run([sys.executable, str(KG), "freshness", base], cwd=tmp_path, capture_output=True, timeout=60)
    assert not [p for p in tmp_path.rglob("*") if "pwned" in p.name]


@pytest.mark.parametrize("length", [39, 41, 63, 65])
def test_a_build_commit_of_another_length_is_ignored(tmp_path, length):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, _graph_text([], (SHA_64 * 2)[:length]))
    rec = _Rec()
    r = _check(kg.freshness(base, run=rec))
    assert r["built_at_commit"] is None and rec.calls == []
    _graph(base, _graph_text([], SHA_64[:40]))           # the same fixture at 40 characters: it is read
    assert _check(kg.freshness(base, run=_Rec()))["built_at_commit"] == SHA_64[:40]


def test_the_last_build_commit_in_the_tail_wins(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, '{"nodes": [], "built_at_commit": "' + SHA_A + '", ' + _stamped(SHA_B))
    r = _check(kg.freshness(base, run=_Rec()))
    assert r["built_at_commit"] == SHA_B


def _holds_code(kg, base, nodes):
    _graph(base, _graph_text(nodes, SHA_A))
    return _check(kg.freshness(base, run=_Rec()))["holds_code"]


def _doc_node(i=0, **kw):
    return {"id": f"doc_{i}", "label": f"Section {i}", "file_type": "document", "source_file": f"docs/d{i}.txt",
            "source_location": "section 1", **kw}


def test_freshness_finds_a_code_node_in_the_head(tmp_path):
    """indent=2 splits each node over lines, and an object that mentions file_type but is not JSON comes first."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    nodes = json.dumps([_doc_node(), _code_node()], indent=2)
    assert "\n" in nodes.split('"app_main"')[1].split("}")[0]          # the code node really spans lines
    _graph(base, '{\n  "nodes": [\n    { "file_type": "code", not json },\n' + nodes[1:] + ",\n"
           + _stamped(SHA_A))
    r = _check(kg.freshness(base, run=_Rec()))
    assert r["holds_code"] is True


def test_freshness_reports_holds_code_unknown_for_a_notes_only_graph(tmp_path):
    """A document node with a code-like source file and an L<digits> location: file_type alone rejects it."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    notes = [_doc_node(source_file="src/app.py", source_location="L4"),
             {"id": "c", "label": "Idea", "file_type": "concept", "source_file": "docs/a.txt"}]
    assert _holds_code(kg, base, notes) is None                         # unknown, never False
    assert _holds_code(kg, base, notes + [_code_node()]) is True


@pytest.mark.parametrize("source_file", [".sdlc/knowledge/analysis/a.md", ".sdlc/knowledge/research/a.json",
                                         "docs/guide.md", None])
def test_a_document_node_labelled_code_does_not_count(tmp_path, source_file):
    """Each fixture carries file_type code and an L<digits> location, so only the source_file clause rejects it."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    assert _holds_code(kg, base, [_code_node(source_file=source_file, source_location="L3")]) is None
    assert _holds_code(kg, base, [_code_node(source_file="src/app.py", source_location="L3")]) is True


def test_freshness_does_not_look_past_the_first_mebibyte_for_code(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    others = [_doc_node(i, label="x" * 200) for i in range(4000)]
    _graph(base, _graph_text(others + [_code_node()], SHA_A))
    text = (pathlib.Path(base).parent / "graphify-out" / "graph.json").read_text()
    assert text.index('"app_main"') > 1_100_000
    assert _check(kg.freshness(base, run=_Rec()))["holds_code"] is None
    assert _holds_code(kg, base, [_code_node()] + others) is True       # the same nodes, code first: found


def test_holds_code_ignores_the_scope_in_config(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True, "scope": "full"})
    assert _holds_code(kg, base, [_doc_node()]) is None
    assert _holds_code(kg, base, [_doc_node(), _code_node()]) is True


@pytest.mark.parametrize("source_location", [None, "section 2", "L"])
def test_a_code_node_without_a_line_location_does_not_count(tmp_path, source_location):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    node = _code_node()
    if source_location is None:
        del node["source_location"]
    else:
        node["source_location"] = source_location
    assert _holds_code(kg, base, [node]) is None
    assert _holds_code(kg, base, [_code_node(source_location="L7")]) is True


def _never_raises(fn, *args, **kwargs):
    """freshness never raises: an exception is the assertion failing, not a crash of the test."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"raised {exc!r}") from exc


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if k not in _GIT_LOCATION_ENV}
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _repo(path):
    """A real repository with one commit of a.txt and b.txt. -> (repo, commit)"""
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    for name in ("a.txt", "b.txt"):
        (path / name).write_text("one\n")
    _git(path, "add", "a.txt", "b.txt")
    _git(path, "commit", "-q", "-m", "A")
    return path, _git(path, "rev-parse", "HEAD")


def _repo_one_commit_behind(tmp_path):
    """Commit A, the graph stamped with A, then commit B changing exactly two files. -> sdlc dir"""
    repo, a = _repo(tmp_path / "repo")
    base = _sdlc(repo, {"enabled": True})
    _graph(base, _graph_text([_code_node()], a))
    for name in ("a.txt", "b.txt"):
        (repo / name).write_text("two\n")
    _git(repo, "add", "a.txt", "b.txt")                    # only the two named files: changed_files is exact
    _git(repo, "commit", "-q", "-m", "B")
    return base


def test_freshness_counts_commits_and_changed_files(tmp_path):
    kg = _kg()
    base = _repo_one_commit_behind(tmp_path)
    r = _check(kg.freshness(base))                         # default repo_root: the parent of the sdlc dir
    assert (r["ancestor"], r["commits_behind"], r["changed_files"]) == (True, 1, 2)
    assert r["holds_code"] is True


@pytest.mark.parametrize("case", ["real-repo", "merge-base-exit-1", "merge-base-exit-128"])
def test_freshness_marks_a_commit_outside_history(tmp_path, case):
    """Counts are computed only when ancestor is True: a False or an unknown ancestor makes exactly one git call."""
    kg = _kg()
    if case == "real-repo":
        repo, _ = _repo(tmp_path / "repo")
        base = _sdlc(repo, {"enabled": True})
        _graph(base, _graph_text([], SHA_B))               # 40 hex, but no object in this repository
        r = _check(kg.freshness(base))
        assert r["ancestor"] in (False, None)
    else:
        rc = int(case.rsplit("-", 1)[1])
        base = _sdlc(tmp_path, {"enabled": True})
        _graph(base, _graph_text([], SHA_A))
        rec = _Rec({"merge-base": (rc, "")})               # rev-list and diff would answer if asked
        r = _check(kg.freshness(base, run=rec))
        assert [argv[3] for argv in rec.calls] == ["merge-base"]
        assert r["ancestor"] is {1: False, 128: None}[rc]
    assert r["built_at_commit"] is not None
    assert r["commits_behind"] is None and r["changed_files"] is None


def test_every_git_call_puts_the_commit_behind_end_of_options(tmp_path):
    """repo_root is explicit and is NOT the sdlc dir's parent, so ignoring it is seen."""
    kg = _kg()
    base = _sdlc(tmp_path / "proj", {"enabled": True})
    _graph(base, _graph_text([], SHA_A))
    elsewhere = str(tmp_path / "elsewhere")
    assert elsewhere != str(pathlib.Path(base).resolve().parent)
    rec = _Rec()
    r = _check(kg.freshness(base, elsewhere, run=rec))
    assert r["ancestor"] is True
    by_verb = {argv[3]: argv for argv in rec.calls}
    assert sorted(by_verb) == ["diff", "merge-base", "rev-list"]
    expected = {"merge-base": SHA_A, "rev-list": f"{SHA_A}..HEAD", "diff": f"{SHA_A}..HEAD"}
    for verb, argv in by_verb.items():
        assert argv[:3] == ["git", "-C", elsewhere], verb
        assert "--end-of-options" in argv, verb
        assert argv[argv.index("--end-of-options") + 1] == expected[verb], verb


@pytest.mark.parametrize("exc", [FileNotFoundError(2, "No such file", "git"),
                                 subprocess.TimeoutExpired(["git"], 30)], ids=["missing", "timeout"])
def test_freshness_survives_a_missing_git(tmp_path, exc):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, _graph_text([], SHA_A))
    calls = []

    def run(argv):
        calls.append(argv)
        raise exc

    r = _check(_never_raises(kg.freshness, base, run=run))
    assert calls, "the injected run was never called"
    assert r["built_at_commit"] == SHA_A
    assert (r["ancestor"], r["commits_behind"], r["changed_files"]) == (None, None, None)


@pytest.mark.parametrize("answers, behind, changed", [
    ({"rev-list": (128, "3\n")}, None, 2),
    ({"diff": (128, "a\nb\n")}, 3, None),
    ({"rev-list": (0, "x")}, None, 2),
], ids=["rev-list-exit-128", "diff-exit-128", "rev-list-not-a-number"])
def test_a_git_call_that_fails_gives_null_for_that_key(tmp_path, answers, behind, changed):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, _graph_text([], SHA_A))
    r = _check(_never_raises(kg.freshness, base, run=_Rec(answers)))
    assert r["ancestor"] is True
    assert (r["commits_behind"], r["changed_files"]) == (behind, changed)


def test_freshness_ignores_an_exported_git_dir(tmp_path, monkeypatch):
    """A hook's or a stray GIT_DIR must not send the default runner to another repository than repo_root."""
    kg = _kg()
    base = _repo_one_commit_behind(tmp_path)
    other, _ = _repo(tmp_path / "other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    r = _check(kg.freshness(base))
    assert (r["ancestor"], r["commits_behind"], r["changed_files"]) == (True, 1, 2)


def test_the_default_git_runner_survives_undecodable_output():
    """git can print a path that is not UTF-8 (core.quotePath=false); decoding it must not raise."""
    kg = _kg()
    proc = _never_raises(lambda: kg._run_git([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'a\\xff\\n')"]))
    assert proc.returncode == 0 and proc.stdout.startswith("a")


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads through mode 000")
def test_an_unreadable_graph_directory_reports_built_unknown(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    graph = _graph(base, _graph_text([_code_node()], SHA_A))
    graph.parent.chmod(0o000)
    try:
        r = _check(_never_raises(kg.freshness, base, run=_Rec()))
    finally:
        graph.parent.chmod(0o755)
    assert r["enabled"] is True
    assert [r[k] for k in sorted(KEYS - {"enabled"})] == [None] * 6


def test_a_graph_path_that_is_a_directory_reports_unknown(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    graph = pathlib.Path(base).resolve().parent / "graphify-out" / "graph.json"
    graph.mkdir(parents=True)
    r = _check(_never_raises(kg.freshness, base, run=_Rec()))
    assert r["built"] is True
    assert [r[k] for k in sorted(KEYS - {"enabled", "built"})] == [None] * 5
    proc = subprocess.run([sys.executable, str(KG), "freshness", base], cwd=tmp_path, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_a_graph_that_vanishes_before_open_reports_unknown(tmp_path):
    """Deleted or replaced between the is_file() check and open(): unknown, not a traceback."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _graph(base, _graph_text([_code_node()], SHA_A))

    def vanished(*a, **k):
        raise FileNotFoundError("gone")
    kg.open = vanished                         # module global shadows the builtin for this module only
    r = _check(_never_raises(kg.freshness, base, run=_Rec()))
    assert r["built"] is True
    assert [r[k] for k in sorted(KEYS - {"enabled", "built"})] == [None] * 5


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes on this platform")
def test_a_graph_path_that_is_a_fifo_reports_unknown_promptly(tmp_path):
    """open() on a FIFO with no writer blocks forever; a regression must fail here, not hang the suite."""
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    graph = pathlib.Path(base).resolve().parent / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    os.mkfifo(graph)
    out = []
    t = threading.Thread(target=lambda: out.append(kg.freshness(base, run=_Rec())), daemon=True)
    t.start()
    t.join(5)
    hung = t.is_alive()
    if hung:                                   # release the blocked open() so no thread outlives the test
        os.close(os.open(graph, os.O_WRONLY | os.O_NONBLOCK))
        t.join(5)
    assert not hung, "freshness blocked opening a FIFO at the graph path"
    r = _check(out[0])
    assert r["built"] is True
    assert [r[k] for k in sorted(KEYS - {"enabled", "built"})] == [None] * 5


def _split_char_at(base, offset, size):
    """A code-first graph of exactly `size` bytes whose two-byte 'é' starts at `offset`, so a cut at offset+1
    splits it. Written as bytes: json.dumps would escape the character away."""
    pre, post = _graph_text([_code_node(), {"id": "pad", "label": "@@"}], SHA_A).split("@@")
    text = pre + "x" * (offset - len(pre)) + "é" + "x" * (size - offset - 2 - len(post)) + post
    p = _graph(base, "")
    p.write_bytes(text.encode("utf-8"))
    assert p.stat().st_size == size and p.read_bytes()[offset:offset + 2] == "é".encode("utf-8")


def test_a_multibyte_char_cut_by_the_head_window_reads_normally(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _split_char_at(base, kg._HEAD_BYTES - 1, kg._HEAD_BYTES + 100_000)
    r = _check(_never_raises(kg.freshness, base, run=_Rec()))
    assert (r["holds_code"], r["built_at_commit"], r["commits_behind"]) == (True, SHA_A, 3)


def test_a_multibyte_char_cut_by_the_tail_window_reads_normally(tmp_path):
    kg = _kg()
    base = _sdlc(tmp_path, {"enabled": True})
    _split_char_at(base, 200_000 - kg._TAIL_BYTES - 1, 200_000)
    r = _check(_never_raises(kg.freshness, base, run=_Rec()))
    assert (r["holds_code"], r["built_at_commit"], r["commits_behind"]) == (True, SHA_A, 3)


@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "enabled-graph-no-git-repo"])
def test_freshness_cli_prints_one_json_line_and_exits_zero(tmp_path, enabled):
    base = _sdlc(tmp_path, {"enabled": enabled})
    if enabled:
        _graph(base, _graph_text([_code_node()], SHA_A))   # tmp_path is not a git repository: git cannot answer
    proc = subprocess.run([sys.executable, str(KG), "freshness", base], cwd=tmp_path, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert len(proc.stdout.splitlines()) == 1
    out = json.loads(proc.stdout)
    assert set(out) == KEYS
    assert proc.stdout.strip() == json.dumps(out, sort_keys=True)
    if enabled:
        assert out["built_at_commit"] == SHA_A and out["ancestor"] is None


def test_usage_names_freshness():
    assert "freshness <sdlc_dir> [repo_root]" in _kg().USAGE
