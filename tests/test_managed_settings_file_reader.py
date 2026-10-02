"""Unit tests for managed_settings.py file-based reader for org policy.

Tests the four-status vocabulary and file-reading logic without subprocess or private-side dependencies.
All tests use tmp_path fixtures to mock the managed-settings.json file."""

import importlib.util
import json
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"


def _load(name):
    """Load a module from skills/agrim-loop/scripts/ without importing it into the package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


managed_settings = _load("managed_settings")


# ============================================================================== is_adopted tests

def test_is_adopted_with_project_id_declared(tmp_path):
    """Checkout with `managed_settings.project_id` declared is adopted."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    assert managed_settings.is_adopted(str(sdlc_dir), config) is True


def test_is_adopted_with_managed_settings_file(tmp_path):
    """Checkout with managed-settings.json file is adopted."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text("{}")
    config = {}
    assert managed_settings.is_adopted(str(sdlc_dir), config) is True


def test_is_adopted_neither_signal_false(tmp_path):
    """Checkout with no project_id and no file is not adopted."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    config = {}
    assert managed_settings.is_adopted(str(sdlc_dir), config) is False


def test_is_adopted_stat_fails_returns_false(tmp_path):
    """If file existence check fails, treated as not adopted."""
    # Non-existent directory: stat will fail
    assert managed_settings.is_adopted("/nonexistent/.sdlc", {}) is False


# ============================================================================== read tests

def test_read_file_absent_returns_not_adopted(tmp_path):
    """File absent → not-adopted status, no locked dict, no refreshed_at."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_NOT_ADOPTED
    assert locked_dict is None
    assert refreshed_at is None


def test_read_invalid_json_returns_locked_key_unverifiable(tmp_path):
    """Invalid JSON → locked-key-unverifiable status."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text("{invalid json")
    status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE
    assert locked_dict is None


def test_read_unknown_version_returns_locked_key_unverifiable(tmp_path):
    """Unknown version → locked-key-unverifiable status."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 99,
        "status": "ok",
        "locked": {}
    }))
    status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE


def test_read_unknown_status_returns_locked_key_unverifiable(tmp_path):
    """Unknown status value → locked-key-unverifiable status."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "banana",
        "locked": {}
    }))
    status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE


def test_read_valid_ok_returns_status_and_locked_dict(tmp_path):
    """Valid file with status=ok → ok status, locked dict, refreshed_at."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    locked_dict = {
        "work.require_review": "approval",
        "gates.hard_plan_gate": True,
        "journal.enabled": True
    }
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "ok",
        "issued_at": "2026-09-18T10:00:00Z",
        "refreshed_at": "2026-09-18T10:30:00Z",
        "locked": locked_dict
    }))
    status, returned_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_OK
    assert returned_dict == locked_dict
    assert refreshed_at == "2026-09-18T10:30:00Z"


def test_read_valid_access_revoked_returns_status(tmp_path):
    """Valid file with status=access-revoked → access-revoked status, None locked dict."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "access-revoked",
        "issued_at": "2026-09-18T10:00:00Z",
        "refreshed_at": "2026-09-18T10:30:00Z"
    }))
    status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
    assert status == managed_settings.STATUS_ACCESS_REVOKED
    assert locked_dict is None
    assert refreshed_at == "2026-09-18T10:30:00Z"


def test_read_unreadable_file_returns_locked_key_unverifiable(tmp_path):
    """File exists but cannot be read (permissions) → locked-key-unverifiable."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text("{}")
    # Make file unreadable
    settings_file.chmod(0o000)
    try:
        status, locked_dict, refreshed_at = managed_settings.read(str(sdlc_dir))
        assert status == managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE
    finally:
        # Restore permissions for cleanup
        settings_file.chmod(0o644)


# ============================================================================== gated_check tests

def test_gated_check_unadopted_returns_ok_allowed(tmp_path):
    """Unadopted checkout → status=not-adopted, allowed=True."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    config = {}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_NOT_ADOPTED
    assert result["allowed"] is True
    assert result["key"] == "work.require_review"
    assert result["value"] is None
    assert result["locked"] is False


