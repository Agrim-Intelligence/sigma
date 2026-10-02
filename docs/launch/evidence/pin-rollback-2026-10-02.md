# Pin and rollback experiment — 2026-10-02

## Scope and integrity check

Measured commit: `a3c913c95803a360d7f4882ab1762d6adfe2faf8`.

Claude Code 2.1.284 ran the two plugin commands in distinct temporary profiles:
`HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `XDG_CONFIG_HOME` each named a
path below that probe's temporary directory. The read-only clone used a separate
temporary destination and did not invoke a plugin host. The real profile plugin
surface used by `tools/onboarding_control.py` was hashed before and after all
probes: `4c72c166677e3d48` / `4c72c166677e3d48` (unchanged). The real profile and
predecessor installations were not modified. No repository, release, tag, or
GitHub setting was created or changed.

The proposed public release source is `Agrim-Intelligence/sigma` at `v1.0.0`.
The source tag lookup exited 2 with no output; it was absent.

## Command transcripts

Temporary paths below are rendered as `<temporary-dir>`; this is the only
redaction. Exit codes and diagnostic text are otherwise copied from the runs.

### a. `repo@tag` — exit 1

```
$ env HOME=<temporary-dir>/a-home CLAUDE_CONFIG_DIR=<temporary-dir>/a-config CODEX_HOME=<temporary-dir>/a-codex claude plugin marketplace add Agrim-Intelligence/sigma@v1.0.0 --scope user
Adding marketplace…
✘ Failed to add marketplace: Failed to clone marketplace repository: SSH authentication failed. Please ensure your SSH keys are configured for GitHub, or use an HTTPS URL instead.

Original error: Cloning into '<temporary-dir>/a-config/plugins/marketplaces/Agrim-Intelligence-sigma..clone'...
git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.
```

### b. `repo#tag` — exit 1

```
$ env HOME=<temporary-dir>/b-home CLAUDE_CONFIG_DIR=<temporary-dir>/b-config CODEX_HOME=<temporary-dir>/b-codex claude plugin marketplace add Agrim-Intelligence/sigma#v1.0.0 --scope user
Adding marketplace…
✘ Failed to add marketplace: Failed to clone marketplace repository: SSH authentication failed. Please ensure your SSH keys are configured for GitHub, or use an HTTPS URL instead.

Original error: Cloning into '<temporary-dir>/b-config/plugins/marketplaces/Agrim-Intelligence-sigma..clone'...
git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.
```

### c. local clone/path — exit 128

```
$ git clone --branch v1.0.0 --depth 1 https://github.com/Agrim-Intelligence/sigma.git <temporary-dir>/tagged-clone
Cloning into '<temporary-dir>/tagged-clone'...
fatal: Remote branch v1.0.0 not found in upstream origin
```

The clone did not create a tagged directory, so `claude plugin marketplace add
<temporary-dir>/tagged-clone` was not attempted: it could not test the required
tagged local-path gesture.

## Result

No gesture installed a tagged Sigma plugin. Therefore pinning is **not
supported for this unreleased source**, and no rollback-by-pin instruction is
published. The release runbook instead prescribes a forward fix or a new revert
commit on the default branch. Repeat all three isolated probes after the owner
has created the public repository and release tag; record the installed
`plugin.json` version before changing this result.
