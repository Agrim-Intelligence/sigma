#!/usr/bin/env python3
"""#2311 (slice 1 of Epic #2310, `.sdlc/design/2289.md` `### 6`): one Slack-posting primitive.

ONE PRIMITIVE, TWO CALLERS -- BY DESIGN. `drift_watch.py`'s passive tick calls `post_message`
directly (slice 1, `#2311`). Slice 2 (`#2312`, this one) adds `slack_client.py`'s own `main(argv)`
-- a direct "push a message to Slack" CLI entry point -- reusing this exact function unchanged, so
there is exactly one Slack-posting code path in this design, never two independently-maintained
ones. See `main`'s own docstring below for the CLI shape.

Zero deps beyond the stdlib -- `urllib.request` for the POST, matching `channel_notify.py`'s own
zero-dependency posture (BR-14) and this kit's established discipline for every outbound call.

THE TOKEN NEVER LIVES IN CONFIG. `drift_watch.slack_bot_token_env` NAMES an environment variable
this file only READS (default `SIGMA_SLACK_BOT_TOKEN`) -- `agent_watch.py`'s own `pass_env`
convention (BR-15), because `config.json` is git-committed and a literal token there would ship to
every clone. An empty/unset token is not an error: it degrades to a `[stub]` log line and a no-op,
the same safe-by-default behaviour `agent_watch.py`'s own missing-credential path already holds,
and the exact thing AGENTS.md's SAFETY bar asks for ("nothing sends data... without the operator
opting in").

TEXT IS SCRUBBED BEFORE IT EVER LEAVES THE MACHINE (BR-15). `scrub.scrub` runs over the message
body before the POST is built -- the same defense-in-depth this kit already applies anywhere text
crosses a trust boundary (kit-finding sharing, `ledger.py`'s own free-text sanitizing)."""
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


scrub = _load("scrub")
ledger = _load("ledger")
legacy = _load("legacy")    # #239: the token variable and the channel key under the previous name

DEFAULT_TOKEN_ENV = "SIGMA_SLACK_BOT_TOKEN"
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
TIMEOUT_SECONDS = 10
STUB_PREVIEW_CHARS = 80
#: `main`'s own default sdlc_dir when none is given -- matches every sibling watch-tick script's
#: `argv[1] if len(argv) > 1 else ".sdlc"` idiom, except `post`'s own argv shape (design `### 6`:
#: `slack_client.py post <channel> <text>`) leaves no positional slot for it.
DEFAULT_SDLC_DIR = ".sdlc"
#: The two config-named channel aliases `main`'s `post` verb accepts, resolved against
#: `config["drift_watch"]["channels"]` -- the SAME two keys `drift_watch._channel_id` itself reads
#: (D-5 / design `### 5`), never a third, independently-maintained list.
CHANNEL_ALIASES = ("sigma", "org")
#: A loose but real shape check for a literal Slack channel/group/DM id typed directly on the CLI
#: (e.g. "C0123456"): a leading uppercase letter plus 7-10 more uppercase alphanumerics. Not a live
#: Slack API validity check -- this file never queries Slack for that -- just enough to refuse an
#: obviously-wrong string (a typo'd alias, a repo name) loudly here rather than silently POSTing to
#: it and only surfacing Slack's own "channel_not_found" several network hops later.
_CHANNEL_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{7,10}$")


def _token_env(config):
    value = ((config or {}).get("drift_watch") or {}).get("slack_bot_token_env")
    return value if isinstance(value, str) and value.strip() else DEFAULT_TOKEN_ENV


