"""Keep the launch definition's two copies in sync (#330).

`docs/launch/definition.md` is the page a human reads; `docs/launch/definition.json` is the same
decisions for code. Every readiness threshold points at them, so they must not disagree:

  - the JSON parses and carries `schema == "launch-definition/v1"`;
  - `status` is `proposed` or `signed`; a signed page names who signed it (`signed_by`), when
    (`signed_on`, YYYY-MM-DD) and the public repository (`public_repo`, owner/name);
  - `public_repo`, whenever it is set, is an `owner/name` slug;
  - every `supported` host/OS pair is a `supported` row of the Markdown's "Supported cells" table,
    with the same Python versions and modes, and every `experimental` entry is an `experimental` row;
  - the Markdown `## Status` line agrees with the JSON `status` (and, when signed, with who and when).

Stdlib + pytest only; no network. Control (seen red, recorded in
`docs/launch/evidence/330-control.md`): change one supported OS in the JSON to "windows" without
touching the Markdown, or set status "signed" with `signed_by: null` -- each must fail here.
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MD_PATH = ROOT / "docs" / "launch" / "definition.md"
JSON_PATH = ROOT / "docs" / "launch" / "definition.json"

HEADINGS = [
    "# Launch definition",
    "## Status",
    "## What ships",
    "## To whom",
    "## Supported cells",
    "## Out of scope",
    "## How to change this page",
]
PROPOSED_LINE = "Proposed — not yet signed by the owner"
SIGNED_LINE = re.compile(r"Signed by (\S+) on (\d{4}-\d{2}-\d{2})")
SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _definition():
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def _markdown():
    return MD_PATH.read_text(encoding="utf-8")


def _section(md, heading):
    """The lines under `heading`, up to the next heading of any level."""
    lines = md.splitlines()
    start = lines.index(heading) + 1
    body = []
    for line in lines[start:]:
        if line.startswith("#"):
            break
        body.append(line)
    return body


def _cell_rows(md):
    """The "Supported cells" table as dicts: host, os, python, modes, cell (backticks stripped)."""
    rows = [l for l in _section(md, "## Supported cells") if l.strip().startswith("|")]
    assert len(rows) >= 3, "the Supported cells table is missing or empty"
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
    assert header == ["host", "os", "python", "modes", "cell"], header
    out = []
    for row in rows[2:]:  # skip header and the |---| separator
        cells = [c.strip().replace("`", "") for c in row.strip().strip("|").split("|")]
        out.append(dict(zip(header, cells)))
    return out


def _split(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def test_json_parses_with_the_v1_schema():
    assert _definition()["schema"] == "launch-definition/v1"


def test_status_is_proposed_or_signed_and_a_signature_is_complete():
    d = _definition()
    assert d["status"] in ("proposed", "signed"), d["status"]
    if d["status"] == "signed":
        assert d.get("signed_by"), "status is signed but signed_by is empty"
        assert d.get("signed_on") and DATE.fullmatch(d["signed_on"]), \
            "status is signed but signed_on is not YYYY-MM-DD: %r" % d.get("signed_on")
        assert d.get("public_repo") and SLUG.fullmatch(d["public_repo"]), \
            "status is signed but public_repo is not owner/name: %r" % d.get("public_repo")


def test_public_repo_when_set_is_an_owner_name_slug():
    repo = _definition().get("public_repo")
    if repo is not None:
        assert SLUG.fullmatch(repo), "public_repo is not owner/name: %r" % repo


def test_markdown_headings_are_present_in_order():
    lines = _markdown().splitlines()
    found = [l for l in lines if l in HEADINGS]
    assert found == HEADINGS, found


def test_every_supported_pair_is_a_supported_row_with_the_same_python_and_modes():
    rows = _cell_rows(_markdown())
    for entry in _definition()["supported"]:
        match = [r for r in rows if r["host"] == entry["host"] and r["os"] == entry["os"]]
        assert match, "supported %s/%s has no row in the Markdown table" % (entry["host"], entry["os"])
        for r in match:
            assert r["cell"] == "supported", \
                "%s/%s is supported in the JSON but %r in the Markdown" % (entry["host"], entry["os"], r["cell"])
            assert _split(r["python"]) == entry["python"], (r["python"], entry["python"])
            assert _split(r["modes"]) == entry["modes"], (r["modes"], entry["modes"])


def test_every_experimental_entry_is_an_experimental_row():
    rows = _cell_rows(_markdown())
    for entry in _definition()["experimental"]:
        match = [r for r in rows if all(r[k] == v for k, v in entry.items())]
        assert match, "experimental %r has no row in the Markdown table" % entry
        assert all(r["cell"] == "experimental" for r in match), (entry, match)


def test_every_markdown_row_is_backed_by_the_json():
    """The reverse direction: a `supported` row the JSON does not list would be a claim made in prose
    only, which is exactly the drift this file exists to stop."""
    d = _definition()
    supported = {(e["host"], e["os"]) for e in d["supported"]}
    for r in _cell_rows(_markdown()):
        assert r["cell"] in ("supported", "experimental"), r
        if r["cell"] == "supported":
            assert (r["host"], r["os"]) in supported, "Markdown row %r is not in the JSON" % r


def test_markdown_status_line_agrees_with_json():
    d = _definition()
    body = [l.strip() for l in _section(_markdown(), "## Status") if l.strip()]
    assert body, "## Status has no line"
    first = body[0]
    if d["status"] == "proposed":
        assert first == PROPOSED_LINE, first
    else:
        m = SIGNED_LINE.fullmatch(first)
        assert m, "status is signed but the Markdown says %r" % first
        assert (m.group(1), m.group(2)) == (d["signed_by"], d["signed_on"]), (m.groups(), d)


@pytest.mark.parametrize("path", [MD_PATH, JSON_PATH])
def test_both_files_exist(path):
    assert path.is_file(), path
