"""Hermetic controls for the seeded-defect tool (#352): the ``apply`` and ``score`` verbs.

Every test builds its own temporary repository (local identity, no signing, no hooks), keeps the
clone DETACHED the way ``baseline.py snapshot`` leaves it, and runs the tool the way the
documentation prints it: ``seed_defects.py apply <clone> <seed_dir>``. Patches are made by editing
a file, running ``git diff`` and putting the file back; none is written by hand. The ``score``
tests need no repository: they write the manifest.json ``apply`` would have written and a findings
file, and run ``seed_defects.py score <seed_dir> <findings.json>``.

A refusal must change nothing: ``_state`` is every non-.git file under the temp root plus the
clone's HEAD, and each refusal test asserts it is EQUAL before and after (not "status is empty",
which is false for a seed directory that sits inside the clone).

The call-shaped strings the seeds plant (an eval or a shell call) are inert text written into
temporary files; nothing here executes them.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "readiness" / "seed_defects.py"
EXAMPLES = ROOT / "tests" / "fixtures" / "readiness_seed_examples"
DOC = ROOT / "docs" / "launch" / "seeded-defects.md"
LINK = re.compile(r"\]\(([^)\s]+)\)")
ODD = "tools/odd [x] café/f g.py"
# Two versions of one 41-line file that git reads differently under diff.algorithm: myers as the
# changes "+34" and "+36,2", histogram as "+34,3" and "-35".
ALGO_BASE = ("def f():\n\n    x = 1\n\ndef f():\n    x = 1\n    x = 1\n}\n}\n\ndef f():\n}\n}\ndef f():\n}\n"
             "def f():\n\n\n}\ndef f():\n    x = 1\n    return 1\ndef f():\n\ndef f():\n\n}\n    x = 1\n"
             "    return 1\n}\n}\n\ndef f():\n}\n    x = 1\n\n    x = 1\n    x = 1\n\n\n")
ALGO_NEW = ("def f():\n\n    x = 1\n\ndef f():\n    x = 1\n    x = 1\n}\n}\n\ndef f():\n}\n}\ndef f():\n}\n"
            "def f():\n\n\n}\ndef f():\n    x = 1\n    return 1\ndef f():\n\ndef f():\n\n}\n    x = 1\n"
            "    return 1\n}\n}\n\ndef f():\n    y = 2\n    x = 1\n}\n}\n\n    x = 1\n    x = 1\n\n\n")


def _tool():
    spec = importlib.util.spec_from_file_location("readiness_seed_defects", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _numbered(prefix, count=60):
    return "".join("%s_%02d = 1\n" % (prefix, i) for i in range(1, count + 1))


def _commit(repo, message="fixture"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _clone(tmp_path, name="clone", files=None):
    """A clean, DETACHED temporary clone of ``files`` (path -> text): by default tools/a.py, b.py
    and c.py of 60 lines (c.py has no final newline) and one 60-line file whose path has spaces,
    brackets and a non-ASCII letter."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false"),
                       ("core.quotePath", "true"), ("core.fileMode", "true")):
        _git(repo, "config", key, value)
    if files is None:
        files = {"tools/a.py": _numbered("a"), "tools/b.py": _numbered("b"),
                 "tools/c.py": _numbered("c")[:-1], ODD: _numbered("f")}
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "--detach")
    return repo


def _patch(repo, rel, mutate):
    """The ``git diff`` patch from the committed ``rel`` to ``mutate(lines)``; the file is put back."""
    path = Path(repo) / rel
    original = path.read_bytes()
    path.write_text("".join(mutate(original.decode("utf-8").splitlines(True))), encoding="utf-8")
    try:
        done = subprocess.run(["git", "-C", str(repo), "diff", "--no-ext-diff", "--src-prefix=a/",
                               "--dst-prefix=b/", "--", ":(literal)" + rel], capture_output=True)
    finally:
        path.write_bytes(original)
    assert done.returncode == 0 and done.stdout, done.stderr
    return done.stdout


def _replace(number, text, count=1):
    """Replace ``count`` lines starting at line ``number`` with the lines of ``text``."""
    def mutate(lines):
        end = "\n" if lines[number - 1].endswith("\n") else ""
        return lines[:number - 1] + [part + "\n" for part in text.split("\n")[:-1]] + \
            [text.split("\n")[-1] + end] + lines[number - 1 + count:]
    return mutate


def _delete(number):
    return lambda lines: lines[:number - 1] + lines[number:]


def _other_base_patch(clone, tmp_path, rel, mutate):
    """A patch for ``rel`` taken against a DIFFERENT base: a scratch copy of the clone whose lines
    15 to 26 were rewritten and committed, so its context does not exist in the real clone."""
    scratch = tmp_path / "scratch-other-base"
    shutil.copytree(str(clone), str(scratch))
    path = scratch / rel
    lines = path.read_text(encoding="utf-8").splitlines(True)
    lines[14:26] = ["# rewritten %d\n" % i for i in range(12)]
    path.write_text("".join(lines), encoding="utf-8")
    _commit(scratch, "other base")
    patch = _patch(scratch, rel, mutate)
    shutil.rmtree(str(scratch))
    return patch


