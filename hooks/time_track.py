#!/usr/bin/env python3
"""Process-time hook: how long each Sigma skill is actually worked, per session, no goal needed.

Registered three times in hooks/hooks.json — `UserPromptSubmit`, `PreToolUse` (matcher `Skill`)
and `Stop` — and all three are ONE state machine over ONE marker file, which is why this is one
module and not three scripts. A SEGMENT is a stretch of assistant work attributed to one skill: it
opens when a user turn begins or a skill is invoked mid-turn, and closes when a skill is invoked
(a switch) or the assistant's turn ends. Idle — the gap between `Stop` and the next prompt, i.e.
waiting on a human — falls inside no segment and is excluded by construction.

`apply()` is pure and is the whole design; `main()` is a thin shell around it. Ships in the plugin
and fires in EVERY repository on the machine, so it is fail-open, exits 0 no matter what, and
records only where Sigma is adopted (a `.sdlc/` directory exists). It is an ACCELERATOR, never
load-bearing: a host with no hooks still has the script-time floor.

THE ONE SECURITY BOUNDARY. `UserPromptSubmit` hands this process the user's prompt on stdin, and
`PreToolUse` hands it tool arguments. Nothing from either reaches disk except a timestamp taken by
this process, the session id, and — for a `Skill` call or a typed `/command` — the skill's NAME.
`apply()` returns nothing else, so there is nothing else `main()` could write.
"""
from __future__ import annotations
import json
import os
import pathlib
import re
import sys
import time

_HOOKS = pathlib.Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HOOKS.parent / "skills" / "sigma-loop" / "scripts"


