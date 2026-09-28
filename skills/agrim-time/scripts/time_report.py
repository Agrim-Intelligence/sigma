# SPDX-License-Identifier: MIT
"""Read side of the always-on timing store: how long work actually took, for one goal or one unit.

Renders `timing_store.totals`, and falls back to pairing the ledger's own `phase` start/end
timestamps for goals that finished before the store existed. Writes nothing.

Loads `skills/agrim-loop/scripts/*.py` by file path — the established idiom for reaching a sibling
skill's scripts (`backlog_check.py` does the same) — rather than duplicating the store's own
reader, de-duplication and rollup rules where a second copy could drift from them.
"""
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "agrim-loop" / "scripts"


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _LOOP_SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


timing_store = _load("timing_store")


# --------------------------------------------------------------------------- formatting


def format_ms(ms):
    """`2h40m` / `8m11s` / `41s`, from integer milliseconds. None for anything unmeasurable —
    never a fabricated `0s`.

    Its own function rather than `phase_report.format_elapsed`: that module IMPORTS the timing
    store, so reaching back into it from this side of the same feature invites a cycle. The shapes
    are deliberately identical so the two surfaces read alike."""
    try:
        raw = int(ms)
    except (TypeError, ValueError):
        return None
    # Sign checked BEFORE the divide, not after: a small negative (a clock that moved backwards by
    # a few milliseconds) rounds to 0 seconds and would print a fabricated `0s`. Measured here —
    # `format_ms(-5)` returned `0s` until this order was fixed.
    if raw < 0:
        return None
    total = int(round(raw / 1000.0))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


# --------------------------------------------------------------------------- history fallback


def _local_events(directory):
    """The local events destination, read the way doctor.py already reads it — `ledger.read_all`
    covers only the SHARED directory and has no seam for this one."""
    out = []
    try:
        files = sorted(p for p in directory.glob("*.jsonl") if p.is_file())
    except OSError:
        return out
    for path in files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):        # both, as `doctor._read_events_from` does
                continue
            if isinstance(entry, dict) and entry.get("kind"):
                out.append(entry)
    return out


def all_events(sdlc_dir):
    """Every `phase` event, from BOTH event destinations, unioned.

    THE UNION IS NOT OPTIONAL, and it replaces a routing rule that is now false (#2574/S1-G3).
    The journal writes to `.sdlc/events/`; `.sdlc/ledger/events/` holds one release of legacy
    history that is never moved. A reader that picked one destination from config would report a
    silent zero for whichever half it did not pick — and a goal whose work straddles the move has
    its `start` in one directory and its `end` in the other, so only a union can pair them at all.

    The two paths are DISJOINT directories, so the union cannot double-count. No config is
    consulted: reading is not gated on the switch, because what was written before the switch was
    turned off is still there and still true."""
    ledger = _load("ledger")
    try:
        events = ledger.read_all(sdlc_dir, stream=ledger.EVENTS)
    except Exception:                                       # noqa: BLE001 - a reader never raises
        events = []
    try:
        events = events + _local_events(ledger.local_events_dir(sdlc_dir))
    except Exception:                                       # noqa: BLE001
        pass
    return events


