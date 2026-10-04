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

- **Artifact:** `public-snapshot` — a fresh public snapshot repository, built from a reviewed tree.
  The snapshot does not carry this repository's issue and pull-request history or its other
  branches.
- **Public repository:** `Agrim-Intelligence/sigma` is the value `definition.json` records today.
  The owner decided on 2026-10-05 that the public repository is a NEW repository, created fresh
  with a clean one-commit snapshot of this one; this repository is not renamed. Its name replaces
  `public_repo` in `definition.json` in the separate rename. Creating and pushing it is the owner's
  action, done by hand (no Sigma tool, goal or agent creates or changes a repository); the export
  builder and the steps are in [the public snapshot](../public-snapshot.md).
- **Install channel:** the Claude Code plugin marketplace, pointing at the public repository. It is
  the only channel with a recorded end-to-end run.
- **Version:** `1.0.0`, git-tagged `v1.0.0` in the public repository. `.claude-plugin/plugin.json`
  and `.claude-plugin/marketplace.json` already say `1.0.0`.

## What the rename changes

Once this private repository is renamed, GitHub redirects its old name to the new one — until the
new public repository takes `Agrim-Intelligence/sigma`. From then on the redirect is gone, and
everything that names `Agrim-Intelligence/sigma` points at the public repository, not the private
one. The release goal (#359) must handle each of these before the public repository takes the name:

- **Safety hazard — the loop's own configured repository.** `/agrim-setup` writes
  `discovery.github.repo` into every adopter's `.sdlc/config.json`
  (`skills/agrim-setup/scripts/setup.py:190`, `:255`; `/agrim-init` fills the same key,
  `skills/agrim-init/scripts/init_flow.py:309`), and this repository's own `.sdlc/config.json`
  holds `Agrim-Intelligence/sigma` there. The loop addresses GitHub by that slug directly, not by
  the git remote: it reads goals from it (`skills/agrim-loop/scripts/sources.py:88`) and passes it
  as `--repo` (`sources.py:1253`) to every label edit, comment, close, body edit and issue create
  (for example `:2819`, `:2838`, `:3608`). After the name takeover a
  running loop reads goals from, and writes labels and comments to, the PUBLIC repository, while
  its pushes go wherever `origin` points; repointing `origin` (below) does not fix it. #359 must
  ship a pre-rename step: stop every loop and watcher, repoint or blank `discovery.github.repo`,
  and verify it with a read-only `gh api repos/<slug> --jq .full_name`. The same step covers
  `discovery.github.project.owner` / `number` if the board moves, and `ledger.handoff.upstream_repo`
  (`skills/agrim-loop/scripts/upstream.py:332`; opt-in, empty by default, but an adopter who set it
  to this slug files kit findings on the public repository). The other remote settings —
  `work.remote`, `ledger.remote`, `knowledge_graph.sync.remote` — name a git remote (`origin`),
  not a slug, so they follow `origin`'s URL: until `origin` is repointed, pull requests, the ledger
  ops branch and the knowledge branch are pushed to the public repository too;
- existing clones' `origin` remote;
- the loop's `gh` calls that have no configured slug and address `{owner}/{repo}`, which `gh`
  fills from the checkout's git remote;
- `docs/publish-runbook.md`'s `tools/leak_refs.py scan` and `rewrite` commands, which pass
  `--repo Agrim-Intelligence/sigma`;
- `docs/board.md`'s milestone-assignment commands (`REPO=` at `docs/board.md:162`, `$Repo` at
  `:183`), which PATCH the milestone on issues in the named repository;
- `contract/golden/config.json:2` (`discovery.github.repo` in the golden config);
- the README's CI badge (`README.md:7`);
- `_MARKETPLACE_REPO` in `skills/agrim-doctor/scripts/doctor.py:1515`. `/agrim-doctor`'s version
  check reads the repository the plugin was installed from first, and uses `_MARKETPLACE_REPO`
  only as a fallback when that record cannot be read.
- existing plugin installs: each recorded `Agrim-Intelligence/sigma` as its marketplace source, so
  after the takeover they update from the public snapshot. Whether that is intended is the owner's
  decision, in #359.

