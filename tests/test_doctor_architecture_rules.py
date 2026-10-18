"""sigma-doctor `rules` (#662, R-08): map every numbered north-star architecture rule to its enforcement.

Each clause of the parser, the readers and the classifier has its OWN test so it can be broken alone
and seen red (AGENTS.md "run the control"). Nothing here runs a model, the network or the whole suite.
"""
import json, os, pathlib, re, shlex, subprocess, sys, tempfile, textwrap, importlib.util

import pytest

D = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
SKILL_DIR = D.parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"


def _doc():
    spec = importlib.util.spec_from_file_location("doctor", D)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@pytest.fixture(autouse=True)
def _isolate_claude_config(monkeypatch, tmp_path_factory):
    """Hermetic: the developer's installed plugins and any ambient git env must not leak in."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    for var in ("CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


HEAD = "## Architecture (how it's built + the rules we develop by)\nHow the system is shaped.\n\n"


def _ns(rules, head=HEAD):
    return "# North star\n\n## Vision\nSomething.\n\n" + head + rules + "\n"


def _repo(d, north_star=None, decisions=None, files=()):
    """tmp/.sdlc/{config.json,context/north-star.md,decisions.json} plus tracked-looking files.
    No `git init`: `project_root` falls back to resolve() for a non-repo."""
    root = pathlib.Path(d)
    base = root / ".sdlc"; (base / "context").mkdir(parents=True)
    (base / "config.json").write_text('{"gates": {"phase_record": {"enabled": true}}}')
    if north_star is not None:
        (base / "context" / "north-star.md").write_text(north_star)
    if decisions is not None:
        (base / "decisions.json").write_text(decisions if isinstance(decisions, str) else json.dumps(decisions))
    for f in files:
        p = root / f; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x = 1\n")
    return str(base)


def _reg(*entries):
    return {"decisions": list(entries)}


def _one(spec_line, **kw):
    """Parse one rule `1. Layering holds. Enforced by: <spec_line>` and return its spec list."""
    rules = _doc().architecture_rules(_ns("1. Layering holds. Enforced by: " + spec_line))
    assert len(rules) == 1, rules
    return rules[0][2]


# ------------------------------------------------------------------ Task 1: the parser (pure)

def test_parser_dot_marker():
    assert _doc().architecture_rules(_ns("1. First rule.\n2. Second rule.")) == [
        (1, "First rule.", None), (2, "Second rule.", None)]


def test_parser_paren_marker():
    assert [r[0] for r in _doc().architecture_rules(_ns("1) First.\n2) Second."))] == [1, 2]


def test_parser_heading_with_trailing_parenthetical():
    assert len(_doc().architecture_rules(_ns("1. A rule."))) == 1


def test_parser_no_architecture_heading_is_empty():
    assert _doc().architecture_rules("# N\n\n## Vision\n1. A numbered thing.\n") == []


def test_parser_heading_needs_a_word_boundary():
    """`## Architectures` is not the Architecture section."""
    assert _doc().architecture_rules("## Architectures\n1. A rule.\n") == []


def test_parser_section_ends_at_a_heading_of_the_same_level():
    text = _ns("1. In section.\n\n## Design\n2. Not in section.")
    assert [r[0] for r in _doc().architecture_rules(text)] == [1]


def test_parser_section_ends_at_a_heading_of_a_higher_level():
    text = "## Architecture\n1. In section.\n\n# Top\n2. Not in section.\n"
    assert [r[0] for r in _doc().architecture_rules(text)] == [1]


def test_parser_section_survives_a_deeper_heading():
    text = "## Architecture\n1. In section.\n\n### Sub\n2. Still in section.\n"
    assert [r[0] for r in _doc().architecture_rules(text)] == [1, 2]


def test_parser_skips_scaffold_placeholders():
    text = _ns("1. <e.g. the UI layer holds no business logic>\n2. <e.g. dependencies point inward>\n3. Real rule.")
    assert [r[0] for r in _doc().architecture_rules(text)] == [3]


def test_parser_suffix_on_the_same_line():
    assert _one("review") == ["review"]


def test_parser_suffix_on_a_continuation_line():
    text = _ns("1. Layering holds.\n   Enforced by: ci:tests/test_layers.py")
    assert _doc().architecture_rules(text)[0][2] == ["ci:tests/test_layers.py"]


def test_parser_suffix_keyword_is_case_insensitive():
    text = _ns("1. Layering holds.\n   ENFORCED BY: review")
    assert _doc().architecture_rules(text)[0][2] == ["review"]


def test_parser_specs_split_on_commas():
    assert _one("ci:tests/a.py, decide:INV-1,review") == ["ci:tests/a.py", "decide:INV-1", "review"]


def test_parser_keeps_the_double_colon_in_the_spec_text():
    assert _one("ci:tests/a.py::test_x") == ["ci:tests/a.py::test_x"]


def test_parser_mid_sentence_mention_is_not_a_suffix():
    text = _ns("1. Anything that is enforced by: convention is fine here.")
    assert _doc().architecture_rules(text)[0][2] is None


def test_parser_uses_the_last_suffix_occurrence():
    text = _ns("1. Layering holds.\n   Enforced by: ci:tests/old.py\n   Enforced by: review")
    assert _doc().architecture_rules(text)[0][2] == ["review"]


def test_parser_continuation_stops_at_a_blank_line_then_unindented_paragraph():
    text = _ns("1. Layering holds.\n\nEnforced by: review\n")
    assert _doc().architecture_rules(text)[0][2] is None


def test_parser_continuation_continues_across_a_blank_line_when_indented():
    text = _ns("1. Layering holds.\n\n   Enforced by: review\n")
    assert _doc().architecture_rules(text)[0][2] == ["review"]


def test_parser_continuation_stops_at_the_next_numbered_rule():
    text = _ns("1. First.\n2. Second. Enforced by: review")
    got = _doc().architecture_rules(text)
    assert got[0][2] is None and got[1][2] == ["review"]


def test_parser_empty_suffix_yields_one_empty_spec():
    assert _one("") == [""]


@pytest.mark.parametrize("line", [
    "4. four \u2014 Enforced by: ci:tests/a.py",
    "4. four \u2013 Enforced by: ci:tests/a.py",
    "4. four - Enforced by: ci:tests/a.py",
    "4. four, Enforced by: ci:tests/a.py",
    "4. four: Enforced by: ci:tests/a.py",
    "4. four (Enforced by: ci:tests/a.py)",
])
def test_parser_suffix_after_dash_comma_colon_or_open_paren(line):
    assert _doc().architecture_rules(_ns(line))[0][2] == ["ci:tests/a.py"]


def test_parser_word_before_enforced_by_is_still_prose():
    assert _doc().architecture_rules(_ns("4. this is enforced by: convention"))[0][2] is None


def test_spec_surrounding_backticks_are_stripped():
    assert _doc()._arch_parse_spec("`ci:tests/a.py`")[:2] == ("ci", "tests/a.py")
    assert _doc()._arch_parse_spec("`review`")[0] == "review"


def test_spec_trailing_slash_is_a_directory():
    assert "is a directory, name the file" in _doc()._arch_parse_spec("ci:tests/")[2]


def test_report_import_failure_is_a_typed_note_not_a_raise(tmp_path, monkeypatch):
    m = _doc()

    def boom(name): raise ImportError("no north_star here")
    monkeypatch.setattr(m, "_load_loop_script", boom)
    results, note = m.architecture_report(_repo(tmp_path, _ns("1. A.")))
    assert results == [] and "no north_star here" in note


# spec parser: (kind, value, problem)

def test_spec_review_keyword_is_case_insensitive():
    assert _doc()._arch_parse_spec("ReViEw") == ("review", None, None)


def test_spec_ci_keyword_is_case_insensitive():
    assert _doc()._arch_parse_spec("CI:tests/a.py")[:2] == ("ci", "tests/a.py")


def test_spec_ci_double_colon_checks_only_the_file_part():
    assert _doc()._arch_parse_spec("ci:tests/a.py::test_x")[:2] == ("ci", "tests/a.py")


def test_spec_ci_empty_path_is_malformed():
    kind, _, why = _doc()._arch_parse_spec("ci:")
    assert why and "no path" in why


def test_spec_decide_with_whitespace_is_malformed():
    _, _, why = _doc()._arch_parse_spec("decide: INV-1")
    assert why and "single token" in why


def test_spec_decide_empty_is_malformed():
    assert _doc()._arch_parse_spec("decide:")[2]


def test_spec_unknown_word_is_malformed():
    _, _, why = _doc()._arch_parse_spec("trust-me")
    assert why and "trust-me" in why


def test_spec_absolute_path_refused_with_its_own_reason():
    assert "not repo-relative" in _doc()._arch_parse_spec("ci:/etc/hosts")[2]


def test_spec_windows_drive_path_refused_as_not_repo_relative():
    assert "not repo-relative" in _doc()._arch_parse_spec("ci:C:/x.py")[2]


def test_spec_tilde_refused_with_its_own_reason():
    why = _doc()._arch_parse_spec("ci:~/x.py")[2]
    assert "not repo-relative" in why and "~" in why


def test_spec_backslash_refused_with_its_own_reason():
    why = _doc()._arch_parse_spec("ci:tests\\x.py")[2]
    assert "backslash" in why


def test_spec_nul_refused_with_its_own_reason():
    why = _doc()._arch_parse_spec("ci:tests/x\0.py")[2]
    assert "NUL" in why


# ------------------------------------------------------------------ Task 2: bounded readers

def _fake_sysconf(m, monkeypatch, pages=None, size=None, exc=None):
    real = os.sysconf

    def fake(name):
        if name == "SC_PHYS_PAGES":
            if exc: raise exc
            return pages
        if name == "SC_PAGE_SIZE":
            return size
        return real(name)
    monkeypatch.setattr(os, "sysconf", fake)


KiB = 1024


def test_ceiling_is_phys_over_4096_when_inside_the_clamp(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, pages=524288, size=4096)   # 2 GiB
    assert m._arch_read_ceiling() == 512 * KiB


def test_ceiling_is_floored_at_64_kib(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, pages=16, size=4096)
    assert m._arch_read_ceiling() == 64 * KiB


def test_ceiling_is_capped_at_8_mib(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, pages=2 ** 26, size=4096)   # 256 GiB
    assert m._arch_read_ceiling() == 8 * KiB * KiB


def test_ceiling_none_when_sysconf_raises_oserror(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, exc=OSError("no"))
    assert m._arch_read_ceiling() is None


def test_ceiling_none_when_sysconf_raises_valueerror(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, exc=ValueError("no"))
    assert m._arch_read_ceiling() is None


def test_ceiling_none_when_pages_is_not_positive(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, pages=-1, size=4096)
    assert m._arch_read_ceiling() is None


def test_ceiling_none_when_page_size_is_not_positive(monkeypatch):
    m = _doc(); _fake_sysconf(m, monkeypatch, pages=524288, size=0)
    assert m._arch_read_ceiling() is None


def test_ceiling_on_this_host_is_inside_the_clamp():
    assert 64 * KiB <= _doc()._arch_read_ceiling() <= 8 * KiB * KiB


def test_read_text_normal(tmp_path):
    f = tmp_path / "a.md"; f.write_text("hello")
    assert _doc()._arch_read_text(f) == ("hello", None)


def test_read_text_refuses_when_ceiling_is_underivable(tmp_path, monkeypatch):
    m = _doc(); f = tmp_path / "a.md"; f.write_text("hello")
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: None)
    text, why = m._arch_read_text(f)
    assert text is None and why.startswith("unreadable: cannot derive a read limit")


def test_read_text_refuses_an_oversize_file_naming_ceiling_and_size(tmp_path, monkeypatch):
    m = _doc(); f = tmp_path / "a.md"; f.write_text("x" * 5000)
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: 1000)
    text, why = m._arch_read_text(f)
    assert text is None and "exceeds the derived read limit" in why and "1000" in why and "5000" in why


def test_read_text_bounds_a_file_that_grows_after_the_stat(tmp_path, monkeypatch):
    """The stat says small, the file is big: only `read(ceiling+1)` stops it."""
    m = _doc(); f = tmp_path / "a.md"; f.write_text("x" * 5000)
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: 1000)
    real = os.stat

    def small(p, *a, **k):
        r = real(p, *a, **k)
        if os.fspath(p) == os.fspath(f):
            vals = list(r)
            vals[6] = 10
            return os.stat_result(vals)
        return r
    monkeypatch.setattr(os, "stat", small)
    text, why = m._arch_read_text(f)
    assert text is None and "exceeds the derived read limit" in why and "1000" in why


def test_read_text_never_asks_the_file_for_more_than_ceiling_plus_one(tmp_path, monkeypatch):
    """The memory bound itself: a refused oversize file is still only ever read `ceiling+1` bytes deep.
    (An unbounded `read()` then a length check refuses identically, so only the call argument shows it.)"""
    m = _doc(); f = tmp_path / "a.md"; f.write_text("x" * 5000)
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: 1000)
    real = os.stat

    def small(p, *a, **k):
        r = real(p, *a, **k)
        if os.fspath(p) == os.fspath(f):
            vals = list(r); vals[6] = 10
            return os.stat_result(vals)
        return r
    monkeypatch.setattr(os, "stat", small)
    sizes, real_fdopen = [], os.fdopen

    class Spy:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): return self
        def __exit__(self, *a): self.fh.close()
        def fileno(self): return self.fh.fileno()
        def read(self, n=-1):
            sizes.append(n); return self.fh.read(n)
    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: Spy(real_fdopen(fd, *a, **k)))
    text, why = m._arch_read_text(f)
    assert text is None and sizes == [1001]


def test_read_text_refuses_a_directory(tmp_path):
    text, why = _doc()._arch_read_text(tmp_path)
    assert text is None and "not a regular file" in why


def test_read_text_refuses_a_fifo_promptly(tmp_path):
    """A hang is a failure, not a stall: the probe runs under a timeout."""
    fifo = tmp_path / "f"; os.mkfifo(fifo)
    code = ("import importlib.util,sys\n"
            "s=importlib.util.spec_from_file_location('doctor',%r);m=importlib.util.module_from_spec(s);"
            "s.loader.exec_module(m)\nprint(m._arch_read_text(%r))\n" % (str(D), str(fifo)))
    try:
        run = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pytest.fail("reading a FIFO hung instead of being refused")
    assert "not a regular file" in run.stdout, (run.stdout, run.stderr)


def test_read_text_refuses_a_file_swapped_for_a_fifo_after_the_stat(tmp_path, monkeypatch):
    """TOCTOU seam: the path stat'd as a regular file but is a FIFO by the time it is opened. Only the
    post-open fstat catches it; a SIGALRM turns a hang into a failure."""
    import signal, stat as st_mod
    m = _doc(); fifo = tmp_path / "f"; os.mkfifo(fifo)
    real = os.stat

    def liar(p, *a, **k):
        r = real(p, *a, **k)
        if os.fspath(p) == os.fspath(fifo):
            vals = list(r); vals[0] = st_mod.S_IFREG | 0o644
            return os.stat_result(vals)
        return r
    monkeypatch.setattr(os, "stat", liar)

    def boom(*_):
        raise AssertionError("reading the swapped FIFO hung")
    old = signal.signal(signal.SIGALRM, boom); signal.alarm(10)
    try:
        text, why = m._arch_read_text(fifo)
    finally:
        signal.alarm(0); signal.signal(signal.SIGALRM, old)
    assert text is None and "not a regular file" in why


def test_read_text_missing_file_is_typed_not_raised(tmp_path):
    text, why = _doc()._arch_read_text(tmp_path / "nope.md")
    assert text is None and why.startswith("unreadable:")


def test_read_text_invalid_utf8_is_typed(tmp_path):
    f = tmp_path / "a.md"; f.write_bytes(b"\xff\xfe\x00bad")
    text, why = _doc()._arch_read_text(f)
    assert text is None and "not valid UTF-8" in why


def test_decisions_missing_registry(tmp_path):
    assert _doc()._arch_load_decisions(tmp_path / "decisions.json") == (None, "registry missing")


def test_decisions_malformed_json(tmp_path):
    f = tmp_path / "d.json"; f.write_text("{nope")
    got, why = _doc()._arch_load_decisions(f)
    assert got is None and why.startswith("registry unreadable:")


def test_decisions_top_level_list_is_unreadable(tmp_path):
    f = tmp_path / "d.json"; f.write_text("[]")
    got, why = _doc()._arch_load_decisions(f)
    assert got is None and why.startswith("registry unreadable:")


def test_decisions_oversize_is_unreadable(tmp_path, monkeypatch):
    m = _doc(); f = tmp_path / "d.json"; f.write_text(json.dumps({"decisions": [], "pad": "x" * 5000}))
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: 1000)
    got, why = m._arch_load_decisions(f)
    assert got is None and why.startswith("registry unreadable:") and "derived read limit" in why


def test_decisions_status_absent_is_active_superseded_is_not(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps(_reg({"id": "A"}, {"id": "B", "status": "active"},
                                 {"id": "C", "status": "superseded"}, "junk", 7)))
    got, why = _doc()._arch_load_decisions(f)
    assert why is None and got == {"A": "active", "B": "active", "C": "superseded"}


def test_decisions_int_ids_are_normalised_to_str(tmp_path):
    f = tmp_path / "d.json"; f.write_text(json.dumps(_reg({"id": 9})))
    assert _doc()._arch_load_decisions(f)[0] == {"9": "active"}


def test_decisions_duplicate_id_is_active_if_any_entry_is(tmp_path):
    f = tmp_path / "d.json"
    f.write_text(json.dumps(_reg({"id": "A", "status": "superseded"}, {"id": "A"},
                                 {"id": "C"}, {"id": "C", "status": "superseded"},
                                 {"id": "B", "status": "superseded"}, {"id": "B", "status": "superseded"})))
    got = _doc()._arch_load_decisions(f)[0]       # A: active last; C: active FIRST (last-wins would lose it)
    assert got["A"] == "active" and got["C"] == "active" and got["B"] == "superseded"


def test_decisions_entry_without_an_id_is_ignored(tmp_path):
    f = tmp_path / "d.json"; f.write_text(json.dumps(_reg({"status": "active"}, {"id": ""}, {"id": "A"})))
    assert _doc()._arch_load_decisions(f)[0] == {"A": "active"}


def test_decisions_null_status_is_inactive_like_the_gate(tmp_path):
    """Known: the hook reads `d.get("status", "active") != "active"`, so a null/non-string status is inactive."""
    f = tmp_path / "d.json"; f.write_text(json.dumps(_reg({"id": "A", "status": None}, {"id": "B", "status": 3})))
    got = _doc()._arch_load_decisions(f)[0]
    assert got["A"] != "active" and got["B"] != "active"


def test_decisions_active_meaning_agrees_with_the_gate_state(tmp_path):
    """Pinning: the new reader and `_decision_gate_state` count the same entries active (distinct ids)."""
    m = _doc()
    entries = _reg({"id": "A", "class": "invariant"}, {"id": "B", "status": "active", "class": "recipe"},
                   {"id": "C", "status": "superseded", "class": "invariant"},
                   {"id": "D", "status": None, "class": "recipe"}, "junk",
                   {"id": "E", "class": "recipe"})
    base = pathlib.Path(_repo(tmp_path, decisions=entries))
    got, _ = m._arch_load_decisions(base / "decisions.json")
    mine = sum(1 for s in got.values() if s == "active")
    state = m._decision_gate_state(base, {})
    assert state.startswith("ON")
    assert mine == sum(int(n) for n in re.findall(r"\d+", state)) == 3


# ------------------------------------------------------------------ Task 3: the classifier

def _classify(spec, root, decisions=None, reason=None):
    m = _doc()
    rules = [(4, "A rule.", [spec])]
    (r,) = m.check_rule_enforcement(rules, root, decisions, reason)
    return r


def _outside(tmp_path):
    """A real file OUTSIDE the repo root, so a containment break is not masked by 'not found'."""
    root = tmp_path / "repo"; root.mkdir()
    out = tmp_path / "outside"; out.mkdir()
    (out / "secret.py").write_text("x")
    (root / "tests").mkdir(); (root / "tests" / "t.py").write_text("x")
    return root, out


def test_ci_existing_file_is_enforced(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("ci:tests/t.py", root)["cls"] == "enforced"


def test_ci_missing_file_is_broken_naming_rule_and_path(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("ci:tests/gone.py", root)
    assert r["cls"] == "broken" and r["n"] == 4
    assert "tests/gone.py" in r["problems"][0] and "file not found" in r["problems"][0]


def test_ci_directory_is_broken_with_its_own_reason(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("ci:tests", root)
    assert r["cls"] == "broken" and "is a directory, name the file" in r["problems"][0]


def test_ci_dotdot_escape_is_broken_as_escape_not_as_missing(tmp_path):
    root, out = _outside(tmp_path)
    r = _classify("ci:../outside/secret.py", root)
    assert r["cls"] == "broken" and "escapes the repository" in r["problems"][0]


def test_ci_dotdot_escape_to_a_directory_still_reads_as_escape(tmp_path):
    """ORDER control: containment is checked before any stat of the target."""
    root, out = _outside(tmp_path)
    r = _classify("ci:../outside", root)
    assert "escapes the repository" in r["problems"][0]


def test_ci_absolute_path_inside_the_repo_is_refused_as_not_repo_relative(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("ci:" + str(root / "tests" / "t.py"), root)
    assert r["cls"] == "broken" and "not repo-relative" in r["problems"][0]


def test_ci_symlink_out_of_the_repo_is_broken_as_escape(tmp_path):
    root, out = _outside(tmp_path)
    os.symlink(out / "secret.py", root / "tests" / "link.py")
    r = _classify("ci:tests/link.py", root)
    assert r["cls"] == "broken" and "escapes the repository" in r["problems"][0]


def test_ci_symlink_inside_the_repo_is_fine(tmp_path):
    root, _ = _outside(tmp_path)
    os.symlink(root / "tests" / "t.py", root / "tests" / "alias.py")
    assert _classify("ci:tests/alias.py", root)["cls"] == "enforced"


def test_ci_symlink_loop_is_typed_and_never_raises(tmp_path):
    root, _ = _outside(tmp_path)
    os.symlink(root / "tests" / "b", root / "tests" / "a"); os.symlink(root / "tests" / "a", root / "tests" / "b")
    r = _classify("ci:tests/a", root)
    assert r["cls"] == "broken" and r["problems"]


def test_ci_overlong_path_is_typed_and_never_raises(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("ci:" + "a" * 5000, root)
    assert r["cls"] == "broken" and r["problems"]


def test_ci_double_colon_accepted_when_the_file_exists(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("ci:tests/t.py::test_x", root)["cls"] == "enforced"


def test_ci_double_colon_broken_when_the_file_is_gone(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("ci:tests/gone.py::test_x", root)
    assert r["cls"] == "broken" and "tests/gone.py" in r["problems"][0]


def test_ci_empty_path_is_broken(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("ci:", root)["cls"] == "broken"


def test_decide_active_is_enforced(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("decide:A", root, {"A": "active"})["cls"] == "enforced"


def test_decide_id_absent_is_broken_naming_the_id(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("decide:INV-009", root, {"A": "active"})
    assert r["cls"] == "broken" and "INV-009" in r["problems"][0] and "not in the registry" in r["problems"][0]


def test_decide_superseded_is_broken_naming_the_status(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("decide:INV-001", root, {"INV-001": "superseded"})
    assert r["cls"] == "broken" and "superseded" in r["problems"][0] and "INV-001" in r["problems"][0]


def test_decide_status_absent_default_active_stays_enforced(tmp_path):
    f = tmp_path / "d.json"; f.write_text(json.dumps(_reg({"id": "A"})))
    root, _ = _outside(tmp_path)
    got, _ = _doc()._arch_load_decisions(f)
    assert _classify("decide:A", root, got)["cls"] == "enforced"


def test_decide_registry_missing_is_broken_and_says_so(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("decide:A", root, None, "registry missing")
    assert r["cls"] == "broken" and "registry missing" in r["problems"][0]


def test_decide_registry_unreadable_is_broken_and_says_why(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("decide:A", root, None, "registry unreadable: boom")
    assert r["cls"] == "broken" and "registry unreadable: boom" in r["problems"][0]


def test_decide_int_id_matches_the_str_spec(tmp_path):
    f = tmp_path / "d.json"; f.write_text(json.dumps(_reg({"id": 9})))
    root, _ = _outside(tmp_path)
    got, _ = _doc()._arch_load_decisions(f)
    assert _classify("decide:9", root, got)["cls"] == "enforced"


def test_unknown_spec_word_is_broken_not_untagged(tmp_path):
    root, _ = _outside(tmp_path)
    r = _classify("trust-me", root)
    assert r["cls"] == "broken" and "trust-me" in r["problems"][0]


def test_decide_with_a_space_is_broken(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("decide: A", root, {"A": "active"})["cls"] == "broken"


def test_a_good_spec_does_not_rescue_a_bad_one(tmp_path):
    root, _ = _outside(tmp_path)
    (r,) = _doc().check_rule_enforcement([(1, "R.", ["ci:tests/t.py", "ci:tests/gone.py"])], root, None)
    assert r["cls"] == "broken"


def test_review_only_rule(tmp_path):
    root, _ = _outside(tmp_path)
    assert _classify("review", root)["cls"] == "review-only"


def test_review_plus_a_good_ci_is_enforced(tmp_path):
    root, _ = _outside(tmp_path)
    (r,) = _doc().check_rule_enforcement([(1, "R.", ["review", "ci:tests/t.py"])], root, None)
    assert r["cls"] == "enforced"


def test_untagged_rule(tmp_path):
    root, _ = _outside(tmp_path)
    (r,) = _doc().check_rule_enforcement([(1, "R.", None)], root, None)
    assert r["cls"] == "untagged" and r["problems"] == []


# ------------------------------------------------------------------ Task 4: report, verb, row, advisory


def _patch_north_star(m, monkeypatch, **overrides):
    """Wrap `_load_loop_script("north_star")` so selected functions are replaced; the rest stay real."""
    real_load = m._load_loop_script

    def load(name):
        mod = real_load(name)
        if name == "north_star":
            for k, v in overrides.items():
                setattr(mod, k, v)
        return mod
    monkeypatch.setattr(m, "_load_loop_script", load)

def _rules_run(capsys, sdlc, *extra):
    rc = _doc().main(["doctor.py", "rules", sdlc, *extra])
    return rc, capsys.readouterr().out


def _check_rows(sdlc, **kw):
    m = _doc()
    return {c["name"]: c for c in m.check(sdlc, run=lambda a: "", **kw)}


def test_rules_lists_untagged_and_review_only_and_exits_zero(tmp_path, capsys):
    sdlc = _repo(tmp_path, _ns("1. Layers hold.\n2. Tests exist. Enforced by: review"))
    rc, out = _rules_run(capsys, sdlc)
    assert rc == 0
    assert "rule 1" in out and "Layers hold." in out and "untagged" in out
    assert "rule 2" in out and "review-only" in out
    assert "1 untagged" in out and "1 review-only" in out
    assert "exists, not proof CI runs it or that it covers the rule" in out
    assert "untracked or gitignored file satisfies `ci:`" in out
    assert "case-insensitive filesystem" in out


def test_rules_broken_rule_exits_one_naming_number_and_path(tmp_path, capsys):
    sdlc = _repo(tmp_path, _ns("1. Layers hold. Enforced by: ci:tests/test_gone.py"))
    rc, out = _rules_run(capsys, sdlc)
    assert rc == 1 and "rule 1" in out and "tests/test_gone.py" in out and "broken" in out


def test_rules_enforced_rule_is_green(tmp_path, capsys):
    sdlc = _repo(tmp_path, _ns("1. Layers hold. Enforced by: ci:tests/t.py"), files=["tests/t.py"])
    rc, out = _rules_run(capsys, sdlc)
    assert rc == 0 and "1 enforced" in out


def test_rules_decide_superseded_and_absent_and_missing_registry(tmp_path, capsys):
    text = _ns("1. A. Enforced by: decide:OLD\n2. B. Enforced by: decide:NOPE")
    sdlc = _repo(tmp_path, text, decisions=_reg({"id": "OLD", "status": "superseded"}))
    rc, out = _rules_run(capsys, sdlc)
    assert rc == 1 and "OLD" in out and "superseded" in out and "NOPE" in out
    with tempfile.TemporaryDirectory() as d2:
        sdlc2 = _repo(d2, text)
        rc, out = _rules_run(capsys, sdlc2)
        assert rc == 1 and "registry missing" in out


def test_rules_with_no_north_star_or_section_is_silent_zero(tmp_path, capsys):
    rc, out = _rules_run(capsys, _repo(tmp_path))
    assert rc == 0 and "no architecture rules" in out
    with tempfile.TemporaryDirectory() as d2:
        rc, out = _rules_run(capsys, _repo(d2, "# N\n\n## Vision\nx\n"))
        assert rc == 0 and "no architecture rules" in out


def test_check_has_a_row_only_when_a_rule_is_broken(tmp_path):
    sdlc = _repo(tmp_path, _ns("1. A. Enforced by: ci:tests/test_gone.py"))
    row = _check_rows(sdlc)["architecture rule enforcement"]
    assert row["ok"] is False and "tests/test_gone.py" in row["fix"] and "doctor.py rules" in row["fix"]


def test_check_row_lists_the_first_three_broken_rules_then_points_at_rules(tmp_path):
    body = "\n".join("%d. R%d. Enforced by: ci:tests/g%d.py" % (i, i, i) for i in range(1, 6))
    row = _check_rows(_repo(tmp_path, _ns(body)))["architecture rule enforcement"]
    assert "g1.py" in row["fix"] and "g3.py" in row["fix"] and "g4.py" not in row["fix"]
    assert "doctor.py rules" in row["fix"]


@pytest.mark.parametrize("ns", [
    None,                                               # absent north-star
    "# N\n\n## Vision\nx\n",                            # no Architecture section
    "## Architecture\n1. Untagged.\n2. Rev. Enforced by: review\n",   # advisory only
])
def test_check_adds_no_row_and_no_denominator_change_when_nothing_is_broken(tmp_path, ns):
    with tempfile.TemporaryDirectory() as d2:
        base = _repo(d2)                                  # same fixture, no architecture content at all
        before = len(_doc().check(base, run=lambda a: ""))
    sdlc = _repo(tmp_path, ns)
    rows = _doc().check(sdlc, run=lambda a: "")
    assert "architecture rule enforcement" not in {c["name"] for c in rows}
    delta = len(rows) - before
    assert delta in (0, 1)                                # the existing "north-star filled" row may appear


def test_check_row_is_skipped_under_cheap_only(tmp_path):
    sdlc = _repo(tmp_path, _ns("1. A. Enforced by: ci:tests/test_gone.py"))
    rows = {c["name"] for c in _doc().check(sdlc, run=lambda a: "", cheap_only=True)}
    assert "architecture rule enforcement" not in rows


def test_check_row_fires_for_a_goal_worktree_that_has_no_local_context(tmp_path, monkeypatch):
    """The north-star and registry live in the OWNING checkout; the worktree's own .sdlc has neither."""
    m = _doc()
    owner = tmp_path / "owner"; owner.mkdir()
    _repo(owner, _ns("1. A. Enforced by: ci:tests/test_gone.py"))
    wt = tmp_path / "wt"; (wt / ".sdlc").mkdir(parents=True)
    (wt / ".sdlc" / "config.json").write_text('{"gates": {"phase_record": {"enabled": true}}}')
    _patch_north_star(m, monkeypatch, project_root=lambda start: (owner, None), notice=lambda *a, **k: None)
    row = {c["name"]: c for c in m.check(str(wt / ".sdlc"), run=lambda a: "")}["architecture rule enforcement"]
    assert row["ok"] is False and "tests/test_gone.py" in row["fix"]


