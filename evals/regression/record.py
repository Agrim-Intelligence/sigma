#!/usr/bin/env python3
"""Regression record builder: `record.py build <run_dir>` (#874, slice 1 of epic #870).

Reads the durable evidence a Sigma run leaves in a scratch directory and writes ONE JSON record,
schema `sigma.regression-run/v1`, that later slices (checks, properties, CI) consume. The field
names below are a contract for those slices.

Run directory layout (every part optional; each is reported present or absent, never guessed):

    <run_dir>/repo/        a git clone whose `.sdlc/` is the sdlc dir
    <run_dir>/remote.git/  the run's bare remote
    <run_dir>/gh_state.json  the fake GitHub store
    <run_dir>/record.json  the output (override with --out)

Guarantees:
  * read-only against the run dir; the only write is the one output file, created through a temp
    file and `os.replace`, so a crash leaves the old record or none, never half of one;
  * no network, no `gh`, no model call; git runs with every inherited `GIT_*` variable removed;
  * action-log rows are filtered by `actionlog.INTERNAL_KINDS`, the SAME object imported from the
    plugin by path (never a copy), so an agent-writable kind can never reach the record;
  * a missing or malformed stream never raises: it becomes `present: false` with a reason;
  * no build timestamp and no absolute path: two builds of one directory are byte-identical.

Evidence class: a phase `end` row proves `phase_report.py end` was CALLED, not that the phase did
its work, so every phase carries `evidence: "call-existence"` and the record says so in `caveats`.

Cost: one pass over each file, O(journal lines + action-log rows + commits). The journal is held in
memory (bounded by its 30-day prune); `git log` of `main` is unbounded on a huge remote. Scale was
NOT measured in this slice. One output file, overwritten, so nothing grows.

Exit codes: 0 built (absent streams included), 2 refusal (bad arguments or ambiguous goal), 1 an
unexpected failure.
"""
import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

SCHEMA = "sigma.regression-run/v1"
EVIDENCE = "call-existence"
CAVEATS = [
    "phase end rows prove phase_report.py end was called, not that the phase did its work "
    "(evidence: call-existence)",
    "journal and action log are off by default; a stream absent here may mean the feature was off",
]
PHASE_ORDER = ("goal", "research", "plan", "plan_review", "implement", "review", "retro")
# Sibling artifacts a phase files NEXT to the plan/research doc under the same `<stem>-` prefix.
# Dropped from the plan/research glob so a newer `874-controls.md` never answers for the plan.
# This list can go stale: a new sibling suffix someone starts filing must be added here.
SIBLING_SUFFIXES = ("-controls", "-retro", "-code-review", "-measurements", "-plan-review")
STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "skills" / "sigma-loop" / "scripts"
_MODULES = {}


def _load_scripts(name):
    """Load `skills/sigma-loop/scripts/<name>.py` by path, once; later calls return the same module."""
    mod = _MODULES.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location("_sigma_" + name, str(_SCRIPTS / (name + ".py")))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULES[name] = mod
    return mod


INTERNAL = _load_scripts("actionlog").INTERNAL_KINDS     # the same object, never a copy


class Refusal(Exception):
    pass


# --------------------------------------------------------------------------- small readers


def _read_jsonl(path):
    """(rows, skipped): every dict line, and a count of lines that were malformed or not objects."""
    rows, skipped = [], 0
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows, skipped
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            skipped += 1
    return rows, skipped


