# Plan-review findings for #2026 (verdict SOUND-WITH-REFINEMENTS)

Recorded against plan sha256 `6e5ff2a7229a29b573b2f4c43e913427668d5a5c3c29b85341a0684fb551cb73`
(`.sdlc/state/gates/2026.json`). The plan file stays unedited; these refinements are applied during
Implement.

## Required
1. An unavailable reading must not reset the baseline. When the reading is unavailable and the
   validated session id has not changed, record `session=<validated id>` (never an unsafe id) and
   carry the old `first_pick`. Test: host 70k → unavailable (same id) → host 90k ⇒ `first_pick == 70k`.
   Control: drop the carry on unavailable → red.
2. The F1 test must register the session first, because `refresh_registered_session_heartbeat` does
   nothing without a marker (`loop.py:3165-3166`). Call `begin_run` before pick 1 and assert the
   refresh returned True. Without that, the test passes against a whole-dict replace.
3. Add a partial-last-line case: a valid assistant line, then a truncated trailing line with no
   newline ⇒ the earlier line's tokens.
4. Drop the `env` parameter from `context_reading`, because the reused `_session_id` /
   `_codex_session_id` read `os.environ` only. Tests use monkeypatch. Add one Codex test with only
   `CODEX_SESSION_ID` set.
5. In the measurement step, time the whole `context_reading` call against the real
   `~/.claude/projects`, record that directory's entry count, and state the 100x behaviour.
6. Add a re-write case at exactly 50%, with its `>=` vs `>` control.
7. The real-run step must use an explicit scratch `.sdlc` created in local mode with an empty
   backlog. Never use the root's or the worktree's real github-mode config, which would claim live
   issues.

## Optional (both applied)
8. Word the tail as "context N tokens at last pick (host)", so a refreshed `last_seen` does not make
   a stale reading look fresh.
9. Keep `write_session_heartbeat`'s `path.parent.mkdir` before it calls `state.session_write`.
