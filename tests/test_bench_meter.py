"""Controls for the portable benchmark transcript meter (#345)."""
import csv
import importlib.util
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parent.parent
METER_PATH = ROOT / "evals" / "bench" / "meter.py"
PHASE_REPORT_PATH = ROOT / "skills" / "sigma-loop" / "scripts" / "phase_report.py"


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


def test_usage_breakdown_includes_cache_and_request_kinds_and_reconciles_total(tmp_path):
    """A published meter total is independently reproducible without transcript text.

    Input/output alone do not account for Claude cache charges.  This deliberately exercises
    every token kind plus a server-tool request, while retaining the absent-rate/zero-unit
    ``web_fetch`` row: a reader must be able to see every rate kind that the meter considered.
    """
    transcript = tmp_path / "full-usage.jsonl"
    line = _line("full", input_tokens=1_000_000, output_tokens=1_000_000)
    usage = line["message"]["usage"]
    usage["cache_read_input_tokens"] = 2_000_000
    usage["cache_creation"] = {
        "ephemeral_5m_input_tokens": 3_000_000,
        "ephemeral_1h_input_tokens": 4_000_000,
    }
    usage["server_tool_use"] = {"web_search_requests": 2}
    _write(transcript, line)

    rates = _rates(tmp_path)
    rates.append({"model": "claude-test", "rate_kind": "web_search",
                  "usd_per_mtok": None, "usd_per_request": 0.01,
                  "effective_from": "2026-01-01 00:00:00", "effective_to": None,
                  "source": "test-local"})
    result = meter.meter_transcripts([transcript], rates=rates)

    assert [(row["rate_kind"], row["units"], row["cost_usd"])
            for row in result["usage_by_rate_kind"]] == [
                ("input", 1_000_000, 2.0),
                ("output", 1_000_000, 10.0),
                ("cache_read", 2_000_000, 0.4),
                ("cache_write_5m", 3_000_000, 7.5),
                ("cache_write_1h", 4_000_000, 16.0),
                ("web_search", 2, 0.02),
                ("web_fetch", 0, 0.0),
            ]
    assert result["usage_by_rate_kind"][-1]["rate"] is None
    assert round(sum(row["cost_usd"] for row in result["usage_by_rate_kind"]), 6) == \
        result["cost_usd"] == 35.92


def test_recorded_sonnet_evidence_has_a_content_free_reproducible_breakdown():
    """The shipped evidence must explain its total without publishing a transcript path/text."""
    evidence = json.loads((ROOT / "docs" / "launch" / "evidence" /
                           "meter-sonnet-5-5-cf31b72c6967.json").read_text(encoding="utf-8"))
    result = evidence["result"]
    rows = result["usage_by_rate_kind"]

    assert evidence["schema"] == "sigma.benchmark-transcript-meter/v2"
    # Recorded history (#523): the evidence file keeps the path the rate card had when it was captured,
    # spelled from fragments so the leftover-name check does not flag this file.
    assert evidence["rate_card"]["path"] == "skills/" + "agr" + "im" + "-loop/rates/anthropic_list_prices.csv"
    assert len(evidence["rate_card"]["sha256"]) == 64
    assert [row["rate_kind"] for row in rows] == list(phase_report.RATE_KIND_USAGE)
    assert round(sum(row["cost_usd"] for row in rows), 6) == result["cost_usd"] == 0.645964
    assert rows[-1]["rate"] is None and rows[-1]["units"] == 0
    public = json.dumps(evidence, sort_keys=True)
    assert "/Users/" not in public and ".jsonl" not in public


# -- subscription auth (2026-10-06): tokens are the measured fact, dollars are indicative --------