def _scratch_patch(clone, tmp_path, operate):
    """A patch made from a scratch copy of the clone after ``operate(scratch)`` (create, delete,
    rename, mode change, binary content, two files): ``git diff --cached`` against HEAD."""
    scratch = tmp_path / "scratch-operate"
    shutil.copytree(str(clone), str(scratch))
    operate(scratch)
    _git(scratch, "add", "-A")
    done = subprocess.run(["git", "-C", str(scratch), "diff", "--cached", "--no-ext-diff", "--binary",
                           "-M", "--src-prefix=a/", "--dst-prefix=b/", "HEAD"], capture_output=True)
    shutil.rmtree(str(scratch))
    assert done.returncode == 0 and done.stdout, done.stderr
    return done.stdout


def _seeds(tmp_path, name="seeds", **patches):
    """A seed directory (outside the clone) holding ``patches`` as ``<name>.patch`` files."""
    seeds = tmp_path / name
    seeds.mkdir()
    for stem, blob in patches.items():
        (seeds / (stem + ".patch")).write_bytes(blob)
    return seeds


def _state(root, clone=None):
    """Everything a refusal must leave alone: every file under ``root`` that is not under a .git
    directory (path and sha256; a symlink as its target), plus the clone's HEAD (``score`` has no
    clone)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, str(root))
            if os.path.islink(full):
                found.append((rel, "link:" + os.readlink(full)))
            elif os.path.isdir(full):
                found.append((rel, "dir"))
            else:
                with open(full, "rb") as handle:
                    found.append((rel, hashlib.sha256(handle.read()).hexdigest()))
    return sorted(found), None if clone is None else _git(clone, "rev-parse", "HEAD")


def _cli(*args):
    return subprocess.run([sys.executable, str(TOOL), *[str(a) for a in args]],
                          text=True, capture_output=True, timeout=30)


def _manifest(seeds):
    """The parsed manifest. Existence is asserted first, so a tool that wrote none is a failed
    assertion, not a FileNotFoundError."""
    path = Path(seeds) / "manifest.json"
    assert path.exists(), "apply wrote no manifest.json in %s" % seeds
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_refused(done, tmp_path, clone, before, seeds=None):
    """Exit 2 with the tool's prefix on stderr, nothing on stdout, nothing changed, no manifest."""
    assert done.returncode == 2, done.stderr
    assert done.stderr.startswith("seed_defects.py: "), done.stderr
    assert done.stdout == ""
    assert _state(tmp_path, clone) == before
    for directory in (seeds, clone, clone / "seeds"):
        if directory is not None:
            assert not (Path(directory) / "manifest.json").exists()


# ---- the post-image lines ----------------------------------------------------------------------

def test_apply_records_post_image_lines_and_base_sha(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(
        tmp_path,
        **{"01-data-loss-a": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)")),
           "02-race-b": _patch(clone, "tools/b.py", _replace(20, "b_20 = os.system(x)\nb_20x = 1\nb_21x = 2", 2)),
           "03-injection-odd": _patch(clone, ODD, _delete(30))})
    base = _git(clone, "rev-parse", "HEAD")

    done = _cli("apply", clone, seeds)

    assert done.returncode == 0, done.stderr
    manifest = _manifest(seeds)
    assert list(manifest) == ["schema", "base_sha", "seeds"]
    assert manifest["schema"] == "readiness-seeds/v1" and manifest["base_sha"] == base
    # The planted lines, not the three context lines git shows around them. The deletion has no
    # line of its own: it records the two surviving neighbours.
    assert manifest["seeds"] == [
        {"id": "01-data-loss-a", "patch": "01-data-loss-a.patch", "file": "tools/a.py", "lines": [10]},
        {"id": "02-race-b", "patch": "02-race-b.patch", "file": "tools/b.py", "lines": [20, 21, 22]},
        {"id": "03-injection-odd", "patch": "03-injection-odd.patch", "file": ODD, "lines": [29, 30]},
    ]
    assert all(list(seed) == ["id", "patch", "file", "lines"] for seed in manifest["seeds"])
    assert (clone / "tools/a.py").read_text(encoding="utf-8").splitlines()[9] == "a_10 = eval(x)"
    assert len((clone / ODD).read_text(encoding="utf-8").splitlines()) == 59
    assert len(done.stdout.splitlines()) == 1 and "3" in done.stdout
    assert sorted(path.name for path in seeds.iterdir()) == [
        "01-data-loss-a.patch", "02-race-b.patch", "03-injection-odd.patch", "manifest.json"]
    assert _git(clone, "rev-parse", "HEAD") == base
    changed = subprocess.run(["git", "-C", str(clone), "status", "--porcelain", "-z"],
                             capture_output=True).stdout.split(b"\0")
    assert sorted(entry[:2] for entry in changed if entry) == [b" M", b" M", b" M"]
    # Seeded clones are dirty by design; a second run refuses rather than stacking seeds.
    before = _state(tmp_path, clone)
    again = _cli("apply", clone, seeds)
    assert again.returncode == 2 and _state(tmp_path, clone) == before


