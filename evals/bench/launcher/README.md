# Benchmark isolation launcher

`sigma_bench_launcher.py` is the **operator-supplied isolation launcher** that `evals/bench/bench.py` requires
(`--isolation-launcher`). It is a stdlib-only, POSIX-only set of guards. **It is not a sandbox.** Read the limits
below, and read the script, before you trust it.

Status: written and tested only against a fake `claude` executable. No `claude -p` has ever been run through it,
against a model or otherwise. In particular no `claude -p` run authenticated by a subscription token has been observed
(see [Authentication](#authentication-the-subscription-owner-decision-2026-10-06)).

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
   against a real agent run: a missing variable shows up as a first run with no readable usage records, which stops the harness safely.
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
Because the harness discards stderr, **read `<scratch_root>/launcher-alerts.log`** when a run stops: every refusal is appended there, and the latch holds the cause of a 97. The harness itself will only say "no readable usage records (claude exit 2)" or "claude exit 97" (a missing token is refused by the launcher itself, naming the variable, on stderr and in the alert log).

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
  deleted before it is read), so the reported spend is a lower bound; on resume the in-flight marker counts such a pair as an unknown-token run.
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
  the old version, the credential is withheld and the first run stops with no readable usage records; name the stable path, pin the version,
  or add `DISABLE_AUTOUPDATER` to `extra_env`.
- **Hash cost** is two full hashes of the four paths per invocation (the launcher's before-hash, the supervisor's
  after-hash), one invocation per command. The harness's own
  measurement is 332 MB in about 2 s (`evals/README.md`); the launcher's cost on your plugin directories is
  unmeasured, so leave headroom in `deadline_seconds`.
- Never exercised against a real `claude`, a model, or a platform the tests did not run on.

## Authentication: the subscription (owner decision, 2026-10-06)

The owner has no API key and no API funds: the benchmark runs through headless `claude -p` on the owner's Max
**subscription** login. Because every run starts in a fresh, empty profile (so no login state is copied into an
agent-readable directory), the subscription login has to arrive as a token: create it once, interactively, with
`claude setup-token` (a long-lived OAuth token for the subscription; the Claude Code documentation fetched 2026-10-05
says it takes precedence over keychain credentials in `-p` mode). The launcher takes it as the one credential
variable, `CLAUDE_CODE_OAUTH_TOKEN`, and passes it only to the program whose real path is `claude_path`.

- **`credential_var` must be `CLAUDE_CODE_OAUTH_TOKEN`.** `null` is refused ("required") and `ANTHROPIC_API_KEY` is
  refused ("pay-per-token"): the same documentation says `ANTHROPIC_API_KEY` "is always used when present" in `-p` mode,
  so a stray key would silently move the benchmark from the subscription to per-token billing. The launcher builds the
  environment from nothing, so an API key in your shell never reaches `claude` either.
- **A `claude` model run refuses loudly when the token is missing or empty** (exit 2, alert log, the message names the
  variable, never a value). `claude --version`, the dry run and the scoring commands do not need it.
- **The token is the one secret, and it is never in a file or a log.** The config names the variable, not the value;
  the launcher never writes the environment anywhere. The token is a one-year credential: it reaches the agent's own tool
  subprocesses and the sigma arm's plugin hooks (unavoidable, as before), and the tasks pull external repositories, so
  **revoke or rotate it after the benchmark** (re-run `claude setup-token`, or revoke it in your account's settings).
- **Unmeasured:** that a token-authenticated `claude -p` in an empty profile reads the subscription's limits and writes
  usage records in the same shape as an interactive login. The first batch is the measurement: if it leaves no readable
  usage record the harness stops with "no readable usage records ... authentication is missing" before scoring anything.
- Copying your real login into the profile is **not supported**: it puts your real account in an agent-readable directory.

Tokens, not dollars, are what the harness meters and limits: dollars in a results file are **indicative** (the same
tokens priced at published list rates) and are never what a subscription is charged. See `docs/bench/token-budget.md`.

## Install

```
mkdir -p ~/.sigma-ops/bench/launcher ~/.sigma-ops/bench/launcher-scratch
install -m 0700 evals/bench/launcher/sigma_bench_launcher.py ~/.sigma-ops/bench/launcher/
cp evals/bench/launcher/launcher.example.json ~/.sigma-ops/bench/launcher/sigma_bench_launcher.json
```

The script's first line is `#!/usr/bin/env -S python3 -B` (`-B` because Apple's python otherwise writes its startup
bytecode into the profile's `HOME`, which would make it non-empty; `env -S` needs macOS or GNU coreutils 8.30+).
Edit the JSON (it must sit beside the script with the same name and `.json`). Keys: `repo_root`, `hidden_root`,
`scratch_root` (required, see above), `deadline_seconds` (required, no default), `credential_var` (required:
`CLAUDE_CODE_OAUTH_TOKEN`) with `claude_path` (required; from `command -v claude`), `extra_env`
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

Only after you have read this file and the script and created the subscription token. From the repository root, add
`--dry-run` to this same command first (the harness then checks its own inputs and the launcher's location and
executable bit, and prints each arm's planned facts; it neither runs the launcher nor reads its config, and the sigma
arm's facts step runs `git`). The run is **batched**: this command runs at most `--batch-pairs` task/arm pairs (default 3,
one task's three arms) and then stops cleanly. Run it again with `--resume` (and the same other flags) to continue, and
use `--resume` after any rate-limit stop; `docs/bench/token-budget.md` explains the cursor, the batch size and the stop rule.

```
env -i PATH="$PATH" HOME="$HOME" SHELL="$SHELL" USER="$USER" LOGNAME="$LOGNAME" LANG="$LANG" TERM="$TERM" "CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN" python3 evals/bench/bench.py run --manifest <tasks/manifest.json> --hidden-root <hidden-root> --results <results.json> --max-tokens <token-ceiling> --batch-pairs 3 --scratch-root <outside-home> --isolation-launcher ~/.sigma-ops/bench/launcher/sigma_bench_launcher.py --arm all --model <pinned-model-id> --permission-mode <mode> --sigma-commit <sha> --deadline-seconds <seconds-above-deadline_seconds>
```

The token's value passes through `env`'s argument list for the instant before it starts `python3` (and is visible to
`ps` for that instant): export it in your shell without typing the value on a command line, so it is not in your shell
history, and do not paste it anywhere else. `<outside-home>` is an
empty directory outside your home that only you can read, for example one made with `mkdir -m 700` under a private
parent; it holds task copies and, during scoring, the hidden bundle, so it must not be world-readable. Your launcher
`scratch_root` is a different, persistent directory (not in `/tmp`, which a reboot clears and would lose the latch); it must
not be that harness scratch root or inside any run. Do not put it inside the harness scratch root either: the
harness refuses to start when that is not empty (a crashed run leaves its run directory there; empty it deliberately).

Exit codes the harness gives a driving script: 0 (the batch finished or the whole run is complete; the JSON on stdout
says `complete`), 75 (the host reported a rate limit; wait for the reset it names, then run again with `--resume`), 76
(the token ceiling was reached; raising it is a recorded decision), 77 (a run exited non-zero with real work done and no
rate-limit record to explain it: it is not scored, because it could be an infrastructure fault; find the cause, then
`--resume` retries it), 78 (the run ended with not-run pairs that nothing else explains), 2 (refused: nothing was
started, or a stop with an `aborted` object in the results file). Every non-zero stop and every batch stop prints what to do on stderr.

## Cost expectations

Measured figures only. The first table is from `docs/launch/evidence/cost-calibration.md` (2026-10-04, list prices, one
run per row, no variance known); these are **review** passes, not benchmark runs, and the dollars are indicative (the
tokens priced at list rates), not a bill:

| Run | Indicative dollars |
|---|---|
| review of unit A01 | $0.628 |
| verification of A01's findings | $0.415 |
| review of unit B01 | $0.502 |
| verification of B01's findings | $0.271 |
| A01 reviewed and verified | $1.044 |
| B01 reviewed and verified | $0.773 |

The measured token counts of 24 headless validation sessions (22 Sigma loop sessions, 2 plain runs) and the ceiling
derived from them are in `docs/bench/token-budget.md`, with their stated uncertainty. **The benchmark itself is
unmeasured:** no benchmark task has run, so there is no measured cost per arm or task on the frozen set, and none of the
figures above predicts one. `--max-tokens` is checked between runs (the host has no per-run token cap, so one run can
overshoot, and the overshoot is counted); `--belt-usd` is the host's client-side per-run dollar estimate, a runaway
guard only.