def derived_totals(sdlc_dir, goal):
    """Working time reconstructed from paired ledger timestamps, for goals that predate the store.

    DERIVED, NEVER MEASURED, and the caller must say so: ledger timestamps are whole-second, so
    every figure here is coarser than a stored one, and the pairing can only see phases the journal
    was recording at the time.

    Pairs each `end` with the LATEST preceding `start` for its phase — the same later-start-wins
    rule a downstream duration metric settled on, for the same reason: a repeated start is the routine
    signature of park-and-resume, and measuring from the earlier one would fold parked wall-clock
    into active time. Undercounting toward absence is the safer direction."""
    ledger = _load("ledger")
    stem = str(goal)
    try:
        stem = _load("work").stem(goal)
    except Exception:                                       # noqa: BLE001 - fall back to the raw ref
        pass
    mine = [e for e in all_events(sdlc_dir) if str(e.get("goal")) in (str(goal), stem)]
    rows = [e for e in mine if e.get("kind") == "phase"]
    rows.sort(key=lambda e: (str(e.get("ts") or ""), str(e.get("state") or "")))

    active_ms, phases, open_start = 0, 0, {}
    for row in rows:
        phase, state_ = row.get("phase"), row.get("state")
        ts = ledger._epoch(row.get("ts"))
        if ts is None or ts < 0:
            # `ledger._epoch` returns -1 for a missing or malformed timestamp, never None: a start
            # at epoch -1 followed by a real end added ~1.79 billion seconds (code review C2).
            continue
        if state_ == "start":
            open_start[phase] = ts                          # a later start REPLACES an open one
        elif state_ == "end" and phase in open_start:
            span = ts - open_start.pop(phase)
            if span >= 0:
                active_ms += int(span * 1000)
                phases += 1

    # `verify` events have carried a real, self-measured `ms` since long before this feature — the
    # subprocess is timed inside one call, so unlike a phase there is nothing to pair. Reporting a
    # flat zero here (as this function first did) would have stated a measurement nobody took.
    background_ms, runs = 0, 0
    for row in mine:
        if row.get("kind") != "verify":
            continue
        try:
            ms = int(row.get("ms"))
        except (TypeError, ValueError):
            continue                                        # unmeasured, so not counted as zero
        if ms > 0:
            background_ms += ms
            runs += 1

    if not phases and not runs:
        return {"recorded": False, "derived": True, "active_ms": None, "background_ms": None,
                "effort_ms": None, "phases": 0, "background_runs": 0}
    if not phases:
        # Background is measured, `active` is UNKNOWN — a crash can leave a phase started and never
        # ended while its verify runs still recorded. A 0 would claim those phases took no time,
        # and an unknown cannot be added, so `effort` is unknown too.
        return {"recorded": True, "derived": True, "active_ms": None,
                "background_ms": background_ms, "effort_ms": None, "phases": 0,
                "background_runs": runs}
    return {"recorded": True, "derived": True, "active_ms": active_ms,
            "background_ms": background_ms, "effort_ms": active_ms + background_ms,
            "phases": phases, "background_runs": runs}


def goal_totals(sdlc_dir, goal):
    """The store's own figures, else the derived fallback. Measured always wins."""
    stored = timing_store.totals(sdlc_dir, goal)
    if stored.get("recorded"):
        return {**stored, "derived": False}
    return derived_totals(sdlc_dir, goal)


_TERMINAL = ("done", "parked", "failed")


def elapsed_ms(sdlc_dir, goal):
    """Calendar time from the goal's `claimed` entry to its first terminal entry — idle included,
    context only, and available only where the coordination ledger recorded both. None otherwise;
    never required for the other figures. (Documented in the spec from the start; built only
    after code review noticed it never had been.)"""
    ledger = _load("ledger")
    stem = str(goal)
    try:
        stem = _load("work").stem(goal)
    except Exception:                                       # noqa: BLE001 - fall back to the raw ref
        pass
    try:
        rows = ledger.read_all(sdlc_dir, stream=ledger.ENTRIES)
    except Exception:                                       # noqa: BLE001 - a reader never raises
        return None
    mine = [r for r in rows if str(r.get("goal")) in (str(goal), stem)]
    claimed = [ledger._epoch(r.get("ts")) for r in mine if r.get("kind") == "claimed"]
    claimed = [t for t in claimed if t is not None and t >= 0]
    if not claimed:
        return None
    start = min(claimed)
    ends = [ledger._epoch(r.get("ts")) for r in mine if r.get("kind") in _TERMINAL]
    ends = [t for t in ends if t is not None and t >= start]
    if not ends:
        return None
    return int((min(ends) - start) * 1000)


# --------------------------------------------------------------------------- rendering


def render_goal(sdlc_dir, goal):
    t = goal_totals(sdlc_dir, goal)
    head = f"goal {goal}"
    if not t.get("recorded"):
        # NOT `0m`. "Nothing was recorded" and "the work took no time" are different statements and
        # only one of them is true here.
        return (f"{head}\n"
                "  not recorded — no working time has been captured for this goal\n"
                "  (journal-independent recording starts at this goal's next phase boundary)")
    note = " · derived from paired timestamps, whole-second precision" if t.get("derived") else ""
    lines = [f"{head}{note}"]
    if t.get("active_ms") is None:
        # Measured background, unknown active — a phase started and never ended. Named as not
        # measurable rather than rendered as a zero that would claim those phases were instant.
        lines.append("  active      not measurable — no paired phase boundaries were recorded")
    else:
        lines.append(f"  active      {format_ms(t['active_ms'])}   "
                     f"({t['phases']} phases, lower bound)")
    if t.get("background_runs"):
        lines.append(f"  background  {format_ms(t['background_ms'])}   "
                     f"({t['background_runs']} background runs)")
    if t.get("effort_ms") is None:
        lines.append("  effort      not measurable — an unknown active time cannot be added to")
    else:
        lines.append(f"  effort      {format_ms(t['effort_ms'])}   "
                     "(active + background; not wall-clock)")
    wall = elapsed_ms(sdlc_dir, goal)
    if wall is not None and format_ms(wall):
        lines.append(f"  elapsed     {format_ms(wall)}   (idle included)")
    return "\n".join(lines)