def test_ci_paths_resolve_against_the_checkout_given_not_the_owner(tmp_path, monkeypatch):
    """Registry/north-star come from the owner, tracked files from the worktree under check."""
    m = _doc()
    owner = tmp_path / "owner"; owner.mkdir()
    _repo(owner, _ns("1. A. Enforced by: ci:tests/t.py"))
    wt = tmp_path / "wt"; _repo(wt, files=["tests/t.py"])
    _patch_north_star(m, monkeypatch, project_root=lambda start: (owner, None), notice=lambda *a, **k: None)
    results, note = m.architecture_report(str(wt / ".sdlc"))
    assert note is None and results[0]["cls"] == "enforced"


def test_report_unreachable_root_is_silent(tmp_path, monkeypatch):
    m = _doc()
    _patch_north_star(m, monkeypatch, project_root=lambda s: (None, "git down"))
    assert m.architecture_report(_repo(tmp_path, _ns("1. A."))) == ([], None)


def test_report_computes_the_project_root_once_per_call(tmp_path, monkeypatch):
    m = _doc(); calls = []
    ns_mod = m._load_loop_script("north_star")

    def pr(start):
        calls.append(start); return ns_mod.project_root(start)
    _patch_north_star(m, monkeypatch, project_root=pr)
    m.architecture_report(_repo(tmp_path, _ns("1. A.")))
    assert len(calls) == 1


