# SPDX-License-Identifier: MIT
"""A hand-typed ledger write has to reach the team, and has to say so when it does not (#1599).

THE DEFECT. Publish was automatic on exactly ONE path: `watch.sh` runs `sync.py publish` every
tick, and `loop.py` starts that watcher on every loop trigger. Every write path OUTSIDE the loop --
`ledger.py append`, `handoff.py open`/`track`/`ack`, which are precisely the commands
`/agrim-ledger` tells a person to run by hand -- wrote to disk and stopped there. No publish, no
watcher, and not one word saying so. On a machine that was not concurrently looping, a note, a
hand-off or an ack was invisible to the team indefinitely; one adopter machine held 67% of its
ledger history locally for two and a half weeks while every surface read as healthy.

WHY THIS PUBLISHES RATHER THAN STARTING A WATCHER. Two precedents decide it, and neither is
"interactive commands must not touch the network" -- `handoff.py open` already creates a GitHub
issue, so the kit plainly does not hold that rule. `cheap_only` (setup_wizard.py) draws the line at
CONSENT, not at cost: the wizard's unconditional SessionStart hook takes the cheap subset while
`/agrim-doctor`, "which the user typed on purpose", keeps the full sweep including the network. A
`/agrim-ledger` write is the typed-on-purpose half, and being seen is the entire point of it.
`discovery.reconcile.mode` draws the other line at UNATTENDED writes -- default off, because "a
typo must never switch on a mechanism that WRITES" while nobody is watching. A person typing a note
is attended by construction. Starting the watcher instead would fail both tests at once: it enrols
the machine in a recurring background job -- fetches, `gh` reads in agent_watch/comment_watch, a
webhook post -- every 15 minutes forever, off one typed command. A single bounded push finishes; a
daemon does not.

AND WHY IT DEFERS TO A LIVE WATCHER. Not cost -- correctness. `watch.sh` holds a mutex precisely so
two publishers never contend on the ledger worktree's git index lock. Publishing from the CLI
behind a live watcher's back is exactly the second publisher that mutex exists to exclude, so when
the heartbeat is fresh the write says which tick will carry it instead of racing it.

Every one of these behaviours is a guard whose removal must change what the operator SEES, so each
test is named for the behaviour it scores rather than for the function it calls.
"""
import importlib.util
import json
import os
import pathlib
import subprocess

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sync = _mod("sync")
ledger = _mod("ledger")

ON = {"ledger": {"enabled": True, "actor": "dana"}}


def _sdlc(root, config=None):
    base = root / ".sdlc"
    (base / "state").mkdir(parents=True, exist_ok=True)
    (base / "config.json").write_text(json.dumps(config or ON), encoding="utf-8")
    return base


# --------------------------------------------------------------------- real git ledger fixture


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr or proc.stdout}"
    return proc.stdout.strip()


@pytest.fixture
def clone(tmp_path):
    """A REAL repo with a REAL bare origin and a REAL ledger worktree on the ops branch.

    Stubbing git here would settle nothing: every claim under test is a claim about what git
    reports (an untracked entry file, a commit ahead of the remote ref, a missing remote-tracking
    ref), and a stub would only ever confirm the shape this file already assumes. Same reasoning
    `test_work.py`'s own `test_real_git_*` cases give for their fixture."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / ".gitignore").write_text(".sdlc/\n", encoding="utf-8")
    _git(repo, "add", "--", ".gitignore")
    _git(repo, "commit", "-q", "-m", "base")
    base = _sdlc(repo)
    return repo, base


def _bootstrap(base):
    out = sync.bootstrap(base, ON)
    assert "published" in out, out
    return out


def _note(base, why="a note"):
    return ledger.append(base, ON, "note", "g.md", why=why)


# --------------------------------------------------------------------- unpublished(): what git says


def test_a_written_but_unpushed_entry_is_reported_as_unpublished(clone):
    """The field failure, reproduced end to end: an entry written with no watcher running is
    invisible to the team, and `unpublished()` is what makes it nameable."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    state = sync.unpublished(base, ON)
    assert state is not None
    assert state["files"] == 1, state
    assert state["unpushed_commits"] == 0, state


