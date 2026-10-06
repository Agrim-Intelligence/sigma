import importlib.util
import json
import os
import pathlib
import subprocess
import sys

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


agent_watch = _mod("agent_watch")
loop = _mod("loop")
ledger = _mod("ledger")
watch_classify = _mod("watch_classify")

ME = "watcher"       # the identity agent_watch.py's OWN ledger writes are attributed to
ACTOR = "amy"         # the claim holder / notification recipient
ON = {"ledger": {"enabled": True, "actor": ME}, "agent_watch": {"enabled": True}}


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else ON))
    return d


def _claim(d, goal, actor=ACTOR, ts=None):
    """Write a raw `claimed` ledger entry directly — mirrors test_watch.py's own direct-JSONL
    fixture style, so a test doesn't have to run the real loop.py claim path just to set up an
    open lease. `ts` defaults to "now" (never a hardcoded date) so the claim never falls outside
    the default 12h lease TTL just because the suite happens to run on a different day."""
    ledger.entries_dir(d).mkdir(parents=True, exist_ok=True)
    path = ledger.entry_file(d, actor)
    path.write_text(json.dumps({"id": f"{actor}:1", "ts": ts or ledger._stamp(), "actor": actor,
                                 "kind": "claimed", "goal": goal}) + "\n")


# ------------------------------------------------------------------ enabled() / tick() gating


def test_tick_is_a_noop_when_agent_watch_disabled(tmp_path):
    d = _sdlc(tmp_path, {"ledger": {"enabled": True}, "agent_watch": {"enabled": False}})
    assert agent_watch.tick(d) == ""


def test_tick_is_a_noop_when_agent_watch_key_absent(tmp_path):
    d = _sdlc(tmp_path, {"ledger": {"enabled": True}})
    assert agent_watch.tick(d) == ""


def test_tick_skips_a_goal_with_no_registered_marker_at_all(tmp_path):
    """'unknown' (no marker) must never be treated as 'dead' -- an open claim with nothing
    registered for it must never fire a false collapse."""
    d = _sdlc(tmp_path)
    _claim(d, "g.md")
    assert agent_watch.tick(d) == ""
    assert [e for e in ledger.read_all(d) if e["kind"] == "note"] == []


def test_tick_does_not_fire_while_the_registered_pid_is_still_alive(tmp_path):
    d = _sdlc(tmp_path)
    _claim(d, "g.md")
    loop.agent_start(d, "g.md", os.getpid(), ON)     # this test process is unambiguously alive
    assert agent_watch.tick(d) == ""


# ------------------------------------------------------------------ exactly-once, real SIGKILL (mirrors N_RACERS bar)


