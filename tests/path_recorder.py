"""`Recorder`: every filesystem path this process opens, renames, removes or creates while installed.

Shared by `tests/test_feature_registry.py` and `tests/test_feature_doc.py`, whose isolation tests
prove a write for one unit never touches another unit's file by the PATHS touched, not by the
result: a write that reads B, holds it, and writes it back unchanged passes every result-shaped
assertion and still loses B when two of them interleave.

WHY AUDIT HOOKS AND NOT MONKEYPATCHED `open` (issue #241). The earlier recorder -- one copy per
test file -- replaced `io.open`, `builtins.open` and the mutating `os` functions with spies. That
is blind on Python 3.10: there `pathlib.Path.open` (and so `read_text`/`read_bytes`) calls
`self._accessor.open`, and `pathlib._NormalAccessor.open` is `io.open` captured as a class
attribute at import time, so replacing `io.open` afterwards changes nothing pathlib sees. The same
accessor captures `os.mkdir`, `os.rename`, `os.replace` and `os.unlink` for `Path.mkdir` and friends.
3.11 removed the accessor, which is why 3.12 passed and 3.10 failed on CI. Patching the accessor as
well would be version-coupled to a private class; the interpreter's audit events (PEP 578) are
raised by the C implementation itself -- `_io.open` and `os.open` raise "open", `os.rename` and
`os.replace` raise "os.rename", `os.remove`/`os.unlink` raise "os.remove" -- whatever Python-level
name the caller reached them through, on every supported version and on Windows alike.

THE ONE COST, NAMED: an audit hook cannot be removed. It is installed once per process, on first
use, and afterwards costs one Python call per audit event that returns immediately while no
recorder is active. Nested recorders each receive every event.

Still true from the old design, and still the reason the controls exist: a correct write never
opens a sibling, so the "open" route cannot be exercised by a correct write. It is controlled by
`test_the_path_recorder_records_an_open_not_only_a_replace` (drives a read through it) and the two
`test_the_isolation_check_catches_*` tests (rebuild the module doing the forbidden sibling read and
assert the recorder sees it)."""

import os
import pathlib
import sys
import threading

_ACTIVE = []
_LOCK = threading.Lock()
_INSTALLED = False

# audit event -> (Recorder attribute, how many leading args are paths)
_EVENTS = {
    "open": ("opened", 1),
    "os.rename": ("replaced", 2),    # raised by os.rename AND os.replace
    "os.remove": ("made", 1),        # raised by os.remove AND os.unlink
    "os.mkdir": ("made", 1),         # os.makedirs reaches the disk through os.mkdir
    "os.rmdir": ("made", 1),
}


def _hook(event, args):
    if not _ACTIVE:
        return
    spec = _EVENTS.get(event)
    if spec is None:
        return
    group, n = spec
    for rec in list(_ACTIVE):
        log = getattr(rec, group)
        for p in args[:n]:
            if isinstance(p, (str, bytes, os.PathLike)):      # an int is an fd: no path to record
                log.append(os.fsdecode(p))


def _install():
    global _INSTALLED
    with _LOCK:
        if not _INSTALLED:
            sys.addaudithook(_hook)
            _INSTALLED = True


class Recorder:
    def __init__(self):
        self.opened, self.replaced, self.made = [], [], []

    def __enter__(self):
        _install()
        _ACTIVE.append(self)
        return self

    def __exit__(self, *exc):
        _ACTIVE.remove(self)
        return False

    def paths(self, under):
        """Every recorded path inside `under`, as a set of resolved strings."""
        under = str(pathlib.Path(under).resolve())
        out = set()
        for group in (self.opened, self.replaced, self.made):
            for p in group:
                full = str(pathlib.Path(p).resolve())
                if full == under or full.startswith(under + os.sep):
                    out.add(full)
        return out

    def files(self, under):
        """Paths that are, or would be, FILES -- the directory carve-out named explicitly. A shared
        parent directory is unavoidable for any per-unit layout, and `mkdir(exist_ok=True)` is
        race-safe in the kernel; the requirement is about the files two writers would both rewrite."""
        return {p for p in self.paths(under) if not pathlib.Path(p).is_dir()}