def _real_post(token, channel_id, text):
    """The real network call. Slack's own `ok` field, not the HTTP status, is the truth: a
    misconfigured token or an unknown channel id both come back as a 200 with `"ok": false` and an
    `"error"` string, so checking status alone would silently report success on either."""
    payload = json.dumps({"channel": channel_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        SLACK_POST_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": "Bearer %s" % token})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:   # noqa: S310 - a fixed,
        body = json.loads(resp.read().decode("utf-8"))                   # documented Slack endpoint
        return bool(body.get("ok"))


def post_message(channel_id, text, config, post=None, token_env=None):
    """POST `text` to Slack channel `channel_id`. Returns True iff Slack itself confirmed the send.

    NEVER RAISES -- a broken or unreachable Slack endpoint must not crash a watcher tick (or, once
    slice 2 exists, a direct CLI push), the same fail-open contract `channel_notify.py`'s own
    `_post` already holds (BR-14). `post` is DI for tests: `post(token, channel_id, text) -> bool`;
    never make a real network call from the automated suite.

    `token_env` (#2336, design `.sdlc/design/2329.md` Component E, BR-24): OPTIONAL, additive
    override -- when given, the NAMED env var is read verbatim instead of `_token_env(config)`'s
    own `config["drift_watch"]["slack_bot_token_env"]` lookup. This is what lets a second,
    independent caller (the inbound Slack-commands listener, its own dedicated app/bot per decision
    #4) reuse this one posting primitive for its replies without either collapsing onto the drift
    watcher's own token or forking a second, independently-maintained posting function. Omitted
    (the default), every existing call site -- `drift_watch.py`'s own tick -- is byte-for-byte
    unaffected: `_token_env(config)` is consulted exactly as before."""
    if not isinstance(channel_id, str) or not channel_id.strip():
        print("slack_client: no channel_id given — nothing to post", file=sys.stderr)
        return False
    text = scrub.scrub(text) if text else (text or "")
    env_name = token_env if (isinstance(token_env, str) and token_env.strip()) else _token_env(config)
    token = legacy.getenv(env_name, "")
    if not token:
        preview = text[:STUB_PREVIEW_CHARS]
        ellipsis = "..." if len(text) > STUB_PREVIEW_CHARS else ""
        print("slack_client: [stub] slack post to %s: %s%s" % (channel_id, preview, ellipsis),
              file=sys.stderr)
        return False
    send = post or _real_post
    try:
        return bool(send(token, channel_id, text))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        print("slack_client: POST failed (non-fatal): %s" % exc, file=sys.stderr)
        return False


# --------------------------------------------------------------------------- CLI (slice 2, #2312)


def _resolve_channel(config, channel):
    """`channel` is either one of the two config-named aliases (`CHANNEL_ALIASES`, resolved against
    `config["drift_watch"]["channels"]` -- the same schema `drift_watch._channel_id` reads) or a
    literal Slack channel id typed directly on the CLI. -> `(channel_id, None)` on success,
    `(None, <why>)` on a resolution failure.

    THE ONE THING `main` REFUSES LOUDLY (design `### 6`, S-4) rather than degrading -- unlike an
    unset TOKEN, which still safely stubs through `post_message`'s own existing no-op path (never
    crashes, never sends). AGENTS.md's SAFETY bar ("nothing sends data... without the operator
    opting in") is about SENDING; it says nothing about silently accepting a plainly wrong
    destination, and a bad channel argument is a caller mistake worth surfacing immediately rather
    than a network call worth swallowing."""
    if channel in CHANNEL_ALIASES:
        channels = ((config or {}).get("drift_watch") or {}).get("channels") or {}
        value = legacy.channel_value(channels, channel)
        if isinstance(value, str) and value.strip():
            return value.strip(), None
        return None, ('drift_watch.channels.%s is not set in config.json -- fill it in, or pass a '
                       'literal channel id instead' % channel)
    if isinstance(channel, str) and _CHANNEL_ID_RE.match(channel):
        return channel, None
    return None, ('%r is neither "sigma"/"org" nor a channel-id-shaped string '
                   '(e.g. "C0123456")' % (channel,))


USAGE = "usage: slack_client.py post <sigma|org|channel-id> <text...>"


def main(argv):
    """`python3 slack_client.py post <sigma|org|a literal channel id> <text...>` -- the direct
    "push a message to Slack from Claude Code" entry point (slice 2 of Epic #2310, design `### 6`,
    S-4). `text` is every remaining argv joined with a single space, so an ordinary unquoted
    multi-word message works without the caller having to remember to quote it (a single quoted
    arg still works too -- `" ".join` on a length-1 remainder is that string, unchanged).

    Calls `post_message` UNCHANGED -- the SAME function `drift_watch.py`'s passive tick already
    calls (module docstring) -- so there is exactly one Slack-posting code path in this design,
    never two independently-maintained ones. `config.json` is read via `ledger._config`, the same
    helper every sibling watch-tick script in this file's own family already uses
    (`reconcile_tick.main`, `drift_watch.main`); an unreadable/missing config degrades to `{}`
    rather than refusing outright -- a literal channel id needs no config at all, and the alias
    path still fails with the correct, specific "not configured" message off an empty dict, so
    nothing is lost by degrading here instead of hard-erroring.

    A bad CHANNEL is the one thing refused loudly (see `_resolve_channel`); an unset/empty Slack
    token is not -- it still degrades to `post_message`'s own `[stub]` no-op, exactly as it does
    for the passive tick, and this entry point returns 0 either way: a safe stub is a successfully
    completed, non-fatal command, not a CLI failure."""
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 4 or argv[1] != "post":
        print(USAGE, file=sys.stderr)
        return 2
    channel_arg, text = argv[2], " ".join(argv[3:])
    try:
        config = ledger._config(DEFAULT_SDLC_DIR)
    except Exception as exc:                    # noqa: BLE001 - see docstring: degrade, don't refuse
        print("slack_client: could not read %s/config.json (%s) -- channel aliases "
              "unavailable; a literal channel id still works" % (DEFAULT_SDLC_DIR, exc),
              file=sys.stderr)
        config = {}
    channel_id, error = _resolve_channel(config, channel_arg)
    if error is not None:
        print("slack_client: %s" % error, file=sys.stderr)
        return 1
    post_message(channel_id, text, config)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
