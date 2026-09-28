"""Always-on timing store — write side (skills/agrim-loop/scripts/timing_store.py).

Mirrors tests/test_actionlog.py's shape. The load-bearing test here is the concurrency one, and
it exists because the precedent this store was nearly built on does NOT hold on every host:
`actionlog.append`'s own docstring scopes its atomicity argument to POSIX ("On POSIX, a `write()`
to an `O_APPEND` file..."), and `test_concurrent_appends_from_two_real_processes_do_not_corrupt_
the_file` is measured FAILING on Windows — repeated runs lose lines and can corrupt one.

That is not a hypothetical for this store. A single goal has TWO writers (the phase-end call site
and the verify call site), so a shared per-goal file contends even with no parallel goals at all.
The store therefore gives every writing process its own file, and the test below pins that
structurally — not merely "no lines were lost this run", which a lossy design passes most of the
time, but "the two processes never wrote to the same file", which it cannot pass at all.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


timing_store = _mod("timing_store")


def _sdlc(tmp_path):
    """No config is written at all — deliberately. This store reads none, and a test that supplied
    one could not tell an always-on writer from a gated one that happened to be switched on."""
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    return str(d)


# --------------------------------------------------------------------- concurrency (real processes)

_CONCURRENT_WRITER_SCRIPT = """
import sys, importlib.util
mod_py, sdlc_dir, goal, n, tag = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
spec = importlib.util.spec_from_file_location("timing_store", mod_py)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
for i in range(n):
    m.safe_append(sdlc_dir, goal, "phase", tag + "-" + str(i), 1000 + i)
"""


def test_two_real_processes_never_share_a_timing_file(tmp_path):
    """S1's acceptance criterion. Two genuine OS processes (Popen, so they actually overlap in
    wall-clock time — this repo's own "real processes, not threads" bar) write N intervals each for
    the SAME goal.

    Three assertions, in order of how much they prove:
      1. every interval survives — 2N entries, none lost;
      2. every line parses — no interleaved bytes;
      3. the two processes used DIFFERENT files — the structural property that makes 1 and 2 true
         on every host rather than on POSIX only.

    Assertion 3 is the one that cannot be satisfied by luck. A shared-file design passes 1 and 2
    on a quiet run and fails them intermittently; it fails 3 every single time.
    """
    d = _sdlc(tmp_path)
    goal = "concurrent-goal"
    n = 40
    script = tmp_path / "writer.py"
    script.write_text(_CONCURRENT_WRITER_SCRIPT)
    procs = [subprocess.Popen([sys.executable, str(script), str(S / "timing_store.py"), d, goal, str(n), tag])
             for tag in ("a", "b")]
    for p in procs:
        assert p.wait(timeout=60) == 0

    entries = timing_store.read_goal(d, goal)
    assert len(entries) == 2 * n, f"intervals were lost: {len(entries)} of {2 * n}"
    assert sum(1 for e in entries if e["name"].startswith("a-")) == n
    assert sum(1 for e in entries if e["name"].startswith("b-")) == n

    files = timing_store.goal_files(d, goal)
    assert len(files) == 2, (
        f"the two writing processes shared {len(files)} file(s); each must own its own, because "
        "append atomicity is POSIX-only and this store must hold on every host")


# --------------------------------------------------------------------- the always-on property


def test_append_reads_no_config_at_all(tmp_path):
    """The whole reason this store exists. There is no config.json in the fixture, so a module that
    consulted one would raise or skip; this one must record regardless."""
    d = _sdlc(tmp_path)
    assert not (pathlib.Path(d) / "config.json").exists()
    timing_store.append(d, "482", "phase", "implement", 4440000)
    assert [e["ms"] for e in timing_store.read_goal(d, "482")] == [4440000]


# --------------------------------------------------------------------- path safety


def test_an_unsafe_goal_is_refused_rather_than_escaping_the_store(tmp_path):
    """The one place a goal identifier becomes a path. `append` must go through it, so a traversal
    attempt cannot reach the filesystem even via the fail-open wrapper."""
    d = _sdlc(tmp_path)
    with pytest.raises(ValueError):
        timing_store.goal_dir(d, "../../etc/passwd")
    with pytest.raises(ValueError):
        timing_store.writer_path(d, "../../etc/passwd")
    assert timing_store.safe_append(d, "../../etc/passwd", "phase", "x", 1) is None
    assert not list(timing_store.store_dir(d).rglob("*.jsonl"))


# --------------------------------------------------------------------- fail-open contract


def test_a_write_failure_is_swallowed_so_a_goal_never_dies_for_a_timing_line(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(timing_store, "append", boom)
    assert timing_store.safe_append(d, "482", "phase", "implement", 1000) is None


# --------------------------------------------------------------------- read tolerance


def test_read_skips_a_malformed_and_a_blank_line(tmp_path):
    """A process killed mid-write can leave a partial line; one bad line must not hide the rest."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "158", "phase", "first", 10)
    path = timing_store.goal_files(d, "158")[0]
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write("\n")
    timing_store.append(d, "158", "phase", "second", 20)
    assert [e["name"] for e in timing_store.read_goal(d, "158")] == ["first", "second"]


def test_read_is_empty_for_a_goal_with_nothing_recorded(tmp_path):
    assert timing_store.read_goal(_sdlc(tmp_path), "no-such-goal") == []


# --------------------------------------------------------------------- de-duplication (S3)


