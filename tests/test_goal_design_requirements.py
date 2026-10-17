"""#659: `goal_design.py requirements|served` -- numbered functional requirements and their trace
to the slice table. Artifacts are built in tmp_path; the verbs read one file and write nothing."""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "skills" / "sigma-goal-design" / "scripts" / "goal_design.py"
_spec = importlib.util.spec_from_file_location("goal_design_req", _SCRIPT)
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

FR1 = "WHEN a user logs in, THE system SHALL show the home page"
FR2 = "IF the disk is full, THEN THE writer SHALL refuse the write"

HEAD = "# Design: x\n\n## Intent\nsome intent\n\n## Premise check\n- None.\n\n"
TAIL = "## Handoff\n- nothing\n"


def art(frs=(FR1, FR2), rows=None, header="| Slice | Covers | Requirements | Depends on |",
        fr_block=None, detailed="## Detailed design\ntext\n\n", before_table=""):
    """Build an artifact. `rows` are raw table lines (default: FR-1 -> slice 1, FR-2 -> slice 2)."""
    if fr_block is None:
        fr_block = "\n".join("- **FR-%d.** %s" % (i, s) for i, s in enumerate(frs, 1)) if frs else "- None."
    if rows is None:
        rows = ["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | 1 |"]
    cols = header.count("|") - 1
    sep = "|" + " --- |" * cols
    return (HEAD + "## Functional requirements\n" + fr_block + "\n\n## Queries run\n- `x`\n\n"
            + detailed + "## Slice-count estimate\n" + before_table + header + "\n" + sep + "\n"
            + "\n".join(rows) + "\n\n" + TAIL)


def check(text):
    return gd.check_requirements(text)


def viol(text):
    return "\n".join(check(text)["violations"])


def run(tmp_path, text, *extra):
    p = tmp_path / "d.md"
    p.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(_SCRIPT), "requirements", str(p), *extra],
                          capture_output=True, text=True)


def test_ac1_valid_artifact_passes(tmp_path):
    r = check(art())
    assert r["ok"] is True and r["violations"] == []
    assert [f["id"] for f in r["frs"]] == [1, 2]
    p = run(tmp_path, art())
    assert p.returncode == 0
    out = json.loads(p.stdout)
    assert out["ok"] is True and len(out["frs"]) == 2


def test_ac1_none_bullet_with_dash_cells_passes():
    t = art(frs=(), rows=["| 1 | a | — | — |"])
    assert check(t)["ok"] is True


def test_ac1_missing_heading_is_violation():
    t = art().replace("## Functional requirements", "## Something else")
    v = viol(t)
    assert "Functional requirements" in v and "re-run goal-design" in v and "FR-1" in v
    assert check(t)["ok"] is False


def test_heading_order_not_required_but_section_must_not_be_inside_other():
    # a FR bullet living in Detailed design does not count as the section
    t = art().replace("## Functional requirements", "## Not it")
    assert not check(t)["ok"]


def test_ac2_non_ears_fr_named_and_refused(tmp_path):
    t = art(frs=(FR1, "Add support for caching"))
    assert "FR-2" in viol(t)
    p = run(tmp_path, t)
    assert p.returncode == 2 and "FR-2" in p.stderr
    assert json.loads(p.stdout)["ok"] is False


def test_tagged_sentence_does_not_qualify():
    assert "FR-1" in viol(art(frs=("PROCESS: do it", FR2)))


def test_ac3_uncovered_fr_named():
    t = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | — | 1 |"])
    v = viol(t)
    assert "FR-2" in v and "no slice" in v


def test_unknown_token_in_requirements_cell():
    t = art(rows=["| 1 | a | FR-1, FR-9 | — |", "| 2 | b | FR-2 | 1 |"])
    assert "FR-9" in viol(t)


def test_none_section_with_nondash_cell():
    t = art(frs=(), rows=["| 1 | a | FR-1 | — |"])
    v = viol(t)
    assert "None" in v and "FR-1" in v


def test_requirements_column_missing_with_fr_present():
    t = art(header="| Slice | Covers | Depends on |", rows=["| 1 | a | — |", "| 2 | b | 1 |"])
    assert "Requirements column" in viol(t)


