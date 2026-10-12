"""The public export passes its own suite and gates because the BUILDER rewrites a few files, not
because the source holds a switch (#1203). Trust model: the exported behaviour is fixed by the builder's
reviewed code at export time; nothing a source contributor adds (a marker file, a field, a symlink) can
make a private-tree guard skip or ignore a row, because no guard reads any such thing."""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_build_public_tree import _argv, _build, _fields, _fixture, _git, _out, _report, _tool  # noqa: E402

PINNED_SKIPS = {
    ("tests/test_b6_466.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_goal_state_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_liveness_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_worktree_disposition.py", "test_documented_gesture_resolves_the_rows"),
    ("tests/test_no_private_sdlc_citations.py", "test_no_shipped_file_cites_an_untracked_sdlc_design_or_plan"),
}
SKIP_RX = re.compile(r'@__import__\("pytest"\)\.mark\.skip\(reason="EXPORT-SKIP[^\n]*\n\s*def (test\w+)\(')


def test_the_exact_skip_set_is_pinned_and_every_target_exists_once():
    mod = _tool()
    assert set(mod.EXPORT_SKIPS) == PINNED_SKIPS and len(mod.EXPORT_SKIPS) == len(PINNED_SKIPS)
    for path, test in PINNED_SKIPS:
        text = (ROOT / path).read_text(encoding="utf-8")
        assert len(re.findall(r"^def %s\(" % test, text, re.M)) == 1, (path, test)


def test_tests_carry_no_export_skip_beyond_the_pinned_set_in_either_tree():
    """Source tree (`.sdlc/` present, or inventories not pruned): none at all. Exported tree: none or exactly
    the pinned set, so a new skip fails here."""
    from test_no_skip_hooks import export_mode
    found = set()
    for path in sorted((ROOT / "tests").glob("*.py")):
        if path.name in ("test_export_transforms.py", "test_build_public_tree.py"):
            continue
        for test in SKIP_RX.findall(path.read_text(encoding="utf-8")):
            found.add(("tests/" + path.name, test))
    allowed = (set(), PINNED_SKIPS) if export_mode() else (set(),)
    assert found in allowed, sorted(found ^ PINNED_SKIPS)


SKIP_FILES = {path: "def %s(tmp_path):\n    assert False\n" % test for path, test in sorted(PINNED_SKIPS)}
ROWS = {"entries": [{"path": ".sdlc/r/a.py", "function": "f", "rule": "gh-issue", "count": 1, "gate": "g", "risk": "low"},
                    {"path": "tools/b.py", "function": "g", "rule": "gh-issue", "count": 1, "gate": "g", "risk": "low"}]}
ALLOW = [{"path": ".sdlc/plans/1.md", "rule": "r", "reason": "x", "lines": ["0" * 64]}]


def _extra():
    extra = dict(SKIP_FILES)
    extra.update({".sdlc/x.md": "x\n", "docs/launch/write-surface.json": json.dumps(ROWS, indent=2) + "\n",
                  "docs/launch/exposure-allowlist.json": json.dumps(ALLOW, indent=2) + "\n"})
    return extra


def test_the_builder_prunes_rows_and_skips_the_pinned_tests_and_reports_it(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra=_extra())
    rc, out, err = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 0, err
    export = _out(tmp_path)
    assert json.loads((export / "docs/launch/write-surface.json").read_text())["entries"] == ROWS["entries"][1:]
    assert json.loads((export / "docs/launch/exposure-allowlist.json").read_text()) == []
    for path, test in PINNED_SKIPS:
        text = (export / path).read_text()
        assert SKIP_RX.findall(text) == [test], path
    transforms = _report(_fields(out))["export"]["transforms"]
    assert sorted((t["path"], t["change"]) for t in transforms) == sorted(
        [("docs/launch/write-surface.json", "rows-dropped"), ("docs/launch/exposure-allowlist.json", "rows-dropped")]
        + [(p, "skip") for p, _t in PINNED_SKIPS])
    # the rewritten files are what the export commit holds, and the source is untouched
    assert _git(export, "show", "HEAD:tests/test_b6_466.py") == (export / "tests/test_b6_466.py").read_text().rstrip("\n")
    assert "EXPORT-SKIP" not in _git(repo, "show", "HEAD:tests/test_b6_466.py")


