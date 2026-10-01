import csv
import importlib.util
import itertools
import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest
from journal_events import journal_events

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"
_MESSAGE_IDS = itertools.count(1)


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pr = _mod("phase_report")
loop = _mod("loop")
#: #2112: the module `phase_report.py` SHELLS OUT to. Loaded here only to read its constants, so a
#: sentence this file asserts is the renderer's own text, never a copy that can drift from it.
render = _mod("render")


def _run(*args):
    return subprocess.run(
        [sys.executable, str(S / "phase_report.py"), *args],
        capture_output=True, text=True,
    )


def _sdlc(tmp_path, config=None):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True)
    (sdlc / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
    # `journal.enabled: true` is this module's default fixture (#2580; the only key read since
    # #2706). `share` is not set because there is no `share` key any more: every EVENTS write lands in
    # `local_events_dir()` (`.sdlc/events/`). `ledger.read_all(stream=EVENTS)` still reads only
    # `entries_dir(EVENTS)` (`.sdlc/ledger/events/`) and is therefore blind to it -- read events
    # back through `journal_events()`, which unions both the way the production readers do.
    cfg = config or {"journal": {"enabled": True}}
    (sdlc / "config.json").write_text(json.dumps(cfg))
    return sdlc


def test_documented_phase_start_cannot_recreate_a_closed_session_heartbeat(tmp_path):
    """A delayed phase gesture is a no-op for loop liveness after its session has ended."""
    sdlc = _sdlc(tmp_path)
    pid = os.getpid()
    generation = loop.session_start(sdlc, pid)
    loop.session_end(sdlc, pid, generation=generation)

    result = _run("start", str(sdlc), "42", "research", "--model", "sonnet",
                  "--pid", str(pid))

    assert result.returncode == 0, result.stderr
    assert not loop._session_marker_path(sdlc, pid).exists()
    assert not loop.session_heartbeat_path(sdlc, pid).exists()


def _write_transcript(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _assistant_line(model="claude-sonnet-5", input_tokens=100, output_tokens=20,
                     cache_read=0, cache_5m=0, cache_1h=0, ts="2026-08-24T18:16:45.403Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": f"msg_test_{next(_MESSAGE_IDS)}",
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": cache_5m,
                    "ephemeral_1h_input_tokens": cache_1h,
                },
            },
        },
    }


# --------------------------------------------------------------------------- Task 1: pricing engine


def test_norm_ts_strips_millis_and_z():
    assert pr.norm_ts("2026-08-24T18:16:45.403Z") == "2026-08-24 18:16:45"


def test_norm_ts_handles_no_millis():
    assert pr.norm_ts("2026-08-24T18:16:45Z") == "2026-08-24 18:16:45"


#: Every column `load_rate_rows` reads by name. A card missing one of these raises `KeyError`
#: INSIDE `load_rate_rows`, so the column check below must run BEFORE it — a crash is not a
#: named failure.
REQUIRED_RATE_COLUMNS = ("model", "rate_kind", "usd_per_mtok", "usd_per_request",
                         "effective_from", "effective_to", "source")


def _assert_rate_card_shape(csv_path):
    """The core's rate card is well-formed enough to price a phase.

    This matters MORE since #2573/S1-G9's degrade fix than it did before it: a missing card used
    to raise `FileNotFoundError` loudly, and now returns `[]` quietly. The non-zero-row assertion
    below is the ONLY thing in CI that notices a card that failed to ship.

    Assertion order is load-bearing — see `REQUIRED_RATE_COLUMNS`.
    """
    with open(csv_path, newline="", encoding="utf-8") as fh:
        cols = csv.DictReader(fh).fieldnames or []
    assert sorted(cols) == sorted(REQUIRED_RATE_COLUMNS), f"rate card columns: {cols}"

    rows = pr.load_rate_rows(csv_path)
    assert rows, f"expected at least one row from {csv_path}"

    kinds = {r["rate_kind"] for r in rows}
    # SUBSET, never equality: the card ships 6 kinds and RATE_KIND_USAGE declares 7 (`web_fetch`
    # is deliberately absent), so `==` would be red against a CORRECT card. Paired with the
    # non-zero-row assertion above, because a subset holds vacuously on an empty card.
    assert kinds <= set(pr.RATE_KIND_USAGE), f"unknown rate_kind(s): {kinds - set(pr.RATE_KIND_USAGE)}"

    # The exact (model, rate_kind, ts) triple `cost_equivalent_tokens` depends on, and the one the
    # recorded CLI fixture prices $12.00 from.
    assert pr.select_rate(rows, "claude-sonnet-5", "input", "2026-08-24 00:00:00") is not None, \
        "reference model claude-sonnet-5/input does not resolve at 2026-08-24"
    return rows


def test_rate_card_shape_of_the_real_csv():
    rows = _assert_rate_card_shape(pr.DEFAULT_RATES_CSV)
    sonnet_input = [r for r in rows if r["model"] == "claude-sonnet-5" and r["rate_kind"] == "input"]
    assert len(sonnet_input) == 2, "sonnet-5 has an intro + standard vintage for input"
    for r in sonnet_input:
        assert isinstance(r["usd_per_mtok"], float)
        assert r["usd_per_request"] is None


#: #2735 (Q1-G6; carries #2669's acceptance): the tier->concrete-id map the rate-card coverage
#: tests below read. Loaded the way tests/test_model_predict.py loads it, NOT imported -- skills do
#: not import each other's Python, and neither does a test pretend they do.
_PREDICT_PY = ROOT / "skills" / "agrim-model" / "scripts" / "predict.py"
_predict_spec = importlib.util.spec_from_file_location("predict", _PREDICT_PY)
predict = importlib.util.module_from_spec(_predict_spec)
_predict_spec.loader.exec_module(predict)

#: The five per-token kinds a REAL Claude Code transcript always carries usage for. `web_search`
#: and `web_fetch` (RATE_KIND_USAGE's per_request kinds) are deliberately absent: a missing request
#: count is 0 units, and `price_units(0, None, ...)` is 0.0 without a rate.
TOKEN_KINDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


def _now_ts():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _full_usage_line(model):
    """One assistant turn with 1M units of every token kind, so every rate is exercised and the
    dollar figure is the plain sum of the five per-MTok list prices."""
    return _assistant_line(model=model, input_tokens=1_000_000, output_tokens=1_000_000,
                           cache_read=1_000_000, cache_5m=1_000_000, cache_1h=1_000_000,
                           ts="2026-09-25T00:00:00Z")


def test_claude_tier_models_cover_exactly_the_tier_set():
    """Every tier in the price order names a concrete id, and nothing else does -- a tier added to
    `_TIER_PRICE_ORDER` without a pricing-coverage id goes red here, not silently unpriced."""
    assert set(predict._CLAUDE_TIER_MODELS) == set(predict._TIER_PRICE_ORDER)


def test_every_claude_tier_model_has_all_token_kinds_effective_now():
    """Every concrete `claude-*` id a tier alias resolves to today has ALL FIVE token kinds
    effective NOW in the shipped card.

    All five, not input/output: `price_turn` poisons the whole turn if ANY kind with nonzero
    units lacks a rate, and real transcripts always carry cache usage, so checking input/output
    alone would be decoration -- green while every real opus turn printed `unavailable`. NOW, on
    the wall clock, on purpose: a rate whose `effective_to` has lapsed is the defect this exists
    to catch (#2735 risk register)."""
    rows = pr.load_rate_rows()
    now = _now_ts()
    gaps = [(model, kind)
            for model in predict._CLAUDE_TIER_MODELS.values()
            for kind in TOKEN_KINDS
            if pr.select_rate(rows, model, kind, now) is None]
    assert not gaps, f"no rate effective at {now} for: {gaps}"


def test_codex_host_model_ids_are_enumerated_and_excluded_as_non_anthropic():
    """The Codex catalog is excluded from the coverage test above BY DESIGN -- this CSV is
    Anthropic list prices and cannot price a `gpt-*` id (phase_report's `codex` branch prints
    `Codex model absent from bundled rate card`). Enumerating the exclusion here keeps it a
    named decision: a `claude-*` id creeping into the Codex catalog, or a Codex id doubling as a
    Claude tier's pricing id, goes red instead of slipping past the coverage test unpriced."""
    codex = ({v["model"] for v in predict._CODEX_HOST_MODELS.values()}
             | set(predict._CODEX_ALLOWED_MODELS))
    assert codex, "Codex catalog is empty -- the exclusion would be vacuous"
    assert not [m for m in codex if m.startswith("claude-")], f"claude-* id in Codex catalog: {codex}"
    assert codex.isdisjoint(predict._CLAUDE_TIER_MODELS.values()), \
        f"Codex id doubles as a Claude tier pricing id: {codex & set(predict._CLAUDE_TIER_MODELS.values())}"


def test_opus_5_5_transcript_prices_to_a_dollar_figure_and_unknown_model_does_not(tmp_path):
    """#2669's acceptance, in-process: an opus-5-5 turn with every token kind prices to a dollar
    figure (4 + 20 + 0.20 + 5 + 8 per MTok), and a model the card does not know still degrades
    to the documented `unavailable` line rather than a silent zero.

    `price_transcript` returns no `source` key and `end_measurements` only prints the `$`/`(model
    not in rate card)` pair for `claude-code[-inline]`, so the render is done the way
    `collect_phase_usage` would hand it over."""
    opus = tmp_path / "opus.jsonl"
    _write_transcript(opus, [_full_usage_line("claude-opus-5-5")])
    result = pr.price_transcript(opus)
    assert result["unpriced_turns"] == 0
    assert result["cost_usd"] == pytest.approx(37.20)
    assert "$37.20" in pr.end_measurements("0s", {"source": "claude-code", **result})

    unknown = tmp_path / "unknown.jsonl"
    _write_transcript(unknown, [_full_usage_line("claude-opus-9-9-unknown")])
    result = pr.price_transcript(unknown)
    assert result["cost_usd"] is None
    assert "cost: unavailable on this host (model not in rate card)" in \
        pr.end_measurements("0s", {"source": "claude-code", **result})


def test_rate_card_missing_file_returns_empty_rows(tmp_path):
    """A missing card degrades to `[]` — `collect_phase_usage`'s "Never raises" docstring is a
    promise this makes true (#2573/S1-G9). It used to raise `FileNotFoundError`, which cost the
    caller the whole of Block B *and* the phase's ledger event."""
    assert pr.load_rate_rows(tmp_path / "nope.csv") == []


def test_rate_card_missing_and_unpriced_reasons_are_distinguishable(tmp_path, monkeypatch):
    """`cost: unavailable on this host (...)` must say WHICH failure — hunting a model name while
    a data file is absent is a support engineer's wasted hour."""
    result = {"source": "claude-code", "cost_usd": None, "tokens_in": 1, "tokens_out": 2,
              "turns": 1, "models": ["claude-sonnet-5"]}
    monkeypatch.setattr(pr, "DEFAULT_RATES_CSV", tmp_path / "absent.csv")
    assert "(rate card missing from this install)" in pr.end_measurements("0s", result)

    present = tmp_path / "present.csv"
    present.write_text("model,rate_kind,usd_per_mtok,usd_per_request,effective_from,effective_to,source\n")
    monkeypatch.setattr(pr, "DEFAULT_RATES_CSV", present)
    assert "(model not in rate card)" in pr.end_measurements("0s", result)


def test_load_rate_rows_empty_effective_to_becomes_none(tmp_path):
    csv_path = tmp_path / "rates.csv"
    csv_path.write_text(
        "model,rate_kind,usd_per_mtok,usd_per_request,effective_from,effective_to,source\n"
        "test-model,input,1.00,,2026-01-01 00:00:00,,test\n"
    )
    rows = pr.load_rate_rows(csv_path)
    assert rows[0]["effective_to"] is None
    assert rows[0]["usd_per_request"] is None


def test_load_rate_rows_web_search_has_no_per_mtok(tmp_path):
    csv_path = tmp_path / "rates.csv"
    csv_path.write_text(
        "model,rate_kind,usd_per_mtok,usd_per_request,effective_from,effective_to,source\n"
        "test-model,web_search,,0.01,2026-01-01 00:00:00,,test\n"
    )
    rows = pr.load_rate_rows(csv_path)
    assert rows[0]["usd_per_mtok"] is None
    assert rows[0]["usd_per_request"] == 0.01


def _rows():
    return pr.load_rate_rows()  # the real CSV — exercises real overlapping vintages


def test_select_rate_picks_intro_vintage_before_cutover():
    rate = pr.select_rate(_rows(), "claude-sonnet-5", "input", "2026-08-24 00:00:00")
    assert rate["usd_per_mtok"] == 2.00


def test_select_rate_picks_standard_vintage_after_cutover():
    rate = pr.select_rate(_rows(), "claude-sonnet-5", "input", "2026-09-02 00:00:00")
    assert rate["usd_per_mtok"] == 2.00


def test_fable_5_1_prices_all_published_components(tmp_path):
    path = tmp_path / "fable.jsonl"
    _write_transcript(path, [_assistant_line(
        model="claude-fable-5-1", input_tokens=1_000_000, output_tokens=1_000_000,
        cache_read=1_000_000, cache_5m=1_000_000, cache_1h=1_000_000,
        ts="2026-09-15T00:00:00Z",
    )])
    result = pr.price_transcript(path)
    assert result["unpriced_turns"] == 0
    assert result["cost_usd"] == 92.75


def test_select_rate_none_for_unknown_model():
    assert pr.select_rate(_rows(), "claude-nonexistent-9", "input", "2026-08-24 00:00:00") is None


def test_price_units_zero_is_free_regardless_of_rate():
    assert pr.price_units(0, None, False) == 0.0


def test_price_units_none_units_is_absent():
    assert pr.price_units(None, {"usd_per_mtok": 3.0, "usd_per_request": None}, False) is None


def test_price_units_no_rate_is_absent():
    assert pr.price_units(100, None, False) is None


def test_price_units_per_mtok_math():
    rate = {"usd_per_mtok": 3.0, "usd_per_request": None}
    assert pr.price_units(1_000_000, rate, False) == 3.0


def test_price_units_per_request_math_no_division():
    rate = {"usd_per_mtok": None, "usd_per_request": 0.01}
    assert pr.price_units(5, rate, True) == 0.05


# --------------------------------------------------------------------------- #2515: cost_equivalent_tokens
#
# The budget-token formula (Decision 1, .sdlc/plans/2515.md): cost_usd anchored to
# REFERENCE_MODEL/REFERENCE_RATE_KIND's own rate, resolved LIVE at the phase's own timestamp —
# never a raw token sum, never a per-phase-model-relative conversion.


def test_cost_equivalent_tokens_anchors_to_reference_model_input_rate():
    # sonnet-5 intro vintage (pre-2026-09-01): $2.00/Mtok input.
    # 12.0 / (2.00 / 1_000_000) = 6_000_000
    result = pr.cost_equivalent_tokens(12.0, _rows(), "2026-08-24 00:00:00")
    assert result == 6_000_000


def test_cost_equivalent_tokens_honors_a_rate_cutover():
    # Anthropic retained the introductory $2/Mtok price on September 1, 2026.
    result = pr.cost_equivalent_tokens(12.0, _rows(), "2026-09-02 00:00:00")
    assert result == 6_000_000


def test_cost_equivalent_tokens_returns_none_when_cost_usd_is_none():
    assert pr.cost_equivalent_tokens(None, _rows(), "2026-08-24 00:00:00") is None


def test_cost_equivalent_tokens_returns_none_when_reference_rate_has_no_coverage():
    # a ts_norm before ANY rate row's effective_from -- no coverage, never a guess or a
    # ZeroDivisionError.
    result = pr.cost_equivalent_tokens(12.0, _rows(), "2000-01-01 00:00:00")
    assert result is None

    # same story with an explicit rate table that has no reference-model rows at all.
    stripped = [r for r in _rows() if r["model"] != pr.REFERENCE_MODEL]
    assert pr.cost_equivalent_tokens(12.0, stripped, "2026-08-24 00:00:00") is None


def test_price_transcript_now_returns_cost_equivalent_tokens(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000, ts="2026-08-24T00:00:00Z"),
        _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000, ts="2026-08-24T00:00:01Z"),
    ])
    result = pr.price_transcript(path)
    # cost_usd == 24.00 (per test_price_transcript_sums_tokens_and_prices_cost), anchored at the
    # FIRST real turn's own ts (2026-08-24T00:00:00Z, pre-cutover, $2.00/Mtok input):
    # 24.0 / (2.00 / 1_000_000) = 12_000_000
    assert result["cost_equivalent_tokens"] == 12_000_000


# --------------------------------------------------------------------------- Task 2: transcript parsing


def test_usage_value_walks_nested_path():
    usage = {"cache_creation": {"ephemeral_5m_input_tokens": 42}}
    assert pr.usage_value(usage, ("cache_creation", "ephemeral_5m_input_tokens")) == 42


def test_usage_value_missing_key_is_none():
    assert pr.usage_value({}, ("input_tokens",)) is None
    assert pr.usage_value({"cache_creation": {}}, ("cache_creation", "ephemeral_5m_input_tokens")) is None


# --------------------------------------------------------------------------- #2558: unpriceable counts


@pytest.mark.parametrize("value", [
    float("inf"), float("-inf"), float("nan"), json.loads("1e400"),
], ids=["inf", "-inf", "nan", "1e400"])
def test_usage_value_non_finite_is_none(value):
    """#2558: `json.loads` accepts `Infinity`, `-Infinity`, `NaN` and an overflowing `1e400` (which
    parses to inf). `int(inf)` raises OverflowError, which `usage_value` did not catch, so one such
    token count crashed `phase_report.py end`. A non-finite count is unpriceable -> None, and the
    turn takes the existing `unpriced` path. The `nan` case was already None (`int(nan)` is a
    ValueError) and is a regression pin, not a change."""
    assert pr.usage_value({"x": value}, ("x",)) is None


@pytest.mark.parametrize("value", [-1, -500_000])
def test_usage_value_negative_is_none(value):
    """#2558: a negative token count cannot be priced honestly -- it would produce a negative
    dollar figure. It is None (unpriceable), never returned as-is. `-0.5` truncates to 0 and is
    out of scope: float truncation is unchanged."""
    assert pr.usage_value({"x": value}, ("x",)) is None


@pytest.mark.parametrize("value, expected", [(0, 0), (42, 42), (10**12, 10**12), (3.0, 3)])
def test_usage_value_valid_ints_unchanged(value, expected):
    assert pr.usage_value({"x": value}, ("x",)) == expected


# --------------------------------------------------------------------------- #2531, third fix
# round: shared safe-accessor helpers (as_str/get_str/get_dict/get_list). Direct unit tests for the
# helpers themselves, on the same "usage_value" model (walk-and-coerce, None on anything that
# doesn't fit, never raise) -- see orchestrator_context_report.py's own test file for the
# end-to-end crash-site regressions these helpers fix.


def test_as_str_accepts_only_non_empty_strings():
    assert pr.as_str("hello") == "hello"
    assert pr.as_str("") is None
    assert pr.as_str(None) is None
    assert pr.as_str(12345) is None
    assert pr.as_str(1.5) is None
    assert pr.as_str(True) is None
    assert pr.as_str(["a", "list"]) is None
    assert pr.as_str({"a": "dict"}) is None


def test_get_str_guards_both_the_container_and_the_values_type():
    assert pr.get_str({"k": "v"}, "k") == "v"
    assert pr.get_str({"k": ""}, "k") is None            # empty string is as unusable as missing
    assert pr.get_str({"k": None}, "k") is None
    assert pr.get_str({"k": 12345}, "k") is None
    assert pr.get_str({"k": ["not", "a", "string"]}, "k") is None
    assert pr.get_str({}, "k") is None                    # missing key
    assert pr.get_str(["not", "a", "dict"], "k") is None  # container itself is the wrong type
    assert pr.get_str(None, "k") is None


def test_get_dict_guards_both_the_container_and_the_values_type():
    assert pr.get_dict({"k": {"nested": 1}}, "k") == {"nested": 1}
    assert pr.get_dict({"k": "not a dict"}, "k") is None
    assert pr.get_dict({"k": ["not", "a", "dict"]}, "k") is None
    assert pr.get_dict({}, "k") is None
    assert pr.get_dict("not a dict either", "k") is None


def test_get_list_guards_both_the_container_and_the_values_type():
    assert pr.get_list({"k": [1, 2, 3]}, "k") == [1, 2, 3]
    assert pr.get_list({"k": "not a list"}, "k") is None
    assert pr.get_list({"k": {"not": "a list"}}, "k") is None
    assert pr.get_list({}, "k") is None
    assert pr.get_list("not a dict either", "k") is None


def test_iter_assistant_turns_skips_non_assistant_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        _assistant_line(),
        {"type": "assistant", "message": {"role": "assistant", "model": "<synthetic>", "usage": {}}},
    ])
    turns = list(pr.iter_assistant_turns(path))
    assert len(turns) == 1
    assert turns[0]["model"] == "claude-sonnet-5"


def test_iter_assistant_turns_survives_a_non_string_timestamp_instead_of_crashing(tmp_path):
    """#2531, third fix round: `timestamp` was only truthiness-guarded (`if not ts: continue`), so
    a TRUTHY non-string value (a JSON number, here -- the reviewer's own repro shape) reached
    `norm_ts`'s `.strip()` and crashed with AttributeError. Reachable from THIS module's own
    `price_transcript`/`cmd_end` path directly, not only additively through
    orchestrator_context_report.py."""
    path = tmp_path / "t.jsonl"
    bad = _assistant_line()
    bad["timestamp"] = 1758189654                    # a JSON number, not a string
    _write_transcript(path, [_assistant_line(), bad])
    turns = list(pr.iter_assistant_turns(path))       # must not raise
    assert len(turns) == 1                            # the malformed-timestamp line is excluded


