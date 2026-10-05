# Public snapshot: building the export

One reviewed commit of this repository becomes the tree of a NEW, fresh, one-commit public
repository (the owner decided on 2026-10-05: this repository is not renamed and keeps its history).
`tools/build_public_tree.py` builds that tree deterministically, scans it, and writes a report
outside every repository. It creates and pushes nothing: the owner creates the EMPTY new repository
and pushes the one commit, and the commands are below. The new repository's name is an input
everywhere (`--repo` of the verifier; the name the commit itself records is
`public_repo` in `docs/launch/definition.json`), never fixed in these tools. The goal behind it is
#397; the release checklist, pin and rollback are #359's.

Everything here is host-agnostic: the builder and the verifier are plain Python on
git and `gh api` GET calls, and nothing depends on a hook or on a host.

## What it is

- **The builder** (`python3 tools/build_public_tree.py`) takes a COMMIT and writes the export
  repository to `OUT`: one commit, no parent, branch `main`, a fixed neutral author and committer
  (`sigma-public-snapshot`), the commit date of the source commit, and the message `Initial public
  snapshot (Sigma <plugin version>)`. The export's commit id is therefore a pure function of the
  source commit: the same commit twice gives the same tree and the same export commit, and one
  changed byte changes both. The export's git calls see no global, system or environment-given
  configuration (`GIT_CONFIG_COUNT`/`_KEY_n`/`_VALUE_n` and `GIT_CONFIG_PARAMETERS` are removed) and
  commit with `i18n.commitEncoding=UTF-8`, so the operator's environment cannot change the id. No reflog is written (`core.logAllRefUpdates=false` on every ref
  update), so the export's `.git` records no operator name, e-mail or host.
- **The verifier** (`python3 tools/verify_public_repo.py`) reads a PUSHED export over REST and
  proves it is exactly that one commit with CI green on every leg.
- **The report** is a JSON file and a `.md` twin, written outside every repository. It names the
  commit, the verdict and every scan.

The builder never runs a hook, never uses `git add` or `git archive`, never writes to the source
repository (its git reads use a private object directory, and its status read is `git status
--no-optional-locks`), and refuses
loudly where a guarantee cannot be made: Windows, a non-sha1 repository, a symlink, a gitlink, a
case collision, a secret-looking file name, a binary or oversize or non-UTF-8 file.

## Build

The build is run by the owner, from a clean detached checkout of the commit to export (the builder
refuses a dirty checkout or one whose `HEAD` is not the commit, and it compares its own running
copy of itself and of `scrub.py`, `leak_refs.py` and `exposure_scan.py` with the commit's bytes; the commit's own `leak_scan.py` runs from the export itself). `$PATTERNS` is the private
patterns file kept outside every repository; its content is never printed or hashed anywhere.

```sh
git fetch origin main
COMMIT=$(git rev-parse origin/main)
SRC="$HOME/.sigma-ops/public-src"
OUT="$HOME/.sigma-ops/public-out"
PATTERNS="$HOME/.sigma-ops/leak-patterns.txt"
REVIEW_REPO="OWNER/NAME-OF-THIS-REPOSITORY"
git worktree add --detach "$SRC" "$COMMIT"
cd "$SRC"
python3 tools/build_public_tree.py "$COMMIT" --out "$OUT" --patterns "$PATTERNS" --repo "$REVIEW_REPO" --allow-public-source
```

`git fetch` and `git worktree add` write to the shared repository; they are the owner's steps and
the builder never runs them. `--patterns` may be replaced by `SIGMA_LEAK_PATTERNS`; one of the two
is required and there is no default path.

Flags (`python3 tools/build_public_tree.py --help` is the authority): `--out DIR` (required),
`--source DIR`, `--patterns FILE`, `--sdlc exclude|include`, `--exclude PREFIX` (repeatable),
`--review-level clean|landed|pr-merged|owner-merged`, `--repo OWNER/NAME`, `--allow-public-source`, `--remote NAME`,
`--base BRANCH`, `--report-dir DIR`, `--scan-timeout S`.

Exit codes and what is on disk:

- `0`, verdict `VERIFIED`: the export is at `OUT`. stdout, in order: `verdict:`, `commit:`, `tree:`,
  `export-commit:`, `out:`, `report:`, `scans:`.
- `1`, verdict `REJECTED`: a scan found something. The export is at `OUT.rejected`, with NO branch
  and NO export commit (it is pruned; see Recovery), and the report is written. stdout has the same
  lines; its `export-commit:` names the pruned commit, which no longer exists there.
