# security review · 2066-plan (environment approval for the verify path, `SIGMA_TRUSTED_VERIFY_SHA256`)

Reviewed: `.sdlc/plans/2066.md`, `.sdlc/research/2066.md`, against the code at `dcfde5f` in this worktree. Independent,
author-blind. North star: `north_star.py` printed `absent`, so there were no declared non-negotiables to check;
AGENTS.md's five properties were used instead.

## summary
0 critical · 0 high · 5 med · 5 low (+3 info)
ready to ship: not without remediation. The plan can go ahead once S1 to S5 are folded into it. S1 can instead be
accepted as a recorded decision, with the rationale written in the plan's Risks.

No case in the plan's failure table fails open as written: the strings that are hashed are the strings that run.
Five gaps remain. The approval is not tied to a repository (S1). A repository can set the variable through host
config it commits (S2). The re-approval prompt is blind (S3). The documented hash gesture is itself a code-execution
path (S4). And no test pins the one call-site slip that would fail open (S5).

## entry points
- `loop.py verify <sdlc> <goal>` → `_verify_goal_body` gate at `skills/sigma-loop/scripts/loop.py:6535`. auth: none (local
  CLI, operator or agent shell). authz today: git-local key on `root` (`shell_policy.py:29-36`). Planned: git-local
  OR an environment value equal to sha256 of every string in `commands` (`loop.py:6466`, executed at `loop.py:6549`
  or `loop.py:6566-6567`).
- `loop.py red <sdlc> <goal>` → gate at `loop.py:6392`. authz: as above. Planned: the hash of
  `declared_verify_commands`. Note that red never runs those strings. It runs `python3 -m pytest` on the planned
  nodes (`red_green.py:113-114`, `red_green.py:184-185`).
- Process environment `SIGMA_TRUSTED_VERIFY_SHA256` (new input). Writers: the host's environment settings, the agent's
  shell, shell profiles, CI env, setup scripts, and also host config the repository commits (S2).
- `doctor.py` trust row (`skills/sigma-doctor/scripts/doctor.py:4459-4470`) and its parity copy (`doctor.py:175-186`).
  Output only, but it becomes the place the approval value comes from (S3).
- `init_flow._verify_trust_lines` (`skills/sigma-init/scripts/init_flow.py:498-523`): output only.
- Documented hash gesture in `docs/cloud-sessions.md` (planned; research line 73, plan test 20). The operator runs it
  inside the checkout being approved (S4).
- Not entry points, and confirmed to stay on the git-local grant: the 12 other `repository_shell_commands_allowed`
  call sites (`autowatch.py:885,1046`, `backlog_check.py:812`, `bounded_run.py:470`, `loop.py:4750`, `pipeline.py:68`,
  `reviewer.py:1066`, `work.py:4078`, `verify_merge.py:122`, `init_flow.py:506` before the swap), plus the separate
  copies in `risk-detect.sh:139` and `kg.py:412-415`.

## findings

