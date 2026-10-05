import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


comment_watch = _mod("comment_watch")
ledger = _mod("ledger")
watch_classify = _mod("watch_classify")

# ACTOR is deliberately BOTH the claim holder AND this machine's own configured ledger.actor -- the
# normal solo/self-claimed deployment shape the plan review's B1 finding proved was broken pre-#477
# (a self-addressed note, actor==to, silently dropped before it ever reached the signature check or
# the inbox). Every "notifies the claimant" test below exercises exactly this shape, not the easier
# cross-actor case that already worked before #477.
ACTOR = "amy"
ON = {"ledger": {"enabled": True, "actor": ACTOR}, "comment_watch": {"enabled": True},
      "discovery": {"source": "github"}}


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else ON))
    return d


def _claim(d, goal, actor=ACTOR, ts=None):
    """Write a raw `claimed` ledger entry directly -- mirrors test_agent_watch.py's own
    direct-JSONL fixture style, so a test doesn't have to run the real loop.py claim path just to
    set up an open lease. `ts` defaults to "now" so the claim never falls outside the default 12h
    lease TTL just because the suite happens to run on a different day."""
    ledger.entries_dir(d).mkdir(parents=True, exist_ok=True)
    path = ledger.entry_file(d, actor)
    path.write_text(json.dumps({"id": f"{actor}:1", "ts": ts or ledger._stamp(), "actor": actor,
                                 "kind": "claimed", "goal": goal}) + "\n")


def _comment(cid, author, body, created_at):
    return {"id": cid, "author": {"login": author}, "body": body, "createdAt": created_at}


def _runner(by_goal):
    """Fake `gh` runner keyed by the issue number embedded in the `issue view <goal> ... --json
    comments` call (args[2]) -- comment_watch.py polls several issues in one tick, each needing its
    own canned answer. A value that is an Exception instance is raised instead of returned, so a
    single-issue `gh` failure can be simulated without affecting the others."""
    def run(args):
        goal = args[2]
        payload = by_goal.get(goal, [])
        if isinstance(payload, Exception):
            raise payload
        return json.dumps({"comments": payload})
    return run


# ------------------------------------------------------------------ enabled() / tick() gating


def test_tick_is_a_noop_when_comment_watch_disabled(tmp_path):
    """A claim MUST exist here -- without one, open_claims() == {} makes tick() a no-op
    regardless of whether the enabled() gate runs at all, so the assertion below would pass even
    with the gate deleted. A call-recorder (not a raising `run`) proves the gate returns before
    ever attempting a `gh` call -- `fetch_comments`'s own fail-open `except Exception` would
    silently swallow a raise, letting a deleted gate go undetected (independent review finding)."""
    d = _sdlc(tmp_path, {**ON, "comment_watch": {"enabled": False}})
    _claim(d, "50")
    calls = []
    assert comment_watch.tick(d, run=lambda args: calls.append(args)) == ""
    assert calls == []


def test_tick_is_a_noop_when_comment_watch_key_absent(tmp_path):
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}, "discovery": {"source": "github"}}
    d = _sdlc(tmp_path, cfg)
    _claim(d, "50")
    calls = []
    assert comment_watch.tick(d, run=lambda args: calls.append(args)) == ""
    assert calls == []


def test_tick_is_a_noop_in_local_mode(tmp_path):
    """Comments aren't a concept for local goal files -- must never even attempt a `gh` call. A
    call-recorder, not a raising `run`: `fetch_comments`'s own fail-open `except Exception` would
    silently swallow a raise, so a raise-based check can't actually prove `run` was never invoked
    (independent review finding)."""
    cfg = {"ledger": {"enabled": True, "actor": ACTOR}, "comment_watch": {"enabled": True}}
    d = _sdlc(tmp_path, cfg)          # no discovery.source: github at all -> local-goals default
    _claim(d, "50")

    calls = []
    assert comment_watch.tick(d, run=lambda args: calls.append(args)) == ""
    assert calls == []


# ------------------------------------------------------------------ core acceptance criteria


