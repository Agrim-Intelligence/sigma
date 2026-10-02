# SPDX-License-Identifier: MIT
"""`tools/handover_check.py sequence` and the CLI gestures (issue 397).

`sequence` is pure text: it must read nothing and call nothing, so its in-process tests inject a
`run` that fails on ANY call. The CLI tests run `[sys.executable, TOOL, ...]` in an environment
built from scratch: a failing fake `gh` first on PATH (it logs `UNEXPECTED` and exits 97), HOME and
GH_CONFIG_DIR in tmp, cwd in tmp. Slugs are synthetic. Every test body starts with `mod = _tool()`.
"""
import importlib.util
import itertools
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "handover_check.py"
OLD = "acme-old/widget"
NEW = "acme-old/widget-private"
OLD_URL = "https://github.com/acme-old/widget.git"
NEW_URL = "https://github.com/acme-old/widget-private.git"
THROWAWAY = "acme-old/widget-throwaway"
_N = itertools.count(1)


def _load(path, name):
    assert path.exists(), "%s is missing" % path.relative_to(ROOT)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tool():
    assert TOOL.exists(), "tools/handover_check.py is missing"
    return _load(TOOL, "handover_check_seq_under_test_%d" % next(_N))


def _no_run(args, input_text=None, timeout=60):
    raise AssertionError("`sequence` must read nothing and call nothing, but ran %r" % (args,))


def _sequence(mod, capsys, *argv):
    try:
        rc = mod.main(["handover_check.py", "sequence"] + list(argv), run=_no_run)
    except SystemExit as exc:
        rc = exc.code
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def _blocks(out):
    """-> (rehearsal_text, sequence_text) split at the line that opens each block."""
    lines = out.splitlines()
    r = [i for i, l in enumerate(lines) if re.match(r"^\s*REHEARSAL\b", l)]
    s = [i for i, l in enumerate(lines) if re.match(r"^\s*SEQUENCE\b", l)]
    assert r and s, out
    assert r[0] < s[0], "the REHEARSAL block must come before the SEQUENCE block"
    return "\n".join(lines[r[0]:s[0]]), "\n".join(lines[s[0]:])


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    for name in ("GH_REPO", "CLAUDE_CONFIG_DIR", "GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home").mkdir()
    (tmp_path / "cwd").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(tmp_path / "cwd")


# ------------------------------------------------------------------------------ sequence

def test_sequence_orders_the_owner_steps(capsys):
    mod = _tool()
    rc, out, err = _sequence(mod, capsys, "--old", OLD, "--new", NEW)
    assert rc == 0 and err == "", (rc, out, err)
    _rehearsal, seq = _blocks(out)
    needles = ["touch .sdlc/state/watch.stop",
               "check --old " + OLD,
               "gh api repos/%s --jq '.id, .private'" % OLD,
               "gh repo rename widget-private -R %s --yes" % OLD,
               "remote set-url origin " + NEW_URL,
               "gh api repos/%s --jq '.id, .private'" % NEW,
               "gh repo create %s --public" % OLD,
               'git -C "$OUT" push https://github.com/acme-old/widget.git main',
               "tools/verify_public_repo.py --repo %s --report \"$REPORT\" --expect-visibility public" % OLD,
               "rm .sdlc/state/watch.stop"]
    positions = []
    for needle in needles:
        assert needle in seq, "the SEQUENCE block lacks %r" % needle
        positions.append(seq.index(needle))
    assert positions == sorted(positions) and len(set(positions)) == len(positions), list(zip(needles, positions))
    assert seq.count("check --old") >= 3                       # steps 2, 6 and 8
    assert "--repo-id" in seq[positions[3]:] and "old-name-taken" in seq
    assert "--scan-root ~/.sigma-ops" in seq
    assert "ps -axo pid,etime,command | grep -E 'watch_daemon|loop\\.py'" in seq
    numbers = [int(m.group(1)) for l in seq.splitlines() for m in [re.match(r"^\s*\(?(\d+)[.)]", l)] if m]
    assert numbers == list(range(1, 10)), numbers
    for placeholder in ("<old>", "<new>", "<newname>"):
        assert placeholder not in out, placeholder
    assert "ONLY THEN" in seq


def test_rehearsal_block_comes_first_and_names_the_throwaway(capsys):
    mod = _tool()
    rc, out, err = _sequence(mod, capsys, "--old", OLD, "--new", NEW, "--throwaway", THROWAWAY)
    assert rc == 0 and err == "", (rc, out, err)
    rehearsal, seq = _blocks(out)
    assert THROWAWAY in rehearsal and (THROWAWAY + "-renamed") in rehearsal, rehearsal
    assert "--expect-visibility private" in rehearsal
    assert "docs/name-handover.md" in rehearsal
    for tag in ("R1", "R2", "R3", "R4"):
        assert tag in rehearsal, tag
    assert THROWAWAY not in seq, "the real sequence must not name the throwaway"
    # the block exists with no --throwaway too
    rc, out, err = _sequence(mod, capsys, "--old", OLD, "--new", NEW)
    assert rc == 0, (out, err)
    assert "R1" in _blocks(out)[0]


