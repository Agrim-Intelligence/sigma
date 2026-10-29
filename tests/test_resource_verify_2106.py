"""#2106 - a verify held for a literal `-n N` records nothing: no ring entry, no lease left behind.

The hold used to leave `resource_verify.run` through its `finally`, which called `lease.release`; `release` measured
the lease's pid (the caller, a few MiB) and wrote that into the `verify` ring and `verify_run`, so after two holds the
measured cost that caused them was an outlier and the third attempt ran the count it had held.

Controls (break one clause, see red):
  * call `lease.release` instead of `lease.discard` on the hold path and the byte-identity, six-holds and
    no-lease tests go red;
  * make `discard` record the peak like `_finish` does and `test_discard_*` and the byte-identity test go red;
  * make `discard` skip the unlink and the no-lease tests go red;
  * make `discard` skip the event and `test_discard_logs_an_event` goes red;
  * make `discard` return True for an unknown lease and `test_discard_of_an_unknown_lease_*` goes red;
  * make `run` discard on the ran path too and `test_a_verify_that_ran_*` goes red.
"""
import json
import pathlib

from test_resource_verify_1601 import GIB, MIB, env, seed  # noqa: F401


def faked_machine(env):
    """The issue's reproduction: 15.7 GiB total, 15.2 GiB available."""
    guard = env.lease.guard

    def fake():
        return guard.Sample(total=int(15.7 * GIB), available=int(15.2 * GIB), swap_used=0, swap_total=0, swapouts=0,
                            swapouts_at=0.0, pressure=1, cpu_count=8, load1=0.0)
    env.lease.take_sample = fake


def events(env):
    path = env.state / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def leases(env):
    return list((env.state / "leases").glob("*.json"))


def test_a_hold_for_a_literal_count_leaves_observed_json_byte_for_byte(env, tmp_path):
    faked_machine(env)
    seed(env, [5632 * MIB])
    before = (env.state / "observed.json").read_bytes()
    out = env.run("verify", "echo ran -n 2", str(tmp_path), 30, "g1", config={"mode": "enforce"})
    assert out.hold is not None and "literal -n 2" in out.hold.reason and out.result is None
    assert (env.state / "observed.json").read_bytes() == before


def test_six_holds_of_the_same_literal_command_all_hold_and_none_runs(env, tmp_path):
    faked_machine(env)
    seed(env, [5632 * MIB])
    marker = tmp_path / "ran"
    before = (env.state / "observed.json").read_bytes()
    for attempt in range(6):
        out = env.run("verify", "echo ran -n 2 >> %s" % marker, str(tmp_path), 30, "g1", config={"mode": "enforce"})
        assert out.hold is not None, "attempt %d ran the held count" % (attempt + 1)
        assert out.result is None
    assert not marker.exists()
    assert (env.state / "observed.json").read_bytes() == before
    assert env.lease.learned_cost(json.loads(before)["verify"]) == 5632 * MIB


def test_a_hold_leaves_no_lease_file_so_it_reserves_nothing(env, tmp_path):
    faked_machine(env)
    seed(env, [5632 * MIB])
    cfg = {"mode": "enforce"}
    out = env.run("verify", "echo ran -n 2", str(tmp_path), 30, "g1", config=cfg)
    assert out.hold is not None
    assert leases(env) == []
    assert env.lease.status(config=cfg)["leases"] == []
    # the next admit sees the whole machine: one worker fits, as it did before the hold
    nxt = env.lease.admit("verify", 1, str(tmp_path), "g2", config=cfg)
    assert not isinstance(nxt, env.lease.Hold) and nxt.n == 1


def test_a_hold_still_records_the_goal_as_waiting(env, tmp_path):
    faked_machine(env)
    seed(env, [5632 * MIB])
    out = env.run("verify", "echo ran -n 2", str(tmp_path), 30, "g1", config={"mode": "enforce"})
    assert out.hold is not None
    assert "g1" in json.loads((env.state / "waiting.json").read_text())


def test_a_hold_logs_a_discard_event_and_no_release(env, tmp_path):
    faked_machine(env)
    seed(env, [5632 * MIB])
    env.run("verify", "echo ran -n 2", str(tmp_path), 30, "g1", config={"mode": "enforce"})
    kinds = [e["event"] for e in events(env)]
    assert "discard" in kinds and "release" not in kinds


def test_a_verify_that_ran_still_records_its_peak_into_verify_and_verify_run(env, tmp_path):
    faked_machine(env)
    env.lease.take_footprint = lambda pid: (3 * GIB, False)
    out = env.run("verify", "echo ran", str(tmp_path), 30, "g1", config={"mode": "enforce"}, sample_gap=0.05)
    assert out.hold is None and out.returncode == 0
    ring = json.loads((env.state / "observed.json").read_text())
    assert ring["verify"] and ring["verify_run"]
    assert "release" in [e["event"] for e in events(env)]


def test_discard_removes_the_lease_without_touching_the_ring(env):
    faked_machine(env)
    seed(env, [5632 * MIB])
    before = (env.state / "observed.json").read_bytes()
    grant = env.lease.admit("verify", 1, "r", "g1", config={"mode": "enforce"})
    assert len(leases(env)) == 1
    assert env.lease.discard(grant.id) is True
    assert leases(env) == []
    assert (env.state / "observed.json").read_bytes() == before


def test_discard_logs_an_event(env):
    faked_machine(env)
    grant = env.lease.admit("verify", 1, "r", "g1", config={"mode": "enforce"})
    env.lease.discard(grant.id)
    found = [e for e in events(env) if e["event"] == "discard"]
    assert len(found) == 1 and found[0]["id"] == grant.id and found[0]["kind"] == "verify"


def test_discard_of_an_unknown_lease_is_false_and_logs_nothing(env):
    faked_machine(env)
    assert env.lease.discard("nope") is False
    assert not [e for e in events(env) if e["event"] == "discard"]

