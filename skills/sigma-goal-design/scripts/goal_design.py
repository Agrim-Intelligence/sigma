#!/usr/bin/env python3
"""goal_design.py (#2032) -- the engine behind `sigma-goal-design`'s sweep-round budget, and the
verb `sigma-goal-review` re-runs to check it.

WHY THIS SCRIPT EXISTS AT ALL. Before #2032, "the budget is three sweep rounds" was a number
stated only in `SKILL.md` prose -- nothing in Python counted a round, and nothing checked what a
design pass claimed it ran under. A `goal_design.rounds` config key that no code path read would
have been exactly that same silent half-guarantee with an extra name on it, which SAFETY forbids
(`AGENTS.md`) -- and it is why an earlier pass correctly declined to add one. This module is the
fix: `sweep_budget()` is engine-owned data behind a CLI verb, mirroring
`skills/sigma-dossier/scripts/dossier.py`'s `followups()` -- the number a pass obeys is READ BACK
from here, never remembered, and the same call is what `goal-review` re-runs to check the
artifact's `**Budget**` field against what the verb actually returns, closing the hole the #2032
adversarial review found: a pass that skips the verb and writes `3` from memory produced an
artifact indistinguishable from a correct one.

THE SHAPE. `goal_design.rounds` (an optional positive integer under the existing `goal_design`
config block) raises the ceiling a `full`-mode design pass obeys, so an operator who wants a pass
to "scan the whole product" can raise it -- bounded, always a number the operator chose, never an
`"unbounded"` sentinel (each round is real Anthropic spend, and an infinite sweep is not something
anyone can opt into safely). Absent, the ceiling is unchanged from before this issue: 3, so an
install that touches no config gets byte-identical behaviour.

`mode: lane` does NOT read this key at all -- lane budgets are fixed at one round per measured lane
(`small`=1, `medium`=2, `large`=3; only `small` was ever numbered anywhere in `SKILL.md` before
this -- `medium`/`large` are a genuine spec gap this closes, not a new decision dressed as a
restatement). No evidence across the three real design passes on record (#1942, #1973, #2017) ever
needed lane's own ceiling moved, and the live config runs `mode: full` -- so a `rounds` key set
under `mode: lane` is reported back as ignored (`rounds_config_ignored`) rather than silently doing
nothing, with the escape hatch (switch to `mode: full`) named on stderr.

THE BOOL TRAP. `True == 1` and `isinstance(True, int)` are both true in Python (`bool` is an `int`
subclass), so a bare `isinstance(raw, int)` check alone would silently accept a stray
`"rounds": true` in a hand-edited config as `rounds=1`. The explicit `not isinstance(raw, bool)`
guard in `sweep_budget()` below is the only thing standing between that and a config typo passing
as a deliberate override -- see `tests/test_goal_design.py`'s own control, which removes the guard
and watches `True` get silently accepted.

    python3 goal_design.py sweep-budget <sdlc_dir> --mode full|lane --lane small|medium|large
"""
import json, pathlib, re, sys, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load_loop_script(name):
    """Cross-load a script from the sibling `sigma-loop` skill (skills/sigma-goal-design/scripts/ ->
    skills/sigma-loop/scripts/<name>.py) -- the established, narrow, named exception to "don't reach
    across skill directories" (`dossier.py`'s own `_load_loop_script`, `compile_plan.py`'s,
    `define.py`'s, `brainstorm.py`'s `_load`). `state.py` is where `load_config`/`ConfigMissing`
    already live, and `discovery.py` is where the `LANES`/`DEFAULT_LANE` rubric this module's own
    lane budgets key off of already lives -- reimplementing either here would be exactly the
    hardened-sibling-divergence bug class this plugin's own docs already warn against."""
    path = _HERE.parent.parent / "sigma-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


state = _load_loop_script("state")
discovery = _load_loop_script("discovery")
ears_patterns = _load_loop_script("ears_patterns")

