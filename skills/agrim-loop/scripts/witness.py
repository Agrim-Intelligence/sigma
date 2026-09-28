"""Red-before-green as ledger data (issue #1934) -- and the shared vocabulary #1935 writes into.

WHAT A WITNESS IS. A durable claim that a specific test, AT A SPECIFIC CONTENT HASH, was seen to
FAIL for a stated reason. AGENTS.md already holds the discipline -- "Every guard is deliberately
broken once and seen to fail before it is trusted... A test never seen red proves nothing" -- but
holds it as CONVENTION: a reviewer does it by hand and writes prose in a commit message, so the fact
is unqueryable and nothing can gate on it. This makes it data.

It matters more for agent-authored code than human-authored: when the same context writes both the
implementation and its test, the test can be written to match whatever the code already does --
including a bug -- and it passes on its first ever run.

`kind` IS THE STRENGTH AXIS, and that is why it is an enum rather than a boolean:

  assertion         STRONG -- the assertion ran and rejected the behaviour.
  mutation          STRONG -- a killed mutant IS an existing test failing for the right reason.
                    Written by #1935, and the ONLY way a behaviour-preserving refactor can ever
                    earn a witness (see THE REFACTOR GAP below).
  collection-error  WEAK   -- the symbol did not exist. Red only in the trivial sense; it proves
                    nothing about whether the assertion checks anything.
  other             WEAK   -- anything else.

A test is `verified` only on a STRONG witness whose hash still matches its current source. Weak
witnesses are recorded and reported, never credited -- #1934 requires explicitly that an
import/collection error not be laundered into the same field as an assertion failure.

THE HASH RULE closes the cheat this issue names: write a strict test, see red, write the
implementation, watch it STILL fail, then quietly weaken the test until it goes green. The record
would show a legitimate red-then-green while the test that PASSED is not the test that FAILED. Any
edit voids the witness and the red must be re-earned.

THE REFACTOR GAP, stated rather than implied away. Red-first covers NEW BEHAVIOUR ONLY. A
behaviour-preserving refactor produces no new red -- its tests are green throughout -- so this module
can never witness one. That is not a limitation to apologise for; it is the reason #1935 exists as
its pair, and an already-green test earns its `mutation` witness by killing a mutant instead.

VERDICT VOCABULARY IS #1933's, NOT A NEW ONE: verified / unverified / absent, the same three strings
already in the verify evidence's `flake` key. `absent` means nothing was measured and is never
silently a pass.

HOST-AGNOSTIC BY CONTRACT (#1934's own judged_when): this is Sigma's own Python, called from
loop.py's verify path. Not a Claude Code hook -- Cursor has none, and docs/output-contract.md is
host-agnostic.
"""
import ast
import hashlib
import json
import pathlib
import re
import time

#: Witness kinds that actually earn a credit. The two weak kinds are recorded so a reader can SEE
#: that the only red was trivial, which is strictly more useful than not recording them at all.
STRONG_KINDS = ("assertion", "mutation")
KINDS = ("assertion", "mutation", "collection-error", "other")

#: #1933's vocabulary, reused verbatim. A fourth string here is the divergence both issues warn about.
VERIFIED, UNVERIFIED, ABSENT = "verified", "unverified", "absent"

_ASSERTION = re.compile(r"\bAssertionError\b|^E\s+assert\b", re.M)
_COLLECTION = re.compile(r"\b(ImportError|ModuleNotFoundError|NameError|CollectError)\b"
                          r"|errors? during collection", re.M)


def path(sdlc_dir, goal):
    stem = str(goal).replace("/", "_").replace("..", "_")
    return pathlib.Path(sdlc_dir) / "state" / "witness" / f"{stem}.jsonl"


def test_source(node_id, root="."):
    """The source text of the test `node_id` names, or None if it cannot be located.

    Handles the three id shapes pytest emits: `f.py::test_a`, `f.py::TestC::test_a`, and a
    parametrised `f.py::test_a[case]` -- the bracket is stripped, so every case of one parametrised
    test shares one hash, which is correct: they share one body.

    Returns None rather than raising on a missing file, a syntax error, or an id naming something
    that is not there. A hash we cannot compute must degrade to "no witness", never to a crash in
    the verify path."""
    parts = str(node_id).split("::")
    if len(parts) < 2:
        return None
    file_path = pathlib.Path(root) / parts[0]
    try:
        src = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return None
    scope, first = tree, True
    for name in (p.split("[")[0] for p in parts[1:]):
        found = None
        candidates = ast.walk(scope) if first else scope.body
        for node in candidates:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == name:
                found = node
                break
        if found is None:
            return None
        scope, first = found, False
    try:
        return ast.get_source_segment(src, scope)
    except Exception:                       # noqa: BLE001 - see the None contract above
        return None


