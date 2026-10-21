"""`goal_design.py nfr-bank|quality`: the quality-requirements bank and the checker for the
`## Quality requirements` section of a design artifact. Artifacts are built in tmp_path; both verbs
read at most one file and write nothing.

Every clause of the checker has its own parametrised case, so deleting one clause turns exactly that
case red (the controls are recorded with the change, not in this file)."""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "skills" / "sigma-goal-design" / "scripts" / "goal_design.py"
_spec = importlib.util.spec_from_file_location("goal_design_quality", _SCRIPT)
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

IDS = ["performance_efficiency", "compatibility", "interaction_capability", "reliability", "security",
       "maintainability", "flexibility", "safety", "observability", "data_retention_privacy", "cost"]
DASH = "—"
GOOD_FIELDS = ["a user", "submits a request", "normal load", "the system replies", "p95 under 200 ms"]
FIELD_NAMES = ["Source", "Stimulus", "Environment", "Response", "Response measure"]
MAIN_HEADER = "| Category | Answer | Source | Stimulus | Environment | Response | Response measure | Evidence |"
STRIDE_HEADER = "| Container | Threat | Mitigation |"
BLOCKLIST = ["n/a", "na", "none", "nil", "tbd", "todo", "-", "—", "–", "...", "?", "unknown", "no", "later",
             "not applicable", "to be determined", "not applicable here", "not yet known"]


# ---------------------------------------------------------------------------------- fixture builders

def line(*cells):
    return "| " + " | ".join(cells) + " |"


def sep_for(header):
    return "|" + " --- |" * (header.count("|") - 1)


def row(cat, answer="scenario", fields=None, evid=None):
    """One main-table line. Scenario rows default to a clean answer; N/A and open rows to dashes."""
    if fields is None:
        fields = list(GOOD_FIELDS) if answer == "scenario" else [DASH] * 5
    if evid is None:
        evid = "BR-1" if answer == "scenario" else DASH
    return line(cat, answer, *fields, evid)


def with_field(cat, idx, value, answer="scenario"):
    f = list(GOOD_FIELDS) if answer == "scenario" else [DASH] * 5
    f[idx] = value
    return row(cat, answer, f)


def stride_rows(containers, drop=(), mit="mitigated by input validation"):
    return [line(c, t, mit) for c in containers for t in "STRIDE" if (c, t) not in drop]


def stride_table(rows, header=STRIDE_HEADER):
    return "\n".join([header, sep_for(header), *rows])


def art(lane="medium", overrides=None, drop=(), extra=(), stride=None, components=("Alpha", "Beta"),
        blast=("BR-1", "BR-2"), doubts=("D-1", "D-2"), slices=1, header=MAIN_HEADER, header_line=None,
        intent="some intent", stride_sep="\n\n", stride_first=False, blast_extra="", doubts_extra="",
        qr_heading=True, qr_body=None, tail_extra=""):
    """A full clean artifact; every knob breaks exactly one thing. `stride=None` means a valid table
    in the large lane and none otherwise; `stride=False` means none; a list means those rows."""
    overrides = overrides or {}
    rows = [overrides.get(c, row(c)) for c in IDS if c not in drop] + list(extra)
    if header_line is None:
        header_line = "**Target**: 1 · **Path**: x · **Lane**: %s · **Budget**: 2" % lane
    if stride is None:
        stride = stride_rows(["Alpha", "Beta"]) if lane == "large" else False
    stride_text = stride_table(stride) if isinstance(stride, list) else (stride or "")
    main = "\n".join([header, sep_for(header), *rows])
    if stride_text and stride_first:
        qr_inner = stride_text + stride_sep + main
    elif stride_text:
        qr_inner = main + stride_sep + stride_text
    else:
        qr_inner = main
    if qr_body is not None:
        qr_inner = qr_body
    qr = ("## Quality requirements\n" + qr_inner + "\n\n") if qr_heading else ""
    comp = "\n".join(["| Component | Role |", "| --- | --- |", *[line(c, "a part") for c in components]])
    br = "\n".join(["| ID | Where | Why |", "| --- | --- | --- |", *[line(b, "x", "y") for b in blast]])
    dd = "\n".join("- **%s.** a question" % d for d in doubts)
    if slices:
        sl = "\n".join(["| Slice | Covers | Requirements | Depends on |", "| --- | --- | --- | --- |",
                        *[line(str(i), "a", DASH, DASH) for i in range(1, slices + 1)]])
    else:
        sl = "no slices yet"
    return ("# Design: x\n\n" + header_line + "\n\n## Intent\n" + intent + "\n\n## Premise check\n- None.\n\n"
            "## Functional requirements\n- None.\n\n" + qr
            + "## Blast radius\n" + br + "\n" + blast_extra + "\n\n## Components\n" + comp + "\n\n"
            "## Doubts\n" + dd + "\n" + doubts_extra + "\n\n## Slice-count estimate\n" + sl + "\n\n"
            "## Handoff\n- nothing\n" + tail_extra)


def V(text, lane=None):
    return gd.check_quality(text, lane)["violations"]


def has(v, sub):
    return any(sub in x for x in v)


def only(v, sub):
    assert len(v) == 1 and sub in v[0], v


