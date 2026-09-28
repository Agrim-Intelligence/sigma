"""#1574 -- ONE ADDRESS RULE, AND THE TEST THAT CATCHES THE SIXTH WRITER.

THE DEFECT, MEASURED: six sites address a ledger note to a unit's owner, and five of them wrote the
branching-model registry's raw `@handle` into `to`. `watch_classify.classify` -- the thing that
decides whether a session is WOKEN -- compared that field EXACTLY, so those five notes were written,
were correct, and reached nobody. The quietest possible failure: nothing errors, nothing is missing,
and the ask simply never arrives.

The sixth, `feature_owner`, normalised at its own call site -- and was ALSO undeliverable, for the
opposite reason. `ledger.address_key` folds case (GitHub logins are case-insensitively unique) while
`ledger.actor` does not, so its already-normalised `here-owner` failed the exact match against the
login `Here-Owner`. Normalising ONE side of a comparison is not a fix.

SO THIS FILE ASSERTS DELIVERY, NEVER SPELLING. A test that pinned `to == "unit-owner"` would pass
for a writer whose notes nobody can receive; every case here ends at the consumer -- `addressed_to`,
`classify`, `triage._bucket_inbox` -- and asks whether the named person is actually reached.

AND IT DISCOVERS ITS SUBJECTS. `_tell_modules()` globs the loop's script directory for every module
that defines a `_tell` rather than listing the five that exist today, so a SIXTH one added next year
is tested by this file the moment it is written, with nobody remembering to come back here. That is
the specific drift this issue is about: five call sites and one shared rule, with nothing structural
holding them together.
"""
import importlib.util
import inspect
import json
import pathlib

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ledger = _load("ledger")
watch = _load("watch")
watch_classify = _load("watch_classify")
triage = _load("triage")
cross_repo = _load("cross_repo")

#: The registry's spelling and the login it names. `docs/branching-model.md` ships
#: `"owner": "@unit-owner"` as its own canonical example, so the leading `@` is the DOCUMENTED
#: shape, not a user error -- and the mixed case is what proves the fold is needed on both sides.
HANDLE = "@Unit-Owner"
LOGIN = "Unit-Owner"

#: Who the session writing these entries is. Deliberately NOT the addressee: `classify` suppresses a
#: writer's own un-addressed writes, and an actor that happened to equal the addressee would let a
#: test pass through the self-addressed branch instead of the one under test.
ACTOR = "someone-else"

#: The five that exist today. Asserted as a SUBSET, never as the whole set -- pinning the whole set
#: would turn "a sixth writer was added" into a failure of this list rather than a test of that
#: writer, which is exactly backwards.
KNOWN_TELLS = {"feature_doc", "feature_owner", "feature_propagate", "feature_sync",
               "unit_completion"}


def _tell_modules():
    """Every loop script that defines a `_tell`, discovered by reading the tree."""
    found = {}
    for path in sorted(_SCRIPTS.glob("*.py")):
        if "\ndef _tell(" in path.read_text(encoding="utf-8"):
            found[path.stem] = _load(path.stem)
    return found


def _sdlc(root, ledger_on=True):
    d = pathlib.Path(root) / ".sdlc"
    (d / "state").mkdir(parents=True, exist_ok=True)
    cfg = {"ledger": {"enabled": ledger_on, "actor": ACTOR}} if ledger_on else {}
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    ledger.reset_actor_cache()
    return str(d)


def _woken(entries, me):
    """What `watch.tick` would surface for `me` -- the real classifier, wired the real way."""
    items, _ = watch_classify.classify(entries, {}, me, address=ledger.address_key)
    return items


# --- the writers ----------------------------------------------------------------------------------

def test_every_tell_site_is_found_and_has_the_one_shape_this_file_can_drive():
    """The discovery is only a guard if it actually finds things, and only a CONTRACT if the sixth
    site cannot quietly differ. `(sdlc_dir, <subject>, <subject>, why, to=None)` is the shape all
    five share; a new `_tell` that renamed `to` or moved it would fail HERE, loudly, rather than
    being called wrongly by the delivery test below and reported as a delivery bug."""
    mods = _tell_modules()
    assert KNOWN_TELLS <= set(mods), "a known _tell site disappeared or was renamed"
    for name, mod in sorted(mods.items()):
        params = list(inspect.signature(mod._tell).parameters)
        assert len(params) == 5, f"{name}._tell has an unexpected arity: {params}"
        assert params[0] == "sdlc_dir" and params[3] == "why" and params[4] == "to", \
            f"{name}._tell does not take the shared (sdlc_dir, _, _, why, to=None) shape: {params}"