def test_iter_assistant_turns_message_id_is_none_for_any_non_string_value(tmp_path):
    """`message.get('id')` had no type guard at all -- yielded verbatim, any JSON type.
    orchestrator_context_report.py's dedup_calls uses this value as a dict/set key, so a
    non-hashable id (a JSON list/dict) crashed there with TypeError('unhashable type'). Guarded
    here, at the source, for every current and future consumer -- not only the one caller that
    happened to get the crash reported first."""
    path = tmp_path / "t.jsonl"
    bad = _assistant_line()
    bad["message"]["id"] = ["not", "hashable"]
    _write_transcript(path, [bad])
    turns = list(pr.iter_assistant_turns(path))       # must not raise
    assert len(turns) == 1
    assert turns[0]["message_id"] is None             # sanitized, never the raw unhashable value


def test_iter_assistant_turns_skips_malformed_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('not json\n' + json.dumps(_assistant_line()) + '\n')
    turns = list(pr.iter_assistant_turns(path))
    assert len(turns) == 1


def test_price_transcript_sums_tokens_and_prices_cost(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000, ts="2026-08-24T00:00:00Z"),
        _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000, ts="2026-08-24T00:00:01Z"),
    ])
    result = pr.price_transcript(path)
    assert result["tokens_in"] == 2_000_000
    assert result["tokens_out"] == 2_000_000
    assert result["models"] == ["claude-sonnet-5"]
    assert result["unpriced_turns"] == 0
    # 2 * (1 Mtok @ $2.00 input + 1 Mtok @ $10.00 output) = 2 * 12.00 = 24.00 (intro vintage, pre-2026-09-01)
    assert result["cost_usd"] == 24.00


def test_claude_progressive_usage_lines_count_once_by_message_id(tmp_path):
    """`iter_assistant_turns` itself stays raw/undeduped (one entry per line, #2531 needs this for
    orchestrator_context_report.py's own dedup_calls control test to stay meaningful) -- the dedup
    that keeps a phase's billed cost from double-counting progressive snapshots now lives in
    `_dedup_turns_by_message_id`, used internally by `price_transcript` (#2515)."""
    path = tmp_path / "t.jsonl"
    first = _assistant_line(input_tokens=1_000_000, output_tokens=500,
                            ts="2026-08-24T00:00:00Z")
    last = _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                           ts="2026-08-24T00:00:01Z")
    last["message"]["id"] = first["message"]["id"]
    first["message"]["usage"]["server_tool_use"] = {"web_search_requests": 2}
    _write_transcript(path, [first, last])
    raw_turns = list(pr.iter_assistant_turns(path))
    assert len(raw_turns) == 2                        # raw, undeduped -- one entry per line
    turns = pr._dedup_turns_by_message_id(raw_turns)
    assert len(turns) == 1
    assert turns[0]["ts"] == "2026-08-24 00:00:00"
    assert turns[0]["usage"]["output_tokens"] == 1_000_000
    assert turns[0]["usage"]["server_tool_use"]["web_search_requests"] == 2
    result = pr.price_transcript(path)
    assert result["turns"] == 1
    assert result["tokens_in"] == 1_000_000
    assert result["tokens_out"] == 1_000_000


def test_price_transcript_unknown_model_prices_none(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [_assistant_line(model="claude-nonexistent-9")])
    result = pr.price_transcript(path)
    assert result["tokens_in"] == 100
    assert result["unpriced_turns"] == 1
    assert result["cost_usd"] is None


def test_price_transcript_infinite_count_is_unpriced_not_a_crash(tmp_path):
    """#2558: `_write_transcript` uses `json.dumps`, which writes `float("inf")` as the literal
    `Infinity` -- exactly what `json.loads` reads back as inf. Before the fix this line raised
    `OverflowError: cannot convert float infinity to integer` out of `usage_value`, killing the
    whole `end`. Now the turn is unpriced, never $0 and never a crash."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [_assistant_line(input_tokens=float("inf"))])
    assert "Infinity" in path.read_text()             # non-vacuity: the fixture carries the literal
    result = pr.price_transcript(path)
    assert result["turns"] == 1
    assert result["unpriced_turns"] == 1
    assert result["cost_usd"] is None
    assert result["cost_equivalent_tokens"] is None


def test_price_transcript_negative_count_is_unpriced(tmp_path):
    """#2558: `input_tokens=-500000, output_tokens=20` priced to -0.9998 before the fix (-1 Mtok
    of input @ $2/Mtok plus 20 output tokens), with `unpriced_turns == 0`. A negative count is
    unpriceable: the turn is unpriced and the cost is None, never negative."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [_assistant_line(input_tokens=-500_000, output_tokens=20)])
    result = pr.price_transcript(path)
    assert result["turns"] == 1
    assert result["unpriced_turns"] == 1
    assert result["cost_usd"] is None


def test_price_transcript_infinite_turn_poisons_only_itself(tmp_path):
    """#2558: one priceable 1 Mtok turn (pre-cutover ts, intro $2/Mtok, zero output) beside one
    `Infinity` turn -> the valid turn's $2.00 survives, the bad turn is the one unpriced turn,
    and the whole transcript is neither crashed nor zeroed."""
    path = tmp_path / "t.jsonl"
    valid = _assistant_line(input_tokens=1_000_000, output_tokens=0, ts="2026-08-24T00:00:00Z")
    bad = _assistant_line(input_tokens=float("inf"), output_tokens=0, ts="2026-08-24T00:00:01Z")
    _write_transcript(path, [valid, bad])
    assert "Infinity" in path.read_text()
    result = pr.price_transcript(path)
    assert result["turns"] == 2
    assert result["unpriced_turns"] == 1
    assert result["cost_usd"] == 2.00
    assert result["cost_equivalent_tokens"] is None


def test_price_transcript_empty_file_returns_none(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("")
    assert pr.price_transcript(path) is None


def test_price_transcript_missing_file_returns_none(tmp_path):
    assert pr.price_transcript(tmp_path / "nope.jsonl") is None


def test_price_transcript_web_fetch_without_rate_coverage_poisons_only_that_turn(tmp_path):
    # the rate card (anthropic_list_prices.csv) has NO web_fetch row for any model. A turn that
    # genuinely invoked web_fetch (non-zero, real usage) cannot be honestly priced and must
    # poison ONLY that turn (the real all-or-nothing rule) -- never silently absorbed
    # as if it cost $0, and never allowed to zero out the WHOLE transcript's cost either.
    path = tmp_path / "t.jsonl"
    turn_no_fetch = _assistant_line(input_tokens=1_000_000, output_tokens=0, ts="2026-08-24T00:00:00Z")
    turn_with_fetch = _assistant_line(input_tokens=1_000_000, output_tokens=0, ts="2026-08-24T00:00:01Z")
    turn_with_fetch["message"]["usage"]["server_tool_use"] = {"web_fetch_requests": 1}
    _write_transcript(path, [turn_no_fetch, turn_with_fetch])
    result = pr.price_transcript(path)
    assert result["tokens_in"] == 2_000_000        # both turns' tokens still counted honestly
    assert result["unpriced_turns"] == 1
    assert result["turns"] == 2
    assert result["cost_usd"] == 2.00               # only the priceable turn's $2.00 (1 Mtok @ intro $2/Mtok)
    assert result["cost_equivalent_tokens"] is None  # partial dollars cannot enforce a hard budget


def test_price_transcript_tool_never_invoked_is_a_confirmed_zero_not_a_poison(tmp_path):
    # A turn that never called web_search/web_fetch at all has NO `server_tool_use` key in its
    # usage dict (confirmed against a real transcript) -- that must price as a confirmed $0 for
    # that kind, never poison the turn just because the key is absent.
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [_assistant_line(input_tokens=1_000_000, output_tokens=0,
                                              ts="2026-08-24T00:00:00Z")])
    result = pr.price_transcript(path)
    assert result["unpriced_turns"] == 0
    assert result["cost_usd"] == 2.00


def test_price_transcript_since_ts_excludes_earlier_turns(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_line(input_tokens=500_000, output_tokens=0, ts="2026-08-23T00:00:00Z"),
        _assistant_line(input_tokens=1_000_000, output_tokens=0, ts="2026-08-24T00:00:00Z"),
    ])
    result = pr.price_transcript(path, since_ts="2026-08-24T00:00:00Z")
    assert result["turns"] == 1
    assert result["tokens_in"] == 1_000_000


# --------------------------------------------------------------------------- Task 3: host discovery


def test_find_claude_session_dir_found(tmp_path):
    home = tmp_path
    session_dir = home / ".claude" / "projects" / "-some-slug" / "sess-123"
    session_dir.mkdir(parents=True)
    found = pr.find_claude_session_dir("sess-123", home=home)
    assert found == session_dir


def test_find_claude_session_dir_not_found(tmp_path):
    assert pr.find_claude_session_dir("sess-nope", home=tmp_path) is None


def test_find_claude_agent_transcript_found(tmp_path):
    home = tmp_path
    subagents = home / ".claude" / "projects" / "-slug" / "sess-1" / "subagents"
    subagents.mkdir(parents=True)
    target = subagents / "agent-abc123.jsonl"
    target.write_text(json.dumps(_assistant_line()) + "\n")
    found = pr.find_claude_agent_transcript("sess-1", "abc123", home=home)
    assert found == target


def test_find_claude_agent_transcript_missing_session(tmp_path):
    assert pr.find_claude_agent_transcript("no-such-session", "abc123", home=tmp_path) is None


def test_find_claude_main_transcript_found(tmp_path):
    home = tmp_path
    proj_dir = home / ".claude" / "projects" / "-slug"
    proj_dir.mkdir(parents=True)
    target = proj_dir / "sess-1.jsonl"
    target.write_text(json.dumps(_assistant_line()) + "\n")
    found = pr.find_claude_main_transcript("sess-1", home=home)
    assert found == target


def test_find_claude_main_transcript_not_found(tmp_path):
    assert pr.find_claude_main_transcript("no-such-session", home=tmp_path) is None


# --------------------------------------------------------------------------- PR review, 2026-08-25:
# BLOCKING path-traversal finding. session_id/agent_id reached a filesystem join with zero
# validation -- a crafted CLAUDE_CODE_SESSION_ID could escape ~/.claude/projects/ entirely and
# have its content written into the goal's real phase ledger event as fabricated tokens/cost.
# Each test below plants a real "secret" file OUTSIDE the fake home and proves the attack path is
# now closed -- reproducing the reviewer's own executed finding as a permanent regression guard.


def test_find_claude_session_dir_rejects_path_traversal(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "-slug").mkdir(parents=True)
    outside = tmp_path / "outside_secret_dir"
    outside.mkdir()
    assert pr.find_claude_session_dir("../../../outside_secret_dir", home=home) is None
    assert pr.find_claude_session_dir("/etc", home=home) is None


def test_find_claude_agent_transcript_rejects_path_traversal(tmp_path):
    home = tmp_path / "home"
    session_dir = home / ".claude" / "projects" / "-slug" / "sess-1"
    (session_dir / "subagents").mkdir(parents=True)
    outside = tmp_path / "outside_secret.jsonl"
    outside.write_text(json.dumps(_assistant_line(input_tokens=50_000_000, output_tokens=50_000_000)) + "\n")
    # A crafted agent-id that would otherwise walk out of subagents/ and read the planted file.
    assert pr.find_claude_agent_transcript("sess-1", "../../../../outside_secret", home=home) is None


def test_find_claude_main_transcript_rejects_path_traversal(tmp_path):
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "-slug").mkdir(parents=True)
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "planted.jsonl").write_text(json.dumps(_assistant_line()) + "\n")
    assert pr.find_claude_main_transcript("../outside_secret/planted", home=home) is None


def test_cli_end_with_crafted_session_id_does_not_fabricate_a_ledger_entry(tmp_path):
    # Reproduces the reviewer's exact executed attack end to end through the real CLI: a file
    # planted completely outside the fake HOME must never be read, and no fabricated tokens must
    # ever land in the goal's real ledger event.
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "-slug").mkdir(parents=True)
    outside = tmp_path / "outside_secret"
    outside.mkdir()
    (outside / "planted.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=50_000_000, output_tokens=50_000_000,
                                    model="claude-opus-5")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "../outside_secret/planted"}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "opus"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    assert "50000000" not in result.stdout
    assert "cost: unavailable on this host" in result.stdout

    ledger = _mod("ledger")
    events = journal_events(ledger, sdlc)
    phase_events = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
                    and e.get("state") == "end"]
    assert len(phase_events) == 1
    assert "tokens_in" not in phase_events[0]      # no fabricated count ever reached the ledger


def test_find_codex_token_totals_parses_rollout_fixture(tmp_path):
    home = tmp_path
    session_id = "01a03637-7800-7000-8000-000000000001"
    sess_dir = home / ".codex" / "sessions" / "2026" / "08" / "25"
    sess_dir.mkdir(parents=True)
    rollout = sess_dir / f"rollout-2026-08-25T00-00-00-{session_id}.jsonl"
    with open(rollout, "w") as fh:
        fh.write(json.dumps({"type": "session_meta", "payload": {"model_provider": "openai"}}) + "\n")
        fh.write(json.dumps({"type": "token_count", "timestamp": "2026-08-25T00:00:10Z",
                              "payload": {"input_tokens": 500, "output_tokens": 50}}) + "\n")
        fh.write(json.dumps({"type": "token_count", "timestamp": "2026-08-25T00:00:20Z",
                              "payload": {"input_tokens": 300, "output_tokens": 30}}) + "\n")
    totals = pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=home,
                                        session_id=session_id)
    assert totals == {"tokens_in": 800, "tokens_out": 80, "models": [],
                      "unknown_model_turns": 2}


def test_codex_retry_credits_growing_usage_and_refuses_unknown_model_turn(tmp_path):
    sdlc = _sdlc(tmp_path, {"budget": {"max_codex_raw_tokens": 60}})
    parent = "01a03637-7800-7000-8000-000000000001"
    child = "01a03637-7800-7000-8000-000000000002"
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sessions.mkdir(parents=True)
    rollout = sessions / f"rollout-2026-08-25T00-00-00-{child}.jsonl"
    _write_transcript(rollout, [
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 50, "output_tokens": 5}}},
    ])
    pr.write_marker(sdlc, "42", "research", "sonnet", now=1787616000,
                    host_model="gpt-5.6-sol", expect_agent_id=True)
    env = {**os.environ, "HOME": str(tmp_path), "CODEX_THREAD_ID": parent,
           "CLAUDE_CODE_SESSION_ID": ""}
    command = [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42",
               "research", "--agent-id", child]
    first = subprocess.run(command, capture_output=True, text=True, env=env)
    assert first.returncode == 2
    assert _mod("state").load_cursor(sdlc)["run_codex_raw_tokens"] == 55
    with rollout.open("a") as handle:
        handle.write(json.dumps({"type": "turn_context", "timestamp": "2026-08-25T00:00:11Z",
                                 "payload": {"model": "gpt-5.6-sol"}}) + "\n")
        handle.write(json.dumps({"type": "token_usage_record",
                                 "timestamp": "2026-08-25T00:00:20Z",
                                 "payload": {"usage": {"input_tokens": 20,
                                                       "output_tokens": 2}}}) + "\n")
    retried = subprocess.run(command, capture_output=True, text=True, env=env)
    assert retried.returncode == 2  # first request still has no observed model
    assert _mod("state").load_cursor(sdlc)["run_codex_raw_tokens"] == 77
    assert "model unverified/mismatched" in retried.stderr
    ledger = _mod("ledger")
    assert not [e for e in journal_events(ledger, sdlc)
                if e.get("kind") == "phase" and e.get("state") == "end"]


def test_find_codex_token_totals_no_codex_dir_returns_none(tmp_path):
    assert pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=tmp_path,
                                      session_id="abc") is None


def test_codex_unsupported_id_does_not_scan_saved_rollout_history(tmp_path, monkeypatch):
    sessions = tmp_path / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    def unbounded_scan(*args, **kwargs):
        raise AssertionError("unbounded rollout scan")
    monkeypatch.setattr(pathlib.Path, "rglob", unbounded_scan)
    assert pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=tmp_path,
                                      session_id="a-valid-but-non-v7-id") is None


def test_codex_partial_duplicate_stream_does_not_claim_complete_usage(tmp_path):
    session_id = "01a03637-7800-7000-8000-000000000001"
    sess_dir = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sess_dir.mkdir(parents=True)
    rollout = sess_dir / f"rollout-2026-08-25T00-00-00-{session_id}.jsonl"
    _write_transcript(rollout, [
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 100, "output_tokens": 10}}},
        {"type": "event_msg", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"type": "token_count", "info": {"last_token_usage": {
             "input_tokens": 100, "output_tokens": 10}}}},
        {"type": "event_msg", "timestamp": "2026-08-25T00:00:20Z",
         "payload": {"type": "token_count", "info": {"last_token_usage": {
             "input_tokens": 200, "output_tokens": 20}}}},
    ])
    assert pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=tmp_path,
                                      session_id=session_id) is None


def test_codex_stale_repeated_event_is_counted_once(tmp_path):
    session_id = "01a03637-7800-7000-8000-000000000001"
    sess_dir = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sess_dir.mkdir(parents=True)
    rollout = sess_dir / f"rollout-2026-08-25T00-00-00-{session_id}.jsonl"
    def record(tin, tout, second):
        return {"type": "token_usage_record", "timestamp": f"2026-08-25T00:00:{second:02d}Z",
                "payload": {"usage": {"input_tokens": tin, "output_tokens": tout}}}
    def event(tin, tout, total_in, total_out, second):
        return {"type": "event_msg", "timestamp": f"2026-08-25T00:00:{second:02d}Z",
                "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": tin, "output_tokens": tout},
                    "total_token_usage": {"input_tokens": total_in,
                                          "output_tokens": total_out}}}}
    _write_transcript(rollout, [record(100, 10, 1), event(100, 10, 100, 10, 1),
                                event(100, 10, 100, 10, 2),  # stale repeat
                                record(100, 10, 3), event(100, 10, 200, 20, 3)])
    assert pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=tmp_path,
                                      session_id=session_id) == {
                                          "tokens_in": 200, "tokens_out": 20, "models": [],
                                          "unknown_model_turns": 2}
    # A new request can have the same request-sized pair; its cumulative total advances.
    _write_transcript(rollout, [record(100, 10, 1), event(100, 10, 100, 10, 1),
                                event(100, 10, 200, 20, 2)])
    assert pr.find_codex_token_totals("2026-08-25T00:00:00Z", home=tmp_path,
                                      session_id=session_id) is None


def test_collect_phase_usage_codex_counts_only_selected_rollout_and_request_deltas(tmp_path):
    # A real Codex rollout writes both records for each request. `usage` and
    # `info.last_token_usage` are request deltas; turn/thread totals are cumulative.
    session_id = "01a03637-7800-7000-8000-000000000001"
    other_id = "01a03637-7800-7000-8000-000000000002"
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sessions.mkdir(parents=True)
    selected = sessions / f"rollout-2026-08-25T00-00-00-{session_id}.jsonl"
    other = sessions / f"rollout-2026-08-25T00-00-00-{other_id}.jsonl"
    _write_transcript(selected, [
        {"type": "session_meta", "payload": {"id": session_id}},
        {"type": "turn_context", "timestamp": "2026-08-25T00:00:01Z",
         "payload": {"model": "gpt-5.6-sol"}},
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:05Z",
         "payload": {"usage": {"input_tokens": 1000, "output_tokens": 100},
                     "turn_token_usage": {"input_tokens": 1000, "output_tokens": 100},
                     "thread_token_usage": {"input_tokens": 1000, "output_tokens": 100}}},
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 500, "output_tokens": 50},
                     "turn_token_usage": {"input_tokens": 1500, "output_tokens": 150},
                     "thread_token_usage": {"input_tokens": 1500, "output_tokens": 150}}},
        {"type": "event_msg", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"input_tokens": 500, "output_tokens": 50},
             "total_token_usage": {"input_tokens": 1500, "output_tokens": 150}}}},
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:20Z",
         "payload": {"usage": {"input_tokens": 300, "output_tokens": 30},
                     "turn_token_usage": {"input_tokens": 1800, "output_tokens": 180},
                     "thread_token_usage": {"input_tokens": 1800, "output_tokens": 180}}},
        {"type": "event_msg", "timestamp": "2026-08-25T00:00:20Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"input_tokens": 300, "output_tokens": 30},
             "total_token_usage": {"input_tokens": 1800, "output_tokens": 180}}}},
    ])
    _write_transcript(other, [
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:15Z",
         "payload": {"usage": {"input_tokens": 9999, "output_tokens": 999}}},
    ])

    result = pr.collect_phase_usage("", None, "2026-08-25T00:00:08Z", home=tmp_path,
                                    codex_session_id=session_id)
    assert result["source"] == "codex"
    assert (result["tokens_in"], result["tokens_out"]) == (800, 80)
    assert result["models"] == ["gpt-5.6-sol"]
    assert result["cost_usd"] is None

    agent_result = pr.collect_phase_usage("", other_id, "2026-08-25T00:00:08Z",
                                          home=tmp_path, codex_session_id=session_id)
    assert (agent_result["tokens_in"], agent_result["tokens_out"]) == (9999, 999)

    unscoped = pr.collect_phase_usage("", None, "2026-08-25T00:00:08Z", home=tmp_path)
    assert unscoped["source"] == "unavailable"
    claude_scoped = pr.collect_phase_usage("sess-1", None, "2026-08-25T00:00:08Z",
                                           home=tmp_path, codex_session_id=session_id)
    assert claude_scoped["source"] == "unavailable"


def test_collect_phase_usage_claude_code_path(tmp_path):
    home = tmp_path
    subagents = home / ".claude" / "projects" / "-slug" / "sess-1" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-xyz.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                                    ts="2026-08-24T00:00:00Z")) + "\n"
    )
    result = pr.collect_phase_usage("sess-1", "xyz", "2026-08-24T00:00:00Z", home=home)
    assert result["source"] == "claude-code"
    assert result["tokens_in"] == 1_000_000
    assert result["cost_usd"] == 12.00


