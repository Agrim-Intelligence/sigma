"""#466 (B6 #419): fixed-name singletons, prose-only patterns and the home root.

What this file pins, and what it does not:

* The 26 patterns of the issue leave `unresolved_patterns` through the documented scan gesture.
* THE HOME ROOT CONTROL (`test_no_pruner_acts_outside_the_project_sdlc`): a decoy "host configuration
  root" that is reachable ONLY through links and records placed at the exact points each pruner
  iterates must come out byte-, size-, mtime- and link-identical after every pruner has run. Each
  pruner first gets a positive control (an in-project target of the same shape IS removed, or for
  `worktree_prune` is examined and kept with a named reason) so a pruner that quietly does nothing
  cannot pass. Live, claimed and successor-owner protection of each pruner is pinned by that pruner's
  own test module (named in the evidence doc), not here.
* The measured per-item ceilings of the inbox and the three cursors. For the cursors this checks
  bytes PER KEY through each cursor's own `_save_cursor` on synthetic keys; it does NOT prove the
  key set is bounded (that rests on the code reading in the evidence doc).
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "agrim-loop" / "scripts"
INIT = ROOT / "skills" / "agrim-init" / "scripts"
AUDIT_DOC = ROOT / "docs" / "launch" / "growth-audit.md"
EVIDENCE_DOC = ROOT / "docs" / "launch" / "b6-singletons-and-home.md"
FILE = ROOT / "docs" / "launch" / "dispositions" / "466.json"

PATTERNS = [
    ".sdlc/*", ".sdlc/.sdlc/", ".sdlc/config.json", ".sdlc/state", ".sdlc/state/",
    ".sdlc/state/STATE.md", ".sdlc/state/agent-watch-cursor.json", ".sdlc/state/autowatch-spend.json",
    ".sdlc/state/channel-notify-cursor.json", ".sdlc/state/coexist.notice",
    ".sdlc/state/comment-watch-cursor.json", ".sdlc/state/drift.meta.json",
    ".sdlc/state/embeddings.json", ".sdlc/state/feature-judge-spend.json", ".sdlc/state/inbox.md",
    ".sdlc/state/kg-refresh.json", ".sdlc/state/kg-warned.stamp", ".sdlc/state/ledger-delivery-attempt",
    ".sdlc/state/owner.json", ".sdlc/state/reconcile.meta.json", ".sdlc/state/setup-wizard-cache.json",
    ".sdlc/state/setup-wizard-dismissed.json", ".sdlc/state/slack-commands.heartbeat.json",
    ".sdlc/state/slack-commands.pid", ".sdlc/state/slack-commands.stop", "<home>/",
]

DAY = 86400
OLD = time.time() - 200 * DAY
FAR_PID = 4294967      # past any pid_max, so `pid_alive` is false


def _mod(name, base=S):
    spec = importlib.util.spec_from_file_location("b6_466_" + name, base / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["b6_466_" + name] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ disposition and the scan gesture

def _gesture():
    block = re.search(r"```sh\n(.*?)```", AUDIT_DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def _scratch_copy(tmp_path):
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split("\0")
    for name in filter(None, names):
        src = ROOT / name
        if src.is_file() and not src.is_symlink():
            dst = tmp_path / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test"]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(git + ["add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "scratch"], cwd=tmp_path, check=True, capture_output=True)


def test_file_covers_every_issue_pattern_once():
    assert FILE.is_file(), "docs/launch/dispositions/466.json must exist"
    entries = json.loads(FILE.read_text(encoding="utf-8"))
    assert sorted(e["pattern"] for e in entries) == sorted(PATTERNS)
    assert {e["issue"] for e in entries} == {"#466"}
    assert not any(e.get("unscanned") for e in entries), "the scan produces all 26; no waiver"
    for e in entries:
        assert set(e) == {"pattern", "issue", "decision", "pruner_or_cap", "evidence"}, e["pattern"]
        assert all(isinstance(v, str) and v.strip() for v in e.values()), e["pattern"]


def test_documented_gesture_resolves_the_rows(tmp_path):
    _scratch_copy(tmp_path)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    data = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text(encoding="utf-8"))
    unresolved = set(data["b6_disposition"]["unresolved_patterns"])
    rows = {r["pattern"]: r for r in data["store_measurements"]}
    for pattern in PATTERNS:
        assert pattern in rows, pattern
        assert pattern not in unresolved, pattern
        assert rows[pattern]["decision"] and rows[pattern]["pruner_or_cap"], pattern


# ------------------------------------------------------------------ inbox and cursor ceilings

INBOX_BATCH_B_PER_ITEM = 300      # measured 196 to 227
INBOX_TICK_B_PER_ITEM = 600       # measured 517 at one item per tick (the 450 B header repeats)
AGENT_WATCH_B_PER_KEY = 25        # measured 19
CHANNEL_NOTIFY_B_PER_KEY = 60     # measured 45
COMMENT_WATCH_B_PER_GOAL = 800    # measured 630 at 20 ids


def _decision(pattern):
    """The disposition entry for `pattern`, so a claimed ceiling and its guard cannot drift apart."""
    assert FILE.is_file(), "docs/launch/dispositions/466.json must exist"
    return next(e for e in json.loads(FILE.read_text(encoding="utf-8")) if e["pattern"] == pattern)


def _ledger_sdlc(tmp_path, name):
    d = tmp_path / name / ".sdlc"
    (d / "state").mkdir(parents=True)
    return d


def test_inbox_bytes_per_item_stay_under_the_ceiling_and_a_loop_read_reclaims_them(tmp_path):
    entry = _decision(".sdlc/state/inbox.md")
    assert "%d B per item" % INBOX_TICK_B_PER_ITEM in entry["pruner_or_cap"], entry
    ledger, watch = _mod("ledger"), _mod("watch")
    other = {"ledger": {"enabled": True, "actor": "alice"}}
    me = {"ledger": {"enabled": True, "actor": "me"}}
    why = "please review the hand-off for the shared schema change before friday"

    batch = _ledger_sdlc(tmp_path, "batch")
    for i in range(100):
        ledger.append(batch, other, "note", str(100 + i), to="me", issue=100 + i, priority="P2",
                      ref="r%d" % i, why=why)
    assert watch.tick(batch, me, me="me")
    size = (batch / "state" / "inbox.md").stat().st_size
    assert 0 < size <= INBOX_BATCH_B_PER_ITEM * 100, size
    assert watch.tick(batch, me, me="me") == ""                  # nothing new: nothing written
    assert (batch / "state" / "inbox.md").stat().st_size == size

    each = _ledger_sdlc(tmp_path, "each")
    for i in range(100):
        ledger.append(each, other, "note", str(100 + i), to="me", issue=100 + i, priority="P2",
                      ref="r%d" % i, why=why)
        assert watch.tick(each, me, me="me")
    size_each = (each / "state" / "inbox.md").stat().st_size
    assert 0 < size_each <= INBOX_TICK_B_PER_ITEM * 100, size_each

    # the pair `loop._surface_inbox` calls reads the whole file and empties it
    assert watch.read_inbox(each)
    watch.clear_inbox(each)
    assert (each / "state" / "inbox.md").stat().st_size == 0 and watch.read_inbox(each) == ""


def test_cursor_bytes_per_key_stay_under_the_documented_ceilings(tmp_path):
    """Bytes PER KEY through each cursor's own writer, on synthetic keys. It pins the per-key cost
    (a cursor that started storing whole entries would fail); it does not prove the key set is
    bounded, which rests on the code reading in the evidence doc."""
    for pattern, ceiling in ((".sdlc/state/agent-watch-cursor.json", "%d B per key" % AGENT_WATCH_B_PER_KEY),
                             (".sdlc/state/channel-notify-cursor.json", "%d B per key" % CHANNEL_NOTIFY_B_PER_KEY),
                             (".sdlc/state/comment-watch-cursor.json", "%d B per goal" % COMMENT_WATCH_B_PER_GOAL)):
        assert ceiling in _decision(pattern)["pruner_or_cap"], pattern
    agent, comment, channel = _mod("agent_watch"), _mod("comment_watch"), _mod("channel_notify")
    d = _ledger_sdlc(tmp_path, "cur")

    sigs = sorted("%d:main:%d" % (1000 + i, 40000 + i) for i in range(1000))
    agent._save_cursor(agent.cursor_path(d), {"notified": sigs})
    assert agent.cursor_path(d).stat().st_size <= AGENT_WATCH_B_PER_KEY * 1000
    assert agent._load_cursor(agent.cursor_path(d)) == {"notified": sigs}

    ids = {"alice:%d:%d" % (5000 + i, i): {"hop": 1, "attempts": 2} for i in range(1000)}
    channel._save_cursor(channel.cursor_path(d), {"notified": ids})
    assert channel.cursor_path(d).stat().st_size <= CHANNEL_NOTIFY_B_PER_KEY * 1000
    assert channel._load_cursor(channel.cursor_path(d)) == {"notified": ids}

    goals = {str(1000 + i): ["IC_kwDOAbCdEf%014d" % (j + i * 20) for j in range(20)] for i in range(100)}
    comment._save_cursor(comment.cursor_path(d), goals)
    assert comment.cursor_path(d).stat().st_size <= COMMENT_WATCH_B_PER_GOAL * 100
    assert comment._load_cursor(comment.cursor_path(d)) == goals


def test_wizard_dismissed_file_holds_only_known_check_names(tmp_path):
    wizard = _mod("setup_wizard", INIT)
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    known = sorted(wizard._MODES)
    wizard.write_dismissed(d, ["not-a-check", "x" * 5000] + known[:2])
    path = d / "state" / "setup-wizard-dismissed.json"
    assert json.loads(path.read_text(encoding="utf-8")) == known[:2]
    wizard.write_dismissed(d, [5, None, ("tuple",), ["list"], known[0]])      # junk types: never raises
    assert json.loads(path.read_text(encoding="utf-8")) == known[:1]
    wizard.write_dismissed(d, known + ["another-unknown"])
    assert json.loads(path.read_text(encoding="utf-8")) == known
    assert path.stat().st_size <= 40 * len(wizard._MODES)
    assert wizard.read_dismissed(d) == set(known)


# ------------------------------------------------------------------ singletons, spend files, STATE.md

SINGLETON_CEILING_B = {"drift.meta.json": 128, "reconcile.meta.json": 128, "kg-refresh.json": 640,
                       "setup-wizard-cache.json": 128, "owner.json": 128}


def test_singleton_writers_rewrite_one_small_record_in_place(tmp_path):
    """Each fixed-name singleton is one record rewritten in place: fifty rewrites leave one file of
    the same size under its ceiling and no temp files beside it."""
    drift, loop, kg = _mod("drift_watch"), _mod("loop"), _mod("kg", ROOT / "skills" / "agrim-kg" / "scripts")
    wizard, coexist = _mod("setup_wizard", INIT), _mod("coexist")
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text("{}")
    writers = {
        "drift.meta.json": lambda: drift._stamp(d, now=1.0),
        "reconcile.meta.json": lambda: loop._reconcile_stamp(d, now=1.0),
        "kg-refresh.json": lambda: kg._write_refresh_record(d, {"ok": False, "ran": True, "detail": "x" * 500}),
        "setup-wizard-cache.json": lambda: wizard._write_cache(d, True, now=1.0),
        "owner.json": lambda: coexist.write_owner(d),
    }
    state = d / "state"
    for name, write in writers.items():
        write()
        first, listing = (state / name).stat().st_size, sorted(os.listdir(state))
        for _ in range(50):
            write()
        assert abs((state / name).stat().st_size - first) <= 24, name        # `at` is a float repr
        assert 0 < (state / name).stat().st_size <= SINGLETON_CEILING_B[name], name
        assert sorted(os.listdir(state)) == listing, name
    assert sorted(os.listdir(state)) == sorted(writers)


def test_spend_files_drop_records_older_than_their_window(tmp_path):
    autowatch, judge = _mod("autowatch"), _mod("feature_judge")
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    for module, name, window in ((autowatch, "autowatch-spend.json", autowatch.SPEND_WINDOW_SECONDS),
                                 (judge, "feature-judge-spend.json", judge.SPEND_WINDOW_SECONDS)):
        path = d / "state" / name
        for _ in range(100):
            if module is autowatch:
                module._record_spend(d, 10.0, 0.01, 1000.0)
            else:
                module._record_spend(d, 0.01, 1000.0)
        assert len(json.loads(path.read_text())["records"]) == 100, name
        if module is autowatch:
            module._record_spend(d, 10.0, 0.01, 1000.0 + window + 1)
        else:
            module._record_spend(d, 0.01, 1000.0 + window + 1)
        assert len(json.loads(path.read_text())["records"]) == 1, name


def test_state_md_run_lists_stop_at_the_cap_and_start_run_resets_them(tmp_path, monkeypatch):
    state = _mod("state")
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    state.start_run(d)
    started = state.load_cursor(d)["run_started_at"]
    monkeypatch.setattr(state, "_MAX_RUN_TOKEN_CREDITS", 3)
    for i in range(3):
        state.record_phase_end(d, "attempt-%d" % i, started, budget_tokens=5, codex_raw_tokens=7)
    with pytest.raises(RuntimeError, match="limit"):
        state.record_phase_end(d, "attempt-3", started, budget_tokens=5, codex_raw_tokens=7)
    text = (d / "state" / "STATE.md").read_text()
    assert 'run_phase_ends: ["' in text and 'run_token_credits: ["' in text      # three keys each
    state.start_run(d)
    text = (d / "state" / "STATE.md").read_text()
    assert "run_phase_ends: []" in text and "run_token_credits: []" in text
    assert "run_codex_token_credits: {}" in text
    assert state.load_cursor(d)["run_tokens"] == 0


# ------------------------------------------------------------------ the home root control

def _snapshot(root):
    """Every entry under `root` (and `root` itself): type, size, mtime, inode and content or link target."""
    out = {}
    for p in [root] + sorted(root.rglob("*")):
        st = p.lstat()
        entry = [stat.S_IFMT(st.st_mode), st.st_size, st.st_mtime_ns, st.st_ino]
        if p.is_symlink():
            entry.append(os.readlink(p))
        elif p.is_file():
            entry.append(hashlib.sha256(p.read_bytes()).hexdigest())
        out[str(p.relative_to(root.parent))] = entry
    return out


def _age(path, when=OLD):
    os.utime(path, (when, when), follow_symlinks=False)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _log_rows(t, *kinds):
    out = []
    for i, kind in enumerate(kinds):
        k, _, result = kind.partition(":")
        row = {"actor": "loop", "goal": "g", "kind": k, "thread": "main", "ts": _iso(t + i)}
        if result:
            row["result"] = result
        out.append(json.dumps(row, sort_keys=True))
    return "\n".join(out) + "\n"


def _write(path, text="x\n", age=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _age(path, age)
    return path


def _project(tmp_path, name):
    sdlc = tmp_path / name / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "config.json").write_text(json.dumps({"action_log": {"enabled": True}}))
    return sdlc


def _link(sdlc_relative, sdlc, target):
    """Point `<sdlc>/<sdlc_relative>` at `target` (a directory of the decoy host root)."""
    target.mkdir(parents=True, exist_ok=True)
    link = sdlc / sdlc_relative
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target, target_is_directory=True)
    return link


# One entry per pruner: (name, positive(tmp) -> bool, escape(tmp, host) -> act). `positive` runs the
# pruner over an in-project target of the same shape and returns True when it acted (or, for the
# worktree sweep, examined and named a reason). `escape` builds EVERYTHING (the decoy and the links or
# records that reach it) and returns `act`, a zero-argument callable that runs the pruner; the test
# snapshots the host root after the build and before `act()`, so only the pruner can change it.

def _retention_positive(tmp_path):
    r = _mod("retention")
    sdlc = _project(tmp_path, "ret-pos")
    _write(sdlc / "state" / "log" / "101.jsonl", _log_rows(OLD, "claimed", "recorded:done"))
    _write(sdlc / "state" / "witness" / "101.jsonl", "{}\n")
    return r.prune_closed_goal_streams(sdlc)["removed"] == ["101"]


def _retention_escape(tmp_path, host):
    r = _mod("retention")
    sdlc = _project(tmp_path, "ret-esc")
    _write(host / "log" / "202.jsonl", _log_rows(OLD, "claimed", "recorded:done"))
    _write(host / "witness" / "202.jsonl", "{}\n")
    _link("state/log", sdlc, host / "log")
    _link("state/witness", sdlc, host / "witness")
    sdlc2 = _project(tmp_path, "ret-esc2")                   # the whole state directory is a link
    (sdlc2 / "state").rmdir()
    (sdlc2 / "state").symlink_to(host, target_is_directory=True)
    _write(host / "log" / "202.jsonl", _log_rows(OLD, "claimed", "recorded:done"))

    def act():
        r.prune_closed_goal_streams(sdlc)
        r.prune_closed_goal_streams(sdlc2)
    return act


def _goal_state_positive(tmp_path):
    g = _mod("goal_state_prune")
    sdlc = _project(tmp_path, "gs-pos")
    _write(sdlc / "state" / "log" / "101.jsonl", _log_rows(OLD, "claimed", "recorded:done"))
    _write(sdlc / "state" / "verify" / "101.json", "{}\n")
    return bool(g.sweep(sdlc)["removed"]) and not (sdlc / "state" / "verify" / "101.json").exists()


def _goal_state_escape(tmp_path, host):
    g = _mod("goal_state_prune")
    sdlc = _project(tmp_path, "gs-esc")
    _write(sdlc / "state" / "log" / "202.jsonl", _log_rows(OLD, "claimed", "recorded:done"))
    for family in ("verify", "landing", "phase", "escalation"):
        _write(host / family / "202.json", "{}\n")
        _link("state/" + family, sdlc, host / family)
    _write(host / "run_stop" / "old.json", "{}\n")
    _link("state/run_stop", sdlc, host / "run_stop")
    _write(host / "agents" / "202" / "main.active", "{}\n")
    _link("state/agents", sdlc, host / "agents")
    sdlc2 = _project(tmp_path, "gs-esc2")                    # the whole state directory is a link
    (sdlc2 / "state").rmdir()
    (sdlc2 / "state").symlink_to(host, target_is_directory=True)
    _write(host / "log" / "202.jsonl", _log_rows(OLD, "claimed", "recorded:done"))

    def act():
        g.sweep(sdlc)
        g.sweep(sdlc2)
    return act


def _liveness_positive(tmp_path):
    lp = _mod("liveness_prune")
    if lp.fcntl is None:
        return True            # the sweep refuses without flock on this platform; nothing to prove
    sdlc = _project(tmp_path, "lv-pos")
    _write(sdlc / "state" / "claims" / "101.lock", "")
    _write(sdlc / "state" / "claims" / "102.claimed", "")
    lp.sweep(sdlc)
    return not (sdlc / "state" / "claims" / "101.lock").exists()


def _liveness_escape(tmp_path, host):
    lp = _mod("liveness_prune")
    sdlc = _project(tmp_path, "lv-esc")
    _write(host / "claims" / "202.lock", "")
    _write(host / "claims" / "202.claimed", "")
    _link("state/claims", sdlc, host / "claims")
    _write(host / "sessions" / ("%d.active" % FAR_PID), json.dumps({"pid": FAR_PID, "in_flight": []}))
    _write(host / "sessions" / ("%d.active.%d.tmp" % (FAR_PID, FAR_PID)), "x")
    _link("state/sessions", sdlc, host / "sessions")
    sdlc2 = _project(tmp_path, "lv-esc2")                    # the whole state directory is a link
    (sdlc2 / "state").rmdir()
    (sdlc2 / "state").symlink_to(host, target_is_directory=True)

    def act():
        lp.sweep(sdlc)
        lp.sweep(sdlc2)
    return act


def _session_positive(tmp_path):
    loop = _mod("loop")
    sdlc = _project(tmp_path, "ss-pos")
    _write(sdlc / "state" / "sessions" / ("%d.active" % FAR_PID),
           json.dumps({"pid": FAR_PID, "in_flight": []}))
    loop._prune_dead_session_entries(sdlc, {})
    return not (sdlc / "state" / "sessions" / ("%d.active" % FAR_PID)).exists()


def _session_escape(tmp_path, host):
    loop = _mod("loop")
    sdlc = _project(tmp_path, "ss-esc")
    _write(host / "sessions" / ("%d.active" % FAR_PID), json.dumps({"pid": FAR_PID, "in_flight": []}))
    _write(host / "sessions" / ("%d.active.%d.tmp" % (FAR_PID, FAR_PID)), "x")
    _link("state/sessions", sdlc, host / "sessions")
    sdlc2 = _project(tmp_path, "ss-esc2")                    # the whole state directory is a link
    (sdlc2 / "state").rmdir()
    (sdlc2 / "state").symlink_to(host, target_is_directory=True)

    def act():
        loop._prune_dead_session_entries(sdlc, {})
        loop._prune_dead_session_entries(sdlc2, {})
    return act


def _worktree_positive(tmp_path):
    wp = _mod("worktree_prune")
    sdlc = _project(tmp_path, "wt-pos")
    work_dir = sdlc / "work" / "101"
    work_dir.mkdir(parents=True)
    rec = {"worktree": str(work_dir), "branch": "sdlc/101", "base": "main"}
    _write(sdlc / "state" / "work" / "101.json", json.dumps(rec), age=time.time())
    result = wp.sweep(sdlc, config={"work": {"enabled": True}}, limit=0, max_examine=0)
    # examined (a reason is named) and kept: a plain directory is not a git worktree
    return (result["removed"] == [] and work_dir.exists()
            and any(k["goal"] == "101" and k["reason"] for k in result["kept"]))


def _worktree_escape(tmp_path, host):
    wp = _mod("worktree_prune")
    sdlc = _project(tmp_path, "wt-esc")
    decoy = host / "wt" / "202"
    _write(decoy / "keep.txt", "host-owned\n")
    rec = {"worktree": str(decoy), "branch": "sdlc/202", "base": "main", "pr": 7}
    _write(sdlc / "state" / "work" / "202.json", json.dumps(rec), age=time.time())
    link = sdlc / "work" / "203"                             # a link in place of the checkout
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(decoy, target_is_directory=True)
    rec = {"worktree": str(link), "branch": "sdlc/203", "base": "main", "pr": 8}
    _write(sdlc / "state" / "work" / "203.json", json.dumps(rec), age=time.time())

    def act():
        result = wp.sweep(sdlc, config={"work": {"enabled": True}}, limit=0, max_examine=0)
        assert result["removed"] == []
        assert any(k["goal"] == "202" and k["reason"] == "outside-work-root"
                   for k in result["kept"]), result
    return act


def _logroll_positive(tmp_path):
    lr = _mod("logroll")
    log = _write(tmp_path / "lr-pos" / ".sdlc" / "state" / "x.log", "y" * 50, age=time.time())
    lr.rotate(log, cap=10)
    return not log.exists() and (log.parent / "x.log.1").read_text() == "y" * 50


def _logroll_escape(tmp_path, host):
    lr = _mod("logroll")
    state = tmp_path / "lr-esc" / ".sdlc" / "state"
    state.mkdir(parents=True)
    big = _write(host / "big.log", "z" * 400)
    (state / "x.log").symlink_to(big)                        # the log itself is a link
    gen = _write(host / "gen.log", "g" * 400)
    _write(state / "y.log", "y" * 50, age=time.time())
    (state / "y.log.1").symlink_to(gen)                      # a predecessor generation is a link
    lock_target = _write(host / "lock.target", "l" * 10)
    _write(state / "w.log", "w" * 50, age=time.time())
    lock = state / "w.log.rotating"                          # a stale rotation lock is a link
    lock.symlink_to(lock_target)
    _age(lock)

    def act():
        for name in ("x.log", "y.log", "w.log"):
            lr.rotate(state / name, cap=10)
    return act


def _review_copy_positive(tmp_path):
    work = _mod("work")
    sdlc = _project(tmp_path, "rc-pos")
    copied = sdlc / "evidence" / "101" / "rv1" / "wt"
    _write(copied / "f.txt", "copy\n")
    return work.prune_terminal_review_copies(sdlc, "101") == [copied] and not copied.exists()


def _review_copy_escape(tmp_path, host):
    work = _mod("work")
    sdlc = _project(tmp_path, "rc-esc")
    _write(host / "evidence" / "202" / "rv1" / "wt" / "keep.txt", "host-owned\n")
    _link("evidence", sdlc, host / "evidence")               # the evidence ROOT is a link
    sdlc2 = _project(tmp_path, "rc-esc2")
    _write(host / "e2" / "rv1" / "wt" / "keep.txt", "host-owned\n")
    _link("evidence/303", sdlc2, host / "e2")                # one goal's directory is a link

    def act():
        work.prune_terminal_review_copies(sdlc, "202")
        work.prune_terminal_review_copies(sdlc2, "303")
    return act


def _ledger_journal_positive(tmp_path):
    ledger = _mod("ledger")
    sdlc = _project(tmp_path, "lj-pos")
    old = _write(ledger.local_events_dir(sdlc) / "a-h.1.jsonl", "{}\n")
    ledger.prune_journal(sdlc)
    return not old.exists()


def _ledger_journal_escape(tmp_path, host):
    ledger = _mod("ledger")
    sdlc = _project(tmp_path, "lj-esc")
    _write(host / "events" / "a-h.1.jsonl", "{}\n")
    link = ledger.local_events_dir(sdlc)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(host / "events", target_is_directory=True)
    return lambda: ledger.prune_journal(sdlc)


def _timing_positive(tmp_path):
    ts = _mod("timing_store")
    sdlc = _project(tmp_path, "ts-pos")
    f = _write(ts.store_dir(sdlc) / "101" / "a.jsonl", "{}\n")
    return ts.prune(sdlc) == ["101"] and not f.exists()


def _timing_escape(tmp_path, host):
    ts = _mod("timing_store")
    sdlc = _project(tmp_path, "ts-esc")
    _write(host / "time" / "202" / "a.jsonl", "{}\n")
    link = ts.store_dir(sdlc)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(host / "time", target_is_directory=True)
    sdlc2 = _project(tmp_path, "ts-esc2")
    _write(host / "g" / "a.jsonl", "{}\n")
    store = ts.store_dir(sdlc2)
    store.mkdir(parents=True)
    (store / "303").symlink_to(host / "g", target_is_directory=True)
    (store / ts.SESSIONS).symlink_to(host / "time", target_is_directory=True)

    def act():
        ts.prune(sdlc)
        ts.prune(sdlc2)
    return act


PRUNERS = [
    ("retention.prune_closed_goal_streams", _retention_positive, _retention_escape),
    ("goal_state_prune.sweep", _goal_state_positive, _goal_state_escape),
    ("liveness_prune.sweep", _liveness_positive, _liveness_escape),
    ("loop._prune_dead_session_entries", _session_positive, _session_escape),
    ("worktree_prune.sweep", _worktree_positive, _worktree_escape),
    ("logroll.rotate", _logroll_positive, _logroll_escape),
    ("work.prune_terminal_review_copies", _review_copy_positive, _review_copy_escape),
    ("ledger.prune_journal", _ledger_journal_positive, _ledger_journal_escape),
    ("timing_store.prune", _timing_positive, _timing_escape),
]


def _host(tmp_path):
    host = tmp_path / "home" / ".claude"
    host.mkdir(parents=True)
    _write(host / "settings.json", "{}\n")
    return host


def test_no_pruner_acts_outside_the_project_sdlc(tmp_path):
    """Run every pruner over a project whose `.sdlc` reaches a decoy host configuration root only
    through links and records. Each failure is named, so a regression says which pruner escaped."""
    failures = []
    for name, positive, escape in PRUNERS:
        slot = tmp_path / re.sub(r"\W", "_", name)
        try:
            if not positive(slot / "pos"):
                failures.append("%s: positive control did not act (the harness is inert)" % name)
        except Exception as exc:                       # noqa: BLE001 - name the pruner, keep going
            failures.append("%s: positive control raised %r" % (name, exc))
        host = _host(slot / "esc")
        try:
            act = escape(slot / "esc" / "proj", host)
        except Exception as exc:                       # noqa: BLE001
            failures.append("%s: could not build the escape scenario: %r" % (name, exc))
            continue
        before = _snapshot(host)
        try:
            act()
        except Exception as exc:                       # noqa: BLE001
            failures.append("%s: raised %r while the host root was linked in" % (name, exc))
        after = _snapshot(host)
        if after != before:
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            failures.append("%s: changed the host root: %s" % (name, ", ".join(changed[:6])))
    assert not failures, "\n".join(failures)


def test_a_symlinked_evidence_root_is_not_followed_by_review_copy_cleanup(tmp_path, capsys):
    work = _mod("work")
    host = _host(tmp_path / "e")
    keep = _write(host / "evidence" / "123" / "rv1" / "wt" / "keep.txt", "host-owned\n")
    sdlc = _project(tmp_path, "proj")
    _link("evidence", sdlc, host / "evidence")
    assert work.prune_terminal_review_copies(sdlc, "123") == []
    assert keep.read_text() == "host-owned\n"
    assert "review-copy cleanup skipped" in capsys.readouterr().err


# ------------------------------------------------------------------ the doc quotes what the code holds

def test_doc_numbers_match_the_code():
    assert EVIDENCE_DOC.is_file(), "docs/launch/b6-singletons-and-home.md must exist"
    doc = EVIDENCE_DOC.read_text(encoding="utf-8")
    backlog = _mod("backlog_check")
    state = _mod("state")
    wizard = _mod("setup_wizard", INIT)
    assert "%s entries" % format(backlog._EMBED_CACHE_MAX_ENTRIES, ",") in doc
    assert "%d attempts" % state._MAX_RUN_TOKEN_CREDITS in doc
    assert "%d modes" % len(wizard._MODES) in doc
    for number in (INBOX_BATCH_B_PER_ITEM, INBOX_TICK_B_PER_ITEM, AGENT_WATCH_B_PER_KEY,
                   CHANNEL_NOTIFY_B_PER_KEY, COMMENT_WATCH_B_PER_GOAL):
        assert "%d B" % number in doc, number
    for name, _positive, _escape in PRUNERS:
        assert name in doc, name
