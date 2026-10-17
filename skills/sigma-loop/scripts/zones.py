"""Print each file's change zone, whether it is new on this branch, and the one rule that applies.

    zones.py classify [--base REF] [--] <path>...
    zones.py check --goal N (--plan FILE | --diff) [--base REF]

One output row per path, tab-separated: `<path>\\t<zone>\\t<state>\\t<rule>`.
  zone   legacy | modern | undeclared | unreachable     (from `design.zones` in `.sdlc/config.json`)
  state  new | existing | unknown                        (is the path absent at the branch's fork point)

READ-ONLY. Its only write is the kit-wide script-time record every script makes through
`timing_store.timed_main` (gitignored, under `.sdlc/state/time`); it calls no network and no `gh`; it runs a constant number of `git`
processes whatever the path count (one `rev-parse`, one `merge-base`, one batched `cat-file`).
Nothing infers zones: the owner declares them (docs/change-zones.md).

REFUSAL (exit 2, empty stdout, typed reason on stderr): a path outside the checkout, a path
containing a newline, carriage return or tab (the output is line- and tab-delimited), a `--base`
value starting with a dash, or malformed
`design.zones` (the reason names `design.zones[i]`). Everything else exits 0, including rows whose
state is `unknown` or whose zone is `unreachable`: those are stated gaps, never a silent pass.

A path that starts with `-` must follow `--`: `zones.py classify -- -odd.py`.

`check` measures how much LEGACY code (existing at the fork point, zone `legacy`) a plan or the
working-tree diff touches, and whether the goal's acceptance record declared that intent. One row per
legacy file, `<path>\\tlegacy\\t<changed|binary|->\\t<restructure|->\\t<intent: yes|no|unknown>`, then
`verdict: over|within|unknown`; exit 0 for all three. See docs/change-zones.md.
"""
import argparse
import fnmatch
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent

RULE_LEGACY = "legacy: change only what the goal needs; extend by sprout or wrap at one seam"
RULE_NEW = "follow sound design: structure for the forces the goal names"
RULE_UNDECLARED = "follow the surrounding code"
RULE_UNKNOWN = "unknown: merge-base unresolved; apply no zone rule"
RULE_UNREACHABLE = "unreachable: config unreadable; apply no zone rule"

GIT_TIMEOUT = 10
DEFAULT_RESTRUCTURE_LINES = 40
MAX_PLAN_BYTES = 256 * 1024


class Refusal(Exception):
    """A typed refusal: exit 2, nothing on stdout."""


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(args, cwd, stdin=None):
    """(rc, stdout) of one git call. rc is None when git could not run or timed out."""
    try:
        # bytes in and out: a path need not be UTF-8, and surrogateescape round-trips every byte
        p = subprocess.run([os.fsencode("git"), b"-C", os.fsencode(str(cwd)), b"--no-optional-locks",
                            *map(os.fsencode, args)],
                           input=None if stdin is None else os.fsencode(stdin),
                           capture_output=True, timeout=GIT_TIMEOUT)
        return p.returncode, os.fsdecode(p.stdout)
    except Exception:                   # noqa: BLE001 - git missing or wedged: typed as unknown by callers
        return None, ""


def _toplevel(cwd):
    rc, out = _git(["rev-parse", "--show-toplevel"], cwd)
    if rc != 0 or not out.strip():
        raise Refusal("not inside a git checkout: %s" % cwd)
    return os.path.realpath(out.strip())


def _normalise(arg, cwd, top):
    if "\n" in arg or "\r" in arg or "\t" in arg:
        raise Refusal("path contains a newline, carriage return or tab: the output is "
                      "line-oriented and tab-separated")
    p = arg.replace(os.sep, "/") if os.sep != "/" else arg      # a backslash is a name byte on POSIX
    full = p if os.path.isabs(p) else os.path.join(str(cwd), p)
    base = os.path.basename(full)
    if base in ("", ".", ".."):
        resolved = os.path.realpath(full)
    else:
        # git tracks a symlink ITSELF, so only the parent directory is resolved, never the last part
        resolved = os.path.join(os.path.realpath(os.path.dirname(full)), base)
    rel = os.path.relpath(resolved, top)
    if rel == ".." or rel.startswith(".." + os.sep) or rel == ".":
        raise Refusal("path is outside the checkout: %s" % arg)
    return rel.replace(os.sep, "/")


