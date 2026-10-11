# Zero-touch routines

Running Sigma safely from a scheduler. Back to the [README](../README.md).

## Zero-touch routines (optional, off by default)

Scheduling isn't something Sigma drives itself — that's the host's own recurring-trigger feature
(Claude Desktop's "Routines," a cron job calling `claude -p`, whatever fires an agent unattended on a
timer). What Sigma needs to be is **safe under repeated, possibly-overlapping invocation**, so a
routine can be configured once and left running without double-launching a redundant session or ever
substituting ambient conversational memory for a real backlog pick.

**The session registry** (`loop.py session-active` / `session-end`, `start --session-pid`) is what makes the first half cheap. `loop.py
start` records the CALLER's own long-lived process id under its own registry entry — **not** any
individual `loop.py` call's own: each `loop.py` invocation is a short-lived subprocess that exits
within moments of returning, so recording ITS pid would make the marker read as dead the instant it's
written. `start`/`next`/`next-batch`/`session-end` all fall back to `os.getppid()` — THIS invocation's
own immediate parent — when `--session-pid` isn't given, but that
fallback is documented for what it actually is: correct for a genuinely continuous caller (one
live process across the whole session), and confirmed **incorrect** for a routine/agent that issues
each `loop.py` call as its own separate command — each such call's immediate parent is a fresh
per-call shell process, not any long-lived ancestor, so the fallback alone cannot be trusted to tell
this routine's own earlier picks apart from a stale, already-exited one. **Always pass
`--session-pid "$PPID"` explicitly** — that's your OWN invoking shell's `$PPID`, read fresh on every
call, which testably IS the same stable value across your routine's separate commands (unlike
`os.getppid()` read from inside a spawned `loop.py` process, which is not). A routine firing checks
`session-active` first: `ACTIVE` means at least one PID is live and its lease is fresh; the firing
exits immediately. `FREE` means every entry is absent, has a dead PID, or has an expired lease.
Multiple concurrent sessions register independently — one session ending never erases another's
entry, and `loop.py next`/`next-batch` skip goals another live session claimed. Each also keeps its
own run: its budgets (above), and the verify-evidence freshness of the goals it holds, are anchored
to its own `start`, so one session's `start` never voids another's passing verify. A goal no live
session holds (one armed by `/sigma-goal`, or awaiting merge after `record review`) is anchored to
the checkout's latest `start`, as before. In Codex Desktop,
separate tasks may share `$PPID`; Sigma also keys their entries by the validated
`CODEX_THREAD_ID`. If one such task dies while its shared host PID remains alive, its entry expires
after the configured lease TTL (12 hours by default); `session-end` clears it sooner on a clean exit.
An unreadable worker marker also fails closed until its lease expires. If lease expiry is disabled,
the operator must remove that damaged marker after confirming no worker is live.

**Recommended routine prompt** (Claude Desktop, local use — adapt the `.sdlc` path for your project):

> Run `python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py session-active .sdlc`. If it prints `ACTIVE`,
> stop here — a session is already running, nothing to do.
>
> If it prints `FREE`: capture `session_generation=$(python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py start .sdlc --session-pid "$PPID")` — `$PPID` is YOUR OWN invoking shell's parent process id; read it fresh from your own
> shell on THIS call and on every `next`/`next-batch`/`session-end` call below, rather than trying to
> remember a value captured earlier — it stays the same stable value every time you read it during
> this one routine firing, which is exactly what makes it safe to use as this firing's own session
> identity. Then follow `/sigma-loop` exactly as documented above (its own steps already pass
> `--session-pid "$PPID"` on `next`/`next-batch` the same way), with `parallel.goals.enabled` set in
> `.sdlc/config.json` if you want it draining several goals at once. **Always call `loop.py next` /
> `next-batch` for the next goal — never phrase this prompt as "continue where you left off" or
> otherwise lean on memory of a prior run.** A fresh routine firing has no transcript continuity with
> whatever ran before it; ambient conversational continuity must never substitute for a real backlog
> pick, or the whole point of the marker (knowing precisely what's still live) is undermined by the
> one thing it can't see. Let it run to backlog-empty or budget, exactly as `/sigma-loop` already does
> unattended. When it stops, run `python3 <installed-sigma>/skills/sigma-loop/scripts/loop.py session-end .sdlc
> --session-pid "$PPID" --session-generation "$session_generation"` (the SAME `$PPID` value this firing used throughout) before exiting, so the
> next firing correctly sees `FREE` again.

Not zero-flag at the `loop.py` CLI level — `--session-pid "$PPID"` appears on every call above, and
that's deliberate, not an oversight: no value `loop.py` could compute FOR ITSELF from inside a
short-lived subprocess is both stable across your routine's separate commands and unique to this one
firing (see the session-registry paragraph above for why `os.getppid()` alone cannot be that value).
"Zero-touch" describes the SCHEDULING — your host fires this prompt on a timer, nothing here starts
one — and the fact that the prompt itself needs no per-firing hand-editing: `$PPID` is the same three
characters every time, resolved fresh by the shell on each call, not a value this prompt asks you to
capture once and carry.
