"""Read-only merge-queue advisor (split (b) of #408's research; issue #976). Zero-dep.

WHY THIS EXISTS. A prior research pass (#408) found that GitHub's native merge queue would fully
serialize concurrent-loop landings on a repo running the loop, eliminating the #406 BEHIND-rebase
race entirely instead of just bounding it. But *enabling* a merge queue needs `administration:write`
plus, for a private repo, a paid plan (Team/Enterprise) -- a scope and a cost this tool must never
assume or silently reach for. Confirmed live: this very repo (private, free) gets a 403 from the
classic branch-protection API on that scope.

This module is the SAFE half: detect the shape a merge queue would fix, using only the ordinary
read-only permissions the loop already holds to talk to `gh` (repo read, PR read, Actions read -- no
`administration` scope, ever, and no call here is anything but a plain `gh api` GET), and surface a
RECOMMENDATION. Mutating anything (enabling the queue, changing protection) is out of scope by
design -- that decision needs a human with admin access and a plan to match; #977 is its (separate)
territory if it's ever built.

WHICH SIGNALS AND WHY.

  1. Strict-protected base branch (`strict_protection`) -- `branches/{branch}/protection`. A merge
     queue only helps a repo that actually re-validates each PR against a moving base before merge;
     that's exactly what `required_status_checks.strict` + a non-empty required-checks list means.
     Without it, PRs merge without re-running CI against latest main, and #408's race isn't
     happening in the first place. No canary call needed here (unlike doctor.py's
     `_self_merge_risk`, which reads this same endpoint for the OPPOSITE signal -- absence of
     protection -- and needs a canary because an unreachable repo and a genuinely-unprotected one
     both read as empty): this check's signal is PRESENCE, so an unreachable/403/404 repo collapses
     safely to "not strict" via `_real_run`'s own success-only contract, never a false positive.

  2. Evidence of concurrent-loop landings (`merge_burst`) -- a burst of PRs landing on the base
     branch close together in time, via `gh api .../pulls?state=closed&base={branch}`. Chosen over
     parsing the ledger's BEHIND-rebase trail because the ledger is opt-in and OFF by default
     (ledger.py: every entry point is a no-op with no `ledger` config block) -- most customer repos
     running this loop will never have it on. It also, in fact, has no dedicated "rebase" kind to
     read: `work.py`'s `_reconcile_behind` (the actual BEHIND-retry path) never calls
     `ledger.safe_append` at all -- only the terminal `gate`/`merge` event and `merge-armed`/`merged`
     do. `gh api .../pulls` needs nothing beyond ordinary PR-read and is available on every repo,
     every time.

  3. A required check whose runtime is long/growing (`growing_required_check`) -- `gh api
     .../actions/runs`, comparing the most recent runs' duration to the previous batch. Best-effort
     and NEVER gating: plenty of healthy repos either don't use Actions for their required check or
     have a short/flat one. Its presence sharpens the recommendation's evidence; its absence must
     never suppress signals 1+2, which are the actual trigger.

DESIGN DECISION: the recommendation fires on (1 AND 2) -- strict protection AND merge-burst
evidence -- because that pair IS the #408 shape: a gate worth serializing against, plus proof the
base moves fast enough to make BEHIND races routine. Signal 3 is enrichment text when measurable,
never a gate, both because requiring it would blind the detector on any repo not using GitHub
Actions for its required check, and because "runtime growing" alone says nothing about WHO is
racing to land -- only 1+2 together describe the actual failure mode a merge queue fixes.

ACCEPTED FALSE-POSITIVE CLASS: a repo that happens to squash-merge many small, healthy PRs in a
burst with no real BEHIND races would also trip signal 2. Accepted because the output here is
advisory text only -- never a mutation, never a block -- and a repo merging that fast either already
benefits from serialization or is about to need it.

FAIL-OPEN, EVERYWHERE. Every `gh api` call here can 404, time out, rate-limit, or return a shape
this code doesn't expect (an error body, an empty string, a list where a dict was expected) -- any
of that must read as "no evidence", never raise and never block the dashboard that calls this. Same
idiom as status.py's own `_github_counts`."""
import datetime
import json

