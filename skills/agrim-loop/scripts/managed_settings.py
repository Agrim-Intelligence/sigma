#!/usr/bin/env python3
"""File-based reader for org policy: `.sdlc/managed-settings.json`.

The core reads org policy through a local file written by whatever manages this machine's policy
(a device-management tool, or a downstream service's own writer). The core reads the file and
nothing else: no subprocess, no network, no import of anything outside the core.

## Status vocabulary

The four statuses a downstream policy writer uses, so gate behaviour does not depend on which
side wrote the file, plus one the core adds (#423):

- `ok`: file is valid and has status='ok'. Policy is applied; locked values override config.
- `not-adopted`: file is absent and this checkout has never held a valid policy file. Use config
  values. (A declared `managed_settings.project_id` with no file reads exactly the same -- see
  `is_adopted`: a fresh clone has not been enrolled, and refusing it would be an outage.)
- `enrolled-policy-missing` (#423): file is absent BUT this checkout once held a valid one (see
  "Enrolment memory"). Gate refuses; restore the file or run the `unenroll` lever.
- `access-revoked`: file has status='access-revoked'. Gate refuses; member is locked out.
- `locked-key-unverifiable`: file is unreadable, invalid JSON, unknown version/status, or a
  locked key the core does not allow. Gate refuses; operator must investigate.

## Enrolment memory (#423)

Deleting one file must not switch policy off. When `read()` parses a valid policy file (status
`ok`, `access-revoked` or `locked-key-unverifiable`) it records enrolment, write-once, in two
places, EITHER of which keeps the refusal alive:

- `<sdlc_dir>/state/managed-enrolled.json`
- `<git dir>/sigma-managed-enrolled-<16 hex of sha256(realpath(sdlc_dir))>.json`, where the git dir
  is the one at the sdlc dir's IMMEDIATE parent (`.git/`, or the linked worktree's own git dir
  named by a `.git` file). Found by reading files; no subprocess. Keyed by path so two `.sdlc`
  directories under one git dir never enrol each other.

Both live in Sigma's own Python and git-shared on-disk state: no host hook, so Claude Code,
Cursor and Codex see the same answer. Presence is all that is checked, so a truncated marker
still counts (fail closed).

Residual risk, stated plainly. This is detection and refusal, not a guarantee against a user who
can write the checkout. Anyone who can delete the policy file can also delete both markers, or
run `unenroll`, and the checkout then reads `not-adopted`. What it does defend: a one-file
deletion (or removing `.sdlc/` or `.sdlc/state/`), a stray cleanup, a script that clears
`.sdlc`. Enrolment is also lazy: it is learned at the first read after a valid file arrives, so
a file delivered and deleted before any gate, ledger append or `status` call leaves no memory.
`hooks/plan_gate.sh` (Claude Code only, an accelerator) still reads just the file;
`work.py pr`/`merge` are the host-agnostic enforcement.

Recovery lever: restoring the file clears the refusal on its own; for a deliberate removal run
`python3 managed_settings.py unenroll .sdlc` (local, idempotent, no network). Only the
policy-sensitive gates (merge, PR, journal) refuse; every other command keeps working.

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
import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
from typing import Optional, Tuple, Dict, Any

#: Status constants -- the shared four-status vocabulary above.
STATUS_OK = "ok"
STATUS_NOT_ADOPTED = "not-adopted"
STATUS_ACCESS_REVOKED = "access-revoked"
STATUS_LOCKED_KEY_UNVERIFIABLE = "locked-key-unverifiable"
#: The core's own addition (#423): the file is gone from a checkout that once held a valid one.
STATUS_ENROLLED_POLICY_MISSING = "enrolled-policy-missing"

#: Statuses that cause a gate refusal.
REFUSING_STATUSES = frozenset({STATUS_ACCESS_REVOKED, STATUS_LOCKED_KEY_UNVERIFIABLE,
                               STATUS_ENROLLED_POLICY_MISSING})

#: Enrolment markers (#423); see "Enrolment memory" in the module docstring.
ENROLMENT_STATE_MARKER = ("state", "managed-enrolled.json")
ENROLMENT_GIT_PREFIX = "sigma-managed-enrolled-"

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


def _git_dir(sdlc_dir: str) -> Optional[pathlib.Path]:
    """The git dir of the checkout whose root is `sdlc_dir`'s IMMEDIATE parent, or None.

    `.git` a directory -> it; `.git` a file `gitdir: X` (a linked worktree) -> X. Reads files only,
    no subprocess. Never walks further up: a `.sdlc` nested below a repo root (or a non-git
    project inside someone's dotfiles repo) must not borrow, or write into, an outer repo."""
    try:
        parent = pathlib.Path(sdlc_dir).resolve().parent
        dot_git = parent / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith("gitdir:"):
                target = pathlib.Path(text[len("gitdir:"):].strip())
                target = target if target.is_absolute() else (parent / target)
                target = target.resolve()
                return target if target.is_dir() else None
    except OSError:
        return None
    return None


def enrolment_markers(sdlc_dir: str) -> list:
    """Every path that, if present, says this checkout was enrolled (#423). Pure path math."""
    try:
        base = pathlib.Path(sdlc_dir).resolve()
    except OSError:
        base = pathlib.Path(sdlc_dir)
    markers = [base.joinpath(*ENROLMENT_STATE_MARKER)]
    git_dir = _git_dir(str(base))
    if git_dir is not None:
        digest = hashlib.sha256(str(base).encode("utf-8")).hexdigest()[:16]
        markers.append(git_dir / f"{ENROLMENT_GIT_PREFIX}{digest}.json")
    return markers


def is_enrolled(sdlc_dir: str) -> bool:
    """Has a valid policy file ever been read here? Any one marker suffices. Never raises."""
    for marker in enrolment_markers(sdlc_dir):
        try:
            if marker.exists():
                return True
        except OSError:
            continue
    return False


def record_enrolment(sdlc_dir: str) -> None:
    """Write each missing marker, once (atomic, unique tmp name). Best-effort: never raises, because
    `read()` runs on every ledger append and a read-only disk must not cost a phase boundary."""
    for marker in enrolment_markers(sdlc_dir):
        try:
            if marker.exists():
                continue
            marker.parent.mkdir(parents=True, exist_ok=True)
            tmp = marker.with_name(f".{marker.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
            tmp.write_text(json.dumps({"version": 1, "enrolled_at": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), encoding="utf-8")
            os.replace(tmp, marker)
        except OSError:
            continue


def unenroll(sdlc_dir: str) -> list:
    """The recovery lever: remove every enrolment marker. Idempotent. Returns what was removed."""
    removed = []
    for marker in enrolment_markers(sdlc_dir):
        try:
            marker.unlink()
            removed.append(marker)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return removed


def is_adopted(sdlc_dir: str, config: Optional[Dict[str, Any]]) -> bool:
    """Has this checkout opted into org policy?

    Two signals, OR'd:
      * `.sdlc/config.json` declares `managed_settings.project_id`
      * managed-settings file exists
      * the checkout is enrolled (#423): it held a valid file once, even if that file is gone now

    False means: no file read needed, behaviour byte-identical to before. That is every
    unadopted checkout."""
    if declared_project_id(config):
        return True
    try:
        if (pathlib.Path(sdlc_dir) / MANAGED_SETTINGS_FILENAME).exists():
            return True
    except OSError:
        pass
    return is_enrolled(sdlc_dir)


def read(sdlc_dir: str, refresh_needed: bool = False) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    """Read managed-settings.json and return (status, locked_dict, refreshed_at).

    Args:
        sdlc_dir: Path to .sdlc directory
        refresh_needed: Currently unused; kept for future extension

    Returns:
        Tuple of (status, locked_dict, refreshed_at):
        - status: one of STATUS_OK, STATUS_NOT_ADOPTED, STATUS_ACCESS_REVOKED,
          STATUS_LOCKED_KEY_UNVERIFIABLE, STATUS_ENROLLED_POLICY_MISSING
        - locked_dict: the "locked" object if status==STATUS_OK, else None
        - refreshed_at: ISO-8601 timestamp if present and readable, else None

    Never raises. All edge cases (ENOENT, EACCES, JSONDecodeError, schema errors) are
    returned as locked-key-unverifiable with reason None."""
    file_path = pathlib.Path(sdlc_dir) / MANAGED_SETTINGS_FILENAME

    # File absent: never enrolled -> not-adopted; enrolled (it existed once) -> refusing (#423)
    if not file_path.exists():
        if is_enrolled(sdlc_dir):
            return (STATUS_ENROLLED_POLICY_MISSING, None, None)
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

    # A valid file from the writer enrols this checkout (garbage above never reaches here)
    record_enrolment(sdlc_dir)

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
    elif status == STATUS_ENROLLED_POLICY_MISSING:
        lever = f"python3 {pathlib.Path(__file__).resolve()} unenroll {sdlc_dir}"
        reason = (f"this checkout was enrolled in org policy but {MANAGED_SETTINGS_FILENAME} is "
                  f"missing: restore it from your policy writer, or if this checkout was "
                  f"deliberately removed from org policy run `{lever}` (managed_settings.py unenroll)")

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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="managed_settings.py", usage="%(prog)s [-h] status|unenroll [sdlc_dir]",
        description="Inspect or reset this checkout's org-policy enrolment (#423). "
                    "`status` prints the managed-settings status; `unenroll` forgets enrolment so "
                    "a deliberately removed policy file stops refusing (local, idempotent, no network).")
    parser.add_argument("cmd", choices=("status", "unenroll"))
    parser.add_argument("sdlc_dir", nargs="?", default=".sdlc")
    args = parser.parse_args(argv)
    if args.cmd == "status":
        print(read(args.sdlc_dir)[0])
        return 0
    removed = unenroll(args.sdlc_dir)
    print(f"unenrolled: removed {len(removed)} marker(s)" if removed
          else "unenrolled: nothing to remove (already not enrolled)")
    if (pathlib.Path(args.sdlc_dir) / MANAGED_SETTINGS_FILENAME).exists():
        print(f"note: {MANAGED_SETTINGS_FILENAME} is present; the next read enrols again")
    return 0


if __name__ == "__main__":
    sys.exit(main())