[S1] severity:med — authz / design (A01, A04) — `shell_policy` env predicate (planned); plan lines 12, 20-22, 47, 83 —
The approval binds command TEXT only, not repository, checkout or directory. The plan admits the reach is "every
checkout in the environment" and that the same string in any other repository is trusted (plan:20-22). Its only
control is prose: "Set it only in an environment dedicated to the repository" (plan:22). Nothing enforces that.
The common verify strings (`make test`, `pytest -q`, `npm test`, `bash scripts/verify.sh`) recur across unrelated
repositories, so one environment used for a second repository silently trusts that repository's code. The same
unbinding also does three other things:
- it grants outside a Git worktree (plan:83), removing a property the doctor states today ("this is not a Git
  worktree, so it can never be trusted", `doctor.py:4467-4469`);
- it ignores `root`, which comes from agent-written state or a config-derived path (`work.py:1796-1817`);
- it ignores the `sdlc_dir` argument, which picks any config in the environment (`loop.py:6463`).
AGENTS.md SAFETY asks for "refuses loudly rather than proceeding weakly". —
fix: bind the value to the repository as well as the command, for example
`sha256("sigma-verify-trust/v1\0" + <owner>/<repo> + "\0" + command)`. Take `<owner>/<repo>` from the last two path
segments of `git -C <root> config --get remote.<work.remote or origin>.url`, which is local git metadata that a
repository's committed content cannot set. If there is no remote or `root` is not a Git worktree, refuse with a
`no-identity` reason, which restores the outside-Git refusal. The doctor computes and prints the value where it runs.
The proxy remote shape in a cloud checkout is UNVERIFIED (`docs/cloud-sessions.md:615-620`), so measure it before
relying on the segment parse. Minimum fallback if binding is rejected: an explicit git-local `false` becomes a
per-checkout deny that the environment value cannot override, and the rejection is recorded in the plan's Risks.

[S2] severity:med — authz / entry point (A01, A05) — plan:40-42 ("A repository cannot grant it through committed
files") — That claim is false for the plan's own target host, and it is about to be copied into
`docs/threat-model.md`. Hosts fill the process environment from repository-committed files:
- Claude Code's `.claude/settings.json` `env` key;
- a project SessionStart hook writing to `$CLAUDE_ENV_FILE`;
- devcontainer `containerEnv` / `remoteEnv`;
- direnv `.envrc`;
- mise `[env]`.
This is per Claude Code's and those tools' documented behaviour; I did not exercise it in this review. Most of
these hosts also run repository hooks, so the extra capability is usually small. But an organisation that turns
off project hooks through managed settings still has the declarative `env` applied (UNVERIFIED for the current host
version). There, a repository authorises its own shell, which is exactly what `shell_policy.py:3-11` and #422
forbid. —
fix: (a) correct the Exposure text and the TM-04 row: "Sigma reads no file for it, but a host that applies
repository-committed environment lets that repository set it". (b) Add a cheap fail-closed check. When a tracked
file in `root` or the project root, from a closed list, contains the literal variable name, refuse the environment
grant with reason `repository-sourced`. The list: `.claude/settings.json`, `.devcontainer/**`, `.envrc`, `.env`,
`.env.*`, `mise.toml`, `.mise.toml`, `.codex/**`, `.cursor/environment.json`. The check is one
`git -C <root> grep -l -F SIGMA_TRUSTED_VERIFY_SHA256 -- <list>` call, costing O(size of those files). Add a test
and a control: drop the check and the test goes red. Say in the docs that a hook which builds the name at runtime
evades the check, and that such a hook is already code execution.

[S3] severity:med — integrity of the approval (A04) — plan:104-107 and test 13 (plan:154) — On a failing row,
including `mismatch`, the doctor prints `SIGMA_TRUSTED_VERIFY_SHA256=<sha256 of verify.command>`. Test 13 pins that
it never shows the command. So the operator copies an opaque hash, computed in the possibly untrusted checkout from
whatever `verify.command` is there now. The plan's tripwire, "survives a changed command: no" (plan:48), becomes
"paste the new value the doctor offers", with no prompt to look at what changed. Today's git-local hint at least
says "inspect the verify command in .sdlc/config.json" first (`doctor.py:4463`). —
fix: every hint that carries a hash also prints the hashed command(s) on their own line. JSON-escape them with
`_vd.printable` (the precedent is `init_flow.py:510`), pass them through `scrub` for token-shaped values, and keep
them out of the gesture line. Add the words "inspect it; this value approves exactly this text". For `mismatch`, add
"the command changed since it was approved". Change test 13 to assert that the command appears only as a
JSON-escaped standalone line, and never inside the `SIGMA_TRUSTED_VERIFY_SHA256=` line.

[S4] severity:med — injection / documented gesture (A03, A08) — research:73 and plan test 20 (plan:161, "a
documented stdlib one-liner") — The operator will run the hash one-liner inside the checkout being approved, because
it reads `.sdlc/config.json`. `python3 -c` puts the current directory first on `sys.path`, so a repository-planted
`json.py` or `hashlib.py` runs on the operator's machine at the moment of inspection. It can also print the hash of a
different command from the one inspected. Measured here: a planted `json.py` printed `PLANTED json.py EXECUTED`
under `python3 -c 'import json,...'`. The same one-liner with `python3 -I -c` did not run it and printed the hash.
(Scratch at `<scratch>`.) —
fix: document `python3 -I -c '...' <path-to-config.json>`, which ignores the current directory and the
`PYTHON*` variables, with the path passed as an argument. Have it print the JSON-escaped command beside the hash.
In test 20, run the documented text in a temp project that also holds a planted `json.py` and `hashlib.py`, each
writing a sentinel, and assert that no sentinel appears. Control: remove `-I` and test 20 goes red. Also add a
`{workers}` token to test 20's command (see S11).

[S5] severity:med — test adequacy of the core binding (failure mode, A04) — plan tests 5, 9 and 11 (plan:146, 150,
152); the control table (plan:181-197) — The plan's central claim is "every command that path is about to run" must
match. It is tested only at the policy layer (test 5). The loop tests vary only `verify.command`. Suppose the gate
is wired with `[config["verify"]["command"]]`, or with the `cmd` label (`state.py:1651` returns a JSON array string
when there are several commands), instead of `commands` (`loop.py:6466`). Every listed test still passes, and two
cases fail OPEN:
- (a) an acceptance record adds a second command (`state.py:1640-1643`). Acceptance records are committed
  repository content (`.sdlc/acceptance/` is tracked, `.gitignore:20`);
- (b) in local mode, goal frontmatter `verify_command` REPLACES the repository command (`state.py:1628-1634,1644`),
  and the frontmatter string then runs under an approval of the repository string. —
fix: add three loop-level tests, each writing a sentinel:
- `verify_goal` with an acceptance record carrying a second command, and the environment value set to
  sha256(repo_cmd), gives exit 2 and neither sentinel;
- a local-mode `.md` goal whose frontmatter `verify_command` differs, with the same environment value, gives exit 2
  and no sentinel;
- `red_goal` on both setups gives exit 2 and pytest is not invoked.
Controls: change the gate argument to `[verify.command]`, and the first two go red. Make `red` compute its commands
from `stem` instead of `goal`, which skips the frontmatter (`state.py:1627-1628`), and the third goes red.

[S6] severity:low — failure modes — plan:75-76 and plan:98-99 — The failure table says a config that cannot be read
gives "verify: exit 2 before the gate". In fact `state.load_config` raises `ConfigMissing` (a plain `Exception`,
`state.py:19`) or `ConfigInvalid` (`state.py:36`) at `loop.py:6463`, outside the `try`. That is an uncaught exception
(`loop.py:6372-6375`): it fails closed, but it is not exit 2. `red_goal` loads no config today (`loop.py:6386-6394`).
The planned `[]`-on-error path covers only `OSError`/`ValueError`, so `ConfigMissing` would propagate. That still
fails closed, but no test pins the red error path at all. —
fix: correct the table; in `red`, catch `state.ConfigMissing` and `ValueError`/`OSError` around both the config load
and `declared_verify_commands`, so either becomes `[]`. Add a test where an invalid acceptance record under a
matching environment value makes `red` refuse with exit 2. Control: fall back to `[repo_cmd]` on error, and the
test goes red.

[S7] severity:low — design honesty (A04) — plan:12-14 and plan:46 ("only the exact approved string") — Besides the
command and `observe`, the verify path runs repository code through Sigma's own fixed argv:
- `pytest --collect-only` on the planned selectors (`red_green.py:113-114`);
- three flake re-runs (`loop.py:6032,6042`; `flake_check.py:243`);
- a diff-revert run in a scratch worktree at the fork point (`loop.py:6183`; `diff_revert.py:334,357`);
- `python3 -c "import xdist"` with `cwd=root` (`flake_check.py:144`), which imports a repository-root `xdist.py`
  if one exists.
An operator who approves an inert string (say `ruff check .`) still gets the repository's code run. The binding is
a tripwire on one config string, not a limit on what executes. The plan says text is not content (plan:15-19), but
the comparison table overstates. —
fix: reword the table row and the docs to "approves running this repository's code at the checked-out branch,
through the command and through Sigma's own pytest runs, while the configured string is unchanged", and list the
four runs.

[S8] severity:low — secrets / echo (A02) — plan:39 and test 10 (plan:151) — "The loop's refusal text never prints the
value to set" has no test. Test 10 asserts only that the environment value is absent. If
`verify_refusal_message(commands)` printed `command_sha256(cmd)`, every agent reading loop stderr would get a
copy-paste self-grant. —
fix: test 10 also asserts that neither `command_sha256(c)` nor `c` appears for each command `c`. Control: add the
hash to the message, and the test goes red.

[S9] severity:low — input validation — `legacy.py:109-122` — If the variable is read through `legacy.getenv`, or
registered in `FALLBACK_ENV`, two things change. the retired-prefix spelling of the variable would also grant. And
`SIGMA_TRUSTED_VERIFY_SHA256=""` would count as unset and fall through to the retired spelling, turning the planned
`empty` refusal into a grant. `POST_RENAME_ENV` (`legacy.py:49-53`) is the right registry, but no control pins it. —
fix: `shell_policy` and the doctor read `environ.get(TRUSTED_VERIFY_ENV)` directly. Add a test that a matching
value under the retired prefix grants nothing, and that an empty Sigma value next to a matching retired value gives
`empty`. Control: move the name to `FALLBACK_ENV`, and the test goes red.

[S10] severity:low — authn ceiling / auditability — plan:28-39 and test 18 (plan:159) — No technical control can
tell an operator's environment panel from an agent's `export` under the same uid. The plan says so, but four things
weaken its procedural control:
- the ratchet skips `AGENTS.md`, `CLAUDE.md` and the docs that AGENTS.md sends agents to: `AGENTS.md:175` links
  `docs/cloud-sessions.md`, which is where the gesture goes;
- the loop's refusal names the variable;
- the doctor hint names it;
- TM-01 (prompt injection, high, no mitigation, `docs/threat-model.md:40`) means an injected agent is a realistic
  writer.
—
fix: write the ceiling into the TM-04 residual the way TM-11 states its own (`docs/threat-model.md:50`).
Put the gesture in a section that AGENTS.md does not route agents to, or say plainly that agents can read it. Record
`trust_source` (`git-local`, `environment`, or both) and the first 12 hex characters of the approval in the verify
evidence (`loop.py:6645-6657`) and the PR body, so a reviewer can see that a goal was verified under environment
trust.

[S11] severity:info — the hashed text versus the executed text — `resource_verify.py:90-94,269` — The resource path
replaces `{workers}` with an integer before the shell sees it. What is hashed is the declared template; what runs
differs from it only by that integer. This is not a fail-open (`substitute_workers` accepts positive ints only),
but the docs and the doctor must hash the raw configured string.

[S12] severity:info — the research's open question ("a planned node id with a leading `-`") is RESOLVED.
`red_green.selectors` refuses any selector whose file part starts with `-` or `/`, or that contains whitespace,
`;`, `|` or `&` (`red_green.py:89-92`). Collected nodes must extend a selector (`red_green.py:107-108,116`).

[S13] severity:info — positive: for goal-supplied commands the environment path is STRICTER than git-local. An
acceptance-record or frontmatter command that is not byte-identical to the approved string is refused
(`state.py:1640-1644` with the plan's all-match rule), where git-local runs it. Strings and the `commands` list are
immutable between the gate (`loop.py:6535`) and execution (`loop.py:6549/6567`). The auto-rebase in
`work.ensure_fresh` (`loop.py:6500`) runs before the gate, and `commands` was captured before it (`loop.py:6466`),
so there is no string TOCTOU. Every pytest execution site is downstream of the two gates: the only callers of
`observe`, `collect`, `flake_check.check` and `diff_revert.run` are `loop.py:6032-6183,6414,6575`.

## checklist trace
1. authn — none at the CLI; the caller is whoever controls the process environment, operator or agent alike (S10).
   A repository can also be that caller through host config it commits (S2).
2. authz — the new grant is confined to `loop.py:6392` and `loop.py:6535`; the 12 other sinks keep git-local only
   (verified with the `git grep` above). But it is bound to no repository, checkout or directory (S1). The call-site
   argument that makes it "every command" is unpinned (S5).
3. input validation — strict `fullmatch` on `[0-9a-f]{64}` with no trimming is right: it fails closed, and a
   multi-value string cannot partly match. Read it directly from `environ`, never through `legacy.getenv` (S9).
   Non-str and unencodable commands give `error`.
4. injection surfaces — the documented hash gesture loads repository modules through `python3 -c` (S4, measured).
   Command execution itself is unchanged (`shell=True` on the configured strings, `loop.py:6567`).
   Pytest argv injection is refused (S12).
5. PII / secrets / crypto — sha256 of public text, not a secret, and no constant-time comparison is needed. The env
   value is never echoed (planned). The doctor's printed hash of a command that carries a token allows an offline
   guess, but the ledger already records `command_sha256` (`loop.py:6665`). Add `scrub` to the command display
   proposed in S3. Loop stderr must not carry the value to set (S8).
6. rate / cost — O(1) per gate: one env read and one sha256 per command. The S2 check adds one `git grep` over at
   most ~10 small paths, and S1 adds one `git config --get`. Neither grows with repository size.
7. dependencies & integrity — no new packages. The approval's integrity depends on what the operator is shown when
   they approve (S3, S4). The repository's code still runs under the approval by design (S7).
8. failure modes — every row of the plan's failure table fails closed against the code. Two corrections: an
   unreadable config is an uncaught exception, not exit 2, and the red error path has no test (S6). The only
   fail-open found is an unguarded implementation slip at the call site (S5).
9. configuration — the default is unset, which leaves git-local deciding. The unsafe operational default is that one
   environment can serve several repositories (S1). The doctor hint invites blind re-approval (S3).
10. outbound requests — n/a: the change fetches nothing. The env value is read locally and git metadata is read
    locally (S1's remote URL is only parsed, never contacted).
