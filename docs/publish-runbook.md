# The publish runbook: private references

Before a fresh public snapshot repository is published, no issue, pull request, comment, review or
release text copied into it may name or link the source repository it grew out of. That source
repository is not renamed and this runbook does not change its visibility; the public repository is a
new one (`Agrim-Intelligence/sigmaloop`) that starts from one snapshot commit. `tools/leak_refs.py`
(issue 282) finds every such reference, plans a neutral rewrite of the ones it can safely edit, and
lists the rest for the owner. It is run by hand, by the owner, attended; no loop runs it.

The names it looks for are the owner's, in an owner-private patterns file that never enters any
repository. The tool prints where a pattern matched and the pattern's line number, never what it
matched.

## The patterns file

One Python regular expression per line, matched case-insensitively. Blank lines and lines that
start with `#` are skipped. The tool reads `--patterns FILE`, else the `SIGMA_LEAK_PATTERNS`
environment variable; there is no default path, so forgetting both is a refusal, never a silent
empty scan.

It refuses the file (exit 2, one line naming a code) when it cannot be read, when it holds no
pattern, when a line does not compile or matches the empty string (by line number, never its text),
and when it resolves inside ANY git work tree, including a git-managed home directory. Keep it
somewhere like `~/.sigma-ops/`, outside every repository.

## Run it

From the root of this checkout, with the patterns file exported once:

```
export SIGMA_LEAK_PATTERNS="$HOME/.sigma-ops/leak-patterns.txt"     # yours; outside every repository
python3 tools/leak_refs.py scan --repo Agrim-Intelligence/sigma
python3 tools/leak_refs.py tree
python3 tools/leak_refs.py text pr-body.md
python3 tools/leak_refs.py rewrite --repo Agrim-Intelligence/sigma --out ~/.sigma-ops/leak-dryrun.md
python3 tools/leak_refs.py rewrite --apply --from ~/.sigma-ops/leak-dryrun.md --repo Agrim-Intelligence/sigma
```

Text you are about to post can be checked from stdin, before it reaches GitHub:

```
printf '%s' "$TITLE" | python3 tools/leak_refs.py text -
git log -1 --format=%B | python3 tools/leak_refs.py text -
```

`--repo` is required for `scan` and `rewrite`; the tool never infers it. `--patterns FILE` works on
every verb in place of the variable. `--timeout S` bounds every single `gh` or git call (default 60).

| Verb | What it reads | Writes |
| --- | --- | --- |
| `scan` | every issue and PR in every state: title, body, conversation comments, review comments, review bodies; commit comments; release names and bodies; and the edit history of all of these (revisions, and title renames) | nothing |
| `tree` | this checkout: every file `git ls-files --cached --others --exclude-standard` lists (tracked, and untracked but not ignored, so an uncommitted plan counts), each path, and every commit message reachable from HEAD; a tracked file deleted in the checkout is read from its index copy (`tree <path> (index) line ...`), and an unreadable file or a submodule is refused, never skipped | nothing |
| `text` | one file, or stdin with `-` | nothing |
| `rewrite` | what `scan` reads | only the `--out` file |
| `rewrite --apply --from FILE` | the live text of each planned edit | the planned edits, on GitHub |

## Output and exit codes

A hit is one line: its location and the pattern's line number, such as
`issue 12 body line 3 pattern 2`, `pr 5 review 987 body line 1 pattern 2`,
`history issue 12 body revision <editedAt> line 1 pattern 2 [manual: web UI, delete the revision]`
or `tree path entry 40 pattern 1`. Then come counts per surface and per pattern line. No location
carries `#` before a number, but a `commit <sha> message line ...` location holds a commit SHA, and
GitHub turns a pasted SHA into a link. So **location lines are never pasted into GitHub text**.
Post counts and the dry-run file's `~` path, nothing else.

Every printed line is matched against the patterns once more, and any match becomes
`[redacted: pattern P]`. Of a failing `gh` call's stderr, only the first line is shown, redacted
whole and then cut to 200 characters.

| Exit | Meaning |
| --- | --- |
| 0 | nothing matched (for `--apply`: every edit verified or already applied) |
| 1 | something matched or was truncated, history lines included (for `--apply`: an edit was skipped, deleted since the dry-run, unverified, or left for the next run) |
| 2 | a refusal, a bad argument, or any `gh` or git failure: never read as clean |

A refusal is one stderr line, `leak_refs: REFUSED [<code>] <detail>`, with nothing on stdout:

