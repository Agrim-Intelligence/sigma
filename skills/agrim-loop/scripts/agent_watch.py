#!/usr/bin/env python3
"""One agent-watch tick: find goals with an open ledger claim whose registered driving pid has
genuinely died, and notify — an email if `agent_watch.notify.email` is fully configured and the
send succeeds, ELSE (not configured, incomplete, or the send itself fails) a ledger note addressed
to the claimant, reusing the existing `to`-addressed hand-off/inbox mechanism. Never both, never
neither — see the plan (sigma-core-changes-plan.md section 6) for the full design.

Thin wiring, mirroring watch.py's own shape: every judgement this composes (marker liveness,
claim ownership) already lives in loop.py/ledger.py, not reinvented here. Off by default
(`agent_watch.enabled`), and — like `watch_daemon.py` itself — only ever runs when the ledger is on, so
every call site here is inert on a repo that hasn't opted in. Zero deps: stdlib `smtplib`/
`email.message` only, for the (also off-by-default) email path.
"""
import importlib.util
import json
import os
import pathlib
import smtplib
import socket
import sys
from email.message import EmailMessage

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")
legacy = _load("legacy")   # #239: `pass_env` may name the previous prefix
loop = _load("loop")

CURSOR = "agent-watch-cursor.json"


def cursor_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / CURSOR


def enabled(config):
    """Strict `is True`, mirroring `ledger.enabled()`'s own idiom — a truthy string or a stray 1
    must not silently switch a safety feature on."""
    return (config.get("agent_watch") or {}).get("enabled") is True


def _load_cursor(path):
    """{"notified": [signature, ...]} — fail-open to empty on anything missing or corrupt,
    matching `watch_classify.load_cursor`'s own discipline for exactly this shape of local
    per-machine state file. Unlike that cursor, this one is a flat set of already-notified
    signatures (see `tick()`), not a per-actor high-water seq — there is no cost reason to
    suppress re-observation of an ALIVE goal, only of an already-notified DEAD one."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        notified = data.get("notified") if isinstance(data, dict) else None
        return {"notified": list(notified) if isinstance(notified, list) else []}
    except (OSError, ValueError):
        return {"notified": []}


def _save_cursor(path, cursor):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursor, sort_keys=True), encoding="utf-8")


def _send_email(email_cfg, subject, body, run_email=None):
    """Best-effort SMTP send. NEVER raises — a broken mail server must not crash the watcher
    tick, the same fail-open contract every other watch_daemon.py step already has. Returns True iff
    handed off successfully. `run_email` is DI for tests; default None uses the real smtplib
    transport."""
    send = run_email or _real_send_email
    try:
        return bool(send(email_cfg, subject, body))
    except Exception as exc:                        # noqa: BLE001 - smtplib's exception surface is
        print(f"agent_watch: smtp send failed (non-fatal): {exc}", file=sys.stderr)  # wide; the
        return False                                 # fail-open CONTRACT matters more than which one


def _real_send_email(email_cfg, subject, body):
    """The credential is NEVER read from config — only from the environment variable NAMED by
    `pass_env` (default SIGMA_SMTP_PASS). There is structurally no code path that accepts a
    literal password value out of `config.json`, so a future `notify.email.pass` key, even if
    someone adds one, is simply never read here."""
    host, port = email_cfg["host"], int(email_cfg.get("port") or 587)
    timeout = float(email_cfg.get("timeout_seconds") or 10)
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, email_cfg["to"]
    msg["From"] = email_cfg.get("from") or email_cfg.get("user") or f"sigma@{socket.gethostname()}"
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=timeout) as server:   # timeout covers connect AND every
        if email_cfg.get("tls", True):                          # subsequent blocking op on the
            server.starttls()                                   # connection (smtplib's own contract)
        user = email_cfg.get("user")
        if user:
            password = legacy.getenv(email_cfg.get("pass_env") or "SIGMA_SMTP_PASS", "")
            if not password:
                raise RuntimeError(
                    "user is set but env var "
                    f"{email_cfg.get('pass_env') or 'SIGMA_SMTP_PASS'} is empty/unset")
            server.login(user, password)
        server.send_message(msg)
    return True


def _notify(sdlc_dir, config, goal, thread, pid, actor, run_email=None):
    """Email if `notify.email.enabled` and fully configured and the send succeeds; ELSE (off,
    incomplete, or the send itself failed) the ledger fallback — never both, never neither, per
    the plan's "fail loud AND always fall back, never silently skip" decision. `to=actor` (the
    bare claim holder, not the dead writer id) so it correctly surfaces via watch_classify's
    existing `to == me` filter to ANY of that actor's later sessions."""
    email_cfg = ((config.get("agent_watch") or {}).get("notify") or {}).get("email") or {}
    subject = f"sigma: background agent died (goal={goal}, thread={thread}, pid={pid})"
    body = (f"A background agent driving goal '{goal}' (thread={thread}) appears to have died "
            f"(pid {pid} is no longer alive). Reclaim or re-open the goal.")
    if email_cfg.get("enabled") is True:
        if not email_cfg.get("host") or not email_cfg.get("to"):
            print("agent_watch: notify.email is enabled but host/to is missing — "
                  "falling back to ledger note", file=sys.stderr)
        elif _send_email(email_cfg, subject, body, run_email=run_email):
            return
    # #476: `ref` (ledger.OPTIONAL_FIELDS) is folded into watch_classify.signature() as its fourth,
    # additive component — without it every dead-agent note for this SAME goal collides on an
    # identical `kind:goal:state:priority` signature, so the second, later, genuinely distinct
    # dead-agent event on this goal would be silently dropped by classify()'s dedup, forever (the
    # signature set has no expiry). `f"{goal}:{thread}:{pid}"` reuses the exact composite `tick()`
    # already builds locally as its own `sig` (see below) rather than a bare `ref=pid` like
    # comment_watch.py's sibling call: a GitHub comment id is globally unique so a bare ref is safe
    # there, but an OS pid recycles over a machine's lifetime, and `thread` is a real independent
    # dimension (loop.agent_threads) `signature()` has no other way to distinguish — the composite
    # closes both gaps for free.
    ledger.safe_append(sdlc_dir, "note", goal, config=config, to=actor, priority="P1",
                        ref=f"{goal}:{thread}:{pid}",
                        why=f"background agent died (thread={thread}, pid={pid}) — reclaim or re-open")


