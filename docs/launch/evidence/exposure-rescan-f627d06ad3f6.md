# Exposure and private-reference scans repeated at commit f627d06ad3f6

Dimension D10. Measured commit: `f627d06ad3f6fe391936a0e0bb1e6061f2af6aa4` (origin/main when run, 2026-10-12).
Run locally on macOS, Python 3.12, from a clean worktree of that commit. This is NOT GitHub CI. The private
patterns file stayed outside every repository; only counts are recorded here, never a pattern or a matched value.
This repeats the scans of `exposure-8aee0c74c526.md`, `refs-8aee0c74c526.md` and `exposure-disposition-8aee0c74c526.md`
on the current commit and, new, on the exported public tree.

## Result

| # | Scan | Target | Exit | Count |
|---|------|--------|-----:|-------|
| 1 | `exposure_scan.py history` | source, 250 commits | 1 | 488 raw findings (390 distinct blobs); history mode reads no allowlist |
| 2 | `exposure_scan.py tracked` with the committed allowlist | source, 1850 tracked files | 0 | 0 findings, 70 allowlist entries covering 70 findings, 0 stale, 0 skipped |
| 3 | `exposure_scan.py refs` | source remotes and tags | 0 | 28 remote refs, 1 local-only tag, 3 owner-checklist items |
| 4 | `leak_refs.py tree` with the private patterns | source, 1850 files, 250 commits | 0 | 0 hits |
| 5 | `build_public_tree.py` at `--review-level clean` | export of the same commit | 0 | VERIFIED; tree `199248718f3f959501911a9c673b173aa2d3ce50`, export commit `e4a729663d175ea2557706ba07da4d0baf2964bd`, 1237 files, 24,496,806 bytes; builder's own scans line `builder=0 leak_refs=0/0 exposure=0/0+0 leak_scan=0` |
| 6 | `leak_refs.py tree` with the private patterns | export tree, 1237 files, 1 commit | 0 | 0 hits |
| 7 | `exposure_scan.py history` | export tree, 1 commit, no allowlist | 1 | 66 raw findings |
| 8 | `exposure_scan.py tracked` | export tree, committed allowlist read from its HEAD | 0 | 0 findings, 66 allowlist entries covering 66 findings, 0 stale |

Exit codes 1 on rows 1 and 7 mean "findings present in history mode", not a crash. Scan 7 is the same 66 findings
that scan 8 shows are covered by the export's own committed allowlist.

## What the 488 history findings are, by location (class counts only)

| Location class | Count |
|----------------|------:|
| `tests/` (fixtures and detector controls) | 336 |
| `skills/`, `tools/`, `hooks/`, `evals/` (detector literals, fixture strings) | 120 |
| `.sdlc/` (two historical plan and research records) | 23 |
| docs and community files | 9 |
| historical non-shipped paths | 0 |

By rule: email-address 230, auth 88, absolute-home-path 55, credential-assignment 39, aws-key 36, private-key 17,
gh-token 15, url-password 3, jwt 3, sk-key 1, slack-token 1. The nine docs and community findings are all
rule `email-address`. Per the allowlist's own reasons: the owners' role address published on purpose by the owner's
decision of 2026-10-12 (in the code-owners file and the launch definition), the owner-approved maintainer contact in
the code of conduct and the security policy, a decorator in a recorded mutation diff, and an ssh error line naming
the public git user. The 70 tracked-mode allowlist entries carry a human-written reason each
(`docs/launch/exposure-allowlist.json`); this run did not re-judge those reasons.

## Comparison with the earlier record, and what this does not show

- The earlier history scan at `8aee0c74c526` reported 233 raw findings; this one reports 488. The growth is measured,
  not explained away: 321 of the 488 do not match an earlier finding on (rule, blob, line), because the blobs are new
  (more fixtures and detector tests added since, and a rename of the skill directories). That is a count comparison, not a
  proof that each new finding is a fixture; the class table above is by path prefix only.
- NOT done: a per-finding disposition of the 488. The earlier disposition record
  (`exposure-disposition-8aee0c74c526.md`) routed the historical absolute-home-path findings to blocker issue #400; this
  run did not re-read or re-disposition any of the new findings individually, so "every raw finding is a fixture or a
  published role address" is an inference from the path class and from `tracked` mode returning 0 on the current files,
  not a measurement of each blob.
- The history of the SOURCE repository is the 250 commits above. The export has one commit by construction, so
  nothing in rows 6 to 8 says anything about the history that is already in the source repository.
- Secret scanning and push protection on the public repository remain an owner action (the refs scan's checklist
  still lists them, plus the fate of the backup tag and the remote branches).
- `leak_scan` inside the builder is labelled by the tool as a commit-time scan whose key-body rule is known incomplete
  (#433); the pattern-scan line-wrap gap recorded in `export-verify-de188d1fb5dd.md` is not re-tested here.

## Commands (placeholders for local paths)

```
python3 tools/readiness/exposure_scan.py history . --patterns <owner patterns file> --allowlist docs/launch/exposure-allowlist.json --json <out>
python3 tools/readiness/exposure_scan.py tracked . --patterns <owner patterns file> --allowlist docs/launch/exposure-allowlist.json --json <out>
python3 tools/readiness/exposure_scan.py refs . --json <out>
python3 tools/leak_refs.py tree --patterns <owner patterns file>
python3 tools/build_public_tree.py f627d06ad3f6fe391936a0e0bb1e6061f2af6aa4 --out <dir> --patterns <owner patterns file> \
    --repo Agrim-Intelligence/sigma --allow-public-source --review-level clean --report-dir <dir>
python3 tools/leak_refs.py tree --patterns <owner patterns file> --root <export dir>
python3 tools/readiness/exposure_scan.py history|tracked <export dir> --patterns <owner patterns file> [--allowlist ...]
```

A first attempt passed the repository slug where `exposure_scan.py` requires a working-tree path; the tool refused
(history, tracked: "repository must be a working tree") or raised (refs). Those three runs are discarded and the table
above is from the reruns with `.`. `--review-level clean` reaches only the clean-tree proof; the landed, pr-merged and
owner-merged levels were not requested, and no push of the export happened.
