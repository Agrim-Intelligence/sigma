# B4-1 control evidence — goal 1439

Status: implementation/focused controls only. Full repository verification and current-PR-head
Linux/macOS/Windows native CI are still required before delivery. This is not parity certification.

## Own-clause controls

The final machine-generated [manifest](1439-mutation-manifest.json) records 189 deliberately
broken variants: 189 killed, zero survivors, zero syntax/collection-invalid runs. Each variant was
executed independently against the real focused tests and the original source bytes restored.
Measured control-run time was 63.055421208 seconds, not an inventory throughput measurement.

The matrix covers rejecting predicates and their individual Boolean/comparison clauses, all
22 argument/return declarations of the 11 foreign functions, budget/source arithmetic, complete
ancestor traversal, encoded/decoded expansion, directory device/inode identity, Windows GUID
volume selection, bytecode suppression under the literal documented CLI, and the actual CI
validator. Both source and CI order checks were separately weakened to membership; both went red.
Source-only variants omit only the independent workflow-validator test; workflow variants execute
that actual validator against intact and corrupted receipts for all three platform shapes.

Earlier runs found real coverage gaps and three malformed-fact error paths: unverified cgroup
provenance could break refusal serialization, a malformed disk snapshot could escape as TypeError,
and POSIX close failure could escape untyped. Tests first reproduced those failures; the current
implementation returns named refusals. Redundant checks were removed rather than credited as
independent controls when another invariant already rejected exactly the same input.

Pinned bytes at the final control run:

- tests: `c82559e7d000602e115bfda0f759b0af5ff152f1f3951fc0c191b22aed510d15`
- collector: `bb77d1a1a0199c9660fdf6e98dc74d543e501e50e06a34da7b3367d98b45d856`
- calculator/CLI: `7117f0f6b371d77c6e9d1fdff74da3f7302a51ac8180f28063e19ac52be715ad`
- workflow: `4581ac3e0c9c56606cc64981e1d072b48b71f67005a1d81bb8f218f646ad1e07`
- complete raw control report: `d2919a8aeee5c2d67455d7834dd723c09c1b78bdc79776a2b0c42d827cd14bfa`

The manifest retains each mutation and its first failing test for targeted reproduction. Apply
one variant only in an isolated disposable worktree at the pinned bytes, run its named focused
test (or the complete focused test file), then restore and rerun intact. Do not mutate a shared
root checkout. The ephemeral diagnostic harness itself is not a shipped runtime command.

## Native development measurement

The exact documented gesture succeeded on this real macOS host with Python 3.12.13 at
2026-10-15T11:38:25.999681Z, against the collector/CLI bytes pinned above. Its native source rows
were HOST_VM_INFO64 and unlimited RLIMIT_AS; the volume probe was fstatvfs. Raw local receipt
SHA-256: `98a13e22ba2ff402d8ce2bd1339c277271f6fe1d196c9c4977d5a6e1df207485`.
The raw local receipt is retained outside the checkout; personal machine paths are not published
in this summary. This local run is not a substitute for the three current-PR-head CI receipts.

Available memory at that snapshot was 9,613,606,912 bytes; usable was 7,210,205,184 bytes,
with 12 worker slots and a 3,605,102,592-byte output ceiling. These are observed snapshot facts,
not fleet defaults, reservations, or measured scan speed. Linux and Windows local fixtures are
labelled simulations; native availability on those targets remains unmeasured until CI runs.

## Verification scope and limitations

The focused acceptance command passed 54 tests after restoration. The repository's literal
direct-GitHub ratchet gesture is additionally required. The initial complete repository baseline
had 18,065 passes and seven failures before production implementation; it is not green evidence.
Two reproduced reviewer-fixture failures came from the real desktop Codex CLI leaking into a
test that assumes that binary is absent; all 113 reviewer tests passed with that CLI directory
excluded from diagnostic PATH. Final verification must disclose its environment and run the
complete configured command without the temporary red-only diagnostic plugin.

The plan's row-level scale shorthand is refined in the operator document: Linux has O(depth)
rows/probes, but path work and receipt bytes follow the sum of ancestor path lengths, potentially
quadratic in nesting depth. No runtime performance or complete-host parity is inferred from counts.
