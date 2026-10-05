"""Keep the launch review plan, the scorecard and the decision rule in step (#350).

`docs/launch/review-plan.md` is the page a human reads; `docs/launch/scorecard.json` is what
`tools/readiness/decide.py` scores; `docs/launch/decision-rule.md` pins which dimensions exist, what
they are called and which gate. They must not disagree:

  - every `D<n>` in the plan's Dimensions table is in the scorecard with the same name and the same
    gating flag, and the reverse (D0 is the one row that is not scored: its gating cell reads
    `prerequisite` and the scorecard has no D0);
  - the plan's dimension names and gating flags equal the decision rule's two tables;
  - the review ceiling line says 75M and 40M and not the superseded 150M;
  - the nine reviewer rules are numbered 1 to 9, and rule 9 is the phantom-blocker rule;
  - every dimension has a row saying what already exists on main and what is still to be produced,
    and every file path cited as existing is on disk;
  - D13 says it is not scoreable until counsel answers; D3 says "as amended" and names 3 arms;
  - the plan contains no sentence the loop's own blocker scan would read as a dependency edge;
  - the calibration record (#361) has one section per pilot, each measured or "Not measured" with a reason, its rows
    add up, and the plan's measured ceilings carry their formulas, the headroom factor and the pilot counters.

Stdlib + pytest only; no network. Short test names and one-line assertion messages on purpose:
`loop.py verify` reads a red run's reason from pytest's one-line summary.

The documented gesture (the last section of the plan): `python -m pytest tests/test_review_plan.py -q`.
Control, seen red and recorded in the PR: flip D12's `gating` flag in `scorecard.json` to `true`
(`test_sync_scorecard_matches_plan` fails), and put `this needs the model field from #3` into the
plan (`test_plan_has_no_phantom_blocker` fails). The `test_control_*` tests feed those faults to the
same helpers on synthetic text, so the guard cannot silently stop failing.
"""
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "launch" / "review-plan.md"
CARD = ROOT / "docs" / "launch" / "scorecard.json"
RULE = ROOT / "docs" / "launch" / "decision-rule.md"
BLOCKER_SCAN = ROOT / "skills" / "sigma-loop" / "scripts" / "blocker_scan.py"

HEADINGS = ["## Status", "## Scope", "## Dimensions", "## Reviewer rules",
            "## Execution order and ceilings", "## What changed from #307 and why"]
GATING_WORD = {"yes": True, "no": False}


def _check(ok, message):
    if not ok:
        raise AssertionError(" ".join(str(message).split()))


def _section(text, heading, stop="\n## "):
    start = text.index(heading)
    end = text.find(stop, start + len(heading))
    return text[start:end if end != -1 else len(text)]


def _rows(block):
    """Table rows (cells stripped) of a block; the header and the `---` rule are dropped."""
    out = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if set(cells[0]) <= set("-: ") or cells[0] in ("#", "Id"):
            continue
        out.append(cells)
    return out


def plan_dimensions(text):
    """{id: (name, gating cell)} from the Dimensions table (before its evidence subsection)."""
    block = _section(text, "## Dimensions", "\n### ")
    return {r[0]: (r[1], r[4]) for r in _rows(block) if re.fullmatch(r"D\d+", r[0])}


def card_dimensions(card):
    return {d["id"]: (d["name"], d["gating"]) for d in card["dimensions"]}


def rule_dimensions(text):
    """{id: (name, gating bool)} from decision-rule.md's two dimension tables."""
    gating = _section(text, "Gating dimensions:", "Informational dimensions:")
    info = _section(text, "Informational dimensions:", "Market comparison blocks")
    out = {}
    for block, flag in ((gating, True), (info, False)):
        for r in _rows(block):
            if re.fullmatch(r"D\d+", r[0]):
                out[r[0]] = (r[1], flag)
    return out


def sync_problems(plan_text, card, rule_text):
    plan, scored, rule = plan_dimensions(plan_text), card_dimensions(card), rule_dimensions(rule_text)
    problems = []
    if plan.get("D0", (None, None))[1] != "prerequisite":
        problems.append("D0 must be the prerequisite row")
    for did in sorted(set(plan) | set(scored) | set(rule), key=lambda s: int(s[1:])):
        if did == "D0":
            if did in scored or did in rule:
                problems.append("D0 is not a scored dimension")
            continue
        if did not in plan:
            problems.append(f"{did} is in the scorecard or rule but not in the plan")
            continue
        name, cell = plan[did]
        if cell not in GATING_WORD:
            problems.append(f"{did} gating cell {cell!r} is not yes or no")
            continue
        for label, other in (("scorecard", scored), ("decision rule", rule)):
            if did not in other:
                problems.append(f"{did} is in the plan but not in the {label}")
            elif other[did] != (name, GATING_WORD[cell]):
                problems.append(f"{did} differs from the {label}: plan {(name, GATING_WORD[cell])} {label} {other[did]}")
    return problems


