"""#2119 AC-2 / AC-3: /sigma-doctor shows two informational burst-readiness rows, read from local files only.

Row 1 (dependency bot): a bot config with version updates enabled, `work.ci_required` true and no verify recorded
in the last 14 days (a local proxy: CI runs themselves are never read, so no network call). Each clause is broken on
its own below. Row 2: the newest recorded verify result on this checkout. Neither row is ever a failure. Not
measured on a real cloud container or a real consumer repository."""
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "sigma-doctor" / "scripts" / "doctor.py"
BOT = "dependency-update bot has version updates on, CI is required and no recent verify is recorded"
NOW = 1_800_000_000.0

DEPENDABOT = "version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n    schedule:\n      interval: weekly\n"
DEPENDABOT_PAUSED = ("version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n    open-pull-requests-limit: 0\n"
                     "  - package-ecosystem: npm\n    directory: /\n    open-pull-requests-limit: 0  # paused\n")


def _doctor():
    spec = importlib.util.spec_from_file_location("doctor_2119", D)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _repo(tmp_path, dependabot=None, renovate=None):
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text("{}")
    if dependabot is not None:
        (tmp_path / ".github").mkdir(exist_ok=True)
        (tmp_path / ".github" / "dependabot.yml").write_text(dependabot)
    if renovate is not None:
        (tmp_path / "renovate.json").write_text(renovate)
    return tmp_path / ".sdlc"


def _evidence(base, goal, exit_code, age_s):
    d = base / "state" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    (d / (goal + ".json")).write_text(json.dumps({"exit": exit_code, "at": NOW - age_s}))


def test_enabled_dependabot_with_ci_required_and_no_recent_verify_gives_the_info_row(tmp_path):
    rows = _doctor()._dependency_bot_rows(_repo(tmp_path, DEPENDABOT), {}, now=NOW)
    assert len(rows) == 1 and rows[0]["name"] == BOT, rows
    assert rows[0]["level"] == "INFO" and rows[0]["ok"] is True, rows
    assert "dependabot.yml" in rows[0]["fix"] and "not read" in rows[0]["fix"]


def test_a_paused_dependabot_gives_no_row(tmp_path):
    """Clause 1 broken alone: every update entry has open-pull-requests-limit 0 -> no version updates."""
    assert _doctor()._dependency_bot_rows(_repo(tmp_path, DEPENDABOT_PAUSED), {}, now=NOW) == []


def test_one_enabled_entry_among_paused_ones_still_gives_the_row(tmp_path):
    text = DEPENDABOT_PAUSED + "  - package-ecosystem: github-actions\n    directory: /\n"
    assert len(_doctor()._dependency_bot_rows(_repo(tmp_path, text), {}, now=NOW)) == 1


def test_no_bot_config_gives_no_row(tmp_path):
    assert _doctor()._dependency_bot_rows(_repo(tmp_path), {}, now=NOW) == []


@pytest.mark.parametrize("text,rows", [('{"extends": ["config:base"]}', 1), ('{"enabled": false}', 0),
                                       ('{"extends": [":disableRenovate"]}', 0)],
                         ids=["enabled", "enabled-false", "disableRenovate"])
def test_renovate_gives_the_row_unless_it_is_disabled(tmp_path, text, rows):
    assert len(_doctor()._dependency_bot_rows(_repo(tmp_path, renovate=text), {}, now=NOW)) == rows


def test_an_unreadable_renovate_file_says_nothing(tmp_path):
    assert _doctor()._dependency_bot_rows(_repo(tmp_path, renovate="{not json"), {}, now=NOW) == []


def test_ci_not_required_gives_no_row(tmp_path):
    """Clause 2 broken alone: the config turned the CI gate off, so the bot cannot park a goal on CI."""
    cfg = {"work": {"ci_required": False}}
    assert _doctor()._dependency_bot_rows(_repo(tmp_path, DEPENDABOT), cfg, now=NOW) == []


