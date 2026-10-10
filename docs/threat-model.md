# Threat model

Sigma runs hooks and scripts on developer machines, can read and write GitHub
state, and can start optional background workers. This model states what it
protects, where untrusted data enters, and the mitigation actually present at
the cited source location. It is a security inventory, not a claim that every
risk has been eliminated.

## Assets

1. The user's source code and git history.
2. GitHub state: issues, PRs, branches, board, labels.
3. Credentials: the `gh` token, `SIGMA_SLACK_BOT_TOKEN`, `SIGMA_SMTP_PASS`, and
   the host's model credentials.
4. The developer machine: processes and files.
5. The user's model spend.

## Trust boundaries

| boundary | crossing and entry points |
| --- | --- |
| T1 | Issue, PR, and comment prose enters goal prompts and parsers: `skills/sigma-loop/scripts/blocker_scan.py:130`, `skills/sigma-loop/scripts/comment_watch.py:88`, `skills/sigma-loop/scripts/features.py:356`, `skills/sigma-loop/scripts/review_context.py:49`, and `skills/sigma-loop/scripts/slack_commands_listen.py:524`. |
| T2 | Repository content enters commands through configured checks: `skills/sigma-loop/scripts/pipeline.py:68`, `skills/sigma-loop/scripts/backlog_check.py:791`, `skills/sigma-loop/scripts/loop.py:5222`, `skills/sigma-rebase/scripts/verify_merge.py:122`, and `skills/sigma-loop/scripts/reviewer.py:239`. |
| T3 | Host hooks execute at prompts and tool calls: `hooks/hooks.json:3`, repo adoption is checked by `hooks/sigma_gate.sh:20`, and session-start's watcher/doctor diagnostic layer begins at `hooks/session_start.sh:131`. |
| T4 | Network destinations are GitHub, Slack, a configured webhook, SMTP, and the host's model process, enumerated in `docs/privacy.md:9`. |
| T5 | Background boundaries include detached watcher startup `skills/sigma-loop/scripts/loop.py:75`, the watcher loop `skills/sigma-loop/scripts/watch_daemon.py:906`, Slack listener lifecycle `skills/sigma-loop/scripts/slack_commands_listen.py:757`, and model subprocesses `skills/sigma-loop/scripts/autowatch.py:915`. |
| T6 | Local policy comes from managed settings `skills/sigma-loop/scripts/managed_settings.py:237` and the direct-edit sentinel is described in `docs/enforcement.md:54`. |
| T7 | Model-spawned sessions include `skills/sigma-loop/scripts/feature_judge.py:155`, `skills/sigma-loop/scripts/autowatch.py:915`, and `skills/sigma-loop/scripts/supervise_daemon.py:1`. |

## Threats and current mitigations

`none` means no current technical mitigation at this crossing. Medium and high
`none` rows point to an open GitHub issue; those issues are implementation work,
not evidence that the risk is already fixed. STRIDE letters are S (spoofing), T
(tampering), R (repudiation), I (information disclosure), D (denial of service),
and E (elevation of privilege).

