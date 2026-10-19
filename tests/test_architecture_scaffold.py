"""`sdlc_init.py --architecture-doc`: the opt-in scaffold of the architecture document (#1784, R-03
slice 2 of epic #1782).

Every gesture-level test runs the DOCUMENTED gesture as a subprocess, `python3
skills/sigma-init/scripts/sdlc_init.py <repo> --architecture-doc`, on a throwaway git repository.
`_snapshot` covers the WHOLE repository minus `.git`: the plan's idempotence check (run
`sdlc_init.py <repo>` twice, `diff -r`) found the two trees byte-identical, so a refused or kept
run that changes nothing leaves the snapshot equal.

Rules this file keeps (the recorded red/green proof depends on them): no test is parametrized (the
tables are literals, looped inside ONE test with the row named in every assertion message); nothing
prints; nothing skips; a missing product piece is an ASSERTION failure, never an import, attribute
or file error (each test that needs the product asserts it exists first); tests that look vacuous
before the implementation carry a RED PRECONDITION that fails first.

`test_rerun_keeps_existing_bytes` is a gesture-level check (a subprocess rerun). The deterministic
control at the exact seam is `test_exclusive_create_survives_a_check_then_create_race`: it lies to
`exists()` for the scaffold's own destination only, so a check-then-create implementation overwrites
and the exclusive `open(dest, "x")` does not.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SDLC_INIT = ROOT / "skills" / "sigma-init" / "scripts" / "sdlc_init.py"
INIT_FLOW = ROOT / "skills" / "sigma-init" / "scripts" / "init_flow.py"
ARCH_DOC = ROOT / "skills" / "sigma-init" / "scripts" / "architecture_doc.py"
TEMPLATE = ROOT / "skills" / "sigma-init" / "architecture-templates" / "architecture.md.tmpl"
TEMPLATES_DIR = ROOT / "skills" / "sigma-init" / "templates"
DOC = ROOT / "docs" / "architecture-doc.md"
DEFAULT_REL = "docs/architecture/architecture.md"
FLAG = "--architecture-doc"


# ------------------------------------------------------------------------------------------ helpers

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _env():
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def _init(repo, *flags):
    p = subprocess.run([sys.executable, str(SDLC_INIT), str(repo), *flags], capture_output=True,
                       text=True, env=_env(), timeout=120)
    return p.returncode, p.stdout, p.stderr


def _repo(tmp_path, name="repo"):
    """A git repository that has had one plain /sigma-init run (so `.sdlc/config.json` exists)."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_env())
    rc, out, err = _init(repo)
    assert rc == 0, out + err
    return repo


def _scaffold(repo, *extra):
    return _init(repo, FLAG, *extra)


def _snapshot(repo):
    snap = {}
    for dirpath, dirnames, filenames in os.walk(str(repo)):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel_dir = os.path.relpath(dirpath, str(repo))
        snap[rel_dir + "/"] = None
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, str(repo))
            if os.path.islink(full):
                snap[rel] = ("link", os.readlink(full))
            else:
                with open(full, "rb") as fh:
                    snap[rel] = fh.read()
    return snap


def _set_root(repo, value):
    path = repo / ".sdlc" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.setdefault("architecture", {})["system_root"] = value
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _block(out):
    """The architecture scaffold's own lines: the line before the first `architecture.md` line up to
    the next blank line (the rest of the output holds unrelated text, e.g. the preflight's git tips)."""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if "architecture.md" in line:
            j = i
            while j < len(lines) and lines[j].strip():
                j += 1
            return "\n".join(lines[max(i - 1, 0):j])
    return ""


def _engine():
    return _load("architecture_doc_for_scaffold", ARCH_DOC)


def _lint_clean(repo, rel_dir="docs/architecture"):
    p = subprocess.run([sys.executable, str(ARCH_DOC), "lint", rel_dir, "--repo-root", str(repo)],
                       cwd=str(repo), capture_output=True, text=True, env=_env(), timeout=60)
    return p.returncode, p.stdout + p.stderr


# --------------------------------------------------------------------------------- module and files

def test_scaffold_module_attributes_present():
    mod = _load("sdlc_init_scaffold_attrs", SDLC_INIT)
    assert getattr(mod, "scaffold_architecture_doc", None), "scaffold_architecture_doc missing"
    assert getattr(mod, "ARCH_TEMPLATES", None), "ARCH_TEMPLATES missing"
    assert hasattr(mod, "_write_text"), "the _write_text write seam is missing"
    assert pathlib.Path(mod.ARCH_TEMPLATES).name == "architecture-templates"


