"""`tools/verify_public_repo.py` as a command: refusals, `--help`, no write site, GET-only argv (#397, Task 1c).

Children run `[sys.executable, TOOL, ...]` in an environment built from scratch with a failing fake `gh`
first on PATH (it logs `UNEXPECTED <argv>` and exits 97, as tests/test_leak_refs.py does).
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "verify_public_repo.py"
LEAK_REFS = ROOT / "tools" / "leak_refs.py"
WRITE_SURFACE = ROOT / "tools" / "readiness" / "write_surface.py"

SCHEMA = "sigma.public-tree-report/v1"
FORBIDDEN = ("-X", "--method", "-f", "-F", "--field", "--raw-field", "--input", "--jq")

_FAKE_GH = """#!/bin/sh
echo "UNEXPECTED $*" >> "$FAKE_GH_LOG"
exit 97
"""


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_COUNTER = [0]


def _tool():
    assert TOOL.exists(), "tools/verify_public_repo.py is missing"
    _COUNTER[0] += 1
    return _load(TOOL, "verify_public_repo_cli_under_test_%d" % _COUNTER[0])


def _report(**over):
    report = {"schema": SCHEMA, "verdict": "VERIFIED", "finalise": "done",
              "generated_at": "2026-10-02T00:00:00Z",
              "export": {"export_tree": "7e" * 20, "commit": "c0" * 20}}
    report.update(over)
    return report


def _cli(tmp_path, argv):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text(_FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {"PATH": "%s:%s:/usr/bin:/bin" % (bindir, os.path.dirname(sys.executable)),
           "HOME": str(home), "GH_CONFIG_DIR": str(tmp_path / "gh-config"), "FAKE_GH_LOG": str(log),
           "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    work = tmp_path / "cwd"
    work.mkdir(exist_ok=True)
    proc = subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in argv], cwd=str(work), env=env,
                          capture_output=True, text=True, timeout=120)
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, lines


def test_help_lists_every_flag(tmp_path):
    mod = _tool()
    proc, log = _cli(tmp_path, ["--help"])
    assert proc.returncode == 0, proc.stderr
    for flag in ("--repo", "--report", "--expect-visibility", "--branch", "--legs"):
        assert flag in proc.stdout, flag
    assert "private" in proc.stdout and "public" in proc.stdout
    assert log == []
    assert mod.CHECKS[0] == "visibility"


def test_missing_expect_visibility_is_an_argument_error(tmp_path):
    mod = _tool()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    proc, log = _cli(tmp_path, ["--repo", "acme/demo", "--report", report])
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "--expect-visibility" in proc.stderr
    assert "REFUSED" not in proc.stderr, "an argparse error is argparse's own message"
    assert log == []
    assert callable(mod.main)


def test_unreadable_report_refuses(tmp_path):
    mod = _tool()
    proc, log = _cli(tmp_path, ["--repo", "acme/demo", "--report", tmp_path / "absent.json",
                                "--expect-visibility", "private"])
    assert proc.returncode == 2
    assert proc.stdout == ""
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1, proc.stderr
    assert lines[0].startswith("verify_public_repo: REFUSED [report-unreadable] "), lines[0]
    assert log == []
    assert callable(mod.main)


def test_not_verified_report_refuses_with_empty_stdout(tmp_path):
    mod = _tool()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(verdict="NOT-VERIFIED", finalise="pending")), encoding="utf-8")
    proc, log = _cli(tmp_path, ["--repo", "acme/demo", "--report", report, "--expect-visibility", "private"])
    assert proc.returncode == 2
    assert proc.stdout == ""
    lines = proc.stderr.strip().splitlines()
    assert len(lines) == 1, proc.stderr
    assert lines[0].startswith("verify_public_repo: REFUSED [report-not-verified] "), lines[0]
    assert log == [], "a refusal before any gh call must not call gh"
    assert callable(mod.main)


def test_gh_failure_through_the_real_runner_refuses_and_the_argv_is_an_api_get(tmp_path):
    mod = _tool()
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report()), encoding="utf-8")
    proc, log = _cli(tmp_path, ["--repo", "acme/demo", "--report", report, "--expect-visibility", "private"])
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr.startswith("verify_public_repo: REFUSED [gh-failed]"), proc.stderr
    assert log, "the real runner never reached the fake gh"
    for line in log:
        assert line.startswith("UNEXPECTED api repos/acme/demo"), line
        assert not any((" " + flag + " ") in (line + " ") for flag in FORBIDDEN), line
    assert callable(mod.main)


def test_verifier_has_no_write_site():
    mod = _tool()
    ws = _load(WRITE_SURFACE, "write_surface_for_verifier_test")
    assert ws.scan_paths(ROOT, [TOOL]) == []
    assert callable(mod.main)


def test_verbatim_copies_still_equal_leak_refs():
    mod = _tool()
    lr = _load(LEAK_REFS, "leak_refs_for_verifier_test")
    assert mod._GIT_SCRUB == lr._GIT_SCRUB
    assert mod._NOT_A_REPO == lr._NOT_A_REPO
    assert mod._REPO_RE.pattern == lr._REPO_RE.pattern
    assert inspect.getsource(mod._real_run) == inspect.getsource(lr._real_run)


def _gh_literals(tree):
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "gh":
                found.append(node)
    return found


def test_gh_argv_literals_are_api_get_only():
    mod = _tool()
    tree = ast.parse(TOOL.read_text(encoding="utf-8"), filename=str(TOOL))
    literals = _gh_literals(tree)
    assert literals, "the verifier holds no gh literal at all"
    for node in literals:
        consts = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        assert len(node.elts) >= 3, ast.dump(node)
        second = node.elts[1]
        assert isinstance(second, ast.Constant) and second.value == "api", ast.dump(node)
        assert not any(c in FORBIDDEN or c.startswith("--method") for c in consts), consts
    assert callable(mod.main)
