"""#2066: the environment approval of the verify command is an operator act, and these keep it one.

- No agent-read prose names SIGMA_TRUSTED_VERIFY_SHA256: an agent must never be told to set it. Sigma cannot tell
  who exported it, so this is friction against a self-grant, not a control that prevents one (docs/threat-model.md).
- The operator gesture in docs/cloud-sessions.md, run exactly as written from a repository root that plants
  `json.py` and `hashlib.py`, prints the approval value and imports nothing from that repository.
- Only the verify path asks the new decision: a fourth caller would widen what the environment value grants.

DOCUMENTED INVOCATION: python -m pytest tests/test_verify_env_trust_prose.py
"""
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = "SIGMA_TRUSTED_VERIFY_SHA256"
SECTION = "## Cloud environment setup"
CALLERS = {"skills/sigma-loop/scripts/loop.py", "skills/sigma-doctor/scripts/doctor.py",
           "skills/sigma-init/scripts/init_flow.py"}


def _agent_prose():
    paths = [p for p in (ROOT / "skills").rglob("*") if p.suffix in (".md", ".tmpl") and p.is_file()]
    paths += sorted((ROOT / ".cursor" / "rules").glob("*.mdc"))
    paths += [ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]
    return paths


def _naming(paths):
    return sorted(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
                  for p in paths if NAME.casefold() in p.read_text(encoding="utf-8").casefold())


def test_no_agent_read_prose_names_the_environment_approval():
    assert _naming(_agent_prose()) == []


def test_control_the_scanner_sees_the_name(tmp_path):
    planted = tmp_path / "SKILL.md"
    planted.write_text("Run `export %s=...` before verify.\n" % NAME.lower())
    assert _naming([planted]) == [str(planted)]


def _sp():
    spec = importlib.util.spec_from_file_location("shell_policy", ROOT / "skills/sigma-loop/scripts/shell_policy.py")
    sp = importlib.util.module_from_spec(spec); spec.loader.exec_module(sp)
    return sp


def _documented_gesture():
    doc = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8")
    assert SECTION in doc, "docs/cloud-sessions.md has no %r section" % SECTION
    body = doc.split(SECTION, 1)[1].split("\n## ", 1)[0]
    lines = [l.strip() for block in re.findall(r"```[a-z]*\n(.*?)```", body, re.S) for l in block.splitlines()
             if "verify-approval" in l]
    assert len(lines) == 1, lines
    return lines[0]


def test_the_documented_gesture_prints_the_value_and_imports_nothing_from_the_repository(tmp_path):
    project = tmp_path / "project"
    (project / ".sdlc").mkdir(parents=True)
    command = "bash scripts/verify.sh"
    (project / ".sdlc" / "config.json").write_text(json.dumps({"verify": {"command": command}}))
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "remote", "add", "origin", "https://github.com/Owner/Repo.git"],
                   check=True)
    for module in ("json", "hashlib"):
        (project / (module + ".py")).write_text(
            "open(%r, 'w').write('planted %s ran')\n" % (str(tmp_path / ("ran-" + module)), module))
    argv = shlex.split(_documented_gesture().replace("<installed-sigma>", str(ROOT)))
    argv[0] = sys.executable if argv[0] == "python3" else argv[0]
    env = dict(os.environ, PYTHONPATH=":", HOME=str(tmp_path / "home"))
    done = subprocess.run(argv, cwd=project, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "%s=%s" % (NAME, _sp().approval_value("owner/repo", command)) in done.stdout
    assert "%s=%s" % (NAME, "f2f22f90a92ac5e37ff4125264cc48159860c871f49246314272632b9b320103") in done.stdout
    assert not list(tmp_path.glob("ran-*")), "the gesture imported a module planted in the repository"


def test_the_cloud_setup_section_says_operator_only_and_unmeasured():
    body = (ROOT / "docs" / "cloud-sessions.md").read_text(encoding="utf-8").split(SECTION, 1)[1].split("\n## ", 1)[0]
    assert "never an agent" in body and "UNMEASURED" in body and "repository-sourced" in body


def test_only_the_verify_path_asks_the_environment_approval():
    pattern = re.compile(r"\b(verify_decision|environment_approval)\(")
    callers = {str(p.relative_to(ROOT)) for p in (ROOT / "skills").rglob("*.py")
               if p.name != "shell_policy.py" and pattern.search(p.read_text(encoding="utf-8"))}
    assert callers == CALLERS
