# SPDX-License-Identifier: MIT
"""claim_refs -- the claim ref primitive: lease-guarded create / renew / reclaim / delete of
`refs/sigma/claims/<goal>` (#858, slice 2 of epic #845, story #542).

A LIBRARY. No CLI, no `__main__`, no config read, and nothing in this slice calls it: a caller
(slice 3) opts in by importing it. It spawns nothing in the background and sends nothing anywhere
unless a verb is called with a remote.

WHY A REF. A claim is a parentless commit on the built-in empty tree, pushed to a ref of its own.
The ref moves in ONE server-side compare-and-swap, so two claimants who both saw the goal free can
not both win. Every push is the explicit form `--force-with-lease=<ref>:<expected>`: the bare form
compares against a remote-tracking ref (a cache of the last fetch, re-armed by any concurrent fetch
against the racer's own sha), and a plain `--force` is no guard at all. `expected` is empty only for
create (`<ref>:` means "must be absent"); renew, reclaim and delete always carry the sha they believe
the ref holds, so a double release LOSES instead of succeeding on absence.

FOUR OUTCOMES, never three. `WON`, `LOST_RACE`, `OUTAGE`, `REFUSED`. A hook decline, a directory/file
ref conflict, a case-fold collision and a permission refusal all print the same `[remote rejected]
(failed to update ref)` family as a real server-side race; reporting them as `LOST_RACE` would make a
caller skip the goal forever as "someone else has it". `REFUSED` is "rejected, and the ref did not
move", and is never retried as a race. Not knowing is `OUTAGE`: never a win, never a lost race.

CLASSIFICATION IS BY MEASUREMENT, not prose. `git push --porcelain` gives a per-ref status flag
(`*` new, `+` forced, `-` deleted, `=` up to date, `!` rejected). On any claimed win or rejection the
module MEASURES the remote tip with `ls-remote` and decides from it. Absent (empty output, rc 0) and
unreadable (non-zero rc) are different results. The only prose recognised is the transport-failure
shape, which has no porcelain line at all, and that only labels the detail: it is `OUTAGE` either way.

KNOWN SEAM (R-8, stated so a caller is not surprised). A claimed win is confirmed by an `ls-remote`
issued AFTER the push. If another claimant re-creates or deletes the ref in the instant between the
two, a REAL win is reported as `OUTAGE` ("tip disagrees"), never as `WON`. That is the safe direction.
Recovery needs no human: the caller re-reads its own ref (shas are unique, so tip == my sha proves the
win and tip != my sha proves it lost), or retries the same verb with the same expected sha, which can
only lose (`LOST_RACE`) and so can never clobber the newer claim.

EVERY CLAIM COMMIT IS UNIQUE. `git commit-tree` is deterministic, so two builds with the same
second-resolution fields would share a sha and both "win". A random 128-bit `nonce` is in the message.
A re-push of the sha the remote already holds exits 0 "up to date" even under a stale lease, so `=`
is `REFUSED` ("up-to-date no-op"), never `WON`.

LIMITS. A timeout kills the whole process group (an `ssh` or `git-remote-*` grandchild too) and is
`OUTAGE`; on Windows the default runner REFUSES loudly because that guarantee is unavailable. The
`seen` mapping behind `observed_age` is the caller's: `time.monotonic()` does not survive a process,
so persisting it (slice 3/5) must use wall time with a skew guard. Measured only on local-path
transport with git 2.39.5: network latency and hosted-remote ref limits are NOT measured.
"""
import dataclasses
import os
import re
import signal
import subprocess
import time

NAMESPACE = "refs/sigma/claims/"
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
WON, LOST_RACE, OUTAGE, REFUSED = "WON", "LOST_RACE", "OUTAGE", "REFUSED"
# A conservative bound for one git call. The real value is a slice 3 decision, UNMEASURED against a
# hosted remote; callers pass `timeout=` by keyword.
DEFAULT_TIMEOUT = 60.0

_GOAL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_RAW_REF = re.compile(r"refs/sigma/claims/[A-Za-z0-9._/-]+")
_PORCELAIN = re.compile(r"^([ +\-*=!])\t([^\t]*)\t(.*)$")
_CLAIM_KEYS = ("holder", "host", "run", "acquired_at", "renewed_at", "lease_seconds", "nonce")
_TRANSPORT = ("Could not read from remote repository", "does not appear to be a git repository",
              "Connection refused", "Could not resolve host", "Connection timed out")


@dataclasses.dataclass(frozen=True)
class Outcome:
    """`sha` is the sha now at the ref when known, else "". `detail` is a short code, never a
    remote URL (it can carry credentials) and never raw stderr."""
    kind: str
    sha: str = ""
    detail: str = ""