def test_ids_must_increase_unique_gaps_ok():
    assert check(art(frs=(FR1, FR2), rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | 1 |"]))["ok"]
    gap = art(fr_block="- **FR-1.** %s\n- **FR-3.** %s" % (FR1, FR2),
              rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-3 | 1 |"])
    assert check(gap)["ok"] is True
    back = art(fr_block="- **FR-2.** %s\n- **FR-1.** %s" % (FR1, FR2))
    assert "FR-1" in viol(back) and "increasing" in viol(back)
    dup = art(fr_block="- **FR-1.** %s\n- **FR-1.** %s" % (FR1, FR2))
    assert "duplicate" in viol(dup)
    zero = art(fr_block="- **FR-0.** %s\n- **FR-1.** %s" % (FR1, FR2))
    assert "FR-0" in viol(zero)
    word = art(fr_block="- **FR-x.** %s\n- **FR-1.** %s" % (FR1, FR2))
    assert "FR-x" in viol(word)


def test_stray_line_in_section_is_violation():
    t = art(fr_block="- **FR-1.** %s\nsome stray prose\n- **FR-2.** %s" % (FR1, FR2))
    assert "stray" in viol(t) or "neither" in viol(t)
    t2 = art(fr_block="- **FR-1.** %s\n- **FR-2.** %s\n- None." % (FR1, FR2))
    assert "None" in viol(t2)


def test_stray_non_fr_bullet_is_violation():
    t = art(fr_block="- **FR-1.** %s\n- a plain bullet\n- **FR-2.** %s" % (FR1, FR2))
    assert "neither an FR bullet" in viol(t)
    assert "malformed FR bullet" in viol(art(fr_block="- **FR-1.**%s\n- **FR-2.** %s" % (FR1, FR2)))


def test_header_name_not_position():
    t = art(header="| Slice | Requirements | Covers | Depends on |",
            rows=["| 1 | FR-1 | a | — |", "| 2 | FR-2 | b | 1 |"])
    assert check(t)["ok"] is True


def test_only_slice_section_table_is_read():
    decoy = ("## Detailed design\n| Slice | Covers | Requirements | Depends on |\n| --- | --- | --- | --- |\n"
             "| 1 | x | FR-1, FR-2 | — |\n\n")
    t = art(rows=["| 1 | a | — | — |", "| 2 | b | — | 1 |"], detailed=decoy)
    v = viol(t)
    assert "FR-1" in v and "FR-2" in v


def test_prose_before_table_and_first_slice_table_is_read():
    t = art(before_table="Some prose first.\n\n")
    assert check(t)["ok"] is True


def test_duplicate_requirements_header_is_violation():
    t = art(header="| Slice | Requirements | Requirements | Depends on |",
            rows=["| 1 | FR-1 | — | — |", "| 2 | FR-2 | — | 1 |"])
    assert "duplicate" in viol(t).lower()


def test_empty_cell_is_violation_naming_slice():
    t = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b |  | 1 |"])
    v = viol(t)
    assert "slice 2" in v and "empty" in v


def test_short_row_is_violation_naming_slice():
    t = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | 1 |"])
    v = viol(t)
    assert "slice 2" in v and "cells" in v


def test_wrapped_fr_bullet_joined_with_single_spaces():
    wrapped = "- **FR-1.** WHEN a user logs in,\n  THE system SHALL show\n  the home page\n- **FR-2.** %s" % FR2
    r = check(art(fr_block=wrapped))
    assert r["ok"] is True
    assert r["frs"][0]["sentence"] == "WHEN a user logs in, THE system SHALL show the home page"
    one = check(art())
    assert one["frs"][0]["sentence"] == FR1


def test_requirements_refuses_hash_issue_ref():
    t = art(frs=("WHEN done, THE system SHALL run after #5", FR2))
    assert "FR-1" in viol(t) and "#" in viol(t)


def test_over_read_cap_refused(tmp_path):
    p = tmp_path / "big.md"
    p.write_bytes(b"x" * (gd.READ_CAP + 1))
    r = subprocess.run([sys.executable, str(_SCRIPT), "requirements", str(p)], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == "" and "cap" in r.stderr and "Traceback" not in r.stderr


def test_exactly_cap_is_read(tmp_path):
    body = art()
    pad = "\n<!-- " + "x" * (gd.READ_CAP - len(body.encode()) - 10) + " -->\n"
    p = tmp_path / "cap.md"
    p.write_bytes((body + pad).encode()[:gd.READ_CAP])
    assert p.stat().st_size == gd.READ_CAP
    r = subprocess.run([sys.executable, str(_SCRIPT), "requirements", str(p)], capture_output=True, text=True)
    assert "cap" not in r.stderr


def test_bad_utf8_and_missing_file_refused_cleanly(tmp_path):
    p = tmp_path / "bad.md"
    p.write_bytes(b"\xff\xfe\x00bad")
    for target in (str(p), str(tmp_path / "nope.md")):
        r = subprocess.run([sys.executable, str(_SCRIPT), "requirements", target], capture_output=True, text=True)
        assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr and r.stderr.strip()


def test_over_long_sentence_refused_before_regex(monkeypatch):
    real = gd.ears_patterns.is_ears

    def guarded(s):
        assert len(s) <= gd.SENTENCE_CAP, "regex reached an over-cap sentence"
        return real(s)
    monkeypatch.setattr(gd.ears_patterns, "is_ears", guarded)
    long = "WHEN a, " * 400
    v = viol(art(frs=(long, FR2)))
    assert "FR-1" in v and "2000" in v


def test_many_frs_warns_not_refuses():
    n = 51
    frs = tuple("THE s SHALL do %d" % i for i in range(n))
    rows = ["| 1 | a | %s | — |" % ", ".join("FR-%d" % i for i in range(1, n + 1))]
    r = check(art(frs=frs, rows=rows))
    assert r["ok"] is True and r["warnings"]


# ---- served ---------------------------------------------------------------------------------

def served(tmp_path, text, slice_):
    p = tmp_path / "d.md"
    p.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(_SCRIPT), "served", str(p), "--slice", str(slice_)],
                          capture_output=True, text=True)


def test_ac4_served_prints_block(tmp_path):
    t = art(rows=["| 1 | a | FR-1, FR-2 | — |", "| 2 | b | — | 1 |"])
    r = served(tmp_path, t, 1)
    assert r.returncode == 0
    assert r.stdout == "## Requirements served\n- **FR-1.** %s\n- **FR-2.** %s\n" % (FR1, FR2)
    r2 = served(tmp_path, t, 2)
    assert r2.returncode == 0 and r2.stdout == "## Requirements served\n- None.\n"


def test_served_wrapped_sentence_is_normalised(tmp_path):
    wrapped = "- **FR-1.** WHEN a user logs in,\n  THE system SHALL show the home page\n- **FR-2.** %s" % FR2
    r = served(tmp_path, art(fr_block=wrapped), 1)
    assert "- **FR-1.** %s\n" % FR1 in r.stdout


def test_served_refuses_hash_issue_ref_in_sentence(tmp_path):
    t = art(frs=("WHEN done, THE system SHALL run after #5", FR2))
    r = served(tmp_path, t, 1)
    assert r.returncode == 2 and r.stdout == "" and "FR-1" in r.stderr


def test_served_unknown_slice_refused(tmp_path):
    r = served(tmp_path, art(), 9)
    assert r.returncode == 2 and r.stdout == "" and "9" in r.stderr


def test_served_missing_slice_flag_is_usage(tmp_path):
    p = tmp_path / "d.md"
    p.write_text(art())
    r = subprocess.run([sys.executable, str(_SCRIPT), "served", str(p)], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == ""


def test_served_phantom_blocker_control_with_blocker_scan(tmp_path):
    bs = importlib.util.spec_from_file_location("blocker_scan", _ROOT / "skills/sigma-loop/scripts/blocker_scan.py")
    blocker_scan = importlib.util.module_from_spec(bs)
    bs.loader.exec_module(blocker_scan)
    clean = "WHEN the cache is stale, THE job SHALL run after the lock is released and SHALL require a token"
    t = art(frs=(clean, FR2), rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | 1 |"])
    out = served(tmp_path, t, 1).stdout
    assert out
    hits = _scan(blocker_scan, out)
    assert hits == []
    # the same body with an issue number WOULD mint an edge -- why `served` refuses the shape
    assert _scan(blocker_scan, "THE job SHALL run after #5") != []


def _scan(blocker_scan, text):
    return blocker_scan._BLOCK_RE.findall(blocker_scan.strip_unpark_qa(text))


# ---- --plan ---------------------------------------------------------------------------------

def plan_run(tmp_path, text, issues):
    p = tmp_path / "d.md"
    p.write_text(text, encoding="utf-8")
    pl = tmp_path / "plan.json"
    pl.write_text(json.dumps({"epic": {"title": "e", "body": "b"}, "issues": issues}))
    return subprocess.run([sys.executable, str(_SCRIPT), "requirements", str(p), "--plan", str(pl)],
                          capture_output=True, text=True)


def _body(fr_lines):
    return "scope\n\n## Requirements served\n" + "\n".join(fr_lines) + "\n\nDesign: .sdlc/design/1.md\n"


def _good_issues():
    return [{"key": "1", "title": "a", "body": _body(["- **FR-1.** " + FR1])},
            {"key": "2", "title": "b", "body": _body(["- **FR-2.** " + FR2])}]


def test_plan_check_passes_when_served_pasted(tmp_path):
    r = plan_run(tmp_path, art(), _good_issues())
    assert r.returncode == 0, r.stderr


def test_plan_check_child_missing_fr_sentence_refused(tmp_path):
    iss = _good_issues()
    iss[1]["body"] = _body(["- **FR-2.** something else entirely"])
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "slice 2" in r.stderr and "FR-2" in r.stderr


def test_plan_check_missing_heading_refused(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = "scope\n- **FR-1.** " + FR1 + "\n"
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "Requirements served" in r.stderr and "slice 1" in r.stderr


def test_plan_check_slice_without_child_is_named(tmp_path):
    r = plan_run(tmp_path, art(), _good_issues()[:1])
    assert r.returncode == 2 and "slice 2" in r.stderr and "no child" in r.stderr


def test_plan_check_non_slice_key_refused(tmp_path):
    iss = _good_issues() + [{"key": "extra", "title": "c", "body": "x"}]
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "extra" in r.stderr


def test_plan_check_accepts_wrapped_body_whitespace(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = _body(["- **FR-1.** WHEN a user logs in,\n  THE system SHALL   show the home page"])
    assert plan_run(tmp_path, art(), iss).returncode == 0


def test_usage_without_args_is_exit_2():
    r = subprocess.run([sys.executable, str(_SCRIPT), "requirements"], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == ""


# ---- AC-5: the DOCUMENTED gesture, copied out of the docs and run (AGENTS.md "run the control on
# the gesture the DOCS give"). tests/test_documented_gestures.py only checks string presence; this
# subprocess test is the real guard. It does not prove a model's REJECT -- that is prose obedience.

import shlex


def _documented_line(doc, skill_dir_name, marker):
    """The gesture as the doc gives it: backslash continuations joined, whitespace flattened (it may
    sit in a fence or in an inline code span), `--plan` variant excluded."""
    text = re.sub(r"\\\n\s*", " ", doc.read_text(encoding="utf-8"))
    flat = " ".join(text.split())
    hits = re.findall(r'python3 "\$\{CLAUDE_SKILL_DIR\}[^"]*goal_design\.py" requirements \.sdlc/design/<n>\.md(?! --plan)', flat)
    assert hits, (doc.name, "no requirements gesture found")
    return hits[0].replace("${CLAUDE_SKILL_DIR}", str(_ROOT / "skills" / skill_dir_name))


def _run_documented(line, tmp_path, text):
    d = tmp_path / ".sdlc" / "design"
    d.mkdir(parents=True)
    (d / "659.md").write_text(text, encoding="utf-8")
    argv = shlex.split(line.replace("<n>", "659"))
    assert argv[0] == "python3"
    return subprocess.run([sys.executable] + argv[1:], cwd=tmp_path, capture_output=True, text=True)


_GESTURES = [
    (_ROOT / "skills/sigma-goal-review/references/reading-the-target.md", "sigma-goal-review",
     'goal_design.py" requirements'),
    (_ROOT / "skills/sigma-goal-design/references/writing-the-artifact.md", "sigma-goal-design",
     'goal_design.py" requirements'),
    (_ROOT / "skills/sigma-goal-design/SKILL.md", "sigma-goal-design", 'goal_design.py" requirements'),
]


def _gesture_ids():
    return [g[0].name for g in _GESTURES]


@pytest.mark.parametrize("doc,skill,marker", _GESTURES, ids=_gesture_ids())
def test_ac5_documented_gesture_subprocess_red(doc, skill, marker, tmp_path):
    line = _documented_line(doc, skill, marker)
    uncovered = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | — | 1 |"])
    p = _run_documented(line, tmp_path, uncovered)
    assert p.returncode == 2, (line, p.stdout, p.stderr)
    assert "FR-2" in p.stderr and "no slice" in p.stderr


@pytest.mark.parametrize("doc,skill,marker", _GESTURES, ids=_gesture_ids())
def test_ac5_documented_gesture_green_twin(doc, skill, marker, tmp_path):
    line = _documented_line(doc, skill, marker)
    p = _run_documented(line, tmp_path, art())
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert json.loads(p.stdout)["ok"] is True


@pytest.mark.parametrize("doc,skill,marker", _GESTURES[:1], ids=_gesture_ids()[:1])
def test_ac5_documented_gesture_short_row_and_empty_cell(doc, skill, marker, tmp_path):
    line = _documented_line(doc, skill, marker)
    short = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | 1 |"])
    p = _run_documented(line, tmp_path / "a", short)
    assert p.returncode == 2 and "slice 2" in p.stderr
    empty = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b |  | 1 |"])
    q = _run_documented(line, tmp_path / "b", empty)
    assert q.returncode == 2 and "slice 2" in q.stderr and "FR-2" in q.stderr


def test_ac5_gesture_without_the_path_is_usage_not_green(tmp_path):
    line = _documented_line(*_GESTURES[0][:2], _GESTURES[0][2])
    line = line.replace(".sdlc/design/<n>.md", "").strip()
    argv = shlex.split(line)
    p = subprocess.run([sys.executable] + argv[1:], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 2 and p.stdout == ""


# ---- review send-back fixes (#659) ----------------------------------------------------------

def test_duplicate_slice_label_is_violation_naming_label():
    t = art(rows=["| 1 | a | FR-1 | — |", "| 1 | b | FR-2 | — |"])
    assert "duplicate slice label '1'" in viol(t)


def test_plan_duplicate_key_is_violation(tmp_path):
    iss = _good_issues() + [{"key": "1", "title": "dup", "body": _body(["- **FR-1.** " + FR1])}]
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "duplicate" in r.stderr and "'1'" in r.stderr


def test_plan_non_dict_entry_is_violation_not_crash(tmp_path):
    r = plan_run(tmp_path, art(), [1])
    assert r.returncode == 2 and "not an object" in r.stderr and "Traceback" not in r.stderr


def test_fr_token_in_depends_on_is_violation():
    t = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | FR-1 |"])
    out = viol(t)
    assert "slice 2" in out and "Depends on" in out and "FR-1" in out


def test_plan_fr_sentence_only_in_scope_text_is_violation(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = "scope says " + FR1 + "\n\n## Requirements served\n- None.\n\nDesign: x\n"
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "slice 1" in r.stderr and "FR-1" in r.stderr


def test_plan_fr_sentence_after_design_line_is_violation(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = "scope\n\n## Requirements served\n- None.\n\nDesign: x\n- **FR-1.** " + FR1 + "\n"
    assert plan_run(tmp_path, art(), iss).returncode == 2


def test_plan_served_block_may_end_at_end_of_body(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = "scope\n\n## Requirements served\n- **FR-1.** " + FR1 + "\n"
    assert plan_run(tmp_path, art(), iss).returncode == 0


def test_plan_fr_sentence_under_later_heading_is_violation(tmp_path):
    iss = _good_issues()
    iss[0]["body"] = "scope\n\n## Requirements served\n- None.\n\n## Notes\n- **FR-1.** " + FR1 + "\n"
    assert plan_run(tmp_path, art(), iss).returncode == 2


# ---- review send-back fixes, round 2 (#659) -------------------------------------------------

@pytest.mark.parametrize("bad", [123, ["- **FR-1.** x"], {"a": 1}, True])
def test_plan_non_string_body_is_named_violation_not_crash(tmp_path, bad):
    iss = _good_issues()
    iss[0]["body"] = bad
    r = plan_run(tmp_path, art(), iss)
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert "slice 1" in r.stderr and "body is not a string" in r.stderr


def test_plan_deeply_nested_json_is_refusal_not_crash(tmp_path):
    p = tmp_path / "d.md"
    p.write_text(art(), encoding="utf-8")
    pl = tmp_path / "plan.json"
    pl.write_text("[" * 100000)
    r = subprocess.run([sys.executable, str(_SCRIPT), "requirements", str(p), "--plan", str(pl)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr
    assert "cannot read plan" in r.stderr


@pytest.mark.parametrize("heading", ["Slice-count estimate", "Functional requirements", "Intent", "Handoff"])
def test_duplicate_required_heading_is_violation(heading):
    t = art() + "\n## %s\nagain\n" % heading
    assert "duplicate `## %s` heading" % heading in viol(t)


def test_duplicate_unrequired_heading_is_not_flagged():
    assert check(art() + "\n## Notes\na\n\n## Notes\nb\n")["ok"] is True


@pytest.mark.parametrize("tok", ["fr-1", "FR 1", "Fr1", "fr - 1"])
def test_fr_token_in_depends_on_any_case_or_spacing(tok):
    t = art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | %s |" % tok])
    out = viol(t)
    assert "slice 2" in out and "Depends on" in out


def test_depends_on_plain_slice_ids_still_ok():
    assert check(art(rows=["| 1 | a | FR-1 | — |", "| 2 | b | FR-2 | 1, 1a |"]))["ok"] is True


def test_plan_sentence_under_wrong_fr_label_is_violation(tmp_path):
    t = art(rows=["| 1 | a | FR-1, FR-2 | — |", "| 2 | b | — | 1 |"])
    iss = [{"key": "1", "title": "a", "body": _body(["- **FR-2.** " + FR1, "- **FR-1.** " + FR2])},
           {"key": "2", "title": "b", "body": _body(["- None."])}]
    r = plan_run(tmp_path, t, iss)
    assert r.returncode == 2 and "FR-1" in r.stderr and "FR-2" in r.stderr


def test_plan_prefix_sentence_not_satisfied_by_longer_one(tmp_path):
    short = "THE system SHALL log"
    long_ = "THE system SHALL log every request"
    t = art(frs=(short, long_), rows=["| 1 | a | FR-1, FR-2 | — |", "| 2 | b | — | 1 |"])
    # FR-1's bullet carries FR-2's (longer) sentence: a substring match would accept it
    iss = [{"key": "1", "title": "a", "body": _body(["- **FR-1.** " + long_, "- **FR-2.** " + long_])},
           {"key": "2", "title": "b", "body": _body(["- None."])}]
    r = plan_run(tmp_path, t, iss)
    assert r.returncode == 2 and "FR-1" in r.stderr


def test_plan_accepts_exact_served_output(tmp_path):
    t = art(rows=["| 1 | a | FR-1, FR-2 | — |", "| 2 | b | — | 1 |"])
    blocks = {}
    for s in ("1", "2"):
        blocks[s] = served(tmp_path, t, s).stdout
    iss = [{"key": s, "title": s, "body": "scope\n\n" + blocks[s] + "\nDesign: x\n"} for s in ("1", "2")]
    r = plan_run(tmp_path, t, iss)
    assert r.returncode == 0, r.stderr


# --- fix round 3: nested list markers on continuation lines, duplicate cell tokens ----------


@pytest.mark.parametrize("marker", ["- ", "* ", "+ ", "1. ", "12) "])
def test_nested_non_ears_fr_bullet_is_violation_naming_line(marker, tmp_path):
    block = "- **FR-1.** %s\n  %s**FR-2.** nope not ears" % (FR1, marker)
    t = art(fr_block=block, rows=["| 1 | a | FR-1 | — |"])
    r = check(t)
    assert r["ok"] is False
    v = "\n".join(r["violations"])
    assert "nested list item" in v and "nope not ears" in v
    # the nested line is never joined into FR-1's sentence
    assert r["frs"][0]["sentence"] == FR1
    assert run(tmp_path, t).returncode == 2


def test_nested_ears_fr_bullet_is_violation_too():
    block = "- **FR-1.** %s\n  - **FR-2.** %s" % (FR1, FR2)
    r = check(art(fr_block=block))
    assert r["ok"] is False
    assert "nested list item" in "\n".join(r["violations"])
    assert r["frs"][0]["sentence"] == FR1


def test_plain_wrapped_lines_still_join_beside_nested_guard():
    wrapped = "- **FR-1.** WHEN a user logs in,\n  THE system SHALL show\n  the home page\n- **FR-2.** %s" % FR2
    r = check(art(fr_block=wrapped))
    assert r["ok"] is True, r["violations"]
    assert r["frs"][0]["sentence"] == FR1


def test_duplicate_token_in_one_requirements_cell_is_violation():
    t = art(rows=["| 1 | a | FR-1, FR-1 | — |", "| 2 | b | FR-2 | 1 |"])
    v = viol(t)
    assert "slice 1" in v and "'FR-1'" in v and "more than once" in v