def test_main_check_computes_the_report_once(tmp_path, monkeypatch, capsys):
    m = _doc(); n = []
    real = m.architecture_report
    monkeypatch.setattr(m, "architecture_report", lambda *a, **k: (n.append(1), real(*a, **k))[1])
    monkeypatch.setattr(m, "features", lambda *a, **k: [])
    sdlc = _repo(tmp_path, _ns("1. A. Enforced by: ci:tests/gone.py"))
    monkeypatch.setattr(m, "_runner_default", None, raising=False)
    orig_check = m.check
    monkeypatch.setattr(m, "check", lambda *a, **k: orig_check(*a, run=lambda args: "", **k))
    m.main(["doctor.py", "check", sdlc])
    assert len(n) == 1


def test_advisory_caps_at_twenty_then_says_how_many_more_and_rules_prints_all(tmp_path, capsys, monkeypatch):
    body = "\n".join("%d. Untagged rule number %d." % (i, i) for i in range(1, 26))
    sdlc = _repo(tmp_path, _ns(body))
    m = _doc()
    m._print_arch_advisory(m.architecture_report(sdlc))
    out = capsys.readouterr().out
    assert "rule 20" in out and "rule 21" not in out and "and 5 more (doctor.py rules for all)" in out
    rc, out = _rules_run(capsys, sdlc)
    assert "rule 25" in out and "more (doctor.py rules for all)" not in out and rc == 0


