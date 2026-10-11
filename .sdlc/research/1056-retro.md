# Retro 1056 - mutation engine (slice 1 of story 808, epic 1055)

Grade: achieved. All five acceptance criteria have evidence; tests/test_regression_mutators.py: 23 passed (21s), `anchors` lists 7 lines.

## Intent vs shipped
- evals/regression/mutators.py (190 lines, stdlib): ast-anchored `apply` and `anchors`, exits 10/11/12 named on stderr, 2 for unknown regression/form.
- Four secret-scan variants plus both code regressions edit only the named span; double-context appends the measured words to one phase file and the budget gate goes 0 -> 1 (tested on a tmp copy).
- Inventory row for `_write_text` landed in this slice (design had assigned it to slice 5; research caught that), plus the DOCUMENTED_WRITES entry in tests/test_regression_ledger.py.

## Residual debt
- `drop-redactor-only` reading is a default, not a ruling: shipped as dropping the `authorization-header` entry of `_SECRET_PATTERN_SPECS`. The other reading (remove the name from `_REDACTOR_ONLY`) widens the commit gate and disables nothing. Goal 806 must confirm or change one table row.
- Rename hint (`candidates:`) only sees an uncommitted rename; after commit `git diff HEAD` is empty. Slice 2 / goal 879 needs a base ref for the PR form.
- `_candidates.root` is module-level mutable state set by `apply`; fine single-threaded, wrong if `apply` is ever called concurrently. Pass root explicitly if that changes.
- Second `apply` of a `drop-element` mutation exits 10 (entry gone), not 12; only body/value mutators reach `mutation-noop` on re-apply. Documented exit meaning is slightly narrower than the name suggests.
- `_find` counts same-named defs anywhere (methods, nested), so a future duplicate helper name in work.py reads as 11; that is intended but will surprise.
- double-context also raises other phases that share the doubled file; acceptable, not asserted per phase.
- Prose form of double-context is unregistered (pending 805 cards, design D-2).
- Not measured: Windows, Python 3.10 (stated in the changelog).

## Lessons
- A design table entry with two readings (`drop-redactor-only`) should be resolved at design review, not defaulted in implement; the cost here was one flagged open point.
- Resolving entries as Tuple children of the NAMED assignment avoided the `_REDACTOR_ONLY` string trap; a test pins it.
- Research found the missed write-surface inventory before the ratchet went red; keep reading the scanner rules at research for any new writer.
- Controls were run, not assumed: rename gives 10, duplicate def gives 11, repeat gives 12, and the guard test fails rather than skips on a rename.
