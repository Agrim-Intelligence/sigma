"""The journal switch: lock x lease x config, resolved in one place (`ledger.journal_on`).

S1-G3 / #2574, PRD §6.2. The three-step resolution is:

  1. a refusing managed-settings status (`access-revoked`, `locked-key-unverifiable`) -> OFF
  2. status `ok` AND `journal.enabled` present in `locked` -> the LOCKED value, but only while the
     lease is fresh; a stale lease is TERMINAL OFF, never a fall-through to config (plan Delta-1)
  3. otherwise -> the local config's `journal.enabled`, strict `is True` (the only key read since
     #2706 dropped the one-release alias; `tests/test_no_private_names.py` proves the old key and
     every other private name switch nothing)

WHY THE FIXTURE CONFIGS CARRY NO `journal` KEY IN THE LOCK CASES (PRD §8, plan control C-2). If the
fixture config also said `journal.enabled: true`, the user opt-in would already turn the journal on
and removing the lease check from `journal_on` would change nothing -- the control that is supposed
to prove the lease is load-bearing would pass against the very bug it targets. Every locked-branch
case below therefore starts from a config with NO `journal` key at all.

WHY ONE CASE WRITES THE FILE NOW AND BACK-DATES ONLY THE FIELD
(`test_lease_reads_the_refreshed_at_field_not_the_file_mtime`). An mtime-based lease passes every
other case in this file by accident: a fixture file is always written during the test, so its mtime
is always fresh. That one case is the only one that goes red against an mtime implementation, and
it is why the lease keys on `refreshed_at`. A `cp`, a `git checkout` or a restored backup resets
mtime and would otherwise renew a dead lease forever (plan R-7).

One tmp dir per case: `_managed_read_cached` keys its cache on the resolved path, so two dirs can
never resolve each other's answer.
"""
import importlib.util
import json
import os
import pathlib
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")

DAY = 86400


def _sdlc(tmp_path, name=".sdlc"):
    d = tmp_path / name
    d.mkdir(parents=True)
    return d


def _managed(sdlc_dir, *, status="ok", locked=None, refreshed_at="now", version=1, raw=None):
    """Write `.sdlc/managed-settings.json`. `refreshed_at="now"` stamps the current time."""
    path = sdlc_dir / "managed-settings.json"
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    if refreshed_at == "now":
        refreshed_at = _stamp(time.time())
    body = {"version": version, "status": status, "issued_at": _stamp(time.time() - DAY)}
    if refreshed_at is not None:
        body["refreshed_at"] = refreshed_at
    if locked is not None:
        body["locked"] = locked
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _stamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# ------------------------------------------------------------------ step 3: config alone


def test_not_adopted_config_true_turns_journal_on(tmp_path):
    d = _sdlc(tmp_path)
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is True


@pytest.mark.parametrize("block", [
    {"enabled": False},
    {},
    {"enabled": "true"},
    {"enabled": 1},
])
def test_not_adopted_config_is_strict_is_true(tmp_path, block):
    """A truthy string or a stray 1 must not switch a write surface on silently -- the same strict
    `is True` rule `enabled()` and the old `telemetry_enabled()` already used."""
    d = _sdlc(tmp_path)
    assert ledger.journal_on(d, {"journal": block}) is False


def test_not_adopted_absent_journal_key_is_off(tmp_path):
    d = _sdlc(tmp_path)
    assert ledger.journal_on(d, {}) is False
    assert ledger.journal_on(d, {"ledger": {"enabled": True}}) is False


@pytest.mark.parametrize("cfg", [
    {"journal": "on"},
    {"journal": True},
    {"journal": None},
    "not-a-dict",
    None,
])
def test_a_hand_edited_non_dict_block_never_raises(tmp_path, cfg):
    """The switch now sits at the TOP of `append()`, so the blast radius of an AttributeError here
    is every phase boundary, not one write. The config and the block are both isinstance-guarded
    (plan N-7)."""
    d = _sdlc(tmp_path)
    assert ledger.journal_on(d, cfg) is False


# ------------------------------------------------------------------ step 2: the lock + the lease


def test_locked_true_with_a_fresh_lease_turns_journal_on(tmp_path):
    """Config carries NO `journal` key (PRD §8): the lock alone is what turns it on."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=_stamp(time.time() - 3600))
    assert ledger.journal_on(d, {}) is True


def test_lease_eight_days_old_turns_journal_off(tmp_path):
    """C-2's case. Stale is TERMINAL OFF -- it does NOT fall through to config (plan Delta-1)."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=_stamp(time.time() - 8 * DAY))
    assert ledger.journal_on(d, {}) is False


def test_lease_stale_is_terminal_and_never_falls_back_to_config(tmp_path):
    """The distinguishing assertion for Delta-1: a config that says `true` does NOT rescue a stale
    lease. Under fall-through semantics this would be True, and PRD §6.2's stated Ceiling ("a
    policy writer dead for more than 7 days stops the journal until it is revived") would mean nothing
    on any repo whose config says true."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=_stamp(time.time() - 8 * DAY))
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is False


def test_lease_missing_turns_journal_off(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=None)
    assert ledger.journal_on(d, {}) is False


@pytest.mark.parametrize("value", ["", "not-a-date", "2026-13-45T99:99:99Z", 12345, True, []])
def test_lease_unparseable_turns_journal_off(tmp_path, value):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=value)
    assert ledger.journal_on(d, {}) is False


def test_lease_future_dated_turns_journal_off(tmp_path):
    """A future stamp is not "fresh" -- a corrected clock or a restored backup would otherwise
    renew a dead lease forever. Mirrors `timing_store.py:307`'s own `0 <= t - ...` guard."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, refreshed_at=_stamp(time.time() + 3 * DAY))
    assert ledger.journal_on(d, {}) is False


