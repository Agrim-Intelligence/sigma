"""feature_doc.py -- the managed block in `.sdlc/features/<name>.md` (#1470, epic #1464).

The registry (#1469) holds the machine truth. This is the half a HUMAN reads, and the whole
difficulty is that one file now has two owners: a block Sigma regenerates, and everything
outside it, which belongs to whoever wrote it and must come back byte-for-byte.

The issue states four rules in precedence order, and every test here is one of them:

  1. the registry is authoritative for structured state -- the block is regenerated from it, and
     prose is NEVER parsed to recover state on the normal path;
  2. the region outside the block is human-owned and never rewritten;
  3. a hand-edit INSIDE the block is overwritten, and the divergence is written to the ledger --
     silently discarding someone's edit is the one outcome to avoid, and overwriting it while
     saying so is fine, because the block is marked as not theirs;
  4. a `<name>.md` with no entry in the registry is rebuilt from what still parses, and if nothing
     does, the file is left untouched and FLAGGED -- never deleted, never truncated.

THE THREE PROPERTIES THAT ARE ASSERTED ON BYTES, NOT ON RESULTS, because a result-shaped assertion
cannot tell "preserved" from "reconstructed identically":

  - what sits below the end marker comes back byte-identical, including CRLF, undecodable bytes and
    a missing final newline. That is why this module works on `bytes` end to end and never decodes
    the human region at all;
  - a file whose markers are damaged is byte-identical after a sync that refused to touch it;
  - a sync for unit A never opens unit B's file, the chart sheet, or any `units/` shard -- recorded
    by the paths touched, exactly as `tests/test_feature_registry.py` records them, because a write
    that opens B and happens to write it back unchanged passes every result assertion there is.

WHY A CHECKSUM, WHICH IS THE ONE DESIGN DECISION WORTH ARGUING WITH. Rule 3 needs to tell "a human
typed in the block" apart from "the registry changed", and comparing the block on disk against the
block about to be written cannot: both differ. Re-rendering the parsed block cannot either -- a hand
edit to a title produces a still-canonical block, so the check passes and the edit is discarded
silently, which is precisely the outcome the rule exists to prevent. The block therefore carries a
digest of its own body in its begin marker: a block whose digest vouches for its bytes was last
written by Sigma, and one whose digest does not is reported. A block with no digest at all --
the literal the design doc prints -- is reported too, because "cannot prove nothing was discarded"
must fail towards saying so.

WRITTEN AGAINST A MUTATION RUN, and the discipline is `tests/test_feature_registry.py`'s: every
tightening has a test that fails when that tightening ALONE is removed, via `_mod_with`, and every
such test asserts the MUTANT'S OWN BEHAVIOUR against HEAD's rather than merely asserting the suite
went red. An assertion about a mutant is worth nothing until the mutant has been shown to behave
differently from HEAD on some input.
"""
import builtins
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"
P = SCRIPTS / "feature_doc.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mod():
    return _load("feature_doc")


def _registry():
    return _load("feature_registry")


def _ledger():
    return _load("ledger")


def _mod_with(old, new):
    """The module rebuilt with a source substitution applied.

    Both of `tests/test_feature_registry.py::_mod_with`'s guards, for both of its reasons: the
    target is asserted to exist, so a drifted `old` cannot report "no survivors" while testing
    nothing, and EVERY occurrence is replaced, because this module quotes its own rules in its
    docstrings above the lines implementing them -- a first-occurrence replace mutates the prose and
    leaves the code untouched."""
    src = P.read_text(encoding="utf-8")
    assert old in src, "mutation target has drifted out of the source: %r" % (old,)
    namespace = {"__name__": "feature_doc_variant", "__file__": str(P)}
    exec(compile(src.replace(old, new), str(P), "exec"), namespace)          # noqa: S102 - test-only
    return types.SimpleNamespace(**namespace)


def _entry(**over):
    """A full, canonical entry -- every field present, so a test that changes ONE field is testing
    that field and nothing else."""
    base = {"title": "Manifest duration contract", "owner": "@unit-owner", "open": True,
            "parent": None, "tracking_issue": "org/repo#3100", "priority": None,
            "repos": {"org/repo": {"branch": "feature/int-contract", "owner": "@unit-owner",
                                   "authorized": True, "goals": [2871, 2879]}}}
    base.update(over)
    return base


def _sdlc(tmp_path, **ledger_settings):
    """A `.sdlc` with the ledger ON and an explicit actor -- explicit because `ledger.actor()` would
    otherwise shell out to `gh api user`, which is neither fast nor deterministic in a test."""
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir(parents=True, exist_ok=True)
    settings = {"enabled": True, "actor": "tester"}
    settings.update(ledger_settings)
    (sdlc / "config.json").write_text(json.dumps({"ledger": settings}), encoding="utf-8")
    return sdlc


def _features(tmp_path, **ledger_settings):
    return _sdlc(tmp_path, **ledger_settings) / "features"


def _entries(sdlc):
    return _ledger().read_all(sdlc)


def _inner_of(m, text):
    """The block's own body, as the module itself delimits it."""
    data = text.encode("utf-8")
    where = m.locate(data)
    assert where.state == m.INTACT, where
    return data[where.inner_start:where.inner_stop].decode("utf-8")


# --------------------------------------------------------------------------- 1. the markers


def test_the_markers_are_the_ones_the_design_prints():
    """The begin/end markers are a format other people write by hand and other tools will one day
    grep for, so the literals are a contract, not an internal detail."""
    m = _mod()
    assert m.BEGIN_OPEN == "<!-- sigma:begin managed"
    assert m.END == "<!-- sigma:end managed -->"


def test_the_documented_literal_begin_marker_is_still_recognised():
    """The design doc prints `<!-- sigma:begin managed -->` with no attributes. Adding the
    checksum must not make the published literal unreadable -- a file someone created by copying the
    doc has to be a file this module can find a block in."""
    m = _mod()
    text = "<!-- sigma:begin managed -->\nhand written\n" + m.END + "\n"
    where = m.locate(text.encode("utf-8"))
    assert where.state == m.INTACT
    assert where.checksum is None


def test_a_generated_block_carries_a_checksum_of_its_own_body():
    m = _mod()
    block = m.render_block("alpha", _entry())
    data = block.encode("utf-8")
    where = m.locate(data)
    assert where.state == m.INTACT
    assert where.checksum == hashlib.sha256(
        data[where.inner_start:where.inner_stop]).hexdigest()[:m.CHECKSUM_CHARS]


def test_the_checksum_covers_the_body_and_not_the_block_that_carries_it():
    """A digest over the WHOLE block cannot be computed -- the digest is inside the block. Pinned
    because that refactor is silently circular rather than loudly wrong: it would simply mean
    nothing ever vouches for itself again, and rule 3 would fire on every sync forever."""
    m = _mod()
    data = m.render_block("alpha", _entry()).encode("utf-8")
    where = m.locate(data)
    assert where.inner_start == data.index(b"-->") + len(m.BEGIN_CLOSE)
    assert where.checksum != hashlib.sha256(data).hexdigest()[:m.CHECKSUM_CHARS]


# --------------------------------------------------------------------------- 2. the round trip


def test_the_block_round_trips_through_its_own_rendering():
    """The recovery half of rule 4 is only worth having if what was written comes back. Compared
    against `normalise_entry`, never against the raw input: the registry owns every type rule, and
    a second opinion here would be a second answer."""
    m, r = _mod(), _registry()
    entry = _entry()
    assert m.parse_block(_inner_of(m, m.render_block("alpha", entry))) == r.normalise_entry(entry)


def test_a_units_priority_survives_the_managed_block_in_both_directions():
    """#2261. The block is a SECOND silent-loss path alongside the registry's own: `_body` and
    `parse_block` iterate the SAME fixed `FIELDS` tuple, so a field the tuple does not name is
    dropped on render AND unreadable on parse -- and this file is what rule 4 rebuilds a lost
    registry from, so a field missing here is a field a recovery cannot give back.

    Asserted on the rendered LINE as well as on the round trip: an entry that survived by some other
    route while the page said nothing would still be a page a human cannot read the priority off,
    which is half of what `<name>.md` is for."""
    m, r = _mod(), _registry()
    entry = _entry(priority="P0")
    block = m.render_block("alpha", entry)
    assert "- **priority:** `P0`" in block
    assert m.parse_block(_inner_of(m, block)) == r.normalise_entry(entry)
    assert m.parse_block(_inner_of(m, block))["priority"] == "P0"


