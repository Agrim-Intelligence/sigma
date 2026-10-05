#!/usr/bin/env python3
"""research-radar (Phase A, dry-run): the deterministic bookkeeping for a proactive research scout —
a rotation cursor over the backlog (agenda) and a ledger so the radar never re-surfaces the same
finding. The research + ranking themselves are agent-driven (the /sigma-radar skill); this is the
repeat-prevention + the agenda math. Zero-dep."""
import sys, pathlib


def agenda(n, k, cursor=0):
    """Rotate research across the backlog: pick k item-indices from a backlog of n, starting at
    cursor and wrapping, so consecutive runs cover different items. Returns {indices, next_cursor}."""
    if n <= 0 or k <= 0:
        return {"indices": [], "next_cursor": 0}
    k = min(k, n)
    return {"indices": [(cursor + i) % n for i in range(k)], "next_cursor": (cursor + k) % n}


def _ledger(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "knowledge" / "radar" / "ledger.md"


def seen(sdlc_dir):
    """Finding-keys the radar has already surfaced (one per line). [] if none."""
    f = _ledger(sdlc_dir)
    if not f.exists():
        return []
    return [ln[2:].strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


def record(sdlc_dir, key):
    """Remember a surfaced finding so the radar won't repeat it. Whitespace-normalized exact-dedup.
    Returns True if newly recorded, False if empty or already surfaced.
    ponytail: exact-match dedup; semantic near-dup only if the ledger gets noisy."""
    key = " ".join(key.split())
    if not key or key in seen(sdlc_dir):
        return False
    f = _ledger(sdlc_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    head = "" if f.exists() else "# Research-radar ledger - findings already surfaced (do not repeat)\n\n"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(head + f"- {key}\n")
    return True


USAGE = 'usage: radar.py agenda <n> <k> [cursor] | seen [sdlc_dir] | record "<key>" [sdlc_dir]'


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 4 and argv[1] == "agenda":
        a = agenda(int(argv[2]), int(argv[3]), int(argv[4]) if len(argv) > 4 else 0)
        print(f"indices: {' '.join(map(str, a['indices']))} | next_cursor: {a['next_cursor']}")
        return 0
    if len(argv) >= 2 and argv[1] == "seen":
        s = seen(argv[2] if len(argv) > 2 else ".sdlc")
        print("\n".join(f"- {x}" for x in s) if s else "radar: ledger empty.")
        return 0
    if len(argv) >= 3 and argv[1] == "record":
        gdir = argv[3] if len(argv) > 3 else ".sdlc"
        print(f"radar: recorded - {argv[2]}" if record(gdir, argv[2]) else f"radar: already surfaced - {argv[2]}")
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