def test_lease_reads_the_refreshed_at_field_not_the_file_mtime(tmp_path):
    """THE CASE THAT KILLS AN MTIME IMPLEMENTATION (plan R-7, control C-2 decoration (b)).

    The file is written NOW -- its mtime is seconds old -- and only `refreshed_at` is back-dated.
    A lease keyed on `stat().st_mtime` reads this as fresh and returns ON."""
    d = _sdlc(tmp_path)
    path = _managed(d, locked={"journal.enabled": True},
                    refreshed_at=_stamp(time.time() - 8 * DAY))
    assert time.time() - path.stat().st_mtime < 60, "fixture must have a fresh mtime to be a control"
    assert ledger.journal_on(d, {}) is False


def test_lease_boundary_just_inside_seven_days_is_still_fresh(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True},
             refreshed_at=_stamp(time.time() - (7 * DAY - 3600)))
    assert ledger.journal_on(d, {}) is True


def test_lease_boundary_just_past_seven_days_is_stale(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True},
             refreshed_at=_stamp(time.time() - (7 * DAY + 3600)))
    assert ledger.journal_on(d, {}) is False


def test_locked_false_beats_a_config_true(tmp_path):
    """The locked branch returns first: an org that locks the journal OFF cannot be overridden by
    a developer editing their own config."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": False})
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is False


def test_locked_true_beats_a_config_false(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True})
    assert ledger.journal_on(d, {"journal": {"enabled": False}}) is True


@pytest.mark.parametrize("value", ["true", 1, None, {}])
def test_locked_value_is_strict_is_true_as_well(tmp_path, value):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": value})
    assert ledger.journal_on(d, {}) is False


def test_the_lock_is_per_key_so_config_still_decides_when_journal_is_unlocked(tmp_path):
    """`locked` carrying OTHER keys does not freeze the journal -- only `journal.enabled` does."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"work.require_review": "changes"})
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is True
    e = _sdlc(tmp_path, name=".agrim-b")
    _managed(e, locked={"work.require_review": "changes"})
    assert ledger.journal_on(e, {}) is False


def test_an_empty_lock_with_a_stale_lease_still_falls_through_to_config(tmp_path):
    """The lease only gates the LOCKED branch. With no journal lock there is no locked value to
    hold stale, so a stale file must not turn a developer's own opt-in off."""
    d = _sdlc(tmp_path)
    _managed(d, locked={}, refreshed_at=_stamp(time.time() - 30 * DAY))
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is True


# ------------------------------------------------------------------ step 1: refusing statuses


def test_access_revoked_turns_journal_off(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, status="access-revoked")
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is False


@pytest.mark.parametrize("kwargs", [
    {"raw": "{not json at all"},
    {"raw": ""},
    {"version": 2},
    {"status": "some-future-status"},
    {"raw": json.dumps([1, 2, 3])},
])
def test_locked_key_unverifiable_turns_journal_off(tmp_path, kwargs):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": True}, **kwargs)
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is False


def test_an_unreadable_managed_settings_file_never_raises(tmp_path):
    """`journal_on` never raises: anything the managed read cannot resolve degrades, it does not
    propagate. A directory where the file should be is the cheap portable stand-in for EACCES."""
    d = _sdlc(tmp_path)
    (d / "managed-settings.json").mkdir()
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) in (True, False)


# ------------------------------------------------------------------ the cached read


def test_the_cache_keys_on_path_so_two_checkouts_never_share_an_answer(tmp_path):
    a = _sdlc(tmp_path, name="a")
    b = _sdlc(tmp_path, name="b")
    _managed(a, locked={"journal.enabled": True})
    _managed(b, locked={"journal.enabled": False})
    assert ledger.journal_on(a, {}) is True
    assert ledger.journal_on(b, {}) is False
    assert ledger.journal_on(a, {}) is True


def test_the_cache_holds_the_file_read_not_the_resolved_boolean(tmp_path):
    """Config is a caller-supplied argument; folding it into the cache key would make a changed
    config read stale. The same dir must answer differently for two different configs."""
    d = _sdlc(tmp_path)
    _managed(d, locked={"work.require_review": "changes"})
    assert ledger.journal_on(d, {"journal": {"enabled": True}}) is True
    assert ledger.journal_on(d, {"journal": {"enabled": False}}) is False


def test_the_cache_reloads_when_the_file_changes(tmp_path):
    d = _sdlc(tmp_path)
    _managed(d, locked={"journal.enabled": False})
    assert ledger.journal_on(d, {}) is False
    _managed(d, locked={"journal.enabled": True, "work.require_review": "changes"})
    os.utime(d / "managed-settings.json", (time.time() + 2, time.time() + 2))
    assert ledger.journal_on(d, {}) is True


# ------------------------------------------------------------------ the core-side lockable list


def test_lockable_keys_names_the_three_keys_the_core_recognises():
    """S1-G3 CREATES this constant -- `managed_settings.py`'s own list was prose only, with no
    constant anywhere in the core (a downstream reader's sibling list lives on the private side,
    which the core may not import)."""
    assert ledger.LOCKABLE_KEYS == (
        "work.require_review", "gates.hard_plan_gate", "journal.enabled")


def test_journal_enabled_is_the_config_half_on_its_own():
    assert ledger.journal_enabled({"journal": {"enabled": True}}) is True
    assert ledger.journal_enabled({}) is False