def test_advisory_is_silent_without_rules(tmp_path, capsys):
    m = _doc(); m._print_arch_advisory(m.architecture_report(_repo(tmp_path)))
    assert capsys.readouterr().out == ""


def test_advisory_lists_untagged_and_review_only_but_not_enforced(tmp_path, capsys):
    sdlc = _repo(tmp_path, _ns("1. Plain.\n2. Rev. Enforced by: review\n3. Good. Enforced by: ci:tests/t.py"),
                 files=["tests/t.py"])
    m = _doc(); m._print_arch_advisory(m.architecture_report(sdlc))
    out = capsys.readouterr().out
    assert "rule 1" in out and "rule 2" in out and "rule 3" not in out


def test_unreadable_north_star_prints_the_could_not_be_read_line_and_rules_exits_one(tmp_path, capsys, monkeypatch):
    m = _doc()
    sdlc = _repo(tmp_path, _ns("1. A rule.") + "x" * 5000)
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: 1000)
    m._print_arch_advisory(m.architecture_report(sdlc))
    assert "architecture rules could not be read: " in capsys.readouterr().out
    rc = m.main(["doctor.py", "rules", sdlc])
    out = capsys.readouterr().out
    assert rc == 1 and "architecture rules could not be read: " in out and "derived read limit" in out