def test_new_comment_on_claimed_issue_notifies_the_claimant_exactly_once(tmp_path):
    """#385's core acceptance criterion, in the NORMAL solo deployment shape: this machine's own
    ledger.actor IS the claimant. Proves #477 (delivery) AND this module's ref-signature fix are
    both load-bearing together, not just that a ledger entry gets written -- asserts the note
    reaches the claimant's own `watch_classify.classify()` view, not only `ledger.read_all()`."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    run = _runner({"50": [_comment("IC_1", "bob", "please hold off on this", "2026-08-01T00:00:00Z")]})

    summary = comment_watch.tick(d, run=run)
    assert "#50" in summary and "1 new comment" in summary

    notes = [e for e in ledger.read_all(d) if e["kind"] == "note"]
    assert len(notes) == 1
    note = notes[0]
    assert note["actor"] == ACTOR and note["to"] == ACTOR       # solo shape: actor == to == claimant
    assert note["ref"] == "IC_1"
    assert note["issue"] == 50
    assert "bob" in note["why"] and "please hold off on this" in note["why"]

    # end-to-end: it must actually reach the claimant's own inbox, not just the ledger file
    items, _ = watch_classify.classify(ledger.read_all(d), dict(watch_classify.EMPTY_CURSOR), ACTOR)
    assert len(items) == 1 and items[0]["ref"] == "IC_1"


def test_second_tick_over_the_same_comment_notifies_nothing(tmp_path):
    """The cursor, not just the ledger's own signature dedup, must independently prevent the second
    write -- proves comment_watch.py's OWN exactly-once, not just a downstream safety net."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    run = _runner({"50": [_comment("IC_1", "bob", "hello", "2026-08-01T00:00:00Z")]})

    comment_watch.tick(d, run=run)
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1

    second = comment_watch.tick(d, run=run)                    # identical canned run both times
    assert second == ""
    assert len([e for e in ledger.read_all(d) if e["kind"] == "note"]) == 1   # still exactly one


def test_comment_on_unclaimed_issue_notifies_nothing(tmp_path):
    """A call-recorder, not a raising `run`: `fetch_comments`'s own fail-open `except Exception`
    would silently swallow a raise, so a raise-based check can't actually prove `run` was never
    invoked for an issue with no open claim (independent review finding)."""
    d = _sdlc(tmp_path)
    # no _claim() at all for "50"

    calls = []
    assert comment_watch.tick(d, run=lambda args: calls.append(args)) == ""
    assert calls == []
    assert [e for e in ledger.read_all(d) if e["kind"] == "note"] == []


def test_self_comment_on_own_claimed_issue_notifies_nothing(tmp_path):
    """The claimant commenting on their own claimed issue: suppressed via the comment's real GitHub
    author compared directly against the claimant (case-insensitive), not left to classify()'s
    downstream actor==me filter alone. Also proves requirement 4 (cursor advances unconditionally,
    even for a comment that triggered no note)."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    run = _runner({"50": [_comment("IC_1", ACTOR, "note to self", "2026-08-01T00:00:00Z"),
                          _comment("IC_2", "Amy", "case-varied self-comment", "2026-08-01T01:00:00Z")]})

    assert comment_watch.tick(d, run=run) == ""
    assert [e for e in ledger.read_all(d) if e["kind"] == "note"] == []

    cursor = comment_watch._load_cursor(comment_watch.cursor_path(d))
    assert set(cursor.get("50") or []) == {"IC_1", "IC_2"}


def test_two_distinct_comments_on_the_same_issue_both_notify(tmp_path):
    d = _sdlc(tmp_path)
    _claim(d, "50")
    run = _runner({"50": [_comment("IC_1", "bob", "first thing", "2026-08-01T00:00:00Z"),
                          _comment("IC_2", "bob", "second, different thing", "2026-08-01T01:00:00Z")]})

    comment_watch.tick(d, run=run)
    notes = [e for e in ledger.read_all(d) if e["kind"] == "note"]
    assert len(notes) == 2
    assert {n["ref"] for n in notes} == {"IC_1", "IC_2"}


def test_two_distinct_comments_both_surface_in_the_inbox_not_just_the_ledger(tmp_path):
    """The test the plan review names as the one that actually proves the ref-signature fix is
    load-bearing: two ledger entries getting WRITTEN is not enough (a naive, ref-unaware signature()
    would still write both) -- watch_classify.classify()'s OWN signature dedup is what would have
    collapsed them to one SURFACED item. Fails on the pre-fix 3-field signature; passes after."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    run = _runner({"50": [_comment("IC_1", "bob", "first thing", "2026-08-01T00:00:00Z"),
                          _comment("IC_2", "bob", "second, different thing", "2026-08-01T01:00:00Z")]})

    comment_watch.tick(d, run=run)
    items, _ = watch_classify.classify(ledger.read_all(d), dict(watch_classify.EMPTY_CURSOR), ACTOR)
    assert len(items) == 2
    assert {i["ref"] for i in items} == {"IC_1", "IC_2"}


