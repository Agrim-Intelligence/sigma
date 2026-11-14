#!/usr/bin/env python3
"""Where a follow-up goes (#2224): one pure function, one code-owned count, the facts it is decided from.

`route(goal_unit, item_unit, added, cap)` maps RECORDED facts to `("slice", None)` or `("separate", <reason>)`.
It takes positional facts only: no keyword catch-all, no reason input, no environment read, no config read. The two
reasons are `REASONS`; nothing a caller passes can name one. The facts come from `gather_facts` / `item_unit`
(recorded sources) and `added_count` (the record only `add_slice.py` writes, plus the plan's own headings).

Residual, stated: the item's feature is still caller-declared text (a `Feature:` marker in the body file, a
`feature:` label). Code narrows it (the unit must be an open registered one; the `<goal>` must match the work record
whose worktree holds the cwd) but cannot prove the agent declared its find's TRUE feature. The count is
machine-local state (`.sdlc/state/followups/<stem>.json`); the plan's `## Added slice` headings travel with the
branch and can only raise it, so the ceiling is the cap wherever the plan is present and `cap x machines` otherwise.
"""
import contextlib
import hashlib
import importlib.util
import json
import pathlib
import re
import time
import unicodedata

try:
    import fcntl
except ImportError:                    # pragma: no cover - non-POSIX
    fcntl = None

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_FEATURES = None


def _features():
    """One `features` module instance: `AmbiguousUnit` must be one class for the raiser and every catcher."""
    global _FEATURES
    if _FEATURES is None:
        _FEATURES = _load("features")
    return _FEATURES


REASONS = ("different-feature", "slice-cap")
MARKER_PREFIX = "<!-- sigma:followup-reason:"
_MARKER_RE = re.compile(r"<!-- sigma:followup-reason:(different-feature|slice-cap) -->")
SLICE_HEADING_RE = re.compile(r"^## Added slice \d+: (.+?)\s*$", re.M)
#: The doctor row reads at most this many open `sdlc:followup` issues; above it the row says it under-reports.
DOCTOR_CEILING = 200


class RouteRefused(Exception):
    """`track` must refuse and file nothing; `.code` is the exit code (2: no honest fact to route on)."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


class RecordError(ValueError):
    """The code-owned record is unreadable. Never read as zero: a silent zero would reopen the cap."""


class LockUnavailable(RuntimeError):
    """No `fcntl` here: the count cannot be made safe, so the verb refuses rather than proceed weakly."""


def route(goal_unit, item_unit, added, cap):
    """-> ("slice", None) | ("separate", "different-feature") | ("separate", "slice-cap"). Pure."""
    if item_unit is None:                           # (1) an undeclared find inherits the goal's feature
        item_unit = goal_unit
    if item_unit != goal_unit:                      # (2) another feature: checked before the cap
        return ("separate", "different-feature")
    if added >= cap:                                # (3) same feature, plan already holds `cap` added slices
        return ("separate", "slice-cap")
    return ("slice", None)                          # (4)


def reason_marker(reason):
    if reason not in REASONS:
        raise ValueError("unknown follow-up reason %r" % (reason,))
    return "%s%s -->" % (MARKER_PREFIX, reason)


def parse_reason(body):
    found = _MARKER_RE.search(body or "")
    return found.group(1) if found else None


# --- facts ------------------------------------------------------------------------------------------------------

def item_unit(sdlc_dir, body, labels):
    """-> (unit | None, warnings): the unit the item's OWN declaration names, only if it is an open registered unit.
    Body wins over label (`features.read`). `AmbiguousUnit` propagates: there is no honest unit to route on."""
    features = _features()
    declared = features.read({"body": body, "labels": list(labels or [])}).unit
    if not declared:
        return None, []
    resolved = _load("feature_registry").resolve_open_unit(sdlc_dir, declared)
    if resolved:
        return resolved, []
    return None, ["the item declares unit %r, which is not an open registered unit: it is not a declaration, so "
                  "the item inherits the goal's feature" % declared]


def goal_unit(source, goal):
    """The unit the goal's own issue declares (body wins); None when the source has no unit surface.
    A failed read or a self-contradicting goal raises `RouteRefused`: routing needs the recorded fact."""
    if source is None or not hasattr(source, "fetch_body_labels"):
        return None
    features = _features()
    try:
        issue = source.fetch_body_labels(goal)
    except Exception as exc:                        # noqa: BLE001
        raise RouteRefused("could not read goal %s to route this follow-up (%s): nothing filed" % (goal, exc))
    try:
        return features.read(issue).unit
    except features.AmbiguousUnit as exc:
        raise RouteRefused("goal %s contradicts itself about its unit (%s): nothing filed; edit the goal's issue"
                           % (goal, exc))


def bound_goal(sdlc_dir, cwd):
    """The goal whose work-record worktree holds `cwd`, or None (root checkout, orchestrator: nothing to bind to)."""
    here = pathlib.Path(cwd).resolve()
    for rec_file in sorted(pathlib.Path(sdlc_dir, "state", "work").glob("*.json")):
        try:
            rec = json.loads(rec_file.read_text(encoding="utf-8"))
            tree = pathlib.Path(rec["worktree"]).resolve()
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if here == tree or tree in here.parents:
            return rec_file.stem
    return None


def gather_facts(sdlc_dir, config, goal, source, body, labels, cwd):
    """-> (goal_unit, item_unit, added, cap, warnings). The only place the router's facts are read."""
    handoff_cap = _load("handoff").max_added_slices(config)
    stem = _stem(goal)
    bound = bound_goal(sdlc_dir, cwd)
    if bound is not None and bound != stem:
        raise RouteRefused("this session works in goal %s's worktree but the follow-up names goal %s: refused, "
                           "nothing filed (file it against %s)" % (bound, stem, bound))
    features = _features()
    gunit = goal_unit(source, goal)
    try:
        iunit, warnings = item_unit(sdlc_dir, body, labels)
    except features.AmbiguousUnit as exc:
        raise RouteRefused("the follow-up declares two units (%s): nothing filed" % exc)
    try:
        added = added_count(sdlc_dir, goal)
    except RecordError as exc:
        raise RouteRefused(str(exc))
    return gunit, iunit, added, handoff_cap, warnings


