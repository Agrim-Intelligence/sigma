"""`tools/release_notes.py` extracts one release section and refuses an undated one (owner decision 2026-10-06).

The documented gesture (docs/release.md step 7): `python3 tools/release_notes.py X.Y.Z`. A heading that
still carries the DATE-PENDING placeholder must make it exit 2 with nothing on stdout, so the one
irreversible step (`gh release create`) cannot publish an undated release.
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "release_notes.py"


def _run(tmp_path, text, *args):
    log = tmp_path / "CHANGELOG.md"
    log.write_text(text, encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOL), *args, str(log)] if args else [sys.executable, str(TOOL)],
                          capture_output=True, text=True, cwd=tmp_path)


DATED = "# Changelog\n\n## Unreleased\n\n## 1.0.0 — 2026-10-07 — the first public release\n\n- one\n- two\n\n## 0.9.0 — 2026-01-02\n\n- old\n"


def test_dated_section_is_extracted_alone(tmp_path):
    done = _run(tmp_path, DATED, "1.0.0")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "- one\n- two"


def test_placeholder_heading_is_refused(tmp_path):
    done = _run(tmp_path, DATED.replace("2026-10-07", "DATE-PENDING"), "1.0.0")
    assert done.returncode == 2 and done.stdout == "" and "REFUSED" in done.stderr and "date" in done.stderr


def test_missing_or_empty_section_is_refused(tmp_path):
    gone = _run(tmp_path, DATED, "2.0.0")
    assert gone.returncode == 2 and gone.stdout == "" and "REFUSED" in gone.stderr
    empty = _run(tmp_path, "# Changelog\n\n## 1.0.0 — 2026-10-07\n\n## 0.9.0 — 2026-01-02\n- old\n", "1.0.0")
    assert empty.returncode == 2 and empty.stdout == "" and "REFUSED" in empty.stderr


def test_bad_arguments_are_refused(tmp_path):
    done = _run(tmp_path, DATED)
    assert done.returncode == 2 and done.stdout == "" and "REFUSED" in done.stderr


def test_shipped_changelog_refuses_while_the_placeholder_stands():
    """The gesture the docs give, on the real file: red while DATE-PENDING is in CHANGELOG.md."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    done = subprocess.run([sys.executable, str(TOOL), "1.0.0"], capture_output=True, text=True, cwd=ROOT)
    if "— DATE-PENDING —" in text:
        assert done.returncode == 2 and done.stdout == "" and "REFUSED" in done.stderr
    else:
        assert done.returncode == 0 and done.stdout.strip()


def test_end_marker_stops_the_notes(tmp_path):
    text = DATED.replace("- two\n", "- two\n\n<!-- release-notes:end -->\n\n- detailed log\n")
    done = _run(tmp_path, text, "1.0.0")
    assert done.returncode == 0 and done.stdout.strip() == "- one\n- two"


def test_oversize_notes_are_refused(tmp_path):
    done = _run(tmp_path, DATED.replace("- two", "- " + "x" * 121000), "1.0.0")
    assert done.returncode == 2 and done.stdout == "" and "REFUSED" in done.stderr and "limit" in done.stderr


def test_shipped_notes_fit_once_dated(tmp_path):
    """The real fold, dated in a copy: the extraction must succeed and stay under GitHub's body limit."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").replace("— DATE-PENDING —", "— 2099-01-01 —", 1)
    done = _run(tmp_path, text, "1.0.0")
    assert done.returncode == 0, done.stderr
    assert 500 < len(done.stdout) < 20000 and "development log" not in done.stdout
