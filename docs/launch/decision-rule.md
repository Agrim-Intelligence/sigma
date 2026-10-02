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
- a relative, `/`-separated path with no whitespace, NUL or other control character and no `.` or
  `..` component, that is a non-empty regular **file** (not a symlink, a directory or a submodule)
  at the `main` commit (see below). Absolute paths are not evidence.

Every evidence entry of a gating dimension that scores 3 or more must be a link. `TODO`, `n/a`, a
path that is not a non-empty regular file at the `main` commit, or a `../` escape is NO-GO, and the
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
the checker cannot read (a scorecard missing at `main`, a scorecard or definition at `main` that is
not a regular file of at most 1 MiB or is not JSON, a JSON key given twice, an unknown key, a score
outside 0–4, a `benchmark_results` path outside `docs/launch/evidence/`, a blocker list that cannot
be fetched or that is empty output rather than `[]`, a path the operating system rejects) is
neither: it exits 2 with `decide.py: MALFORMED: <detail>` and prints no verdict.

Everything is read from `main` as ONE commit, and there is no fallback. The checker must run at the
top of a git work tree whose `refs/remotes/origin/main` names a commit. It reads that exact ref —
never the short name `origin/main`, which a local branch, a tag or `refs/origin/main` would shadow
— once, refuses it when it is a symbolic ref (that would make a local branch `main`), and reads the
scorecard, the definition, every repository-path evidence entry and the benchmark file from that
commit's tree. The working tree is never read: an untracked, edited, deleted or symlinked file in
the checkout changes nothing. That includes the checker's own folder: Python puts a script's folder
first on its import path, so before importing anything but the builtins `sys` and `posix` (which
no file can shadow) the checker removes every import-path entry that is that folder (compared by
device and inode), and an untracked `json.py`, a `json/` package or a sourceless `json.pyc` beside
it is never imported.
When `main` cannot be read — git is missing or older than 2.32, the
directory is not a git work tree or is below its top level, there is no `refs/remotes/origin/main`
(a single-branch clone, a pull-request CI checkout), it is symbolic or not a commit, or an object
is missing from a partial clone — the checker refuses: it exits 2 with `decide.py: REFUSED: <why>`
and prints no verdict. A verdict that cannot see `main` is never GO.

## Computing the verdict

On a checkout whose `main` was just fetched (`git fetch origin`), from the repository root:

```
python3 tools/readiness/decide.py .
```

It reads the open `launch:blocker` issues over REST with `gh api` (read-only, never GraphQL). It
prints one line per reason the verdict is not GO, then `info:` lines — informational dimensions,
`info: main is refs/remotes/origin/main at <sha>`, `info: --repo <OWNER>/<NAME> is the operator's
assertion: ...` when `origin` cannot name the repository (below), and where the blockers were read
from (`info: blockers read over REST from <host>/<OWNER>/<NAME>`, or `info: blockers read offline
from '<file>'`) — then `GO` or `NO-GO`.