# --- the code-owned record ---------------------------------------------------------------------------------------

def _stem(goal):
    p = pathlib.Path(str(goal))
    return p.stem if p.suffix == ".md" else str(goal)


def record_path(sdlc_dir, goal):
    stem = _stem(goal)
    reason = _load("state").unsafe_goal_reason(stem)
    if reason:
        raise ValueError("unsafe goal %r for the follow-up record: %s" % (goal, reason))
    return pathlib.Path(sdlc_dir) / "state" / "followups" / (stem + ".json")


def normalize_title(title):
    return " ".join(unicodedata.normalize("NFKC", str(title)).casefold().split())


def slice_id(goal, title):
    """The slice's IDENTITY: goal stem + normalised title, never its criteria text."""
    return hashlib.sha256(("%s\0%s" % (_stem(goal), normalize_title(title))).encode("utf-8")).hexdigest()[:16]


def read_record(sdlc_dir, goal):
    path = record_path(sdlc_dir, goal)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"first_routed_at": None, "added": []}
    except OSError as exc:
        raise RecordError("follow-up record %s unreadable (%s)" % (path, exc))
    try:
        rec = json.loads(text)
        if not isinstance(rec, dict) or not isinstance(rec.get("added"), list):
            raise ValueError("wrong shape")
    except ValueError as exc:
        raise RecordError("follow-up record %s is corrupt (%s): fix or delete it only after confirming the plan's "
                          "`## Added slice` count" % (path, exc))
    return rec


@contextlib.contextmanager
def locked(sdlc_dir, goal):
    """Exclusive per-goal lock on a sibling `.lock` file. REFUSES where `fcntl` is absent."""
    if fcntl is None:
        raise LockUnavailable("follow-up counting needs fcntl file locks, which this platform lacks: not supported "
                              "here (nothing written)")
    path = record_path(sdlc_dir, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(sdlc_dir, goal, rec):
    _load("state").atomic_write_text(record_path(sdlc_dir, goal), json.dumps(rec, indent=2, sort_keys=True))


def append_slice(sdlc_dir, goal, sid, plan_sha_before, held=False, _pause=None):
    """Count one added slice, idempotent on `sid`. Read-modify-write inside the lock (`held`: the caller already
    holds `locked`). `_pause` is a TEST-ONLY seam between read and write; it is never a router input."""
    def body():
        rec = read_record(sdlc_dir, goal)
        if any(item.get("slice_id") == sid for item in rec["added"]):
            return rec
        if _pause:
            _pause()
        rec["added"].append({"slice_id": sid, "plan_sha256_before": plan_sha_before,
                             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        rec["first_routed_at"] = rec.get("first_routed_at") or rec["added"][-1]["ts"]
        _write(sdlc_dir, goal, rec)
        return rec
    if held:
        return body()
    with locked(sdlc_dir, goal):
        return body()


def first_routed(sdlc_dir, goal):
    return read_record(sdlc_dir, goal).get("first_routed_at")


def plan_headings(sdlc_dir, goal):
    plan = _load("review_context").phase_doc_file(sdlc_dir, goal, "plans")
    if not plan:
        return []
    try:
        return SLICE_HEADING_RE.findall(pathlib.Path(plan).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def added_count(sdlc_dir, goal):
    """max(record count, plan `## Added slice` headings): the headings travel with the plan, so they can only raise."""
    return max(len(read_record(sdlc_dir, goal)["added"]), len(plan_headings(sdlc_dir, goal)))


# --- doctor ------------------------------------------------------------------------------------------------------

def unrecorded(issues, mode):
    """-> (cutoff | None, [numbers]) for the doctor row. `issues` are the rows of ONE capped read of open
    `sdlc:followup` issues ({number, createdAt, body}). The cutoff is the earliest `createdAt` among rows carrying
    the reason marker; flagged are rows created at or after it without one. `mode == "file"`: routing is off, so
    nothing is flagged. No marker-bearing row: nothing flagged (routing not yet in use)."""
    if mode == "file":
        return None, []
    stamped = sorted(str(i.get("createdAt") or "") for i in issues if parse_reason(i.get("body")))
    if not stamped:
        return None, []
    cutoff = stamped[0]
    flagged = [i.get("number") for i in issues
               if not parse_reason(i.get("body")) and str(i.get("createdAt") or "") >= cutoff]
    return cutoff, flagged
