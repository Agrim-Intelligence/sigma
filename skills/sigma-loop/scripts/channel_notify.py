#!/usr/bin/env python3
"""One channel-notify tick (#1322): the CLI/Channels adapter's Python half. On detecting the SAME
candidate `autowatch.py` itself would pick up on its next tick (the oldest unactioned mention/
assignment/blocker addressed to `me`, matching `ledger.autowatch.scope`), POST a small JSON payload
to `ledger.autowatch.channel_webhook_url`. That URL is limited to a local sigma-autowatch
listener unless the operator sets the exact remote-delivery opt-in. The channel server
(`skills/sigma-loop/channels/sigma-autowatch/webhook.ts`, run inside an already-
open `claude --dangerously-load-development-channels server:sigma-autowatch` session — see that
plugin's own SKILL.md for the one-time setup). The channel server forwards the payload into the
session as a `<channel>` event; the session's own instructions (declared by the channel server, not
here) tell it to run `autowatch.py tick .sdlc --issue N` in response — autowatch.py itself is the
sole decision point for whether anything actually drives `/sigma-loop`. This script makes NO safety
decisions of its own; it only decides WHETHER and WHEN to push a wake-up nudge.

Thin wiring, mirroring agent_watch.py's own shape exactly: reuses autowatch.py's own candidate
discovery (`_find_candidate`, `_autowatch_settings`) rather than reimplementing it, so a candidate
this script notifies about is always the identical one `autowatch.py tick` (no `--issue`) would
itself pick up next — never a second, independent selection that could disagree.

NOTIFY-ONCE-PER-ID, not once-per-tick. Without a cursor, an unresolved candidate would re-notify on
every single watch_daemon.py tick (every `ledger.watch.interval_seconds`) for as long as it stays open,
which could keep re-triggering a real, costly `claude -p` drive on the receiving end if its own
preconditions happen to keep passing — turning a bounded retry (autowatch's own `hop_limit`) into an
UNBOUNDED notification storm at this layer instead. A flat, exactly-once cursor (mirrors
agent_watch.py's own `_load_cursor`/`_save_cursor`) means each candidate wakes the channel session
once; if that push is missed, the next watch_daemon.py tick that finds the SAME candidate re-notifies only
if something has genuinely changed since the last push — either autowatch's own recorded hop
advanced (`autowatch._prior_autowatch_hop`) OR autowatch recorded a NEW outcome note at all
(`_prior_autowatch_attempts`, #1337). Hop alone is not enough: autowatch.py deliberately does NOT
advance the recorded hop on a retriable-but-unattempted outcome (a precondition block, a hop_limit
refusal, a drive exception, a nonzero exit — see autowatch.py's own `_tick_inner`), so gating only
on hop made this adapter go permanently silent for a candidate after its very first transient
block. Tracking attempts too closes that gap while still refusing to notify twice for the exact
same, unchanged state — that would be pure noise.

Zero deps beyond the stdlib: `urllib.request` for the POST, matching this kit's established
zero-dependency posture for every other watch-tick script (agent_watch.py's stdlib smtplib)."""
import importlib.util
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
autowatch = _load("autowatch")

CURSOR = "channel-notify-cursor.json"


def cursor_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / CURSOR


def enabled(config):
    """Strict `is True` on `ledger.enabled`/`ledger.autowatch.enabled`, mirroring every other
    watch-tick script's own idiom — a truthy string or stray 1 must never silently switch this on.
    Also requires a real `channel_webhook_url`: absent/null means no adapter is configured to
    receive the push, matching `autowatch.adapter_wired`'s own reading of the same key."""
    if not ledger.enabled(config):
        return False
    if not autowatch.enabled(config):
        return False
    settings = autowatch._autowatch_settings(config)
    return bool(settings.get("channel_webhook_url"))


