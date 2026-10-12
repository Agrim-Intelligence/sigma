"""Cap `.sdlc/state/unit-approvals` (#1264): the one place unit-landing approvals are ever deleted.

Kept apart from `feature_land_approval.py` on purpose: that module is pinned to delete nothing, and the
consent decision must not be able to reach a delete. This module only removes a record and its single-use
marker after the record expired more than `grace` seconds ago. Inside the grace a used marker still refuses a
second consume. Only regular files named `<40 hex>.json` / `<40 hex>.used` are touched; a symlink or any other
name is left alone. Best effort: an OS error leaves the file for the next call.

Residual, stated: once a pair is gone, a fresh `approve` of the SAME unit, repository and head more than the grace
after the old one expired is usable again (the old marker no longer says it was used). That is a new explicit
human approval of a head that has not moved in 30 days.
"""
import json
import os
import pathlib
import re
import stat

_NAME = re.compile(r"([0-9a-f]{40})\.(json|used)\Z")


def _regular(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def prune(base, now, grace, max_ttl):
    """-> number of files removed. `base` is the unit-approvals directory."""
    base = pathlib.Path(base)
    removed = 0
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return 0
    digests = {}
    for name in names:
        m = _NAME.match(name)
        if m and _regular(base / name):
            digests.setdefault(m.group(1), {})[m.group(2)] = base / name
    for files in digests.values():
        record, marker = files.get("json"), files.get("used")
        try:
            if record is not None:
                try:
                    expires = json.loads(record.read_text(encoding="utf-8")).get("expires_at")
                except (OSError, ValueError, AttributeError):
                    expires = None
                if not (isinstance(expires, int) and not isinstance(expires, bool)):
                    expires = int(os.lstat(record).st_mtime) + max_ttl      # malformed: age it from its mtime
                if now - expires <= grace:
                    continue
            elif now - os.lstat(marker).st_mtime <= grace + max_ttl:        # a marker with no record
                continue
            for p in (record, marker):
                if p is not None:
                    p.unlink()
                    removed += 1
        except OSError:
            continue
    return removed