This list is from a grep of the whole tree for the slug and for every configuration key that
holds one (`skills/agrim-init/templates/config.json.tmpl`, hooks, `.claude-plugin/`, the ledger,
`sync.py`, the board settings). Tests that use the slug as a fixture are not listed; they do not
address GitHub. `.claude-plugin/marketplace.json` names no repository (its source is `./`), and
`.github/CODEOWNERS` names an organisation team, which the rename does not touch.

`docs/publish-runbook.md` (its opening paragraph and its "Before the visibility flip" section)
still assumes this repository's visibility flips to public. That contradicts the artifact above,
a fresh snapshot while this repository stays private; it is a known contradiction for #359 to fix.

## To whom

- **Audience:** `github-individuals-and-small-teams` — individual developers and small teams who
  keep their work on GitHub. The board and the label model assume GitHub.

## Supported cells

A `supported` cell is launch-blocking: it must work at launch. An `experimental` cell ships, does
not block launch, and is to be labelled experimental in the README; that labelling belongs to the
README-claims goal and is not claimed here as done.

Every supported cell is a cell CI runs (`.github/workflows/ci.yml`): the full suite on Ubuntu with
Python 3.10, 3.11, 3.12 and 3.13, and on macOS with Python 3.12 only (#338). The owner decided on
2026-10-03 that the launch claim goes no further than that, so a claim cannot be false on a cell no
one checks. A cell CI runs is not a statement that it passes today, only that something would
notice if it did not. The macOS cells on Python 3.10, 3.11 and 3.13 have no CI leg: they are
untested, not unsupported. Nothing says they fail; the launch simply does not claim them, and they
are listed as `untested` in `definition.json`. `tests/test_launch_ci_cells.py` derives the cells CI
runs from the workflow file and fails when this page, its JSON twin or any other doc claims a
supported cell CI does not run.

No experimental cell has a recorded END-TO-END run. Two have partial validation:

- **Codex:** the first `loop.py start` gesture succeeded in a disposable real Codex CLI session,
  and Codex phase usage was checked against real rollout records (README, "Codex (partial live
  validation)"). A complete Codex goal through pull-request merge has not been measured.
- **Windows:** four successful `windows-latest` runs of `.github/workflows/windows.yml`, which
  covers the watcher primitives only (`tests/test_windows_real.py`), not a goal.

The goals that would close these gaps are #302, #303 and #305.

| Host | OS | Python | Modes | Cell |
|---|---|---|---|---|
| `claude-code` | `macos` | 3.12 | `local-goals`, `github` | `supported` |
| `claude-code` | `linux` | 3.10, 3.11, 3.12, 3.13 | `local-goals`, `github` | `supported` |
| `codex` | any | any | any | `experimental` |
| `cursor` | any | any | any | `experimental` |
| any | `windows` | any | any | `experimental` |

A host, OS, Python version or mode that no row names is neither supported nor experimental. That
includes macOS on Python 3.10, 3.11 and 3.13, which is untested (see above).

## Out of scope

These are opt-in features with no recorded end-to-end run. They are not part of the launch and no
launch threshold covers them:

- the Slack listener (`slack-listener`);
- cross-repository units (`cross-repo-units`);
- managed settings (`managed-settings`).

## How to change this page

Any change, including signing it, is a pull request that the owner merges. Change
`definition.md` and `definition.json` in the same pull request; `tests/test_launch_definition.py`
fails when they disagree on the artifact, public repository, version, audience, status, supported
cells, experimental cells or out-of-scope list. It reads the page from fixed spots: the
`**Artifact:**`, `**Public repository:**`, `**Version:**` and `**Audience:**` bullets (the first
backticked value), the first line under Status, the table rows, and the backticked id ending each
out-of-scope bullet. Keep those shapes. `python -m pytest tests/test_launch_ci_cells.py -q` fails when a
`supported` row names an OS and Python that no CI leg runs, so a change to the table needs a CI leg
first (a separate goal; adding one is not part of this page).
