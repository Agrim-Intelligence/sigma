# SPDX-License-Identifier: MIT
"""Always-on local record of how long work actually took — one line per COMPLETED interval.

Deliberately NOT an event stream, and deliberately NOT gated. Every other store in the kit is
opt-in (`journal.enabled`, `ledger.enabled`, `action_log.enabled`), so a timing feature built on
any of them measures nothing on a stock install. This module reads no config at all: it needs
`work.stem` for goal identity (loaded lazily, on a goal path only) and a local, byte-identical
copy of `state.unsafe_goal_reason` for the path refusal, and nothing else. Adding a config read
here would defeat the only reason it exists.

It records durations and nothing else. Kinds, prose, and state transitions belong to the ledger;
adding them here turns this into the second event stream it was carved out to avoid.
"""
import json
import os
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# NOTHING is loaded at import. `work` is loaded LAZILY, on the first goal path, because `work.py`
# imports `subprocess` and the session path never needs `work.stem()`. `state` is not loaded at
# all: the process-time hook loads this module on every turn boundary, and importing `state.py`
# (`hashlib`, `subprocess`, `tempfile`, …) measured ~0.2 s of the ~0.3 s each event cost, for one
# three-line function — which lives here instead as `_unsafe_stem_reason`, see below.
_work = None


def _unsafe_stem_reason(stem):
    """Local copy of `state.unsafe_goal_reason` (skills/sigma-loop/scripts/state.py) — kept
    byte-identical in body on purpose, and PROVEN so: `tests/test_timing_store.py` compares the two
    bodies statement for statement, which the repository's other sanctioned copy
    (`skills/sigma-log/scripts/log.py`) relies on discipline alone for.

    Why a copy at all, when `state.py`'s own docstring says "a single implementation, not one per
    caller": the same reason `sigma-log` was granted one. This module is loaded by the process-time
    hook on every user prompt, every skill switch and every `Stop`, and importing `state.py`
    costs ~0.2 s per event for the sake of these three lines. The owner chose the copy over the
    per-turn cost, on 2026-09-10, with that number in hand.

    The rule itself, unchanged: a `stem` about to become a single path component must carry no
    separator and no `..` — pathlib's `/` re-parses a string for separators, so one inside `stem`
    becomes extra segments, one of which can be `..`. `:` is refused for the Windows
    drive-letter form of the same escape. Never raises."""
    text = str(stem)
    if any(c in text for c in ("/", "\\", ":")) or ".." in text:
        return "must not contain '/', '\\', ':', or '..' once reduced to a path component"
    return None


def _work_module():
    global _work
    if _work is None:
        _work = _load("work")
    return _work


# --------------------------------------------------------------------------- paths


def store_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "time"


def _is_link(path):
    """A symlink — or, on Windows, any reparse point: a junction reports as a plain directory to
    `is_symlink()` on Python 3.11, and a junction is exactly what a hostile checkout can plant."""
    try:
        if path.is_symlink():
            return True
        if os.name == "nt":
            return getattr(os.lstat(path), "st_reparse_tag", 0) != 0
    except OSError:
        return False
    return False


def _refuse_links(path, sdlc_dir):
    """Raise unless `path` is a plain path inside the store: no component from it up to the
    store root may be a link, and its real path must resolve inside the root's real path.

    Security review 2026-09-11, HIGH: every writer here opens with `"a"` and the sweep unlinks
    through `is_file()`, both of which FOLLOW links. A link planted inside `.sdlc/state/time/` —
    committable on macOS/Linux, a junction on Windows — turned typing a prompt into deleting
    `*.jsonl` and overwriting one file outside the store, exit 0, silently. Every write and every
    delete in this module now passes through here or `_is_link` first. Components ABOVE the store
    root (`.sdlc/state`, `.sdlc`, the project) are the host's business and are not checked."""
    root = store_dir(sdlc_dir).absolute()
    q = pathlib.Path(path).absolute()
    while True:
        if _is_link(q):
            raise ValueError(f"{q} is a link; the timing store never writes through a link")
        if q == root:
            break
        if q.parent == q:
            raise ValueError(f"{path} is outside the timing store")
        q = q.parent
    real_root = pathlib.Path(os.path.realpath(root))
    real = pathlib.Path(os.path.realpath(path))
    if real != real_root and real_root not in real.parents:
        raise ValueError(f"{path} resolves outside the timing store")