def test_a_published_ledger_reports_nothing_unpublished(clone):
    """The control. A row that cannot go quiet when everything is fine is a row that gets ignored
    along with its true positives."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    assert sync.publish(base, ON) == "published"
    state = sync.unpublished(base, ON)
    assert state["files"] == 0 and state["unpushed_commits"] == 0, state


def test_a_commit_that_never_reached_the_remote_is_counted_separately(clone):
    """`publish()` commits before it pushes and reports "publish deferred" when the push fails, so
    a failed push leaves a LOCAL COMMIT, not a dirty file. Counting only the dirty files would
    report that clone as fully published."""
    repo, base = clone
    _bootstrap(base)
    _note(base)
    wt = sync.worktree(base)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: local only")
    state = sync.unpublished(base, ON)
    assert state["files"] == 0, state
    assert state["unpushed_commits"] == 1, state


def test_the_oldest_unpublished_write_carries_its_age(clone):
    """"How long has the team been missing this?" is the question the field report was actually
    about (two and a half weeks). A count alone cannot answer it."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    pending = next(iter(sync.worktree(base).glob("entries/*.jsonl")))
    os.utime(pending, (1000.0, 1000.0))
    state = sync.unpublished(base, ON, now=1000.0 + 7200)
    assert state["oldest_age_seconds"] == pytest.approx(7200, abs=1), state


def test_an_unanswerable_git_reads_as_unknown_never_as_all_clear(tmp_path):
    """The `_secret_file_coverage` discipline: "we could not look" and "we looked and found
    nothing" are different answers, and reporting the first as the second is the worst of the
    three outcomes."""
    base = _sdlc(tmp_path)
    (base / "ledger").mkdir()
    (base / "ledger" / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")   # looks like a
    assert sync.unpublished(base, ON) is None                                      # worktree, is not


def test_a_ledger_that_is_not_a_worktree_reads_as_unknown(tmp_path):
    """A plain `.sdlc/ledger` directory has no published side to compare against at all -- the
    #1391 LOCAL-ONLY state, which the row above it already names."""
    base = _sdlc(tmp_path)
    (base / "ledger" / "entries").mkdir(parents=True)
    assert sync.unpublished(base, ON) is None


def test_a_missing_remote_ref_leaves_the_commit_count_unknown_not_zero(clone):
    """A clone that has never pushed has no `origin/sdlc-ledger` to compare against. Reading that
    as "0 commits behind" would turn the never-published case into the healthiest-looking one."""
    _repo, base = clone
    sync.init(base, ON)                       # init only: nothing is ever pushed
    _note(base)
    state = sync.unpublished(base, ON)
    assert state["unpushed_commits"] is None, state


# --------------------------------------------------------------------- pending_entry_count(): the
# real unit `_ensure_ledger_delivery` (#2393) escalates on. `unpublished()`'s own `files` count is
# the wrong one for a THRESHOLD -- one file can hold one entry or five hundred, so two clones with
# an identical `files` count can be nowhere near equally behind.


def test_pending_entry_count_sums_real_lines_across_dirty_files(clone):
    """The metric `unpublished()`'s own `files` count cannot give: three notes in one still-dirty
    file is a backlog of 3, not 1."""
    _repo, base = clone
    _bootstrap(base)
    _note(base, why="n1")
    _note(base, why="n2")
    _note(base, why="n3")
    assert sync.pending_entry_count(base, ON) == 3


def test_pending_entry_count_sums_across_multiple_pending_files(clone):
    """Real field shape: more than one actor/host/pid file can be dirty at once (#540's own
    single-writer-per-file design), and every one of them counts."""
    _repo, base = clone
    _bootstrap(base)
    _note(base, why="mine")
    other = sync.worktree(base) / ledger.ENTRIES / "rae-hostb.77.jsonl"
    other.write_text(
        '{"id":"rae-hostb.77:1","ts":"2026-01-01T00:00:00Z","actor":"rae","kind":"note","goal":"g.md"}\n'
        '{"id":"rae-hostb.77:2","ts":"2026-01-01T00:00:01Z","actor":"rae","kind":"note","goal":"g.md"}\n',
        encoding="utf-8")
    assert sync.pending_entry_count(base, ON) == 3


def test_pending_entry_count_also_counts_committed_but_unpushed_lines(clone):
    """Plan-review finding 1, the metric's whole reason for existing: `publish()` commits before
    it pushes, so a failed push leaves a CLEAN working tree with the commit still unreachable --
    invisible to a dirty-files-only count. The dirty count is 0 here; the real backlog is 1."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    assert sync.publish(base, ON) == "published"
    _note(base, why="stuck")
    wt = sync.worktree(base)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: local only")
    state = sync.unpublished(base, ON)
    assert state["files"] == 0 and state["unpushed_commits"] == 1, state    # the blind spot
    assert sync.pending_entry_count(base, ON) == 1


def test_pending_entry_count_combines_both_dimensions_at_once(clone):
    """Neither half alone is the real number -- a clone can be behind BOTH ways simultaneously."""
    _repo, base = clone
    _bootstrap(base)
    _note(base, why="committed-but-unpushed")
    wt = sync.worktree(base)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "ledger: local only")
    _note(base, why="still-dirty")
    assert sync.pending_entry_count(base, ON) == 2


def test_pending_entry_count_is_none_when_not_a_worktree(tmp_path):
    """Mirrors `unpublished()`'s own contract exactly: a plain `.sdlc/ledger` directory has no
    published side to compare against, so this reads as "we did not look", never as zero."""
    base = _sdlc(tmp_path)
    (base / "ledger" / "entries").mkdir(parents=True)
    assert sync.pending_entry_count(base, ON) is None


def test_pending_entry_count_is_none_on_an_unanswerable_git(tmp_path):
    """The `_secret_file_coverage` discipline, same as `unpublished()`'s own copy: a worktree that
    LOOKS real but cannot answer `git status` reads as unknown, not as an all-clear."""
    base = _sdlc(tmp_path)
    (base / "ledger").mkdir()
    (base / "ledger" / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
    assert sync.pending_entry_count(base, ON) is None


def test_pending_entry_count_treats_a_missing_remote_ref_as_zero_for_that_half_only(clone):
    """A clone that cannot see `origin/sdlc-ledger` at all -- never fetched, or (as reproduced
    here) fetched once and then lost the ref -- has no baseline to diff the committed-but-unpushed
    half against. That half is uncountable, but the real, measured NOT-YET-COMMITTED backlog must
    still be reported rather than the whole function going dark (unlike `unpublished()`'s own
    `unpushed_commits`, which is allowed to answer `None` for just that one field)."""
    _repo, base = clone
    _bootstrap(base)
    wt = sync.worktree(base)
    _git(wt, "update-ref", "-d", f"refs/remotes/{sync.remote(ON)}/{sync.branch(ON)}")
    _note(base, why="stuck")
    assert sync.pending_entry_count(base, ON) == 1


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads mode-000 files, so the unreadable-file case cannot arise")
def test_pending_entry_count_never_raises_on_an_unreadable_pending_file(clone):
    """Fail-open, matching every other reader on this path: a pending file this process cannot
    open contributes 0 to the sum rather than taking down the whole count. Uses a file that was
    NEVER `git add`ed (a second actor's, written directly) -- an already-published file that is
    merely modified is read via `git diff`, not Python's own file I/O, so only a genuinely
    untracked file exercises this fallback."""
    _repo, base = clone
    _bootstrap(base)
    pending = sync.worktree(base) / ledger.ENTRIES / "rae-hostb.77.jsonl"
    pending.write_text(
        '{"id":"rae-hostb.77:1","ts":"2026-01-01T00:00:00Z","actor":"rae","kind":"note","goal":"g.md"}\n',
        encoding="utf-8")
    os.chmod(pending, 0o000)
    try:
        assert sync.pending_entry_count(base, ON) == 0
    finally:
        os.chmod(pending, 0o644)


# --------------------------------------------------------------------- watcher liveness


def test_a_watcher_that_stopped_ticking_reads_as_stale(tmp_path):
    """#1227's own lesson, one level up: a dead watcher leaves its markers behind, so only the
    heartbeat's AGE tells the difference. Same age-not-error rule every daemon-liveness row
    in this project follows."""
    base = _sdlc(tmp_path)
    hb = base / "state" / "watch.heartbeat"
    hb.write_text("", encoding="utf-8")
    os.utime(hb, (1000.0, 1000.0))
    assert sync.watcher_liveness(base, ON, now=1000.0 + 10)[0] == "live"
    assert sync.watcher_liveness(base, ON, now=1000.0 + 86400)[0] == "stale"


def test_the_staleness_bound_follows_the_configured_tick_interval(tmp_path):
    """watch.sh derives its own bound from the interval (3x, floored at 180s). A fixed bound here
    would call a slow-but-healthy watcher dead on a long interval, and a genuinely dead one live on
    a short one."""
    base = _sdlc(tmp_path)
    assert sync.watcher_stale_after_seconds({"ledger": {"watch": {"interval_seconds": 3600}}}) == 10800
    assert sync.watcher_stale_after_seconds({"ledger": {"watch": {"interval_seconds": 5}}}) == 180
    assert sync.watcher_stale_after_seconds({}) == 2700


def test_a_malformed_interval_falls_back_to_the_shipped_default(tmp_path):
    """Same fail-open direction every other config reader in the kit takes: a hand-edit typo must
    degrade to the default, never crash the one command someone runs BECAUSE their config is wrong."""
    for bad in ("soon", None, 0, -5, {"nested": 1}):
        assert sync.watch_interval_seconds({"ledger": {"watch": {"interval_seconds": bad}}}) == 900
    assert sync.watch_interval_seconds({"ledger": {"watch": "every so often"}}) == 900


def test_no_heartbeat_at_all_reads_as_absent_not_live(tmp_path):
    base = _sdlc(tmp_path)
    assert sync.watcher_liveness(base, ON) == ("absent", None)


def test_an_age_is_rendered_at_the_grain_a_reader_can_act_on(tmp_path):
    """These strings are the whole answer to "how long has the team been missing this?", so the
    banding is behaviour, not formatting: seconds while it is a blip, minutes while it is a tick or
    two, hours once it is the two-and-a-half-week class of problem this issue is about."""
    assert sync._ago(12) == "12s ago"
    assert sync._ago(600) == "10 min ago"
    assert sync._ago(7200) == "2.0h ago"
    assert sync._ago(None) == "unknown"


# --------------------------------------------------------------------- publish_after_write


def test_a_hand_typed_write_publishes_itself_when_nothing_else_will(clone):
    """The fix. No watcher is running, so the write's own command is the only thing that can carry
    it to the team -- and it does, rather than leaving it for a loop trigger that may never come."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    said = sync.publish_after_write(base, ON)
    assert "published" in said and "NOT published" not in said, said
    assert sync.unpublished(base, ON)["files"] == 0


def test_it_defers_to_a_live_watcher_instead_of_racing_it_for_the_index_lock(clone):
    """watch.sh serialises its own publishes behind a mutex for exactly this reason. A CLI publish
    fired behind a live watcher's back is the second publisher that mutex exists to exclude -- so a
    fresh heartbeat means NAME the tick that will carry it, do not race it."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    (base / "state" / "watch.heartbeat").write_text("", encoding="utf-8")
    calls = []
    said = sync.publish_after_write(base, ON, publish_fn=lambda *a, **k: calls.append(a))
    assert calls == [], "published behind a live watcher's back"
    assert "watcher" in said and "not published yet" in said.lower(), said


def test_a_stale_heartbeat_publishes_rather_than_trusting_a_dead_watcher(clone):
    """The whole bug in one case: the leftover markers of a watcher that died weeks ago must not
    be read as "something else will handle it"."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    hb = base / "state" / "watch.heartbeat"
    hb.write_text("", encoding="utf-8")
    os.utime(hb, (1000.0, 1000.0))
    said = sync.publish_after_write(base, ON)
    assert "published" in said and "NOT published" not in said, said
    assert sync.unpublished(base, ON)["files"] == 0


def test_a_ledger_that_was_never_bootstrapped_is_told_so_rather_than_pushed(tmp_path):
    """The #1391 LOCAL-ONLY shape. There is nothing to push to, so the honest output is the one
    command that fixes it -- not a failed push, and above all not silence."""
    base = _sdlc(tmp_path)
    (base / "ledger" / "entries").mkdir(parents=True)
    said = sync.publish_after_write(base, ON)
    assert "LOCALLY ONLY" in said and "/agrim-ledger" in said, said


def test_a_failed_publish_says_so_instead_of_failing_silently(clone):
    """The failure this issue is about is SILENCE, not the failure itself. A push that cannot land
    is fine; a push that cannot land and says nothing is how 2.5 weeks of history went missing."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)
    said = sync.publish_after_write(
        base, ON, publish_fn=lambda *a, **k: "publish deferred (will retry next tick): no route")
    assert "NOT published" in said and "no route" in said, said


def test_a_publish_that_raises_never_escapes_into_the_caller(clone):
    """The entry is already on disk when this runs. A network problem must not turn a successful
    write into a traceback or a non-zero exit -- the same fail-open posture `_ensure_watcher` and
    `safe_append` already take."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)

    def boom(*a, **k):
        raise RuntimeError("origin unreachable")

    said = sync.publish_after_write(base, ON, publish_fn=boom)
    assert "NOT published" in said and "origin unreachable" in said, said


def test_a_publish_whose_commit_is_rejected_by_a_host_hook_returns_a_status_not_a_raise(clone):
    """The core bug (#2449): sync.publish()'s own git commit call is unguarded, so a host
    pre-commit hook's rejection escapes as a RuntimeError instead of the status string every other
    branch of this function returns. `run=` delegates to the real _run_git for every call except
    commit, so the rest of publish()'s real code path (add, diff --cached) still executes for real."""
    _repo, base = clone
    _bootstrap(base)
    _note(base)

    def hook_rejects_commit(cwd, args):
        if args and args[0] == "commit":
            raise RuntimeError("git commit: hook rejected -- no .pre-commit-config.yaml file was found")
        return sync._run_git(cwd, args)

    result = sync.publish(base, ON, run=hook_rejects_commit)      # must not raise
    assert isinstance(result, str)
    assert "publish deferred" in result
    assert "hook rejected" in result


def test_a_real_host_precommit_hook_cannot_block_or_crash_a_ledger_publish(clone):
    """Full-stack proof, no injected run=: a REAL rejecting pre-commit hook on the HOST repo (the
    ledger worktree shares its .git/hooks with the main checkout -- #2449's L1) must never even run
    against a ledger commit once hooks are disabled for ledger git operations, and if it somehow
    did, publish() must still return a status string, never raise."""
    repo, base = clone
    _bootstrap(base)
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _note(base)
    result = sync.publish(base, ON)            # must not raise
    assert result == "published", result       # the hook never ran
    # The override is invocation-scoped only (#2449 plan-review round 1's hook-revival worry) --
    # confirm it left no trace in the worktree's own config for any later invocation to inherit.
    hooks_path = subprocess.run(
        ["git", "-C", str(sync.worktree(base)), "config", "core.hooksPath"],
        capture_output=True, text=True)
    assert hooks_path.returncode != 0 or not hooks_path.stdout.strip(), hooks_path.stdout


def test_bootstrap_does_not_crash_when_the_underlying_commit_is_rejected(tmp_path):
    """bootstrap() calls publish() unguarded at sync.py:291 -- a second real caller exposed to the
    exact same raise, not named in the original issue (BR-4)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "remote", "add", "origin", str(origin))
    base = _sdlc(repo)

    def hook_rejects_commit(cwd, args):
        if args and args[0] == "commit":
            raise RuntimeError("git commit: hook rejected")
        return sync._run_git(cwd, args)

    result = sync.bootstrap(base, ON, run=hook_rejects_commit)    # must not raise
    assert isinstance(result, str)
    assert "publish deferred" in result


def test_an_off_ledger_says_nothing_at_all(tmp_path):
    off = {"ledger": {"enabled": False}}
    assert sync.publish_after_write(_sdlc(tmp_path, off), off) is None


def test_the_operator_can_decline_the_network_write(clone):
    """`ledger.publish_on_write: false`. The two precedents this design is argued against are both
    ultimately about the operator KEEPING THE CHOICE, so declining has to be expressible -- and
    declining is then silent, because a setting you turned off on purpose must not nag."""
    _repo, base = clone
    _bootstrap(base)
    cfg = {"ledger": {"enabled": True, "actor": "dana", "publish_on_write": False}}
    _note(base)
    calls = []
    assert sync.publish_after_write(base, cfg, publish_fn=lambda *a, **k: calls.append(a)) is None
    assert calls == []


def test_only_a_literal_false_declines_it(clone):
    """The opposite default from `_reconcile_mode`, deliberately and for its own stated reason: a
    typo there could switch ON a mechanism that writes unattended, while a typo here can only make
    a typed command do the thing its own SKILL.md says it is for."""
    assert sync.publish_on_write({}) is True
    assert sync.publish_on_write({"ledger": {"publish_on_write": "no"}}) is True
    assert sync.publish_on_write({"ledger": {"publish_on_write": 0}}) is True
    assert sync.publish_on_write({"ledger": {"publish_on_write": False}}) is False


# --------------------------------------------------------------------- the CLI verbs themselves


def _run_cli(script, args):
    proc = subprocess.run(["python3", str(S / script), *args], capture_output=True, text=True)
    return proc


def _is_entry_id(stdout):
    """stdout is the entry id and nothing else -- the contract every scripted caller parses. Its
    internal spelling is `append()`'s business (and `test_ledger_docs.py`'s), so this asserts the
    actor and the sequence it must end on, never the shape in between."""
    text = stdout.strip()
    return text.startswith("dana:") and text.endswith(":1") and "\n" not in text


def test_ledger_append_reports_the_publish_state_on_stderr(clone):
    """The command `/agrim-ledger` actually tells a person to type. stdout stays the bare entry id
    because callers parse it; the delivery state is a human's business, so it goes to stderr."""
    _repo, base = clone
    _bootstrap(base)
    proc = _run_cli("ledger.py", ["append", str(base), "note", "g.md", "--why", "spike looks viable"])
    assert proc.returncode == 0, proc.stderr
    assert _is_entry_id(proc.stdout), proc.stdout
    assert "published" in proc.stderr, proc.stderr
    assert sync.unpublished(base, ON)["files"] == 0


def test_handoff_ack_reports_the_publish_state_on_stderr(clone):
    """An ack is the half of a hand-off that tells a WAITING PERSON they were answered. Unpublished,
    it answers nobody -- and `ack` writes no issue, so the ledger is its only delivery channel."""
    _repo, base = clone
    _bootstrap(base)
    proc = _run_cli("handoff.py", ["ack", str(base), "--goal", "g.md", "--state", "accepted"])
    assert proc.returncode == 0, proc.stderr
    assert _is_entry_id(proc.stdout), proc.stdout
    assert "published" in proc.stderr, proc.stderr
    assert sync.unpublished(base, ON)["files"] == 0


def test_handoff_open_reports_the_publish_state_on_stderr(clone):
    """The formal cross-area hand-off, opened by hand. The ISSUE is delivery enough on a github
    board -- but the ledger entry is what `ledger.py mine` and TEAM.md read, and on a local backlog
    it is the only record that exists at all."""
    _repo, base = clone
    _bootstrap(base)
    proc = _run_cli("handoff.py", ["open", str(base), "g.md", "--area", "engine",
                                   "--why", "needs an engine flag"])
    assert proc.returncode == 0, proc.stderr
    assert "published" in proc.stderr, proc.stderr
    assert sync.unpublished(base, ON)["files"] == 0


def test_handoff_track_reports_the_publish_state_on_stderr(clone):
    """`track` is the verb that ALSO fires inside an autonomous run (a mid-goal follow-up, a
    decomposition child). That is exactly why the deferral to a live watcher matters: on the loop
    path `_ensure_watcher` has already started one, so the loop keeps publishing on its own tick
    instead of paying a push per filing."""
    _repo, base = clone
    _bootstrap(base)
    proc = _run_cli("handoff.py", ["track", str(base), "g.md", "--area", "engine",
                                   "--why", "a flaky test", "--queue", "queued",
                                   "--assignee", "same-area", "--blocks", "no"])
    assert proc.returncode == 0, proc.stderr
    assert "published" in proc.stderr, proc.stderr
    assert sync.unpublished(base, ON)["files"] == 0


def test_a_publish_failure_never_changes_the_exit_code_of_the_write(tmp_path):
    """`handoff.py`'s exit codes are load-bearing (#1203: a genuinely failed filing must exit 1).
    Publishing is a courtesy on top of a write that already succeeded, so it must never move them."""
    base = _sdlc(tmp_path)                      # no worktree at all -- publish cannot possibly land
    (base / "ledger" / "entries").mkdir(parents=True)
    proc = _run_cli("handoff.py", ["ack", str(base), "--goal", "g.md", "--state", "accepted"])
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert _is_entry_id(proc.stdout), proc.stdout
    assert "LOCALLY ONLY" in proc.stderr, proc.stderr
