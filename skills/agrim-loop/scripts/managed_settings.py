#!/usr/bin/env python3
"""File-based reader for org policy: `.sdlc/managed-settings.json`.

The core reads org policy through a local file written by whatever manages this machine's policy
(a device-management tool, or a downstream service's own writer). The core reads the file and
nothing else: no subprocess, no network, no import of anything outside the core.

## Four-status vocabulary

The same four statuses a downstream policy writer uses, so gate behaviour does not depend on which
side wrote the file:

- `ok`: file is valid and has status='ok'. Policy is applied; locked values override config.
- `not-adopted`: file is absent. Use config values. (A declared `managed_settings.project_id`
  with no file reads exactly the same -- see `is_adopted`.)
- `access-revoked`: file has status='access-revoked'. Gate refuses; member is locked out.
- `locked-key-unverifiable`: file is unreadable, invalid JSON, unknown version/status, or a
  locked key the core does not allow. Gate refuses; operator must investigate.

## File format (version 1)

```json
{
  "version": 1,
  "status": "ok",
  "issued_at": "2026-09-18T10:00:00Z",
  "refreshed_at": "2026-09-18T10:30:00Z",
  "locked": {
    "work.require_review": "changes",
    "gates.hard_plan_gate": {"enabled": true},
    "journal.enabled": true
  }
}
```

- `version`: schema version (1 only, for now).
- `status`: one of "ok", "access-revoked", "not-adopted", "locked-key-unverifiable".
- `issued_at`: ISO-8601 timestamp when the server issued the policy.
- `refreshed_at`: ISO-8601 timestamp when the file was last confirmed/updated.
- `locked`: dict of lockable keys. Only present when status='ok'. Unknown keys are ignored
  (fail-open for extensibility).

## Lockable keys

The core recognizes three keys. The authoritative list is `ledger.LOCKABLE_KEYS` (#2574/S1-G3) —
this section describes it, it does not define it:
- `work.require_review`: string value, e.g. "changes", "approval" (same as config)
- `gates.hard_plan_gate`: bool or dict with "enabled" key
- `journal.enabled`: bool. Read through `ledger.journal_on(sdlc_dir, config)`, never re-derived by
  a caller — that function also applies the `refreshed_at` lease (PRD §6.2), which this reader
  returns but deliberately does not interpret.

Unknown keys in the locked dict are ignored and do not cause a refusal. Only a genuinely
invalid file (bad JSON, unknown version, unknown status, or an unsupported NAME) produces
locked-key-unverifiable.
"""
import json
import pathlib
from typing import Optional, Tuple, Dict, Any

#: Status constants -- the shared four-status vocabulary above.
STATUS_OK = "ok"
STATUS_NOT_ADOPTED = "not-adopted"
STATUS_ACCESS_REVOKED = "access-revoked"
STATUS_LOCKED_KEY_UNVERIFIABLE = "locked-key-unverifiable"

#: Statuses that cause a gate refusal.
REFUSING_STATUSES = frozenset({STATUS_ACCESS_REVOKED, STATUS_LOCKED_KEY_UNVERIFIABLE})

#: Filename for managed settings.
MANAGED_SETTINGS_FILENAME = "managed-settings.json"

#: Schema version (only v1 supported).
SCHEMA_VERSION = 1


#: The config block that declares adoption: `{"managed_settings": {"project_id": "..."}}` (#2580, D1).
CONFIG_KEY = "managed_settings"


def declared_project_id(config: Optional[Dict[str, Any]]) -> Optional[str]:
    """`config[CONFIG_KEY]["project_id"]`, stripped, or None. `CONFIG_KEY` is the only block read:
    the one-release read of the old block name (#2580, D1) was dropped by #2706, so a config that
    declares adoption only there is not adopted by the core.

    Never raises on a malformed shape -- a hand-edited config must not crash the merge path."""
    cfg = config if isinstance(config, dict) else {}
    section = cfg.get(CONFIG_KEY)
    if not isinstance(section, dict):
        return None
    declared = section.get("project_id")
    return declared.strip() if isinstance(declared, str) and declared.strip() else None


