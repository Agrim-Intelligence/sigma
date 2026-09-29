"""The permanent core-only write census (#2574/S1-G3; PRD R2, acceptance bullets 1 and 2).

R2: *"The core writes only files the core itself reads, with one exception: the journal. The
journal is off unless the user or the managed-settings file turns it on. A core-only install writes
zero journal bytes, and a permanent test proves it."* This is that test.

WHY IT WORKS FROM THE OUTSIDE. Every other test of the journal calls `ledger.append()` with a
config dict the test built. This one never imports `ledger` at all: it scaffolds a real project
with `sdlc_init.py`, drives five REAL gestures as subprocesses, and then looks at the directory
tree. A unit test cannot see an emitter that reaches disk by a path nobody thought to mock, and
"writes zero journal bytes" is a claim about the disk, not about a function.

BOTH CASES RUN THE SAME GESTURE SEQUENCE. The only difference is one key in the config
`sdlc_init.py` itself wrote. An off-case with no on-case counterpart would prove only that the
gestures are inert -- so `test_on_case_*` asserts the exact event set, and control C-1b breaks one
gesture and watches this census name the kind that went missing.

TWO WAYS THIS TEST COULD PASS WHILE PROVING NOTHING, both guarded below:

  1. **A vacuous gesture.** `phase_report.py start ... P2` is REJECTED (exit 2) and writes nothing,
     so a census written with output-contract phase tokens would pass whatever the default was,
     forever. The phase token here is `research`; see the comment beside it. Every subprocess's
     exit status is asserted, for the same reason.
  2. **Counting the wrong thing.** The prune stamp `.last-prune` lives INSIDE `.sdlc/events/`, so
     `iterdir()` returns six entries for five events. Everything here counts `*.jsonl` files or
     parsed JSONL lines; `test_on_case_the_prune_stamp_is_not_an_event` pins the stamp's presence
     so a future reader meets it deliberately rather than as a surprise off-by-one.

THE GATE LEG IS A CONSTRUCTED PRECONDITION, AND SAYS SO HERE RATHER THAN HIDING IT.
`.sdlc/decisions.json` is NOT something `/agrim-init` writes -- `/agrim-decide` authors it
interactively, out of a conversation with a human. So this one leg of the sequence departs from
"the gesture a new user makes", and the fixture seeds a minimal registry by hand. It is kept rather
than dropped because `hooks/decision_gate.py` is the ONLY EVENTS writer outside
`skills/agrim-loop/scripts/`: a separate process that cross-loads `ledger.py` from `hooks/` by
relative path. That makes it the write path most likely to break under a change to the journal and
the least likely to be noticed. Naming the departure is the honest form of keeping it; building the
registry and still calling the whole sequence "what a new user does" would not be.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INIT = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
PHASE_REPORT = ROOT / "skills" / "agrim-loop" / "scripts" / "phase_report.py"
LOOP = ROOT / "skills" / "agrim-loop" / "scripts" / "loop.py"
DECISION_GATE = ROOT / "hooks" / "decision_gate.py"

#: `gh` stubbed by exclusion -- the idiom `tests/test_decision_gate.py` already uses for this hook:
#: a PATH with no `gh` on it. Nothing in the sequence needs the network, and a census that quietly
#: resolved a real GitHub identity would depend on which account was logged in that day.
STUB_ENV = {"PATH": "/usr/bin:/bin"}


def _host_inventory():
    """#240: the empty plugin inventories the autouse conftest fixture points this test at. STUB_ENV
    is built from scratch, so without these the scan would fall back to the real home directory."""
    return {k: os.environ[k] for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME") if k in os.environ}

#: The example goal `/agrim-init` scaffolds. Using it rather than authoring one keeps the sequence
#: inside what a fresh install actually contains.
GOAL = "0001-example.md"

#: The smallest registry `decision_gate.py` will DENY on -- `tests/test_decision_gate.py`'s own
#: `_inv()`, inlined rather than imported so this file stands alone. See the module docstring for
#: why authoring it at all is a named departure from the pure `/agrim-init` gesture.
DECISIONS = {"version": 1, "decisions": [{
    "id": "INV-001", "title": "Bounded timeouts", "class": "invariant", "status": "active",
    "statement": "No call may set a timeout above 30s.", "rationale": "one slow dep = outage",
    "protected_paths": ["src/**/*.py"],
    "protected_params": [{"name": "timeout", "op": "le", "value": 30}]}]}

#: An `Edit` that violates INV-001, so the gate returns a real `deny` and records a `gate` event.
GATE_PAYLOAD = {"tool_name": "Edit",
                "tool_input": {"file_path": "src/a.py", "new_string": "timeout = 120"}}

#: Exactly what the five gestures record, measured against a real `/agrim-init` temp repo on
#: 2026-09-21 (and independently by the P4 round-2 reviewer). `phase` appears twice, as `start` and
#: `end`, which is why the signature carries the state rather than collapsing to a bare kind set --
#: a phase pair that lost its `end` would otherwise still satisfy `{"phase", ...}`.
EXPECTED_SIGNATURES = {"phase:start", "phase:end", "verify", "gate", "park"}
EXPECTED_EVENTS = 5

#: `loop.py verify` exits 3 with `NO-COMMAND` on a fresh scaffold: nothing declares a
#: `verify_command` yet. That is a documented refusal, not a crash, and it still records its
#: `verify` event -- which is precisely why the expected count is 5 and not 4. Asserted as an
#: allowed status rather than waived, so a genuine crash here could never pass as "expected".
ALLOWED_EXITS = {str(PHASE_REPORT): {0}, str(LOOP): {0, 3}, str(DECISION_GATE): {0}}


def _run(argv, cwd=None, stdin=""):
    """One gesture, with its exit status checked against what it is allowed to return.

    A gesture that silently did nothing is the failure mode this whole census exists to catch (a
    rejected phase token writes no event AND changes no assertion), so "it ran" is asserted here
    rather than inferred from the event count downstream."""
    proc = subprocess.run([sys.executable, *[str(a) for a in argv]], cwd=cwd, input=stdin,
                          capture_output=True, text=True, env={**STUB_ENV, **_host_inventory()})
    allowed = ALLOWED_EXITS.get(str(argv[0]), {0})
    assert proc.returncode in allowed, (
        f"{pathlib.Path(str(argv[0])).name} exited {proc.returncode} (allowed: {sorted(allowed)})\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc


def _scaffold(tmp_path):
    """A real `/agrim-init`, plus the one constructed precondition the gate leg needs. (#229: init
    refuses a non-git directory, so the fixture is a fresh `git init` -- the normal first run.)"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _run([INIT, tmp_path])
    sdlc = tmp_path / ".sdlc"
    assert (sdlc / "config.json").exists(), "sdlc_init.py wrote no config"
    (sdlc / "decisions.json").write_text(json.dumps(DECISIONS), encoding="utf-8")
    return sdlc