def test_collect_phase_usage_no_agent_id_falls_back_to_windowed_main_transcript(tmp_path):
    # The inline-phase path (no dispatched subagent -- e.g. model_selection: off, the shipped
    # default): the caller's own top-level session file, windowed by since_ts, still prices.
    home = tmp_path
    proj_dir = home / ".claude" / "projects" / "-slug"
    proj_dir.mkdir(parents=True)
    main = proj_dir / "sess-1.jsonl"
    _write_transcript(main, [
        _assistant_line(input_tokens=500_000, output_tokens=0, ts="2026-08-23T00:00:00Z"),  # earlier phase, excluded
        _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000, ts="2026-08-24T00:00:00Z"),
    ])
    result = pr.collect_phase_usage("sess-1", None, "2026-08-24T00:00:00Z", home=home)
    assert result["source"] == "claude-code-inline"
    assert result["tokens_in"] == 1_000_000        # the earlier, out-of-window turn is excluded
    assert result["cost_usd"] == 12.00


def test_collect_phase_usage_no_agent_id_and_no_main_transcript_is_unavailable(tmp_path):
    # The genuinely irreducible gap: caller is itself a nested subagent with no top-level file.
    result = pr.collect_phase_usage("sess-1", None, "2026-08-24T00:00:00Z", home=tmp_path)
    assert result["source"] == "unavailable"
    assert "inline" in result["reason"]


def test_collect_phase_usage_no_source_found_is_unavailable(tmp_path):
    # The Cursor / "run the control" case: an empty HOME with neither a Claude Code nor a Codex
    # transcript store present at all.
    result = pr.collect_phase_usage("sess-1", "xyz", "2026-08-24T00:00:00Z", home=tmp_path)
    assert result["source"] == "unavailable"
    assert result["reason"]


def test_collect_phase_usage_codex_never_prices_cost(tmp_path):
    home = tmp_path
    session_id = "01a03637-7800-7000-8000-000000000001"
    sess_dir = home / ".codex" / "sessions" / "2026" / "08" / "25"
    sess_dir.mkdir(parents=True)
    rollout = sess_dir / f"rollout-2026-08-25T00-00-00-{session_id}.jsonl"
    with open(rollout, "w") as fh:
        fh.write(json.dumps({"type": "token_count", "timestamp": "2026-08-25T00:00:10Z",
                              "payload": {"input_tokens": 500, "output_tokens": 50}}) + "\n")
    result = pr.collect_phase_usage("", None, "2026-08-25T00:00:00Z", home=home,
                                    codex_session_id=session_id)
    assert result["source"] == "codex"
    assert result["tokens_in"] == 500
    assert result.get("cost_usd") is None


def test_end_codex_uses_current_thread_and_event_delta_when_record_absent(tmp_path):
    sdlc = _sdlc(tmp_path)
    parent_id = "01a03637-7800-7000-8000-000000000001"
    thread_id = "01a03637-7800-7000-8000-000000000002"
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sessions.mkdir(parents=True)
    _write_transcript(sessions / f"rollout-2026-08-25T00-00-00-{parent_id}.jsonl", [
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 9000, "output_tokens": 900}}},
    ])
    _write_transcript(sessions / f"rollout-2026-08-25T00-00-00-{thread_id}.jsonl", [
        {"type": "turn_context", "timestamp": "2026-08-25T00:00:01Z",
         "payload": {"model": "gpt-5.6-sol"}},
        {"type": "event_msg", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"type": "token_count", "info": {
             "last_token_usage": {"input_tokens": 5, "output_tokens": 2},
             "total_token_usage": {"input_tokens": 5000, "output_tokens": 2000}}}},
    ])
    pr.write_marker(sdlc, "42", "research", "", now=1787616000,
                    pid=os.getpid(), codex_thread_id=thread_id)
    env = {**os.environ, "HOME": str(tmp_path), "CODEX_SESSION_ID": parent_id,
           "CODEX_THREAD_ID": thread_id, "CLAUDE_CODE_SESSION_ID": ""}

    ended = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--pid", str(os.getpid())],
        capture_output=True, text=True, env=env,
    )
    assert ended.returncode == 0
    assert "tokens 5 in, 2 out" in ended.stdout
    assert "gpt-5.6-sol" in ended.stdout
    assert "cost: unavailable on this host" in ended.stdout
    ledger = _mod("ledger")
    phase_ends = [event for event in journal_events(ledger, sdlc)
                  if event.get("kind") == "phase" and event.get("state") == "end"]
    assert len(phase_ends) == 1
    assert (phase_ends[0]["tokens_in"], phase_ends[0]["tokens_out"]) == ("5", "2")
    state = _mod("state")
    assert state.load_cursor(sdlc)["run_codex_raw_tokens"] == 7
    assert state.load_cursor(sdlc)["run_tokens"] == 0
    repeated = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--pid", str(os.getpid())],
        capture_output=True, text=True, env=env,
    )
    assert repeated.returncode == 0
    assert state.load_cursor(sdlc)["run_codex_raw_tokens"] == 7
    pr.write_marker(sdlc, "42", "research", "sonnet", now=1787616001,
                    host_model="gpt-5.6-terra", pid=os.getpid(), codex_thread_id=thread_id)
    mismatched = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--pid", str(os.getpid())],
        capture_output=True, text=True, env=env,
    )
    assert mismatched.returncode == 2
    assert "model unverified/mismatched" in mismatched.stderr
    # Wrong-model tokens were consumed and remain budgeted. A phase-end event is a physical
    # boundary, not a success verdict; the nonzero exit is the model gate.
    assert state.load_cursor(sdlc)["run_codex_raw_tokens"] == 14
    ends_after_mismatch = [event for event in journal_events(ledger, sdlc)
                           if event.get("kind") == "phase" and event.get("state") == "end"]
    assert len(ends_after_mismatch) == 2


def test_codex_documented_phase_start_requires_and_records_actual_host_model(tmp_path):
    sdlc = _sdlc(tmp_path)
    env = {**os.environ, "CODEX_THREAD_ID": "01a03637-7800-7000-8000-000000000002",
           "CLAUDE_CODE_SESSION_ID": ""}
    base = [sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
            "research", "--model", "sonnet", "--expect-agent-id"]
    missing = subprocess.run(base, capture_output=True, text=True, env=env)
    assert missing.returncode == 2
    assert pr.read_marker(sdlc, "42") is None
    documented = subprocess.run(base + ["--host-model", "gpt-5.6-sol"],
                                capture_output=True, text=True, env=env)
    assert documented.returncode == 0
    assert pr.read_marker(sdlc, "42")["host_model"] == "gpt-5.6-sol"


def test_codex_ceiling_refuses_phase_end_without_measured_rollout(tmp_path):
    sdlc = _sdlc(tmp_path, {"budget": {"max_codex_raw_tokens": 100}})
    pr.write_marker(sdlc, "42", "research", "sonnet", now=1787616000,
                    host_model="gpt-5.6-sol", expect_agent_id=True)
    env = {**os.environ, "HOME": str(tmp_path),
           "CODEX_THREAD_ID": "01a03637-7800-7000-8000-000000000001",
           "CLAUDE_CODE_SESSION_ID": ""}
    ended = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "01a03637-7800-7000-8000-000000000002"],
        capture_output=True, text=True, env=env,
    )
    assert ended.returncode == 2
    assert "cannot be enforced" in ended.stderr or "model unverified" in ended.stderr
    assert _mod("state").load_cursor(sdlc)["run_codex_raw_tokens"] == 0
    ledger = _mod("ledger")
    assert not [e for e in journal_events(ledger, sdlc)
                if e.get("kind") == "phase" and e.get("state") == "end"]


def test_codex_agent_id_gesture_prints_own_thread_id_only():
    own_id = "01a03637-7800-7000-8000-000000000002"
    parent_id = "01a03637-7800-7000-8000-000000000001"
    env = {**os.environ, "CODEX_SESSION_ID": parent_id, "CODEX_THREAD_ID": own_id,
           "CLAUDE_CODE_SESSION_ID": ""}
    result = subprocess.run([sys.executable, str(S / "phase_report.py"), "codex-agent-id"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0
    assert result.stdout == own_id + "\n"
    assert result.stderr == ""


def test_codex_agent_id_gesture_refuses_missing_or_invalid_thread_id():
    for thread_id in ("", "../other", "not-a-uuid"):
        env = {**os.environ, "CODEX_SESSION_ID": "01a03637-7800-7000-8000-000000000001",
               "CODEX_THREAD_ID": thread_id, "CLAUDE_CODE_SESSION_ID": "claude-session"}
        result = subprocess.run([sys.executable, str(S / "phase_report.py"), "codex-agent-id"],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 2
        assert result.stdout == ""
        assert "CODEX_THREAD_ID" in result.stderr


def test_end_refuses_parent_codex_usage_when_dispatched_agent_id_expected(tmp_path):
    sdlc = _sdlc(tmp_path)
    parent_id = "01a03637-7800-7000-8000-000000000001"
    child_id = "01a03637-7800-7000-8000-000000000002"
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "08" / "25"
    sessions.mkdir(parents=True)
    _write_transcript(sessions / f"rollout-2026-08-25T00-00-00-{parent_id}.jsonl", [
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 9000, "output_tokens": 900}}},
    ])
    _write_transcript(sessions / f"rollout-2026-08-25T00-00-00-{child_id}.jsonl", [
        {"type": "turn_context", "timestamp": "2026-08-25T00:00:01Z",
         "payload": {"model": "gpt-5.6-sol"}},
        {"type": "token_usage_record", "timestamp": "2026-08-25T00:00:10Z",
         "payload": {"usage": {"input_tokens": 7, "output_tokens": 3}}},
    ])
    env = {**os.environ, "HOME": str(tmp_path), "CODEX_SESSION_ID": parent_id,
           "CODEX_THREAD_ID": parent_id, "CLAUDE_CODE_SESSION_ID": ""}
    start = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42", "research",
         "--expect-agent-id", "--host-model", "gpt-5.6-sol"],
        capture_output=True, text=True, env=env,
    )
    assert start.returncode == 0
    marker_path = pr.marker_path(sdlc, "42")
    marker = json.loads(marker_path.read_text())
    assert marker["expect_agent_id"] is True
    marker["ts_start"] = "2026-08-25T00:00:00Z"
    marker_path.write_text(json.dumps(marker))

    for extra in ([], ["--agent-id", "../other"]):
        ended = subprocess.run(
            [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
             *extra], capture_output=True, text=True, env=env,
        )
        assert ended.returncode == 2
        assert "cost: unavailable on this host" in ended.stdout
        assert "agent ID" in ended.stdout
        assert "tokens 9,000" not in ended.stdout
    child_end = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", child_id], capture_output=True, text=True, env=env,
    )
    assert child_end.returncode == 0
    assert "tokens 7 in, 3 out" in child_end.stdout
    ledger = _mod("ledger")
    phase_ends = [event for event in journal_events(ledger, sdlc)
                  if event.get("kind") == "phase" and event.get("state") == "end"]
    assert len(phase_ends) == 3  # ends are boundaries, including two unmeasured refusals
    assert all("tokens_in" not in event for event in phase_ends[:2])
    assert (phase_ends[2]["tokens_in"], phase_ends[2]["tokens_out"]) == ("7", "3")


def test_claude_observed_model_mismatch_refuses_gate_but_records_boundary(tmp_path):
    sdlc = _sdlc(tmp_path)
    agent_file = (tmp_path / ".claude" / "projects" / "-slug" / "sess-1" /
                  "subagents" / "agent-abc123.jsonl")
    _write_transcript(agent_file, [_assistant_line(model="claude-opus-5",
                                                    ts="2026-08-25T00:00:10Z")])
    pr.write_marker(sdlc, "42", "research", "sonnet", now=1787616000)
    env = {**os.environ, "HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": "sess-1",
           "CODEX_THREAD_ID": ""}
    ended = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc123"], capture_output=True, text=True, env=env,
    )
    assert ended.returncode == 2
    assert "expected 'sonnet'" in ended.stderr
    ledger = _mod("ledger")
    assert len([e for e in journal_events(ledger, sdlc)
                if e.get("kind") == "phase" and e.get("state") == "end"]) == 1


def test_expect_agent_id_marker_does_not_change_claude_inline_usage(tmp_path):
    sdlc = _sdlc(tmp_path)
    main = tmp_path / ".claude" / "projects" / "-slug" / "sess-1.jsonl"
    _write_transcript(main, [_assistant_line(input_tokens=100, output_tokens=20,
                                              ts="2026-08-25T00:00:10Z")])
    pr.write_marker(sdlc, "42", "research", "sonnet", now=1787616000,
                    expect_agent_id=True, pid=os.getpid())
    env = {**os.environ, "HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": "sess-1",
           "CODEX_SESSION_ID": "01a03637-7800-7000-8000-000000000001",
           "CODEX_THREAD_ID": "01a03637-7800-7000-8000-000000000001"}
    ended = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--pid", str(os.getpid())],
        capture_output=True, text=True, env=env,
    )
    assert ended.returncode == 0
    assert "tokens 100 in, 20 out" in ended.stdout
    assert "claude-sonnet-5 (inline, windowed)" in ended.stdout


def test_flags_name_equals_value_form():
    assert pr._flags(["--model=sonnet"]) == {"model": "sonnet"}


def test_flags_rejects_whitespace_bearing_name_before_equals():
    # loop.py's own #541 guard: a name is never legitimately whitespace-bearing -- that shape is
    # always leaked prose from a value the parser failed to consume, dropped rather than kept as
    # a nonsense key.
    assert pr._flags(["--some thing=value"]) == {}


def test_flags_value_flag_consumes_a_value_starting_with_dashes():
    assert pr._flags(["--agent-id", "--looks-like-a-flag"]) == {"agent-id": "--looks-like-a-flag"}


# --------------------------------------------------------------------------- Task 4: CLI verbs


def test_marker_round_trip(tmp_path):
    sdlc = _sdlc(tmp_path)
    written = pr.write_marker(sdlc, "42", "research", "sonnet", now=1_000_000.0)
    read_back = pr.read_marker(sdlc, "42")
    assert read_back == written
    assert read_back["phase"] == "research"
    assert read_back["model"] == "sonnet"


def test_read_marker_missing_returns_none(tmp_path):
    sdlc = _sdlc(tmp_path)
    assert pr.read_marker(sdlc, "no-such-goal") is None


def test_cli_start_prints_banner_and_writes_marker(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("start", str(sdlc), "42", "research", "--model", "sonnet")
    assert result.returncode == 0
    assert "PHASE START" in result.stdout
    # #2100 changed HOW the phase is named, not whether it is: the bare `research` became the
    # `P2 RESEARCH` token `docs/output-contract.md` §2 defines, which is the same fact in the
    # vocabulary the contract already required of every status line. #2112 left this line alone --
    # a start is an announcement, not a boundary, and never became a Block B.
    assert "P2 RESEARCH" in result.stdout
    assert "sonnet" in result.stdout
    assert pr.read_marker(sdlc, "42") is not None


def test_cli_start_rejects_unknown_phase(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("start", str(sdlc), "42", "not_a_real_phase", "--model", "sonnet")
    assert result.returncode == 2
    assert "unknown phase" in result.stderr
    assert pr.read_marker(sdlc, "42") is None       # nothing written on a rejected phase


def test_cli_end_rejects_unknown_phase(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("end", str(sdlc), "42", "not_a_real_phase")
    assert result.returncode == 2
    assert "unknown phase" in result.stderr


def test_cli_end_with_missing_marker_warns_on_stderr_but_still_proceeds(tmp_path, monkeypatch):
    sdlc = _sdlc(tmp_path)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(empty_home), "CLAUDE_CODE_SESSION_ID": "sess-none"},
    )
    assert result.returncode == 0
    assert "no phase-start marker found" in result.stderr
    assert "cost: unavailable on this host" in result.stdout


def test_cli_end_with_no_agent_id_and_no_transcript_prints_unavailable(tmp_path, monkeypatch):
    sdlc = _sdlc(tmp_path)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sess-none")
    _run("start", str(sdlc), "42", "research", "--model", "sonnet")
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(empty_home), "CLAUDE_CODE_SESSION_ID": "sess-none"},
    )
    assert result.returncode == 0
    assert "cost: unavailable on this host" in result.stdout


def test_cli_end_with_real_transcript_prints_cost_and_writes_ledger_event(tmp_path, monkeypatch):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-1"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    partial = _assistant_line(input_tokens=1_000_000, output_tokens=0,
                              ts="2026-08-24T00:00:00Z")
    complete = _assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                               ts="2026-08-24T00:00:01Z")
    complete["message"]["id"] = partial["message"]["id"]
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(partial) + "\n" + json.dumps(complete) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0
    assert "$12.00" in result.stdout
    assert "claude-sonnet-5" in result.stdout

    ledger = _mod("ledger")
    events = journal_events(ledger, sdlc)
    phase_events = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
                    and e.get("state") == "end"]
    assert len(phase_events) == 1
    assert phase_events[0]["tokens_in"] == "1000000"
    assert phase_events[0]["tokens_out"] == "1000000"


def test_cli_end_with_infinite_usage_prints_unavailable_and_exits_zero(tmp_path):
    """#2558, the documented gesture: the real `phase_report.py end --agent-id` CLI over a subagent
    transcript whose one turn carries `"input_tokens": Infinity`. Before the fix the process died
    with `OverflowError: cannot convert float infinity to integer` (rc=1, no Block B at all). Now it
    exits 0 and prints the documented `cost: unavailable on this host` line -- a measurement that
    cannot be made is a fact to print, never a reason to lose the boundary."""
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-2558"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    line = _assistant_line(input_tokens=float("inf"), output_tokens=0, ts="2026-08-24T00:00:00Z")
    transcript = subagents / "agent-abc.jsonl"
    transcript.write_text(json.dumps(line) + "\n")
    assert "Infinity" in transcript.read_text()
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id,
           "CODEX_THREAD_ID": ""}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "OverflowError" not in result.stderr
    assert "cost: unavailable on this host" in result.stdout


@pytest.mark.parametrize("model, expected", [
    ("claude-opus-5-5", "$37.20"),
    ("claude-opus-9-9-unknown", "cost: unavailable on this host (model not in rate card)"),
])
def test_cli_end_prices_opus_5_5_and_refuses_unknown_model(tmp_path, model, expected):
    """#2669's gesture, verbatim: the real `phase_report.py end` CLI over a subagent transcript.
    An opus-5-5 transcript prices to a dollar figure; an unknown model prints the documented
    `unavailable` line, not a zero and not a crash. Both exit 0 -- a cost that cannot be measured
    is a fact to print, never a reason to lose Block B."""
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-2735"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(json.dumps(_full_usage_line(model)) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id,
           "CODEX_THREAD_ID": ""}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "opus"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    # The concrete observed id, never the bare word "model" (it appears in the unavailable line).
    assert model in result.stdout


def test_cli_end_with_real_transcript_also_writes_the_spend_event(tmp_path, monkeypatch):
    # #1686: phase_report.py priced tokens_in/tokens_out into the `phase` event above (already
    # covered by the sibling test) but never called the already-built `spend` kind
    # (ledger.EVENT_FIELDS["spend"] = ("phase", "model", "tokens_in", "tokens_out", "cost_cents")),
    # the only kind that carries the observed model + a dollar cost at all. A real, measured phase
    # end must write BOTH ledger events, not just `phase`.
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-2"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                                    ts="2026-08-24T00:00:00Z")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0

    ledger = _mod("ledger")
    events = journal_events(ledger, sdlc)
    spend_events = [e for e in events if e.get("kind") == "spend" and e.get("goal") == "42"]
    assert len(spend_events) == 1
    assert spend_events[0]["phase"] == "research"
    assert spend_events[0]["model"] == "claude-sonnet-5"          # the OBSERVED model, never the
                                                                    # predicted tier ("sonnet")
    assert spend_events[0]["tokens_in"] == "1000000"
    assert spend_events[0]["tokens_out"] == "1000000"
    # $12.00 (1 Mtok input @ intro $2/Mtok + 1 Mtok output @ intro $10/Mtok) -> 1200 cents
    assert spend_events[0]["cost_cents"] == "1200"


def test_cli_end_with_unpriceable_transcript_writes_no_spend_event(tmp_path):
    # The mirror image: when no real dollar cost can be computed (unknown model, no rate-card
    # coverage), no `spend` event is written at all -- never a fabricated $0.00 or an empty model.
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-3"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-xyz.jsonl").write_text(
        json.dumps(_assistant_line(model="claude-nonexistent-9", input_tokens=100,
                                    output_tokens=20, ts="2026-08-24T00:00:00Z")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "xyz"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2  # unknown observed model cannot satisfy the Sonnet dispatch

    ledger = _mod("ledger")
    events = journal_events(ledger, sdlc)
    spend_events = [e for e in events if e.get("kind") == "spend" and e.get("goal") == "42"]
    assert spend_events == []
    phase_events = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
                    and e.get("state") == "end"]
    assert len(phase_events) == 1
    # No spend event exists for an unpriced model, so the rate-card coverage fact must survive on
    # the durable phase boundary that `/agrim-doctor` reads later.
    assert phase_events[0]["model"] == "claude-nonexistent-9"
    assert phase_events[0]["unpriced_turns"] == "1"
    assert "budget: 1 of 1 turns in this phase are unpriced" in result.stderr


# --------------------------------------------------------------------------- #2515: cmd_end feeds
# state.add_tokens (Decision 2) — the producer half of "budget.max_tokens actually stops a run".


def test_cli_end_with_real_transcript_also_feeds_the_budget_counter(tmp_path, monkeypatch):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-budget-1"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                                    ts="2026-08-24T00:00:00Z")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0

    st = _mod("state")
    cursor = st.load_cursor(sdlc)
    # $12.00 at the intro sonnet-5 input rate ($2.00/Mtok) -> 6_000_000 cost-equivalent tokens.
    assert cursor["run_tokens"] == 6_000_000

    # Retry the documented end gesture after an uncertain acknowledgement. The same phase
    # attempt must not charge the cursor or write a second pair of ledger events.
    repeated = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "abc"], capture_output=True, text=True, env=env,
    )
    assert repeated.returncode == 0
    assert st.load_cursor(sdlc)["run_tokens"] == 6_000_000
    ledger = _mod("ledger")
    events = journal_events(ledger, sdlc)
    assert len([e for e in events if e.get("kind") == "phase" and e.get("state") == "end"]) == 1
    assert len([e for e in events if e.get("kind") == "spend"]) == 1