def test_a_recent_recorded_verify_gives_no_row_and_an_old_one_does(tmp_path):
    """Clause 3 broken alone, and the 14-day edge on both sides."""
    base = _repo(tmp_path, DEPENDABOT)
    _evidence(base, "1", 0, 13 * 86400)
    assert _doctor()._dependency_bot_rows(base, {}, now=NOW) == []
    _evidence(base, "1", 0, 15 * 86400)
    assert len(_doctor()._dependency_bot_rows(base, {}, now=NOW)) == 1


def test_a_failed_recent_verify_is_still_a_recent_run(tmp_path):
    base = _repo(tmp_path, DEPENDABOT)
    _evidence(base, "1", 1, 3600)
    assert _doctor()._dependency_bot_rows(base, {}, now=NOW) == []


def test_last_verify_row_reports_the_newest_result_and_its_age(tmp_path):
    base = _repo(tmp_path)
    _evidence(base, "7", 0, 5 * 86400)
    _evidence(base, "9", 1, 2 * 86400)
    cfg = {"verify": {"command": "pytest"}}
    rows = _doctor()._last_verify_rows(base, cfg, now=NOW)
    assert len(rows) == 1 and rows[0]["level"] == "INFO" and rows[0]["ok"] is True, rows
    assert "exit 1" in rows[0]["name"] and "2d ago" in rows[0]["name"] and "9" in rows[0]["name"], rows
    assert "not a run of the base" in rows[0]["fix"]


def test_last_verify_row_with_nothing_recorded_says_so(tmp_path):
    rows = _doctor()._last_verify_rows(_repo(tmp_path), {"verify": {"command": "pytest"}}, now=NOW)
    assert len(rows) == 1 and "no verify result recorded" in rows[0]["name"] and rows[0]["ok"] is True, rows


def test_last_verify_row_needs_a_verify_command(tmp_path):
    base = _repo(tmp_path)
    _evidence(base, "9", 0, 60)
    assert _doctor()._last_verify_rows(base, {}, now=NOW) == []


def test_unreadable_evidence_is_skipped_not_fatal(tmp_path):
    base = _repo(tmp_path)
    (base / "state" / "verify").mkdir(parents=True)
    (base / "state" / "verify" / "3.json").write_text("{nope")
    _evidence(base, "4", 0, 60)
    rows = _doctor()._last_verify_rows(base, {"verify": {"command": "x"}}, now=NOW)
    assert len(rows) == 1 and "exit 0" in rows[0]["name"], rows


def test_the_rows_make_no_network_or_subprocess_call(tmp_path, monkeypatch):
    """AC-3: both detectors read files only. Any socket or subprocess use raises."""
    def boom(*a, **k):
        raise AssertionError("network or subprocess call")
    doctor = _doctor()
    base = _repo(tmp_path, DEPENDABOT)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert len(doctor._dependency_bot_rows(base, {}, now=NOW)) == 1
    assert len(doctor._last_verify_rows(base, {"verify": {"command": "x"}}, now=NOW)) == 1


def test_the_documented_gesture_prints_the_rows_and_exits_zero(tmp_path):
    """`python3 .../doctor.py check .sdlc` in the repository root: the rows print as INFO, the pass is not failed."""
    base = _repo(tmp_path, DEPENDABOT)
    (base / "config.json").write_text(json.dumps({"verify": {"command": "true"}}))
    env = dict(os.environ)
    done = subprocess.run([sys.executable, str(D), "check", ".sdlc"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert "[INFO] " + BOT in done.stdout, (done.stdout[-1500:], done.stderr[-500:])
    assert "[INFO] last recorded verify" in done.stdout, done.stdout[-1500:]
    assert "[MISSING] " + BOT not in done.stdout
    (tmp_path / ".github" / "dependabot.yml").write_text(DEPENDABOT_PAUSED)
    done = subprocess.run([sys.executable, str(D), "check", ".sdlc"], cwd=str(tmp_path), env=env,
                          capture_output=True, text=True, timeout=120)
    assert BOT not in done.stdout
