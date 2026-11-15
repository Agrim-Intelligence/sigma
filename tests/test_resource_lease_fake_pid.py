"""#2228: the fake-pid session tests must hold when a real, long-lived process occupies pid 100.

Kept in its own file so the guard's bytes do not change when `_take_over` (the fix, in
tests/test_resource_lease.py) does."""

import pytest

import test_resource_lease as base
from test_resource_lease import lease  # noqa: F401  (fixture)


@pytest.mark.parametrize("body", [
    "test_peak_taken_while_the_session_runs_is_what_release_records",
    "test_ring_unit_is_bytes_per_sizeable_process_of_the_run",
], ids=["peak", "ring"])
def test_session_tests_hold_with_a_live_process_at_the_fake_pid(lease, monkeypatch, body):  # noqa: F811
    """#2228: a real, long-lived process 100 must not turn the fake-pid premise hostile.

    The wrapper simulates that occupant with a FIXED start far outside START_TOLERANCE (never
    computed from the real pid 100, which is usually absent), then runs the real body of each
    named test. The bodies must make the record say which process it claims to be."""
    real = lease.process_start
    monkeypatch.setattr(lease, "process_start", lambda pid: 1e6 if pid == 100 else real(pid))
    getattr(base, body)(lease, monkeypatch)