def test_phase_end_retry_repairs_telemetry_after_it_was_off(tmp_path):
    sdlc = _sdlc(tmp_path, {"journal": {"enabled": False}})
    home = tmp_path / "home"
    session_id = "sess-cli-retry-journal"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                                    ts="2026-08-24T00:00:00Z")) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    command = [sys.executable, str(S / "phase_report.py")]
    subprocess.run(command + ["start", str(sdlc), "42", "research", "--model", "sonnet"],
                   capture_output=True, text=True, env=env, check=True)
    end = command + ["end", str(sdlc), "42", "research", "--agent-id", "abc"]
    subprocess.run(end, capture_output=True, text=True, env=env, check=True)
    st, ledger = _mod("state"), _mod("ledger")
    assert st.load_cursor(sdlc)["run_tokens"] == 6_000_000
    assert journal_events(ledger, sdlc) == []

    (sdlc / "config.json").write_text(json.dumps({"journal": {"enabled": True}}))
    subprocess.run(end, capture_output=True, text=True, env=env, check=True)
    subprocess.run(end, capture_output=True, text=True, env=env, check=True)
    events = journal_events(ledger, sdlc)
    assert st.load_cursor(sdlc)["run_tokens"] == 6_000_000
    assert len([e for e in events if e.get("kind") == "phase" and e.get("state") == "end"]) == 1
    assert len([e for e in events if e.get("kind") == "spend"]) == 1


def test_two_concurrent_phase_ends_credit_and_log_once(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-concurrent-end"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=1_000_000,
                                    ts="2026-08-24T00:00:00Z")) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    command = [sys.executable, str(S / "phase_report.py")]
    subprocess.run(command + ["start", str(sdlc), "42", "research", "--model", "sonnet"],
                   capture_output=True, text=True, env=env, check=True)
    end = command + ["end", str(sdlc), "42", "research", "--agent-id", "abc"]
    children = [subprocess.Popen(end, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=env) for _ in range(2)]
    for child in children:
        out, err = child.communicate(timeout=15)
        assert child.returncode == 0, (out, err)
    assert _mod("state").load_cursor(sdlc)["run_tokens"] == 6_000_000
    events = journal_events(_mod("ledger"), sdlc)
    assert len([e for e in events if e.get("kind") == "phase" and e.get("state") == "end"]) == 1
    assert len([e for e in events if e.get("kind") == "spend"]) == 1



def test_cli_end_with_unpriceable_transcript_does_not_touch_the_budget_counter(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-budget-2"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-xyz.jsonl").write_text(
        json.dumps(_assistant_line(model="claude-nonexistent-9", input_tokens=100,
                                    output_tokens=20, ts="2026-08-24T00:00:00Z")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "xyz"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2  # unknown model also fails the dispatch verification

    st = _mod("state")
    cursor = st.load_cursor(sdlc)
    # "skip, never guess" (Decision 1's fallback) -- the counter stays at its no-file default (0),
    # never fabricated from raw tokens_in/tokens_out.
    assert cursor["run_tokens"] == 0
    again = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "xyz"], capture_output=True, text=True, env=env,
    )
    assert again.returncode == 2
    events = journal_events(_mod("ledger"), sdlc)
    assert len([e for e in events if e.get("kind") == "phase" and e.get("state") == "end"]) == 1


def test_cli_end_with_partly_unpriced_transcript_does_not_undercount_budget(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-budget-partial"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text("\n".join([
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=0,
                                    ts="2026-08-24T00:00:00Z")),
        json.dumps(_assistant_line(model="claude-nonexistent-9", input_tokens=1_000_000,
                                    output_tokens=0, ts="2026-08-24T00:00:01Z")),
    ]) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    command = [sys.executable, str(S / "phase_report.py")]
    subprocess.run(command + ["start", str(sdlc), "42", "research", "--model", "sonnet"],
                   capture_output=True, text=True, env=env, check=True)
    result = subprocess.run(command + ["end", str(sdlc), "42", "research", "--agent-id", "abc"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 2  # one observed turn used an unknown, unpriced model
    assert "partial" in result.stdout
    assert _mod("state").load_cursor(sdlc)["run_tokens"] == 0
    events = journal_events(_mod("ledger"), sdlc)
    assert not any(e.get("kind") == "spend" for e in events)


def test_cli_end_accumulates_across_multiple_real_calls(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-cli-budget-3"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-one.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=0,
                                    ts="2026-08-24T00:00:00Z")) + "\n"
    )
    (subagents / "agent-two.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=1_000_000, output_tokens=0,
                                    ts="2026-08-24T00:00:00Z")) + "\n"
    )
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "research", "--model", "sonnet"], capture_output=True, text=True, env=env)
    r1 = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "research",
         "--agent-id", "one"],
        capture_output=True, text=True, env=env,
    )
    assert r1.returncode == 0
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                     "plan", "--model", "sonnet"], capture_output=True, text=True, env=env)
    r2 = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "plan",
         "--agent-id", "two"],
        capture_output=True, text=True, env=env,
    )
    assert r2.returncode == 0

    st = _mod("state")
    cursor = st.load_cursor(sdlc)
    # each phase: 1 Mtok input @ intro $2.00/Mtok = $2.00 -> 2.0 / (2.00 / 1e6) = 1_000_000
    # cost-equivalent tokens; two phases summed (add_tokens increments, never overwrites).
    assert cursor["run_tokens"] == 2_000_000


# --------------------------------------------------------------------------- #2100: legible banner
#
# Every guard below was RUN RED before it was trusted (AGENTS.md, "Run the control, or the check is
# decoration"). The mutation each one catches is named in its own docstring/comment, and the
# controls were driven through the CLI gesture `skills/agrim-loop/SKILL.md` actually prescribes --
# `phase_report.py start/end .sdlc "$goal" <phase>` -- not a stronger, flag-laden one.

import ast
import calendar
import re
import socket
import time


def _phase_kinds():
    return _mod("ledger").PHASE_KINDS


# --- the phase-token vocabulary, pinned BOTH ways ---------------------------------------------


def test_phase_tokens_cover_ledger_phase_kinds_in_order():
    """SIBLING PIN 1: the banner's phase vocabulary IS `ledger.PHASE_KINDS`, same members, same
    order. Order is load-bearing -- `next_phase_token` reads the flow straight off this tuple."""
    assert tuple(kind for kind, _ in pr.PHASE_TOKENS) == tuple(_phase_kinds())


def test_phase_tokens_match_the_output_contract_table():
    """SIBLING PIN 2: the `P<n> NAME` tokens the console prints are the ones
    `docs/output-contract.md` §2 tells a model to use. Parsed out of the contract's own markdown
    table, so a reworded contract fails here instead of drifting away from the machine half."""
    text = (ROOT / "docs" / "output-contract.md").read_text(encoding="utf-8")
    in_contract = set(re.findall(r"^\|\s*`(P\d [A-Z-]+)`\s*\|", text, re.MULTILINE))
    assert in_contract, "could not find §2's phase-token table in docs/output-contract.md"
    assert {token for _, token in pr.PHASE_TOKENS} == in_contract


def test_next_phase_token_walks_the_flow_and_stops_at_the_last_phase():
    assert pr.next_phase_token("research") == "P3 PLAN"
    assert pr.next_phase_token("plan") == "P4 PLAN-REVIEW"
    assert pr.next_phase_token("retro") is None            # last phase -- no successor to invent
    assert pr.next_phase_token("not_a_phase") is None


# --- the phase badges: one colour per phase, pinned BOTH ways, never a liveness marker ----------


def _contract_phase_badges():
    """`{token: badge}` read from the `Badge` column of `docs/output-contract.md` §2's Axis 2 table,
    located by its header rather than by position; `{}` when the table has no such column."""
    text = (ROOT / "docs" / "output-contract.md").read_text(encoding="utf-8")
    section = text[text.index("### Axis 2 —"):]
    section = section[:section.index("\n---")]
    rows = [[c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.startswith("|")]
    header = next(r for r in rows if r[0] == "Token")
    if "Badge" not in header:
        return {}
    col = header.index("Badge")
    return {r[0]: r[col] for r in rows if re.fullmatch(r"P\d [A-Z-]+", r[0])}


def test_phase_badges_cover_ledger_phase_kinds_with_seven_distinct_glyphs():
    """One badge per phase, in the ledger's own order, and no two alike: a shared colour would make
    two phases indistinguishable at a glance, which is the one thing a badge is for."""
    assert tuple(pr.PHASE_BADGES) == tuple(_phase_kinds())
    assert len(set(pr.PHASE_BADGES.values())) == len(pr.PHASE_BADGES) == 7


def test_phase_badges_match_the_output_contract_table():
    """SIBLING PIN, both ways: the badge the console prints for a phase is the one §2's table gives
    it, so the contract a model reads and the banner a human reads show the same colour."""
    in_contract = _contract_phase_badges()
    assert in_contract, "no Badge column in §2's phase-token table in docs/output-contract.md"
    assert in_contract == {pr.phase_token(kind): badge for kind, badge in pr.PHASE_BADGES.items()}


def test_phase_badges_are_never_liveness_markers_or_red():
    """A badge names a phase, never a state. §2's markers carry meanings (`🔴` blocked, `🟢`
    merging, ...), so no badge may be or contain one, nor be the red square that reads as blocked
    beside `🔴`."""
    marker_chars = {ch for glyph, _ in _mod("render").MARKERS for ch in glyph} - {chr(0xFE0F)}
    for kind, badge in pr.PHASE_BADGES.items():
        assert not (set(badge) & marker_chars), (kind, badge)
        assert badge != "🟥", kind


def test_no_badge_is_invented_for_an_unknown_phase_or_past_the_last_phase():
    """Only the seven known phases are badged. Anything else prints bare, exactly as `phase_token`
    already returns it, and the slot after P7 RETRO reads `last phase` with no colour in front."""
    assert pr.phase_label("implement") == "🟦 P5 IMPLEMENT"
    assert pr.phase_label("not_a_phase") == "not_a_phase"
    body = pr.end_lines("#1", "retro", "", "1m", {"source": "unavailable", "reason": "r"})[1]
    assert body.endswith(" · last phase"), body


def test_mirror_rel_matches_mirror_module():
    """The one constant duplicated instead of imported (see its comment) is pinned to its source."""
    assert pr.MIRROR_REL == _mod("mirror").MIRROR_REL


# --- title resolution: already-on-disk only, and honest when nothing has it ---------------------


def _mirror(sdlc, records):
    path = sdlc / "state" / "board-mirror.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records),
                    encoding="utf-8")


def test_title_from_mirror_reads_the_already_cached_board_snapshot(tmp_path):
    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1982, "title": "Something else"},
                    {"number": 1983, "title": "Resolve reviewer independence per host"}])
    assert pr.title_from_mirror(sdlc, "1983") == "Resolve reviewer independence per host"


def test_title_from_mirror_skips_a_malformed_line_without_losing_the_rest(tmp_path):
    sdlc = _sdlc(tmp_path)
    path = sdlc / "state" / "board-mirror.ndjson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"number": 1983, NOT JSON\n'
                     + json.dumps({"number": 1983, "title": "Real title"}) + "\n",
                     encoding="utf-8")
    assert pr.title_from_mirror(sdlc, "1983") == "Real title"


def test_title_from_goal_file_reads_local_mode_frontmatter(tmp_path):
    sdlc = _sdlc(tmp_path)
    goals = sdlc / "goals"
    goals.mkdir(parents=True)
    (goals / "0004-vision.md").write_text(
        '---\nid: 0004\ntitle: "Dual-mode: vision-first on-ramp"\n---\n\nbody\n', encoding="utf-8")
    assert pr.title_from_goal_file(sdlc, "0004-vision") == "Dual-mode: vision-first on-ramp"


def test_resolve_title_prefers_the_explicit_flag_then_mirror_then_goal_file(tmp_path):
    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1983, "title": "From the mirror"}])
    assert pr.resolve_title(sdlc, "1983", "From the flag") == "From the flag"
    assert pr.resolve_title(sdlc, "1983") == "From the mirror"


def test_resolve_title_degrades_to_empty_when_nothing_on_this_host_has_it(tmp_path):
    """The Cursor/Codex/fresh-adopter case: no mirror, no goal file. "" is the caller's cue to
    print the BARE id -- never a blank sitting where a title should be, never an invented one."""
    sdlc = _sdlc(tmp_path)
    assert pr.resolve_title(sdlc, "1983") == ""
    assert pr.resolve_title(sdlc, "1983", "   ") == ""       # whitespace is not a title


def test_clean_title_collapses_whitespace_and_caps_length():
    assert pr.clean_title("  a\n  b  ") == "a b"
    long_title = "x" * (pr.TITLE_MAX + 40)
    capped = pr.clean_title(long_title)
    assert len(capped) == pr.TITLE_MAX and capped.endswith("…")


def test_title_resolution_spawns_no_subprocess_and_opens_no_socket(tmp_path, monkeypatch):
    """THE CONTROL for "no new network call on the phase-boundary path" (#2100's own DoD).

    GraphQL is a rate-limited, shared resource and this is the hottest path in the system, so the
    guard is executed rather than asserted: every `subprocess` entry point and `socket.socket`
    itself is replaced with a raiser, and the FULL resolution chain -- flag, mirror, goal file,
    and the nothing-found path -- is run against it. A `gh issue view` added to any of them turns
    this red immediately.

    Deliberately scoped to title resolution: `ledger.safe_append` may resolve its actor via `gh`,
    which is PRE-EXISTING behaviour on this path and not what #2100 is allowed to regress."""
    def _boom(*a, **k):
        raise AssertionError("phase-boundary title resolution spawned a process or opened a socket")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _boom, raising=False)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)

    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1983, "title": "Resolve reviewer independence per host"}])
    goals = sdlc / "goals"
    goals.mkdir(parents=True)
    (goals / "0004-vision.md").write_text('---\ntitle: "Local goal"\n---\n', encoding="utf-8")

    assert pr.resolve_title(sdlc, "1983", "explicit") == "explicit"
    assert pr.resolve_title(sdlc, "1983") == "Resolve reviewer independence per host"
    assert pr.resolve_title(sdlc, "0004-vision") == "Local goal"
    assert pr.resolve_title(sdlc, "999999") == ""


def test_phase_report_loads_no_network_capable_sibling_module():
    """The static half of the same guard, as an AST walk rather than a grep: this module documents
    what it does NOT do, so `grep sources` over it returns the opposite of the truth. Asserts the
    literal set of modules `_load(...)` is ever called with, so adding `_load("sources")` (the
    `gh`-shelling helper) to the banner path fails here even if a mocked test would not notice.

    `subprocess` LEFT the banned import set at #2112, because the renderer is invoked by shell-out
    and never imported (design D-6) -- so the ban it used to carry moves down a level and becomes
    sharper rather than looser: the module may import `subprocess`, and the ONLY thing it is
    allowed to spawn is `render.py`. A plain ban would now be unsatisfiable; a hole with nothing in
    its place would let a `gh` call back onto the hottest path in the system, which is what the
    guard was for. Every network-capable module stays banned outright."""
    tree = ast.parse((S / "phase_report.py").read_text(encoding="utf-8"))
    loaded = {node.args[0].value
              for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "_load" and node.args
              and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)}
    # `timing_store` joined this set with the always-on working-time record. It qualifies on the
    # same terms as the rest: it loads `work` lazily (already here) and nothing else at import,
    # reads no config, opens no socket and spawns no subprocess — its whole surface is one
    # appended line per completed interval. The exact-set assertion is the point of this guard,
    # so it is updated deliberately rather than loosened to a subset check.
    #
    # `sources` joined it with #233 (the board's Phase column), DELIBERATELY and fenced: it may be
    # loaded from `mirror_phase_to_board` ONLY, which returns before loading it unless the config is
    # github mode with `project.enabled` AND a pinned `project.number` -- so a repo without a board
    # still loads nothing network-capable here (executed, not asserted, in tests/test_board_phase.py
    # `test_a_repo_without_a_pinned_board_spawns_nothing_at_a_phase_start`).
    assert loaded == {"state", "work", "ledger", "frontmatter", "timing_store", "sources"}, loaded
    sources_callers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                       for call in ast.walk(fn)
                       if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                       and call.func.id == "_load" and call.args
                       and isinstance(call.args[0], ast.Constant) and call.args[0].value == "sources"}
    assert sources_callers == {"mirror_phase_to_board"}, sources_callers

    imported = {n.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.Import) for n in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert not (imported & {"socket", "urllib", "http", "requests"}), imported

    # every `subprocess.*` call in the file, and where it sits
    spawns = [(fn.name, call) for fn in ast.walk(tree)
              if isinstance(fn, ast.FunctionDef)
              for call in ast.walk(fn)
              if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
              and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"]
    # #233: the other spawns are the bounded `gh` runner the board write uses (`_bounded_gh` and
    # the `run` closure inside it are the same call, seen from both function scopes) and the
    # Windows tree-kill of a hung one (`_kill_tree`, bounded by its own timeout).
    assert sorted({name for name, _ in spawns}) == ["_bounded_gh", "_kill_tree", "render_block",
                                                   "run"], spawns
    by_name = {name: ast.unparse(call.args[0]) for name, call in spawns}
    argv = by_name["render_block"]
    assert "sys.executable" in argv and "RENDER_SCRIPT" in argv, argv
    assert by_name["run"].startswith("[BOARD_GH"), by_name["run"]
    assert by_name["_kill_tree"].startswith("['taskkill'"), by_name["_kill_tree"]
    assert any(k.arg == "timeout" for _n, c in spawns if _n == "_kill_tree" for k in c.keywords)
    # the gh call itself is bounded where it is WAITED on: every `communicate` in the runner
    # carries a timeout (the call's own, and the post-kill reap's)
    runner = next(fn for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                  and fn.name == "_bounded_gh")
    waits = [c for c in ast.walk(runner) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr in ("communicate", "wait")]
    assert len(waits) == 2 and all(any(k.arg == "timeout" for k in c.keywords) for c in waits), \
        [ast.unparse(c) for c in waits]


# --- elapsed wall time ---------------------------------------------------------------------


def test_format_elapsed_reads_as_a_human_duration():
    assert pr.format_elapsed(41) == "41s"
    assert pr.format_elapsed(491) == "8m11s"
    assert pr.format_elapsed(305) == "5m05s"
    assert pr.format_elapsed(7500) == "2h05m"


def test_format_elapsed_is_honest_about_what_it_cannot_measure():
    assert pr.format_elapsed(None) is None          # no marker -> "elapsed: unavailable"
    assert pr.format_elapsed(-5) is None            # clock moved backwards -> not a fabricated 0s


def test_marker_started_at_prefers_the_exact_epoch(tmp_path):
    sdlc = _sdlc(tmp_path)
    written = pr.write_marker(sdlc, "42", "research", "sonnet", now=1_000_000.5)
    assert pr.marker_started_at(written) == 1_000_000.5


def test_marker_started_at_parses_a_pre_2100_marker_as_utc_not_local_time():
    """A marker written before #2100 has only the ISO `ts_start`. Parsing it with `time.mktime`
    would read a `Z`-suffixed stamp as LOCAL time and shift every elapsed figure by the machine's
    UTC offset -- an hours-wrong "8m11s" is worse than none. `calendar.timegm` is the fix."""
    legacy = {"phase": "research", "model": "sonnet", "ts_start": "2026-08-25T03:13:26Z"}
    expected = calendar.timegm(time.strptime("2026-08-25 03:13:26", "%Y-%m-%d %H:%M:%S"))
    assert pr.marker_started_at(legacy) == float(expected)


def test_marker_started_at_returns_none_when_unreadable():
    assert pr.marker_started_at(None) is None
    assert pr.marker_started_at({}) is None
    assert pr.marker_started_at({"ts_start": "not-a-timestamp"}) is None


# --- the printed lines ----------------------------------------------------------------------


def _priced(**over):
    base = {"source": "claude-code", "tokens_in": 52, "tokens_out": 35433, "cost_usd": 4.98,
            "unpriced_turns": 0, "turns": 9, "models": ["claude-opus-5"]}
    base.update(over)
    return base


def test_start_lines_carry_id_title_phase_token_and_tier():
    lines = pr.start_lines("#1983", "research", "sonnet", "Resolve reviewer independence per host")
    assert lines[0] == "⚪ PHASE START · #1983 Resolve reviewer independence per host"
    assert lines[1] == ("   🟨 P2 RESEARCH · requested host model: unrecorded · "
                        "predicted model tier: sonnet (agent-set)")


def test_start_lines_degrade_to_the_bare_id_with_no_title():
    lines = pr.start_lines("#1983", "research", "sonnet", "")
    assert lines[0] == "⚪ PHASE START · #1983"          # no trailing space, no placeholder
    assert lines[1].startswith("   🟨 P2 RESEARCH")


def test_end_lines_carry_elapsed_cost_tokens_model_and_the_next_phase():
    lines = pr.end_lines("#1983", "research", "Resolve reviewer independence per host",
                          "8m11s", _priced())
    assert lines[0] == "🟨 PHASE END · #1983 Resolve reviewer independence per host"
    assert lines[1] == ("   🟨 P2 RESEARCH · 8m11s · $4.98 · tokens 52 in, 35,433 out · "
                        "claude-opus-5 · next: 🟧 P3 PLAN")


def test_end_lines_name_the_last_phase_rather_than_inventing_a_p8():
    lines = pr.end_lines("#1983", "retro", "T", "1m02s", _priced())
    assert lines[1].endswith(" · last phase")


def test_end_lines_keep_every_honest_degradation_string_verbatim():
    """`docs/output-contract.md`'s Block B quotes `cost: unavailable on this host (<reason>)` and
    tells a model to reproduce it. Keep that shape for all three unavailable sources."""
    unavailable = pr.end_lines("#1", "research", "", "8m11s",
                                {"source": "unavailable", "reason": "no per-turn usage source"})
    assert "cost: unavailable on this host (no per-turn usage source)" in unavailable[1]

    no_rate = pr.end_lines("#1", "research", "", "8m11s", _priced(cost_usd=None))
    assert "cost: unavailable on this host (model not in rate card)" in no_rate[1]

    codex = pr.end_lines("#1", "research", "", "8m11s",
                          {"source": "codex", "tokens_in": 500, "tokens_out": 50,
                           "cost_usd": None, "models": []})
    assert "cost: unavailable on this host (Codex model absent from bundled rate card)" in codex[1]
    assert "tokens 500 in, 50 out" in codex[1]

    partial = pr.end_lines("#1", "research", "", "8m11s", _priced(unpriced_turns=2))
    assert "$4.98 (partial — 2 of 9 turns unpriced)" in partial[1]

    inline = pr.end_lines("#1", "research", "", "8m11s", _priced(source="claude-code-inline"))
    assert "claude-opus-5 (inline, windowed)" in inline[1]


def test_end_lines_say_elapsed_is_unavailable_rather_than_printing_a_zero():
    lines = pr.end_lines("#1", "research", "", None, _priced())
    assert "elapsed: unavailable (no phase-start marker)" in lines[1]
    assert " 0s " not in lines[1]


def test_goal_ref_only_hashes_a_real_issue_number():
    assert pr.goal_ref("1983") == "#1983"
    assert pr.goal_ref("0004-vision-first-onramp") == "0004-vision-first-onramp"


# --- end to end, through the gesture the SKILLs actually prescribe -----------------------------


def test_cli_start_and_end_print_the_title_from_the_local_mirror(tmp_path):
    """The documented gesture, verbatim from `skills/agrim-loop/SKILL.md`: `start`, then `end
    --agent-id <id>`. No `--title` is passed -- the title has to come from what is already on
    disk, or this whole change is decoration."""
    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1983, "title": "Resolve reviewer independence per host"}])
    home = tmp_path / "home"
    session_id = "sess-2100"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(input_tokens=52, output_tokens=35433,
                                    ts="2026-08-24T00:00:00Z")) + "\n")
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": session_id}

    start = subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc),
                             "1983", "research", "--model", "sonnet"],
                            capture_output=True, text=True, env=env)
    assert start.returncode == 0
    assert start.stdout.startswith(
        "⚪ PHASE START · #1983 Resolve reviewer independence per host\n")
    assert "P2 RESEARCH" in start.stdout
    assert pr.read_marker(sdlc, "1983")["title"] == "Resolve reviewer independence per host"

    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "1983",
                           "research", "--agent-id", "abc"],
                          capture_output=True, text=True, env=env)
    assert end.returncode == 0
    assert end.stderr == "", end.stderr
    assert end.stdout.startswith(
        "**#1983 Resolve reviewer independence per host** P2 RESEARCH → P3 PLAN — ")
    assert "tokens 52 in, 35,433 out" in end.stdout
    assert re.search(r"— \d+s ·", end.stdout), end.stdout      # a real elapsed figure