#: The `full`-mode default, unchanged from the fixed constant #2032 replaces ("The budget is three
#: sweep rounds", `SKILL.md` §2, pre-#2032) -- an absent `goal_design.rounds` must resolve here so
#: an install that changes no config gets byte-identical behaviour.
DEFAULT_FULL_ROUNDS = 3

#: `mode: lane` budgets -- fixed, and NOT configurable (#2032's own ruling: no evidence lane's
#: ceiling ever needed moving across #1942/#1973/#2017, none of which ran under lane, and the live
#: config runs `mode: full`). Only `small=1` was ever stated in `SKILL.md` before this issue.
LANE_ROUNDS = {"small": 1, "medium": 2, "large": 3}


def sweep_budget(config, mode, lane):
    """Resolve the sweep-round ceiling for one design pass. `config` is a parsed `config.json`
    dict (`state.load_config`'s return, or `{}` where none exists -- see `main()`). Never raises on
    a malformed `goal_design.rounds`: an invalid override falls back to the unconfigured default
    rather than refusing the whole pass over one bad config value -- the same fail-open posture
    the two private-side daemon starters applied to a malformed `interval`, applied here
    to a malformed `rounds`. (Those two were `loop.py`'s own when this was written; S1-G7 moved them
    out of the core, so that reference is PROVENANCE -- the posture is what was copied, not the
    location, and `loop.py` defines neither today.)"""
    goal_design_cfg = config.get("goal_design") or {}
    raw = goal_design_cfg.get("rounds")
    resolved_lane = lane if lane in discovery.LANES else discovery.DEFAULT_LANE

    if mode == "lane":
        ignored = raw is not None
        if ignored:
            print(
                "goal_design.py sweep-budget: goal_design.rounds is ignored under mode: lane "
                "(lane budgets are fixed at small=1/medium=2/large=3) -- set mode: full to raise "
                "the ceiling instead.",
                file=sys.stderr,
            )
        return {"rounds": LANE_ROUNDS[resolved_lane], "mode": "lane", "lane": resolved_lane,
                "rounds_config_ignored": ignored}

    # mode == "full" (or an already-normalised caller): the config key is live here.
    # `not isinstance(raw, bool)` MUST come before `raw > 0` -- see the module docstring's BOOL
    # TRAP. Order matters for a different reason too: `isinstance(True, int)` is `True`, so
    # checking `isinstance(raw, int)` alone (without the bool exclusion) would let `True` through.
    valid = isinstance(raw, int) and not isinstance(raw, bool) and raw > 0
    rounds = raw if valid else DEFAULT_FULL_ROUNDS
    return {"rounds": rounds, "mode": "full", "lane": resolved_lane,
            "rounds_config_ignored": False}


#: Safety bounds for `requirements` / `served` (#659). The read cap is a bounded read (cap+1 bytes
#: then refuse); the sentence cap is checked BEFORE the EARS regex so the regex never sees unbounded
#: input; the FR-count warning is a decomposition hint, not a refusal (the threshold is a judgement,
#: not a measurement). Measured 2026-10-17 on this machine (Python 3.12, best of 5): the largest local
#: artifact (181,293 bytes) checks in 0.6 ms; a synthetic artifact with 100 FRs / 50 slice rows in
#: 0.9 ms and 1000 FRs / 500 rows in 4.6 ms (one pass plus set lookups, linear); the EARS regex on
#: 2000-char adversarial sentences (repeated `WHEN a, `, `THE ` plus `x ` runs without SHALL) takes
#: under 0.1 ms; 100 KB single-line sentences are refused by the length cap in about 1 ms; a file at
#: the 1 MiB cap or one byte over exits in under 100 ms including interpreter start. The real
#: ceiling is reviewer readability, hence the 50-FR warning. No lock, writer, table, network, `gh`
#: call or background process; a pure function of one file, so recovery is "run it again".
READ_CAP = 1024 * 1024
SENTENCE_CAP = 2000
FR_WARN = 50

