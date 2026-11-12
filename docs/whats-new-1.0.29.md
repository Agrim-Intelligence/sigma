# What is new in 1.0.29

This page lists every change in this release. The same entries are in the `1.0.29` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.28](#upgrading-from-1028) if you only want to know what
changes for you.

Two changes are fixes and one is a new skill. Nothing here adds a config key you must set.

## Upgrading from 1.0.28

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **A cloud session no longer gets the setup wizard's question (#2143).** The session-start hook skips the wizard when
   `CLAUDE_CODE_REMOTE` is `true` or `1`, the same quiet path as a supervised run. `/sigma-doctor` still lists every
   item. A repository can also decline a wizard check in committed config with `setup_wizard.dismissed`, a list of
   check names; a malformed value dismisses nothing.
   **Measured live (13 Nov):** in a real Claude Code cloud container, a hook run from the PR branch printed nothing
   with `CLAUDE_CODE_REMOTE=true` and printed the wizard text with the variable unset.
3. **A 403 on the classic protection read no longer stops auto-merge on every base (#2144).** When the classic
   branch-protection read is refused with a permission 403, Sigma falls back to the branch's `protected` flag. The base
   counts as unprotected only when the classic read was that 403, the rulesets read answered with nothing counted, and
   the flag is exactly `false`; every other case still fails closed. The awaiting-merge note now says why the loop did
   not merge.
   **Measured live (13 Nov):** in a real Claude Code cloud container, the classic branch-protection read answered 403
   with the cloud credential while the rulesets read answered; the fallback path itself was not reached live because
   the repository has no unprotected base.
4. **New skill `/sigma-orchestrate` (#2145, partial delivery).** It turns a list, a search or a description into goals,
   plans them in dependency waves with a wave planner, shows one go-ahead screen (`--dry-run` stops there), and starts
   one `/sigma-goal <N> --land` session per goal as its blockers merge. Docs: `docs/orchestrate.md`.

## Not done for /sigma-orchestrate

- The follow-up decision still rides in the launch prompt; there is no config key yet.
- The approve comment does not yet name the head commit it approved, so the merge check compares times.
- No real batch has been run end to end.

## Known follow-ups

- The loop skill's routing rule does not yet tell agents to pass the merge line to `record review`.
- The detail text is only whitespace-collapsed and capped, so an absolute path in it could reach a public issue note.
- A committed `setup_wizard.dismissed` can silence setup-critical checks.

## Not measured

A real cloud `--land` run through the protection fallback to a merged PR, and a real cloud session started as
`/sigma-goal <N> --land`. Each CHANGELOG entry lists its own gaps.
