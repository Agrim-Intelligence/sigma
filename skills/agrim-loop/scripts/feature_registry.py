#!/usr/bin/env python3
"""The feature registry -- `.sdlc/features/`: schema, read and write (#1469, epic #1464, story #1427).

REMOTE IS THE TRUTH. The branches matching `feature/*` that actually exist ARE the live set of units
of work, and that cannot drift from reality. This module is not that; it is the COMMITTED BACKUP,
because a remote branch can be deleted -- by cleanup, by accident, by a merge -- taking with it every
trace of what that unit was and which goals belonged to it. Everything below follows from having
exactly one job: survive, and never be the thing that breaks a pick.

`features.py` is the read half one level down (what unit does this ISSUE declare?). This is the
store: what does the REPO remember about that unit? The two never re-derive each other's rules --
`is_unit_name` here IS `features._is_unit_name`, deliberately, because a unit name becomes both a
`feature:<name>` label and a filename, and two rules would be two answers. (The `.lock` clause is
the tell: git rejects `voice.lock` as a branch segment but accepts `voice.LOCK`, so a
case-insensitive re-derivation gets that one pair wrong and nothing else.)

THE LAYOUT, AND THE ONE PLACE IT GOES BEYOND THE ISSUE'S OWN DIRECTORY TREE:

    .sdlc/features/
    ├── index.json              the chart sheet -- the whole registry as one document
    ├── units/
    │   ├── int-contract.json   one file per unit: THE WRITE SURFACE
    │   └── voice-interview.json
    ├── int-contract.md         the human-readable per-unit file (#1470 owns its managed block)
    └── voice-interview.md

The design doc (§6) draws only `index.json` plus the `.md` files, and says the machine state lives
in `index.json`. Its own definition of done then requires that "a write for unit A never opens unit
B's file" and that "two concurrent picks on different units never touch the same path" -- and those
two sentences are not satisfiable by a single shared JSON document, because every pick would rewrite
the same file, every write would touch the same lines, and git would turn each pair of concurrent
picks into a merge conflict on the one file that exists to be a backup. That is precisely the
failure the doc's own "one file per unit, the same reasoning as the ledger" paragraph names.

So the requirement wins over the drawing, and the resolution is the ledger's, one axis over. The
ledger writes `ledger/entries/<actor>-<host>.<pid>.jsonl` -- one file per WRITING PROCESS, never
touching anyone else's -- and computes the team view as their UNION on read. Here it is one file per
UNIT, and:

  - `units/<name>.json` is what a pick writes. A write opens that path and nothing else, so two
    concurrent picks on different units are structurally incapable of colliding.
  - `index.json` is the chart sheet: the snapshot a fresh clone carries, and the form the registry
    is propagated to sibling repos in (§7.1 -- every participating repo holds the WHOLE entry).
    It is written only by `write_index`, which is a deliberate materialisation, never on the pick
    path.
  - `read` returns their union, and a unit's own file WINS for its own unit -- a shard is a write
    that has not been folded into the sheet yet, so it is the later statement. Folding them in (and
    deciding whether the folded shard is then removed) belongs to the sync goal, #1473, which is the
    only thing that knows whether the branch a shard names still exists.

WHY A `units/` SUBDIRECTORY RATHER THAN `<name>.json` BESIDE `<name>.md`. `index` is itself a legal
unit name, so a per-unit file sitting in the same directory as the chart sheet would one day BE the
chart sheet. A subdirectory removes the collision instead of reserving a name against it.

WHAT THE SHARED DIRECTORY COSTS, STATED RATHER THAN IMPLIED. Two concurrent writers do both touch
`units/` itself -- no per-unit-file layout can avoid a shared parent -- via
`mkdir(parents=True, exist_ok=True)`, whose race is resolved in the kernel and whose result is the
same directory either way. The requirement is about the FILES two writers would both rewrite, and
of those there are none.

WHAT THIS DOES **NOT** BUY, AND THE SENTENCE ABOVE MUST BE READ WITH IT: "structurally incapable of
colliding" IS ABOUT TWO DIFFERENT UNITS, AND ONLY THAT. Two picks on the SAME unit are the case a
unit of work exists for -- `_goals` de-duplicates precisely because re-picks happen -- and this
module gives them NOTHING. Each is a read-modify-write with no lock and no compare-and-swap, so the
last writer wins the whole file. MEASURED, not feared: 10 OS processes appending one goal number
each to one unit recorded 2-4 of 10, losing the rest, across repeated runs. The file was never
corrupt -- `os.replace` holds, every read parsed -- so the loss is silent, which is worse than a
crash and is the honest reason it is written here in full. #1469 scopes it out ("two concurrent
picks on DIFFERENT units"); serialising same-unit writes belongs to #1473, which owns the pick-path
write and is the only layer that knows a pick is happening at all.

THE READ SIDE IS TOTAL; THE WRITE SIDE IS NOT. `read` never raises, on anything: a missing
directory, a missing file, truncated JSON, undecodable bytes, a document nested deep enough to
blow the recursion limit, a schema version this code cannot read, a directory where a file should
be. Every one of those degrades to "no units known" (or, for one bad unit file, to "that one unit
is not known"), because a registry that throws takes the pick path down with it, which is strictly
worse than a registry that is merely empty. The tolerances are a faithful port of
`ledger.read_all`'s, which documents each one and why the obvious narrower catch is wrong.
`write_unit`, by contrast, RAISES on a name that is not a legal unit name -- a caller writing
`../../etc/passwd` has a bug, not a corrupt file, and there is no path such a name could safely
become.

AND THAT IS THE ONLY THING IT RAISES, WHICH IS THE OTHER HALF OF THE SAME PROMISE: WRITING CANNOT
FAIL ON DATA READING ACCEPTED. Those two totalities have to meet, because the fold #1473 performs is
literally `write_index(dir, read(dir))` -- and they did not. `read` accepted a lone surrogate out of
an untrusted, propagated `index.json`, and the serialiser then died on it with `UnicodeEncodeError`,
which IS a `ValueError`, which is what the naming contract was spelled as. Two fixes, and both
matter: `dumps` is now ASCII-only so the serialiser has nothing left it can fail on (see its
docstring for the measurement), and the naming contract is a NAMED exception, `InvalidUnitName`, so
a caller catching the contract it was told about cannot catch anything else by accident. The general
rule this module now holds itself to: a promise stated in terms of an exception hierarchy has to be
checked against every member of that hierarchy that can reach the caller, not only the one the
author was thinking about. This is the third defect of exactly that shape found here -- NUL raising
`ValueError` rather than `OSError`, the surrogate above, and `is_authorized` raising `TypeError` on
an unhashable repo key.

DEFAULTS ARE CHOSEN FOR WHICH WAY THEY FAIL, and the two that matter fail in opposite directions
for the same reason -- each towards the state you get by doing nothing:

  - `authorized` is a board owner's per-unit grant, so ONLY a real boolean `True` grants. Under a
    truthiness test the string "false" would grant, which is the exact opposite of what it says;
    absent, `"true"`, `1` and `[]` all mean not-granted.
  - `open` marks a unit live, and `open: false` is how a closed one is marked (labels are never
    deleted, so this is the field tooling filters on). ONLY a real boolean `False` closes: closing
    is the direction that makes a unit's goals stop being recorded, so a stringly-typed `"false"`
    would be a guess about someone's intent, and the safe guess is that nobody closed anything.

Module shape follows `features.py` and `owners.py`: zero third-party dependencies, module-level
constants, pure functions apart from the filesystem calls that are the point, loaded by siblings
via `_load("feature_registry")`.
"""
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