def test_two_intervals_sharing_a_start_collapse_to_one(tmp_path):
    """`phase_report.py end` never consumes the marker, so nothing stops it running twice for one
    phase — a retried boundary, or two orchestrators marking the same one. Both calls measure from
    the SAME start, so both are the same interval seen twice, and counting them twice would inflate
    `active` by a whole phase."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 4440000, started=1_000_000)
    timing_store.append(d, "482", "phase", "implement", 4441500, started=1_000_000)
    assert [e["ms"] for e in timing_store.read_goal(d, "482")] == [4440000]


def test_the_shorter_of_two_duplicates_wins(tmp_path):
    """A second `end` measures from the same start to a LATER instant, so it reports a longer
    interval than the real one — the phase actually finished at the first `end`. Keeping the
    smaller value undercounts toward absence, the same direction a downstream duration metric chose
    deliberately for its own double-start case.

    It is also the only DETERMINISTIC choice available here: entries are spread across one file per
    writing process and the glob order between them carries no meaning, so "whichever was seen
    first" would depend on filesystem ordering. Smallest-wins does not."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "review", 900, started=55)
    timing_store.append(d, "482", "phase", "review", 100, started=55)
    timing_store.append(d, "482", "phase", "review", 500, started=55)
    assert [e["ms"] for e in timing_store.read_goal(d, "482")] == [100]


def test_a_different_start_is_a_different_interval(tmp_path):
    """The key is the start epoch, not the phase name. A phase legitimately re-entered after a park
    has a NEW start, and its interval is genuinely additional — collapsing those would undercount
    every resumed goal."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 1000, started=10)
    timing_store.append(d, "482", "phase", "implement", 2000, started=99)
    assert sorted(e["ms"] for e in timing_store.read_goal(d, "482")) == [1000, 2000]


def test_totals_separate_phase_time_from_background_time(tmp_path):
    """S5's acceptance criterion. `active` is the sum of phase intervals — the headline "how long
    was work actually happening" — and `background` is everything else."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 4440000, started=1)
    timing_store.append(d, "482", "phase", "review", 1440000, started=2)
    timing_store.append(d, "482", "verify", "command", 252000)
    timing_store.append(d, "482", "verify", "flake", 228000)

    t = timing_store.totals(d, "482")
    assert t["active_ms"] == 5880000
    assert t["background_ms"] == 480000
    assert t["phases"] == 2
    assert t["background_runs"] == 2
    assert t["recorded"] is True


def test_effort_is_active_plus_background_even_though_that_double_counts(tmp_path):
    """The deliberate arithmetic of the ask, pinned so nobody "fixes" it later. A background run
    dispatched during a phase is ALREADY inside that phase's duration, so adding it again counts
    it twice — `effort` can therefore exceed real elapsed time. That is what was asked for, which
    is exactly why it must never be presented as wall-clock."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 1000, started=1)
    timing_store.append(d, "482", "verify", "command", 400)

    t = timing_store.totals(d, "482")
    assert t["effort_ms"] == t["active_ms"] + t["background_ms"] == 1400


def test_totals_for_a_goal_with_nothing_recorded_are_absent_not_zero(tmp_path):
    """"Not recorded" and "recorded as zero" are different answers and only one of them is true on
    a goal that never ran. The caller needs to be able to tell them apart, so the sums are None
    rather than 0 and `recorded` is False."""
    t = timing_store.totals(_sdlc(tmp_path), "never-ran")
    assert t["recorded"] is False
    assert t["active_ms"] is None and t["background_ms"] is None and t["effort_ms"] is None


def test_totals_count_a_repeated_phase_end_once(tmp_path):
    """Totals read through the de-duplication, so a retried phase boundary cannot inflate `active`
    by a whole phase. Without this the headline figure is the thing that breaks."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 4440000, started=1_000_000)
    timing_store.append(d, "482", "phase", "implement", 4441500, started=1_000_000)
    assert timing_store.totals(d, "482")["active_ms"] == 4440000


# --------------------------------------------------------------------- retention (S11)


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_prune_removes_a_goal_whose_intervals_are_all_older_than_the_window(tmp_path):
    d = _sdlc(tmp_path)
    timing_store.append(d, "ancient", "phase", "implement", 1000, started=1)
    for f in timing_store.goal_files(d, "ancient"):
        _age(f, timing_store.RETENTION_DAYS + 5)

    assert timing_store.prune(d) == ["ancient"]
    assert timing_store.read_goal(d, "ancient") == []


def test_prune_keeps_a_goal_inside_the_window(tmp_path):
    d = _sdlc(tmp_path)
    timing_store.append(d, "recent", "phase", "implement", 1000, started=1)
    assert timing_store.prune(d) == []
    assert len(timing_store.read_goal(d, "recent")) == 1


def test_prune_keeps_a_goal_with_one_recent_file_among_old_ones(tmp_path):
    """A long-running goal has one file per writing process; the oldest may be well outside the
    window while the goal is still active. Retention is per GOAL, keyed on its most recent
    activity, never per file — pruning file by file would silently amputate the early phases of a
    goal that is still going."""
    d = _sdlc(tmp_path)
    goal_dir = timing_store.goal_dir(d, "long")
    goal_dir.mkdir(parents=True)
    (goal_dir / "111.jsonl").write_text(
        json.dumps({"ts": "x", "goal": "long", "kind": "phase", "name": "a", "ms": 1}) + "\n")
    (goal_dir / "222.jsonl").write_text(
        json.dumps({"ts": "x", "goal": "long", "kind": "phase", "name": "b", "ms": 2}) + "\n")
    _age(goal_dir / "111.jsonl", timing_store.RETENTION_DAYS + 5)

    assert timing_store.prune(d) == []
    assert len(timing_store.read_goal(d, "long")) == 2