def test_cli_prints_the_bare_id_when_no_host_source_has_the_title(tmp_path):
    """Cursor / Codex / a fresh adopter: no mirror, no goal file, nothing to fetch from. The
    banner still names the phase and the flow; the title is simply absent, not blank or invented."""
    sdlc = _sdlc(tmp_path)
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    env = {**os.environ, "HOME": str(empty_home), "CLAUDE_CODE_SESSION_ID": "sess-none"}
    start = subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "42",
                             "research", "--model", "sonnet"],
                            capture_output=True, text=True, env=env)
    assert "⚪ PHASE START · #42\n" in start.stdout       # no trailing space, no placeholder
    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42",
                           "research"], capture_output=True, text=True, env=env)
    # the bold ref with NOTHING after it -- not a blank, not a placeholder, and no `**#42 **`
    assert end.stdout.startswith("**#42** P2 RESEARCH → P3 PLAN — "), end.stdout
    assert "cost: unavailable on this host" in end.stdout


def test_cli_start_accepts_an_explicit_title_the_orchestrator_already_holds(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("start", str(sdlc), "42", "research", "--model", "sonnet",
                   "--title", "Make the machine-printed half legible")
    assert "PHASE START · #42 Make the machine-printed half legible" in result.stdout


def test_banner_identity_is_byte_identical_with_no_claude_env_at_all(tmp_path):
    """HOST-AGNOSTIC, PROVEN BY EXECUTION rather than claimed (#2100's DoD; AGENTS.md, "Host-
    agnostic or it does not ship").

    Cursor and Codex differ from Claude Code here in exactly two ways, and neither touches the
    banner's identity half: they set no `CLAUDE_CODE_SESSION_ID`, and they have no
    `~/.claude/projects` transcript store. So this runs the documented gesture TWICE over the same
    `.sdlc` -- once with the Claude environment, once with EVERY `CLAUDE_*` variable stripped and
    `HOME` pointed at an empty directory -- and asserts the goal ref, the title, the phase token
    and the next-phase arrow come out byte-identical. Only the cost half differs, and it differs
    into the pre-existing honest `cost: unavailable on this host (<reason>)` line.

    This is what a Claude Code hook could not deliver, and why the change lives in this module."""
    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1983, "title": "Resolve reviewer independence per host"}])
    claude_home = tmp_path / "claude_home"
    session_id = "sess-hosts"
    subagents = claude_home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(ts="2026-08-24T00:00:00Z")) + "\n")
    bare_home = tmp_path / "bare_home"
    bare_home.mkdir()

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    claude_env = {**base_env, "HOME": str(claude_home), "CLAUDE_CODE_SESSION_ID": session_id}
    other_host_env = {**base_env, "HOME": str(bare_home)}      # no CLAUDE_* of any kind

    def banner(verb, env, *extra):
        return subprocess.run(
            [sys.executable, str(S / "phase_report.py"), verb, str(sdlc), "1983", "research",
             *extra], capture_output=True, text=True, env=env).stdout.splitlines()

    on_claude_start = banner("start", claude_env, "--model", "sonnet")
    on_other_start = banner("start", other_host_env, "--model", "sonnet")
    assert on_claude_start == on_other_start                   # start is identical in FULL
    assert on_claude_start[0] == "⚪ PHASE START · #1983 Resolve reviewer independence per host"

    (body_claude,) = banner("end", claude_env, "--agent-id", "abc")
    (body_other,) = banner("end", other_host_env)              # ONE line: Block B is a paragraph
    for line in (body_claude, body_other):
        assert line.startswith(
            "**#1983 Resolve reviewer independence per host** P2 RESEARCH → P3 PLAN — ")
        assert line.endswith(".")
    # the ONLY divergence is the cost half, and it degrades into the pre-existing honest line
    assert "cost: unavailable on this host (" in body_other
    assert "claude-sonnet-5" in body_claude


#: §2's liveness markers that assert an OUTCOME. `⚪` (queued) is absent on purpose -- it claims
#: nothing and is the one marker this module is allowed to print.
_VERDICT_BEARING_MARKERS = ("✅", "🟢", "🟣", "🔴", "⏸️", "⏳", "🔵")


def test_phase_end_never_claims_an_outcome_it_cannot_know():
    """BLOCKING finding on PR #2102, and the reason it blocked rather than being a nitpick.

    `cmd_end` takes NO verdict argument: `skills/agrim-loop/SKILL.md` calls it whenever the phase's
    subagent returns -- pass or block -- and the console print is unconditional. So a `✅` on that
    line (an earlier draft had one, as a module constant) is a success claim the module is
    structurally unable to back. Measured against ~1,564 live gate facts, a green check on
    P4 PLAN-REVIEW would be wrong 88% of the time (6 pass / 16 block / 29 warn) and roughly a
    quarter of the time across all verdict-bearing gates. The pre-#2100 line made no outcome claim
    at all -- ugly but neutral -- so shipping one would have inverted this goal's own intent.

    Asserted for EVERY phase and EVERY usage source, so it cannot be re-introduced for one branch.
    The structural half is what makes it airtight: no verdict can reach this function, so nothing
    here could ever be entitled to print a marker."""
    import inspect
    for kind, _ in pr.PHASE_TOKENS:
        for result in (_priced(),
                       _priced(cost_usd=None),
                       {"source": "unavailable", "reason": "no per-turn usage source"},
                       {"source": "codex", "tokens_in": 5, "tokens_out": 5, "cost_usd": None,
                        "models": []}):
            head, body = pr.end_lines("#1983", kind, "Resolve reviewer independence per host",
                                      "8m11s", result)
            assert head.startswith(f"{pr.PHASE_BADGES[kind]} PHASE END · "), head
            for marker in _VERDICT_BEARING_MARKERS:
                assert marker not in head and marker not in body, (kind, marker, head, body)

    # structural: there is no verdict to print, on either the function or the CLI
    assert "verdict" not in inspect.signature(pr.end_lines).parameters
    assert not any("verdict" in flag for flag in pr._VALUE_FLAGS)
    assert not hasattr(pr, "MARK_END")


def test_cli_phase_end_on_a_blocked_plan_review_prints_no_green_check(tmp_path):
    """The same guard through the documented gesture, on the phase where it matters most.

    `phase_report.py end` is called identically whether P4 PLAN-REVIEW passed or blocked -- there
    is no flag distinguishing them -- so this run IS the blocked case as far as the module can
    tell. It must name the phase, the elapsed time and the cost, and claim nothing about how it
    went."""
    sdlc = _sdlc(tmp_path)
    _mirror(sdlc, [{"number": 1983, "title": "Resolve reviewer independence per host"}])
    empty_home = tmp_path / "empty_home"
    empty_home.mkdir()
    env = {**os.environ, "HOME": str(empty_home), "CLAUDE_CODE_SESSION_ID": "sess-blocked"}
    subprocess.run([sys.executable, str(S / "phase_report.py"), "start", str(sdlc), "1983",
                    "plan_review", "--model", "opus"], capture_output=True, text=True, env=env)
    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "1983",
                          "plan_review"], capture_output=True, text=True, env=env)
    assert end.returncode == 0
    assert end.stdout.startswith(
        "**#1983 Resolve reviewer independence per host** P4 PLAN-REVIEW → P5 IMPLEMENT — ")
    for marker in _VERDICT_BEARING_MARKERS + (pr.MARK_START,):
        assert marker not in end.stdout, (marker, end.stdout)
    # `→ P5 IMPLEMENT` is the standing SDLC's POSITION, which is knowable and true whatever the
    # verdict; what nobody here knows is the OUTCOME, and #2100's answer to that was never a word
    # on this line -- it is that no marker is stamped on it at all, which the loop above checks.
    assert "P5 IMPLEMENT" in end.stdout


def test_the_next_phase_is_worded_as_flow_position_not_a_destination():
    """`_tail` is a static forward walk and knows nothing about the verdict -- on a blocked P4 the
    loop actually returns to P3. `next:` states where the phase sits on the map; the earlier
    `→ P5 IMPLEMENT` read as a promise about where this run was going."""
    body = pr.end_lines("#1", "plan_review", "", "8m11s", _priced())[1]
    assert body.endswith(" · next: 🟦 P5 IMPLEMENT")
    assert "→" not in body


def test_phase_start_still_carries_the_queued_marker():
    """`⚪` is §2's "claimed, not started", which is exactly true when SKILL.md fires `start`
    before dispatching the subagent. It asserts no outcome, so it stays.

    #2112 briefly removed it, as a consequence of routing `start` through Block B -- which has no
    `marker` key. On review that routing was the mistake, not the glyph: a start announces a state
    and IS a state line, so §2's rule applies to it and Block B's does not. Asserted through the
    documented gesture as well as the function, because the glyph's whole job is to be on screen."""
    assert pr.start_lines("#1", "research", "sonnet", "T")[0].startswith("⚪ PHASE START · ")


# ------------------------------------------------------- the always-on timing store (working time)

#: Everything OFF. Not merely "share omitted" like `_sdlc`'s default — every opt-in store in the
#: kit is explicitly disabled here, so a duration that still lands proves the timing writer
#: consults no config at all. These tests read the STORE off disk rather than stdout on purpose:
#: seven tests in this file already fail on this host because `text=True` decodes the child's
#: UTF-8 markers through cp1252, and a new proof must not inherit that.
_ALL_OFF = {"journal": {"enabled": False}, "action_log": {"enabled": False},
            "ledger": {"enabled": False}}

timing_store = _mod("timing_store")


def test_a_phase_duration_reaches_the_timing_store_with_telemetry_off(tmp_path):
    """S2's acceptance criterion, and the premise of the whole feature.

    `ledger.append` returns without writing when `journal.enabled` is not True, so on a stock
    install no phase event exists at all. The timing store is the answer to that, and it is only
    an answer if it records here — with the journal, the ledger and the action log all explicitly
    off. If this test can pass while the write is gated, it is proving nothing."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    assert _run("start", str(sdlc), "482", "implement").returncode == 0
    assert _run("end", str(sdlc), "482", "implement").returncode == 0

    entries = timing_store.read_goal(str(sdlc), "482")
    assert len(entries) == 1, f"expected one recorded interval, got {entries}"
    assert entries[0]["kind"] == "phase"
    assert entries[0]["name"] == "implement"
    assert entries[0]["ms"] > 0

    # ... and the gate really was shut: no ledger event was written by that same call.
    assert not list((sdlc / "ledger").rglob("*.jsonl"))
    assert not list((sdlc / "events").rglob("*.jsonl"))


def test_a_phase_ended_against_another_phases_marker_records_no_interval(tmp_path):
    """`cmd_end` warns on a marker/phase mismatch and then measures NOTHING from it — the marker is
    dropped on the spot and this end takes the identical path a missing marker takes.

    This test used to bless the opposite ("computing `elapsed` from the OTHER phase's start;
    printing a wrong number is cosmetic"), and that premise was false. Only `interval_ms` was ever
    guarded by `phase_matches`; `since_ts` never was, so the stale marker's `ts_start` still
    windowed `collect_phase_usage` — and the resulting cost was STORED, in the `phase`/`spend`
    ledger events and in `STATE.md`'s `run_tokens` budget cursor. Measured on a 30-day-old marker:
    `720h00m · $9.01 · tokens 901,000 out`, `run_tokens: 4507000`, from a review phase that ran for
    seconds. See `test_a_mismatched_marker_never_windows_another_phases_cost` for the pin."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    assert _run("start", str(sdlc), "482", "research").returncode == 0
    end = _run("end", str(sdlc), "482", "implement")
    assert end.returncode == 0
    assert "marker phase" in end.stderr
    assert timing_store.read_goal(str(sdlc), "482") == []
    assert "elapsed: unavailable" in end.stdout, end.stdout


def _stale_marker_fixture(tmp_path, stale_phase="retro", age_days=30):
    """A goal carrying an ABANDONED `stale_phase` marker `age_days` old, and a session transcript
    holding one expensive turn from inside that dead window plus one cheap turn from just now.
    The shape goal #2577 hit live: a `retro` marker left open while the goal sat blocked in
    review."""
    sdlc = _sdlc(tmp_path, {"journal": {"enabled": True}})
    home = tmp_path / "stale_home"
    proj = home / ".claude" / "projects" / "-slug"
    proj.mkdir(parents=True)
    now = time.time()
    stale = now - age_days * 86400

    def _at(epoch):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))

    _write_transcript(proj / "sess-1.jsonl", [
        _assistant_line(output_tokens=900_000, ts=_at(stale + 60)),   # the abandoned phase's spend
        _assistant_line(output_tokens=1_000, ts=_at(now - 5)),        # this phase's own spend
    ])
    phase_dir = sdlc / "state" / "phase"
    phase_dir.mkdir(parents=True)
    (phase_dir / "2577.json").write_text(json.dumps({
        "phase": stale_phase, "model": "opus", "host_model": "", "requested_model": "",
        "title": "Some abandoned retro", "goal": "2577",
        "ts_start": _at(stale), "ts_start_epoch": stale,
    }, sort_keys=True))
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "sess-1"}
    env.pop("CODEX_THREAD_ID", None)
    return sdlc, env


def test_a_mismatched_marker_never_windows_another_phases_cost(tmp_path):
    """THE defect this guard exists for, and the one the `records_no_interval` test above could
    never see: a stale marker's `ts_start` used as `since_ts` bills every turn since that dead
    phase began to the phase being ended now — and `end` STORES that number. Run against the code
    before the fix this goes red on all four assertions at once:
    `720h00m · $9.01 · tokens 901,000 out`, a `spend` event of 901 cents, `run_tokens: 4507000`,
    and the dead phase's title on the live phase's banner.

    A mismatched marker has no lower bound to offer that is better than "now", so the honest
    output is UNMEASURED — never a measurement windowed from the wrong instant."""
    sdlc, env = _stale_marker_fixture(tmp_path)
    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "2577",
                          "review"], capture_output=True, text=True, env=env)
    assert end.returncode == 0, end.stderr
    assert "marker phase 'retro' != 'review'" in end.stderr

    # 1. No elapsed measured from the dead phase's start.
    assert "720h" not in end.stdout, end.stdout
    assert "elapsed: unavailable" in end.stdout, end.stdout
    # 2. No cost windowed from it either — the abandoned phase's 900k output tokens stay its own.
    assert "901,000" not in end.stdout, end.stdout
    assert "$9.0" not in end.stdout, end.stdout
    # 3. Nothing wrong was STORED: no spend event, and the budget cursor is untouched.
    # Read through `journal_events`, which unions BOTH destinations: on this branch the journal
    # writes to `.sdlc/events/` (#2574/S1-G3), so a scan filtered to paths containing "ledger"
    # saw no file at all and this assertion could not fail (#2664). The phase-end event is the
    # non-vacuity partner -- `end` always writes one, so an empty scan is a broken scan.
    events = journal_events(_mod("ledger"), sdlc)
    assert [e for e in events if e.get("kind") == "phase" and e.get("state") == "end"], events
    assert [e for e in events if e.get("kind") == "spend"] == [], events
    assert "run_tokens" not in (sdlc / "state" / "STATE.md").read_text(encoding="utf-8")
    # 4. The dead phase's title is not borrowed for the live phase's banner.
    assert "Some abandoned retro" not in end.stdout, end.stdout


def test_a_phase_end_with_no_marker_records_no_interval(tmp_path):
    """No marker means no start time, so there is no interval to record. A zero here would claim
    "measured, and instantaneous" — the one thing this store must never say."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    end = _run("end", str(sdlc), "482", "implement")
    assert end.returncode == 0
    assert "no phase-start marker" in end.stderr
    assert timing_store.read_goal(str(sdlc), "482") == []


def test_the_phase_end_event_carries_ms(tmp_path):
    """S12. `ledger.EVENT_FIELDS["phase"]` has declared `ms` all along and the contract's golden
    fixture already carries it; it simply had no writer. Additive — a downstream duration metric derives
    its own durations from paired timestamps and never reads this field."""
    sdlc = _sdlc(tmp_path)                      # journal ON, share omitted -> the journal dir
    assert _run("start", str(sdlc), "482", "implement").returncode == 0
    assert _run("end", str(sdlc), "482", "implement").returncode == 0

    ledger = _mod("ledger")
    ends = [e for e in journal_events(ledger, str(sdlc))
            if e.get("kind") == "phase" and e.get("state") == "end"]
    assert len(ends) == 1
    assert isinstance(ends[0]["ms"], int) and ends[0]["ms"] > 0


def test_a_phase_end_with_no_interval_omits_ms_rather_than_zeroing_it(tmp_path):
    """No marker means no interval. `append` discards a None, and a 0 would claim the phase was
    measured and instantaneous — the same rule the banner's `elapsed: unavailable` already keeps."""
    sdlc = _sdlc(tmp_path)
    assert _run("end", str(sdlc), "482", "implement").returncode == 0

    ledger = _mod("ledger")
    ends = [e for e in journal_events(ledger, str(sdlc))
            if e.get("kind") == "phase" and e.get("state") == "end"]
    assert len(ends) == 1
    assert "ms" not in ends[0]


def test_the_banner_carries_goal_totals_not_this_phases_own_duration():
    """S10's acceptance criterion, and the reason the field is LABELLED. It rides a per-PHASE
    banner but carries GOAL-cumulative numbers, so an unlabelled figure there would be read as the
    phase's own. The assertion is that it equals the goal's total and NOT this phase's duration —
    a weaker test that only checked the field landed would miss exactly that misattribution."""
    totals = {"recorded": True, "active_ms": 9_600_000, "background_ms": 720_000,
              "effort_ms": 10_320_000, "phases": 7, "background_runs": 3}
    body = pr.end_lines("#1", "implement", "T", "8m11s", _priced(), totals=totals)[1]
    assert "goal so far: active 2h40m · effort 2h52m" in body
    assert "8m11s" in body                      # this phase's own duration still stands, separately
    assert body.endswith(" · next: 🟪 P6 REVIEW")  # the field went BEFORE the tail, never after it


def test_the_banner_is_unchanged_when_there_are_no_totals_to_show():
    """Default None, so every existing caller and every pinned assertion is untouched. A goal with
    nothing recorded says nothing here rather than printing a zero."""
    plain = pr.end_lines("#1", "implement", "T", "8m11s", _priced())
    assert plain == pr.end_lines("#1", "implement", "T", "8m11s", _priced(), totals=None)
    assert "goal so far" not in plain[1]
    absent = {"recorded": False, "active_ms": None, "background_ms": None, "effort_ms": None,
              "phases": 0, "background_runs": 0}
    assert "goal so far" not in pr.end_lines("#1", "implement", "T", "8m11s", _priced(),
                                             totals=absent)[1]


def test_the_banner_totals_field_carries_no_verdict_bearing_marker():
    """The marker sweep checks head AND body. A liveness glyph here would assert an outcome this
    module cannot know — the same defect that made an earlier check-mark on this line blocking."""
    totals = {"recorded": True, "active_ms": 60_000, "background_ms": 0, "effort_ms": 60_000,
              "phases": 1, "background_runs": 0}
    body = pr.end_lines("#1", "implement", "T", "1s", _priced(), totals=totals)[1]
    assert not (set(body) & set("⚪🔵🟣🟢✅🔴⏸️⏳"))


