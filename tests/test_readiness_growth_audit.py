"""#349 — deterministic growing-store audit controls.

Each assertion names a scanner regression that would otherwise hide a durable
store: dropping code writers, treating skill prose as invisible, losing the
normalised store key, or walking an entire home directory during measurement.
"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

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


def test_scan_follows_discovered_helper_returns_for_claim_review_receipt_and_worktree(tmp_path):
    """A writer hidden behind a newly-added helper must still become an inventory row.

    This deliberately does not register the helper anywhere in the scanner.  The
    source import and its return expression are the only mapping authority.
    """
    (tmp_path / "state.py").write_text(
        "from pathlib import Path\n"
        "def claim_path(sdlc_dir, goal):\n"
        "    return Path(sdlc_dir) / 'state' / 'claims' / f'{goal}.json'\n"
        "def receipt_path(sdlc_dir, generation):\n"
        "    return Path(sdlc_dir) / 'state' / 'receipts' / f'{generation}.json'\n"
    )
    (tmp_path / "writer.py").write_text(
        "from pathlib import Path\n"
        "import state\n"
        "def persist(sdlc_dir, goal, generation):\n"
        "    claim = state.claim_path(sdlc_dir, goal)\n"
        "    claim.write_text('{}')\n"
        "    receipt = state.receipt_path(sdlc_dir, generation)\n"
        "    receipt.write_text('{}')\n"
        "    (Path(sdlc_dir) / 'state' / 'review-generations' / generation / 'evidence.json').write_text('{}')\n"
        "    (Path(sdlc_dir) / 'work' / goal).mkdir()\n"
    )

    assert _mod().scan(tmp_path) == [
        {"pattern": ".sdlc/state/claims/<goal>.json", "source": "code", "writer": "writer.py:5"},
        {"pattern": ".sdlc/state/receipts/<generation>.json", "source": "code", "writer": "writer.py:7"},
        {"pattern": ".sdlc/state/review-generations/<generation>/evidence.json", "source": "code", "writer": "writer.py:8"},
        {"pattern": ".sdlc/work/<goal>/", "source": "code", "writer": "writer.py:9"},
    ]


def test_measure_repository_sdlc_is_explicit_and_binds_the_measured_revision(tmp_path, monkeypatch):
    """Removing the repo-root or revision binding makes this measurement lie."""
    sdlc = tmp_path / ".sdlc"; sdlc.mkdir()
    (sdlc / "state.json").write_bytes(b"abc")
    monkeypatch.setattr(_mod().subprocess, "run", lambda *args, **kwargs: type(
        "Run", (), {"returncode": 0, "stdout": "abc123\n"})())

    assert _mod().measure_repository(tmp_path) == {
        "path": ".sdlc", "size_bytes": 3, "revision": "abc123",
    }


def test_scan_is_identical_across_hash_seeds_when_a_path_branch_has_two_values(tmp_path):
    """A set-backed helper branch must not choose a different durable row per process."""
    (tmp_path / "writer.py").write_text(
        "from pathlib import Path\n"
        "def path(sdlc_dir, selected):\n"
        "    suffix = 'first' if selected else 'second'\n"
        "    return Path(sdlc_dir) / 'state' / f'{suffix}.json'\n"
        "def persist(sdlc_dir, selected):\n"
        "    path(sdlc_dir, selected).write_text('{}')\n"
    )
    command = (
        "import importlib.util, json, sys; "
        "spec=importlib.util.spec_from_file_location('audit', sys.argv[1]); "
        "mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
        "print(json.dumps(mod.scan(sys.argv[2]), sort_keys=True))"
    )
    results = []
    for seed in ("1", "2", "3"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-c", command, str(SCRIPT), str(tmp_path)],
            check=True, capture_output=True, text=True, env=env,
        )
        results.append(json.loads(completed.stdout))
    assert results[0] == results[1] == results[2]
    assert results[0] == [
        {"pattern": ".sdlc/state/first.json", "source": "code", "writer": "writer.py:6"},
        {"pattern": ".sdlc/state/second.json", "source": "code", "writer": "writer.py:6"},
    ]


def test_scan_records_one_argument_path_rename_and_replace_destinations(tmp_path):
    """Path.rename/replace each take one destination, unlike os.rename's two args."""
    (tmp_path / "writer.py").write_text(
        "from pathlib import Path\n"
        "def persist(sdlc_dir):\n"
        "    pending = Path(sdlc_dir) / 'state' / 'pending.json'\n"
        "    pending.replace(Path(sdlc_dir) / 'state' / 'committed.json')\n"
        "    pending.rename(Path(sdlc_dir) / 'state' / 'archived.json')\n"
    )

    assert _mod().scan(tmp_path) == [
        {"pattern": ".sdlc/state/archived.json", "source": "code", "writer": "writer.py:5"},
        {"pattern": ".sdlc/state/committed.json", "source": "code", "writer": "writer.py:4"},
    ]
