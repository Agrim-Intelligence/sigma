# Benchmark isolation launcher

`sigma_bench_launcher.py` is the **operator-supplied isolation launcher** that `evals/bench/bench.py` requires
(`--isolation-launcher`). It is a stdlib-only, POSIX-only set of guards. **It is not a sandbox.** Read the limits
below, and read the script, before you trust it.

Status: written and tested only against a fake `claude` executable. No `claude -p` has ever been run through it,
against a model or otherwise.

## What the harness does with it

Every command the harness starts runs as `launcher -- <command> [args...]`: the `claude -p` runs, `claude --version`
and the visible and hidden scoring commands. The harness sets `HOME`, `CLAUDE_CONFIG_DIR`, `CODEX_HOME` and `TMPDIR`
to fresh per-run directories (a run's scoring commands reuse one profile, so the launcher checks where the
directories are, not that they are empty), passes the prompt on stdin, **discards stderr** and (except for
`--version`) stdout, and tests only whether the exit status is 0. It refuses a launcher inside the repository, so the
file in this directory is a **source you install**, not the path you pass.

## What it does, in order, before anything starts

1. Refuses on a non-POSIX host, a bad config, a launcher installed inside `repo_root`, a real home with no `.claude`
   directory (a guard over nothing can never fail), or any `CLAUDE.md`, `CLAUDE.local.md`, `.mcp.json` or `.claude` in a parent of
   the working directory (Claude Code reads instruction files from parents, and whether it reads a parent's `.claude`
   is unmeasured; setting `HOME` does not stop it). So the harness `--scratch-root` must be outside your home
   directory, which holds `.claude`.
2. Checks the four profile directories: absolute, existing, distinct, one common parent, not the real home and not
   inside its `.claude` or `.codex`; for the `claude` program (`claude_path`) `HOME` and `CLAUDE_CONFIG_DIR` must also be
   **empty** (every harness `claude` call is the first use of its profile; scoring commands reuse a profile, so they
   are not held to that); the profile and its parent must not be inside `repo_root` or `hidden_root`. The
   real home comes from the account database, not `$HOME` (the harness replaced `$HOME`).
3. Allows `--plugin-dir` once, as a separate argument, only for a directory shaped like the harness's clean export:
   top level within `.claude-plugin skills hooks`, holding `.claude-plugin`, no symlinks, no path component named
   `evals` or `tests`, not inside the repository, hidden root or real `.claude`/`.codex`, and a sibling of the profile
   directory (where the harness puts it).
4. Builds the command's environment from nothing: `PATH LANG LC_ALL LC_CTYPE TZ TERM SHELL USER LOGNAME`, names you
   list in `extra_env` (never a name containing KEY, TOKEN, SECRET, PASSWORD, CREDENTIAL, AUTH or COOKIE), the four
   profile variables, and the one credential variable you name, passed **only** to the program whose real path is
   `claude_path`. Scoring commands and every other program never receive it. The allowlist has not been measured
   against a real agent run: a missing variable shows up as an unpriced first run, which stops the harness safely.
5. Hashes the real plugin surface: `~/.claude/plugins`, `~/.claude/settings.json`, `~/.codex/config.toml`,
   `~/.codex/plugins`.
6. Starts a **supervisor** in its own session. The supervisor runs the command in the command's own process group,
   kills that **whole group** at `deadline_seconds` (exit 124), after a normal exit, and when the launcher dies (the
   harness SIGKILLs the launcher at its own deadline; the supervisor sees the end of a pipe only the launcher holds),
   and only then takes the after-hash, so a harness kill cannot skip it. SIGTERM, SIGHUP and SIGINT to the launcher
   end the run the same way and exit 128+n.
7. Compares the real surface before and after. A difference exits **97** naming the path (never contents), writes the
   **latch** `<scratch_root>/real-profile-changed.txt`, appends to the alert log, and sends SIGTERM to the harness
   (only if its command line contains `parent_marker`, default `bench.py`): its documented stop, which keeps paid rows
   in an `aborted` report. Every later invocation refuses (exit 2) until you delete the latch after inspecting what
   changed. Nothing else persists between runs, so ordinary plugin updates between benchmarks never trip it.

Exit status: the command's own (128+n when a signal ended it); 2 refused (nothing started); 97 real profile changed;
124 deadline; 127 cannot start. The deadline clock starts after the before-hash, so one call can take the before-hash,
`deadline_seconds` and the after-hash.
Because the harness discards stderr, **read `<scratch_root>/launcher-alerts.log`** when a run stops: every refusal is appended there, and the latch holds the cause of a 97. The harness itself will only say "could not be priced (claude exit 2)" or "claude exit 97".

The launcher writes only inside `scratch_root` (its dry-run profile, the latch, the alert log; the log stops growing at 1 MiB). It is the
config's own directory, not the harness's `--scratch-root`: use two different directories.

## What it does NOT guarantee