def source_hash(source):
    """sha256 of the test source, trailing whitespace stripped per line.

    NAMED `source_hash`, AND NOT BY PREFERENCE. mirror.py owns an identically-shaped name for its
    board-mirror digest, reserved to that module by an explicit decision which a guard in
    tests/test_mirror.py enforces across the whole of skills/ -- so that, in its own words, the
    decision "has to be re-argued rather than inherited" if a second reader ever appears. That guard is right and this module's first name was wrong: the
    collision was purely lexical (a test's source text has nothing to do with a board mirror), but
    firing on the identifier is exactly how an unrelated second reader gets noticed at all. Note the
    guard is a PLAIN STRING MATCH over the file, so even a docstring that spells the reserved name
    out trips it -- which is why this paragraph describes it instead of quoting it. Do not rename
    this back.

    Normalising ONLY trailing whitespace is deliberate: a reformat that changes indentation or
    wraps a line DOES change the hash, and should. The rule this hash enforces is "the test that
    passed is the test that failed", and a reflowed assertion is a different assertion until
    somebody has looked at it."""
    if source is None:
        return None
    normalised = "\n".join(line.rstrip() for line in source.splitlines())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def classify(output):
    """-> 'assertion' | 'collection-error' | 'other' for one test's failure output.

    ORDER MATTERS AND IS NOT ARBITRARY. Collection is checked FIRST: a module that fails to import
    often prints an AssertionError from somewhere else in the traceback, and reading that as a
    genuine assertion failure would launder the weakest possible red into the strongest kind --
    exactly what #1934 forbids."""
    text = output or ""
    if _COLLECTION.search(text):
        return "collection-error"
    if _ASSERTION.search(text):
        return "assertion"
    return "other"


def record(sdlc_dir, goal, test, kind, hash_, detail=None, now=None):
    """Append one witness. Never raises -- a bookkeeping write must not break a verify."""
    if kind not in KINDS:
        return None
    entry = {"test": str(test), "hash": hash_, "kind": kind,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
             "detail": detail}
    try:
        p = path(sdlc_dir, goal)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        return None
    return entry


def witnesses(sdlc_dir, goal):
    """Every witness for `goal`, oldest first. A malformed line is skipped, never fatal -- one bad
    append must not blind the whole record, mirroring ledger.read_all's own handling."""
    p = path(sdlc_dir, goal)
    if not p.exists():
        return []
    out = []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def verdict(sdlc_dir, goal, tests, root="."):
    """-> {"verdict", "verified": [...], "unverified": [...], "detail": {...}} for `tests`.

    A test is VERIFIED iff it has a STRONG witness whose hash equals its CURRENT source hash. A
    changed test voids its witness, which is the whole point of the hash. No tests at all -> absent;
    #1933's own ABSENT-vs-PASS distinction, and never silently a pass."""
    if not tests:
        return {"verdict": ABSENT, "verified": [], "unverified": [], "detail": {}}
    by_test = {}
    for w in witnesses(sdlc_dir, goal):
        by_test.setdefault(w.get("test"), []).append(w)
    good, bad, detail = [], [], {}
    for test in tests:
        current = source_hash(test_source(test, root=root))
        strong = [w for w in by_test.get(test, [])
                  if w.get("kind") in STRONG_KINDS and w.get("hash") == current and current]
        weak = [w for w in by_test.get(test, []) if w.get("kind") not in STRONG_KINDS]
        stale = [w for w in by_test.get(test, [])
                 if w.get("kind") in STRONG_KINDS and w.get("hash") != current]
        if strong:
            good.append(test)
            detail[test] = {"kind": strong[-1]["kind"], "why": "strong witness, hash matches"}
        else:
            bad.append(test)
            if stale:
                why = "test edited since its red -- witness voided, the red must be re-earned"
            elif weak:
                why = "only weak red (%s) -- proves the symbol was missing, not that the "\
                      "assertion checks anything" % weak[-1].get("kind")
            else:
                why = "never seen red"
            detail[test] = {"kind": None, "why": why}
    return {"verdict": VERIFIED if not bad else UNVERIFIED,
            "verified": good, "unverified": bad, "detail": detail}
