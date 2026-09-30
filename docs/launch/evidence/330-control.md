# #330 control: the launch-definition sync test, seen red

Measured on branch `sdlc/330`, base commit `b67a3e38e8e6a207f8486b563f0e5c5e65f00851`, with the
goal's three files uncommitted in the working tree (sha256 of the tree measured):

- `docs/launch/definition.json` `2ef8185d9cf68fd07934685fabab66a4200069eeff8adf799a52ee3e5a0f08af`
- `docs/launch/definition.md` `048cbf6639931dd002d1fa9c0a0f7219a1b1a22102f58c872fbd4f5d1a1202d7`
- `tests/test_launch_definition.py` `2dc3f0fa3106d2ff70fa9d07bf266a34a0099387c2d639a21bb7ed8b99b203fd`

Gesture (the one the test's docstring and the issue give): `python -m pytest
tests/test_launch_definition.py -q`, run on Python 3.12. Each control edited only the JSON, and
the JSON was restored from a copy and checked byte-identical (`cmp`) afterwards.

## Before any file existed

The test was written first: `10 failed`.

## Control 1: one supported OS changed to "windows", Markdown untouched

`supported[1].os`: `linux` -> `windows`.

```
E           AssertionError: supported claude-code/windows has no row in the Markdown table
E               AssertionError: Markdown row {'host': 'claude-code', 'os': 'linux', 'python': '3.10, 3.11, 3.12, 3.13', 'modes': 'local-goals, github', 'cell': 'supported'} is not in the JSON
FAILED tests/test_launch_definition.py::test_every_supported_pair_is_a_supported_row_with_the_same_python_and_modes
FAILED tests/test_launch_definition.py::test_every_markdown_row_is_backed_by_the_json
2 failed, 8 passed
```

## Control 2: status "signed" with signed_by null

`status`: `proposed` -> `signed`, `signed_on` set to `2026-09-30`, `signed_by` left `null`.

```
E           AssertionError: status is signed but signed_by is empty
E           AssertionError: status is signed but the Markdown says 'Proposed — not yet signed by the owner'
FAILED tests/test_launch_definition.py::test_status_is_proposed_or_signed_and_a_signature_is_complete
FAILED tests/test_launch_definition.py::test_markdown_status_line_agrees_with_json
```

The first failure is the signature check itself; the second is the Status-line sync, which also
catches a JSON signed without the Markdown.

## Control 3: public_repo not in owner/name form

`public_repo`: `Agrim-Intelligence/sigma` -> `sigma`.

```
E           AssertionError: public_repo is not owner/name: 'sigma'
FAILED tests/test_launch_definition.py::test_public_repo_when_set_is_an_owner_name_slug
1 failed, 9 passed
```

## Restored

JSON restored and `cmp`-identical to the copy: `10 passed` on Python 3.12 and on Python 3.10.
