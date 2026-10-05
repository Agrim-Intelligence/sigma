"""Controls for the prefix rename (#523): the mechanical rename and the leftover check.

The old name is spelled from fragments everywhere in this file, so the check does not flag it.
Every case drives the tools as a SUBPROCESS through the documented gesture (`python3
tools/rename_check.py` from inside the repository, no flags), so a documented invocation that cannot
fail is caught here, not only a function that can.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECK = ROOT / "tools" / "rename_check.py"
RENAME = ROOT / "tools" / "rename_prefix.py"
OLD = "agr" + "im"            # the retired skill prefix, never written whole
ORG = OLD.capitalize() + "-Intelligence"
MARK = "- **Every skill and command now starts with `sigma-`**"   # CHANGELOG history marker


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path, files, executable=()):
    _git(tmp_path, "init", "-q")
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if rel in executable:
            p.chmod(0o755)
    _git(tmp_path, "add", "-A")
    return tmp_path


def _run(tool, cwd, *args):
    return subprocess.run([sys.executable, str(tool), *args], cwd=cwd, text=True, capture_output=True)


def test_check_exits_1_and_names_a_planted_file_via_the_documented_gesture(tmp_path):
    _repo(tmp_path, {"docs/x.md": "fine\nfine\nrun /%s-loop now\n" % OLD, "docs/y.md": "clean\n"})
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "docs/x.md:3:" in r.stdout and "docs/y.md" not in r.stdout


def test_check_flags_a_tracked_path_with_the_old_prefix(tmp_path):
    _repo(tmp_path, {"skills/%s-x/SKILL.md" % OLD: "clean\n", "hooks/%s_gate.sh" % OLD.upper(): "clean\n"})
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1, (r.returncode, r.stdout)
    assert "skills/%s-x/SKILL.md" % OLD in r.stdout and "hooks/%s_gate.sh" % OLD.upper() in r.stdout


def test_check_flags_non_utf8_and_uppercase_content(tmp_path):
    _repo(tmp_path, {"a.txt": "ok\n"})
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe " + OLD.upper().encode() + b"-X\n")
    _git(tmp_path, "add", "-A")
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1 and "b.bin" in r.stdout, (r.returncode, r.stdout)


def test_check_allowlist_and_clean_tree_exit_0(tmp_path):
    ev = "docs/launch/evidence/330-control.md"
    allowed = {
        "README.md": "by %s and %s\n" % (ORG, OLD.capitalize() + " Intelligence"),
        "CHANGELOG.md": "# Changelog\n\n%s rename\n\n- old /%s-loop entry\n" % (MARK, OLD),
        ev: "recorded /%s-doctor output\n" % OLD,
    }
    _repo(tmp_path, allowed)
    r = _run(CHECK, tmp_path)
    assert r.returncode == 0 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
    # a new CHANGELOG line ABOVE the marker is not history
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n- new /%s-loop\n\n%s rename\n" % (OLD, MARK))
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1 and "CHANGELOG.md:3:" in r.stdout, (r.returncode, r.stdout)
    # a NEW evidence file is not history
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n%s rename\n" % MARK)
    new = tmp_path / "docs/launch/evidence/new-run.md"
    new.write_text("/%s-loop\n" % OLD)
    _git(tmp_path, "add", "-A")
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1 and "docs/launch/evidence/new-run.md:1:" in r.stdout, (r.returncode, r.stdout)


def test_check_refuses_outside_a_git_repo(tmp_path):
    r = _run(CHECK, tmp_path)
    assert r.returncode == 2 and r.stdout == "" and "REFUSED" in r.stderr, (r.returncode, r.stdout, r.stderr)


def test_check_works_from_a_subdirectory(tmp_path):
    _repo(tmp_path, {"top.md": "x /%s-loop\n" % OLD, "sub/ok.md": "clean\n"})
    r = _run(CHECK, tmp_path / "sub")
    assert r.returncode == 1 and "top.md:1:" in r.stdout, (r.returncode, r.stdout)


def _sample(extra=None):
    files = {
        "skills/%s-a/SKILL.md" % OLD: "name: %s-a\nprint(\"\\n%s-init: x\")\nby %s/%s-a\n" % (OLD, OLD, ORG, OLD),
        "skills/%s-a/scripts/run.py" % OLD: "import %s_gate\n" % OLD,
        "hooks/%s_gate.sh" % OLD: "#!/bin/sh\n# %s_gate\n" % OLD,
        "CHANGELOG.md": "%s\nhistory /%s-a\n" % (MARK, OLD),
        "docs/launch/evidence/330-control.md": "recorded /%s-a\n" % OLD,
        "notes.md": "none\n",
    }
    files.update(extra or {})
    return files


def test_rename_moves_dirs_rewrites_tokens_and_is_idempotent(tmp_path):
    _repo(tmp_path, _sample(), executable=("hooks/%s_gate.sh" % OLD,))
    cache = tmp_path / "skills" / ("%s-a" % OLD) / "scripts" / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_bytes(b"x")                          # untracked: must travel with the dir
    dry = _run(RENAME, tmp_path, "--dry-run")
    assert dry.returncode == 0 and (tmp_path / ("skills/%s-a" % OLD)).is_dir(), dry.stderr
    assert "skills/sigma-a" in dry.stdout
    r = _run(RENAME, tmp_path)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert (tmp_path / "skills/sigma-a/SKILL.md").is_file()
    assert not (tmp_path / ("skills/%s-a" % OLD)).exists()
    assert (tmp_path / "skills/sigma-a/scripts/__pycache__/x.pyc").is_file()
    body = (tmp_path / "skills/sigma-a/SKILL.md").read_text()
    assert body == "name: sigma-a\nprint(\"\\nsigma-init: x\")\nby %s/sigma-a\n" % ORG, body
    assert (tmp_path / "skills/sigma-a/scripts/run.py").read_text() == "import sigma_gate\n"
    gate = tmp_path / "hooks/sigma_gate.sh"
    assert gate.read_text() == "#!/bin/sh\n# sigma_gate\n" and os.access(gate, os.X_OK)
    assert (tmp_path / "CHANGELOG.md").read_text() == "%s\nhistory /%s-a\n" % (MARK, OLD)   # history untouched
    assert (tmp_path / "docs/launch/evidence/330-control.md").read_text() == "recorded /%s-a\n" % OLD
    assert "skills/sigma-a/SKILL.md" in r.stdout and "hooks/sigma_gate.sh" in r.stdout
    # idempotent: a second run changes and prints nothing; the check then agrees the tree is clean
    again = _run(RENAME, tmp_path)
    assert again.returncode == 0 and again.stdout == "", again.stdout
    assert _run(CHECK, tmp_path).returncode == 0       # only allowlisted history still carries the old name


def test_rename_also_rewrites_untracked_files_git_would_commit(tmp_path):
    _repo(tmp_path, {"notes.md": "none\n"})
    (tmp_path / "plan.md").write_text("see skills/%s-loop/x\n" % OLD)     # untracked, not ignored
    r = _run(RENAME, tmp_path)
    assert r.returncode == 0 and (tmp_path / "plan.md").read_text() == "see skills/sigma-loop/x\n", r.stderr


def test_rename_refuses_an_existing_destination(tmp_path):
    _repo(tmp_path, {"skills/%s-a/x" % OLD: "1\n", "skills/sigma-a/y": "2\n"})
    r = _run(RENAME, tmp_path)
    assert r.returncode == 2 and "REFUSED" in r.stderr, (r.returncode, r.stderr)
    assert (tmp_path / ("skills/%s-a/x" % OLD)).is_file() and (tmp_path / "skills/sigma-a/y").is_file()


def test_repo_tree_has_no_old_prefix():
    """The documented gesture, from the repository root, no flags (AC-1, AC-2, AC-6)."""
    r = subprocess.run([sys.executable, "tools/rename_check.py"], cwd=ROOT, text=True, capture_output=True)
    assert r.returncode == 0, "\n".join(r.stdout.splitlines()[:20])


# ---- the old plugin install id (#524) ------------------------------------------------------------
PLUGIN = "sig" + "ma"
OLD_ID = PLUGIN + "@" + PLUGIN       # the pre-launch install id, never written whole


def test_check_flags_the_old_install_id_outside_the_allowlist(tmp_path):
    _repo(tmp_path, {
        "docs/a.md": "run `claude plugin install %s` now\n" % OLD_ID,
        "docs/b.md": "the old id was %s.\n" % OLD_ID,                 # sentence-final full stop
        "docs/c.md": "a fork id %s-market is not it\n" % OLD_ID,      # longer id: a different thing
        "docs/d.md": "a mail address like %s.example is not it either\n" % OLD_ID,
    })
    r = _run(CHECK, tmp_path)
    assert r.returncode == 1, (r.returncode, r.stdout)
    assert "docs/a.md:1:" in r.stdout and "docs/b.md:1:" in r.stdout, r.stdout
    assert "docs/c.md" not in r.stdout and "docs/d.md" not in r.stdout, r.stdout
    # allowed: the migration document, a pinned history record; NOT a new history file
    clean = tmp_path / "clean"
    clean.mkdir()
    _repo(clean, {"docs/upgrading.md": "## From the pre-launch name\n%s\n" % OLD_ID,
                  ".sdlc/plans/231.md": "recorded run on %s\n" % OLD_ID})
    assert _run(CHECK, clean).returncode == 0
    (clean / ".sdlc/plans/999.md").write_text("new plan names %s\n" % OLD_ID)
    _git(clean, "add", "-A")
    r = _run(CHECK, clean)
    assert r.returncode == 1 and ".sdlc/plans/999.md:1:" in r.stdout, (r.returncode, r.stdout)
