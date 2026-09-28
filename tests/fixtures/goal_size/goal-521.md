Blocked by #519 (independent of the other slices; may land any time after it)

Slice 4 of 4 of the reviewed section-8 v2 design. Model: daily — narrow change, but the dedup semantics are subtle.

## Scope (8d)

`backlog_check.py`: in the confident **duplicate** and **obsoleted-by** flag paths ONLY, exempt a goal whose doc's FIRST LINE carries `sigma:decomposed-from=`.

Rationale (two distinct reasons, one per path): the duplicate path's `_earlier()` one-sided rule always parks the NEWER of a similar pair, and a freshly created decomposition child is always the newest — so a child similar to its own parent or siblings would always be the one parked. The obsoleted-by path has no `_earlier()` gate but compares against closed work, which a freshly authored child can also spuriously resemble. Both flags are wrong for a deliberately authored decomposition child.

Explicitly NOT a blanket early-return: `_explicit_blockers()` and every other signal still apply to marked children in full. The 500-char mirror excerpt keeps a first-line marker visible, so the check works on mirrored excerpts too.

Inert in this repo today (`backlog_check.enabled: false`); this is correctness for any adopting repo that enables both features.

CHANGELOG entry under `## Unreleased`.

## Acceptance criteria (tests)

- exemption fires for a marked child vs an older similar issue (duplicate path)
- exemption fires on the obsoleted-by path for a marked child resembling closed work
- does NOT fire for two unrelated similar issues (the dedup-not-weakened pin)
- does NOT disable blocker enforcement on the same marked child
- mid-body marker does NOT exempt (first-line anchoring)
- full gate green

## Verification

```
python3 -m pytest tests/ -q --cov=skills --cov=hooks --cov-fail-under=85 && python3 evals/run.py
```