def test_prune_never_deletes_a_file_it_does_not_own(tmp_path):
    """It removes its own `*.jsonl` and then the directory only if that leaves it empty. Anything
    else in there belongs to somebody else, and a retention sweep is not a licence to delete it."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "ancient", "phase", "implement", 1000, started=1)
    stranger = timing_store.goal_dir(d, "ancient") / "NOTES.txt"
    stranger.write_text("not mine")
    for f in timing_store.goal_files(d, "ancient"):
        _age(f, timing_store.RETENTION_DAYS + 5)

    timing_store.prune(d)
    assert stranger.exists()


def test_the_store_prunes_itself_without_the_reader_ever_running(tmp_path, monkeypatch):
    """Retention has to be a GUARANTEE, not a hope. Pruning only in the on-demand reader would mean
    a repository where nobody runs it accumulates goal directories forever — which is precisely the
    unbounded growth the action log's own directory already suffers (`doctor.py` records that a log
    file is never pruned once its goal is done). So the writer sweeps, once per process."""
    d = _sdlc(tmp_path)
    # Seeding the ancient interval must not itself be this process's once-only sweep: when this test
    # is the first timing write in its process (alone, or first on an xdist worker) that sweep wrote
    # `.last-prune` NOW and the sweep under test below then skipped -- an order-dependent red, seen
    # on origin/main too during #239's verify. The flag is shut for the seed, then opened.
    monkeypatch.setattr(timing_store, "_pruned_this_process", True)
    timing_store.append(d, "ancient", "phase", "implement", 1000, started=1)
    for f in timing_store.goal_files(d, "ancient"):
        _age(f, timing_store.RETENTION_DAYS + 5)

    monkeypatch.setattr(timing_store, "_pruned_this_process", False)
    timing_store.append(d, "fresh", "phase", "implement", 1000, started=1)   # no reader involved
    assert timing_store.read_goal(d, "ancient") == []
    assert len(timing_store.read_goal(d, "fresh")) == 1


# --------------------------------------------------------------------- unit rollup (S6)


def _unit(sdlc_dir, name, repos):
    """Write one unit shard — `.sdlc/features/units/<name>.json` is the registry's write surface."""
    units = pathlib.Path(sdlc_dir) / "features" / "units"
    units.mkdir(parents=True, exist_ok=True)
    (units / f"{name}.json").write_text(json.dumps({
        "schema": "sigma/features@1",
        "features": {name: {"title": name, "owner": None, "open": True, "parent": None,
                            "tracking_issue": None, "repos": repos}}}), encoding="utf-8")


def test_a_unit_totals_the_goals_recorded_in_this_repo(tmp_path):
    d = _sdlc(tmp_path)
    _unit(d, "uploader-hardening", {"acme/app": {"branch": "feature/uploader-hardening",
                                                 "owner": None, "authorized": True,
                                                 "goals": [481, 482]}})
    timing_store.append(d, "481", "phase", "implement", 1000, started=1)
    timing_store.append(d, "482", "phase", "implement", 2000, started=2)
    timing_store.append(d, "482", "verify", "command", 500)

    u = timing_store.unit_totals(d, "uploader-hardening", repo="acme/app")
    assert u["found"] is True
    assert u["active_ms"] == 3000
    assert u["background_ms"] == 500
    assert u["effort_ms"] == 3500          # background reported alongside effort, never instead
    assert u["counted_goals"] == 2


def test_a_unit_names_the_repos_whose_durations_are_not_local(tmp_path):
    """S6's acceptance criterion, and the review finding it exists for. The registry stores goals
    REPO-SCOPED while this store is local to one checkout, so a sibling repo's durations live in
    that checkout and can never be read from here. Summing only what happens to be local and
    presenting it as the unit's total would be a confidently-wrong smaller number — the same
    failure the spec forbids, wearing a non-zero disguise."""
    d = _sdlc(tmp_path)
    _unit(d, "uploader-hardening", {
        "acme/app": {"branch": "f", "owner": None, "authorized": True, "goals": [482]},
        "acme/sibling": {"branch": "f", "owner": None, "authorized": True, "goals": [901, 902]},
    })
    timing_store.append(d, "482", "phase", "implement", 2000, started=1)

    u = timing_store.unit_totals(d, "uploader-hardening", repo="acme/app")
    assert u["counted_goals"] == 1
    assert u["elsewhere"] == [("acme/sibling", 2)], u["elsewhere"]


def test_an_unknown_unit_is_reported_as_absent_not_as_zero(tmp_path):
    u = timing_store.unit_totals(_sdlc(tmp_path), "no-such-unit", repo="acme/app")
    assert u["found"] is False
    assert u["active_ms"] is None and u["effort_ms"] is None


def test_a_unit_whose_goals_have_no_intervals_is_recorded_as_absent(tmp_path):
    """A unit that exists but whose goals never ran is 'not recorded', the same distinction the
    per-goal totals draw — not a confident zero."""
    d = _sdlc(tmp_path)
    _unit(d, "quiet", {"acme/app": {"branch": "f", "owner": None, "authorized": True,
                                    "goals": [700]}})
    u = timing_store.unit_totals(d, "quiet", repo="acme/app")
    assert u["found"] is True and u["recorded"] is False
    assert u["active_ms"] is None


def test_intervals_with_no_start_key_are_never_deduplicated(tmp_path):
    """A verify run carries no start epoch, so it has no idempotency key — and two genuinely
    distinct runs of the same command can take an identical number of milliseconds. Collapsing
    those would silently discard real work, so an entry without a key is always kept."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "verify", "pytest", 252000)
    timing_store.append(d, "482", "verify", "pytest", 252000)
    assert [e["ms"] for e in timing_store.read_goal(d, "482")] == [252000, 252000]


# --------------------------------------------------------------------- sessions (process time, S1)


def test_a_session_line_never_lands_under_a_goal_stem(tmp_path):
    """S1's acceptance criterion, and the plan-review finding it answers: the first draft had NO
    path from a session line to disk at all, because `append` resolves through `goal_dir` and
    `goal_dir` refuses the reserved name. Session lines take their own route, and that route
    creates no goal directory as a side effect."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "4f2a91", "turn", "agrim-plan", 1238000, started=1789041912400)

    entries = timing_store.read_session(d, "4f2a91")
    assert [e["name"] for e in entries] == ["agrim-plan"]
    assert entries[0]["started"] == 1789041912400          # milliseconds, kept exactly
    # The store's top level holds ONLY the reserved subtree — no goal dir was minted.
    assert sorted(p.name for p in timing_store.store_dir(d).iterdir()) == [timing_store.SESSIONS]
    assert timing_store.read_goal(d, "4f2a91") == []