class ClaimOutage(RuntimeError):
    """A read could not be answered; carries the OUTAGE `Outcome`. Raised instead of returning
    an empty mapping, because 'no claims' and 'could not look' must never look alike."""

    def __init__(self, outcome):
        super().__init__("OUTAGE: %s" % outcome.detail)
        self.outcome = outcome


# ----------------------------------------------------------------------------------- validation


def validate_goal_id(goal):
    """The ref component for a goal id, or raise `ValueError("REFUSED: ...")`. Strictly stricter than
    git: one component of `[a-z0-9][a-z0-9._-]{0,63}`, no `..`, no trailing `.`, no `.lock` suffix, no
    `/`. Uppercase is REFUSED, never folded (folding maps two goals to one ref, and a case-insensitive
    remote already treats `Case` and `case` as one ref)."""
    if (not isinstance(goal, str) or not _GOAL.fullmatch(goal) or ".." in goal
            or goal.endswith(".") or goal.endswith(".lock")):
        raise ValueError("REFUSED: goal id %r is not a safe claim ref name "
                         "([a-z0-9][a-z0-9._-]{0,63}, no '..', no trailing '.', no '.lock')" % (goal,))
    return goal


def validate_remote(remote):
    """The `sync._ref_value` checks, copied (importing `sync` drags in `ledger`): a remote that is
    not a string, is empty, starts with `-` (git would read it as an option, e.g.
    `--upload-pack=<cmd>`) or holds whitespace/control characters is refused."""
    if (not isinstance(remote, str) or not remote or remote.startswith("-")
            or any(c.isspace() or ord(c) < 32 for c in remote)):
        raise ValueError("REFUSED: remote %r would reach git as an option or is not a plain "
                         "remote name or path" % (remote,))
    return remote


def claim_ref(goal):
    return NAMESPACE + validate_goal_id(goal)


def _sha(value, what):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("REFUSED: %s must be a full lowercase hex sha, got %r" % (what, value))
    return value


# ----------------------------------------------------------------------------------- git runner


def _is_windows():
    return os.name == "nt"


def _env(extra=None):
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    env.update(extra or {})
    return env


def _default_runner(cwd, argv, env, timeout):
    """Run `argv`; return `(rc, stdout, stderr, timed_out)`. On timeout the WHOLE process group is
    killed, so a hung `ssh`/`git-remote-*` grandchild is not orphaned. Refuses on Windows."""
    if _is_windows():
        raise RuntimeError("REFUSED: claim_refs is not supported on Windows "
                           "(process-group kill unavailable)")
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                            errors="replace", start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        return (-signal.SIGKILL, "", "", True)
    return (proc.returncode, out, err, False)


def _run(runner, cwd, argv, env, timeout):
    rc, out, err, timed_out = (runner or _default_runner)(cwd, argv, env, timeout=timeout)
    return rc, out or "", err or "", bool(timed_out)


# ----------------------------------------------------------------------------------- claim commit


def _field(name, value):
    value = str(value)
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("REFUSED: claim field %s must be a non-empty single line" % name)
    return value


def build_claim(cwd, holder, host, run, acquired_at, lease_seconds, renewed_at=None, *,
                runner=None, timeout=DEFAULT_TIMEOUT):
    """The sha of a new parentless empty-tree commit whose message is the claim. `lease_seconds` is
    REQUIRED (no default clock lives here). Unique per call (random nonce). Identity and date are set
    explicitly, so nothing depends on the host's git config."""
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("REFUSED: lease_seconds must be a positive integer, got %r" % (lease_seconds,))
    fields = [("holder", holder), ("host", host), ("run", run), ("acquired_at", acquired_at),
              ("renewed_at", acquired_at if renewed_at is None else renewed_at),
              ("lease_seconds", lease_seconds), ("nonce", os.urandom(16).hex())]
    message = "sigma-claim: 1\n" + "".join("%s: %s\n" % (k, _field(k, v)) for k, v in fields)
    stamp = "%d +0000" % int(time.time())
    who = {"GIT_AUTHOR_NAME": "sigma", "GIT_AUTHOR_EMAIL": "sigma@localhost",
           "GIT_COMMITTER_NAME": "sigma", "GIT_COMMITTER_EMAIL": "sigma@localhost",
           "GIT_AUTHOR_DATE": "@" + stamp, "GIT_COMMITTER_DATE": "@" + stamp}
    rc, out, _err, timed_out = _run(runner, cwd, ["git", "commit-tree", EMPTY_TREE, "-m", message],
                                    _env(who), timeout)
    sha = out.strip()
    if rc != 0 or timed_out or not _SHA.fullmatch(sha):
        raise RuntimeError("OUTAGE: could not build a claim commit")
    return sha


