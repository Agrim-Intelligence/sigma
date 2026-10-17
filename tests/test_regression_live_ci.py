"""Compensating controls around the live entrypoint (#810 slice 4): a gh-write scan over `evals/regression`, the
README gestures, and the Windows workflow step.

THE GH-WRITE SCAN IS BOUNDED, NOT COMPLETE. `gh_write_findings` is an AST scan of `*.py` under
`evals/regression` for the literal shapes a GitHub write takes. What it CANNOT see: an obfuscated
`"g" + "h"` argv, a call built with `getattr`, a command assembled from a variable, a shell script under
`evals/regression`, a non-`.py` workflow file, and anything outside `evals/regression`. It is a ratchet against
the obvious shapes (beside `tests/test_no_direct_gh.py`), not a proof that nothing writes to GitHub. Where the
scan says "no `subprocess`", it applies to the two spend-path files only (`live.py`, `spend_ledger.py`):
`control.py`, `matrix.py`, `mutators.py`, `record.py` and `budget.py` legitimately run `git`.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GH_ARGV = re.compile(r"\bgh\s+(issue|pr|project|label|api|release|repo|workflow|run)\b")
TOKEN_NAMES = ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT")
SPEND_PATH_FILES = {"live.py", "spend_ledger.py"}


def _docstrings(tree):
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def scan_source(src, filename="x.py"):
    tree = ast.parse(src)
    docs = _docstrings(tree)
    spend_path = pathlib.PurePosixPath(filename).name in SPEND_PATH_FILES
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            text = node.value
            if GH_ARGV.search(text):
                found.append("gh command text")
            if "api.github.com" in text:
                found.append("api.github.com")
            if any(re.search(r"\b%s\b" % n, text) for n in TOKEN_NAMES):
                found.append("token name")
        if isinstance(node, (ast.List, ast.Tuple)):
            if any(isinstance(e, ast.Constant) and e.value == "gh" for e in node.elts):
                found.append('"gh" argv element')
        if isinstance(node, ast.Call):
            if any(isinstance(a, ast.Constant) and a.value == "gh" for a in node.args):
                found.append('"gh" call argument')
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "os" \
                    and f.attr in {"system", "popen"}:
                found.append("os." + f.attr)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
            if any("gh_api" in n for n in names):
                found.append("gh_api import")
            if spend_path and any(n.split(".")[0] == "subprocess" for n in names):
                found.append("subprocess import")
        if isinstance(node, ast.Name) and "gh_api" in node.id:
            found.append("gh_api name")
        if isinstance(node, ast.Attribute):
            if "gh_api" in node.attr:
                found.append("gh_api attribute")
            if spend_path and isinstance(node.value, ast.Name) and node.value.id == "subprocess":
                found.append("subprocess use")
    return found


def gh_write_findings(root):
    out = []
    for path in sorted((pathlib.Path(root) / "evals" / "regression").rglob("*.py")):
        for hit in scan_source(path.read_text(encoding="utf-8"), path.name):
            out.append("%s: %s" % (path.relative_to(root).as_posix(), hit))
    return out


PLANTED = [
    'import subprocess\nsubprocess.run(["gh","issue","comment","1"])',
    'import os\nos.system("gh pr create")',
    'def f(args):\n    run(["gh", *args])',
    'from skills.sigma_loop.scripts import gh_api',
    'gh_api.comment_pr(1, "x")',
    'url = "https://api.github.com/repos/x/y/issues"',
    'import os\nos.environ["GITHUB_TOKEN"]',
    'import os\nos.popen("true")',
    'import os\nos.system("true")',
    'x = "gh pr create"',                                   # only the command-text clause sees a bare string
    'x = ["gh", "auth", "status"]',                         # only the argv-element clause sees this (no write verb)
]


@pytest.mark.parametrize("src", PLANTED, ids=[s.splitlines()[-1][:40] for s in PLANTED])
def test_gh_scan_flags_each_planted_shape(tmp_path, src):
    reg = tmp_path / "evals" / "regression"
    reg.mkdir(parents=True)
    (reg / "x.py").write_text(src + "\n")
    assert gh_write_findings(tmp_path) != [], src


def test_gh_scan_permits_what_the_regression_files_legitimately_do(tmp_path):
    reg = tmp_path / "evals" / "regression"
    reg.mkdir(parents=True)
    (reg / "x.py").write_text(
        '"""Mentions gh issue comment in a docstring only."""\n'
        "import subprocess, os\n"
        'subprocess.run(["git", "status"])\n'
        'a = os.environ.get("CI"), os.environ.get("GITHUB_ACTIONS"), os.environ.get("GITHUB_EVENT_NAME")\n'
        'b = os.environ.get("GITHUB_SHA")\n')
    assert gh_write_findings(tmp_path) == []
    (reg / "live.py").write_text("import subprocess\n")                         # spend-path file: no subprocess
    assert gh_write_findings(tmp_path) != []


def test_gh_scan_documents_what_it_cannot_see():
    """A docstring-present test, not a proof: the claim is bounded in text."""
    doc = " ".join(__doc__.split())
    for blind in ('obfuscated `"g" + "h"`', "`getattr`", "a shell script under `evals/regression`",
                  "a non-`.py` workflow file"):
        assert blind in doc, blind


def test_no_gh_write_in_evals_regression():
    assert gh_write_findings(ROOT) == []


# --------------------------------------------------------------------------- Task 9: README gestures

import importlib.util  # noqa: E402
import os  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

README = ROOT / "evals" / "regression" / "README.md"
LIVE = ROOT / "evals" / "regression" / "live.py"
SCRUB = ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py"
HEADING = "## Live entrypoint"


def _live_module():
    spec = importlib.util.spec_from_file_location("regression_live_for_ci_tests", LIVE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["regression_live_for_ci_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


def readme_section():
    text = README.read_text(encoding="utf-8")
    assert HEADING in text, "the README has no '%s' section" % HEADING
    rest = text.split(HEADING, 1)[1]
    nxt = re.search(r"\n## ", rest)
    return rest[:nxt.start()] if nxt else rest


def documented_gestures(section):
    """Fenced command lines of the section that run live.py, with their trailing `# REFUSED [code]` comment."""
    out = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", section, flags=re.S):
        for line in block.splitlines():
            if "evals/regression/live.py" in line:
                cmd, _, comment = line.partition("  #")
                m = re.search(r"REFUSED \[([a-z-]+)\]", comment)
                out.append((cmd.strip(), m.group(1) if m else None))
    return out


def run_documented(cmd, tmp_path, held):
    """Run a README command line exactly as written: `env -i`, NAME=value words, "$VAR" expansion from `held`,
    python3 and the script path rebound to a temp tree copy so rows land in temp."""
    tree = tmp_path / "tree"
    (tree / "evals" / "regression").mkdir(parents=True)
    (tree / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    shutil.copy(str(LIVE), str(tree / "evals" / "regression" / "live.py"))
    shutil.copy(str(SCRUB), str(tree / "skills" / "sigma-loop" / "scripts" / "scrub.py"))
    words = shlex.split(cmd)
    env = {}
    if words[:2] == ["env", "-i"]:
        words = words[2:]
    else:
        env.update({"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        name, _, value = words.pop(0).partition("=")
        env[name] = re.sub(r"\$(\w+)", lambda m: held.get(m.group(1), ""), value)
    assert words[0] == "python3", words
    words[0] = sys.executable
    words = [str(tree / "evals" / "regression" / "live.py") if w == "evals/regression/live.py" else w for w in words]
    return subprocess.run(words, env=env, cwd=str(tmp_path), capture_output=True, text=True)


def test_readme_documents_the_live_gestures_and_they_refuse(tmp_path):
    section = readme_section()
    gestures = documented_gestures(section)
    refusals = [(c, code) for c, code in gestures if code]
    assert {code for _, code in refusals} >= {"no-credential", "no-cap"}, gestures
    held = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
            "CLAUDE_CODE_OAUTH_TOKEN": "zq9-" + os.urandom(12).hex()}
    for n, (cmd, code) in enumerate(refusals):
        workdir = tmp_path / ("g%d" % n)
        workdir.mkdir()
        r = run_documented(cmd, workdir, held)
        assert r.returncode == 2, (cmd, r.stderr)
        assert r.stdout == "", cmd
        assert "REFUSED [%s]" % code in r.stderr, (cmd, r.stderr)


def test_readme_section_names_every_code_and_the_limits():
    live = _live_module()
    section = readme_section()
    for code in list(live.CODES) + list(live.RUN_CODES):
        assert code in section, "README does not name the code %s" % code
    flat = " ".join(section.split())
    for word in ("unmeasured", "Windows", "settle_orphan", "per machine", "RAN", "RUN-FAILED", "never GREEN",
                 "Concurrent runs are allowed and cap-clamped", "~/.sigma/eval/ledger", "parent_marker", "scratch_root", "ledger_id", "spent",
                 "exit_code", "timed_out", "cost_basis", "/proc", "--commit", "--cell", "absolute and executable"):
        assert word in flat, "README does not mention %r" % word
    assert "one run at a time" not in flat.lower()                        # R2: no run lock exists, so no such claim


def test_no_one_run_at_a_time_claim_anywhere_in_the_live_surface():
    for path in (LIVE, README, ROOT / "evals" / "regression" / "spend_ledger.py"):
        assert "one run at a time" not in " ".join(path.read_text(encoding="utf-8").split()).lower(), path.name


# --------------------------------------------------------------------------- Task 10: Windows step

WORKFLOW = ROOT / ".github" / "workflows" / "windows.yml"
STEP_NAME = "Live regression entrypoint refuses on Windows (no model, no network)"


def windows_step():
    """(env mapping, run text) of the live-entrypoint step, read as plain text (no YAML dependency)."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if STEP_NAME in l)
    env, run, mode = {}, [], None
    for l in lines[start + 1:]:
        if re.match(r"\s{6}- ", l):                      # the next step
            break
        if re.match(r"\s{8}env:\s*$", l):
            mode = "env"
        elif re.match(r"\s{8}run:\s*\|\s*$", l):
            mode = "run"
        elif mode == "env" and re.match(r"\s{10}\w+:", l):
            k, _, v = l.strip().partition(":")
            env[k] = v.strip().strip('"')
        elif mode == "run" and l.startswith(" " * 10):
            run.append(l[10:])
        elif l.strip() and not l.startswith(" " * 10):
            mode = None if not l.startswith(" " * 8) else mode
    return env, "\n".join(run) + "\n"