def _gestures(tmp_path):
    """The five gestures, in the order a goal really meets them."""
    sdlc = tmp_path / ".sdlc"
    # THE PHASE TOKEN IS `research`, NEVER `P2`. `phase_report.py` validates against its own phase
    # names (`goal|research|plan|plan_review|implement|review|retro`); the output contract's `P2
    # RESEARCH` is a CONSOLE token and is rejected here with exit 2, writing nothing. A census
    # written with contract tokens would therefore pass against any default, forever -- so this is
    # the single most load-bearing string in the file.
    _run([PHASE_REPORT, "start", sdlc, GOAL, "research", "--model", "sonnet"])
    _run([PHASE_REPORT, "end", sdlc, GOAL, "research"])
    _run([LOOP, "verify", sdlc, GOAL])
    # THE GATE LEG MUST RUN WITH `cwd=<tmp>`: `decision_gate.py` resolves the repo root from the
    # WORKING DIRECTORY, not from an argument (the other four gestures take `<tmp>/.sdlc`
    # explicitly). Without it, this leg would silently resolve against the test runner's own
    # repository -- reading its registry and writing its journal.
    gate = _run([DECISION_GATE], cwd=str(tmp_path), stdin=json.dumps(GATE_PAYLOAD))
    # THE EXIT CODE PROVES NOTHING FOR THIS ONE LEG. `decision_gate.py` is fail-open by design: its
    # outer handler is `except Exception: sys.exit(0)`, and it prints nothing on the way out -- byte
    # identical to a genuine allow. So an internal error here would exit 0, write no `gate` event,
    # and leave the OFF-case assertion just as true as a working gate does. The deny payload on
    # stdout is the only evidence this leg really ran, so it is asserted rather than assumed.
    # Parsed defensively: an allow is SILENCE, so the failure to guard against is an empty stdout,
    # and a bare `json.loads` would report that as a JSONDecodeError rather than as "the gate did
    # not fire" -- a stack trace where a sentence belongs.
    assert gate.stdout.strip(), "the decision gate stayed silent, which is an ALLOW: no gate event"
    decision = json.loads(gate.stdout)["hookSpecificOutput"]["permissionDecision"]
    assert decision == "deny", f"the decision gate returned {decision!r}: no gate event recorded"
    _run([LOOP, "record", sdlc, sdlc / "goals" / GOAL, "parked", "--detail", "census"])
    return sdlc


def _events(directory):
    """Every parsed JSONL record under `directory`, or [] when it does not exist.

    `*.jsonl`, NEVER `iterdir()`: the prune stamp `.last-prune` shares this directory. Every
    production reader globs `*.jsonl` too, so this counts what they count."""
    if not directory.exists():
        return []
    out = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _signature(event):
    """`phase:start` / `phase:end` for a phase boundary, the bare kind for everything else."""
    kind = event.get("kind")
    return f"{kind}:{event['state']}" if kind == "phase" and event.get("state") else kind


