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
| T1 | Issue, PR, and comment prose enters goal prompts and parsers: `skills/agrim-loop/scripts/blocker_scan.py:130`, `skills/agrim-loop/scripts/comment_watch.py:88`, `skills/agrim-loop/scripts/features.py:356`, `skills/agrim-loop/scripts/review_context.py:49`, and `skills/agrim-loop/scripts/slack_commands_listen.py:524`. |
| T2 | Repository content enters commands through configured checks: `skills/agrim-loop/scripts/pipeline.py:68`, `skills/agrim-loop/scripts/backlog_check.py:791`, `skills/agrim-loop/scripts/loop.py:5222`, `skills/agrim-rebase/scripts/verify_merge.py:122`, and `skills/agrim-loop/scripts/reviewer.py:239`. |
| T3 | Host hooks execute at prompts and tool calls: `hooks/hooks.json:3`, repo adoption is checked by `hooks/agrim_gate.sh:20`, and session-start's watcher/doctor diagnostic layer begins at `hooks/session_start.sh:131`. |
| T4 | Network destinations are GitHub, Slack, a configured webhook, SMTP, and the host's model process, enumerated in `docs/privacy.md:9`. |
| T5 | Background boundaries include detached watcher startup `skills/agrim-loop/scripts/loop.py:75`, the watcher loop `skills/agrim-loop/scripts/watch_daemon.py:906`, Slack listener lifecycle `skills/agrim-loop/scripts/slack_commands_listen.py:757`, and model subprocesses `skills/agrim-loop/scripts/autowatch.py:915`. |
| T6 | Local policy comes from managed settings `skills/agrim-loop/scripts/managed_settings.py:237` and the direct-edit sentinel is described in `docs/enforcement.md:54`. |
| T7 | Model-spawned sessions include `skills/agrim-loop/scripts/feature_judge.py:155`, `skills/agrim-loop/scripts/autowatch.py:915`, and `skills/agrim-loop/scripts/supervise_daemon.py:1`. |

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
| TM-04 | T2 | Command injection (E) | A cloned repository configures a check containing arbitrary shell syntax. | `shell_policy.py` refuses repository-configured shell strings until an operator sets the Git-local opt-in | med | #422 |
| TM-05 | T3 | Hook-triggered command execution (E) | A host invokes a configured hook on a prompt or tool call. | `hooks/agrim_gate.sh:20` | med | — |
| TM-06 | T4 | Credential exposure in logs (I) | A token-shaped value reaches a command or comment diagnostic. Coverage of every `gh` comment site is unverified. | `skills/agrim-loop/scripts/scrub.py:150` | med | — |
| TM-07 | T4 | Remote webhook egress (I) | `channel_webhook_url` names an Internet host instead of the documented local adapter. | none | high | #358 |
| TM-08 | T5 | Stale watcher appears idle (D) | A dead watcher reports no errors and is mistaken for an idle one. | `hooks/session_start.sh:131` | med | — |
| TM-09 | T5 | Listener cleanup omission (D) | SIGTERM or SIGHUP leaves Slack pid, heartbeat, and lock markers, preventing restart. | `skills/agrim-loop/scripts/slack_commands_listen.py:806` | low | — |
| TM-10 | T5 | Orphaned model descendant (D) | A timed-out, signalled, or SIGKILLed autowatch tick leaves a model descendant running. Residual: a descendant that calls `setsid` itself escapes the group, and a SIGKILL of the lifeline sentinel removes the backstop for a later SIGKILL of the tick. | `skills/agrim-loop/scripts/autowatch.py:772` terminates the driven session's whole process group (SIGTERM, grace, SIGKILL), a lifeline sentinel repeats that if the tick dies, and hosts without process groups refuse | low | #425 |
| TM-11 | T6 | Managed-policy deletion (T) | Removing the file makes an enrolled checkout read as unadopted. | `skills/agrim-loop/scripts/managed_settings.py:237` refuses an enrolled checkout whose file is gone, via markers outside the file. Residual: a checkout writer who also deletes the markers or runs `unenroll` is not bound (README, Managed settings) | med | — |
| TM-12 | T6 | Direct-edit bypass (T) | A local sentinel bypasses a hard plan gate when the key is not organization-locked. | `docs/enforcement.md:54` | med | — |
| TM-13 | T7 | Unbounded model spend (D) | A spawned model process spends beyond a sensible per-invocation budget. | `skills/agrim-loop/scripts/feature_judge.py:167` | low | — |
| TM-14 | T7 | Unsafe Slack merge (E) | `--unsafe-merge` removes interactive confirmation from a merge request. | `skills/agrim-loop/scripts/slack_commands_listen.py:1353` | med | — |

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

`/agrim-init`'s explicit `verify_detect.py confirm` and `set` gestures record
the same Git-local setting only after the operator confirms or supplies the
command. They refuse to enable a command outside a Git worktree. `decline` and
initial scaffolding never grant it.