def test_template_exists_outside_templates_dir():
    assert TEMPLATE.is_file(), "template missing: %s" % TEMPLATE
    assert TEMPLATES_DIR not in TEMPLATE.parents, "the template must NOT live under templates/"
    assert not [p for p in TEMPLATES_DIR.rglob("*") if "architecture" in p.name], \
        "no architecture file may sit under templates/ (every file there is copied on every init)"
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "\r" not in text and text.isascii(), "template must be ASCII with LF endings"


# ---------------------------------------------------------------------------------- fresh scaffold

def test_fresh_scaffold_lints_clean_via_documented_gesture(tmp_path):
    repo = _repo(tmp_path)
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    assert (repo / DEFAULT_REL).is_file(), out
    code, text = _lint_clean(repo)
    assert code == 0, text


def test_every_required_heading_present(tmp_path):
    repo = _repo(tmp_path)
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    doc = repo / DEFAULT_REL
    assert doc.is_file(), out
    lines = doc.read_text(encoding="utf-8").splitlines()
    headings = [l[3:].strip() for l in lines if l.startswith("## ")]
    required = _engine().REQUIRED_HEADINGS
    assert required, "engine REQUIRED_HEADINGS empty"
    for name in required:
        assert name in headings, "missing heading %r in %r" % (name, headings)
    h1 = [l for l in lines if l.startswith("# ")]
    assert h1 and repo.name in h1[0], h1
    assert not [l for l in lines if "{{" in l], "unrendered placeholder left in the document"


# -------------------------------------------------------------------------------------- never overwrites

def test_rerun_keeps_existing_bytes(tmp_path):
    """Gesture-level check (a subprocess rerun); the deterministic control is the race test."""
    repo = _repo(tmp_path)
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    doc = repo / DEFAULT_REL
    assert doc.is_file(), out
    doc.write_bytes(b"# my own edit\n")
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    assert doc.read_bytes() == b"# my own edit\n"
    assert "(exists, kept)" in out and DEFAULT_REL in out, out


def test_exclusive_create_survives_a_check_then_create_race(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    mod = _load("sdlc_init_scaffold_race", SDLC_INIT)
    assert getattr(mod, "scaffold_architecture_doc", None), "scaffold_architecture_doc missing"
    dest = pathlib.Path(os.path.realpath(str(repo))) / DEFAULT_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"# written by someone else a moment ago\n")
    real_dest = os.path.realpath(str(dest))
    real_path_exists, real_os_exists = pathlib.Path.exists, os.path.exists

    def lies_path(self, *a, **k):
        if os.path.realpath(str(self)) == real_dest:
            return False
        return real_path_exists(self, *a, **k)

    def lies_os(path, *a, **k):
        if os.path.realpath(os.fspath(path)) == real_dest:
            return False
        return real_os_exists(path, *a, **k)

    monkeypatch.setattr(pathlib.Path, "exists", lies_path)
    monkeypatch.setattr(os.path, "exists", lies_os)
    created, rel = mod.scaffold_architecture_doc(str(repo))
    monkeypatch.undo()
    assert dest.read_bytes() == b"# written by someone else a moment ago\n"
    assert created is False and rel == DEFAULT_REL, (created, rel)


def test_existing_symlink_and_directory_at_path_are_kept(tmp_path):
    cases = {}
    repo = _repo(tmp_path, "dangling")
    (repo / "docs" / "architecture").mkdir(parents=True)
    os.symlink(str(tmp_path / "does-not-exist"), str(repo / DEFAULT_REL))
    cases["dangling"] = repo
    repo = _repo(tmp_path, "tolink")
    (repo / "docs" / "architecture").mkdir(parents=True)
    other = repo / "other.txt"
    other.write_bytes(b"other file\n")
    os.symlink(str(other), str(repo / DEFAULT_REL))
    cases["tolink"] = repo
    repo = _repo(tmp_path, "adir")
    (repo / DEFAULT_REL).mkdir(parents=True)
    (repo / DEFAULT_REL / "keep.txt").write_bytes(b"inside\n")
    cases["adir"] = repo
    for name, repo in cases.items():
        before = _snapshot(repo)
        rc, out, err = _scaffold(repo)
        assert rc == 0, "case %s: %s" % (name, out + err)
        assert "= " + DEFAULT_REL + " (exists, kept)" in out, "case %s: %s" % (name, out)
        assert _snapshot(repo) == before, "case %s: the path or its target changed" % name
    assert (cases["tolink"] / "other.txt").read_bytes() == b"other file\n"


# --------------------------------------------------------------------------------------- refusals