- `2`: a refusal (one stderr line `build_public_tree: REFUSED [<code>] <detail>`, nothing on
  stdout, no export, no report, the partial removed), or verdict `NOT-VERIFIED` (a scan exited with
  anything but 0 or 1, or timed out; the report is written, `OUT.rejected` holds no branch and no
  export commit, stderr
  says `REFUSED [scan-failed]` and the report path, stdout is empty). A scanner that crashes is
  NOT-VERIFIED, never REJECTED, never VERIFIED.

The refusal codes: `windows`, `no-patterns-source`, `patterns-inside-work-tree`,
`patterns-unreadable`, `pattern-compile`, `pattern-matches-empty`, `zero-patterns`, `out-exists`
(for `OUT` and for `OUT.rejected`), `out-inside-work-tree`, `report-dir-inside-work-tree`,
`not-a-repo`, `bad-commit`, `bad-version` (the plugin version is not a plain `N.N.N[-alpha|beta|rc[.N]]` number, or the export commit message matches a content rule: the version enters the commit message, which no file scan reads), `object-format`, `bad-exclude`, `exclude-matches-nothing`, `symlink`,
`gitlink`, `bad-mode`, `bad-path`, `secret-file-name`, `case-collision` (two paths equal after
case folding and Unicode NFC normalisation), `head-not-commit`,
`dirty`, `tool-mismatch`, `tool-missing`, `no-remote-ref`, `not-landed`, `repo-required`, `bad-slug`,
`gh-failed`, `review-repo-public`, `not-pr-merged`, `not-owner-merged`, `dispositions-malformed`,
`tree-mismatch`, `oversize`, `binary`, `non-utf8`, `report-exists`, `unpublish-failed`,
`work-tree-unknown`, `git-failed`, `bad-ref`, `bad-timeout`, `leak-scan-absent` (the commit holds
`tools/leak_scan.py` and an `--exclude` drops it, so the fourth scan could not run; refused before
anything is built), `internal`; `scan-failed` is the NOT-VERIFIED refusal below.

## The `.sdlc/` switch and `--exclude`

`--sdlc exclude|include` decides whether the `.sdlc/` directory ships. The default is `exclude`,
and the report records `sdlc: {mode, source}` with the source `default` when no flag was given. The
`.md` twin then says that the choice came from the builder's default, not from an explicit owner
choice, and asks for `--sdlc exclude` or `--sdlc include` to record one. Shipping is the
irreversible act and excluding is recoverable by re-running, which is why exclude is the default;
the owner reverses it with `--sdlc include` and dispositions what the report then lists.

Everything else that is tracked ships: `tests/`, `tools/` and `docs/launch/` are part of the shipped
surface because CI and `python3 tools/onboarding_control.py` need them. `--exclude PREFIX` leaves a
path prefix out (relative, no `..`, repeatable). Use it only after reading each exclusion's
`references_remaining` in the report (the count of exported lines that still name an excluded
file) and after running the export's own tests. An exclusion that matches nothing is refused.

## Review levels

`--review-level` states how much review proof the commit must carry. The levels are cumulative,
each reported by name, and the report records `requested` and `reached`:

- `clean`: the checkout is clean and its `HEAD` is the commit (always checked).
- `landed`: the commit is an ancestor of the local remote-tracking ref of the base branch. The
  builder does not fetch, so the report records how many commits are past it: a stale ref is
  visible, not hidden.
- `pr-merged` (the default): GitHub reports the commit as the merge commit of a merged pull request
  into the base branch, so a direct push is refused. The report records whether one account opened
  and merged (`independent: false`, with the sentence "one account opened and merged: not
  independent review") and what branch protection was measured on the base branch (`present`,
  `none`, or `unreadable`) and whether the source repository is private (`source_private`). A source that is not
  private (public, or visibility unreadable) is REFUSED (`review-repo-public`) unless
  `--allow-public-source` is passed. The owner's flow needs the flag: this repository is already
  public, so its history is already public, and the point of the new repository is a clean
  one-commit history, not secrecy of the source. The flag prints a loud stderr warning and records
  `public_source_override: true` (JSON and `.md`) beside `source_private`; every other refusal and
  all four scans still apply.
- `owner-merged`: the merger holds the `admin` or `maintain` role on the review repository.

The REST levels need `--repo OWNER/NAME` and make GET-only `gh api` calls; nothing writes. An
approve-marker level and a signed-tag level are stronger and are NOT built: most recent merges
carry no approve marker and no signing is configured, so only these four are checkable. The report
never says "reviewed commit": it says what was measured.

