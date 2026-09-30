# #330 control: the launch-definition sync test, seen red

Measured on branch `sdlc/330` at commit `0c0ddf326a6a261fbe7936e7d349c03ea9c81c61`, with the
review-block-1 edits uncommitted in the working tree (sha256 of the tree measured):

- `docs/launch/definition.json` `9bf8cd5426d6e2d5d47b71b123006900f935a229098590b54208eb0a7d5fe2d2`
- `docs/launch/definition.md` `097f6351b3cb72b434bdc0e8a08b726a133326699378410cd53adba6cb725e3e`
- `tests/test_launch_definition.py` `747a0de019707349aa4c23b778d5a7195c0a2e5b9a003b8f0d5172d5c25a9c6e`

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

## Unplanted

The working tree itself: `15 passed` on Python 3.12 and on Python 3.10.
