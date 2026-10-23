"""#2002 (FR-32): `doctor.py changes` lists what the lean release changes and which of YOUR config values
it overrides -- informational, read-only, no check() row, no wizard step, no config key.

Honesty rule (slice 1 ships NO lean route): every registry entry is `active=False` and prints
`PLANNED (slice N, not in this build)`; nothing here may read as a shipped lean behaviour. Each clause
below is meant to be broken alone (see the controls recorded with the goal)."""
import ast
import importlib.util
import inspect
import json
import pathlib
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_doctor as td  # noqa: E402
from test_doctor import _isolate_claude_config  # noqa: E402,F401  (autouse: hermetic ~/.claude)
import upgrade_fixtures as u  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "sigma-doctor"
DOCTOR = SKILL_DIR / "scripts" / "doctor.py"

def _doc():
    return td._doc()


def _proj(tmp_path, cfg=None):
    sdlc = td._sdlc(tmp_path, cfg if cfg is not None else u.omega_shaped_config())
    return sdlc


def _changes_out(capsys, sdlc, doc=None):
    rc = (doc or _doc()).main(["doctor.py", "changes", sdlc])
    return rc, capsys.readouterr().out


# --------------------------------------------------------------------------- (a) registry honesty

def test_registry_entries_are_planned_slice_two_or_later_and_none_is_active_in_this_build():
    d = _doc()
    assert d.LEAN_CHANGES
    ids = [c["id"] for c in d.LEAN_CHANGES]
    assert len(ids) == len(set(ids))
    for c in d.LEAN_CHANGES:
        assert set(c) >= {"id", "text", "slice", "active"} and c["text"]
        assert isinstance(c["slice"], int) and not isinstance(c["slice"], bool) and c["slice"] >= 2
    assert [c["id"] for c in d.LEAN_CHANGES if c["active"] is not False] == []   # slice 1 ships no lean route


def test_report_labels_come_from_active_and_slice(tmp_path, capsys):
    d = _doc()
    sdlc = _proj(tmp_path)
    rc, out = _changes_out(capsys, sdlc, d)
    assert rc == 0 and "ACTIVE in this build" not in out
    for c in d.LEAN_CHANGES:
        assert f"PLANNED (slice {c['slice']}, not in this build)" in out and c["text"] in out
    d.LEAN_CHANGES = tuple(dict(c, active=True) if i == 0 else c for i, c in enumerate(d.LEAN_CHANGES))
    rc, out = _changes_out(capsys, sdlc, d)
    first = d.LEAN_CHANGES[0]
    line = next(ln for ln in out.splitlines() if first["text"] in ln)
    assert "ACTIVE in this build" in line and "PLANNED" not in line
    assert out.count("ACTIVE in this build") == 1


# --------------------------------------------------------------------------- (b) overrides

def _ov(cfg):
    return {o["key"]: o for o in _doc().lean_overrides(cfg)}


def test_test_review_false_is_overridden_by_the_lean_route_planned():
    o = _ov({"review": {"test_review": False}})["review.test_review"]
    assert o["overridden"] is True and "overridden by lean route (planned, slice" in o["effect"]


def test_test_review_true_or_absent_is_not_reported_as_overridden():
    assert "review.test_review" not in _ov({"review": {"test_review": True}})
    assert "review.test_review" not in _ov({"review": {}})


def test_absent_action_log_is_read_as_on_for_lean_today_off():
    o = _ov({})["action_log"]
    assert o["overridden"] is True
    assert "read as on for lean (FR-96)" in o["effect"] and "today: off" in o["effect"]


def test_explicit_action_log_values_are_reported_as_explicit_not_overridden():
    off = _ov({"action_log": {"enabled": False}})["action_log"]
    assert off["overridden"] is False and "explicit" in off["effect"] and "false" in off["effect"]
    assert "action_log" not in _ov({"action_log": {"enabled": True}})


def test_non_lean_values_produce_no_row():
    keys = set(_ov(u.omega_shaped_config()))
    assert keys == {"review.test_review", "action_log"}                  # x_custom_key etc. never appear
    assert not any("x_custom" in k or "team_notes" in k for k in keys)


def test_malformed_blocks_never_crash():
    for cfg in ({"review": "x", "action_log": 5}, {"review": None}, []):
        _doc().lean_overrides(cfg)


# --------------------------------------------------------------------------- (c) informational only

