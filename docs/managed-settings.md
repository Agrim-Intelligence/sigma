# Managed settings

Organisation-wide policy for Sigma. Back to the [README](../README.md).

## Managed settings (optional org policy)

When installed in an organization with a device-management tool or another policy writer, the core
can read org policy from `.sdlc/managed-settings.json`. A checkout declares that it is adopted with
`managed_settings.project_id` in `.sdlc/config.json`, or simply by having that file.
This file specifies which branch reviews are required
(`work.require_review`), whether a plan is required before opening a PR (`gates.hard_plan_gate`),
and whether the event journal is enabled (`journal.enabled`).

If the file was never present, the core uses the repository's own config. If the file
specifies a locked policy, that value overrides the local config. If the member's access is
revoked, or the file is unreadable or invalid, the core refuses to merge or open a PR.

**A deleted policy file does not switch policy off.** The first time the core reads a valid policy
file it records that this checkout is *enrolled*, in two places that a one-file deletion does not
reach: `.sdlc/state/managed-enrolled.json` and a marker inside the checkout's own git directory
(`.git/sigma-managed-enrolled-<hash>.json`, per checkout, keyed by the `.sdlc` path). Both live in
Sigma's Python and git state, not in a host hook, so every host sees the same answer. If the file
later disappears from an enrolled checkout the status is `enrolled-policy-missing` and the merge
gate, the PR gate and the journal refuse, naming the fix. Everything else keeps working.

Recovery, in order: restore the file from your policy writer (the refusal clears by itself), or,
if this checkout was deliberately removed from org policy:

```
python3 <installed-sigma>/skills/sigma-loop/scripts/managed_settings.py unenroll .sdlc
```

It is local, idempotent and needs no network. A writer that removes the file on offboarding makes
each enrolled checkout need that one command (or a restored file); a writer that replaces the file
(delete then create) causes at most a transient refusal that clears itself.

**This is detection and refusal, not a guarantee against a local user.** Anyone who can write the checkout can delete
the policy file and both markers, or run `unenroll`, and the checkout then reads as never enrolled.
What this does defend is a
one-file deletion, a stray cleanup, or a script that clears `.sdlc/` or `.sdlc/state/`. Enrolment
is learned lazily at the first read after a valid file arrives, so a file delivered and deleted
before any gate, journal write or `doctor` run leaves no memory. A fresh clone that never held the
file reads as never enrolled (refusing it would be an outage). The Claude Code `plan_gate.sh` hook
is an accelerator that reads only the file; `work.py pr` and `work.py merge` are the host-agnostic
enforcement points.

The managed-settings file is advisory against anyone with write access to the checkout: they can edit
or delete it, and deleting it falls back to the local config once no enrolment marker remains (a
checkout that never held the file, or one where the writer also removed the markers or ran
`unenroll`). Deleting the file alone is refused for an enrolled checkout, as above.

This feature is opt-in and requires something that writes that file. Repositories without one have
no managed-settings file and behave identically to the public default.
