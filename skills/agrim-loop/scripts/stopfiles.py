#!/usr/bin/env python3
"""Which daemon stop-files are sitting in <sdlc>/state, and how old (#416).

A stop-file is the polite, documented way to halt a daemon -- and the daemon that honours it exits
SILENTLY from the operator's point of view, then (for the watcher) is not restarted while the file
exists. A forgotten one therefore looks exactly like a dead daemon with nothing to say. Age is the
tell: a file touched a minute ago is a deliberate stop, one a week old is almost certainly forgotten.

One home for the facts so `/agrim-doctor` and `hooks/session_start.sh` (an accelerator only; Cursor
has no hooks) say the same thing. Read-only: it never removes a stop-file -- that is the operator's
lever. Never raises.

    stopfiles.py line <sdlc_dir>    # prints one line when any stop-file exists, nothing otherwise
"""
import os
import pathlib
import sys
import time

#: (filename under <sdlc>/state, what honours it). Names are a cross-component contract with
#: watch_daemon.py, supervise_daemon.py and slack_commands_listen.py.
STOP_FILES = (
    ("watch.stop", "ledger watcher"),
    ("supervisor.stop", "loop supervisor"),
    ("slack-commands.stop", "slack-commands listener"),
)


def _age(seconds):
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % round(seconds / 60.0)
    if seconds < 172800:
        return "%.1fh" % (seconds / 3600.0)
    return "%dd" % (seconds // 86400)


def present(sdlc_dir, now=None):
    """[(filename, daemon, age_seconds_or_None, path)] for every stop-file that exists."""
    now = time.time() if now is None else now
    state = pathlib.Path(sdlc_dir) / "state"
    found = []
    for name, daemon in STOP_FILES:
        path = state / name
        try:
            if not os.path.lexists(path):
                continue
        except OSError:
            continue
        try:
            age = max(0, now - path.stat().st_mtime)
        except OSError:
            age = None
        found.append((name, daemon, age, path))
    return found


def line(sdlc_dir, now=None):
    """One human line, or "" when no stop-file exists."""
    found = present(sdlc_dir, now)
    if not found:
        return ""
    parts = ["%s (%s, %s old)" % (n, d, "unknown age" if a is None else _age(a)) for n, d, a, _ in found]
    return ("STOPPED by stop-file: %s. That daemon exits (watcher: not restarted) while the file "
            "exists; delete %s to resume." % ("; ".join(parts), "it" if len(found) == 1 else "them"))


def main(argv):
    if len(argv) != 3 or argv[1] != "line":
        print("usage: stopfiles.py line <sdlc_dir>", file=sys.stderr)
        return 2
    try:
        text = line(argv[2])
    except Exception:            # noqa: BLE001 - a report must never break its caller
        return 0
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