def test_the_cli_banner_shows_the_goal_running_total(tmp_path):
    """End to end: two phases recorded, and the second banner reports the goal's cumulative time
    rather than only its own. The store write happens BEFORE the totals are read, or every banner
    would be one phase stale."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    for phase in ("research", "implement"):
        assert _run("start", str(sdlc), "482", phase).returncode == 0
        assert _run("end", str(sdlc), "482", phase).returncode == 0

    entries = timing_store.read_goal(str(sdlc), "482")
    assert len(entries) == 2
    total = sum(e["ms"] for e in entries)
    end = _run("end", str(sdlc), "482", "implement")     # re-read, same start: dedup keeps it at 2
    assert "goal so far:" in end.stdout
    assert pr.format_elapsed(total / 1000.0) in end.stdout


def test_running_end_twice_for_one_phase_records_one_interval(tmp_path):
    """S3, end to end. `cmd_end` never consumes the marker, so a retried phase boundary runs the
    whole measurement again against the same start — and against an ACCUMULATING store that would
    count the phase twice. The store's own de-duplication is what makes the repeat harmless; the
    marker is deliberately left untouched, since changing its shape is out of scope."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    assert _run("start", str(sdlc), "482", "implement").returncode == 0
    assert _run("end", str(sdlc), "482", "implement").returncode == 0
    assert _run("end", str(sdlc), "482", "implement").returncode == 0

    entries = timing_store.read_goal(str(sdlc), "482")
    assert len(entries) == 1, f"a repeated phase end was counted twice: {entries}"


def test_the_stored_duration_and_the_printed_elapsed_come_from_one_measurement(tmp_path):
    """The banner rounds to whole seconds and the store keeps milliseconds, but both must derive
    from ONE subtraction — two `time.time()` calls would let a phase report `0s` while storing a
    duration from a measurably later instant. Asserted on the ASCII `elapsed` field only, never on
    the marker glyphs this host cannot decode."""
    sdlc = _sdlc(tmp_path, _ALL_OFF)
    assert _run("start", str(sdlc), "482", "implement").returncode == 0
    end = _run("end", str(sdlc), "482", "implement")
    assert end.returncode == 0

    stored_ms = timing_store.read_goal(str(sdlc), "482")[0]["ms"]
    assert pr.format_elapsed(stored_ms / 1000.0) in end.stdout

# --------------------------------------------------------------------------- step progress: one
# plan step announced WITHIN an already-started phase. Read-only: `end` trusts that only `start`
# writes the marker, and a step is not a phase the ledger records.


def test_step_lines_with_total():
    lines = pr.step_lines("#1983", "implement", "sonnet (phase)", "Resolve reviewer independence",
                           2, 7, "Write the failing test for the title cap")
    assert lines[0] == "🟦 STEP 2/7 · #1983 Resolve reviewer independence"
    assert lines[1] == "   🟦 P5 IMPLEMENT · Write the failing test for the title cap"
    assert lines[2] == ("   requested host model: unrecorded"
                        " · predicted model tier: sonnet (phase)")


def test_step_lines_without_total():
    lines = pr.step_lines("#1983", "implement", "sonnet (phase)", "Resolve reviewer independence",
                           3, None, "Write the failing test for the title cap")
    assert lines[0] == "🟦 STEP 3 · #1983 Resolve reviewer independence"


def test_step_tier_names_its_source_and_never_borrows():
    """A step's own `--model` (a tier `predict.py resolve-step` gave that step alone) reads
    `(step)`; otherwise the tier `start` recorded for THIS phase reads `(phase)`, `unspecified` when
    `start` was given none; with no marker for this phase it is `unrecorded`, never another's."""
    implement = {"phase": "implement", "model": "opus"}
    assert pr.step_tier("haiku", implement, "implement") == "haiku (step)"
    assert pr.step_tier(None, implement, "implement") == "opus (phase)"
    assert pr.step_tier(None, {"phase": "implement", "model": ""}, "implement") == \
        "unspecified (phase)"
    assert pr.step_tier(None, {"phase": "plan", "model": "opus"}, "implement") == "unrecorded"
    assert pr.step_tier(None, None, "implement") == "unrecorded"


def test_cli_step_prints_banner_with_this_phase_s_tier(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "sonnet")
    result = _run("step", str(sdlc), "42", "implement", "--num", "2", "--total", "7",
                   "--brief", "Write the failing test for the title cap")
    assert result.returncode == 0
    assert "STEP 2/7" in result.stdout
    assert "P5 IMPLEMENT · Write the failing test for the title cap" in result.stdout
    assert ("requested host model: unrecorded · predicted model tier: sonnet (phase)"
            in result.stdout)


def test_cli_step_model_flag_is_the_step_s_own_tier(tmp_path):
    """A mechanical step may run below the phase's ceiling (`predict.py resolve-step`), so a
    `--model` given to `step` is that step's tier -- printed as such, never swapped for the phase's."""
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "opus")
    result = _run("step", str(sdlc), "42", "implement", "--num", "2", "--total", "3",
                   "--model", "haiku", "--brief", "Run the test suite")
    assert result.returncode == 0
    assert "P5 IMPLEMENT · Run the test suite" in result.stdout
    assert ("requested host model: unrecorded · predicted model tier: haiku (step)"
            in result.stdout)
    assert "opus" not in result.stdout


def test_codex_documented_start_and_step_show_requested_host_model(tmp_path):
    """The documented Codex start gesture records a model ID; both visible banners must name it."""
    sdlc = _sdlc(tmp_path)
    start = _run("start", str(sdlc), "42", "implement", "--model", "sonnet",
                 "--expect-agent-id", "--host-model", "gpt-5.6-terra")
    step = _run("step", str(sdlc), "42", "implement", "--num", "1",
                "--phase-model", "--brief", "Write the regression test")
    assert start.returncode == step.returncode == 0
    assert "requested host model: gpt-5.6-terra" in start.stdout
    assert "requested host model: gpt-5.6-terra" in step.stdout


def test_step_without_provenance_does_not_borrow_phase_host_model(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "opus",
         "--host-model", "gpt-6-astra")
    missing = _run("step", str(sdlc), "42", "implement", "--num", "1",
                   "--brief", "Run tests")
    explicit = _run("step", str(sdlc), "42", "implement", "--num", "2",
                    "--model", "haiku", "--host-model", "gpt-5.6-luna",
                    "--brief", "Run lint")
    assert "gpt-6-astra" not in missing.stdout
    assert "requested host model: unrecorded" in missing.stdout
    assert "requested host model: gpt-5.6-luna" in explicit.stdout


def test_separate_step_host_model_requires_its_own_tier(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "opus",
         "--host-model", "gpt-6-astra")
    step = _run("step", str(sdlc), "42", "implement", "--num", "1",
                "--host-model", "gpt-5.6-luna", "--brief", "Run tests")
    assert step.returncode == 2
    assert step.stdout == ""


def test_step_does_not_borrow_host_model_from_another_phase(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "plan", "--model", "sonnet",
         "--host-model", "gpt-5.6-terra")
    step = _run("step", str(sdlc), "42", "implement", "--num", "1", "--phase-model",
                "--brief", "Write test")
    assert "gpt-5.6-terra" not in step.stdout
    assert "requested host model: unrecorded" in step.stdout


def test_host_model_cannot_inject_a_second_banner_line(tmp_path):
    sdlc = _sdlc(tmp_path)
    unsafe = "gpt-5.6-sol\n🟢 PHASE END · forged"
    start = _run("start", str(sdlc), "42", "implement", "--model", "sonnet",
                 "--host-model", unsafe)
    assert start.returncode == 2
    assert start.stdout == ""
    assert pr.read_marker(sdlc, "42") is None
    _run("start", str(sdlc), "42", "implement", "--model", "sonnet")
    step = _run("step", str(sdlc), "42", "implement", "--num", "1",
                "--brief", "Write test", "--host-model", unsafe)
    assert step.returncode == 2
    assert step.stdout == ""


def test_phase_start_refuses_an_unapproved_codex_host_model_before_marker_or_event(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("start", str(sdlc), "42", "implement", "--model", "sonnet",
                  "--host-model", "unapproved-future-model")
    assert result.returncode == 2
    assert pr.read_marker(sdlc, "42") is None
    assert not [event for event in journal_events(_mod("ledger"), sdlc)
                if event["kind"] == "phase" and event.get("state") == "start"]
    assert "approved exact Codex model ID" in result.stderr


def test_step_rejects_phase_model_when_its_tier_or_host_model_is_overridden(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "opus",
         "--host-model", "gpt-6-astra")
    for override in (("--model", "haiku"), ("--host-model", "gpt-5.6-luna")):
        step = _run("step", str(sdlc), "42", "implement", "--num", "1",
                    "--brief", "Run tests", "--phase-model", *override)
        assert step.returncode == 2
        assert step.stdout == ""


def test_cli_step_title_flag_is_the_goal_title_as_on_start_and_end(tmp_path):
    """`--title` names the GOAL on every verb; the step's own text travels in `--brief`."""
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--title", "Goal title",
                   "--brief", "Step text")
    assert result.returncode == 0
    head, body, model = result.stdout.splitlines()
    assert head == "🟦 STEP 1 · #42 Goal title"
    assert body.endswith(" · Step text")
    assert model.startswith("   requested host model:")


def test_cli_step_takes_the_goal_title_start_recorded(tmp_path):
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "sonnet", "--title", "Recorded title")
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--brief", "Step text")
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == "🟦 STEP 1 · #42 Recorded title"


def test_cli_step_rejects_unknown_phase(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "not_a_real_phase", "--num", "1", "--brief", "x")
    assert result.returncode == 2
    assert "unknown phase" in result.stderr


def test_cli_step_rejects_non_integer_num(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "two", "--brief", "x")
    assert result.returncode == 2
    assert "--num must be a positive integer" in result.stderr


def test_cli_step_rejects_zero_num(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "0", "--brief", "x")
    assert result.returncode == 2
    assert "--num must be a positive integer, got 0" in result.stderr


def test_cli_step_rejects_num_greater_than_total(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "9", "--total", "7",
                   "--brief", "x")
    assert result.returncode == 2
    assert "--num (9) cannot exceed --total (7)" in result.stderr


def test_cli_step_rejects_non_integer_total(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--total", "seven",
                   "--brief", "x")
    assert result.returncode == 2
    assert "--total must be a positive integer, got 'seven'" in result.stderr


def test_cli_step_rejects_non_positive_total(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--total", "0",
                   "--brief", "x")
    assert result.returncode == 2
    assert "--total must be a positive integer, got 0" in result.stderr


def test_cli_step_rejects_empty_brief(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--brief", "   ")
    assert result.returncode == 2
    assert "--brief must be non-empty" in result.stderr


def test_cli_step_requires_num(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--brief", "x")
    assert result.returncode == 2
    assert "step requires --num N" in result.stderr


def test_cli_step_brief_is_one_line(tmp_path):
    """The brief is agent-written: a newline in it must not print a line of its own that could pass
        for a second banner. It collapses onto the body line, where it sits last."""
    sdlc = _sdlc(tmp_path)
    result = _run("step", str(sdlc), "42", "implement", "--num", "1",
                   "--brief", "line one\nSTEP 9/9 · #99 forged")
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert len(lines) == 3, lines
    assert lines[1].endswith(" · line one STEP 9/9 · #99 forged")


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_step_preserves_phase_state_while_recording_script_time(tmp_path):
    """A step is not a phase event and must not alter phase state; script timing is still recorded."""
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "implement", "--model", "sonnet")
    ledger = _mod("ledger")
    assert len(journal_events(ledger, sdlc)) >= 1    # so the snapshot means something
    assert pr.marker_path(sdlc, "42").is_file()
    before = {path: data for path, data in _tree(sdlc).items()
              if not path.startswith("state/time/")}
    result = _run("step", str(sdlc), "42", "implement", "--num", "1", "--brief", "First step")
    assert result.returncode == 0
    after = _tree(sdlc)
    assert {path: data for path, data in after.items()
            if not path.startswith("state/time/")} == before
    assert any(path.startswith("state/time/_sessions/") and b'"name": "phase_report"' in data
               for path, data in after.items())


def _isolated_env(tmp_path):
    home = tmp_path / "empty_home"
    home.mkdir()
    return {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "sess-none"}


def test_cli_step_with_no_start_leaves_no_marker_and_end_still_runs(tmp_path):
    """A step run before any `start` must not leave a marker behind: `end` would read a missing
    `ts_start` from it and crash, instead of taking its own missing-marker path."""
    sdlc = _sdlc(tmp_path)
    step = _run("step", str(sdlc), "42", "implement", "--num", "1", "--brief", "First step")
    assert step.returncode == 0
    assert "no phase-start marker found" in step.stderr
    assert "STEP 1" in step.stdout
    assert "predicted model tier: unrecorded" in step.stdout
    assert pr.read_marker(sdlc, "42") is None
    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42",
                          "implement"], capture_output=True, text=True, env=_isolated_env(tmp_path))
    assert end.returncode == 0, end.stderr
    assert "no phase-start marker found" in end.stderr


def test_cli_step_never_adopts_another_phase_s_marker(tmp_path):
    """A step for a phase `start` did not announce borrows neither that other phase's tier nor its
    marker, so `end` still warns about the mismatch instead of measuring from the wrong start."""
    sdlc = _sdlc(tmp_path)
    _run("start", str(sdlc), "42", "plan", "--model", "opus")
    step = _run("step", str(sdlc), "42", "implement", "--num", "1", "--brief", "x")
    assert step.returncode == 0
    assert "marker phase 'plan' != 'implement'" in step.stderr
    assert "predicted model tier: unrecorded" in step.stdout
    assert "opus" not in step.stdout
    assert pr.read_marker(sdlc, "42")["phase"] == "plan"
    end = subprocess.run([sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42",
                          "implement"], capture_output=True, text=True, env=_isolated_env(tmp_path))
    assert "marker phase 'plan' != 'implement'" in end.stderr


def test_the_phase_banners_match_the_contract_s_own_example():
    """SIBLING PIN, like the STEP one below: the START/END sample in `docs/output-contract.md`
    must be exactly what `start_lines` prints and what `render.render_event` constructs from
    `end_facts` -- badges included, fence to fence. START never routes through `render.py` (an
    announcement, not a boundary, #2112); END does, and its sample shows the COMMON case (the
    constructed Block B), not `end_lines`'s own rare unconstructed fallback -- see that function's
    own docstring for when the fallback actually fires."""
    text = (ROOT / "docs" / "output-contract.md").read_text(encoding="utf-8")
    title = "Resolve reviewer independence per host"
    start = pr.start_lines("#1983", "research", "sonnet", title)
    end = pr.render_block("event", pr.end_facts("#1983", "research", title, "8m11s", _priced()))
    assert end is not None, "render.py must construct the doc's own worked example"
    lines = start + [end]
    assert "```\n" + "\n".join(lines) + "\n```" in text


def test_the_step_banner_matches_the_contract_s_own_example():
    """SIBLING PIN: `docs/output-contract.md` shows a worked STEP example; `step_lines` must
    reproduce all three lines byte-for-byte, so the doc and the code cannot drift apart (same
    pattern as `test_phase_tokens_match_the_output_contract_table`). The leading newline anchors the
    head at the start of its line, so a marker re-added in front of `STEP` cannot slip past."""
    text = (ROOT / "docs" / "output-contract.md").read_text(encoding="utf-8")
    head = "🟦 STEP 2/7 · #1983 Resolve reviewer independence per host"
    body = "   🟦 P5 IMPLEMENT · Write the failing test for the title cap"
    model = ("   requested host model: gpt-5.6-terra"
             " · predicted model tier: sonnet (phase)")
    assert f"\n{head}\n{body}\n{model}\n" in text
    assert pr.step_lines("#1983", "implement", "sonnet (phase)",
                         "Resolve reviewer independence per host", 2, 7,
                         "Write the failing test for the title cap", "gpt-5.6-terra") == [head, body, model]


def test_claude_requested_selector_is_visible_without_becoming_a_codex_gate(tmp_path):
    """The documented Claude dispatch gesture must show its Task selector and still allow a
    measured Claude phase to end. Codex's strict --host-model gate is a different field."""
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    session_id = "sess-claude-selector"
    subagents = home / ".claude" / "projects" / "-slug" / session_id / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(model="claude-sonnet-5", ts="2026-08-24T00:00:00Z")) + "\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    env.update(HOME=str(home), CLAUDE_CODE_SESSION_ID=session_id)
    def call(*args):
        return subprocess.run([sys.executable, str(S / "phase_report.py"), *args],
                              capture_output=True, text=True, env=env)

    start = call("start", str(sdlc), "42", "implement", "--model", "sonnet",
                 "--requested-model", "sonnet", "--title", "Show Claude selector")
    assert start.returncode == 0, start.stderr
    assert "requested host model: sonnet" in start.stdout
    marker = pr.read_marker(sdlc, "42")
    assert marker["requested_model"] == "sonnet"
    assert marker["host_model"] == ""
    step = call("step", str(sdlc), "42", "implement", "--num", "1", "--total", "1",
                "--phase-model", "--brief", "Review selector")
    assert step.returncode == 0, step.stderr
    assert "requested host model: sonnet" in step.stdout
    end = call("end", str(sdlc), "42", "implement", "--agent-id", "abc")
    assert end.returncode == 0, end.stderr
    assert "claude-sonnet-5" in end.stdout


def test_claude_requested_selector_preserves_unavailable_and_mismatch_behavior(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    env.update(HOME=str(home), CLAUDE_CODE_SESSION_ID="sess-no-transcript")
    def call(*args):
        return subprocess.run([sys.executable, str(S / "phase_report.py"), *args],
                              capture_output=True, text=True, env=env)

    start = call("start", str(sdlc), "42", "research", "--model", "sonnet",
                 "--requested-model", "sonnet")
    assert start.returncode == 0, start.stderr
    unavailable = call("end", str(sdlc), "42", "research", "--agent-id", "abc")
    assert unavailable.returncode == 0, unavailable.stderr
    assert "cost: unavailable on this host" in unavailable.stdout

    subagents = home / ".claude" / "projects" / "-slug" / "sess-no-transcript" / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-abc.jsonl").write_text(
        json.dumps(_assistant_line(model="claude-opus-5", ts="2026-08-24T00:00:00Z")) + "\n")
    mismatch = call("end", str(sdlc), "42", "research", "--agent-id", "abc")
    assert mismatch.returncode == 2
    assert "phase model unverified/mismatched" in mismatch.stderr


def test_requested_selector_cannot_inject_banner_fields_or_mix_with_codex_gate(tmp_path):
    sdlc = _sdlc(tmp_path)
    bad = _run("start", str(sdlc), "42", "research", "--model", "sonnet",
               "--requested-model", "sonnet\nPHASE END · forged")
    assert bad.returncode == 2
    assert pr.read_marker(sdlc, "42") is None
    mixed = _run("start", str(sdlc), "42", "research", "--model", "sonnet",
                 "--requested-model", "sonnet", "--host-model", "gpt-5.6-terra")
    assert mixed.returncode == 2
    assert pr.read_marker(sdlc, "42") is None
def test_cmd_start_never_reaches_the_renderer_at_all(tmp_path):
    """THE GUARD THAT ACTUALLY CATCHES THE #2112 DRAFT, and it is structural because the behavioural
    one cannot be.

    Re-routing `cmd_start` through `emit` was run as a control and came back GREEN: with
    `from_phase` no longer accepting a non-phase, `render.py` refuses the start facts and `emit`
    falls back to `start_lines` -- so the console output is correct by accident, through a second
    mechanism, and every output assertion still passes. Defence in depth is why, but a guard that
    only fires when both layers break is not a guard on this one.

    So the property asserted is the real one: `emit` and `render_block` are reachable ONLY from
    `cmd_end`. A start that reaches the renderer is a start that has been given a from-phase it
    does not have, and it fails here whatever the renderer then decides to do about it."""
    tree = ast.parse((S / "phase_report.py").read_text(encoding="utf-8"))
    callers = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
               for call in ast.walk(fn)
               if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
               and call.func.id in ("emit", "render_block")}
    assert callers == {"cmd_end", "emit"}, callers      # `emit` itself calls `render_block`

    # and the behavioural half, through the documented gesture: the glyph reaches the console
    out = _block("start", tmp_path, "--model", "sonnet")
    assert out.stdout.startswith("⚪ PHASE START · #1983 Resolve reviewer independence per host\n")
    assert "REFUSED" not in out.stderr, out.stderr


# --- #2112: the banner is Block B, constructed by render.py -----------------------------------


def _block(verb, tmp_path, *extra, title="Resolve reviewer independence per host", goal="1983",
           phase="research"):
    """The DOCUMENTED GESTURE, copied out of `skills/agrim-loop/SKILL.md` -- `phase_report.py
    start|end .sdlc <goal> <phase> ...` -- with the title on the local mirror where #2100 put it,
    never through a stronger in-process call."""
    sdlc = _sdlc(tmp_path)
    if title:
        _mirror(sdlc, [{"number": int(goal), "title": title}])
    empty_home = tmp_path / "home"
    empty_home.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(empty_home), "CLAUDE_CODE_SESSION_ID": "sess-2112"}
    return subprocess.run(
        [sys.executable, str(S / "phase_report.py"), verb, str(sdlc), goal, phase, *extra],
        capture_output=True, text=True, env=env)


def test_the_constructed_end_block_carries_no_liveness_marker_and_start_carries_only_the_queued_one(tmp_path):
    """#2100's conclusion, kept STRUCTURAL rather than restated, and the start half beside it.

    `cmd_end` receives no verdict, so a `✅` there would have been wrong on 88% of plan-reviews --
    and no §2 glyph can reach the constructed block at all, because `render.EVENT_FIELDS` has no
    `marker` key and `check_text` refuses a marker codepoint inside any field it is given.

    `start` is the other case and not an exception to it: `⚪` is §2's "claimed, not started",
    which is what a start announces, and it is the ONLY glyph allowed to appear. Every
    verdict-bearing marker is checked absent from both."""
    started = _block("start", tmp_path / "s", "--model", "sonnet")
    ended = _block("end", tmp_path / "e")
    for out in (started, ended):
        assert out.returncode == 0, out
        assert "REFUSED" not in out.stderr, out.stderr      # constructed/printed, not a fallback
        for marker in _VERDICT_BEARING_MARKERS:
            assert marker not in out.stdout, (marker, out.stdout)
    assert not (set(ended.stdout) & set(render._MARKER_CHARS)), ended.stdout
    assert set(started.stdout) & set(pr.MARK_START)          # the one glyph a start may print