def test_an_absent_priority_stays_absent_rather_than_becoming_a_tier():
    """The distinction `ABSENT_MARK` exists to keep: "this unit has no priority" is not "this unit is
    P4", and a renderer that collapsed them would invent a rank the registry never recorded."""
    m = _mod()
    block = m.render_block("alpha", _entry())
    assert "- **priority:** " + m.ABSENT_MARK in block
    assert m.parse_block(_inner_of(m, block))["priority"] is None


def test_dropping_priority_from_FIELDS_is_a_mutant_this_suite_kills():
    """The tuple is the whole of this module's half of slice 1, so it is pinned as a mutant rather
    than merely exercised -- and the mutant's OWN behaviour is asserted against HEAD's, not just the
    suite's colour. Both directions are checked on the mutant, because `FIELDS` drives both: it
    renders no priority line, and it cannot read one back out of a body that has it."""
    m = _mod()
    variant = _mod_with(
        '\n          ("tracking issue", "tracking_issue"), ("priority", "priority"))',
        '\n          ("tracking issue", "tracking_issue"))')
    entry = _entry(priority="P0")
    assert m.parse_block(_inner_of(m, m.render_block("alpha", entry)))["priority"] == "P0"
    assert "priority" not in variant.render_block("alpha", entry)
    # And the read half, given a body that DOES carry the line -- one written by a current install
    # and parsed by the mutant, which is exactly the older-plugin case B-1 accepted.
    assert variant.parse_block(_inner_of(m, m.render_block("alpha", entry)))["priority"] is None


def test_a_block_stating_only_a_priority_is_not_a_recovery(tmp_path):
    """The boundary `_is_substantive` draws, pinned rather than moved. `.sdlc/design/2253.md` names
    `_is_substantive` as a site this slice does NOT touch, so a unit whose block states a priority
    and nothing else stays non-substantive: rebuilding a lost registry as a registry of hollow units
    is worse than knowing it was lost, and a bare tier says nothing about what the unit IS.

    Written down because the alternative is discovering it -- the field is new, and "is a priority
    substance?" is a question somebody will otherwise answer by accident."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True, exist_ok=True)
    m.sync(features, "alpha", {"priority": "P0"})
    assert m.recover(features, "alpha") is None


@pytest.mark.parametrize("value", [
    "plain",
    "",
    "a | b",                                    # would split a markdown table cell
    "back\\slash",
    "a `code` span",
    "line\nbreak",
    "carriage\rreturn",
    "trailing space   ",
    "   leading space",
    "↔ astral \U0001f600 and Latin-1 é",
    "\ud800",                                   # a lone surrogate -- `read` accepts one
    "\udfff",
    "\x00embedded nul",
    "﻿byte order mark",
    "**bold** and # heading and | pipe",
    "<!-- sigma:end managed -->",            # THE MARKERS THEMSELVES -- a published format,
    "<!-- sigma:begin managed -->",          # so a unit documenting it carries them in a value
    "Adopter doc for <!-- sigma:end managed -->",
    "<!-- an unrelated html comment -->",
    "arrow --> in prose",
    "a < b and 1<2",                             # a bare `<` is not a hazard and must stay readable
])
def test_every_value_the_registry_accepts_round_trips_through_the_block(value):
    """The registry's read side is TOTAL, so any of these can arrive in an entry. Anything that did
    not survive the block would be data loss on the one file that exists to be a backup -- and a
    lone surrogate additionally has to survive the ENCODE, which is the exact defect
    `feature_registry.dumps` records fixing.

    THE LOCATABILITY ASSERTION IS THE ONE THAT MATTERS, and it was missing: parsing what was
    rendered says nothing about whether the FILE can be read again. Anything this module writes it
    must be able to find, or the first sync writes a doc that every later sync flags -- for ever,
    with a diagnosis blaming the file for damage the module inflicted."""
    m, r = _mod(), _registry()
    entry = _entry(title=value, owner=value, parent=value, tracking_issue=value, priority=value,
                   repos={value or "repo": {"branch": value, "owner": value,
                                            "authorized": True, "goals": [7]}})
    block = m.render_block("alpha", entry)
    block.encode("utf-8")                                   # the encode itself must not raise
    assert m.locate(block.encode("utf-8")).state == m.INTACT
    assert m.parse_block(_inner_of(m, block)) == r.normalise_entry(entry)


def test_anything_this_module_writes_it_can_read_back_including_its_own_markers():
    """The invariant, stated over the marker CONSTANTS rather than over copies of them, so it keeps
    holding if a marker's spelling ever changes. Every field is exercised because every field is
    rendered into the body, and a value only has to reach ONE of them to forge a marker."""
    m, r = _mod(), _registry()
    for marker in (m.BEGIN_OPEN, m.END, m.BEGIN_OPEN + " " + m.BEGIN_CLOSE):
        for field in [key for _label, key in m.FIELDS if key != "open"]:
            entry = _entry(**{field: marker})
            block = m.render_block("alpha", entry)
            assert m.locate(block.encode("utf-8")).state == m.INTACT, (field, marker)
            assert m.parse_block(_inner_of(m, block)) == r.normalise_entry(entry)
        for key in ("repo", "branch", "owner"):
            repo = {"branch": "b", "owner": "@o", "authorized": True, "goals": [1]}
            name = "org/repo"
            if key == "repo":
                name = marker
            else:
                repo[key] = marker
            entry = _entry(repos={name: repo})
            block = m.render_block("alpha", entry)
            assert m.locate(block.encode("utf-8")).state == m.INTACT, (key, marker)
            assert m.parse_block(_inner_of(m, block)) == r.normalise_entry(entry)


def test_a_unit_documenting_this_very_format_stays_writable(tmp_path):
    """The most plausible instance of the whole class, driven through `sync` rather than through the
    renderer: the unit created to document the managed-block format. Before the escape existed this
    was `created` once and `flagged` for ever after."""
    m = _mod()
    features = _features(tmp_path)
    entry = _entry(title="Adopter doc for " + m.END)
    assert m.sync(features, "alpha", entry)["outcome"] == m.CREATED
    assert m.sync(features, "alpha", entry)["outcome"] == m.UNCHANGED
    assert m.sync(features, "alpha", entry)["outcome"] == m.UNCHANGED
    assert m.recover(features, "alpha")["title"] == "Adopter doc for " + m.END


def test_the_escape_that_protects_the_markers_covers_every_marker():
    """WHY escaping one sequence is sufficient, checked rather than asserted: both markers begin
    with the HTML comment opener, so a value that cannot contain that opener cannot forge either."""
    m = _mod()
    for marker in (m.BEGIN_OPEN, m.END):
        assert marker.startswith(m.COMMENT_OPEN), marker
    assert m.COMMENT_OPEN not in m._escape(m.COMMENT_OPEN)


def test_an_entry_with_no_repos_round_trips_as_an_entry_with_no_repos():
    m, r = _mod(), _registry()
    entry = _entry(repos={})
    assert m.parse_block(_inner_of(m, m.render_block("a", entry))) == r.normalise_entry(entry)


def test_an_absent_field_and_an_empty_one_are_different_after_a_round_trip():
    """`owner: null` and `owner: ""` are different registry states, so the block has to be able to
    say both. Rendering both as a blank would collapse them, which is a silent edit of the backup."""
    m = _mod()
    absent = m.parse_block(_inner_of(m, m.render_block("a", _entry(owner=None))))
    empty = m.parse_block(_inner_of(m, m.render_block("a", _entry(owner=""))))
    assert absent["owner"] is None
    assert empty["owner"] == ""


def test_a_closed_unit_and_an_unauthorised_repo_round_trip_as_themselves():
    """The two fields whose safe value is a real boolean, taken on the side that is NOT the default:
    `open: false` is how a closed unit is marked (labels are never deleted, so this is what tooling
    filters on), and an unauthorised repo is one a board owner has not granted. A truthy read of
    either cell would quietly flip both back to the permissive value."""
    m, r = _mod(), _registry()
    entry = _entry(open=False, repos={"org/repo": {"branch": "b", "owner": None,
                                                   "authorized": False, "goals": []}})
    parsed = m.parse_block(_inner_of(m, m.render_block("a", entry)))
    assert parsed == r.normalise_entry(entry)
    assert parsed["open"] is False
    assert parsed["repos"]["org/repo"]["authorized"] is False


def test_the_block_states_both_levels_of_ownership():
    """Section 7.3: the unit owner and each repo's owner both appear in the managed block, because a
    cross-repo unit reaches into a repo whose owner has to be visible to a person reading the file."""
    m = _mod()
    block = m.render_block("alpha", _entry(owner="@unit-owner", repos={
        "org/repo": {"branch": "feature/alpha", "owner": "@repo-owner",
                     "authorized": True, "goals": [1]}}))
    assert "@unit-owner" in block and "@repo-owner" in block


def test_the_rendered_block_is_stable_so_a_pick_is_not_a_diff():
    """Two renderings of one entry are byte-identical, and repos are ordered by name rather than by
    whatever order a dict happened to carry -- the same reason `feature_registry.dumps` sorts keys:
    the file lives in git."""
    m = _mod()
    one = m.render_block("alpha", _entry(repos={
        "b/b": {"branch": "x", "owner": None, "authorized": False, "goals": []},
        "a/a": {"branch": "y", "owner": None, "authorized": True, "goals": [3]}}))
    two = m.render_block("alpha", _entry(repos={
        "a/a": {"branch": "y", "owner": None, "authorized": True, "goals": [3]},
        "b/b": {"branch": "x", "owner": None, "authorized": False, "goals": []}}))
    assert one == two
    assert one.index("a/a") < one.index("b/b")


def test_rendering_normalises_first_so_an_abbreviated_entry_renders_the_defaults():
    m, r = _mod(), _registry()
    assert m.render_block("a", {}) == m.render_block("a", r.normalise_entry({}))


def test_only_a_real_boolean_true_reaches_the_block_as_an_authorisation():
    """`authorized` is a board owner's grant, so the block must show what the REGISTRY would grant,
    not what a truthiness test would. The string "false" granting here would put a false statement
    in the one file a person without repo access reads."""
    m = _mod()
    block = m.render_block("a", _entry(repos={
        "org/repo": {"branch": "b", "owner": None, "authorized": "false", "goals": []}}))
    row = [line for line in block.split("\n")
           if line.startswith("|") and "org/repo" in line][0]
    assert row.endswith("| no | — |")


# --------------------------------------------------------------------------- 3. locating a block


def test_a_file_with_no_markers_at_all_has_no_block():
    m = _mod()
    assert m.locate(b"# notes\n\njust prose\n").state == m.ABSENT


@pytest.mark.parametrize("body,diagnosis", [
    ("<!-- sigma:begin managed sha256:0000000000000000 -->\nbody\n", "end marker is missing"),
    ("<!-- sigma:end managed -->\nbody\n", "end marker with no begin marker"),
    ("<!-- sigma:end managed -->\n<!-- sigma:begin managed -->\n",
     "end marker comes before"),
    ("<!-- sigma:begin managed -->\n<!-- sigma:begin managed -->\n"
     "<!-- sigma:end managed -->\n", "2 begin markers"),
    ("<!-- sigma:begin managed -->\n<!-- sigma:end managed -->\n"
     "<!-- sigma:end managed -->\n", "2 end markers"),
    ("<!-- sigma:begin managed\n<!-- sigma:end managed -->\ntail\n",
     "begin marker is never closed"),
])
def test_a_damaged_marker_pair_is_garbled_rather_than_guessed_at(body, diagnosis):
    """Every one of these is a shape where SOME splice is imaginable and all of them are wrong: the
    file's real boundaries are unknown, so the only safe verdict is that there is no usable block.
    The two-marker cases matter most -- a doc that quotes the format inside a fence produces them,
    and guessing which pair is real is how a human's prose gets truncated.

    THE DIAGNOSIS IS PINNED, NOT JUST THE VERDICT. A flag a person has to act on is only useful if
    it names the right damage: an unclosed begin marker diagnosed as "the end marker comes before
    the begin marker" sends them looking at the wrong line, and both misdiagnoses are reachable by
    deleting a single clause."""
    m = _mod()
    where = m.locate(body.encode("utf-8"))
    assert where.state == m.GARBLED
    assert diagnosis in where.reason, where.reason


def test_a_block_whose_body_contains_a_comment_is_still_located():
    """The generated body carries its own `<!-- ... -->` note, so the begin marker's close must be
    found by the marker's own `-->` and not by the next comment's."""
    m = _mod()
    where = m.locate(m.render_block("alpha", _entry()).encode("utf-8"))
    assert where.state == m.INTACT
    assert b"Generated" not in m.render_block("alpha", _entry()).encode("utf-8")[:where.inner_start]


# --------------------------------------------------------------------------- 4. sync: writing


def test_a_missing_file_is_created_with_a_block_and_a_human_region(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.CREATED
    text = report["path"].read_text(encoding="utf-8")
    assert m.locate(text.encode("utf-8")).state == m.INTACT
    assert text.index(m.END) < len(text.rstrip())          # something exists below the end marker


def test_a_second_sync_with_the_same_entry_writes_nothing(tmp_path):
    """An unchanged registry must not touch the file: a rewrite is a git diff, and a file that
    changes on every pick is one nobody can read a history out of."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    before = path.read_bytes()
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.UNCHANGED
    assert report["diverged"] is False
    assert path.read_bytes() == before