#: Shipped defaults, named so a future tune-up has one place to look, not buried magic numbers.
MERGE_BURST_WINDOW_HOURS = 24     #: how far back from the newest merge counts as "the same burst"
MERGE_BURST_THRESHOLD = 5         #: merges within that window before it counts as concurrent-landing evidence
GROWTH_RATIO = 1.5                #: newest-half average must be at least this many times the oldest-half's
GROWTH_FLOOR_SECONDS = 300        #: ...AND at least this long in absolute terms, or a ratio on noise (a
                                   #: 3s->10s "3x growth") would misread as the #408 shape
RUNS_SAMPLE = 30                  #: workflow runs fetched for the growth comparison

RECOMMENDATION = (
    "this repo would merge faster with a GitHub merge queue "
    "(requires: repo auto-merge enabled, admin access, a supported plan -- "
    "Team/Enterprise for private repos)"
)


def _api(run, repo, path):
    """One `gh api` read against `repos/{repo}/{path}`, parsed as JSON. Empty/invalid/non-2xx (via
    `run`'s own convention of returning "" on nonzero exit -- see status.py's `_real_run`) all
    collapse to None, the one shape every caller below treats as 'no evidence', never an error to
    propagate.

    PR-REVIEW FIX: every caller below builds its own endpoint-relative `path` (e.g.
    `branches/{branch}/protection`) and relies on THIS function to prefix it with `repos/{repo}/` --
    the earlier version silently dropped `repo` and called the bare relative path, which the `gh`
    CLI happily accepts but resolves against the wrong (default/current) repo context, so every
    signal read the wrong data and `advise()` could never fire against the actual target repo. Fail-
    open masked it completely: a wrong-repo read 404s/403s exactly like an unreachable one, so every
    test that only checked "does this collapse to None on a bad response" passed regardless."""
    try:
        out = run(["gh", "api", f"repos/{repo}/{path}"])
    except Exception:
        return None
    try:
        return json.loads(out) if out else None
    except (ValueError, TypeError):
        return None


def _required_checks(protection):
    """A required-checks list under either key GitHub has used across API versions -- `contexts` is
    the legacy REST shape, `checks` (a list of {context, app_id}) is the newer one. Either non-empty
    means "there is something to serialize against"."""
    rsc = (protection or {}).get("required_status_checks")
    if not isinstance(rsc, dict):
        return []
    contexts = rsc.get("contexts")
    if contexts:
        return contexts
    checks = rsc.get("checks")
    if isinstance(checks, list):
        return [c.get("context") for c in checks if isinstance(c, dict) and c.get("context")]
    return []


def strict_protection(repo, branch, run):
    """True only when the base branch requires status checks to be up to date (`strict`) AND names
    at least one required check -- a queue has nothing to serialize against otherwise. 404/403 (no
    protection, or unreadable -- this repo, private+free, gets a 403 on this exact endpoint) and any
    malformed response both read as False, never raise."""
    data = _api(run, repo, f"branches/{branch}/protection")
    if not isinstance(data, dict):
        return False
    rsc = data.get("required_status_checks")
    if not isinstance(rsc, dict):
        return False
    return bool(rsc.get("strict")) and bool(_required_checks(data))


