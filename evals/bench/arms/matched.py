"""The matched-spend arm's retry loop: the plain agent, again and again, until it passes or its token spend matches A1's."""
from pathlib import Path
import shutil
import time

try:
    from .common import KIND_NAMES, MIN_ATTEMPT_SECONDS, ArmRefusal, remove_tree
except ImportError:  # loaded by path rather than as a package
    from common import KIND_NAMES, MIN_ATTEMPT_SECONDS, ArmRefusal, remove_tree


def _replace_tree(workdir, source):
    """Make ``workdir`` hold exactly ``source``'s contents: remove, then copy; never overlay."""
    workdir = Path(workdir)
    for child in list(workdir.iterdir()):
        if child.is_dir() and not child.is_symlink():
            remove_tree(child)
        else:
            child.unlink()
    shutil.copytree(source, workdir, symlinks=True, dirs_exist_ok=True)


def run_matched(workdir, attempts_root, *, attempt, visible, guard, match_tokens, remaining_tokens,
                max_attempts, deadline, spend):
    """Run attempts in fresh copies of ``workdir``'s starting tree and leave the scored one in ``workdir``.

    Spend is TOKENS (the host's own usage records, all four kinds summed), never dollars: the retry stops
    once the cumulative tokens reach ``match_tokens``, A1's token spend for the task.

    ``attempt(tree, profile_dir)`` returns an ``Attempt``; ``visible(tree)`` returns whether the visible
    command passes; ``guard()`` re-checks the operator's plugin directories and raises on a change.
    Stops, in this order after each attempt: host rate limit, no readable usage, unexplained non-zero exit, visible pass, token spend
    reached ``match_tokens``, ``max_attempts`` reached, remaining global token ceiling exhausted,
    ``deadline`` passed.  At least one attempt always runs.  Failed attempts are deleted before the next
    starts.  The host has no per-run token cap, so the last attempt can overshoot by up to one attempt;
    that overshoot is counted, never hidden.

    Returns ``{"tokens", "detail", "cost_usd", "reason", "rate_limit"}``: ``tokens`` is the cumulative spend
    of every attempt (None when an attempt left no readable usage), ``cost_usd`` is indicative dollars (None
    when any attempt could not be priced), and ``rate_limit`` is set (the pair did not complete) when the
    host throttled an attempt, in which case the caller records the pair as not-run; ``suspect_exit`` is set
    likewise when an attempt exited non-zero with real work and no record to explain it.
    """
    attempts_root = Path(attempts_root)
    snapshot = attempts_root / "pristine"
    shutil.copytree(workdir, snapshot, symlinks=True)
    spent, usd, number, chosen, stop = 0, 0.0, 0, None, ""
    usd_known, usage_known, throttled, suspect, noted = True, True, None, None, False
    detail = dict.fromkeys(KIND_NAMES, 0)
    while True:
        number += 1
        guard()
        if remaining_tokens - spent <= 0:
            raise ArmRefusal("the remaining token ceiling is exhausted before the first attempt")
        attempt_dir = attempts_root / ("attempt-%d" % number)
        tree = attempt_dir / "worktree"
        shutil.copytree(snapshot, tree, symlinks=True)
        result = attempt(tree, attempt_dir / "profile")
        spend.add(result.cost, result.priced_part, result.tokens, result.detail)
        spent += result.tokens or 0
        for name in KIND_NAMES:
            detail[name] += (result.detail or {}).get(name, 0)
        usd += result.cost if result.cost is not None else (result.priced_part or 0.0)
        usd_known = usd_known and result.cost is not None
        noted = noted or (result.rate_limit is not None and result.throttled is None)
        if result.throttled is not None:  # first: a limit hit before any model turn has zero tokens too
            throttled, stop = result.throttled, "rate-limit"
        elif not result.tokens:
            usage_known, stop = False, "no-usage"
        elif result.suspect is not None:
            suspect, stop = result.suspect, "suspect-exit"
        elif visible(tree):
            stop = "visible-pass"
        elif spent >= match_tokens:
            stop = "token-cap"
        elif number >= max_attempts:
            stop = "attempt-bound"
        elif remaining_tokens - spent <= 0:
            stop = "ceiling"
        elif deadline - time.monotonic() < MIN_ATTEMPT_SECONDS:
            stop = "deadline"
        if stop:
            chosen = number
            if throttled is None and suspect is None:
                _replace_tree(workdir, tree)
            break
        remove_tree(attempt_dir)
    reason = "attempts=%d stop=%s scored=%d tokens=%d cap=%d" % (number, stop, chosen, spent, match_tokens)
    if noted:
        reason += " (a rate-limit record was seen on a run that exited 0; scored as usual)"
    if not usage_known:
        reason += " (an attempt left no readable usage record)"
    return {"tokens": spent if usage_known else None, "detail": detail,
            "cost_usd": round(usd, 6) if usd_known else None, "reason": reason, "rate_limit": throttled,
            "suspect_exit": suspect}