def test_comment_text_reaching_the_ledger_is_scrubbed(tmp_path):
    d = _sdlc(tmp_path)
    _claim(d, "50")
    secret = "AK" "IAIOSFODNN7EXAMPLE"
    run = _runner({"50": [_comment("IC_1", "bob", f"here is a key: {secret}", "2026-08-01T00:00:00Z")]})

    comment_watch.tick(d, run=run)
    note = [e for e in ledger.read_all(d) if e["kind"] == "note"][0]
    assert secret not in note["why"]
    assert "[REDACTED:aws-key]" in note["why"]


def test_excerpt_is_truncated_before_the_ledger_caps_it(tmp_path):
    """Plan-review §8.5 spot-check: asserting only `len(why) <= 200` would be vacuous (ledger's own
    FREE_TEXT_CAP guarantees that regardless of whether `_excerpt` does anything at all). Assert the
    160-char excerpt boundary itself fires (the trailing "...") -- proves comment_watch.py's OWN
    truncation ran, not merely that the ledger's downstream cap saved it."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    long_body = "word " * 400                          # far past both the 160-char excerpt and 200-char ledger cap
    run = _runner({"50": [_comment("IC_1", "bob", long_body, "2026-08-01T00:00:00Z")]})

    comment_watch.tick(d, run=run)
    note = [e for e in ledger.read_all(d) if e["kind"] == "note"][0]
    assert len(note["why"]) <= 200                      # ledger.FREE_TEXT_CAP
    assert note["why"].startswith("new comment on #50 from bob:")
    assert note["why"].endswith("...")                  # the 160-char excerpt boundary fired, not the 200-cap


def test_multiple_claimed_issues_are_each_polled_independently(tmp_path):
    d = _sdlc(tmp_path)
    _claim(d, "50", actor=ACTOR)
    _claim(d, "60", actor="bo")
    run = _runner({
        "50": [_comment("IC_1", "bob", "on fifty", "2026-08-01T00:00:00Z")],
        "60": [_comment("IC_2", "amy", "on sixty", "2026-08-01T00:00:00Z")],
    })

    comment_watch.tick(d, run=run)
    notes = [e for e in ledger.read_all(d) if e["kind"] == "note"]
    assert len(notes) == 2
    by_goal = {n["goal"]: n for n in notes}
    assert by_goal["50"]["to"] == ACTOR and "on fifty" in by_goal["50"]["why"]
    assert "on sixty" not in by_goal["50"]["why"]
    assert by_goal["60"]["to"] == "bo" and "on sixty" in by_goal["60"]["why"]
    assert "on fifty" not in by_goal["60"]["why"]


def test_tick_fails_open_when_fetch_comments_raises_for_one_issue(tmp_path):
    """Mirrors sources.fetch_comments's own fail-open contract (any error -> []) and proves
    comment_watch.py degrades per-issue, not all-or-nothing across the whole tick -- same "one bad
    record doesn't blind the batch" convention as mirror.build_records/ledger.read_all."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    _claim(d, "60")
    run = _runner({"50": RuntimeError("gh: HTTP 502 Bad Gateway"),
                   "60": [_comment("IC_1", "bob", "still works", "2026-08-01T00:00:00Z")]})

    summary = comment_watch.tick(d, run=run)
    assert "#60" in summary and "#50" not in summary
    notes = [e for e in ledger.read_all(d) if e["kind"] == "note"]
    assert len(notes) == 1 and notes[0]["goal"] == "60"


# ------------------------------------------------------------------ R3: cursor eviction order