def goal_dir(sdlc_dir, goal):
    """This goal's own directory. Raises `ValueError` for an unsafe `goal`, mirroring
    `actionlog.log_path`'s refusal — the single chokepoint where a goal identifier becomes a
    filesystem path, so `append()` must never accept a caller-built path."""
    stem = _work_module().stem(goal)
    reason = _unsafe_stem_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the timing store: {reason}")
    # The shared rule refuses only separators and `..`. Two more refusals live HERE, beside it,
    # so the byte-identical copy stays byte-identical:
    #   - `""` and `.` pass the rule, and pathlib collapses `store / "."` to the store ROOT, where
    #     files land beside the goal directories and the sweep never looks (security review S5);
    #   - the sessions subtree's name is a legal goal id by that rule, and on a case-insensitive
    #     filesystem so is any casing of it (security review S6). A goal literally named
    #     `_sessions` would otherwise be misfiled among sessions, silently.
    if not stem.strip() or stem.strip() == ".":
        raise ValueError(f"goal {goal!r} reduces to an empty path component")
    if stem.casefold() == SESSIONS.casefold():
        raise ValueError(f"goal {goal!r} names the timing store's reserved sessions subtree")
    return store_dir(sdlc_dir) / stem


def _writer_id():
    """The pid, and deliberately NOT the hostname the ledger's own `<actor>-<host>.<pid>` fan-out
    carries. The ledger needs a host because its entries are shared through git and two machines'
    files land side by side; this store is local and gitignored, so they never do.

    Pid alone is sufficient for the property that matters: it is unique among CONCURRENTLY RUNNING
    processes, and concurrency is the only thing that can corrupt an append. A later process that
    reuses a retired pid appends to that file sequentially, which is safe.

    Not importing `socket` for a hostname is the other half of the reason. This module is loaded
    on the phase-boundary path, which `tests/test_phase_report.py::
    test_phase_report_loads_no_network_capable_sibling_module` deliberately keeps free of
    network-capable modules — `socket` is named on that test's own list. `gethostname()` reaches no
    network, but a reader auditing this path should not have to know that."""
    return str(os.getpid())


def writer_path(sdlc_dir, goal):
    """The file THIS process appends to. One file per writing process is the whole defence: append
    atomicity is POSIX-only by `actionlog.append`'s own admission and is measured failing on
    Windows, and a single goal has two writers (phase end and verify) so a shared per-goal file
    contends even with no parallel goals. Nothing here relies on the OS ordering concurrent
    appends, because no two processes ever open the same file."""
    return goal_dir(sdlc_dir, goal) / f"{_writer_id()}.jsonl"


#: The reserved subtree holding process-time records keyed by SESSION rather than by goal. Sits
#: beside the goal directories under `store_dir`; `goal_dir` refuses this name so no goal can ever
#: resolve into it.
SESSIONS = "_sessions"

#: Hook-written segments for one session share this file. One session's hook events are
#: sequential by construction (a turn cannot overlap itself), so a single file is safe here and
#: avoids minting one file per segment — every hook invocation is a fresh process, and per-process
#: files would otherwise multiply without bound.
TURNS_FILE = "turns.jsonl"


def session_dir(sdlc_dir, session):
    """This session's own directory. Raises `ValueError` for an unsafe or empty id.

    The id reaches here from hook stdin or an environment variable — untrusted either way — and
    becomes a path component, so it is checked with the SAME shared rule a goal stem is
    (`state.unsafe_goal_reason`), not a second rule that could drift from it. Never calls
    `goal_dir`: the two routes to disk are deliberately disjoint."""
    text = str(session).strip()
    if not text or text == ".":
        raise ValueError("empty session id for the timing store")
    reason = _unsafe_stem_reason(text)
    if reason:
        raise ValueError(f"unsafe session id {session!r} for the timing store: {reason}")
    return store_dir(sdlc_dir) / SESSIONS / text