def _parse_ts(s):
    """GitHub's REST timestamps are always `%Y-%m-%dT%H:%M:%SZ` UTC. `strptime` (not
    `datetime.fromisoformat`, whose bare trailing `Z` needs Python 3.11+) so this parses on every
    interpreter this repo supports. Anything else -- None, a non-string, a malformed value -- reads
    as unparseable, not an error."""
    if not isinstance(s, str):
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def merge_burst(repo, branch, run, window_hours=MERGE_BURST_WINDOW_HOURS, threshold=MERGE_BURST_THRESHOLD):
    """Evidence of concurrent-loop landings: N-or-more PRs merged to the base branch within
    `window_hours` of each other. `state=closed` also returns closed-but-UNMERGED PRs (rejected,
    abandoned) -- those are filtered out (`merged_at` must be a real, parseable timestamp) before
    counting or anchoring, or a burst of abandoned PRs would masquerade as a landing burst.

    Anchored on the NEWEST merge in the sample rather than wall-clock `now` -- deterministic (a test
    fixture needs no time mocking) and answers the only question that matters here: "was there
    recently a burst", not "is one happening this exact second".

    Single page (`per_page=100`, sorted by `updated` descending -- a `merged_at` write always bumps
    `updated_at`, so a genuine recent burst is reliably inside one page): this is a heuristic
    advisory signal, not an exact audit, and a burst worth flagging is by definition recent.

    Returns (count, window_hours) on a burst, else None. Fail-open on any malformed/empty response."""
    data = _api(run, repo, f"pulls?state=closed&base={branch}&sort=updated&direction=desc&per_page=100")
    if not isinstance(data, list):
        return None
    merged_at = sorted(
        (t for t in (_parse_ts(pr.get("merged_at")) for pr in data if isinstance(pr, dict)) if t),
        reverse=True,
    )
    if not merged_at:
        return None
    cutoff = merged_at[0] - datetime.timedelta(hours=window_hours)
    count = sum(1 for t in merged_at if t >= cutoff)
    return (count, window_hours) if count >= threshold else None


def growing_required_check(repo, run, per_page=RUNS_SAMPLE, growth_ratio=GROWTH_RATIO,
                            floor_seconds=GROWTH_FLOOR_SECONDS):
    """Best-effort: is the most recent half of workflow runs taking meaningfully longer than the
    older half? Never gates the recommendation (see module docstring) -- returns None on anything
    short of a clear, sizeable trend, including 'not enough runs to compare' and 'no Actions at
    all', both of which are completely normal and must not read as evidence of anything.

    Returns (avg_old_seconds, avg_new_seconds) on a real trend, else None."""
    data = _api(run, repo, f"actions/runs?per_page={per_page}")
    runs = data.get("workflow_runs") if isinstance(data, dict) else None
    if not isinstance(runs, list) or len(runs) < 6:
        return None
    durations = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        start = _parse_ts(r.get("run_started_at") or r.get("created_at"))
        end = _parse_ts(r.get("updated_at"))
        if start and end and end > start:
            durations.append((start, (end - start).total_seconds()))
    if len(durations) < 6:
        return None
    # newest first -- the API already returns runs that way, but sort explicitly rather than trust it
    durations.sort(key=lambda pair: pair[0], reverse=True)
    half = len(durations) // 2
    newest = [d for _, d in durations[:half]]
    oldest = [d for _, d in durations[half:half * 2]]
    if not newest or not oldest:
        return None
    avg_new, avg_old = sum(newest) / len(newest), sum(oldest) / len(oldest)
    if avg_old > 0 and avg_new >= floor_seconds and avg_new >= avg_old * growth_ratio:
        return (avg_old, avg_new)
    return None


def advise(gh_cfg, run, branch="main"):
    """The one entry point status.py calls. Returns a one-line recommendation string, or None when
    the #408 shape isn't present (or the repo is unreadable -- fail-open, never a false positive
    from a `gh` hiccup). Gated on strict protection AND a merge burst (see module docstring for
    why); calls short-circuit in that order so an unprotected repo (the common case) costs exactly
    one `gh api` call, not three. A growing required check, when measurable, is appended as extra
    evidence but never gates alone."""
    repo = (gh_cfg or {}).get("repo") or ""
    if not repo:
        return None
    if not strict_protection(repo, branch, run):
        return None
    burst = merge_burst(repo, branch, run)
    if not burst:
        return None
    count, window = burst
    msg = f"{RECOMMENDATION} -- {count} merges to {branch} within {window}h"
    growth = growing_required_check(repo, run)
    if growth:
        avg_old, avg_new = growth
        msg += f"; a required check's runtime grew {avg_old:.0f}s -> {avg_new:.0f}s"
    return msg