def blocker_hits(text):
    spec = importlib.util.spec_from_file_location("review_plan_blocker_scan", BLOCKER_SCAN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [m.group(0) for m in module._BLOCK_RE.finditer(text)]


def _plan():
    _check(PLAN.exists(), "docs/launch/review-plan.md is missing")
    return PLAN.read_text(encoding="utf-8")


def _card():
    return json.loads(CARD.read_text(encoding="utf-8"))


def test_plan_headings_present_in_order():
    text = _plan()
    positions = [text.find(h) for h in HEADINGS]
    _check(all(p >= 0 for p in positions), f"missing heading in {HEADINGS}: {positions}")
    _check(positions == sorted(positions), f"headings out of order: {positions}")
    _check(re.search(r"^## Facts as of \d{4}-\d{2}-\d{2}$", text, re.M), "no dated Facts heading")


def test_sync_scorecard_matches_plan():
    problems = sync_problems(_plan(), _card(), RULE.read_text(encoding="utf-8"))
    _check(not problems, "; ".join(problems))


def test_control_flipped_gating_flag_is_caught():
    card = _card()
    next(d for d in card["dimensions"] if d["id"] == "D12")["gating"] = True
    problems = sync_problems(_plan_or_sample(), card, RULE.read_text(encoding="utf-8"))
    _check(any("D12" in p for p in problems), f"a flipped D12 flag went unseen: {problems}")


def test_control_dropped_dimension_is_caught():
    card = _card()
    card["dimensions"] = [d for d in card["dimensions"] if d["id"] != "D13"]
    problems = sync_problems(_plan_or_sample(), card, RULE.read_text(encoding="utf-8"))
    _check(any("D13" in p for p in problems), f"a dropped D13 went unseen: {problems}")


def _plan_or_sample():
    """The real plan when it exists; the control tests then run on exactly the shipped table."""
    return _plan() if PLAN.exists() else SAMPLE_PLAN


SAMPLE_PLAN = "\n".join(
    ["## Dimensions", "| # | Dimension | Measurement | Pass threshold | Gating |", "|---|---|---|---|---|",
     "| D0 | Launch definition | m | t | prerequisite |", "| D12 | Market | m | t | no |",
     "| D13 | Legal and naming | m | t | yes |", "", "## Reviewer rules"])


def test_ceiling_line_is_75m_with_a_40m_checkpoint():
    lines = [line for line in _plan().splitlines() if line.startswith("The review ceiling is")]
    _check(len(lines) == 1, f"expected one ceiling line, found {len(lines)}")
    _check("75M" in lines[0] and "40M" in lines[0], f"ceiling line lacks 75M and 40M: {lines[0]!r}")
    _check("150M" not in lines[0], "the superseded 150M is still the ceiling line")
    text = _plan()
    _check("2026-10-03" in text and "unmeasured" in text, "ceiling decision date or unmeasured label missing")


def test_ceiling_binding_rule_never_drops_units_silently():
    block = _section(_plan(), "**At 75M the review will cover less", "\n## ")
    for needle in ("High-risk units are reviewed in full first", "sample rate is reduced",
                   "listed by id in the scorecard evidence", "never dropped silently"):
        _check(needle in block, f"ceiling-binding rule lacks {needle!r}")
    text = _plan()
    for needle in ("OWNER DECISION", "Recommendation:", "S3 (the pilots) is part of it"):
        _check(needle in text, f"checkpoint counter statement lacks {needle!r}")


def test_reviewer_rules_are_numbered_one_to_nine():
    block = _section(_plan(), "## Reviewer rules")
    numbers = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. ", block, re.M)]
    _check(numbers == list(range(1, 10)), f"reviewer rules numbered {numbers}")
    _check("before an issue number in prose" in block.split("\n9. ", 1)[1], "rule 9 is not the phantom-blocker rule")
    _check("dedup.py" in block and "15 new issues" in block, "dedup and filing-cap rules missing")


def _evidence_rows(text):
    block = _section(text, "### Where each dimension stands on main", "\n## ")
    return {r[0]: r for r in _rows(block) if re.fullmatch(r"D\d+", r[0])}


def test_every_dimension_has_an_exists_versus_to_produce_row():
    rows, dims = _evidence_rows(_plan()), plan_dimensions(_plan())
    _check(set(rows) == set(dims), f"evidence rows {sorted(rows)} differ from dimensions {sorted(dims)}")
    for did, row in rows.items():
        _check(len(row) == 4 and all(row[1:]), f"{did} has an empty evidence cell")
        _check(row[2] != "none", f"{did} has nothing still to produce")


def test_cited_existing_paths_are_on_disk():
    """Covers slash paths and files with a known extension; the one bare root name, LICENSE, is checked by name."""
    _check((ROOT / "LICENSE").is_file() and "`LICENSE`" in _plan(), "LICENSE is cited but not a file")
    missing = []
    for did, row in _evidence_rows(_plan()).items():
        for token in re.findall(r"`([^`]+)`", row[1]):
            looks_like_path = "/" in token or re.search(r"\.(md|json|py|yml|sh)$", token)
            if not looks_like_path or re.search(r"[\s<>*]", token):
                continue
            if not (ROOT / token).exists():
                missing.append(f"{did}: {token}")
    _check(not missing, f"cited as existing but not on disk: {missing}")


def test_d13_waits_for_counsel_and_d3_states_the_reduced_design():
    text, rows = _plan(), _evidence_rows(_plan())
    _check("not scoreable until counsel answers" in rows["D13"][3], "D13 owner cell lacks the counsel statement")
    d3 = next(line for line in text.splitlines() if line.startswith("| D3 |"))
    _check("as amended" in d3 and "3 arms" in d3 and "5 arms" not in d3, f"D3 row is stale: {d3!r}")
    _check("`3.12`" not in text and "Python 3.12" in _section(text, "## Scope"), "macOS 3.12 claim missing from Scope")


def test_owner_gated_pieces_are_all_named():
    text = _plan()
    for needle in ("throwaway repository", "trap author", "legal and naming review", "pilot spend ceiling"):
        _check(needle in text, f"owner-gated piece {needle!r} missing")


def test_plan_has_no_phantom_blocker():
    hits = blocker_hits(_plan())
    _check(not hits, f"the loop's blocker scan would read these as dependency edges: {hits[:3]}")


def test_control_phantom_blocker_scan_can_fail():
    _check(blocker_hits("this needs the model field from #3"), "the blocker scan no longer flags a plain needs sentence")


def test_named_still_to_produce_files_are_absent():
    """A dated snapshot guard: when one of these lands, the plan's table must be updated in the same change."""
    present = [p for p in ("docs/launch/evidence/legal.md", "NOTICE") if (ROOT / p).exists()]
    _check(not present, f"now on main, so update the evidence table: {present}")


CAL = ROOT / "docs" / "launch" / "evidence" / "cost-calibration.md"
CUM_TOKENS = 3887193


def _cal():
    _check(CAL.exists(), "docs/launch/evidence/cost-calibration.md is missing")
    return CAL.read_text(encoding="utf-8")


def _num(cell):
    return int(cell.replace(",", ""))


def run_rows(text):
    """Per-run rows of the pilot tables: (tokens in, out, cache read, cache write, processed)."""
    out = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 11 and re.fullmatch(r"[\d,]+", cells[2]):
            out.append([_num(cells[i]) for i in (2, 3, 4, 5, 6)])
    return out


def row_problems(rows):
    problems = [f"row {r} does not sum" for r in rows if sum(r[:4]) != r[4]]
    if sum(r[4] for r in rows) != CUM_TOKENS:
        problems.append(f"rows sum to {sum(r[4] for r in rows)}, not {CUM_TOKENS}")
    return problems


def test_calibration_has_a_section_per_pilot():
    text = _cal()
    for pilot in ("P-a", "P-b", "P-c", "P-d"):
        _check(f"## {pilot}" in text, f"no section for {pilot}")
    for pilot in ("P-c", "P-d"):
        body = _section(text, f"## {pilot}")
        _check("**Not measured:" in body, f"{pilot} is neither measured nor 'Not measured' with a reason")
    _check("a5c615062313" in text and "e48420fa97f6" in text, "record does not name the commits it measured")
    _check("unpriced_turns: 0" in text and "cost_usd: null" in text, "the unknown-model control is not recorded")


def test_calibration_has_no_host_path():
    _check("/Users/" not in _cal() and "/home/" not in _cal(), "a host path is in the calibration record")


def test_calibration_rows_add_up_to_the_cumulative_spend():
    rows = run_rows(_cal())
    _check(len(rows) == 5, f"expected 5 measured runs, found {len(rows)}")
    _check(not row_problems(rows), "; ".join(row_problems(rows)))
    _check(f"{CUM_TOKENS:,}" in _cal(), "the cumulative token count is not stated")


def test_control_a_wrong_row_is_caught():
    rows = run_rows(_cal())
    rows[0][4] += 1
    _check(row_problems(rows), "a row that does not add up went unseen")


def test_plan_measured_ceilings_carry_formulas_and_counters():
    text = _plan()
    _check("### Measured ceilings" in text, "the plan has no Measured ceilings section")
    block = _section(text, "### Measured ceilings", "\n## ")
    for step in ("S3", "S4, Tier A", "S4, Tier B", "S4 total", "S9"):
        row = next((r for r in block.splitlines() if r.startswith(f"| {step}")), "")
        _check(row, f"no measured-ceiling row for {step}")
        _check(step in ("S3", "S4 total") or "×" in row, f"{step} has no formula")
    for step in ("S4, Tier A", "S4, Tier B"):
        row = next(r for r in block.splitlines() if r.startswith(f"| {step}"))
        _check("× 1.3" in row, f"{step} lacks the headroom factor")
    _check(f"{40_000_000 - CUM_TOKENS:,}" in text and f"{75_000_000 - CUM_TOKENS:,}" in text,
           "tokens remaining under 40M and 75M are not stated")


def test_plan_records_the_pilot_counting_decision():
    text = _plan()
    _check("The owner decided on 2026-10-04" in text, "the pilot-counting decision is not recorded as made")
    _check("Until the owner decides, the pilots do not start" not in text, "the stale open decision is still there")


def pilot_table(text):
    """{(run kind, unit): (lines or None, processed)} from the record's per-run rows."""
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) == 11 and re.fullmatch(r"[\d,]+", cells[2]):
            m = re.fullmatch(r"([AB]\d+)(?: \(([\d,]+)\))?", cells[1])
            kind = "verify" if cells[0].startswith("verification") else "review"
            out[(kind, m.group(1))] = (_num(m.group(2)) if m.group(2) else None, _num(cells[6]))
    return out


