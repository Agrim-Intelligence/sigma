"""The ledger watcher's staleness rule and heartbeat filename each have exactly ONE home (#2490).

WHAT THIS GUARD PINS, EXACTLY. Two things, and not a third:

  1. the ARITHMETIC — `max(interval * 3, MIN_STALE_AFTER_SECONDS)` — lives only in
     `sync.stale_after_seconds`, and every reader of the bound reaches it through that function;
  2. the FILENAME — `watch.heartbeat` — is written only in `sync.heartbeat_path`, and every reader
     of the file reaches it through that function.

WHAT IT DOES **NOT** CLAIM. It does not claim the three INTERVAL READERS agree, because they
measurably do not: a quoted `"3600"` answers 10800 through `sync.watch_interval_seconds` and 2700
through `doctor._ledger_watcher_state`'s own `isinstance` guards; a float keeps its float-ness in
doctor and is truncated to `int` in sync; `True` answers 180 in sync and 2700 in doctor. That
divergence is PRE-EXISTING, doctor's guards are the stricter of the two on two of the three, and
converging them changes a real answer at `loop.py:133`, `sync.py:609` and `doctor.py:2422` — it is
follow-up F-1, with a test per changed answer, not this slice. A guard must not read as a stronger
claim than it makes, so `test_the_watcher_stays_env_first_and_the_config_reader_stays_config_only`
below pins one of those differences as a DIFFERENCE, on purpose.

WHY AN AST WALK RATHER THAN A GREP. A grep for `max(3 * interval, 180)` false-positives on this very
paragraph and on every docstring that quotes the rule (`watch_daemon.py:238`'s own does), and
false-negatives on `max(interval*3, FLOOR)` and on any whitespace the author chose differently.
`ast` sees expressions, never comments — comments produce no node at all — and normalises the
spelling: this checker matches the SHAPE `max(<anything> * 3, …)` / `max(…, 3 * <anything>)` or
`max(…, 180)`, in either operand order.

SCOPE OF THE SWEEP: `skills/` AND `hooks/`. `hooks/` holds 3 owned `.py` files with zero hits today
(measured, not assumed), so including it costs nothing and closes the one place a fourth copy could
otherwise hide from a `skills/`-only scan.

`hooks/session_start.sh` KEEPS ITS OWN COPY, BY DESIGN, AND ITS EXCLUSION IS STRUCTURAL RATHER THAN
LISTED. `.sdlc/design/2417.md` X-2 leaves that file unported (a Claude Code lifecycle hook stays
host-specific per AGENTS.md's host-agnostic rule), so its `max(3 * interval, 180)` is an ACCEPTED
residual duplicate. It is also invisible to this checker by construction, not by an allowlist entry
that could rot: the file is SHELL, its Python lives inside a heredoc, and a walk over `*.py` files
never opens it. Verified by measurement — the sweep finds 3 `.py` files under `hooks/` and none of
them is `session_start.sh`.

NO CARVE-OUT LIST IS NEEDED for the two other `180`s in the tree, either. `slack_commands_listen.py`'s
`DEFAULT_STALE_AFTER_SECONDS = 180` and `doctor.py`'s `_SLACK_COMMANDS_STALE_AFTER_SECONDS = 180`
are bare assignments — a different daemon, a different heartbeat file, a flat floor with no interval
to take a multiple of — and this checker only ever looks inside a `max()` call, so neither is
structurally reachable by it. That is a stronger fence than an allowlist someone has to maintain.

RESIDUE, stated so this is not mistaken for airtight: a copy written as `interval + interval +
interval`, or with the floor read from a re-declared local constant, or built by `eval`, is not a
shape this checker matches. It catches the copy-paste that actually happened three times, not every
conceivable re-derivation.
"""
import ast
import importlib.util
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"

#: Both plugin directories. `hooks/` is scanned even though it has no hits today — see the module
#: docstring — so a future copy cannot hide there.
SCAN_DIRS = ("skills", "hooks")