@pytest.mark.parametrize("case", ["owner-differs", "same-name", "bad-slug", "windows", "new-missing"])
def test_sequence_refuses_bad_names(case, capsys, monkeypatch):
    mod = _tool()
    argv = ["--old", OLD, "--new", NEW]
    if case == "owner-differs":
        argv[3] = "other-owner/widget-private"
    elif case == "same-name":
        argv[3] = OLD
    elif case == "bad-slug":
        argv[1] = "not a slug"
    elif case == "windows":
        monkeypatch.setattr(mod, "_platform_is_windows", lambda: True)
    else:
        argv = argv[:2]
    rc, out, err = _sequence(mod, capsys, *argv)
    assert rc == 2 and out == "", (rc, out, err)
    if case == "new-missing":
        assert "--new" in err, err
    else:
        assert err.startswith("handover_check: REFUSED [%s] " % case), err
        assert len(err.strip().splitlines()) == 1, err


def test_sequence_names_ownership_and_never_a_stop_signal(capsys):
    mod = _tool()
    rc, out, err = _sequence(mod, capsys, "--old", OLD, "--new", NEW, "--throwaway", THROWAWAY)
    assert rc == 0, (out, err)
    assert "#359" in out and "#397" in out
    last = [l for l in out.splitlines() if l.strip()][-1]
    assert last == "release checklist, pin and rollback: #359; this checker and sequence: #397", last
    tokens = ["SIG" + "STOP", "SIG" + "CONT", "kill " + "-ST" + "OP", "kill " + "-CO" + "NT"]
    low = out.lower()
    for token in tokens:
        assert token.lower() not in low, token
    assert "never pause a process with a stop signal" in out


# ------------------------------------------------------------------------------ CLI

def _cli_env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\necho UNEXPECTED \"$@\" >> '%s'\nexit 97\n" % log, encoding="utf-8")
    gh.chmod(0o755)
    env = {"PATH": "%s:%s:/usr/bin:/bin" % (bindir, os.path.dirname(sys.executable)),
           "HOME": str(tmp_path / "home"), "GH_CONFIG_DIR": str(tmp_path / "gh-config"),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
           "GIT_AUTHOR_EMAIL": "test@example.invalid", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
    return env, log


def _cli(tmp_path, argv):
    env, log = _cli_env(tmp_path)
    proc = subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in argv],
                          cwd=str(tmp_path / "cwd"), env=env, capture_output=True, text=True,
                          timeout=120)
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "UNEXPECTED" not in text, text
    return proc


def _git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd)] + [str(a) for a in args], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, (args, proc.stdout, proc.stderr)


def _clone(path, origin):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    _git(path, "remote", "add", "origin", origin)
    return path.resolve()


def test_cli_help_lists_verbs_and_flags(tmp_path):
    mod = _tool()
    top = _cli(tmp_path, ["--help"])
    assert top.returncode == 0 and top.stderr == "", top
    assert "check" in top.stdout and "sequence" in top.stdout
    check = _cli(tmp_path, ["check", "--help"])
    assert check.returncode == 0, check
    for flag in ("--old", "--new", "--repo-id", "--clone", "--scan-root", "--max-depth",
                 "--max-repos", "--claude-config", "--offline", "--json"):
        assert flag in check.stdout, flag
    seq = _cli(tmp_path, ["sequence", "--help"])
    assert seq.returncode == 0, seq
    for flag in ("--old", "--new", "--throwaway"):
        assert flag in seq.stdout, flag
    assert hasattr(mod, "main")


def test_cli_control_gesture_on_temporary_clones(tmp_path):
    mod = _tool()
    a = _clone(tmp_path / "clones" / "a", OLD_URL)
    b = _clone(tmp_path / "clones" / "b", NEW_URL)
    (tmp_path / "claude").mkdir()
    gesture = ["check", "--old", OLD, "--new", NEW, "--clone", a, "--clone", b, "--offline",
               "--claude-config", tmp_path / "claude"]
    first = _cli(tmp_path, gesture)
    assert first.returncode == 1, (first.stdout, first.stderr)
    lines = first.stdout.splitlines()
    assert lines[0] == "handover_check: old=%s new=%s repo-id=- rest=offline" % (OLD, NEW), lines[0]
    blocks = [l for l in lines if l.startswith("BLOCK remote-url ")]
    assert len(blocks) == 1 and str(a) in blocks[0] and str(b) not in blocks[0], first.stdout
    assert any(l.startswith("INFO  remote-url ") and str(b) in l for l in lines), first.stdout
    assert lines[-1].startswith("handover_check: 1 blocking, "), lines[-1]
    _git(a, "remote", "set-url", "origin", NEW_URL)
    second = _cli(tmp_path, gesture)
    assert second.returncode == 0, (second.stdout, second.stderr)
    assert not [l for l in second.stdout.splitlines() if l.startswith("BLOCK ")], second.stdout
    assert second.stdout.splitlines()[-1].startswith("handover_check: 0 blocking, ")
    assert hasattr(mod, "main")