def test_the_reserved_session_name_is_refused_as_a_goal(tmp_path):
    """`state.unsafe_goal_reason` refuses only separators and `..`, so `_sessions` is a legal goal
    id today. The refusal lives here, in the one place a goal becomes a store path, and the
    fail-open wrapper turns it into a skipped write rather than a misfiled one."""
    d = _sdlc(tmp_path)
    with pytest.raises(ValueError):
        timing_store.goal_dir(d, timing_store.SESSIONS)
    assert timing_store.safe_append(d, timing_store.SESSIONS, "phase", "x", 1) is None
    assert not timing_store.store_dir(d).exists()


def test_a_traversal_shaped_session_id_is_refused(tmp_path):
    """The session id arrives from hook stdin — untrusted — and becomes a path component. It is
    checked with the SAME shared rule a goal stem is, not a second one that could drift."""
    d = _sdlc(tmp_path)
    for bad in ("../../etc", "a/b", "c:evil", ""):
        with pytest.raises(ValueError):
            timing_store.session_dir(d, bad)
    assert not timing_store.store_dir(d).exists()


def test_hook_lines_share_one_file_and_script_lines_get_their_own(tmp_path):
    """One session's hook events are sequential by construction, so a single `turns.jsonl` is
    safe and avoids one file per segment; the script floor CAN run concurrently with a hook, so it
    keeps the per-process file the goal layer already proved. Two writers, never one file."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "turn", "agrim-plan", 100, started=1000)
    timing_store.append_session(d, "s1", "turn", "agrim-implement", 200, started=2000)
    timing_store.append_session(d, "s1", "script", "loop", 300)

    names = sorted(p.name for p in timing_store.session_dir(d, "s1").glob("*.jsonl"))
    assert names == [f"{os.getpid()}.jsonl", "turns.jsonl"]
    assert len(timing_store.read_session(d, "s1")) == 3


def test_read_session_collapses_a_segment_recorded_twice(tmp_path):
    """A crash between "append the closed segment" and "write the new marker" re-closes the same
    segment on the next event, with the same `started`. The existing de-duplication absorbs it,
    shortest wins — the same rule the goal layer already proved."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "turn", "agrim-plan", 1238000, started=1789041912400)
    timing_store.append_session(d, "s1", "turn", "agrim-plan", 1240100, started=1789041912400)
    assert [e["ms"] for e in timing_store.read_session(d, "s1")] == [1238000]


def test_session_id_resolves_by_the_chain(monkeypatch):
    """Claude Code session first, the loop's own run id second, a dated shared bucket last — and
    never a fourth source. Each link tested with the ones above it absent."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cc-123")
    monkeypatch.setenv("SIGMA_RUN_ID", "run-9")
    assert timing_store.session_id() == "cc-123"

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert timing_store.session_id() == "run-9"

    monkeypatch.delenv("SIGMA_RUN_ID")
    assert timing_store.session_id(now=1_789_000_000) == "unattributed-2026-09-10"


def test_an_unsafe_session_id_from_the_environment_falls_through_the_chain(monkeypatch):
    """An id that would be refused as a path is treated as absent, not as a crash — the next link
    is used. A hook must never fail a turn because an env var held a slash."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "../escape")
    monkeypatch.setenv("SIGMA_RUN_ID", "run-9")
    assert timing_store.session_id() == "run-9"


# --------------------------------------------------------------------- the script-time floor (S5)

#: The thirteen scripts the loop, goal and front-half skills invoke that have a CLI entry.
#: `features.py` is library-only and deliberately absent. A structural pin, so a future edit
#: cannot drop one from the floor silently.
#: Paths are where the files LIVE, found by name — not the skill whose SKILL.md invokes them. The
#: first draft of this tuple placed seven of them under the invoking skill and none of those
#: paths existed; a `${CLAUDE_SKILL_DIR}` reference resolves to the invoking skill, a sibling
#: reference to another, and only `find` tells the truth.
_FLOOR_SCRIPTS = (
    "agrim-loop/scripts/loop.py", "agrim-loop/scripts/phase_report.py", "agrim-loop/scripts/work.py",
    "agrim-loop/scripts/discovery.py", "agrim-loop/scripts/north_star.py",
    "agrim-loop/scripts/review_context.py", "agrim-loop/scripts/reviewer.py",
    "agrim-model/scripts/predict.py", "agrim-dossier/scripts/dossier.py",
    "agrim-goal-design/scripts/goal_design.py", "agrim-define/scripts/define.py",
    "agrim-scope/scripts/brainstorm.py", "agrim-scope/scripts/compile_plan.py",
)

_PROBE_SCRIPT = """
import importlib.util, os, pathlib, sys, time
spec = importlib.util.spec_from_file_location("timing_store", sys.argv[1])
ts = importlib.util.module_from_spec(spec); spec.loader.exec_module(ts)

def main(argv):
    # The loop arms SIGMA_RUN_ID INSIDE its verbs, not at CLI entry (`_arm_run_id`), so the
    # session must be resolved after main returns, not before it runs.
    os.environ["SIGMA_RUN_ID"] = "run-armed-inside-main"
    time.sleep(0.02)
    return 0

sys.exit(ts.timed_main(main, sys.argv[2:], "probe"))
"""


