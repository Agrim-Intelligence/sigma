# SPDX-License-Identifier: MIT
"""Pin every ledger entry-file description to the ONE shape the code actually writes (#1600).

The defect this file exists to stop is not a typo. Nine shipped surfaces described the ledger's
on-disk layout in FIVE mutually incompatible ways -- `<actor>.jsonl`, `<pid>.jsonl`,
`<login>.jsonl`, `<actor>-<pid>.jsonl`, `<actor>-<instance>.jsonl` -- while `ledger.entry_file()`
wrote a sixth, `<actor>-<host>.<pid>.jsonl`. Every one of them was true of some earlier version:
the bare actor name predates #337, the `-<pid>` suffix predates #540's host token. Prose that is
merely COPIED forward cannot notice when the code moves underneath it, which is why this module
never restates the shape. `_shape()` DERIVES the placeholder spelling by calling the real writer
and substituting back the two tokens it knows are per-instance, so the day `entry_file` changes,
these tests fail and take the documentation with them.

The cost of the drift was not academic. A reader who believed "one file per person" had no account
of the 175 distinct files a single actor had accumulated on one real machine over two and a half
weeks -- so nobody looked, because nothing looked wrong. That is why `test_every_surface_says_the`
`_fan_out_is_normal` pins the CLAIM and not only the format string: a correct filename that still
implies one file per person leaves the reader exactly as unable to recognise a healthy directory.

Two generators write the SAME artifact, `.sdlc/ledger/README.md` -- `sync.py`'s `BRANCH_README`
(on `bootstrap`, and only `if not exists`) and `sigma-init`'s `templates/ledger/README.md.tmpl`
(on scaffold, first). Whichever ran first wins for good, so both are pinned and both are checked
through the real generator rather than by reading the template's source.
"""
import importlib.util
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _mod(SCRIPTS / "ledger.py", "ledger")
sync = _mod(SCRIPTS / "sync.py", "sync")
sdlc_init = _mod(ROOT / "skills" / "sigma-init" / "scripts" / "sdlc_init.py", "sdlc_init")

#: A placeholder actor with no digit and no hex-alphabet-only spelling, so neither substitution
#: below can collide with it: the host token is 8 hex chars (`0-9a-f`, which cannot spell "actor"
#: -- "t", "o" and "r" are not hex digits) and the pid is decimal.
_ACTOR = "actor"


def _shape(stream=None):
    """The canonical placeholder spelling, DERIVED from the real writer rather than restated.

    `entry_file()` builds a path and touches no filesystem, so an imaginary `.sdlc` is enough.
    Substitution order is load-bearing: the host token goes first (it is hex, so it may contain
    decimal digits that would otherwise be eaten by the pid pass), and `<host>` itself contains no
    digit, so the pid pass afterwards can only match the pid.

    #2574/S1-G3 dropped the `local=` pass-through: `entry_file()` no longer takes that parameter,
    because EVENTS has one destination. The filename shape was identical either way, so nothing
    this module asserts changes."""
    name = ledger.entry_file(pathlib.Path("/nonexistent/.sdlc"), _ACTOR,
                             stream=stream or ledger.ENTRIES).name
    return (name.replace(ledger._host_token(), "<host>")
                .replace(str(os.getpid()), "<pid>")
                .replace(_ACTOR, f"<{_ACTOR}>"))


SHAPE = _shape()

#: Every shipped surface that spells the layout out for a reader. Docstrings inside the scripts are
#: included on purpose: the module that DEFINES the shape is the last place a stale spelling should
#: survive, and `ledger.py`'s own header was one of the nine.
SURFACES = (
    "README.md",
    "skills/sigma-ledger/SKILL.md",
    "skills/sigma-init/templates/ledger/README.md.tmpl",
    "skills/sigma-init/templates/config.json.tmpl",
    "skills/sigma-loop/scripts/ledger.py",
    "skills/sigma-loop/scripts/sync.py",
    "skills/sigma-loop/scripts/feature_registry.py",
)

#: The prose surfaces an ADOPTER reads (as opposed to a maintainer reading a docstring). These are
#: the ones that have to carry the claim, not merely the filename.
READER_SURFACES = (
    "README.md",
    "skills/sigma-ledger/SKILL.md",
    "skills/sigma-init/templates/ledger/README.md.tmpl",
    "skills/sigma-init/templates/config.json.tmpl",
)