def test_cursor_eviction_retains_by_created_at_order_not_lexicographic_id_sort(tmp_path, monkeypatch):
    """Plan-review R3: the original design did `cursor[goal] = sorted(seen)[-limit:]`, sorting
    opaque GraphQL comment ids LEXICOGRAPHICALLY to decide what to forget -- wrong, since the ids
    are not sortable integers (sources.fetch_comments's own docstring). Demonstrated concretely:
    ids chosen so lexicographic order is the REVERSE of created_at order, with the cap lowered to 2.
    Lexicographic eviction would keep {"IC_B", "IC_C"} (alphabetically last) -- wrongly DROPPING
    "IC_A", the actual NEWEST comment, and wrongly KEEPING "IC_C", the OLDEST. The fix keeps the
    ids from the fetch's own (already created_at-sorted) order instead."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    monkeypatch.setattr(comment_watch.sources, "DEFAULT_COMMENT_LIMIT", 2)
    comments = [
        _comment("IC_C", "bob", "oldest", "2026-08-01T00:00:00Z"),
        _comment("IC_B", "bob", "middle", "2026-08-02T00:00:00Z"),
        _comment("IC_A", "bob", "newest", "2026-08-03T00:00:00Z"),
    ]
    run = _runner({"50": comments})

    comment_watch.tick(d, run=run)
    cursor = comment_watch._load_cursor(comment_watch.cursor_path(d))
    assert cursor.get("50") == ["IC_B", "IC_A"]          # the two chronologically-newest, oldest-first
    assert cursor.get("50") != sorted(["IC_A", "IC_B", "IC_C"])[-2:]   # not the lexicographic answer


def test_cursor_eviction_is_a_noop_when_the_fetch_is_already_within_the_cap(tmp_path):
    """In real operation the fetch and the cursor cap are always the SAME constant
    (sources.DEFAULT_COMMENT_LIMIT, since comment_watch.py never passes an explicit `limit=` to
    fetch_comments) -- fetch_comments itself already returns at most that many comments, so this
    slice is a no-op belt-and-braces in practice, not reachable dead logic in the sense of being
    wrong; this pins that down so a future change can't silently break the assumption."""
    d = _sdlc(tmp_path)
    _claim(d, "50")
    comments = [_comment(f"IC_{i}", "bob", str(i), f"2026-08-{i:02d}T00:00:00Z") for i in range(1, 6)]
    run = _runner({"50": comments})

    comment_watch.tick(d, run=run)
    cursor = comment_watch._load_cursor(comment_watch.cursor_path(d))
    assert cursor.get("50") == [c["id"] for c in comments]        # nothing evicted -- well within the cap


# ------------------------------------------------------------------ cursor


def test_cursor_round_trips_and_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "cursor.json"
    assert comment_watch._load_cursor(path) == {}
    comment_watch._save_cursor(path, {"50": ["IC_1"]})
    assert comment_watch._load_cursor(path) == {"50": ["IC_1"]}
    path.write_text("{not json")
    assert comment_watch._load_cursor(path) == {}


def test_cursor_survives_a_truthy_non_dict_top_level(tmp_path):
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps(["not", "a", "dict"]))
    assert comment_watch._load_cursor(path) == {}


# ------------------------------------------------------------------ CLI


def test_main_cli_runs_a_tick_and_prints_the_summary(tmp_path, capsys, monkeypatch):
    d = _sdlc(tmp_path)
    _claim(d, "50")
    monkeypatch.setattr(
        comment_watch.sources, "fetch_comments",
        lambda config, goal, run=None, limit=20: (
            [{"id": "IC_1", "author": "bob", "body": "hi", "created_at": "2026-08-01T00:00:00Z"}]
            if str(goal) == "50" else []))
    assert comment_watch.main(["comment_watch.py", str(d)]) == 0
    assert "1 new comment" in capsys.readouterr().out


def test_main_cli_is_quiet_when_comment_watch_disabled(tmp_path, capsys):
    d = _sdlc(tmp_path, {**ON, "comment_watch": {"enabled": False}})
    assert comment_watch.main(["comment_watch.py", str(d)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_cli_is_never_fatal_even_when_tick_raises(monkeypatch, capsys):
    """A watcher tick is never fatal (matches agent_watch.py's own main() contract)."""
    def boom(_sdlc_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(comment_watch, "tick", boom)
    assert comment_watch.main(["comment_watch.py", "/nonexistent"]) == 1
    assert "tick failed (non-fatal): boom" in capsys.readouterr().err
