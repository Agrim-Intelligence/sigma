# SPDX-License-Identifier: MIT
"""#2076 / #616: a claim renewed by its holder stays held, and nobody else's renewal extends it.

THE DEFECT. `ledger._held_full` folded `claimed` and the terminal kinds and skipped everything else, so
a claim made 13 hours ago and renewed 30 minutes ago by its holder read as expired, and the stale-claim
sweep could release a live claim. `lease-renew` was not even a writable kind.

Each reader clause is scored on its own: the holder's renewal extends the lease, and a renewal from
another actor, another machine or another run does not. The writer is throttled to a third of the lease
TTL, so a long run keeps a claim alive without writing on every call.
"""
import importlib.util
import json
import pathlib

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")

HOUR = 3600
TTL = 12 * HOUR
NOW = 1_800_000_000


def _entry(kind, age_s, actor="dana", host="aaaa1111", pid=100, goal="7", run=None, seq=1, **extra):
    e = {"id": f"{actor}:{host}.{pid}:{seq}", "ts": ledger._stamp(NOW - age_s), "actor": actor,
         "kind": kind, "goal": goal}
    if run:
        e["run_id"] = run
    e.update(extra)
    return e


def _open(entries, ttl=TTL):
    return ledger.open_claims(entries, now=NOW, ttl_seconds=ttl)


# ------------------------------------------------------------------ the vocabulary


def test_lease_renew_is_a_kind_but_not_a_team_view_kind():
    assert "lease-renew" in ledger.KINDS
    assert "lease-renew" not in ledger.SHARED_KINDS, "a keep-alive must not fill TEAM.md"


# ------------------------------------------------------------------ the reader (#616)