def test_apply_records_the_landed_line_when_git_applies_at_an_offset(tmp_path):
    clone = _clone(tmp_path)
    patch = _patch(clone, "tools/a.py", _replace(40, "a_40 = eval(x)"))
    seeds = _seeds(tmp_path, **{"01-race-offset": patch})
    assert b"@@ -37,7 +37,7 @@" in patch   # the header's number is 37; the changed line is 40
    # The clone moved on: two lines were added above the patch's context, so it lands two later.
    path = clone / "tools/a.py"
    path.write_text("# one\n# two\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    base = _commit(clone, "moved")

    done = _cli("apply", clone, seeds)

    assert done.returncode == 0, done.stderr
    manifest = _manifest(seeds)
    assert manifest["base_sha"] == base
    assert manifest["seeds"][0]["lines"] == [42]
    assert path.read_text(encoding="utf-8").splitlines()[41] == "a_40 = eval(x)"


def test_apply_records_only_changed_lines_under_diff_interhunkcontext(tmp_path):
    clone = _clone(tmp_path)
    both = _patch(clone, "tools/a.py", lambda lines: _replace(14, "a_14 = eval(y)")(
        _replace(10, "a_10 = eval(x)")(lines)))
    seeds = _seeds(tmp_path, **{"01-injection-two-lines": both})
    # A user's diff.interHunkContext would merge two changes four lines apart into one hunk and
    # make the unchanged lines between them look planted.
    _git(clone, "config", "diff.interHunkContext", "5")

    done = _cli("apply", clone, seeds)

    assert done.returncode == 0, done.stderr
    assert _manifest(seeds)["seeds"][0]["lines"] == [10, 14]


def test_apply_records_the_same_lines_under_any_diff_algorithm(tmp_path):
    clone = _clone(tmp_path)
    (clone / "tools/a.py").write_text(ALGO_BASE, encoding="utf-8")
    _commit(clone, "algorithm fixture")
    seeds = _seeds(tmp_path, **{"01-silent-fallback-algorithm": _patch(
        clone, "tools/a.py", lambda lines: ALGO_NEW.splitlines(True))})
    # A user's diff.algorithm moves where git cuts the hunks, and with them the recorded numbers.
    _git(clone, "config", "diff.algorithm", "histogram")

    done = _cli("apply", clone, seeds)

    assert done.returncode == 0, done.stderr
    assert _manifest(seeds)["seeds"][0]["lines"] == [34, 36, 37]


def test_ranges_pure_deletion_records_both_neighbours_clamped():
    sd = _tool()

    def hunks(*headers):
        return "diff --git a/f b/f\n--- a/f\n+++ b/f\n" + "".join(h + "\n-x\n+y\n" for h in headers)

    assert sd._ranges(hunks("@@ -31,2 +30,0 @@"), 60) == [30, 31]
    assert sd._ranges(hunks("@@ -1,2 +0,0 @@"), 60) == [1]
    assert sd._ranges(hunks("@@ -59,2 +58,0 @@"), 58) == [58]
    assert sd._ranges(hunks("@@ -5,3 +5,3 @@"), 60) == [5, 6, 7]
    assert sd._ranges(hunks("@@ -7 +7 @@"), 60) == [7]            # an omitted count is 1
    assert sd._ranges(hunks("@@ -9,0 +10,2 @@"), 60) == [10, 11]  # a pure insertion
    # Several hunks merge, sorted and without repeats; a deleted line that reads like a file header
    # ("--- x") and an added line that reads like a hunk header are content, not headers.
    text = ("@@ -3 +3 @@\n--- x\n+++ y\n@@ -4,0 +4,2 @@\n+@@ -99 +99 @@\n+b\n@@ -20,1 +21,0 @@\n-gone\n")
    assert sd._ranges(text, 60) == [3, 4, 5, 21, 22]
    # A form feed or U+2028 inside a planted line is not a line end here, so what follows it is not
    # a hunk header (str.splitlines would split there and count it).
    fake = "@@ -8 +8 @@\n+x\x0c@@ -50 +50 @@\n+y\u2028@@ -51,0 +52,3 @@\n"
    assert sd._ranges(fake, 60) == [8]
    assert sd._ranges("", 60) == []


# ---- refusals: exit 2, nothing changed ---------------------------------------------------------

@pytest.mark.parametrize("where", ["subdir", "equal", "symlink"])
def test_apply_refuses_seed_dir_inside_clone(tmp_path, where):
    clone = _clone(tmp_path)
    patch = _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))
    if where == "equal":
        seeds = clone
    else:
        seeds = clone / "seeds"
        seeds.mkdir()
    (seeds / "01-data-loss-a.patch").write_bytes(patch)
    if where == "symlink":
        # A link OUTSIDE the clone that points at a clone SUBDIRECTORY holding the patches.
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(str(seeds), str(outside / "link"))
        seeds = outside / "link"
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, seeds)

    _assert_refused(done, tmp_path, clone, before)
    assert "inside" in done.stderr