#: THE one rule for what a unit may be called, borrowed rather than re-derived -- see the module
#: docstring. `features.py` measured it against `git check-ref-format`; this module must not have a
#: second opinion, because the name it validates is the same string that becomes a branch segment.
is_unit_name = _load("features")._is_unit_name

#: #239: the previous name's schema id (`<previous>/features@1`) reads as `SCHEMA`; see legacy.py.
legacy = _load("legacy")

#: The version key. `index.json` is duplicated in full into every participating repo (§7.1), so this
#: string is a cross-repo contract: a document that does not carry it is not one this code can read.
SCHEMA = "sigma/features@1"

#: ADDING A FIELD TO AN ENTRY COSTS THAT FIELD ON EVERY OLDER INSTALL (#1477 F6). `parse` ->
#: `normalise_entry` is a WHITELIST: a key it does not know is dropped on the way through, and
#: `document`/`dumps` then write the reduced form back. That is correct for a reader hardening
#: against a hand-edited file, and it means an addition made INSIDE `@1` does not survive a round
#: trip through an older plugin. `feature_propagate` performs exactly such a round trip on every
#: cross-repo pick -- read the sibling's document, merge, write it back -- so a peer still on an
#: older version silently ERASES a newer field from that sibling's file, rather than merely failing
#: to read it. A bump is what makes the older reader refuse the document outright instead, which is
#: the failure everyone can see.
#:
#: THE COST IS THE DECISION, AND IT HAS BEEN TAKEN ONCE, DELIBERATELY. `priority` (#2261, B-1 of
#: `.sdlc/design/2253.md`) was added inside `@1` on the team's explicit call: backward compatibility
#: over loud refusal, because refusal is not narrower here -- a document declaring a version the
#: reader does not know contributes NOTHING, so a bump trades the loss of one optional field for the
#: loss of every unit, every recorded goal and every `authorized` grant on every install that has
#: not upgraded. `tests/test_feature_registry.py` measures that trade on a real run rather than
#: asserting it here: it rebuilds the older normaliser by mutation and reads off exactly what such
#: an install loses (the field) and keeps (everything else).
#:
#: SO THIS IS A JUDGEMENT TO MAKE PER FIELD, NOT A RULE THAT NOW SAYS YES. The question a field
#: addition has to answer is which loss is worse for THAT field: silently missing on old installs,
#: or an old install refusing the whole registry. A field the loop's correctness depends on -- one
#: whose absence would make an older peer do the WRONG thing rather than the previous thing -- still
#: needs the bump. `priority` is not one of those: without it a picker simply orders as it always
#: did, which is exactly the state every repo is in today.

#: `.sdlc/features/` -- the registry directory. Deliberately NOT under any of `setup.py`'s
#: RUNTIME_IGNORES: a gitignored backup is not a backup.
REGISTRY_DIRNAME = "features"

#: The chart sheet, and the per-unit files' own directory. See the module docstring for why the
#: latter is a subdirectory: `index` is a legal unit name.
INDEX_NAME = "index.json"
UNITS_DIRNAME = "units"

#: The suffix a per-unit file carries, and the glob that finds them. The glob is `*.json` and the
#: temp files `_atomic_write_text` leaves behind end in `.tmp`, so a crash between `mkstemp` and
#: `os.replace` costs litter and nothing else -- never a phantom unit.
UNIT_SUFFIX = ".json"