def test_a_changed_registry_regenerates_the_block_and_is_not_a_divergence(tmp_path):
    """The block moving because the REGISTRY moved is the normal case and must not be reported as
    somebody's edit -- a divergence signal that fires on every pick is not a signal."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    report = m.sync(features, "alpha", _entry(title="A different title"))
    assert report["outcome"] == m.UPDATED
    assert report["diverged"] is False
    assert "A different title" in path.read_text(encoding="utf-8")
    assert not [e for e in _entries(_sdlc(tmp_path)) if "alpha.md" in str(e.get("why", ""))]


def test_an_existing_file_with_no_block_keeps_every_byte_it_had(tmp_path):
    """A human deleted the block, or wrote the file themselves. The block is Sigma's and has to
    exist, but nothing they wrote may be lost -- so it is prepended, not merged into."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    path = features / "alpha.md"
    original = b"# my own notes\r\n\r\nno markers here\xff\n"
    path.write_bytes(original)
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.PREPENDED
    assert path.read_bytes().endswith(original)
    assert m.locate(path.read_bytes()).state == m.INTACT


# --------------------------------------------------------------------------- 5. the human region


@pytest.mark.parametrize("tail", [
    b"\n\n## Notes\n\nplain prose\n",
    b"\r\n\r\n## Notes\r\n\r\nCRLF prose\r\n",                       # newline translation would eat this
    b"\n\n## Notes\n\nundecodable \xff\xfe bytes\n",                # decoding would replace these
    b"\n\n## Notes\n\nno trailing newline",
    b"",                                                            # nothing at all below the marker
    b"\n\n```\n<!-- a fenced example, not a marker -->\n```\n",
])
def test_everything_below_the_end_marker_survives_byte_identically(tmp_path, tail):
    """RULE 2, asserted on bytes. This is why the module works on `bytes` end to end: reading the
    file as text would normalise CRLF and replace undecodable bytes, and writing it back would then
    silently rewrite a region it promised never to touch."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes(m.render_block("alpha", _entry()).encode("utf-8") + tail)

    report = m.sync(features, "alpha", _entry(title="a new title"))

    assert report["outcome"] == m.UPDATED
    assert path.read_bytes().endswith(tail)


def test_a_crlf_checkout_is_neither_a_divergence_nor_a_rewrite(tmp_path):
    """A CONSUMER REPO ON `core.autocrlf=true` -- the Git-for-Windows default -- gets every `\n` in
    the committed file turned into `\r\n` on checkout. Nothing pins `.sdlc/**` to LF, so this is an
    ordinary checkout, not a hostile one.

    Digesting the raw bytes made that checkout a PERMANENT false divergence: the block was reported
    as edited, the whole body handed back as discarded, a named owner accused in the ledger, and the
    block rewritten with LF -- which git converts straight back on the next checkout, for ever. That
    is exactly the failure the digest was chosen to avoid ("a signal that fires every time is not a
    signal"), moved to a different population of checkouts and made worse by naming somebody.

    Both halves are asserted. Not a divergence, and not a rewrite either: the file keeps the line
    endings its own checkout wants, so this does not become a diff on every pick instead."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry())["path"]
    assert m.sync(features, "alpha", _entry())["outcome"] == m.UNCHANGED          # baseline

    crlf = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert crlf != path.read_bytes(), "the fixture did not actually convert anything"
    path.write_bytes(crlf)

    for pass_number in range(3):
        report = m.sync(features, "alpha", _entry())
        assert report["diverged"] is False, pass_number
        assert report["outcome"] == m.UNCHANGED, pass_number
        assert path.read_bytes() == crlf, pass_number
    assert not [e for e in _entries(sdlc) if e.get("kind") == "note"], (
        "a line-ending conversion accused somebody in the ledger")


def test_crlf_tolerance_cannot_mask_a_real_edit_on_a_crlf_checkout(tmp_path):
    """The other side of the same coin: folding CRLF must buy the false negative NOTHING. A real
    hand-edit on a CRLF checkout is still caught, because only the line ENDING is folded and no
    content edit is expressible as "a CR before every LF"."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    crlf = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(crlf.replace(b"Manifest duration contract", b"TYPED BY A HUMAN"))
    assert m.sync(features, "alpha", _entry())["diverged"] is True


def test_an_edit_made_of_lone_carriage_returns_is_still_an_edit(tmp_path):
    """THE FOLD MUST STAY EXACTLY AS WIDE AS THE PROBLEM. `\r\n` is a line ENDING git writes, so
    folding it hides nothing anybody typed. A LONE `\r` is not: git never produces one, so a body
    whose line breaks are bare carriage returns is somebody's edit, and a fold wide enough to cover
    it would make that edit vouch for itself and be discarded in silence.

    Found by a mutation run, not by reasoning: the widened fold survived the whole suite."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    data = path.read_bytes()
    where = m.locate(data)
    body = data[where.inner_start:where.inner_stop].replace(b"\n", b"\r")
    assert b"\r\n" not in body, "the fixture must be LONE carriage returns, not CRLF"
    path.write_bytes(data[:where.inner_start] + body + data[where.inner_stop:])

    assert m.sync(features, "alpha", _entry())["diverged"] is True


def test_a_rendered_body_never_contains_a_raw_carriage_return():
    """WHY folding CRLF is lossless, measured rather than argued: if no body this module renders can
    contain a raw CR, then a CR on disk is always a line-ending conversion and never content."""
    m = _mod()
    nasty = ["\r", "\r\n", "a\rb", "\n", "\\r", "x" * 3, "", "`|\\", "\ud800", "—", "\u2028"]
    for i, one in enumerate(nasty):
        for other in nasty:
            entry = _entry(title=one, owner=other, parent=one, tracking_issue=other,
                           repos={one or "r%d" % i: {"branch": other, "owner": one,
                                                     "authorized": True, "goals": [1]}})
            body = _inner_of(m, m.render_block("alpha", entry))
            assert "\r" not in body, (one, other)


def test_a_value_carrying_a_line_terminator_cannot_be_normalised_out_from_under_the_digest(tmp_path):
    """A structural character written RAW into the block is a line ending somebody's editor will
    normalise on save -- and the digest, which covers the body byte for byte, then stops vouching
    for a body nobody edited. The block would be reported as diverged, and overwritten, because a
    text editor touched the file. Escaping the terminator is what keeps that from ever being true.

    This is the property that justifies escaping `\r`, which the round trip alone does not: this
    parser splits on `"\n"` and would recover a raw `\r` perfectly well."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry(title="carriage\rreturn"))["path"]

    tidied = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert tidied == path.read_bytes(), "a raw line terminator reached the file"

    path.write_bytes(tidied)
    assert m.sync(features, "alpha", _entry(title="carriage\rreturn"))["diverged"] is False


def test_prose_above_the_begin_marker_survives_too(tmp_path):
    """The convention puts the block first, but the rule is about ownership, not position: bytes
    outside the block are not Sigma's wherever they sit."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    path = features / "alpha.md"
    head = b"---\ntitle: hand written front matter\n---\n\n"
    path.write_bytes(head + m.render_block("alpha", _entry()).encode("utf-8") + b"\ntail\n")
    m.sync(features, "alpha", _entry(title="changed"))
    assert path.read_bytes().startswith(head)


# --------------------------------------------------------------------------- 6. divergence


def _hand_edit(path, m):
    """Type inside the managed block, the way a person would."""
    data = path.read_bytes()
    where = m.locate(data)
    body = data[where.inner_start:where.inner_stop].replace(
        b"Manifest duration contract", b"MY OWN TITLE, TYPED BY A HUMAN")
    path.write_bytes(data[:where.inner_start] + body + data[where.inner_stop:])


def test_a_hand_edit_inside_the_block_is_overwritten_and_reported(tmp_path):
    """RULE 3, both halves. The edit does not survive -- and it is not swallowed either."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry())["path"]
    _hand_edit(path, m)

    report = m.sync(features, "alpha", _entry())

    assert report["outcome"] == m.OVERWRITTEN
    assert report["diverged"] is True
    assert "MY OWN TITLE" not in path.read_text(encoding="utf-8")
    assert "MY OWN TITLE" in report["discarded"]
    named = [e for e in _entries(sdlc) if "alpha.md" in str(e.get("why", ""))]
    assert named, "the divergence produced no ledger entry naming the file"


def test_the_divergence_entry_reaches_the_unit_owner(tmp_path):
    """Section 7.3's pattern: an entry addressed to the owner reaches a person, where an unaddressed
    one sits in a stream nobody is reading."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry(owner="@unit-owner"))["path"]
    _hand_edit(path, m)
    m.sync(features, "alpha", _entry(owner="@unit-owner"))
    # #1574: the registry writes `@unit-owner`; the ledger stores the comparison spelling.
    assert [e for e in _entries(sdlc) if e.get("to") == "unit-owner"]


def test_a_hand_edit_that_only_changes_a_value_is_still_caught(tmp_path):
    """THE CASE A CANONICAL-FORM CHECK CANNOT SEE, and the reason the checksum exists. Editing a
    title in place leaves the block perfectly well-formed, so re-rendering what was parsed produces
    exactly the bytes on disk -- and the edit is then discarded in silence."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry())["path"]
    _hand_edit(path, m)

    body = _inner_of(m, path.read_text(encoding="utf-8"))
    canonical = _inner_of(m, m.render_block("alpha", m.parse_block(body)))
    assert canonical == body, (
        "the edited block is still canonical, so a check that re-renders what it parses sees "
        "nothing -- which is why this module does not use one")

    assert m.sync(features, "alpha", _entry())["diverged"] is True


@pytest.mark.parametrize("edit", [
    b" NOTE-FROM-A-HUMAN-please-keep-this -->",     # someone writes inside the marker line
    b"  -->",                                       # or merely respaces it
])
def test_an_edit_inside_the_begin_marker_line_is_inside_the_block(tmp_path, edit):
    """THE BLOCK IS WHAT `sync` SPLICES, and that is `data[start:stop]` -- which includes the begin
    marker's own interior. The digest covers only the body, so anything written on the marker line
    was deleted with no check at all and reported as `UPDATED`, which means "the registry moved"
    when nothing moved. The done-when says an edit inside the managed block is overwritten AND
    produces a ledger entry; the marker line is inside the block.

    The fix is not circular: the marker is compared against the exact marker `render_block` would
    emit FOR THE DIGEST THE MARKER ITSELF CARRIES, so nothing is hashed that contains the hash."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry())["path"]
    data = path.read_bytes()
    where = m.locate(data)
    tampered = data[:where.inner_start - len(m.BEGIN_CLOSE) - 1] + edit + data[where.inner_start:]
    assert tampered != data, "the fixture changed nothing"
    path.write_bytes(tampered)

    report = m.sync(features, "alpha", _entry())

    assert report["outcome"] == m.OVERWRITTEN
    assert report["diverged"] is True
    assert edit.decode("ascii").strip(" ->") in report["discarded"], (
        "what was discarded has to include the marker line, or the report describes a smaller "
        "loss than the one that happened")
    assert [e for e in _entries(sdlc) if "alpha.md" in str(e.get("why", ""))]


def test_a_marker_a_human_left_alone_still_vouches(tmp_path):
    """The control for the test above: tightening the marker check must not make every ordinary
    sync a divergence, which is the way this fix could go wrong."""
    m = _mod()
    features = _features(tmp_path)
    m.sync(features, "alpha", _entry())
    assert m.sync(features, "alpha", _entry())["outcome"] == m.UNCHANGED
    assert m.sync(features, "alpha", _entry(title="moved"))["outcome"] == m.UPDATED


def test_a_block_with_no_checksum_cannot_vouch_for_itself_and_is_reported(tmp_path):
    """The safe direction. A block whose provenance is unknown might be carrying somebody's work,
    and the one outcome to avoid is discarding it without saying so."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes(("<!-- sigma:begin managed -->\nhand written\n" + m.END + "\n")
                     .encode("utf-8"))
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.STAMPED
    assert report["diverged"] is False
    assert "hand written" in report["discarded"]


def test_a_block_with_no_digest_is_not_accused_of_an_edit_nobody_made(tmp_path):
    """NO DIGEST AND A WRONG DIGEST ARE DIFFERENT FACTS AND MUST NOT SHARE A REPORT.

    A file written by FOLLOWING THE DOCUMENTATION carried no digest, and the one report it produced
    was byte-for-byte the shape of a real discarded edit -- `OVERWRITTEN`, `diverged`, the whole body
    handed back, and a ledger line telling a named owner their work was thrown away. In this fixture
    nothing was edited, and in the general case nothing is KNOWABLE either way, which is the point:
    "cannot prove" is not "somebody edited this", and #1473 routes on this vocabulary, so the two
    need separate names.

    Nothing is hidden by the split: the body that was replaced still comes back in `discarded`, and
    the event is still recorded. Only the accusation, and the owner it was addressed to, are gone.

    THE SENTENCE ITSELF IS PINNED, not just the absence of the accusing one. The ledger line is read
    by exactly the person deciding whether to trust the machine, so it may not claim more than the
    code knows: an absent checksum tested against nothing supports "this cannot be shown", never
    "nothing was edited". A softer report is not licence for a stronger sentence."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes((m.BEGIN_OPEN + " " + m.BEGIN_CLOSE + "\nlegacy\n" + m.END + "\n")
                     .encode("utf-8"))

    report = m.sync(features, "alpha", _entry(owner="@unit-owner"))

    assert report["outcome"] == m.STAMPED
    assert report["diverged"] is False
    assert "legacy" in report["discarded"]           # still handed back -- nothing is hidden
    assert report["reason"]
    noted = [e for e in _entries(sdlc) if "alpha.md" in str(e.get("why", ""))]
    assert noted, "the event was not recorded at all"
    assert not [e for e in noted if e.get("to")], "a format upgrade was addressed to a person"
    assert "regenerated over an edit" not in noted[0]["why"]
    assert "cannot be shown" in noted[0]["why"], noted[0]["why"]
    assert "nothing is known to have been edited" not in noted[0]["why"], (
        "that phrasing reads as 'nothing was edited' -- a claim an absent checksum cannot support")
    assert m.sync(features, "alpha", _entry())["outcome"] == m.UNCHANGED    # one-shot, as claimed


