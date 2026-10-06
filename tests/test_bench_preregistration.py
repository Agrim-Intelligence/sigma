"""#344, #502 — the benchmark method is committed before benchmark execution, and its decision rule
is the exact paired test that fits 15 tasks (not the superseded bootstrap bound)."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "bench" / "preregistration.md"


def _text():
    return DOC.read_text(encoding="utf-8")


def _section(text, heading):
    start = text.index(heading)
    nxt = re.search(r"^## ", text[start + len(heading):], re.M)
    return text[start:start + len(heading) + nxt.start()] if nxt else text[start:]


def _live(text):
    """Everything above the Deviations log: the rules in force."""
    return text[:text.index("## Deviations")]


def test_preregistration_fixes_the_arms_tasks_metrics_and_analysis_before_runs():
    text = _text()
    for heading in ("## Arms", "## Held constant", "## Tasks", "## Isolation", "## Metrics",
                    "## Analysis", "## Go threshold", "## Owner decisions",
                    "## Operating characteristics", "## Runs", "## Deviations"):
        assert heading in text
    for arm in ("A1 Sigma", "A2 plain agent", "A3 matched-spend"):
        assert arm in text
    arm_rows = [line for line in text.splitlines() if line.startswith("| A") and line[3].isdigit()]
    assert len(arm_rows) == 3
    assert "about 15 tasks" in text
    assert "one repeat per arm and task" in text
    assert "three traps" in text
    assert "outside the Sigma team" in text
    assert "agent-authored" in text and "weaker claim" in text
    assert "frozen task set" in text
    assert "~/.sigma-ops/bench/hidden/" in text
    assert "The predecessor is no longer an arm" in text
    assert "no launch claim compares" in text


def test_go_threshold_is_an_exact_paired_test_with_owner_decisions():
    text = _text()
    live = _live(text)
    # the old bootstrap bound is not a rule in force
    for stale in ("10,000 resamples", "20261001", "−5 points", "lower 95% CI bound"):
        assert stale not in live, stale
    go = _section(text, "## Go threshold")
    for needle in ("exact one-sided binomial", "discordant", "alpha = 0.05", "GO", "NO-GO",
                   "INCONCLUSIVE", "is never a win", "intersection-union", "margin of 0 tasks",
                   "H0", "H1", "Binomial(D, 1/2)"):
        assert needle in go, needle
    assert "python3 evals/bench/decision_rule.py --check docs/bench/preregistration.md" in text
    owner = _section(text, "## Owner decisions")
    for needle in ("alpha", "margin", "2026-10-05", "DECIDED"):
        assert needle in owner, needle
    assert owner.count("DECIDED") >= 4
    oc = _section(text, "## Operating characteristics")
    assert "<!-- operating-characteristics:begin -->" in oc and "<!-- operating-characteristics:end -->" in oc
    assert "no non-inferiority claim" in oc
    assert "low power" in oc


def test_claim_wording_has_no_duplicated_clause_and_covers_every_outcome():
    text = _text()
    live = _live(text)
    analysis = _section(text, "## Analysis")
    reporting = ('**"On this frozen reduced benchmark, Sigma won W and lost L of N paired tasks against ARM '
                 '(observed paired pass-rate difference X percentage points; exact one-sided sign-test p = P)."**')
    assert reporting in analysis
    for sentence in (
            '**"This meets the pre-registered release check (exact one-sided sign test at alpha = 0.05 '
            'against each other arm)."**',
            '**"The pre-registered release check was inconclusive: this benchmark does not show whether '
            'Sigma is better or worse than the other arms."**',
            '**"The pre-registered release check was not passed: Sigma was significantly worse than ARM '
            'on this frozen benchmark."**'):
        assert sentence in analysis, sentence
    clause = "evidence of superiority or non-inferiority beyond these tasks"
    assert live.count(clause) == 1, "the forbidden-claim clause is stated once, not twice"
    assert "forbidden" in analysis
    assert "general performance estimate" in analysis


def test_old_threshold_is_kept_but_marked_superseded_in_the_deviations_log():
    text = _text()
    dev = _section(text, "## Deviations")
    assert "2026-10-03" in dev and "#502" in dev
    assert "SUPERSEDED" in dev
    marker = dev.index("SUPERSEDED")
    for old in ("lower 95% CI bound for `A1 − arm` is at", "least −5 points against every other arm",
                "10,000 resamples and seed 20261001"):
        assert old in dev, old
        assert dev.index(old) > marker, "the old text appears only after its superseded marker"
    assert "Replaced the go threshold with an exact paired test" in dev
    assert "What it replaces" in dev and "What replaces them" in dev


def test_preregistration_deviations_section_is_a_real_post_run_log():
    text = _text()
    assert "## Deviations" in text
    assert "2026-10-03" in text
    assert "Reduced launch scope before the first benchmark run" in text
    assert "No benchmark task has run under either design" in text
    dev = _section(text, "## Deviations")
    assert "#502" in dev
    assert "No benchmark task has run" in dev.split("#502", 1)[1]


# -- 2026-10-06: the benchmark runs on the owner's subscription (tokens, batches, resume) -------------

BUDGET = ROOT / "docs" / "bench" / "token-budget.md"
FROZEN = "12232c788c133338ffe46d05298fd677e8d6f542"
CEILING = 210_000_000


def _entry(text, date):
    dev = _section(text, "## Deviations")
    start = dev.index("- **" + date)
    nxt = re.search(r"^- \*\*", dev[start + 3:], re.M)
    return dev[start:start + 3 + nxt.start()] if nxt else dev[start:]


def _budget_rows():
    rows = {}
    for line in BUDGET.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if re.match(r"^\| (S\d\d|P\d) \|", line):
            ident, kind, *numbers = cells
            rows[ident] = (kind, [float(n.replace(",", "")) for n in numbers])
    return rows


def test_the_subscription_deviation_is_recorded_before_any_arm_ran():
    text = _text()
    entry = _entry(text, "2026-10-06")
    for needle in ("subscription", "$150", "API-key", "supersedes", FROZEN, "No arm has been run",
                   "indicative", "not-run", "token ceiling", f"{CEILING:,}", "no dollars-per-task claim",
                   "exact one-sided sign test", "INCONCLUSIVE", "--resume", "--batch-pairs",
                   "token-budget.md", "unmeasured"):
        assert needle in entry, needle
    assert "bill" in entry and "never a bill" in entry.replace("\n", " ").replace("  ", " ")


def test_the_rules_in_force_speak_in_tokens_not_dollars_and_name_the_resume_lever():
    text = _text()
    live = _live(text)
    for stale in ("$150", "$15 ", "API-key authentication", "re-run once", "spend reaches A1's spend"):
        assert stale not in live, stale
    arms = _section(text, "## Arms")
    assert "token spend reaches A1's token spend" in arms
    assert "tokens per passing run" in _section(text, "## Metrics")
    runs = _section(text, "## Runs")
    for needle in ("not-run", "--resume", "--batch-pairs", "rate limit", "never recorded as a failure"):
        assert needle in runs, needle
    assert f"{CEILING:,}" in live


def test_the_token_ceiling_is_recomputed_from_the_measured_table():
    """Control: edit one measured figure (or the ceiling) and this goes red."""
    rows = _budget_rows()
    assert len(rows) == 24 and sum(k == "sigma-loop" for k, _ in rows.values()) == 22
    ratios = []
    for ident, (kind, numbers) in rows.items():
        parts, total, usd = numbers[:4], numbers[4], numbers[5]
        assert sum(parts) == total, ident
        ratios.append(total / usd)
    floor_ceiling = int(150 * min(ratios) // 10_000_000 * 10_000_000)
    assert floor_ceiling == CEILING
    sigma = [n[4] for k, n in rows.values() if k == "sigma-loop"]
    plain = [n[4] for k, n in rows.values() if k == "plain"]
    worst_pair_set = max(sigma) + max(plain) + (max(sigma) + max(plain))
    assert 15 * worst_pair_set < CEILING, "the observed worst case must fit under the ceiling"
    text = BUDGET.read_text(encoding="utf-8")
    assert "/Users/" not in text and ".jsonl" not in text
    assert "not a bound" in text and "effective n" in text