def _mod(name):
    """`tests/test_watch.py:17`'s loader, for the two agrim-loop scripts."""
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _doctor():
    """`tests/test_doctor.py:4240`'s `_dr()`, with its one trap fixed: that helper re-execs the
    module on EVERY call, so `monkeypatch.setattr(_dr(), ...)` patches a DIFFERENT object from the
    one `_dr()._ledger_watcher_state(...)` then reads, and the patch silently does nothing. Every
    test below binds ONE module object to a local and uses that for both the patch and the call."""
    spec = importlib.util.spec_from_file_location(
        "doctor_2490", ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------------- the two detectors
# Pure functions of a root directory, so they can be proved against planted fixtures (below) as
# well as against the real tree — tests/test_import_boundary.py's own shape.


def _is_int_constant(node, value):
    """`ast.Constant` holding exactly this int. `not isinstance(..., bool)` because `True` is an
    `int` in Python and `True == 1`; a boolean literal is never this rule."""
    return (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool) and node.value == value)


def _owned_py_files(root):
    """Every `.py` file under `root` this repo owns. `*.[pP][yY]` because pathlib's glob is
    case-sensitive on POSIX regardless of the filesystem (test_import_boundary.py's own note), and
    the two by-name skips are `__pycache__` and `*.egg-info` — neither is a plausible module name."""
    out = []
    for path in sorted(root.rglob("*.[pP][yY]")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or any(part.endswith(".egg-info") for part in rel.parts):
            continue
        out.append(path)
    return out


def _parsed(root):
    """`(relpath, tree)` for every owned file. An unparseable file is REPORTED as a hit rather than
    skipped: a scan that silently drops files it cannot read is a scan that can pass vacuously."""
    for path in _owned_py_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            yield rel, ast.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
        except (SyntaxError, ValueError) as exc:
            yield rel, exc


def _rederivations(root):
    """Sorted `"relpath:line: shape"` for every `max()` call that re-derives the watcher's bound:
    an argument that multiplies something by 3 (either operand order), or an argument that is the
    literal floor 180."""
    hits = []
    for rel, tree in _parsed(root):
        if isinstance(tree, Exception):
            hits.append(f"{rel}:{getattr(tree, 'lineno', 0) or 0}: unparseable ({tree})")
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "max"):
                continue
            for arg in node.args:
                if (isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mult)
                        and (_is_int_constant(arg.left, 3) or _is_int_constant(arg.right, 3))):
                    hits.append(f"{rel}:{node.lineno}: max(... * 3, ...)")
                    break
                if _is_int_constant(arg, 180):
                    hits.append(f"{rel}:{node.lineno}: max(..., 180)")
                    break
    return sorted(hits)


