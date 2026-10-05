#!/usr/bin/env python3
"""Exact benchmark decision rule and its operating characteristics (stdlib only).

The rule (docs/bench/preregistration.md, "Go threshold") looks only at the discordant
tasks of one paired comparison: W tasks Sigma passed and the other arm failed, L tasks the
other arm passed and Sigma failed.  Ties carry no information and are ignored.  Conditional
on D = W + L, under the null "no difference" W ~ Binomial(D, 1/2), so the one-sided exact
p-value is a binomial tail computed with ``math.comb`` and compared with alpha as an exact
fraction, never a float.

    python3 evals/bench/decision_rule.py                 # print the published tables
    python3 evals/bench/decision_rule.py --check DOC     # exit 1 if DOC's tables drifted

Probabilities are exact rationals (``fractions.Fraction``) rounded only when printed, so the
output is identical on every platform.  The model behind every probability is stated in the
document: independent, identically distributed tasks, each Sigma-win with probability pw,
Sigma-loss with probability pl, tie otherwise.  That is an assumption about the task pool,
not something this script can check.

The script reads one file at most (the document named by ``--check``) and writes only to
standard output.
"""
import argparse
import sys
from fractions import Fraction
from functools import lru_cache
from math import comb, factorial, gcd
from pathlib import Path

ALPHA = Fraction(1, 20)          # one-sided alpha; decided by the owner 2026-10-05
N_TASKS = 15                     # the frozen design
N_RANGE = range(12, 19)          # the 12 to 18 task range the document reports
MAX_D = 18
BEGIN = "<!-- operating-characteristics:begin -->"
END = "<!-- operating-characteristics:end -->"
DISCORDANCE = (Fraction(1, 5), Fraction(2, 5))
DELTAS = tuple(Fraction(k, 10) for k in (-2, -1, 0, 1, 2, 3, 4))
SELECTED = ((Fraction(2, 5), Fraction(-1, 5)), (Fraction(2, 5), Fraction(0)),
            (Fraction(2, 5), Fraction(1, 5)), (Fraction(2, 5), Fraction(3, 10)),
            (Fraction(2, 5), Fraction(2, 5)))
GRID = 100                       # discordance-rate grid for "worst case over d"
ALPHAS = (Fraction(1, 40), Fraction(1, 20), Fraction(1, 10), Fraction(1, 5))


def tail(d, k):
    """P(Binomial(d, 1/2) >= k), exactly."""
    return Fraction(sum(comb(d, j) for j in range(k, d + 1)), 2 ** d)


@lru_cache(maxsize=None)
def min_wins(d, alpha=ALPHA):
    """Smallest W among d discordant tasks whose exact one-sided p is <= alpha, or None."""
    for k in range(d + 1):
        if tail(d, k) <= alpha:
            return k
    return None


def classify(wins, losses, alpha=ALPHA):
    """GO, NO-GO or INCONCLUSIVE for one comparison (a NO-GO is the same test the other way)."""
    k = min_wins(wins + losses, alpha)
    if k is None:
        return "INCONCLUSIVE"
    if wins >= k:
        return "GO"
    if losses >= k:
        return "NO-GO"
    return "INCONCLUSIVE"


def _integer_weights(n, pw, pl):
    """Each (wins, losses) among n tasks with weight an integer over the returned denominator:
    multinomial coefficient * a**w * b**l * c**t over m**n, where pw = a/m, pl = b/m, tie = c/m."""
    pt = 1 - pw - pl
    m = 1
    for x in (pw, pl, pt):
        m = m * x.denominator // gcd(m, x.denominator)
    a, b, c = int(pw * m), int(pl * m), int(pt * m)
    weights = {(w, l): factorial(n) // (factorial(w) * factorial(l) * factorial(n - w - l))
               * a ** w * b ** l * c ** (n - w - l)
               for w in range(n + 1) for l in range(n - w + 1)}
    return weights, m ** n


def pmf(n, pw, pl):
    """Exact multinomial probability of each (wins, losses) among n tasks."""
    weights, denominator = _integer_weights(n, pw, pl)
    return {key: Fraction(value, denominator) for key, value in weights.items()}


def outcome_probs(n, pw, pl, alpha=ALPHA):
    weights, denominator = _integer_weights(n, pw, pl)
    go = nogo = 0
    for (w, l), weight in weights.items():
        verdict = classify(w, l, alpha)
        if verdict == "GO":
            go += weight
        elif verdict == "NO-GO":
            nogo += weight
    return Fraction(go, denominator), Fraction(nogo, denominator)


def probs_for(n, d, delta, alpha=ALPHA):
    return outcome_probs(n, (d + delta) / 2, (d - delta) / 2, alpha)


def worst_case_go(n, delta, alpha=ALPHA):
    """Largest P(GO) over the discordance-rate grid, for a true difference delta <= 0."""
    best, at = Fraction(0), None
    for i in range(1, GRID + 1):
        d = Fraction(i, GRID)
        if d < abs(delta):
            continue
        go, _ = probs_for(n, d, delta, alpha)
        if go > best:
            best, at = go, d
    return best, at


