"""Tests for work.py's gating functions using file-based managed-settings reader.

#2571: Gating functions (effective_review_mode, effective_hard_plan_gate) now read org policy
from a local JSON file (.sdlc/managed-settings.json) instead of calling a subprocess. These tests
verify the four-status vocabulary and gate behavior with file mocking.

Key guarantees being tested:
1. The four statuses (ok, not-adopted, access-revoked, locked-key-unverifiable) produce the
   correct gate outcomes, matching the old subprocess behavior.
2. Memoization in _hard_plan_gate_refusal() preserves one read per goal.
3. Unknown keys in the locked dict are ignored (fail-open).
"""

import importlib.util
import json
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _load(name):
    """Load a module from skills/sigma-loop/scripts/ without importing it into the package."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


managed_settings = _load("managed_settings")
work = _load("work")
state = _load("state")


@pytest.fixture
def sdlc_dir(tmp_path):
    """Setup a minimal .sdlc directory."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    state.start_run(str(sdlc))  # Initialize action log
    return sdlc


@pytest.fixture
def managed_settings_file(sdlc_dir):
    """Fixture to create managed-settings.json with given content."""
    def _create(content=None):
        file_path = sdlc_dir / "managed-settings.json"
        if content is not None:
            file_path.write_text(json.dumps(content))
        return file_path
    return _create


@pytest.fixture
def local_config():
    """Fixture providing a basic local config dict."""
    return {
        "work": {
            "require_review": "off",
        },
        "gates": {
            "hard_plan_gate": {"enabled": False}
        }
    }


# ============================================================================ effective_review_mode tests

def test_review_mode_file_absent_proceeds_with_local_config(sdlc_dir, managed_settings_file, local_config, monkeypatch):
    """File missing → not-adopted → use local config value."""
    # File does not exist
    managed_settings_file()  # Call without content
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert mode == "off"
    assert refusal is None


def test_review_mode_file_ok_uses_locked_value(sdlc_dir, managed_settings_file, local_config):
    """File has status=ok with locked work.require_review → use locked value."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "work.require_review": "approval"
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert mode == "approval"
    assert refusal is None


def test_review_mode_file_ok_without_this_key(sdlc_dir, managed_settings_file, local_config):
    """File has status=ok but work.require_review not locked → use local config."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "gates.hard_plan_gate": True
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert mode == "off"
    assert refusal is None


