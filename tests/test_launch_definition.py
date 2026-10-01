"""Keep the launch definition's two copies in sync (#330).

`docs/launch/definition.md` is the page a human reads; `docs/launch/definition.json` is the same
decisions for code. Every readiness threshold points at them, so they must not disagree:

  - the JSON parses, with no key given twice, and carries `schema == "launch-definition/v1"`;
  - `status` is `proposed` or `signed`; a signed page names who signed it (`signed_by`, a plain
    login: letters, digits, `-`, at most 39), when (`signed_on`, YYYY-MM-DD) and the public
    repository (`public_repo`, owner/name);
  - `public_repo`, whenever it is set, is an `owner/name` slug;
  - both directions, each field: `artifact`, `public_repo`, `version` (and its `v` tag) and
    `audience` equal the first backticked value of their `- **Label:**` bullet; the `supported` and
    `experimental` entries equal the table rows with that cell (a row in only one copy fails); the
    `out_of_scope` list equals the backticked ids ending the Out of scope bullets, in order;
  - the Markdown `## Status` line agrees with the JSON `status` (and, when signed, with who and when);
    when status is signed, `signed_on` is a real calendar day (2026-02-30 fails) no later than
    today -- the same signature #331's tools/readiness/decide.py accepts, so the two cannot
    disagree on what a valid signature is;
  - the rename-hazard section names `discovery.github.repo`, the loop's own configured repository.

Stdlib + pytest only; no network. Control (seen red, recorded in
`docs/launch/evidence/330-control.md`): change one supported OS in the JSON to "windows" without
touching the Markdown, or set status "signed" with `signed_by: null`, or change any one field in
only one of the two files -- each must fail here.
"""
import datetime
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
    "## What the rename changes",
    "## To whom",
    "## Supported cells",
    "## Out of scope",
    "## How to change this page",
]
PROPOSED_LINE = "Proposed — not yet signed by the owner"
SIGNED_LINE = re.compile(r"Signed by (\S+) on (\d{4}-\d{2}-\d{2})")
SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9._-]+")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LOGIN = re.compile(r"[A-Za-z0-9-]{1,39}")  # decide.py's LOGIN_RE


def _is_date(value):
    """YYYY-MM-DD, a real calendar day (2026-02-30 is not) and not later than today, as #331's
    decide.py checks a signing date."""
    if not (isinstance(value, str) and DATE.fullmatch(value)):
        return False
    try:
        return datetime.date.fromisoformat(value) <= datetime.date.today()
    except ValueError:
        return False


def _unique_pairs(pairs):
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, "definition.json gives a key twice: %r" % dupes
    return dict(pairs)


def _definition():
    return json.loads(JSON_PATH.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)


def _markdown():
    return MD_PATH.read_text(encoding="utf-8")


def _section(md, heading):
    """The lines under `heading`, up to the next heading of any level."""
    lines = md.splitlines()
    start = lines.index(heading) + 1
    body = []
    for line in lines[start:]:
        if re.match(r"#+ ", line):  # a heading; "#302" at line start is an issue ref, not one
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
        signed_by = d.get("signed_by")
        assert isinstance(signed_by, str) and LOGIN.fullmatch(signed_by), \
            "status is signed but signed_by is not a plain login: %r" % signed_by
        assert _is_date(d.get("signed_on")), \
            "status is signed but signed_on is not a real YYYY-MM-DD date on or before today: %r" \
            % d.get("signed_on")
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


def _bullet_value(md, heading, label):
    """The first backticked value on the one `- **<label>:**` bullet under `heading`."""
    hits = [l for l in _section(md, heading) if l.startswith("- **%s:**" % label)]
    assert len(hits) == 1, "expected one '- **%s:**' bullet under %s, found %d" % (label, heading, len(hits))
    m = re.search(r"`([^`]+)`", hits[0])
    assert m, "the %s bullet has no backticked value: %r" % (label, hits[0])
    return m.group(1)


@pytest.mark.parametrize("key,heading,label", [
    ("artifact", "## What ships", "Artifact"),
    ("public_repo", "## What ships", "Public repository"),
    ("version", "## What ships", "Version"),
    ("audience", "## To whom", "Audience"),
])
def test_scalar_decisions_agree(key, heading, label):
    assert _bullet_value(_markdown(), heading, label) == _definition()[key]


def test_version_tag_matches_version():
    line = [l for l in _section(_markdown(), "## What ships") if l.startswith("- **Version:**")][0]
    assert "git-tagged `v%s`" % _definition()["version"] in line, line


def _row_entry(r):
    """A table row as the JSON would write it: `any` columns dropped, lists split."""
    out = {}
    for k in ("host", "os", "python", "modes"):
        if r[k] != "any":
            out[k] = _split(r[k]) if k in ("python", "modes") else r[k]
    return out


def _canon(entries):
    return sorted(json.dumps(e, sort_keys=True) for e in entries)


@pytest.mark.parametrize("cell", ["supported", "experimental"])
def test_cells_agree_both_ways(cell):
    """Every JSON entry is a row with that cell, and every such row is a JSON entry -- a row in
    prose only, or an entry in JSON only, is the drift this file exists to stop."""
    rows = _cell_rows(_markdown())
    assert all(r["cell"] in ("supported", "experimental") for r in rows), rows
    md = _canon(_row_entry(r) for r in rows if r["cell"] == cell)
    assert md == _canon(_definition()[cell]), "Markdown %s rows %r != JSON %r" % (cell, md, _canon(_definition()[cell]))


def test_out_of_scope_agrees_both_ways():
    items = []
    for l in _section(_markdown(), "## Out of scope"):
        if l.startswith("- "):
            m = re.search(r"\(`([^`]+)`\)[;.]$", l)
            assert m, "out-of-scope bullet does not end with its (`id`): %r" % l
            items.append(m.group(1))
    assert items == _definition()["out_of_scope"], (items, _definition()["out_of_scope"])


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
        assert _is_date(m.group(2)), "the Markdown's signing date is not a real date: %r" % m.group(2)
        assert (m.group(1), m.group(2)) == (d["signed_by"], d["signed_on"]), (m.groups(), d)


@pytest.mark.parametrize("path", [MD_PATH, JSON_PATH])
def test_both_files_exist(path):
    assert path.is_file(), path


def test_rename_hazard_names_the_loops_configured_repo():
    """The loop reads goals from, and writes labels and comments to, `discovery.github.repo`, not
    the git remote -- the hazard a rename list most easily drops (#384 review block 2)."""
    section = "\n".join(_section(_markdown(), "## What the rename changes"))
    assert "`discovery.github.repo`" in section, "the rename-hazard list omits discovery.github.repo"