def ni_min_net(n, margin_tasks, alpha):
    """Smallest c so that "reject when W - L >= c" has size <= alpha for the null that Sigma is
    worse by at least margin_tasks tasks, with the size maximised over a grid of discordance
    rates (a grid maximum, not a proof of the supremum)."""
    delta0 = Fraction(-margin_tasks, n)
    worst = {}
    for i in range(1, GRID + 1):
        d = Fraction(i, GRID)
        if d < abs(delta0):
            continue
        dist = {}
        for (w, l), p in pmf(n, (d + delta0) / 2, (d - delta0) / 2).items():
            dist[w - l] = dist.get(w - l, 0) + p
        run = Fraction(0)
        for c in range(n, -n - 1, -1):
            run += dist.get(c, 0)
            worst[c] = max(worst.get(c, Fraction(0)), run)
    for c in range(-n, n + 1):
        if worst[c] <= alpha:
            return c, worst[c]
    return None, None


def tasks_needed(d, delta, target=Fraction(4, 5), alpha=ALPHA, limit=120):
    """Fewest tasks n at which P(GO) first reaches target (it can dip again at the next n: the
    test is discrete), for one comparison with discordant rate d and true difference delta."""
    for n in range(5, limit + 1):
        if probs_for(n, d, delta, alpha)[0] >= target:
            return n
    return None


def bootstrap_lower(wins, losses, n=N_TASKS, tail_mass=Fraction(1, 40)):
    """Lower 2.5 percent point of the paired bootstrap of the mean difference, as a fraction, in
    the limit of infinitely many resamples (the superseded rule used 10,000, so its value can
    differ from this only by Monte Carlo error near a percentile boundary)."""
    dist = {}
    for (w, l), p in pmf(n, Fraction(wins, n), Fraction(losses, n)).items():
        dist[w - l] = dist.get(w - l, 0) + p
    running = Fraction(0)
    for net in sorted(dist):
        running += dist[net]
        if running >= tail_mass:
            return Fraction(net, n)


def superseded_table():
    rows = ["| Sigma wins | Sigma losses | ties | superseded rule: lower bound (points) | passes superseded rule "
            "(bound >= -5) | this rule |", "|---|---|---|---|---|---|"]
    for w, l in ((0, 0), (2, 0), (3, 0), (3, 1), (4, 1), (5, 0), (5, 1), (6, 2), (7, 1), (8, 2)):
        low = bootstrap_lower(w, l)
        rows.append("| %d | %d | %d | %s | %s | %s |" % (w, l, N_TASKS - w - l, pct(low),
                                                     "yes" if low >= Fraction(-1, 20) else "no",
                                                     classify(w, l)))
    return rows


def pct(x):
    return "%.1f" % (float(x) * 100)


def whole(x):
    return "%d" % round(float(x) * 100)


def signed(x):
    n = round(float(x) * 100)
    return "%+d" % n if n else "0"


def prob(x):
    return "%.3f" % float(x)


def outcome_table(alpha=ALPHA):
    rows = ["| Discordant tasks D | GO needs wins W of at least | exact p at that W | "
            "NO-GO needs losses L of at least | INCONCLUSIVE when |", "|---|---|---|---|---|"]
    for d in range(1, MAX_D + 1):
        k = min_wins(d, alpha)
        if k is None:
            rows.append("| %d | cannot (even %d of %d has p = %s) | - | cannot | always |"
                        % (d, d, d, prob(tail(d, d))))
        else:
            lo, hi = d - k + 1, k - 1
            span = "W = %d to %d" % (lo, hi) if lo <= hi else "never"
            rows.append("| %d | %d | %s | %d | %s |" % (d, k, prob(tail(d, k)), k, span))
    return rows


def scenario_table(n, alpha=ALPHA):
    rows = ["| Discordant rate d | True difference (points) | P(GO) one arm | P(NO-GO) one arm | "
            "P(GO) against both arms, between |", "|---|---|---|---|---|"]
    for d in DISCORDANCE:
        for delta in DELTAS:
            if abs(delta) > d:
                continue
            go, nogo = probs_for(n, d, delta, alpha)
            lo = max(Fraction(0), 2 * go - 1)
            rows.append("| %s | %s | %s | %s | %s and %s |" % (whole(d), signed(delta),
                        prob(go), prob(nogo), prob(lo), prob(go)))
    return rows


def range_table(alpha=ALPHA):
    head = "| Tasks n | " + " | ".join("d=%s, diff %s" % (whole(d), signed(x))
                                      for d, x in SELECTED) + " |"
    rows = [head, "|---|" + "---|" * len(SELECTED)]
    for n in N_RANGE:
        cells = [prob(probs_for(n, d, x, alpha)[0]) for d, x in SELECTED]
        rows.append("| %d | %s |" % (n, " | ".join(cells)))
    return rows