def test_a_hand_written_block_can_never_carry_a_valid_digest(tmp_path):
    """WHY `STAMPED` IS A PERMANENT LANDING PLACE AND NOT A MIGRATION STEP. A person cannot compute
    the digest of a body they have not written yet, so no way of documenting the attributed marker
    can produce a hand-authored block that vouches for itself. Anything written by hand arrives here
    -- which is what makes the placeholder question below matter at all."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    body = _inner_of(m, m.render_block("alpha", _entry(title="what a person meant to write")))
    (features / "alpha.md").write_bytes(
        (m.BEGIN_OPEN + " " + m.BEGIN_CLOSE + body + m.END + "\n").encode("utf-8"))
    assert m.sync(features, "alpha", _entry())["outcome"] == m.STAMPED


@pytest.mark.parametrize("placeholder,parses,outcome_key", [
    ("0123456789abcdef", True, "OVERWRITTEN"),      # a PLAUSIBLE CONCRETE digest: the trap
    ("<written-by-sigma>", False, "STAMPED"),
    ("...", False, "STAMPED"),
    ("<digest>", False, "STAMPED"),
    ("0123456789abcde", False, "STAMPED"),          # one short of the width
    ("0123456789ABCDEF", False, "STAMPED"),         # upper case is not the written form
])
def test_a_documented_placeholder_must_not_be_readable_as_a_digest(tmp_path, placeholder, parses,
                                                                   outcome_key):
    """THE PLACEHOLDER A DOCUMENT PRINTS DECIDES WHAT HAPPENS TO EVERY FILE COPIED FROM IT, and the
    two directions are not symmetric. A placeholder this module can PARSE is read as a real digest,
    so it mismatches the body beside it and the file lands as a divergence accusing a named owner --
    strictly worse than printing no attribute at all, and worst for exactly the people `STAMPED`
    exists to protect. A placeholder it cannot parse reads as absent, which is benign and one-shot.

    Pinned here because the document is not in this repository and `_CHECKSUM_RE` is the only thing
    that actually decides the question. If the width or the alphabet ever changes, this fails and
    names the coupling instead of letting a document quietly become a landmine."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True)
    marker = "%s %s:%s %s" % (m.BEGIN_OPEN, m.CHECKSUM_KEY, placeholder, m.BEGIN_CLOSE)
    (features / "alpha.md").write_bytes((marker + "\nwhat a person wrote\n" + m.END + "\n")
                                        .encode("utf-8"))

    assert (m.locate((features / "alpha.md").read_bytes()).checksum is not None) is parses
    report = m.sync(features, "alpha", _entry(owner="@unit-owner"))

    assert report["outcome"] == getattr(m, outcome_key)
    accused = [e for e in _entries(sdlc) if e.get("to") == "unit-owner"]
    assert bool(accused) is parses, (
        "a placeholder that parses as a digest must accuse, and one that does not must not -- "
        "which is the whole reason a document may only print the second kind")