#: Each row maps to the ONE clause that turns it red alone (Task 8 control). Literals, never read
#: from product. Two rows are double-covered and exist for completeness, not for a clause control:
#: the well-formed ssh URL (resolver-url and the `://` check) and the malformed URL (resolver-invalid
#: and the `://` check).
REFUSED = (
    "https" + "://example.com/org/repo",     # double-covered: resolver-url AND the scaffold `://` check
    "ssh" + "://example.com/org/repo",       # double-covered: resolver-url AND the scaffold `://` check
    "git@example.com:org/repo.git",          # resolver-url alone (no `://`)
    "https" + "://bad host/x",               # double-covered: resolver-invalid AND the `://` check
    "/abs/place",                            # resolver-invalid alone
    "a/../b",                                # resolver-invalid alone
    "HTTPS" + "://x/y",                      # the scaffold `://` check alone (the resolver reads a folder)
    ".",                                     # the repo-root clause
    ".git",                                  # the .git clause
    ".git/x",                                # the .git clause
    "./.git/x",                              # the .git clause, read after normalising
    ".Git/x",                                # the .git clause on a case-insensitive filesystem
    ".sdlc",                                 # the .sdlc clause
    ".sdlc/x",                               # the .sdlc clause
    "docs\\x",                               # the backslash clause
)


def test_refused_values_write_nothing(tmp_path):
    repo = _repo(tmp_path)
    for row in REFUSED:
        _set_root(repo, row)
        before = _snapshot(repo)
        rc, out, err = _scaffold(repo)
        assert rc == 2, "row %r: expected exit 2, got %s: %s" % (row, rc, out + err)
        assert "architecture.system_root" in err, "row %r: refusal does not name the key: %s" % (row, err)
        assert "AGENTS.md" not in err and "Codex" not in err, "row %r: Codex remedy leaked: %s" % (row, err)
        assert _snapshot(repo) == before, "row %r: the refused run wrote something" % (row,)
        assert not (repo / ".git" / "architecture.md").exists(), "row %r: wrote inside .git" % (row,)
        assert not (repo / ".git" / "x" / "architecture.md").exists(), "row %r: wrote inside .git" % (row,)


def test_root_symlinked_into_dot_git_or_sdlc_is_refused(tmp_path):
    """The `.git`/`.sdlc` clause also reads the REAL path: a tracked-looking link that leads there."""
    repo = _repo(tmp_path)
    for target in (".git", ".sdlc"):
        link = repo / ("link-" + target.strip("."))
        os.symlink(str(repo / target), str(link))
        _set_root(repo, link.name)
        before = _snapshot(repo)
        rc, out, err = _scaffold(repo)
        assert rc == 2, "link to %s: expected exit 2, got %s: %s" % (target, rc, out + err)
        assert "architecture.system_root" in err, "link to %s: %s" % (target, err)
        assert _snapshot(repo) == before, "link to %s: wrote through the link" % target
        assert not (repo / target / "architecture.md").exists(), "link to %s: wrote inside it" % target


def test_custom_relative_root_and_file_in_the_way(tmp_path):
    repo = _repo(tmp_path)
    _set_root(repo, "docs/arch/sys")
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    assert (repo / "docs" / "arch" / "sys" / "architecture.md").is_file(), out
    assert not (repo / DEFAULT_REL).exists()
    code, text = _lint_clean(repo, "docs/arch/sys")
    assert code == 0, text
    blocked = _repo(tmp_path, "blocked")
    (blocked / "docs").mkdir()
    (blocked / "docs" / "archfile").write_bytes(b"i am a file\n")
    _set_root(blocked, "docs/archfile")
    before = _snapshot(blocked)
    rc, out, err = _scaffold(blocked)
    assert rc == 2, out + err
    assert _snapshot(blocked) == before
    assert (blocked / "docs" / "archfile").read_bytes() == b"i am a file\n"


def test_write_error_leaves_no_partial_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    mod = _load("sdlc_init_scaffold_werr", SDLC_INIT)
    assert getattr(mod, "scaffold_architecture_doc", None), "scaffold_architecture_doc missing"
    assert hasattr(mod, "_write_text"), "the _write_text write seam is missing"

    def boom(fh, text):
        raise OSError("simulated disk full")

    monkeypatch.setattr(mod, "_write_text", boom)
    with pytest.raises(OSError):
        mod.scaffold_architecture_doc(str(repo))
    dest = repo / DEFAULT_REL
    assert not os.path.lexists(str(dest)), "a partial file was left behind"
    monkeypatch.undo()
    created, rel = mod.scaffold_architecture_doc(str(repo))
    assert created is True and dest.is_file(), "a rerun after the failure must create the file"


