"""#456 — per-slice disposition files for the growth-store audit.

Every test runs the exact regeneration gesture that ``docs/launch/growth-audit.md``
documents (the line is parsed out of the doc, only the script path is made absolute)
inside a scratch repository, so a gesture that cannot fail cannot hide here.  Each test
asserts behaviour only the disposition-file reader produces, so it is red against the
old hard-coded constant.
"""

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "growth_audit.py"
DOC = ROOT / "docs" / "launch" / "growth-audit.md"
REVIEW_COPY = ROOT / "docs" / "launch" / "dispositions" / "review-copy.json"
REVIEW_PATTERN = ".sdlc/evidence/<goal>/rv*/wt"
LOG_PATTERN = ".sdlc/state/log/<goal>.jsonl"
SENTINEL = b"committed snapshot sentinel\n"


def _documented_gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable, str(SCRIPT)] + argv[2:]


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-C", str(repo),
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    (tmp_path / ".sdlc" / "state" / "seed").write_bytes(b"abc")
    (tmp_path / "docs" / "launch").mkdir(parents=True)
    (tmp_path / "docs" / "launch" / "growth-audit.json").write_bytes(SENTINEL)
    (tmp_path / "writer.py").write_text(
        "import shutil\n"
        "def persist(sdlc_dir, goal):\n"
        "    shutil.copytree('source', sdlc_dir / 'evidence' / goal / 'rv9' / 'wt')\n"
        "    (sdlc_dir / 'state' / 'log' / f'{goal}.jsonl').write_text('x')\n"
        "    (sdlc_dir / 'evidence' / goal).mkdir(parents=True)\n"
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def _entry(pattern, **overrides):
    entry = {"pattern": pattern, "issue": "#419", "decision": "capped by test",
             "pruner_or_cap": "test cap", "evidence": "tests/test_growth_disp.py"}
    entry.update(overrides)
    return entry


def _write(repo, name, payload):
    folder = repo / "docs" / "launch" / "dispositions"
    folder.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (folder / name).write_text(text, encoding="utf-8")


def _gesture(repo):
    return subprocess.run(_documented_gesture(), cwd=repo, capture_output=True, text=True, timeout=60)


def _snapshot(repo):
    return json.loads((repo / "docs" / "launch" / "growth-audit.json").read_text(encoding="utf-8"))


def _store(data, pattern):
    return next(row for row in data["store_measurements"] if row["pattern"] == pattern)


def _refused(repo, completed):
    assert completed.returncode != 0
    assert "REFUSED" in completed.stderr
    assert (repo / "docs" / "launch" / "growth-audit.json").read_bytes() == SENTINEL


def test_review_copy_row(repo):
    """The hard-coded review-copy entry now lives in the committed file, same row output."""
    assert REVIEW_COPY.is_file()
    entries = json.loads(REVIEW_COPY.read_text(encoding="utf-8"))
    assert [e["pattern"] for e in entries] == [REVIEW_PATTERN]
    assert entries[0].get("unscanned") is True
    _write(repo, "review-copy.json", entries)

    completed = _gesture(repo)

    assert completed.returncode == 0, completed.stderr
    data = _snapshot(repo)
    assert REVIEW_PATTERN not in data["b6_disposition"]["unresolved_patterns"]
    assert _store(data, REVIEW_PATTERN) == {
        "pattern": REVIEW_PATTERN,
        "growth_event": "writer invoked (writer.py:3)",
        "pruner_or_cap": "terminal review-copy lifecycle prune",
        "size_now_bytes": 0,
        "size_10x_bytes": 0,
        "size_100x_bytes": 0,
        "decision": "source-proven prune",
    }


def test_resolves_both(repo):
    """The pattern is data, so a sibling slice only adds its own file."""
    _write(repo, "log.json", [_entry(LOG_PATTERN, pruner_or_cap="keep newest 50", decision="capped")])

    completed = _gesture(repo)

    assert completed.returncode == 0, completed.stderr
    data = _snapshot(repo)
    unresolved = data["b6_disposition"]["unresolved_patterns"]
    assert LOG_PATTERN not in unresolved
    assert ".sdlc/evidence/<goal>/" in unresolved
    row = _store(data, LOG_PATTERN)
    assert (row["pruner_or_cap"], row["decision"]) == ("keep newest 50", "capped")
    assert _store(data, ".sdlc/evidence/<goal>/")["decision"] == "B6 #419 disposition required"


def test_no_file(repo):
    """The review-copy pattern is no longer special-cased in code."""
    completed = _gesture(repo)

    assert completed.returncode == 0, completed.stderr
    data = _snapshot(repo)
    assert REVIEW_PATTERN in data["b6_disposition"]["unresolved_patterns"]
    assert _store(data, REVIEW_PATTERN)["decision"] == "B6 #419 disposition required"


def test_unscanned(repo):
    """Only an entry that says unscanned: true may name a pattern the scan lacks."""
    pattern = ".sdlc/evidence/<goal>/rvX/wt"
    _write(repo, "log.json", [_entry(LOG_PATTERN)])
    _write(repo, "ext.json", [_entry(pattern, unscanned=True)])

    completed = _gesture(repo)

    assert completed.returncode == 0, completed.stderr
    assert "matched no scan row" in completed.stderr
    assert LOG_PATTERN not in _snapshot(repo)["b6_disposition"]["unresolved_patterns"]
    _write(repo, "ext.json", [_entry(pattern)])
    (repo / "docs" / "launch" / "growth-audit.json").write_bytes(SENTINEL)
    _refused(repo, _gesture(repo))


@pytest.mark.parametrize("pattern", [
    ".sdlc/nowhere/<goal>.json",
    ".sdlc/state/log/<goal>.jsonlx",
    "<codex-home>/state/",
], ids=["absent", "typo", "host"])
def test_unknown(repo, pattern):
    """The red/green control: this fails if the unknown-pattern guard is deleted."""
    _write(repo, "bogus.json", [_entry(pattern)])

    completed = _gesture(repo)

    _refused(repo, completed)
    assert pattern in completed.stderr


def test_duplicate(repo):
    """Two slices must not both claim a store; the refusal names the pattern."""
    _write(repo, "a.json", [_entry(LOG_PATTERN)])
    _write(repo, "b.json", [_entry(LOG_PATTERN, issue="#420")])
    completed = _gesture(repo)
    _refused(repo, completed)
    assert LOG_PATTERN in completed.stderr
    (repo / "docs" / "launch" / "dispositions" / "b.json").unlink()
    _write(repo, "a.json", [_entry(LOG_PATTERN), _entry(LOG_PATTERN)])
    _refused(repo, _gesture(repo))


@pytest.mark.parametrize("payload", [
    "{not json",
    "",
    '{"pattern": "x"}',
    "[1]",
    '[{"pattern": "' + LOG_PATTERN + '", "pattern": "' + LOG_PATTERN + '", "issue": "#1", '
    '"decision": "d", "pruner_or_cap": "p", "evidence": "e"}]',
    [{"pattern": LOG_PATTERN}],
    [dict(_entry(LOG_PATTERN), extra="x")],
    [_entry(LOG_PATTERN, decision="")],
    [_entry(LOG_PATTERN, evidence=None)],
    [_entry(LOG_PATTERN, issue="419")],
    [_entry(LOG_PATTERN, unscanned="yes")],
    [_entry(LOG_PATTERN, unscanned=False)],
], ids=["syntax", "empty", "object", "scalar", "dupkey", "missing", "extra", "blank", "null", "issue",
        "flagstr", "flagfalse"])
def test_malformed(repo, payload):
    """Parse errors, bad shape, empty or extra fields all exit non-zero."""
    _write(repo, "bad.json", payload)

    _refused(repo, _gesture(repo))


@pytest.mark.parametrize("name", ["slice.JSON", "slice.json.bak", "notes.txt"], ids=["upper", "bak", "txt"])
def test_stray_file(repo, name):
    """A misnamed disposition file is refused, not silently skipped."""
    _write(repo, name, [_entry(LOG_PATTERN)])

    _refused(repo, _gesture(repo))


def test_dotfile_ignored(repo):
    """Editor/OS litter such as .DS_Store is not a slice file."""
    _write(repo, ".DS_Store", "binary litter")
    _write(repo, "log.json", [_entry(LOG_PATTERN)])

    completed = _gesture(repo)

    assert completed.returncode == 0, completed.stderr
    assert LOG_PATTERN not in _snapshot(repo)["b6_disposition"]["unresolved_patterns"]


def test_no_b6_flag(repo):
    """A bad file cannot hide behind a run that only prints the scan rows."""
    _write(repo, "bogus.json", [_entry(".sdlc/nowhere/<goal>.json")])

    completed = subprocess.run([sys.executable, str(SCRIPT), str(repo)], capture_output=True,
                               text=True, timeout=60)

    assert completed.returncode != 0
    assert completed.stdout == ""