def test_every_tell_addresses_a_handle_the_named_owner_is_actually_woken_by(tmp_path):
    """THE ONE THIS ISSUE EXISTS FOR, and it is written over the DISCOVERED set so a sixth site is
    covered without anyone editing this file.

    Three consumers, because they fail differently and only one of them was ever the symptom:
    `addressed_to` is how `ledger mine` and `autowatch` retrieve an ask, and it already normalised;
    `classify` is what WAKES a session and did not; and the stored spelling is asserted last and
    only as the ledger's own namespace, so this can never degrade into a spelling test."""
    for name, mod in sorted(_tell_modules().items()):
        sdlc = _sdlc(tmp_path / name)
        mod._tell(sdlc, "a-unit", "0001-x.md", "the owner is needed here", to=HANDLE)
        entries = ledger.read_all(sdlc)
        assert len(entries) == 1, f"{name}._tell wrote {len(entries)} entries"
        assert ledger.addressed_to(entries, LOGIN) == entries, f"{name}: not retrievable by {LOGIN}"
        assert _woken(entries, LOGIN) == entries, f"{name}: written, correct, and never delivered"
        assert entries[0]["to"] == ledger.address_key(HANDLE), f"{name}: not the ledger's namespace"


def test_the_addressed_writer_that_is_NOT_a_tell_goes_through_the_same_rule(tmp_path):
    """`cross_repo._raise_to` is the sixth site the issue counts and the one a `_tell` sweep cannot
    see: it calls `ledger.safe_append` directly. It is covered here BY NAME rather than by
    discovery, and that is the honest limitation of the sweep above -- which is also why the fix
    lives in `append` and not in five call sites: the chokepoint covers this one for free."""
    sdlc = _sdlc(tmp_path / "cross_repo")
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}}
    cross_repo._raise_to(sdlc, cfg, "0001-x.md", HANDLE, "that half needs somebody with access")
    entries = ledger.read_all(sdlc)
    assert len(entries) == 1
    assert ledger.addressed_to(entries, LOGIN) == entries
    assert _woken(entries, LOGIN) == entries


def test_an_address_that_names_nobody_is_dropped_rather_than_written_blank(tmp_path):
    """`"@"`, whitespace and a non-string all reduce to nothing. Writing `to: ""` would make the
    entry addressed to "two people nobody can name" -- which `address_key` explicitly refuses to
    treat as anybody -- and would make `classify` hand it to whichever session also failed to name
    itself. Dropping the field makes it honestly unaddressed, which every consumer already handles.
    """
    for value in ("@", "   ", "\t@ ", 7, True, None, ""):
        sdlc = _sdlc(tmp_path / ("blank-%s" % repr(value)))
        entry = ledger.append(sdlc, {"ledger": {"enabled": True, "actor": ACTOR}},
                              "note", "0001-x.md", to=value, why="w")
        assert "to" not in entry, f"{value!r} was written as an address"


def test_the_chokepoint_normalises_whatever_spelling_a_writer_hands_it(tmp_path):
    """`append` is where this lives, so no `_tell` has to remember and a seventh writer cannot get
    it wrong. The same rule `addressed_to` already matched with -- never a second one."""
    for spelling in ("@Unit-Owner", "unit-owner", "  @UNIT-OWNER  ", "Unit-Owner"):
        sdlc = _sdlc(tmp_path / ("spelling-%d" % len(spelling)))
        entry = ledger.append(sdlc, {"ledger": {"enabled": True, "actor": ACTOR}},
                              "note", "0001-x.md", to=spelling, why="w")
        assert entry["to"] == "unit-owner", f"{spelling!r} did not reach the ledger's namespace"


# --- the readers ------------------------------------------------------------------------------------

def _on_disk(to, kind="note", actor=ACTOR, seq=1):
    return {"id": f"{actor}:host.1:{seq}", "ts": "2026-08-20T00:00:00Z", "actor": actor,
            "kind": kind, "goal": "0001-x.md", "to": to, "why": "the owner is needed here"}


