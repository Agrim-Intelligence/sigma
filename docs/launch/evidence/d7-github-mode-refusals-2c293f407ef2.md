# D7 evidence: GitHub-mode refusals print their fix (#1290, #1291, #1289)

Code state measured: the pull request heads `da8f1378418e` (#1350, remedy helper and surfaces), `00b57fbaa03b`
(#1351, `gh_api.py` sites) and `2c293f407ef2` (#1352, opt-in refusals, local-park wording; contains the other two).
All three are now squash-merged to main, as `37a67ba5` (#1350), `abe5bfc5` (#1351) and `2c305546` (#1352), and this
record's own pull request (#1357) is the fourth. The squash merges changed the shas: the measured code is what main
holds for those three changes, and the pull request heads above are the states that were run.

An independent scoring review found D7 "every refusal prints its fix" not met for GitHub mode. This record is what was
reproduced, read, fixed, and what is left.

## Reproduced before the fix (invalid token, nothing written)

`GH_TOKEN` set to an obviously invalid value, an empty `GH_CONFIG_DIR`, read-only calls only. The exact output of
`gh api rate_limit`:

- exit status 1
- stderr: `gh: Bad credentials (HTTP 401)`
- stdout (content of; the real output is pretty-printed over several lines): a JSON object with `message` set to
  `Bad credentials`, `documentation_url` set to `https://docs.github.com/rest`, and `status` set to `401`

The same text came back for a repository read, so a bad token hides a missing repository behind a 401. Before the fix,
`loop.py next` against a GitHub-mode config printed those lines inside the sweep and backlog messages, then
`DONE (degraded read ...)` and `DONE`, with no remedy; `promote.py list` said it could not resolve assignee `@me`.

## What was fixed

One table, `gh_session.REMEDIES`, keyed by the `gh_api.classify` kind (auth, not_found, permission, rate_limit,
transport, server), appended after gh's own text. The scopes named for `permission` are the ones the first-run
preflight demands (repo and workflow, plus project when the board is on). It is applied only to a `GhApiError` or to an
exception whose `.hint` carries gh's stderr; any other exception keeps its text, because `str()` of an exception embeds
argv and `classify` must not read argv. The already-applied test is the exact remedy string.

After the fix, with the same invalid token and the real `gh` (the reachable surfaces, first 120 characters of each):

```
sigma: could not list sdlc:needs-label issues (gh api repos/OWNER/NAME/issues ... failed: gh: Bad credentials (HTTP 401) -- fix: run `gh auth login` ...
sources.py: gh could not read the signed-in user (gh api user --jq .login failed: gh: Bad credentials (HTTP 401) -- fix: run `gh auth login` ...
sources.py: could not read the backlog (gh api repos/OWNER/NAME/issues ... failed: gh: Bad credentials (HTTP 401) -- fix: run `gh auth login` ...
DONE (degraded read — backlog state unknown)      <- stderr, unchanged
DONE                                               <- stdout, unchanged
```

`promote.py list` now reports gh's reason and the fix, not the assignee wording; with no gh failure the old assignee
wording is unchanged. Surfaces wired: `sources.py` (backlog read, signed-in user, needs-label and needs-unit sweeps, board
queue, label lookups), `promote.py`, `unpark.py`, `work.py`'s gh runner, and `GhApiError.__str__` for everything that
prints a `gh_api` error. The final failure line of the backlog read carries the remedy; the "retrying" line does not.
A `GhApiError` with no gh stderr (a hint-less test double, say) gets no remedy: its class would come from `str()`,
which embeds argv. The permission and Not Found lines avoid the words callers scan error text for (`project`, `scope`,
the single-sign-on terms, "try again"); a test checks every marker tuple against every remedy, and the scans themselves
read the text without the appended line.

Opt-in refusals (#1291): a missing `gh` is reported as missing (it read "not logged in"); `board_setup` and
`board_layout` number, owner and board-list errors; the five `setup.py hooks` causes; the label-summary line for a bad
token; the wizard's pip step; the `define.py declare` rows. The existing hook value is still never revealed.

## `gh_api.py`: every refusal site, read

Method: every `raise` statement plus the four helpers that build an error (`_malformed`, `_refuse`, `_bad_pr`,
`too_many`), found with the Python AST, then read in context, not taken from a grep line list. 121 sites at the measured
state. The earlier triage counted 119 sites and called 105 unreachable on a first run; that number came from a line
list and is superseded by this table.

| Class | Sites | Reachable on a first run | What is there |
|---|---|---|---|
| Re-raise of an error built elsewhere | 16 | carries the class below | no message of its own |
| Wraps a gh failure and keeps its class | 11 | yes (a bad token, a 404, no network) | **fixed**: the fix is appended |
| Already printed its fix | 4 | yes | missing gh (two), the feature-label refusal, the team-slug refusal |
| Reachable, no fix, message edited | 1 | yes (an assignee who is not a collaborator) | **fixed** |
| Caller-contract violation (`ValueError`, invalid argument built by Sigma code) | 24 | no | a Sigma bug, not a user condition |
| Malformed, unparseable or inconsistent reply | 57 | no, needs a reply GitHub does not send | no user fix exists |
| Truncation or scale ceiling (cap pages, 100-row lookup limit) | 8 | no, only at scale | names its cap; no first-run fix |

The class counts come from a rule-based classifier over the AST list and a read of each group; the boundaries between
"malformed" and "scale" are judgment calls (a few sites could sit on either side). The totals sum to 121. The re-raise
class is the loosest: an independent reading got 18 to 20 against 16 here, depending on whether the two missing-`gh`
re-raises in `_default_run` count as re-raises or as "already printed a fix".

Found while reading: `gh_api.list_issues` wrapped a gh failure with kind `other`, dropping its class; fixed.

## Local goals, #1289

Measured on a scratch local-goals project: after `loop.py record ... parked`, `loop.py next` prints `DONE`; after
changing `status: parked` to `status: pending` in the goal file by hand, `loop.py next` offers the goal again, and
`.sdlc/state/review-queue.md` still lists it. So a real but manual mechanism exists (the picker reads only the `status`
field). The local-goals notice from `/sigma-unpark` and `/sigma-promote` and `docs/backlog.md` now say so. There is
still no Sigma gesture for it and nothing cleans the queue entry, so #1289 stays open for that.

## What remains

- The scale ceilings and malformed-reply sites print no fix by design.
- `work.py` has other direct `RuntimeError` surfaces that carry argv; only its gh runner was changed.
- The retry loop treats an auth failure as a transient degraded read and backs off; the message now says to log in, but
  the loop does not stop for it.
- Other opt-in refusals filed with #1291 and not touched here: the second-plugin warning in `init_flow.py`, the opt-in
  auto-merge arm failure, the ledger publish deferral, hand-corrupted config tracebacks in `verify_detect.write_verify`
  and `define.py open --config`.

## Not established

- No Windows, no Linux and no Codex run. Everything was run on one macOS machine.
- Only the 401 text was captured from the real `gh`. The 403 (permission and rate limit), 404 and 5xx wordings are
  inferred, as `gh_api.classify` already states; a 403 cannot be produced safely with an invalid token, since GitHub
  answers a bad token with 401 before it checks permission.
- The 401 capture used `gh api rate_limit` and a repository read; no authenticated account was used.
- The class table is a reading, not a measurement; only the fixed sites were triggered, each by a failing test.
- Whether a person new to the tool finds the appended text enough is not measured.