def _each(cases, check):
    """Run `check(*args)` for every (case_id, args); one failing case never hides the others. The plan's
    selectors name ONE node per function (red_green.py credits a red only for an unambiguous node), so
    these tests loop instead of using pytest.mark.parametrize."""
    failures = []
    for cid, args in cases:
        try:
            check(*args)
        except Exception as e:  # AssertionError from a check, or any crash: report it against its case
            failures.append("[%s] %s: %s" % (cid, type(e).__name__, e))
    assert not failures, "\n".join(failures)


def run(*args, cwd=_ROOT):
    return subprocess.run([sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, cwd=cwd)


def write(tmp_path, text, name="a.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ------------------------------------------------------------------------------------- Task 1: bank

def test_nfr_bank_order_is_pinned():
    cats = gd.nfr_bank()["categories"]
    assert [c["id"] for c in cats] == IDS  # a literal list, not a set: order is the contract
    assert [c["origin"] for c in cats] == ["iso-25010-2023"] * 8 + ["sigma-extension"] * 3
    assert all(c["label"] and c["ask"] for c in cats)


def test_nfr_bank_shape_and_cli():
    b = gd.nfr_bank()
    assert list(b) == ["version", "categories", "scenario_fields", "stride", "answer_forms"]
    assert b["version"] == "iso-iec-25010-2023+sigma-ext/1"
    assert all(set(c) == {"id", "label", "origin", "ask"} for c in b["categories"])
    assert b["scenario_fields"] == FIELD_NAMES
    assert [s["letter"] for s in b["stride"]] == list("STRIDE")
    assert all(s["name"] for s in b["stride"])
    assert b["answer_forms"] == ["scenario", "N/A: <reason>", "open: D-<n>"]
    r = run("nfr-bank")
    assert r.returncode == 0 and r.stderr == ""
    assert json.loads(r.stdout) == b
    assert r.stdout == json.dumps(b, indent=2) + "\n"
    r = run("nfr-bank", "extra")
    assert r.returncode == 2 and r.stdout == "" and "usage:" in r.stderr
    r = run("nfr-bank", "--lane", "small")
    assert r.returncode == 2 and r.stdout == ""


def test_nfr_bank_returns_copies():
    pristine = json.loads(json.dumps(gd.nfr_bank()))  # a deep snapshot, so an aliased list cannot hide
    b = gd.nfr_bank()
    b["categories"].pop(0)
    b["categories"][0]["ask"] = "mutated"
    b["scenario_fields"].append("Extra")
    b["stride"][0]["name"] = "mutated"
    b["answer_forms"].clear()
    b["version"] = "mutated"
    assert gd.nfr_bank() == pristine
    assert [c["id"] for c in gd.nfr_bank()["categories"]] == IDS
    r = gd.check_quality(art())
    assert [x["category"] for x in r["rows"]] == IDS and r["violations"] == []


# ---------------------------------------------------------------------------- Task 2: skeleton/shape

def test_clean_fixture_passes_in_every_lane():
    _each([(l, (l,)) for l in ("small", "medium", "large")], _check_clean_fixture)


def _check_clean_fixture(lane):
    r = gd.check_quality(art(lane=lane))
    assert r["ok"] is True and r["violations"] == [] and r["warnings"] == []
    assert r["lane"] == lane and len(r["rows"]) == 11
    assert [x["category"] for x in r["rows"]] == IDS
    assert {x["answer"] for x in r["rows"]} == {"scenario"}


def test_json_row_answers():
    text = art(overrides={"cost": row("cost", "N/A: no spend is incurred"), "safety": row("safety", "open: D-1"),
                          "flexibility": row("flexibility", "maybe")})
    got = {x["category"]: x["answer"] for x in gd.check_quality(text)["rows"]}
    assert got["cost"] == "na" and got["safety"] == "open" and got["flexibility"] == "invalid"
    assert got["reliability"] == "scenario"
    assert set(gd.check_quality(text)["rows"][0]) == {"category", "answer"}


def test_requirements_verb_flags_duplicate_quality_heading():
    """`Quality requirements` is in REQUIRED_HEADINGS, so the sibling verb reports a second copy too."""
    text = art() + "\n## Quality requirements\nagain\n"
    assert has(gd.check_requirements(text)["violations"], "duplicate `## Quality requirements` heading")
    assert not has(gd.check_requirements(art())["violations"], "duplicate")


def _cells7():
    return line("cost", "scenario", *GOOD_FIELDS[:4], "BR-1")  # 7 cells: one scenario field short


def _cells9():
    return "| cost | scenario | a user | submits | normal load || the system replies | p95 under 200 ms | BR-1 |"


_MALFORMED_CASES = [
    "wrong_header", "seven_cells", "doubled_pipe", "cell_2001", "cell_2000_ok", "measure_2001_digit",
    "fenced_heading", "duplicate_heading", "no_category_table", "missing_section", "no_separator",
]


def test_malformed_table_shapes():
    _each([(c, (c,)) for c in _MALFORMED_CASES], _check_malformed)


def _check_malformed(case):
    if case == "wrong_header":
        text = art(header="| Category | Answer | Source |")
        only(V(text), "header")
        assert not has(V(text), "missing row")
    elif case == "seven_cells":
        v = V(art(overrides={"cost": _cells7()}))
        only(v, "cost: row ")
        assert "has 7 cells but the header has 8" in v[0]
    elif case == "doubled_pipe":
        v = V(art(overrides={"cost": _cells9()}))
        only(v, "cost: row ")
        assert "has 9 cells but the header has 8" in v[0]
    elif case == "cell_2001":
        v = V(art(overrides={"cost": with_field("cost", 0, "x" * 2001)}))
        only(v, "cost: cell is 2001 chars, over the 2000 cap")
    elif case == "cell_2000_ok":
        assert V(art(overrides={"cost": with_field("cost", 0, "x" * 2000)})) == []
    elif case == "measure_2001_digit":
        # the cap message appears, and for a no-digit cell it is the ONLY message: the cap is checked
        # before any regex on the row, so the measure rule never runs on an over-cap cell.
        only(V(art(overrides={"cost": with_field("cost", 4, "x" * 2001)})), "over the 2000 cap")
        v = V(art(overrides={"cost": with_field("cost", 4, "1" + "x" * 2000)}))
        only(v, "over the 2000 cap")
    elif case == "fenced_heading":
        text = art(qr_heading=False).replace(
            "## Blast radius", "```\n## Quality requirements\n" + MAIN_HEADER + "\n```\n\n## Blast radius", 1)
        v = V(text)
        assert len(v) == 12 and has(v, "quality_requirements: no `## Quality requirements` section")
        assert sum(1 for x in v if x.endswith(": missing row")) == 11
    elif case == "duplicate_heading":
        v = V(art() + "\n## Quality requirements\nagain\n")
        only(v, "quality_requirements: duplicate `## Quality requirements` heading")
    elif case == "no_category_table":
        v = V(art(qr_body="only prose here"))
        assert len(v) == 12 and has(v, "quality_requirements: no table")
        assert sum(1 for x in v if x.endswith(": missing row")) == 11
    elif case == "missing_section":
        v = V(art(qr_heading=False))
        assert len(v) == 12 and has(v, "quality_requirements: no `## Quality requirements` section")
    elif case == "no_separator":
        text = art(qr_body=MAIN_HEADER + "\n" + row("cost"))
        v = V(text)
        only(v, "quality_requirements: ")
        assert "separator" in v[0]


_LANE_HEADER_CASES = ["missing", "huge", "capitalised", "later_section_only", "fenced",
                      "first_wins", "first_wins_next_line", "valid", "override_wins",
                      "override_missing_still_reported", "missing_runs_medium_rules",
                      "unfilled_template", "slash_value", "real_header", "end_of_line", "bold_follows"]


def test_lane_header_rules():
    _each([(c, (c,)) for c in _LANE_HEADER_CASES], _check_lane_header)


def _check_lane_header(case):
    if case == "missing":
        r = gd.check_quality(art(header_line=""))
        only(r["violations"], "lane: missing or not one of small, medium, large; proceeding as medium")
        assert r["lane"] == "medium" and r["ok"] is False
    elif case == "huge":
        r = gd.check_quality(art(header_line="**Lane**: huge"))
        only(r["violations"], "lane: missing or not one of")
        assert r["lane"] == "medium"
    elif case == "capitalised":  # the regex is case-sensitive on the VALUE: `Medium` is not a lane
        only(V(art(header_line="**Lane**: Medium")), "lane: missing or not one of")
    elif case == "later_section_only":  # position control: a `**Lane**` after the first heading never counts
        r = gd.check_quality(art(header_line="", intent="**Lane**: small"))
        only(r["violations"], "lane: missing")
        assert r["lane"] == "medium"
    elif case == "fenced":  # a fenced header line does not count
        only(V(art(header_line="```\n**Lane**: small\n```")), "lane: missing")
    elif case == "first_wins":  # only the first `**Lane**` before the first heading counts
        r = gd.check_quality(art(header_line="**Lane**: huge · **Lane**: small"))
        only(r["violations"], "lane: missing")
    elif case == "first_wins_next_line":  # ... also when the second mention is on a later header line
        only(V(art(header_line="**Lane**: huge\n**Lane**: small")), "lane: missing")
    elif case == "valid":
        r = gd.check_quality(art(header_line="**Lane**: large · x", stride=stride_rows(["Alpha", "Beta"])))
        assert r["violations"] == [] and r["lane"] == "large"
    elif case == "override_wins":
        r = gd.check_quality(art(lane="small"), "large")  # large rules: no STRIDE table in the artifact
        assert r["lane"] == "large" and has(r["violations"], "security: no STRIDE table")
        assert not has(r["violations"], "lane:")
    elif case == "override_missing_still_reported":
        r = gd.check_quality(art(header_line=""), "small")
        only(r["violations"], "lane: missing")
        assert r["lane"] == "small"
    elif case == "missing_runs_medium_rules":
        # never weaker: the missing header runs MEDIUM rules, where `N/A: small lane` is illegal
        v = V(art(header_line="", overrides={"cost": row("cost", "N/A: small lane")}))
        assert has(v, "lane: missing") and has(v, "cost:") and has(v, "only legal in the small lane")
    elif case == "unfilled_template":  # the skeleton line must NOT read as `small` (the weakest lane)
        r = gd.check_quality(art(header_line="**Lane**: small | medium | large"))
        only(r["violations"], "lane: missing or not one of")
        assert r["lane"] == "medium"
    elif case == "slash_value":
        r = gd.check_quality(art(header_line="**Lane**: small/medium"))
        only(r["violations"], "lane: missing or not one of")
        assert r["lane"] == "medium"
    elif case == "real_header":
        r = gd.check_quality(art(header_line="**Target**: x · **Lane**: medium · **Budget**: 2"))
        assert r["violations"] == [] and r["lane"] == "medium"
    elif case == "end_of_line":
        r = gd.check_quality(art(header_line="**Lane**: large", stride=stride_rows(["Alpha", "Beta"])))
        assert r["violations"] == [] and r["lane"] == "large"
    elif case == "bold_follows":
        r = gd.check_quality(art(header_line="**Lane**: large **Budget**: 2", stride=stride_rows(["Alpha", "Beta"])))
        assert r["violations"] == [] and r["lane"] == "large"


# ------------------------------------------------------------------------------------ Task 3: rows

@pytest.mark.parametrize("cat", IDS)
def test_row_key_clauses_missing(cat):
    only(V(art(drop=(cat,))), "%s: missing row" % cat)


def test_row_key_clauses():
    _each([(c, (c,)) for c in ("duplicate", "misspelt", "empty_slug", "wrong_case")], _check_row_key)


def _check_row_key(case):
    if case == "duplicate":
        only(V(art(extra=[row("cost")])), "cost: duplicate row")
    elif case == "misspelt":
        v = V(art(drop=("observability",), extra=[row("observabilty")]))
        assert has(v, "quality_requirements: unknown category 'observabilty'")
        assert has(v, "observability: missing row") and len(v) == 2
    elif case == "empty_slug":
        v = V(art(drop=("cost",), extra=[row("")]))
        assert has(v, "quality_requirements: ") and has(v, "empty category")
        assert has(v, "cost: missing row") and len(v) == 2
    elif case == "wrong_case":
        v = V(art(drop=("observability",), extra=[row("Observability")]))
        assert has(v, "quality_requirements: unknown category 'Observability'")
        assert has(v, "observability: missing row") and len(v) == 2


def test_answered_row_scenario_fields():
    _each([("%s-%s" % (FIELD_NAMES[i], vid), (i, v)) for i in range(5)
           for vid, v in (("blank", ""), ("dash", DASH))], _check_answered_row_field)


def _check_answered_row_field(idx, value):
    v = V(art(overrides={"reliability": with_field("reliability", idx, value)}))
    assert has(v, "reliability: scenario field %s is" % FIELD_NAMES[idx])
    assert len(v) == 1, v  # exactly this field's violation, no other field and no extra line


_MEASURE_CASES = [
    ("99.9% over 30d", True), ("p95 under 200 ms", True), ("test: tests/x.py::t", True), ("test:x", True),
    ("test:", False), ("test:   ", False), ("fast", False), ("test", False), ("Test: x", False),
]


def test_response_measure_forms():
    _each([(repr(m), (m, ok)) for m, ok in _MEASURE_CASES], _check_response_measure)


def _check_response_measure(measure, ok):
    v = V(art(overrides={"safety": with_field("safety", 4, measure)}))
    if ok:
        assert v == []
    else:
        only(v, "safety: Response measure %r has no digit and does not start with 'test:'" % measure.strip())


def _na(answer, cat="cost"):
    return V(art(overrides={cat: row(cat, answer)}))


@pytest.mark.parametrize("answer", ["N/A", "N/A:", "N/A:   "])
def test_na_reason_rules_empty(answer):
    only(_na(answer), "cost: N/A without a reason")


@pytest.mark.parametrize("token", BLOCKLIST)
def test_na_reason_rules_blocklist(token):
    only(_na("N/A: " + token), "cost: placeholder reason")


def test_blocklist_constant_is_the_pinned_set():
    assert gd._REASON_BLOCKLIST == frozenset(BLOCKLIST) and len(BLOCKLIST) == 18


_NA_REASON_CASES = [
    ("N/A: two words", "fewer than 3 words"),
    ("N/A: one", "fewer than 3 words"),
    ("N/A: tbd.", "placeholder reason"),           # rejected via the punctuation-stripped form
    ("N/A: TBD", "placeholder reason"),            # lower-cased form
    ("N/A: not applicable.", "placeholder reason"),
    ("N/A: ...", "placeholder reason"),            # the raw form: stripping dots would empty it
    ("Scenario", "malformed Answer"), ("maybe", "malformed Answer"), ("", "malformed Answer"),
    ("n/a: three word reason", "malformed Answer"), ("N/A:three word reason", "malformed Answer"),
    ("NA: x", "malformed Answer"), ("N/A: three word reason", None),
    ("N/A:   spaced   out   reason", None),
]


def test_na_reason_rules():
    _each([(repr(a), (a, sb)) for a, sb in _NA_REASON_CASES], _check_na_reason)


def _check_na_reason(answer, sub):
    if sub is None:
        assert _na(answer) == []
    else:
        v = _na(answer)
        only(v, "cost: ")
        assert sub in v[0]


_NA_LANE_CASES = [
    ("small", "N/A: small lane", None),
    ("small", "N/A: Small   Lane", None),
    ("small", "N/A: valid three word reason", None),  # small does not force the literal
    ("medium", "N/A: small lane", "only legal in the small lane"),
    ("large", "N/A: small lane", "only legal in the small lane"),
    ("medium", "N/A: Small   Lane", "only legal in the small lane"),
    ("small", "N/A: big lane", "fewer than 3 words"),
    ("medium", "N/A: big lane", "fewer than 3 words"),
    ("large", "N/A: big lane", "fewer than 3 words"),
]


def test_na_small_lane_literal():
    _each([("%s/%r" % (l, a), (l, a, sb)) for l, a, sb in _NA_LANE_CASES], _check_na_small_lane)


def _check_na_small_lane(lane, answer, sub):
    v = V(art(lane=lane, overrides={"cost": row("cost", answer)}))
    if sub is None:
        assert v == []
    else:
        only(v, "cost: ")
        assert sub in v[0]


_OPEN_ROW_CASES = [
    "open_missing_d", "open_ok", "open_no_digit_sep", "open_no_space", "open_capital",
    *["na_field_%d" % i for i in range(5)], *["open_field_%d" % i for i in range(5)],
]


def test_open_row_and_dash_cell_rules():
    _each([(c, (c,)) for c in _OPEN_ROW_CASES], _check_open_row)


def _check_open_row(case):
    if case == "open_missing_d":
        only(V(art(overrides={"cost": row("cost", "open: D-9")})), "cost: open: D-9 is not a bullet id in ## Doubts")
    elif case == "open_ok":
        assert V(art(overrides={"cost": row("cost", "open: D-1")})) == []
    elif case in ("open_no_digit_sep", "open_no_space", "open_capital"):
        ans = {"open_no_digit_sep": "open: D1", "open_no_space": "open:D-1", "open_capital": "Open: D-1"}[case]
        only(V(art(overrides={"cost": row("cost", ans)})), "cost: malformed Answer")
    else:
        kind, idx = case.split("_field_")
        ans = "N/A: no spend is incurred" if kind == "na" else "open: D-1"
        v = V(art(overrides={"cost": with_field("cost", int(idx), "text", answer=ans)}))
        only(v, "cost: scenario field %s must be '—'" % FIELD_NAMES[int(idx)])


_EVIDENCE_CASES = [
    ("BR-1", None), ("BR-99", "evidence token 'BR-99' is not a row id in ## Blast radius"),
    ("D-1", None), ("D-99", "evidence token 'D-99' is not a bullet id in ## Doubts"),
    ("Dossier:constraints", None), ("Dossier:", "evidence token 'Dossier:'"),
    ("Dossier:a b", "evidence token 'b'"),
    ("src/a.py:10", None), ("src/a.py:10-20", None), ("src/a.py:", "evidence token 'src/a.py:'"),
    ("a.py:x", "evidence token 'a.py:x'"),
    ("agent-proposed", None), ("Agent-Proposed", "evidence token 'Agent-Proposed'"),
    (DASH, "evidence is empty"), ("", "evidence is empty"),
    ("BR-1, agent-proposed", None), ("BR-1, nonsense", "evidence token 'nonsense'"),
]


def test_evidence_forms():
    _each([(repr(e), (e, sb)) for e, sb in _EVIDENCE_CASES], _check_evidence)


def _check_evidence(evid, sub):
    v = V(art(overrides={"reliability": row("reliability", evid=evid)}))
    if sub is None:
        assert v == []
    else:
        only(v, "reliability: ")
        assert sub in v[0]


def test_evidence_ids_in_prose_only_are_rejected():
    """BR-9 / D-9 present only as prose (not a table first cell / a `- **D-9.**` bullet) do not exist."""
    v = V(art(blast_extra="\nThe row BR-9 is mentioned here in a paragraph.\n",
              overrides={"reliability": row("reliability", evid="BR-9")}))
    only(v, "evidence token 'BR-9' is not a row id in ## Blast radius")
    v = V(art(doubts_extra="\nWe also wondered about D-9 in prose.\n",
              overrides={"reliability": row("reliability", evid="D-9")}))
    only(v, "evidence token 'D-9' is not a bullet id in ## Doubts")
    v = V(art(doubts_extra="\nWe also wondered about D-9 in prose.\n",
              overrides={"cost": row("cost", "open: D-9")}))
    only(v, "cost: open: D-9 is not a bullet id in ## Doubts")


# ------------------------------------------------------------------------------ Task 4: lane gating

def test_small_lane_rules():
    one = {"cost": row("cost", evid="agent-proposed")}
    v = V(art(lane="small", overrides=one))
    only(v, "cost: small lane needs at least one BR-<n> evidence token")
    assert V(art(lane="small", overrides={"cost": row("cost", evid="agent-proposed, BR-1")})) == []
    assert V(art(lane="medium", overrides=one)) == []  # lane gating: medium does not require it
    # `not problems` clause: a bad token is reported ALONE, not doubled with the missing-BR line
    only(V(art(lane="small", overrides={"cost": row("cost", evid="nonsense")})), "cost: ")
    assert not has(V(art(lane="small", overrides={"cost": row("cost", evid="nonsense")})), "needs at least one BR")
    r = gd.check_quality(art(lane="small", slices=2))
    assert r["ok"] is True and r["warnings"] == ["lane: small lane but 2 slice rows"]
    for lane, n in (("small", 1), ("small", 0), ("medium", 2), ("large", 2)):
        assert gd.check_quality(art(lane=lane, slices=n))["warnings"] == [], (lane, n)


def test_large_lane_stride_required():
    only(V(art(lane="large", stride=False)), "security: no STRIDE table")
    na = row("security", "N/A: nothing crosses a boundary")
    only(V(art(lane="large", overrides={"security": na})), "security: N/A is not allowed in the large lane")
    assert V(art(lane="medium", overrides={"security": na})) == []  # medium keeps N/A legal
    assert V(art(lane="large", overrides={"security": row("security", "open: D-1")})) == []
    escape = ["| — | — | no container crosses a boundary here |"]
    assert V(art(lane="large", stride=escape)) == []
    only(V(art(lane="large", stride=["| — | — | no boundary |"])), "security: ")
    assert has(V(art(lane="large", stride=["| — | — | no boundary |"])), "fewer than 3 words")
    normal = stride_rows(["Alpha"])
    for rows in (escape + normal, normal + escape):  # the escape row must be the ONLY row, both orders
        assert has(V(art(lane="large", stride=rows)), "escape row")
    assert V(art(lane="large", stride=stride_rows(["Alpha", "Beta"]))) == []
    assert has(V(art(lane="large", stride=["| — | — | tbd |"])), "placeholder reason")
    assert has(V(art(lane="large", stride=[])), "STRIDE table has no rows")


@pytest.mark.parametrize("letter", list("STRIDE"))
def test_stride_row_rules_each_threat_required(letter):
    only(V(art(lane="large", stride=stride_rows(["Alpha", "Beta"], drop={("Alpha", letter)}))),
         "security: STRIDE container 'Alpha' is missing threat(s) %s" % letter)


_STRIDE_CASES = [
    "not_in_components", "duplicate", "bad_letter", "empty_mitigation", "ruled_out_two_words",
    "ruled_out_ok", "ruled_out_tbd", "ruled_out_empty", "wrong_header", "wrong_cell_count", "medium_reported",
    "medium_clean_accepted", "cell_cap", "order_free", "stride_first", "adjacent_tables", "container_dash_only",
]


def test_stride_row_rules():
    _each([(c, (c,)) for c in _STRIDE_CASES], _check_stride_row)


def _check_stride_row(case):
    base = stride_rows(["Alpha", "Beta"])
    if case == "not_in_components":
        only(V(art(lane="large", stride=base + stride_rows(["Gamma"]))),
             "security: STRIDE container 'Gamma' is not a row in ## Components")
    elif case == "duplicate":
        only(V(art(lane="large", stride=base + [line("Alpha", "S", "again")])),
             "security: duplicate STRIDE row for container 'Alpha' threat S")
    elif case == "bad_letter":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "X", "m") for x in base]
        v = V(art(lane="large", stride=rows))
        assert has(v, "security: STRIDE threat 'X' is not one of S, T, R, I, D, E")
    elif case == "empty_mitigation":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "S", "") for x in base]
        only(V(art(lane="large", stride=rows)), "security: STRIDE mitigation for 'Alpha'/S is empty")
    elif case == "ruled_out_two_words":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "S", "ruled out: ok") for x in base]
        only(V(art(lane="large", stride=rows)), "fewer than 3 words")
    elif case == "ruled_out_ok":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "S", "ruled out: not a trust boundary")
                for x in base]
        assert V(art(lane="large", stride=rows)) == []
    elif case == "ruled_out_tbd":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "S", "ruled out: tbd") for x in base]
        only(V(art(lane="large", stride=rows)), "placeholder reason")
    elif case == "ruled_out_empty":
        rows = [x if not x.startswith("| Alpha | S ") else line("Alpha", "S", "ruled out:") for x in base]
        only(V(art(lane="large", stride=rows)), "ruled out without a reason")
    elif case == "wrong_header":
        text = art(lane="large", stride="| Container | Threat |\n| --- | --- |\n| Alpha | S |")
        only(V(text), "security: STRIDE table header must be exactly")
    elif case == "wrong_cell_count":
        v = V(art(lane="large", stride=base + ["| Alpha | S |"]))
        only(v, "security: STRIDE row ")
        assert "has 2 cells but the header has 3" in v[0]
    elif case == "medium_reported":  # D-8: a malformed table is never silently ignored in a non-large lane
        assert has(V(art(lane="medium", stride=stride_rows(["Alpha"], drop={("Alpha", "S")}))),
                   "missing threat(s) S")
    elif case == "medium_clean_accepted":
        assert V(art(lane="medium", stride=base)) == []
    elif case == "cell_cap":
        v = V(art(lane="large", stride=base[:-1] + [line("Beta", "E", "m" * 2001)]))
        assert has(v, "security: cell is 2001 chars, over the 2000 cap")
    elif case == "order_free":  # the six threats are a SET: reversed order is fine
        assert V(art(lane="large", stride=list(reversed(base)))) == []
    elif case == "stride_first":  # a Container table before the Category table is still handled
        assert V(art(lane="large", stride=base, stride_first=True)) == []
        assert gd.check_quality(art(lane="large", stride=base, stride_first=True))["rows"][0]["category"] == IDS[0]
    elif case == "adjacent_tables":  # no blank line between the two tables: split on the header row
        assert V(art(lane="large", stride=base, stride_sep="\n")) == []
        assert V(art(lane="large", stride=base, stride_sep="\n", stride_first=True)) == []
    elif case == "container_dash_only":
        assert has(V(art(lane="large", stride=base + ["| — | S | m |"])),
                   "security: STRIDE container '—' is not a row in ## Components")


