Blocked by #519

Slice 2 of 4 of the reviewed section-8 v2 design. Model: daily — corpus harvesting and threshold tuning need judgment, but against a settled spec.

## Scope (8b)

New `tests/test_goal_size_corpus.py`: two-direction validation of `goal_size.classify` against CHECKED-IN fixtures — never live `gh` fetches at test time (`verify.command` gates every future goal's `done`, so a GitHub outage must not be able to refuse the whole backlog).

- Harvest once, at implementation time: the 8 real epic bodies of this repo's issues #287-#294 as fixture files -> assert **flagged**.
- Real small-issue bodies (#488, #499, #500, #505, #506 — all genuinely small) as fixtures -> assert **NOT flagged**.
- Both directions, so a classifier that flags nothing — or everything — fails loudly, naming the offending fixture.
- Threshold tuning happens here, in one place, against pinned inputs: if a threshold must move, it moves in `goal_size.py` module constants with the corpus proving both directions still hold.
- Sanitize fixture text before check-in: replace any org/owner/account strings with neutral `acme`-style placeholders per this repo's scrub norms (the classifier measures structure and size, not names; keep replacements length-similar). The repo's denylist grep on added lines must pass.
- CHANGELOG entry under `## Unreleased`.

## Acceptance criteria

- All 8 epic fixtures flagged; all 5 small fixtures not flagged; assertion messages name the fixture.
- Fixtures checked in under `tests/` (e.g. `tests/fixtures/goal_size/`), loaded from disk, no network.
- Full gate green.

## Verification

```
python3 -m pytest tests/test_goal_size_corpus.py -q && python3 -m pytest tests/ -q --cov=skills --cov=hooks --cov-fail-under=85 && python3 evals/run.py
```

