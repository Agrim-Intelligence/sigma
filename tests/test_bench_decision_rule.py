"""#502 — the benchmark decision rule and the operating characteristics the pre-registration publishes.

The rule is an exact one-sided binomial (sign) test on the discordant tasks of a paired comparison.
These tests pin (a) the rule against hand-computed tails and a brute-force enumeration written
without the script's own functions, (b) the numbers the document publishes against the script's
output, on the exact command the document gives, and (c) that the drift guard actually fails.
"""
import importlib.util
import itertools
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "bench" / "decision_rule.py"
DOC = ROOT / "docs" / "bench" / "preregistration.md"
ALPHA = Fraction(1, 20)


@lru_cache(maxsize=1)
def _rule():
    assert SCRIPT.exists(), "evals/bench/decision_rule.py is missing"
    spec = importlib.util.spec_from_file_location("bench_decision_rule", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documented_gesture(doc_path):
    """The `--check` command exactly as the document gives it, with only the doc path swapped."""
    text = DOC.read_text(encoding="utf-8")
    found = re.search(r"^python3 (evals/bench/decision_rule\.py --check \S+)$", text, re.M)
    assert found, "the document gives no `--check` command"
    args = found.group(1).split()
    args[-1] = str(doc_path)
    return [sys.executable] + args


def test_min_wins_matches_hand_computed_binomial_tails():
    rule = _rule()
    expected = [None, None, None, None, 5, 6, 7, 7, 8, 9, 9, 10, 10, 11, 12, 12, 13, 13]
    assert [rule.min_wins(d, ALPHA) for d in range(1, 19)] == expected
    # hand-computed numerators over 2**D: 4 of 4 is 1/16 (> 0.05), 5 of 5 is 1/32, 7 of 8 is 9/256,
    # 10 of 13 is 378/8192
    assert rule.tail(4, 4) == Fraction(1, 16)
    assert rule.tail(5, 5) == Fraction(1, 32)
    assert rule.tail(8, 7) == Fraction(9, 256)
    assert rule.tail(13, 10) == Fraction(378, 8192)
    # equality passes (<=), a hair below does not: the comparison is exact, never a float
    assert rule.min_wins(5, Fraction(1, 32)) == 5
    assert rule.min_wins(5, Fraction(1, 32) - Fraction(1, 10 ** 9)) is None


def test_classify_covers_go_nogo_and_inconclusive():
    rule = _rule()
    cases = {(5, 0): "GO", (0, 5): "NO-GO", (4, 0): "INCONCLUSIVE", (0, 4): "INCONCLUSIVE",
             (3, 1): "INCONCLUSIVE", (0, 0): "INCONCLUSIVE", (7, 1): "GO", (1, 7): "NO-GO",
             (6, 2): "INCONCLUSIVE", (12, 3): "GO", (11, 4): "INCONCLUSIVE", (3, 12): "NO-GO",
             (3, 11): "NO-GO", (4, 11): "INCONCLUSIVE"}
    for (w, l), verdict in cases.items():
        assert rule.classify(w, l, ALPHA) == verdict, (w, l)
    assert rule.classify(4, 0, Fraction(1, 10)) == "GO"          # a looser alpha changes the verdict
    for w in range(0, 16):                                        # never both, whatever the counts
        for l in range(0, 16 - w):
            assert not (rule.min_wins(w + l, ALPHA) is not None
                        and w >= rule.min_wins(w + l, ALPHA) and l >= rule.min_wins(w + l, ALPHA))


def _brute(n, pw, pl, alpha):
    """GO and NO-GO probabilities by enumerating all 3**n task outcomes and all 2**D coin sequences."""
    pt = 1 - pw - pl
    go = nogo = Fraction(0)
    for seq in itertools.product("WLT", repeat=n):
        w, l = seq.count("W"), seq.count("L")
        weight = pw ** w * pl ** l * pt ** (n - w - l)
        d = w + l
        coins = list(itertools.product((0, 1), repeat=d))
        p_win_tail = Fraction(sum(1 for c in coins if sum(c) >= w), len(coins))
        p_loss_tail = Fraction(sum(1 for c in coins if sum(c) >= l), len(coins))
        if d and p_win_tail <= alpha:
            go += weight
        elif d and p_loss_tail <= alpha:
            nogo += weight
    return go, nogo


def test_probabilities_match_brute_force_enumeration():
    rule = _rule()
    scenarios = [(Fraction(1, 5), Fraction(1, 5)), (Fraction(3, 10), Fraction(1, 10)),
                 (Fraction(1, 10), Fraction(3, 10)), (Fraction(2, 5), Fraction(0)),
                 (Fraction(1, 2), Fraction(1, 4))]
    for n, alpha in ((3, Fraction(1, 10)), (5, Fraction(1, 20)), (6, Fraction(1, 10)), (7, Fraction(1, 10)),
                     (7, Fraction(1, 20))):
        for pw, pl in scenarios:
            assert rule.outcome_probs(n, pw, pl, alpha) == _brute(n, pw, pl, alpha), (n, pw, pl, alpha)
    # the multinomial really sums to one
    assert sum(rule.pmf(6, Fraction(3, 10), Fraction(1, 5)).values()) == 1


def test_published_tables_match_the_script_on_the_documented_gesture():
    rule = _rule()
    text = DOC.read_text(encoding="utf-8")
    assert rule.published(text) == rule.render()
    done = subprocess.run(_documented_gesture("docs/bench/preregistration.md"), cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr


def test_check_fails_when_a_published_number_drifts(tmp_path):
    rule = _rule()
    original = DOC.read_text(encoding="utf-8")
    block = rule.published(original)
    assert block, "the document has no published block"
    drifted_doc = original.replace("| 5 | 5 | 0.031 |", "| 5 | 5 | 0.030 |", 1)
    assert drifted_doc != original, "the control must change a published number"
    no_markers = original.replace(rule.BEGIN, "").replace(rule.END, "")
    for name, text in (("drifted.md", drifted_doc), ("unmarked.md", no_markers)):
        copy = tmp_path / name
        copy.write_text(text, encoding="utf-8")
        done = subprocess.run(_documented_gesture(copy), cwd=ROOT, capture_output=True, text=True, timeout=300)
        assert done.returncode == 1, (name, done.stdout, done.stderr)
        assert "does not contain the current operating-characteristics block" in done.stderr


def test_prose_figures_match_the_code():
    rule = _rule()
    text = DOC.read_text(encoding="utf-8")
    prose = text.replace(rule.published(text), "")
    n = 15
    assert min(2 * rule.min_wins(d, ALPHA) - d for d in range(1, n + 1) if rule.min_wins(d, ALPHA)) == 5
    assert "5 of 5 discordant tasks" in prose
    d = Fraction(2, 5)
    for diff, label in ((Fraction(1, 5), "+20"), (Fraction(3, 10), "+30"), (Fraction(2, 5), "+40")):
        go, _ = rule.probs_for(n, d, diff, ALPHA)
        assert "%s points %s" % (label, rule.prob(go)) in prose, label
    worst, _ = rule.worst_case_go(n, Fraction(0), ALPHA)
    assert "worst case, %s at 15 tasks" % rule.prob(worst) in prose
    # the guarantee the document states: a wrong GO never exceeds alpha for any n in range, at a tie
    # or when Sigma is worse; and the NO-GO side is the mirror image, so two arms give at most 2 alpha
    for tasks in rule.N_RANGE:
        for delta in (Fraction(0), Fraction(-1, 10), Fraction(-1, 5)):
            assert rule.worst_case_go(tasks, delta, ALPHA)[0] <= ALPHA
        go, nogo = rule.probs_for(tasks, Fraction(1, 2), Fraction(0), ALPHA)
        assert go == nogo
    needed = {k: rule.tasks_needed(Fraction(2, 5), Fraction(k, 10)) for k in (2, 3, 4)}
    for k, tasks in needed.items():
        assert "+%d points needs %d tasks" % (k * 10, tasks) in prose, (k, tasks)
    assert rule.probs_for(needed[2], Fraction(2, 5), Fraction(1, 5), ALPHA)[0] >= Fraction(4, 5)
    assert rule.probs_for(needed[2] - 1, Fraction(2, 5), Fraction(1, 5), ALPHA)[0] < Fraction(4, 5)