| Code | Cause |
| --- | --- |
| `no-patterns-source` | neither `--patterns` nor `SIGMA_LEAK_PATTERNS` |
| `patterns-unreadable`, `zero-patterns`, `pattern-compile`, `pattern-matches-empty` | the patterns file |
| `inside-work-tree` | the patterns file or `--out` resolves inside a git work tree |
| `work-tree-unknown` | git could not say (missing, timed out, "dubious ownership", a path inside `.git`): fails closed |
| `bad-repo`, `bad-arguments`, `out-required`, `input-unreadable` | the arguments |
| `gh-failed` | a `gh` call failed, timed out, or returned unparsable JSON or GraphQL errors |
| `bad-shape` | a response of the wrong shape: a non-list page, a null or unexpected node, a missing connection or one without a boolean `hasNextPage`, a revision without its text |
| `page-cap` | a list longer than `--max-pages` pages (default 1000): refused, never truncated |
| `budget` | the GraphQL budget is under twice a query's cost; the line gives the reset time |
| `moved-during-scan` | an item, comment, commit comment or release was created or deleted during the walk: re-run once nothing is writing |
| `git-failed`, `root-not-toplevel`, `listing-incomplete` | `tree` could not list the checkout |
| `file-unreadable`, `submodule` | `tree` met a listed file it cannot read (or one gone since the listing), or a submodule, whose content it does not scan: run `tree --root` on the submodule's own checkout |
| `out-exists`, `out-unwritable` | the dry-run never overwrites a file |
| `replacement-unsafe` | a `--replacement` carrying a link, number, mention or marker, or matching a pattern |
| `blocker-scan-unavailable` | the blocker vocabulary the invariants read could not be loaded |
| `apply-needs-from` | `--apply` without `--from` (checked before any `gh` call) |
| `manifest-unreadable`, `manifest-schema`, `manifest-repo`, `manifest-invariant` | the `--from` file is missing, cut short, of another schema or repository, or hand-edited |
| `rate-limited` | still rate limited after 3 retries: re-run later |
| `internal` | an unexpected error; only its type is printed, never its message |

## The rewrite

The dry-run widens each hit to the whole reference it sits in (a markdown link, an autolink with its
angle brackets, a bare URL, an `owner/repo#N` reference) and replaces it with a fixed phrase that
carries no number: `a private predecessor issue` for a reference to an issue or a URL, else
`the private predecessor repository`. A bare URL never takes a markdown or HTML delimiter with it:
it ends before a backtick, a quote or a pipe, and drops trailing punctuation, `*`, `_`, `~` and an
unbalanced `)` or `]`, so a URL in a code span or in bold is replaced inside it. An edit is planned
only when all of these hold; otherwise the item goes on the manual list with every reason it failed:

| Reason | Rule |
| --- | --- |
| `title` | a title is never rewritten: a rename keeps the old title in a public timeline event |
| `not-owned` | only OWNER, MEMBER or COLLABORATOR text is edited (a release counts as owned) |
| `pattern-left` | no pattern may match the rewritten text |
| `blocker-edge` | no blocker edge may appear, in any view a blocker consumer takes of the whole text |
| `token-added` | no link, number, mention, SHA or HTML tag may appear that was not there |
| `cap-shift` | no text may move across the 500-character excerpt the check-time blocker scan reads (conservative) |
| `marker-in-span` | the replaced span may not hold a Sigma marker |
| `delimiter` | the replaced span may not hold a delimiter it cannot keep (a pipe, one quote of a pair, an unbalanced bracket), split a run of backticks or emphasis characters, or sit inside an HTML tag (an attribute value); and no text outside the replaced spans may move into or out of a code span, so a quoted `#N` or `@name` never becomes a live link or mention |

The before and after text go only to the `--out` file: created with mode 0600, never overwriting,
never following a symlink, and refused inside any git work tree. It holds one diff per edit, the
manual list, the history purge list with each item's URL, and a closing manifest line that
`--apply` reads. Stdout gets counts and the path in its `~` form.

`--apply --from FILE` re-checks every edit in the file first and refuses a hand-edited file whole.
Then, per edit, it reads the live text: the read answers 404 or 410, skipped
(`deleted since the dry-run`, never a write, and every re-run skips it again); not owned now,
skipped (`not-owned`); already the planned text, skipped (`already applied`); neither the planned
before-text nor the after-text, skipped (`changed since the dry-run`, make a new dry-run for it);
otherwise it writes the after-text, with the text on stdin, and reads it back (`verified`, or
`verify mismatch`). Writes are at least one
second apart, at most 400 per run (`--max-writes`); a rate limit is retried after its
`Retry-After` (at most 300 seconds, at most 3 times). A re-run after a crash or a lost reply skips
what already landed. There is no revert, on purpose: the before-text is the leak.