_FR_BULLET = re.compile(r"^- \*\*FR-([^.\s*]+)\.\*\* (.+)$")
_HASH_REF = re.compile(r"#\d+")
#: A list marker (`- `, `* `, `+ `, `1. `, `1) `) opening an indented line: a nested list item,
#: never wrapped text, so it is refused rather than joined (it would hide an un-checked FR).
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])\s")
_SEP_CELL = re.compile(r"^:?-{1,}:?$")
_SERVED_HEADING = "## Requirements served"
_DEP_FR = re.compile(r"(?i)^fr[\s-]*\d")
#: The skeleton headings `writing-the-artifact.md` §5 requires. A second copy of any of them is a
#: violation: `_sections` reads only the first, so a duplicate would be silently ignored.
REQUIRED_HEADINGS = ("Intent", "Premise check", "Functional requirements", "Queries run", "Seeds",
                     "Blast radius", "Components", "Out of scope", "Doubts", "Blockers",
                     "Detailed design", "Slice-count estimate", "Handoff")
_MISSING_FR = ("no `## Functional requirements` section -- re-run goal-design to add the section; "
               "FR-1 is expected even for one-behaviour targets (`- None.` only when there is "
               "genuinely nothing)")


class Refusal(Exception):
    """The input could not be read safely: reported on stderr, exit 2, nothing on stdout."""


def _norm(s):
    """The ONE whitespace normalisation: `served`, the EARS test and the `--plan` match all use it."""
    return " ".join(s.split())