def test_gated_check_adopted_file_missing_still_returns_not_adopted(tmp_path):
    """Adopted but file missing → still not-adopted, allowed=True."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    # Declare adoption but don't create file
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_NOT_ADOPTED
    assert result["allowed"] is True


def test_gated_check_ok_with_locked_key(tmp_path):
    """File with status=ok, key is locked → returns locked value."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "ok",
        "locked": {
            "work.require_review": "approval",
            "gates.hard_plan_gate": True
        }
    }))
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_OK
    assert result["allowed"] is True
    assert result["value"] == "approval"
    assert result["locked"] is True


def test_gated_check_ok_without_this_key(tmp_path):
    """File with status=ok but key not in locked dict → value=None, locked=False."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "ok",
        "locked": {
            "gates.hard_plan_gate": True
        }
    }))
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_OK
    assert result["allowed"] is True
    assert result["value"] is None
    assert result["locked"] is False


def test_gated_check_access_revoked(tmp_path):
    """File with status=access-revoked → allowed=False."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text(json.dumps({
        "version": 1,
        "status": "access-revoked"
    }))
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_ACCESS_REVOKED
    assert result["allowed"] is False


def test_gated_check_locked_key_unverifiable(tmp_path):
    """File unreadable/invalid → locked-key-unverifiable, allowed=False."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    settings_file = sdlc_dir / managed_settings.MANAGED_SETTINGS_FILENAME
    settings_file.write_text("{invalid")
    config = {managed_settings.CONFIG_KEY: {"project_id": "proj-1"}}
    result = managed_settings.gated_check(str(sdlc_dir), config, "work.require_review")
    assert result["status"] == managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE
    assert result["allowed"] is False


# ============================================================================== Constants defined

def test_status_constants_are_defined():
    """All status constants are defined."""
    assert managed_settings.STATUS_OK == "ok"
    assert managed_settings.STATUS_NOT_ADOPTED == "not-adopted"
    assert managed_settings.STATUS_ACCESS_REVOKED == "access-revoked"
    assert managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE == "locked-key-unverifiable"


def test_refusing_statuses_contains_refusals():
    """REFUSING_STATUSES contains the statuses that cause gate refusal."""
    assert managed_settings.STATUS_ACCESS_REVOKED in managed_settings.REFUSING_STATUSES
    assert managed_settings.STATUS_LOCKED_KEY_UNVERIFIABLE in managed_settings.REFUSING_STATUSES
    assert managed_settings.STATUS_ENROLLED_POLICY_MISSING in managed_settings.REFUSING_STATUSES
    assert managed_settings.STATUS_NOT_ADOPTED not in managed_settings.REFUSING_STATUSES
    assert managed_settings.STATUS_OK not in managed_settings.REFUSING_STATUSES


# ============================================================================== D1 (#2580): the adoption key

def test_the_config_key_is_managed_settings():
    assert managed_settings.CONFIG_KEY == "managed_settings"


def test_the_config_key_adopts(tmp_path):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    config = {managed_settings.CONFIG_KEY: {"project_id": "  proj-1  "}}
    assert managed_settings.declared_project_id(config) == "proj-1"
    assert managed_settings.is_adopted(str(sdlc_dir), config) is True


@pytest.mark.parametrize("value", ["", "   ", 7, None, ["p"], True])
def test_blank_non_string_or_non_dict_values_do_not_adopt(tmp_path, value):
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    key = managed_settings.CONFIG_KEY
    for config in ({key: {"project_id": value}}, {key: value}):
        assert managed_settings.declared_project_id(config) is None, config
        assert managed_settings.is_adopted(str(sdlc_dir), config) is False, config


def test_a_declared_id_with_no_file_is_byte_identical_to_undeclared(tmp_path):
    """K3's truth table: the key's only observable gate effect is none. With no managed-settings
    file, a declared id and no declaration return the SAME `gated_check` dict."""
    sdlc_dir = tmp_path / ".sdlc"
    sdlc_dir.mkdir()
    undeclared = managed_settings.gated_check(str(sdlc_dir), {}, "gates.hard_plan_gate")
    declared = managed_settings.gated_check(
        str(sdlc_dir), {managed_settings.CONFIG_KEY: {"project_id": "p1"}}, "gates.hard_plan_gate")
    assert declared == undeclared