def test_the_13_hour_claim_renewed_30_minutes_ago_is_open_not_expired():
    """#616 AC-4: the executed fixture. Red on a tree that skips lease-renew."""
    entries = [_entry("claimed", 13 * HOUR), _entry("lease-renew", HOUR // 2, seq=2)]
    assert _open(entries) == {"7": "dana"}
    assert ledger.expired_claims(entries, now=NOW, ttl_seconds=TTL) == {}


def test_the_same_claim_without_the_renewal_is_expired():
    entries = [_entry("claimed", 13 * HOUR)]
    assert _open(entries) == {}
    assert set(ledger.expired_claims(entries, now=NOW, ttl_seconds=TTL)) == {"7"}


def test_a_renewal_by_another_actor_does_not_extend_the_claim():
    entries = [_entry("claimed", 13 * HOUR), _entry("lease-renew", 60, actor="eve", host="aaaa1111")]
    assert _open(entries) == {}


def test_a_renewal_from_another_machine_does_not_extend_the_claim():
    entries = [_entry("claimed", 13 * HOUR), _entry("lease-renew", 60, host="bbbb2222", pid=900)]
    assert _open(entries) == {}


def test_a_renewal_from_another_run_does_not_extend_the_claim():
    entries = [_entry("claimed", 13 * HOUR, run="s-1"), _entry("lease-renew", 60, run="s-2", pid=900)]
    assert _open(entries) == {}


def test_a_renewal_from_the_same_run_in_a_later_process_extends_it():
    """Every loop.py verb is a new process, so the pid differs; actor, host and run are what hold."""
    entries = [_entry("claimed", 13 * HOUR, run="s-1"), _entry("lease-renew", 60, run="s-1", pid=900)]
    assert _open(entries) == {"7": "dana"}


def test_a_renewal_that_names_no_run_is_compatible_with_a_claim_that_does():
    """`note`, `agent-start` and `verify` carry no --session-pid, so they write no run id."""
    entries = [_entry("claimed", 13 * HOUR, run="s-1"), _entry("lease-renew", 60, pid=900)]
    assert _open(entries) == {"7": "dana"}


def test_a_renewal_never_moves_the_lease_backwards():
    entries = [_entry("claimed", HOUR), _entry("lease-renew", 5 * HOUR, seq=2)]
    held = ledger._held(entries, now=NOW, ttl_seconds=TTL)
    assert held["7"][2] == ledger._stamp(NOW - HOUR)


def test_a_renewal_dated_far_in_the_future_is_not_believed():
    """A skewed clock must not hold a claim for ever: a renewal dated beyond the skew allowance is ignored."""
    entries = [_entry("claimed", 13 * HOUR), _entry("lease-renew", -30 * 24 * HOUR, seq=2)]
    assert _open(entries) == {}
    assert set(ledger.expired_claims(entries, now=NOW, ttl_seconds=TTL)) == {"7"}


def test_a_renewal_within_the_skew_allowance_still_counts():
    entries = [_entry("claimed", 13 * HOUR), _entry("lease-renew", -600, seq=2)]
    assert _open(entries) == {"7": "dana"}


def test_a_renewal_does_not_reopen_a_goal_that_already_ended():
    for end in ("done", "parked", "failed", "release"):
        entries = [_entry("claimed", 2 * HOUR), _entry(end, HOUR, seq=2), _entry("lease-renew", 60, seq=3)]
        assert _open(entries) == {}, end


def test_a_renewal_for_a_goal_nobody_holds_is_ignored():
    assert _open([_entry("lease-renew", 60)]) == {}


def test_an_unknown_kind_is_read_without_raising():
    entries = [_entry("claimed", 13 * HOUR), _entry("from-the-future", 60, seq=2),
               _entry("lease-renew", HOUR // 2, seq=3)]
    assert _open(entries) == {"7": "dana"}


def test_expired_claim_run_ids_agree_with_a_renewal():
    entries = [_entry("claimed", 13 * HOUR, run="s-1"), _entry("lease-renew", 60, run="s-1", pid=900)]
    assert ledger.expired_claim_run_ids(entries, now=NOW, ttl_seconds=TTL) == {}


# ------------------------------------------------------------------ the writer


def _sdlc(tmp_path, **ledger_cfg):
    cfg = {"ledger": dict({"enabled": True, "actor": "dana", "host_alias": "boxa"}, **ledger_cfg)}
    base = tmp_path / ".sdlc"
    (base / "state").mkdir(parents=True)
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return base, cfg


def _claim(base, cfg, age_s, **kw):
    return ledger.append(base, cfg, "claimed", "7", now=NOW - age_s, **kw)


def _renewals(base):
    return [e for e in ledger.read_all(base) if e["kind"] == "lease-renew"]


def test_the_writer_renews_a_held_claim_once_a_third_of_the_ttl_has_passed(tmp_path):
    base, cfg = _sdlc(tmp_path)
    _claim(base, cfg, 5 * HOUR)
    entry = ledger.renew_lease(base, cfg, "7", now=NOW)
    assert entry and entry["kind"] == "lease-renew" and entry["goal"] == "7"
    assert len(_renewals(base)) == 1
    assert _open(ledger.read_all(base))  # the claim is alive on the strength of the renewal


def test_the_writer_is_throttled_to_a_third_of_the_ttl(tmp_path):
    base, cfg = _sdlc(tmp_path)
    _claim(base, cfg, 3 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None, "3h < 4h: not due"
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 2 * HOUR) is not None, "5h >= 4h: due"
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 2 * HOUR + 60) is None, "just renewed"
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 2 * HOUR + 3 * HOUR) is None, "3h after: not due"
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 2 * HOUR + 4 * HOUR + 60) is not None
    assert len(_renewals(base)) == 2


def test_the_writer_renews_only_a_claim_this_process_holds(tmp_path):
    base, cfg = _sdlc(tmp_path)
    other = {"ledger": dict(cfg["ledger"], actor="eve")}
    ledger.append(base, other, "claimed", "7", now=NOW - 6 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None
    assert _renewals(base) == []


def test_the_writer_does_not_renew_a_claim_held_from_another_machine(tmp_path):
    base, cfg = _sdlc(tmp_path)
    elsewhere = {"ledger": dict(cfg["ledger"], host_alias="boxb")}
    ledger.append(base, elsewhere, "claimed", "7", now=NOW - 6 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None


def test_the_writer_does_not_renew_a_claim_of_another_run(tmp_path, monkeypatch):
    base, cfg = _sdlc(tmp_path)
    ledger.append(base, cfg, "claimed", "7", now=NOW - 6 * HOUR, run_id="s-1")
    monkeypatch.setenv("SIGMA_RUN_ID", "s-2")
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None


def test_the_writer_writes_nothing_for_an_unclaimed_goal_or_a_finished_one(tmp_path):
    base, cfg = _sdlc(tmp_path)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None
    _claim(base, cfg, 6 * HOUR)
    ledger.append(base, cfg, "done", "7", now=NOW - 5 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None


def test_the_writer_writes_nothing_when_the_lease_never_expires_or_the_ledger_is_off(tmp_path):
    base, cfg = _sdlc(tmp_path, lease={"ttl_hours": 0})
    _claim(base, cfg, 99 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is None
    base2, cfg2 = _sdlc(tmp_path / "b", enabled=False)
    assert ledger.renew_lease(base2, cfg2, "7", now=NOW) is None


def test_a_broken_writer_never_raises(tmp_path):
    base, cfg = _sdlc(tmp_path)
    _claim(base, cfg, 6 * HOUR)
    (base / "state" / "lease-renew-7").mkdir()          # a directory where the marker file belongs
    ledger.renew_lease(base, cfg, "7", now=NOW)          # must not raise, whatever it decides


def test_a_renewal_that_is_not_due_does_not_read_the_ledger(tmp_path, monkeypatch):
    """The marker is what keeps the common call to one `stat`: inside the window the ledger is not read."""
    base, cfg = _sdlc(tmp_path)
    _claim(base, cfg, 5 * HOUR)
    assert ledger.renew_lease(base, cfg, "7", now=NOW) is not None
    reads = []
    real = ledger.read_all
    monkeypatch.setattr(ledger, "read_all", lambda *a, **k: (reads.append(1), real(*a, **k))[1])
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 60) is None
    assert reads == [], "a call inside the throttle window must cost one stat, not a ledger read"
    assert ledger.renew_lease(base, cfg, "7", now=NOW + 5 * HOUR) is not None
    assert reads, "once due, the ledger is read"