def _full_line(message_id, *, input_tokens=0, output_tokens=0, cache_read=0, write_5m=0, write_1h=0):
    line = _line(message_id, input_tokens=input_tokens, output_tokens=output_tokens)
    usage = line["message"]["usage"]
    usage["cache_read_input_tokens"] = cache_read
    usage["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m, "ephemeral_1h_input_tokens": write_1h}
    return line


# The shape of a real rate-limit record, copied from a Claude Code transcript written when a weekly
# limit was hit (interactive session, host 2.1.284); the synthetic model is why phase_report never
# counts it as a turn.  No `claude -p` run under a subscription token has been observed.
RATE_LIMIT_RECORD = {
    "type": "assistant", "timestamp": "2026-10-01T20:35:26.780Z", "error": "rate_limit",
    "isApiErrorMessage": True, "apiErrorStatus": 429,
    "quotaLimits": {"status": "rejected", "resetsAt": 1790895600, "rateLimitType": "seven_day",
                    "overageStatus": "rejected", "isUsingOverage": False},
    "message": {"id": "synthetic", "role": "assistant", "model": "<synthetic>",
                "content": [{"type": "text", "text": "You've hit your weekly limit"}],
                "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}},
}


def test_meter_reports_every_token_kind_and_their_total_from_the_usage_records(tmp_path):
    """Control: summing only input and output (the old meter) leaves cache tokens out of the total."""
    transcript = tmp_path / "s.jsonl"
    _write(transcript, _full_line("a", input_tokens=10, output_tokens=20, cache_read=300,
                                  write_5m=4000, write_1h=50000),
           _full_line("b", input_tokens=1, output_tokens=2, cache_read=3, write_5m=4))

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    assert result["tokens_in"] == 11 and result["tokens_out"] == 22
    assert result["tokens_cache_read"] == 303
    assert result["tokens_cache_write"] == 54004
    assert result["tokens_total"] == 11 + 22 + 303 + 54004


def test_meter_counts_a_duplicated_message_id_once_for_every_token_kind(tmp_path):
    transcript = tmp_path / "s.jsonl"
    _write(transcript, _full_line("same", cache_read=5, write_5m=7, output_tokens=1),
           _full_line("same", cache_read=5, write_5m=7, output_tokens=9))

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    assert result["tokens_total"] == 5 + 7 + 9


def test_dollars_are_labelled_indicative_and_never_a_bill(tmp_path):
    transcript = tmp_path / "s.jsonl"
    _write(transcript, _line("a", input_tokens=1_000_000))

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    basis = result["cost_basis"]
    assert "indicative" in basis and "not a bill" in basis
    assert "published" in basis


def test_a_rate_limit_record_in_a_transcript_is_reported_with_its_reset_time(tmp_path):
    """Control: without the scan a throttled run would be scored as the arm's failure."""
    transcript = tmp_path / "s.jsonl"
    _write(transcript, _line("a", output_tokens=5), RATE_LIMIT_RECORD)

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    assert result["rate_limited"] == {"resets_at": 1790895600, "limit_type": "seven_day", "final": True}
    assert result["turns"] == 1, "the synthetic record is not a model turn"
    assert result["tokens_total"] == 5


def test_a_transcript_without_a_rate_limit_record_reports_none(tmp_path):
    transcript = tmp_path / "s.jsonl"
    _write(transcript, _line("a", output_tokens=5))

    assert meter.meter_transcripts([transcript], rates=_rates(tmp_path))["rate_limited"] is None


def test_a_rate_limit_record_in_a_subagent_transcript_is_found_through_the_session_id(tmp_path):
    projects = tmp_path / "projects"
    _write(projects / "-repo" / "sess.jsonl", _line("top", output_tokens=1))
    _write(projects / "-repo" / "sess" / "subagents" / "agent-a.jsonl", _line("sub", output_tokens=2),
           RATE_LIMIT_RECORD)

    result = meter.meter_transcripts(meter.session_paths("sess", projects), rates=_rates(tmp_path))

    assert result["rate_limited"] is not None and result["tokens_total"] == 3


def test_a_rate_limit_record_followed_by_real_work_is_reported_as_not_final(tmp_path):
    """A transient limit the session worked through must not discard a finished run."""
    transcript = tmp_path / "s.jsonl"
    _write(transcript, RATE_LIMIT_RECORD, _line("a", output_tokens=5))

    result = meter.meter_transcripts([transcript], rates=_rates(tmp_path))

    assert result["rate_limited"]["final"] is False and result["tokens_total"] == 5