def test_container_warning_boundary():
    names = ["C%d" % i for i in range(1, 22)]
    r = gd.check_quality(art(lane="large", components=names, stride=stride_rows(names[:20])))
    assert r["violations"] == [] and r["warnings"] == []
    r = gd.check_quality(art(lane="large", components=names, stride=stride_rows(names)))
    assert r["ok"] is True and r["violations"] == []
    assert len(r["warnings"]) == 1 and r["warnings"][0].startswith("security: ")
    assert "21" in r["warnings"][0] and "20" in r["warnings"][0]


# ---------------------------------------------------------------------------------- Task 5: the verb

_SLUG = re.compile(r"^(%s|lane|quality_requirements): " % "|".join(IDS))


def test_every_violation_line_starts_with_a_category_slug():
    names = ["C%d" % i for i in range(1, 22)]
    bad_rows = {
        "performance_efficiency": _cells7(),
        "security": with_field("security", 4, "fast"),
        "reliability": with_field("reliability", 1, ""),
        "safety": row("safety", evid="BR-99"),
        "maintainability": row("maintainability", "N/A: tbd"),
        "flexibility": row("flexibility", "open: D-9"),
        "compatibility": row("compatibility", "maybe"),
        "interaction_capability": with_field("interaction_capability", 2, "x" * 2001),
        "data_retention_privacy": row("data_retention_privacy", "N/A: small lane"),
    }
    texts = [
        art(lane="small", slices=2, components=names, stride=stride_rows(names) + [line("Gamma", "Q", "")],
            overrides=bad_rows, drop=("observability",), extra=[row("cost"), row("nope")]),
        art(header_line="", header="| Category | Answer |", stride="| Container | Threat |\n| --- | --- |"),
        art(qr_heading=False),
        art(lane="large", stride=False, overrides={"security": row("security", "N/A: nothing crosses here")}),
    ]
    seen = set()
    for t in texts:
        r = gd.check_quality(t)
        assert r["violations"], t
        for m in r["violations"] + r["warnings"]:
            assert _SLUG.match(m), m
            seen.add(m.split(":")[0])
    assert {"lane", "quality_requirements", "security", "observability", "cost"} <= seen