def session_writer_path(sdlc_dir, session, shared):
    """`shared` picks the single per-session file (hook segments, sequential) or this process's
    own file (the script floor, which can run concurrently with a hook). Two writers, never one
    file — the goal layer's guarantee, kept."""
    directory = session_dir(sdlc_dir, session)
    return directory / (TURNS_FILE if shared else f"{_writer_id()}.jsonl")


# --------------------------------------------------------------------------- timestamps


def _stamp(now=None):
    """UTC, millisecond precision — matching `actionlog._stamp()` rather than the ledger's
    whole-second form, because two intervals of one goal can complete inside the same second."""
    t = now if now is not None else time.time()
    ms_total = int(round(t * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms_total // 1000)) + f".{ms_total % 1000:03d}Z"


# --------------------------------------------------------------------------- write


#: How long a finished goal's durations are kept. Bounded growth is a design-time requirement, not
#: an operational surprise. The action log once grew without limit (#457 has since added
#: `retention.py`, which prunes a closed-as-done goal's log); this store prunes by its own rolling
#: window. The precedent copied is `autowatch._record_spend`:
#: append-only, pruned to a rolling window on the write side, fail-open throughout.
RETENTION_DAYS = 90

#: One `stat` of the stamp per PROCESS; the stamp decides whether a sweep runs (at most daily). A
#: long-lived loop process pays the stat once; a hook process pays it once per event, which is
#: what makes the hook path cheap.
_pruned_this_process = False


def prune(sdlc_dir, keep_days=RETENTION_DAYS, now=None):
    """Drop goals whose LAST recorded activity is older than the window. -> the goal dirs removed.

    Per GOAL, keyed on its most recent file, never per file: a long-running goal has one file per
    writing process and its earliest may be well outside the window while the goal is still going.
    Pruning file by file would silently amputate that goal's early phases and quietly shrink
    `active` — a wrong number is worse than an absent one.

    Removes only its own `*.jsonl`, then the directory itself and only if that left it empty.
    Anything else in there belongs to somebody else, and a retention sweep is not a licence to
    delete it. Never raises: retention runs on the write path, and a failed sweep must cost a
    little disk, never a goal."""
    cutoff = (now if now is not None else time.time()) - keep_days * 86400
    root = store_dir(sdlc_dir)
    # A linked store root or sessions root would make every "inside" path resolve elsewhere; the
    # sweep DELETES, so a link anywhere on its path means it does nothing (security review S1).
    if _is_link(root):
        return []
    try:
        # The goal pass skips the sessions subtree EXPLICITLY. It used to be skipped only because
        # no `*.jsonl` sits directly under it — an incidental effect, pinned as a rule now.
        goals = [p for p in root.iterdir()
                 if p.is_dir() and p.name.casefold() != SESSIONS.casefold()]
    except OSError:
        return []
    removed = _sweep(goals, cutoff, sdlc_dir)
    sessions = []
    if not _is_link(root / SESSIONS):
        try:
            sessions = [p for p in (root / SESSIONS).iterdir() if p.is_dir()]
        except OSError:
            sessions = []
    removed += [f"{SESSIONS}/{name}" for name in _sweep(sessions, cutoff, sdlc_dir)]
    return sorted(removed)


def _sweep(directories, cutoff, sdlc_dir):
    """One retention pass over per-key directories: each is kept if ANY file in it is newer than
    the cutoff (retention is per key on its most recent activity), else its `*.jsonl` are removed
    and the directory only if that left it empty.

    A linked directory is skipped outright and a linked file is never unlinked — `is_dir()`,
    `is_file()` and `glob` all follow links, and this is the one place the store deletes."""
    removed = []
    for directory in directories:
        try:
            if _is_link(directory):
                continue
            files = [f for f in directory.glob("*.jsonl") if f.is_file() and not _is_link(f)]
            if not files or max(f.stat().st_mtime for f in files) >= cutoff:
                continue
            for f in files:
                _refuse_links(f, sdlc_dir)
                f.unlink()
            try:
                directory.rmdir()
            except OSError:
                pass                    # something else lives here; its owner keeps it
            removed.append(directory.name)
        except OSError:
            continue
    return removed


#: The sweep runs at most this often, across ALL callers, throttled by a stamp file. Every hook
#: invocation is a fresh process, so "once per process" there would mean "on every event" — a
#: synthetic 100 sessions × 100 files sweep measured 553 ms, which is not a per-turn cost.
PRUNE_INTERVAL_SECONDS = 86400


def maybe_prune(sdlc_dir, now=None):
    """The sweep, if the stamp says it is due; else nothing but one `stat`. -> the sweep's result,
    or None when it did not run. Fail-open: a stamp that cannot be read or written costs at most
    one extra sweep, never a write."""
    t = now if now is not None else time.time()
    stamp = store_dir(sdlc_dir) / ".last-prune"
    # A linked stamp is hostile (`write_text` follows it — security review S1, probe E): neither
    # write nor sweep. A stamp dated in the FUTURE is not "recently swept" either — a corrected
    # clock or a restored backup would otherwise suppress retention forever (code review C3).
    if _is_link(stamp):
        return None
    try:
        if stamp.exists() and 0 <= t - stamp.stat().st_mtime < PRUNE_INTERVAL_SECONDS:
            return None
    except OSError:
        pass
    removed = prune(sdlc_dir, now=t)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        _refuse_links(stamp, sdlc_dir)
        stamp.write_text(str(int(t)), encoding="utf-8")
        os.utime(stamp, (t, t))
    except (OSError, ValueError):
        pass
    return removed


def _prune_if_due(sdlc_dir):
    """Once per process reaches the stamp check; the stamp decides whether a sweep runs. Set the
    flag BEFORE the check, not after: a raising sweep must not leave it clear and retry on every
    subsequent write for the life of the process."""
    global _pruned_this_process
    if _pruned_this_process:
        return
    _pruned_this_process = True
    try:
        maybe_prune(sdlc_dir)
    except Exception:                                       # noqa: BLE001 - retention never breaks a write
        pass


def append(sdlc_dir, goal, kind, name, ms, started=None, now=None):
    """Append one completed interval. `ms` is integer milliseconds; a negative or unmeasurable
    interval is the CALLER's to omit entirely — a zero here would claim "measured, and
    instantaneous", which is the one thing this store must never say."""
    _prune_if_due(sdlc_dir)
    entry = {
        "ts": _stamp(now),
        "goal": str(goal),
        "kind": kind,
        "name": name,
        "ms": int(ms),
    }
    if started is not None:
        entry["started"] = int(started)
    path = writer_path(sdlc_dir, goal)
    path.parent.mkdir(parents=True, exist_ok=True)
    _refuse_links(path, sdlc_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def safe_append(sdlc_dir, goal, kind, name, ms, started=None, now=None):
    """The form every call site uses. Fail-open, mirroring `actionlog.safe_append`: losing one
    timing line is acceptable, killing a goal over one is not."""
    try:
        return append(sdlc_dir, goal, kind, name, ms, started=started, now=now)
    except Exception as exc:                                # noqa: BLE001 - fail-open by design
        print(f"timing_store: interval skipped (non-fatal): {exc}", file=sys.stderr)
        return None


#: Session-scoped interval kinds. `turn` is a hook-closed segment attributed to a skill; `script`
#: is one self-timed script run — the host-agnostic floor. Never summed with each other.
TURN_KIND = "turn"
SCRIPT_KIND = "script"


def append_session(sdlc_dir, session, kind, name, ms, started=None, now=None, shared=None):
    """Append one completed interval keyed by SESSION, not goal. `started` is the segment's start
    in MILLISECONDS and is the idempotency key `_deduplicate` collapses on — two same-skill
    segments opened within one second must not collide, which whole seconds would allow.

    `shared` defaults from the kind: hook segments go to the session's single file, everything
    else to this process's own. The retention check runs here too — one `stat` of the stamp, and
    a sweep only when the stamp says one is due — so the hook IS a delete caller, once a day at
    most. (An earlier docstring said no sweep ran here; the code and its test said otherwise.)"""
    if shared is None:
        shared = kind == TURN_KIND
    _prune_if_due(sdlc_dir)
    entry = {
        "ts": _stamp(now),
        "session": str(session),
        "kind": kind,
        "name": name,
        "ms": int(ms),
    }
    if started is not None:
        entry["started"] = int(started)
    path = session_writer_path(sdlc_dir, session, shared)
    path.parent.mkdir(parents=True, exist_ok=True)
    _refuse_links(path, sdlc_dir)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


# --------------------------------------------------------------------------- the script-time floor


def _find_sdlc(argv):
    """The `.sdlc` this script run belongs to, or None.

    The floor is host-agnostic, so it cannot assume an environment variable names the project.
    In order: a `.sdlc` directory named on the command line (every loop verb takes one), then
    one under `CLAUDE_PROJECT_DIR`, then one under the working directory. Nowhere → nothing is
    recorded, silently: a script run outside any adopted repository has no store to record into,
    and inventing one would seed `.sdlc/` where Sigma was never adopted."""
    for arg in argv or []:
        try:
            candidate = pathlib.Path(str(arg))
            if candidate.name == ".sdlc" and candidate.is_dir():
                return candidate
        except (OSError, ValueError):
            continue
    for base in (os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if base:
            candidate = pathlib.Path(base) / ".sdlc"
            if candidate.is_dir():
                return candidate
    return None


def _record_script(argv, name, started):
    """Fail-open in full: a failure to record never changes a script's result or exit code."""
    try:
        ms = int((time.perf_counter() - started) * 1000)
        sdlc_dir = _find_sdlc(argv)
        if sdlc_dir is None:
            return
        # Resolved HERE, after `main` — never at entry. The loop arms `SIGMA_RUN_ID` inside its
        # own verbs (`_arm_run_id`, from `start`/`next`/`next-batch`), so a run id read before
        # `main` would file a loop-spawned script under the dated fallback bucket instead.
        append_session(sdlc_dir, session_id(), SCRIPT_KIND, name, ms)
    except Exception:                                       # noqa: BLE001 - the floor never breaks a script
        pass


def _is_help(argv):
    """True when the argv's ONLY user argument is `-h` or `--help` — a help request, not a run.

    Callers hand `timed_main` three shapes: `sys.argv` (script name first), `sys.argv[1:]`
    (`reviewer.py`), `sys.argv[2:]` (the test probe), so "only user argument" is `len <= 2` with
    the flag last. The bound keeps a real run whose last word is a help flag
    (`loop.py note <dir> <goal> --help`) recorded. Deliberately, this also skips timing for
    `reviewer.py check --help` (`sys.argv[1:]` = `["check", "--help"]`), which `main` itself treats
    as bad argv and answers with usage on stderr, rc 2 — a mis-typed help request is not a run
    worth timing either, so the over-match is harmless."""
    argv = list(argv or [])
    return len(argv) <= 2 and argv[-1:] in (["-h"], ["--help"])


def timed_main(fn, argv, name):
    """Run a script's `main` and record its wall time as one `script` line — the host-agnostic
    floor beneath the turn hooks. Wraps the CLI entry: `sys.exit(timed_main(main, sys.argv, "loop"))`.

    A `SystemExit` raised inside `main` is a real run that ended with an exit code: it is recorded
    and re-raised untouched. The result of `main` is returned untouched. Nothing about recording
    can change what the script does or what it returns.

    A help request (`_is_help`) is not a script run: `main` is called and nothing is recorded, so
    `<script> --help` leaves an adopted repository's `.sdlc/` untouched (#2736)."""
    if _is_help(argv):
        return fn(argv)
    started = time.perf_counter()
    try:
        return fn(argv)
    finally:
        # `finally`, not `except SystemExit`: a script that crashes with any exception still ran
        # and still took time (code review C4). The exception propagates untouched because
        # `_record_script` swallows everything of its own.
        _record_script(argv, name, started)


# --------------------------------------------------------------------------- read


def goal_files(sdlc_dir, goal):
    """Every file holding this goal's intervals — one per process that ever wrote for it."""
    directory = goal_dir(sdlc_dir, goal)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.jsonl") if p.is_file())


def _ms_of(entry):
    try:
        return int(entry.get("ms"))
    except (TypeError, ValueError):
        return None


def _deduplicate(entries):
    """Collapse intervals that are one interval recorded more than once.

    `phase_report.py end` does not consume the marker it measures from, so a retried phase
    boundary runs the whole measurement again against the SAME start and appends a second line.
    Both describe one interval; counting both inflates `active` by a whole phase.

    The key is `(kind, name, started)` — the start epoch, not the phase name, because a phase
    legitimately re-entered after a park carries a NEW start and its interval is genuinely
    additional. An entry with no `started` is never collapsed: a verify run has no idempotency key,
    and two real runs of one command can take an identical number of milliseconds, so collapsing
    them would discard measured work.

    The SHORTEST duration wins. A repeat measures from the same start to a later instant and so
    reports a longer interval than the real one; the phase in fact ended at the first `end`.
    Undercounting toward absence is the direction a downstream duration metric already chose for its own
    double-start case. It is also the only deterministic rule available: entries are spread over
    one file per writing process and the order between those files is filesystem-dependent, so
    "keep the first seen" would vary run to run — observed directly, a repeated end returned its
    LATER line first."""
    seen = {}
    out = []
    for entry in entries:
        started = entry.get("started")
        if started is None:
            out.append(entry)
            continue
        try:
            key = (entry.get("kind"), str(entry.get("name")), int(started))
        except (TypeError, ValueError):
            continue                        # an unusable key is a malformed line: skipped, never raised
        if key not in seen:
            seen[key] = len(out)
            out.append(entry)
            continue
        held = out[seen[key]]
        challenger, incumbent = _ms_of(entry), _ms_of(held)
        if incumbent is None or (challenger is not None and challenger < incumbent):
            out[seen[key]] = entry          # replaced in place, so ordering stays stable
    return out


#: Intervals of this kind are the phases that make up `active`; everything else is background.
PHASE_KIND = "phase"


def totals(sdlc_dir, goal):
    """The four figures for one goal, in raw milliseconds.

    Numbers, never formatted strings: `phase_report.py` imports THIS module, so importing its
    `format_elapsed` back would be a cycle. Presentation belongs to each consumer.

    -> {"recorded", "active_ms", "background_ms", "effort_ms", "phases", "background_runs"}

      active      Σ of the goal's phase intervals — "how long was work actually happening".
                  Idle between phases is excluded by construction: time spent parked, blocked or
                  awaiting a human sits inside no phase, so summing phases omits it with no
                  pause/resume state machine. It is a LOWER BOUND — a phase parked and resumed
                  measures only from the resume, and a phase that never ended has no interval at
                  all.
      background  Σ of everything else (verify runs, and the flake/diff-revert passes). Also a
                  lower bound: an untimed background activity is invisible by definition.
      effort      active + background, which DELIBERATELY DOUBLE-COUNTS. A background run
                  dispatched during a phase is already inside that phase's duration, so this can
                  exceed real elapsed time. It is the arithmetic that was asked for, and the
                  reason every caller must label it effort and never wall-clock.

    A goal with no intervals returns `recorded: False` and None sums — not zeros. "Not recorded"
    and "recorded as zero" are different answers, and only one of them is true of a goal whose
    work was never observed."""
    entries = read_goal(sdlc_dir, goal)
    if not entries:
        return {"recorded": False, "active_ms": None, "background_ms": None, "effort_ms": None,
                "phases": 0, "background_runs": 0}
    active = background = phases = runs = 0
    for entry in entries:
        ms = _ms_of(entry)
        if ms is None or ms < 0:
            continue
        if entry.get("kind") == PHASE_KIND:
            active += ms
            phases += 1
        else:
            background += ms
            runs += 1
    return {"recorded": True, "active_ms": active, "background_ms": background,
            "effort_ms": active + background, "phases": phases, "background_runs": runs}


def unit_totals(sdlc_dir, unit, repo=None):
    """The same four figures for a whole unit of work — "how long did this project take".

    SINGLE-REPO BY CONSTRUCTION, and it says so. The feature registry stores a unit's goals
    REPO-SCOPED, while this store is local to one checkout: a goal driven in a sibling repo wrote
    its intervals into that checkout's `.sdlc/state/time/`, and no amount of reading here will
    find them. Summing only what happens to be local and calling it the unit's total would be a
    confidently-wrong smaller number — the same failure as reading the wrong directory, wearing a
    non-zero disguise. So every repo whose goals are not counted is NAMED, in `elsewhere`, with
    how many goals it holds.

    `repo` is the caller's own repo key. Flattening the registry's repo scoping away would also
    collide two repos' identical issue numbers into one goal.

    `feature_registry` and `feature_sync` are loaded LAZILY, inside this function: `phase_report.py`
    loads this module on every phase boundary and must not pay for a registry it never reads. The
    same reason `phase_report` defers its own `ledger` load.

    -> {"found", "recorded", "unit", "active_ms", "background_ms", "effort_ms",
        "counted_goals", "elsewhere": [(repo, goal_count), ...]}"""
    absent = {"found": False, "recorded": False, "unit": unit, "active_ms": None,
              "background_ms": None, "effort_ms": None, "counted_goals": 0, "elsewhere": []}
    registry = _load("feature_registry")
    try:
        entries = registry.read(registry.registry_dir(sdlc_dir))
    except Exception:                                       # noqa: BLE001 - a reader never raises
        return absent
    entry = (entries or {}).get(unit)
    if not isinstance(entry, dict):
        return absent

    repos = entry.get("repos")
    repos = repos if isinstance(repos, dict) else {}
    # The registry's OWN spelling of this repo, never an exact-key lookup — two casings are one
    # repo, and `feature_sync.repo_key` is the single authority on that so a second rule here
    # cannot drift from it.
    local = None
    if repo is not None:
        try:
            local = _load("feature_sync").repo_key(repos, repo)
        except Exception:                                   # noqa: BLE001 - fall back to exact
            local = repo

    counted, elsewhere = [], []
    for key, one in repos.items():
        goals = (one or {}).get("goals") if isinstance(one, dict) else None
        goals = [g for g in (goals or [])]
        if key == local:
            counted.extend(goals)
        elif goals:
            elsewhere.append((key, len(goals)))

    active = background = 0
    recorded = False
    for goal in counted:
        one = totals(sdlc_dir, goal)
        if not one["recorded"]:
            continue
        recorded = True
        active += one["active_ms"]
        background += one["background_ms"]
    if not recorded:
        return {**absent, "found": True, "counted_goals": len(counted), "elsewhere": sorted(elsewhere)}
    return {"found": True, "recorded": True, "unit": unit, "active_ms": active,
            "background_ms": background, "effort_ms": active + background,
            "counted_goals": len(counted), "elsewhere": sorted(elsewhere)}


def _read_entries(paths):
    """Parse every interval line in `paths`. Malformed lines are skipped, never raised — a process
    killed mid-write can leave a partial line, and one bad line must not hide the rest."""
    out = []
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (ValueError, RecursionError):
                # `RecursionError` too: `doctor._read_events_from` records that narrowing this to
                # `ValueError` "took the whole ledger view down" more than once (code review C6).
                continue
            if not (isinstance(entry, dict) and isinstance(entry.get("kind"), str)):
                continue
            # A JSON-valid line with a non-string name or an unusable start is malformed for THIS
            # store — it would crash de-duplication, the totals, or the renderer (security
            # review S4, code review C9). Skipped, never raised.
            if "name" in entry and not isinstance(entry["name"], str):
                continue
            if entry.get("started") is not None:
                try:
                    int(entry["started"])
                except (TypeError, ValueError):
                    continue
            out.append(entry)
    return out


def read_goal(sdlc_dir, goal):
    """Every recorded interval for one goal, de-duplicated."""
    return _deduplicate(_read_entries(goal_files(sdlc_dir, goal)))


# --------------------------------------------------------------------------- sessions (read)


def session_files(sdlc_dir, session):
    """Every file holding this session's intervals — the shared hook file plus one per script
    process that ever wrote for it."""
    directory = session_dir(sdlc_dir, session)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.jsonl") if p.is_file())


def read_session(sdlc_dir, session):
    """Every recorded interval for one session, de-duplicated by the same rule as a goal's — a
    segment re-closed after a crash between "append" and "write marker" carries the same
    `started` and collapses to one line, shortest wins."""
    return _deduplicate(_read_entries(session_files(sdlc_dir, session)))


def session_totals(sdlc_dir, session):
    """Process time for one session, in raw milliseconds.

    -> {"recorded", "precise", "turn_ms", "segments", "by_skill", "script_ms", "script_runs"}

      turn_ms    Σ of hook-closed segments — THE total. Idle is excluded by construction (a gap
                 between `Stop` and the next prompt sits in no segment). None when no segment
                 exists: turn time was not observable, which is not the same as "took none".
      by_skill   turn time per skill, descending, the "(none)" bucket included — work before any
                 skill was invoked is still work.
      script_ms  the script-time floor. Reported BESIDE turn time and NEVER added to it: scripts
                 run inside turns, so the sum would double-count. Also a floor that excludes
                 interpreter start-up, and that cannot see skills which invoke no scripts.
      precise    True when at least one segment exists — the host had turn hooks."""
    entries = read_session(sdlc_dir, session)
    if not entries:
        return {"recorded": False, "precise": False, "turn_ms": None, "segments": 0,
                "by_skill": {}, "script_ms": None, "script_runs": 0}
    by_skill, turn_ms, segments, script_ms, runs = {}, 0, 0, 0, 0
    for entry in entries:
        ms = _ms_of(entry)
        if ms is None or ms < 0:
            continue
        if entry.get("kind") == TURN_KIND:
            name = entry.get("name") or "(none)"
            by_skill[name] = by_skill.get(name, 0) + ms
            turn_ms += ms
            segments += 1
        elif entry.get("kind") == SCRIPT_KIND:
            script_ms += ms
            runs += 1
    precise = segments > 0
    return {"recorded": True, "precise": precise, "turn_ms": turn_ms if precise else None,
            "segments": segments,
            "by_skill": dict(sorted(by_skill.items(), key=lambda kv: (-kv[1], kv[0]))),
            "script_ms": script_ms, "script_runs": runs}


def session_id(now=None):
    """The session a record belongs to, resolved by a fixed chain and never a fourth source:

      1. `CLAUDE_CODE_SESSION_ID` — a Claude Code session, the common case;
      2. `SIGMA_RUN_ID` — an unattended loop run with no interactive session;
      3. `unattributed-<YYYY-MM-DD>` — a host with neither, bucketed by UTC day so the script floor
         still lands somewhere honest instead of being discarded. This bucket is SHARED: two
         concurrent hookless sessions merge into it, and the reader says so.

    A value that would be refused as a path component is treated as ABSENT, not as an error, and
    the next link is used — a hook must never fail a turn because an environment variable held a
    slash."""
    for var in ("CLAUDE_CODE_SESSION_ID", "SIGMA_RUN_ID"):
        value = (os.environ.get(var) or "").strip()
        if value and value != "." and not _unsafe_stem_reason(value):
            return value
    t = now if now is not None else time.time()
    return "unattributed-" + time.strftime("%Y-%m-%d", time.gmtime(t))
