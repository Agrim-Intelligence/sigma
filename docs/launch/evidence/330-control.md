# #330 control: the launch-definition sync test, seen red

Controls 1-4 were measured on branch `sdlc/330` at commit
`0c0ddf326a6a261fbe7936e7d349c03ea9c81c61` with the review-block-1 edits uncommitted; Control 5
and the unplanted run on commit `b311890c3d1c5e1393323eb03d5858d5689b3502` with the
review-block-2 edits uncommitted. sha256 of the files as last measured:

- `docs/launch/definition.json` `9bf8cd5426d6e2d5d47b71b123006900f935a229098590b54208eb0a7d5fe2d2`
- `docs/launch/definition.md` `32dd54ddc712ce009982eaae630e637d9056e2551ec04e662e7c328e10a85299`
- `tests/test_launch_definition.py` `f10e8d7cc7506708ab6beb0b5fce5cfa7cd846c614157478bbc970f5ac6cd3c9`

Gesture (the one the test's docstring and the issue give): `python -m pytest
tests/test_launch_definition.py -q`, run on Python 3.10.20 and on Python 3.12, with identical
results on both. Each control was planted in a throwaway copy of the three files (the test finds
its files from its own path), never in the working tree. Durations are stripped from the summary
lines below.

## Before any file existed

The test was written first: `10 failed`.

## Control 1: one supported OS changed to "windows", Markdown untouched

`supported[1].os`: `linux` -> `windows`.

```
E       AssertionError: Markdown supported rows [... "os": "linux" ..., ... "os": "macos" ...] != JSON [... "os": "macos" ..., ... "os": "windows" ...]
FAILED tests/test_launch_definition.py::test_cells_agree_both_ways[supported]
1 failed, 14 passed
```

## Control 2: status "signed" with signed_by null

`status`: `proposed` -> `signed`, `signed_on` set to `2026-09-30`, `signed_by` left `null`.

```
E           AssertionError: status is signed but signed_by is empty
E           AssertionError: status is signed but the Markdown says 'Proposed — not yet signed by the owner'
FAILED tests/test_launch_definition.py::test_status_is_proposed_or_signed_and_a_signature_is_complete
FAILED tests/test_launch_definition.py::test_markdown_status_line_agrees_with_json
2 failed, 13 passed
```

## Control 3: public_repo not in owner/name form

`public_repo`: `Agrim-Intelligence/sigma` -> `sigma`.

```
E           AssertionError: public_repo is not owner/name: 'sigma'
E       AssertionError: assert 'Agrim-Intelligence/sigma' == 'sigma'
FAILED tests/test_launch_definition.py::test_public_repo_when_set_is_an_owner_name_slug
FAILED tests/test_launch_definition.py::test_scalar_decisions_agree[public_repo-## What ships-Public repository]
2 failed, 13 passed
```

## Control 4: every field changed in only one copy (review block 1)

The first version of this test checked only the `supported` rows in both directions; a JSON-only
change of `artifact`, `public_repo`, `version`, `experimental` or `out_of_scope`, or a
Markdown-only change of the public repository, the version or an out-of-scope bullet, stayed
green. Each plant below changes one field in one file only. Every one is red, on both Pythons:

```
json artifact                    RED  1 failed, 14 passed
json public_repo                 RED  1 failed, 14 passed
json version                     RED  2 failed, 13 passed
json audience                    RED  1 failed, 14 passed
json experimental codex only     RED  1 failed, 14 passed
json out_of_scope empty          RED  1 failed, 14 passed
json supported linux python      RED  1 failed, 14 passed
json supported drop linux        RED  1 failed, 14 passed
json status signed               RED  1 failed, 14 passed
json signed_on 2026-02-30        RED  2 failed, 13 passed
md artifact                      RED  1 failed, 14 passed
md public repo                   RED  1 failed, 14 passed
md version                       RED  1 failed, 14 passed
md version tag                   RED  1 failed, 14 passed
md audience                      RED  1 failed, 14 passed
md delete slack bullet           RED  1 failed, 14 passed
md add out-of-scope bullet       RED  1 failed, 14 passed
md extra supported row           RED  1 failed, 14 passed
md supported python              RED  1 failed, 14 passed
md drop cursor row               RED  1 failed, 14 passed
md extra experimental row        RED  1 failed, 14 passed
md status signed                 RED  1 failed, 14 passed
plants that stayed green: 0
```

`json signed_on 2026-02-30` is a signed JSON with a date that matches YYYY-MM-DD but is not a
calendar day: the signature check fails on the date, and the Status-line sync fails too.

## Control 5: signature rules shared with decide.py, and the configured-repo hazard (review block 2)

Review block 2 found the rename list omitted `discovery.github.repo`, and that this test accepted
signatures #331's readiness decider (`decide.py`) rejects. The new rename-section test was written
first and was red against the unfixed page on both Pythons (`1 failed, 15 passed`). Each plant
below was run against the new test and against the block-1 test (`HEAD`); the signed plants also
set the Markdown Status line to match, so only the rule under test can fail:

```
plant                                new test            block-1 test
json duplicate key                   RED  12 failed, 4 passed   green 15 passed
signed_by '@swapnil-agrim'           RED  1 failed, 15 passed   green 15 passed
signed_by 'a/b'                      RED  1 failed, 15 passed   green 15 passed
signed_by 40 chars                   RED  1 failed, 15 passed   green 15 passed
signed_on future 2099-01-01          RED  2 failed, 14 passed   green 15 passed
md drops discovery.github.repo       RED  1 failed, 15 passed   green 15 passed
valid signature (must stay green)    green 16 passed            green 15 passed
```

Identical on Python 3.10.20 and 3.12.13.

## Unplanted

The working tree itself: `16 passed` on Python 3.12 and on Python 3.10.

## Control 6: the owner's rename-first decision, and the rebase (2026-10-01)

Measured on the working tree on top of branch head `093a1a4b299636cadeabd411e058fd3864142f67` (the
rebase onto `origin/main` `880de21ecebb7b73ec993c9ea7ad8e2323192c82`), with these edits to
`docs/launch/definition.md` uncommitted: the `**Public repository:**` bullet (the owner's
rename-first decision), the `setup.py` and README line citations that the rebase moved, and the CI
coverage sentence that #338 made stale. sha256 as measured:

- `docs/launch/definition.md` `28c00e66938a4ec6f5aa3da5730c96b74d61b35c955c0e83e0a4e0ca0afd495e`
- `docs/launch/definition.json` `9bf8cd5426d6e2d5d47b71b123006900f935a229098590b54208eb0a7d5fe2d2`
- `tests/test_launch_definition.py` `f10e8d7cc7506708ab6beb0b5fce5cfa7cd846c614157478bbc970f5ac6cd3c9`

The bullet's prose changed, and the test reads that bullet by its shape (the first backticked
value). Gesture: `python -m pytest tests/test_launch_definition.py -q`, Python 3.12.13, each plant
made in the working tree and restored from a copy kept under `~/.sigma-ops/`:

```
unplanted                                      green  16 passed
json public_repo -> Agrim-Intelligence/other   RED    1 failed, 15 passed
md bullet slug un-backticked                   RED    1 failed, 15 passed
restored                                       green  16 passed
```

Not re-measured on Python 3.10 here; Controls 1-5 above were.