# --------------------------------------------------------------------------------------- opt-in only

def test_plain_init_creates_no_architecture_file(tmp_path):
    # RED PRECONDITION: the template exists and the flag DOES create the document.
    assert TEMPLATE.is_file(), "template missing"
    flagged = _repo(tmp_path, "flagged")
    rc, out, err = _scaffold(flagged)
    assert rc == 0 and (flagged / DEFAULT_REL).is_file(), out + err
    plain = _repo(tmp_path, "plain")
    found = [p for p in plain.rglob("architecture.md") if ".git" not in p.parts]
    assert not found, "plain init wrote %s" % found
    assert not (plain / "docs").exists(), "plain init created docs/"


def test_template_under_templates_would_be_copied_by_scaffold(tmp_path):
    assert TEMPLATE.is_file(), "template missing"
    mod = _load("sdlc_init_scaffold_copy", SDLC_INIT)
    copy = tmp_path / "templates"
    import shutil
    shutil.copytree(str(TEMPLATES_DIR), str(copy))
    shutil.copyfile(str(TEMPLATE), str(copy / "architecture.md.tmpl"))
    mod.TEMPLATES = copy
    repo = tmp_path / "ctl"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_env())
    created, _ = mod.scaffold(str(repo))
    assert "architecture.md" in created, "the control: a template under templates/ IS copied by init"


# ------------------------------------------------------------------------------------------- output

def test_created_output_prints_both_clearing_commands(tmp_path):
    repo = _repo(tmp_path)
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    block = _block(out)
    assert "+ " + DEFAULT_REL in block, out
    assert "git add " + DEFAULT_REL in block, block
    assert "commit it through your normal goal or branch flow" in block, block
    assert "rm " + DEFAULT_REL in block, block
    assert "git commit" not in block, "must never point an operator at committing directly"


def test_kept_output_claims_no_creation(tmp_path):
    repo = _repo(tmp_path)
    rc, out, err = _scaffold(repo)
    assert rc == 0 and (repo / DEFAULT_REL).is_file(), out + err
    rc, out, err = _scaffold(repo)
    assert rc == 0, out + err
    block = _block(out)
    assert "= " + DEFAULT_REL + " (exists, kept)" in block, out
    assert "+ " + DEFAULT_REL not in out, out
    assert "git add" not in block and "rm " not in block, block


def test_typo_flag_is_ignored_by_sdlc_init(tmp_path):
    good = _repo(tmp_path, "good")
    rc, out, err = _scaffold(good)
    assert rc == 0 and (good / DEFAULT_REL).is_file(), out + err
    typo = _repo(tmp_path, "typo")
    rc, out, err = _init(typo, "--architecture")
    assert rc == 0, out + err
    assert "architecture.md" not in out
    assert not (typo / "docs").exists()


# ----------------------------------------------------------------------------------- wording and docs

def test_usage_names_the_flag():
    assert FLAG in _load("sdlc_init_usage", SDLC_INIT).USAGE
    assert FLAG in _load("init_flow_usage", INIT_FLOW).USAGE


def test_skill_wording_names_the_flag():
    for rel in ("skills/sigma-init/SKILL.md", "skills/sigma-vision/SKILL.md",
                "skills/sigma-vision/references/architecture.md"):
        assert FLAG in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_docs_gesture_copied_out_creates_the_file(tmp_path):
    text = DOC.read_text(encoding="utf-8")
    fenced = [l.strip() for l in text.splitlines() if l.strip().endswith(FLAG) and "sdlc_init.py" in l]
    assert fenced == ["python3 skills/sigma-init/scripts/sdlc_init.py . " + FLAG], fenced
    argv = shlex.split(fenced[0])
    assert argv[0] == "python3" and argv[1] == "skills/sigma-init/scripts/sdlc_init.py"
    repo = tmp_path / "docrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_env())
    p = subprocess.run([sys.executable, str(ROOT / argv[1]), *argv[2:]], cwd=str(repo),
                       capture_output=True, text=True, env=_env(), timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (repo / DEFAULT_REL).is_file(), p.stdout


def test_scaffold_section_precedes_lint_section_and_placement_paragraph_intact():
    text = DOC.read_text(encoding="utf-8")
    scaffold = text.find("\n## The scaffold gesture\n")
    lint = text.find("\n## The lint gesture\n")
    assert scaffold != -1, "section '## The scaffold gesture' missing"
    assert lint != -1 and scaffold < lint, (scaffold, lint)
    flat = " ".join(text.split())
    assert "The system document lives at `architecture.system_root` (default" in flat
    assert "Every flowchart under Building blocks is read as a container diagram, not only the first" in flat
