# Public snapshot: building the export

One reviewed commit of this private repository becomes the tree of a fresh one-commit public
repository. `tools/build_public_tree.py` builds that tree deterministically, scans it, and writes a
report outside every repository. It creates, renames and pushes nothing: the owner does those, and
the commands are in [the name handover](name-handover.md) and below. The goal behind it is #397;
the release checklist, pin and rollback are #359's.

Everything here is host-agnostic: the builder, the verifier and the checker are plain Python on
git and `gh api` GET calls, and nothing depends on a hook or on a host.

## What it is

- **The builder** (`python3 tools/build_public_tree.py`) takes a COMMIT and writes the export
  repository to `OUT`: one commit, no parent, branch `main`, a fixed neutral author and committer
  (`sigma-public-snapshot`), the commit date of the source commit, and the message `Initial public
  snapshot (Sigma <plugin version>)`. The export's commit id is therefore a pure function of the
  source commit: the same commit twice gives the same tree and the same export commit, and one
  changed byte changes both. No reflog is written (`core.logAllRefUpdates=false` on every ref
  update), so the export's `.git` records no operator name, e-mail or host.
- **The verifier** (`python3 tools/verify_public_repo.py`) reads a PUSHED export over REST and
  proves it is exactly that one commit with CI green on every leg.
- **The report** is a JSON file and a `.md` twin, written outside every repository. It names the
  commit, the verdict and every scan.

The builder never runs a hook, never uses `git add` or `git archive`, never writes to the source
repository (it reads it with `--no-optional-locks` and a private object directory), and refuses
loudly where a guarantee cannot be made: Windows, a non-sha1 repository, a symlink, a gitlink, a
case collision, a secret-looking file name, a binary or oversize or non-UTF-8 file.

## Build

The build is run by the owner, from a clean detached checkout of the commit to export (the builder
refuses a dirty checkout or one whose `HEAD` is not the commit, and it compares its own running
copy of itself and of the three scanners with the commit's bytes). `$PATTERNS` is the private
patterns file kept outside every repository; its content is never printed or hashed anywhere.

```sh
git fetch origin main
COMMIT=$(git rev-parse origin/main)
SRC="$HOME/.sigma-ops/public-src"
OUT="$HOME/.sigma-ops/public-out"
PATTERNS="$HOME/.sigma-ops/leak-patterns.txt"
REVIEW_REPO="OWNER/NAME-OF-THE-PRIVATE-REPOSITORY"
git worktree add --detach "$SRC" "$COMMIT"
cd "$SRC"
python3 tools/build_public_tree.py "$COMMIT" --out "$OUT" --patterns "$PATTERNS" --repo "$REVIEW_REPO"
```

`git fetch` and `git worktree add` write to the shared repository; they are the owner's steps and
the builder never runs them. `--patterns` may be replaced by `SIGMA_LEAK_PATTERNS`; one of the two
is required and there is no default path.

Flags (`python3 tools/build_public_tree.py --help` is the authority): `--out DIR` (required),
`--source DIR`, `--patterns FILE`, `--sdlc exclude|include`, `--exclude PREFIX` (repeatable),
`--review-level clean|landed|pr-merged|owner-merged`, `--repo OWNER/NAME`, `--remote NAME`,
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
`not-a-repo`, `bad-commit`, `object-format`, `bad-exclude`, `exclude-matches-nothing`, `symlink`,
`gitlink`, `bad-mode`, `bad-path`, `secret-file-name`, `case-collision` (two paths equal after
case folding and Unicode NFC normalisation), `head-not-commit`,
`dirty`, `tool-mismatch`, `tool-missing`, `no-remote-ref`, `not-landed`, `repo-required`, `bad-slug`,
`gh-failed`, `review-repo-public`, `not-pr-merged`, `not-owner-merged`, `dispositions-malformed`,
`tree-mismatch`, `oversize`, `binary`, `non-utf8`, `report-exists`, `unpublish-failed`,
`work-tree-unknown`, `git-failed`, `bad-ref`, `bad-timeout`, `internal`; `scan-failed` is the
NOT-VERIFIED refusal below.

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
  `none`, or `unreadable`). The review repository must be PRIVATE: after the name handover the old
  slug is the public repository and the level refuses it (`review-repo-public`).
