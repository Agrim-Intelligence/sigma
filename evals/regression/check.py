#!/usr/bin/env python3
"""Regression checker: `check.py <record> <properties.json>` (#877, slice 2 of epic #870).

Reads ONE record written by `record.py build` (schema `sigma.regression-run/v1`) and a property list,
and prints one line per property, starting with exactly one of three words:

    PASS            the evidence satisfies the property
    FAIL            the evidence is present and does not satisfy it
    NOT EVALUABLE   the source stream is absent (or the field is missing), so nothing can be said

Evidence class: a phase end row proves `phase_report.py end` was CALLED, not that the phase did its
work. Output says "phase end row recorded", never "completed". This checker reads a record; it ran
nothing live and measured nothing about Sigma itself.

Properties (`properties.json`): each has `id`, `family` (outcome, process, cost, quality, safety),
`hard`, `stream` (a record stream name, a list of them, or `record` for the whole record), `check`
(a closed operator: log_row, log_phase_ends, stream_equals, stream_nonempty, sum_positive, subset_of,
no_abs_paths, kinds_internal, caveat_present) and `why`. A `hard` property whose stream is absent
is NOT EVALUABLE and exits 1; a soft one is NOT EVALUABLE and exits 0.

Process properties read INTERNAL action-log rows only (`streams.action_log.rows`), never the
journal-derived `phases[]`. A property naming an agent-writable kind (`actionlog.AGENT_KINDS`, which
an agent can append through `loop.py log`) is refused: INTERNAL is the SAME object `record.py` filters
with, loaded through it, never a copy.

Exit codes: 0 every property PASS (soft NOT EVALUABLE allowed); 1 any FAIL or any hard property that is
NOT EVALUABLE (or an unexpected failure, one `check.py: failed:` line); 2 REFUSAL (`check.py: REFUSED:
<reason>` on stderr, nothing on stdout): unreadable or malformed input, wrong schema, no `streams`,
a malformed property list, or a property naming an agent-writable kind. 2 is distinct from 1 so a
broken input is never read as a product regression.

Cost: O(properties x action-log rows) plus one walk of every string in the record; the record is held
in memory (same ceiling as record.py, not measured at 10x/100x). Read-only, no state file.
"""
import importlib.util
import json
import pathlib
import re
import sys

SCHEMA = "sigma.regression-run/v1"
FAMILIES = ("outcome", "process", "cost", "quality", "safety")
OPERATORS = ("log_row", "log_phase_ends", "stream_equals", "stream_nonempty", "sum_positive",
             "subset_of", "no_abs_paths", "kinds_internal", "caveat_present")
REQUIRED = ("id", "family", "hard", "stream", "check", "why")

_RECORD_PY = pathlib.Path(__file__).resolve().with_name("record.py")
_spec = importlib.util.spec_from_file_location("_sigma_regression_record", str(_RECORD_PY))
record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(record)
_actionlog = record._load_scripts("actionlog")
INTERNAL = _actionlog.INTERNAL_KINDS              # the same object record.py filters with, never a copy
AGENT_KINDS = _actionlog.AGENT_KINDS

_ABS_POSIX = re.compile(r"(?<![\w.])/[\w.@+~-]+(?:/[\w.@+~-]*)+")
_ABS_WIN = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s'\"]*")


class Refusal(Exception):
    pass


class Absent(Exception):
    """The evidence a property needs is not in the record."""


def _load_json(path, what):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refusal("cannot read %s %r: %s" % (what, str(path), exc.strerror or type(exc).__name__))
    except ValueError as exc:
        raise Refusal("%s %r is not valid JSON: %s" % (what, str(path), exc))


def load_record(path):
    rec = _load_json(path, "record")
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        raise Refusal("record schema is not %s" % SCHEMA)
    if not isinstance(rec.get("streams"), dict):
        raise Refusal("record has no `streams` object")
    return rec


def load_properties(path):
    doc = _load_json(path, "properties")
    props = doc.get("properties") if isinstance(doc, dict) else None
    if not isinstance(props, list) or not props:
        raise Refusal("properties file needs a non-empty `properties` list")
    seen = set()
    for p in props:
        if not isinstance(p, dict):
            raise Refusal("a property is not an object")
        missing = [f for f in REQUIRED if f not in p]
        if missing:
            raise Refusal("property %r is missing %s" % (p.get("id"), ", ".join(missing)))
        if not isinstance(p["id"], str) or p["id"] in seen:
            raise Refusal("duplicate or non-string property id %r" % (p["id"],))
        seen.add(p["id"])
        if p["family"] not in FAMILIES:
            raise Refusal("property %s: unknown family %r" % (p["id"], p["family"]))
        if p["check"] not in OPERATORS:
            raise Refusal("property %s: unknown check operator %r" % (p["id"], p["check"]))
        if not isinstance(p["hard"], bool):
            raise Refusal("property %s: `hard` must be true or false" % p["id"])
        kinds = p.get("kinds", [])
        if not isinstance(kinds, list):
            raise Refusal("property %s: `kinds` must be a list" % p["id"])
        for k in kinds:
            if k in AGENT_KINDS or k not in INTERNAL:
                raise Refusal("property %s names kind %r, which is not an INTERNAL action-log kind "
                              "(agent-writable kinds cannot back a process property)" % (p["id"], k))
    return props


# --------------------------------------------------------------------------- operators
# Each returns (ok, detail) and raises Absent when the evidence is not in the record.


def _streams(rec, prop):
    names = prop["stream"] if isinstance(prop["stream"], list) else [prop["stream"]]
    out = []
    for n in names:
        if n == "record":
            out.append(rec)
            continue
        s = rec["streams"].get(n)
        if not isinstance(s, dict) or s.get("present") is not True:
            raise Absent("stream %s is absent" % n)
        out.append(s)
    return out


