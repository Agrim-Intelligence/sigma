"""30-day retention over the journal directory (`.sdlc/events/`).

S1-G3 / #2574, PRD §6.2. Structurally a copy of `timing_store.py`'s own retention prior art
(`prune`/`maybe_prune`/`_prune_if_due`), with ONE deliberate deviation and one deliberate scope
narrowing, both of which have their own cases below:

F-1 — PRUNE NEVER CREATES ITS OWN DIRECTORY. `timing_store.maybe_prune` does
`stamp.parent.mkdir(parents=True, exist_ok=True)` before writing `.last-prune`. Prune runs BEFORE
the journal gate (it has to: the journal-off install is the only one it exists for), and `append()`
is called unconditionally on a core-only install — so a verbatim copy would create `.sdlc/events/`
on every install with the journal off, and acceptance bullet 1's census would fail on the shipped
default forever. `_maybe_prune_journal`'s first line is an early return on a missing directory, and
nothing here ever calls `mkdir`. That is `test_dir_absent_creates_nothing_on_a_core_only_install`,
and control C-5.

F-2/D-5 — THE SWEEP TOUCHES `.sdlc/events/` ONLY, NEVER `.sdlc/ledger/events/`. `sync.py` stages
`ledger/events/*.jsonl` into the ops-branch worktree, where on this repo those files are
git-TRACKED. A retention sweep that reached them would create staged deletions in a live worktree
and destroy a teammate's pull. `test_the_legacy_shared_dir_is_never_swept` pins it.

WHY THE CURRENT-WRITER CASE BACK-DATES ITS OWN FILE. "the current one never goes" is VACUOUS as
the issue states it: a file created during the test has a now-mtime and can never be a prune
candidate, so the assertion would hold with no guard at all. The honest form back-dates the current
writer's OWN file past the cutoff and asserts it survives — its red is deleting the
`-{_instance_token()}.jsonl` suffix guard.

THE STAMP LIVES INSIDE THE JOURNAL DIRECTORY, not in `store_dir()` where `timing_store` keeps its
own. That is what makes C-5's stated red ("`.sdlc/events/` exists") true: an implementer mirroring
`timing_store`'s stamp path would `mkdir` a different directory entirely. It also means
`.sdlc/events/` holds one non-`.jsonl` file — every reader glob and the sweep itself are
`*.jsonl`, so nothing reads it by accident, but a census counting `iterdir()` would see it.
"""
import importlib.util
import json
import os
import pathlib
import time

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod("ledger")

DAY = 86400
JOURNAL_ON = {"journal": {"enabled": True}, "ledger": {"actor": "dana"}}
#: No `journal` key AT ALL — the shipped core-only default, and the case §6.2's prune exists for.
JOURNAL_OFF = {"ledger": {"actor": "dana"}}


@pytest.fixture(autouse=True)
def _fresh_process():
    """`_journal_pruned_this_process` is a module global that survives between tests in one pytest
    process. Every case below is written as "a fresh process's first append", so it is reset."""
    ledger._journal_pruned_this_process = False
    yield
    ledger._journal_pruned_this_process = False


def _sdlc(tmp_path, config):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    return d


