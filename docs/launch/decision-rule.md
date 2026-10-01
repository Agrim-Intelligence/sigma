# Launch decision rule

## Status

Proposed — merged by the owner = signed. The owner's merge of the pull request that adds or changes
this page is the sign-off on the blocker classes and the GO rule below. Until then they are a
proposal, and nothing an agent does makes them binding.

This page is fixed **before** any dimension is scored, so the verdict cannot be steered by
relabelling a finding or re-flagging a dimension once the results are in. The verdict is computed
by `tools/readiness/decide.py`, not by a person.

## Blocker classes

A confirmed finding is `launch:blocker` if and only if it is one of:

| Code | Class |
|---|---|
| B1 | confirmed data loss or corruption of user data, git history, or GitHub state |
| B2 | a secret, credential, private reference or personal data that would become public or leave the machine |
| B3 | a claim in README, docs or launch material that is false on a **supported** cell of docs/launch/definition.md |
| B4 | a gate documented as enforced by code that is prose-only on a supported cell |
| B5 | spend beyond a configured cap, or a loop that cannot be stopped by its documented gesture |
| B6 | a failure with no automatic recovery and no documented recovery lever |
| B7 | a code path that writes to GitHub or git outside the documented write surface |
| B8 | a high or critical security finding (injection, path traversal, token exposure) on a supported cell |

Everything else is `launch:next`. Which findings get the label is decided by people against this
table; the checker only counts the open issues that carry it.

## Dimensions

Which dimensions exist, what they are called and which of them gate are part of this rule. The
scorecard (`docs/launch/scorecard.json`) cannot change them: `decide.py` refuses a scorecard whose
`gating` flags, dimension ids, dimension names or dimension count differ from the tables below, or
that carries any key other than `schema`, `benchmark_results` and `dimensions` (and, per dimension,
`id`, `name`, `gating`, `score`, `evidence`) — a typo such as `waived` is refused, not ignored
(exit 2).

Gating dimensions:

| Id | Dimension |
|---|---|
| D1 | Correctness |
| D2 | Skills |
| D3 | Outcomes and cost |
| D5 | Five properties |
| D6 | Platform |
| D7 | Onboarding |
| D8 | Docs |
| D9 | Upgrade, coexistence, uninstall |
| D10 | Security, privacy, exposure |
| D11 | Operations |
| D13 | Legal and naming |

Informational dimensions:

| Id | Dimension |
|---|---|
| D12 | Market |

Market comparison blocks only through B3, when it contradicts a launch claim.

D0 (launch definition) is not a scored row. It is the "definition signed" clause of the rule, read
from `docs/launch/definition.json` directly, so it cannot be scored around. There is no D4; the
numbering follows the revised review table in the readiness epic.

Scores are integers from 0 to 4, or `null` while unscored.

An evidence entry is a link, and counts only if it is exactly one of:

- an `http://` or `https://` URL with a host and no whitespace anywhere in the entry. The checker
  checks its shape only; it never fetches it, so it cannot tell whether the page exists;
- a relative, `/`-separated path with no whitespace and no `.` or `..` component, naming an
  existing **file** (not a directory) in the repository, which still resolves inside the repository
  after symlinks are followed, **and** which is a non-empty regular file (not a symlink) tracked at
  `origin/main`. Absolute paths are not evidence.

Every evidence entry of a gating dimension that scores 3 or more must be a link. `TODO`, `n/a`, a
path that does not exist, is untracked or empty on `main`, or a `../` escape is NO-GO, and the
reason names the dimension and the entry.

## The rule

GO if and only if all four hold:

1. every gating dimension scores 3 or more, with at least one evidence entry, and every evidence
   entry is a link as defined above;
2. zero open issues carry `launch:blocker`;
3. `docs/launch/definition.json` has `status: "signed"`, a `signed_by` that is a plain GitHub
   login (letters, digits and `-`, at most 39 characters), and a `signed_on` that is a real date
   written `YYYY-MM-DD` and no later than today on the machine running the checker;
4. the benchmark results file named in the scorecard (`benchmark_results`) is a file under
   `docs/launch/evidence/`, is non-empty, and exists on `main`.