def test_a_script_records_its_run_time_to_the_session_resolved_at_exit(tmp_path):
    """S5's acceptance criterion. A real subprocess, no session id in its environment at start;
    `main` arms the loop's run id while running, the way `loop.py` does. The floor line must land
    under THAT id, which is only possible if the session is resolved after `main` — resolved at
    entry it would land in `unattributed-<date>`."""
    d = _sdlc(tmp_path)
    script = tmp_path / "probe.py"
    script.write_text(_PROBE_SCRIPT)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CODE_SESSION_ID", "SIGMA_RUN_ID", "CLAUDE_PROJECT_DIR")}
    r = subprocess.run([sys.executable, str(script), str(S / "timing_store.py"), "verify", d, "482"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr

    lines = timing_store.read_session(d, "run-armed-inside-main")
    assert [(e["kind"], e["name"]) for e in lines] == [("script", "probe")]
    assert lines[0]["ms"] >= 20
    assert not timing_store.session_dir(d, timing_store.session_id()).exists()   # not the fallback


def test_timed_main_returns_mains_result_and_records_a_sys_exit_too(tmp_path, monkeypatch):
    """A script that ends with `sys.exit(3)` still ran; its exit code is re-raised untouched and
    its duration is recorded first."""
    d = _sdlc(tmp_path)
    monkeypatch.setenv("SIGMA_RUN_ID", "run-x")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    assert timing_store.timed_main(lambda argv: 7, ["verify", d], "alpha") == 7
    with pytest.raises(SystemExit) as raised:
        timing_store.timed_main(lambda argv: sys.exit(3), ["verify", d], "beta")
    assert raised.value.code == 3
    assert sorted(e["name"] for e in timing_store.read_session(d, "run-x")) == ["alpha", "beta"]


def test_timed_main_never_breaks_the_script_when_recording_fails(tmp_path, monkeypatch):
    d = _sdlc(tmp_path)
    monkeypatch.setenv("SIGMA_RUN_ID", "run-x")

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(timing_store, "append_session", boom)
    assert timing_store.timed_main(lambda argv: 0, ["verify", d], "gamma") == 0


def test_timed_main_records_nothing_when_no_sdlc_dir_can_be_found(tmp_path, monkeypatch):
    """The floor is host-agnostic, so it cannot assume an environment variable names the project.
    It looks for a `.sdlc` directory on the command line, then under `CLAUDE_PROJECT_DIR`, then
    under the working directory — and records nothing, silently, when none exists."""
    monkeypatch.setenv("SIGMA_RUN_ID", "run-x")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert timing_store.timed_main(lambda argv: 0, ["no", "sdlc", "here"], "delta") == 0
    assert not list(tmp_path.rglob("*.jsonl"))


def test_timed_main_records_nothing_for_a_help_request(tmp_path, monkeypatch):
    """`<script> --help` is not a script run worth timing (#2736): the guard inside `main`
    cannot stop `timed_main`'s post-`main` write, so the skip lives here, once, for every
    wrapper. Three argv shapes reach `timed_main` — `sys.argv`, `sys.argv[1:]` (reviewer),
    `sys.argv[2:]` (the probe above) — so "help" means: the argv's ONLY user argument is a help
    flag. A real run whose LAST word happens to be `--help` (`note <dir> <goal> --help`) still
    records — that is the positive control, and it proves the bound rather than assuming it."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("SIGMA_RUN_ID", "run-x")
    for argv in (["x.py", "--help"], ["x.py", "-h"], ["--help"], ["-h"]):
        assert timing_store.timed_main(lambda a: 0, argv, "eps") == 0
        assert not list(sdlc.iterdir()), f"{argv} recorded: {sorted(p.name for p in sdlc.iterdir())}"
    assert timing_store.timed_main(lambda a: 0, ["x.py", "note", "d", "g", "--help"], "eps") == 0
    assert [e["name"] for e in timing_store.read_session(str(sdlc), "run-x")] == ["eps"]


def _main_block_calls_timed_main(source):
    """True iff the `if __name__ == "__main__":` block contains a real call to `timed_main` — an
    AST walk, because a substring check is satisfied by a commented-out call (code review C7)."""
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_main = (isinstance(test, ast.Compare)
                   and isinstance(test.left, ast.Name) and test.left.id == "__name__"
                   and any(isinstance(c, ast.Constant) and c.value == "__main__"
                           for c in test.comparators))
        if not is_main:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                fn = inner.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
                if name == "timed_main":
                    return True
    return False


def test_the_enumerated_scripts_opt_in_to_the_floor():
    """Structural: each floor script wraps its CLI entry in `timed_main`. `agrim-plan` and
    `agrim-implement` invoke no scripts at all, so on a hookless host the floor is blind to them by
    construction — that limit is stated in the output, not fixed here."""
    root = S.parent.parent
    missing = [rel for rel in _FLOOR_SCRIPTS
               if not _main_block_calls_timed_main((root / rel).read_text(encoding="utf-8"))]
    assert missing == [], missing


def test_a_commented_out_opt_in_does_not_satisfy_the_pin():
    assert _main_block_calls_timed_main(
        'import sys\nif __name__ == "__main__":\n    sys.exit(ts.timed_main(main, sys.argv, "x"))\n')
    assert not _main_block_calls_timed_main(
        'import sys\nif __name__ == "__main__":\n    # sys.exit(ts.timed_main(main, sys.argv, "x"))\n    sys.exit(main(sys.argv))\n')


def test_timed_main_records_the_floor_even_when_main_raises(tmp_path, monkeypatch):
    """A script that crashes still ran and still took time (code review C4). The exception
    propagates untouched; the floor line is written on the way out."""
    d = _sdlc(tmp_path)
    monkeypatch.setenv("SIGMA_RUN_ID", "run-crash")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    def crash(argv):
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        timing_store.timed_main(crash, ["verify", d], "crasher")
    assert [e["name"] for e in timing_store.read_session(d, "run-crash")] == ["crasher"]


def test_a_future_dated_stamp_does_not_suppress_the_sweep(tmp_path):
    """A corrected clock or a restored backup can leave the stamp in the future; treating that as
    "recently swept" would suppress retention indefinitely (code review C3). A stamp not in the
    past is not fresh."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "old", "turn", "agrim-plan", 1000, started=1)
    for f in timing_store.session_files(d, "old"):
        _age(f, timing_store.RETENTION_DAYS + 5)
    stamp = timing_store.store_dir(d) / ".last-prune"
    stamp.write_text("x")
    future = time.time() + 10 * 86400
    os.utime(stamp, (future, future))
    assert timing_store.maybe_prune(d) == ["_sessions/old"]


def test_a_json_valid_line_with_an_unhashable_key_is_skipped_not_raised(tmp_path):
    """"Malformed lines are skipped, never raised" must hold for JSON that parses but carries an
    unusable key (code review C9): `started: [1]` is unhashable and would raise inside
    de-duplication, taking the reader down for the whole goal."""
    d = _sdlc(tmp_path)
    timing_store.append(d, "482", "phase", "implement", 1000, started=1)
    path = timing_store.goal_files(d, "482")[0]
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "phase", "name": "x", "started": [1], "ms": 5}) + "\n")
        f.write(json.dumps({"kind": "phase", "name": ["x"], "started": 2, "ms": 5}) + "\n")
    assert [e["ms"] for e in timing_store.read_goal(d, "482")] == [1000]
    assert timing_store.totals(d, "482")["active_ms"] == 1000


