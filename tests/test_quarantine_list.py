"""tests/quarantine.txt is the one quarantine list; each row needs a date, an issue and a reason (#2255)."""
import importlib.util
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("quarantine_conftest", HERE / "conftest.py")
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)


def test_every_row_has_a_date_an_issue_and_an_existing_test_file():
    for node, date, issue, reason in conftest.read_quarantine():
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), (node, date)
        assert re.fullmatch(r"#\d+", issue), (node, issue)
        assert (HERE.parent / node.split("::", 1)[0]).is_file(), node
        assert len(reason) >= 10, node


def test_a_row_without_a_reason_is_refused(tmp_path):
    bad = tmp_path / "q.txt"
    bad.write_text("tests/test_x.py::test_y | 2026-11-18 | #1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        conftest.read_quarantine(bad)