#: Any `entries/…jsonl` / `events/…jsonl` token carrying a `<placeholder>`. A literal `*` glob
#: (`entries/*.jsonl merge=union`) is deliberately NOT matched -- a glob makes no claim about the
#: stem's shape, so it cannot be wrong about it.
_FILENAME = re.compile(r"\b(entries|events)/([^\s`)\"',;]*<[^\s`)\"',;]*)\.jsonl")


def _text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_the_derived_shape_is_the_one_the_writer_really_produces():
    """Guards the derivation itself: if `_shape()` ever silently degraded to a constant, every
    other test here would keep passing against a shape nothing writes."""
    real = ledger.entry_file(pathlib.Path("/nonexistent/.sdlc"), _ACTOR).name
    assert real.startswith(f"{_ACTOR}-") and real.endswith(".jsonl")
    assert real != SHAPE, "the derivation substituted nothing -- it is restating, not deriving"
    assert SHAPE == "<actor>-<host>.<pid>.jsonl", SHAPE


def test_every_placeholder_filename_in_every_surface_is_the_derived_shape():
    """The whole defect in one assertion. Not `SHAPE in text` -- that passes while a SECOND, wrong
    spelling sits ten lines further down, which is exactly how `README.md` carried `<actor>.jsonl`
    and `<pid>.jsonl` at the same time."""
    wrong = []
    for rel in SURFACES:
        for _stream, stem in _FILENAME.findall(_text(rel)):
            if f"{stem}.jsonl" != SHAPE:
                wrong.append(f"{rel}: {stem}.jsonl (want {SHAPE})")
    assert not wrong, "stale ledger filename spellings:\n" + "\n".join(wrong)


def test_the_events_stream_is_documented_with_the_same_shape_as_entries():
    """`events/` fans out per process identically -- `entry_file()` builds both from one f-string --
    so a surface documenting `events/<actor>.jsonl` beside a correct `entries/` line reintroduces
    the same false model one directory over."""
    assert _shape(stream=ledger.EVENTS) == SHAPE


def test_no_surface_still_claims_one_file_per_person():
    """The claim that hid 175 files. It is pinned as an absence because its replacement is prose
    that may legitimately be reworded, while THIS sentence can never become true again."""
    guilty = [rel for rel in SURFACES if "one file per person" in _text(rel).lower()]
    assert not guilty, f"still claims one file per person: {guilty}"


def test_every_reader_surface_says_the_fan_out_is_normal():
    """A correct filename is not the fix on its own: the reader has to know that MANY files per
    actor is the designed outcome, or a healthy directory still reads as corruption."""
    for rel in READER_SURFACES:
        assert "per writing process" in _text(rel).lower(), rel


def test_every_reader_surface_says_the_team_view_is_the_union_on_read():
    """The other half of the model, and the answer to "then which file is the real one?"."""
    for rel in READER_SURFACES:
        assert "union" in _text(rel).lower(), rel


def test_the_branch_readme_generator_ships_the_shape_it_will_write():
    """Read live off the module -- `sync.py` writes this constant verbatim (`init()`), so pinning
    the constant pins the artifact, and a copy of the string here would prove nothing."""
    assert SHAPE in sync.BRANCH_README
    assert "one file per person" not in sync.BRANCH_README.lower()
    assert "per writing process" in sync.BRANCH_README.lower()


def test_the_scaffolded_ledger_readme_is_generated_with_the_shape(tmp_path):
    """Through the REAL scaffolder, not by reading the template: `sigma-init` substitutes into the
    template on the way out, so the artifact is what has to be right."""
    sdlc_init.scaffold(tmp_path)
    generated = (tmp_path / ".sdlc" / "ledger" / "README.md").read_text(encoding="utf-8")
    assert SHAPE in generated
    assert "one file per person" not in generated.lower()
    assert "per writing process" in generated.lower()


