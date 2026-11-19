"""tests/quarantine.txt is the one quarantine list; each row needs a date, an issue and a reason, and the
list and the xfail decorators on the tests agree both ways (#2255)."""
import ast
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LIST = HERE / "quarantine.txt"
TAG = "QUARANTINED"


def read_rows(path=LIST):
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [p.strip() for p in line.split(" | ", 3)]
        if len(parts) != 4 or not all(parts):
            raise ValueError(f"{path.name}:{n}: want `<file>::<test> | <date> | <#issue> | <reason>`, got {line!r}")
        rows.append(tuple(parts))
    return rows


def decorated():
    """{(file, test): (date, issue)} of every test whose decorators carry a QUARANTINED xfail reason."""
    found = {}
    for path in sorted(HERE.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if TAG not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    src = ast.get_source_segment(text, dec) or ""
                    if "xfail" in src and TAG in src:
                        found[("tests/" + path.name, node.name)] = re.search(TAG + r" (\S+) (#\d+)", src).groups()
    return found


def test_every_row_has_a_date_an_issue_a_reason_and_an_existing_test_file():
    for node, date, issue, reason in read_rows():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), (node, date)
        assert re.fullmatch(r"#\d+", issue), (node, issue)
        assert (HERE.parent / node.split("::", 1)[0]).is_file(), node
        assert len(reason) >= 10, node


def test_the_list_and_the_decorators_agree_both_ways():
    rows = {tuple(r[0].split("::", 1)): (r[1], r[2]) for r in read_rows()}
    assert rows == decorated(), "tests/quarantine.txt and the QUARANTINED xfail decorators differ"


def test_a_row_without_a_reason_is_refused(tmp_path):
    bad = tmp_path / "q.txt"
    bad.write_text("tests/test_x.py::test_y | 2026-11-18 | #1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_rows(bad)