def _config(top):
    """(config dict | None). None = unreachable (missing, unreadable, malformed, or no root)."""
    try:
        ns = _load("north_star")
        root, why = ns.project_root(top)
        if root is None:
            return None
        state = _load("state")
        return state.load_config(pathlib.Path(root) / ".sdlc")
    except Exception:                   # noqa: BLE001 - every failure is the one typed state
        return None


def _tname(v):
    return "null" if v is None else type(v).__name__


def _validate(cfg):
    """The validated zone list (possibly empty). Refuses a malformed `design.zones`, naming the entry."""
    if "design" not in cfg:
        return []
    design = cfg["design"]
    if not isinstance(design, dict):
        raise Refusal("design must be an object, got %s" % _tname(design))
    if "zones" not in design:
        return []
    zones = design["zones"]
    if not isinstance(zones, list):
        raise Refusal("design.zones must be a list, got %s" % _tname(zones))
    out = []
    for i, e in enumerate(zones):
        where = "design.zones[%d]" % i
        if not isinstance(e, dict):
            raise Refusal("%s must be an object with glob and zone" % where)
        if set(e) != {"glob", "zone"}:
            raise Refusal("%s must have exactly the keys glob and zone, got %s" % (where, sorted(e)))
        if not isinstance(e["glob"], str) or not e["glob"]:
            raise Refusal("%s.glob must be a non-empty string" % where)
        if e["zone"] not in ("legacy", "modern"):
            raise Refusal("%s.zone must be legacy or modern" % where)
        out.append((e["glob"], e["zone"]))
    return out


def _match(zones, rel):
    """Zone of the LAST entry whose glob matches `rel` (case-sensitive; `*` crosses `/`)."""
    for glob, zone in reversed(zones):
        if fnmatch.fnmatchcase(rel, glob):
            return zone
    return "undeclared"