def tick(sdlc_dir, config=None, run_email=None, now=None):
    """-> a one-line summary for watch_daemon.py's log, or "" when nothing needed anyone. Mirrors
    watch.py's tick() shape exactly: a no-op unless `agent_watch.enabled`, one pass over every
    goal with an open ledger claim, checking each of its registered (goal, thread) markers for a
    definitively dead pid, notifying and advancing the exactly-once cursor only for what actually
    fired this tick."""
    config = config if config is not None else ledger._config(sdlc_dir)
    if not enabled(config):            # agent_watch.enabled is not True -> no-op, matches watch.py
        return ""
    ttl = ledger.lease_ttl_seconds(config)
    claims = ledger.open_claims_detailed(ledger.read_all(sdlc_dir), now=now, ttl_seconds=ttl)
    cursor = _load_cursor(cursor_path(sdlc_dir))
    notified = set(cursor.get("notified") or [])
    fired = []
    for goal, (actor, _writer) in claims.items():
        for thread in loop.agent_threads(sdlc_dir, goal):
            state, pid = loop.agent_alive(sdlc_dir, goal, config, thread=thread)
            if state != "dead":
                continue
            sig = f"{goal}:{thread}:{pid}"
            if sig in notified:
                continue
            _notify(sdlc_dir, config, goal, thread, pid, actor, run_email=run_email)
            notified.add(sig)
            fired.append((goal, thread))
    if fired:
        _save_cursor(cursor_path(sdlc_dir), {"notified": sorted(notified)})
    return (f"{len(fired)} agent(s) confirmed dead — "
            + ", ".join(f"{g}[{t}]" for g, t in fired)) if fired else ""


USAGE = "usage: agent_watch.py [sdlc_dir]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc_dir = argv[1] if len(argv) > 1 else ".sdlc"
    try:
        print(tick(sdlc_dir))
    except Exception as exc:                    # noqa: BLE001 - a watcher tick is never fatal
        print(f"agent_watch: tick failed (non-fatal): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