def recomputed_s4(text, lines_a, lines_b):
    """(Tier A, Tier B) token ceilings: per-line rates from the record x tier lines x 1.3."""
    rows = pilot_table(text)
    (la1, ra1), (la6, ra6) = rows[("review", "A01")], rows[("review", "A06")]
    rate_a = (ra1 + ra6) / (la1 + la6) + rows[("verify", "A01")][1] / la1
    lb, rb = rows[("review", "B01")]
    rate_b = (rb + rows[("verify", "B01")][1]) / lb
    return rate_a * lines_a * 1.3, rate_b * lines_b * 1.3


def stated_millions(row):
    """The token figure of a Measured ceilings row: its third cell, e.g. `37.03M (28.48M without headroom)`."""
    return float(re.search(r"([\d.]+)M", row.split("|")[3]).group(1)) * 1e6


def test_plan_ceiling_figures_recompute_from_the_record():
    units = json.loads((ROOT / "docs" / "launch" / "review-units.json").read_text(encoding="utf-8"))
    lines_a = sum(u["lines"] for u in units["tier_a"])
    lines_b = sum(u["lines"] for u in units["tier_b_sample"])
    got_a, got_b = recomputed_s4(_cal(), lines_a, lines_b)
    block = _section(_plan(), "### Measured ceilings", "\n## ")
    row_a = next(r for r in block.splitlines() if r.startswith("| S4, Tier A"))
    row_b = next(r for r in block.splitlines() if r.startswith("| S4, Tier B"))
    row_t = next(r for r in block.splitlines() if r.startswith("| S4 total"))
    for stated, want, name in ((stated_millions(row_a), got_a, "Tier A"),
                               (stated_millions(row_b), got_b, "Tier B")):
        _check(abs(stated - want) / want < 0.005, f"{name} ceiling states {stated:.0f}, record recomputes {want:.0f}")
    _check(abs(stated_millions(row_t) - (got_a + got_b)) < 0.02e6,
           "S4 total is not the sum of the tiers")


def test_control_a_changed_line_count_moves_the_recompute():
    text = _cal().replace("A01 (2,267)", "A01 (2,000)", 1)
    _check(recomputed_s4(text, 36018, 11972) != recomputed_s4(_cal(), 36018, 11972),
           "the recompute ignores the line counts in the record")