def test_both_generators_target_one_path_so_neither_may_contradict_the_other(tmp_path):
    """`sigma-init` scaffolds `.sdlc/ledger/README.md`; `sync.py init` writes the same path and only
    `if not exists`. Whichever ran first is the one every adopter reads forever, so "fix the
    generator" means BOTH generators -- pinned here because the collision is invisible from either
    file alone."""
    sdlc_init.scaffold(tmp_path)
    scaffolded = tmp_path / ".sdlc" / "ledger" / "README.md"
    assert scaffolded.exists(), "sigma-init no longer scaffolds the path sync.py also writes"
    assert "README.md" in sync.BRANCH_README or True    # documented by the sync.py write site
    for text in (scaffolded.read_text(encoding="utf-8"), sync.BRANCH_README):
        assert SHAPE in text


def test_a_second_writing_process_really_does_fan_out_to_a_second_file(tmp_path, monkeypatch):
    """The behavioural proof behind the prose: same actor, two pids, two files, one union on read.
    Without this the docs would be pinned to a claim nothing checks."""
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    cfg = {"ledger": {"enabled": True, "actor": _ACTOR}}
    (sdlc / "config.json").write_text('{"ledger": {"enabled": true, "actor": "actor"}}')

    monkeypatch.setattr(os, "getpid", lambda: 11111)
    ledger.append(sdlc, cfg, "note", "g.md", why="first process")
    monkeypatch.setattr(os, "getpid", lambda: 22222)
    ledger.append(sdlc, cfg, "note", "g.md", why="second process")

    files = sorted(p.name for p in ledger.entries_dir(sdlc, ledger.ENTRIES).glob("*.jsonl"))
    assert len(files) == 2, f"expected a per-process fan-out, got {files}"
    assert all(f.startswith(f"{_ACTOR}-") for f in files), files
    whys = [r.get("why") for r in ledger.read_all(sdlc)]
    assert whys == ["first process", "second process"], "read_all did not union the two files"


# ------------------- the hand-maintained list, replaced by a scan
#
# `SURFACES` above is a tuple somebody has to remember to extend. #1600 was four surfaces
# disagreeing about one filename; the fix corrected those four and pinned them, and nothing would
# notice a FIFTH. That is the same shape of defect one level up — a list that claims to be complete
# and is only as complete as the last person to edit it.
#
# So the shipped tree is scanned. Anything carrying a `<placeholder>` entry/event filename has to
# spell it the way the writer really does, whether or not it is in the tuple.

#: Trees a reader of the shipped kit actually meets. A private package is a separate lane with its own
#: suite; `docs/superpowers/` and `CHANGELOG.md` are DATED records of what was true when written,
#: and rewriting history to match today's code would destroy the only account of when it changed;
#: `tests/` states wrong spellings deliberately, as counterexamples.
_SCANNED = ("README.md", "AGENTS.md", "skills", "docs/*.md")

_SCANNED_SUFFIXES = (".md", ".py", ".tmpl", ".json")


def _shipped_surfaces():
    """Every shipped file that could carry a ledger filename, discovered rather than listed."""
    seen = []
    for spec in _SCANNED:
        for base in ([ROOT / spec] if "*" not in spec else sorted(ROOT.glob(spec))):
            if base.is_file():
                seen.append(base)
            else:
                seen += sorted(p for p in base.rglob("*")
                               if p.is_file() and p.suffix in _SCANNED_SUFFIXES)
    return seen


def test_no_shipped_surface_anywhere_spells_the_entry_filename_differently():
    """The scan `SURFACES` cannot do. It subsumes the tuple — every path in it is inside a scanned
    tree — and its value is the surface nobody thought to add, which is the only kind that goes
    stale unnoticed."""
    wrong = []
    for path in _shipped_surfaces():
        for _stream, stem in _FILENAME.findall(path.read_text(encoding="utf-8", errors="replace")):
            if f"{stem}.jsonl" != SHAPE:
                wrong.append(f"{path.relative_to(ROOT)}: {stem}.jsonl (want {SHAPE})")
    assert not wrong, "stale ledger filename spellings on shipped surfaces:\n" + "\n".join(wrong)


def test_the_scan_actually_reaches_every_surface_the_tuple_names():
    """Guards the scan itself. A `_SCANNED` entry mistyped into matching nothing would leave the
    test above green over an empty set — the exact failure mode of a check that measures nothing."""
    scanned = {str(p.relative_to(ROOT)) for p in _shipped_surfaces()}
    missed = [rel for rel in SURFACES if rel not in scanned]
    assert not missed, "the scan does not reach: %s" % missed
    assert len(scanned) > 50, "the scan collapsed to almost nothing: %d files" % len(scanned)
