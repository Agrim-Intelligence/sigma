"""goal_decompose 8b (#520): two-direction corpus validation of `goal_size.classify` against
CHECKED-IN real-issue fixtures — never live `gh` fetches at test time (`verify.command` gates every
future goal's `done`, so a GitHub outage must not be able to refuse the whole backlog).

CALIBRATION PROVENANCE: the fixtures below were harvested once, at implementation time
(`gh issue view N --json body -q .body`, 2026-08-08), from this repo's own issue history — the same
269-issue corpus whose measurement retuned the constants in `goal_size.py` (see that module's own
docstring for the full both-direction margins). This file is the checked-in PROOF subset, not the
full 269 — small enough to read, large enough that a classifier which flags everything or nothing
fails loudly, naming the offending fixture.

THREE DIRECTIONS: POSITIVES (real epics, must flag), NEGATIVES (real small/conventionally-shaped
goals, must NOT flag), and a third direction, STUBS (#287-#294) — thin tracking-issue bodies that
must also NOT flag, but for a DIFFERENT reason than the negatives: they are exempted by this repo's
OWN label scheme (`epic` + deliberately not `sdlc:goal`), never reaching the loop's pick path at
all — not by anything the classifier itself detects. The classifier has no signal that could tell a
genuinely tiny goal from a tiny tracking stub; it isn't asked to. This direction pins that real
limit instead of asserting something the classifier structurally cannot do.

RECALL HONESTY: in this corpus, only the checkbox signal (signal 3) produces a true positive — all
9 real epics below are flagged via checkboxes, never via word count, line count, sections, or phase
structure alone. Those other four signals are validated here for PRECISION only (they correctly
stay silent across every negative and every stub); nothing in this corpus proves any of them can,
by itself, catch a real epic.

KNOWN MISS (#156): a real epic with only 3 top-level checkboxes — one under CHECKBOX_THRESHOLD=4 —
is NOT flagged by this classifier, and is deliberately not in the POSITIVES list below (it would
fail). Lowering CHECKBOX_THRESHOLD to 3 would catch it, at a measured cost of 34 additional false
positives across the 269-issue corpus. One true positive for thirty-four false ones was judged a
bad trade; #156 stays a documented, accepted miss rather than a silently-passing assumption.

No conftest.py in this repo (tests/test_import_boundary.py's own docstring records why) — `_mod()`
below is copied from tests/test_decompose_check.py:17-19, same as every other test file here.
Fixture loading mirrors a sub-package's `Path(__file__).parent / "fixtures"` convention — the
only on-disk fixture pattern already in this repo — but only the PATH SHAPE is borrowed; the
private side is never imported from here (tests/test_import_boundary.py forbids it, and this file
doesn't need it).

PROVENANCE (#2580): the fixtures are real issue bodies, neutralised TOKEN FOR TOKEN so the core's
tests name no private path, product or component -- the private package's name became `viz` (a
bare word, `Dashboard`), its ingest-adapter noun `gatherer`, and in goal-495.md its file, table,
function and command names were renamed one-for-one too. Word count, line count and `classify()` are identical for all
29 fixtures before and after (plan control C10), so the corpus measures what it always did."""
import ast
import importlib.util
import pathlib
import re

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"
FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "goal_size"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _load(name):
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# Filename encodes the expected direction (epic-* / stub-* / small-*+goal-*). A manifest-count
# guard below cross-checks this list against what's actually on disk, so a fixture lost to a bad
# rebase or an accidental `git rm` fails LOUDLY instead of the corpus quietly validating fewer
# inputs than it claims to.
_POSITIVES = [
    "epic-093.md", "epic-098.md", "epic-107.md", "epic-115.md", "epic-123.md",
    "epic-130.md", "epic-135.md", "epic-143.md", "epic-150.md",
]
_STUBS = [f"stub-{n}.md" for n in range(287, 295)]
_NEGATIVES = [
    "small-488.md", "small-499.md", "small-500.md", "small-505.md", "small-506.md",
    "goal-519.md", "goal-520.md", "goal-521.md", "goal-522.md",
    "goal-464.md", "goal-494.md", "goal-495.md",
]