def is_adopted(sdlc_dir: str, config: Optional[Dict[str, Any]]) -> bool:
    """Has this checkout opted into org policy?

    Two signals, OR'd:
      * `.sdlc/config.json` declares `managed_settings.project_id`
      * managed-settings file exists

    False means: no file read needed, behaviour byte-identical to before. That is every
    unadopted checkout."""
    if declared_project_id(config):
        return True
    try:
        return (pathlib.Path(sdlc_dir) / MANAGED_SETTINGS_FILENAME).exists()
    except OSError:
        return False


def read(sdlc_dir: str, refresh_needed: bool = False) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """Read managed-settings.json and return (status, locked_dict, refreshed_at).

    Args:
        sdlc_dir: Path to .sdlc directory
        refresh_needed: Currently unused; kept for future extension

    Returns:
        Tuple of (status, locked_dict, refreshed_at):
        - status: one of STATUS_OK, STATUS_NOT_ADOPTED, STATUS_ACCESS_REVOKED, STATUS_LOCKED_KEY_UNVERIFIABLE
        - locked_dict: the "locked" object if status==STATUS_OK, else None
        - refreshed_at: ISO-8601 timestamp if present and readable, else None

    Never raises. All edge cases (ENOENT, EACCES, JSONDecodeError, schema errors) are
    returned as locked-key-unverifiable with reason None."""
    file_path = pathlib.Path(sdlc_dir) / MANAGED_SETTINGS_FILENAME

    # File absent is not-adopted (unadopted checkout or not enrolled yet)
    if not file_path.exists():
        return (STATUS_NOT_ADOPTED, None, None)

    # Try to read and parse JSON
    try:
        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        # Unreadable or invalid JSON: lock the gate
        return (STATUS_LOCKED_KEY_UNVERIFIABLE, None, None)

    if not isinstance(data, dict):
        return (STATUS_LOCKED_KEY_UNVERIFIABLE, None, None)

    # Validate schema version
    version = data.get("version")
    if version != SCHEMA_VERSION:
        return (STATUS_LOCKED_KEY_UNVERIFIABLE, None, None)

    # Extract status
    status = data.get("status")
    refreshed_at = data.get("refreshed_at")

    # Validate status value
    if status not in {STATUS_OK, STATUS_ACCESS_REVOKED, STATUS_LOCKED_KEY_UNVERIFIABLE}:
        # Unknown status value: lock the gate
        return (STATUS_LOCKED_KEY_UNVERIFIABLE, None, None)

    # For ok status, extract locked dict (unknown keys are ignored)
    locked_dict = None
    if status == STATUS_OK:
        locked = data.get("locked")
        if isinstance(locked, dict):
            locked_dict = locked
        else:
            # ok status but no valid locked dict: still ok, just no locks
            locked_dict = {}

    return (status, locked_dict, refreshed_at)


def gated_check(sdlc_dir: str, config: Optional[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """Check if a locked key is allowed.

    Returns a dict in the same result shape a downstream policy reader also uses:
        {
            "key": str,
            "status": str (one of STATUS_*),
            "allowed": bool,
            "value": str or bool or dict or None,
            "locked": bool,
            "reason": None (file-based reader doesn't track reasons)
        }

    Never raises."""

    # Unadopted checkout: proceed with local config
    if not is_adopted(sdlc_dir, config):
        return {
            "key": key,
            "status": STATUS_NOT_ADOPTED,
            "allowed": True,
            "value": None,
            "locked": False,
            "reason": None
        }

    # Read the file
    status, locked_dict, refreshed_at = read(sdlc_dir)

    # Determine reason text for refusing statuses
    reason = None
    if status == STATUS_ACCESS_REVOKED:
        reason = "this checkout's member access is revoked"
    elif status == STATUS_LOCKED_KEY_UNVERIFIABLE:
        reason = "managed settings file is unreadable or contains an invalid value"

    # Build the response
    result = {
        "key": key,
        "status": status,
        "allowed": status not in REFUSING_STATUSES,
        "value": None,
        "locked": False,
        "reason": reason
    }

    # If ok and key is locked, extract the value
    if status == STATUS_OK and locked_dict:
        if key in locked_dict:
            result["value"] = locked_dict[key]
            result["locked"] = True

    return result