def _turn_the_journal_on(sdlc):
    """THE DOCUMENTED OPT-IN, applied to the config `/agrim-init` itself wrote -- never a hand-built
    config file. Flipping the template's own key is what makes the on-case evidence about the
    shipped default rather than about a fixture."""
    path = sdlc / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["journal"]["enabled"] is False, "the template must ship the journal OFF"
    cfg["journal"]["enabled"] = True
    path.write_text(json.dumps(cfg), encoding="utf-8")


# --------------------------------------------------------------------------- off (the default)


def test_off_case_a_fresh_install_writes_zero_journal_bytes(tmp_path):
    """Acceptance bullet 1. Five real gestures against an untouched `/agrim-init` scaffold, and
    afterwards there is no journal at all -- neither directory is so much as created.

    THE ASSERTION IS NOT THE ISSUE'S LITERAL TEXT, DELIBERATELY (plan D-2). The issue says assert
    `.sdlc/ledger/` does not exist; that is FALSE on the shipped default today, before any change,
    because `/agrim-init` scaffolds `.sdlc/ledger/README.md`. Asserting it would give a permanently
    red census whose first "fix" is to weaken it. The clause below keeps the intent -- nothing
    ACCUMULATES under `ledger/` -- and is strictly stronger than "the directory is absent" would
    have been, because it also catches a stray file appearing beside the README."""
    sdlc = _scaffold(tmp_path)
    _gestures(tmp_path)

    assert not (sdlc / "events").exists(), sorted(p.name for p in (sdlc / "events").iterdir())
    assert not (sdlc / "ledger" / "events").exists()
    assert not (sdlc / "ledger" / "entries").exists()
    assert sorted(p.name for p in (sdlc / "ledger").iterdir()) == ["README.md"]


def test_off_case_is_what_the_template_ships(tmp_path):
    """The census above measures behaviour; this measures the default that produces it. Separate,
    because "off by default" can break in two unrelated ways -- the switch could stop honouring the
    key, or the template could start shipping it on -- and a single test would not say which."""
    sdlc = _scaffold(tmp_path)
    cfg = json.loads((sdlc / "config.json").read_text(encoding="utf-8"))
    assert cfg["journal"]["enabled"] is False


# --------------------------------------------------------------------------- on (the opt-in)


def test_on_case_the_documented_opt_in_records_exactly_the_five_gestures(tmp_path):
    """Acceptance bullet 2, strengthened. The bullet as written ("one phase start/end writes one
    event each") passes vacuously if a gesture is silently rejected; asserting the exact SET of
    signatures is what makes the off-case's red mean something, and what C-1b breaks."""
    sdlc = _scaffold(tmp_path)
    _turn_the_journal_on(sdlc)
    _gestures(tmp_path)

    events = _events(sdlc / "events")
    signatures = sorted(_signature(e) for e in events)
    assert set(signatures) == EXPECTED_SIGNATURES, signatures
    assert len(events) == EXPECTED_EVENTS, signatures


def test_on_case_writes_nothing_to_the_legacy_published_destination(tmp_path):
    """The other half of bullet 2, and the one that pins the T4 flip from the outside: publishing
    is deleted, so `.sdlc/ledger/events/` -- the path the old writer shared to the ops branch -- is
    never created, however the journal is turned on."""
    sdlc = _scaffold(tmp_path)
    _turn_the_journal_on(sdlc)
    _gestures(tmp_path)

    assert (sdlc / "events").exists()                    # the journal really did write
    assert not (sdlc / "ledger" / "events").exists()
    assert sorted(p.name for p in (sdlc / "ledger").iterdir()) == ["README.md"]


def test_on_case_the_prune_stamp_is_not_an_event(tmp_path):
    """The off-by-one this file is written to avoid, pinned so nobody meets it by accident.

    Retention keeps its `.last-prune` stamp INSIDE the journal directory -- that placement is what
    lets prune early-return on a missing directory and never `mkdir` (which is what keeps the
    off-case above true). The cost is that `iterdir()` sees six entries for five events. Every
    reader glob and the sweep itself are `*.jsonl`, so nothing reads it by accident."""
    sdlc = _scaffold(tmp_path)
    _turn_the_journal_on(sdlc)
    _gestures(tmp_path)

    journal = sdlc / "events"
    assert ".last-prune" in {p.name for p in journal.iterdir()}
    assert len(list(journal.glob("*.jsonl"))) == EXPECTED_EVENTS
    assert len(list(journal.iterdir())) == EXPECTED_EVENTS + 1      # the stamp, and only the stamp