#: An issue number written as a string. Three separate decisions, each of which was found unpinned
#: by a mutation run at least once, so each is spelled out:
#:
#:  - `\Z`, NEVER `$`. `$` also matches immediately before a trailing newline, so `"12\n"` would
#:    parse as goal 12 -- the exact widening the no-strip rule in `_goal` refuses on the other side,
#:    and `$` is by far the likelier future edit of the two. `\Z` is the only end-of-STRING anchor
#:    Python offers.
#:  - `{1,9}`, not unbounded and not merely large. Nine digits covers any real issue number and
#:    keeps the matched text far below the point where `int()` refuses it (the CVE-2020-10735 digit
#:    limit `ledger._seq` documents), so the bound is what makes the `int()` on the next line
#:    unconditionally safe rather than safe-in-practice.
#:  - a regex at all, rather than `str.isdigit()`, which is additionally true of Unicode digits
#:    (`²`, `٠`) that `int()` cannot parse -- turning a hand-edited registry into an exception.
_GOAL_RE = re.compile(r"[0-9]{1,9}\Z")


class InvalidUnitName(ValueError):
    """The write side's ONE failure, given a type of its own so it cannot be confused with anybody
    else's.

    `write_unit` promises to raise when handed a name that is not a unit name, and that promise used
    to be spelled `ValueError` -- a class broad enough that `UnicodeEncodeError` arriving from the
    serialiser satisfied it too, and a caller catching `ValueError` to mean "bad name" would have
    said so about an encoding failure. A named subclass lets a caller catch exactly the contract it
    was told about; it still subclasses `ValueError`, so nothing that already catches the broad form
    changes behaviour. Same reasoning as `features.AmbiguousUnit`: an exception a caller is expected
    to handle deserves a name, not a base class."""