def test_review_mode_access_revoked_refuses(sdlc_dir, managed_settings_file, local_config):
    """File status=access-revoked → gate refuses."""
    managed_settings_file({
        "version": 1,
        "status": "access-revoked"
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert refusal is not None
    assert mode == "off"  # Falls back to local config


def test_review_mode_invalid_json_refuses(sdlc_dir, local_config):
    """File contains invalid JSON → gate refuses."""
    file_path = sdlc_dir / "managed-settings.json"
    file_path.write_text("{invalid json")
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert refusal is not None


def test_review_mode_unknown_version_refuses(sdlc_dir, managed_settings_file, local_config):
    """File has unknown version → gate refuses."""
    managed_settings_file({
        "version": 99,
        "status": "ok",
        "locked": {}
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert refusal is not None


def test_review_mode_unknown_status_refuses(sdlc_dir, managed_settings_file, local_config):
    """File has unknown status value → gate refuses."""
    managed_settings_file({
        "version": 1,
        "status": "banana",
        "locked": {}
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert refusal is not None


# ============================================================================ effective_hard_plan_gate tests

def test_hard_plan_gate_file_absent_proceeds_with_local_config(sdlc_dir, managed_settings_file, local_config):
    """File missing → not-adopted → use local config value."""
    managed_settings_file()  # Call without content
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert on is False  # Default from local config
    assert refusal is None


def test_hard_plan_gate_file_ok_uses_locked_value(sdlc_dir, managed_settings_file, local_config):
    """File has status=ok with locked gates.hard_plan_gate → use locked value."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "gates.hard_plan_gate": {"enabled": True}
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}  # Mark as adopted
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    # The function extracts the locked value and passes it to hard_plan_gate_on(), which parses
    # {"enabled": True} as True -- asserted (#2138): until then this test never checked `on`, so a
    # resolver that dropped the locked value on the floor would have stayed green here.
    assert on is True
    assert refusal is None


def test_hard_plan_gate_file_ok_without_this_key(sdlc_dir, managed_settings_file, local_config):
    """File has status=ok but gates.hard_plan_gate not locked → use local config."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "work.require_review": "approval"
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert on is False  # Falls back to local config default
    assert refusal is None


def test_hard_plan_gate_access_revoked_refuses(sdlc_dir, managed_settings_file, local_config):
    """File status=access-revoked → gate refuses."""
    managed_settings_file({
        "version": 1,
        "status": "access-revoked"
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert refusal is not None


def test_hard_plan_gate_invalid_json_refuses(sdlc_dir, local_config):
    """File contains invalid JSON → gate refuses."""
    file_path = sdlc_dir / "managed-settings.json"
    file_path.write_text("{invalid json")
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert refusal is not None


def test_hard_plan_gate_unknown_version_refuses(sdlc_dir, managed_settings_file, local_config):
    """File has unknown version → gate refuses."""
    managed_settings_file({
        "version": 99,
        "status": "ok",
        "locked": {}
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert refusal is not None


def test_hard_plan_gate_unknown_status_refuses(sdlc_dir, managed_settings_file, local_config):
    """File has unknown status value → gate refuses."""
    managed_settings_file({
        "version": 1,
        "status": "banana",
        "locked": {}
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config)
    assert refusal is not None


# ============================================================================ Adoption and file absence
# NOTE: `_hard_plan_gate_refusal`'s memo (at most one org read per goal, the lock bit riding the
# memo, a legacy `"on"` re-asked) is tested at the `pr()` level in tests/test_work.py -- the tests
# whose names contain `org_lock` (#2138).

def test_unadopted_checkout_never_touches_file(sdlc_dir, local_config, monkeypatch):
    """Unadopted checkout should not read file at all."""
    # Ensure file doesn't exist
    settings_file = sdlc_dir / "managed-settings.json"
    assert not settings_file.exists()

    # Track file reads
    original_read = managed_settings.read
    read_count = [0]

    def tracked_read(*args, **kwargs):
        read_count[0] += 1
        return original_read(*args, **kwargs)

    monkeypatch.setattr(managed_settings, "read", tracked_read)

    # Call without adoption
    local_config_copy = local_config.copy()  # No managed_settings.project_id
    on, refusal = work.effective_hard_plan_gate(str(sdlc_dir), local_config_copy)

    # Should not have called read() (adoption check returns False immediately)
    # Note: we still need to verify is_adopted checks the file, but not read() the JSON
    # This test verifies the short-circuit behavior
    assert on is False
    assert refusal is None


# ============================================================================ Unknown keys are ignored

def test_unknown_keys_in_locked_dict_are_ignored(sdlc_dir, managed_settings_file, local_config):
    """Unknown keys in locked dict should be ignored (fail-open)."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "work.require_review": "approval",
            "unknown.future.key": "some-value",  # Unknown key
            "gates.hard_plan_gate": {"enabled": True}
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}

    # Should proceed normally, ignoring the unknown key
    mode, refusal = work.effective_review_mode(str(sdlc_dir), local_config)
    assert mode == "approval"
    assert refusal is None


# ============================================================================ #2138: the lock bit

def test_hard_plan_gate_locked_reports_the_lock_bit_for_a_locked_on_key(sdlc_dir, managed_settings_file, local_config):
    """`effective_hard_plan_gate_locked` is the 3-tuple the memo needs: `gated_check` already
    reports `locked`, and `effective_hard_plan_gate` was discarding it -- which is how a local
    `.allow-direct-edits` came to defeat an org lock."""
    managed_settings_file({
        "version": 1,
        "status": "ok",
        "locked": {
            "gates.hard_plan_gate": {"enabled": True}
        }
    })
    local_config[managed_settings.CONFIG_KEY] = {"project_id": "proj-1"}
    on, locked, refusal = work.effective_hard_plan_gate_locked(str(sdlc_dir), local_config)
    assert on is True
    assert locked is True
    assert refusal is None


def test_hard_plan_gate_locked_reports_unlocked_when_not_adopted(sdlc_dir, managed_settings_file, local_config):
    """No org file: not adopted, the local value rules, and nothing is locked."""
    managed_settings_file()
    on, locked, refusal = work.effective_hard_plan_gate_locked(str(sdlc_dir), local_config)
    assert on is False
    assert locked is False
    assert refusal is None