- **Same user, no sandbox.** The agent and the host CLI run as you. They can read anything you can read **and write
  anything you can write, including your real `~/.claude`** (hashing detects changes to four paths and prevents
  nothing), use the network, and read the config file beside the launcher (which names your hidden-test root). Hidden-test
  confidentiality at scoring time depends on you, not on this file.
- **OS keychain and system-wide config.** The host CLI may read the OS keychain, system-wide managed settings and
  other files outside the four hashed paths; the launcher cannot see or stop that. Claude Code documents that with
  `CLAUDE_CONFIG_DIR` set, its credentials file and its macOS Keychain entry are keyed to that directory, so a fresh
  directory reads a different entry (documentation fetched 2026-10-05); that is the host's claim, unmeasured here.
  Whether `.claude` directories in a parent folder are read as project configuration is also unmeasured.
- **Credentials in the parent.** The harness forwards its whole environment to the launcher, so the launcher and the
  harness hold every credential you started them with, readable by the agent through `/proc/<pid>/environ` on Linux (not observed with `ps` on the macOS host this was written on, but do not rely on that). Start the
  harness under `env -i` (the first-run command below does) so only the one credential exists. The agent's own tool
  subprocesses, and the sigma arm's plugin hooks, inherit the credential: unavoidable.
- **Only your home's four paths are hashed.** `~/.claude.json` and `~/.claude/skills`, `agents`, `hooks` and
  `CLAUDE.md` are not. A `CLAUDE_CONFIG_DIR` your shell exports is invisible (the harness
  replaced it). Another Claude or Codex session that updates plugins or settings during a run also exits 97: do not use
  either while the benchmark runs.
- **Deadline.** One `deadline_seconds` covers every command, `--version` and scoring included. Set it a little below
  the harness's `--deadline-seconds`. The harness deadline is per arm and shrinks for later attempts of the matched
  arm and after the plugin export, so it can kill the launcher first; the supervisor still hashes (step 6).
- **Rows after a trip are invalid.** With `parent_marker` null (or a harness started through a wrapper) the tripping run's
  scoring is refused by the latch and recorded as failed, and the SIGTERM can land after that run's row was appended
  or, in a very small window, after the final write.
- **Hash time** is counted in the harness's `wall_seconds` and deadline, and the matched arm pays it twice per attempt. An
  escaping process that keeps stdout open can also stall the harness's `--version` read (a limit of the
  harness).
- Exit 2 can collide with a command's own exit 2, and the launcher's 124 reads as "claude exit 124" in the harness row.
- **One extra paid run is possible after a harness kill.** The next invocation can start before the original
  supervisor has written the latch, and the run that tripped is not in `aborted.cost_usd_spent` (its transcript is
  deleted unpriced), so the reported spend is a lower bound.
- **What the hash proves.** It compares file contents (and link targets) at the start and end of one invocation. A change
  reverted before the end, or a permissions-only change, is not seen.