def _read_json(path):
    try:
        obj = json.loads(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _int(v):
    """An int for an int or a digit string, else None. Never raises. A bool is not a count."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_ABS_POSIX = re.compile(r"(?<![\w.])/[\w.@+~-]+(?:/[\w.@+~-]*)+")
_ABS_WIN = re.compile(r"(?<![\w])[A-Za-z]:[\\/][^\s'\"]*")


def _scrub_str(s):
    """Replace any absolute path inside a string by its last segment (the record carries none)."""
    def last(m):
        return [p for p in re.split(r"[\\/]", m.group(0)) if p][-1]
    return _ABS_WIN.sub(last, _ABS_POSIX.sub(last, s))


def _scrub(value):
    if isinstance(value, str):
        return _scrub_str(value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return dict((k, _scrub(v)) for k, v in value.items())
    return value


def _stream(source, present, reason=None, **fields):
    out = {"source": source, "present": bool(present)}
    if reason:
        out["reason"] = reason
    out.update(fields)
    return out


# --------------------------------------------------------------------------- goal and files


def find_goal(sdlc, goal):
    if goal:
        if not STEM_RE.match(goal):
            raise Refusal("unsafe --goal %r" % goal)
        return goal
    stems = sorted(p.stem for p in (sdlc / "state" / "log").glob("*.jsonl")) if (sdlc / "state" / "log").is_dir() else []
    if len(stems) == 1:
        return stems[0]
    if not stems:
        raise Refusal("no goal found under state/log; pass --goal <stem>")
    raise Refusal("several goals under state/log (%s); pass --goal <stem>" % ", ".join(stems))


def phase_doc(sdlc, sub, stem):
    """(path or None, reason, source). Resolved by `review_context.phase_doc_file`, the one resolver
    the reviewer brief and the pre-push guard share: an exact `<stem>.md` always wins; only a purely
    numeric stem also globs `<stem>-*.md`, newest first; a non-numeric stem resolves exact only.
    On top of it, and only for a slugged hit, sibling artifacts (SIBLING_SUFFIXES) are dropped and the
    newest remaining slugged file wins."""
    base = sdlc / sub
    source = "%s/%s.md" % (sub, stem)
    if not base.is_dir():
        return None, "no %s directory" % sub, source
    hit = _load_scripts("review_context").phase_doc_file(sdlc, stem, sub)
    if hit is None:
        return None, "no file matches", source
    if not (stem.isdigit() and hit.name != stem + ".md" and hit.stem.endswith(SIBLING_SUFFIXES)):
        return hit, None, "%s/%s" % (sub, hit.name)
    kept = [p for p in base.glob(stem + "-*.md") if p.is_file() and not p.stem.endswith(SIBLING_SUFFIXES)]
    if not kept:
        return None, "only sibling artifacts found", source
    best = sorted(kept, key=lambda p: (p.stat().st_mtime, p.name))[-1]
    return best, None, "%s/%s" % (sub, best.name)


def read_doc_stream(sdlc, sub, stem):
    path, reason, source = phase_doc(sdlc, sub, stem)
    if path is None:
        return _stream(source, False, reason, bytes=None, sha256=None)
    return _stream(source, True, bytes=path.stat().st_size, sha256=_sha256(path))


# --------------------------------------------------------------------------- streams


def _action_log_enabled(sdlc):
    cfg = _read_json(sdlc / "config.json") or {}
    block = cfg.get("action_log")
    return isinstance(block, dict) and block.get("enabled") is True


def read_action_log(sdlc, stem):
    path = sdlc / "state" / "log" / (stem + ".jsonl")
    source = "state/log/%s.jsonl" % stem
    if not path.is_file():
        why = "no log file" if _action_log_enabled(sdlc) else "no log file; action_log.enabled is not true"
        return _stream(source, False, why, rows=[], dropped_non_internal=0), []
    rows, skipped = _read_jsonl(path)
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda p: (str(p[1].get("ts", "")), p[0]))
    kept = [_scrub(r) for _, r in indexed if r.get("kind") in INTERNAL]
    return _stream(source, True, rows=kept, dropped_non_internal=len(rows) - len(kept),
                   skipped_malformed=skipped), kept


def read_plan_review(rows, log_present, sdlc, stem):
    source = "state/log (kind=verdict, phase=plan_review)"
    gate = (sdlc / "state" / "gates" / (stem + ".json")).is_file()
    blank = dict(verdict=None, route=None, plan_hash=None, verified=None, gate_file_present=gate)
    if not log_present:
        return _stream(source, False, "action log absent", **blank)
    hits = [r for r in rows if r.get("kind") == "verdict" and r.get("phase") == "plan_review"]
    if not hits:
        return _stream(source, False, "no plan_review verdict row", **blank)
    last = hits[-1]                                    # rows are already ordered by ts
    return _stream(source, True, verdict=last.get("verdict"), route=last.get("route"),
                   plan_hash=last.get("plan_hash"), verified=last.get("verified"), gate_file_present=gate)


def read_verify(sdlc, stem):
    source = "state/verify/%s.json" % stem
    data = _read_json(sdlc / "state" / "verify" / (stem + ".json"))
    if data is None:
        return _stream(source, False, "no readable verify file")
    tf = data.get("test_first")
    return _stream(source, True, exit=data.get("exit"), verify_state=data.get("verify_state"),
                   at=data.get("at"), head=data.get("head"),
                   test_first_passed=tf.get("passed") if isinstance(tf, dict) else None)


def read_journal(sdlc, stem):
    """(stream, goal rows ordered by ts then file order). Only `events/*.jsonl` are read."""
    events = sdlc / "events"
    files = sorted(events.glob("*.jsonl")) if events.is_dir() else []
    if not files:
        return _stream("events/*.jsonl", False, "no journal files", files=0, lines=0,
                       skipped_malformed=0, kinds={}), []
    lines = skipped = 0
    mine = []
    for f in files:
        rows, bad = _read_jsonl(f)
        skipped += bad
        lines += len(rows) + bad
        for row in rows:
            if str(row.get("goal")) == stem:
                mine.append((str(row.get("ts", "")), f.name, len(mine), row))
    mine.sort(key=lambda t: t[:3])
    rows = [t[3] for t in mine]
    kinds = {}
    for r in rows:
        k = str(r.get("kind"))
        kinds[k] = kinds.get(k, 0) + 1
    return _stream("events/*.jsonl", True, files=len(files), lines=lines, skipped_malformed=skipped,
                   kinds=kinds), rows


def _attempt_key(row, pos, notes):
    aid = row.get("attempt_id")
    if isinstance(aid, str) and aid:
        return aid
    notes.append("no attempt_id")
    return "#row%d" % pos


def _sum_field(rows, field, notes):
    vals = []
    for r in rows:
        if field not in r:
            continue
        n = _int(r[field])
        if n is None:
            notes.append("non-numeric %s" % field)
        else:
            vals.append(n)
    return sum(vals) if vals else None


def build_phases(journal_rows):
    by = {}
    for pos, row in enumerate(journal_rows):
        phase = row.get("phase")
        if not isinstance(phase, str) or not phase:
            continue
        kind = row.get("kind")
        if kind == "phase" and row.get("state") in ("start", "end"):
            slot = by.setdefault(phase, {"start": False, "ends": {}, "spends": {}, "notes": []})
            if row["state"] == "start":
                slot["start"] = True
            else:
                slot["ends"][_attempt_key(row, pos, slot["notes"])] = row
        elif kind == "spend":
            slot = by.setdefault(phase, {"start": False, "ends": {}, "spends": {}, "notes": []})
            slot["spends"][_attempt_key(row, pos, slot["notes"])] = row
    out = []
    for phase, s in by.items():
        notes = list(s["notes"])
        out.append({
            "phase": phase, "evidence": EVIDENCE, "start_present": s["start"],
            "end_present": bool(s["ends"]), "attempts": len(s["ends"]),
            # tokens from END events only; cost from SPEND events only, so one attempt is never double counted
            "tokens_in": _sum_field(s["ends"].values(), "tokens_in", notes),
            "tokens_out": _sum_field(s["ends"].values(), "tokens_out", notes),
            "cost_cents": _sum_field(s["spends"].values(), "cost_cents", notes),
            "notes": sorted(set(notes)),
        })
    rank = dict((p, i) for i, p in enumerate(PHASE_ORDER))
    out.sort(key=lambda p: (rank.get(p["phase"], len(rank)), p["phase"]))
    return out


def build_tokens(phases, journal_present):
    source = "journal phase end (tokens) + spend (cost)"
    by_phase = dict((p["phase"], {"tokens_in": p["tokens_in"], "tokens_out": p["tokens_out"],
                                  "cost_cents": p["cost_cents"]})
                    for p in phases if p["tokens_in"] is not None or p["tokens_out"] is not None
                    or p["cost_cents"] is not None)
    if not journal_present:
        return _stream(source, False, "journal absent", by_phase={})
    if not by_phase:
        return _stream(source, False, "no measured phase", by_phase={})
    return _stream(source, True, by_phase=by_phase)


def read_review_evidence(journal_rows, gh_state_path):
    source = "journal review_posted + gh_state.json comment marker"
    posted = []
    for r in journal_rows:
        if r.get("kind") == "review_posted":
            posted.append({"pr": _int(r.get("pr")), "head_sha": r.get("head_sha"),
                           "verdict": r.get("verdict"), "evidence_id": r.get("evidence_id")})
    if not posted:
        return _stream(source, False, "no review_posted event", review_posted=[], marker_in_store=None)
    store = _read_json(gh_state_path) if gh_state_path else None
    marker = None
    if store is not None:
        bodies = []
        prs = store.get("prs")
        for pr in (prs.values() if isinstance(prs, dict) else []):
            comments = pr.get("comments") if isinstance(pr, dict) else None
            for c in (comments if isinstance(comments, list) else []):
                if isinstance(c, dict) and isinstance(c.get("body"), str):
                    bodies.append(c["body"])
        marker = any("<!-- sigma-review-evidence:%s -->" % p["evidence_id"] in b
                     for p in posted for b in bodies if p["evidence_id"])
    return _stream(source, True, review_posted=posted, marker_in_store=marker)


def _git_env():
    """Hermetic: every inherited GIT_* variable removed (GIT_DIR, GIT_OBJECT_DIRECTORY, ...)."""
    env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(remote, *args):
    try:
        p = subprocess.run(["git", "--git-dir", str(remote)] + list(args), env=_git_env(),
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _commits(remote, ref):
    out = _git(remote, "log", "--reverse", "--format=%H%x09%s", ref)
    if out is None:
        return None
    rows = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        rows.append({"sha": sha, "subject": subject})
    return rows


def read_commit_order(remote, stem):
    source = "remote.git"
    if not remote or not pathlib.Path(remote).is_dir():
        return _stream(source, False, "no bare remote", main=[], goal_branches={})
    main = _commits(remote, "refs/heads/main")
    if main is None:
        return _stream(source, False, "git log of main failed", main=[], goal_branches={})
    heads = _git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads/") or ""
    names = sorted(h for h in heads.splitlines() if fnmatch.fnmatchcase(h, "sdlc/*%s*" % stem))
    branches = {}
    for name in names:
        c = _commits(remote, "refs/heads/" + name)
        if c is not None:
            branches[name] = c
    extra = {}
    if not branches:
        extra = {"goal_branches_reason": "no goal branch"}
    return _stream(source, True, main=main, goal_branches=branches,
                   goal_branches_present=bool(branches), **extra)


# --------------------------------------------------------------------------- build + write


def build_record(run_dir, goal=None, repo=None, remote=None, gh_state=None):
    run_dir = pathlib.Path(run_dir)
    repo = pathlib.Path(repo) if repo else run_dir / "repo"
    remote = pathlib.Path(remote) if remote else run_dir / "remote.git"
    gh_state = pathlib.Path(gh_state) if gh_state else run_dir / "gh_state.json"
    sdlc = repo / ".sdlc"
    stem = find_goal(sdlc, goal)
    log_stream, log_rows = read_action_log(sdlc, stem)
    journal_stream, journal_rows = read_journal(sdlc, stem)
    phases = build_phases(journal_rows)
    streams = {
        "plan": read_doc_stream(sdlc, "plans", stem),
        "research": read_doc_stream(sdlc, "research", stem),
        "plan_review_verdict": read_plan_review(log_rows, log_stream["present"], sdlc, stem),
        "verify": read_verify(sdlc, stem),
        "journal": journal_stream,
        "action_log": log_stream,
        "review_evidence": read_review_evidence(journal_rows, gh_state),
        "commit_order": read_commit_order(remote, stem),
        "tokens": build_tokens(phases, journal_stream["present"]),
    }
    return {"schema": SCHEMA, "goal": stem, "run": run_dir.resolve().name, "caveats": list(CAVEATS),
            "phases": phases, "streams": streams}


def write_atomic(path, record):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp.%d" % (path.name, os.getpid()))
    try:
        tmp.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="record.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="write the regression record for a run directory")
    b.add_argument("run_dir")
    b.add_argument("--goal")
    b.add_argument("--repo")
    b.add_argument("--remote")
    b.add_argument("--gh-state", dest="gh_state")
    b.add_argument("--out")
    args = ap.parse_args(argv)
    if args.cmd != "build":
        ap.print_usage(sys.stderr)
        return 2
    try:
        if not pathlib.Path(args.run_dir).is_dir():
            raise Refusal("run_dir %r is not a directory" % args.run_dir)
        rec = build_record(args.run_dir, args.goal, args.repo, args.remote, args.gh_state)
        out = pathlib.Path(args.out) if args.out else pathlib.Path(args.run_dir) / "record.json"
        write_atomic(out, rec)
    except Refusal as exc:
        print("record.py: REFUSED: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:                                # noqa: BLE001 - one line, not a traceback
        print("record.py: failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