def size_table(alpha=ALPHA):
    rows = ["| Tasks n | worst-case P(GO) when Sigma truly ties (diff 0) | at discordant rate | "
            "worst-case P(GO) when Sigma is truly 10 points worse | at discordant rate |",
            "|---|---|---|---|---|"]
    for n in N_RANGE:
        a, ad = worst_case_go(n, Fraction(0), alpha)
        b, bd = worst_case_go(n, Fraction(-1, 10), alpha)
        rows.append("| %d | %s | %s | %s | %s |" % (n, prob(a), whole(ad), prob(b), whole(bd)))
    return rows


def alpha_table():
    rows = ["| One-sided alpha | fewest net wins that can pass (W - L) | worst-case P(GO) at a true tie | "
            "P(GO) at +20 points, d=40 | P(GO) at +30 points, d=40 | P(GO) at +40 points, d=40 |",
            "|---|---|---|---|---|---|"]
    n = N_TASKS
    for alpha in ALPHAS:
        nets = [2 * k - d for d in range(1, n + 1) for k in [min_wins(d, alpha)] if k is not None]
        tie, _ = worst_case_go(n, Fraction(0), alpha)
        vals = [probs_for(n, Fraction(2, 5), Fraction(x, 10), alpha)[0] for x in (2, 3, 4)]
        rows.append("| %s | %d | %s | %s |" % (prob(alpha), min(nets), prob(tie),
                                              " | ".join(prob(v) for v in vals)))
    return rows


def ni_table():
    rows = ["| Margin in tasks | Margin in points | one-sided alpha | fewest net wins (W - L) that "
            "certify it | size reached |", "|---|---|---|---|---|"]
    for m in (0, 1, 2, 3):
        for alpha in (Fraction(1, 20), Fraction(1, 10)):
            c, size = ni_min_net(N_TASKS, m, alpha)
            rows.append("| %d | %s | %s | %d | %s |"
                        % (m, pct(Fraction(m, N_TASKS)), prob(alpha), c, prob(size)))
    return rows


def needed_table(alpha=ALPHA):
    rows = ["| True difference (points), d=40 | Tasks needed for P(GO) to first reach 0.800 |", "|---|---|"]
    for x in (2, 3, 4):
        needed = tasks_needed(Fraction(2, 5), Fraction(x, 10), alpha=alpha)
        rows.append("| %s | %s |" % (signed(Fraction(x, 10)), needed if needed else "more than 120"))
    return rows


def render(alpha=ALPHA):
    """The exact text the document publishes between BEGIN and END."""
    out = [BEGIN, ""]
    out.append("Outcomes by discordant count, one comparison, one-sided alpha = %s (identical for any "
               "number of tasks; D cannot exceed n):" % prob(alpha))
    out += [""] + outcome_table(alpha) + [""]
    out.append("Probabilities for n = %d tasks, one comparison unless the column says both arms "
               "(d is the share of tasks on which the two arms differ; difference = Sigma minus "
               "the other arm, in points). P(GO) ignores the trap-catch condition, which can only lower it, "
               "and the both-arms column assumes both arms have this same d and difference:" % N_TASKS)
    out += [""] + scenario_table(N_TASKS, alpha) + [""]
    out.append("P(GO) for one comparison at other task counts:")
    out += [""] + range_table(alpha) + [""]
    out.append("Wrong-GO probability when Sigma truly ties or is worse, maximised over discordant "
               "rates 1 to 100 percent in steps of 1 (a grid maximum):")
    out += [""] + size_table(alpha) + [""]
    out.append("Tasks needed for 80 percent power (one comparison, same alpha):")
    out += [""] + needed_table(alpha) + [""]
    out.append("The superseded bootstrap rule on the same counts (n = %d, one comparison, exact "
               "infinite-resample limit):" % N_TASKS)
    out += [""] + superseded_table() + [""]
    out.append("Alpha alternatives considered at n = %d (decided: 0.05; one comparison):" % N_TASKS)
    out += [""] + alpha_table() + [""]
    out.append("Rejected alternative, non-inferiority by an unconditional test that rejects when "
               "W - L is at least c, size maximised over a discordant-rate grid in steps of 1 percent:")
    out += [""] + ni_table() + ["", END]
    return "\n".join(out)


def published(text):
    """The block between the markers, or None."""
    if BEGIN not in text or END not in text:
        return None
    start = text.index(BEGIN)
    return text[start:text.index(END, start) + len(END)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", metavar="DOC",
                        help="exit 1 unless DOC contains exactly the tables this script prints")
    args = parser.parse_args(argv)
    block = render()
    if args.check is None:
        print(block)
        return 0
    found = published(Path(args.check).read_text(encoding="utf-8"))
    if found != block:
        print("decision_rule.py: %s does not contain the current operating-characteristics block; "
              "regenerate it with `python3 evals/bench/decision_rule.py`" % args.check,
              file=sys.stderr)
        return 1
    print("operating characteristics in %s match" % args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