def _typed_in_another_case(tmp_path, clone):
    if not (tmp_path / clone.name.swapcase()).exists():
        pytest.skip("case-sensitive filesystem: a path typed in another case is another path")
    return tmp_path / clone.name.swapcase()


def test_apply_refuses_seed_dir_reached_in_another_case(tmp_path):
    clone = _clone(tmp_path)
    seeds = clone / "seeds"
    seeds.mkdir()
    (seeds / "01-data-loss-a.patch").write_bytes(_patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)")))
    typed = _typed_in_another_case(tmp_path, clone) / "seeds"
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, typed)

    _assert_refused(done, tmp_path, clone, before)
    assert "inside" in done.stderr


def test_apply_accepts_a_clone_typed_in_another_case(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(tmp_path, **{"01-data-loss-a": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))})
    typed = _typed_in_another_case(tmp_path, clone)

    done = _cli("apply", typed, seeds)   # rev-parse reports the real case; the same directory

    assert done.returncode == 0, done.stderr
    assert _manifest(seeds)["seeds"][0]["lines"] == [10]


def test_apply_refuses_clone_that_is_not_its_own_toplevel(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(tmp_path, **{"01-data-loss-a": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))})
    plain = tmp_path / "plain"
    plain.mkdir()

    for where in (clone / "tools", plain):   # a subdirectory of a repository; not a repository
        before = _state(tmp_path, clone)

        done = _cli("apply", where, seeds)

        _assert_refused(done, tmp_path, clone, before, seeds)


def test_apply_refuses_a_clone_on_a_branch(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(tmp_path, **{"01-data-loss-a": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))})
    _git(clone, "checkout", "-q", "-b", "work")
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, seeds)

    _assert_refused(done, tmp_path, clone, before, seeds)
    assert "detached" in done.stderr


def test_apply_refuses_dirty_tracked_files(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(tmp_path, **{"01-data-loss-a": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))})
    with (clone / "tools/c.py").open("a", encoding="utf-8") as handle:
        handle.write("\nc_61 = 2\n")
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, seeds)

    _assert_refused(done, tmp_path, clone, before, seeds)
    assert "uncommitted" in done.stderr


def test_apply_refuses_no_patches_or_two_patches_for_one_file(tmp_path):
    clone = _clone(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.txt").write_text("not a patch\n", encoding="utf-8")
    not_a_directory = tmp_path / "afile"
    not_a_directory.write_text("x\n", encoding="utf-8")
    twice = _seeds(tmp_path, "twice", **{
        "01-data-loss-first": _patch(clone, "tools/a.py", _replace(5, "a_05 = eval(x)")),
        "02-race-second": _patch(clone, "tools/a.py", _replace(40, "a_40 = eval(x)"))})

    for seeds in (empty, tmp_path / "missing", not_a_directory, twice):
        before = _state(tmp_path, clone)

        done = _cli("apply", clone, seeds)

        _assert_refused(done, tmp_path, clone, before, seeds)
    assert "01-data-loss-first.patch" in done.stderr and "02-race-second.patch" in done.stderr
    assert "tools/a.py" in done.stderr


def _create(scratch):
    (scratch / "tools/new.py").write_text(_numbered("n", 5), encoding="utf-8")


def _delete_file(scratch):
    (scratch / "tools/c.py").unlink()


def _rename(scratch):
    (scratch / "tools/c.py").rename(scratch / "tools/c2.py")
    with (scratch / "tools/c2.py").open("a", encoding="utf-8") as handle:
        handle.write("\nc_61 = 2\n")


def _mode(scratch):
    os.chmod(str(scratch / "tools/a.py"), 0o755)


def _binary(scratch):
    (scratch / "tools/a.py").write_bytes(b"\x00\x01\x02 binary \xff\n")


def _two_files(scratch):
    for rel, prefix in (("tools/a.py", "a"), ("tools/b.py", "b")):
        path = scratch / rel
        path.write_text(path.read_text(encoding="utf-8").replace(prefix + "_10 = 1", prefix + "_10 = 2"),
                        encoding="utf-8")


@pytest.mark.parametrize("kind", ["create", "delete", "rename", "mode", "binary", "two-files", "truncated"])
def test_apply_refuses_create_delete_rename_mode_binary_and_two_file_patches(tmp_path, kind):
    clone = _clone(tmp_path)
    operations = {"create": _create, "delete": _delete_file, "rename": _rename, "mode": _mode,
                  "binary": _binary, "two-files": _two_files}
    if kind == "truncated":
        # A patch cut short in its last hunk line is not a patch git will read; a corrupt seed is
        # a bad input, not a failure to apply.
        blob = _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)"))[:-1]
    else:
        blob = _scratch_patch(clone, tmp_path, operations[kind])
    stem = "01-injection-" + kind
    seeds = _seeds(tmp_path, **{stem: blob})
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, seeds)

    _assert_refused(done, tmp_path, clone, before, seeds)
    assert stem + ".patch" in done.stderr