def parse_claim(message):
    """`key: value` lines to a dict. Unknown keys are tolerated (forward compatible); a missing
    required key or a non-integer `lease_seconds` raises `ValueError`."""
    found = {}
    for line in str(message).splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            found.setdefault(key.strip(), value.strip())
    missing = [k for k in _CLAIM_KEYS if not found.get(k)]
    if missing:
        raise ValueError("claim is missing %s" % ", ".join(missing))
    try:
        found["lease_seconds"] = int(found["lease_seconds"])
    except ValueError:
        raise ValueError("claim lease_seconds %r is not an integer" % found["lease_seconds"])
    return found


# ----------------------------------------------------------------------------------- classify


def _porcelain_flag(porcelain):
    """(flag, reason) of the first ref line of `git push --porcelain`, else (None, "")."""
    for line in porcelain.splitlines():
        m = _PORCELAIN.match(line)
        if m:
            return m.group(1), m.group(3)
    return None, ""


def classify(rc, porcelain, stderr, timed_out, tip_after, tip_known, leased_sha, our_sha):
    """Pure. Decide the outcome of ONE push from what was measured. `tip_after` is the sha at the ref
    after the push ("" = absent) and `tip_known` says whether that `ls-remote` was readable;
    `leased_sha` is what the lease expected ("" for create); `our_sha` is what we pushed ("" for
    delete). Never returns WON unless the porcelain says so AND the measured tip agrees."""
    if timed_out:
        return Outcome(OUTAGE, detail="timeout")
    flag, reason = _porcelain_flag(porcelain)
    if flag is None:
        transport = rc == 128 or any(t in stderr for t in _TRANSPORT)
        return Outcome(OUTAGE, detail="transport failure" if transport else "unrecognised output")
    if flag == "=":
        return Outcome(REFUSED, sha=our_sha, detail="up-to-date no-op")
    if flag in "*+-":
        if rc != 0:
            return Outcome(OUTAGE, detail="win flag with non-zero exit")
        if not tip_known:
            return Outcome(OUTAGE, detail="win not confirmed: tip unreadable")
        if tip_after != our_sha:
            return Outcome(OUTAGE, sha=tip_after, detail="win not confirmed: tip disagrees")
        return Outcome(WON, sha=our_sha)
    if flag == "!":
        if not tip_known:
            return Outcome(OUTAGE, detail="rejection not measured: tip unreadable")
        if our_sha and tip_after == our_sha:
            return Outcome(OUTAGE, sha=tip_after, detail="rejected but the tip is ours; re-read")
        if tip_after != leased_sha:
            return Outcome(LOST_RACE, sha=tip_after, detail="ref moved")
        # Rejected, and the ref is exactly what we leased (a create target still absent counts):
        # not a race win and not proof anyone else holds it. A hook, a conflict, a permission.
        return Outcome(REFUSED, sha=tip_after, detail="rejected, ref unchanged (%s)" % reason.strip()[:80])
    return Outcome(OUTAGE, detail="unrecognised status flag")


# ----------------------------------------------------------------------------------- push


def _lease_flag(ref, expected):
    """THE seam of the guard: the explicit lease. `expected` is "" only for create."""
    return "--force-with-lease=%s:%s" % (ref, expected)


def _tip(runner, cwd, remote, ref, timeout):
    """`(known, sha)`: known is False when ls-remote could not be read; sha "" means absent."""
    rc, out, _err, timed_out = _run(runner, cwd, ["git", "ls-remote", "--", remote, ref],
                                    _env(), timeout)
    if rc != 0 or timed_out:
        return False, ""
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name.strip() == ref and _SHA.fullmatch(sha):
            return True, sha
    return True, ""


def _push(cwd, remote, ref, expected, sha_or_none, *, runner, timeout):
    """The ONE `git push` in this module (the write-surface ratchet counts per function)."""
    if not _RAW_REF.fullmatch(ref) or ".." in ref:
        raise ValueError("REFUSED: %r is not under %s" % (ref, NAMESPACE))
    validate_remote(remote)
    if expected:
        _sha(expected, "expected")
    if sha_or_none is not None:
        _sha(sha_or_none, "claim sha")
    refspec = ":" + ref if sha_or_none is None else sha_or_none + ":" + ref
    rc, out, err, timed_out = _run(
        runner, cwd, ["git", "push", "--porcelain", _lease_flag(ref, expected), "--", remote, refspec],
        _env(), timeout)
    flag, _reason = _porcelain_flag(out)
    tip_known, tip = False, ""
    if not timed_out and flag is not None and flag in "*+-!":
        tip_known, tip = _tip(runner, cwd, remote, ref, timeout)
    return classify(rc, out, err, timed_out, tip, tip_known, expected, sha_or_none or "")