def test_underivable_ceiling_prints_the_line_for_a_present_north_star(tmp_path, capsys, monkeypatch):
    m = _doc()
    sdlc = _repo(tmp_path, _ns("1. A rule."))
    monkeypatch.setattr(m, "_arch_read_ceiling", lambda: None)
    rc = m.main(["doctor.py", "rules", sdlc])
    out = capsys.readouterr().out
    assert rc == 1 and "architecture rules could not be read: unreadable: cannot derive a read limit" in out


def test_absent_north_star_never_prints_the_could_not_be_read_line(tmp_path, capsys, monkeypatch):
    m = _doc(); monkeypatch.setattr(m, "_arch_read_ceiling", lambda: None)
    m._print_arch_advisory(m.architecture_report(_repo(tmp_path)))
    assert capsys.readouterr().out == ""


def test_symlinked_sdlc_is_refused_not_guessed(tmp_path, capsys):
    real = tmp_path / "real"; real.mkdir()
    _repo(real, _ns("1. A. Enforced by: ci:tests/t.py"), files=["tests/t.py"])
    proj = tmp_path / "proj"; proj.mkdir()
    os.symlink(real / ".sdlc", proj / ".sdlc")
    rc = _doc().main(["doctor.py", "rules", str(proj / ".sdlc")])
    out = capsys.readouterr().out
    assert rc == 1 and "refused: .sdlc is a symlink" in out