def test_manifest_counts_match_the_checked_in_fixture_set():
    on_disk = sorted(p.name for p in FIXTURES_DIR.glob("*.md"))
    manifest = sorted(_POSITIVES + _STUBS + _NEGATIVES)
    assert on_disk == manifest, f"fixture set drifted from the manifest — disk={on_disk} manifest={manifest}"
    assert (len(_POSITIVES), len(_STUBS), len(_NEGATIVES)) == (9, 8, 12)


def test_all_positive_epics_are_flagged():
    gs = _mod("goal_size")
    for name in _POSITIVES:
        flagged, _ = gs.classify(_load(name))
        assert flagged is True, f"{name}: expected FLAGGED (real epic), got NOT flagged"


def test_all_stubs_are_not_flagged():
    """#287-#294: structurally invisible tracking stubs — thin bodies, no sections, no checkboxes.
    See the module docstring's THREE DIRECTIONS note: these are exempted by the label scheme, not
    the classifier."""
    gs = _mod("goal_size")
    for name in _STUBS:
        flagged, reason = gs.classify(_load(name))
        assert flagged is False, f"{name}: expected NOT flagged (label-exempted stub), got: {reason}"


def test_all_negatives_are_not_flagged():
    gs = _mod("goal_size")
    for name in _NEGATIVES:
        flagged, reason = gs.classify(_load(name))
        assert flagged is False, f"{name}: expected NOT flagged, got: {reason}"


def test_small_488_sanitization_preserved_word_count():
    """#488's fixture had its account-name strings replaced with acme-user/Acme-User before
    check-in — same-shape placeholders; the CASE difference is load-bearing for that body's own
    case-sensitivity bug story, so it's preserved, only the account identity is scrubbed. The
    substitution must be word-count-neutral: 409 before, 409 after (both are single hyphenated
    tokens, so whitespace-split word count cannot drift from swapping one for the other).

    Word count alone doesn't prove the sanitization survived — a fixture could be re-harvested
    from the live issue (raw account name back in place) and still coincidentally land on 409
    words. The placeholder-presence check below is what actually pins that: both case variants
    must still be there."""
    body = _load("small-488.md")
    assert len(body.split()) == 409
    assert "acme-user" in body and "Acme-User" in body, \
        "small-488.md looks re-harvested raw — the sanitized placeholders are gone"


# --------------------------------------------------------------------- fixture self-containment

# tests/test_self_contained.py's own leakage scanner (lines 53-85) explicitly SKIPS every path
# under tests/ ("test files legitimately NAME the banned words as leakage guards") — so a
# checked-in fixture under tests/fixtures/ is invisible to that scan by construction. Fixture
# cleanliness is self-enforced here instead, on two fronts: every checked-in fixture is scanned
# below for the same banned-word list that scanner uses (derived from its own source, not
# duplicated — see _shared_banned_words), and small-488.md specifically carries its own
# placeholder-presence pin above (test_small_488_sanitization_preserved_word_count) rather than a
# repo-wide account-name regex.


def _shared_banned_words():
    """Read tests/test_self_contained.py's own `banned = (...)` tuple as TEXT and parse it with
    `ast.literal_eval`, rather than hand-copying a second literal list that could silently drift
    from that file's own denylist — the same "derive a value from its source instead of
    re-deriving/duplicating it" precedent a downstream reader already follows for pipeline.py's
    severity order."""
    src = (pathlib.Path(__file__).resolve().parent / "test_self_contained.py").read_text(encoding="utf-8")
    m = re.search(r"banned = \(([^)]*)\)", src)
    assert m, "tests/test_self_contained.py's own banned=(...) tuple was not found by this reader"
    return ast.literal_eval("(" + m.group(1) + ")")


_BANNED = _shared_banned_words()


def test_fixtures_carry_no_banned_internal_strings():
    offenders = []
    for path in sorted(FIXTURES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {b!r}" for b in _BANNED if b in text]
    assert not offenders, "internal-string leakage in goal_size fixtures:\n" + "\n".join(offenders)