# ---- exit 1: a patch that does not apply -------------------------------------------------------

def test_apply_names_the_failing_patch_and_changes_nothing(tmp_path):
    clone = _clone(tmp_path)
    seeds = _seeds(tmp_path, **{
        "01-data-loss-good": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)")),
        "02-race-bad": _other_base_patch(clone, tmp_path, "tools/b.py", _replace(20, "b_20 = eval(x)"))})
    before = _state(tmp_path, clone)

    done = _cli("apply", clone, seeds)

    assert done.returncode == 1, done.stderr
    assert done.stderr.startswith("seed_defects.py: ")
    # git's own message names only the target file; the tool must name the patch, and only that one.
    assert "02-race-bad.patch" in done.stderr
    assert "01-data-loss-good" not in done.stderr
    assert _state(tmp_path, clone) == before
    assert not (seeds / "manifest.json").exists()


def test_stream_apply_is_atomic_even_if_the_check_phase_is_skipped(tmp_path, monkeypatch):
    sd = _tool()
    clone = _clone(tmp_path)
    # The bad patch sorts LAST: given as file arguments git stops at the first failing patch, so
    # "bad good" would apply nothing even without a stream, and only "good bad" arms this control.
    seeds = _seeds(tmp_path, **{
        "01-data-loss-good": _patch(clone, "tools/a.py", _replace(10, "a_10 = eval(x)")),
        "02-race-bad": _other_base_patch(clone, tmp_path, "tools/b.py", _replace(20, "b_20 = eval(x)"))})
    monkeypatch.setattr(sd, "_check_each", lambda *args, **kwargs: None)
    before = _state(tmp_path, clone)

    try:
        sd.apply(clone, seeds)
        raised = None
    except RuntimeError as exc:
        raised = exc

    assert raised is not None, "apply returned although git refused the stream"
    assert _state(tmp_path, clone) == before
    assert not (seeds / "manifest.json").exists()


# ---- score: recall of a findings list against the manifest -------------------------------------

SEEDED = [("01-data-loss-x", "tools/a.py", [10]), ("02-race-y", "tools/b.py", [20, 21]),
          ("03-injection-z", "tools/c.py", [30])]


def _seed_dir(tmp_path, seeds=SEEDED, name="seeds"):
    """A seed directory holding the manifest.json ``apply`` would have written for ``seeds``
    (``(id, file, lines)`` triples). ``score`` reads nothing else."""
    directory = tmp_path / name
    directory.mkdir()
    manifest = {"schema": "readiness-seeds/v1", "base_sha": "0" * 40,
                "seeds": [{"id": ident, "patch": ident + ".patch", "file": rel, "lines": lines}
                          for ident, rel, lines in seeds]}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return directory


def _findings_file(tmp_path, content, name="findings.json"):
    path = tmp_path / name
    path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return path


def _score(tmp_path, findings, seeds=SEEDED, *flags):
    return _cli("score", _seed_dir(tmp_path, seeds), _findings_file(tmp_path, findings), *flags)


def test_score_two_of_three_is_recall_0_667_exit_1(tmp_path):
    seeds = _seed_dir(tmp_path)
    findings = _findings_file(tmp_path, [{"path": "tools/a.py", "line": 12, "id": "r1"},
                                         {"path": "tools/b.py", "line": 25},
                                         {"path": "tools/c.py", "line": 90}])
    before = _state(tmp_path)

    done = _cli("score", seeds, findings)

    assert done.returncode == 1, done.stderr
    # Matched seeds in full, with the first finding that matched (a missing id is its index); a
    # missed one only by its class; then the recall.
    assert done.stdout.splitlines() == ['matched 01-data-loss-x (finding "r1")',
                                        'matched 02-race-y (finding 1)',
                                        "missed 03-injection",
                                        "recall: 0.667 (2/3)"]
    assert done.stderr == ""
    assert _state(tmp_path) == before   # score reads, it never writes


def test_score_all_three_is_recall_1_000_exit_0(tmp_path):
    done = _score(tmp_path, [{"path": "tools/a.py", "line": 10}, {"path": "tools/b.py", "line": 21},
                             {"path": "tools/c.py", "line": 30}])

    assert done.returncode == 0, done.stderr
    assert [line[:8] for line in done.stdout.splitlines()] == ["matched "] * 3 + ["recall: "]
    assert done.stdout.splitlines()[-1:] == ["recall: 1.000 (3/3)"]