def test_a_wrong_digest_is_still_an_accusation_because_it_is_still_an_edit(tmp_path):
    """The control for the split: loosening the no-digest case must not loosen the case rule 3 is
    actually about."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    path = m.sync(features, "alpha", _entry(owner="@unit-owner"))["path"]
    _hand_edit(path, m)
    report = m.sync(features, "alpha", _entry(owner="@unit-owner"))
    assert report["outcome"] == m.OVERWRITTEN
    assert report["diverged"] is True
    assert [e for e in _entries(sdlc) if e.get("to") == "unit-owner"]


def test_every_outcome_is_declared(tmp_path):
    """`OUTCOMES` is the vocabulary #1473 routes on, so a new outcome that never reached the tuple
    would be a caller's unhandled branch."""
    m = _mod()
    assert m.STAMPED in m.OUTCOMES
    assert len(set(m.OUTCOMES)) == len(m.OUTCOMES)


def test_a_checksumless_block_that_already_says_the_right_thing_is_still_restored(tmp_path):
    """The subtle half of "cannot vouch for itself". A body that HAPPENS to equal what would be
    rendered still has unknown provenance, and leaving it alone because the bytes match would leave
    it unvouched for ever -- so the next real edit to it would look like the first one again, and
    the report a person is owed would keep being deferred."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    path = features / "alpha.md"
    body = _inner_of(m, m.render_block("alpha", _entry()))
    path.write_bytes((m.BEGIN_OPEN + " " + m.BEGIN_CLOSE + body + m.END + "\n").encode("utf-8"))

    report = m.sync(features, "alpha", _entry())

    assert report["outcome"] == m.STAMPED
    assert report["diverged"] is False
    assert m.locate(path.read_bytes()).checksum is not None
    assert m.sync(features, "alpha", _entry())["outcome"] == m.UNCHANGED     # and settles after


def test_a_forged_checksum_does_not_vouch_for_a_body_it_does_not_match(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    data = path.read_bytes()
    forged = data.replace(m.locate(data).checksum.encode("ascii"), b"0" * m.CHECKSUM_CHARS)
    assert forged != data                                            # the fixture really changed it
    path.write_bytes(forged)
    assert m.sync(features, "alpha", _entry())["diverged"] is True


def test_the_ledger_being_off_does_not_break_the_sync(tmp_path):
    """Every ledger call in this repo is fail-open. The block is still regenerated and the
    divergence still reaches the caller through the report."""
    m = _mod()
    features = _features(tmp_path, enabled=False)
    path = m.sync(features, "alpha", _entry())["path"]
    _hand_edit(path, m)
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.OVERWRITTEN and report["diverged"] is True


def test_a_missing_config_does_not_break_the_sync(tmp_path):
    """`safe_append` reads config.json inside its own guard, and this pins that we rely on that
    rather than reading it ourselves outside one."""
    m = _mod()
    features = tmp_path / ".sdlc" / "features"
    report = m.sync(features, "alpha", _entry())
    assert report["outcome"] == m.CREATED


# --------------------------------------------------------------------------- 7. damaged markers


@pytest.mark.parametrize("body", [
    "<!-- sigma:begin managed sha256:0000000000000000 -->\nbody\n\n## Notes\n\nmine\n",
    "<!-- sigma:begin managed -->\nbody\n<!-- sigma:begin managed -->\n"
    "<!-- sigma:end managed -->\n\n## Notes\n\nmine\n",
    "<!-- sigma:end managed -->\n\n## Notes\n\nmine\n",
])
def test_a_file_with_damaged_markers_is_flagged_and_left_byte_identical(tmp_path, body):
    """THE DONE-WHEN THAT MATTERS MOST: never truncated. With the end marker missing there is no
    boundary, so any splice this module could invent would eat somebody's prose."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes(body.encode("utf-8"))

    report = m.sync(features, "alpha", _entry())

    assert report["outcome"] == m.FLAGGED
    assert report["reason"]
    assert path.read_bytes() == body.encode("utf-8")
    assert [e for e in _entries(sdlc) if "alpha.md" in str(e.get("why", ""))]