def _note(message):
    """One stderr line, never an exception -- same shape and same reason as `features._note`: a
    diagnostic must never be the thing that breaks a pick."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a pick
        pass


# --------------------------------------------------------------------------- paths


def registry_dir(sdlc_dir):
    """`.sdlc/features/` for a given `.sdlc`."""
    return pathlib.Path(sdlc_dir) / REGISTRY_DIRNAME


def index_path(features_dir):
    return pathlib.Path(features_dir) / INDEX_NAME


def units_dir(features_dir):
    return pathlib.Path(features_dir) / UNITS_DIRNAME


def unit_key(name):
    """THE FOLD, and the one definition of it: a unit NAME -> the ADDRESS every derived key uses.

    #1566 folded `unit_path` and `feature_sync.lock_path` and was believed to have folded "the
    derived keys"; #1577 then found two more unfolded, #1638 a third, and #1673 two more again --
    each fixed by writing `.lower()` once more at the new site. Five copies of one rule is how the
    rule drifts, and drifting is what let the same defect be found four separate times. So the rule
    is here, once, for a derived key to borrow (#1673).

    HOW MUCH OF THE FAMILY ACTUALLY BORROWS IT, said plainly rather than left to be assumed: this
    function is called by `unit_path`, `feature_doc.doc_path` and `feature_rebase.worktree_path`.
    `feature_propagate.sibling_path` inherits the same fold one step removed, by reading its
    filename off `unit_path`'s return (#1672). `feature_sync.lock_path`, `feature_rebase.lock_path`
    and `feature_rebase.filed_path` still spell `.lower()` themselves -- they fold correctly and the
    inventory pins them, so that is consolidation left undone rather than a defect, but "one
    definition" describes four of the seven fold sites and not all seven.

    IT DOES NOT GUARD, AND THAT IS DELIBERATE. The refusal stays at each site, where it can say what
    that particular path is for -- a shard, a page a person opens, a lock, a real checkout -- and
    each site re-derives `is_unit_name` for itself, which was measured and is fine. What must not be
    per-site is the fold.

    CALL IT AFTER THE GUARD, NEVER BEFORE. `is_unit_name` rejects `voice.lock` (git rejects that
    ref) but accepts `voice.LOCK` (git accepts it), so folding first turns an accepted name into the
    spelling of a rejected one. Any suffix is appended AFTER the fold, for the same reason.

    NOT ITSELF A DERIVED KEY, and the inventory that counts them does not mistake it for one.
    `tests/test_feature_registry.py` DISCOVERS every function in a `feature_*` module that JOINS A
    PATH and demands each be classified as folding, not folding, or taking no unit name at all.
    This function joins nothing -- it is the fold, not an address -- so it is excluded by what it
    does rather than by what it is called (#1674). Its name was once load-bearing, because that
    discovery matched on `*_path(..., name)`; it no longer is, and a rename would be safe."""
    return name.lower()


def unit_path(features_dir, name):
    """The SHARD's path, and the reference implementation of the refusal every unit path owes.

    Raises `ValueError` for anything `is_unit_name` rejects -- which is what keeps `a/b`, `..` and
    `../../etc/passwd` from ever addressing a file.

    IT IS NOT THE CHOKEPOINT THIS DOCSTRING USED TO CLAIM IT WAS (#1638). It said "every other
    function that needs a unit's path goes through here, so the guard cannot be bypassed", and that
    is false: `feature_doc.doc_path` and `feature_rebase.worktree_path` build a unit path without
    calling this at all, and `feature_propagate.sibling_path` calls it for the REFUSAL and then
    assembles a different string. The refusal is fine in all three -- measured, each rejects
    `../../etc/passwd` -- because each re-derives `is_unit_name` for itself. What did NOT propagate
    is the FOLD, which was the whole of #1638.

    ALL THREE NOW FOLD, by two different routes and in two goals of one unit.
    `feature_propagate.sibling_path` reads its filename off THIS function's return rather than
    rebuilding it (#1672); `feature_doc.doc_path` and `feature_rebase.worktree_path` borrow
    `unit_key` above rather than a copy of it (#1673). Either route ends at one definition, which
    is the property that matters -- the defect reopened three times because each fix wrote the rule
    again somewhere new.

    DO NOT READ THAT AS FINISHED. It is the state of a MEASURED set, not a guarantee about the next
    key somebody adds: `tests/test_feature_registry.py` discovers every function in the `feature_*`
    modules that JOINS A PATH and fails on one nobody has classified -- a name, a visibility and a
    parameter spelling are all things that discovery deliberately no longer reads, because matching
    on them let four of five injected keys through (#1674). That test is the count; this docstring
    is not. Its own comment states what the `/` predicate cannot see -- a string-joined address, and
    a derived key that is not a filesystem path at all, such as `feature_labels.label_for`."""
    if not (isinstance(name, str) and is_unit_name(name)):
        raise InvalidUnitName(
            "%r is not a unit name -- a unit name is one git branch segment (no '/', no '..', no "
            "leading '.', no trailing '.' or '.lock'), because it becomes both a feature:<name> "
            "label and a file in %s/" % (name, UNITS_DIRNAME))
    # #1566: THE FILENAME IS FOLDED, THE RECORD IS NOT. `_read_unit_file` already treats two
    # casings as one unit -- "two casings were never two units", for the reasons it records -- but
    # this line concatenated the RAW name, so the write side disagreed with the read side. On a
    # case-sensitive filesystem that is two shards for one unit, and `read` then resolves them by
    # DROPPING one (`_drop_same_unit`, last file wins), so the loser's goals and its `authorized`
    # grant vanish with no note. Folding here means the second shard is never created.
    #
    # AFTER the guard above, never before: `is_unit_name` rejects `voice.lock` (git rejects that ref)
    # but accepts `voice.LOCK` (git accepts it), so folding first would turn an accepted name into
    # the spelling of a rejected one. The suffix is appended after the fold for the same reason.
    #
    # #1673: THE FOLD ITSELF MOVED UP INTO `unit_key` and is borrowed here rather than spelled here.
    # The behaviour is unchanged; what changed is that `doc_path` and `worktree_path` now borrow the
    # same call instead of carrying a fourth and fifth copy of `.lower()`.
    return units_dir(features_dir) / (unit_key(name) + UNIT_SUFFIX)


# --------------------------------------------------------------------------- the schema


def _text(value):
    """A string field, or None. A non-string is degraded rather than raised on: a garbled title is
    a cosmetic loss, and losing the whole entry over it would not be."""
    return value if isinstance(value, str) else None


def _goal(value):
    """One issue number, or None.

    `isinstance(True, int)` is True in Python, so a bool slips through any plain `isinstance(x, int)`
    guard -- and `True` would silently become goal 1, a link to whatever issue #1 happens to be.
    Strings are accepted (a hand-edited registry writes `"2879"` as readily as `2879`) but only via
    `_GOAL_RE`; see its own note for why not `isdigit()`. Zero and negatives are not issue numbers.
    The string is matched AS WRITTEN, with no surrounding whitespace stripped first: nothing that
    serialises JSON emits `" 12 "`, so tolerating it would widen the accepted language to buy a case
    that does not occur, and every tolerance granted here is one more edge to keep off prose."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and _GOAL_RE.match(value):
        number = int(value)
        return number if number > 0 else None
    return None


def _goals(value):
    """The goals recorded against one repo: real issue numbers, de-duplicated, in FIRST-SEEN order.

    De-duplicated because #1473 re-picks a goal onto a unit it is already recorded under, and the
    record has to stay a set of goals rather than a tally of picks -- doing it here also makes
    `parse(document(r)) == r` hold, which a list that could grow on every pass would not.
    NOT sorted: the sequence is pick order, and that is itself part of what the backup remembers."""
    if not isinstance(value, (list, tuple)):
        return []
    out, seen = [], set()
    for item in value:
        number = _goal(item)
        if number is not None and number not in seen:
            seen.add(number)
            out.append(number)
    return out


def normalise_repo(raw):
    """One repo's participation in a unit, with every field at a known type.

    `raw.get("authorized") is True` is the whole of the grant rule and is deliberately not
    `bool(...)`: see the module docstring. A repo entry that is not a mapping at all degrades to the
    defaults rather than vanishing -- the repo KEY is the fact worth keeping (this unit touches that
    repo), and its garbled detail is not worth losing the relationship over."""
    raw = raw if isinstance(raw, dict) else {}
    return {"branch": _text(raw.get("branch")),
            "owner": _text(raw.get("owner")),
            "authorized": raw.get("authorized") is True,
            "goals": _goals(raw.get("goals"))}


def normalise_entry(raw):
    """One unit's entry, with every field at a known type and every default applied.

    `parent` is kept VERBATIM when it is a string, even one that is not a legal unit name: it is
    never turned into a path here (only `unit_path` does that, and it validates independently), so
    keeping an unresolvable value beats deleting it. The registry is a record, not a cache.

    `priority` (#2261) is `parent`'s rule for `parent`'s reason. It is the unit's own tier -- the
    tie-break a picker consults only among issues that are ALREADY eligible, never a rank written
    onto any member issue -- and it is stored exactly as it was written, unvalidated. Ranking it is
    `discovery.priority_rank`'s single job: that function already accepts a bare tier, any case, a
    value still carrying the `priority:` label prefix and an adopter's configured aliases, and it
    ranks whatever it cannot classify as unprioritised rather than guessing. A second opinion here
    would be a second answer to that question, and the one thing a normaliser must never do is
    decide that a value it did not recognise was never said. Absent is absent: `None` means this
    unit is unranked, which is NOT the same fact as the lowest tier."""
    raw = raw if isinstance(raw, dict) else {}
    repos = {}
    raw_repos = raw.get("repos")
    if isinstance(raw_repos, dict):
        for key, value in raw_repos.items():
            if isinstance(key, str) and key.strip():
                repos[key] = normalise_repo(value)
    return {"title": _text(raw.get("title")) or "",
            "owner": _text(raw.get("owner")),
            "open": raw.get("open") is not False,
            "parent": _text(raw.get("parent")),
            "tracking_issue": _text(raw.get("tracking_issue")),
            "priority": _text(raw.get("priority")),
            "repos": repos}


def is_authorized(entry, repo):
    """Has `repo`'s board owner granted this unit permission to create work there? (§7.3)

    Lives here rather than in the normaliser alone because the access check is handed RAW entries as
    often as normalised ones -- straight off a propagated `index.json`, say -- and a default that
    only holds after normalisation is exactly the default that is not there when it matters.
    Tolerates every missing layer for the same reason `read` does: an access check that raises is an
    access check somebody wraps in a bare `except` at the call site, and a swallowed exception
    defaults to whatever that call site felt like."""
    if not isinstance(entry, dict):
        return False
    if not isinstance(repo, str):
        # `repos.get(repo)` raises TypeError on an UNHASHABLE repo -- a list or a dict read straight
        # out of a hand-edited registry. Tolerating every malformed entry and no malformed repo was
        # the same half-kept promise as the two above: the repo key is registry CONTENT, and this
        # module trusts none of that.
        return False
    repos = entry.get("repos")
    if not isinstance(repos, dict):
        return False
    one = repos.get(repo)
    if not isinstance(one, dict):
        return False
    return one.get("authorized") is True


def parse(doc):
    """-> `{name: entry}` for one `sigma/features@1` document. Never raises; `{}` means nothing
    readable was in it.

    THE SCHEMA MUST MATCH. A document with no `schema`, or one naming a version this code does not
    know, contributes NOTHING rather than being read hopefully with `@1` semantics -- reading a `@2`
    document as `@1` is the exact failure the version key exists to prevent, and "no units known" is
    the specified degradation. A stderr note makes the silence discoverable, mirroring
    `features._note_hidden_marker`'s reason for existing.

    A KEY THAT IS NOT A LEGAL UNIT NAME IS DROPPED. `index.json` is copied wholesale between repos,
    so it is genuinely an untrusted document; a key the write side could never round-trip (`a/b`,
    `../../etc/passwd`, `voice.lock`) is one no file could ever hold, and handing it back would let
    the sheet name paths the name rule exists to forbid."""
    if not isinstance(doc, dict):
        return {}
    if not legacy.schema_is(doc.get("schema"), SCHEMA):
        if doc.get("features") is not None:
            _note("sigma: features: a registry document declares schema %r, not %r, so none of "
                  "it was read. Upgrade the plugin, or correct the schema key.\n"
                  % (doc.get("schema"), SCHEMA))
        return {}
    features = doc.get("features")
    if not isinstance(features, dict):
        return {}
    out = {}
    for name, entry in features.items():
        if isinstance(name, str) and is_unit_name(name):
            out[name] = normalise_entry(entry)
    return out


def document(registry):
    """-> the `sigma/features@1` document for a whole registry. The inverse of `parse`, at the
    level of VALUES rather than bytes: `parse(document(r)) == r` for any registry `parse` produced,
    while the bytes gain the defaults an abbreviated hand-written entry left out."""
    features = {}
    for name, entry in (registry if isinstance(registry, dict) else {}).items():
        if isinstance(name, str) and is_unit_name(name):
            features[name] = normalise_entry(entry)
    return {"schema": SCHEMA, "features": features}


def dumps(doc):
    """The committed form, and the half of WRITING CANNOT FAIL ON DATA READING ACCEPTED that lives
    outside the write functions.

    `sort_keys` because the file lives in git and a serialiser whose key order wanders makes every
    pick a diff. A trailing newline because a committed file without one is a permanent one-line
    diff.

    `ensure_ascii=True` IS THE LOAD-BEARING ONE, and it was `False`. The read side is total, so it
    accepts a lone surrogate out of a propagated `index.json` -- a document this module itself calls
    untrusted -- and `"\\ud800"` is ordinary JSON that `json.loads` returns as a real `str`. Encoding
    that str as UTF-8 then raises `UnicodeEncodeError`, so `write_index(dir, read(dir))` -- exactly
    the fold #1473 performs -- died on data this module had just handed the caller. Worse,
    `UnicodeEncodeError` IS a `ValueError`, and `write_unit` documents `ValueError` as meaning "that
    is not a unit name", so a caller obeying this module's own contract would have diagnosed an
    encoding failure as a naming bug. Same class as the NUL/`OSError` hole: a promise made in terms
    of one exception hierarchy, broken by a different member of it arriving from somewhere else.

    MEASURED, over lone surrogates (`\\ud800`, `\\udfff`), an embedded NUL, a BOM, an astral emoji
    and Latin-1: with `ensure_ascii=True` every one serialises, the output is ASCII-ONLY -- so the
    subsequent `.encode("utf-8")` has nothing left that could fail -- and every one comes back
    byte-identical through `json.loads`. Nothing is lost; only the on-disk spelling changes.

    THE COST IS REAL AND SMALL: a title's `\u2194` is written `\\u2194`. That is the right trade
    here and the specification already made it -- the design's own `index.json` example carries
    `"Manifest \\u2194 video duration contract"`, escaped -- because `index.json` is machine truth
    and `<name>.md` is the file humans read."""
    return json.dumps(doc, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


# --------------------------------------------------------------------------- reading


def _read_json(path):
    """-> a parsed JSON value, or None. NEVER raises.

    Every guard is a faithful port of `ledger.read_all`'s, which documents each one and why the
    obvious narrower catch is wrong:
      - `utf-8-sig` + `errors="replace"`: a plain-utf-8 read raises `UnicodeDecodeError` (a
        ValueError, NOT an OSError, so `except OSError` misses it) on one invalid byte, and a BOM on
        line 1 silently eats the first record;
      - `except (ValueError, RecursionError)`: a deeply nested document raises `RecursionError` (a
        RuntimeError, NOT a ValueError), which `except ValueError` alone lets escape. The tripping
        depth is interpreter-dependent, which is why the TYPE is caught rather than a depth bounded;
      - `except OSError` around the read: a directory where the file should be, a mode nothing can
        read, a dangling symlink -- each raises before any parsing happens;
      - `ValueError` alongside it, for the same "the obvious narrower catch is wrong" reason as the
        two above: a path carrying an embedded NUL raises `ValueError` from the syscall wrapper
        BEFORE any OSError can be produced, so an `except OSError` alone leaves `read`'s
        never-raises promise true only for paths the operating system can express."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def read_index(features_dir):
    """-> `{name: entry}` from the chart sheet alone. `{}` when it is missing, unreadable, corrupt,
    or written in a schema version this code cannot read."""
    doc = _read_json(index_path(features_dir))
    return {} if doc is None else parse(doc)


def _read_unit_file(path):
    """-> `(name, entry)` for one per-unit file, or None.

    WHAT A UNIT FILE CAN SAY IS BOUNDED BY ITS OWN FILENAME. It is a full `sigma/features@1`
    document -- one schema, one parser, no second format -- but only the entry whose key matches the
    filename is read. Otherwise a write to alpha's path could rewrite beta, and the isolation this
    whole layout exists for would be a convention rather than a structure.

    The match is CASE-INSENSITIVE, for the two reasons `ledger.files_for` records for the same
    comparison: a case-insensitive filesystem (macOS APFS, Windows NTFS) resolves `Alpha.json` and
    `alpha.json` to one file, and GitHub label names are case-insensitively unique anyway -- so two
    casings were never two units. The FILE's own spelling is what is returned, because that is what
    its author wrote."""
    doc = _read_json(path)
    if doc is None:
        return None
    stem = pathlib.Path(path).name[:-len(UNIT_SUFFIX)].lower()
    for name, entry in parse(doc).items():
        if name.lower() == stem:
            return name, entry
    return None


def _unit_files(features_dir):
    """Every per-unit file, sorted.

    MEASURED, not assumed, and the measurement corrected the guard. `pathlib.Path.glob` swallows
    the errors its own directory scan hits -- a missing directory, a mode-000 one, a plain file
    where the directory should be, an over-long name all return [] on CPython 3.13 rather than
    raising -- so `except OSError` here is the belt `ledger.read_all` documents needing, and it
    fires on no input this module could produce. `ValueError` is the one that actually fires: a path
    carrying an embedded NUL raises it, not an OSError, and it would otherwise escape `read`."""
    try:
        return sorted(units_dir(features_dir).glob("*" + UNIT_SUFFIX))
    except (OSError, ValueError):
        return []


def _drop_same_unit(registry, name):
    """Remove any key naming the same unit as `name`, case-insensitively -- see `_read_unit_file`
    for why two casings are one unit."""
    lowered = name.lower()
    for existing in [k for k in registry if k.lower() == lowered]:
        del registry[existing]


def read(features_dir):
    """-> `{name: entry}`, the whole registry. NEVER raises, whatever is on disk.

    The chart sheet supplies the baseline -- it is what a fresh clone carries, and the form the
    registry is propagated to sibling repos in -- and each per-unit file then REPLACES its own
    unit's entry outright. Replaces rather than merges: two half-descriptions of one unit merged
    together invent a third that neither source ever said. A shard is a write that has not been
    folded into the sheet yet, so for its own unit it is the later statement.

    ONE UNREADABLE UNIT FILE COSTS EXACTLY THAT ONE UNIT, AND COSTS IT OUTRIGHT. A shard that exists
    but cannot be used -- truncated, undecodable, declaring a schema version this code cannot read,
    or naming a different unit than its filename -- REMOVES its unit from the result rather than
    letting the chart sheet's older entry stand in for it. That is the ruling, and the reasoning is
    the registry's whole purpose: the shard is the write surface, so it is authoritative for its own
    unit, and the sheet's entry for that unit is by construction the state before the write that
    produced the shard. Serving it silently would answer a question about a unit with information
    known to be superseded -- which is the failure this registry exists to prevent, dressed as
    success. An absent unit makes the caller look; a stale one does not.

    The cost is stated plainly: this is strictly less available than falling back would be. It is
    also the only behaviour that matches the sentence above it, and the alternative was measured
    doing the opposite in silence -- a stale sheet entry winning over a corrupt shard, with no note
    and no signal to the caller. A note now names the file, because a unit vanishing from the
    registry is exactly the kind of thing whose cause has to be findable."""
    registry = read_index(features_dir)
    for path in _unit_files(features_dir):
        got = _read_unit_file(path)
        if got is None:
            _drop_same_unit(registry, path.name[:-len(UNIT_SUFFIX)])
            _note("sigma: features: %s could not be read as its unit's record, so that unit is "
                  "reported as unknown rather than from the older %s. Repair or delete the file.\n"
                  % (path, INDEX_NAME))
            continue
        name, entry = got
        _drop_same_unit(registry, name)
        registry[name] = entry
    return registry


def read_unit(features_dir, name):
    """-> the entry `name`'s OWN file declares, or None. Never the chart sheet's -- callers that
    want the effective entry want `read`. Degrades to None on an illegal name rather than raising:
    unlike the write side, a read of a name that cannot exist has an honest answer."""
    try:
        path = unit_path(features_dir, name)
    except ValueError:
        return None
    got = _read_unit_file(path)
    return None if got is None else got[1]


def resolve_open_unit(sdlc_dir, name):
    """#1820: is `name` a known, OPEN unit? -> the registry's OWN spelling of it, or `None`.

    The check `handoff.create_tracked_issue`'s `target_unit` override needs before it may stamp a
    unit onto an issue nobody inherited it from: #1820 asks that a discovered issue be cross-mapped
    onto "the other OPEN units in `.sdlc/features/index.json`", never onto one that does not exist
    or has already finished.

    MATCHED CASE-INSENSITIVELY, RETURNING THE REGISTRY'S OWN SPELLING -- the same rule this module
    already applies to itself. `_drop_same_unit` and `unit_key` both fold two casings of one name
    onto one address, because the registered spelling is what the label and the branch already use;
    a caller's own casing (typed by hand, or copied from an issue title) must never override it, or
    the stamped body marker and the label it is meant to agree with could disagree on casing alone.

    A CLOSED UNIT (`open: False`) ANSWERS `None`, INDISTINGUISHABLE FROM "UNKNOWN" -- deliberately.
    #1820's own stated exclusion is that a finished unit must never be auto- (or explicitly)
    retargeted; collapsing "closed" into the same answer as "no such unit" is what keeps every
    caller of this function from being tempted to special-case a closed one into some weaker,
    still-permitted retarget. Reopening a unit stays a human's call, made by hand-editing the issue
    itself -- not something this resolver, or anything built on it, may do on anyone's behalf.

    THE ONE DELIBERATE CARVE-OUT (#2362), STATED HERE SO IT IS NOT A SILENT CONTRADICTION OF THE
    PARAGRAPH ABOVE: a future tier-1 auto-classification step (`feature_classify.py`, slice B of
    epic #2260 -- not built yet) is permitted to reopen a closed unit WITHOUT a human editing the
    issue, but only when its own AI-judgment match is confident. That step does not use THIS
    function -- a confident classifier has to find a closed unit in order to reopen it, and this
    resolver answers `None` for one by design -- it uses `resolve_any_unit` below, which matches
    regardless of `open` state. So the rule this docstring states ("reopening a unit stays a
    human's call") is still true of `resolve_open_unit` itself; the exception lives one level up,
    in what a caller holding `resolve_any_unit`'s answer is permitted to do with it.

    AN ILLEGAL NAME IS REFUSED BEFORE THE REGISTRY IS EVEN READ (`is_unit_name`, the same guard
    `read_unit`'s own docstring describes) -- a name git could never accept as a unit was never
    going to match a registered one, and there is no point paying a filesystem read to learn that.

    NEVER RAISES: a missing or corrupt `.sdlc/features/` degrades to "no units known", exactly as
    `read()` itself degrades -- see this module's own docstring for why a registry that throws is
    strictly worse than one that is merely empty."""
    if not name or not is_unit_name(str(name)):
        return None
    lowered = str(name).lower()
    for key, entry in read(registry_dir(sdlc_dir)).items():
        if key.lower() == lowered:
            return key if entry.get("open") is True else None
    return None


def resolve_any_unit(sdlc_dir, name):
    """#2362: is `name` a known unit, OPEN OR CLOSED? -> the registry's OWN spelling of it, or
    `None`.

    THE SIBLING `resolve_open_unit` DELIBERATELY REFUSES, THIS FUNCTION DELIBERATELY DOES NOT.
    `resolve_open_unit` answers `None` for a closed unit on purpose, so that nothing built on it is
    ever tempted to retarget one -- see its own docstring. This function exists for the opposite
    need: a caller (the tier-1 auto-classifier, slice B of epic #2260, not built yet) that must
    decide whether to REOPEN a closed unit has to be able to find it first, and `resolve_open_unit`
    structurally cannot help with that, by design. Matching regardless of `open` state is this
    function's whole reason to exist, not an oversight to be reconciled with the sibling above.

    READS VIA `read()`, NEVER `read_unit()` ALONE -- THIS IS THE PART THAT MATTERS. `read_unit`
    answers only what a unit's OWN per-unit shard file says, and a shard is written only on a pick
    (`write_unit`); a unit whose latest state still lives solely in `index.json`, with no shard
    file yet, is invisible to `read_unit` even though the registry plainly knows about it. `read()`
    is the module's own specified total view -- the chart sheet with every shard folded over it --
    and it is what makes this resolver correct for that case. Getting this wrong would be a real
    bug for any later caller that reopens a unit based on this function's answer and then writes it
    back the way `read_unit`'s shape implies: `write_unit` demands the WHOLE entry (see its own
    docstring), and a caller who resolved a unit it could not actually see the full record of would
    write a truncated one, silently erasing whatever fields `index.json` alone was carrying (other
    repos, their goals, `tracking_issue`, `parent`) the moment that write lands.

    MATCHED CASE-INSENSITIVELY, RETURNING THE REGISTRY'S OWN SPELLING -- identical rule to
    `resolve_open_unit`, for the identical reason: the registered spelling is what the label and
    the branch already use, and a caller's own casing must never override it.

    AN ILLEGAL NAME IS REFUSED BEFORE THE REGISTRY IS EVEN READ, and a missing or corrupt
    `.sdlc/features/` degrades to "no units known" -- both inherited from `resolve_open_unit`
    verbatim; see its docstring for why."""
    if not name or not is_unit_name(str(name)):
        return None
    lowered = str(name).lower()
    for key, entry in read(registry_dir(sdlc_dir)).items():
        if key.lower() == lowered:
            return key
    return None


# --------------------------------------------------------------------------- writing


def _atomic_write_text(path, text):
    """Write via a temp file in the SAME directory, then `os.replace`.

    Mirrors `triage._atomic_write_text` and `state._patch_cursor`'s publish tail: same directory so
    `os.replace` stays on one filesystem, the only case it is guaranteed atomic, and a reader
    therefore only ever observes the fully-old or the fully-new file. That guarantee is the point
    here more than anywhere -- the backup's job is to still be readable after the crash that made it
    necessary.

    `mkstemp` names the temp file uniquely, so even two writers on the SAME unit cannot collide on
    it; the prefix carries the target's name so a leftover is obviously that unit's, and the `.tmp`
    suffix keeps it outside `_unit_files`' `*.json` glob, so a crash between the two steps costs
    litter and never a phantom unit.

    `exist_ok=True` on the shared parent is load-bearing: two concurrent writers on DIFFERENT units
    both reach this line, and the race is resolved in the kernel with the same directory either
    way. Without it, whichever writer loses the race dies on a directory that already exists."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:                   # noqa: S110 - cleanup must not mask the original failure
            pass
        raise


def write_unit(features_dir, name, entry):
    """Record one unit, and touch nothing else. -> the path written.

    This is the pick path's write, and its entire contract is negative: it opens `units/<name>.json`
    and no other file. Not `index.json`, not any other unit's file, not any `.md`. That is what
    makes two concurrent picks on different units structurally incapable of corrupting each other,
    and it is why the chart sheet is materialised separately (`write_index`) rather than here.

    PASS THE WHOLE ENTRY, NEVER A DELTA. THIS IS THE CALLER'S OBLIGATION AND IT IS NOT OPTIONAL.
    `read` REPLACES a unit's entry with its shard rather than merging the two, so whatever this
    write omits is gone from the effective registry: a pick that knows only its own repo and writes
    an entry naming only that repo erases the unit's other repos, their goals, its `tracking_issue`,
    its `parent`, and any field outside the schema. The loss then becomes permanent the moment
    anything folds the registry back into the chart sheet (`write_index(dir, read(dir))`, which is
    what #1473 does), and §7.1 requires every participating repo to hold the WHOLE entry, so a
    half-entry is not merely lossy -- it is invalid. The safe shape is read-modify-write:
    `entry = read(dir).get(name, {})`, amend it, write it back. This module deliberately ships no
    helper for that; see the module docstring for why doing it SAFELY under concurrency is #1473's
    problem, not something a helper here could honestly promise.

    RAISES `InvalidUnitName` -- a `ValueError` subclass, so existing broad handlers are unaffected --
    on a name that is not a unit name, and NOTHING ELSE. That "nothing else" is a promise, not an
    omission: `dumps` is ASCII-only precisely so that no value `read` accepted can make this raise
    from the serialiser. The read side is total; the write side is total except for the one
    condition it names, because a caller writing `../escape` has a bug rather than a corrupt file,
    and nothing is written when it does."""
    path = unit_path(features_dir, name)
    _atomic_write_text(path, dumps({"schema": SCHEMA,
                                    "features": {name: normalise_entry(entry)}}))
    return path


def write_index(features_dir, registry):
    """Materialise the chart sheet from a whole registry. -> the path written.

    DELIBERATELY NOT ON THE PICK PATH. `index.json` is the one file every unit would share, so
    writing it is a whole-registry act -- a fresh scaffold, a propagation to a sibling repo, or the
    sync pass that folds accumulated per-unit files back into the sheet. Whether that pass then
    removes the shards it folded in is #1473's call, not this module's: only the sync knows whether
    the branch a shard names still exists. Until it does, `read`'s shard-wins rule keeps the union
    correct with both present."""
    path = index_path(features_dir)
    _atomic_write_text(path, dumps(document(registry)))
    return path