def _load_cursor(path):
    """{"notified": {id: {"hop": int, "attempts": int}, ...}} -- fail-open to empty on anything
    missing or corrupt, matching every other cursor file in this kit's own discipline for exactly
    this shape of local, per-machine state. Tolerates a pre-#1337 cursor (the old hop-only shape,
    a bare int per id) by reading it as {"hop": <that int>, "attempts": 0} -- worst case one extra
    re-push right after upgrading, never a crash on an old on-disk file."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        raw = data.get("notified") if isinstance(data, dict) else None
        notified = {}
        if isinstance(raw, dict):
            for cid, value in raw.items():
                if isinstance(value, dict):
                    notified[cid] = {"hop": int(value.get("hop") or 0),
                                      "attempts": int(value.get("attempts") or 0)}
                elif isinstance(value, (int, float)):
                    notified[cid] = {"hop": int(value), "attempts": 0}
        return {"notified": notified}
    except (OSError, ValueError, TypeError):
        return {"notified": {}}


def _save_cursor(path, cursor):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor, sort_keys=True), encoding="utf-8")


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}   # one set for the local-only check and the proxy bypass


def _real_post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                  headers={"Content-Type": "application/json"})
    # #627: a loopback destination never goes through a proxy named by http_proxy/HTTPS_PROXY (the
    # environment would otherwise carry the full local URL to it). A remote host, reachable only
    # by the explicit allow_remote_webhook opt-in, keeps the operator's own egress proxy.
    if urllib.parse.urlparse(url).hostname in _LOOPBACK_HOSTS:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=10) as resp:      # noqa: S310 - config-set URL, local unless opted in
        return 200 <= resp.status < 300


def _post(url, payload, run_post=None):
    """Best-effort. NEVER raises -- a channel server that's down or unreachable must not break the
    watcher tick, the same fail-open contract every other watch_daemon.py step already has."""
    send = run_post or _real_post
    try:
        return bool(send(url, payload))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        print(f"channel_notify: POST failed (non-fatal): {exc}", file=sys.stderr)
        return False


def _local_webhook_url(url, settings):
    """Whether this configured destination is safe to call without a separate opt-in.

    `channel_notify` is a local wake-up adapter, not a general outbound webhook facility. Keep the
    permissive path intentionally exact: only the JSON boolean `true` grants remote delivery, so
    a truthy typo cannot silently exfiltrate a ledger candidate to a network host.
    """
    try:
        parsed = urllib.parse.urlparse(str(url))
        if parsed.scheme not in {"http", "https"}:
            return False
        if settings.get("allow_remote_webhook") is True:
            return True
        return parsed.hostname in _LOOPBACK_HOSTS
    except (TypeError, ValueError):
        return False


def _prior_autowatch_attempts(entries, me, ref):
    """#1337: count of autowatch's own outcome notes recorded against this candidate's ledger id
    (`ref`) -- unlike `autowatch._prior_autowatch_hop`, this increases on EVERY outcome autowatch
    records for the candidate, INCLUDING a precondition block, a hop_limit refusal, a drive
    exception, or a nonzero exit. `_tick_inner` deliberately records those retriable-but-
    unattempted outcomes at `hop=incoming_hop` (unchanged), never `next_hop` -- see autowatch.py's
    own module docstring -- so `hop` alone cannot tell "nothing has happened since the last push"
    apart from "autowatch tried once and was blocked". Without this second signal, `tick()` below
    would gate re-push purely on hop advancing and go permanently silent for a candidate after its
    very first transient block, even though the candidate stays legitimately open and autowatch's
    own design intends it to be retried once conditions clear."""
    if not ref:
        return 0
    return sum(1 for e in entries
               if e.get("kind") == "note" and e.get("actor") == me and e.get("ref") == ref)


def tick(sdlc_dir, config=None, run_post=None, now=None):
    """-> a one-line summary for watch_daemon.py's log, or "" when nothing needed a push. `now`/`run_post`
    are DI for tests, matching every sibling watch-tick script's convention."""
    config = config if config is not None else ledger._config(sdlc_dir)
    if not enabled(config):
        return ""
    entries = ledger.read_all(sdlc_dir)
    me = ledger.actor(config)
    settings = autowatch._autowatch_settings(config)
    candidate = autowatch._find_candidate(entries, me, settings)
    if candidate is None:
        return ""
    cid = candidate.get("id")
    if not cid:
        return ""
    hop = max(int(candidate.get("autowatch_hop") or 0),
              autowatch._prior_autowatch_hop(entries, me, cid))
    attempts = _prior_autowatch_attempts(entries, me, cid)
    cursor = _load_cursor(cursor_path(sdlc_dir))
    notified = cursor["notified"]
    prior = notified.get(cid)
    # #1337: re-push unless BOTH signals are unchanged since the last push -- hop alone missed
    # every retriable-but-unattempted autowatch outcome (a precondition block, a hop_limit
    # refusal, a drive exception, a nonzero exit), all of which leave `hop` at its prior value by
    # design (see `_prior_autowatch_attempts`'s own docstring). `attempts` catches those; `hop` is
    # kept too so a genuine hop advance still counts even in the (currently impossible, but not
    # worth coupling to) case attempts somehow did not move with it.
    if prior is not None and prior["hop"] >= hop and prior["attempts"] >= attempts:
        return ""                        # nothing has changed since the last push -- avoid a storm
    target = str(candidate.get("issue") or candidate.get("goal"))
    url = settings.get("channel_webhook_url")
    if not _local_webhook_url(url, settings):
        print("channel_notify: refusing non-local or malformed webhook URL (set "
              "ledger.autowatch.allow_remote_webhook: true to opt in)", file=sys.stderr)
        return ""
    payload = {"issue": target, "kind": candidate.get("kind"), "goal": str(candidate.get("goal")),
               "id": cid}
    if not _post(url, payload, run_post=run_post):
        return "push failed (non-fatal) — will retry next tick"
    notified[cid] = {"hop": hop, "attempts": attempts}
    _save_cursor(cursor_path(sdlc_dir), cursor)
    return f"pushed channel notification for {target} ({candidate.get('kind')})"


USAGE = "usage: channel_notify.py [sdlc_dir]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"channel_notify: tick failed (non-fatal): {exc}", file=sys.stderr)
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