def test_rows_are_informational_and_not_a_check_row_or_a_wizard_step(tmp_path):
    d = _doc()
    rows = list(d.lean_overrides(u.omega_shaped_config())) + list(d.LEAN_CHANGES)
    assert all(r.get("ok", True) is True and r.get("fix", "") == "" for r in rows)
    sdlc = _proj(tmp_path)
    names = sorted(r["name"] for r in d.check(sdlc, run=td._runner(), cheap_only=True))
    assert not any(re.search(r"lean|changes", n, re.I) for n in names)
    sys.path.insert(0, str(ROOT / "skills" / "sigma-init" / "scripts"))
    import setup_wizard
    assert setup_wizard._MODES and not any(re.search(r"lean|changes", str(k), re.I) for k in setup_wizard._MODES)


def test_check_prints_one_informational_pointer_line_without_changing_the_count(tmp_path, monkeypatch, capsys):
    d = _doc()
    sdlc = _proj(tmp_path)
    monkeypatch.setattr(d, "features", lambda *a, **k: [])
    monkeypatch.setattr(d, "architecture_report", lambda *a, **k: ([], None))
    orig = d.check
    monkeypatch.setattr(d, "check", lambda *a, **k: orig(*a, run=lambda args: "", **k))
    rows = d.check(sdlc)
    assert d.main(["doctor.py", "check", sdlc]) == 0
    out = capsys.readouterr().out
    ready = re.search(r"sigma-doctor: (\d+)/(\d+) ready", out)
    assert ready and int(ready.group(2)) == len(rows)                       # same N/M as the rows themselves
    line = [ln for ln in out.splitlines() if "lean release changes" in ln]
    assert len(line) == 1 and "doctor.py changes" in line[0]
    assert out.index(ready.group(0)) < out.index(line[0])                   # after 'N/M ready'
    assert "[MISSING] lean" not in out and "[OK ] lean" not in out


# --------------------------------------------------------------------------- (d) the documented gesture

def _documented(text, verb):
    m = re.search(r'^[ \t]*`?(python3 "\$\{CLAUDE_SKILL_DIR\}/scripts/doctor\.py" %s \.sdlc)`?' % verb, text, re.M)
    return m.group(1) if m else None


def _run_documented(cmd, cwd, home):
    argv = shlex.split(cmd.replace("${CLAUDE_SKILL_DIR}", str(SKILL_DIR)))
    argv[0] = sys.executable
    env = {"PATH": __import__("os").environ.get("PATH", ""), "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home),
           "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
                          stdin=subprocess.DEVNULL)


def test_documented_changes_gesture_prints_both_sections_and_writes_nothing(tmp_path):
    cmd = _documented((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"), "changes")
    assert cmd, "SKILL.md must document `doctor.py changes .sdlc`"
    repo = tmp_path / "repo"; repo.mkdir()
    sdlc = _proj(repo)
    cfg = pathlib.Path(sdlc) / "config.json"
    before = (cfg.read_bytes(), cfg.stat().st_mtime_ns, sorted(p.name for p in pathlib.Path(sdlc).rglob("*")))
    home = tmp_path / "home"; home.mkdir()
    r = _run_documented(cmd, repo, home)
    assert r.returncode == 0, r.stderr
    assert "PLANNED (slice" in r.stdout and "ACTIVE in this build" not in r.stdout
    assert "review.test_review: false -> overridden by lean route" in r.stdout
    assert "action_log: absent -> read as on for lean" in r.stdout
    assert (cfg.read_bytes(), cfg.stat().st_mtime_ns,
            sorted(p.name for p in pathlib.Path(sdlc).rglob("*"))) == before


def test_documented_check_gesture_prints_the_pointer_line(tmp_path):
    cmd = _documented((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"), "check")
    repo = tmp_path / "repo"; repo.mkdir()
    _proj(repo)
    home = tmp_path / "home"; home.mkdir()
    r = _run_documented(cmd, repo, home)
    assert r.returncode == 0, r.stderr
    assert "lean release changes: run `doctor.py changes" in r.stdout


def test_missing_or_invalid_config_is_a_message_and_exit_one_not_a_traceback(tmp_path, capsys):
    d = _doc()
    assert d.main(["doctor.py", "changes", str(tmp_path / "nope")]) == 1
    bad = tmp_path / ".sdlc"; bad.mkdir(); (bad / "config.json").write_text("{not json")
    assert d.main(["doctor.py", "changes", str(bad)]) == 1
    assert "config.json" in capsys.readouterr().out


# --------------------------------------------------------------------------- (e) standalone

def test_doctor_stays_standalone_and_the_lean_code_loads_nothing():
    tree = ast.parse(DOCTOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "work" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "work"
    d = _doc()
    for fn in (d.lean_overrides, d.lean_changes):
        src = inspect.getsource(fn)
        assert "_load_loop_script" not in src and "import " not in src and "subprocess" not in src