def test_agent_watch_detects_a_real_sigkilled_process_and_notifies_via_ledger_exactly_once(tmp_path):
    """The plan's own explicit ask, matching #387/#339's real-process bar (test_watch.py's
    N_RACERS precedent): spawn a genuine child, register its REAL pid, claim the goal, SIGKILL it
    for real, then prove exactly one `note` ledger entry fires -- and a second tick over the same
    collapse fires no more (exactly-once)."""
    d = _sdlc(tmp_path)
    goal = "158.md"
    _claim(d, goal)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        loop.agent_start(d, goal, proc.pid, ON)
        assert agent_watch.tick(d) == ""                           # still alive: no notification yet

        proc.kill()                                                 # a genuine SIGKILL
        proc.wait(timeout=10)

        summary = agent_watch.tick(d)
        assert "1 agent(s) confirmed dead" in summary and goal in summary
        notes = [e for e in ledger.read_all(d) if e["kind"] == "note" and e.get("to") == ACTOR]
        assert len(notes) == 1
        assert f"pid={proc.pid}" in notes[0]["why"]

        second = agent_watch.tick(d)
        assert second == ""                                         # exactly-once: no repeat
        notes_after = [e for e in ledger.read_all(d) if e["kind"] == "note" and e.get("to") == ACTOR]
        assert len(notes_after) == 1                                 # still exactly one, not two
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def test_agent_watch_notifies_again_after_a_fresh_agent_start_on_a_new_pid(tmp_path):
    """A NEW agent_start (a fresh signature) after a reclaim must be able to notify again if IT
    also dies -- suppression is per (goal, thread, pid), not per (goal, thread) forever.

    #476: two ledger entries existing on disk is NOT enough to prove this -- that was the exact
    gap that let the dead-agent notifications collapse silently: agent_watch.py wrote two `note`
    entries just fine, but omitted `ref=`, so watch_classify.classify()'s signature-dedup still
    swallowed the second one before it ever reached the claimant's inbox. The assertions below
    call classify() -- the real read-side consumer -- and prove BOTH surface as distinct inbox
    items, not just that the ledger file has two lines."""
    d = _sdlc(tmp_path)
    goal = "161.md"
    _claim(d, goal)
    dead_pid_1 = 2**30                        # not a real pid on any sane system
    loop.agent_start(d, goal, dead_pid_1, ON)
    agent_watch.tick(d)
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1

    dead_pid_2 = 2**30 - 1
    loop.agent_start(d, goal, dead_pid_2, ON)   # a fresh registration, e.g. after a reclaim
    agent_watch.tick(d)
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 2

    items, _ = watch_classify.classify(ledger.read_all(d), watch_classify.EMPTY_CURSOR, ACTOR)
    assert len(items) == 2                                          # both delivered, neither swallowed
    refs = {item.get("ref") for item in items}
    assert refs == {f"{goal}:main:{dead_pid_1}", f"{goal}:main:{dead_pid_2}"}
    assert {item["why"] for item in items} == {
        f"background agent died (thread=main, pid={dead_pid_1}) — reclaim or re-open",
        f"background agent died (thread=main, pid={dead_pid_2}) — reclaim or re-open",
    }


# ------------------------------------------------------------------ SMTP (DI, never a real server)


EMAIL_ON = {**ON, "agent_watch": {"enabled": True, "notify": {"email": {
    "enabled": True, "host": "smtp.example.com", "to": "lead@example.com",
}}}}


def test_notify_sends_email_when_fully_configured(tmp_path):
    d = _sdlc(tmp_path, EMAIL_ON)
    goal = "1.md"
    _claim(d, goal)
    dead_pid = 2**30
    loop.agent_start(d, goal, dead_pid, EMAIL_ON)
    calls = []

    def fake_send(email_cfg, subject, body):
        calls.append((email_cfg, subject, body))
        return True

    agent_watch.tick(d, run_email=fake_send)
    assert len(calls) == 1
    email_cfg, subject, body = calls[0]
    assert email_cfg["to"] == "lead@example.com"
    assert goal in subject and str(dead_pid) in body
    assert [e for e in ledger.read_all(d) if e["kind"] == "note"] == []   # email OK: no ledger note too


def test_notify_falls_back_to_ledger_when_email_not_configured(tmp_path):
    d = _sdlc(tmp_path)                                             # ON has no notify.email at all
    goal = "1.md"
    _claim(d, goal)
    loop.agent_start(d, goal, 2**30, ON)
    calls = []
    agent_watch.tick(d, run_email=lambda *a: calls.append(a) or True)
    assert calls == []                                              # never even attempted
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1


def test_notify_falls_back_to_ledger_when_enabled_but_host_or_to_missing_and_warns(tmp_path, capsys):
    cfg = {**ON, "agent_watch": {"enabled": True,
                                  "notify": {"email": {"enabled": True, "host": "", "to": ""}}}}
    d = _sdlc(tmp_path, cfg)
    goal = "1.md"
    _claim(d, goal)
    loop.agent_start(d, goal, 2**30, cfg)
    calls = []
    agent_watch.tick(d, run_email=lambda *a: calls.append(a) or True)
    assert calls == []
    assert "host/to is missing" in capsys.readouterr().err
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1