def test_usage_names_the_rules_verb(capsys):
    assert _doc().main(["doctor.py", "--help"]) == 0
    assert re.search(r"\brules\b", capsys.readouterr().out)


def test_rule_text_is_sanitised_before_it_reaches_the_console(tmp_path, capsys):
    sdlc = _repo(tmp_path, _ns("1. Bad \x1b[31mred\x1b[0m rule."))
    _, out = _rules_run(capsys, sdlc)
    assert "\x1b" not in out


# ------------------------------------------------------------------ Task 5: the documented gestures (AC-4)

def _documented(text, verb):
    """The exact documented command line for `doctor.py <verb> .sdlc`, or None when it is not there."""
    m = re.search(r'^[ \t]*`?(python3 "\$\{CLAUDE_SKILL_DIR\}/scripts/doctor\.py" %s \.sdlc)`?[ \t]*$' % verb,
                  text, re.M)
    return m.group(1) if m else None


def _run_documented(cmd, cwd, home, cfg):
    argv = shlex.split(cmd.replace("${CLAUDE_SKILL_DIR}", str(SKILL_DIR)))
    assert argv[0] == "python3"
    argv[0] = sys.executable
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "CLAUDE_CONFIG_DIR": str(cfg),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
                          stdin=subprocess.DEVNULL)


