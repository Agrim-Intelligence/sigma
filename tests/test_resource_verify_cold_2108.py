"""#2108 - a cold cost ring admits a verify at more than one worker when the machine has room for them.

No model call, no network. The machine is the faked 16 GiB, 8-cpu one of test_resource_verify_1601 (reserve 3.2 GiB,
share cap 9.6 GiB, so the derived cold estimate of 2 GiB per worker (1/8 of total) leaves room for 4 and the cpu
count wants 7).
The state folder is always a temp one. Controls (break one clause alone, see red) are named in each docstring and
were run before the code was trusted; the UNMEASURED real-machine number is stated in docs/parallelism.md.
"""
import json
import pathlib

import pytest

from test_resource_verify_1601 import GIB, MIB, _restore_guard_modules, env  # noqa: F401  (fixtures)

ROOT = pathlib.Path(__file__).resolve().parent.parent


def admit(env, kind="verify", want=7, config=None, goal="g1"):
    return env.lease.admit(kind, want, "r", goal, config=config)


def seed_other(env, kind, values):
    path = env.state / "observed.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({kind: values}))


# ------------------------------------------------------------------------------------------------- AC-1
def test_ac1_empty_state_with_room_for_two_runs_more_than_one_worker(env, tmp_path):
    """The documented gesture: a `-n {workers}` command through `resource_verify.run` on an empty state folder.
    Control: dropping the `room // estimate` division (bound back to 1) turns this red (granted 1)."""
    assert not (env.state / "observed.json").exists()
    out = env.run("verify", "echo {workers} $SIGMA_VERIFY_WORKERS $PYTEST_XDIST_AUTO_NUM_WORKERS -n {workers}",
                  str(tmp_path), 30, "g1")
    assert out.hold is None and out.returncode == 0
    assert out.workers == 4
    assert out.outputs[0].split()[:3] == ["4", "4", "4"]


def test_the_cold_grant_reserves_what_it_admitted_and_marks_the_lease_cold(env):
    got = admit(env)
    record = json.loads((env.state / "leases" / (got.id + ".json")).read_text())
    assert got.n == 4 and record["cold"] is True and record["est_mem"] == 4 * 2 * GIB


def test_a_machine_estimate_is_a_ratio_of_the_machine(env):
    """Derived from the machine, never a laptop constant: one eighth of total memory."""
    g = env.lease.guard
    small = g.Sample(total=2 * GIB, available=GIB, swap_used=0, swap_total=0, swapouts=0, swapouts_at=0.0,
                     pressure=1, cpu_count=2, load1=0.0)
    big = g.Sample(total=64 * GIB, available=GIB, swap_used=0, swap_total=0, swapouts=0, swapouts_at=0.0,
                   pressure=1, cpu_count=2, load1=0.0)
    assert g.derive(small).verify_cold_cost == 256 * MIB
    assert g.derive(big).verify_cold_cost == 8 * GIB


# ------------------------------------------------------------------------------------------------- AC-2
@pytest.mark.parametrize("available_gib", [6, 3.5])
def test_ac2_room_for_fewer_than_two_estimated_workers_runs_one(env, available_gib):
    """6 GiB: headroom 2.8 GiB, one 2 GiB estimate fits and two do not. 3.5 GiB: headroom 0.3 GiB, under one estimate,
    still one (the `max(1, ...)` floor). Controls: `want` instead of the division turns the first red; dropping the
    floor turns the second red (granted 0)."""
    env.box["available"] = int(available_gib * GIB)
    assert admit(env).n == 1


def test_ac2_no_room_above_the_reserve_holds(env):
    """Control: removing the `room <= 0` hold turns this red (a cold verify would be admitted at 1)."""
    env.box["available"] = 3 * GIB            # below the 3.2 GiB reserve, above the critical half of it
    got = admit(env)
    assert isinstance(got, env.lease.Hold) and "no room above the reserve" in got.reason


def test_a_second_cold_verify_still_holds_while_the_first_runs(env):
    first = admit(env)
    second = admit(env, goal="g2")
    assert first.n == 4 and isinstance(second, env.lease.Hold) and "no observation of verify" in second.reason


def test_the_worker_cap_applies_on_a_cold_ring(env):
    """Control: leaving the cap off the cold branch turns this red (granted 4)."""
    assert admit(env, config={"max_verify_workers": 2}).n == 2


