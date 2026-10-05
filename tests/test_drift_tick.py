"""#2311: `drift_watch.sweep`'s own composed gate (`ledger.enabled` + `drift_watch.enabled`) and TTL
watermark only ever fire from wherever calls `sweep()` directly -- `drift_tick.py` gives it a
wall-clock-driven caller threaded into `watch.sh`'s own tick sequence, the identical role
`reconcile_tick.py` (#2294) already plays for `loop._reconcile_sweep`. These tests prove
`drift_tick.tick` reuses `drift_watch.sweep` exactly (and therefore its existing gate and TTL
watermark) rather than re-deriving any of that here -- mirrors `test_reconcile_tick.py`'s own shape
call for call."""
import importlib.util
import json
import pathlib

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


drift_tick = _mod("drift_tick")
drift_watch = drift_tick.drift_watch


def _sdlc(tmp_path, config=None):
    d = tmp_path / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config if config is not None else {}))
    return d


def _config():
    return {"ledger": {"enabled": True, "actor": "watcher"},
            "drift_watch": {"enabled": True, "ttl_minutes": 90,
                             "channels": {"sigma": "C123", "org": None}},
            "work": {"base": "main", "remote": "origin"},
            "discovery": {"github": {"repo": "acme/app"}}}


# ------------------------------------------------------------------ tick()


def test_tick_calls_sweep_with_the_same_sdlc_dir_and_config(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, _config())
    calls = []

    def fake_sweep(sdlc_dir, config=None, run=None, now=None):
        calls.append((sdlc_dir, config, run, now))
        return "drifted"

    monkeypatch.setattr(drift_watch, "sweep", fake_sweep)
    summary = drift_tick.tick(str(d))
    assert calls == [(str(d), _config(), None, None)]
    assert summary == "drifted"


def test_tick_reads_config_fresh_when_none_is_passed(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, _config())
    seen = []
    monkeypatch.setattr(drift_watch, "sweep",
                         lambda sdlc_dir, config=None, run=None, now=None: seen.append(config) or "")
    drift_tick.tick(str(d))
    assert seen == [_config()]


def test_tick_passes_an_explicit_config_through_unchanged(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, _config())
    explicit = {"ledger": {"enabled": False}}
    seen = []
    monkeypatch.setattr(drift_watch, "sweep",
                         lambda sdlc_dir, config=None, run=None, now=None: seen.append(config) or "")
    drift_tick.tick(str(d), config=explicit)
    assert seen == [explicit]


def test_tick_is_a_cheap_noop_when_the_gate_is_closed(tmp_path):
    """No mocking at all here -- `sweep()`'s own composed gate (`ledger.enabled` off) is exercised
    for real, proving `tick()` does not bypass or re-derive it."""
    d = _sdlc(tmp_path, {"ledger": {"enabled": False}, "drift_watch": {"enabled": True}})
    assert drift_tick.tick(str(d)) == ""


def test_tick_never_returns_none_even_when_sweep_does(tmp_path, monkeypatch):
    d = _sdlc(tmp_path, _config())
    monkeypatch.setattr(drift_watch, "sweep", lambda *a, **k: None)
    assert drift_tick.tick(str(d)) == ""


# ------------------------------------------------------------------ CLI


def test_main_cli_runs_a_tick_and_prints_the_summary(tmp_path, capsys, monkeypatch):
    d = _sdlc(tmp_path, _config())
    monkeypatch.setattr(drift_watch, "sweep", lambda *a, **k: "posted drift summary for 1 unit(s)")
    assert drift_tick.main(["drift_tick.py", str(d)]) == 0
    assert "posted drift summary" in capsys.readouterr().out


def test_main_cli_is_quiet_when_the_gate_is_closed(tmp_path, capsys):
    d = _sdlc(tmp_path, {"ledger": {"enabled": False}})
    assert drift_tick.main(["drift_tick.py", str(d)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_main_cli_is_never_fatal_even_when_tick_raises(monkeypatch, capsys):
    def boom(_sdlc_dir):
        raise RuntimeError("boom")

    monkeypatch.setattr(drift_tick, "tick", boom)
    assert drift_tick.main(["drift_tick.py", "/nonexistent"]) == 1
    assert "tick failed (non-fatal): boom" in capsys.readouterr().err


def test_main_cli_defaults_sdlc_dir_when_omitted(monkeypatch):
    seen = []
    monkeypatch.setattr(drift_tick, "tick", lambda d: seen.append(d) or "")
    drift_tick.main(["drift_tick.py"])
    assert seen == [".sdlc"]
