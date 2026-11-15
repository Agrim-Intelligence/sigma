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

The one exemption from routing (#2224 amendment A): `decompose_decision` proves a `--kind child` filing is a decompose
meta-goal's child and exempts only that. It is a pure conjunction over already-read facts (the reader is
`read_decompose_facts`); any leg that cannot be read fails closed. Residual, stated: legs a-c are GitHub text a worker
with `gh` can write by hand and leg d is written through a verb, so this stops a worker that merely calls
`track --kind child`, not a hostile one. The meta-goal's plan-review verdict is hash-bound to the META-GOAL's plan only,
not to the set of children actually filed.
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
_MP = _load("legacy").MARKER_PREFIX_RE      # either brand spelling, built from fragments (no literal retired token)
DECOMPOSE_OF_RE = re.compile(r"^<!-- " + _MP + r":decompose-of=#(\d+) -->\s*$")
DECOMPOSED_FROM_RE = re.compile(r"^<!-- " + _MP + r":decomposed-from=#(\d+) -->\s*$")
DECOMPOSE_FILED_RE = re.compile(_MP + r":decompose-filed=#(\d+)(?!\d)")
DECOMPOSE_LABEL = "sdlc:decompose"
#: Why a decompose child was not exempt: the leg that failed. `unreadable:<leg>` is a leg that could not be read.
DECOMPOSE_LEGS = {
    "kind": "only `--kind child` can be a decompose child",
    "meta-marker": "the goal's body does not start with `<!-- sigma:decompose-of=#P -->` for a different goal P",
    "meta-label": "the goal does not carry the `sdlc:decompose` label",
    "parent-comment": "the parent's comments hold no `sigma:decompose-filed=#<this goal>` marker",
    "child-marker": "the child's --body-file does not start with `<!-- sigma:decomposed-from=#P -->` for the same P",
    "plan": "the goal's plan is not recorded and present",
    "plan-review": "no approving plan-review verdict is recorded for the goal's plan",
    "plan-review-route": "the plan-review verdict was not recorded by an independent subagent (inline route or no agent id)",
    "child-cap": "this decompose goal already has its `goal_decompose.max_children` children recorded",
}
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


def goal_number(goal):
    """The digits of `2224`, `#2224`, `2224.md` (surrounding space ignored), else None. One parse for every spelling."""
    found = re.fullmatch(r"#?(\d+)(?:\.md)?", str(goal).strip()) if goal is not None else None
    return int(found.group(1)) if found else None


def decompose_decision(kind, goal, m_first_line, m_labels, parent_comments, child_first_line, gate, children_filed,
                       max_children):
    """-> (ok, why). Pure: already-read facts in, no I/O, no environment, no configuration. `why` is the first failing
    leg (`DECOMPOSE_LEGS`) or `unreadable:<fact>` for a fact the reader could not get (None = unreadable: fail closed).
    All legs required, in order: (0) kind child; (a) the goal M's body FIRST line is `decompose-of=#P`, P != M, and M
    carries `sdlc:decompose`; (b) P's comments hold `decompose-filed=#M`, M equal to this goal; (c) the child's body
    first line is `decomposed-from=#P`, the same P; (d) M's plan is present and recorded and its approving plan-review
    came from an independent subagent (`gate`); (e) fewer than `max_children` children recorded for M.
    The plan-review verdict is hash-bound to M's own plan, not to the children filed under it."""
    if kind != "child":
        return False, "kind"
    meta = goal_number(goal)
    if m_first_line is None:
        return False, "unreadable:meta-marker"
    found = DECOMPOSE_OF_RE.match(m_first_line)
    if found is None or meta is None or int(found.group(1)) == meta:
        return False, "meta-marker"
    parent = int(found.group(1))
    if m_labels is None:
        return False, "unreadable:meta-label"
    if DECOMPOSE_LABEL not in m_labels:
        return False, "meta-label"
    if parent_comments is None:
        return False, "unreadable:parent-comment"
    if not any(int(n) == meta for text in parent_comments for n in DECOMPOSE_FILED_RE.findall(text or "")):
        return False, "parent-comment"
    mark = DECOMPOSED_FROM_RE.match(child_first_line) if child_first_line else None
    if mark is None or int(mark.group(1)) != parent:
        return False, "child-marker"
    if gate is None:
        return False, "unreadable:gate"
    if not (gate.get("plan_present") and gate.get("plan_ok")):
        return False, "plan"
    if not gate.get("plan_review_ok"):
        return False, "plan-review"
    if gate.get("route") != "subagent" or not gate.get("agent_id"):
        return False, "plan-review-route"
    if children_filed is None:
        return False, "unreadable:child-cap"
    if children_filed >= max_children:
        return False, "child-cap"
    return True, ""


def _status(sdlc_dir, config, goal):
    """The one call into `phase_gate.status` (a seam: a test makes it raise)."""
    return _load("phase_gate").status(sdlc_dir, config, goal)


def _first_line(text):
    return text.split("\n", 1)[0].rstrip("\r") if isinstance(text, str) else None


def _label_names(labels):
    return [str(x.get("name")) if isinstance(x, dict) else str(x) for x in labels]