def test_sdlc_include_ships_every_file_verbatim_and_nothing_is_rewritten(tmp_path, capsys):
    mod = _tool()
    repo, sha = _fixture(tmp_path, extra=_extra())
    rc, out, err = _build(mod, capsys, _argv(tmp_path, repo, sha, extra=["--sdlc", "include"]))
    assert rc in (0, 1), err   # 1: the unused allowlist row is stale when .sdlc ships; the tree is what matters
    assert _report(_fields(out))["export"]["transforms"] == []
    assert _fields(out)["tree"] == _git(repo, "rev-parse", sha + "^{tree}")


def test_a_target_that_cannot_be_rewritten_exactly_is_refused(tmp_path, capsys):
    mod = _tool()
    extra = _extra()
    extra["tests/test_b6_466.py"] = "# renamed\n"
    repo, sha = _fixture(tmp_path, extra=extra)
    rc, out, err = _build(mod, capsys, _argv(tmp_path, repo, sha))
    assert rc == 2 and "REFUSED [transform-failed]" in err, err


FORGERIES = ("tracked", "untracked", "extra-fields", "symlink")


@pytest.mark.parametrize("forgery", FORGERIES)
def test_a_hand_written_marker_cannot_disarm_a_private_tree_guard(tmp_path, forgery):
    """Red-by-design control: a valid-looking export marker, tracked or not, with extra fields or as a
    symlink, next to a deleted .sdlc must change NOTHING: the stale rows are still findings."""
    ws = ROOT / "tools/readiness/write_surface.py"
    ex = ROOT / "tools/readiness/exposure_scan.py"
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "docs").mkdir()
    (repo / "inv.json").write_text(json.dumps(ROWS), encoding="utf-8")
    (repo / "allow.json").write_text(json.dumps(ALLOW), encoding="utf-8")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    forged = json.dumps({"schema": "sigma.public-export/v1", "excluded": [".sdlc"], "source_commit": "0" * 40,
                         "extra": True if forgery == "extra-fields" else None})
    for name in (".sigma-public-export.json",):
        if forgery == "symlink":
            (repo / "real.json").write_text(forged, encoding="utf-8")
            os.symlink("real.json", repo / name)
        else:
            (repo / name).write_text(forged, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if forgery != "untracked":
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    else:
        subprocess.run(["git", "-C", str(repo), "add", "README.md", "inv.json", "allow.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=" + "t" + "@" + "e.invalid", "commit", "-qm", "x"], check=True)
    done = subprocess.run([sys.executable, str(ws), "check", str(repo), str(repo / "inv.json")], capture_output=True, text=True)
    assert done.returncode == 1 and "stale entry .sdlc/r/a.py:f gh-issue" in done.stdout, done.stdout
    done = subprocess.run([sys.executable, str(ex), "tracked", str(repo), "--allowlist", str(repo / "allow.json"),
                           "--json", str(tmp_path / "ev.json")], capture_output=True, text=True)
    assert done.returncode == 1 and "stale-allowlist" in done.stdout, done.stdout


def test_no_guard_or_test_reads_an_export_marker():
    """The old design's switch is gone: no tracked file other than this one names it."""
    needle = "sigma-public-" + "export"
    hits = [str(p.relative_to(ROOT)) for p in list((ROOT / "tools").rglob("*.py")) + list((ROOT / "tests").glob("*.py"))
            + list((ROOT / "hooks").rglob("*.py")) + list((ROOT / "skills").rglob("*.py")) if p.name != 'test_export_transforms.py' and needle in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == [], hits