def _aged(directory, name, days_old):
    """A `*.jsonl` file whose mtime is `days_old` days in the past."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text('{"id":"a:1","ts":"t","actor":"a","kind":"phase","goal":"1"}\n',
                    encoding="utf-8")
    when = time.time() - days_old * DAY
    os.utime(path, (when, when))
    return path


def _append(sdlc_dir, config):
    return ledger.append(sdlc_dir, config, "phase", "0001-a.md", stream=ledger.EVENTS,
                         phase="implement", state="end")


# --------------------------------------------------------------------- the cutoff pair (C-3)


def test_cutoff_a_thirty_one_day_old_file_is_deleted(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    old = _aged(ledger.local_events_dir(d), "someone-else-h.999.jsonl", 31)
    _append(d, JOURNAL_ON)
    assert not old.exists(), "a file past the 30-day window must go"


def test_cutoff_a_twenty_nine_day_old_file_is_kept(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    young = _aged(ledger.local_events_dir(d), "someone-else-h.999.jsonl", 29)
    _append(d, JOURNAL_ON)
    assert young.exists(), "a file inside the window must stay"


# --------------------------------------------------------------------- the writer's own file


def test_the_current_writers_own_file_survives_even_back_dated_past_the_window(tmp_path):
    """The guard keys on the writer-instance suffix, NOT on mtime: a long-running process whose
    file predates the window is still the file this very append is about to write into."""
    d = _sdlc(tmp_path, JOURNAL_ON)
    mine = ledger.entry_file(d, ledger.actor(JOURNAL_ON, None), ledger.EVENTS)
    mine.parent.mkdir(parents=True, exist_ok=True)
    mine.write_text('{"id":"dana:1","ts":"t","actor":"dana","kind":"phase","goal":"1"}\n',
                    encoding="utf-8")
    when = time.time() - 31 * DAY
    os.utime(mine, (when, when))
    assert mine.name.endswith(f"-{ledger._instance_token()}.jsonl"), "fixture must be OUR file"

    _append(d, JOURNAL_ON)
    assert mine.exists(), "the writing process's own file is never a prune candidate"


# --------------------------------------------------------------------- the journal-off case (C-4b)


def test_journal_off_still_prunes_a_thirty_one_day_old_file(tmp_path):
    """THE CASE §6.2's RETENTION EXISTS FOR, and the one control C-4b turns on.

    Retention must run whether or not the journal is currently writing: a developer who turns the
    journal off is exactly the person whose old events nobody will ever come back for. Prune is
    therefore the FIRST statement of `append()`, before the gate — placed after it, it would never
    run on a journal-off install at all, which is decoration by construction."""
    d = _sdlc(tmp_path, JOURNAL_OFF)
    old = _aged(ledger.local_events_dir(d), "someone-else-h.999.jsonl", 31)
    assert _append(d, JOURNAL_OFF) is None, "the gate must still refuse the write"
    assert not old.exists(), "retention runs with the journal off"


# --------------------------------------------------------------------- F-1 / C-5


def test_dir_absent_creates_nothing_on_a_core_only_install(tmp_path):
    """F-1 / control C-5. The shipped default: journal off, `.sdlc/events/` has never existed.
    After a phase boundary it must STILL not exist, and no stamp may have been written anywhere
    under `.sdlc/` — a `mkdir` here breaks acceptance bullet 1's census permanently."""
    d = _sdlc(tmp_path, JOURNAL_OFF)
    assert not ledger.local_events_dir(d).exists()

    assert _append(d, JOURNAL_OFF) is None

    assert not ledger.local_events_dir(d).exists(), \
        ".sdlc/events was created on a journal-off install"
    assert list(pathlib.Path(d).rglob(".last-prune")) == [], \
        "a stamp file was written on a journal-off install"


# --------------------------------------------------------------------- F-2 / D-5


def test_the_legacy_shared_dir_is_never_swept(tmp_path):
    """`.sdlc/ledger/events/` is git-TRACKED inside the ops-branch worktree. Deleting from it
    would stage deletions in a live worktree and destroy a teammate's pull."""
    d = _sdlc(tmp_path, JOURNAL_ON)
    legacy = _aged(ledger.entries_dir(d, ledger.EVENTS), "someone-else-h.999.jsonl", 400)
    _append(d, JOURNAL_ON)
    assert legacy.exists(), "retention must never reach the shared ledger events directory"


# --------------------------------------------------------------------- the stamp
#
# Driven through `_maybe_prune_journal` directly rather than `append()`: these pin the STAMP's own
# logic, not the call site, and routing them through `append()` would widen control C-4's stated
# red beyond the two cases the mutation can actually produce.