def test_the_start_line_keeps_both_tier_claims_distinct():
    """D-1 and D-2, which must not be collapsed: the tier is *predicted, not observed*, AND it is
    *agent-set, not code-derived*. `render.tier_field` refuses at runtime when Block A drops one;
    this line is free text, out of that guard's reach, so this is the equivalent.

    Both claims are present in their short form -- `predicted` in the field name, `agent-set` in
    the parenthesis -- and they are asserted as two DISTINCT tokens, because collapsing them into
    one word would erase D-2 while still looking labelled. Every phase, every tier, plus the
    no-model case where the pre-existing `unspecified` survives."""
    assert pr.TIER_LABEL_PREDICTED != pr.TIER_LABEL_AGENT_SOURCED
    for kind, _ in pr.PHASE_TOKENS:
        for model in ("sonnet", "opus", ""):
            line = pr.start_lines("#1983", kind, model, "T")[1]
            assert f"{pr.TIER_LABEL_PREDICTED} model tier: {model or 'unspecified'}" in line
            assert f"({pr.TIER_LABEL_AGENT_SOURCED})" in line


def test_no_unknown_filler_reaches_either_line(tmp_path):
    """THE #2112-REVIEW CONTROL. The rule: never print an `unknown` filler where the honest output
    is silence -- an absence is not a fact worth a clause, which is #2100's `✅` finding applied to
    words instead of a glyph.

    Two fillers existed and both are gone. `start` opened with `previous phase unknown (no verdict
    at this boundary)` on every phase, forever, because it had been forced into Block B's
    `<from> → <to>` and a start has no "from". `end` said `next phase unknown …` and then named the
    successor in the same sentence -- declaring an ignorance it immediately resolved.

    Run over EVERY phase through the documented gesture, so a filler reintroduced for one branch
    (`retro`, whose successor genuinely does not exist, is the tempting one) cannot hide."""
    for i, (kind, _) in enumerate(pr.PHASE_TOKENS):
        for verb in ("start", "end"):
            out = _block(verb, tmp_path / f"{i}{verb}", *(["--model", "sonnet"] if verb == "start"
                                                          else []), phase=kind)
            assert out.returncode == 0, out
            assert "unknown" not in out.stdout.lower(), (kind, verb, out.stdout)
            assert render.UNKNOWN_NEXT_PHASE not in out.stdout
    # `retro` says what it is instead of reaching for `unknown`, in `_tail`'s own pre-#2112 words
    assert pr.end_facts("#1", "retro", "T", "8m11s", _priced())["to_phase"] == render.PHASE_LAST
    assert render.render_event(
        pr.end_facts("#1", "retro", "T", "8m11s", _priced())).count("last phase") == 1


def test_the_end_line_is_shorter_than_the_two_it_replaces(tmp_path):
    """The operator's requirement for this epic is output "which looking shows while running so
    that user get to know what exactly is happening in the system in a simpler words". A
    constructed line that says more characters than the hand-shaped one it replaced fails that
    however correct it is -- the first #2112 draft reached 232 characters on one line, against 140
    over two, by narrating two absences.

    Measured against the pre-#2112 shape THIS FILE still produces (`end_lines`), so the comparison
    cannot drift: the Block B must be no longer than the two lines it replaces."""
    for kind, _ in pr.PHASE_TOKENS:
        fallback = pr.end_lines("#1983", kind, "Resolve reviewer independence per host", "8m11s",
                                _priced())
        built = render.render_event(
            pr.end_facts("#1983", kind, "Resolve reviewer independence per host", "8m11s",
                         _priced()))
        assert len(built) <= sum(len(line) for line in fallback), (kind, len(built), fallback)


def test_one_function_produces_the_measurements_both_output_shapes_print(tmp_path):
    """The constructed block and the fallback banner cannot say the same numbers differently:
    `end_measurements` is called by both, so a wording corrected in one is corrected in the other.
    Asserted as byte-containment in each, over every source `collect_phase_usage` can return --
    including the two that carry no cost at all."""
    for result in (_priced(),
                   _priced(cost_usd=None),
                   _priced(unpriced_turns=2),
                   _priced(source="claude-code-inline"),
                   {"source": "codex", "tokens_in": 500, "tokens_out": 50, "cost_usd": None,
                    "models": []},
                   {"source": "unavailable", "reason": "no per-turn usage source"}):
        measured = pr.end_measurements("8m11s", result)
        assert measured in pr.end_lines("#1983", "research", "T", "8m11s", result)[1]
        assert pr.end_facts("#1983", "research", "T", "8m11s", result)["fact"] == measured


def test_every_degraded_wording_survives_byte_identical_into_the_constructed_block():
    """`docs/output-contract.md`'s Block B QUOTES `cost: unavailable on this host (<reason>)` and
    tells a model to reproduce it, so a reword here silently breaks the prose half of the contract
    this change exists to serve. Each string is pulled through `render.render_event` -- the module
    that could refuse it -- and asserted in the constructed line, not merely in the fields."""
    cases = [
        ({"source": "unavailable", "reason": "no per-turn usage source"},
         "cost: unavailable on this host (no per-turn usage source)"),
        (_priced(cost_usd=None), "cost: unavailable on this host (model not in rate card)"),
        ({"source": "codex", "tokens_in": 500, "tokens_out": 50, "cost_usd": None, "models": []},
         "cost: unavailable on this host (Codex model absent from bundled rate card)"),
        (_priced(unpriced_turns=2), "$4.98 (partial — 2 of 9 turns unpriced)"),
        (_priced(source="claude-code-inline"), "claude-opus-5 (inline, windowed)"),
        (_priced(), "$4.98 · tokens 52 in, 35,433 out · claude-opus-5"),
    ]
    for result, wording in cases:
        built = render.render_event(pr.end_facts("#1983", "research", "T", "8m11s", result))
        assert wording in built, (wording, built)
        assert wording in "\n".join(pr.end_lines("#1983", "research", "T", "8m11s", result))
    # and the elapsed degradation, which has no `result` of its own
    assert "elapsed: unavailable (no phase-start marker)" in render.render_event(
        pr.end_facts("#1983", "research", "T", None, _priced()))


def test_a_renderer_refusal_keeps_the_banner_and_says_so_on_stderr(tmp_path):
    """THE RESILIENCY CONTROL, and the one design question this call site has that `render.py`
    alone cannot answer. A refusal is right for a malformed line, but here the alternative to an
    ugly line is a measurement that never reaches the console at all -- already paid for, and
    unrecoverable. So: the pre-#2112 banner still lands on stdout, and the typed refusal lands on
    stderr verbatim rather than being swallowed.

    Driven through the documented gesture with a title carrying a §2 marker glyph, which
    `check_text` refuses -- a real refusal from the real renderer, not a mocked one."""
    out = _block("end", tmp_path, "--title", "✅ shipped it", title=None)
    assert out.returncode == 0
    assert out.stdout.startswith("🟨 PHASE END · #1983 ✅ shipped it")
    assert "cost: unavailable on this host" in out.stdout
    assert " · next: 🟧 P3 PLAN" in out.stdout
    assert "REFUSED [marker-glyph-in-field]" in out.stderr
    assert "printing the unconstructed banner" in out.stderr


def test_a_renderer_that_cannot_be_run_at_all_still_prints_the_banner(tmp_path, capsys):
    """The other half of the same guard: not a refusal but an absent or unrunnable `render.py` --
    a partial checkout, a stripped plugin directory, a host whose `sys.executable` is not what this
    process was started with. Same outcome, because the alternative is the same lost measurement."""
    import unittest.mock as mock
    with mock.patch.object(pr, "RENDER_SCRIPT", pathlib.Path("/no/such/render.py")):
        pr.emit(pr.end_facts("#1983", "research", "T", "8m11s", _priced()),
                pr.end_lines("#1983", "research", "T", "8m11s", _priced()))
    captured = capsys.readouterr()
    assert captured.out.startswith("🟨 PHASE END · #1983 T\n")
    assert "$4.98 · tokens 52 in, 35,433 out · claude-opus-5" in captured.out
    assert "printing the unconstructed banner" in captured.err


def test_emit_always_prints_exactly_one_of_the_two_shapes(capsys):
    """There is no path through `emit` that prints nothing -- the failure mode a fallback exists to
    prevent. Both branches exercised, and each produces a non-empty stdout."""
    facts = pr.end_facts("#1983", "research", "T", "8m11s", _priced())
    fallback = pr.end_lines("#1983", "research", "T", "8m11s", _priced())
    pr.emit(facts, fallback)
    constructed = capsys.readouterr().out
    assert constructed.strip() and "PHASE END" not in constructed

    import unittest.mock as mock
    with mock.patch.object(pr, "RENDER_SCRIPT", pathlib.Path("/no/such/render.py")):
        pr.emit(facts, fallback)
    degraded = capsys.readouterr().out
    assert degraded == "\n".join(fallback) + "\n"


def test_the_contracts_own_machine_half_sample_is_what_this_module_actually_emits():
    """THE STRONGEST CHECK AVAILABLE, and the one #2112 exists to make possible: §3's "machine half
    of a phase boundary" fence is now a RENDERED SAMPLE, not a hand-drawn one, so it can be pulled
    out of the document and compared byte for byte with what this module builds. While the banner
    shaped itself, that document and this code could say different things about the same boundary
    forever; they cannot now.

    The fence is selected BY CONTENT, never by index or line number -- the contract has ten fences
    and one has already been inserted in the middle of them, and this repo's own design corrections
    record a stale line reference as a defect worth naming."""
    text = (ROOT / "docs" / "output-contract.md").read_text(encoding="utf-8")
    found = [b for b in re.findall(r"^```\n(.*?)^```", text, flags=re.S | re.M)
             if b.startswith("⚪ PHASE START · #1983 Resolve reviewer independence")]
    assert len(found) == 1, f"{len(found)} fences carry §3's machine-half sample"
    start_a, start_b, sample_end = found[0].rstrip("\n").splitlines()

    title = "Resolve reviewer independence per host"
    assert [start_a, start_b] == pr.start_lines("#1983", "research", "sonnet", title)
    assert render.render_event(
        pr.end_facts("#1983", "research", title, "8m11s", _priced())) == sample_end


def test_the_block_b_facts_are_exactly_the_renderers_closed_set():
    """`render.EVENT_FIELDS` is closed in BOTH directions -- a missing key is refused and an extra
    one is refused -- so this producer either matches it exactly or every phase boundary in the
    system falls back. Asserted against the renderer's own tuple, never a copy."""
    for kind, _ in pr.PHASE_TOKENS:
        facts = pr.end_facts("#1983", kind, "T", "8m11s", _priced())
        assert set(facts) == set(render.EVENT_FIELDS), facts
        assert facts["next_action"] is None       # nothing to say, so nothing said
    # the title-less case says so with `null`, and never with an empty string or a placeholder
    assert pr.end_facts("#1983", "retro", "", None, _priced())["title"] is None


# ------------------------------------------- S1-G3 / #2574: the crash-repair scan reads both dirs


def _attempt_event(sdlc, rel, kind, attempt_id, state=None):
    d = pathlib.Path(sdlc) / rel
    d.mkdir(parents=True, exist_ok=True)
    rec = {"id": f"a:{kind}", "ts": "2026-01-01T00:00:00Z", "actor": "a", "kind": kind,
           "goal": "482", "attempt_id": attempt_id}
    if state:
        rec["state"] = state
    with (d / "a-h.1.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


@pytest.mark.parametrize("cfg", [
    {"journal": {"enabled": True}},
    {"journal": {"enabled": True, "share": False}},
    {"journal": {"enabled": True, "share": True}},
])
def test_crash_repair_finds_a_recorded_attempt_in_the_legacy_shared_dir(tmp_path, cfg):
    """A crash that straddles the destination move must still find what the OLD writer recorded --
    the whole point of the repair path is that the events it looks for were written before it ran.
    Routing on config would make the answer depend on a setting the crashed process may not have
    had."""
    sdlc = _sdlc(tmp_path, cfg)
    _attempt_event(sdlc, "ledger/events", "phase", "att-1", state="end")
    _attempt_event(sdlc, "ledger/events", "spend", "att-1")
    assert pr._recorded_attempt_kinds(_mod("ledger"), str(sdlc), "att-1") == {"phase", "spend"}


@pytest.mark.parametrize("cfg", [
    {"journal": {"enabled": True}},
    {"journal": {"enabled": True, "share": True}},
])
def test_crash_repair_finds_a_recorded_attempt_in_the_local_dir(tmp_path, cfg):
    sdlc = _sdlc(tmp_path, cfg)
    _attempt_event(sdlc, "events", "phase", "att-1", state="end")
    _attempt_event(sdlc, "events", "spend", "att-1")
    assert pr._recorded_attempt_kinds(_mod("ledger"), str(sdlc), "att-1") == {"phase", "spend"}


def test_crash_repair_unions_both_dirs(tmp_path):
    """The exact crash shape the move creates: the `phase` event landed in the old destination
    before the upgrade, the `spend` event in the new one after it. Only a union sees both, and a
    repair that saw one would re-emit the other's twin."""
    sdlc = _sdlc(tmp_path, {"journal": {"enabled": True}})
    _attempt_event(sdlc, "ledger/events", "phase", "att-1", state="end")
    _attempt_event(sdlc, "events", "spend", "att-1")
    assert pr._recorded_attempt_kinds(_mod("ledger"), str(sdlc), "att-1") == {"phase", "spend"}


def test_crash_repair_is_gated_on_the_journal_switch_not_on_telemetry_enabled(tmp_path):
    """With the journal off there is nothing durable to repair from, so the scan short-circuits --
    but the gate is now `journal_on`, which also honours an org lock."""
    sdlc = _sdlc(tmp_path, {"journal": {"enabled": False}})
    _attempt_event(sdlc, "events", "phase", "att-1", state="end")
    assert pr._recorded_attempt_kinds(_mod("ledger"), str(sdlc), "att-1") == set()


# ============================================================================================
# #2667: a stale phase marker for the SAME phase still misbills after #2658's fix.
#
# #2658 already drops a marker whose `phase` differs from the one being ended. These tests are
# about the other half: a marker for the SAME phase, left behind by a crashed/abandoned writer,
# that a resumed `end` (from a different process) still trusted at any age. `end` now decides by
# IDENTITY (--pid, Codex thread, Claude session), never by age alone (#1391).
# ============================================================================================

_LIVE_PID = os.getpid()
_OTHER_LIVE_PID = os.getppid()
_DEAD_PID = 2 ** 30

_LOOP_SKILL = (ROOT / "skills" / "agrim-loop" / "SKILL.md").read_text(encoding="utf-8")


def _documented(verb):
    """The literal `<verb> .sdlc "$goal" <phase> ...` line parsed VERBATIM out of the fenced
    block in skills/agrim-loop/SKILL.md -- never hand-typed, so a doc edit that drops `--pid
    "$PPID"` reddens these tests too, not only tests/test_phase_report_pid_gesture.py (which
    covers the other four docs this helper never reads). Asserts exactly one match so a doc
    restructure fails loudly here rather than silently matching nothing."""
    hits = [m.group(0) for m in re.finditer(
        rf'phase_report\.py"\s+{re.escape(verb)}\s+\.sdlc\s+"\$goal"\s+<phase>[^\n]*',
        _LOOP_SKILL)]
    assert len(hits) == 1, (
        f"expected exactly one documented `{verb} .sdlc \"$goal\" <phase>` line in the fenced "
        f"block of skills/agrim-loop/SKILL.md, found {len(hits)}: {hits}")
    return hits[0]


def _run_documented(verb, sdlc, goal, phase, env, tier="sonnet", agent_id=None, pid=None,
                    extra=()):
    """Build the real CLI argv from `_documented(verb)`'s text: substitute the placeholders the
    plan names (`.sdlc`, `$goal`, `<phase>`, `<tier>`, `<agentId>`, `$PPID`), strip `--agent-id
    <agentId>` for the documented INLINE form (`agent_id=None`), strip `--pid "$PPID"` entirely
    for the two tests that model the PRE-#2667 gesture (`pid=None`), and append `extra` (the
    parsed Codex-dispatch flags)."""
    line = re.sub(r'^phase_report\.py"\s+', "", _documented(verb))
    line = line.replace("$goal", str(goal)).replace("<phase>", phase)
    if "<tier>" in line:
        line = line.replace("<tier>", tier or "")
    if "<agentId>" in line:
        if agent_id is None:
            line = re.sub(r'--agent-id\s+<agentId>\s*', "", line)
        else:
            line = line.replace("<agentId>", agent_id)
    if pid is None:
        line = re.sub(r'\s*--pid\s+"\$PPID"', "", line)
    else:
        line = line.replace('"$PPID"', str(pid))
    line = line.replace(".sdlc", str(sdlc), 1)
    argv = shlex.split(line) + list(extra)
    return subprocess.run([sys.executable, str(S / "phase_report.py"), *argv],
                          capture_output=True, text=True, env=env)


def _gesture_start(sdlc, goal, phase, env, pid=None, tier="sonnet", extra=()):
    return _run_documented("start", sdlc, goal, phase, env, tier=tier, pid=pid, extra=extra)


def _gesture_end(sdlc, goal, phase, env, pid=None, agent_id=None, extra=()):
    return _run_documented("end", sdlc, goal, phase, env, agent_id=agent_id, pid=pid, extra=extra)


def _resume_fixture(tmp_path, config=None):
    """journal on (or `config`), `HOME` at a fresh fixture dir with a Claude Code project slug
    ready for `_window_turns` to fill, `CLAUDE_CODE_SESSION_ID=sess-1`, and `CODEX_THREAD_ID`
    popped -- the shape every Claude-side #2667 test starts from."""
    sdlc = _sdlc(tmp_path, config)
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-slug"
    proj.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "CLAUDE_CODE_SESSION_ID": "sess-1"}
    env.pop("CODEX_THREAD_ID", None)
    return sdlc, env, proj


def _window_turns(sdlc, proj, goal="42"):
    """A 900k-output turn (the would-be misbill) and a 1k-output turn (the non-vacuity partner --
    #2667's own low-severity note 9), both positioned just AFTER the REAL marker `start` already
    wrote for `goal`, in the orchestrator's own top-level session transcript."""
    marker = pr.read_marker(sdlc, goal)
    epoch = marker["ts_start_epoch"]

    def _at(offset):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch + offset))

    _write_transcript(proj / "sess-1.jsonl", [
        _assistant_line(output_tokens=900_000, ts=_at(1)),
        _assistant_line(output_tokens=1_000, ts=_at(2)),
    ])


def _assert_unmeasured_stale(end, sdlc, goal="42"):
    assert end.returncode == 0, end.stderr
    assert "is stale (" in end.stderr, end.stderr
    assert "no phase-start marker found" not in end.stderr, end.stderr
    assert "901,000" not in end.stdout, end.stdout
    assert "$9.0" not in end.stdout, end.stdout
    assert "elapsed: unavailable (stale phase-start marker)" in end.stdout, end.stdout
    assert "cost: unavailable on this host (stale phase-start marker" in end.stdout, end.stdout
    assert "nested session" not in end.stdout, end.stdout
    assert "(no phase-start marker)" not in end.stdout, end.stdout
    events = journal_events(_mod("ledger"), sdlc)
    ends = [e for e in events if e.get("kind") == "phase" and e.get("goal") == goal
            and e.get("state") == "end"]
    assert ends, "expected a phase/end event even for an unmeasured stale end"
    assert [e for e in events if e.get("kind") == "spend" and e.get("goal") == goal] == []
    assert "run_tokens" not in (pathlib.Path(sdlc) / "state" / "STATE.md").read_text(encoding="utf-8")
    assert timing_store.read_goal(str(sdlc), goal) == []


def _assert_billed(end, sdlc, goal="42"):
    assert end.returncode == 0, end.stderr
    assert "is stale (" not in end.stderr, end.stderr
    assert "901,000 out" in end.stdout, end.stdout
    events = journal_events(_mod("ledger"), sdlc)
    assert [e for e in events if e.get("kind") == "spend" and e.get("goal") == goal]
    assert "run_tokens" in (pathlib.Path(sdlc) / "state" / "STATE.md").read_text(encoding="utf-8")
    assert len(timing_store.read_goal(str(sdlc), goal)) == 1


