# `tools/cloud/` — this repository's own cloud environment

`setup.sh` is the setup script of `sigma-cloud`, the Claude Code cloud environment Sigma's maintainers
use to run Sigma's own goals: one session per issue, each running `/sigma-goal <N> --land` (#2090).
Sigma never runs it. For your own repository, read it as a worked example of
[cloud sessions](../../docs/cloud-sessions.md#cloud-environment-setup); its paths, repository and
verify command are this repository's.

**Status: measured in a real container (2026-10-29, and again for #2225 in 2026-11).** A diagnostic session
found setup's work in the session: the config below, the environment's verify approval, `python3` with pytest,
xdist and PyYAML, and git 2.56. Its verify failed tests that pass on a laptop; #2225 traced every one to a fact of
this container (see [The container](#the-container) and [How long a verify takes](#how-long-a-verify-takes)).
Everything else this page says the script does comes from its code and from `tests/test_cloud_setup_script.py`,
which runs it offline with stubbed tools.

A fresh clone of this repository cannot run the loop by itself: `.sdlc/config.json` is git-ignored
(`.gitignore` ignores `.sdlc/*` and re-includes plans, research and acceptance records), and so is
`.claude/`. The script builds the config from the installed Sigma's own template.

## What the script does

| Section | What | Why |
|---|---|---|
| 1 | git from the git-core PPA when the image's is older than 2.46, the key pinned by full fingerprint | the image ships 2.43, which this suite has never run on |
| 2 | pytest, pytest-xdist and PyYAML in every `python3` on PATH | verify and the planned-test checks run the first `python3` on PATH (#2079) |
| 3 | Sigma Loop from the marketplace, updated on every run | a cloud session does not install a repository's plugins |
| 4 | `.sdlc/config.json` from the template plus the choices below, rewritten on every run | the clone has none |

The choices in section 4:

- **Backlog:** GitHub issues in `Agrim-Intelligence/sigma` labelled `sdlc:goal` and assigned to the
  session's account (`@me`). The board is off (it needs GraphQL, which the cloud proxy refuses).
- **Ledger:** off, as on the maintainers' laptops. The claim is the `sdlc:in-progress` label alone.
- **Merging:** `work.auto_merge: protected` (`--land` refuses `off`), `work.require_review: approval`. In
  this repository a run still stops before the merge; see [Merging today](#merging-today).
- **Verify:** `verify.enforce: true`, `verify.timeout_minutes: 240`, and this command:

  `python3 -m pytest tests/ --ignore=tests/test_readiness_drills.py --ignore=tests/test_readiness_growth_audit.py --ignore=tests/test_b6_463.py --ignore=tests/test_mutation_sample.py --ignore=tests/test_onboarding_control.py --ignore=tests/test_shared_sdlc_paths.py -rsEf -q -n 2 -p no:cacheprovider`

  That is the suite minus the six files CI's nightly tier skips (`SLOW` in `.github/workflows/ci.yml`), on two
  workers. A goal's own planned tests still run in full, but no per-goal verify runs those six files: CI's weekly
  full tier is their only regular check. `tests/test_cloud_setup_script.py` fails when CI's list and this one
  differ.
  - **`-n 2`, not `-n auto`.** This suite's `tests/conftest.py` answers `-n auto` itself, with a quarter of
    available memory at 2000 MB per worker. In this container that is one worker, and the resource guard's grant
    never reaches it. On a container's first verify, a literal count is the only thing neither the conftest nor
    the guard lowers.
  - **Two, not three.** A later verify in the same container (after any fix, or a second goal) is admitted at
    that many workers only if they fit the room the guard leaves; otherwise it holds (see
    [If a verify holds](#how-long-a-verify-takes)). The room is the smaller of available memory minus 20% of the
    total, and 60% of the total: about 9.4 GiB here. The guard divides the first verify's peak by its worker
    count, so the room has to hold that whole peak. One worker's run peaked at 3.9 GiB. Doubling and tripling
    that gives estimates, not bounds: about 7.8 GiB for two workers, which fits, and about 11.7 GiB for three,
    which does not. Three is the next step once a two-worker peak is measured.
  - **`-rsEf`** lists skipped, erroring and failing tests in pytest's summary, failures last. Sigma keeps the
    last five lines of output as the run's evidence, so the evidence names up to four failing tests.
- **Model selection:** off, as on the maintainers' laptops: every phase runs on the session's model.
- **Left at the template's values on purpose:** the budget (a laptop's 500,000-token cap is checked only
  when a goal is picked, so it never stops a `--land` run) and `work.allow_secret_content` (empty; a
  laptop's entry exists for one goal's test fixture).

## Setting up the environment

1. At [claude.ai/code](https://claude.ai/code), open the environment selector, choose **Add cloud
   environment** and name it `sigma-cloud`.
2. **Network access:** the default list reaches GitHub and PyPI. Add `keyserver.ubuntu.com` and
   `ppa.launchpadcontent.net` under Allowed domains for section 1. A blocked host makes its step print a
   `WARNING` and setup still succeeds. Every network step is bounded: with every bound reached, setup takes
   about 8 minutes for git, 10 for each `python3` and 16 for Sigma. The host's own limit on setup time is
   not known.
3. **Setup script:** use the dialog's **Choose file** and pick `tools/cloud/setup.sh` from an up-to-date
   checkout of `main`. Never paste it from a rendered view (GitHub's preview, a chat message): rendered
   markdown drops `$` signs, and the script then fails every session.
4. **Environment variables:** one line, `SIGMA_TRUSTED_VERIFY_SHA256=<value>`. **Only the operator sets
   it**, never an agent and never in a repository file. It approves the verify command above in this
   repository and nothing else. It is a hash, not a secret. Compute it on your own machine, never copy it
   from a session's output: an agent can print any value. From an empty directory, with the verify
   command above pasted between the quotes:

   ```bash
   git init -q && git remote add origin https://github.com/Agrim-Intelligence/sigma.git && mkdir .sdlc
   python3 -c 'import json, sys; json.dump({"verify": {"command": sys.argv[1]}}, open(".sdlc/config.json", "w"))' "<verify command>"
   python3 -I "$(find ~/.claude/plugins/cache -path '*sigma-doctor/scripts/doctor.py' | sort -V | tail -1)" verify-approval .sdlc
   ```

   Start a new session after saving it; a running session keeps its old environment. When the verify
   command changes, every container refuses until the new value is set. No PAT and no `GH_TOKEN`: the
   cloud proxy supplies GitHub credentials.
5. **Check** in a new session:
   - `/sigma-doctor` loads and its trust row names the environment approval. If the row fails, fix the
     value; never apply the git-local `sigma.allowRepositoryShellCommands` fix doctor also offers, which
     would let every command the config names run unattended.
   - `git --version` is 2.46 or newer.
   - `python3 -c 'import pytest, xdist, yaml'` exits 0.
   - From the checkout, `python3 -c 'import json; print(json.load(open(".sdlc/config.json"))["verify"]["command"])'`
     prints the verify command above. What setup writes into the checkout reaches the session, because
     sessions start from setup's saved result: the first diagnostic session confirmed this on 2026-10-29. If
     the file is ever missing, the session started from a fresh clone instead, and no goal should run until
     that is understood.

## Merging today

The `basic` ruleset lets only its bypass actors push to any branch of this repository, `main` included,
so GitHub reports every pull request as `blocked`, to everyone. Maintainers merge through their bypass.
By default Sigma merges only a pull request GitHub reports as clean and never uses an account's bypass, so a
cloud run ends **parked, awaiting merge**: the PR is open, `test` is green and the run's own `sigma:approve` is
posted. A maintainer merges it, the PR's `Closes #N` closes the issue, and its `sdlc:parked` label is
removed by hand.

**Opt-in: merge through the account's bypass (#2092).** Setting `work.merge_bypass` to the exact string `ruleset` in
`.sdlc/config.json` lets `work.py merge` land that BLOCKED pull request itself, but only when four REST-read
conditions hold: every required check passed on the exact head; every rule on `main` comes from a ruleset whose
`current_user_can_bypass` is `always` for the token's account and no classic protection applies; the review gate
passed; the PR is mergeable and not behind where a strict check rule needs it. Anything unreadable parks and names
the condition. The merge line and a PR comment name the bypassed rules. It trusts the setting's owner, GitHub's
rules reads and the self-authored `sigma:approve` (#1195), and it is **UNMEASURED** whether GitHub's REST merge
honours a bypass for the cloud proxy's token, and until one real `--land` run in `sigma-cloud` ends merged (#2092's
AC-5), expect it may park with a refused merge. `tools/cloud/setup.sh` never sets it and the template does not
carry it; a person adds it, in the environment's config, deliberately. Details:
[cloud sessions](../../docs/cloud-sessions.md#merging-through-the-accounts-ruleset-bypass-2092).

## Running a goal

One issue per session. Before starting one, check that no other session or laptop loop holds that issue
(it carries no `sdlc:in-progress` label) and that nobody is fixing it by hand. Then send:

```
/sigma-goal <N> --land
```

Do not record acceptance with `--verify-command`. A goal's own verify command voids the environment's
approval, and the run then stops at verify (#2085). A goal whose tracked `.sdlc/acceptance/<N>.md`
already names one needs that line set to `null` first.

## What a run still needs a person for

- A passing verify. The 2026-11 baseline at `524d533` failed 28 tests and errored 5 here; #2225 fixes each
  cause in the repository (see [How long a verify takes](#how-long-a-verify-takes)). Until that lands, every goal
  here stops at verify.
- The merge itself, unless `work.merge_bypass` is set and works (see [Merging today](#merging-today)).
- A required check still pending after 450 s parks the merge (#2083). `test` took 3.2 to 5.5 minutes on
  the last twelve PRs, so a slow macOS runner queue can push it over.
- A PR with any line comment parks: review threads are GraphQL-only.
- A worker restart in the middle of a merge leaves a bare FAILED (#2086).
- The proxy refuses branch deletion, so merged `sdlc/<N>` branches stay on GitHub (#1682).
- Doctor's "base branch is not protected" warning is a false alarm here: rulesets protect `main` (#984).
- The only approval is the run's own `sigma:approve` comment (#1195).

## How long a verify takes

Measured once, on 2026-10-29 (Sigma 1.0.24, `main` at `6df8c2b`; 4 vCPUs, 15.7 GiB total, 15.2 GiB available).
The earlier command (`-n auto`, `-rs`) ran on one worker for 2h16m25s, and its run peaked at 3.9 GiB. Two
workers are not measured yet: at best about 70 minutes (half of 2h16m, assuming perfect scaling). Each
`sigma:block` fix-up cycle adds another verify.

That verify failed 18 tests. **Measured again for #2225**, the configured command above (`-n 2`) on unmodified
`main` at `524d533`, in this container: `28 failed, 25043 passed, 90 skipped, 9 xfailed, 107 warnings, 5 errors
in 3662.33s (1:01:02)`. Some serial pytest runs shared the 4 vCPUs for its first ~25 minutes, so the time is an
upper bound, not a clean two-worker figure. CI's nightly runs the same suite on a 3-core macOS runner, but it has
been stopped at its 120-minute limit every day since 2026-10-19 (#2099), so it gives no baseline.

Each failure family, its cause in this container, and its fix (all in #2225, none in the image):

| Family | Cause | Fix |
|---|---|---|
| tests that expect a file to be unreadable | the session runs as root with every capability (`CAP_DAC_OVERRIDE`), so mode `000` denies nothing | `tests/unprivileged.py` does the denied read as an unprivileged user (`setpriv`/`runuser`), and fails loudly when it cannot drop |
| tests that kill a timed-out process group | the group IS killed; `os.kill(pid, 0)` succeeds on a zombie, and PID 1 here reaps orphans on a ~2 s poll | `alive()`/`gone()` in `tests/unattended_support.py` read the process state (`/proc/<pid>/stat`, or `ps` without `/proc`) and treat a zombie as gone |
| "refuses without `flock`" tests | this Python builds `fcntl` in, so a `PYTHONPATH` `fcntl.py` shim is never imported | `without_module()` in `tests/unattended_support.py` runs the script with the module set to `None` in `sys.modules` |
| a hung-credential-helper control that could not hang | the environment injects `credential.interactive=false` through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, so git never runs askpass | `hermetic()` drops `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS` and every `GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*` |
| a signing park that quoted git, not the signer | git 2.56 prints `Could not apply <sha>... <subject>` after the signer's line | product fix: `commit_signing._detail_line` skips that narration |
| a descriptor test failing inside pytest | it faked the process-wide `os.fstat`, and pytest 9.2's fd capture calls `os.fstat` while the fake is live | the fakes answer only for the fake descriptor |
| the rest | merged drift (fixtures, inventories, prose budgets) and the collection memory guard, unrelated to the container | fixtures and inventories caught up; prose trimmed with no ceiling changed |

**If a verify holds.** When the guard holds a verify, `loop.py verify` exits 5 with no evidence. Here that most
likely means one of two things:
- the first two-worker run peaked above about 9.4 GiB;
- the guard stopped a run because memory ran low. A very fast allocation can meet the kernel's out-of-memory
  killer before that.

A hold does not last. Each held attempt records `loop.py`'s own small footprint as a verify's cost, and after
about two of them the guard drops the real measurement as an outlier and admits two workers anyway. From then on
the shed is the only protection. That is a Sigma bug: a held literal-count verify should record nothing. What a
`--land` run does after a verify holds is not specified; `docs/configuration.md` says to retry after the stated
wait. Either way, post the peak on #2090 so the worker count can be revisited.

## The container

What `sigma-cloud`'s sessions run on, re-measured for #2225 on 2026-11-15 unless marked. Each fact names how it was
read, so the next person can read it again.

| Fact | Value | Read with |
|---|---|---|
| user | `uid=0(root)`, every capability (`CapEff 000001fffeffffff`), `NoNewPrivs 0` | `id`; `grep CapEff /proc/self/status` |
| unprivileged users | `ubuntu` (uid 1000), `nobody` (65534); `setpriv` and `runuser` on PATH | `getent passwd ubuntu nobody`; `which setpriv runuser` |
| OS, kernel | Ubuntu 24.04.5 LTS; `Linux 6.18.44-fc-v470` (a Firecracker microVM); cgroup v1; no Yama | `/etc/os-release`; `uname -srm`; `/proc/self/cgroup`; `ls /proc/sys/kernel/yama` |
| PID 1 | `process_api`; it reaps orphans on a ~2 s poll, not on `SIGCHLD` (an orphan zombie lived 1.2 to 1.9 s, 5 samples; measured once, 2026-11) | `cat /proc/1/comm`; poll `/proc/<pid>/stat` of an orphaned `sleep` |
| CPU, memory | 4 vCPUs; 16,087 MiB total; no swap; open files limit 20000 | `nproc`; `free -m`; `cat /proc/swaps`; `ulimit -n` |
| Python | 3.13.18 (Clang 23), libpython linked into the executable, `fcntl` built in; pytest 9.2.0, xdist 3.8.0 | `python3 -c "import sys; print('fcntl' in sys.builtin_module_names)"`; `ldd "$(readlink -f "$(which python3)")"` |
| git | 2.56.1; the global config signs every commit (`commit.gpgsign=true`, `gpg.format=ssh`, `gpg.ssh.program=/tmp/code-sign`) | `git --version`; `git config --global --list` |
| git config from the environment | `GIT_CONFIG_COUNT=3`: `credential.interactive=false` and two `url.https://github.com/.insteadOf` | `git config --show-origin --list \| grep '^command line'` |
| checkout | a fresh container's checkout can be a detached HEAD (reported on #2225; not re-seen in this run). `git checkout -B main origin/main` puts `main` back on the remote's tip | `git status -sb` |
| session limits | a background command is stopped at its timeout, 30 minutes unless the call asks for more (up to 2 hours), and the full verify takes about 61 minutes: give it the longer timeout or it is cut short | the timing above |

## How to diagnose a verify that fails only here

1. Read the failing test's own assertion first, then ask which container fact above it touches. Root, PID 1's
   reaping, a built-in module, and environment-injected git config each looked like a product bug before they were
   traced.
2. Run the one test serially (`python3 -m pytest -p no:cacheprovider -q --tb=long <id>`), never the whole suite
   for a single cause.
3. Before fixing, show the cause with the smallest reproduction you can (a ten-line script beats an argument), and
   save its output beside the fix.
4. Fix the test's hermeticity when the container makes the test's premise false; fix the product when the
   container shows a real defect (the signing park above). Never skip, deselect or add to `verify.known_flakes`.
5. Break the fix once and watch the test go red before trusting it green.
