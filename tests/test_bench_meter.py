"""Controls for the portable benchmark transcript meter (#345)."""
import csv
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
METER_PATH = ROOT / "evals" / "bench" / "meter.py"
PHASE_REPORT_PATH = ROOT / "skills" / "agrim-loop" / "scripts" / "phase_report.py"


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


meter = _module(METER_PATH, "bench_meter")
phase_report = _module(PHASE_REPORT_PATH, "bench_phase_report")


def _line(message_id, model="claude-test", input_tokens=0, output_tokens=0):
    return {
        "type": "assistant",
        "timestamp": "2026-10-01T00:00:00Z",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 0,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 0,
                    "ephemeral_1h_input_tokens": 0,
                },
            },
        },
    }


def _write(path, *lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def _rates(tmp_path):
    path = tmp_path / "rates.csv"
    fields = ("model", "rate_kind", "usd_per_mtok", "usd_per_request",
              "effective_from", "effective_to", "source")
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for kind, value in (("input", "2"), ("output", "10"), ("cache_read", "0.2"),
                            ("cache_write_5m", "2.5"), ("cache_write_1h", "4")):
            writer.writerow({"model": "claude-test", "rate_kind": kind,
                             "usd_per_mtok": value, "usd_per_request": "",
                             "effective_from": "2026-01-01 00:00:00",
                             "effective_to": "", "source": "test-local"})
    return phase_report.load_rate_rows(path)


def test_session_id_meter_includes_top_level_and_subagent_transcripts(tmp_path):
    """Control: omitting ``subagents/`` makes this documented input mode fail."""
    projects = tmp_path / "projects"
    session = "session-123"
    _write(projects / "-repo" / f"{session}.jsonl", _line("top", input_tokens=1_000_000,
                                                            output_tokens=1_000_000))
    _write(projects / "-repo" / session / "subagents" / "agent-a.jsonl",
           _line("agent", input_tokens=500_000, output_tokens=250_000))

    result = meter.meter_transcripts(meter.session_paths(session, projects), rates=_rates(tmp_path))

    assert result["transcripts"] == [
        str(projects / "-repo" / f"{session}.jsonl"),
        str(projects / "-repo" / session / "subagents" / "agent-a.jsonl"),
    ]
    assert result["tokens_in"] == 1_500_000
    assert result["tokens_out"] == 1_250_000
    assert result["turns"] == 2
    assert result["unpriced_turns"] == 0
    assert result["cost_usd"] == 15.5


def test_unknown_turn_makes_total_cost_null_but_keeps_priced_part(tmp_path):
    known = tmp_path / "known.jsonl"
    unknown = tmp_path / "unknown.jsonl"
    _write(known, _line("known", input_tokens=1_000_000))
    _write(unknown, _line("unknown", model="claude-unknown-9", input_tokens=1_000_000))

    result = meter.meter_transcripts([known, unknown], rates=_rates(tmp_path))

    assert result["unpriced_turns"] == 1
    assert result["cost_usd"] is None
    assert result["cost_usd_priced_part"] == 2.0


def test_missing_or_empty_requested_input_never_presents_partial_amount_as_total(tmp_path):
    """Control: every explicit input must contribute before the total is usable."""
    known = tmp_path / "known.jsonl"
    missing = tmp_path / "missing.jsonl"
    empty = tmp_path / "empty.jsonl"
    _write(known, _line("known", input_tokens=1_000_000))
    empty.touch()

    for unavailable in (missing, empty):
        result = meter.meter_transcripts([known, unavailable], rates=_rates(tmp_path))

        assert result["cost_usd"] is None
        assert result["cost_usd_priced_part"] == 2.0
        assert result["unavailable_inputs"] == [str(unavailable)]


def test_duplicate_message_ids_are_counted_once_through_wrapper(tmp_path):
    transcript = tmp_path / "session.jsonl"
    _write(transcript, _line("same", input_tokens=1_000_000, output_tokens=500),
           _line("same", input_tokens=1_000_000, output_tokens=1_000_000))

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    assert result["turns"] == 1
    assert result["tokens_in"] == 1_000_000
    assert result["tokens_out"] == 1_000_000
    assert result["cost_usd"] == 12.0


def test_shipped_rate_card_prices_sonnet_5_5_after_its_official_release(tmp_path):
    transcript = tmp_path / "sonnet-5-5.jsonl"
    _write(transcript, _line("sonnet-5-5", model="claude-sonnet-5-5",
                             input_tokens=1_000_000, output_tokens=1_000_000))

    result = meter.meter_transcripts([transcript])

    assert result["unpriced_turns"] == 0
    assert result["cost_usd"] == 12.0