def _printable(name):
    """A skill or script name as it may be shown: control characters stripped, so a hostile
    store line cannot inject terminal escapes into the report (security review S4)."""
    return "".join(ch for ch in str(name) if ch.isprintable()) or "(unnamed)"


def render_unit(sdlc_dir, unit, repo=None):
    if not repo:
        # Code review C1, BLOCKING: run as the docs gave it — without `<repo>` — the rollup filed
        # this repository's own goals as "recorded in that checkout". It is single-repo by
        # construction and cannot know which repository it is running in; it refuses.
        return (f"unit {unit}\n  repository not given — pass <owner/name>, this repository's key "
                "in the feature registry; the rollup is single-repo and cannot infer it")
    u = timing_store.unit_totals(sdlc_dir, unit, repo=repo)
    if not u.get("found"):
        return f"unit {unit}\n  unknown — no such unit in this repository's feature registry"
    lines = [f"unit {unit} · {u['counted_goals']} goals in this repo"]
    if not u.get("recorded"):
        lines.append("  not recorded — none of this unit's goals in this repo have captured time")
    else:
        lines.append(f"  active      {format_ms(u['active_ms'])}   (lower bound)")
        lines.append(f"  background  {format_ms(u['background_ms'])}")
        lines.append(f"  effort      {format_ms(u['effort_ms'])}   (active + background; not wall-clock)")
    for name, count in u.get("elsewhere") or []:
        # Named, never silently dropped: their durations are local to that checkout, so a total
        # that quietly omitted them would be a confidently-wrong smaller number.
        lines.append(f"  ({count} goals in {name} not counted — recorded in that checkout)")
    return "\n".join(lines)


_UNATTRIBUTED = "unattributed-"


def render_session(sdlc_dir, session=None):
    """Process time for one session — the current one by default."""
    session = session or timing_store.session_id()
    t = timing_store.session_totals(sdlc_dir, session)
    if session.startswith(_UNATTRIBUTED):
        # Not a session: two concurrent hookless sessions merge into this dated bucket, and the
        # header must say so rather than present it as one.
        head = (f"all hookless activity on {session[len(_UNATTRIBUTED):]} "
                f"(possibly several sessions)")
    else:
        head = f"session {session}"
    if not t["recorded"]:
        return f"{head}\n  not recorded — no process time has been captured for this session"
    lines = []
    if t["precise"]:
        lines.append(f"{head}  ·  precise (turn hooks)")
        lines.append(f"  total       {format_ms(t['turn_ms'])}   "
                     f"({t['segments']} segments, idle excluded)")
        lines.append("  by process")
        for name, ms in t["by_skill"].items():
            if name == "(none)":
                lines.append(f"    {'(no skill)':<18}{format_ms(ms):>8}   no skill invoked yet")
            else:
                lines.append(f"    {_printable(name):<18}{format_ms(ms):>8}")
        if t["script_runs"]:
            lines.append(f"  scripts     {format_ms(t['script_ms'])}   ({t['script_runs']} runs — "
                         "already inside the turns above, not additional)")
    else:
        # Scripts only: the floor. It cannot see a skill that invokes no scripts, and it excludes
        # interpreter start-up, so it is not a total and is never printed as one.
        lines.append(f"{head}  ·  floor only (this host has no turn hooks)")
        lines.append(f"  scripts     {format_ms(t['script_ms'])}   ({t['script_runs']} runs)")
        lines.append("  total       not measurable — turn time is not observable on this host")
    return "\n".join(lines)


USAGE = ("usage: time_report.py goal <sdlc_dir> <goal> | unit <sdlc_dir> <unit> <owner/name> | "
         "session <sdlc_dir> [<session-id>]")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 4 and argv[1] == "goal":
        print(render_goal(argv[2], argv[3]))
        return 0
    if len(argv) >= 5 and argv[1] == "unit":
        print(render_unit(argv[2], argv[3], argv[4]))
        return 0
    if len(argv) >= 3 and argv[1] == "session":
        print(render_session(argv[2], argv[3] if len(argv) >= 4 else None))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
