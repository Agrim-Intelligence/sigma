# Launch definition

What "launch" means for Sigma: what ships, to whom, and on which hosts. Every launch-readiness
threshold points at this page. The same decisions, machine-readable, are in
[`definition.json`](definition.json); `tests/test_launch_definition.py` keeps the two in sync.

## Status

Proposed — not yet signed by the owner

The owner accepted these defaults in chat on 2026-09-30. Merging the pull request that carries this
page is the signature; until then the page stays `proposed`. On signing, this line becomes
`Signed by <login> on <YYYY-MM-DD>` and the JSON's `status`, `signed_by` and `signed_on` change with
it.

## What ships

- **Artifact:** a fresh public snapshot repository, built from a reviewed tree. The snapshot does not
  carry this repository's issue and pull-request history or its other branches.
- **Public repository:** `Agrim-Intelligence/sigma`. That is the name the current private
  repository has today, so the current private repository is renamed first and the new public
  snapshot repository then takes this name. The rename is the owner's action, done by hand; no
  Sigma tool, goal or agent creates, renames or changes a repository. The name matches the
  repository `/agrim-doctor` reads for its version check.
- **Install channel:** the Claude Code plugin marketplace, pointing at the public repository. It is
  the only channel with a recorded end-to-end run.
- **Version:** `1.0.0`, git-tagged `v1.0.0` in the public repository. `.claude-plugin/plugin.json`
  and `.claude-plugin/marketplace.json` already say `1.0.0`.

## To whom

Individual developers and small teams who keep their work on GitHub. The board and the label model
assume GitHub.

## Supported cells

A `supported` cell is launch-blocking: it must work at launch. An `experimental` cell ships, is
labelled experimental wherever it is documented, and does not block launch; none of them has a
recorded live run yet.

| Host | OS | Python | Modes | Cell |
|---|---|---|---|---|
| `claude-code` | `macos` | 3.10, 3.11, 3.12, 3.13 | `local-goals`, `github` | `supported` |
| `claude-code` | `linux` | 3.10, 3.11, 3.12, 3.13 | `local-goals`, `github` | `supported` |
| `codex` | any | any | any | `experimental` |
| `cursor` | any | any | any | `experimental` |
| any | `windows` | any | any | `experimental` |

A host, OS, Python version or mode that no row names is neither supported nor experimental.

## Out of scope

These are opt-in features with no recorded end-to-end run. They are not part of the launch and no
launch threshold covers them:

- the Slack listener (`slack-listener`);
- cross-repository units (`cross-repo-units`);
- managed settings (`managed-settings`).

## How to change this page

Any change, including signing it, is a pull request that the owner merges. Change
`definition.md` and `definition.json` in the same pull request; `tests/test_launch_definition.py`
fails when they disagree.