def test_documented_gesture_lines_are_found_in_skill_md():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert _documented(text, "check") and _documented(text, "rules")


def test_documented_gesture_regex_goes_red_on_a_wrong_or_missing_line():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert _documented(text.replace("doctor.py\" rules .sdlc", "doctor.py\" rulez .sdlc"), "rules") is None
    assert _documented(text.replace("doctor.py\" check .sdlc", "doctor.py\" chek .sdlc"), "check") is None


def test_documented_gestures_red_then_green_on_a_deleted_ci_path(tmp_path):
    text = SKILL_MD.read_text(encoding="utf-8")
    check_cmd, rules_cmd = _documented(text, "check"), _documented(text, "rules")
    assert check_cmd and rules_cmd
    home = tmp_path / "home"; home.mkdir(); cfg = tmp_path / "cfg"; cfg.mkdir()
    repo = tmp_path / "repo"; repo.mkdir()
    _repo(repo, _ns("1. Layers hold. Enforced by: ci:tests/test_gone.py"))
    red_check = _run_documented(check_cmd, repo, home, cfg)
    red_rules = _run_documented(rules_cmd, repo, home, cfg)
    assert "[MISSING] architecture rule enforcement" in red_check.stdout, (red_check.stdout, red_check.stderr)
    assert "tests/test_gone.py" in red_check.stdout
    assert red_rules.returncode == 1 and "tests/test_gone.py" in red_rules.stdout, (red_rules.stdout, red_rules.stderr)
    (repo / "tests").mkdir(); (repo / "tests" / "test_gone.py").write_text("x = 1\n")
    green_check = _run_documented(check_cmd, repo, home, cfg)
    green_rules = _run_documented(rules_cmd, repo, home, cfg)
    assert "architecture rule enforcement" not in green_check.stdout, green_check.stdout
    assert green_rules.returncode == 0, (green_rules.stdout, green_rules.stderr)