def test_a_file_that_cannot_be_read_is_flagged_rather_than_replaced(tmp_path):
    """An unreadable file is not a missing one. Treating the two the same is how a mode-000 file
    with a year of notes in it becomes a fresh stub."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes(b"precious\n")
    path.chmod(0o000)
    try:
        report = m.sync(features, "alpha", _entry())
    finally:
        path.chmod(0o600)
    assert report["outcome"] == m.FLAGGED
    assert path.read_bytes() == b"precious\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")
def test_the_unreadable_file_fixture_is_really_unreadable(tmp_path):
    """The control for the test above: if the mode does not take, that test proves nothing."""
    path = tmp_path / "x"
    path.write_bytes(b"x")
    path.chmod(0o000)
    try:
        with pytest.raises(OSError):
            path.read_bytes()
    finally:
        path.chmod(0o600)


# --------------------------------------------------------------------------- 8. rule 4: no entry


def test_a_file_with_no_registry_entry_is_recovered_from_its_block(tmp_path):
    """RULE 4. The registry was lost; the block is the only surviving record of the unit, so this is
    the ONE path on which prose is parsed for state -- and the file is not touched while doing it."""
    m, r = _mod(), _registry()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    before = path.read_bytes()

    report = m.sync(features, "alpha", None)

    assert report["outcome"] == m.RECOVERED
    assert report["entry"] == r.normalise_entry(_entry())
    assert path.read_bytes() == before


def test_recovery_is_reachable_on_its_own_without_a_sync(tmp_path):
    m, r = _mod(), _registry()
    features = _features(tmp_path)
    m.sync(features, "alpha", _entry())
    assert m.recover(features, "alpha") == r.normalise_entry(_entry())


def test_a_file_with_no_entry_and_no_block_is_flagged_and_never_deleted(tmp_path):
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True)
    path = features / "alpha.md"
    path.write_bytes(b"# alpha\n\nsomeone wrote this by hand\n")

    report = m.sync(features, "alpha", None)

    assert report["outcome"] == m.ORPHANED
    assert path.exists() and path.read_bytes() == b"# alpha\n\nsomeone wrote this by hand\n"
    assert [e for e in _entries(sdlc) if "alpha.md" in str(e.get("why", ""))]


@pytest.mark.parametrize("text", ["\ud800", "a\udfffb", "\ud800\udc00"])
def test_parse_doc_never_raises_on_text_the_read_side_accepts(text):
    """`locate`, `parse_block` and `recover` all promise never to raise, and `parse_doc` is the
    documented convenience for callers holding TEXT -- so it inherits the promise. Its own
    `str.encode` broke it on exactly the value this module escapes everywhere else: a lone
    surrogate, which the registry's total read side accepts and utf-8 refuses. Same shape as the
    hole `feature_registry.dumps` records closing, re-opened one function along."""
    m = _mod()
    state, entry, reason = m.parse_doc("# notes\n\n" + text + "\n")
    assert state == m.ABSENT
    assert entry is None


def test_parse_doc_reads_a_block_whose_text_carries_a_surrogate():
    """And not merely by not raising: the block is still found in text that carries one."""
    m = _mod()
    state, entry, _reason = m.parse_doc(m.render_block("alpha", _entry()) + "\n\n\ud800\n")
    assert state == m.INTACT
    assert entry["title"] == "Manifest duration contract"


def test_a_block_carrying_nothing_recovers_nothing_rather_than_an_empty_unit(tmp_path):
    """"Rebuilt from the managed block IF IT STILL PARSES" has to mean something. A block whose
    fields are all gone parses into the schema's defaults -- an entry with no title, no owner and no
    repos -- and handing that back would let a lost registry be rebuilt as a registry of hollow
    units, which is worse than knowing it was lost."""
    m = _mod()
    features = _features(tmp_path)
    features.mkdir(parents=True)
    (features / "alpha.md").write_bytes(
        ("<!-- sigma:begin managed -->\n\nnothing structured here\n\n" + m.END + "\n")
        .encode("utf-8"))
    assert m.recover(features, "alpha") is None
    assert m.sync(features, "alpha", None)["outcome"] == m.ORPHANED


def test_a_partly_damaged_block_still_recovers_what_it_can(tmp_path):
    """Best effort is the whole point of rule 4: half an entry recovered beats a lost unit."""
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    data = path.read_bytes()
    path.write_bytes(data.replace(b"- **owner:**", b"- **ownr:**"))
    recovered = m.recover(features, "alpha")
    assert recovered is not None
    assert recovered["title"] == "Manifest duration contract"
    assert recovered["owner"] is None


def test_no_entry_and_no_file_is_simply_nothing_to_do(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    report = m.sync(features, "alpha", None)
    assert report["outcome"] == m.NOTHING
    assert not report["path"].exists()


def test_recovery_reads_the_unit_from_the_filename_not_from_the_block(tmp_path):
    """The same rule `feature_registry._read_unit_file` holds: what a file may speak for is bounded
    by its own name. A heading inside the block is display, never identity."""
    m = _mod()
    features = _features(tmp_path)
    m.sync(features, "alpha", _entry())
    path = features / "alpha.md"
    path.write_bytes(path.read_bytes().replace(b"# alpha", b"# beta"))
    assert m.recover(features, "alpha") is not None
    assert m.recover(features, "beta") is None


# --------------------------------------------------------------------------- 9. names and paths


def test_the_doc_sits_beside_the_chart_sheet_under_sdlc_features(tmp_path):
    m, r = _mod(), _registry()
    features = r.registry_dir(tmp_path / ".sdlc")
    assert m.doc_path(features, "alpha") == features / "alpha.md"
    assert m.doc_path(features, "alpha").parent == r.index_path(features).parent


def test_two_casings_of_one_unit_resolve_to_one_doc_path(tmp_path):
    """#1673. `doc_path` built its path from the RAW name, so `Voice` and `voice` were two pages
    for one unit -- and this one is the most visible of the family, because the second page is a
    document a person opens, that nothing regenerates and nothing reconciles.

    ASSERTED ON THE DERIVED PATH, not on filesystem behaviour, for the reason
    `test_two_casings_of_one_unit_resolve_to_one_shard_path` gives about the shard: this host's
    filesystem is case-INSENSITIVE and would resolve both spellings to one file, hiding the whole
    defect. Comparing the paths as values fails on every platform when the fold is missing."""
    m, r = _mod(), _registry()
    features = r.registry_dir(tmp_path / ".sdlc")
    for spelling in ("Voice", "VOICE", "vOiCe"):
        assert m.doc_path(features, spelling) == m.doc_path(features, "voice"), spelling


def test_a_mixed_case_unit_is_told_about_by_the_file_that_exists_not_the_casing_declared(tmp_path):
    """#1673's second half, and the half nothing else in this suite can see.

    Every other `sync` test here uses a lowercase unit, for which `path.name` and
    `name + DOC_SUFFIX` are byte-identical -- so the whole family of "which name does the message
    print" bugs is invisible to them in BOTH directions. This one uses `Voice`, where the two forms
    differ, which is the only way the assertion can discriminate.

    The FILE is what the message and the ledger `ref` must name. The fold means a unit declared
    `Voice` lives at `voice.md`, so a line saying "Voice.md was left untouched" sends a person to a
    path that is not there -- worse than saying nothing, because it reads as a fact. The unit keeps
    the casing its author wrote everywhere it is a RECORD; this is an ADDRESS."""
    m = _mod()
    sdlc = _sdlc(tmp_path)
    features = sdlc / "features"
    features.mkdir(parents=True, exist_ok=True)

    path = m.doc_path(features, "Voice")
    assert path.name == "voice.md", "the fixture is not exercising the fold at all"
    path.mkdir()                                   # a directory reads back as unreadable, not absent

    report = m.sync(features, "Voice", _entry())
    assert report["outcome"] == m.FLAGGED
    notes = [e for e in _entries(sdlc) if e.get("kind") == "note"]
    assert notes, "an unreadable doc must reach a person"
    said = " ".join(str(e.get("why", "")) for e in notes)
    assert "voice.md" in said, said
    assert "Voice.md" not in said, "the message named a file that does not exist: %s" % said
    assert any(str(e.get("ref", "")).endswith("features/voice.md") for e in notes), (
        "the ledger ref must be the path a person can follow: %s"
        % [e.get("ref") for e in notes])


def test_a_unit_name_ending_in_uppercase_LOCK_still_becomes_a_doc_path(tmp_path):
    """The ordering half of #1673, and it needs a test of its own at THIS site rather than an
    appeal to the registry's.

    `is_unit_name` rejects `voice.lock` (git rejects that ref) and ACCEPTS `voice.LOCK` (git accepts
    it), so folding BEFORE the guard would turn an accepted name into the spelling of a rejected
    one and this call would raise. The registry's own
    `test_a_unit_name_ending_in_uppercase_LOCK_still_addresses_a_file` pins that ordering for
    `unit_path` only -- a guard and a fold rearranged HERE would leave it green, which is why the
    assertion lives here too."""
    m, r = _mod(), _registry()
    features = r.registry_dir(tmp_path / ".sdlc")
    assert m.doc_path(features, "voice.LOCK").name == "voice.lock" + m.DOC_SUFFIX
    with pytest.raises(m.InvalidUnitName):
        m.doc_path(features, "voice.lock")


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "..", ".hidden", "x.", "voice.lock",
                                  "", None, 3, "index/../index"])
def test_a_name_that_is_not_a_unit_name_can_never_become_a_path(name):
    """The one place a name becomes a path is the one place that has to refuse."""
    m = _mod()
    with pytest.raises(m.InvalidUnitName):
        m.doc_path(pathlib.Path("/tmp"), name)


def test_the_naming_contract_is_the_registrys_own_exception_re_exported():
    """A caller must be able to write `except feature_doc.InvalidUnitName`, and it must be the SAME
    class the registry raises -- not a second one that a caller handling the registry's contract
    would miss. It stays a `ValueError` subclass, which is the form that survives the `_load` idiom
    handing every loader its own module object."""
    m = _mod()
    assert m.InvalidUnitName is m.registry.InvalidUnitName
    assert issubclass(m.InvalidUnitName, ValueError)


def test_render_block_refuses_a_name_that_could_not_address_a_file():
    """The name is written into the page as a heading. A name no file could carry has no business
    naming a unit inside one either, so the refusal is at both doors, not only the path one."""
    m = _mod()
    with pytest.raises(m.InvalidUnitName):
        m.render_block("../escape", _entry())


def test_the_name_rule_is_the_registrys_own_not_a_third_copy():
    """A unit name is a label, a branch segment, a shard filename and now a doc filename. Four
    spellings of one rule would be four answers.

    Identity against the registry instance this module actually uses is the structural half; a
    behavioural sweep against a separately loaded registry is the half that would still fail if
    someone replaced the borrowed binding with a copy that drifted."""
    m, r = _mod(), _registry()
    assert m.is_unit_name is m.registry.is_unit_name
    for name in ("alpha", "a.b", "voice.lock", "voice.LOCK", "a.lockfile", "x.", "..", ".hidden",
                 "a/b", "v1..2", "A-b_c.9", "1"):
        assert m.is_unit_name(name) == r.is_unit_name(name), name


def test_sync_refuses_an_illegal_name_and_writes_nothing(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    with pytest.raises(m.InvalidUnitName):
        m.sync(features, "../escape", _entry())
    assert not list(tmp_path.rglob("*.md"))


def test_a_table_row_naming_no_repo_is_skipped_rather_than_keyed_on_nothing():
    """Recovery reads a table that may already be damaged. A row whose repo cell lost its value has
    no repo to be about, and keying it on `None` would put a repo entry in the registry under a name
    that is not a name -- which `normalise_entry` would then drop anyway, silently."""
    m = _mod()
    body = "\n".join(["| repo | branch | owner | authorized | goals |",
                      "| --- | --- | --- | --- | --- |",
                      "| — | `feature/x` | — | yes | 1 |",
                      "| `org/repo` | `feature/y` | — | yes | 2 |"])
    assert list(m.parse_block(body)["repos"]) == ["org/repo"]


def test_recover_degrades_to_nothing_on_a_name_that_could_not_exist(tmp_path):
    """Unlike the write side, a READ of a name that cannot exist has an honest answer -- the same
    split `feature_registry.read_unit` draws against `write_unit`."""
    m = _mod()
    assert m.recover(_features(tmp_path), "../escape") is None


def test_a_diagnostic_that_cannot_be_written_is_still_not_an_exception(monkeypatch):
    """A flag is a diagnostic, and a diagnostic must never be the thing that breaks a pick."""
    m = _mod()

    class Broken:
        def write(self, _text):
            raise OSError("stderr is gone")

    monkeypatch.setattr(m.sys, "stderr", Broken())
    m._note("anything\n")


def test_a_cleanup_failure_never_masks_the_write_that_failed(tmp_path, monkeypatch):
    """The original failure is the one worth reporting; a temp file that also would not delete is
    litter, not a diagnosis."""
    m = _mod()
    features = _features(tmp_path)

    def replace_boom(*a, **kw):
        raise OSError("the real failure")

    def unlink_boom(*a, **kw):
        raise OSError("and the cleanup failed too")

    monkeypatch.setattr(m.os, "replace", replace_boom)
    monkeypatch.setattr(m.os, "unlink", unlink_boom)
    with pytest.raises(OSError, match="the real failure"):
        m.sync(features, "alpha", _entry())


# --------------------------------------------------------------------------- 10. isolation


class _Recorder:
    """Every filesystem path this process opens, replaces or creates, recorded rather than inferred.

    A faithful copy of `tests/test_feature_registry.py::_Recorder` and for its stated reason: a sync
    that opened another unit's file, read it, and happened to write it back unchanged passes every
    result-shaped assertion there is, and is exactly the write that loses that unit under two
    interleaved picks."""

    def __init__(self):
        self.opened, self.replaced, self.made = [], [], []

    def __enter__(self):
        self._saved = {"io": io.open, "builtins": builtins.open, "replace": os.replace,
                       "rename": os.rename, "remove": os.remove, "unlink": os.unlink,
                       "mkdir": os.mkdir, "makedirs": os.makedirs, "rmdir": os.rmdir}

        def wrap(real, log):
            def spy(path, *a, **kw):
                log.append(str(path))
                return real(path, *a, **kw)
            return spy

        def wrap2(real, log):
            def spy(src, dst, *a, **kw):
                log.append(str(src)); log.append(str(dst))
                return real(src, dst, *a, **kw)
            return spy

        io.open = wrap(self._saved["io"], self.opened)
        builtins.open = wrap(self._saved["builtins"], self.opened)
        os.replace = wrap2(self._saved["replace"], self.replaced)
        os.rename = wrap2(self._saved["rename"], self.replaced)
        for name in ("remove", "unlink", "mkdir", "makedirs", "rmdir"):
            setattr(os, name, wrap(self._saved[name], self.made))
        return self

    def __exit__(self, *exc):
        io.open = self._saved["io"]
        builtins.open = self._saved["builtins"]
        os.replace = self._saved["replace"]
        os.rename = self._saved["rename"]
        for name in ("remove", "unlink", "mkdir", "makedirs", "rmdir"):
            setattr(os, name, self._saved[name])
        return False

    def paths(self, under):
        under = str(pathlib.Path(under).resolve())
        out = set()
        for group in (self.opened, self.replaced, self.made):
            for p in group:
                full = str(pathlib.Path(p).resolve())
                if full == under or full.startswith(under + os.sep):
                    out.add(full)
        return out


def test_the_path_recorder_actually_records(tmp_path):
    """The positive control for the control. Without it every isolation test below is vacuous."""
    m = _mod()
    features = _features(tmp_path)
    with _Recorder() as rec:
        m.sync(features, "alpha", _entry())
    assert str(m.doc_path(features, "alpha").resolve()) in rec.paths(features)


def test_the_isolation_check_catches_a_sync_that_reads_a_sibling(tmp_path):
    """The control that matters: the module rebuilt doing the precise thing it must not do."""
    m = _mod()
    sabotaged = _mod_with("    data, unreadable = _read(path)",
                          "    _read(doc_path(features_dir, 'beta'))\n"
                          "    data, unreadable = _read(path)")
    features = _features(tmp_path)
    m.sync(features, "beta", _entry())
    with _Recorder() as rec:
        sabotaged.sync(features, "alpha", _entry())
    assert str(m.doc_path(features, "beta").resolve()) in rec.paths(features), (
        "the sabotaged sync read beta and the recorder did not notice -- every isolation "
        "assertion in this file would be vacuous")


def test_a_sync_for_one_unit_never_opens_another_units_file(tmp_path):
    m, r = _mod(), _registry()
    features = _features(tmp_path)
    m.sync(features, "beta", _entry(title="Beta"))
    r.write_unit(features, "beta", _entry(title="Beta"))
    r.write_index(features, {"beta": _entry(title="Beta")})

    with _Recorder() as rec:
        m.sync(features, "alpha", _entry(title="Alpha"))

    touched = rec.paths(features)
    assert str(m.doc_path(features, "alpha").resolve()) in touched          # positive control
    assert str(m.doc_path(features, "beta").resolve()) not in touched
    assert str(r.index_path(features).resolve()) not in touched
    assert not [p for p in touched if "beta" in pathlib.Path(p).name]


def test_a_sync_never_writes_the_registry_itself(tmp_path):
    """The doc is downstream of the registry. A sync that wrote a shard would make the human file an
    input to the machine truth, which is rule 1 inverted."""
    m, r = _mod(), _registry()
    features = _features(tmp_path)
    with _Recorder() as rec:
        m.sync(features, "alpha", _entry())
    touched = rec.paths(features)
    assert str(r.index_path(features).resolve()) not in touched
    assert not [p for p in touched if pathlib.Path(p).suffix == ".json"]


def test_a_sync_leaves_every_other_units_bytes_untouched(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    beta = m.sync(features, "beta", _entry(title="Beta"))["path"]
    before = beta.read_bytes()
    m.sync(features, "alpha", _entry(title="Alpha"))
    assert beta.read_bytes() == before


# --------------------------------------------------------------------------- 11. writes are atomic


def test_a_write_is_atomic_so_a_reader_never_sees_a_half_file(tmp_path):
    m = _mod()
    features = _features(tmp_path)
    path = m.sync(features, "alpha", _entry())["path"]
    seen = []
    real = os.replace

    def spy(src, dst, *a, **kw):
        seen.append(pathlib.Path(dst).read_bytes())
        return real(src, dst, *a, **kw)

    os.replace = spy
    try:
        m.sync(features, "alpha", _entry(title="second"))
    finally:
        os.replace = real
    assert seen and m.locate(seen[0]).state == m.INTACT       # the OLD file, whole, at replace time
    assert b"second" not in seen[0]
    assert b"second" in path.read_bytes()


def test_a_failed_write_leaves_no_litter(tmp_path, monkeypatch):
    m = _mod()
    features = _features(tmp_path)

    def boom(*a, **kw):
        raise OSError("no")

    monkeypatch.setattr(m.os, "replace", boom)
    with pytest.raises(OSError):
        m.sync(features, "alpha", _entry())
    assert not list(features.glob("*.tmp"))


# --------------------------------------------------------------------------- 12. mutants


def test_always_vouching_for_a_block_is_a_mutant_this_suite_kills(tmp_path):
    """The loosening that matters most: believe every block, and rule 3 silently stops existing --
    a hand-edit is discarded with no report and no ledger entry, which is the one outcome the issue
    names. Asserted as a behaviour difference against HEAD, not as "the suite went red"."""
    m = _mod()
    mutant = _mod_with("vouched = marker_intact and recorded == _checksum(body)", "vouched = True")
    features = _features(tmp_path)

    path = m.sync(features, "alpha", _entry())["path"]
    _hand_edit(path, m)
    assert m.sync(features, "alpha", _entry())["diverged"] is True            # HEAD's probe

    path = mutant.sync(features, "beta", _entry())["path"]
    _hand_edit(path, mutant)
    assert mutant.sync(features, "beta", _entry())["diverged"] is False       # the mutant differs


def test_writing_over_a_damaged_marker_pair_is_a_mutant_this_suite_kills(tmp_path):
    """Remove the refusal and the file is truncated at a boundary that was never found -- the exact
    prose loss the done-when forbids."""
    m = _mod()
    mutant = _mod_with("if where.state == GARBLED:", "if False:")
    features = _features(tmp_path)
    features.mkdir(parents=True)
    body = b"<!-- sigma:begin managed -->\nbody\n\n## Notes\n\nmine\n"

    (features / "alpha.md").write_bytes(body)
    assert m.sync(features, "alpha", _entry())["outcome"] == m.FLAGGED        # HEAD's probe
    assert (features / "alpha.md").read_bytes() == body

    (features / "beta.md").write_bytes(body)
    assert mutant.sync(features, "beta", _entry())["outcome"] != m.FLAGGED    # the mutant differs
    assert (features / "beta.md").read_bytes() != body


def test_recovering_a_hollow_entry_is_a_mutant_this_suite_kills(tmp_path):
    """Drop the substance test and a lost registry is rebuilt out of empty units -- a backup that
    says nothing while claiming to say something."""
    m = _mod()
    mutant = _mod_with("def _is_substantive(entry):", "def _is_substantive(entry):\n    return True")
    features = _features(tmp_path)
    features.mkdir(parents=True)
    hollow = ("<!-- sigma:begin managed -->\n\nnothing structured\n\n" + m.END + "\n")
    (features / "alpha.md").write_bytes(hollow.encode("utf-8"))

    assert m.recover(features, "alpha") is None                              # HEAD's probe
    assert mutant.recover(features, "alpha") is not None                     # the mutant differs


def test_not_escaping_the_table_delimiter_is_a_mutant_this_suite_kills():
    """A `|` in a title splits the row it lands in, and the value comes back truncated -- silent
    data loss on the file that exists to be a backup, and invisible in a rendered table."""
    m, r = _mod(), _registry()
    mutant = _mod_with('("|", "\\\\|"), ', "")
    entry = _entry(repos={"org/repo": {"branch": "a|b", "owner": None,
                                       "authorized": True, "goals": [1]}})

    assert m.parse_block(_inner_of(m, m.render_block("a", entry))) == r.normalise_entry(entry)
    assert mutant.parse_block(
        _inner_of(mutant, mutant.render_block("a", entry))) != r.normalise_entry(entry)


def test_writing_when_the_registry_has_no_entry_is_a_mutant_this_suite_kills(tmp_path):
    """Rule 4 inverted: with no entry, an unguarded sync writes an EMPTY unit over a file that
    described a real one -- deleting the record by rewriting it."""
    m = _mod()
    mutant = _mod_with("    if entry is None:", "    if False:")
    features = _features(tmp_path)

    path = m.sync(features, "alpha", _entry())["path"]
    before = path.read_bytes()
    assert m.sync(features, "alpha", None)["outcome"] == m.RECOVERED          # HEAD's probe
    assert path.read_bytes() == before

    other = mutant.sync(features, "beta", _entry())["path"]
    mutant.sync(features, "beta", None)
    assert b"Manifest duration contract" not in other.read_bytes()            # the mutant differs


def test_decoding_the_human_region_is_a_mutant_this_suite_kills(tmp_path):
    """Working on text rather than bytes is the quiet way rule 2 breaks: the prose survives every
    result-shaped assertion and comes back with its line endings rewritten."""
    m = _mod()
    mutant = _mod_with("        return path.read_bytes(), None",
                       "        return path.read_text(encoding='utf-8', errors='replace')"
                       ".encode('utf-8'), None")
    features = _features(tmp_path)
    features.mkdir(parents=True)
    tail = b"\r\n\r\n## Notes\r\n\r\nCRLF prose\r\n"

    for name, module in (("alpha", m), ("beta", mutant)):
        (features / (name + ".md")).write_bytes(
            module.render_block(name, _entry()).encode("utf-8") + tail)
        module.sync(features, name, _entry(title="changed"))

    assert (features / "alpha.md").read_bytes().endswith(tail)                # HEAD's probe
    assert not (features / "beta.md").read_bytes().endswith(tail)             # the mutant differs
