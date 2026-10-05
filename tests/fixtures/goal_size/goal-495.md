Follow-up filed by #310 [E19.S2], Decision 3 -- not solved there, tracked here as a distinct,
not-yet-scoped provisioning/design question.

## The gap

Two independently-provisioned identity namespaces exist in this codebase, and nothing ties them
together:

- **Session side**: `viz/app/src/login.ts`'s `signIn()` sets the session's actor identity to
  exactly the string typed at `viz users add <username>` (`viz/accounts/users.py`).
- **Analytics side**: `viz_event.actor_id` / `viz_note.sent_by` / `viz_note.sent_to` /
  `viz_review.actor` are populated at ingest time from the SDLC loop's own `ledger.actor`
  config key, else `gh api user -q .login`, else `$USER` (`skills/sigma-loop/scripts/ledger.py`'s
  `actor()`).

`viz/cli/actor.py`'s own module docstring already names this exact failure shape for the
offline CLI equivalent, and `viz/app/src/user.ts`'s `resolveUser()` (issue #310)
repeats the same honesty for the web path.

Two failure shapes follow, both silent:
1. **Safe but useless** -- the two names differ, and an operator's own `/ic` view renders entirely
   absent even though real data about them exists under a different key
   (`viz.cli.ic._user_ever_seen` detects and banners this today, but only the symptom).
2. **Unsafe** -- a web username collides with a DIFFERENT real person's `actor_id` (a reused
   handle, a templated account). Scoping is then correct by construction in SQL and still
   resolves to the wrong human's data.

## Open questions for a future scoping pass

- Should `viz users add` require or validate that its `--username` matches `ledger.actor` for
  the project(s) it will be used against?
- Should there be a runtime cross-check (e.g. a warning banner when the session identity has never
  appeared in the store at all -- already partially covered by `_user_ever_seen`)?
- Does this belong in provisioning tooling, documentation-only operator responsibility (the
  precedent `viz/app/README.md` already accepts for `role`), or a stronger runtime guard?

## Related

- Decision 1 of #310 also named a distinct, not-yet-solved limit worth folding into the same
  future pass when it's scoped: `/ic`'s current CLI-bridge data-access transport (a `python3 -m
  viz app ic` shell-out) is a deliberate interim architecture, diverging from spec section 6's
  eventual "queries and guardrails move to the API" end state. Once E22.S1 wires real internal
  Next-to-FastAPI network transport, `/ic`'s data path should migrate from the CLI bridge to a
  real `viz/api/` endpoint calling the same `viz.cli.ic.build_ic_payload()` the bridge
  already reuses.

Not `sdlc:goal`-labelled by default -- this is a provisioning/design question, not a
ready-to-implement goal.