def _heartbeat_literals(root):
    """Sorted `"relpath:line"` for every string constant that is EXACTLY `"watch.heartbeat"`.

    Exact, never substring: `watch_daemon.py:40`/`:111` and `doctor.py:2491` all mention the
    filename inside prose docstrings, and a substring match would flag every one of them. A
    docstring IS an `ast.Constant`, so the exactness is what keeps this honest — prose never equals
    the bare filename, and `state / "watch.heartbeat"` always does."""
    hits = []
    for rel, tree in _parsed(root):
        if isinstance(tree, Exception):
            hits.append(f"{rel}:{getattr(tree, 'lineno', 0) or 0}: unparseable ({tree})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value == "watch.heartbeat":
                hits.append(f"{rel}:{node.lineno}")
    return sorted(hits)


# --------------------------------------------------------------------------- 1. the real tree


def test_the_rule_has_exactly_one_home():
    """EXACTLY ONE hit, not "none outside sync.py" — the assertion is simultaneously the drift check
    and the anti-vacuity check (tests/test_import_boundary.py:245's own move). If the detector ever
    stops matching — a refactor renames `max`, the AST shape changes, the walk loses the directory —
    the sync.py hit disappears too and this test goes RED rather than passing on an empty scan.

    Both breaks were run: re-literalling `max(3 * interval, 180)` back into doctor.py reds this test
    alone with two entries, and every behavioural test stays green — which is the whole reason the
    structural half has to exist."""
    rules, files = [], []
    for name in SCAN_DIRS:
        root = ROOT / name
        assert _owned_py_files(root), (
            f"{name}/ has no owned .py files — renamed or emptied, this guard would otherwise pass "
            "with zero coverage")
        rules += [f"{name}/{h}" for h in _rederivations(root)]
        files += [f"{name}/{h}" for h in _heartbeat_literals(root)]

    assert len(rules) == 1 and rules[0].startswith("skills/agrim-loop/scripts/sync.py:"), (
        "the watcher's staleness rule must be written in exactly one place, "
        "sync.stale_after_seconds (#2490). Found:\n  " + "\n  ".join(rules or ["nothing at all"]))
    assert len(files) == 1 and files[0].startswith("skills/agrim-loop/scripts/sync.py:"), (
        "the heartbeat filename must be written in exactly one place, sync.heartbeat_path "
        "(#2488/#2490). Found:\n  " + "\n  ".join(files or ["nothing at all"]))


def test_the_out_of_scope_shell_copy_is_excluded_structurally_not_by_an_allowlist():
    """`hooks/session_start.sh` keeps its own copy of the arithmetic (design X-2) and this checker
    never sees it — not because it is listed as an exception, but because it is a SHELL file whose
    Python lives in a heredoc, and the sweep walks `*.py` only. Measured here rather than assumed,
    because "structurally impossible" is exactly the kind of claim that quietly stops being true."""
    hook = ROOT / "hooks" / "session_start.sh"
    assert hook.is_file(), "the hook this exclusion is about no longer exists — re-derive the claim"
    assert "max(3 * interval, 180)" in hook.read_text(encoding="utf-8"), (
        "session_start.sh no longer holds the accepted residual copy — if it was ported, this "
        "exclusion and the module docstring's X-2 argument both need rewriting")
    assert hook not in _owned_py_files(ROOT / "hooks")


# --------------------------------------------------------------------------- 2-3. the detectors


def test_the_detector_fires_on_a_planted_rederivation(tmp_path):
    """The control for the detector itself, in all three shapes a copy actually takes."""
    (tmp_path / "planted.py").write_text(
        "FLOOR = 180\n"
        "import pathlib\n"
        "def a(interval):\n"
        "    return max(3 * interval, 180)\n"           # line 4: constant-first, literal floor
        "def b(interval):\n"
        "    return max(interval * 3, FLOOR)\n"         # line 6: constant-second, named floor
        "def c(interval):\n"
        "    return max(interval, 180)\n"               # line 8: floor alone, no multiply
        "def d(directory):\n"
        "    return pathlib.Path(directory) / 'watch.heartbeat'\n",   # line 10
        encoding="utf-8")
    rules = _rederivations(tmp_path)
    assert rules == ["planted.py:4: max(... * 3, ...)",
                     "planted.py:6: max(... * 3, ...)",
                     "planted.py:8: max(..., 180)"], rules
    assert _heartbeat_literals(tmp_path) == ["planted.py:10"]


def test_the_detector_ignores_comments_and_docstrings(tmp_path):
    """Prose that QUOTES the rule is not a copy of it. A comment produces no AST node at all; a
    docstring produces an `ast.Constant` whose value is the whole paragraph, never the bare
    filename. Both are what let this file's own module docstring say `max(3 * interval, 180)` out
    loud without flagging itself."""
    (tmp_path / "prose.py").write_text(
        '"""Explains the rule: max(interval * 3, 180), written at state/watch.heartbeat."""\n'
        "# also max(3 * interval, 180) and state/watch.heartbeat, in a comment\n"
        "def f():\n"
        '    """Nested docstring naming watch.heartbeat and max(3 * x, 180) in prose."""\n'
        "    return None\n",
        encoding="utf-8")
    assert _rederivations(tmp_path) == []
    assert _heartbeat_literals(tmp_path) == []


def test_the_detector_reports_a_file_it_cannot_parse(tmp_path):
    """A scan that silently skips what it cannot read can pass vacuously on a tree it never
    inspected. An unparseable file is a HIT, so it surfaces as a failure rather than as silence."""
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    assert any("unparseable" in h for h in _rederivations(tmp_path))
    assert any("unparseable" in h for h in _heartbeat_literals(tmp_path))


# --------------------------------------------------------------------------- 4-7. the values


def test_the_daemon_and_the_shared_source_agree_for_every_interval():
    """Value AND type, for the whole spread the shipped code can reach: below the floor, straddling
    it, the shipped default, a float (which must stay a float — `doctor.py` can pass one) and a
    decade far above anything real."""
    wd, sync = _mod("watch_daemon"), _mod("sync")
    for interval in (1, 5, 59, 60, 61, 900, 3600, 600.7, 10 ** 9):
        assert wd.stale_after_seconds(interval) == sync.stale_after_seconds(interval), interval
        assert type(wd.stale_after_seconds(interval)) is type(sync.stale_after_seconds(interval)), \
            interval


def test_doctors_row_flips_at_exactly_the_shared_bound(tmp_path):
    """Doctor's bound asserted THROUGH ITS PUBLIC FUNCTION — the gesture `SKILL.md` and the
    dashboard actually use (`doctor.py:4261`) — never a private constant, so the assertion cannot
    pass against a bound the operator never sees.

    THE `interval_seconds: 5` ROW IS THE LOAD-BEARING ONE and is why the floored case must be in
    this table: a re-forked copy that changes only the FLOOR (`max(3 * interval, 240)`) still agrees
    with the shared rule on the other two rows — `max(2700, 240) == 2700`, `max(10800, 240) ==
    10800` — and is caught only here. Run as Break B: the 5-second row goes red on its `STALE`
    assertion while `{}` and `3600` stay green, exactly as predicted."""
    doctor, sync = _doctor(), _mod("sync")
    (tmp_path / "state").mkdir()
    heartbeat = sync.heartbeat_path(tmp_path)
    for cfg in ({"ledger": {"enabled": True}},
                {"ledger": {"enabled": True, "watch": {"interval_seconds": 3600}}},
                {"ledger": {"enabled": True, "watch": {"interval_seconds": 5}}}):
        bound = sync.stale_after_seconds(sync.watch_interval_seconds(cfg))
        heartbeat.write_text("", encoding="utf-8")
        os.utime(heartbeat, (1000.0, 1000.0))
        fresh = doctor._ledger_watcher_state(tmp_path, cfg, now=1000.0 + bound - 1)
        assert fresh.startswith("ON —"), (cfg, bound, fresh)
        stale = doctor._ledger_watcher_state(tmp_path, cfg, now=1000.0 + bound)
        assert "STALE" in stale, (cfg, bound, stale)


def test_the_config_reader_still_returns_an_int():
    """`watcher_stale_after_seconds` is now a wrapper, and a wrapper is exactly where a return type
    quietly widens. `watch_interval_seconds`'s `int()` coercion is what makes it an `int`, and
    `stale_after_seconds` must not undo that — a float floor, or a float default, would make it
    `180.0`.

    WHERE THE RENDERED VALUE ACTUALLY COMES FROM, stated correctly because the plan got it wrong:
    the literal `"180"` that `tests/test_watch.py:1210` asserts inside `state/watch.log` is
    produced by `watch_daemon.main()` from `stale_after_seconds(resolve_interval(...)[0])` — the
    ENV-first path — not by this config reader at all. So the second pair of assertions below is
    the one that actually covers that log line, and the first pair covers the three other callers
    of the config reader (`sync.publish_after_write`, `loop.py:133`, `doctor.py:2422`)."""
    wd, sync = _mod("watch_daemon"), _mod("sync")
    assert type(sync.watcher_stale_after_seconds({})) is int
    assert type(sync.watcher_stale_after_seconds(
        {"ledger": {"watch": {"interval_seconds": 3600}}})) is int
    # The path the rendered "180" in state/watch.log is on.
    assert type(wd.stale_after_seconds(wd.resolve_interval({}, {"SIGMA_WATCH_INTERVAL": "1"})[0])) is int
    assert f"{wd.stale_after_seconds(1)}" == "180"


def test_the_watcher_stays_env_first_and_the_config_reader_stays_config_only():
    """D-0, pinned as an intended DIFFERENCE rather than an agreement. This is the trap #2490 had to
    not fall into: a config-TAKING single source would have made the watcher ignore
    `SIGMA_WATCH_INTERVAL` entirely — 10800 where the operator asked for 180, a 60x error in
    the direction that makes a LIVE watcher read dead. A future "helpful" unification of these two
    numbers must fail HERE, loudly, with both exact values in front of whoever attempted it."""
    wd, sync = _mod("watch_daemon"), _mod("sync")
    cfg = {"ledger": {"enabled": True, "watch": {"interval_seconds": 3600}}}
    env = {"SIGMA_WATCH_INTERVAL": "1"}
    assert wd.stale_after_seconds(wd.resolve_interval(cfg, env)[0]) == 180
    assert sync.watcher_stale_after_seconds(cfg) == 10800


# --------------------------------------------------------------------------- 8-9. the filename


def test_every_reader_names_the_same_heartbeat_file(tmp_path):
    """Both directions, because the positive alone is nearly a tautology: `watch_daemon.paths()` has
    called `sync.heartbeat_path` since #2488, so asserting they are equal restates a call the
    detector above already pins. The TEETH are the doctor half and the negative control — a
    heartbeat written at a plausibly-wrong neighbouring path must NOT make the row read healthy, so
    this test fails if `_ledger_watcher_state` ever stats a different file, in either direction."""
    doctor, sync, wd = _doctor(), _mod("sync"), _mod("watch_daemon")
    cfg = {"ledger": {"enabled": True}}
    assert sync.heartbeat_path(tmp_path) == wd.paths(tmp_path).heartbeat

    (tmp_path / "state").mkdir()
    decoy = tmp_path / "state" / "watcher.heartbeat"
    decoy.write_text("", encoding="utf-8")
    os.utime(decoy, (1000.0, 1000.0))
    assert not doctor._ledger_watcher_state(tmp_path, cfg, now=1000.0 + 60).startswith("ON —")

    real = sync.heartbeat_path(tmp_path)
    real.write_text("", encoding="utf-8")
    os.utime(real, (1000.0, 1000.0))
    assert doctor._ledger_watcher_state(tmp_path, cfg, now=1000.0 + 60).startswith("ON —")


def test_a_sync_that_will_not_load_is_reported_as_could_not_check(tmp_path, monkeypatch):
    """The one genuinely NEW branch this slice adds: `_ledger_watcher_state` now cross-loads
    `sync.py`, which it never did before, so a load failure must degrade honestly instead of
    crashing the dashboard or — worse — reading as an all-clear (`doctor.py:2402`'s "`None` IS A
    THIRD ANSWER" rule, and `_ledger_delivery_state`'s own precedent 90 lines above).

    ONE module object, bound to a local, used for BOTH the patch and the call — `tests/test_doctor
    .py:4240`'s `_dr()` re-execs the module on every call, so `monkeypatch.setattr(_dr(), ...)`
    followed by `_dr()._ledger_watcher_state(...)` would patch one instance and read another, and
    the monkeypatch would silently do nothing at all."""
    doctor = _doctor()

    def _refuse(name):
        raise ImportError(f"pretend {name}.py is unreadable")

    monkeypatch.setattr(doctor, "_load_loop_script", _refuse)
    state = doctor._ledger_watcher_state(tmp_path, {"ledger": {"enabled": True}})
    assert "COULD NOT CHECK" in state
    assert "not an all-clear" in state
    assert not state.startswith("ON —")
    # The `off` row returns BEFORE the load, so a repo without a ledger never pays for it and never
    # sees this degradation.
    assert doctor._ledger_watcher_state(tmp_path, {}) == "off (no shared team ledger)"


def test_the_guard_covers_the_three_modules_it_claims_to():
    """Anti-vacuity for the value half: every module this file asserts about must actually load.
    A `_mod`/`_doctor` that started returning a stub would make four tests above pass by asserting
    nothing."""
    for mod, attr in ((_mod("sync"), "stale_after_seconds"),
                      (_mod("watch_daemon"), "stale_after_seconds"),
                      (_doctor(), "_ledger_watcher_state")):
        assert callable(getattr(mod, attr, None)), (mod, attr)