- `owner-merged`: the merger holds the `admin` or `maintain` role on the review repository.

The REST levels need `--repo OWNER/NAME` and make GET-only `gh api` calls; nothing writes. An
approve-marker level and a signed-tag level are stronger and are NOT built: most recent merges
carry no approve marker and no signing is configured, so only these four are checkable. The report
never says "reviewed commit": it says what was measured.

## What the scans do not cover

Four scans all run, with no short circuit, and the verdict is REJECTED if any finds anything. The
report prints, in `not_covered`, what they cannot see:

- header-less private key bodies (#433): the builder's own header rule and `tools/leak_scan.py`
  find a key header, not a body with no header;
- `tools/readiness/exposure_scan.py` de-duplicates its rules by name, so a second rule of one name
  never runs there;
- file NAMES are checked against the private patterns only;
- the origin-owner rule of `tools/leak_scan.py` is skipped, because the export has no origin;
- names absent from the patterns file, and e-mail addresses, which `tools/leak_scan.py` does not
  check;
- destination GitHub state beyond what `python3 tools/verify_public_repo.py` counts;
- an unpinned disposition suppresses every match of its rule anywhere in its file, so a NEW real
  value of that rule in that file would ship. The report's `unpinned_dispositions` lists each one so
  the owner sees every whole-file suppression.

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
  `{path, rule, reason}`. Only `.sdlc/**` entries carry a `blob`, so they are pinned to the exact
  bytes; entries for `tests/`, `skills/` and `tools/` are unpinned because those files change in
  every goal.
- `docs/launch/public-tree-dispositions.json`: the builder's own, schema
  `sigma.public-tree-dispositions/v1`, entries `{path, rule, reason[, blob]}` for the two content
  rules. It holds the fixtures and regex sources that name a key header or the placeholder on
  purpose.

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
builder's private byte runner, which the ratchet does not classify as a command call, so those two
are not rows; they too touch only the rejected export repository. The verifier has
no row at all; the checker's only rows are its create-once `--json` output.

## Rehearsal

Owner-only. A rehearsal is the first push of an export, to a throwaway PRIVATE repository, so a
failure costs nothing public. Each step is the owner's, and none runs in a loop.

1. Build the export (above). Expect REJECTED until the two placeholders are filled; fill them, merge,
   and build again for `VERIFIED`.
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
   is a refusal (the report is not a finalised VERIFIED one, or `gh` failed). The checks are
   `visibility`, `default-branch`, `single-commit`, `root-commit`, `branches`, `tags`, `issues`,
   `pulls`, `tree`, `ci-run`, `ci-legs`.
6. Clone the throwaway and remove its `origin` so the clone cannot push (the push-disabled pattern
   of `tools/readiness/baseline.py`), then run the fresh-profile install control from that clone:

   ```sh
   python3 tools/onboarding_control.py --sigma "$CLONE" --install claude --from-install --json "$HOME/.sigma-ops/onboarding.json"
   ```

7. In an initialised scratch repository, run the doctor from the INSTALLED copy:

   ```sh
   python3 skills/agrim-doctor/scripts/doctor.py check .sdlc
   ```

8. Rename rehearsal: follow the rehearsal block printed by `python3 tools/handover_check.py sequence`
   (see [the name handover](name-handover.md)).

The evidence lands as `docs/launch/evidence/rehearsal-<commit12>.md` in a later pull request of the
goal. Limits, stated plainly: `--install` installs from a LOCAL clone, not a GitHub-source
marketplace add (not built here); a rehearsal repository's doctor fallback still names the public
slug; nothing here creates, renames or changes a real repository.

## Recovery and scale

- A REJECTED or NOT-VERIFIED export keeps no branch and no export commit: `git for-each-ref` is
  empty, `HEAD` is unborn, every reflog is expired and the commit is pruned (`git gc --prune=now`),
  and the builder proves `git cat-file -e <export-commit>` fails before it reports, else it refuses
  `unpublish-failed`. So the id the report and stdout carry cannot be pushed from there. The files
  stay on disk for inspection (and the index keeps their blobs), so a person can still commit and
  push them by hand: never push a `.rejected` directory. Fix the cause,
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