| id | boundary | threat (STRIDE letter) | example | existing mitigation (file:line) or `none` | residual risk low/med/high | issue |
| --- | --- | --- | --- | --- | --- | --- |
| TM-01 | T1 | Prompt injection (E) | An issue asks a phase agent to execute a command or edit labels. | none | high | #362 |
| TM-02 | T1 | Phantom blocker write (T) | A confident `_BLOCK_RE` match writes a blocker edge to a third issue. | none | high | #362 |
| TM-03 | T1 | Parser confusion (T) | A hostile `Feature:` marker or Slack argument changes routing. | none | med | #362 |
| TM-04 | T2 | Repository-supplied value reaches a process or a file (E) | Any repository-supplied value that reaches a process or a file: a configured shell string or executable (`verify.command`, `review.command`, `knowledge_graph.builder`, `autowatch.drive_cmd`, `.sdlc/risk-detect.conf`), a git remote or branch that becomes an option (`work.remote`, `work.base`, `ledger.remote`, `ledger.branch`), an SMTP host or password variable (`notify.email`), and a committed symlink under `.sdlc/` that a write follows (#708). | `shell_policy.py` refuses repository-configured commands until an operator sets the Git-local opt-in at every sink above; a leading `-` in a git remote or branch is refused; `state.safe_state_open` refuses symlinks under `.sdlc/`; repo SMTP needs `SIGMA_ALLOW_REPO_SMTP=1` | med | #422, #707, #708, #710 |
| TM-05 | T3 | Hook-triggered command execution (E) | A host invokes a configured hook on a prompt or tool call. | `hooks/sigma_gate.sh:20` | med | — |
| TM-06 | T4 | Credential exposure in logs (I) | A token-shaped value reaches a command or comment diagnostic. Coverage of every `gh` comment site is unverified. | `skills/sigma-loop/scripts/scrub.py:150` | med | — |
| TM-07 | T4 | Remote webhook egress (I) | `channel_webhook_url` names an Internet host instead of the documented local adapter. | none | high | #358 |
| TM-08 | T5 | Stale watcher appears idle (D) | A dead watcher reports no errors and is mistaken for an idle one. | `hooks/session_start.sh:131` | med | — |
| TM-09 | T5 | Listener cleanup omission (D) | SIGTERM or SIGHUP leaves Slack pid, heartbeat, and lock markers, preventing restart. | `skills/sigma-loop/scripts/slack_commands_listen.py:806` | low | — |
| TM-10 | T5 | Orphaned model descendant (D) | A timed-out, signalled, or SIGKILLed autowatch tick leaves a model descendant running. Residual: a descendant that calls `setsid` itself escapes the group, and a SIGKILL of the lifeline sentinel removes the backstop for a later SIGKILL of the tick. | `skills/sigma-loop/scripts/autowatch.py:772` terminates the driven session's whole process group (SIGTERM, grace, SIGKILL), a lifeline sentinel repeats that if the tick dies, and hosts without process groups refuse | low | #425 |
| TM-11 | T6 | Managed-policy deletion (T) | Removing the file makes an enrolled checkout read as unadopted. | `skills/sigma-loop/scripts/managed_settings.py:237` refuses an enrolled checkout whose file is gone, via markers outside the file. Residual: a checkout writer who also deletes the markers or runs `unenroll` is not bound (README, Managed settings) | med | — |
| TM-12 | T6 | Direct-edit bypass (T) | A local sentinel bypasses a hard plan gate when the key is not organization-locked. | `docs/enforcement.md:54` | med | — |
| TM-13 | T7 | Unbounded model spend (D) | A spawned model process spends beyond a sensible per-invocation budget. | `skills/sigma-loop/scripts/feature_judge.py:167` | low | — |
| TM-14 | T7 | Unsafe Slack merge (E) | `--unsafe-merge` removes interactive confirmation from a merge request. | `skills/sigma-loop/scripts/slack_commands_listen.py:1353` | med | — |

## Operational reading

The mitigations above are deliberately narrow. For example, `scrub.py` redacts
known patterns but does not prove that every GitHub comment call is scrubbed;
watcher restart and stale detection reduce a liveness gap but do not make a
stopped process healthy. The linked follow-ups retain the owning acceptance
criteria and must be independently verified before this document can describe
their mitigation as present.

## Repository-configured shell commands

Pipeline checks, backlog embedders, and verification commands are repository
input. Sigma refuses them by default, including in a freshly cloned or adopted
checkout. After inspecting a project an operator may enable the trusted-project
compatibility path with:

```sh
git -C <trusted-project> config --local sigma.allowRepositoryShellCommands true
```

The setting is Git-local rather than `.sdlc/config.json`, so it is neither
committed nor supplied by a clone. It intentionally restores shell semantics
for every linked worktree of that trusted project; do not set it for a checkout
whose repository configuration you have not reviewed.

`/sigma-init`'s explicit `verify_detect.py confirm` and `set` gestures record
the same Git-local setting only after the operator confirms or supplies the
command. They refuse to enable a command outside a Git worktree. `decline` and
initial scaffolding never grant it.

## Credential-shaped lines at `work.py commit`

`work.py commit` stages the goal worktree with `git add -A` and refuses on two axes before anything
reaches a branch. This section is the operator's reference for both. The loop agent's rule is shorter
and is printed by the refusal itself: remove the literal or park the goal, never write an allowlist
entry.

**Secret-shaped names (#1555).** The name refusal covers `.env` and its variants,
`*.pem`/`*.key`/`*.p12`/`*.pfx`/`*.jks`/`*.keystore`, `id_rsa` and its siblings, `credentials.json`
and service-account JSON. The public half of a keypair and a staged deletion are never refused. A
repo that already ignores its `.env` never sees this refusal, because `git add -A` honours
`.gitignore`; `/sigma-doctor` reports the still-unignored ones before the loop's first commit.
`work.allow_secret_paths` clears an exact NAME, never content.

**Added content.** Every added staged line is matched against the commit shapes in `scrub.py`. For
the anchored `credential-assignment` rule (a credential-named key, then `:` or `=`, then a run of 4
or more characters) a gate-only filter (#961) judges the value, failing closed. The value it judges
is the rule's own run, which ends at the first space, quote, `<`, `>` or `&`; anything after that run
is judged only by the two line-wide checks below (a secret-like token, an assigning `=` that starts
a literal):

- Refused in every file: a quoted value directly after the separator; a bare identifier or a
  number as the whole value; a `${...}` or `$(...)` template; any line with an assigning `=` after the separator whose
  right side starts a quoted literal of any length or wraps onto the next line (an annotated,
  chained or keyword-argument default); and any line with a secret-like token after the separator
  (a known provider prefix, 3 or more letter/digit alternations, a run of 24 or more letters and
  digits, or 6 or more characters mixing letters and digits outside an upper-snake name).
- Passes in a code file only: an expression (a call, an attribute or subscript read) and, after
  `:`, a type name from a closed set (`str`, `bytes`, `bool`, `int`, `float`, `Any`, `SecretStr`,
  `string`, `boolean`, `number`, `any`, `unknown`, `String`, or a `|` union of them with `None`,
  `null` or `undefined`).
- Passes in every file: a whole boolean or null word (`True`, `False`, `true`, `false`, `None`,
  `null`, `nil`, `undefined`) and Sigma's own redaction markers (`[REDACTED]`, `[REDACTED:<rule>]`).

A code file is one whose basename has a stem and whose last suffix, compared case-sensitively, is in
`scrub.COMMIT_CODE_SUFFIXES`: Python, JavaScript and TypeScript, the JVM languages, Go, Rust, Ruby,
PHP, C#, Swift, Dart, C and C++, Objective-C, Lua and Elixir. Every other file (config, data, docs,
a suffix nobody listed, an upper-case variant) reads an unquoted value as a literal.

**Residual misses, measured when the filter was designed.** In a code file, a letters-only or
digit-only value under 24 characters passes as a positional call argument or a getenv default (the
old gate refused both), and so does an unquoted dotted value made only of expression characters. On
2,000 random letter/digit runs per length, a run placed as a getenv default was refused 56% of the
time at 6 characters, 70% at 8, 89% at 14, 97% at 20 and 100% at 24 or more; the misses are runs
with no digit at all and upper-snake runs with fewer than 3 alternations. A literal on the line
after a plain assignment (a wrapped `= (` or a triple quote) passes, as it did before. Provider rules
(`aws-key`, `gh-token`, `stripe-key` and the rest) still fire on their own shapes and have no
allowlist: a synthetic value that matches one parks the goal.

**Further misses, found in review and confirmed against this filter.** The old gate refused each of
these, and each follows from judging only the first word as the value. In every file, a boolean or
null first word passes with a quoted literal later on the line (`<key> = None or "<letters>"`,
`<key> = True if x else "<letters>"`, `<key> = null ?? "<letters>"`), and so does YAML's
`<key>: true <letters>`. In a code file, an annotated default passes whatever unquoted number or
identifier it assigns, when the annotation is an expression or a closed-set type of 4 or more
characters (`<key>: Optional[int] = <8 digits>`, `<key>: float = <digits>`; a shorter annotation
never reached the old rule either). The trailing value is still judged by the secret-like check:
on 500 random runs per length, a letters-only trailing literal and a digit-only annotated default
each passed 99-100% of the time at 4 to 23 characters and 0% at 24, and a trailing literal mixing
letters and digits passed only when a draw held no digit (8% at 8 characters, 0% at 16).

**The allowlist, `work.allow_secret_content`.** A line that is still refused and holds no credential
is cleared only by an entry in the MAIN checkout's `.sdlc/config.json`, and an entry is an OPERATOR
ruling: a loop agent never writes one itself, attended or not. The agent removes the literal, reads
the value from the environment, or parks the goal with `record parked "<why>"`, in the order the
refusal prints. Two entry forms, narrowest first:

- `{"rule": "credential-assignment", "line_sha256": "<hash>", "reason": "<why>"}` clears that one
  line, in any file, until the line changes. The refusal prints the command that hashes the goal
  worktree's copy without showing the line, shaped like
  `python3 <sigma>/skills/sigma-loop/scripts/work.py line-hash <worktree>/<path> <line>`. The hash
  is the sha256 of the stripped line; a CRLF line hashes as its LF twin, and bytes that are not
  UTF-8 decode to U+FFFD on both sides.
- `{"rule": "credential-assignment", "path": "<exact repo-relative path>", "reason": "<why>"}` is
  WIDE: it clears every current and future `credential-assignment` hit in that file, a real literal
  added there later included, so it is for a file of fixtures only.

An entry counts only when its `rule` is exactly `credential-assignment` (no wildcard, no provider
rule), its `reason` is a non-empty string, and it carries exactly one of `path`, compared exactly
with git's raw repo-relative path (a leading `./` or another letter case never matches), or
`line_sha256`, 64 lowercase hex. Anything else is ignored, never a crash and never a bypass, and the
refusal says how many entries it ignored. The refusal never prints a line hash.

**What this does not guarantee.** `work.py commit` reads the MAIN checkout's `.sdlc/config.json`, not
the goal branch's, so an entry is visible in review only where that file is committed; a repository
that ignores `.sdlc/` reviews no entry at all. The operator-only rule is prose: nothing in code can
tell an operator's edit of that file from an agent's. The stored hash is an unsalted sha256 of a line
that is being committed anyway.