def create(cwd, remote, goal, claim_sha, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """Claim a free goal: the ref must be absent."""
    return _push(cwd, remote, claim_ref(goal), "", _sha(claim_sha, "claim sha"),
                 runner=runner, timeout=timeout)


def renew(cwd, remote, goal, expected, new_sha, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """The holder advances its own claim; `expected` is the sha it last wrote."""
    return _push(cwd, remote, claim_ref(goal), _sha(expected, "expected"), _sha(new_sha, "claim sha"),
                 runner=runner, timeout=timeout)


def reclaim(cwd, remote, goal, observed_stale_sha, new_sha, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """Take over a claim observed stale; the lease is the sha that was observed."""
    return _push(cwd, remote, claim_ref(goal), _sha(observed_stale_sha, "observed_stale_sha"),
                 _sha(new_sha, "claim sha"), runner=runner, timeout=timeout)


def delete(cwd, remote, goal, expected, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """Release a claim. `expected` is mandatory: a delete without a lease never fails on absence."""
    return _push(cwd, remote, claim_ref(goal), _sha(expected, "expected"), None,
                 runner=runner, timeout=timeout)


# ----------------------------------------------------------------------------------- reads


def read_tips(cwd, remote, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """`{goal: sha}` via `ls-remote` (the cheap read). Raises `ClaimOutage` if it cannot be read."""
    validate_remote(remote)
    rc, out, _err, timed_out = _run(runner, cwd, ["git", "ls-remote", "--", remote, NAMESPACE + "*"],
                                    _env(), timeout)
    if rc != 0 or timed_out:
        raise ClaimOutage(Outcome(OUTAGE, detail="timeout" if timed_out else "ls-remote failed"))
    tips = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name.startswith(NAMESPACE) and _SHA.fullmatch(sha):
            tips[name[len(NAMESPACE):]] = sha
    return tips


def read_all(cwd, remote, *, runner=None, timeout=DEFAULT_TIMEOUT):
    """`{goal: (sha, parsed_claim)}` via a fetch of the claim namespace into `cwd`'s own
    `refs/sigma/claims/*` (pruned, so a released claim disappears). Use `read_tips` when bodies are
    not needed: it is 4x to 13x cheaper. A claim whose body does not parse maps to `{}`."""
    validate_remote(remote)
    spec = "+%s*:%s*" % (NAMESPACE, NAMESPACE)
    rc, _out, _err, timed_out = _run(
        runner, cwd, ["git", "fetch", "--quiet", "--no-tags", "--prune", "--", remote, spec],
        _env(), timeout)
    if rc != 0 or timed_out:
        raise ClaimOutage(Outcome(OUTAGE, detail="timeout" if timed_out else "fetch failed"))
    # ONE process for every ref and body: a cat-file per ref cost ~25 ms each (1000 refs = ~25 s).
    rc, out, _err, timed_out = _run(
        runner, cwd, ["git", "for-each-ref", "--format=%(refname)%00%(objectname)%00%(contents)%01",
                      NAMESPACE], _env(), timeout)
    if rc != 0 or timed_out:
        raise ClaimOutage(Outcome(OUTAGE, detail="for-each-ref failed"))
    found = {}
    for record in out.split("\x01"):
        name, _, rest = record.strip("\n").partition("\x00")
        sha, _, body = rest.partition("\x00")
        if not name.startswith(NAMESPACE) or not _SHA.fullmatch(sha):
            continue
        try:
            parsed = parse_claim(body)
        except ValueError:
            parsed = {}
        found[name[len(NAMESPACE):]] = (sha, parsed)
    return found


# ----------------------------------------------------------------------------------- observed age


def observed_age(seen, goal, sha, now):
    """`(age_seconds, new_seen)`. Pure: `seen` is the caller's mapping `goal -> (sha, first_seen)`,
    never mutated; `now` is injected. An unchanged sha keeps its first_seen and so ages; a changed sha
    (a renewal or a takeover) resets the clock. Age is clamped to >= 0 on a backwards clock. Age since
    the sha last CHANGED is the liveness tell: a dead holder's sha stops changing."""
    prior = seen.get(goal)
    first = prior[1] if prior and prior[0] == sha else now
    new_seen = dict(seen)
    new_seen[goal] = (sha, first)
    return max(0, now - first), new_seen
