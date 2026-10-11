# Invocation adapter (opt-in)

Some hosts refuse the loop's own approval step as self-approval (Claude Code's auto mode did so in a
first live run, #612), and nothing inside Sigma can prove a phase-end line reached a host. An
environment marker (`CLAUDECODE`, `CODEX_THREAD_ID`, `review.host`) cannot settle either: the agent
controls its own environment. So authority is delegated to an **adapter you install and pin outside
the repository**. Off by default; with it off the only cost is one `git config` lookup per approve post
or phase end (no state, no output change), and the attended fallback in the
README ("Before you start `/sigma-loop`") applies.

## What it is, and is not

- It is **operator attestation**: Sigma runs exactly the bytes you pinned and accepts only a strict,
  challenge-bound receipt from them.
- It is **not isolation from the agent**. A process running as you that can edit both your local git
  config and the adapter file can pin an adapter of its own. Install and pin from a shell the agent
  does not control.
- No real Claude, Codex or Cursor adapter ships with Sigma. The tests use a fixture executable and
  prove the **protocol** (parsing, pinning, single use, byte-exact acknowledgement) for each host id.
  Whether a given host's real adapter works is checked only by running `diagnose` below against it;
  none has been run in this repository, so host-visible relay is **unproven on every host** until an
  operator does.
- It gates **Sigma's own `post-review --verdict approve` verb only.** Anything running as you can still
  post a `sigma:approve` comment directly with `gh`, and the merge gate (`work.require_review:
  approval`) reads that comment, not a receipt. Closing that needs a second human identity or branch
  protection on your git host; binding a receipt into the comment and checking it at merge is not built.
- The repository's `config.json` can never switch it off where you pinned an adapter: a pin with
  `invocation.adapter` unset, null or `config.json` unreadable refuses (remove the pin deliberately
  to turn it off).
- A relay `acknowledged` means the adapter acknowledged the exact bytes. It does not claim any UI
  displayed them.

## Install and check (operator, once)

```
git config --local sigma.invocation.adapter.claude.path /absolute/path/to/adapter
git config --local sigma.invocation.adapter.claude.sha256 <sha256 of that file>
python3 <installed-sigma>/skills/sigma-loop/scripts/invocation.py diagnose .sdlc claude
```

Use `claude`, `codex` or `cursor` as the id. The file must be a self-contained executable owned by
you and not group/world writable, not a symlink. `diagnose` prints `installed-and-conforming`,
`defer` (no pin: the safe default) or `refused`, and exits 0 only for the first. Then turn it on in
`.sdlc/config.json`: `"invocation": {"adapter": "claude"}` (`ttl_seconds` 30-600, default 300;
`timeout_seconds` 5-120, default 30). Config can never name a command or a hash.

## What changes when it is on

- `work.py post-review --verdict approve` asks the adapter for a single-use receipt bound to the PR
  number, PR head, review-brief digest and evidence digest. `accepted` posts; `deferred`, a refusal,
  a missing or changed pin, a timeout or any malformed reply parks for attended review with no post.
  `block` and `unblock` are not gated (so an `unblock` can re-activate an earlier approval without a
  receipt).
- `phase_report.py end` sends the exact Block B bytes to the adapter and reports
  `host relay acknowledged ...` or `host relay unavailable: <reason>` on stderr. The local phase
  record is written either way.
- The one-line coexistence notice names a host-specific config path only for a host with a fresh
  adapter receipt. The record is a same-user file, so this is a courtesy filter, not a security
  boundary; `coexist.py check` always lists every detected step.

## Protocol (version 1)

Sigma copies the verified bytes to a private 0700 file (so a swap after hashing cannot substitute
other bytes), runs `adapter verify --stdio` with no shell, a reduced environment and a process-group
kill on timeout, and writes one canonical JSON request `{"challenge": {...}}` (plus `payload_b64` for
a relay). The challenge carries `v, kind, action_id, adapter_id, repo_realpath, goal, phase, pr,
head_sha, brief_sha256, payload_sha256, issued_at, expires_at, nonce`. The adapter answers exactly one
canonical line with `v, action_id, adapter_id, challenge_sha256, receipt_id, status, ack_sha256`;
`accepted` must echo the payload digest, `deferred` carries an empty `ack_sha256`. Extra or missing
fields, non-canonical JSON, a different challenge, a reused `receipt_id`, a nonzero exit or stdout
beyond one line refuse. `adapter describe --stdio` must answer the capabilities `diagnose` checks.

## Failure modes and recovery

| Failure | Result | Recovery |
|---|---|---|
| no pin, wrong hash, symlink, unsafe mode, timeout, malformed reply | refuse, adapter not run (or killed) | attended review; fix the pin; rerun `diagnose` |
| crash after receipt, before the post | the receipt was already consumed (single use) | rerun `post-review`; a new receipt is asked for |
| retry of a post that already has a request | no receipt asked: it only reconciles by marker, so an adapter outage cannot block recovery | `reconcile-review-post` / rerun |
| receipt used | single use (`consumed`) | a new challenge |
| ambiguous remote post | existing evidence-marker path (`reconcile-review-post`) | unchanged |
| Windows / no POSIX locks | refuse | attended review |

## Costs and ceilings

One stable lock file serialises record reads and writes only (never the adapter call); waiting is
bounded at 5 s then refuses. Records are one small JSON file per action, pruned past ten times the
maximum TTL (at most 50 per call). Measured on a developer laptop while a full test suite ran in
parallel: about 0.35-0.43 s per `authorize` with a trivial fixture adapter, flat from 10 to 310
records (a real adapter's own latency is not measured). Worst-case added wall time per phase end is
`timeout_seconds`, and only when configured. Not measured: behaviour on a slow network filesystem.