def _load(name):
    """Sibling-skill script loaded by file path — `decision_gate.py` reaches into `skills/` the
    same way. Never a package import (`tests/test_import_boundary.py` bans only the private package)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _LOOP_SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


timing_store = _load("timing_store")

#: The marker between hook processes — the open segment and the active skill, nothing else. One
#: per session, the phase marker's own pattern one level down.
MARKER = "open.json"

#: A skill name as it may appear in a stored line: the same character class the slash regex
#: allows, bounded. It is a label, never a path, but a label with a newline in it would break the
#: one-JSON-object-per-line store.
_NAME_OK = re.compile(r"[^A-Za-z0-9_:.-]")

#: The bucket for work done before any skill has been invoked in the session. A real, visible
#: bucket — that work happened — worded on output as "no skill invoked yet".
NO_SKILL = "(none)"

#: A user-typed slash command. The dominant way a human invokes a Sigma skill ("use when the
#: user runs /sdlc-…") arrives as PROMPT TEXT, never as a `Skill` tool call — so a prompt that
#: begins this way is a switch. Only the token is taken; group 1 is all `apply()` ever keeps.
#: The token starts with a letter and is the whole first word, at most 80 characters: without
#: those bounds `/e/sigma is the repo` switched to a skill named `e`, `/123` to `123`, and a
#: 200 000-character token landed verbatim in the store (code review C8, security review S3).
_SLASH = re.compile(r"^\s*/([A-Za-z][A-Za-z0-9_:.-]{0,79})(?=\s|$)")


def fresh_state():
    return {"skill": NO_SKILL, "started": None}


def _close(state, now_ms):
    """The open segment as a closed record, or None when there is nothing honest to record: no
    segment open, or a clock that moved backwards (a negative interval is unmeasurable — omitted,
    never a zero, never a negative)."""
    started = state.get("started")
    if started is None:
        return None
    ms = int(now_ms) - int(started)
    if ms < 0:
        return None
    return {"name": state.get("skill") or NO_SKILL, "started": int(started), "ms": ms}


def apply(state, event, now_ms, skill=None, prompt=None):
    """One hook event against the marker state. -> (new_state, closed_segment_or_None).

    `event` is one of `prompt`, `skill`, `stop`; `now_ms` is this process's own clock in
    milliseconds — never a timestamp read from the payload.

      prompt  A segment already open here is DANGLING — a crashed turn, or the provisional segment
              `stop` leaves behind at a genuine end — and is DROPPED, never recorded: that is how
              idle stays excluded. If the prompt is a typed `/command`, the active skill becomes
              that token first. Then a segment opens for the active skill.
      skill   Closes the open segment to the PREVIOUS skill (the seconds before a switch belong to
              what was active) and opens one for the new skill. Splitting here, rather than
              attributing a whole turn to one skill, is what makes "time in plan" true when
              `/sigma-plan` is invoked partway through a turn.
      stop    Closes the open segment and opens a PROVISIONAL one for the same skill. `Stop` is not
              once per turn: with the stop gate on, a blocked Stop makes the agent continue and
              Stop fires again, and the continuation must be measured. A genuine end leaves the
              provisional segment dangling for the next prompt to drop.
    """
    state = dict(state or fresh_state())
    if not state.get("skill"):
        state["skill"] = NO_SKILL
    now_ms = int(now_ms)

    if event == "prompt":
        match = _SLASH.match(prompt or "")
        if match:
            state["skill"] = match.group(1)
        state["started"] = now_ms
        return state, None

    if event == "skill":
        closed = _close(state, now_ms)
        state["skill"] = str(skill).strip() if skill and str(skill).strip() else state["skill"]
        state["started"] = now_ms
        return state, closed

    if event == "stop":
        closed = _close(state, now_ms)
        state["started"] = now_ms
        return state, closed

    return state, None


# --------------------------------------------------------------------------- the shell around it


def _safe_name(value):
    text = _NAME_OK.sub("", str(value or "")).strip()[:80]
    return text or None


def _resolve_session(payload):
    """The payload's own id first (Claude Code delivers it on stdin), else the store's chain
    (environment, then the loop's run id, then the dated shared bucket). An id that would be
    refused as a path component is treated as ABSENT — the next link is used — never as a crash:
    a hook must not fail a turn because stdin held a slash."""
    sid = str((payload or {}).get("session_id") or "").strip()
    if sid and not timing_store._unsafe_stem_reason(sid):
        return sid
    return timing_store.session_id()


def _read_marker(path):
    """The marker is read back from disk with NO type trust (security review S4): a dict `skill`
    became a dict `name` in the store and crashed the reader; a non-int `started` raised on every
    event. The skill is bounded to the same character class as a `Skill` call; a `started` that is
    not an integer, or a marker that is anything but a plain file, is a fresh marker."""
    try:
        if timing_store._is_link(path):
            return fresh_state()
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "skill" in data and "started" in data:
            raw_skill = data["skill"]
            if isinstance(raw_skill, str):
                skill = NO_SKILL if raw_skill == NO_SKILL else _safe_name(raw_skill)
            else:
                skill = None
            started = data["started"]
            started = int(started) if started is not None else None
            return {"skill": skill or NO_SKILL, "started": started}
    except Exception:                                       # noqa: BLE001 - a bad marker is a fresh one
        pass
    return fresh_state()


def handle(project_dir, event, payload, now_ms=None):
    """One event, end to end. -> the closed segment that was recorded, or None.

    Gated on `.sdlc/` existing: this hook fires in every repository on the machine and records
    only where Sigma is adopted — not a config key, the same rule `sigma_gate.sh` follows.

    ORDER IS LOAD-BEARING: the closed segment is appended BEFORE the marker is rewritten. If the
    append raises, the marker still holds the old state, so the next event re-closes the same
    segment with the same `started` and the store's de-duplication collapses it. Written the other
    way round, a failed append would advance the marker and lose the segment outright. This
    function therefore does NOT swallow — `main()` does, at the top, once.

    Of the payload, exactly three things are read: `session_id`, `tool_input.skill` (for a
    `Skill` call), and `prompt` — the last only to test for a leading `/command`, whose token is
    all that `apply()` keeps. The timestamp is this process's own clock, never the payload's."""
    sdlc_dir = pathlib.Path(project_dir) / ".sdlc"
    if not sdlc_dir.is_dir():
        return None
    payload = payload if isinstance(payload, dict) else {}
    now_ms = int(round(time.time() * 1000)) if now_ms is None else int(now_ms)

    session = _resolve_session(payload)
    marker = timing_store.session_dir(sdlc_dir, session) / MARKER
    # `write_text` follows a link (security review S1, probe G: `open.json -> notes.txt` had the
    # target overwritten with marker JSON). A linked marker means this event records nothing.
    if timing_store._is_link(marker):
        return None
    state = _read_marker(marker)

    skill = prompt = None
    if event == "skill":
        tool_input = payload.get("tool_input")
        skill = _safe_name(tool_input.get("skill")) if isinstance(tool_input, dict) else None
    elif event == "prompt":
        raw = payload.get("prompt")
        prompt = raw if isinstance(raw, str) else None

    new_state, closed = apply(state, event, now_ms, skill=skill, prompt=prompt)
    if closed is not None:
        timing_store.append_session(sdlc_dir, session, timing_store.TURN_KIND, closed["name"],
                                    closed["ms"], started=closed["started"], now=now_ms / 1000.0)
    marker.parent.mkdir(parents=True, exist_ok=True)
    timing_store._refuse_links(marker, sdlc_dir)
    marker.write_text(json.dumps(new_state, sort_keys=True), encoding="utf-8")
    return closed


#: hooks.json passes the event as argv[1]; the payload's `hook_event_name` is the fallback so a
#: registration without the argument still resolves.
_EVENT_NAMES = {"UserPromptSubmit": "prompt", "PreToolUse": "skill", "Stop": "stop"}


def main(argv):
    try:
        event = (argv[1] if len(argv) > 1 else "").strip().lower()
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        if not event:
            event = _EVENT_NAMES.get(str((data or {}).get("hook_event_name") or ""), "")
        if event in ("prompt", "skill", "stop"):
            handle(os.environ.get("CLAUDE_PROJECT_DIR", "."), event, data)
    except Exception:                                       # noqa: BLE001 - fail-open: never disrupt the session
        pass
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv)