## Before public-snapshot publication: private references

OWNER RUNBOOK. Run by the owner, attended, from the root of a current checkout.

1. **Stop every writer.** End the loop sessions, then `touch .sdlc/state/watch.stop`. The loop edits
   issue bodies with a read-then-write that an apply could race.
2. **Scan.** `python3 tools/leak_refs.py scan --repo Agrim-Intelligence/sigma` exits 1 while
   anything matches. Note the per-pattern-line counts: a pattern line with 0 hits may be a format
   mismatch in the patterns file, not a clean repository.
3. **Dry-run.** `python3 tools/leak_refs.py rewrite --repo Agrim-Intelligence/sigma --out ~/.sigma-ops/leak-dryrun.md`
   writes the plan outside every repository.
4. **Review the file**: every diff, the manual list and the history list.
5. **Apply.** `python3 tools/leak_refs.py rewrite --apply --from ~/.sigma-ops/leak-dryrun.md --repo Agrim-Intelligence/sigma`.
   Run it again while it says edits remain; make a new dry-run for anything `changed since the
   dry-run`.
6. **Rescan**, and handle the manual items by hand: edit your own, ask other authors, or minimize.
7. **Purge history in the web UI.** Delete every revision a `history ... revision` line names,
   including the ones the apply itself added (each applied edit keeps its before-text in a new
   revision, and the rescan lists them). GitHub has no API for this.
8. **Scan twice in a row; both must exit 0.** Review comments and commit comments have no cheap
   per-item count, so a second clean scan is what catches one created or deleted during the first.
   A `history ... title-rename` line cannot be purged. The options are to delete that issue, or to
   accept the residue; that is the owner's decision, recorded on the issue that gates
   publication of the snapshot, and until the issue is deleted the scan keeps reporting the line.
9. **Restart the writers**: remove `.sdlc/state/watch.stop`.

Before any goal of this work posts to GitHub, check the tree and the text itself: `tree` must exit
0, and each PR title, PR body, commit message and comment must pass `text -` before it is posted.

## Scale and limits

Measured during planning (2026-09-29) with equivalent read-only `gh` calls, not yet with this tool:
the REST reads took 7 pages for 362 items and 244 comments (6.9 s); the history reads took 7
`nodes` queries and 12 GraphQL points for 402 revisions (1.1 MB, 23.2 s); the per-item batch over
366 items took 4 queries (7.4 s). A full scan is therefore about 25 calls and 45 seconds, an
estimate. At 10 times the size, about 225 calls and 6 minutes; at 100 times, about 2,000 calls and
55 minutes, still under both 5,000-an-hour budgets (estimates).

Named ceilings: 1,000 pages per REST list (refused beyond it, at 100,000 rows); 100 revisions,
100 title renames or 100 reviews per item, beyond which the item is reported `truncated` (a finding,
exit 1); calls are serial, 0.25 s apart; the apply writes at most once a second and 400 times per
run, so 6,000 edits take 15 runs.

## What this does NOT cover

File contents in earlier commits: the whole git history goes public with the repository, and `tree`
reads only the checkout and the commit messages. Also commits on other branches, branch names, tags
and tag messages, milestones, labels, release edit history (GitHub has no API for it), Actions logs
and artifacts, Projects, the repository description and topics, the wiki, discussions, forks, and
notification e-mail already sent. The tool cannot purge edit history or title renames; it lists them. A pathological regular expression in the patterns
file can be slow: the standard library has no regex timeout.

## The controls, seen red

`tests/test_leak_refs.py` runs offline, with a fake `gh`. Its CLI tests run `tools/leak_refs.py`
as a child from the repository root, but under the interpreter running pytest (or
`LEAK_REFS_TEST_PYTHON`, which the Python 3.9 check sets to the system `python3`), not a literal
`python3`; most pass `--patterns FILE`, and `test_c2_cli_exported_variable_gesture` runs the
exported-variable gesture with `SIGMA_LEAK_PATTERNS` and no `--patterns`.
`test_runbook_gestures_parse` parses every gesture on this page with the tool's own parser. Each
control in it was broken once and seen red before it was trusted; the record is in the pull
request that added this page.