## What the scans do not cover

Four scans run, with no short circuit, and the verdict is REJECTED if any finds anything. The
fourth, `leak_scan`, is the export's own `tools/leak_scan.py`, so it runs only when the export holds
that file: this repository's commits do, and an `--exclude` that drops it is refused
(`leak-scan-absent`). A commit that never had the file (a test fixture, say) builds with three scans
and records `leak_scan` as `absent` in the report and on the `scans:` line; that build can still be
VERIFIED, so read that field. The report prints, in `not_covered`, what the scans cannot see:

- header-less private key bodies (#433): the builder's own header rule and `tools/leak_scan.py`
  find a key header, not a body with no header;
- `tools/readiness/exposure_scan.py` de-duplicates its rules by name, so a second rule of one name
  never runs there;
- file NAMES are checked against the private patterns only;
- the origin-owner rule of `tools/leak_scan.py` is skipped, because the export has no origin;
- names absent from the patterns file, and e-mail addresses, which `tools/leak_scan.py` does not
  check;
- destination GitHub state beyond what `python3 tools/verify_public_repo.py` counts;

The four scans are: `builder` (the header rule `private-key-header`, the placeholder rule
`owner-placeholder`, and the doctor marketplace slug against `docs/launch/definition.json`),
`leak_refs` (the private patterns over every exported path and file), `exposure` (the exposure
scanner over the export) and `leak_scan` (the export's own commit-time scan).

Every build is REJECTED until the owner fills the two owner-name placeholders in `SECURITY.md` and
`CODE_OF_CONDUCT.md`: the placeholder is a finding, not a pre-materialisation refusal, so one run
still reports every other scan.

## Dispositions and drift

Two files record reviewed exceptions, both read from the COMMIT (never from the export, so excluding
`docs/launch/` cannot drop them):

- `docs/launch/exposure-allowlist.json`: the exposure scanner's allowlist, entries
  `{path, rule, reason}` plus exactly one of `blob` (one exact git blob) or `lines` (sha256 of each
  reviewed matched line). No entry is scoped to a bare path and rule, and the builder refuses a file
  holding one (`dispositions-malformed`). Regenerate rows, from a clean committed checkout, with `python3 tools/readiness/exposure_scan.py tracked . --json /tmp/ev.json --propose /tmp/DRAFT.json`
  (use absolute paths outside the repository for both: the scanner refuses a `--propose` path inside
  it, and omitting `--json` writes `docs/launch/evidence/tracked-<sha>.json` and `.md` INTO the tree, leaving it dirty)
  and read every row of the draft before it is given a reason; a real secret or private reference is removed,
  never allowlisted.
- `docs/launch/public-tree-dispositions.json`: the builder's own, schema
  `sigma.public-tree-dispositions/v1`, entries `{path, rule, reason}` plus exactly one of `blob` (the exact 40-hex git
  blob) or `lines` (sha256 of each reviewed matched line, counted, as in the exposure allowlist) for
  the two content rules; the builder refuses a bare path-and-rule entry (`dispositions-malformed`),
  so no disposition suppresses a whole file. A `lines` entry goes stale (REJECTED) when a reviewed
  line changes or disappears, and a new match is a finding. Where the builder differs from
  `exposure_scan.py` (each difference fails closed, as a finding or a stale entry): it hashes the RAW
  line (a CRLF line hashes with its `\r`, and a hash that `--propose` computed for a line with
  edge whitespace will not match); it counts one finding per matching line per rule, not per regex
  match; a trailing newline in a hash passes validation but such an entry can only go stale; the
  `blob` form takes 40 hex only; the rules are limited to the two content rules, and an entry under
  an excluded prefix is dropped as moot; a non-UTF-8 file is refused (`non-utf8`) rather than
  decoded with replacement. Every report finding carries its
  `line_sha256` so an entry can be written from the report. It holds the fixtures and regex sources
  that name a key header or the placeholder on purpose.

An entry under an excluded prefix is moot (dropped, counted in the report). Any other unused entry
is stale and REJECTS the build, so a fixed file cannot leave a hole behind. A finding that is new
since the last baseline is dispositioned in a normal pull request: re-run the builder, read the
report, add the entry with its reason. A finding in a file the pull request itself creates is fixed
in that file, never allowlisted. There is deliberately no repo-wide exposure test: it costs seconds
in every goal's verify and would block unrelated merges; the builder is the gate, at the export
boundary.

## The report

`~/.sigma-ops/public-tree/<commit12>-<UTC timestamp>-<pid>[-<k>].json` and a `.md` twin, mode 0600,
created once (never overwriting another run's file), schema `sigma.public-tree-report/v1`. The
report directory must be outside every work tree (`--report-dir` is refused inside one). Top-level
keys: `schema`, `verdict`, `finalise`, `generated_at`, `source`, `tools`, `review`, `sdlc`,
`exclusions`, `patterns` (the source and the count, never the content), `export` (`export_tree`,
`commit`, the planned trees, files, bytes, modes), `dispositions`, `scans`, `not_covered`,
`timings`. No matched value and no preview appears anywhere; every string is redacted against the
patterns before it is written.

The report is written BEFORE the export is renamed into place. A would-be VERIFIED build is first
written as `NOT-VERIFIED` with `finalise: pending`, then renamed, then rewritten to `VERIFIED` with
`finalise: done`. A crash between the two leaves a report that reads NOT-VERIFIED, never VERIFIED,
and the verifier refuses it.

## Controls

Each guard was broken once and seen to fail before it was trusted; the red runs, on the gestures
this page gives, are saved with the pull request:

- same commit twice: equal `tree:` and `export-commit:`; one flipped byte: a different `tree:`, and
  a byte flipped after materialising is refused `[tree-mismatch]`;
- a planted synthetic private reference (in content, in a path and in an `--exclude` value): exit 1,
  nothing at `OUT`, the token in no output, and a push of the `.rejected` directory fails;
- `--report-dir` inside a work tree: `[report-dir-inside-work-tree]`;
- every refusal code above is exit 2 with one stderr line and empty stdout;
- every `gh` call in the three tools is a literal `gh api` GET (a test reads their syntax trees); the
  write-surface ratchet has no repository verb, so it could not see anything else.

Write surface: `docs/launch/write-surface.json` records the builder's filesystem writes and ONE
`git-destructive` row, inside `_unpublish_rejected`. It is `update-ref -d` on the REJECTED or
NOT-VERIFIED export repository's own `main`, never on the source repository, and it is recorded as
`ungated` because nothing it touches can reach anything but the throwaway export. The same
function then expires the export's reflogs and runs `git gc --prune=now` in it, through the
builder's private byte runner, which the ratchet does not classify as a command call, so those (and
the `read-tree --empty` that empties its index first) are not rows; they too touch only the rejected
export repository. The verifier has
no row at all; the checker's only rows are its create-once `--json` output.

## Rehearsal

Owner-only. A rehearsal is the first push of an export, to a throwaway PRIVATE repository, so a
failure costs nothing public. Each step is the owner's, and none runs in a loop.

1. Build the export (above). Expect REJECTED until the two placeholders are filled; fill them, merge,
   and build again for `VERIFIED`. Measured on 2026-10-02 in a scratch clone of the #397 branch
   (`--review-level clean`, the real patterns file): unfilled, REJECTED with
   `scans: builder=2 leak_refs=0/0 exposure=0/0+0 leak_scan=0` (the two placeholders only); filled
   in a scratch commit with a synthetic address, `VERIFIED`. A later merge that brings a new exposure
   finding turns this REJECTED for that reason too: disposition it (below) before the rehearsal.
2. Owner: create the throwaway private repository (`THROWAWAY`, in the form `OWNER/NAME`).
3. Owner: push the export.

   ```sh
   git -C "$OUT" push "https://github.com/$THROWAWAY.git" main
   ```

4. Owner: wait for CI on all legs. One CI run is about 129 runner-minutes (34 minutes wall,
   measured on run `36952374676`).
5. Verify the pushed repository:

   ```sh
   REPORT="$HOME/.sigma-ops/public-tree/REPORT.json"
   python3 tools/verify_public_repo.py --repo "$THROWAWAY" --report "$REPORT" --expect-visibility private
   ```

   Exit 0 is every check `ok`; exit 1 prints a `FAIL <check> <detail>` line per failed check; exit 2
   is a refusal, one stderr line with a code: `windows`, `bad-slug`, `gh-failed`, or, for the report, `report-unreadable`, `report-schema` (wrong schema, no export object, or ids that are not 40 hex) and `report-not-verified` (not a finalised VERIFIED build). `visibility` reads the
   reply's `private` field, and a missing or non-boolean one is a `FAIL`, never read as public. The checks are
   `visibility`, `default-branch`, `single-commit`, `root-commit`, `branches`, `tags`, `issues`,
   `pulls`, `tree`, `ci-run`, `ci-legs`.
6. Clone the throwaway and remove its `origin` so the clone cannot push (the push-disabled pattern
   of `tools/readiness/baseline.py`), then run the fresh-profile install control from that clone:

   ```sh
   python3 tools/onboarding_control.py --sigma "$CLONE" --install claude --from-install --json "$HOME/.sigma-ops/onboarding.json"
   ```

7. In an initialised scratch repository, run the doctor from the INSTALLED copy:

   ```sh
   python3 skills/sigma-doctor/scripts/doctor.py check .sdlc
   ```

The evidence lands as `docs/launch/evidence/rehearsal-<commit12>.md` in a later pull request of the
goal. Limits, stated plainly: `--install` installs from a LOCAL clone, not a GitHub-source
marketplace add (not built here); a rehearsal repository's doctor fallback still names the public
slug; nothing here creates or changes a real repository.

## Publishing to the new repository

Owner-only, after the rehearsal above is satisfactory. Nothing here runs in a loop.

1. Build the export from the commit to publish (above) and read a `VERIFIED` report.
2. The ORDER matters. `public_repo` in `docs/launch/definition.json` (and the doctor's
   `_MARKETPLACE_REPO` constant, which the builder's doctor-slug scan requires to equal it) still
   names THIS repository today. A normal pull request on this repository (the #524 change; this
   repository itself is not renamed) updates both to the new repository's name
   (`Agrim-Intelligence/sigmaloop`), and it lands BEFORE step 1's build, so the exported tree carries the right name. The builder prints a NOTE in
   the report when `public_repo` still equals the `--repo` source repository. Then, after the build,
   the owner creates the new repository, EMPTY (no README, licence or `.gitignore`: a pre-existing
   commit would make the push a non-fast-forward and the verifier's `single-commit` check fail),
   named exactly as `public_repo` says.
3. Owner: push the one commit.

   ```sh
   git -C "$OUT" push "https://github.com/$PUBLIC_REPO.git" main
   ```

4. Wait for CI on every leg, then verify read-only (`--branch` names the branch holding the export,
   default `main`; `--legs` the expected number of CI jobs, default 5, the `ci.yml` matrix). It checks the one commit, that no other branch
   or tag exists, the CI legs and that the tree id equals the report's:

   ```sh
   python3 tools/verify_public_repo.py --repo "$PUBLIC_REPO" --report "$REPORT" --expect-visibility public
   ```

The rehearsal push (a throwaway PRIVATE repository, `--expect-visibility private`) and this push are
two separate pushes to two repositories: never publish by making the rehearsal repository public.

There is nothing to repoint: this repository keeps its name, so no clone of it changes remote.

## Recovery and scale

- A REJECTED or NOT-VERIFIED export keeps no branch, no index and no export object: `git
  for-each-ref` is empty, `HEAD` is unborn, the index is emptied (`git read-tree --empty`), every
  reflog is expired and the commit, its trees and its blobs are pruned (`git gc --prune=now`), and the
  builder proves `git cat-file -e <export-commit>` fails before it reports, else it refuses
  `unpublish-failed`. So the id the report and stdout carry cannot be pushed from there. The files
  stay on disk for inspection, and because the build is deterministic anyone can rebuild the
  IDENTICAL commit id from them by hand (`hash-object`, `write-tree`, then `commit-tree` with the
  fixed identity, date and message above) and push it: never push a `.rejected` directory. Fix the cause,
  delete `OUT.rejected`, and re-run: the build is a pure function of the commit, so re-running is
  idempotent.
- A crash leaves `OUT.partial-<pid>` and possibly a report reading NOT-VERIFIED or REJECTED. Delete
  the partial and `OUT` if it exists, re-run. The builder never overwrites `OUT` or a report.
- A failed `update-ref -d` is a refusal that removes the rejected export directory.
- Scale, measured on this repository at roughly 800 files and 17 MB: a build takes about 18 to 26
  seconds, bound by the exposure scanner (11 to 19 seconds under load); the builder's own passes are
  about 2 seconds with memory bounded by the 2 MiB per-file cap. At 10x the exposure scanner is
  extrapolated at about 2 minutes holding every blob in memory, and at 100x about 20 minutes and
  1.7 GB: the ceiling is `tools/readiness/exposure_scan.py`, not measured, filed as a follow-up.
  `--scan-timeout` (default 900 seconds) turns a hung scanner into NOT-VERIFIED.
