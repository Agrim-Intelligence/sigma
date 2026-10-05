"""#460 -- B6: the Slack listener and supervisor logs.

`docs/launch/dispositions/460.json` claims two logs are capped by `logroll`, two supervisor files
are bounded by truncation and one file belongs to the host service manager.  A claim in a JSON file
proves nothing, so each node checks the claim against the real code or the real scan.  The audit
node runs the gesture copied out of `docs/launch/growth-audit.md` in a scratch copy of the
repository (the gesture overwrites the committed snapshot).
"""
import importlib.util
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "sigma-loop" / "scripts"
FILE = ROOT / "docs" / "launch" / "dispositions" / "460.json"
DOC = ROOT / "docs" / "launch" / "growth-audit.md"

CAPPED = [".sdlc/state/slack-commands.log", ".sdlc/state/supervisor.log"]
TRUNCATED = [".sdlc/state/supervisor.run.out", ".sdlc/state/supervisor.tail"]
HOST_OWNED = [".sdlc/state/slack-commands.launchd.log</string>"]
ISSUE_PATTERNS = set(CAPPED + TRUNCATED + HOST_OWNED)


def _entries():
    """{} while the file is absent, so every node below fails by assertion rather than by error."""
    if not FILE.exists():
        return {}
    return {e["pattern"]: e for e in json.loads(FILE.read_text(encoding="utf-8"))}


def _claims(patterns, decision):
    entries = _entries()
    for pattern in patterns:
        assert entries.get(pattern, {}).get("decision", "").startswith(decision), pattern


def _mod(name):
    path = S / f"{name}.py"
    assert path.exists(), path.name
    spec = importlib.util.spec_from_file_location(name + "_b6", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_covers():
    entries = _entries()
    assert set(entries) == ISSUE_PATTERNS
    assert not any("unscanned" in e for e in entries.values())


def test_named():
    """The rotation a capped entry names must still exist and be what the writers call."""
    _claims(CAPPED, "capped")
    logroll = _mod("logroll")
    for pattern in CAPPED:
        cap = _entries()[pattern]["pruner_or_cap"]
        assert cap.startswith("logroll.rotate:"), cap
        assert callable(logroll.rotate) and callable(logroll.append)
    slack = (S / "slack_commands_listen.py").read_text(encoding="utf-8")
    daemon = (S / "supervise_daemon.py").read_text(encoding="utf-8")
    assert "logroll.append(" in slack
    assert "logroll.append(" in daemon


def test_truncated(tmp_path, monkeypatch, capsys):
    """run.out is opened "wb" and the tail is rewritten whole every run, so neither accumulates."""
    _claims(TRUNCATED, "bounded by truncation")
    sd = _mod("supervise_daemon")
    script = tmp_path / "fake_session.py"
    script.write_text('for i in range(200):\n    print("turn", i)\n')
    monkeypatch.setenv("SIGMA_CLAUDE_CMD", "%s %s" % (sys.executable, script))
    monkeypatch.setenv("SIGMA_SUPERVISE_SLEEP_SCALE", "0")
    monkeypatch.setenv("SIGMA_SUPERVISE_MAX_RUNS", "4")
    sd.main(["supervise_daemon.py", str(tmp_path / ".sdlc")])
    state = tmp_path / ".sdlc" / "state"
    one = len("".join("turn %d\n" % i for i in range(200)).encode())
    assert (state / "supervisor.run.out").stat().st_size == one
    assert len((state / "supervisor.tail").read_text().splitlines()) == 50


def test_host_owned():
    """No Sigma source writes the launchd log, so Sigma can neither rotate nor prune it."""
    _claims(HOST_OWNED, "intentionally unbounded")
    assert "launchd writes it" in _entries()[HOST_OWNED[0]]["pruner_or_cap"]
    for path in list((ROOT / "skills").rglob("*.py")) + list((ROOT / "hooks").rglob("*.py")):
        assert "slack-commands.launchd.log" not in path.read_text(encoding="utf-8", errors="replace"), path


def _gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def test_audit(tmp_path):
    for name in ("skills", "hooks", "tools", "contract"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, tmp_path / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "launch", tmp_path / "docs" / "launch")
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-C", str(tmp_path)]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(git + args, check=True, capture_output=True)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text())
    unresolved = set(result["b6_disposition"]["unresolved_patterns"])
    assert not unresolved & ISSUE_PATTERNS
    rows = {r["pattern"]: r for r in result["store_measurements"]}
    for pattern in ISSUE_PATTERNS:
        assert rows[pattern]["pruner_or_cap"] == _entries()[pattern]["pruner_or_cap"]
        assert rows[pattern]["decision"] == _entries()[pattern]["decision"]