@pytest.mark.parametrize("line,hit", [
    pytest.param(14, False, id="minus-6"), pytest.param(15, True, id="minus-5"),
    pytest.param(20, True, id="same-line"),
    pytest.param(25, True, id="plus-5"), pytest.param(26, False, id="plus-6"),
    pytest.param(35, False, id="between-two-lines"),
    pytest.param(45, True, id="second-line-minus-5"), pytest.param(44, False, id="second-line-minus-6"),
    pytest.param(55, True, id="second-line-plus-5"), pytest.param(56, False, id="second-line-plus-6"),
])
def test_score_window_is_inclusive_5_and_6_misses(tmp_path, line, hit):
    # One seed that changed lines 20 and 50: a finding matches within 5 lines of either, inclusive.
    done = _score(tmp_path, [{"path": "tools/a.py", "line": line}], [("01-race-x", "tools/a.py", [20, 50])])

    assert done.returncode == (0 if hit else 1), done.stderr
    assert done.stdout.splitlines()[-1:] == ["recall: 1.000 (1/1)" if hit else "recall: 0.000 (0/1)"]


@pytest.mark.parametrize("matched,total,recall,code", [
    pytest.param(4, 5, "0.800", 0, id="4-of-5"), pytest.param(3, 4, "0.750", 1, id="3-of-4"),
    pytest.param(7, 9, "0.778", 1, id="7-of-9"), pytest.param(8, 10, "0.800", 0, id="8-of-10"),
])
def test_score_threshold_is_integer(tmp_path, matched, total, recall, code):
    seeded = [("%02d-race-s%d" % (n, n), "tools/f%d.py" % n, [10]) for n in range(1, total + 1)]
    findings = [{"path": "tools/f%d.py" % n, "line": 10} for n in range(1, matched + 1)]

    done = _score(tmp_path, findings, seeded)

    assert done.returncode == code, done.stderr
    assert done.stdout.splitlines()[-1:] == ["recall: %s (%d/%d)" % (recall, matched, total)]


GOOD_FINDINGS = [{"path": "tools/a.py", "line": 10}]
ONE = {"id": "01-race-x", "file": "tools/a.py", "lines": [10]}


def _manifest_with(**changes):
    return dict({"schema": "readiness-seeds/v1", "base_sha": "0" * 40, "seeds": [ONE]}, **changes)


# case: (the manifest.json content, the findings content, a word the message must carry); "good" is
# the valid one, a string is written as it is, None means no such file.
BAD_INPUT = {
    "not-json": ("good", "this is not json", "findings"),
    "dict-not-list": ("good", {}, "findings"),
    "one-finding-not-in-a-list": ("good", {"path": "tools/a.py", "line": 10}, "findings"),
    "null": ("good", "null", "findings"),
    "entry-not-object": ("good", ["tools/a.py:10"], "findings"),
    "path-not-string": ("good", [{"path": 5, "line": 10}], "findings"),
    "missing-path": ("good", [{"line": 10}], "findings"),
    "missing-line": ("good", [{"path": "tools/a.py"}], "findings"),
    "line-string": ("good", [{"path": "tools/a.py", "line": "10"}], "findings"),
    "bool-line": ("good", [{"path": "tools/a.py", "line": True}], "findings"),
    "line-zero": ("good", [{"path": "tools/a.py", "line": 0}], "findings"),
    "line-float": ("good", [{"path": "tools/a.py", "line": 5.0}], "findings"),
    "id-not-scalar": ("good", [{"path": "tools/a.py", "line": 10, "id": ["x"]}], "findings"),
    "no-findings-file": ("good", None, "findings"),
    "no-manifest": (None, "good", "manifest"),
    "truncated": ('{"schema": "readiness-seeds/v1", "base_sha": "0", "seeds": [{"id": "01-race-x", "pa',
                  "good", "manifest"),
    "manifest-not-object": ([], "good", "manifest"),
    "bad-schema": (_manifest_with(schema="readiness-seeds/v0"), "good", "manifest"),
    "zero-seeds": (_manifest_with(seeds=[]), "good", "manifest"),
    "seeds-not-list": (_manifest_with(seeds={"id": "01-race-x"}), "good", "manifest"),
    "seed-not-object": (_manifest_with(seeds=["01-race-x"]), "good", "manifest"),
    "duplicate-ids": (_manifest_with(seeds=[ONE, ONE]), "good", "manifest"),
    "id-not-string": (_manifest_with(seeds=[dict(ONE, id=1)]), "good", "manifest"),
    "file-not-string": (_manifest_with(seeds=[dict(ONE, file=None)]), "good", "manifest"),
    "empty-lines": (_manifest_with(seeds=[dict(ONE, lines=[])]), "good", "manifest"),
    "lines-not-ints": (_manifest_with(seeds=[dict(ONE, lines=["10"])]), "good", "manifest"),
    "lines-bool": (_manifest_with(seeds=[dict(ONE, lines=[True])]), "good", "manifest"),
    "line-zero-in-seed": (_manifest_with(seeds=[dict(ONE, lines=[0])]), "good", "manifest"),
}


