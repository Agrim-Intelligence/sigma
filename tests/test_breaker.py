"""Tests for skills/sigma-loop/scripts/breaker.py (issue #1936).

The isolation guarantee is asserted STRUCTURALLY here, not in prose -- #1936 requires "no channel
exists between maker and breaker: verified by construction (spawn topology + no shared scratch
path), not by instructing the maker not to use one", because in this repo policy alone has already
failed once.
"""
import importlib.util
import pathlib

S = pathlib.Path(__file__).parent.parent / "skills" / "sigma-loop" / "scripts"


def _b():
    spec = importlib.util.spec_from_file_location("breaker", S / "breaker.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


SHARP = """## Problem
whatever
## done_when
- [ ] `is_valid_age` returns False for a negative age
- [ ] returns False for an age greater than 120
## judged_when
- [ ] the boundary at 0 and at 120 is covered
"""

VAGUE = """## done_when
- [ ] add age validation
- [ ] improve the handling
"""


# --- reading the acceptance list ---------------------------------------------------------------

def test_acceptance_reads_both_done_when_and_judged_when_in_order():
    items = _b().acceptance(SHARP)
    assert len(items) == 3
    assert items[0].startswith("`is_valid_age` returns False")
    assert "boundary" in items[-1]


def test_acceptance_stops_at_the_next_heading_and_ignores_other_sections():
    assert all("whatever" not in i for i in _b().acceptance(SHARP))


def test_acceptance_is_empty_when_the_issue_has_no_list():
    assert _b().acceptance("## Problem\njust prose\n") == []


# --- a vague list is a PLAN-phase defect, not a test failure -----------------------------------

def test_a_sharp_list_is_actionable():
    ok, _ = _b().actionable(_b().acceptance(SHARP))
    assert ok is True


def test_CONTROL_a_vague_list_is_reported_as_a_PLAN_phase_defect():
    """done_when 3. "Give the breaker only the requirement" assumes the requirement is precise.
    "Add age validation" gives a breaker nothing to probe -- it will either invent noise or go
    looking at the implementation, which is the independence gone. The finding belongs against the
    GOAL at plan time, where it costs one sentence, not against an implementation that may be
    perfectly fine."""
    b = _b()
    ok, reason = b.actionable(b.acceptance(VAGUE))
    assert ok is False
    assert "PLAN-phase defect against the goal" in reason
    assert "not a test failure against the implementation" in reason


def test_no_list_at_all_is_also_a_plan_phase_defect():
    ok, reason = _b().actionable([])
    assert ok is False and "nothing for a breaker to attack" in reason


# --- isolation, by construction ----------------------------------------------------------------

def test_the_brief_has_NO_PARAMETER_for_the_implementation():
    """THE ISOLATION GUARANTEE, asserted against the signature itself rather than against
    behaviour. A maker cannot pass what the function cannot accept -- there is no `diff`,
    `implementation`, `source` or `notes` parameter to persuade anyone to fill in."""
    import inspect
    params = set(inspect.signature(_b().brief).parameters)
    assert params == {"issue_body", "interface"}, params
    assert not (params & {"diff", "implementation", "source", "notes", "maker", "scratch"})


def test_CONTROL_implementation_content_pasted_into_the_list_is_scrubbed_out():
    """The second line of defence. An acceptance list is HUMAN-EDITED text that can quote the code;
    pasting a function body into a done_when bullet would otherwise hand the breaker exactly what
    it must not see, through a channel nobody intended."""
    b = _b()
    leaky = SHARP + "- [ ] def is_valid_age(age): return 0 <= age <= 120\n"
    ok, text = b.brief(leaky, "is_valid_age(age) -> bool")
    assert ok is True
    assert "0 <= age <= 120" not in text
    assert "def is_valid_age" not in text


def test_CONTROL_a_maker_attempting_to_reach_the_breaker_has_no_available_channel():
    """judged_when 2, TESTED rather than asserted. A maker's diff, its notes, and a shared scratch
    path are each offered to the brief through every route available, and none of them arrives."""
    b = _b()
    makers_diff = ("diff --git a/src/age.py b/src/age.py\n"
                   "@@ -1 +1 @@\n"
                   "-def is_valid_age(age): return True\n"
                   "+def is_valid_age(age): return 0 <= age <= 120\n")
    scratch = "/tmp/shared-maker-scratch"
    ok, text = b.brief(SHARP + makers_diff + "\n- [ ] see %s for my notes\n" % scratch,
                       "is_valid_age(age) -> bool")
    assert ok is True
    clean, findings = b.no_channel(text, scratch_paths=[scratch])
    assert clean, findings
    assert "diff --git" not in text
    assert "return 0 <= age <= 120" not in text


def test_no_channel_DETECTS_a_leak_when_one_is_present():
    """The detector must be able to fail, or it is decoration. Fed a brief that genuinely contains
    implementation content and a scratch path, it reports both."""
    b = _b()
    dirty = "ACCEPTANCE:\n- ok\ndef is_valid_age(age):\nsee /tmp/shared for notes\n"
    clean, findings = b.no_channel(dirty, scratch_paths=["/tmp/shared"])
    assert clean is False
    assert len(findings) == 2


def test_the_brief_tells_the_breaker_it_has_not_seen_the_implementation():
    ok, text = _b().brief(SHARP, "is_valid_age(age) -> bool")
    assert ok and text.startswith("You have NOT seen the implementation")
    assert "is_valid_age(age) -> bool" in text


def test_a_vague_goal_never_produces_a_brief_at_all():
    """The plan-phase defect short-circuits: no breaker is dispatched against an unattackable
    goal, so the cost is not paid and the noise is not generated."""
    ok, reason = _b().brief(VAGUE, "whatever")
    assert ok is False and "PLAN-phase defect" in reason


# === #844: read the Done when heading, per-criterion verdicts, narrow scrub, output contract ====
#
# Every test below that needs a function that may not exist yet resolves it with `_need`, so on
# the unrepaired module it fails with a real AssertionError, never an AttributeError.

import ast
import sys


def _need(name):
    fn = getattr(_b(), name, None)
    assert fn is not None, "breaker.%s does not exist yet" % name
    return fn


def _acceptance_module():
    spec = importlib.util.spec_from_file_location("acceptance", S / "acceptance.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


DONE_WHEN = """## Problem
prose that is not a criterion
## Done when
- [ ] `is_valid_age` returns False for a negative age
- [ ] returns False for an age greater than 120
- [x] the boundary at 0 and at 120 is accepted
## Notes
- not a criterion either
"""

PROSE = [
    "Return 0 on success",
    "Returns the count of rejected rows",
    "return the empty list when no input is given",
    "Import of a file with a bad header is rejected",
    "The class of error is reported",
    "Returns None for an empty input",
    "If the input is empty, return None",
    "The CLI must return 0 on success",
    "It must import cleanly",
    "A class of errors is rejected",
    "The import step returns 3",
    "Exit status is 2 when the value is unset",
    "Return nothing",
    "imports nothing",
]

CODE = [
    "return result",
    "return None",
    "if x: return y",
    "import os, sys",
    "x = 1; import os",
    "return x",
    "    return foo(a)",
    "import os",
    "from a import b",
    "self.x = 1",
    "def f(a):",
]


def test_acceptance_reads_a_level_two_Done_when_heading():
    items = _b().acceptance(DONE_WHEN)
    assert len(items) == 3
    assert items[0].startswith("`is_valid_age` returns False")
    assert "boundary" in items[2]
    mixed = DONE_WHEN.replace("## Done when", "## DONE When")
    assert _b().acceptance(mixed) == items


def test_acceptance_matches_acceptance_criteria_and_does_not_double_count():
    strict = _acceptance_module().criteria(DONE_WHEN)
    assert strict is not None and len(strict) == 3
    assert _b().acceptance(DONE_WHEN) == strict
    both = DONE_WHEN + "## done_when\n- [ ] extra one must hold\n- [ ] extra two must hold\n"
    assert _b().acceptance(both) == strict + ["extra one must hold", "extra two must hold"]


def test_acceptance_fallback_accepts_checkbox_and_plain_bullets_and_never_raises():
    b = _b()
    two = "## Done when\n- [ ] first returns 0\n- second rejects a negative\n"
    assert b.acceptance(two) == ["first returns 0", "second rejects a negative"]
    star = "## Done when\n* [x] checked one\n* plain star\n"
    assert b.acceptance(star) == ["checked one", "plain star"]
    for odd in ("", None, "## Done when\nnot a bullet at all\n", "## Done when\n", "no heading"):
        assert b.acceptance(odd) == []
    # KNOWN GAP, pinned as current behaviour and not a promise: the lenient fallback does not skip
    # fenced code blocks, so a bullet inside a fenced example is read as a criterion.
    fenced = "## Done when\n- [ ] real one\n```\n- [ ] inside a fence\n```\n"
    assert b.acceptance(fenced) == ["real one", "inside a fence"]


def test_brief_works_on_a_real_Done_when_goal_body():
    ok, text = _b().brief(DONE_WHEN, "is_valid_age(age) -> bool")
    assert ok is True, text
    assert "returns False for a negative age" in text
    assert "is_valid_age(age) -> bool" in text


def test_criterion_verdicts_shape_order_and_scrubbed_text():
    verdicts_of = _need("criterion_verdicts")
    items = ["rejects a negative age",
             "add validation",
             "def f(a): return a <= 3",
             "add validation\n    self.x = 1"]
    verdicts = verdicts_of(items)
    assert len(verdicts) == 4
    for v in verdicts:
        assert set(v) == {"criterion", "actionable", "reason"}
        assert isinstance(v["actionable"], bool)
        assert (v["reason"] == "") == v["actionable"]
    assert verdicts[0] == {"criterion": "rejects a negative age", "actionable": True, "reason": ""}
    assert verdicts[1]["criterion"] == "add validation" and verdicts[1]["actionable"] is False
    assert verdicts[2]["criterion"] == "" and verdicts[2]["actionable"] is False
    assert "scrubbed to empty" in verdicts[2]["reason"]
    assert verdicts[3]["criterion"] == "add validation"
    assert "self.x" not in verdicts[3]["reason"] and "self.x" not in verdicts[3]["criterion"]
    assert "id" not in verdicts[0]


def test_brief_drops_a_scrubbed_to_empty_criterion_and_refuses_only_when_none_survive():
    _need("criterion_verdicts")
    b = _b()
    body = ("## done_when\n- [ ] def f(a): return a <= 3\n"
            "- [ ] rejects a negative age\n- [ ] returns False above 120\n")
    ok, text = b.brief(body, "f(a) -> bool")
    assert ok is True, text
    bullets = [ln for ln in text.splitlines() if ln.strip().startswith("*")]
    assert len(bullets) == 2 and all(ln.strip() != "*" for ln in bullets)
    assert "1 criteria withheld (scrubbed to empty)" in text
    assert "return a <= 3" not in text
    all_code = "## done_when\n- [ ] def f(a): return a <= 3\n- [ ] self.x = 1 is rejected\n"
    ok, reason = b.brief(all_code, "f(a)")
    assert ok is False
    assert "scrubbed to empty" in reason


def test_brief_refuses_a_mixed_scrubbed_and_vague_list():
    _need("criterion_verdicts")
    b = _b()
    mixed = "## done_when\n- [ ] def f(a): return a <= 3\n- [ ] add validation\n"
    ok, reason = b.brief(mixed, "f(a)")
    assert ok is False, reason
    assert "scrubbed to empty" in reason
    # advisory semantics: with one actionable survivor a vague neighbour is still listed
    advisory = "## done_when\n- [ ] add validation\n- [ ] rejects a negative age\n"
    ok, text = b.brief(advisory, "f(a)")
    assert ok is True
    assert "add validation" in text and "rejects a negative age" in text


def test_scrub_keeps_prose_and_removes_plain_code_lines():
    b = _b()
    for line in PROSE:
        assert b.scrub(line) == line, line
    for line in CODE:
        assert b.scrub(line) == "", line
    assert b.scrub("diff --git a/x b/x") == "" and b.scrub("+added") == ""
    assert b.scrub("see /srv/data/notes") == ""


def test_leak_heuristic_residual_gap_is_stated_and_pinned():
    _need("criterion_verdicts")
    b = _b()
    # Code lines that SURVIVE the scrub: the heuristic cannot tell them from prose.
    for survivor in ("x = 1", "result = compute(a)", "do_thing(a, b)", 'raise ValueError("x")',
                     "assert x == 1"):
        assert b.scrub(survivor) == survivor, survivor
    # Deliberate false positives: lowercase prose that looks like a return statement is blanked,
    # and criterion_verdicts reports it rather than losing it silently.
    blanked = ["return nothing", "if empty: return None", "return 0 when n > 0",
               "Returns the count of rows; return 1 on failure"]
    for line in blanked:
        assert b.scrub(line) == "", line
    for verdict in b.criterion_verdicts(blanked):
        assert verdict["criterion"] == "" and verdict["actionable"] is False
        assert "scrubbed to empty" in verdict["reason"]
    doc = b.__doc__ or ""
    assert "heuristic" in doc and "second line of defence" in doc


# --- the output contract and its validator -------------------------------------------------------

GOOD = "def test_ok():\n    assert 1 + 1 == 2\n"
DIR = "tests/acceptance/844/"


def _blk(name, src=GOOD, path=None):
    return "=== FILE: %s ===\n%s=== END FILE ===\n" % (path or DIR + name, src)


def _validate(text, stem="844"):
    return _need("validate_output")(text, stem)


def _stdlib(monkeypatch, names=("os", "sys", "re", "json")):
    monkeypatch.setattr(sys, "stdlib_module_names", frozenset(names), raising=False)


def test_validate_output_accepts_a_well_formed_block_a_lazy_import_and_crlf(monkeypatch):
    _stdlib(monkeypatch)
    ok, findings = _validate(_blk("test_acc_844_ok.py"))
    assert (ok, findings) == (True, [])
    lazy = "import os\n\n\ndef test_lazy():\n    import work\n    assert work and os\n"
    assert _validate(_blk("test_acc_844_lazy.py", lazy)) == (True, [])
    crlf = _blk("test_acc_844_crlf.py").replace("\n", "\r\n")
    assert _validate(crlf) == (True, [])
    two = _blk("test_acc_844_a.py") + "\n" + _blk("test_acc_844_b.py")
    assert _validate(two.encode("utf-8")) == (True, [])
    assert _validate(_blk("test_acc_a_b_ok.py", path="tests/acceptance/a-b/test_acc_a_b_ok.py"),
                     "a-b") == (True, [])


def test_validate_output_rejects_every_path_escape_shape():
    for path in ("tests/acceptance/844/../x/test_acc_844_x.py",
                 "tests/acceptance/../test_acc_844_x.py",
                 "/abs/tests/acceptance/844/test_acc_844_x.py",
                 "tests/acceptance/844/..\\test_acc_844_x.py",
                 "tests/acceptance/844/test_acc_844_x\x00.py"):
        ok, findings = _validate(_blk("x", path=path))
        assert ok is False and any("escape" in f for f in findings), (path, findings)
    for path in ("tests/other/844/test_acc_844_x.py", "tests/acceptance/845/test_acc_844_x.py",
                 "test_acc_844_x.py"):
        ok, findings = _validate(_blk("x", path=path))
        assert ok is False and any("outside" in f for f in findings), (path, findings)
    ok, findings = _validate(_blk("x", path=DIR + "sub/test_acc_844_x.py"))
    assert ok is False and any("nested" in f for f in findings), findings


def test_validate_output_requires_a_collectable_unique_stem_embedded_name():
    for name in ("helper.py", "test_x.py", "test_acc_845_x.py", "test_acc_844_.py",
                 "test_acc_844_a-b.py"):
        ok, findings = _validate(_blk(name))
        assert ok is False and any("name" in f for f in findings), (name, findings)
    ok, findings = _validate(_blk("test_acc_844_ok.py") + _blk("test_acc_844_ok.py"))
    assert ok is False and any("duplicate" in f for f in findings), findings
    ok, findings = _validate(_blk("test_acc_844_Ok.py") + _blk("test_acc_844_ok.py"))
    assert ok is False and any("duplicate" in f for f in findings), findings
    ok, findings = _validate(_blk("test_acc_844_ok.py"), "bad stem!")
    assert ok is False and any("stem" in f for f in findings), findings


def test_validate_output_rejects_a_non_python_block():
    for name in ("test_acc_844_x.txt", "test_acc_844_x.PY", "test_acc_844_x.pyi",
                 "test_acc_844_x"):
        ok, findings = _validate(_blk(name))
        assert ok is False and any(".py" in f for f in findings), (name, findings)
    ok, findings = _validate(_blk("test_acc_844_x.py", "This is prose, not Python.\n"))
    assert ok is False and any("parse" in f for f in findings), findings


def test_validate_output_rejects_a_syntax_error_and_pathological_nesting():
    ok, findings = _validate(_blk("test_acc_844_x.py", "def test_x(:\n    pass\n"))
    assert ok is False and any("parse" in f for f in findings), findings
    # A deep expression chain: 3.12 raises RecursionError/MemoryError from ast.parse, 3.9 parses it.
    # Either way the validator must return rather than raise; where the interpreter does raise,
    # the block must also be rejected with a parse finding.
    deep = "x = " + "1+" * 15000 + "1\n"
    try:
        ast.parse(deep)
        pathological = False
    except (RecursionError, MemoryError):
        pathological = True
    ok, findings = _validate(_blk("test_acc_844_y.py", deep + GOOD))
    assert isinstance(ok, bool) and isinstance(findings, list)
    assert (not pathological) or (ok is False and any("parse" in f for f in findings)), findings
    ok, findings = _validate(_blk("test_acc_844_y.py", "x = (" * 400 + ")" * 400 + "\n" + GOOD))
    assert ok is False and any("parse" in f for f in findings), findings
    ok, findings = _validate(_blk("test_acc_844_z.py", "x = 1\x00\n" + GOOD))
    assert ok is False and any("parse" in f for f in findings), findings


def test_validate_output_rejects_a_missing_test_function():
    for src in ("x = 1\n", "def helper():\n    return 1\n",
                "class TestX:\n    def test_a(self):\n        pass\n",
                "def outer():\n    def test_inner():\n        pass\n",
                "S = 'def test_in_a_string(): pass'\n"):
        ok, findings = _validate(_blk("test_acc_844_x.py", src))
        assert ok is False and any("test_" in f for f in findings), (src, findings)
    assert _validate(_blk("test_acc_844_x.py", "async def test_a():\n    pass\n")) == (True, [])


def test_validate_output_rejects_a_module_top_repo_or_relative_import(monkeypatch):
    _stdlib(monkeypatch)
    for top in ("import work\n", "from reviewer import check\n", "from . import sibling\n",
                "import os, work\n",
                "try:\n    import yaml\nexcept ImportError:\n    yaml = None\n",
                "if True:\n    import work\n",
                "class K:\n    import work\n"):
        ok, findings = _validate(_blk("test_acc_844_x.py", top + GOOD))
        assert ok is False and any("import" in f for f in findings), (top, findings)
    assert _validate(_blk("test_acc_844_x.py", "import pytest\nimport os\n" + GOOD)) == (True, [])


def test_validate_output_fails_closed_without_stdlib_module_names(monkeypatch):
    monkeypatch.delattr(sys, "stdlib_module_names", raising=False)
    ok, findings = _validate(_blk("test_acc_844_x.py", "import os\n" + GOOD))
    assert ok is False and any("cannot classify imports" in f for f in findings), findings
    assert _validate(_blk("test_acc_844_y.py", "import pytest\n" + GOOD)) == (True, [])
    assert _validate(_blk("test_acc_844_z.py")) == (True, [])


def test_validate_output_grammar_edges_header_in_block_fence_prose_unterminated_trailing_space():
    one = _blk("test_acc_844_a.py")
    trailing = one.replace(" ===\n", " ===  \n", 1).replace("=== END FILE ===", "=== END FILE ===\t")
    assert _validate(trailing) == (True, [])
    ok, findings = _validate("  === FILE: %stest_acc_844_a.py ===\n" % DIR + GOOD + one)
    assert ok is False and any("outside" in f for f in findings), findings
    broken = "=== FILE: %stest_acc_844_a.py ===\ndef test_a():\n    pass\n" % DIR
    ok, findings = _validate(broken + _blk("test_acc_844_b.py", "def test_b(:\n"))
    assert ok is False
    assert any("not terminated" in f for f in findings), findings
    assert any("parse" in f for f in findings), findings
    ok, findings = _validate("```python\n" + one + "```\n")
    assert ok is False and any("fence" in f for f in findings), findings
    ok, findings = _validate("~~~\n" + one)
    assert ok is False and any("fence" in f for f in findings), findings
    ok, findings = _validate("Here is the code:\n" + one)
    assert ok is False and any("outside" in f for f in findings), findings
    ok, findings = _validate("=== FILE: %stest_acc_844_a.py ===\n%s" % (DIR, GOOD))
    assert ok is False and any("unterminated" in f for f in findings), findings
    for empty in ("", "   \n\n", b""):
        ok, findings = _validate(empty)
        assert ok is False and findings, repr(empty)
    ok, findings = _validate(b"\xff\xfe=== FILE")
    assert ok is False and any("UTF-8" in f for f in findings), findings


def test_validate_output_enforces_the_block_and_byte_caps():
    max_blocks = getattr(_b(), "_MAX_BLOCKS", None)
    max_bytes = getattr(_b(), "_MAX_BLOCK_BYTES", None)
    assert max_blocks == 14 and max_bytes == 32768
    many = "".join(_blk("test_acc_844_n%d.py" % i) for i in range(max_blocks))
    assert _validate(many) == (True, [])
    ok, findings = _validate(many + _blk("test_acc_844_extra.py"))
    assert ok is False and any("too many" in f for f in findings), findings
    big = "# " + "x" * max_bytes + "\nthis is not python (\n"
    ok, findings = _validate(_blk("test_acc_844_big.py", big))
    assert ok is False and any("exceeds" in f for f in findings), findings
    assert not any("parse" in f for f in findings), findings
    ok, findings = _validate("\n" * (max_blocks * max_bytes + 1))
    assert ok is False and any("total" in f for f in findings), findings


def test_validate_output_reports_every_finding_not_just_the_first():
    text = ("some prose\n"
            + _blk("test_acc_844_a.py", "def test_a(:\n")
            + _blk("test_acc_844_b.py", "x = 1\n")
            + _blk("x", path=DIR + "sub/test_acc_844_c.py"))
    ok, findings = _validate(text)
    assert ok is False
    assert len(findings) >= 4, findings
    assert all(isinstance(f, str) for f in findings)


def test_the_criteria_cap_equals_acceptance_criteria_upper_bound():
    cap = getattr(_b(), "_MAX_CRITERIA", None)
    assert cap == 7
    acc = _acceptance_module()
    seven = "## Done when\n" + "".join("- [ ] item %d\n" % i for i in range(cap))
    assert len(acc.criteria(seven)) == cap
    eight = "## Done when\n" + "".join("- [ ] item %d\n" % i for i in range(cap + 1))
    try:
        acc.criteria(eight)
    except ValueError:
        raised = True
    else:
        raised = False
    assert raised
    assert getattr(_b(), "_MAX_BLOCKS", None) == 2 * cap


def test_the_module_states_classification_waits_on_535_and_ships_no_ids():
    doc = _b().__doc__ or ""
    assert "535" in doc and "classification" in doc
    verdicts = _need("criterion_verdicts")(["rejects a negative age"])
    assert all("id" not in v and "tag" not in v for v in verdicts)
    source = (S / "breaker.py").read_text(encoding="utf-8")
    assert "waits on issue 535" in source


# --- guards that must already pass on the unrepaired module (pin what must not regress) ----------

def test_scrub_still_removes_code_shaped_lines():
    b = _b()
    for line in ("return 0 <= age <= 120", "class Foo:", "from x import y", "import os",
                 "self.x = 1", "+added line", "-removed line", "/srv/data/notes"):
        assert b.scrub(line) == "", line


def test_no_channel_still_flags_code_diff_and_path():
    b = _b()
    for dirty in ("def f(a):\n", "+x = 1\n", "diff --git a/x b/x\n", "see /srv/data/notes\n",
                  "    self.value = 3\n"):
        clean, findings = b.no_channel(dirty, scratch_paths=["/srv/data/notes"])
        assert clean is False and findings, dirty


def test_actionable_keeps_its_list_level_result():
    b = _b()
    assert b.actionable(["add validation", "rejects a negative age"]) == (True, "")
    ok, reason = b.actionable(["add validation"])
    assert ok is False and "PLAN-phase defect against the goal" in reason


def test_breaker_stays_library_only():
    tree = ast.parse((S / "breaker.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.If) and "__name__" in ast.dump(node.test):
            raise AssertionError("breaker.py grew a __main__ block")


def test_no_acc_prefixed_test_file_exists_outside_the_acceptance_directory():
    assert sorted(pathlib.Path(__file__).parent.glob("test_acc_*.py")) == []