def max_children(config):
    """`goal_decompose.max_children`: a whole number of at least 1, else the default 8 (as `loop.py`)."""
    block = config.get("goal_decompose") if isinstance(config, dict) else None
    value = block.get("max_children") if isinstance(block, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 8


def read_decompose_facts(sdlc_dir, config, source, goal, body):
    """-> the keyword facts `decompose_decision` takes (less `kind` and `goal`). The only place they are read; any fact
    that cannot be read is None, which the decision treats as failing its leg. Parent P is parsed from the goal's own
    body, never passed in. `fetch_comments_strict(P)` is read at most once per call (one `track` call, one read)."""
    number = goal_number(goal)
    key = str(number) if number is not None else str(goal)
    facts = {"m_first_line": None, "m_labels": None, "parent_comments": None, "gate": None, "children_filed": None,
             "child_first_line": _first_line(body) if body else None, "max_children": max_children(config)}
    if source is not None and hasattr(source, "fetch_body_labels"):
        try:
            issue = source.fetch_body_labels(key)
            facts["m_first_line"] = _first_line(issue.get("body"))
            if isinstance(issue.get("labels"), list):
                facts["m_labels"] = _label_names(issue["labels"])
        except Exception:                                  # noqa: BLE001 - unreadable: the leg fails closed
            pass
    found = DECOMPOSE_OF_RE.match(facts["m_first_line"] or "")
    if found and hasattr(source, "fetch_comments_strict"):
        try:
            comments = source.fetch_comments_strict(found.group(1))["comments"]
            if isinstance(comments, list):
                facts["parent_comments"] = [str(c.get("body") if isinstance(c, dict) else c) for c in comments]
        except Exception:                                  # noqa: BLE001
            pass
    try:
        if _load("actionlog").enabled(config):
            pg = _load("phase_gate")
            rows = {r[0]: r for r in _status(sdlc_dir, config, key)["rows"]}
            verdicts = [r for r in pg._rows(sdlc_dir, key) if r.get("kind") == "verdict" and r.get("phase") == "plan_review"]
            plan = _load("review_context").phase_doc_file(sdlc_dir, key, "plans")
            facts["gate"] = {"plan_present": pg._file_ok(plan), "plan_ok": bool(rows.get("plan") and rows["plan"][1]),
                             "plan_review_ok": bool(rows.get("plan_review") and rows["plan_review"][1]),
                             "route": verdicts[-1].get("route") if verdicts else None,
                             "agent_id": verdicts[-1].get("agent_id") if verdicts else None}
    except Exception:                                      # noqa: BLE001 - an unreadable record is never a pass
        facts["gate"] = None
    try:
        facts["children_filed"] = children_filed(sdlc_dir, key)
    except (RecordError, ValueError, OSError):
        pass
    return facts


def decompose_refusal(why):
    """The exit-3 text for a child that was not exempt: names the failing leg and the in-goal gesture, never a lever."""
    leg = DECOMPOSE_LEGS.get(why) or ("a fact could not be read (%s)" % why)
    return ("not filed: this `--kind child` is not a proven decompose child [%s]: %s. A decompose child is exempt from "
            "routing only when its meta-goal, the parent's decompose-filed comment, the child's marker and an independent "
            "plan-review of the meta-goal's plan are all recorded; work that belongs to a goal goes in its plan with "
            "`work.py add-slice`" % (why, leg))


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


def check_bound(sdlc_dir, goal, cwd):
    """Refuse (`RouteRefused`, exit 2) a follow-up naming another goal than the one whose worktree holds `cwd`. Every
    spelling of a numeric goal (`21`, `#21`, `21.md`) is one goal. Shared by `gather_facts` and the decompose exemption,
    which skips `gather_facts` and so must bind the goal itself."""
    number = goal_number(goal)
    stem = str(number) if number is not None else _stem(goal)
    bound = bound_goal(sdlc_dir, cwd)
    if bound is not None and bound != stem:
        raise RouteRefused("this session works in goal %s's worktree but the follow-up names goal %s: refused, "
                           "nothing filed (file it against %s)" % (bound, stem, bound))


def gather_facts(sdlc_dir, config, goal, source, body, labels, cwd):
    """-> (goal_unit, item_unit, added, cap, warnings). The only place the router's facts are read."""
    handoff_cap = _load("handoff").max_added_slices(config)
    check_bound(sdlc_dir, goal, cwd)
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


def _meta_stem(goal):
    number = goal_number(goal)
    return str(number) if number is not None else _stem(goal)


def children_filed(sdlc_dir, goal):
    """Children recorded for decompose goal `goal` (the exempt filings `track` made on this machine). Raises
    `RecordError` on a record whose `children` is not a list: never read as zero. Machine-local, like the slice count:
    the ceiling is `max_children x machines`. A crash between filing a child and recording it under-counts by one; the
    recovery lever is the decompose goal's own step-0 read-back of the children on its timeline, not this count."""
    children = read_record(sdlc_dir, _meta_stem(goal)).get("children", [])
    if not isinstance(children, list):
        raise RecordError("follow-up record for %s has a corrupt `children` list" % goal)
    return len(children)


def append_child(sdlc_dir, goal, child_id):
    """Count one decompose child, idempotent on its issue id, under the same lock and atomic write as `append_slice`."""
    meta = _meta_stem(goal)
    with locked(sdlc_dir, meta):
        rec = read_record(sdlc_dir, meta)
        children = rec.get("children", [])
        if not isinstance(children, list):
            raise RecordError("follow-up record for %s has a corrupt `children` list" % goal)
        if str(child_id) not in children:
            rec["children"] = children + [str(child_id)]
            _write(sdlc_dir, meta, rec)
        return rec


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

def _decomposed_child(body):
    """A proven decompose child is filed with no reason marker (A2), so it is recorded by its FIRST-LINE
    `decomposed-from` marker (the same first-line rule the exemption reads)."""
    return bool(DECOMPOSED_FROM_RE.match(str(body or "").split("\n", 1)[0].rstrip("\r")))


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
               if not parse_reason(i.get("body")) and not _decomposed_child(i.get("body"))
               and str(i.get("createdAt") or "") >= cutoff]
    return cutoff, flagged