def test_windows_workflow_runs_the_live_refusal_step():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^on:\n  workflow_dispatch:\n", text, flags=re.M)             # manual dispatch only
    assert "pull_request" not in text and "push:" not in text and "schedule:" not in text
    assert "python -m pytest tests/test_windows_real.py -v --no-header" in text    # the existing step is untouched
    env, run = windows_step()
    assert "python evals/regression/live.py" in run
    assert 'test "$rc1" -eq 2' in run and 'test "$rc2" -eq 2' in run
    assert "grep -q 'REFUSED \\[no-credential\\]' err1.txt" in run
    assert "grep -q 'REFUSED \\[unsupported-platform\\]' err2.txt" in run
    assert "test ! -s out1.txt" in run and "test ! -s out2.txt" in run
    assert env["SIGMA_REGRESSION_LIVE"] == "1" and env["SIGMA_REGRESSION_CAP_USD"] == "1"
    assert env["GITHUB_EVENT_NAME"] == "workflow_dispatch"
    assert env["ANTHROPIC_API_KEY"] == "windows-probe"       # visibly a fake, not a secret
    assert "secrets." not in text


def _run_step(tmp_path, script):
    tree = tmp_path / "tree"
    (tree / "evals" / "regression").mkdir(parents=True)
    (tree / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    shutil.copy(str(LIVE), str(tree / "evals" / "regression" / "live.py"))
    shutil.copy(str(SCRUB), str(tree / "skills" / "sigma-loop" / "scripts" / "scrub.py"))
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    if not (shim / "python").exists():
        (shim / "python").symlink_to(sys.executable)                  # `python` as on the Windows runner
    env_vars, _ = windows_step()
    env = {"PATH": "%s:/usr/bin:/bin" % shim, "HOME": str(tmp_path)}
    env.update(env_vars)
    proc = subprocess.run(["bash", "-c", script], cwd=str(tree), env=env, capture_output=True, text=True)
    return proc, tree


def test_windows_step_script_can_fail_locally(tmp_path):
    """The step's 'seen red': on this POSIX host the platform leg's expectation is false (the ladder passes and ends
    `no-run-requested`), so the script must exit non-zero, FOR THAT REASON (not a missing interpreter), while the
    first leg alone exits 0. The green leg exists only on a real Windows runner and is UNMEASURED here."""
    _, run = windows_step()
    proc, tree = _run_step(tmp_path / "full", run)
    assert proc.returncode == 1, (proc.returncode, proc.stderr)
    assert "REFUSED [no-run-requested]" in (tree / "err2.txt").read_text()       # the platform leg's own output
    assert "unsupported-platform" not in (tree / "err2.txt").read_text()
    assert "REFUSED [no-credential]" in (tree / "err1.txt").read_text()          # the first leg did its job
    first_leg = "\n".join(l for l in run.splitlines() if "2.txt" not in l and "rc2" not in l) + "\n"
    proc1, _ = _run_step(tmp_path / "first", first_leg)
    assert proc1.returncode == 0, proc1.stderr