def test_read_cap_boundary_cli(tmp_path):
    base = art()
    size = len(base.encode("utf-8"))
    pad = gd.READ_CAP - size
    assert pad > 10
    at_cap = base.replace("- nothing\n", "- " + "x" * (pad + 7) + "\n")  # one long prose line in Handoff
    assert len(at_cap.encode("utf-8")) == gd.READ_CAP
    r = run("quality", write(tmp_path, at_cap, "cap.md"))
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True and "cap" not in r.stderr
    over = at_cap + "x"
    assert len(over.encode("utf-8")) == gd.READ_CAP + 1
    r = run("quality", write(tmp_path, over, "over.md"))
    assert r.returncode == 2 and r.stdout == ""
    assert "read cap" in r.stderr and str(gd.READ_CAP) in r.stderr and "Traceback" not in r.stderr
    assert r.stderr.startswith("goal_design.py quality: ")


def test_cli_usage_errors_and_streams(tmp_path):
    good = write(tmp_path, art(), "good.md")
    bad_text = art(drop=("observability",))
    bad = write(tmp_path, bad_text, "bad.md")
    nonutf = tmp_path / "nonutf.md"
    nonutf.write_bytes(b"\xff\xfe\x00bad")
    warn = write(tmp_path, art(lane="small", slices=2), "warn.md")
    before = sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir())

    usage_cases = [
        ["quality"], ["quality", "--bogus"], ["quality", "--lane"], ["quality", good, "--lane"], ["quality", good, "--lane", "huge"],
        ["quality", "--lane", "small"], ["quality", good, "--bogus"], ["quality", good, "extra"],
        ["quality", good, "--lane", "small", "--lane", "large"], ["quality", good, "--lane", "--lane"],
    ]
    for args in usage_cases:
        r = run(*args)
        assert r.returncode == 2 and r.stdout == "" and "usage:" in r.stderr, args
    for target in (str(tmp_path / "nope.md"), str(nonutf)):
        r = run("quality", target)
        assert r.returncode == 2 and r.stdout == "" and "Traceback" not in r.stderr
        assert r.stderr.startswith("goal_design.py quality: ")

    r = run("quality", bad)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["lane"] == "medium" and out["violations"] == ["observability: missing row"]
    assert r.stdout == json.dumps(out, indent=2) + "\n"
    assert r.stderr == "goal_design.py quality: VIOLATION: observability: missing row\n"

    r = run("quality", warn)
    assert r.returncode == 0 and json.loads(r.stdout)["warnings"] == ["lane: small lane but 2 slice rows"]
    assert r.stderr == "goal_design.py quality: warning: lane: small lane but 2 slice rows\n"
    assert json.loads(run("quality", warn, "--lane", "medium").stdout)["lane"] == "medium"
    assert json.loads(run("quality", warn, "--lane", "medium").stdout)["warnings"] == []
    r = run("quality", good, "--lane", "large")
    assert r.returncode == 2 and json.loads(r.stdout)["lane"] == "large"
    assert "goal_design.py quality: VIOLATION: security: no STRIDE table" in r.stderr
    assert "--lane" in gd._USAGE and "quality <artifact.md> [--lane small|medium|large]" in gd._USAGE
    assert "nfr-bank" in gd._USAGE
    # read-only: the directory and every file's bytes are identical after all those runs
    assert sorted((p.name, p.read_bytes()) for p in tmp_path.iterdir()) == before


