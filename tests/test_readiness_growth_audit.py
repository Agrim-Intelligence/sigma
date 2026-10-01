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

    assert _mod().measure_host(config, home=home, environ={}) == {
        "path": "<codex-home>",
        "size_bytes": 3,
    }


def test_scan_resolves_supported_helper_writers_to_durable_store_rows(tmp_path):
    """Removing the helper map must make real helper-managed stores disappear."""
    scripts = tmp_path / "skills" / "agrim-loop" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "ledger.py").write_text(
        "def append(sdlc_dir):\n"
        "    with path.open('a') as handle:\n"
        "        handle.write('entry')\n"
    )
    (scripts / "actionlog.py").write_text(
        "def append(sdlc_dir, goal):\n"
        "    with path.open('a') as handle:\n"
        "        handle.write('entry')\n"
    )
    (scripts / "timing_store.py").write_text(
        "def append(sdlc_dir, goal):\n"
        "    with path.open('a') as handle:\n"
        "        handle.write('entry')\n"
        "def append_session(sdlc_dir, session):\n"
        "    with path.open('a') as handle:\n"
        "        handle.write('entry')\n"
    )
    (scripts / "witness.py").write_text(
        "def record(sdlc_dir, goal):\n"
        "    with path.open('a') as handle:\n"
        "        handle.write('entry')\n"
    )

    assert _mod().scan(tmp_path) == [
        {"pattern": ".sdlc/events/<actor>-<writer>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/ledger.py:2"},
        {"pattern": ".sdlc/ledger/entries/<actor>-<writer>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/ledger.py:2"},
        {"pattern": ".sdlc/state/log/<goal>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/actionlog.py:2"},
        {"pattern": ".sdlc/state/time/<goal>/<writer>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/timing_store.py:2"},
        {"pattern": ".sdlc/state/time/_sessions/<session>/<writer>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/timing_store.py:5"},
        {"pattern": ".sdlc/state/time/_sessions/<session>/turns.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/timing_store.py:5"},
        {"pattern": ".sdlc/state/witness/<goal>.jsonl", "source": "code",
         "writer": "skills/agrim-loop/scripts/witness.py:2"},
    ]


def test_measure_host_accepts_only_named_configuration_roots(tmp_path):
    """Widening measurement beyond named roots must make this refusal control fail."""
    home = tmp_path / "home"; home.mkdir()
    codex = home / ".codex"; codex.mkdir()
    (codex / "state.json").write_bytes(b"abc")
    sigma_ops = home / ".sigma-ops"; sigma_ops.mkdir()
    (sigma_ops / "audit.log").write_bytes(b"abcd")
    claude = tmp_path / "managed-claude"; claude.mkdir()
    (claude / "cursor.json").write_bytes(b"abcde")
    unowned = home / "Downloads"; unowned.mkdir()

    mod = _mod()
    assert mod.measure_host(codex, home=home, environ={}) == {
        "path": "<codex-home>", "size_bytes": 3,
    }
    assert mod.measure_host(sigma_ops, home=home, environ={}) == {
        "path": "<sigma-ops>", "size_bytes": 4,
    }
    assert mod.measure_host(claude, home=home, environ={"CLAUDE_CONFIG_DIR": str(claude)}) == {
        "path": "<claude-config>", "size_bytes": 5,
    }
    with pytest.raises(ValueError, match="named configuration root"):
        mod.measure_host(unowned, home=home, environ={})