def test_a_unit_with_goals_but_no_intervals_still_reports_its_goal_count(tmp_path):
    """"0 goals in this repo" above "none of this unit's goals in this repo have captured time"
    contradicts itself (code review N2). The count is of goals, not of recorded goals."""
    d = _sdlc(tmp_path)
    _unit(d, "quiet", {"acme/app": {"branch": "f", "owner": None, "authorized": True,
                                    "goals": [700, 701]}})
    u = timing_store.unit_totals(d, "quiet", repo="acme/app")
    assert u["recorded"] is False and u["counted_goals"] == 2


# --------------------------------------------------------------------- session totals (S6)


def test_session_totals_group_turns_by_skill_and_keep_scripts_separate(tmp_path):
    """Turn time is the total; script time sits beside it and is NEVER added — scripts run inside
    turns, so their sum would double-count exactly as `effort` does, and here nobody asked for
    that arithmetic."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "turn", "agrim-implement", 6_000_000, started=1)
    timing_store.append_session(d, "s1", "turn", "agrim-plan", 1_200_000, started=2)
    timing_store.append_session(d, "s1", "turn", "agrim-implement", 100_000, started=3)
    timing_store.append_session(d, "s1", "turn", "(none)", 2_000, started=4)
    timing_store.append_session(d, "s1", "script", "loop", 252_000)
    timing_store.append_session(d, "s1", "script", "loop", 228_000)

    t = timing_store.session_totals(d, "s1")
    assert t["recorded"] is True and t["precise"] is True
    assert t["turn_ms"] == 7_302_000 and t["segments"] == 4
    assert list(t["by_skill"].items()) == [("agrim-implement", 6_100_000), ("agrim-plan", 1_200_000),
                                           ("(none)", 2_000)]            # descending, stable
    assert t["script_ms"] == 480_000 and t["script_runs"] == 2


def test_session_totals_for_a_floor_only_session_have_no_turn_time(tmp_path):
    """Scripts only — a hookless host. `precise` is False and `turn_ms` is None, not zero: turn
    time was not observable, which is a different statement from "took no time"."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "s1", "script", "loop", 252_000)
    t = timing_store.session_totals(d, "s1")
    assert t["recorded"] is True and t["precise"] is False
    assert t["turn_ms"] is None and t["segments"] == 0
    assert t["script_ms"] == 252_000


def test_session_totals_for_nothing_recorded_are_absent(tmp_path):
    t = timing_store.session_totals(_sdlc(tmp_path), "never")
    assert t["recorded"] is False and t["turn_ms"] is None and t["script_ms"] is None


# --------------------------------------------------------------------- retention over sessions (S7)


def test_a_stale_session_is_pruned_goal_dirs_are_untouched_and_the_sweep_is_throttled(tmp_path):
    """S7's acceptance criterion, three properties in one sequence:
      - a session whose last activity is outside the window is removed, by the same
        most-recent-file rule a goal gets;
      - a goal directory of the same age is handled by the goal pass, never by the sessions
        pass, and a FRESH goal is untouched by either;
      - the sweep is throttled: within the window a second call does one `stat` and no sweep."""
    d = _sdlc(tmp_path)
    now = time.time()      # real time, so the aged fixtures and the cutoff mean what they say
    timing_store.append_session(d, "old-session", "turn", "agrim-plan", 1000, started=1)
    for f in timing_store.session_files(d, "old-session"):
        _age(f, timing_store.RETENTION_DAYS + 5)
    timing_store.append(d, "fresh-goal", "phase", "implement", 1000, started=1)

    removed = timing_store.prune(d, now=time.time())
    assert removed == ["_sessions/old-session"]
    assert not timing_store.session_dir(d, "old-session").exists()
    assert len(timing_store.read_goal(d, "fresh-goal")) == 1

    # Throttle: the first maybe_prune sweeps and stamps; a second within the window does not
    # sweep even though a new stale session has appeared.
    assert timing_store.maybe_prune(d, now=now) is not None
    timing_store.append_session(d, "old-2", "turn", "agrim-plan", 1000, started=1)
    for f in timing_store.session_files(d, "old-2"):
        _age(f, timing_store.RETENTION_DAYS + 5)
    assert timing_store.maybe_prune(d, now=now + 3600) is None
    assert timing_store.session_dir(d, "old-2").exists()
    # …and once the window has passed, it sweeps again.
    assert timing_store.maybe_prune(d, now=now + 25 * 3600) == ["_sessions/old-2"]


