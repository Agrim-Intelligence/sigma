"""D7 'every refusal prints its fix': the 'ledger is off' / 'knowledge sync is off' notices (they
named the config key, not the gesture that sets it), the ledger/handoff `OFF` line, the unpark
`--answers` read error and `decision_gate.py init` on an existing registry."""
import importlib.util, json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name, where=LOOP):
    spec = importlib.util.spec_from_file_location(name, where / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _sdlc(tmp_path, cfg=None):
    d = tmp_path / ".sdlc"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(cfg or {}))
    return str(d)


def test_sync_with_the_ledger_off_names_the_gesture_and_the_rerun(tmp_path, capsys):
    sync = _mod("sync")
    d = _sdlc(tmp_path)
    assert sync.main(["sync.py", "publish", d]) == 1
    err = capsys.readouterr().err
    assert err.startswith('ledger is off (config: "ledger": {"enabled": true})')
    assert "`/sigma-init --ledger yes`" in err and f"`sync.py publish {d}`" in err


def test_sync_with_knowledge_sync_off_names_the_keys_and_the_rerun(tmp_path, capsys):
    sync = _mod("sync")
    d = _sdlc(tmp_path)
    assert sync.main(["sync.py", "publish", d, "--channel", "knowledge"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("knowledge sync is off")
    assert '"knowledge_graph": {"enabled": true, "sync": {"enabled": true}}' in err
    assert ".sdlc/config.json" in err and f"`sync.py publish {d}" in err


def test_ledger_append_with_the_ledger_off_says_nothing_was_written_and_how_to_turn_it_on(tmp_path, capsys):
    ledger = _mod("ledger")
    d = _sdlc(tmp_path)
    assert ledger.main(["ledger.py", "append", d, "note", "0001"]) == 0
    out = capsys.readouterr().out
    assert out.startswith('OFF (config: "ledger": {"enabled": true})')
    assert "nothing written" in out and "`/sigma-init --ledger yes`" in out


def test_unpark_unreadable_answers_file_names_the_expected_shape(tmp_path, capsys):
    unpark = _mod("unpark")
    d = _sdlc(tmp_path, {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}})
    rc = unpark.main(["unpark.py", "resolve", d, "5", "--answers", str(tmp_path / "nope.json"),
                      "--decision", "x"])
    err = capsys.readouterr().err
    assert rc == 2 and "could not read --answers" in err
    assert '{"answers": {...}}' in err and "unpark.py brief" in err


def test_decision_gate_init_on_an_existing_registry_says_what_to_do_next(tmp_path):
    gate = ROOT / "hooks" / "decision_gate.py"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "config.json").write_text("{}")          # adopted: init refuses otherwise
    first = subprocess.run([sys.executable, str(gate), "init", str(tmp_path)],
                           capture_output=True, text=True)
    second = subprocess.run([sys.executable, str(gate), "init", str(tmp_path)],
                            capture_output=True, text=True)
    assert second.returncode == 1 and "already exists; not overwriting it" in second.stderr, (first, second)
    assert "decision_gate.py validate" in second.stderr
