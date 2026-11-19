# Contributing to Sigma Loop

Thank you for your interest in Sigma Loop. Outside contributions are not accepted yet; issues are welcome.
The rest of this page is for maintainers and for when that changes. Please open an issue before substantial
work so maintainers can confirm the scope. When outside contributions open, a DCO sign-off (a `Signed-off-by` line on each commit) will be required.

## Development setup

Sigma Loop needs Python 3.10 or newer. CI runs the full suite weekly and on demand on Linux with Python 3.10, 3.11, 3.12 and 3.13, and on
macOS with Python 3.12 only (nightly: the suite minus its heaviest files; pull requests and merges to main: a short smoke set, one macOS Python 3.12 leg); macOS on other Python versions is untested. Install the test dependencies:

```sh
python -m pip install pytest pytest-xdist pyyaml
```

Run the suite before opening a pull request:

```sh
python -m pytest tests -q -n 4 -p no:cacheprovider
python3 tools/rename_check.py
```

The second command exits 1 and prints each file and line while any tracked path or file still carries
the retired skill prefix (every skill and command starts with `sigma-`); CI runs it on every pull
request. A branch cut before the rename lands fixes itself by taking the new paths and running the
rename tool once more, from the repository root:

```sh
python3 tools/rename_prefix.py
```

It refuses (exit 2, nothing moved) if the new folder already exists; `git mv` the files your branch
added under the old folder name into it by hand, then run it again.

## Docs site

The public docs site is plain HTML in `site/`, with one stylesheet and inline SVG: no build step, no
external scripts, fonts or images. To edit a page, change its `site/*.html` file. The sidebar is
generated into every page from the page list in `tools/site_nav.py`; after adding a page or changing a
title, run `python3 tools/site_nav.py write` (it rewrites each page's nav and creates a stub for a
missing page), and `python3 tools/site_nav.py check` to verify.

Rules for page content: version numbers appear only on the Releases page; every `/sigma-*` command
named must be a skill under `skills/`; no private names, home paths or other emails; no path to an internal script, except a line clearly labelled as the command or fallback path the reader types (as on Get started). The checks are
`tests/test_site_checks.py` (leak scan, commands and skill count, links and anchors, SVG well-formedness,
versions, external URLs, nav, host flags), each with a control that breaks one thing and expects red.
Design: the site is monochrome, matching the company's published pages. Black, white and greys carry everything; the one
hue is the link colour (links and focus ring), with a lightened pair in the dark scheme. State is carried by form, not
colour: core is a solid outline, optional a dashed one, a gate or emphasis a solid black fill with white text, and
warnings a heavy left rule with a bold label. Use the CSS variables in `site/assets/style.css`, never an inline colour;
`tests/test_site_checks.py` fails on any colour outside the allowlist and on text under 4.5:1 in either scheme.
The Pages workflow publishes `site/` from `main`; it does not enable Pages, which is a repository setting.

## Working rules

Shipped code is stdlib-only. Every guard must be deliberately seen red once before its green run.
The five design properties — reliability, scalability, resiliency, safety, and liveness — and the
full agent rules are defined in [AGENTS.md](AGENTS.md). Agents must emit status according to the
[output contract](docs/output-contract.md).

Work lands through `sdlc/*` branches and pull requests. Nobody commits directly to a `feature/*`
branch. Keep changes focused, add meaningful tests, and update the relevant documentation.