@pytest.mark.parametrize("case", sorted(BAD_INPUT))
def test_score_bad_input_exit_2(tmp_path, case):
    manifest, findings, word = BAD_INPUT[case]
    seeds = _seed_dir(tmp_path)
    if manifest is None:
        (seeds / "manifest.json").unlink()
    elif manifest != "good":
        (seeds / "manifest.json").write_text(manifest if isinstance(manifest, str) else json.dumps(manifest),
                                             encoding="utf-8")
    path = tmp_path / "findings.json"
    if findings is not None:
        _findings_file(tmp_path, GOOD_FINDINGS if findings == "good" else findings)
    before = _state(tmp_path)

    done = _cli("score", seeds, path)

    assert done.returncode == 2, done.stderr
    assert done.stderr.startswith("seed_defects.py: "), done.stderr
    assert word in done.stderr
    assert done.stdout == ""
    assert _state(tmp_path) == before


def test_score_empty_findings_is_recall_zero_exit_1(tmp_path):
    done = _score(tmp_path, [])

    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines() == ["missed 01-data-loss", "missed 02-race", "missed 03-injection",
                                        "recall: 0.000 (0/3)"]


def test_score_normalises_dot_slash_and_backslash_and_absolute_path_misses(tmp_path):
    done = _score(tmp_path, [{"path": "./tools/a.py", "line": 10}, {"path": "tools\\b.py", "line": 20},
                             {"path": "/srv/clone/tools/c.py", "line": 30}])

    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines() == ['matched 01-data-loss-x (finding 0)',
                                        'matched 02-race-y (finding 1)',
                                        "missed 03-injection",
                                        "recall: 0.667 (2/3)"]


def test_score_default_withholds_slug_file_and_line_flag_shows_them(tmp_path):
    seeded = [("01-data-loss-x", "tools/a.py", [10]), ("02-race-y", "tools/b.py", [20]),
              ("03-injection-quokka", "tools/c.py", [48])]
    seeds = _seed_dir(tmp_path, seeded)
    findings = _findings_file(tmp_path, [{"path": "tools/a.py", "line": 10}, {"path": "tools/b.py", "line": 20}])

    default = _cli("score", seeds, findings)
    shown = _cli("score", seeds, findings, "--show-missed-locations")

    # 4 and 8 occur nowhere else in the output (01 02 03, 0.667, 2/3), so the line is not a false hit.
    assert default.returncode == 1, default.stderr
    assert "03-injection" in default.stdout and "01-data-loss-x" in default.stdout
    for secret in ("quokka", "tools/c.py", "48", "4", "8"):
        assert secret not in default.stdout, secret
    assert shown.returncode == 1, shown.stderr
    for revealed in ("03-injection-quokka", "tools/c.py", "48"):
        assert revealed in shown.stdout, revealed


def test_score_names_hyphenated_classes_and_unclassified(tmp_path):
    sd = _tool()
    assert sd.CLASSES == ("data-loss", "silent-fallback", "injection", "race", "portability",
                          "test-cannot-fail")
    classes = ["data-loss", "silent-fallback", "injection", "race", "portability", "test-cannot-fail"]
    seeded = [("%02d-%s-quokka" % (n, name), "tools/f%d.py" % n, [10]) for n, name in enumerate(classes, 1)]
    seeded += [("07-weird", "tools/g.py", [10]), ("08-race", "tools/h.py", [10])]

    done = _score(tmp_path, [], seeded)

    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines() == [
        "missed 01-data-loss", "missed 02-silent-fallback", "missed 03-injection", "missed 04-race",
        "missed 05-portability", "missed 06-test-cannot-fail", "missed unclassified", "missed unclassified",
        "recall: 0.000 (0/8)"]


# ---- the example seeds, end to end -------------------------------------------------------------