Otherwise NO-GO. An unscored gating dimension, a missing definition, a `signed` definition without
a valid signer or date, or an unnamed benchmark file (`null` or blank) is NO-GO, never GO. An input
the checker cannot read (a missing or unparsable scorecard, a JSON key given twice, an unknown key,
a score outside 0–4, a `benchmark_results` path outside `docs/launch/evidence/`, a blocker list
that cannot be fetched or that is empty output rather than `[]`, a path the operating system
rejects) is neither: it exits 2 and prints no verdict.

"Exists on `main`" is checked against git, and there is no fallback. The checker must run at the
top of a git work tree that has an `origin/main` ref; the file must then be tracked at
`origin/main` as a regular file (not a symlink), non-empty there, not a symlink in the checkout,
and identical in the checkout to `origin/main`, so an untracked, edited, empty or symlinked file
cannot pass. When `main` cannot be read — git is missing, the directory is not a git work tree or
is below its top level, or there is no `origin/main` ref (a single-branch clone, a pull-request CI
checkout) — the benchmark clause, and every repository-path evidence entry, is NO-GO with the
reason `cannot verify on main (<why>)`. A verdict that cannot see `main` is never GO.

## Computing the verdict

On an up-to-date checkout of `main`, from the repository root:

```
python3 tools/readiness/decide.py .
```

It reads the open `launch:blocker` issues over REST with `gh api` (read-only, never GraphQL). It
prints one line per reason the verdict is not GO, then any `info:` lines, then `GO` or `NO-GO`.

Exit codes: `0` = GO, `1` = NO-GO, `2` = malformed input or a usage error (no verdict).
`--help` prints the usage and also exits `2`, so no script can mistake it for GO.

The blocker list can be supplied offline as a JSON file of REST issue objects (the tests use this;
`[]` means none). It replaces the `gh` read only; `main` is still checked through git. Each object
needs an integer `number`, a `state` of `open` or `closed` (any case), and a `labels` list of
objects with a `name`, one of them `launch:blocker`. An object with a `pull_request` object is a
pull request and is dropped; anything else — an unknown or missing `state`, no `labels`, a
`pull_request` that is not an object — is malformed (exit 2), never dropped:

```
python3 tools/readiness/decide.py . --blockers-json <file>
```

Which repository's blockers count is never left to `gh`. Without `--repo OWNER/NAME`, the checkout
must be a git repository whose only remote is `origin`, with exactly one URL of the form
`https://HOST/OWNER/NAME`, `ssh://[user@]HOST[:port]/OWNER/NAME` or `[user@]HOST:OWNER/NAME`
(`.git` optional); the checker derives `HOST` and `OWNER/NAME` from it and passes both to `gh`
explicitly. With `--repo OWNER/NAME` the host is `github.com`. `gh` runs with `GH_REPO` and
`GH_HOST` removed from its environment (the token variables are kept, because they only
authenticate). A checkout with several remotes, none, an `origin` with several URLs or of any other
shape is refused (exit 2) rather than guessed at: pass `--repo OWNER/NAME`. `OWNER` and `NAME` must
each start with a letter, digit or `_`, contain only letters, digits, `_`, `.` and `-`, and be at
most 100 characters; anything else (`../..`, `a/.`, a trailing newline) is refused (exit 2).

## Process rules the checker does not enforce

These bind the people who apply the label. `decide.py` cannot see them: it reads only which open
issues carry `launch:blocker` now, not their bodies or their label history.

- A `launch:blocker` issue names its class code (B1–B8) in its body.
- **Promote.** The owner may promote a `launch:next` to `launch:blocker` with a one-line reason
  comment on the issue.
- **Demote.** Nobody may demote a `launch:blocker`. It leaves the blocker set only by being closed,
  either with the fix or with a reproduced "cannot reproduce".
- Removing the label from an open issue is a demotion and is not allowed; a later review that finds
  one restores the label.

## How to change this page

Any change to the classes, the dimensions or the rule is a pull request that the owner merges, and
it changes `tools/readiness/decide.py` in the same pull request; `tests/test_readiness_decide.py`
fails when the two disagree.
