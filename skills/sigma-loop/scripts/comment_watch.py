#!/usr/bin/env python3
"""One comment-watch tick: for every issue with a currently-open ledger claim, fetch its recent
comments and notify the claimant of anything new — reusing the existing `to`-addressed note + inbox
mechanism (ledger.append + watch_classify.classify), the same delivery #385 asks for, no new
channel.

Thin wiring, mirroring agent_watch.py's own shape: the only judgement here is "is this comment new"
(the per-issue cursor) and "is this comment worth notifying about" (comment author != claimant) —
everything else (who to notify, how it's delivered, exactly-once/dedup) is existing ledger/
watch_classify machinery. Off by default (`comment_watch.enabled`), and — like agent_watch.py —
only ever runs when the ledger is on and discovery is github (comments don't exist for local goal
files).

Two prerequisites, both already shipped, without which this feature cannot work at all:
- **#477** (`watch_classify.classify()`): a DELIBERATE self-addressed ledger note (`actor == me AND
  to == me`) used to be dropped by the own-write filter before it ever reached the signature check —
  exactly the shape this module writes in the normal solo/self-claimed deployment (actor=claimant,
  to=claimant, both resolved from THIS machine's own `ledger.actor`). Without #477, nothing this
  module writes would ever reach that claimant's own inbox.
- **This module's own `ref` fix to `watch_classify.signature()`** (see that function's docstring):
  without `ref` folded into the signature, every comment-notification for the SAME issue collides on
  an identical `kind:issue:state:priority` signature (kind="note", no state, a constant priority),
  and the signature set persists with no expiry — so the FIRST comment notification for an issue
  would permanently "use up" that signature and silently swallow every later, genuinely different
  comment on it, forever.
"""
import importlib.util
import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
mirror = _load("mirror")
sources = _load("sources")

CURSOR = "comment-watch-cursor.json"
_WHY_EXCERPT_CHARS = 160        # ledger.append's own why-field cap (200) does the final defensive cut


def cursor_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / CURSOR


def enabled(config):
    """Strict `is True`, mirroring agent_watch.enabled()'s own idiom — a truthy string or a stray 1
    must not silently switch a ledger-writing surface on."""
    return (config.get("comment_watch") or {}).get("enabled") is True


