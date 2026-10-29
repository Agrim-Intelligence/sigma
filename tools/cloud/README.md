# `tools/cloud/` — this repository's own cloud environment

`setup.sh` is the setup script of `sigma-cloud`, the Claude Code cloud environment Sigma's maintainers
use to run Sigma's own goals: one session per issue, each running `/sigma-goal <N> --land` (#2090).
Sigma never runs it. For your own repository, read it as a worked example of
[cloud sessions](../../docs/cloud-sessions.md#cloud-environment-setup); its paths, repository and
verify command are this repository's.

**Status: measured once in a real container (2026-10-29); no goal has run here yet.** A diagnostic session
found setup's work in the session: the config below, the environment's verify approval, `python3` with pytest,
xdist and PyYAML, and git 2.56. Its one verify failed 18 tests (see
[How long a verify takes](#how-long-a-verify-takes)). Everything else this page says the script does comes from
its code and from `tests/test_cloud_setup_script.py`, which runs it offline with stubbed tools.

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
  - **Two, not three.** A later verify in the same container (after any fix, or a second goal) runs only if that
    many workers fit the room the guard leaves: the smaller of available memory minus 20% of the total, and 60%
    of the total. That is about 9.4 GiB here. The guard divides the first verify's peak by its worker count, so
    the room has to hold that whole peak. One worker's run peaked at 3.9 GiB. Doubling and tripling that gives
    estimates, not bounds: about 7.8 GiB for two workers, which fits, and about 11.7 GiB for three, which does
    not. Three is the next step once a two-worker peak is measured.
  - **`-rsEf`** lists skipped, erroring and failing tests in pytest's summary, failures last. Sigma keeps the
    last five lines of output as the run's evidence.
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
Sigma merges only a pull request GitHub reports as clean and never uses an account's bypass, so a cloud run
ends **parked, awaiting merge**: the PR is open, `test` is green and the run's own `sigma:approve` is
posted. A maintainer merges it, the PR's `Closes #N` closes the issue, and its `sdlc:parked` label is
removed by hand. #2092 tracks an opt-in setting to merge through the account's bypass instead.

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

- A passing verify, today. The 2026-10-29 verify failed 18 tests at `6df8c2b` in this container (see
  [How long a verify takes](#how-long-a-verify-takes)). Until they are fixed, every goal here stops at verify.
- The merge itself, today (see [Merging today](#merging-today)).
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

That verify failed 18 tests. By name they fall into three groups:
- tests that expect a file to be unreadable (the session runs as root, which can read any file);
- `flock` tests;
- tests that kill a timed-out process group.

None is confirmed yet. CI's nightly runs the same suite on a 3-core macOS runner, but it has been stopped at its
120-minute limit every day since 2026-10-19 (#2099), so it gives no baseline.

**If a verify holds.** When the guard holds a verify, `loop.py verify` exits 5 with no evidence. Here that most
likely means one of two things:
- the first two-worker run peaked above about 9.4 GiB;
- the guard stopped a run because memory ran low. A very fast allocation can meet the kernel's out-of-memory
  killer before that.

Either way, the guard records the peak, and every later verify in that container holds. Waiting does not clear
it. What a `--land` run does after a verify holds is not specified, so expect it to stop or park. Start the goal
again in a new session, and post the peak on #2090 so the worker count can be revisited.
