"""#465: the worktree patterns leave `unresolved_patterns` once their disposition file exists.

Runs the regeneration gesture exactly as `docs/launch/growth-audit.md` documents it, in a scratch
copy of the working tree (so the committed snapshot is not rewritten). Red without
`docs/launch/dispositions/465.json`.
"""
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "launch" / "growth-audit.md"
FILE = ROOT / "docs" / "launch" / "dispositions" / "465.json"
PATTERNS = [".sdlc/work/<goal>", ".sdlc/work/<n>", ".sdlc/state/worktree-prune.lock",
            ".sdlc/state/worktree-prune-seen.json"]


def _gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def _scratch_copy(tmp_path):
    names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.split("\0")
    for name in filter(None, names):
        src = ROOT / name
        if src.is_file() and not src.is_symlink():
            dst = tmp_path / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "add", "-A"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q",
                    "-m", "scratch"], cwd=tmp_path, check=True, capture_output=True)


def test_file_covers_every_issue_pattern_once():
    assert FILE.is_file(), "docs/launch/dispositions/465.json must exist"
    entries = json.loads(FILE.read_text(encoding="utf-8"))
    assert sorted(e["pattern"] for e in entries) == sorted(PATTERNS)
    assert {e["issue"] for e in entries} == {"#465"}


def test_documented_gesture_resolves_the_rows(tmp_path):
    _scratch_copy(tmp_path)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    data = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text(encoding="utf-8"))
    unresolved = set(data["b6_disposition"]["unresolved_patterns"])
    rows = {r["pattern"]: r for r in data["store_measurements"]}
    for pattern in PATTERNS:
        full = pattern
        assert full in rows, full
        assert full not in unresolved, full
        assert rows[full]["decision"] and rows[full]["pruner_or_cap"]