def test_notify_falls_back_to_ledger_when_send_raises(tmp_path, capsys):
    d = _sdlc(tmp_path, EMAIL_ON)
    goal = "1.md"
    _claim(d, goal)
    loop.agent_start(d, goal, 2**30, EMAIL_ON)

    def raising_send(email_cfg, subject, body):
        raise RuntimeError("connection refused")

    agent_watch.tick(d, run_email=raising_send)
    assert "smtp send failed" in capsys.readouterr().err
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1  # must never mean nobody is told


def test_send_email_reads_password_from_the_configured_env_var_never_from_config(tmp_path, monkeypatch):
    """Structural, not just conventional: even a literal `pass` key somehow present in config must
    never be read -- only os.environ, via `pass_env`."""
    monkeypatch.delenv("SIGMA_SMTP_PASS", raising=False)
    monkeypatch.setenv("SIGMA_ALLOW_REPO_SMTP", "1")
    monkeypatch.setenv("SIGMA_SMTP_CUSTOM", "s3cret")
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            sent["user"] = user
            sent["password"] = password

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(agent_watch.smtplib, "SMTP", FakeSMTP)
    email_cfg = {"host": "smtp.example.com", "to": "lead@example.com", "user": "bot",
                 "pass_env": "SIGMA_SMTP_CUSTOM", "pass": "LITERAL-SHOULD-NEVER-BE-READ"}
    assert agent_watch._real_send_email(email_cfg, "subj", "body") is True
    assert sent["password"] == "s3cret"
    assert sent["password"] != "LITERAL-SHOULD-NEVER-BE-READ"


def test_real_send_email_raises_when_user_set_but_password_env_var_missing(monkeypatch):
    monkeypatch.delenv("SIGMA_SMTP_PASS", raising=False)
    monkeypatch.setenv("SIGMA_ALLOW_REPO_SMTP", "1")

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            raise AssertionError("must never reach login with an empty password")

        def send_message(self, msg):
            raise AssertionError("must never reach send with a missing password")

    monkeypatch.setattr(agent_watch.smtplib, "SMTP", FakeSMTP)
    email_cfg = {"host": "smtp.example.com", "to": "x@example.com", "user": "bot"}
    try:
        agent_watch._real_send_email(email_cfg, "s", "b")
        assert False, "expected a RuntimeError"
    except RuntimeError as exc:
        assert "SIGMA_SMTP_PASS" in str(exc)


# ------------------------------------------------------------------ cursor


def test_cursor_round_trips_and_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "cursor.json"
    assert agent_watch._load_cursor(path) == {"notified": []}
    agent_watch._save_cursor(path, {"notified": ["1.md:main:123"]})
    assert agent_watch._load_cursor(path) == {"notified": ["1.md:main:123"]}
    path.write_text("{not json")
    assert agent_watch._load_cursor(path) == {"notified": []}


def test_cursor_survives_a_truthy_non_list_notified(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps({"notified": "garbage"}))
    assert agent_watch._load_cursor(path) == {"notified": []}


# ------------------------------------------------------------------ CLI


def test_main_cli_runs_a_tick_and_prints_the_summary(tmp_path, capsys):
    d = _sdlc(tmp_path)
    goal = "1.md"
    _claim(d, goal)
    loop.agent_start(d, goal, 2**30, ON)
    assert agent_watch.main(["agent_watch.py", str(d)]) == 0
    assert "1 agent(s) confirmed dead" in capsys.readouterr().out


def test_main_cli_is_quiet_when_nothing_fired(tmp_path, capsys):
    d = _sdlc(tmp_path, {"ledger": {"enabled": True}, "agent_watch": {"enabled": False}})
    assert agent_watch.main(["agent_watch.py", str(d)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_cli_is_never_fatal_even_when_tick_raises(monkeypatch, capsys):
    """A watcher tick is never fatal (matches watch.py's own main() contract) -- an unexpected
    exception must print a non-fatal message and return 1, never propagate a raw traceback."""
    def boom(_sdlc_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_watch, "tick", boom)
    assert agent_watch.main(["agent_watch.py", "/nonexistent"]) == 1
    assert "tick failed (non-fatal): boom" in capsys.readouterr().err
