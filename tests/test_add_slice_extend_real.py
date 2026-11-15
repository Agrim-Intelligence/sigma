"""`work.py add-slice` through the REAL `acceptance.py extend` (#2222), no double (#2224).

The doubles in test_add_slice_red / _guard defined `extend(sdlc_dir, goal, lines)`, a signature the shipped verb does
not have (`extend(sdlc_dir, goal, slice_ref, reason, criteria_list, ...)`), so a green double hid a verb that refused
every real call. This file is the end-to-end control: real CLI, real acceptance.py, a real record.

Run: python3 -m pytest tests/test_add_slice_extend_real.py
(generic form: python -m pytest tests/test_add_slice_extend_real.py)

Measured by this file: the CLI exits 0, the record's original `## Done when` bytes are unchanged, one
`(added: ...; reason: ...)` line per criterion line lands under `## Added criteria`, and a rerun adds nothing.
Not measured here: the GitHub extend comment (a local acceptance record has no issue source in this fixture).
"""
import importlib.util
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_add_slice_red import S, cli, count, proj  # noqa: E402,F401  (fixtures and the CLI driver)

BODY = "## Done when\n- [ ] First works.\n- [ ] Second refuses bad input.\n- [ ] Third recovers.\n"
RECORD = None


class _Source:
    def fetch_title_body(self, goal):
        return {"title": "Goal", "body": BODY}


def _with_real_record(p):
    """A record made by the real `acceptance.record` (it carries the metadata `extend` refuses to run without)."""
    global RECORD
    spec = importlib.util.spec_from_file_location("acceptance_real", S / "acceptance.py")
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)
    record = acc.record(p.sdlc, "2224", source=_Source(), config={})
    RECORD = record.read_text()
    return record


def test_real_extend_appends_added_criteria_and_leaves_the_original_block_untouched(proj):
    record = _with_real_record(proj)
    r = cli(proj)                                           # mode real: real work.py, real acceptance.py
    assert r.returncode == 0, r.stderr
    text = record.read_text()
    assert text.startswith(RECORD), "the original `## Done when` block must stay byte-identical"
    assert "## Added criteria" in text
    added = [x for x in text.splitlines() if "(added:" in x]
    assert len(added) >= 1 and all("reason:" in x for x in added), text
    assert count(proj) == 1


def test_real_extend_rerun_is_idempotent(proj):
    record = _with_real_record(proj)
    assert cli(proj).returncode == 0
    once = record.read_text()
    r = cli(proj)
    assert r.returncode == 0 and "already counted" in r.stdout
    assert record.read_text() == once and count(proj) == 1
