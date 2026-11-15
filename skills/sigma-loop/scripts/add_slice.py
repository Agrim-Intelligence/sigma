#!/usr/bin/env python3
"""`work.py add-slice` (#2224): a follow-up that belongs to this goal joins its plan as a slice.

    work.py add-slice <sdlc_dir> <goal> --title T --criteria-file F [--test SELECTOR ...]

Order matters for crash recovery. The per-goal lock is taken first and HELD ACROSS the cap check, the plan edit, the
acceptance extension and the count, so two sessions cannot both pass the check. The count is bumped LAST, so a crash
before it re-runs cleanly: a slice's identity is goal + normalised title, never its criteria text, so a retry with
edited criteria cannot count a second slice.

Exit codes: 0 done or already counted; 2 usage, unsafe goal, no plan; 3 past the cap (it is a separate goal now);
4 `acceptance.extend` absent or of unknown signature (#2222), nothing written; 5 not supported here (no fcntl) or the
`## Tests` selector ceiling would be exceeded.

The plan edit alone does not make verify evidence stale and does not by itself force a re-review: the plan-review hash
gate (`gates.plan_review`, OFF by default) refuses `work.py pr` until a fresh review of the new bytes is recorded, and
`verify.enforce` (also opt-in) refuses stale verify evidence once the slice's own code changes. Both are conditional.

Test-only seams (module-level no-op callables the tests replace in-process; never a router input, no flag, no
environment variable, no config key): `_after_check`, `_after_plan_write`, and `_acceptance` (so tests fake `extend`
present or absent without touching the real acceptance.py).
"""
import importlib.util
import os
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent
MAX_SELECTORS = 25                      # the plan parser's ceiling (red_green.MAX_SELECTORS)


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _after_check():
    """Test-only seam, a no-op: called inside the critical section right after the cap check passes."""


def _after_plan_write():
    """Test-only seam, a no-op: called right after the plan copies are written and before the count."""


def _acceptance():
    return _load("acceptance")


def _parse(argv):
    if len(argv) < 2 or argv[0].startswith("--") or argv[1].startswith("--"):
        return None
    opts = {"title": None, "criteria": None, "tests": []}
    rest, i = argv[2:], 0
    while i < len(rest):
        flag = rest[i]
        if flag not in ("--title", "--criteria-file", "--test") or i + 1 >= len(rest):
            return None
        value = rest[i + 1]
        if flag == "--title":
            opts["title"] = value
        elif flag == "--criteria-file":
            opts["criteria"] = value
        else:
            opts["tests"].append(value)
        i += 2
    if not opts["title"] or not opts["criteria"]:
        return None
    return argv[0], argv[1], opts


USAGE = ("usage: work.py add-slice <sdlc_dir> <goal> --title T --criteria-file F [--test SELECTOR ...]")


def _tests_span(lines):
    """(start, end) of the `## Tests` section's lines, or None."""
    for n, line in enumerate(lines):
        if line.strip() == "## Tests":
            end = len(lines)
            for m in range(n + 1, len(lines)):
                if lines[m].startswith("## "):
                    end = m
                    break
            return n, end
    return None


def _selectors(lines, span):
    found = []
    for line in lines[span[0] + 1:span[1]]:
        match = re.fullmatch(r"\s*- `([^`]+)`\s*", line)
        if match:
            found.append(match[1])
    return found


def _covered(selector, existing):
    return selector in existing or selector.split("::")[0] in existing


def transform(text, number, title, criteria, tests):
    """-> (new_text, error). The one transform both plan copies get, so their bytes stay identical."""
    lines = text.split("\n")
    new_tests = []
    span = _tests_span(lines)
    if tests:
        if span is None:
            return None, "the plan has no `## Tests` section to add the selectors to"
        existing = _selectors(lines, span)
        new_tests = [t for t in dict.fromkeys(tests) if not _covered(t, existing + new_tests)]
        if len(existing) + len(new_tests) > MAX_SELECTORS:
            return None, ("adding %d selectors would exceed the %d-selector `## Tests` ceiling: cover the slice with "
                          "a FILE selector (`tests/test_x.py`) instead" % (len(new_tests), MAX_SELECTORS))
        at = span[1]
        while at > span[0] + 1 and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = ["- `%s`" % t for t in new_tests]
    body = "\n".join(lines).rstrip("\n")
    return "%s\n\n## Added slice %d: %s\n\n%s\n" % (body, number, title, criteria.strip("\n")), None


