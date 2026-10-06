#!/usr/bin/env python3
"""One watcher tick: read the ledger, work out what needs me, write the inbox.

Thin on purpose — every judgement lives in the pure `watch_classify`, and every git call in `sync`.
This is the wiring between them plus the two files that carry state across ticks: the inbox the loop
reads, and the cursor that stops the same mention firing twice. Zero deps.
"""
import importlib.util
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
classify = _load("watch_classify")
state = _load("state")

INBOX = "inbox.md"
CURSOR = "watch-cursor.json"


def inbox_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / INBOX


def cursor_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / CURSOR


def tick(sdlc_dir, config=None, me=None):
    """-> a one-line summary for the log, or "" when nothing needed anyone.

    New items are APPENDED to whatever is already in the inbox: a tick must never drop an item the
    loop has not read yet, and the loop is what clears it."""
    config = config if config is not None else ledger._config(sdlc_dir)
    if not ledger.enabled(config):
        return ""
    me = me or ledger.actor(config)
    cursor = classify.load_cursor(cursor_path(sdlc_dir))
    # ponytail: entries only for now. read_all()/classify() are cursor-aware for `events` too
    # (#137), but no call site writes real events yet (#139/#140) and EVENT_FIELDS has no `to`
    # for any kind, so reading events here would double per-tick I/O for a consumer that
    # provably surfaces nothing. Wire the events loop in when the first real emitter lands.
    # #1574: `ledger.address_key` is handed IN, because `watch_classify` stays import-free (see its
    # `classify` docstring). Without it this comparison is exact, and a note addressed `@Here-Owner`
    # -- the shape `docs/branching-model.md` ships as its own canonical registry example -- never
    # wakes the person whose login is `Here-Owner`. Written, correct, and undeliverable.
    items, cursor = classify.classify(ledger.read_all(sdlc_dir), cursor, me,
                                      address=ledger.address_key)
    classify.save_cursor(cursor_path(sdlc_dir), cursor)
    if not items:
        return ""
    path = inbox_path(sdlc_dir)
    state.refuse_symlinks(sdlc_dir, path, create_parents=True)         # #708
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rendered = classify.render_inbox(items, me)
    path.write_text((existing + "\n" + rendered) if existing.strip() else rendered, encoding="utf-8")
    return classify.summarise(items)


def read_inbox(sdlc_dir):
    path = inbox_path(sdlc_dir)
    try:
        state.refuse_symlinks(sdlc_dir, path)                          # #708: no leak through a link
        return path.read_text(encoding="utf-8").strip()
    except state.UnsafeStatePath as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return ""
    except OSError:
        return ""


def clear_inbox(sdlc_dir):
    """Called once the loop has surfaced the items — the inbox is a hand-off point, not a log; the
    ledger is the durable record."""
    path = inbox_path(sdlc_dir)
    try:
        state.refuse_symlinks(sdlc_dir, path)                          # #708
    except state.UnsafeStatePath as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return
    if path.exists():
        path.write_text("", encoding="utf-8")


USAGE = "usage: watch.py [sdlc_dir] [show]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    if len(argv) > 2 and argv[2] == "show":
        text = read_inbox(sdlc_dir)
        print(text or "inbox empty")
        return 0
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"watch: tick failed (non-fatal): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