def test_example_seeds_apply_then_score_end_to_end(tmp_path):
    # The three committed examples: a made-up base (base/tools/report_*.py) and one patch each in
    # seeds/, made by git diff. apply plants them in a clone of the base, score reads them back.
    assert EXAMPLES.is_dir(), "the example seeds are missing: %s" % EXAMPLES
    base = EXAMPLES / "base" / "tools"
    names = sorted(path.name for path in (EXAMPLES / "seeds").glob("*.patch"))
    assert names == ["01-data-loss-truncate-in-place.patch", "02-silent-fallback-swallow-bad-json.patch",
                     "03-injection-shell-tar.patch"]
    assert sorted(path.name for path in base.glob("*.py")) == ["report_load.py", "report_pack.py", "report_save.py"]
    clone = _clone(tmp_path, files={"tools/" + path.name: path.read_text(encoding="utf-8")
                                    for path in base.glob("*.py")})
    seeds = tmp_path / "seeds"
    shutil.copytree(str(EXAMPLES / "seeds"), str(seeds))   # apply writes manifest.json here, not in the repo

    planted = _cli("apply", clone, seeds)

    assert planted.returncode == 0, planted.stderr
    manifest = _manifest(seeds)
    assert [(seed["id"], seed["file"], seed["lines"]) for seed in manifest["seeds"]] == [
        ("01-data-loss-truncate-in-place", "tools/report_save.py", [9, 10, 11]),
        ("02-silent-fallback-swallow-bad-json", "tools/report_load.py", [12]),
        ("03-injection-shell-tar", "tools/report_pack.py", [8])]
    # The defects are really in the clone, on the recorded lines.
    assert (clone / "tools/report_save.py").read_text(encoding="utf-8").splitlines()[8].strip() == \
        'with open(path, "w", encoding="utf-8") as handle:'
    assert (clone / "tools/report_load.py").read_text(encoding="utf-8").splitlines()[11].strip() == "except Exception:"
    assert "shell=True" in (clone / "tools/report_pack.py").read_text(encoding="utf-8").splitlines()[7]

    everything = [{"path": seed["file"], "line": seed["lines"][0]} for seed in manifest["seeds"]]
    full = _cli("score", seeds, _findings_file(tmp_path, everything))
    two = _cli("score", seeds, _findings_file(tmp_path, everything[:2], name="two.json"))

    assert full.returncode == 0, full.stderr
    assert full.stdout.splitlines() == ['matched 01-data-loss-truncate-in-place (finding 0)',
                                        'matched 02-silent-fallback-swallow-bad-json (finding 1)',
                                        'matched 03-injection-shell-tar (finding 2)',
                                        "recall: 1.000 (3/3)"]
    assert two.returncode == 1, two.stderr
    assert two.stdout.splitlines()[-2:] == ["missed 03-injection", "recall: 0.667 (2/3)"]
    assert "shell-tar" not in two.stdout and "report_pack" not in two.stdout


# ---- the protocol page ---------------------------------------------------------------------------

def test_doc_states_the_tool_constants(tmp_path):
    # docs/launch/seeded-defects.md and the tool must say the same thing: the constants the page
    # quotes are read from the tool, and the sample output it prints is what the tool prints on the
    # example seeds. The page is read only after its existence is asserted.
    assert DOC.exists(), "the protocol page is missing: %s" % DOC
    text = DOC.read_text(encoding="utf-8")
    tool = _tool()
    wanted = ["~/.sigma-ops/readiness/seeds/", tool.SCHEMA, "within %d lines" % tool.WINDOW, "80%",
              "re-run", "not reported", "--show-missed-locations", "Known leak", "22%",
              "large files", "install.sh", "frozen commit"] + ["`%s`" % slug for slug in tool.CLASSES]
    missing = [item for item in wanted if item not in text]
    assert not missing, "the page does not state: %s" % ", ".join(missing)

    links = LINK.findall(text)
    assert links, "the page links to nothing"
    broken = [link for link in links
              if not link.startswith(("http://", "https://", "mailto:"))
              and not (DOC.parent / link.split("#", 1)[0]).resolve().exists()]
    assert not broken, "links that resolve to nothing: %s" % ", ".join(broken)

    # The tool the page documents, run: its own help names the flag, and what it prints on the
    # three example seeds is what the page shows (exit codes first, then the text).
    helped = _cli("score", "--help")
    assert helped.returncode == 0, helped.stderr
    assert "--show-missed-locations" in helped.stdout
    assert EXAMPLES.is_dir(), "the example seeds are missing: %s" % EXAMPLES
    base = EXAMPLES / "base" / "tools"
    clone = _clone(tmp_path, files={"tools/" + path.name: path.read_text(encoding="utf-8")
                                    for path in base.glob("*.py")})
    seeds = tmp_path / "seeds"
    shutil.copytree(str(EXAMPLES / "seeds"), str(seeds))
    planted = _cli("apply", clone, seeds)
    assert planted.returncode == 0, planted.stderr
    found = [{"path": seed["file"], "line": seed["lines"][0]} for seed in _manifest(seeds)["seeds"][:2]]
    findings = _findings_file(tmp_path, found)
    shown = _cli("score", seeds, findings)
    located = _cli("score", seeds, findings, "--show-missed-locations")
    assert shown.returncode == 1, shown.stderr
    assert located.returncode == 1, located.stderr
    assert shown.stdout.rstrip("\n") in text, "the sample output on the page is not what score prints"
    assert located.stdout.splitlines()[-2] in text, "the --show-missed-locations line is not on the page"
