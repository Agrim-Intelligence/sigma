"""The one surface all three boundary guards scan: `tests/public_surface.py` (#2584, S1-G11).

Every test here that needs a tree plants a REAL git tree (`public_surface.plant`), because the
surface is defined by git: in the monorepo, what `work.py commit`'s `git add -A` would commit and
the allowlist manifest selects; in a public tree, every tracked file.
"""
import pathlib
import subprocess

import pytest

import public_surface

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest(*lines):
    return "\n".join(lines) + "\n"


def test_a_manifest_selects_its_directories_and_files(tmp_path):
    public_surface.plant(tmp_path, {"a/x.py": "", "a/b/y.md": "", "c.txt": "", "d.txt": "",
                                    "e/z.py": ""},
                         manifest=_manifest("# the core", "", "a/", "  # indented comment", "c.txt"))
    assert public_surface.public_files(tmp_path) == ["a/b/y.md", "a/x.py", "c.txt"]


def test_a_directory_entry_matches_at_a_segment_boundary(tmp_path):
    public_surface.plant(tmp_path, {"a/x.py": "", "ab/y.py": ""}, manifest=_manifest("a/"))
    assert public_surface.public_files(tmp_path) == ["a/x.py"]


def test_no_manifest_means_every_tracked_file_and_nothing_untracked(tmp_path):
    public_surface.plant(tmp_path, {"a/x.py": "", "README.md": ""})
    (tmp_path / "untracked.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "a" / "later.py").write_text("x\n", encoding="utf-8")
    assert public_surface.public_files(tmp_path) == ["README.md", "a/x.py"]


def test_the_monorepo_mode_includes_a_new_untracked_file_and_skips_deleted_and_ignored_ones(tmp_path):
    public_surface.plant(tmp_path, {"a/x.py": "", "a/gone.py": "", ".gitignore": "a/*.log\n"},
                         manifest=_manifest("a/"))
    (tmp_path / "a" / "new.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "a" / "noise.log").write_text("x\n", encoding="utf-8")
    (tmp_path / "a" / "gone.py").unlink()
    assert public_surface.public_files(tmp_path) == ["a/new.py", "a/x.py"]


def test_an_entry_that_selects_nothing_is_refused(tmp_path):
    public_surface.plant(tmp_path, {"a/x.py": ""}, manifest=_manifest("a/", "docs"))
    with pytest.raises(public_surface.SurfaceError, match=r"line 2: 'docs' selects nothing"):
        public_surface.public_files(tmp_path)


@pytest.mark.parametrize("bad", ["a/*.py", "a/?.py", "a/[xy].py", "!a/", "/a", "../a", "a//b",
                                 "a/./b", "a\\b", "DUP"])
def test_bad_manifest_syntax_is_refused(bad):
    lines = ["a/", "a/"] if bad == "DUP" else ["a/", bad]
    with pytest.raises(public_surface.SurfaceError, match=r"line 2"):
        public_surface.parse_manifest(_manifest(*lines))


def test_a_root_that_is_not_its_own_git_toplevel_is_refused(tmp_path):
    public_surface.plant(tmp_path, {"sub/a.py": ""})
    with pytest.raises(public_surface.SurfaceError, match="not its own git toplevel"):
        public_surface.public_files(tmp_path / "sub")
    plain = tmp_path.parent / (tmp_path.name + "-plain")
    plain.mkdir()
    with pytest.raises(public_surface.SurfaceError, match="not its own git toplevel"):
        public_surface.public_files(plain)


def test_the_real_tree_selects_the_core():
    """Both branches named: in this repository the manifest exists and selects the core; in a
    public tree (no manifest) the surface is exactly `git ls-files`."""
    files = public_surface.public_files(ROOT)
    manifest = ROOT.joinpath(*public_surface.MANIFEST)
    if manifest.is_file():
        for rel in ("AGENTS.md", "skills/sigma-loop/scripts/loop.py", "tools/generate_vocabulary.py"):
            assert rel in files, rel
        assert "tools/public-manifest.txt" not in files
        assert not [f for f in files if f.startswith(".sdlc/")]
        everything = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                                    text=True, check=True).stdout.split()
        assert len(files) < len(everything), "the manifest selected the whole repository"
        print("monorepo mode: the manifest selects %d file(s)" % len(files))
    else:
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True,
                                 text=True, check=True).stdout.split("\0")
        assert files == sorted(p for p in tracked if p and (ROOT / p).exists())
        print("public-tree mode: %d tracked file(s)" % len(files))
