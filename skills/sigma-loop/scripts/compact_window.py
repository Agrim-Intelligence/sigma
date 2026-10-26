#!/usr/bin/env python3
"""The compaction safety net's window for a launched Claude session (#2028, design 2024 FR-5).

Both Claude launchers -- `supervise_daemon.py` and `autowatch.py`'s drive -- call `apply` on the
child's OWN env dict (never `os.environ`) so a runaway orchestrator compacts before the host
ceiling, while a normal one hands off first (slice 2's context hand-off, the load-bearing control;
this is only the net under it).

The rule:
* `handoff.after_context_tokens` (a) unset, 0, negative, a bool or not an int -> set NOTHING. The
  host's own threshold (about 967k on a native 1M window, the 200k boundary otherwise) is the net.
  The host window cannot be known before `claude -p` picks its model, so no value is guessed.
* a set -> max(2a, PHASE_SUBAGENT_FLOOR), set only when below 900,000, i.e. never later than the
  host's own ~967k point. 2a is headroom for the drain: the hand-off is checked only at a pick and
  live slots finish after it (measured 603,142 tokens over 3 goals, loop.py's own note). It is
  always above a, because max(...) >= 2a > a.

WHY THE FLOOR: subagents and every nested `claude -p` INHERIT CLAUDE_CODE_AUTO_COMPACT_WINDOW (the
Claude sub-agents doc: "the subagent uses that variable's window instead"), so a window sized for
the orchestrator alone would compact phase subagents mid-phase. 420,000 is the measured p99 subagent
peak context rounded up: 411,359 tokens over 6,906 subagent transcripts on one machine in the 30
days to 2026-10-24 (median 111,638, max 905,845), script `.sdlc/research/2028-subpeaks.py`.
Ceiling: one machine, one month, mostly Sigma phases -- re-measure with that script when the
phase mix changes.

The operator's window always wins. The env var OUTRANKS `/autocompact`, `--autocompact` and the
`autoCompactWindow` setting (Claude model-config doc), so a blind setdefault would silently
override them; `operator_set` therefore also looks at the launch command and the settings files
Claude reads. LIMIT: MDM / OS policy, server-managed settings and an embedding host's
`managedSettings` cannot be read from here and are NOT detected.
"""
import os
import pathlib

KEY = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
PHASE_SUBAGENT_FLOOR = 420_000
#: Exclusive: the host compacts a native 1M window at about 967k on its own.
HOST_POINT_MARGIN = 900_000
#: File-based managed settings (macOS, Linux). Module-level so tests can repoint them.
MANAGED_SETTINGS_PATHS = (pathlib.Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
                          pathlib.Path("/etc/claude-code/managed-settings.json"))
_SETTINGS_MARKERS = ("autoCompactWindow", KEY)


def _explicit_context_tokens(cfg):
    """`handoff.after_context_tokens` as a positive int, else None. An integral float (300000.0,
    which JSON tools emit) counts as that int; 0, a bool, a string, a negative, a non-integral
    float or any malformed value counts as ABSENT -- so `true` or `0` can never yield a window.
    Mirrors slice 2's (#2027) `_explicit_context_tokens` in loop.py exactly; switch to importing
    that one once slice 2 is on main."""
    handoff = cfg.get("handoff") if isinstance(cfg, dict) else None
    a = handoff.get("after_context_tokens") if isinstance(handoff, dict) else None
    if isinstance(a, float) and a.is_integer():
        a = int(a)
    if isinstance(a, bool) or not isinstance(a, int) or a <= 0:
        return None
    return a


def window(cfg):
    """The window to set for this config, or None to leave the host's own threshold in force."""
    a = _explicit_context_tokens(cfg)
    if a is None:
        return None
    candidate = max(2 * a, PHASE_SUBAGENT_FLOOR)
    return candidate if candidate < HOST_POINT_MARGIN else None


def _settings_paths(env, repo_dir):
    user = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or pathlib.Path.home() / ".claude")
    repo = pathlib.Path(repo_dir)
    return (user / "settings.json", repo / ".claude" / "settings.json",
            repo / ".claude" / "settings.local.json", *MANAGED_SETTINGS_PATHS)


def operator_set(env, cmd, repo_dir):
    """Why the operator's own window applies, or None. A substring match: a false positive only
    means Sigma sets nothing."""
    if KEY in env:
        return f"operator set {KEY}"
    if "--autocompact" in (cmd or ""):
        return "operator passed --autocompact"
    for path in _settings_paths(env, repo_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in _SETTINGS_MARKERS):
            return f"operator set the window in {path}"
    return None


def apply(env, cfg, cmd, repo_dir):
    """Set the window in `env` (the child's dict) when Sigma should. Returns (value or None, why)."""
    why = operator_set(env, cmd, repo_dir)
    if why:
        return None, f"left to the operator ({why})"
    value = window(cfg)
    if value is None:
        if _explicit_context_tokens(cfg) is not None:
            return None, (f"not set: max(2 x handoff.after_context_tokens, {PHASE_SUBAGENT_FLOOR}) "
                          f"is not below {HOST_POINT_MARGIN}; the host's own threshold applies")
        return None, ("not set: handoff.after_context_tokens is unset or not a positive integer; "
                      "the host's own threshold applies")
    env[KEY] = str(value)
    return value, f"{value} from handoff.after_context_tokens"