def test_the_stamp_throttles_a_second_process_inside_the_interval(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    _aged(ledger.local_events_dir(d), "a-h.1.jsonl", 31)
    first = ledger._maybe_prune_journal(d)
    assert first is not None, "the first sweep runs"

    second_old = _aged(ledger.local_events_dir(d), "b-h.2.jsonl", 31)
    assert ledger._maybe_prune_journal(d) is None, "a second process inside 24h does not sweep"
    assert second_old.exists()


def test_the_stamp_no_longer_throttles_once_the_interval_has_passed(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    old = _aged(ledger.local_events_dir(d), "a-h.1.jsonl", 31)
    ledger._maybe_prune_journal(d, now=time.time() - 2 * DAY)
    assert ledger._maybe_prune_journal(d) is not None
    assert not old.exists()


def test_a_future_dated_stamp_does_not_suppress_retention(tmp_path):
    """`timing_store.py:307`'s lesson: a corrected clock or a restored backup would otherwise
    suppress retention forever. A stamp dated in the FUTURE is not "recently swept"."""
    d = _sdlc(tmp_path, JOURNAL_ON)
    old = _aged(ledger.local_events_dir(d), "a-h.1.jsonl", 31)
    stamp = ledger.local_events_dir(d) / ".last-prune"
    stamp.write_text("0", encoding="utf-8")
    ahead = time.time() + 365 * DAY
    os.utime(stamp, (ahead, ahead))

    assert ledger._maybe_prune_journal(d) is not None
    assert not old.exists()


def test_a_symlinked_stamp_is_neither_swept_nor_written_through(tmp_path):
    """Security review S1, ported with the code: `write_text` FOLLOWS a link, so a link planted
    inside `.sdlc/events/` would turn a phase boundary into a write outside the journal."""
    d = _sdlc(tmp_path, JOURNAL_ON)
    old = _aged(ledger.local_events_dir(d), "a-h.1.jsonl", 31)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched", encoding="utf-8")
    (ledger.local_events_dir(d) / ".last-prune").symlink_to(outside)

    assert ledger._maybe_prune_journal(d) is None
    assert old.exists(), "a hostile stamp must stop the sweep, not be worked around"
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_a_symlinked_journal_dir_is_never_swept(tmp_path):
    """`glob`, `is_file` and `unlink` all follow links, and this is the one place the journal
    deletes. A linked journal directory means the sweep does nothing at all."""
    real = tmp_path / "elsewhere"
    real.mkdir()
    victim = _aged(real, "a-h.1.jsonl", 400)
    d = _sdlc(tmp_path, JOURNAL_ON)
    ledger.local_events_dir(d).symlink_to(real, target_is_directory=True)

    assert ledger._maybe_prune_journal(d) is None
    assert victim.exists()


def test_a_symlinked_jsonl_file_is_never_unlinked(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    ledger.local_events_dir(d).mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("keep me\n", encoding="utf-8")
    link = ledger.local_events_dir(d) / "a-h.1.jsonl"
    link.symlink_to(outside)
    when = time.time() - 400 * DAY
    os.utime(link, (when, when), follow_symlinks=False)

    ledger._maybe_prune_journal(d)
    assert outside.exists() and outside.read_text(encoding="utf-8") == "keep me\n"


# --------------------------------------------------------------------- shape and safety


def test_prune_journal_never_raises_and_never_creates_the_directory(tmp_path):
    d = _sdlc(tmp_path, JOURNAL_ON)
    assert ledger.prune_journal(d) == []
    assert not ledger.local_events_dir(d).exists()


def test_the_once_per_process_flag_is_set_before_the_sweep(tmp_path, monkeypatch):
    """A raising sweep must not leave the flag clear and retry on every subsequent write for the
    life of the process — `timing_store._prune_if_due`'s own rule, ported with the code."""
    d = _sdlc(tmp_path, JOURNAL_ON)
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(ledger, "_maybe_prune_journal", boom)
    ledger._prune_journal_if_due(d)
    ledger._prune_journal_if_due(d)
    assert calls == [1], "the sweep is attempted exactly once per process, even when it raises"
    assert ledger._journal_pruned_this_process is True


def test_retention_constants_are_the_documented_ones():
    assert ledger.JOURNAL_RETENTION_DAYS == 30
    assert ledger.JOURNAL_PRUNE_INTERVAL_SECONDS == 86400
