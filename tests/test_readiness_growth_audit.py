"""#349 — deterministic growing-store audit controls.

Each assertion names a scanner regression that would otherwise hide a durable
store: dropping code writers, treating skill prose as invisible, losing the
normalised store key, or walking an entire home directory during measurement.
"""

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "readiness" / "growth_audit.py"


def _mod():
    spec = importlib.util.spec_from_file_location("growth_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scan_reports_code_and_skill_prose_writers_as_sorted_normalized_rows(tmp_path):
    """Removing either scanner branch must make this inventory assertion fail."""
    source = tmp_path / "writer.py"
    source.write_text(
        "from pathlib import Path\n"
        "def persist(sdlc_dir, goal):\n"
        "    with open(sdlc_dir / 'state' / 'log' / f'{goal}.jsonl', 'a') as stream:\n"
        "        stream.write('event\\n')\n"
        "    (sdlc_dir / 'evidence' / goal).mkdir(parents=True)\n"
    )
    skill = tmp_path / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Save the review copy under `.sdlc/evidence/7/rv7/wt`.\n")

    rows = _mod().scan(tmp_path)

    assert rows == [
        {"pattern": ".sdlc/evidence/<goal>/", "source": "code", "writer": "writer.py:5"},
        {"pattern": ".sdlc/evidence/<goal>/rv*/wt", "source": "skill-prose", "writer": "skills/review/SKILL.md:1"},
        {"pattern": ".sdlc/state/log/<goal>.jsonl", "source": "code", "writer": "writer.py:3"},
    ]


def test_scan_keeps_named_host_writers_inventory_only(tmp_path):
    """Replacing explicit host-root recognition must not erase this host row."""
    (tmp_path / "host_writer.py").write_text(
        "from pathlib import Path\n"
        "def persist():\n"
        "    (Path.home() / '.sigma-ops' / 'ledger').mkdir(parents=True)\n"
    )

    assert _mod().scan(tmp_path) == [
        {"pattern": "<sigma-ops>/ledger/", "source": "code", "writer": "host_writer.py:3"},
    ]


def test_scan_includes_copytree_and_git_worktree_destinations(tmp_path):
    """Dropping a pinned durable-writer form must make its store row disappear."""
    (tmp_path / "writer.py").write_text(
        "import shutil\n"
        "import subprocess\n"
        "def persist(sdlc_dir, goal):\n"
        "    shutil.copytree('source', sdlc_dir / 'evidence' / goal / 'rv9' / 'wt')\n"
        "    subprocess.run(['git', 'worktree', 'add', sdlc_dir / 'work' / goal])\n"
        "    subprocess.run(['git', 'clone', 'origin', sdlc_dir / 'state' / 'clone'])\n"
    )

    assert _mod().scan(tmp_path) == [
        {"pattern": ".sdlc/evidence/<goal>/rv*/wt", "source": "code", "writer": "writer.py:4"},
        {"pattern": ".sdlc/state/clone/", "source": "code", "writer": "writer.py:6"},
        {"pattern": ".sdlc/work/<goal>/", "source": "code", "writer": "writer.py:5"},
    ]


def test_measure_host_refuses_home_and_only_measures_the_opted_in_root(tmp_path):
    """Changing the home refusal or widening traversal must make this test fail."""
    home = tmp_path / "home"; home.mkdir()
    config = home / ".codex"; config.mkdir()
    (config / "events.jsonl").write_bytes(b"abc")
    (home / "unrelated-private-data").write_bytes(b"this must not be counted")

    with pytest.raises(ValueError, match="home directory"):
        _mod().measure_host(home, home=home)

    assert _mod().measure_host(config, home=home) == {
        "path": "<home>/.codex",
        "size_bytes": 3,
    }