def _sections(text):
    """{heading: [body lines]} for the FIRST occurrence of each `## ` heading, plus a list of
    duplicate headings. Lines inside ``` fences are never headings and never body."""
    secs, dups, cur, fence = {}, [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        if line.startswith("## "):
            cur = line[3:].strip()
            if cur in secs:
                dups.append(cur)
                cur = None
            else:
                secs[cur] = []
            continue
        if cur is not None:
            secs[cur].append(line)
    return secs, dups


def _split_row(line):
    t = line.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|") and not t.endswith("\\|"):
        t = t[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", t)]


def _parse_frs(body, v):
    """-> (frs, none_form). Appends violations to `v`."""
    frs, none, last, prev, seen = [], False, None, 0, set()
    for raw in body:
        if not raw.strip():
            last = None
            continue
        if raw.startswith("- "):
            last = None
            if raw.strip() == "- None.":
                none = True
                continue
            m = _FR_BULLET.match(raw.rstrip())
            if not m:
                if raw.startswith("- **FR-"):
                    v.append("malformed FR bullet (expected `- **FR-<n>.** <sentence>`): %r" % raw[:80])
                else:
                    v.append("line in Functional requirements is neither an FR bullet nor `- None.`: %r" % raw[:80])
                continue
            tok = m.group(1)
            if not (tok.isdigit() and tok.isascii() and int(tok) >= 1):
                v.append("FR-%s: id must be an integer >= 1 (gaps allowed, never renumbered)" % tok)
                continue
            n = int(tok)
            if n in seen:
                v.append("FR-%d: duplicate id" % n)
            elif n < prev or n == prev:
                v.append("FR-%d: ids must be strictly increasing in document order (after FR-%d)" % (n, prev))
            prev = max(prev, n)
            seen.add(n)
            last = {"id": n, "raw": m.group(2)}
            frs.append(last)
        elif raw[0] in " \t" and _LIST_MARKER.match(raw.strip()):
            last = None
            v.append("nested list item in Functional requirements (an indented bullet is never "
                     "wrapped text and is not EARS-checked or traced; outdent it): %r" % raw.strip()[:80])
        elif raw[0] in " \t" and last is not None:
            last["raw"] += " " + raw.strip()
        else:
            last = None
            v.append("line in Functional requirements is neither an FR bullet nor `- None.`: %r" % raw[:80])
    for f in frs:
        f["sentence"] = _norm(f.pop("raw"))
    if none and frs:
        v.append("`- None.` cannot sit beside FR bullets")
    if not none and not frs:
        v.append("Functional requirements section is empty (write FR bullets or a single `- None.`)")
    return frs, none


def _parse_table(body, v):
    """The first table in the Slice-count estimate body whose header has a Slice column ->
    (header names lowercased, [(slice_label, {name: cell})]) or None."""
    groups, cur = [], []
    for line in body:
        if line.lstrip().startswith("|"):
            cur.append(line)
        else:
            if cur:
                groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    for g in groups:
        head = [c.lower() for c in _split_row(g[0])]
        if "slice" not in head:
            continue
        if len(g) < 2 or not all(_SEP_CELL.match(c) for c in _split_row(g[1])):
            v.append("slice table has no `| --- |` separator row after its header")
            return head, []
        rows = []
        for i, line in enumerate(g[2:], 1):
            cells = _split_row(line)
            label = cells[head.index("slice")] if len(cells) > head.index("slice") and cells[head.index("slice")] else "row %d" % i
            if len(cells) != len(head):
                v.append("slice %s: row has %d cells but the header has %d (a cell was removed or an "
                         "unescaped `|` sits in a cell)" % (label, len(cells), len(head)))
                rows.append((label, None))
                continue
            rows.append((label, dict(zip(head, cells))))
        return head, rows
    return None


def check_requirements(text):
    """Pure check of one design artifact -> {"ok", "frs", "slices", "violations", "warnings"}.
    `slices` maps each slice label to the FR ids its Requirements cell claims."""
    v, w = [], []
    secs, dups = _sections(text)
    frs, none, slices = [], False, {}
    for h in REQUIRED_HEADINGS:
        if h in dups:
            v.append("duplicate `## %s` heading (only the first is read)" % h)
    if "Functional requirements" not in secs:
        v.append(_MISSING_FR)
    else:
        frs, none = _parse_frs(secs["Functional requirements"], v)
    for f in frs:
        if len(f["sentence"]) > SENTENCE_CAP:
            v.append("FR-%d: sentence is %d chars, over the %d cap (refused before the EARS test)"
                     % (f["id"], len(f["sentence"]), SENTENCE_CAP))
        elif _HASH_REF.search(f["sentence"]):
            v.append("FR-%d: sentence cites an issue number (`#<n>`); an FR carries behaviour, never a "
                     "reference (it would be pasted into child bodies and can mint a blocker edge)" % f["id"])
        elif not ears_patterns.is_ears(f["sentence"]):
            v.append("FR-%d: not an EARS sentence (`[WHEN|IF|WHILE|WHERE ..., ]THE <x> SHALL [NOT] <y>`): %r"
                     % (f["id"], f["sentence"][:80]))
    declared = {"FR-%d" % f["id"] for f in frs}
    table = None
    if "Slice-count estimate" in secs:
        table = _parse_table(secs["Slice-count estimate"], v)
    elif frs or none:
        v.append("no `## Slice-count estimate` section to trace requirements into")
    if table is None:
        if frs:
            v.append("no slice table (a table with a Slice column) under `## Slice-count estimate`")
    else:
        head, rows = table
        has_col = "requirements" in head
        if head.count("requirements") > 1:
            v.append("duplicate `Requirements` column in the slice table")
        elif not has_col and frs:
            v.append("Requirements column missing from the slice table while FR bullets are present "
                     "(missing is never read as 'no coverage claimed')")
        covered, seen_labels = set(), set()
        for label, cells in rows:
            if label in seen_labels:
                v.append("duplicate slice label %r in the slice table (a later row would overwrite "
                         "the earlier row's claims)" % label)
                continue
            seen_labels.add(label)
            if cells is None:
                continue
            slices[label] = []
            dep = cells.get("depends on", "")
            for tok in [t.strip() for t in dep.split(",")]:
                if _DEP_FR.match(tok):
                    v.append("slice %s: `Depends on` cell names %r; it carries slice ids only, "
                             "FR ids belong in the Requirements column" % (label, tok))
            if not has_col:
                continue
            cell = cells["requirements"]
            if cell == "":
                v.append("slice %s: empty Requirements cell (write `—` for none)" % label)
                continue
            if cell == "—":
                continue
            cell_seen = set()
            for tok in [t.strip() for t in cell.split(",")]:
                if tok in cell_seen:
                    v.append("slice %s: Requirements cell names %r more than once (`served` would "
                             "print its bullet twice)" % (label, tok))
                    continue
                cell_seen.add(tok)
                if tok in declared:
                    covered.add(tok)
                    slices[label].append(int(tok[3:]))
                elif none:
                    v.append("slice %s: Requirements cell names %r but the section is `- None.`" % (label, tok))
                else:
                    v.append("slice %s: Requirements token %r is not a declared FR" % (label, tok))
        if has_col or frs:
            for f in frs:
                if "FR-%d" % f["id"] not in covered:
                    v.append("FR-%d: appears in no slice row's Requirements cell" % f["id"])
    if len(frs) > FR_WARN:
        w.append("%d functional requirements (> %d): consider decomposing the target" % (len(frs), FR_WARN))
    return {"ok": not v, "frs": frs, "slices": slices, "violations": v, "warnings": w}


def _read_artifact(path):
    try:
        with open(path, "rb") as fh:
            data = fh.read(READ_CAP + 1)
    except OSError as e:
        raise Refusal("cannot read %s: %s" % (path, e.strerror or e))
    if len(data) > READ_CAP:
        raise Refusal("%s is over the %d-byte read cap; refusing" % (path, READ_CAP))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Refusal("%s is not valid UTF-8 (%s)" % (path, e.reason))


def _served_bullets(body):
    """{FR id: [normalised sentence, ...]} for the `- **FR-n.** <sentence>` bullets of a child
    body's `## Requirements served` block (from that heading up to the next `## ` or `Design:`
    line, or the end). Indented continuation lines join their bullet, and every sentence goes
    through the same `_norm` that `served` and `check_requirements` use, so `served`'s own output
    matches by construction. Empty when the heading is absent."""
    out, on, last = {}, False, None
    for line in body.splitlines():
        if not on:
            on = line.strip() == _SERVED_HEADING
            continue
        if line.startswith("## ") or line.startswith("Design:"):
            break
        m = _FR_BULLET.match(line.rstrip())
        if m and m.group(1).isdigit():
            last = [m.group(2)]
            out.setdefault(int(m.group(1)), []).append(last)
        elif line[:1] in (" ", "\t") and line.strip() and last is not None:
            last.append(line.strip())
        else:
            last = None
    return {k: [_norm(" ".join(parts)) for parts in vs] for k, vs in out.items()}


def check_plan(result, plan):
    """Violations for a compiled-children plan against a clean `check_requirements` result: every
    child (key == str(slice number)) carries `## Requirements served` and every claimed sentence."""
    v = []
    issues = plan.get("issues") if isinstance(plan, dict) else None
    if not isinstance(issues, list):
        return ["plan has no issues[] list"]
    by_key = {}
    for n, it in enumerate(issues):
        if not isinstance(it, dict):
            v.append("plan issues[%d] is not an object" % n)
            continue
        k = str(it.get("key"))
        if k in by_key:
            v.append("plan has more than one child with key %r (duplicate key)" % k)
            continue
        by_key[k] = it
    for key in by_key:
        if key not in result["slices"]:
            v.append("plan child %r is not a slice number in the Slice-count estimate" % key)
    sentence = {f["id"]: f["sentence"] for f in result["frs"]}
    for label, ids in result["slices"].items():
        child = by_key.get(label)
        if child is None:
            if ids:
                v.append("slice %s claims %s but the plan has no child with key %r"
                         % (label, ", ".join("FR-%d" % i for i in ids), label))
            continue
        body = child.get("body")
        if body is None:
            body = ""
        if not isinstance(body, str):
            v.append("slice %s: child body is not a string (got %s)" % (label, type(body).__name__))
            continue
        if not re.search(r"(?m)^%s\s*$" % re.escape(_SERVED_HEADING), body):
            v.append("slice %s: child body lacks the `%s` heading" % (label, _SERVED_HEADING))
        got = _served_bullets(body)
        for i in ids:
            # exact, per bullet, tied to its own id: FR-1's sentence under an FR-2 label, or a
            # longer sentence that merely contains FR-1's, does not satisfy FR-1
            if sentence[i] not in got.get(i, ()):
                v.append("slice %s: child body's `%s` block has no `- **FR-%d.**` bullet carrying "
                         "exactly the FR-%d sentence its slice row claims" % (label, _SERVED_HEADING, i, i))
    return v


def served_block(result, label):
    ids = result["slices"].get(label)
    if ids is None:
        raise Refusal("slice %r is not in the Slice-count estimate (slices: %s)"
                      % (label, ", ".join(result["slices"]) or "none"))
    sent = {f["id"]: f["sentence"] for f in result["frs"]}
    lines = ["- **FR-%d.** %s" % (i, sent[i]) for i in ids] or ["- None."]
    return _SERVED_HEADING + "\n" + "\n".join(lines) + "\n"


_USAGE = ("usage: goal_design.py sweep-budget <sdlc_dir> --mode full|lane --lane small|medium|large\n"
          "       goal_design.py requirements <artifact.md> [--plan <plan.json>]\n"
          "       goal_design.py served <artifact.md> --slice <n>")


def _value(tail, name):
    if name in tail:
        i = tail.index(name)
        return tail[i + 1] if i + 1 < len(tail) else None
    return None


def _requirements_main(path, tail):
    plan_path = _value(tail, "--plan")
    if "--plan" in tail and plan_path is None:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        result = check_requirements(_read_artifact(path))
        if plan_path is not None and result["ok"]:
            try:
                with open(plan_path, "rb") as fh:
                    raw = fh.read(READ_CAP + 1)
                if len(raw) > READ_CAP:
                    raise Refusal("%s is over the %d-byte read cap; refusing" % (plan_path, READ_CAP))
                plan = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError, RecursionError) as e:
                # RecursionError: deeply nested JSON (e.g. 100k `[`) exhausts the decoder's stack;
                # any failure to load the plan is a refusal, never a traceback.
                raise Refusal("cannot read plan %s: %s" % (plan_path, e))
            result["violations"] += check_plan(result, plan)
            result["ok"] = not result["violations"]
    except Refusal as e:
        print("goal_design.py requirements: %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    for msg in result["warnings"]:
        print("goal_design.py requirements: warning: %s" % msg, file=sys.stderr)
    for msg in result["violations"]:
        print("goal_design.py requirements: VIOLATION: %s" % msg, file=sys.stderr)
    return 0 if result["ok"] else 2


def _served_main(path, tail):
    label = _value(tail, "--slice")
    if label is None:
        print(_USAGE, file=sys.stderr)
        return 2
    try:
        result = check_requirements(_read_artifact(path))
        if not result["ok"]:
            raise Refusal("the artifact has requirement violations; run `requirements` first:\n  "
                          + "\n  ".join(result["violations"]))
        block = served_block(result, label)
    except Refusal as e:
        print("goal_design.py served: %s" % e, file=sys.stderr)
        return 2
    sys.stdout.write(block)
    return 0


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    if len(argv) >= 3 and argv[1] == "sweep-budget":
        sdlc_dir, tail = argv[2], argv[3:]
        mode = _value(tail, "--mode") or "full"
        lane = _value(tail, "--lane") or discovery.DEFAULT_LANE
        if mode not in ("full", "lane"):
            print("goal_design.py sweep-budget: --mode must be full or lane (got %r)" % mode,
                  file=sys.stderr)
            return 2
        try:
            config = state.load_config(sdlc_dir)
        except state.ConfigMissing:
            # No .sdlc/config.json at all -- there is no configured `goal_design.rounds` to read,
            # the same reading `SKILL.md` §2 already gives this exception for `mode` resolution.
            config = {}
        print(json.dumps(sweep_budget(config, mode, lane), indent=2))
        return 0
    if len(argv) >= 3 and argv[1] == "requirements":
        return _requirements_main(argv[2], argv[3:])
    if len(argv) >= 3 and argv[1] == "served":
        return _served_main(argv[2], argv[3:])
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    sys.exit(_load_loop_script("timing_store").timed_main(main, sys.argv, "goal_design"))