def test_an_entry_ALREADY_on_disk_in_the_raw_spelling_is_still_delivered():
    """THE HALF THE WRITE SIDE CANNOT REACH. Every note the five raw writers ever wrote is already
    in the shared ledger branch in the `@handle` spelling, and no change to `append` retrieves one
    of them. The read side is what does, which is why #1574 fixed both and why `address_key`'s
    docstring keeps its "on the READ side on purpose" paragraph."""
    entries = [_on_disk("@Unit-Owner")]
    assert _woken(entries, LOGIN) == entries
    assert ledger.addressed_to(entries, LOGIN) == entries


def test_a_case_folded_address_reaches_a_MIXED_CASE_login():
    """`feature_owner` normalised its own writes from the start and was STILL undeliverable through
    `classify`: `address_key` folds case and `ledger.actor` does not, so `here-owner` never equalled
    `Here-Owner`. This is the case the issue's own framing missed -- fixing only the writers would
    have made this WORSE, by folding four more writers into a spelling the waker could not match."""
    entries = [_on_disk("here-owner")]
    assert _woken(entries, "Here-Owner") == entries


def test_the_classifier_left_alone_still_compares_exactly():
    """The rule is INJECTED, not imported, because `watch_classify` pulls nothing in -- and the
    default therefore has to be the old behaviour byte-for-byte, so no other caller changes by
    being left alone. What keeps the wiring honest is `watch.tick`'s own test below, not this."""
    entries = [_on_disk("@Unit-Owner")]
    items, _ = watch_classify.classify(entries, {}, LOGIN)
    assert items == []


def test_watch_tick_is_actually_wired_to_the_rule(tmp_path):
    """END TO END, THROUGH THE PRODUCTION PATH. The injection above is only worth anything if the
    one production caller passes it, and an assertion on `classify` alone would pass forever after
    somebody dropped the keyword from `watch.tick`."""
    sdlc = _sdlc(tmp_path / "tick")
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}}
    ledger.append(sdlc, cfg, "note", "0001-x.md", to=HANDLE, why="the owner is needed here")
    summary = watch.tick(sdlc, cfg, me=LOGIN)
    assert summary, "the owner was not woken by the note addressed to them"
    assert "the owner is needed here" in watch.read_inbox(sdlc)


def test_triage_puts_a_handoff_addressed_to_a_handle_in_MY_inbox(tmp_path):
    """The other exact-match consumer. `triage`'s inbox bucket is the durable one (`unanswered`,
    not `classify`'s cursor-gated set), so a hand-off it cannot see is one nobody is reminded of
    again -- and it is the bucket the campaign planner puts FIRST."""
    sdlc = _sdlc(tmp_path / "triage")
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}}
    entries = [_on_disk("@Unit-Owner", kind="handoff")]
    bucket = triage._bucket_inbox(sdlc, cfg, entries, LOGIN)
    assert bucket["count"] == 1 and bucket["items"][0]["from"] == ACTOR


def test_triage_still_does_not_count_a_handoff_I_filed_myself(tmp_path):
    """The `actor != me` half is deliberately NOT folded -- `actor` has one writer and one
    spelling -- so widening the addressee match must not widen this. A hand-off I filed, addressed
    to me because CODEOWNERS resolved the area to me, is not a teammate blocked on us."""
    sdlc = _sdlc(tmp_path / "self")
    cfg = {"ledger": {"enabled": True, "actor": LOGIN}}
    entries = [_on_disk("@Unit-Owner", kind="handoff", actor=LOGIN)]
    assert triage._bucket_inbox(sdlc, cfg, entries, LOGIN)["count"] == 0


def test_an_unnameable_actor_is_addressed_by_nothing(tmp_path):
    """`me_key` falsy means the session could not name itself, which is addressed to NOBODY rather
    than to everybody whose entry happens to carry no `to`. `address_key`'s own ruling: two people
    nobody can name are not the same person."""
    sdlc = _sdlc(tmp_path / "nameless")
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}}
    entries = [_on_disk(None, kind="handoff"), _on_disk("@Unit-Owner", kind="handoff", seq=2)]
    assert triage._bucket_inbox(sdlc, cfg, entries, "")["count"] == 0