def _criteria_lines(criteria):
    """The one-line acceptance statements `acceptance.extend` takes, from the slice's criteria file: the bullets under
    its `Done when` heading, else every bullet. A bullet marker (`- `, `* `, `- [ ] `) is stripped; prose is never one."""
    lines, in_done, found = [], False, False
    for raw in criteria.splitlines():
        head = re.match(r"^#{1,6}\s+(.*?)\s*$", raw)
        if head:
            in_done = bool(re.match(r"(?i)done when\b", head.group(1)))
            found = found or in_done
            continue
        bullet = re.match(r"^\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*$", raw)
        if bullet:
            lines.append((in_done, bullet.group(1)))
    chosen = [t for done, t in lines if done] if found else [t for _, t in lines]
    return chosen


def _heading_titles(text):
    return _load("followup_route").SLICE_HEADING_RE.findall(text)


def main(argv):
    if argv and argv[0] in ("--help", "-h"):
        print(USAGE)
        return 0
    parsed = _parse(argv)
    if not parsed:
        print(USAGE, file=sys.stderr)
        return 2
    sdlc_dir, goal, opts = parsed
    fr, work, state = _load("followup_route"), _load("work"), _load("state")
    title = " ".join(opts["title"].split())
    try:
        criteria = pathlib.Path(opts["criteria"]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("work.py add-slice: could not read --criteria-file: %s" % exc, file=sys.stderr)
        return 2
    if not title or re.search(r"(?m)^## (Added slice|Tests)\b", criteria):
        print("work.py add-slice: refused: an empty title, or criteria that carry their own `## Added slice` / "
              "`## Tests` heading (they would change the plan's structure and the count)", file=sys.stderr)
        return 2
    try:
        fr.record_path(sdlc_dir, goal)               # the unsafe-goal check, before anything is written
        config = state.load_config(sdlc_dir)
    except (ValueError, OSError) as exc:
        print("work.py add-slice: %s" % exc, file=sys.stderr)
        return 2
    cap = _load("handoff").max_added_slices(config)
    sid = fr.slice_id(goal, title)
    try:
        with fr.locked(sdlc_dir, goal):
            return _critical(sdlc_dir, goal, config, title, criteria, opts, cap, sid, fr, work)
    except fr.LockUnavailable as exc:
        print("work.py add-slice: %s" % exc, file=sys.stderr)
        return 5
    except fr.RecordError as exc:
        print("work.py add-slice: %s" % exc, file=sys.stderr)
        return 2


def _critical(sdlc_dir, goal, config, title, criteria, opts, cap, sid, fr, work):
    rec = fr.read_record(sdlc_dir, goal)
    if any(item.get("slice_id") == sid for item in rec["added"]):
        print("add-slice: %r is already counted as an added slice of %s; nothing changed (a retry never replaces the "
              "criteria already recorded)." % (title, goal))
        return 0
    wrec = work._record(sdlc_dir, goal)
    main_copy, branch = work.plan_copies(sdlc_dir, goal, wrec, work._run)
    targets = [p for p in (main_copy, branch) if p]
    if not targets:
        print("work.py add-slice: no plan for %s in %s/plans/: nothing to add a slice to" % (goal, sdlc_dir),
              file=sys.stderr)
        return 2
    plan_text = pathlib.Path(targets[0]).read_text(encoding="utf-8")
    resume = any(fr.slice_id(goal, t) == sid for t in _heading_titles(plan_text))
    if not resume:
        added = fr.added_count(sdlc_dir, goal)
        verdict, reason = fr.route(None, None, added, cap)      # the verb adds to THIS goal: its own feature
        if verdict != "slice":
            print("work.py add-slice: refused (%s): %s already holds %d added slices (cap %d). File it with "
                  "`handoff.py track`; it will be filed as a separate goal with reason %s." % (reason, goal, added,
                                                                                                cap, reason),
                  file=sys.stderr)
            return 3
    _after_check()
    acceptance = _acceptance()
    criteria_file = acceptance.path(sdlc_dir, goal)
    has_record = pathlib.Path(criteria_file).exists()
    if has_record and not hasattr(acceptance, "extend"):
        print("work.py add-slice: refused: %s has an acceptance record but `acceptance.py extend` (goal #2222) is "
              "absent, so its criteria cannot be extended. Nothing was written. Update the plugin once #2222 ships; "
              "until then ask the operator how to proceed." %
              goal, file=sys.stderr)
        return 4
    statements = _criteria_lines(criteria) if has_record else []
    if has_record and not statements:
        print("work.py add-slice: refused: %s has an acceptance record but --criteria-file holds no bullet to add as a "
              "criterion (put them under `### Done when`). Nothing was written." % goal, file=sys.stderr)
        return 2
    number = len(_heading_titles(plan_text)) + 1
    if not resume:
        edits = []
        for path in targets:
            original = pathlib.Path(path).read_text(encoding="utf-8")
            new, error = transform(original, number, title, criteria, opts["tests"])
            if error:
                print("work.py add-slice: refused: " + error, file=sys.stderr)
                return 5
            edits.append((path, new))
        state = _load("state")
        before = _load("review_context").plan_sha256(targets[0])
        for path, new in edits:
            state.atomic_write_text(path, new)
        _after_plan_write()
    else:
        before = _load("review_context").plan_sha256(targets[0])
        number = [fr.slice_id(goal, t) for t in _heading_titles(plan_text)].index(sid) + 1
    if wrec and not branch:
        name = pathlib.Path(sdlc_dir).resolve().name
        if _load("review_context").phase_doc_file(pathlib.Path(wrec["worktree"]) / name, goal, "plans"):
            print("add-slice: the branch copy of the plan is modified or untracked, so only the main copy was "
                  "edited; `work.py commit` carries the main copy's bytes over it.")
    if has_record:
        try:
            acceptance.extend(sdlc_dir, goal, "slice %d" % number, title, statements)
        except ValueError as exc:
            print("work.py add-slice: refused: `acceptance.extend` declined (%s). The plan was %s; fix the cause and "
                  "re-run (the same command resumes)." % (exc, "left as written" if not resume else "already written"),
                  file=sys.stderr)
            return 4
        except TypeError as exc:
            print("work.py add-slice: refused: `acceptance.extend` has a signature this verb does not know (%s): not "
                  "guessing. The plan was %s; re-run once the verb is updated (the same command resumes)." %
                  (exc, "left as written" if not resume else "already written"), file=sys.stderr)
            return 4
    else:
        print("add-slice: no acceptance record for %s: criteria not extended." % goal)
    fr.append_slice(sdlc_dir, goal, sid, before, held=True)       # the count is bumped LAST
    if pathlib.Path(sdlc_dir, "plans", work.stem(goal) + ".slices.json").exists():
        print("add-slice: notice: %s.slices.json exists and was NOT updated (the plan-review hash excludes it)." %
              work.stem(goal))
    gate = ("gates.plan_review is ON: `work.py pr` refuses until a fresh review is recorded" if
            work._plan_review_on(config) else
            "gates.plan_review is OFF (its default): nothing forces the re-review unless you turn it on")
    print("add-slice: added slice %d (%r) to %s. Next: record a fresh plan-review of the new plan bytes "
          "(`work.py record-plan-review %s %s --verdict ... --plan-sha256 <sha>`; %s), re-run implement, review and "
          "retro in order, then run verify again (a stale verify is refused only while verify.enforce is on)." %
          (number, title, goal, sdlc_dir, goal, gate))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
