# #1059 retro (P7, advisory)

## Intent vs shipped
- Intent (acceptance/1059.md): `matrix.py check` fails on missing/invalid evidence, `control.py run --record` writes evidence for seen-red pairs only (atomic via temp + os.replace), matrix.json holds real evidence, README states the record is a claim, ledger/write-surface list the new write.
- Shipped: matrix.py (159 lines), control.py --record, matrix.json (104 lines), README, write-surface.json/md, tests (control, ledger, matrix), CHANGELOG, .gitignore for the temp file.
- DEVIATION: committed evidence in matrix.json is recorded against origin/main df5a6e13, not branch HEAD. Squash merges drop branch commits, so a HEAD-recorded commit would point at nothing after merge. README documents the recipe (`--repo <worktree of origin/main> --matrix ...`).

## Residual debt
- CI is shallow, so object (commit-existence) checks are skipped there and print UNVERIFIED.
- No workflow runs control.py yet; the live check is operator-run today.
- The pin detects re-pointing of row/gate only, not drift in mutator or test content.
- Commit existence does not prove reachability from main.
- A row added by a PR is recorded on its own head; after the squash it is orphaned and the owner must re-record on main.
- Concurrent --record is last-writer-wins; serial only.

## Proposed lessons
1. `phase_report.py end` needs `--agent-id` for every phase, or the cost line degrades to unavailable.
2. Plan-review must run after the final plan edit; a review of a superseded plan certifies nothing.
3. Backticked `evals/...` paths in CHANGELOG must exist in the shipped snapshot, or the doc-path guard goes red.