- **A command can outlive its group.** The supervisor kills the command's process group. A process that leaves it
  (`setsid`, a double fork; whether Claude Code's tool shells do is unmeasured) keeps running after the deadline and can
  write to the real profile after the after-hash, unseen: the next invocation hashes the changed state as its new
  start. The harness's own guard watches only `~/.claude/plugins` at the end. A command that kills the supervisor
  (SIGKILL to its own parent) escapes the deadline and the check the same way, and the launcher then exits with the
  supervisor's signal status (for example 247), not 97. Nothing here prevents any of this; only an OS sandbox would.
- **What the harness reports.** A trip stops the harness with only "signal SIGTERM" (no results file at all if it
  fires at the `--version` step), and a change found during a scoring command aborts before that row is appended.
  The latch and alert log carry the cause.
- **The credential match is a path.** `claude_path` is compared by real path with the program the harness starts. If
  `claude_path` names a version-specific file rather than the stable symlink, an auto-update leaves it pointing at
  the old version, the credential is withheld and the first run stops unpriced; name the stable path, pin the version,
  or add `DISABLE_AUTOUPDATER` to `extra_env`.
- **Hash cost** is two full hashes of the four paths per invocation (the launcher's before-hash, the supervisor's
  after-hash), one invocation per command. The harness's own
  measurement is 332 MB in about 2 s (`evals/README.md`); the launcher's cost on your plugin directories is
  unmeasured, so leave headroom in `deadline_seconds`.
- Never exercised against a real `claude`, a model, or a platform the tests did not run on.

## Authentication: the owner's decision

Claude Code documentation (code.claude.com/docs/en/authentication and /env-vars, fetched 2026-10-05) says that in
non-interactive mode (`-p`) `ANTHROPIC_API_KEY` "is always used when present", and that `CLAUDE_CODE_OAUTH_TOKEN`
(from `claude setup-token`, a one-year subscription token) takes precedence over keychain-stored credentials. Either is
one environment variable, so an empty profile can authenticate **without copying any login state**, and this
launcher never copies your real credentials anywhere. Recommended: `ANTHROPIC_API_KEY`, because it is priced in dollars, which is what `--max-budget-usd` and the harness meter
assume. Choose one:

| Option | `credential_var` | Billing and risk |
|---|---|---|
| Console API key | `ANTHROPIC_API_KEY` | Pay per token; `--max-budget-usd` and the harness meter apply in dollars. Use a key made for this benchmark with a spend limit and revoke it afterwards. |
| Subscription token | `CLAUDE_CODE_OAUTH_TOKEN` | Counts against your plan's usage limits, not dollars, so the meter's list-price dollars are not what you are charged; the token lasts a year. |
| Copy your real login into the profile | none | **Not supported:** it puts your real account in an agent-readable directory. |

The harness requires a priced run, so the first run stops at "could not be priced" if no credential reaches `claude`.

## Install

```
mkdir -p ~/.sigma-ops/bench/launcher ~/.sigma-ops/bench/launcher-scratch
install -m 0700 evals/bench/launcher/sigma_bench_launcher.py ~/.sigma-ops/bench/launcher/
cp evals/bench/launcher/launcher.example.json ~/.sigma-ops/bench/launcher/sigma_bench_launcher.json
```

The script's first line is `#!/usr/bin/env -S python3 -B` (`-B` because Apple's python otherwise writes its startup
bytecode into the profile's `HOME`, which would make it non-empty; `env -S` needs macOS or GNU coreutils 8.30+).
Edit the JSON (it must sit beside the script with the same name and `.json`). Keys: `repo_root`, `hidden_root`,
`scratch_root` (required, see above), `deadline_seconds` (required, no default), `credential_var` (`null`,
`ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`) with `claude_path` (required; from `command -v claude`), `extra_env`
(for example `HTTPS_PROXY`, `NODE_EXTRA_CA_CERTS`, `SSL_CERT_FILE`; a proxy URL can itself hold a password),
`parent_marker` (default `bench.py`; null never signals a parent) and `real_home` (tests only: refused unless
`SIGMA_LAUNCHER_TEST_MODE=1`; leave it out). Unknown keys refuse.

## Dry run (starts `claude --version` only; no credential, no model)

Run it from outside your home directory (the launcher refuses a working directory under a `.claude` parent):

```
cd /tmp && ~/.sigma-ops/bench/launcher/sigma_bench_launcher.py --dry-run -- claude --version
```

It makes a fresh profile under `scratch_root`, runs the guards, prints the version and
removes the profile. It refuses any other command.

## First run (owner)

Only after you have read this file and the script and chosen a credential option; it spends real money. From the
repository root, add `--dry-run` to this same command first (the harness then checks its own inputs and the launcher's location and executable bit, and prints each arm's planned
facts; it neither runs the launcher nor reads its config, and the sigma arm's facts step runs `git`):

```
env -i PATH="$PATH" HOME="$HOME" SHELL="$SHELL" USER="$USER" LOGNAME="$LOGNAME" LANG="$LANG" TERM="$TERM" "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY" python3 evals/bench/bench.py run --manifest <tasks/manifest.json> --hidden-root <hidden-root> --results <results.json> --max-usd <ceiling> --scratch-root <outside-home> --isolation-launcher ~/.sigma-ops/bench/launcher/sigma_bench_launcher.py --arm all --model <pinned-model-id> --permission-mode <mode> --sigma-commit <sha> --deadline-seconds <seconds-above-deadline_seconds>
```

The key's value passes through `env`'s argument list for the instant before it starts `python3`. (With the subscription token, name `CLAUDE_CODE_OAUTH_TOKEN` instead of `ANTHROPIC_API_KEY`, in both places.) `<outside-home>` is an
empty directory outside your home that only you can read, for example one made with `mkdir -m 700` under a private
parent; it holds task copies and, during scoring, the hidden bundle, so it must not be world-readable. Your launcher
`scratch_root` is a different, persistent directory (not in `/tmp`, which a reboot clears and would lose the latch); it must
not be that harness scratch root or inside any run. Do not put it inside the harness scratch root either: the
harness refuses to start when that is not empty.

## Cost expectations

Measured figures only, from `docs/launch/evidence/cost-calibration.md` (2026-10-04, list prices, one run per row, no
variance known). These are **review** passes, not benchmark runs:

| Run | Dollars |
|---|---|
| review of unit A01 | $0.628 |
| verification of A01's findings | $0.415 |
| review of unit B01 | $0.502 |
| verification of B01's findings | $0.271 |
| A01 reviewed and verified | $1.044 |
| B01 reviewed and verified | $0.773 |

**The benchmark pilot is unmeasured.** No benchmark task has run, so there is no measured cost per arm, task or run,
and none of the figures above predicts one. Set `--max-usd` to a number you accept losing; the harness stops at it,
and `--belt-usd` bounds each `claude -p`.