def test_the_cold_grant_never_exceeds_what_the_caller_wanted(env):
    assert admit(env, want=3).n == 3


# ------------------------------------------------------------------------------------------------- the key
@pytest.mark.parametrize("mb,expected", [(4096, 2), (16384, 1)])
def test_a_larger_configured_estimate_admits_fewer(env, mb, expected):
    """8.8 GiB of room: 4 GiB per worker fits two; 16 GiB per worker fits none, and a run is still granted one."""
    assert admit(env, config={"verify_cold_mb": mb}).n == expected


def test_a_smaller_configured_estimate_admits_more_up_to_want(env):
    assert admit(env, config={"verify_cold_mb": 256}).n == 7


@pytest.mark.parametrize("bad", [0, -5, True, 1.5, "512", None, float("nan")])
def test_a_bad_estimate_reads_as_the_derived_one_with_a_note(env, bad):
    g = env.lease.guard
    resolved, notes = g.resolve_config({"verify_cold_mb": bad})
    assert resolved["verify_cold_mb"] is None
    assert (len(notes) == 1 and "verify_cold_mb" in notes[0]) or bad is None
    assert admit(env, config={"verify_cold_mb": bad}).n == 4


# ------------------------------------------------------------------------------------------ unchanged paths
def test_a_literal_n_on_a_cold_ring_still_runs_that_many_workers(env, tmp_path):
    """The lease is cold but reserves an estimate; the literal path must key on `cold`, not on `est_mem == 0`.
    Control: keying it on `est_mem > 0` again holds this literal 12 ("literal -n 12 needs 12 workers, 4 fit")."""
    out = env.run("verify", "echo $SIGMA_VERIFY_WORKERS -n 12", str(tmp_path), 30, "g1", config={"mode": "enforce"})
    assert out.hold is None and out.workers == 12 and out.outputs[0].split()[0] == "12"


def test_a_warm_ring_is_untouched_by_the_estimate(env):
    seed_other(env, "verify", [GIB])
    got = admit(env, config={"verify_cold_mb": 64})
    record = json.loads((env.state / "leases" / (got.id + ".json")).read_text())
    assert got.n == 7 and "cold" not in record


@pytest.mark.parametrize("kind", ["goal", "unknown", "doctor"])
def test_every_other_kind_stays_at_one_unit_on_a_cold_ring(env, kind):
    """AC-3. Control: letting the cold branch apply to every kind turns this red (granted 4)."""
    first = admit(env, kind=kind, want=7)
    second = admit(env, kind=kind, want=7, goal="g2")
    assert first.n == 1
    assert isinstance(second, env.lease.Hold) and "no observation of %s" % kind in second.reason


def test_a_session_keeps_its_own_cold_bound(env):
    """room 8.8 GiB // session_cost (16 GiB / 32 = 0.5 GiB) = 17 sessions; unchanged by the verify estimate."""
    grants = [admit(env, kind="session", want=1, goal="g%d" % i) for i in range(17)]
    assert all(getattr(g, "n", None) == 1 for g in grants)
    assert isinstance(admit(env, kind="session", want=1, goal="g99"), env.lease.Hold)


def test_observe_mode_still_grants_what_was_wanted(env):
    env.box["available"] = 3 * GIB
    got = admit(env, want=5, config={"mode": "observe"})
    assert got.n == 5


# ------------------------------------------------------------------------------------------------- docs
def test_the_docs_and_the_doctor_state_the_cold_verify_bound():
    """AC-4. Control: delete the key row or the sentence and this goes red."""
    cfg = " ".join((ROOT / "docs" / "configuration.md").read_text().split())
    par = " ".join((ROOT / "docs" / "parallelism.md").read_text().split())
    doctor = (ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py").read_text()
    assert "`resource.verify_cold_mb`" in cfg and "cold ring" in cfg
    cold = par[par.index("**Cold start.**"):par.index("**The ring unit.**")]
    for needle in ("`verify`", "verify_cold_mb", "one eighth", "UNMEASURED", "shed"):
        assert needle in cold, needle
    assert "first verify" in par and "cold ring" in doctor and "verify_cold_mb" in doctor
    tmpl = json.loads((ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text())
    assert tmpl["resource"]["verify_cold_mb"] is None
