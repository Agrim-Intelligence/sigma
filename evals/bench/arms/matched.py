"""The matched-spend arm's retry loop: the plain agent, again and again, until it passes or spend matches A1."""
from pathlib import Path
import shutil
import time

try:
    from .common import MIN_ATTEMPT_SECONDS, MIN_BUDGET_USD, ArmRefusal, remove_tree
except ImportError:  # loaded by path rather than as a package
    from common import MIN_ATTEMPT_SECONDS, MIN_BUDGET_USD, ArmRefusal, remove_tree


def _replace_tree(workdir, source):
    """Make ``workdir`` hold exactly ``source``'s contents: remove, then copy; never overlay."""
    workdir = Path(workdir)
    for child in list(workdir.iterdir()):
        if child.is_dir() and not child.is_symlink():
            remove_tree(child)
        else:
            child.unlink()
    shutil.copytree(source, workdir, symlinks=True, dirs_exist_ok=True)


def run_matched(workdir, attempts_root, *, attempt, visible, guard, match_usd, remaining_usd,
                max_attempts, deadline, spend):
    """Run attempts in fresh copies of ``workdir``'s starting tree and leave the scored one in ``workdir``.

    ``attempt(tree, profile_dir, budget_usd)`` returns an ``Attempt``; ``visible(tree)`` returns whether the
    visible command passes; ``guard()`` re-checks the operator's plugin directories and raises on a change.
    Stops, in this order after each attempt: unpriced attempt, visible pass, spend reached ``match_usd``,
    ``max_attempts`` reached, remaining global ceiling exhausted, ``deadline`` passed.  At least one attempt
    always runs.  Failed attempts are deleted before the next starts.  Returns
    ``(cost_usd_or_None, reason)``; the cost is the cumulative spend of every attempt.
    """
    attempts_root = Path(attempts_root)
    snapshot = attempts_root / "pristine"
    shutil.copytree(workdir, snapshot, symlinks=True)
    spent, number, chosen, stop, unpriced = 0.0, 0, None, "", False
    while True:
        number += 1
        guard()
        budget = remaining_usd - spent
        if budget < MIN_BUDGET_USD:
            raise ArmRefusal("the remaining spend ceiling is too small for even one attempt")
        attempt_dir = attempts_root / ("attempt-%d" % number)
        tree = attempt_dir / "worktree"
        shutil.copytree(snapshot, tree, symlinks=True)
        result = attempt(tree, attempt_dir / "profile", budget)
        spend.add(result.cost, result.priced_part)
        spent += result.cost if result.cost is not None else (result.priced_part or 0.0)
        if result.cost is None:
            unpriced, stop = True, "unpriced"
        elif visible(tree):
            stop = "visible-pass"
        elif spent >= match_usd:
            stop = "spend-cap"
        elif number >= max_attempts:
            stop = "attempt-bound"
        elif remaining_usd - spent < MIN_BUDGET_USD:
            stop = "ceiling"
        elif deadline - time.monotonic() < MIN_ATTEMPT_SECONDS:
            stop = "deadline"
        if stop:
            chosen = number
            _replace_tree(workdir, tree)
            break
        remove_tree(attempt_dir)
    reason = "attempts=%d stop=%s scored=%d spent=%.6f cap=%.6f" % (number, stop, chosen, spent, match_usd)
    if unpriced:
        return None, reason + " (an attempt could not be priced; priced part spent so far is %.6f)" % spent
    return round(spent, 6), reason