def _find_record_base(top, branch):
    """`<remote>/<base>` from the work record whose `branch` is `branch`, else None. The record is
    found by SCANNING `<common .sdlc>/state/work/*.json` (plain JSON): the branch prefix is configurable,
    so none is assumed."""
    try:
        root, _why = _load("north_star").project_root(top)
        if root is None:
            return None
        for f in sorted((pathlib.Path(root) / ".sdlc" / "state" / "work").glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get("branch") == branch and rec.get("base"):
                return "%s/%s" % (rec.get("remote") or "origin", rec["base"])
    except Exception:                   # noqa: BLE001 - no record is the typed `unknown`
        return None
    return None


def _fork_point(top, base):
    """Commit id of `git merge-base HEAD <ref>`, or None (every failure reads as unknown)."""
    ref = base
    if ref is None:
        rc, out = _git(["symbolic-ref", "--short", "HEAD"], top)
        if rc != 0 or not out.strip():
            return None                 # detached or unborn HEAD
        ref = _find_record_base(top, out.strip())
        if ref is None:
            return None
    rc, out = _git(["merge-base", "HEAD", ref], top)
    fork = out.strip()
    return fork if rc == 0 and fork else None


_EXISTING_TYPES = ("blob", "tree", "commit", "submodule")
# A path that is a gitlink prints `submodule` when its commit is not in this object store and `commit`
# when it is (measured, git 2.55); a tree entry can only be a `commit` if it is a gitlink.
_GITLINK_TYPES = ("commit", "submodule")


def _newness(top, fork, rels):
    """{rel: new|existing|unknown}. ONE `cat-file --batch-check` over `<fork>:<path>` for each path and
    each of its parent directories. `missing` = new; a second token in blob|tree|commit|submodule =
    existing (a gitlink prints `<oid> submodule`, two fields); anything else, a non-zero rc, or a
    different line count = unknown for every path. A path under a gitlink is unknown, never new."""
    if fork is None:
        return {r: "unknown" for r in rels}
    seen = {}
    for r in rels:
        parts = r.split("/")
        for i in range(1, len(parts) + 1):
            seen["/".join(parts[:i])] = None        # insertion-ordered set: O(1) per key
    keys = list(seen)
    rc, out = _git(["cat-file", "--batch-check"], top,
                   stdin="".join("%s:%s\n" % (fork, k) for k in keys))
    lines = out.split("\n")[:-1] if out.endswith("\n") else out.split("\n")   # not splitlines(): \x0b, \x0c, \x85 are path bytes
    if rc != 0 or len(lines) != len(keys):
        return {r: "unknown" for r in rels}
    kind = {}
    for k, line in zip(keys, lines):
        # Only a `missing` line echoes the key we sent, and a path may hold spaces: compare the whole
        # line to `<key> missing`, never split it. An existing line is `<oid> <type> <size>`, no path.
        toks = line.split()
        if line == "%s:%s missing" % (fork, k):
            kind[k] = "missing"
        elif len(toks) >= 2 and toks[1] in _EXISTING_TYPES:
            kind[k] = toks[1]
        else:
            return {r: "unknown" for r in rels}
    res = {}
    for r in rels:
        parts = r.split("/")
        parents = ["/".join(parts[:i]) for i in range(1, len(parts))]
        if any(kind[p] in _GITLINK_TYPES for p in parents):
            res[r] = "unknown"
        else:
            res[r] = "new" if kind[r] == "missing" else "existing"
    return res


def _rule(zone, state):
    if state == "unknown":
        return RULE_UNKNOWN
    if state == "new" or zone == "modern":
        return RULE_NEW
    return RULE_LEGACY if zone == "legacy" else RULE_UNDECLARED


def classify(paths, cwd, base=None):
    if base is not None and base.startswith("-"):
        raise Refusal("--base must be a ref, not an option: %s" % base)
    top = _toplevel(cwd)
    rels = [_normalise(a, cwd, top) for a in paths]
    cfg = _config(top)
    zones = _validate(cfg) if cfg is not None else []
    state = _newness(top, _fork_point(top, base), rels)
    rows = []
    for rel in rels:
        if cfg is None:        # unreachable and unknown rows print ahead of any zone rule
            rows.append((rel, "unreachable", state[rel], RULE_UNREACHABLE))
        else:
            zone = _match(zones, rel)
            rows.append((rel, zone, state[rel], _rule(zone, state[rel])))
    return rows


def _restructure_lines(cfg):
    """The `design.restructure_lines` threshold: 40 when absent, else an int >= 1 (never a bool)."""
    design = cfg.get("design")
    if not isinstance(design, dict) or "restructure_lines" not in design:
        return DEFAULT_RESTRUCTURE_LINES
    v = design["restructure_lines"]
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise Refusal("design.restructure_lines must be an integer >= 1, got %r" % (v,))
    return v


def _main_root(top):
    root, _why = _load("north_star").project_root(top)
    return None if root is None else pathlib.Path(root)


def _goal_base(top, goal):
    """(`<remote>/<base>`, None) from this goal's work record, else (None, why). The record's branch
    must be HEAD's branch: a record for another branch says nothing about this checkout."""
    root = _main_root(top)
    rc, out = _git(["symbolic-ref", "--short", "HEAD"], top)
    if root is None or rc != 0 or not out.strip():
        return None, "no branch or project root to read the work record from"
    try:
        rec = json.loads((root / ".sdlc" / "state" / "work" / ("%s.json" % goal)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "no readable work record for goal %s" % goal
    if not isinstance(rec, dict) or rec.get("branch") != out.strip() or not rec.get("base"):
        return None, "the work record for goal %s is not for branch %s" % (goal, out.strip())
    return "%s/%s" % (rec.get("remote") or "origin", rec["base"]), None


def _diff_counts(top, fork):
    """[(path, changed)] for the WORKING TREE against `fork`, or None when git failed. `changed` is
    added plus deleted lines, or "binary". A rename is one entry keyed on the OLD path. `diff-index`,
    never `diff`: `diff` rewrites a stat-stale `.git/index`, `diff-index` never touches it."""
    rc, out = _git(["-c", "diff.algorithm=myers", "-c", "diff.renames=true", "diff-index", "--numstat",
                    "-z", "-M", "--no-textconv", "--no-ext-diff", fork], top)
    if rc != 0:
        return None
    items, i, found = out.split("\0"), 0, []
    while i < len(items) and items[i]:
        parts = items[i].split("\t", 2)
        if len(parts) != 3:
            return None
        added, deleted, path = parts
        i += 1
        if path == "":                              # rename: `A\tD\t\0old\0new\0`
            if i + 1 >= len(items):
                return None
            path, i = items[i], i + 2
        if added == "-" and deleted == "-":
            found.append((path, "binary"))
        elif added.isdigit() and deleted.isdigit():
            found.append((path, int(added) + int(deleted)))
        else:
            return None
    return _drop_unchanged_binaries(top, fork, found)


def _drop_unchanged_binaries(top, fork, found):
    """`diff-index --numstat` prints `-\\t-` for a binary file that is merely stat-stale (its bytes equal
    the fork blob), where a text file prints nothing. Drop each such entry by comparing content: one
    `cat-file --batch-check` for the fork oids and one read-only `hash-object --stdin-paths` (no `-w`,
    so nothing is written and the index is untouched). Hashing WITH filters, the default, matches
    what diff-index compares (the clean-filtered worktree bytes). Entries that cannot be compared
    (deleted, not a regular file, newline in the path, any git failure) are kept: fail toward over."""
    cand = [p for p, n in found if n == "binary" and "\n" not in p
            and os.path.isfile(os.path.join(top, p)) and not os.path.islink(os.path.join(top, p))]
    if not cand:
        return found
    rc, out = _git(["cat-file", "--batch-check"], top, stdin="".join("%s:%s\n" % (fork, p) for p in cand))
    lines = out.split("\n")[:-1]
    rc2, out2 = _git(["hash-object", "--stdin-paths"], top, stdin="".join(p + "\n" for p in cand))
    hashes = out2.split("\n")[:-1]
    if rc != 0 or rc2 != 0 or len(lines) != len(cand) or len(hashes) != len(cand):
        return found
    same = set(p for p, line, h in zip(cand, lines, hashes)
               if line.split()[:1] == [h] and line.split()[1:2] == ["blob"])
    return [(p, n) for p, n in found if p not in same]


def _spans(text):
    """Backticked, whitespace-free, non-empty spans, in order."""
    return [m for m in re.findall(r"`([^`]*)`", text) if m and not re.search(r"\s", m)]


def _intent_tokens(criteria):
    """Paths a criterion names: each span with `./` and one trailing `/` stripped. Absolute spans,
    any `..` component and any of `*?[` are skipped, so a token is always a literal relative path."""
    out = []
    for crit in criteria:
        for span in _spans(crit):
            t = span[2:] if span.startswith("./") else span
            t = t[:-1] if t.endswith("/") else t
            if t in ("", ".") or t.startswith("/") or ".." in t.split("/") or any(c in t for c in "*?["):
                continue
            out.append(t)
    return out


def _intent(top, goal):
    """(reader, known). The reader maps path -> yes|no|unknown from the MAIN checkout's record;
    `known` is False when the record is missing, malformed, unreadable or undated. A drafted record
    (`drafted: true`) never grants intent."""
    try:
        root = _main_root(top)
        if root is None:
            raise ValueError("no project root")
        rec = _load("acceptance").read(root / ".sdlc", goal)
    except (OSError, ValueError):
        return (lambda rel: "unknown"), False
    if rec.get("drafted") is None:
        return (lambda rel: "unknown"), False
    if rec["drafted"] is True:
        return (lambda rel: "no"), True
    tokens = _intent_tokens(rec["criteria"])
    return (lambda rel: "yes" if any(rel == t or rel.startswith(t + "/") for t in tokens) else "no"), True


def _plan_paths(plan, top):
    """Repo-relative paths from backtick spans inside the plan's `## Files` / `## File structure`
    section, de-duplicated; None when the plan has no such section."""
    try:
        with open(plan, "rb") as fh:
            data = fh.read(MAX_PLAN_BYTES + 1)
    except OSError as exc:
        raise Refusal("cannot read plan %s: %s" % (plan, exc.strerror or exc))
    if len(data) > MAX_PLAN_BYTES:
        raise Refusal("plan %s exceeds %d KiB" % (plan, MAX_PLAN_BYTES // 1024))
    inside, section, paths = False, False, {}
    for line in data.decode("utf-8", "replace").splitlines():
        if re.match(r"^##\s+", line):
            inside = bool(re.match(r"^##\s+(Files|File structure)\b", line))
            section = section or inside
            continue
        if inside:
            for span in _spans(line):
                try:
                    paths[_normalise(span, top, top)] = None
                except Refusal:
                    pass
    return list(paths) if section else None


def _blobs(top, fork, rels):
    """The subset of `rels` that is a blob at `fork` (one batched `cat-file`), or None on failure."""
    if not rels:
        return set()
    rc, out = _git(["cat-file", "--batch-check"], top, stdin="".join("%s:%s\n" % (fork, r) for r in rels))
    lines = out.split("\n")[:-1] if out.endswith("\n") else out.split("\n")
    if rc != 0 or len(lines) != len(rels):
        return None
    return set(r for r, line in zip(rels, lines) if len(line.split()) >= 2 and line.split()[1] == "blob")


def _row_text(row):
    for cell in row:
        if "\n" in cell or "\r" in cell or "\t" in cell:
            raise Refusal("path contains a newline, carriage return or tab: the output is "
                          "line-oriented and tab-separated")
    return "\t".join(row)


def _verdict(rows, any_unknown, plan):
    """over: a restructure with no intent (a plan has no line count: naming a legacy file is the
    measure). unknown: any intent or state unknown. Else within."""
    if any((plan or r[3] == "restructure") and r[4] == "intent: no" for r in rows):
        return "over"
    if any_unknown or any(r[4] == "intent: unknown" for r in rows):
        return "unknown"
    return "within"


def check(goal, plan, diff, base, cwd):
    """(lines, why). `lines` is the printable rows and verdict; `why` is a stderr note or None."""
    if goal is None:
        raise Refusal("--goal is required")
    if (plan is None) == (not diff):
        raise Refusal("exactly one of --plan and --diff is required")
    if base is not None and base.startswith("-"):
        raise Refusal("--base must be a ref, not an option: %s" % base)
    reason = _load("state").unsafe_goal_reason(goal)
    if reason:
        raise Refusal("unsafe goal: %s" % reason)
    top = _toplevel(cwd)
    cfg = _config(top)
    zones = _validate(cfg) if cfg is not None else []
    limit = _restructure_lines(cfg) if cfg is not None else DEFAULT_RESTRUCTURE_LINES
    paths = _plan_paths(plan, top) if plan is not None else None
    if cfg is None:
        return ["verdict: unknown"], "config unreadable"
    ref, why = (base, None) if base is not None else _goal_base(top, goal)
    fork = _fork_point(top, ref) if ref is not None else None
    if fork is None:
        return ["verdict: unknown"], "verdict unknown: " + (why or "merge-base unresolved")
    intent, known = _intent(top, goal)
    odd = []
    if plan is not None:
        if paths is None:
            return ["verdict: unknown"], "the plan has no `## Files` section"
        blobs = _blobs(top, fork, paths)
        if blobs is None:
            return ["verdict: unknown"], "git cat-file failed"
        legacy = [r for r in paths if r in blobs and _match(zones, r) == "legacy"]
        rows = [(r, "legacy", "-", "-", "intent: " + intent(r)) for r in legacy]
        unknown = not paths
    else:
        found = _diff_counts(top, fork)
        if found is None:
            return ["verdict: unknown"], "git diff-index failed"
        odd = [p for p, _n in found if "\n" in p]      # a newline breaks the line-based batch below
        state = _newness(top, fork, [p for p, _n in found if "\n" not in p])
        rows, unknown = [], False
        if odd:
            unknown = True
            why = "%d changed path(s) contain a newline and cannot be measured, e.g. %r" % (len(odd), odd[0])
        for path, n in found:
            if path in odd:
                continue
            if state[path] == "unknown":
                unknown = True
            elif state[path] == "existing" and _match(zones, path) == "legacy":
                restructure = n == "binary" or n > limit
                rows.append((path, "legacy", str(n), "restructure" if restructure else "-",
                             "intent: " + intent(path)))
    if not known:
        unknown = True                  # AC-4: no usable record is never `within`, rows or no rows
    head = [] if rows or known else ["intent: unknown"]
    return head + [_row_text(r) for r in rows] + ["verdict: " + _verdict(rows, unknown, plan is not None)], (
        why if plan is None and odd else None)


def main(argv):
    ap = argparse.ArgumentParser(prog="zones.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify", help="print zone, state and rule per path")
    c.add_argument("--base", default=None, help="ref to take the fork point against")
    c.add_argument("paths", nargs="+", help="paths; put -- before any that starts with a dash")
    k = sub.add_parser("check", help="measure legacy change and refactor intent for a plan or diff")
    k.add_argument("--goal", default=None, help="goal number (reads its acceptance record and work record)")
    k.add_argument("--plan", default=None, help="plan file: measure the legacy files its Files section names")
    k.add_argument("--diff", action="store_true", help="measure the working tree against the fork point")
    k.add_argument("--base", default=None, help="ref to take the fork point against")
    args = ap.parse_args(argv[1:])
    try:
        if args.cmd == "check":
            lines, why = check(args.goal, args.plan, args.diff, args.base, os.getcwd())
            if why:
                print("zones.py: note: %s" % why, file=sys.stderr)
            sys.stdout.buffer.write(os.fsencode("".join(ln + "\n" for ln in lines)))
            return 0
        rows = classify(args.paths, os.getcwd(), args.base)
    except Refusal as exc:
        print("zones.py: REFUSED: %s" % exc, file=sys.stderr)
        return 2
    sys.stdout.buffer.write(os.fsencode("".join("\t".join(r) + "\n" for r in rows)))
    return 0


if __name__ == "__main__":
    _ts = _load("timing_store")
    sys.exit(_ts.timed_main(main, sys.argv, "zones"))