def test_the_goal_pass_skips_the_sessions_subtree_explicitly(tmp_path):
    """Today the goal pass leaves `_sessions/` alone only because it finds no `*.jsonl` directly
    under it — an incidental effect. Pinned as a rule: a stray stale file placed directly in
    `_sessions/` (a malformed layout) is deleted by neither pass, because it is neither a goal
    directory nor a session directory."""
    d = _sdlc(tmp_path)
    stray = timing_store.store_dir(d) / timing_store.SESSIONS / "stray.jsonl"
    stray.parent.mkdir(parents=True)
    stray.write_text("{}\n")
    _age(stray, timing_store.RETENTION_DAYS + 5)
    assert timing_store.prune(d) == []
    assert stray.exists()


def test_the_hook_path_prunes_through_the_throttle_not_once_per_process(tmp_path, monkeypatch):
    """Every hook invocation is a fresh process, so "once per process" would be "on every event" —
    measured at up to 553 ms per sweep on a synthetic store. The hook path goes through the stamp
    instead: with a fresh stamp present, a session append does not sweep."""
    d = _sdlc(tmp_path)
    timing_store.append_session(d, "old", "turn", "agrim-plan", 1000, started=1)
    for f in timing_store.session_files(d, "old"):
        _age(f, timing_store.RETENTION_DAYS + 5)
    timing_store.maybe_prune(d)                          # sweeps `old`, writes the stamp
    assert not timing_store.session_dir(d, "old").exists()

    timing_store.append_session(d, "old-again", "turn", "agrim-plan", 1000, started=1)
    for f in timing_store.session_files(d, "old-again"):
        _age(f, timing_store.RETENTION_DAYS + 5)
    monkeypatch.setattr(timing_store, "_pruned_this_process", False)
    timing_store.append_session(d, "live", "turn", "agrim-plan", 5, started=2)   # a hook event
    assert timing_store.session_dir(d, "old-again").exists()     # stamp fresh → no sweep


# --------------------------------------------------------------------- the local validator (S9)


def _body_without_docstring(fn):
    """The executable statements of `fn`, as AST dumps — docstrings and comments may differ
    between the original and its copy; the rule must not."""
    import ast, inspect, textwrap
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    stmts = node.body
    if (stmts and isinstance(stmts[0], ast.Expr)
            and isinstance(getattr(stmts[0], "value", None), ast.Constant)
            and isinstance(stmts[0].value.value, str)):
        stmts = stmts[1:]
    return [ast.dump(s) for s in stmts]


def test_the_local_validator_is_byte_identical_to_states():
    """S9's acceptance criterion. `state.py` sanctions exactly one local copy of
    `unsafe_goal_reason` — `agrim-log`'s, kept byte-identical by discipline alone. This is the
    second, with the same justification (the hook loads this module on every turn boundary, and
    `state.py`'s import is ~0.2 s of it) and a stronger guarantee: the two bodies are compared
    statement for statement, so a change to either without the other fails here."""
    state = _mod("state")
    assert _body_without_docstring(timing_store._unsafe_stem_reason) == \
        _body_without_docstring(state.unsafe_goal_reason)


def test_the_local_validator_behaves_like_the_original():
    """The behavioural half, mirroring `test_log.py`'s own checks for its copy."""
    for bad in ("../../../SECRET", "goals/nested", "c:evil", "a\\b"):
        assert timing_store._unsafe_stem_reason(bad) is not None
    for ok in ("158", "0007-cache", "4f2a91", "unattributed-2026-09-10"):
        assert timing_store._unsafe_stem_reason(ok) is None


