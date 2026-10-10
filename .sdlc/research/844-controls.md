# #844 controls (P5 IMPLEMENT)

Gesture: the documented one, `python -m pytest tests/test_breaker.py -q -p no:cacheprovider`
(no CLI exists; no stronger flag). Each control edits `skills/sigma-loop/scripts/breaker.py` once,
runs the file, records which tests go red, restores the file, and `diff` against a pristine copy
confirmed byte-identical after every control and at the end. Baseline both interpreters: 39 passed.

Red witness: with the original `breaker.py` the 22 planned ids each failed by assertion (22
`assertion` rows in the goal's witness journal, file hash matching the final test file); the helper
then reported passed true after the implementation.

Results (host Python 3.9.6, then a 3.12 venv; every row red on both unless stated):
- heading: drop the `criteria` call and use the underscore-only pattern -> 4 heading tests red.
- fallback: `except ValueError` removed -> fallback test red; `_BULLET_ANY` requires a checkbox -> fallback test red.
- verdict shape: add an `id` key -> verdicts test and the 535 test red; one verdict for the whole list -> verdicts test, brief-drop and mixed tests red; `criterion` is the RAW item -> verdicts, brief-drop, residual-gap tests red.
- scrubbed-to-empty: treated as actionable -> verdicts, brief-drop, mixed tests red; `brief` refuses on any dropped criterion -> brief-drop and mixed tests red; stop dropping -> brief-drop test red.
- mixed refusal: re-run of actionability on survivors removed -> mixed test red; require ALL survivors actionable -> brief-drop and mixed tests red (advisory half).
- scrub: old `\b(def |class |import |return |self\.)` arm restored -> prose-keeps test red; bare-return, compound and semicolon arms deleted -> scrub test and residual-gap test red; return-with-operator and `self.` arms deleted -> the green-before guard `test_scrub_still_removes_code_shaped_lines` (and `test_no_channel_still_flags_code_diff_and_path`) red, proving the guard can fail; assignment arm or raise/assert arm added -> residual-gap test red; `_LEAK` compiled with `re.I` -> prose-keeps test (capitalised "Return nothing") red.
- validator, one break each, each turning its matching test red: skip `..` check; skip stem-directory check; skip nested-dir check (all path-escape test); accept non-.py (non-python test); drop name-prefix rule (name test); skip `ast.parse` (syntax, non-python, missing-test, import, grammar and every-finding tests); skip the `test_` search; count nested functions (missing-test test); skip module-top import check (import and fail-closed tests); reject lazy imports (accept test); remove the `getattr` fallback (fail-closed test plus others, since the 3.9 host lacks the attribute and the 3.12 venv has it deleted by the test); drop duplicate check (name test); drop prose-outside check; drop stray-fence check; allow header inside open block; stop normalising CRLF (accept test); raise total cap and raise block cap (cap test); return on first finding (grammar and every-finding tests).
- Remove `RecursionError`/`MemoryError` from the except tuple: red on 3.12 only (its `ast.parse` raises them on a 15000-term chain). NOT red on 3.9.6, where `ast.parse` raises neither for any input tried, so on the 3.9 host this guard is unexercised; stated, not hidden.

Known pinned behaviour: lowercase prose that reads like a statement is blanked and reported by `criterion_verdicts`; capitalised "If empty: return None" is NOT blanked (case-sensitive), the lowercase "if empty: return None" is.
