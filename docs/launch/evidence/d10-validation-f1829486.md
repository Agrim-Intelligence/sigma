# D10 validation on main at f1829486 (2026-10-13)

Dimension D10 (security, privacy, exposure). Run on a clean checkout of `origin/main` at `f1829486`, locally on macOS,
one command at a time, with the private-pattern file supplied from outside the repository. Only counts and exit codes
are recorded; no matched value and no pattern content appears here.

## What was run

| # | Check | Exit | Result |
|---|-------|-----:|--------|
| 1 | `exposure_scan.py tracked .` (allowlist applied) | 0 | clean; oversized=0 binary=0 |
| 2 | `exposure_scan.py refs .` | 0 | 28 remote refs, 1 local-only tag |
| 3 | `leak_refs.py tree` (private-reference scan) | 0 | hits: 0 |
| 4 | `build_public_tree.py HEAD --review-level clean` (export + all scans) | 0 | verdict VERIFIED; scans builder=0 leak_refs=0/0 exposure=0/0+0 leak_scan=0; peak resident 106 MB |
| 5 | `tools/rename_check.py` | 0 | clean |

Tests, one file at a time, no `-n`: `test_hostile_inputs` 70 passed, 7 xfailed; `test_threat_model` 3; `test_readiness_exposure_scan` 26;
`test_leak_scan` 119; `test_no_private_names` 18; `test_scrub` 50; `test_post_guard` 93; `test_write_surface` 33. All pass.

## Control (the scanner goes red on a planted secret)

A throwaway local commit in a scratch worktree (never pushed) added a file with a token-shaped string built at run time
and an absolute home path. `exposure_scan.py tracked .` exited 1 and named both rules (`gh-token`, `absolute-home-path`)
with the values redacted. After undoing the commit the same scan exited 0.

## The 7 expected failures in the hostile-input suite

They are strict, known-defect markers, not passes: `H02` and `H03` (issue 511: the blocker scan reads a dependency marker
inside a fenced code block or HTML comment) and `H08` (three cases) and `H11` (two cases) (issue 190: the status block
refuses to render when a claimed title contains the field separator). Both issues are open, `launch:next`. They are
robustness bugs; none is a secret, a write outside the documented surface, or an injection that executes.

## Blockers

Open `launch:blocker` issues, read over REST: 4 (the public-repository build, the owner-side rehearsal, the owner decisions
and the legal and naming check). None is a B2, B7 or B8 finding. The history finding formerly open as a blocker (9 findings
in old records, the owner's local account name in absolute home paths; personal data, not a secret) was decided by the
owner on 2026-10-13: accepted, label removed, issue closed; see `exposure-history-disposition-793a1ed612f6.md`.

## What this does NOT show

- **The model-level injection drill has not run.** The throwaway private repository and the three hostile issues are
  prepared; the single supervised model pick belongs to the owner (the tool forbids Sigma from launching it), so no
  model has been shown to follow or decline hostile text. The state diff in `injection_drill.py check` would be the evidence;
  its egress check is a coarse tripwire and a pass would not be proof.
- The scans read committed blobs: nothing about the working tree, blobs over 2 MiB, binary files, secrets split across
  lines, or values no rule recognises.
- Linux and Windows were not run for this record; the tracked, refs and private-reference scans are platform-independent
  but were only exercised on macOS here.