def test_loading_the_store_imports_neither_state_nor_subprocess():
    """The cost claim in its testable form. With `state` gone and `work` lazy, loading the store
    for a hook write pulls in nothing beyond the standard-library modules it names itself — and
    in particular not `subprocess`, which both `state.py` and `work.py` import. Fresh interpreter,
    so this process's own imports cannot leak in."""
    probe = (
        "import importlib.util, sys, pathlib, tempfile\n"
        f"p = pathlib.Path({str(S / 'timing_store.py')!r})\n"
        "s = importlib.util.spec_from_file_location('timing_store', p)\n"
        "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
        "m.append_session(tempfile.mkdtemp(), 'probe', 'turn', 'agrim-plan', 5, started=1)\n"
        "print('state-attr', 'state' in vars(m))\n"
        "print('subprocess-imported', 'subprocess' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "state-attr False" in out.stdout
    assert "subprocess-imported False" in out.stdout


# --------------------------------------------------------------------- security review (2026-09-11)


def _link_dir(target, link):
    """A directory link the OS will let this test create: a symlink, else a Windows junction, else
    skip — the finding depends on a real link, and a mocked one would prove nothing."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    except Exception:                                       # noqa: BLE001 - skip, not fail
        pytest.skip("neither symlink nor junction creation is available on this host")


def _link_file(target, link):
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("file symlink creation is not available on this host")


def _victim(tmp_path):
    """A directory OUTSIDE the store holding an old `*.jsonl` and an unrelated file — what a
    link-following sweep or write would destroy."""
    v = tmp_path / "victim"
    v.mkdir()
    (v / "ledger-2026-01.jsonl").write_text("precious\n")
    (v / "notes.txt").write_text("precious\n")
    for f in v.iterdir():
        _age(f, timing_store.RETENTION_DAYS + 200)
    return v


def test_the_sweep_never_follows_a_linked_goal_directory_out_of_the_store(tmp_path):
    """Security S1, HIGH. A link planted at `time/<name>` pointing outside the store — committable
    on macOS/Linux, a junction on Windows — made `prune` delete the target's `*.jsonl` and report
    it as a swept goal. Reproduced end to end from the hook, exit 0, stderr empty."""
    d = _sdlc(tmp_path)
    victim = _victim(tmp_path)
    timing_store.store_dir(d).mkdir(parents=True)
    _link_dir(victim, timing_store.store_dir(d) / "planted")

    removed = timing_store.prune(d)
    assert "planted" not in removed
    assert (victim / "ledger-2026-01.jsonl").read_text() == "precious\n"
    assert (victim / "notes.txt").read_text() == "precious\n"


def test_the_sweep_never_follows_a_linked_session_directory(tmp_path):
    d = _sdlc(tmp_path)
    victim = _victim(tmp_path)
    sessions = timing_store.store_dir(d) / timing_store.SESSIONS
    sessions.mkdir(parents=True)
    _link_dir(victim, sessions / "sess-link")

    removed = timing_store.prune(d)
    assert removed == []
    assert (victim / "ledger-2026-01.jsonl").read_text() == "precious\n"


def test_the_sweep_never_follows_a_linked_store_root(tmp_path):
    """Probe D: `time/` itself a link to a directory of victims."""
    d = _sdlc(tmp_path)
    victim_root = tmp_path / "victim-root"
    (victim_root / "goal-a").mkdir(parents=True)
    old = victim_root / "goal-a" / "1.jsonl"
    old.write_text("precious\n")
    _age(old, timing_store.RETENTION_DAYS + 200)
    (timing_store.store_dir(d).parent).mkdir(parents=True, exist_ok=True)
    _link_dir(victim_root, timing_store.store_dir(d))

    assert timing_store.prune(d) == []
    assert old.read_text() == "precious\n"


def test_the_stamp_is_never_written_through_a_link(tmp_path):
    """Probe E: `.last-prune -> notes.txt` had `notes.txt`'s content replaced with an integer."""
    d = _sdlc(tmp_path)
    victim = _victim(tmp_path)
    timing_store.store_dir(d).mkdir(parents=True)
    _link_file(victim / "notes.txt", timing_store.store_dir(d) / ".last-prune")

    timing_store.maybe_prune(d)
    assert (victim / "notes.txt").read_text() == "precious\n"


def test_an_append_never_follows_a_linked_file(tmp_path):
    """`open(path, "a")` follows a link. A `turns.jsonl` or `<pid>.jsonl` that is a link must be
    refused, not appended through — and the fail-open wrapper turns that into a skipped line."""
    d = _sdlc(tmp_path)
    victim = _victim(tmp_path)
    sdir = timing_store.session_dir(d, "s1")
    sdir.mkdir(parents=True)
    _link_file(victim / "notes.txt", sdir / timing_store.TURNS_FILE)

    with pytest.raises(ValueError):
        timing_store.append_session(d, "s1", "turn", "agrim-plan", 5, started=1)
    assert (victim / "notes.txt").read_text() == "precious\n"


def test_empty_and_dot_identifiers_are_refused_at_both_chokepoints(tmp_path, monkeypatch):
    """Security S5. The shared validator refuses separators and `..` but admits `""` and `.`;
    pathlib collapses `store / "."` to the store root, so those files land where the sweep never
    looks. Refused here, beside the validator, so the byte-identical copy stays byte-identical."""
    d = _sdlc(tmp_path)
    for bad in ("", ".", " ", "..md"):
        with pytest.raises(ValueError):
            timing_store.goal_dir(d, bad)
    for bad in (".", " . "):
        with pytest.raises(ValueError):
            timing_store.session_dir(d, bad)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ".")
    monkeypatch.setenv("SIGMA_RUN_ID", "run-9")
    assert timing_store.session_id() == "run-9"
    assert not timing_store.store_dir(d).exists()


def test_the_reserved_name_is_refused_case_insensitively(tmp_path):
    """Security S6. On a case-insensitive filesystem `_SESSIONS` and `_sessions` are one
    directory; a goal named either must not reach the sessions root."""
    d = _sdlc(tmp_path)
    for name in ("_SESSIONS", "_Sessions", "_sessions"):
        with pytest.raises(ValueError):
            timing_store.goal_dir(d, name)


def test_loading_the_store_does_not_load_work_until_a_goal_path_is_needed():
    """The hook path's cost, pinned as a property: eagerly loading `work.py` cost 148 ms of
    interpreter start on this host against 61 ms bare, and the session path never needs
    `work.stem`. So `work` stays unloaded until `goal_dir` runs — and a session write must not
    trigger it. (`sys.modules` cannot observe this: `_load` never registers the module there, and
    `state`, which IS required, imports `subprocess` itself.) Run in a fresh interpreter so this
    process's own earlier goal calls cannot leak in."""
    probe = (
        "import importlib.util, sys, pathlib\n"
        f"p = pathlib.Path({str(S / 'timing_store.py')!r})\n"
        "s = importlib.util.spec_from_file_location('timing_store', p)\n"
        "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
        "print('after-load', m._work is None)\n"
        "m.append_session(sys.argv[1], 'probe', 'turn', 'agrim-plan', 5, started=1)\n"
        "print('after-session-write', m._work is None)\n"
        "m.goal_dir(sys.argv[1], '482')\n"
        "print('after-goal_dir', m._work is None)\n"
    )
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run([sys.executable, "-c", probe, tmp], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "after-load True" in out.stdout
    assert "after-session-write True" in out.stdout      # a hook write never pays for `work`
    assert "after-goal_dir False" in out.stdout
