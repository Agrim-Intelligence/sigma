"""Read back what the EVENTS stream wrote, from BOTH destinations (#2574/S1-G3).

Imported as a plain sibling module (`from journal_events import journal_events`) -- pytest puts
each test file's own directory on `sys.path`, the same way `gqlfake` and `skill_corpus` are used
here already.

WHY THIS EXISTS RATHER THAN A WIDER `read_all`. `ledger.read_all(sdlc, stream=EVENTS)` reads
`entries_dir(sdlc, EVENTS)` -- `.sdlc/ledger/events/` -- and S1-G3 moved the writer to
`local_events_dir(sdlc)` (`.sdlc/events/`). `read_all` deliberately keeps its
one-stream-one-directory shape: the production readers that need both (doctor's three journal
checks, `phase_report.py`'s crash repair, `time_report.all_events`, and a downstream reader) each
union the two directories at their OWN call site, and teaching `read_all` to union as well would
make every one of them double-count. So the suite unions the same way its callers do, in one place
instead of forty.

Both directories, not just the new one: an adopter's existing history stays in the legacy shared
directory for one release, several tests seed it on purpose to pin exactly that, and a helper that
read only the journal dir would quietly stop covering it.
"""
import json
import pathlib


def journal_events(ledger, sdlc_dir):
    """Every EVENTS record under `sdlc_dir`, from both destinations, in `read_all`'s own order.

    Sorted with `ledger._sort_key`, not by insertion, so a test asserting on `events[0]` sees what
    a production reader would. The parsing tolerances below mirror `ledger.read_all` (utf-8-sig +
    `errors="replace"`, malformed lines skipped, no `kind` means not a record) rather than being
    stricter: a helper that raised where the real reader shrugs would fail tests for the wrong
    reason."""
    out = list(ledger.read_all(sdlc_dir, stream=ledger.EVENTS))
    for path in sorted(ledger.local_events_dir(sdlc_dir).glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(item, dict) and item.get("kind"):
                out.append(item)
    return sorted(out, key=ledger._sort_key)


def journal_dir(sdlc_dir):
    """`.sdlc/events/` as a path, for the handful of tests that assert on the directory itself
    without holding a ledger module."""
    return pathlib.Path(sdlc_dir) / "events"