def _rows(stream):
    rows = stream.get("rows")
    if not isinstance(rows, list):
        raise Absent("stream has no rows")
    return [r for r in rows if isinstance(r, dict)]


def op_log_row(rec, prop, stream):
    rows = [r for r in _rows(stream) if r.get("kind") in prop.get("kinds", [])
            and all(r.get(k) == v for k, v in (prop.get("match") or {}).items())]
    return bool(rows), "%d matching row(s)" % len(rows)


def op_log_phase_ends(rec, prop, stream):
    ends = set(r.get("phase") for r in _rows(stream)
               if r.get("kind") in prop.get("kinds", ["phase"]) and r.get("state") == "end")
    missing = [p for p in prop["phases"] if p not in ends]
    if missing:
        return False, "no phase end row recorded for: %s" % ", ".join(missing)
    return True, "phase end row recorded for: %s" % ", ".join(prop["phases"])


def op_stream_equals(rec, prop, stream):
    for field, want in prop["equals"].items():
        if field not in stream:
            raise Absent("field %s missing" % field)
        if stream[field] != want or type(stream[field]) is not type(want):
            return False, "%s is %r, expected %r" % (field, stream[field], want)
    return True, ", ".join("%s == %r" % kv for kv in sorted(prop["equals"].items()))


def op_stream_nonempty(rec, prop, stream):
    for field in prop["fields"]:
        if field not in stream:
            raise Absent("field %s missing" % field)
        if not stream[field]:
            return False, "%s is empty" % field
    return True, "%s non-empty" % ", ".join(prop["fields"])


def op_sum_positive(rec, prop, stream):
    group = stream.get(prop["path"])
    if not isinstance(group, dict):
        raise Absent("%s missing" % prop["path"])
    vals = [v.get(prop["field"]) for v in group.values() if isinstance(v, dict)]
    vals = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
    if not vals:
        raise Absent("no measured %s" % prop["field"])
    return sum(vals) > 0, "sum of %s is %d" % (prop["field"], sum(vals))


def op_subset_of(rec, prop, stream):
    branches = stream.get("goal_branches")
    if not isinstance(stream.get("main"), list) or not isinstance(branches, dict) or not branches:
        raise Absent("no goal branch in the remote")
    main = set(c.get("sha") for c in stream["main"] if isinstance(c, dict))
    stray = [c.get("sha", "")[:12] for cs in branches.values() for c in cs
             if isinstance(c, dict) and c.get("sha") not in main]
    if stray:
        return False, "goal-branch commit(s) not on main: %s" % ", ".join(stray)
    return True, "every goal-branch commit is on main"


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k if isinstance(k, str) else str(k)
            for s in _strings(v):
                yield s
    elif isinstance(node, list):
        for v in node:
            for s in _strings(v):
                yield s


def op_no_abs_paths(rec, prop, stream):
    for s in _strings(stream):
        m = _ABS_POSIX.search(s) or _ABS_WIN.search(s)
        if m:
            return False, "absolute path in the record: %s" % m.group(0)
    return True, "no absolute path"


def op_kinds_internal(rec, prop, stream):
    bad = sorted(set(str(r.get("kind")) for r in _rows(stream) if r.get("kind") not in INTERNAL))
    if bad:
        return False, "non-INTERNAL kind(s) in the log: %s" % ", ".join(bad)
    return True, "every row kind is INTERNAL"


def op_caveat_present(rec, prop, stream):
    caveats = stream.get("caveats")
    needle = prop.get("contains", "call-existence")
    if not isinstance(caveats, list):
        raise Absent("record has no caveats list")
    return any(isinstance(c, str) and needle in c for c in caveats), "caveat mentioning %s" % needle


_OPS = {"log_row": op_log_row, "log_phase_ends": op_log_phase_ends, "stream_equals": op_stream_equals,
        "stream_nonempty": op_stream_nonempty, "sum_positive": op_sum_positive, "subset_of": op_subset_of,
        "no_abs_paths": op_no_abs_paths, "kinds_internal": op_kinds_internal,
        "caveat_present": op_caveat_present}


def evaluate(rec, prop):
    """(word, detail) for one property."""
    try:
        ok = detail = None
        for stream in _streams(rec, prop):
            ok, detail = _OPS[prop["check"]](rec, prop, stream)
            if not ok:
                break
    except Absent as exc:
        return "NOT EVALUABLE", str(exc)
    except (KeyError, TypeError, AttributeError) as exc:
        return "NOT EVALUABLE", "record shape unexpected (%s)" % type(exc).__name__
    return ("PASS" if ok else "FAIL"), detail


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 2:
            raise Refusal("usage: check.py <record> <properties.json>")
        rec = load_record(argv[0])
        props = load_properties(argv[1])
        results = [(p, ) + evaluate(rec, p) for p in props]
    except Refusal as exc:
        print("check.py: REFUSED: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:                                # noqa: BLE001 - one line, not a traceback
        print("check.py: failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    for p, word, detail in results:
        print("%-13s %s [%s%s] %s" % (word, p["id"], p["family"], ", hard" if p["hard"] else "", detail))
    fails = [p["id"] for p, w, _ in results if w == "FAIL"]
    stuck = [p["id"] for p, w, _ in results if w == "NOT EVALUABLE" and p["hard"]]
    n = dict((w, sum(1 for _, x, _ in results if x == w)) for w in ("PASS", "FAIL", "NOT EVALUABLE"))
    print("summary: %d PASS, %d FAIL, %d NOT EVALUABLE" % (n["PASS"], n["FAIL"], n["NOT EVALUABLE"]))
    return 1 if fails or stuck else 0


if __name__ == "__main__":
    sys.exit(main())