def _load_cursor(path):
    """{"<issue>": ["<comment-id>", ...]} — a bounded, per-issue LIST of already-processed comment
    ids (not a single "last_seen_comment_id": gh's comment id is an opaque GraphQL node id, not a
    sortable integer — verified live, see sources.fetch_comments's own docstring — so "seen set
    within the fetch window" is the correct, robust equivalent). Fail-open to {} on anything
    missing/corrupt, matching agent_watch.py's own cursor discipline."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cursor(path, cursor):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor, sort_keys=True), encoding="utf-8")


def _excerpt(body):
    body = " ".join(body.split())                    # flatten; ledger.append flattens again, cheap no-op
    return body if len(body) <= _WHY_EXCERPT_CHARS else body[:_WHY_EXCERPT_CHARS].rstrip() + "..."


def tick(sdlc_dir, config=None, run=None, now=None):
    """-> a one-line summary for watch_daemon.py's log, or "" when nothing needed anyone. One pass over
    every issue with an open ledger claim (ledger.open_claims — actor-level; WHICH of a claimant's
    own processes holds it, per #374, doesn't change who the note is addressed to, so the simpler,
    non-writer-detailed view is enough here), fetching comments and diffing each against this
    machine's own durable per-issue cursor.

    Multi-watcher race, explicitly scoped out (matches this codebase's existing single-watcher
    testing convention, e.g. test_agent_watch.py, and confirmed benign by the plan review): if two
    different teammates' own watch_daemon.py instances both poll the same open claim, both could
    independently see the same new comment as "new" on their own per-machine cursor and each write a
    note — bounded (cheap JSONL appends, not API calls) and harmless, since both notes carry the
    identical `ref=<comment id>` and watch_classify.classify()'s signature dedup collapses them to
    one surfaced item."""
    config = config if config is not None else ledger._config(sdlc_dir)
    if not enabled(config) or not mirror.is_github_mode(config):
        return ""
    ttl = ledger.lease_ttl_seconds(config)
    claims = ledger.open_claims(ledger.read_all(sdlc_dir), now=now, ttl_seconds=ttl)
    cursor = _load_cursor(cursor_path(sdlc_dir))
    fired = []
    dirty = False
    for goal, claimant in claims.items():
        # sources.fetch_comments is itself fail-open (any error -> []), so one issue's `gh` call
        # blowing up degrades only THAT issue's evidence this tick, never the whole batch — no
        # extra try/except needed here to get that property.
        comments = sources.fetch_comments(config, goal, run=run)
        seen = set(cursor.get(goal) or [])
        new = [c for c in comments if c["id"] and c["id"] not in seen]
        if not new:
            continue
        for c in new:
            # Explicit, always-correct self-suppression — not left to classify()'s downstream
            # actor==me filter alone. That filter only self-suppresses when THIS machine's own
            # ledger.actor() happens to equal the claimant (true in the common solo-watcher case,
            # post-#477), but a DIFFERENT teammate's watcher discovering the SAME self-comment would
            # write actor=<teammate> != claimant, which actor==me can't catch. Comparing the
            # comment's real GitHub author against the claimant directly is correct regardless of
            # which machine's tick discovers it — and is also just cheaper (skips the ledger write
            # entirely rather than writing-then-relying-on-a-downstream-filter).
            if c["author"].strip().lower() != str(claimant).strip().lower():
                # `ref=c["id"]`: ledger.append()'s ENTRIES branch now flattens/scrubs/caps `ref` to
                # BOUNDED_ID_CAP (#385, plan-review R2) precisely because this is the first ENTRIES
                # caller ever to source it from something outside our own control — no extra
                # validation is needed at this call site, only there, once, for every future caller
                # too. `ref` is also what gives each distinct comment its own
                # watch_classify.signature() (see that module) instead of colliding on the first.
                #
                # Deliberately NOT threading run=/now= through to the ledger write, matching
                # agent_watch.py's own _notify() exactly (agent_watch.py:124-125, no run=/now=
                # either) — append()'s `run` is only used to resolve actor() when config doesn't pin
                # `ledger.actor`, and reusing THIS tick's comment-fetch `run` fake there would make
                # every test's fake `run` also have to answer `["api", "user", ...]`-shaped calls it
                # has no reason to know about. Tests set `ledger.actor` explicitly instead.
                ledger.safe_append(
                    sdlc_dir, "note", goal, config=config, to=claimant,
                    issue=int(goal) if str(goal).isdigit() else None, priority="P2", ref=c["id"],
                    why=f"new comment on #{goal} from {c['author']}: {_excerpt(c['body'])}")
                fired.append((goal, c["id"]))
        # Unconditional advance: every comment seen this tick is cursored, notified or not (#385
        # requirement 4 — "at most once, ever"). Plan-review R3: retain by the fetch's OWN
        # created_at order, not by sorting the opaque id set lexicographically — `comments` is
        # already oldest-first (sources.fetch_comments's own contract) and already capped to
        # sources.DEFAULT_COMMENT_LIMIT, so this is simple slicing from the end, never a second sort
        # of ids that were never sortable to begin with (gh's comment id is an opaque GraphQL node
        # id). This REPLACES, not unions with, the previous cursor value for `goal`: an id that has
        # scrolled out of the fetch window can never reappear in a future fetch either (the window
        # only ever moves forward as new comments are posted), so it is correct, not merely
        # convenient, to stop remembering it.
        cursor[goal] = [c["id"] for c in comments][-sources.DEFAULT_COMMENT_LIMIT:]
        dirty = True
    if dirty:
        _save_cursor(cursor_path(sdlc_dir), cursor)
    return (f"{len(fired)} new comment(s) notified — "
            + ", ".join(f"#{g}" for g, _ in fired)) if fired else ""


USAGE = "usage: comment_watch.py [sdlc_dir]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"comment_watch: tick failed (non-fatal): {exc}", file=sys.stderr)
        return 1
    return 0


def _symlink_guard(argv):
    """#708: refuse a committed symlink under .sdlc/state or .sdlc/journey before any write."""
    import importlib.util as _u
    import pathlib as _p
    spec = _u.spec_from_file_location("_guard_state", _p.Path(__file__).resolve().parent / "state.py")
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.guard_argv(argv, _p.Path(__file__).name)


if __name__ == "__main__":
    sys.exit(_symlink_guard(sys.argv) or main(sys.argv))
