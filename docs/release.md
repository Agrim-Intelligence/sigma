# Release, pin, rollback, and incident runbook

This is the owner-attended runbook for a Sigma release. It does not create a
repository, change repository visibility, tag a commit, or publish a GitHub
release. Those are owner actions after the listed checks are green.

## Versioning

Sigma uses semantic versioning. `.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` carry the identical version. A release is a
git tag named `v<version>` on the merge commit and a GitHub release whose notes
are the matching dated CHANGELOG section. The consistency controls reject
manifest drift and an undated published CHANGELOG heading.

## Release checklist

1. Move the intended entries out of `## Unreleased` and under
   `## X.Y.Z — YYYY-MM-DD` in the public CHANGELOG.
2. Bump both plugin manifests to the same `X.Y.Z` version.
3. Run the full suite successfully on every supported CI cell.
4. For the first public release, require `GO` from the separately shipped
   decision-rule checker; do not substitute a local green test result. That
   checker is currently parked and is not present in this snapshot, so this is
   an explicit release stop rather than a copyable command that would fail.
5. Open, independently review, and merge the release pull request.
6. Confirm the merge commit and create the annotated tag `vX.Y.Z` on it.
7. Extract that CHANGELOG section to a reviewed file and, as owner, run
   `gh release create vX.Y.Z --notes-file <section-file>`.
8. Announce the release, including support status and rollback guidance.

## Pin and rollback

**Pinning is not supported for the currently unreleased source.** The planned
public repository and `v1.0.0` tag do not yet exist. The isolated probes in
[`pin-rollback-2026-10-02.md`](launch/evidence/pin-rollback-2026-10-02.md)
did not produce an installed tagged plugin: the two marketplace source forms
could not authenticate over SSH, and cloning the public HTTPS source at
`v1.0.0` reported that the branch/tag does not exist.

Do not document or ask users to use a pin until an owner repeats the following
in an isolated profile against the real public release and records an install
whose installed `plugin.json` reports the selected version:

```
claude plugin marketplace add <repo>@<tag>
claude plugin marketplace add <repo>#<tag>
git clone --branch <tag> <repo> <dir>
claude plugin marketplace add <dir>
```

Until then, recovery is a forward fix or a revert on the default branch, not a
promised pin. Never run these experiments against a real profile or modify an
existing/predecessor installation.

## Pre-rename safety procedure

Before the private repository is renamed and the public snapshot takes its
former name, stop every loop and watcher. In every affected checkout, repoint
or blank `discovery.github.repo`; update `discovery.github.project.owner` and
`number` if the board moved; and update `ledger.handoff.upstream_repo` when it
was set. Repoint `origin` before any push. Verify the intended GitHub target
read-only with:

```
gh api repos/<owner>/<repo> --jq .full_name
```

Only after each configured slug and remote has been checked may the owner
perform the repository rename/snapshot publication. This prevents a surviving
watcher from editing the public repository while its checkout still belongs to
the private one.

## Incident response

The owner owns communication, the emergency merge, release publication, and
the security-advisory decision.

| Severity | Meaning | Owner response |
| --- | --- | --- |
| S1 | Data loss, secret exposure, or an unstoppable loop | Stop affected loops; within 24 hours revert the default branch to the last-good tag's tree as a new commit, bump the patch version, publish the forward-fix release, assess a GitHub Security Advisory, and retain a README banner until resolved. |
| S2 | A supported cell is broken | Stop the release, file/assign the repair, verify every supported cell before the replacement release, and publish corrected notes. |
| S3 | Other defect | File and prioritize it; use a normal patch release if users need the fix. |

The default branch is never rewritten for rollback. If a later isolated control
verifies a tagged pin, this section will name that exact gesture; until then a
user updates through the marketplace only after the owner has published the
forward fix.

## Control evidence

The release-heading control is run with
`$HOME/.sigma-venv312/bin/python -m pytest tests/test_release_consistency.py -q
-p no:cacheprovider`. For the deliberate red run, point
`SIGMA_RELEASE_CHANGELOG` at a temporary copy whose published heading lacks the
ISO date; that exact selector must fail before the unchanged tree is accepted.