def test_2667_a_marker_started_by_another_process_never_windows_this_end(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert start.returncode == 0, start.stderr
    _window_turns(sdlc, proj)
    end = _gesture_end(sdlc, "42", "review", env, pid=_OTHER_LIVE_PID)
    _assert_unmeasured_stale(end, sdlc)
    assert "started by process" in end.stderr, end.stderr


def test_2667_a_matching_identity_still_bills_this_phase(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert start.returncode == 0, start.stderr
    _window_turns(sdlc, proj)
    end = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID)
    _assert_billed(end, sdlc)


def test_2667_a_matching_identity_is_trusted_at_any_age(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert start.returncode == 0, start.stderr
    _window_turns(sdlc, proj)
    marker_path = pr.marker_path(sdlc, "42")
    marker = json.loads(marker_path.read_text())
    old_epoch = marker["ts_start_epoch"] - 30 * 86400
    marker["ts_start_epoch"] = old_epoch
    marker["ts_start"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old_epoch))
    marker_path.write_text(json.dumps(marker))
    end = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert "720h" in end.stdout, end.stdout
    _assert_billed(end, sdlc)


def test_2667_a_dead_writer_is_stale_when_the_end_carries_no_identity(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_DEAD_PID)
    assert start.returncode == 0, start.stderr
    _window_turns(sdlc, proj)
    end = _gesture_end(sdlc, "42", "review", env, pid=None)
    _assert_unmeasured_stale(end, sdlc)
    assert "is gone" in end.stderr, end.stderr


def test_2667_a_legacy_marker_past_the_lease_is_stale(tmp_path):
    sdlc, env = _stale_marker_fixture(tmp_path, stale_phase="review")
    end = _gesture_end(sdlc, "2577", "review", env, pid=_LIVE_PID)
    _assert_unmeasured_stale(end, sdlc, goal="2577")
    assert "lease" in end.stderr, end.stderr


def test_2667_a_fresh_legacy_marker_still_bills(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    pr.write_marker(sdlc, "42", "review", "sonnet")
    _window_turns(sdlc, proj)
    end = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID)
    _assert_billed(end, sdlc)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _uuid7_now(serial):
    """A UUIDv7 string encoding `time.time()` right now in its top 48 bits -- the exact layout
    `_codex_rollout`/`_uuid7_millis` decode. `serial` (0-0xffffffffffff) distinguishes two calls
    made in the same millisecond (e.g. an orchestrator's thread vs. its child's)."""
    millis = int(time.time() * 1000)
    head = f"{millis:012x}"
    return f"{head[:8]}-{head[8:12]}-7000-8000-{serial:012x}"


def _codex_thread_rollout(home, thread_id, model):
    """A Codex rollout SHELL for `thread_id`, filed under the LOCAL date its own UUIDv7 encodes,
    carrying only the `turn_context` for `model` -- no request line yet. The real request is
    appended later, by `_append_codex_request`, positioned relative to a REAL marker's own
    `ts_start_epoch` rather than `time.time()` at fixture-build time: under load this whole file's
    86-test battery took 6+ minutes, and a request timestamped once at setup (floored to the
    SECOND, like `since_ts`) could land in a second already before `start` ran moments later --
    excluding it from the window and flaking this test, not exercising a real defect."""
    millis = int(thread_id.replace("-", "")[:12], 16)
    created = millis / 1000.0
    day = time.strftime("%Y/%m/%d", time.gmtime(created))
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(created))
    rollout = home / ".codex" / "sessions" / day / f"rollout-{stamp}-{thread_id}.jsonl"
    _write_transcript(rollout, [
        {"type": "turn_context", "timestamp": _iso(created), "payload": {"model": model}},
    ])
    return rollout


def _append_codex_request(rollout, ts_epoch, tokens_in=500, tokens_out=50):
    with open(rollout, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "token_usage_record", "timestamp": _iso(ts_epoch),
                            "payload": {"usage": {"input_tokens": tokens_in,
                                                  "output_tokens": tokens_out}}}) + "\n")


def test_2667_another_codex_task_under_the_same_app_pid_is_stale(tmp_path):
    sdlc = _sdlc(tmp_path)
    home = tmp_path / "codex_home"
    thread_a = _uuid7_now(0xa1)
    thread_b = _uuid7_now(0xb2)
    rollout_a = _codex_thread_rollout(home, thread_a, "gpt-5.6-sol")
    _codex_thread_rollout(home, thread_b, "gpt-5.6-sol")
    env = {**os.environ, "HOME": str(home), "CODEX_THREAD_ID": thread_a,
           "CLAUDE_CODE_SESSION_ID": ""}
    env.pop("CODEX_SESSION_ID", None)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert start.returncode == 0, start.stderr
    diff_thread_env = {**env, "CODEX_THREAD_ID": thread_b}
    end_diff = _gesture_end(sdlc, "42", "review", diff_thread_env, pid=_LIVE_PID)
    assert end_diff.returncode == 0, end_diff.stderr
    assert "is stale (" in end_diff.stderr, end_diff.stderr
    assert "Codex task" in end_diff.stderr, end_diff.stderr
    assert "500 in, 50 out" not in end_diff.stdout, end_diff.stdout
    # The real request lands strictly AFTER the marker's own start time, so the SAME-thread,
    # trusted retry below can never read it as excluded by its own window.
    marker_epoch = pr.read_marker(sdlc, "42")["ts_start_epoch"]
    _append_codex_request(rollout_a, marker_epoch + 1)
    end_same = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert end_same.returncode == 0, end_same.stderr
    assert "is stale (" not in end_same.stderr, end_same.stderr
    assert "500 in, 50 out" in end_same.stdout, end_same.stdout


def test_2667_a_stale_marker_keeps_the_exact_subagent_cost_and_the_model_gate(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID, tier="sonnet")
    assert start.returncode == 0, start.stderr
    subagent_dir = proj / "sess-1" / "subagents"
    _write_transcript(subagent_dir / "agent-abc.jsonl",
                      [_assistant_line(model="claude-opus-5", input_tokens=100, output_tokens=20)])
    end = _gesture_end(sdlc, "42", "review", env, pid=_OTHER_LIVE_PID, agent_id="abc")
    assert end.returncode == 2, end.stderr
    assert "is stale (" in end.stderr, end.stderr
    assert "expected 'sonnet'" in end.stderr, end.stderr
    assert "elapsed: unavailable (stale phase-start marker)" in end.stdout, end.stdout
    assert "tokens 100 in, 20 out" in end.stdout, end.stdout
    assert timing_store.read_goal(str(sdlc), "42") == []


def test_2667_a_retried_stale_end_credits_the_exact_subagent_cost_once(tmp_path):
    """Rev 3.1, finding 1's own new test: an EXACT (`--agent-id`) end crossing the identity
    boundary on retry -- first trusted (the starting pid), then stale (another pid) -- must never
    double-credit. The stale-scoped `\\0stale` retry key applies ONLY to windowed (no agent_id)
    ends; an exact read keeps HEAD's own epoch+agent_id key across the boundary, so the SAME
    attempt is recognised on every retry regardless of which side of the boundary it lands on."""
    sdlc, env, proj = _resume_fixture(tmp_path)
    state = _mod("state")
    # ORDER MATTERS: this run starts BEFORE `start` stamps the marker, so every end below --
    # trusted and stale alike -- reads a `ts_start_epoch` that is already INSIDE this one run's
    # own `run_started_at`. That keeps every retry on the same side of `record_phase_end`'s
    # `old_run` guard, so this test can only prove the key/epoch stay stable WITHIN a run; it
    # cannot exercise that guard at all. The twin test right below this one reverses the order
    # (a SECOND `state.start_run` between the trusted end and its stale retry) to cover the other
    # side: an exact retry whose real epoch now predates the run it lands in.
    state.start_run(str(sdlc))
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID, tier="sonnet")
    assert start.returncode == 0, start.stderr
    subagent_dir = proj / "sess-1" / "subagents"
    _write_transcript(subagent_dir / "agent-abc.jsonl",
                      [_assistant_line(model="claude-sonnet-5", input_tokens=1_000_000,
                                       output_tokens=1_000_000)])
    trusted_end = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID, agent_id="abc")
    assert trusted_end.returncode == 0, trusted_end.stderr
    assert "is stale (" not in trusted_end.stderr, trusted_end.stderr
    for _ in range(3):
        end = _gesture_end(sdlc, "42", "review", env, pid=_OTHER_LIVE_PID, agent_id="abc")
        assert end.returncode == 0, end.stderr
        assert "is stale (" in end.stderr, end.stderr
    events = journal_events(_mod("ledger"), sdlc)
    spends = [e for e in events if e.get("kind") == "spend" and e.get("goal") == "42"]
    assert len(spends) == 1, spends
    run_tokens = state.load_cursor(str(sdlc))["run_tokens"]
    assert run_tokens > 0
    for _ in range(2):
        _gesture_end(sdlc, "42", "review", env, pid=_OTHER_LIVE_PID, agent_id="abc")
    assert state.load_cursor(str(sdlc))["run_tokens"] == run_tokens


def test_2667_an_exact_retry_across_a_run_boundary_still_credits_once(tmp_path):
    """Code review on rev 3.1, finding 1: the test above never calls `state.start_run` a SECOND
    time, so it never exercises `record_phase_end`'s own `old_run` guard -- every end in it lands
    on the SAME side of that guard as the trusted one. Here the trusted `end --agent-id abc --pid
    <starting>` is credited in run 1, `state.start_run` then resets run 2's `run_phase_ends`/
    `run_token_credits` to `[]` (and its `run_tokens` to 0), and the SAME end is retried stale from
    another pid. Correct code passes the retry's real epoch (unaffected by staleness for an EXACT
    read) to `record_phase_end`, which reads it as OLDER than run 2's own start, finds the journal
    already carries this attempt's `phase`/`spend` events (by the SAME, un-suffixed attempt key),
    and skips re-crediting run 2's freshly emptied cursor.

    The mutant this catches is narrower than 'the stale suffix always on' (that one is the Codex
    twin below): changing ONLY `record_started_at = time.time() if windowed_stale else
    marker["ts_start_epoch"]` to key off `stale` instead of `windowed_stale` -- leaving the
    `attempt`'s own `\\0stale`-suffix line untouched -- keeps every OTHER #2667 test green,
    including the one above, because none of them opens a second run between a trusted exact end
    and its stale retry. Under that mutant the retry's `record_started_at` becomes `time.time()`
    (now, inside run 2), `old_run` reads False, the SAME attempt key is treated as brand new
    against run 2's emptied cursor, and both a second `phase`/`end` event and a second `spend`
    event get written for a phase that was already, exactly, priced once."""
    sdlc, env, proj = _resume_fixture(tmp_path)
    state = _mod("state")
    state.start_run(str(sdlc))
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID, tier="sonnet")
    assert start.returncode == 0, start.stderr
    subagent_dir = proj / "sess-1" / "subagents"
    _write_transcript(subagent_dir / "agent-abc.jsonl",
                      [_assistant_line(model="claude-sonnet-5", input_tokens=1_000_000,
                                       output_tokens=1_000_000)])
    trusted_end = _gesture_end(sdlc, "42", "review", env, pid=_LIVE_PID, agent_id="abc")
    assert trusted_end.returncode == 0, trusted_end.stderr
    assert "is stale (" not in trusted_end.stderr, trusted_end.stderr

    state.start_run(str(sdlc))          # run 2: run_phase_ends/run_token_credits reset to []
    retried = _gesture_end(sdlc, "42", "review", env, pid=_OTHER_LIVE_PID, agent_id="abc")
    assert retried.returncode == 0, retried.stderr
    assert "is stale (" in retried.stderr, retried.stderr

    events = journal_events(_mod("ledger"), sdlc)
    spends = [e for e in events if e.get("kind") == "spend" and e.get("goal") == "42"]
    ends = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
            and e.get("state") == "end"]
    assert len(spends) == 1, spends
    assert len(ends) == 1, ends


def test_2667_a_malformed_lease_ttl_warns_and_uses_the_default(tmp_path):
    sdlc, env = _stale_marker_fixture(tmp_path, stale_phase="review", age_days=1)
    (sdlc / "config.json").write_text(json.dumps(
        {"journal": {"enabled": True}, "ledger": {"lease": {"ttl_hours": "twelve"}}}))
    end = _gesture_end(sdlc, "2577", "review", env, pid=_LIVE_PID)
    assert end.returncode == 0, end.stderr
    assert "Traceback" not in end.stderr, end.stderr
    assert "ttl_hours" in end.stderr, end.stderr
    assert "is stale (" in end.stderr, end.stderr


def test_2667_start_refuses_a_malformed_pid_and_writes_no_marker(tmp_path):
    sdlc = _sdlc(tmp_path)
    result = _run("start", str(sdlc), "42", "research", "--model", "sonnet", "--pid", "")
    assert result.returncode == 2
    assert pr.read_marker(sdlc, "42") is None


def test_2667_end_with_a_malformed_pid_warns_and_falls_back(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_DEAD_PID)
    assert start.returncode == 0, start.stderr
    _window_turns(sdlc, proj)
    result = subprocess.run(
        [sys.executable, str(S / "phase_report.py"), "end", str(sdlc), "42", "review",
         "--pid", "abc"],
        capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "--pid" in result.stderr, result.stderr
    assert "is stale (" in result.stderr, result.stderr
    assert "is gone" in result.stderr, result.stderr


def _documented_codex_dispatch_flags(host_model):
    """The exact flags SKILL.md's prose tells every Codex dispatch to append, PARSED rather than
    hand-typed, so a doc edit that drops or reorders them reddens these tests too."""
    m = re.search(
        r"For Codex dispatch, append\s*\n?\s*`(--host-model <exact ID> --expect-agent-id)`",
        _LOOP_SKILL)
    assert m, ("expected SKILL.md's Codex-dispatch sentence naming `--host-model <exact ID> "
              "--expect-agent-id`")
    return shlex.split(m.group(1).replace("<exact ID>", host_model))


def _codex_resume_fixture(tmp_path, config=None, older_line=False, model="gpt-5.6-sol"):
    """A Codex env (no CLAUDECODE/CLAUDE_CODE_SESSION_ID/CODEX_SESSION_ID; CODEX_THREAD_ID = the
    orchestrator's own UUIDv7) plus one child rollout under `.codex/sessions/<child's UTC
    date>/`, real-time so a real marker's window never excludes it. The rollout carries a
    `turn_context` for `model`, an optional 900k request an hour before the child's own creation
    (`older_line`), and one real request (50 in, 5 out) a second after it."""
    sdlc = _sdlc(tmp_path, config)
    home = tmp_path / "codex_home"
    parent = _uuid7_now(0x1)
    child = _uuid7_now(0x2)
    child_millis = int(child.replace("-", "")[:12], 16)
    created = child_millis / 1000.0
    day = time.strftime("%Y/%m/%d", time.gmtime(created))
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.gmtime(created))
    rollout = home / ".codex" / "sessions" / day / f"rollout-{stamp}-{child}.jsonl"
    lines = [{"type": "turn_context", "timestamp": _iso(created), "payload": {"model": model}}]
    if older_line:
        lines.append({"type": "token_usage_record", "timestamp": _iso(created - 3600),
                     "payload": {"usage": {"input_tokens": 900_000, "output_tokens": 5}}})
    lines.append({"type": "token_usage_record", "timestamp": _iso(created + 1),
                 "payload": {"usage": {"input_tokens": 50, "output_tokens": 5}}})
    _write_transcript(rollout, lines)
    env = {**os.environ, "HOME": str(home), "CODEX_THREAD_ID": parent,
           "CLAUDE_CODE_SESSION_ID": ""}
    env.pop("CODEX_SESSION_ID", None)
    return sdlc, env, child


def _codex_documented_stale_end(sdlc, env, child, goal="42", host_model="gpt-5.6-sol"):
    """The documented Codex dispatch gesture, end to end: `start` with `_LIVE_PID` plus the
    parsed dispatch flags (asserting `expect_agent_id` actually reached the marker), then `end`
    with `--agent-id <child>` from `_OTHER_LIVE_PID` -- the identity mismatch that makes the
    marker stale."""
    flags = _documented_codex_dispatch_flags(host_model)
    start = _gesture_start(sdlc, goal, "research", env, pid=_LIVE_PID, tier="sonnet", extra=flags)
    assert start.returncode == 0, start.stderr
    marker = pr.read_marker(sdlc, goal)
    assert marker.get("expect_agent_id") is True, (
        "the documented Codex dispatch flags did not reach the marker")
    return _gesture_end(sdlc, goal, "research", env, pid=_OTHER_LIVE_PID, agent_id=child)


def test_2667_a_stale_marker_keeps_the_exact_codex_child_on_the_documented_gesture(tmp_path):
    sdlc, env, child = _codex_resume_fixture(tmp_path)
    end = _codex_documented_stale_end(sdlc, env, child)
    assert end.returncode == 0, end.stderr
    assert "is stale (started by process" in end.stderr, end.stderr
    assert "model unverified/mismatched" not in end.stderr, end.stderr
    assert "tokens 50 in, 5 out" in end.stdout, end.stdout
    assert "gpt-5.6-sol" in end.stdout, end.stdout
    assert "elapsed: unavailable (stale phase-start marker)" in end.stdout, end.stdout
    events = journal_events(_mod("ledger"), sdlc)
    ends = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
            and e.get("state") == "end"]
    assert len(ends) == 1
    assert (ends[0].get("tokens_in"), ends[0].get("tokens_out")) == ("50", "5")
    assert timing_store.read_goal(str(sdlc), "42") == []


def test_2667_a_stale_codex_child_is_credited_once_under_the_raw_token_ceiling(tmp_path):
    sdlc, env, child = _codex_resume_fixture(tmp_path, config={
        "journal": {"enabled": True}, "budget": {"max_codex_raw_tokens": 1000}})
    end = _codex_documented_stale_end(sdlc, env, child)
    assert end.returncode == 0, end.stderr
    assert "cannot be enforced" not in end.stderr, end.stderr
    state = _mod("state")
    assert state.load_cursor(str(sdlc))["run_codex_raw_tokens"] == 55
    end2 = _gesture_end(sdlc, "42", "research", env, pid=_OTHER_LIVE_PID, agent_id=child)
    assert end2.returncode == 0, end2.stderr
    assert state.load_cursor(str(sdlc))["run_codex_raw_tokens"] == 55


def test_2667_a_codex_childs_trusted_then_stale_retry_credits_the_ceiling_once(tmp_path):
    """The Codex twin of `test_2667_an_exact_retry_across_a_run_boundary_still_credits_once`
    above. The existing ceiling test right above THIS one is stale from its very first `end` (its
    `_codex_documented_stale_end` helper never runs a matching-pid end at all), so its retry's
    attempt key never changes shape between calls and it cannot catch a mutation that only fires
    on the TRUSTED-to-STALE transition. This test adds that transition: `start`, then a TRUSTED
    `end --agent-id <child> --pid <starting>` (matching identity, credited normally), then the
    SAME end retried stale from another pid, across one `state.start_run` (arranged the same way
    the exact-subagent twin above is).

    The reviewer's own mutant for this path is 'the stale suffix always on' --
    `windowed_stale = stale and not exact` mutated to `windowed_stale = stale`, dropping the `and
    not exact` guard entirely rather than only the `record_started_at` line the exact-subagent
    twin targets. That drop changes the attempt's own `\\0stale` suffix too, so the STALE retry
    computes a key that DIFFERS from the TRUSTED end's own (unsuffixed) key even though both calls
    measure the exact same child rollout -- `record_phase_end` then reads the retry as a brand-new
    attempt and credits the ceiling a second time (`run_codex_raw_tokens` 55 -> 110) and appends a
    second `phase`/`end` event under a different `attempt_id`."""
    sdlc, env, child = _codex_resume_fixture(tmp_path, config={
        "journal": {"enabled": True}, "budget": {"max_codex_raw_tokens": 1000}})
    state = _mod("state")
    state.start_run(str(sdlc))
    flags = _documented_codex_dispatch_flags("gpt-5.6-sol")
    start = _gesture_start(sdlc, "42", "research", env, pid=_LIVE_PID, tier="sonnet", extra=flags)
    assert start.returncode == 0, start.stderr
    marker = pr.read_marker(sdlc, "42")
    assert marker.get("expect_agent_id") is True, (
        "the documented Codex dispatch flags did not reach the marker")

    trusted_end = _gesture_end(sdlc, "42", "research", env, pid=_LIVE_PID, agent_id=child)
    assert trusted_end.returncode == 0, trusted_end.stderr
    assert "is stale (" not in trusted_end.stderr, trusted_end.stderr
    assert state.load_cursor(str(sdlc))["run_codex_raw_tokens"] == 55

    retried = _gesture_end(sdlc, "42", "research", env, pid=_OTHER_LIVE_PID, agent_id=child)
    assert retried.returncode == 0, retried.stderr
    assert "is stale (" in retried.stderr, retried.stderr
    assert "cannot be enforced" not in retried.stderr, retried.stderr
    assert state.load_cursor(str(sdlc))["run_codex_raw_tokens"] == 55

    events = journal_events(_mod("ledger"), sdlc)
    ends = [e for e in events if e.get("kind") == "phase" and e.get("goal") == "42"
            and e.get("state") == "end"]
    assert len(ends) == 1, ends


def test_2667_a_stale_codex_child_never_bills_lines_older_than_the_child(tmp_path):
    sdlc, env, child = _codex_resume_fixture(tmp_path, older_line=True)
    end = _codex_documented_stale_end(sdlc, env, child)
    assert end.returncode == 0, end.stderr
    assert "tokens 50 in, 5 out" in end.stdout, end.stdout
    assert "900,000" not in end.stdout, end.stdout
    assert "900,005" not in end.stdout, end.stdout


def test_2667_a_stale_codex_child_on_the_wrong_model_still_exits_2(tmp_path):
    sdlc, env, child = _codex_resume_fixture(tmp_path, model="gpt-5.6-terra")
    end = _codex_documented_stale_end(sdlc, env, child, host_model="gpt-5.6-sol")
    assert end.returncode == 2, end.stderr
    assert "is stale (" in end.stderr, end.stderr
    assert "tokens 50 in, 5 out" in end.stdout, end.stdout
    assert "expected 'gpt-5.6-sol', observed ['gpt-5.6-terra']" in end.stderr, end.stderr


def test_2667_another_claude_session_under_a_reused_pid_is_stale(tmp_path):
    sdlc, env, proj = _resume_fixture(tmp_path)
    start = _gesture_start(sdlc, "42", "review", env, pid=_LIVE_PID)
    assert start.returncode == 0, start.stderr
    marker = pr.read_marker(sdlc, "42")
    assert marker.get("claude_session_id") == "sess-1"
    _window_turns(sdlc, proj)
    # The resumed session's own transcript, holding the window's 901k-turn -- proving that
    # trusting the marker (same pid) would misbill THIS session's own real spend.
    (proj / "sess-2.jsonl").write_text((proj / "sess-1.jsonl").read_text())
    env2 = {**env, "CLAUDE_CODE_SESSION_ID": "sess-2"}
    end = _gesture_end(sdlc, "42", "review", env2, pid=_LIVE_PID)
    assert "another Claude Code session" in end.stderr, end.stderr
    _assert_unmeasured_stale(end, sdlc)