Exit codes: `0` = GO, `1` = NO-GO, `2` = refused, malformed input or a usage error (no verdict).
A refusal prints `decide.py: REFUSED: <why>` (the environment, the repository's state, the REST
read of `main`, a `--repo` that is not `origin`'s repository); a malformed input prints
`decide.py: MALFORMED: <detail>` (the scorecard, the definition, the blocker list, origin's URL, an
argument). Both go to stderr with nothing on stdout.
`--help` prints the usage and also exits `2`, so no script can mistake it for GO.

Before it reads the blockers, the live run checks two things and refuses (exit 2) when either
fails. `gh` must not send its requests to a local socket: `gh config get http_unix_socket` and
`gh config get -h <host> http_unix_socket` must succeed and print nothing, because whatever listens
on that socket can answer for GitHub. And `main` read over REST (`gh api
repos/<OWNER>/<NAME>/git/ref/heads/main`) must be one commit whose sha is the commit this
checkout's `refs/remotes/origin/main` names; otherwise the refusal names both, and the remedy is
`git fetch origin`, then rerun. That check compares commits only: a fork or mirror whose `main` is
the same commit passes it, so it does not by itself establish which repository's blockers are read
(see `--repo` below).

The blocker list can be supplied offline as a JSON file of REST issue objects (the tests use this;
`[]` means none). It replaces the `gh` read only; `main` is still read through git. A file cannot
establish that no open `launch:blocker` issue exists, so an offline run is never GO: it adds the
reason `blocker list read offline from '<file>'; only the live REST read can establish that no open
launch:blocker issue exists`. Each object needs an integer `number`, a `state` of `open` or
`closed` (any case), and a `labels` list of objects with a `name`, one of them `launch:blocker`. An
object with a `pull_request` object is a pull request and is dropped; anything else — an unknown or
missing `state`, no `labels`, a `pull_request` that is not an object — is malformed (exit 2), never
dropped:

```
python3 tools/readiness/decide.py . --blockers-json <file>
```

Which repository's blockers count is never left to `gh`. Without `--repo OWNER/NAME`, the checkout
must be a git repository whose only remote is `origin`, with exactly one URL, as this checkout's
own config writes it (no `url.<x>.insteadOf` rewrite applies, and global and system git config are
not read), of the form `https://HOST/OWNER/NAME`, `ssh://[user@]HOST[:port]/OWNER/NAME` or
`[user@]HOST:OWNER/NAME` (`.git` optional); the checker derives `HOST` and `OWNER/NAME` from it and
passes both to `gh` explicitly. A checkout with several remotes, none, an `origin` with several
URLs or of any other shape is refused (exit 2) rather than guessed at: pass `--repo OWNER/NAME`.

With `--repo OWNER/NAME` the host is `github.com`. When `origin` names a repository as above,
`--repo` must name the same one — host `github.com`, `OWNER/NAME` compared case-insensitively, and
`origin`'s spelling is then used — or the checker refuses (exit 2), naming both: a fork or mirror
whose `main` is the same commit has its own blocker list. When `origin` cannot name a repository
(several remotes, none, several URLs, a URL of another shape), `--repo` is the **operator's
assertion**: the checker cannot verify it, accepts it, and says so in an `info: --repo
<OWNER>/<NAME> is the operator's assertion: ...` line. `OWNER` and `NAME` must each start with a
letter, digit or `_`, contain only letters, digits, `_`, `.` and `-`, and be at most 100
characters; anything else (`../..`, `a/.`, a trailing newline) is refused (exit 2).

## What the checker trusts

Every git call it makes runs with every `GIT_*` variable removed from its environment and with
`GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL` set to the null device, `GIT_NO_REPLACE_OBJECTS=1`,
`GIT_NO_LAZY_FETCH=1` and `GIT_TERMINAL_PROMPT=0`, as `git --no-replace-objects --literal-pathspecs
-c core.commitGraph=false -c protocol.allow=never` plus `-c protocol.<p>.allow=never` for `file`,
`git`, `ssh`, `http`, `https` and `ext`. So an inherited `GIT_DIR` or `GIT_CONFIG_*`, a global or
system config, a replace ref or a partial clone's lazy fetch cannot change what is read: in a
partial clone, a blob that is not local is refused, never fetched. Global config being off also
switches off a global `safe.directory`, so a checkout owned by another user is refused (`detected dubious ownership`).
git older than 2.32 ignores `GIT_CONFIG_GLOBAL`, so it is refused; that includes hosts that ship an
older git (Debian 11 ships 2.30). git 2.39.5 and git 2.55.0 honour `GIT_NO_LAZY_FETCH` (measured);
2.32–2.38 were not measured, and on a git that ignores it, lazy-fetch prevention rests on the
protocol pins alone. A remote helper's own `protocol.<name>.allow` is not pinned, so a local
`always` for it falls to the trust placed in the checkout's `.git` directory, below.
`core.commitGraph=false` on the command line beats the checkout's own config, so, per
git-config(1), git does not read a commit-graph file for a commit's tree; no forged commit-graph
was tested.

Every `gh` call runs with every `GIT_*` variable, `GH_REPO`, `GH_HOST`, `GH_FORCE_TTY` and
`CLICOLOR_FORCE` removed from its environment, and with `GH_PAGER` and `PAGER` set to the empty
string and `NO_COLOR=1`. Measured with gh 2.98.0 on a read-only public REST read: with
`GH_FORCE_TTY` set, gh treats its piped output as a terminal and runs `gh api` output through
`GH_PAGER`, gh config's `pager` or `PAGER`, so a pager could print any JSON it likes; an empty
`GH_PAGER` switches the pager off even then; `CLICOLOR_FORCE` colours the JSON even with
`NO_COLOR` set. The token variables are kept, because they only authenticate. Every other variable
gh reads is inherited.

The checker trusts, and does not defend against: the checker file itself; the `python3`, `git` and
`gh` that `PATH` resolves (`.` or the checkout root on `PATH` runs whatever `gh`/`git` it finds
there), and the interpreter's own environment (`PYTHONPATH`, `PYTHONHOME`, site customisation),
which load code before the checker's first line runs; the checkout's whole `.git`
directory — its config and every file that config includes, its object store (`git cat-file` does
not re-hash what it reads) and its refs (offline, `refs/remotes/origin/main` is taken as this
checkout last fetched it), including `origin`'s URL as that config writes it; `gh`'s config
directory (`GH_CONFIG_DIR` or its default) and stored credentials, and every `gh` variable not
removed or pinned above; the network's proxies and certificate authorities, and the environment
variables that choose them (`HTTPS_PROXY`, `SSL_CERT_FILE` and the like); and `--repo` when
`origin` cannot name the repository.

CI runs it on Linux with Python 3.10, 3.11, 3.12 and 3.13 and on macOS with Python 3.12; Python
3.9 was measured by hand, not CI-proven. It refuses to run on Windows (exit 2).

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