def test_ac5_documented_gesture_subprocess_red(tmp_path):
    """The gesture the design says the review step documents: NO `--lane`, script path from this file's
    location (not the cwd), a medium artifact missing its observability row."""
    script = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-goal-design" / "scripts" / "goal_design.py"
    broken = write(tmp_path, art(drop=("observability",)), "broken.md")
    r = subprocess.run([sys.executable, str(script), "quality", broken], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2 and "observability" in r.stderr
    complete = write(tmp_path, art(), "complete.md")
    r = subprocess.run([sys.executable, str(script), "quality", complete], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout)["ok"] is True


# ---- AC-5 (#1781): the quality gesture AS EACH REVIEW DOC GIVES IT, copied out and run. Not a proof
# that a model REJECTs -- that is prose obedience; these only prove the command the docs print works.
import shlex

_REVIEW_REF = _ROOT / "skills/sigma-goal-review/references/reading-the-target.md"
_REVIEW_SKILL = _ROOT / "skills/sigma-goal-review/SKILL.md"
_DOSSIER = _ROOT / "docs/dossier-pipeline.md"
_FORM_SKILL_DIR = r'python3 "\$\{CLAUDE_SKILL_DIR\}[^"]*goal_design\.py" quality \.sdlc/design/<n>\.md(?!\s+--)'
_FORM_REPO = r"python3 skills/sigma-goal-design/scripts/goal_design\.py quality \.sdlc/design/<n>\.md(?!\s+--)"


def _flat_slice(doc, start, end):
    raw = doc.read_text(encoding="utf-8")
    assert start in raw, (doc.name, "start anchor missing", start)
    sl = raw.split(start, 1)[1]
    sl = sl.split(end, 1)[0] if end in sl else sl
    return " ".join(re.sub(r"\\\n\s*", " ", start + sl).split())


def _documented_quality_line(doc, start, end, form):
    flat = _flat_slice(doc, start, end)
    hits = re.findall(form, flat)
    assert hits, (doc.name, "no bare quality gesture in the slice")
    line = hits[0].replace("${CLAUDE_SKILL_DIR}", str(_ROOT / "skills" / "sigma-goal-review"))
    return line.replace("skills/sigma-goal-design", str(_ROOT / "skills" / "sigma-goal-design"))


def _run_quality_line(line, tmp_path, text):
    d = tmp_path / ".sdlc" / "design"
    d.mkdir(parents=True)
    (d / "660.md").write_text(text, encoding="utf-8")
    argv = shlex.split(line.replace("<n>", "660"))
    assert argv[0] == "python3"
    return subprocess.run([sys.executable] + argv[1:], cwd=tmp_path, capture_output=True, text=True)


_A = (_REVIEW_REF, "**`Quality requirements` is checked by RUNNING", "**`Out of scope`", _FORM_SKILL_DIR)
_B = (_REVIEW_SKILL, "**Then run the quality check**", "\n## 2.", _FORM_SKILL_DIR)
_C = (_DOSSIER, "**`Quality requirements` is checked by running the `quality` verb**", "**`Budget` is a claim", _FORM_REPO)


def _red(form, tmp_path):
    p = _run_quality_line(_documented_quality_line(*form), tmp_path, art(drop=("observability",)))
    assert p.returncode == 2 and "observability" in p.stderr, (p.stdout, p.stderr)
    assert json.loads(p.stdout)["ok"] is False


def _green(form, tmp_path):
    p = _run_quality_line(_documented_quality_line(*form), tmp_path, art())
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert json.loads(p.stdout)["ok"] is True


def _bare(form):
    _documented_quality_line(*form)
    assert "quality .sdlc/design/<n>.md --" not in _flat_slice(*form[:3])


def test_ac5_review_reference_gesture_red(tmp_path):
    _red(_A, tmp_path)


def test_ac5_review_reference_gesture_green_twin(tmp_path):
    _green(_A, tmp_path)


def test_ac5_review_reference_gesture_is_the_bare_command():
    _bare(_A)


def test_ac5_review_skill_gesture_red(tmp_path):
    _red(_B, tmp_path)


def test_ac5_review_skill_gesture_green_twin(tmp_path):
    _green(_B, tmp_path)


def test_ac5_review_skill_gesture_is_the_bare_command():
    _bare(_B)


def test_ac5_dossier_7a_gesture_red(tmp_path):
    _red(_C, tmp_path)


def test_ac5_dossier_7a_gesture_green_twin(tmp_path):
    _green(_C, tmp_path)


def test_ac5_dossier_7a_gesture_is_the_bare_command():
    _bare(_C)
