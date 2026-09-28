import ast
import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ocr = _mod("orchestrator_context_report")


def _run(*args):
    return subprocess.run(
        [sys.executable, str(S / "orchestrator_context_report.py"), *args],
        capture_output=True, text=True,
    )


def _write_transcript(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _assistant_line(message_id="msg_1", model="claude-sonnet-5", input_tokens=100,
                     output_tokens=20, cache_read=0, cache_creation=0,
                     ts="2026-09-18T10:00:00.000Z"):
    """One raw JSONL line -- callers combine several with the SAME message_id to build a
    multi-content-block turn (thinking/tool_use/text), the exact shape #2531's research measured
    (72 raw lines, 32 unique message.id on a real transcript)."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "cache_creation": {"ephemeral_5m_input_tokens": cache_creation,
                                    "ephemeral_1h_input_tokens": 0},
            },
        },
    }


# --------------------------------------------------------------------------- Task 1


def test_iter_raw_assistant_calls_yields_one_entry_per_raw_line_with_message_id(tmp_path):
    """Deliberately one-entry-PER-LINE (no grouping here) -- matches phase_report.iter_assistant_
    turns's own per-line contract, which this module's own iter_raw_assistant_calls now reuses
    additively (message_id was added to that function's own yielded dict -- Plan-Review
    refinement 3; see this module's docstring)."""
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        _assistant_line(message_id="msg_1", input_tokens=10),
        _assistant_line(message_id="msg_1", input_tokens=10),  # 2nd content block, same call
        _assistant_line(message_id="msg_2", input_tokens=20),
    ])
    calls = list(ocr.iter_raw_assistant_calls(t))
    assert len(calls) == 3                       # NOT deduped yet -- that's dedup_calls's job
    assert [c["message_id"] for c in calls] == ["msg_1", "msg_1", "msg_2"]


def test_iter_raw_assistant_calls_skips_synthetic_model_and_non_assistant_lines(tmp_path):
    t = tmp_path / "t.jsonl"
    _write_transcript(t, [
        _assistant_line(message_id="msg_1"),
        {**_assistant_line(message_id="msg_2"), "message": {"role": "assistant",
                                                              "model": "<synthetic>", "usage": {}}},
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ])
    calls = list(ocr.iter_raw_assistant_calls(t))
    assert len(calls) == 1
    assert calls[0]["message_id"] == "msg_1"


def test_dedup_calls_collapses_multiple_lines_sharing_one_message_id_to_one_call():
    """TD-1 regression guard -- the exact shape #2531's research measured on a real transcript:
    a message.id spanning multiple lines must become ONE logical call, not N. Built from literal
    dicts (the shape dedup_calls consumes), not derived from iter_raw_assistant_calls's own
    output -- the two functions are tested independently."""
    raw = [
        {"ts": "2026-09-18 10:00:00", "message_id": "msg_1", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 5, "output_tokens": 0, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0}},
        {"ts": "2026-09-18 10:00:00", "message_id": "msg_1", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0}},
        {"ts": "2026-09-18 10:00:01", "message_id": "msg_2", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 8, "output_tokens": 1, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0}},
    ]
    deduped = ocr.dedup_calls(raw)
    assert len(deduped) == 2


def test_dedup_calls_keeps_the_last_lines_usage_not_the_first_when_they_differ():
    """The sharper test Plan §1b adds beyond the dossier's own byte-identical sample: usage
    GROWS across a duplicate group (a real, documented possibility per a downstream transcript
    reader's GRAIN section) -- the LAST line's larger, complete usage must win, not
    the first line's partial snapshot."""
    raw = [
        {"ts": "2026-09-18 10:00:00", "message_id": "msg_1", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0}},          # partial snapshot
        {"ts": "2026-09-18 10:00:00", "message_id": "msg_1", "model": "claude-sonnet-5",
         "usage": {"input_tokens": 2, "output_tokens": 275, "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0}},          # complete total
    ]
    deduped = ocr.dedup_calls(raw)
    assert len(deduped) == 1
    assert deduped[0]["usage"]["output_tokens"] == 275          # last, not first (5) or sum (280)


def test_dedup_calls_uses_min_ts_across_a_group():
    raw = [
        {"ts": "2026-09-18 10:00:05", "message_id": "msg_1", "model": "m",
         "usage": {"input_tokens": 1}},
        {"ts": "2026-09-18 10:00:00", "message_id": "msg_1", "model": "m",
         "usage": {"input_tokens": 1}},
    ]
    deduped = ocr.dedup_calls(raw)
    assert deduped[0]["ts"] == "2026-09-18 10:00:00"


def test_dedup_calls_never_merges_lines_with_no_message_id():
    """A malformed/absent id must not be silently grouped with an unrelated line -- each None-id
    line stays its own call."""
    raw = [
        {"ts": "t1", "message_id": None, "model": "m", "usage": {"input_tokens": 1}},
        {"ts": "t2", "message_id": None, "model": "m", "usage": {"input_tokens": 1}},
    ]
    assert len(ocr.dedup_calls(raw)) == 2


# --------------------------------------------------------------------------- Task 2


def test_peak_context_takes_the_max_never_the_sum_across_calls():
    """Regression guard for the #1 wrong-but-plausible definition #2531's research flagged:
    summing context instead of taking its max."""
    calls = [
        {"ts": "t1", "model": "m", "usage": {"input_tokens": 100, "cache_read_input_tokens": 0,
                                              "cache_creation_input_tokens": 0}},
        {"ts": "t2", "model": "m", "usage": {"input_tokens": 50, "cache_read_input_tokens": 900,
                                              "cache_creation_input_tokens": 0}},
    ]
    assert ocr.peak_context(calls) == 950          # max(100, 950), NOT 100+950=1050
    assert ocr.peak_context([]) == 0


def test_peak_context_sums_the_three_context_fields_for_one_call():
    calls = [{"ts": "t1", "model": "m",
              "usage": {"input_tokens": 10, "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 30}}]
    assert ocr.peak_context(calls) == 60


def test_volume_totals_sums_input_output_cache_read_cache_creation_across_calls():
    calls = [
        {"ts": "t1", "model": "m", "usage": {"input_tokens": 10, "output_tokens": 1,
                                              "cache_read_input_tokens": 2,
                                              "cache_creation_input_tokens": 3}},
        {"ts": "t2", "model": "m", "usage": {"input_tokens": 10, "output_tokens": 1,
                                              "cache_read_input_tokens": 2,
                                              "cache_creation_input_tokens": 3}},
    ]
    assert ocr.volume_totals(calls) == 2 * (10 + 1 + 2 + 3)


def test_priced_totals_reuses_phase_reports_price_turn_unmodified(tmp_path):
    """Integration guard against silently reimplementing pricing divergently: a known
    usage+rate-row combination must price IDENTICALLY through priced_totals as through calling
    phase_report.price_turn directly.

    The usage dict below carries a nested 'cache_creation': {...} (matching the real Anthropic
    usage shape, and this file's own _assistant_line fixture) -- the plan's first draft of this
    test omitted it, which made phase_report.price_turn correctly return None (its own
    documented all-or-nothing rule: cache_write_5m/cache_write_1h look up ('cache_creation',
    'ephemeral_*_input_tokens') and a MISSING nested dict is 'never observed', not 'confirmed
    zero', which poisons the whole turn) -- a broken fixture producing a vacuous None==None
    pass-through, not a bug in priced_totals itself (confirmed directly: calling price_turn on
    the exact same malformed usage dict outside this module returns None too). Fixed at the
    fixture, not the implementation, per this repo's own TDD rule that a fixture satisfying an
    assertion without exercising the real path is the thing that's broken."""
    pr = _mod("phase_report")
    calls = [{"ts": "2026-09-01 00:00:00", "model": "claude-sonnet-5",
              "usage": {"input_tokens": 1_000_000, "output_tokens": 0,
                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                        "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                           "ephemeral_1h_input_tokens": 0}}}]
    rates = [{"model": "claude-sonnet-5", "rate_kind": "input", "usd_per_mtok": 2.0,
              "usd_per_request": None, "effective_from": "2026-01-01 00:00:00",
              "effective_to": None}] + [
        {"model": "claude-sonnet-5", "rate_kind": k, "usd_per_mtok": 0.0, "usd_per_request": 0.0,
         "effective_from": "2026-01-01 00:00:00", "effective_to": None}
        for k in ("output", "cache_read", "cache_write_5m", "cache_write_1h",
                  "web_search", "web_fetch")
    ]
    expected = pr.price_turn(calls[0], rates)
    cost_usd, unpriced = ocr.priced_totals(calls, rates)
    assert cost_usd == expected == 2.0
    assert unpriced == 0


def test_priced_totals_counts_unpriced_calls_never_fabricates_zero():
    calls = [{"ts": "t1", "model": "unknown-model-xyz",
              "usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0}}]
    cost_usd, unpriced = ocr.priced_totals(calls, rates=[])
    assert cost_usd is None                # never $0.00 for an unpriceable model
    assert unpriced == 1


def test_removing_dedup_inflates_peak_and_volume_to_the_wrong_over_counted_values(tmp_path):
    """THE CONTROL for TD-1 (.sdlc/research/2531.md) -- proves the bug would show up in THIS
    tool's own output numbers (both peak_context AND volume_totals), not just in dedup_calls's
    internal call count. Written to FAIL if dedup_calls is ever skipped or no-op'd -- confirmed by
    hand per Step 6 (see .sdlc/plans/2531.md Task 2) before trusting this green: dedup_calls was
    temporarily patched to `return list(raw_calls)` (the literal TD-1 bug) and this test's first
    four asserts were confirmed to fail, then the patch was reverted and this test re-confirmed
    green against the real module.

    Plan-Review refinement 2: the plan's OWN first draft of this fixture (three lines differing
    only in output_tokens) made peak_context dedup-INVARIANT for that specific fixture -- output_
    tokens is not one of the three fields peak_context sums, so every raw line had the identical
    context and the max was unchanged whether or not dedup ran; a test named "...inflates_peak..."
    that never actually moved peak_context was an overclaim, caught before this shipped. This
    fixture is deliberately different: an EARLIER duplicate line carries a higher
    cache_read_input_tokens (5000) than the LAST line (1000, the one dedup_calls keeps -- Plan
    §1b's own rule is "last usage wins", never "max usage wins"). This is a real, possible shape
    under this module's own documented dedup contract (a downstream transcript reader's GRAIN
    section promises only that the LAST line is the complete, correct total -- it does not promise
    every individual field is monotonically non-decreasing across the group on every future host);
    it is not claimed to be the common case measured on this host so far (which was byte-identical
    or monotonically growing), only a case the guard must be correct against regardless.
    """
    t = tmp_path / "orch.jsonl"
    _write_transcript(t, [
        _assistant_line(message_id="msg_1", input_tokens=100, output_tokens=5, cache_read=5000),
        _assistant_line(message_id="msg_1", input_tokens=100, output_tokens=5, cache_read=1000),
        _assistant_line(message_id="msg_1", input_tokens=100, output_tokens=20, cache_read=1000),
    ])
    raw = list(ocr.iter_raw_assistant_calls(t))
    real_calls = ocr.dedup_calls(raw)
    real_peak = ocr.peak_context(real_calls)
    real_volume = ocr.volume_totals(real_calls)
    broken_calls = raw                                     # the mutation: skip dedup entirely
    broken_peak = ocr.peak_context(broken_calls)
    broken_volume = ocr.volume_totals(broken_calls)
    assert len(real_calls) == 1
    assert len(broken_calls) == 3
    assert real_peak == 100 + 1000                          # ONE call, LAST line's cache_read (1000)
    assert real_volume == 100 + 20 + 1000                   # ONE call, last usage (20 output wins)
    assert broken_peak == 100 + 5000                        # max is dominated by the EARLIER, bigger line
    assert broken_volume == (100 + 5 + 5000) + (100 + 5 + 1000) + (100 + 20 + 1000)  # summed, all 3 lines
    assert broken_peak > real_peak * 2, (
        "expected the no-dedup path's peak to be substantially inflated -- if this is failing, "
        "peak_context or dedup_calls changed shape and this control needs updating, not loosening")
    assert broken_volume > real_volume * 2, (
        "expected the no-dedup path's volume to be substantially inflated -- if this is failing, "
        "volume_totals or dedup_calls changed shape and this control needs updating, not loosening")


# --------------------------------------------------------------------------- Task 3


def _write_meta(session_dir, agent_id, **fields):
    (session_dir / "subagents").mkdir(parents=True, exist_ok=True)
    (session_dir / "subagents" / f"agent-{agent_id}.meta.json").write_text(json.dumps(fields))


def test_walk_subagent_tree_reads_every_meta_json_keyed_by_agent_id(tmp_path):
    sess = tmp_path / "sess"
    _write_meta(sess, "aaa", description="#2531 goal-slot — full SDLC to merge",
                spawnDepth=1, requestShape="background", toolUseId="toolu_1")
    _write_meta(sess, "bbb", description="Research goal #2531 measurement approach",
                spawnDepth=2, parentAgentId="aaa", requestShape="foreground", model="sonnet")
    tree = ocr.walk_subagent_tree(sess)
    assert set(tree) == {"aaa", "bbb"}
    assert tree["bbb"]["parentAgentId"] == "aaa"


def test_goal_number_extracts_the_hash_number_from_either_real_description_shape():
    assert ocr.goal_number("#2531 goal-slot — full SDLC to merge") == 2531
    assert ocr.goal_number("Research goal #2531 measurement approach") == 2531
    assert ocr.goal_number("Plan fix for goal #2543") == 2543
    assert ocr.goal_number("no number here") is None
    assert ocr.goal_number(None) is None


def test_classify_tree_separates_goal_slots_from_phases_and_attaches_goal_numbers(tmp_path):
    sess = tmp_path / "sess"
    _write_meta(sess, "gs1", description="#2531 goal-slot — full SDLC to merge",
                spawnDepth=1, toolUseId="toolu_1")
    _write_meta(sess, "ph1", description="Research goal #2531 measurement approach",
                spawnDepth=2, parentAgentId="gs1")
    _write_meta(sess, "other", description="some unrelated top-level dispatch", spawnDepth=1,
                toolUseId="toolu_9")           # spawnDepth==1 but NOT a goal-slot description
    tree = ocr.walk_subagent_tree(sess)
    classified = ocr.classify_tree(tree)
    assert set(classified["goal_slots"]) == {"gs1"}
    assert classified["goal_slots"]["gs1"]["goal"] == 2531
    assert set(classified["phases"]) == {"ph1"}
    assert classified["phases"]["ph1"]["goal"] == 2531           # from the PHASE's own description
    assert "other" not in classified["goal_slots"] and "other" not in classified["phases"]


def test_classify_tree_takes_a_phases_own_goal_number_not_the_parents(tmp_path):
    """Operational definition, verbatim: 'spawnDepth == 2 under it is a phase subagent for the
    same goal N (parsed from its own description)' -- parsed from the CHILD's description, never
    inherited, even though in practice they usually agree."""
    sess = tmp_path / "sess"
    _write_meta(sess, "gs1", description="#2531 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_meta(sess, "ph1", description="a phase with no goal number at all", spawnDepth=2,
                parentAgentId="gs1")
    classified = ocr.classify_tree(ocr.walk_subagent_tree(sess))
    assert "ph1" not in classified["phases"]          # no own goal number -> dropped, not guessed


def test_isSidechain_is_never_consulted_as_a_key_anywhere_in_this_modules_source():
    """Source-level (AST) guard (Global constraints: 'Task 3 adds a source-level (AST) guard, not
    just a behavioral test, so this cannot silently regress') -- dossier: isSidechain measured
    False on every one of 1,139 real records, not a usable discriminator on this host.

    Deliberately AST-based, not a raw substring-in-file check: the plan's own first draft of this
    test asserted the literal 8 characters 'isSidechain' appear NOWHERE in the file, but this
    module's own docstring names 'isSidechain' explicitly, in prose, as one of the three wrong-
    but-plausible alternatives #2531's research measured and rejected -- a real self-contradiction
    (a substring check that would fail against the very docstring explaining the thing it exists
    to guard against), caught by actually running it, not by inspection. Removing the word from
    the docstring would have satisfied the literal check but deleted the WHY (AGENTS.md: comments
    record the reason, not just the what) for a purely mechanical reason. This walks the parsed
    AST instead and only fails on a real READ: a subscript (`obj["isSidechain"]`) or a
    `.get(...)`/`.pop(...)` call whose first argument is the literal string `"isSidechain"` --
    the two shapes that would actually consult it as a discriminator. Mentioning the name in a
    docstring or comment (an `Expr`/`Constant` with no surrounding Subscript/Call) is not flagged."""
    src = (S / "orchestrator_context_report.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    index_cls = getattr(ast, "Index", None)          # pre-3.9 compat only; a no-op unwrap on 3.9+
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            key = node.slice
            if index_cls is not None and isinstance(key, index_cls):
                key = key.value
            assert not (isinstance(key, ast.Constant) and key.value == "isSidechain"), (
                "isSidechain read via subscript -- see this test's own docstring")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "pop") and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "isSidechain"):
            raise AssertionError("isSidechain read via .get()/.pop() -- see this test's own docstring")


# --------------------------------------------------------------------------- Task 4


def _tool_use_line(tool_use_id, ts, name="Agent"):
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "tool_use", "id": tool_use_id, "name": name,
                                     "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}


def _tool_result_line(tool_use_id, ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_use_id}]}}


def test_goal_slot_dispatch_ts_finds_the_tool_use_blocks_own_timestamp(tmp_path):
    """VERIFIED against real data in Plan (.sdlc/plans/2531.md §1c): toolu_011NmGKZcTn8HkPgJc6YE8w2
    -> 2026-09-18T10:00:54.234Z, matching the research dossier's own independently-cited :54Z.

    Full sub-second precision (code review Bug 2 fix, .sdlc/plans/2531.md): the milliseconds are
    now KEPT, not truncated to the whole second -- so two goal-slot dispatches landing in the same
    wall-clock second no longer collide into an identical since_ts (see
    test_goal_windows_distinguishes_dispatches_within_the_same_whole_second)."""
    t = tmp_path / "orch.jsonl"
    _write_transcript(t, [
        _tool_use_line("toolu_1", "2026-09-18T10:00:54.234Z"),
        _tool_result_line("toolu_1", "2026-09-18T10:00:54.253Z"),   # ack, NOT the answer
    ])
    ts = ocr.goal_slot_dispatch_ts(t, "toolu_1")
    assert ts == "2026-09-18 10:00:54.234"      # the tool_use line's own ts, not the tool_result's


def test_goal_slot_dispatch_ts_returns_none_when_the_id_is_never_found(tmp_path):
    t = tmp_path / "orch.jsonl"
    _write_transcript(t, [_tool_use_line("toolu_other", "2026-09-18T10:00:00.000Z")])
    assert ocr.goal_slot_dispatch_ts(t, "toolu_missing") is None


# --------------------------------------------------------------------------- Bug 1 (code review):
# crash on malformed input -- goal_slot_dispatch_ts called .get()/.get() on parsed JSON without
# checking the value was actually a dict first. json.loads() can legally return a str/list/int/
# etc, not just a dict, and a transcript line that happens to decode to one of those (while still
# containing the tool_use_id SUBSTRING, so the cheap pre-filter does not skip it) raised an
# uncaught AttributeError that propagated through goal_windows -> build_report -> cmd_report ->
# main, crashing the whole CLI on one bad line. Matches phase_report.iter_assistant_turns's own
# guard style ("A malformed line is skipped, never fatal") and this module's own
# _session_file_belongs_to_repo, which already guards with isinstance(obj, dict) before .get().


def test_goal_slot_dispatch_ts_skips_a_line_whose_top_level_json_is_not_a_dict(tmp_path):
    """Watched RED against the pre-fix code: obj.get('type') raised AttributeError('str' object
    has no attribute 'get') on the first line below, and AttributeError('list' object has no
    attribute 'get') on the second. Each line still contains the 'toolu_1' SUBSTRING somewhere in
    its JSON text, so the cheap `if tool_use_id not in raw_line` pre-filter does not skip it --
    the crash comes from json.loads() legitimately decoding to a non-dict value, not from the
    pre-filter missing it."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps("mentions toolu_1 only as plain text, not a real tool_use block") + "\n")
        fh.write(json.dumps(["toolu_1", "a list, not a dict either"]) + "\n")
        fh.write(json.dumps(42) + "\n")            # no substring match, but must not raise either
    assert ocr.goal_slot_dispatch_ts(t, "toolu_1") is None    # must not raise


def test_goal_slot_dispatch_ts_skips_a_line_whose_message_field_is_not_a_dict(tmp_path):
    """A second, independent crash site in the SAME function: obj IS a dict and type=='assistant',
    but obj['message'] is itself a non-dict (a bare string) that happens to contain the
    tool_use_id substring. The pre-fix code's `(obj.get('message') or {}).get('content')` still
    crashed here -- a TRUTHY non-dict string is not replaced by the `or {}` fallback, which only
    ever triggers on a FALSY message (None/''/{})."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "assistant", "timestamp": "2026-09-18T10:00:00.000Z",
                              "message": "toolu_1 mentioned in a plain string message field"}) + "\n")
    assert ocr.goal_slot_dispatch_ts(t, "toolu_1") is None    # must not raise


# --------------------------------------------------------------------------- Bug 2 (code review):
# silent misattribution on same-second dispatches -- norm_ts truncated to whole-second precision,
# so two goal-slot dispatches landing within the same wall-clock second collided into an IDENTICAL
# since_ts; the tie was then broken by filesystem/glob iteration order (subagents_dir.glob(...)
# in walk_subagent_tree), not real dispatch order, silently giving one goal a permanently-empty
# attribution window while the other absorbed both goals' calls.


def test_norm_ts_precise_preserves_milliseconds_and_strips_timezone_marker():
    assert ocr._norm_ts_precise("2026-09-18T10:00:54.234Z") == "2026-09-18 10:00:54.234"
    assert ocr._norm_ts_precise("2026-09-18T10:00:54Z") == "2026-09-18 10:00:54"


def test_goal_windows_orders_goal_slots_by_dispatch_and_bounds_each_by_the_next(tmp_path):
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_meta(sess, "gs2", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_2")
    _write_transcript(top, [
        _tool_use_line("toolu_2", "2026-09-18T10:05:00.000Z"),   # dispatched SECOND in the file...
        _tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z"),   # ...but #100 actually went FIRST
    ])
    windows = ocr.goal_windows(sess, top)
    assert [w["goal"] for w in windows] == [100, 200]             # sorted by real dispatch time
    # Full sub-second precision (Bug 2 fix) -- ".000" is kept, never trimmed, so the format is
    # unconditional/consistent regardless of whether a given real timestamp happens to be exact.
    assert windows[0]["since_ts"] == "2026-09-18 10:00:00.000"
    assert windows[0]["until_ts"] == "2026-09-18 10:05:00.000"    # bounded by the NEXT dispatch
    assert windows[1]["until_ts"] is None                          # last one: open-ended


def test_goal_windows_reports_unresolvable_dispatch_ts_honestly_never_fabricated(tmp_path):
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing")
    _write_transcript(top, [])
    windows = ocr.goal_windows(sess, top)
    assert windows[0]["since_ts"] is None
    assert windows[0]["goal"] == 100                                # still reported, not dropped


def test_goal_windows_distinguishes_dispatches_within_the_same_whole_second(tmp_path):
    """THE regression for silent same-second misattribution. This check is deliberately
    independent of filesystem/glob iteration order (unlike an order assertion): under the pre-fix
    code these two since_ts values were ALWAYS byte-identical ('2026-09-18 10:00:54', both
    truncated), regardless of which goal-slot happened to sort first -- so this is a reliable,
    non-flaky proof of the root cause, not a coin flip on this filesystem's directory order."""
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs_a", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_a")
    _write_meta(sess, "gs_b", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_b")
    _write_transcript(top, [
        _tool_use_line("toolu_a", "2026-09-18T10:00:54.234Z"),
        _tool_use_line("toolu_b", "2026-09-18T10:00:54.890Z"),   # same SECOND, later millisecond
    ])
    windows = ocr.goal_windows(sess, top)
    since_values = [w["since_ts"] for w in windows]
    assert since_values[0] != since_values[1], (
        "two dispatches within the same whole second must not collapse to an identical since_ts")
    assert windows[0]["since_ts"] != windows[0]["until_ts"], (
        "a goal's window must never be zero-width [X, X) -- that made it permanently empty")
    assert [w["goal"] for w in windows] == [100, 200]             # real sub-second dispatch order


def test_goal_windows_breaks_an_exact_timestamp_tie_deterministically_by_tool_use_id(tmp_path):
    """The residual case even after sub-second precision: two dispatches at the LITERAL SAME
    precise instant (vanishingly rare, but must still be well-defined -- AGENTS.md SAFETY: 'the
    code REFUSES loudly rather than proceeding weakly', no silent order-dependence). tool_use_id
    is the deterministic tiebreak: stable across repeated runs, unlike filesystem/glob iteration
    order, which is what decided this before the fix."""
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs_z", description="#900 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_zzz")
    _write_meta(sess, "gs_a", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_aaa")
    same_ts = "2026-09-18T10:00:54.234Z"
    _write_transcript(top, [
        _tool_use_line("toolu_zzz", same_ts),
        _tool_use_line("toolu_aaa", same_ts),
    ])
    results = [tuple(w["goal"] for w in ocr.goal_windows(sess, top)) for _ in range(5)]
    assert results == [(100, 900)] * 5, (
        "must be deterministic across repeated runs, ordered by the tiebreak (toolu_aaa < "
        "toolu_zzz lexically), never by incidental filesystem/glob iteration order")


def test_build_report_attributes_a_later_call_to_the_correct_goal_when_dispatches_share_a_second(tmp_path):
    """End-to-end version of the same regression, through build_report: a call that unambiguously
    postdates BOTH same-second dispatches must land in the LAST-dispatched goal's (#200's) window,
    never the first-dispatched goal's (#100's) -- identically across repeated runs. gs_b's meta.json
    is written BEFORE gs_a's on purpose: under the pre-fix code, since both truncated to an
    identical since_ts, the tie fell back to insertion/iteration order, so creating the
    LATER-dispatching goal's file first is what actually exercised the bug in this environment
    (confirmed by running this test against the pre-fix code before this comment was written).

    NUMBERS UPDATED (#2531, SEVENTH fix round -- _filter_calls now floors since_ts/until_ts to
    whole-second before comparing, see that function's own docstring): #100's own window
    [54.234, 54.890) floors to a ZERO-WIDTH [54, 54) -- since==until, so `until_ts` is exclusive of
    the very second `since_ts` requires -- meaning NEITHER same-second dispatch's own noise can
    ever land there now, deterministically, not just in this fixture. Both toolu_a's and toolu_b's
    own dispatch lines (each 1 input + 1 output, whole-second-truncated to that same shared second)
    therefore land in #200 instead, alongside the 10:00:56 call this test was already built to
    check -- this is the "REMAINING, NOW GENUINELY BOUNDED, RESIDUAL" _filter_calls's own docstring
    names: a same-second collision can no longer misattribute a call to the WRONG goal or lose it
    silently, but it also cannot split same-second noise between the two colliding goals, so both
    fall to the later one. #100 still correctly sees zero calls of its own -- asserted below, not
    just claimed."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs_b", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_b")
    _write_meta(sess, "gs_a", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_a")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_a", "2026-09-18T10:00:54.234Z"),
        _tool_use_line("toolu_b", "2026-09-18T10:00:54.890Z"),
        _assistant_line(message_id="after_both", input_tokens=10, ts="2026-09-18T10:00:56.000Z"),
    ])
    for _ in range(3):                                       # repeated runs -> must be deterministic
        report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
        by_goal = {row["goal"]: row for row in report["goals"]}
        # toolu_a (1+1) + toolu_b (1+1) + after_both (10+20) = 34, all three now correctly counted
        # exactly once, none lost -- see this test's own docstring for why both dispatch-line-noise
        # calls land here rather than splitting or landing in #100.
        assert by_goal[200]["orchestrator_window"]["calls"] == 3, (
            "the 10:00:56 call postdates both dispatches and must be counted under #200, the "
            "LAST-dispatched goal, not silently absorbed by #100 -- plus both same-second "
            "dispatch lines, which #100's own now-zero-width window cannot claim")
        assert by_goal[200]["orchestrator_window"]["volume_total"] == (1 + 1) + (1 + 1) + (10 + 20)
        assert by_goal[100]["orchestrator_window"]["calls"] == 0


# --------------------------------------------------------------------------- Task 5


def test_build_report_is_unavailable_for_a_session_id_that_does_not_exist(tmp_path):
    report = ocr.build_report(str(tmp_path / ".sdlc"), "no-such-session", home=tmp_path)
    assert report["source"] == "unavailable"
    assert "reason" in report and report["reason"]


def test_build_report_degrades_gracefully_when_a_session_has_zero_subagents(tmp_path):
    """A session that ran entirely inline (no dispatch) -- orchestrator summary must still work;
    goal rows are simply empty, never a crash."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    _write_transcript(proj / "sess1.jsonl", [_assistant_line(message_id="m1", input_tokens=10)])
    (proj / "sess1").mkdir()                       # session dir exists, no subagents/ inside it
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    assert report["source"] == "ok"
    assert report["goals"] == []
    assert report["orchestrator"]["calls"] == 1


def test_build_report_orchestrator_row_never_fabricates_cost_for_an_unknown_model(tmp_path):
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    _write_transcript(proj / "sess1.jsonl",
                       [_assistant_line(message_id="m1", model="brand-new-unreleased-model")])
    (proj / "sess1").mkdir()
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, rates=[])
    assert report["orchestrator"]["cost_usd"] is None
    assert report["orchestrator"]["unpriced_calls"] == 1


# --------------------------------------------------------------------------- Task 6


def test_resolve_session_id_prefers_an_explicit_value():
    assert ocr.resolve_session_id("abc-123") == "abc-123"


def test_resolve_session_id_falls_back_to_the_env_var(monkeypatch):
    """Same convention phase_report._session_id() already uses -- reused, not reinvented."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "env-session")
    assert ocr.resolve_session_id(None) == "env-session"
    assert ocr.resolve_session_id("auto") == "env-session"


def test_resolve_session_id_falls_back_to_the_most_recently_modified_session_file(tmp_path, monkeypatch):
    """Both candidates carry a `cwd` line under the SAME repo_root -- recency among same-repo
    candidates is still the tiebreaker (Plan-Review refinement 1 scopes the CANDIDATE SET to the
    right repo; it does not change how ties within that set are broken)."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    old = proj / "old-session.jsonl"
    new = proj / "new-session.jsonl"
    _write_transcript(old, [{"type": "init", "cwd": str(repo_root)}])
    _write_transcript(new, [{"type": "init", "cwd": str(repo_root)}])
    import os
    os.utime(old, (1, 1))
    os.utime(new, (2_000_000_000, 2_000_000_000))
    assert ocr.resolve_session_id(None, home=home, repo_root=repo_root) == "new-session"


def test_resolve_session_id_fallback_excludes_a_session_belonging_to_a_different_repo(tmp_path, monkeypatch):
    """THE regression proof for Plan-Review refinement 1: 'resolve_session_id's mtime fallback is
    NOT actually repo-scoped -- it globs ~/.claude/projects/*/*.jsonl machine-wide with no filter
    for the current repo, so on a multi-repo dev machine it can silently return an unrelated
    repo's session as "the" session (a misattribution, not an honest `unavailable`)'. The
    other-repo session is strictly newer on disk; the fix must still pick the SAME-repo one
    (older), never the newer-but-wrong-repo one, because its own recorded `cwd` does not fall
    under repo_root."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo_root = tmp_path / "repo-a"
    repo_root.mkdir()
    other_repo = tmp_path / "repo-b"
    other_repo.mkdir()
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    same_repo = proj / "same-repo-session.jsonl"
    other = proj / "other-repo-session.jsonl"
    _write_transcript(same_repo, [{"type": "init", "cwd": str(repo_root)}])
    _write_transcript(other, [{"type": "init", "cwd": str(other_repo)}])
    import os
    os.utime(same_repo, (1, 1))
    os.utime(other, (2_000_000_000, 2_000_000_000))          # other-repo session is strictly newer
    assert ocr.resolve_session_id(None, home=home, repo_root=repo_root) == "same-repo-session"


def test_resolve_session_id_fallback_also_matches_a_goal_worktree_nested_under_repo_root(tmp_path, monkeypatch):
    """This repo's own convention nests a goal worktree INSIDE the main checkout
    (.sdlc/work/<goal>) -- a session recorded with cwd there must still count as belonging to
    repo_root, matching a downstream transcript reader's own documented reason for not
    matching on directory slug alone (a worktree session gets a different slug than its parent
    repo's)."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo_root = tmp_path / "repo"
    worktree = repo_root / ".sdlc" / "work" / "2531"
    worktree.mkdir(parents=True)
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-y"
    proj.mkdir(parents=True)
    _write_transcript(proj / "worktree-session.jsonl", [{"type": "init", "cwd": str(worktree)}])
    assert ocr.resolve_session_id(None, home=home, repo_root=repo_root) == "worktree-session"


def test_resolve_session_id_returns_none_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert ocr.resolve_session_id(None, home=tmp_path) is None


def test_cli_report_prints_an_orchestrator_row_and_one_row_per_goal_slot(tmp_path, monkeypatch):
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="m1", input_tokens=10),
    ])
    _write_transcript(sess / "subagents" / "agent-gs1.jsonl",
                       [_assistant_line(message_id="m2", input_tokens=50)])
    r = _run("report", str(tmp_path / ".sdlc"), "--session", "sess1", "--home", str(home))
    assert r.returncode == 0
    assert "ORCHESTRATOR" in r.stdout
    assert "#100" in r.stdout


def test_cli_unknown_session_exits_non_zero_with_an_honest_reason(tmp_path):
    r = _run("report", str(tmp_path / ".sdlc"), "--session", "no-such-session",
              "--home", str(tmp_path))
    assert r.returncode == 1
    assert "unavailable" in (r.stdout + r.stderr).lower()


def test_cli_json_format_round_trips_the_same_numbers_as_text(tmp_path):
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    (proj / "sess1").mkdir(parents=True)
    _write_transcript(proj / "sess1.jsonl", [_assistant_line(message_id="m1", input_tokens=10)])
    r = _run("report", str(tmp_path / ".sdlc"), "--session", "sess1", "--home", str(home),
              "--format", "json")
    payload = json.loads(r.stdout)
    assert payload["orchestrator"]["calls"] == 1


def test_report_spawns_no_subprocess_and_opens_no_network_socket(tmp_path, monkeypatch):
    """Mirrors phase_report.py's own test_title_resolution_spawns_no_subprocess_and_opens_no_
    socket exactly, for this tool's own read path (SAFETY, AGENTS.md)."""
    import socket, subprocess as sp

    def _boom(*a, **k):
        raise AssertionError("orchestrator_context_report spawned a process or opened a socket")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(sp, name, _boom, raising=False)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    (proj / "sess1").mkdir(parents=True)
    _write_transcript(proj / "sess1.jsonl", [_assistant_line(message_id="m1")])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    assert report["source"] == "ok"


# --------------------------------------------------------------------------- Non-blocking findings
# (code review, .sdlc/plans/2531.md IMPLEMENT pass fixing up the BLOCKED review of this diff)


def test_goal_number_is_anchored_to_the_goal_slots_own_number_not_a_quoted_parent():
    """Finding 1: goal_number's old regex was an unanchored FIRST #<digits> match anywhere in the
    string. A goal-slot description that also quotes a different #N before its own '#N goal-slot'
    marker (e.g. a parent Epic/Story reference) must still resolve to the goal-slot's OWN number,
    not whichever #N-shaped substring happens to appear first."""
    assert ocr.goal_number("Story #100: #2531 goal-slot — full SDLC to merge") == 2531
    # the three real shapes must keep working unchanged:
    assert ocr.goal_number("#2531 goal-slot — full SDLC to merge") == 2531
    assert ocr.goal_number("Research goal #2531 measurement approach") == 2531
    assert ocr.goal_number("Plan fix for goal #2543") == 2543
    assert ocr.goal_number("no number here") is None
    assert ocr.goal_number(None) is None


def test_goal_number_matches_the_output_contracts_own_dispatch_label_shape():
    """Caught by re-running the fixed tool against REAL transcript data (AGENTS.md: 'run the
    control'), not by inspection: the first anchored-regex draft above only covered the two
    shapes named in this module's own docstring and silently dropped a THIRD, equally-real shape
    -- docs/output-contract.md section 4's own dispatch-label convention, '#<N> P<n> NAME —
    description' (e.g. '#2628 P5 IMPLEMENT — extract coherence validator'). This is not a made-up
    edge case: '#2531 P5 IMPLEMENT — orchestrator context report' is THIS goal's own real phase
    description, live in the actual session this tool reports on, and it silently vanished from
    #2531's own --detail output the first time the anchor fix shipped, before this test was
    added."""
    assert ocr.goal_number("#2531 P5 IMPLEMENT — orchestrator context report") == 2531
    assert ocr.goal_number("#2628 P5 IMPLEMENT — extract coherence validator") == 2628


def test_cost_field_distinguishes_a_genuinely_empty_scope_from_an_unpriced_model():
    """Finding 2: a scope with ZERO calls (nothing to price at all -- e.g. #2544's real
    orchestrator-window in .sdlc/plans/2531.md's own Real-run results) must not print the same
    'model not in rate card' reason as a scope that had calls but genuinely couldn't price them --
    those are different facts and a reader should not be misled about which one happened."""
    empty_scope = {"calls": 0, "cost_usd": None, "unpriced_calls": 0}
    unpriced_scope = {"calls": 1, "cost_usd": None, "unpriced_calls": 1}
    empty_reason = ocr._cost_field(empty_scope)
    unpriced_reason = ocr._cost_field(unpriced_scope)
    assert empty_reason != unpriced_reason
    assert "no calls" in empty_reason.lower()
    assert "not in rate card" in unpriced_reason.lower()


def test_cli_bad_goal_flag_exits_with_a_clear_error_not_a_traceback(tmp_path):
    """Finding 3: --goal was fed straight into int() with no guard -- a non-numeric value raised
    an uncaught ValueError (a raw traceback) instead of a clear CLI usage error."""
    r = _run("report", str(tmp_path / ".sdlc"), "--session", "some-session", "--home", str(tmp_path),
              "--goal", "not-a-number")
    assert r.returncode != 0
    assert "traceback" not in r.stderr.lower()
    assert "--goal" in (r.stdout + r.stderr)


def test_load_caches_the_module_across_calls():
    """Finding 4: _load('phase_report') re-executed the ENTIRE sibling module from scratch on
    every call (spec_from_file_location + module_from_spec + exec_module) -- called from many
    hot-path functions, this is O(goal-slots x calls/goal) redundant full-module executions
    (~185-745+ measured at N=20 goals). Memoized: the same module object must come back every time
    within one process."""
    first = ocr._load("phase_report")
    second = ocr._load("phase_report")
    assert first is second


def test_build_report_phase_row_carries_a_reason_when_its_transcript_is_missing(tmp_path):
    """Finding 5 (data side): an unavailable phase row must carry a 'reason', matching the
    subagent/orchestrator-window rows' own convention -- render_text can only show a reason that
    build_report actually attaches."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_meta(sess, "ph1", description="Research goal #100 blast radius", spawnDepth=2,
                parentAgentId="gs1")             # no agent-ph1.jsonl transcript ever written
    _write_transcript(proj / "sess1.jsonl", [_tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z")])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, detail=True)
    phase = report["goals"][0]["phases"][0]
    assert phase["source"] == "unavailable"
    assert phase.get("reason")


def test_render_text_shows_the_reason_for_an_unavailable_phase_row():
    """Finding 5 (render side): render_text already prints '{reason}' for an unavailable subagent
    or orchestrator-window row -- the phase-row branch printed only the bare word 'unavailable',
    dropping the reason on the floor even when build_report supplied one."""
    report = {
        "source": "ok", "session": "s1", "since": None, "until": None,
        "orchestrator": {"calls": 0, "peak_context": 0, "volume_total": 0, "cost_usd": None,
                          "unpriced_calls": 0},
        "goals": [{
            "goal": 100, "agent_id": "gs1", "dispatch_ts": "2026-09-18 10:00:00", "until_ts": None,
            "subagent": {"calls": 0, "peak_context": 0, "volume_total": 0, "cost_usd": None,
                         "unpriced_calls": 0},
            "orchestrator_window": {"calls": 0, "peak_context": 0, "volume_total": 0,
                                     "cost_usd": None, "unpriced_calls": 0},
            "phases": [{"agent_id": "ph1", "description": "Research goal #100",
                        "source": "unavailable", "reason": "no phase transcript found"}],
        }],
    }
    lines = ocr.render_text(report)
    phase_line = next(l for l in lines if "phase Research goal #100" in l)
    assert "no phase transcript found" in phase_line


def test_build_report_until_filter_excludes_a_goal_with_unresolvable_dispatch_ts_like_since_does(tmp_path):
    """Finding 6a: an unresolvable dispatch ts (since_ts is None) was excluded under --since
    (fail-closed) but INCLUDED under --until (fail-open) -- the same uncertain fact treated two
    different ways depending on which flag happened to be set. Fixed to fail-closed under both,
    matching this module's own 'never fabricate/guess' philosophy."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing")        # never appears in the transcript -> since_ts=None
    _write_transcript(proj / "sess1.jsonl", [])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home,
                               until="2026-12-31T00:00:00Z")
    assert report["goals"] == []


# --------------------------------------------------------------------------- Fifth fix round:
# code review found build_report's per-goal-slot `orchestrator_window` computed unconditionally
# via _filter_calls(top_level_calls, g_since, g_until) with NO guard for g_since is None --
# unlike the subagent/phase rows, which already degrade to an honest {"source": "unavailable",
# "reason": ...} when their own data cannot be resolved. _filter_calls(calls, None, None) applies
# no filtering at all (both bounds optional), so it silently returned the ENTIRE deduped
# top_level_calls list -- the WHOLE SESSION's totals -- for any goal-slot whose own dispatch
# instant could not be resolved. The test immediately above (until_filter_excludes...) already
# covers an unresolvable dispatch_ts, but only under an EXPLICIT --until, where the fail-closed
# `continue` guards a few lines above the bug exclude the row before it ever reaches the buggy
# computation -- so that test, and the identical shape under --since, could never have caught
# this. test_build_report_survives_every_known_malformed_json_type_shape_at_once (further below)
# does call build_report under the tool's own DEFAULT, flag-less invocation with one unresolvable
# goal-slot (#300) reaching this exact line, but never asserts anything about that goal's own
# orchestrator_window -- so 704 passing tests missed it. Both tests below call build_report with
# no since/until at all, the one shape the bug actually lived in.


def test_build_report_orchestrator_window_is_unavailable_not_whole_session_when_dispatch_ts_unresolvable_by_default(tmp_path):
    """Watched RED against the pre-fix code (confirmed by hand before trusting this green --
    AGENTS.md 'run the control'): the unfixed line made row['orchestrator_window'] equal
    _scope_totals(top_level_calls, ...), i.e. calls == report['orchestrator']['calls'] == 3, not
    an 'unavailable' dict. This is the exact fifth-fix-round finding, reproduced directly."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing")        # never appears in the transcript -> since_ts=None
    _write_transcript(proj / "sess1.jsonl", [
        _assistant_line(message_id="m1", input_tokens=500, output_tokens=50,
                         ts="2026-09-18T09:00:00.000Z"),
        _assistant_line(message_id="m2", input_tokens=500, output_tokens=50,
                         ts="2026-09-18T09:05:00.000Z"),
        _assistant_line(message_id="m3", input_tokens=500, output_tokens=50,
                         ts="2026-09-18T09:10:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)   # DEFAULT: no flags
    assert report["orchestrator"]["calls"] == 3, "sanity: the whole-session figure this bug leaked"
    assert len(report["goals"]) == 1, "an unresolvable goal must still be reported, never dropped"
    row = report["goals"][0]
    assert row["dispatch_ts"] is None
    assert row["orchestrator_window"] == {"source": "unavailable",
                                           "reason": "no resolvable dispatch timestamp"}, (
        "must degrade honestly, matching the subagent row's own convention -- not silently fall "
        f"back to the whole session's totals (calls={report['orchestrator']['calls']})")


def test_build_report_two_unresolvable_goal_slots_never_double_count_the_sessions_totals(tmp_path):
    """The double-counting symptom the reviewer measured directly (3 goals summing to 7 calls
    when only 4 distinct calls existed): pre-fix, EVERY goal-slot with an unresolvable
    dispatch_ts independently received the SAME whole-session totals for orchestrator_window, so
    summing a 'per goal' figure across goals multi-counted real activity that happened once. Two
    goal-slots here both carry a toolUseId that never appears in the transcript."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs_bad_1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing_1")
    _write_meta(sess, "gs_bad_2", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing_2")
    _write_transcript(proj / "sess1.jsonl", [
        _assistant_line(message_id="m1", input_tokens=100, output_tokens=10,
                         ts="2026-09-18T09:00:00.000Z"),
        _assistant_line(message_id="m2", input_tokens=100, output_tokens=10,
                         ts="2026-09-18T09:05:00.000Z"),
        _assistant_line(message_id="m3", input_tokens=100, output_tokens=10,
                         ts="2026-09-18T09:10:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)   # DEFAULT: no flags
    assert report["orchestrator"]["calls"] == 3, "sanity: the whole-session figure this bug leaked"
    by_goal = {row["goal"]: row for row in report["goals"]}
    assert set(by_goal) == {100, 200}
    unavailable = {"source": "unavailable", "reason": "no resolvable dispatch timestamp"}
    assert by_goal[100]["orchestrator_window"] == unavailable
    assert by_goal[200]["orchestrator_window"] == unavailable
    # Neither row fabricates the session's totals, and -- since neither carries a 'calls' key at
    # all in the unavailable shape -- summing a naive 'per goal' calls figure across this report
    # can never exceed the calls that genuinely happened once (here: 0, not 3, and not 6).
    total_reported_calls = sum(g["orchestrator_window"].get("calls", 0) for g in report["goals"])
    assert total_reported_calls == 0, (
        f"an unresolvable goal must contribute 0 to any summed per-goal total, never the whole "
        f"session's calls or another unresolvable goal's -- got {total_reported_calls}")


# --------------------------------------------------------------------------- Sixth fix round: a
# THIRD shape of the unresolved-dispatch-ts problem (round 2: two RESOLVED goals collided at the
# same whole second; round 5: an UNRESOLVED goal's own row fabricated the whole session's totals).
# This round: an unresolved goal-slot's real, chronologically-intervening activity gets silently
# absorbed into a RESOLVED neighbor's own orchestrator_window, because goal_windows's until_ts is
# "the next row's since_ts" and every unresolved row sorts after every resolved one regardless of
# its TRUE position -- so the earlier resolved goal's until_ts silently skips past it to the next
# resolved goal, with no signal this happened. Reviewer's exact repro: #100/#200(unresolvable)/#300
# dispatch in that real order; #100's window absorbed #200's own real activity.
#
# Every test below uses clearly distinguishing input_tokens volumes per goal (1000/1500/2000/2500/
# 3000/4000, matching this file's own established convention, e.g. the 111/222 pair above) so a
# swap or leak between two goals' figures is provably caught by an exact-equality assertion, not
# just "did not raise". See goal_windows's own SIXTH fix round docstring paragraph for the full
# proof this fix relies on: no reordering of goal_windows's own sort/boundary arithmetic can ever
# change a resolved row's computed until_ts when an unresolved row exists (every unresolved row
# already sorts after every resolved one), so the fix cannot live there -- it lives in build_report,
# as an honest `boundary_partial`/`boundary_partial_goals`/`boundary_partial_reason` caveat attached
# to the (unchanged, already-best-achievable) computed numbers, applied to EVERY resolved goal's
# orchestrator_window in a report that contains at least one unresolved goal-slot ANYWHERE -- not
# only its two visually-"adjacent" neighbors in sorted order, because "adjacency" in sorted order is
# a fact about the sort, not about the true, unknown timeline an unresolved since_ts=None carries
# zero information about (it could fall before the first resolved row, between any two, or after
# the last -- nothing here can rule any of those out).


def test_goal_windows_boundary_for_a_resolved_row_is_unaffected_by_how_unresolved_rows_are_skipped(tmp_path):
    """Locks in the mathematical claim goal_windows's own SIXTH fix round docstring paragraph makes,
    which this whole round's fix relies on: because every unresolved row already sorts strictly
    after every resolved row (the dominant `since_ts is None` sort-key element), "the next row after
    resolved row R" and "the next row after R, EXPLICITLY skipping any row with no resolved since_ts
    of its own" are the SAME row, for every resolved R, in every arrangement. Proven by computing
    until_ts a SECOND, INDEPENDENT way -- from a locally-built, resolved-only sorted list that skips
    every unresolved row by construction -- and asserting it agrees with goal_windows's own real
    output on every resolved row, across 5 goal-slots mixing resolved/unresolved in mixed relative
    order (two unresolved: one whose true position would be in the middle, one whose true position
    would be at the very end). This is WHY this round's fix could not live inside goal_windows
    itself (changing 'which row counts as next' cannot change the computed value) and had to move
    one layer up, into build_report, as an honesty caveat instead."""
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs_a", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_a")
    _write_meta(sess, "gs_b", description="#150 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing_b")            # unresolved; true position between 100/200
    _write_meta(sess, "gs_c", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_c")
    _write_meta(sess, "gs_d", description="#250 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_missing_d")            # unresolved; true position after 300
    _write_meta(sess, "gs_e", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_e")
    _write_transcript(top, [
        _tool_use_line("toolu_a", "2026-09-18T10:00:00.000Z"),
        _tool_use_line("toolu_c", "2026-09-18T10:05:00.000Z"),
        _tool_use_line("toolu_e", "2026-09-18T10:10:00.000Z"),
    ])
    windows = ocr.goal_windows(sess, top)
    resolved_sorted = sorted((w for w in windows if w["since_ts"] is not None),
                              key=lambda w: w["since_ts"])
    assert [w["goal"] for w in resolved_sorted] == [100, 200, 300]

    def skip_unresolved_until_ts(row):
        idx = resolved_sorted.index(row)
        return resolved_sorted[idx + 1]["since_ts"] if idx + 1 < len(resolved_sorted) else None

    for row in resolved_sorted:
        assert row["until_ts"] == skip_unresolved_until_ts(row), (
            f"goal_windows's own until_ts for goal #{row['goal']} disagreed with the "
            f"independently-computed 'skip unresolved rows' answer")


def test_build_report_flags_boundary_partial_when_an_unresolved_sibling_sits_between_two_resolved_goals(tmp_path):
    """THE reviewer's exact repro: #100/#200(unresolvable)/#300 dispatch in that real chronological
    order, one real call belonging to each. Watched RED against this round's pre-fix code (confirmed
    by hand -- see this round's own report for the exact stash/pop commands run): pre-fix,
    by_goal[100]["orchestrator_window"] had no 'boundary_partial' key at all (KeyError), because
    that key did not exist before this round. Post-fix it is present and True on BOTH #100 and #300
    -- not just #100, the row the reviewer's own report happened to name -- because build_report has
    no way to prove #200's true (unknown) dispatch instant falls outside #300's own open-ended
    [10:10:00, None) window either.

    The raw calls/volume_total figures are DELIBERATELY left asserted at their sixth-round values
    (mostly UNCHANGED by the SEVENTH round below too) -- not changed, not blanked to zero or
    'unavailable' -- matching the sixth round's own chosen resolution (see build_report's own
    comment): goal_windows's own docstring proves no reordering of its sort/boundary logic can make
    these numbers more precise given an unresolved sibling exists, so the honest fix adds a caveat
    to the already-best-achievable number rather than fabricating a different one.

    NUMBERS RE-VERIFIED, SEVENTH fix round (#2531): the sixth round's own comment here previously
    described a goal's OWN dispatch tool_use line as always excluded from its OWN window, "leaking
    BACKWARD" into whichever resolved goal preceded it, because a call's whole-second `ts` always
    string-compared as LESS than its own sub-second-precise `since_ts` -- `_filter_calls`'s own
    "NAMED, BOUNDED RESIDUAL LIMITATION". That mischaracterization (both the "~1s" bound and the
    "never observed" claim) is corrected in `_filter_calls`'s own docstring this round; the fix
    (flooring `since_ts`/`until_ts` to whole-second before comparing) means a goal's OWN dispatch
    line now correctly lands INSIDE its own window instead of leaking into its predecessor's. For
    #100 this is a WASH (verified by hand, not assumed): it gains its own toolu_100 line (1+1) but
    loses #300's toolu_300 line, which no longer leaks backward -- both contribute the same (1+1),
    so `w100`'s numbers below are numerically UNCHANGED from the sixth round. #300 is different: as
    the LAST (open-ended, `until_ts=None`) resolved goal here, it previously had nothing to lose to
    a leak in the first place, so it purely GAINS its own now-correctly-included dispatch line --
    `w300`'s numbers below are updated (+1 call, +2 volume) accordingly."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200_missing")          # never appears -> since_ts unresolvable
    _write_meta(sess, "gs300", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_300")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
        # #200's own real activity -- TRUE chronological position between #100 and #300, but its
        # own dispatch is unresolvable, so nothing marks this call as "belonging to #200":
        _assistant_line(message_id="call_200", input_tokens=2000, ts="2026-09-18T10:06:00.000Z"),
        _tool_use_line("toolu_300", "2026-09-18T10:10:00.000Z"),
        _assistant_line(message_id="call_300", input_tokens=4000, ts="2026-09-18T10:11:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)   # DEFAULT: no flags
    by_goal = {row["goal"]: row for row in report["goals"]}
    assert set(by_goal) == {100, 200, 300}

    assert by_goal[200]["orchestrator_window"] == {"source": "unavailable",
                                                     "reason": "no resolvable dispatch timestamp"}

    w100 = by_goal[100]["orchestrator_window"]
    # call_100 (1000+20) + call_200 (2000+20, the absorbed one) + #100's OWN dispatch line (1+1),
    # now correctly included in its own window (SEVENTH fix round -- see this test's own docstring)
    # instead of #300's dispatch line leaking backward into it; numerically unchanged either way.
    assert w100["calls"] == 3 and w100["volume_total"] == (1000 + 20) + (2000 + 20) + (1 + 1)
    assert w100["boundary_partial"] is True
    assert w100["boundary_partial_goals"] == [200]
    assert "#200" in w100["boundary_partial_reason"]

    w300 = by_goal[300]["orchestrator_window"]
    # call_300 (4000+20) + #300's OWN dispatch line (1+1), now correctly included in its own
    # open-ended window (SEVENTH fix round) instead of leaking backward into #100 -- call_200
    # predates #300's own dispatch, so nothing else lands here in THIS fixture.
    assert w300["calls"] == 2 and w300["volume_total"] == (4000 + 20) + (1 + 1), (
        "#300's own figures happen to be numerically near-exact in THIS fixture (call_200 predates "
        "#300's own dispatch, and only #300's own now-correctly-included dispatch-line noise adds "
        "the extra 1+1) -- but it must STILL be flagged, see this test's own docstring")
    assert w300["boundary_partial"] is True
    assert w300["boundary_partial_goals"] == [200]


def test_build_report_flags_boundary_partial_when_the_unresolved_sibling_dispatches_before_every_resolved_goal(tmp_path):
    """'Unresolved first' -- chronologically the very first dispatch, before any resolved goal at
    all. Not actually covered by round 5's own tests (checked directly, not assumed: round 5's two
    tests -- test_build_report_orchestrator_window_is_unavailable_not_whole_session_when_dispatch_ts_
    unresolvable_by_default and test_build_report_two_unresolvable_goal_slots_never_double_count_
    the_sessions_totals -- both construct reports with ZERO resolved goal-slots, so neither one ever
    asserts anything about a RESOLVED sibling's own orchestrator_window; only the unresolved goal's
    OWN 'unavailable' row is checked either way, which is unaffected by dispatch position by
    construction). This shape is new ground for this round.

    In this specific arrangement, #200's real activity (before #100's own since_ts) is silently
    DROPPED from every window, not absorbed into one (the lower bound already excludes it) -- a
    different, less severe, pre-existing property this round does not change or need to. #100 must
    still be flagged boundary_partial regardless, because build_report cannot know from the data
    alone that #200 dispatched first rather than, say, between #100 and #300."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200_missing")
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs300", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_300")
    _write_transcript(proj / "sess1.jsonl", [
        _assistant_line(message_id="call_200", input_tokens=2000, ts="2026-09-18T09:55:00.000Z"),
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
        _tool_use_line("toolu_300", "2026-09-18T10:10:00.000Z"),
        _assistant_line(message_id="call_300", input_tokens=4000, ts="2026-09-18T10:11:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}

    w100 = by_goal[100]["orchestrator_window"]
    # call_100 (1000+20) + #100's OWN dispatch line (1+1), now correctly included in its own window
    # (SEVENTH fix round, see test 1's own docstring -- numerically the same total as the sixth
    # round's "#300's dispatch line leaks backward" figure, since either way one 1+1 dispatch-line
    # noise call lands here) -- call_200 (09:55) predates #100's own since_ts (10:00) and is
    # excluded, not absorbed.
    assert w100["calls"] == 2 and w100["volume_total"] == (1000 + 20) + (1 + 1), (
        "call_200 (09:55) predates #100's own since_ts (10:00) -- excluded, not absorbed")
    assert w100["boundary_partial"] is True
    assert w100["boundary_partial_goals"] == [200]

    w300 = by_goal[300]["orchestrator_window"]
    # call_300 (4000+20) + #300's OWN dispatch line (1+1), now correctly included in its own
    # open-ended window instead of leaking backward into #100 (SEVENTH fix round, see test 1's own
    # docstring) -- a real, uncompensated +1 call/+2 volume here, since #300 (open-ended) had
    # nothing to lose to a leak the way a bounded-above goal does.
    assert w300["calls"] == 2 and w300["volume_total"] == (4000 + 20) + (1 + 1)
    assert w300["boundary_partial"] is True, (
        "#300 is not adjacent to #200 in EITHER sorted or true chronological order in this "
        "fixture, and its own numbers are near-exact here -- but it must still be flagged, "
        "matching this round's own report-wide (not adjacency-based) rule")


def test_build_report_flags_boundary_partial_on_the_open_ended_tail_when_the_unresolved_sibling_dispatches_last(tmp_path):
    """'Unresolved last' -- chronologically AFTER every resolved goal. Also not actually covered by
    round 5's own tests, for the identical reason given in the 'unresolved first' test above (both
    of round 5's tests use zero resolved goal-slots). This is the one ordering where this round's
    bug manifests on the LAST resolved goal's OPEN-ENDED ([since_ts, None), no upper bound at all)
    window rather than a bounded one -- #300's own numbers ARE numerically contaminated here (unlike
    the 'unresolved first' shape above), because nothing bounds the open tail against #200's real,
    later activity. Both effects are asserted: the (unchanged, still-contaminated-looking) number,
    and the new honest flag explaining it."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs300", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_300")
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200_missing")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
        _tool_use_line("toolu_300", "2026-09-18T10:10:00.000Z"),
        _assistant_line(message_id="call_300", input_tokens=4000, ts="2026-09-18T10:11:00.000Z"),
        # #200's own real activity, chronologically AFTER #300 -- absorbed into #300's open tail:
        _assistant_line(message_id="call_200", input_tokens=2000, ts="2026-09-18T10:20:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}

    w100 = by_goal[100]["orchestrator_window"]
    # call_100 (1000+20) + #100's OWN dispatch line (1+1), now correctly included in its own window
    # instead of #300's dispatch line leaking backward into it (SEVENTH fix round, see test 1's own
    # docstring) -- numerically the same total either way; unaffected by #200's real activity,
    # which lands after #300.
    assert w100["calls"] == 2 and w100["volume_total"] == (1000 + 20) + (1 + 1), (
        "unaffected: bounded above by #300's dispatch")
    assert w100["boundary_partial"] is True and w100["boundary_partial_goals"] == [200]

    w300 = by_goal[300]["orchestrator_window"]
    # call_300 (4000+20) + the absorbed call_200 (2000+20) + #300's OWN dispatch line (1+1), now
    # correctly included in its own open-ended window instead of leaking backward into #100
    # (SEVENTH fix round) -- a real, uncompensated +1 call/+2 volume on top of the sixth round's
    # own figure here, since #300 (open-ended) had nothing to lose to a leak.
    assert w300["calls"] == 3 and w300["volume_total"] == (4000 + 20) + (2000 + 20) + (1 + 1), (
        "call_300 + the absorbed call_200 + #300's own dispatch-line noise -- the open tail has no "
        "upper bound, so it genuinely cannot exclude a later unresolved goal's real activity")
    assert w300["boundary_partial"] is True
    assert w300["boundary_partial_goals"] == [200]
    assert "#200" in w300["boundary_partial_reason"]


def test_build_report_flags_boundary_partial_on_every_resolved_goal_when_two_unresolved_goals_sandwich_one(tmp_path):
    """Two unresolved goal-slots, one resolved goal between their TRUE (but individually unknown)
    positions: #150(unresolved) / #100(resolved) / #250(unresolved). #100 is the ONLY resolved row,
    so its own window is open-ended on BOTH sides in effect -- lower-bounded at its own since_ts
    (excluding #150's earlier activity, matching the 'unresolved first' shape) and open-ended above
    (absorbing #250's later activity, matching the 'unresolved last' shape) simultaneously. The
    boundary_partial_goals list must name BOTH #150 and #250, not just one, proving multiple
    unresolved siblings are all surfaced, not merely "at least one" collapsed to a single name."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs150", description="#150 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_150_missing")
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs250", description="#250 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_250_missing")
    _write_transcript(proj / "sess1.jsonl", [
        _assistant_line(message_id="call_150", input_tokens=1500, ts="2026-09-18T09:55:00.000Z"),
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
        _assistant_line(message_id="call_250", input_tokens=2500, ts="2026-09-18T10:05:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}
    assert set(by_goal) == {100, 150, 250}
    assert by_goal[150]["orchestrator_window"]["source"] == "unavailable"
    assert by_goal[250]["orchestrator_window"]["source"] == "unavailable"

    w100 = by_goal[100]["orchestrator_window"]
    # call_100 (1000+20) + call_250 (2500+20) + #100's OWN dispatch line (1+1) -- #100 is the ONLY
    # resolved goal, so it is both first AND last: its own dispatch line now correctly lands inside
    # its own window (SEVENTH fix round, see test 1's own docstring) with NOTHING to lose to a leak
    # either way (there is no OTHER resolved goal for a leak to have come from, or gone to) -- a
    # real, uncompensated +1 call/+2 volume versus the sixth round's own figure here. call_150
    # (09:55) is excluded (predates #100's own since_ts); call_250 (10:05) is absorbed (#100 is the
    # last resolved row, open-ended above).
    assert w100["calls"] == 3 and w100["volume_total"] == (1000 + 20) + (2500 + 20) + (1 + 1)
    assert w100["boundary_partial"] is True
    assert w100["boundary_partial_goals"] == [150, 250], (
        "both unresolved siblings must be named, sorted, never collapsed to just one")
    assert "#150" in w100["boundary_partial_reason"] and "#250" in w100["boundary_partial_reason"]


def test_build_report_flags_boundary_partial_on_a_resolved_goal_two_slots_away_from_the_unresolved_one(tmp_path):
    """4 goal-slots, 3 resolved (#100/#200/#300) + 1 unresolved (#150, true position between #100
    and #200). Proves the fix is REPORT-WIDE, not adjacency-based, at a scale beyond the reviewer's
    own 3-goal repro: #300 is numerically untouched by #150 (not adjacent to it in either sorted or
    true chronological order) and sits two goal-slots away, yet must still be flagged -- because the
    guarantee it would need ("#150 did not truly dispatch inside MY window") is exactly as
    unprovable for #300 as it is for #100 or #200, which ARE adjacent to #150. Only #100's own
    figures are actually contaminated by #150's real activity here (#200 and #300 both stay
    near-exact -- SEVENTH fix round: each now also correctly counts its OWN dispatch-line noise,
    see the per-window comments below, which is real activity, not contamination from #150)."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs150", description="#150 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_150_missing")
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200")
    _write_meta(sess, "gs300", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_300")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
        _assistant_line(message_id="call_150", input_tokens=1500, ts="2026-09-18T10:04:00.000Z"),
        _tool_use_line("toolu_200", "2026-09-18T10:05:00.000Z"),
        _assistant_line(message_id="call_200", input_tokens=2000, ts="2026-09-18T10:06:00.000Z"),
        _tool_use_line("toolu_300", "2026-09-18T10:10:00.000Z"),
        _assistant_line(message_id="call_300", input_tokens=3000, ts="2026-09-18T10:11:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}
    assert set(by_goal) == {100, 150, 200, 300}

    w100 = by_goal[100]["orchestrator_window"]
    # call_100 (1000+20) + call_150 (1500+20, the absorbed one) + #100's OWN dispatch line (1+1),
    # now correctly included in its own window instead of #200's dispatch line leaking backward
    # into it (SEVENTH fix round, see test 1's own docstring) -- numerically unchanged either way.
    assert w100["calls"] == 3 and w100["volume_total"] == (1000 + 20) + (1500 + 20) + (1 + 1), (
        "absorbs call_150")
    assert w100["boundary_partial"] is True and w100["boundary_partial_goals"] == [150]

    w200 = by_goal[200]["orchestrator_window"]
    # call_200 (2000+20) + #200's OWN dispatch line (1+1), now correctly included in its own window
    # instead of #300's dispatch line leaking backward into it (SEVENTH fix round) -- numerically
    # unchanged either way.
    assert w200["calls"] == 2 and w200["volume_total"] == (2000 + 20) + (1 + 1), "numerically exact"
    assert w200["boundary_partial"] is True and w200["boundary_partial_goals"] == [150], (
        "#200 is directly adjacent to #150 in SORTED order (goal_windows put #150 last), but must "
        "be flagged for the same report-wide reason as #100 and #300, not a different one")

    w300 = by_goal[300]["orchestrator_window"]
    # call_300 (3000+20) + #300's OWN dispatch line (1+1), now correctly included in its own
    # open-ended window instead of leaking backward into #200 (SEVENTH fix round) -- a real,
    # uncompensated +1 call/+2 volume versus the sixth round's own figure, since #300 (the last
    # resolved goal, open-ended) had nothing to lose to a leak.
    assert w300["calls"] == 2 and w300["volume_total"] == (3000 + 20) + (1 + 1), (
        "two slots away from #150, near-exact")
    assert w300["boundary_partial"] is True and w300["boundary_partial_goals"] == [150], (
        "#300 is NOT adjacent to #150 in either sorted or true chronological order, and its own "
        "figures are near-exact -- still flagged, proving the rule is report-wide, not "
        "adjacency-based")


def test_build_report_all_unresolved_goals_never_carry_a_boundary_partial_key(tmp_path):
    """All-unresolved sanity check (round 5's own shape, re-confirmed under this round's fix): with
    zero resolved goal-slots in the report, there is no orchestrator_window ever COMPUTED at all --
    both rows stay the plain two-key {'source': 'unavailable', 'reason': ...} shape, and
    'boundary_partial' never leaks into it. Locks in that the two shapes ('unavailable' vs
    'computed, maybe-partial') stay mutually exclusive after this round's change."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100_missing")
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200_missing")
    _write_transcript(proj / "sess1.jsonl", [
        _assistant_line(message_id="call_1", input_tokens=100, ts="2026-09-18T10:01:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    for row in report["goals"]:
        w = row["orchestrator_window"]
        assert w == {"source": "unavailable", "reason": "no resolvable dispatch timestamp"}
        assert "boundary_partial" not in w


def test_build_report_boundary_partial_is_computed_report_wide_even_under_an_explicit_goal_filter(tmp_path):
    """--goal narrows DISPLAY to one goal, but must not narrow the 'does an unresolved sibling
    exist anywhere in this report' fact it is computed from -- a caller asking only about #100 must
    still be told #100's window is boundary_partial because of #200, even though #200 itself is
    never in the returned report at all."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs100", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_100")
    _write_meta(sess, "gs200", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_200_missing")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_100", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_100", input_tokens=1000, ts="2026-09-18T10:01:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, goal=100)
    assert len(report["goals"]) == 1, "--goal narrows display to exactly the requested goal"
    w100 = report["goals"][0]["orchestrator_window"]
    assert w100["boundary_partial"] is True
    assert w100["boundary_partial_goals"] == [200]


def test_render_text_shows_a_boundary_partial_indicator_for_a_flagged_orchestrator_window():
    """render_text side: a hand-built report (bypassing build_report entirely, matching this file's
    own test_render_text_shows_the_reason_for_an_unavailable_phase_row precedent) with
    'boundary_partial'/'boundary_partial_reason' set must show that reason in the rendered
    orchestrator-window line; the subagent line (no such keys) must render exactly as before,
    unaffected."""
    report = {
        "source": "ok", "session": "s1", "since": None, "until": None,
        "orchestrator": {"calls": 0, "peak_context": 0, "volume_total": 0, "cost_usd": None,
                          "unpriced_calls": 0},
        "goals": [{
            "goal": 100, "agent_id": "gs1", "dispatch_ts": "2026-09-18 10:00:00", "until_ts": None,
            "subagent": {"calls": 5, "peak_context": 50, "volume_total": 60, "cost_usd": None,
                         "unpriced_calls": 0},
            "orchestrator_window": {"calls": 3, "peak_context": 30, "volume_total": 40,
                                     "cost_usd": None, "unpriced_calls": 0,
                                     "boundary_partial": True, "boundary_partial_goals": [200],
                                     "boundary_partial_reason": "goal(s) #200 in this same report "
                                     "have no resolvable dispatch timestamp"},
        }],
    }
    lines = ocr.render_text(report)
    orch_line = next(l for l in lines if l.strip().startswith("orchestrator-window"))
    assert "#200" in orch_line and "PARTIAL" in orch_line
    subagent_line = next(l for l in lines if l.strip().startswith("subagent"))
    assert "PARTIAL" not in subagent_line, "subagent row never carries boundary_partial"


# --------------------------------------------------------------------------- Second fix round
# (code review): ONE residual gap in Bug 1's own fix (Bug 3, below), plus a redundant-read
# efficiency finding.


# Bug 3 -- goal_slot_dispatch_ts guarded obj/message/content/block with isinstance (Bug 1), but
# `ts = obj.get("timestamp")` was then passed straight to _norm_ts_precise behind only a
# truthiness check (`if ts else None`), never a type check. A JSON `null` happens to be falsy and
# was already safe, but a JSON NUMBER (the reviewer's own repro), bool, or non-empty list/dict is
# TRUTHY and reached _norm_ts_precise's `s = iso_ts.strip()`, crashing with AttributeError -- the
# exact same failure class as Bug 1, propagating through the same
# goal_windows -> build_report -> cmd_report -> main chain, at one more call site the first fix
# missed.


def test_goal_slot_dispatch_ts_skips_a_line_whose_timestamp_is_a_json_number(tmp_path):
    """Watched RED against the pre-fix code (confirmed by hand before writing this fix): raised
    AttributeError("'int' object has no attribute 'strip'") out of _norm_ts_precise's
    `s = iso_ts.strip()` -- the reviewer's own repro shape, reproduced exactly."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": 1758189654,     # a JSON NUMBER, not a string
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent",
                                     "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}},
        }) + "\n")
    assert ocr.goal_slot_dispatch_ts(t, "toolu_1") is None    # must not raise


def test_goal_slot_dispatch_ts_skips_a_line_whose_timestamp_is_null(tmp_path):
    """A second malformed-type variation at the same field. `null` is FALSY, so the pre-fix
    `if ts else None` guard already happened to survive this one shape without crashing -- tested
    explicitly anyway, alongside the number and list variations below, so the full set of
    malformed-type shapes at this field is pinned down, not just the one the reviewer tried."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": None,
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent",
                                     "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}},
        }) + "\n")
    assert ocr.goal_slot_dispatch_ts(t, "toolu_1") is None    # must not raise


def test_goal_slot_dispatch_ts_skips_a_line_whose_timestamp_is_a_list(tmp_path):
    """A third malformed-type variation: unlike `null`, a NON-EMPTY list is TRUTHY in Python -- the
    pre-fix `if ts else None` guard would NOT have caught this one. Confirmed by hand it crashed
    the same way as the JSON-number case (AttributeError: 'list' object has no attribute 'strip')
    before this fix."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": ["2026-09-18T10:00:00.000Z"],
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "tool_use", "id": "toolu_1", "name": "Agent",
                                     "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}},
        }) + "\n")
    assert ocr.goal_slot_dispatch_ts(t, "toolu_1") is None    # must not raise


# Exhaustive re-check of the SAME bug class elsewhere in this module (as asked, not just the one
# reported line): classify_tree/goal_number consume meta.json's own 'description' and
# 'parentAgentId' fields, exactly as externally-parsed as goal_slot_dispatch_ts's own 'timestamp'
# -- and neither was type-guarded. Confirmed live, before this fix: a non-string truthy
# 'description' crashed _GOAL_SLOT_RE.search / _GOAL_NUM_RE.search with TypeError('expected string
# or bytes-like object'); a non-string (unhashable) 'parentAgentId' (e.g. a JSON list) crashed the
# `in goal_slots` membership test with TypeError('unhashable type: ...'). Both are reachable from
# the identical goal_windows -> build_report -> cmd_report -> main chain Bug 1/3 describe, via
# goal_windows's own classify_tree(walk_subagent_tree(...)) call.


def test_goal_number_returns_none_for_a_non_string_description_instead_of_raising():
    """Direct call site: classify_tree's phase-branch passes meta.get('description') into
    goal_number with NO coercion at all -- confirmed by hand this crashed with TypeError before
    this fix."""
    assert ocr.goal_number(12345) is None
    assert ocr.goal_number(["#100 goal-slot"]) is None
    assert ocr.goal_number({"not": "a string"}) is None


def test_classify_tree_skips_a_goal_slot_candidate_whose_description_is_not_a_string():
    t = ocr.classify_tree({
        "gs1": {"spawnDepth": 1, "description": 20260918, "agent_id": "gs1"},
    })
    assert t == {"goal_slots": {}, "phases": {}}     # must not raise


def test_classify_tree_skips_a_phase_candidate_whose_description_is_not_a_string():
    t = ocr.classify_tree({
        "gs1": {"spawnDepth": 1, "description": "#100 goal-slot -- x", "agent_id": "gs1"},
        "ph1": {"spawnDepth": 2, "parentAgentId": "gs1", "description": 12345,
                "agent_id": "ph1"},
    })
    assert t["goal_slots"] and t["phases"] == {}     # gs1 still classifies; ph1 dropped, not fatal


def test_classify_tree_skips_a_phase_candidate_whose_parent_agent_id_is_unhashable():
    t = ocr.classify_tree({
        "gs1": {"spawnDepth": 1, "description": "#100 goal-slot -- x", "agent_id": "gs1"},
        "ph1": {"spawnDepth": 2, "parentAgentId": ["not", "a", "string"],
                "description": "#100 x", "agent_id": "ph1"},
    })
    assert t["phases"] == {}                          # must not raise TypeError: unhashable type


# --------------------------------------------------------------------------- Second fix round:
# the non-blocking efficiency finding -- build_report re-read/re-parsed the entire top-level
# transcript ~2N+1 times for N goal-slots (once per goal-slot inside goal_slot_dispatch_ts via
# goal_windows, once per goal-slot inside _windowed_calls for orchestrator_window, plus once for
# the overall scope) -- the identical class of loop-invariant redundancy the first fix round
# already hoisted for classify_tree() in this same function. Both re-read sites are now hoisted:
# goal_windows resolves every goal-slot's dispatch ts in ONE pass (_dispatch_ts_for_ids), and
# build_report parses+dedupes the top-level transcript ONCE (top_level_calls) and filters that
# SAME list in memory per row (_filter_calls, which never mutates its input).


def test_build_report_gives_each_goal_slot_its_own_correct_window_after_the_read_is_shared(tmp_path):
    """Correctness guard for the caching fix: two goal-slots with DIFFERENT windows must each see
    only their OWN calls -- never the other's, never merged, never empty from a shared-and-
    corrupted cache. If top_level_calls were filtered by mutating it in place, or one goal's
    filtered view leaked into the next, this would catch it via both the call COUNTS and the
    VOLUME totals (which encode which specific call landed where).

    Each dispatch's OWN tool_use line is itself a (noise) assistant call. NUMBERS UPDATED (#2531,
    SEVENTH fix round -- _filter_calls now floors since_ts/until_ts to whole-second before
    comparing, see that function's own docstring): gs2's own tool_use line (10:05:00.000, exactly
    #100's until_ts) used to leak into #100, one goal EARLIER than its own dispatch, because a
    dispatch's whole-second-truncated c['ts'] always string-compared as LESS THAN the
    sub-second-precise since_ts/until_ts derived from that SAME instant. Post-fix it correctly
    lands in #200's own window instead (its own dispatch's own window, inclusive at since_ts) --
    and #100 in turn gains ITS OWN dispatch line (10:00:00.000, exactly its own since_ts, same
    fix), which the pre-fix code excluded the identical way. That is a wash for #100 (loses gs2's
    leaked 1+1, gains gs1's own 1+1 -- same total), but a real +1 call/+2 volume gain for #200,
    which is this fixture's LAST (open-ended) goal-slot and so had nothing to lose to a leak of its
    own. This is independent of, and unaffected by, the read-caching fix under test here; call_a's
    and call_b's own 111/222-input-token payloads stay large and distinct enough from the
    1-input-token dispatch-line noise that this test still cleanly proves each goal-slot keeps its
    OWN, non-shared, non-corrupted window."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_meta(sess, "gs2", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_2")
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z"),
        _assistant_line(message_id="call_a", input_tokens=111, ts="2026-09-18T10:02:00.000Z"),
        _tool_use_line("toolu_2", "2026-09-18T10:05:00.000Z"),
        _assistant_line(message_id="call_b", input_tokens=222, ts="2026-09-18T10:07:00.000Z"),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}
    # #100: call_a (111 in + 20 out) + gs1's OWN dispatch-line noise (1 in + 1 out) = 133.
    assert by_goal[100]["orchestrator_window"]["calls"] == 2
    assert by_goal[100]["orchestrator_window"]["volume_total"] == (111 + 20) + (1 + 1)
    # #200: call_b + gs2's OWN dispatch-line noise (1 in + 1 out), now correctly included in its
    # own window instead of leaking into #100 -- gs1's dispatch line / call_a are both safely in
    # the past.
    assert by_goal[200]["orchestrator_window"]["calls"] == 2
    assert by_goal[200]["orchestrator_window"]["volume_total"] == (222 + 20) + (1 + 1)
    # Neither window contains the OTHER goal's own real call (call_a/call_b) -- the exact equality
    # checks above already pin this down (133 vs 244 could not hold if either window absorbed the
    # other's real call), which is the actual cross-contamination this test exists to rule out.
    # The overall (unwindowed) scope sees ALL FOUR real lines (both dispatches' own tool_use lines
    # plus call_a and call_b), from the SAME shared parse -- proving top_level_calls is the
    # complete, unfiltered parse, not a leftover from either window.
    assert report["orchestrator"]["calls"] == 4


def test_build_report_top_level_transcript_reads_stay_constant_as_goal_slots_grow(tmp_path):
    """THE control for the redundant-read fix (AGENTS.md: 'run the control, or the check is
    decoration') -- counts real open() calls against the top-level transcript path while
    build_report runs, comparing a 2-goal-slot session against a 5-goal-slot session. Pre-fix this
    scaled with N (~2N+1: 5 vs 11); post-fix it must be the SAME small constant regardless of N.
    Patches builtins.open directly (not a mock of this module's own functions) so it observes the
    ACTUAL file-open behavior of the code under test -- confirmed by running this control against
    the pre-fix code first and observing it fail (small != large) before trusting this green."""
    import builtins
    import unittest.mock

    def _build_session(n_goals, home):
        proj = home / ".claude" / "projects" / "-x"
        sess = proj / "sess1"
        top = proj / "sess1.jsonl"
        lines = []
        for i in range(n_goals):
            goal = (i + 1) * 100
            _write_meta(sess, f"gs{i}", description=f"#{goal} goal-slot — full SDLC to merge",
                        spawnDepth=1, toolUseId=f"toolu_{i}")
            lines.append(_tool_use_line(f"toolu_{i}", f"2026-09-18T10:{i:02d}:00.000Z"))
        _write_transcript(top, lines)
        return top

    def _count_top_level_opens(n_goals, home):
        top = _build_session(n_goals, home)
        top_level_str = str(top)
        real_open = builtins.open
        counts = {"n": 0}

        def counting_open(file, *args, **kwargs):
            if str(file) == top_level_str:
                counts["n"] += 1
            return real_open(file, *args, **kwargs)

        with unittest.mock.patch("builtins.open", counting_open):
            report = ocr.build_report(str(home / ".sdlc"), "sess1", home=home)
        assert report["source"] == "ok"
        assert len(report["goals"]) == n_goals
        return counts["n"]

    small = _count_top_level_opens(2, tmp_path / "small")
    large = _count_top_level_opens(5, tmp_path / "large")
    assert small == large, (
        f"top-level transcript opens scaled with goal-slot count ({small} for 2 goal-slots, "
        f"{large} for 5) -- expected a constant independent of N; the re-read redundancy regressed")


def test_build_report_detail_mode_walks_the_subagent_tree_once_not_twice(tmp_path, monkeypatch):
    """Non-blocking finding (a), third fix round: --detail used to call
    classify_tree(walk_subagent_tree(session_dir)) TWICE per report -- once inside goal_windows
    (needed regardless of --detail), once more for its own separate `classified_for_detail` -- the
    identical redundancy class already fixed for the top-level transcript
    (test_build_report_top_level_transcript_reads_stay_constant_as_goal_slots_grow, above). Counts
    real calls to walk_subagent_tree itself, the cheapest correct signal (it is what actually globs
    + reads every meta.json under subagents/); a non-detail report must still cost exactly the same
    ONE call it always did."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_1")
    _write_transcript(proj / "sess1.jsonl", [_tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z")])

    real_walk = ocr.walk_subagent_tree
    counts = {"n": 0}

    def counting_walk(*a, **k):
        counts["n"] += 1
        return real_walk(*a, **k)

    monkeypatch.setattr(ocr, "walk_subagent_tree", counting_walk)
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, detail=True)
    assert report["source"] == "ok"
    assert counts["n"] == 1, f"expected exactly 1 subagents/ tree walk in --detail mode, got {counts['n']}"

    counts["n"] = 0
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, detail=False)
    assert report["source"] == "ok"
    assert counts["n"] == 1, f"non-detail mode must cost the same single walk, got {counts['n']}"


# --------------------------------------------------------------------------- Third fix round
# (code review): three prior rounds each patched exactly the one unguarded-JSON-type crash a
# reviewer's own repro handed them (non-string timestamp at one call site, then a second timestamp
# site, then non-string description, then non-hashable parentAgentId) and each time an independent
# re-review found MORE of the identical bug class elsewhere in the SAME file, because every fix was
# a spot-patch. This round's own explicit mandate: "audit every .get(...) call against JSON-parsed
# input in one pass this time, not spot-fixing named repro shapes one at a time." The shared
# accessors (phase_report.as_str/get_str/get_dict/get_list) are the structural fix; the tests below
# cover every call site the sweep actually found -- the 4 the review named explicitly (dedup_calls/
# message.id, iter_assistant_turns~norm_ts/timestamp, _dispatch_ts_for_ids/toolUseId,
# _cwd_within_repo/cwd) AND additional sites the sweep surfaced beyond those four: goal_windows's
# OWN separate dispatch_ts.get()/sort-key crash on a raw (unsanitized) toolUseId, and the tool_use
# BLOCK's own 'id' field inside _dispatch_ts_for_ids (a fourth, independent crash site within that
# one function, distinct from the toolUseId-as-input site the review named).


def test_dedup_calls_never_crashes_or_wrongly_merges_on_a_message_id_of_any_malformed_type():
    """dedup_calls is the single most universal function in this module -- every scope routes
    through it. An UNHASHABLE id (a JSON list/dict) crashed `key not in groups` with
    TypeError('unhashable type'), confirmed by hand against the pre-fix code. A HASHABLE-but-
    wrong-typed id (an int/float/bool) did NOT crash pre-fix, but silently used that raw value as a
    real, shared group key -- so two genuinely unrelated calls that both happened to carry the same
    malformed id (e.g. both `True`) were wrongly MERGED into one, the "silently corrupting" failure
    mode this round's own test-writing mandate calls out by name, not just a crash. Both are fixed
    identically: any non-string id, hashable or not, falls into the same 'own call, never merged'
    bucket a missing (None) id already had."""
    for bad_id in (["not", "hashable"], {"nope": True}, 12345, 1.5, True):
        raw = [
            {"ts": "t1", "message_id": bad_id, "model": "m", "usage": {"input_tokens": 1}},
            {"ts": "t2", "message_id": bad_id, "model": "m", "usage": {"input_tokens": 1}},
        ]
        deduped = ocr.dedup_calls(raw)          # must not raise
        assert len(deduped) == 2, (
            f"malformed id {bad_id!r} must not silently merge two unrelated calls into one")


def test_iter_raw_assistant_calls_survives_non_string_timestamp_message_id_and_model(tmp_path):
    """phase_report.iter_assistant_turns, reused here per this module's own docstring, only
    truthiness-guarded `timestamp` (`if not ts: continue`) -- a TRUTHY non-string (a JSON number)
    reached norm_ts's `.strip()` and crashed with AttributeError. `message.get('id')` and
    `message.get('model')` had no type guard at all. A malformed TIMESTAMP excludes the whole line
    (no ts, no call, matching this module's existing 'skip the malformed line' convention); a
    malformed MODEL likewise excludes the line (a call with no usable model can never be priced,
    matching iter_assistant_turns's existing '<synthetic>'-model exclusion); a malformed MESSAGE
    ID does NOT exclude the line -- only sanitizes the id to None, since ts/model are independently
    valid and a call is still real and countable even when its id cannot be trusted."""
    t = tmp_path / "t.jsonl"
    bad_ts_line = _assistant_line(message_id="bad_ts", input_tokens=20, output_tokens=20)
    bad_ts_line["timestamp"] = 1758189654                        # a JSON number, not a string
    bad_model_line = _assistant_line(message_id="bad_model", input_tokens=30, output_tokens=30)
    bad_model_line["message"]["model"] = {"not": "a string"}
    bad_id_line = _assistant_line(message_id="placeholder", input_tokens=40, output_tokens=40)
    bad_id_line["message"]["id"] = ["not", "hashable"]
    _write_transcript(t, [
        _assistant_line(message_id="good", input_tokens=10, output_tokens=1),
        bad_ts_line,
        bad_model_line,
        bad_id_line,
    ])
    calls = list(ocr.iter_raw_assistant_calls(t))    # must not raise
    assert [c["message_id"] for c in calls] == ["good", None]    # bad_ts/bad_model lines excluded
    assert calls[1]["ts"] and calls[1]["model"]                  # bad_id line still yielded, sane


def test_dispatch_ts_for_ids_never_crashes_on_malformed_ids_in_its_own_input(tmp_path):
    """THREE independent crash sites inside this one function (#2531, third fix round):
    (1) building `wanted` itself crashed on an UNHASHABLE id in `tool_use_ids` -- `{tid for tid in
        tool_use_ids if tid}` raises TypeError('unhashable type') trying to add it to the set;
    (2) a HASHABLE-but-non-string survivor (an int) crashed the very next line, the `tid in
        raw_line` pre-filter -- `in` against a `str` requires a `str` on the left;
    (3) the tool_use BLOCK's own 'id' field (a DIFFERENT dict than `tool_use_ids` -- see the next
        test) had no guard at all.
    This test drives (1) and (2) together by mixing malformed entries into the SAME iterable a real
    caller (goal_windows) builds from meta.json's own toolUseId fields; a genuinely-findable id
    among the malformed ones must still resolve correctly, not just "not crash"."""
    t = tmp_path / "orch.jsonl"
    _write_transcript(t, [_tool_use_line("toolu_good", "2026-09-18T10:00:00.000Z")])
    ids = ["toolu_good", ["unhashable"], {"also": "unhashable"}, 12345, 1.5, True, None, ""]
    found = ocr._dispatch_ts_for_ids(t, ids)         # must not raise
    assert found == {"toolu_good": "2026-09-18 10:00:00.000"}


def test_dispatch_ts_for_ids_never_crashes_when_a_tool_use_blocks_own_id_is_unhashable(tmp_path):
    """The FOURTH, independent crash site in the same function: `bid = block.get('id')` (the
    tool_use block's own id, read from the TRANSCRIPT LINE just parsed -- a completely different
    dict than `tool_use_ids`) had no guard at all. `bid in wanted` / `bid not in found` crashed the
    same way building `wanted` did, from the opposite direction. The malformed id's own list value
    deliberately contains the substring 'toolu_1' (as one of ITS OWN elements) so the cheap
    substring pre-filter does not skip the line before reaching the crash site -- same technique
    this file's own pre-existing Bug-1 tests use for the analogous obj/message-not-a-dict crashes."""
    t = tmp_path / "orch.jsonl"
    with open(t, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2026-09-18T10:00:00.000Z",
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": [{"type": "tool_use", "id": ["not", "hashable", "toolu_1"],
                                     "name": "Agent", "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}},
        }) + "\n")
    found = ocr._dispatch_ts_for_ids(t, ("toolu_1",))    # must not raise
    assert found == {}                                    # never found -- the real id is unusable


def test_goal_windows_never_crashes_when_a_tool_use_id_is_unhashable_and_still_reports_the_goal(tmp_path):
    """Even after _dispatch_ts_for_ids itself is hardened, goal_windows has its OWN separate crash
    site: `dispatch_ts.get(tool_use_id)` reads meta.get('toolUseId') RAW (unsanitized) a second
    time -- a plain dict.get() call still requires a HASHABLE key, so an unhashable toolUseId
    crashed here even though _dispatch_ts_for_ids itself never raised (it degrades safely to an
    empty `wanted` set and returns before ever touching the file). Two goal-slots with different
    unhashable toolUseId types (list, dict) prove this regardless of which one the sort visits
    first; both must still be reported (never dropped), each honestly unresolvable."""
    sess = tmp_path / "sess"
    top = tmp_path / "sess.jsonl"
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId=["not", "a", "string"])
    _write_meta(sess, "gs2", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId={"also": "not a string"})
    _write_transcript(top, [])
    windows = ocr.goal_windows(sess, top)            # must not raise, including the tied sort
    assert {w["goal"] for w in windows} == {100, 200}
    assert all(w["since_ts"] is None for w in windows), "malformed toolUseId must never be fabricated"


def test_cwd_within_repo_returns_false_instead_of_raising_for_every_non_string_json_type(tmp_path):
    """`pathlib.Path(cwd_value)` raises TypeError for anything that is not str/os.PathLike -- NOT
    one of the three exception types this function's own `except` clause already caught
    (OSError/RuntimeError/ValueError). Reachable through resolve_session_id's no-`--session`-given
    auto-fallback, i.e. this tool's own DEFAULT invocation path (see the next test for the
    end-to-end version)."""
    root = tmp_path / "repo"
    root.mkdir()
    for bad_cwd in (["a", "list"], {"a": "dict"}, 12345, 1.5, True):
        assert ocr._cwd_within_repo(bad_cwd, root) is False    # must not raise


def test_resolve_session_id_fallback_skips_a_session_whose_cwd_is_a_malformed_json_type(tmp_path, monkeypatch):
    """End-to-end version of the same bug, through the REAL fallback resolve_session_id runs with
    no explicit session id -- the tool's own default invocation path. The malformed-cwd session is
    strictly NEWER on disk (would win the mtime race if it were merely wrong-repo, not malformed)
    but must be silently excluded, never crash the whole lookup; a later, well-formed, same-repo
    session must still be the one found."""
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-x"
    proj.mkdir(parents=True)
    bad = proj / "bad-cwd-session.jsonl"
    good = proj / "good-session.jsonl"
    _write_transcript(bad, [{"type": "init", "cwd": ["not", "a", "string"]}])
    _write_transcript(good, [{"type": "init", "cwd": str(repo_root)}])
    import os
    os.utime(bad, (2_000_000_000, 2_000_000_000))       # strictly newer, but malformed -> excluded
    os.utime(good, (1, 1))
    assert ocr.resolve_session_id(None, home=home, repo_root=repo_root) == "good-session"


# --------------------------------------------------------------------------- Third fix round: the
# broad malformed-input matrix (the reviewer's own explicit ask, verbatim: "a genuinely broad
# malformed-input test... not one narrow test per repro shape as the last 2 rounds did"). One
# session carrying EVERY malformed shape identified in this round's sweep AT ONCE, run through the
# real public entry point (build_report) and, separately, the actual CLI subprocess -- proving the
# whole pipeline degrades honestly end to end, not just that each isolated helper does.


def _kitchen_sink_session(tmp_path):
    """One session with every malformed JSON-type shape this round's sweep found, alongside enough
    well-formed data to prove none of it silently corrupts a real number. Returns
    (home, sdlc_dir_str)."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"

    # -- top-level transcript: 2 real dispatches, 2 good calls, 3 malformed-but-survivable lines --
    bad_ts_line = _assistant_line(message_id="bad_ts", input_tokens=999, output_tokens=999)
    bad_ts_line["timestamp"] = 1758189654                         # excluded entirely: no usable ts
    bad_model_line = _assistant_line(message_id="bad_model", input_tokens=999, output_tokens=999,
                                      ts="2026-09-18T10:03:00.000Z")
    bad_model_line["message"]["model"] = {"not": "a string"}      # excluded entirely: no usable model
    bad_id_line = _assistant_line(message_id="placeholder", input_tokens=77, output_tokens=7,
                                   ts="2026-09-18T10:02:00.000Z")
    bad_id_line["message"]["id"] = ["not", "hashable"]             # survives, own ungrouped call
    _write_transcript(proj / "sess1.jsonl", [
        _tool_use_line("toolu_gs1", "2026-09-18T10:00:00.000Z"),                        # A: 1+1
        _assistant_line(message_id="good_1", input_tokens=100, output_tokens=10,
                         ts="2026-09-18T10:01:00.000Z"),                                 # B: 110
        bad_ts_line,                                                                     # C: excluded
        bad_id_line,                                                                     # D: 84
        bad_model_line,                                                                  # E: excluded
        _tool_use_line("toolu_gs2", "2026-09-18T10:10:00.000Z"),                        # F: 1+1
        _assistant_line(message_id="good_2", input_tokens=50, output_tokens=5,
                         ts="2026-09-18T10:11:00.000Z"),                                 # G: 55
    ])

    # -- subagents/ tree: 2 real goal-slots, 1 goal-slot with a malformed description (dropped
    # entirely), 1 goal-slot with a malformed toolUseId (reported, honestly unresolvable), 1 real
    # phase, 2 malformed phases (dropped) --
    _write_meta(sess, "gs1", description="#100 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_gs1")
    _write_meta(sess, "gs2", description="#200 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_gs2")
    _write_meta(sess, "gs_bad_desc", description=12345, spawnDepth=1, toolUseId="toolu_bad1")
    _write_meta(sess, "gs_bad_tool", description="#300 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId=["not", "a", "string"])
    _write_meta(sess, "ph_good", description="#100 a real phase", spawnDepth=2, parentAgentId="gs1")
    _write_meta(sess, "ph_bad_desc", description=["bad"], spawnDepth=2, parentAgentId="gs1")
    _write_meta(sess, "ph_bad_parent", description="#100 orphaned by a bad parent", spawnDepth=2,
                parentAgentId={"bad": "parent"})
    return home


def test_build_report_survives_every_known_malformed_json_type_shape_at_once(tmp_path):
    """THE broad test. Would this have caught all of this round's 4 named crash sites in one run?
    Yes -- bad_id_line exercises dedup_calls (crash 1) end to end; bad_ts_line exercises
    iter_assistant_turns/norm_ts (crash 2); gs_bad_tool's toolUseId exercises _dispatch_ts_for_ids
    AND goal_windows (crash 3, both of that function's own internal sites plus goal_windows's own
    separate one); cwd is the one shape that genuinely cannot appear here (build_report takes
    session_id directly, never calls resolve_session_id) -- see the dedicated cwd tests above and
    test_cli_report_survives_a_session_with_every_malformed_shape_at_once below for that one,
    through the real CLI, which DOES call resolve_session_id. Also exercises this round's
    additional finds beyond the 4 named: bad_model_line, and gs_bad_desc/ph_bad_desc/ph_bad_parent.
    Every assertion below is an exact hand-computed number, not just 'did not raise' -- proving the
    malformed lines are excluded from the arithmetic, never silently corrupting it."""
    home = _kitchen_sink_session(tmp_path)
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home, detail=True)
    assert report["source"] == "ok"                  # must not raise, anywhere in the pipeline

    # Orchestrator scope: A(1+1) + B(110) + D(84, malformed id, own ungrouped call) + F(1+1) +
    # G(55) = 5 calls, volume 253 -- C (bad ts) and E (bad model) cleanly excluded, never counted.
    o = report["orchestrator"]
    assert o["calls"] == 5, "bad_ts_line/bad_model_line must be excluded, bad_id_line must survive"
    assert o["volume_total"] == (1 + 1) + 110 + 84 + (1 + 1) + 55

    by_goal = {row["goal"]: row for row in report["goals"]}
    # gs_bad_desc (description=12345) never becomes a goal at all -- not #12345, not any bucket.
    assert set(by_goal) == {100, 200, 300}

    assert by_goal[100]["dispatch_ts"] == "2026-09-18 10:00:00.000"
    # gs1's window [10:00:00.000, 10:10:00.000): A + B + D only (F/G belong to gs2's window).
    assert by_goal[100]["orchestrator_window"]["calls"] == 3
    assert by_goal[100]["orchestrator_window"]["volume_total"] == (1 + 1) + 110 + 84

    assert by_goal[200]["dispatch_ts"] == "2026-09-18 10:10:00.000"

    # gs_bad_tool (#300): malformed toolUseId -> honestly unresolvable, but STILL reported, never
    # dropped and never fabricated -- matching this module's pre-existing 'unresolvable since_ts'
    # contract (test_goal_windows_reports_unresolvable_dispatch_ts_honestly_never_fabricated).
    assert by_goal[300]["dispatch_ts"] is None

    # --detail: exactly the one well-formed phase under gs1; both malformed phases safely dropped.
    phase_descriptions = {p["agent_id"] for p in by_goal[100]["phases"]}
    assert phase_descriptions == {"ph_good"}, (
        "ph_bad_desc (non-string description) and ph_bad_parent (unhashable parentAgentId) must "
        "both be dropped, not merely non-crashing")


def test_cli_report_survives_a_session_with_every_malformed_shape_at_once(tmp_path):
    """The same fixture, through the ACTUAL CLI subprocess (not a direct Python call) -- 'as close
    to the real CLI path as practical', the tool's genuinely real invocation. Both text and JSON
    output formats must survive and exit 0 -- json.dumps must not choke on anything build_report
    stored either (a malformed value sanitized to None serializes fine; the previously-unsanitized
    version would have too, so this is really proving the exit code / no-traceback contract, which
    the earlier direct build_report test cannot see -- a caught exception inside cmd_report's own
    try/except-free body would otherwise surface as a non-zero exit with a raw Python traceback on
    stderr)."""
    home = _kitchen_sink_session(tmp_path)
    r = _run("report", str(tmp_path / ".sdlc"), "--session", "sess1", "--home", str(home))
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "traceback" not in r.stderr.lower()
    assert "ORCHESTRATOR" in r.stdout
    assert "#100" in r.stdout and "#200" in r.stdout and "#300" in r.stdout

    r_json = _run("report", str(tmp_path / ".sdlc"), "--session", "sess1", "--home", str(home),
                   "--format", "json")
    assert r_json.returncode == 0, f"stderr: {r_json.stderr}"
    payload = json.loads(r_json.stdout)              # must round-trip through json.dumps cleanly
    assert payload["orchestrator"]["calls"] == 5


# --------------------------------------------------------------------------- Fourth fix round:
# regex-priority bug -- a confounding bare "#N" sitting at the very START of a description beat a
# later, more specific "#<N> goal-slot" marker, because the two shapes were folded into one `A|B|C`
# alternation and re.search always returns the LEFTMOST match, trying alternatives in listed order
# only as a tiebreak AT THE SAME position -- it cannot express "prefer a less-leftmost but more
# specific alternative". The pre-existing quoted-parent test above
# (test_goal_number_is_anchored_to_the_goal_slots_own_number_not_a_quoted_parent) happened to still
# pass because ITS confounder ("Story #100: #2531 goal-slot") is not itself anchored to string
# start, so branch 1 (^#(\d+)) already failed at position 0 and the scan reached the real marker --
# masking this bug for any shape where the confounder is the very first token in the string.


def test_goal_number_prefers_the_goal_slot_marker_over_a_confounder_at_string_start():
    """The reviewer's exact repro. Pre-fix, classify_tree({"gs_real": {"spawnDepth": 1,
    "description": "#100 #200 goal-slot -- full SDLC to merge", "toolUseId": "toolu_real"}})
    attached goal 100 (the confounder) instead of 200 (the real, specific goal-slot marker) --
    even though _GOAL_SLOT_RE independently confirms "#200 goal-slot" IS a valid marker in that
    same string. Direct goal_number call first (isolates the regex bug from classify_tree's own
    logic), then the identical shape through classify_tree end to end (the reviewer's own repro)."""
    assert ocr.goal_number("#100 #200 goal-slot -- full SDLC to merge") == 200

    classified = ocr.classify_tree({"gs_real": {"spawnDepth": 1,
        "description": "#100 #200 goal-slot -- full SDLC to merge", "toolUseId": "toolu_real"}})
    assert classified["goal_slots"]["gs_real"]["goal"] == 200


def test_goal_number_still_prefers_the_goal_slot_marker_when_the_confounder_comes_second():
    """Reverse ordering of the bug repro above -- the real marker first, a bare confounder after.
    This direction never broke (branch 1 already matched the real number at position 0 in the
    pre-fix code too, since the real marker WAS the leading '#N'), but it must keep working
    unchanged under the new two-pass priority scheme, not just the confounder-first direction the
    bug repro itself exercises."""
    assert ocr.goal_number("#200 goal-slot -- full SDLC to merge, see also #100") == 200


def test_goal_number_priority_holds_with_three_hash_numbers_in_one_description():
    """Not just a narrow patch for the one exact repro shape -- the goal-slot marker must win
    regardless of how many bare #N confounders surround it, before AND after, adjacent or not."""
    assert ocr.goal_number("#100 #200 #300 goal-slot -- full SDLC to merge") == 300
    assert ocr.goal_number("Epic #999: #100 #200 goal-slot -- also references #300 later") == 200


def test_goal_number_falls_back_to_string_start_when_no_goal_slot_marker_exists():
    """Non-regression: a description with NO '#N goal-slot' marker anywhere must still resolve via
    the string-start / 'goal #N' fallbacks exactly as before -- the new two-pass priority scheme
    must not accidentally suppress these just because the higher-priority pass runs first and
    (correctly) finds nothing to match."""
    assert ocr.goal_number("#2531 P5 IMPLEMENT — orchestrator context report") == 2531
    assert ocr.goal_number("#2628 P5 IMPLEMENT — extract coherence validator") == 2628
    assert ocr.goal_number("Research goal #2531 measurement approach") == 2531
    assert ocr.goal_number("Plan fix for goal #2543") == 2543
    assert ocr.goal_number("no number here") is None
    assert ocr.goal_number(None) is None


# --------------------------------------------------------------------------- Fourth fix round:
# goal_slot_dispatch_ts's own trailing .get(tool_use_id) reads its RAW parameter directly, unlike
# every peer in this module (dedup_calls/message_id, goal_number/description,
# _dispatch_ts_for_ids's own `wanted` set, goal_windows's own dispatch_ts.get() -- fixed third
# round, see test_goal_windows_never_crashes_when_a_tool_use_id_is_unhashable_and_still_reports_
# the_goal above), which all sanitize via as_str/get_str before using the value as a dict key or
# set member. dict.get() requires a HASHABLE key regardless of the dict's size -- even the EMPTY
# dict _dispatch_ts_for_ids correctly returns for an unhashable id does not save the final .get()
# call, since Python hashes the key before ever consulting the dict's contents. Confirmed dead from
# the real CLI/build_report path today (goal_windows never calls this function -- it uses
# _dispatch_ts_for_ids directly, with its own independent guard), reachable only through this
# function's own direct callers/tests -- fixed anyway for consistency with the rest of this
# module's now-structural guard discipline.


def test_goal_slot_dispatch_ts_never_crashes_when_the_id_itself_is_unhashable(tmp_path):
    """dict.get() on an unhashable key raises TypeError('unhashable type') even on an EMPTY dict --
    confirmed by hand against the pre-fix code, both with and without a matching transcript line
    present, so this is not merely testing the not-found path."""
    t = tmp_path / "orch.jsonl"
    _write_transcript(t, [_tool_use_line("toolu_1", "2026-09-18T10:00:00.000Z")])
    assert ocr.goal_slot_dispatch_ts(t, ["not", "hashable"]) is None     # must not raise
    assert ocr.goal_slot_dispatch_ts(t, {"also": "unhashable"}) is None  # must not raise


# --------------------------------------------------------------------------- Seventh fix round: a
# BATCH dispatch -- one orchestrator turn emitting SEVERAL `Agent` tool_use blocks at once, all as
# content blocks of one assistant message, so all of it shares ONE message.id even though each
# block/line carries its OWN distinct timestamp and its OWN distinct tool_use id (naming a
# DIFFERENT goal-slot per line). Exactly `/agrim-loop`'s own real batch-dispatch shape, and exactly
# what happened for #2531/#2543/#2544's own dispatch in the live session that found this bug.
# dedup_calls (unchanged this round, see its own docstring) correctly collapses the group to ONE
# call, never triple-counting the shared usage -- but the pre-fix _filter_calls compared that
# merged call's own whole-second ts against every goal-slot's own sub-second-precise since_ts/
# until_ts at MISMATCHED granularity (see that function's own docstring), so the merged call sorted
# before EVERY goal-slot's own window and vanished from all three, while staying fully visible in
# the whole-session total. Verified directly against dd8a2b2a-adb2-47af-9710-ac54e9f9f443.jsonl
# (this exact live session) before writing any test below: lines 1727/1729/1731 share message.id
# msg_011CfAi7XtE2g9KVdFJ6g2vN, each with a DIFFERENT timestamp (10:00:18.175 / :33.734 / :54.234)
# and a DIFFERENT tool_use id naming #2543/#2544/#2531 respectively, but BYTE-IDENTICAL usage
# (input=2, cache_creation=553, cache_read=581856, output=7045 -- the turn's one, final, complete
# total) -- confirming the raw lines are NOT interchangeable duplicates the way dedup_calls's own
# docstring describes elsewhere (each carries real, distinguishing data beyond the shared id), even
# though their USAGE genuinely is one shared, joint cost that cannot be split three ways without
# fabricating a number (AGENTS.md RELIABILITY: "a shared counter's delta is not attribution").


def _batch_tool_use_line(tool_use_id, ts, message_id, usage, name="Agent"):
    """Like _tool_use_line, but for a raw line that is ONE of several sharing a single message.id
    -- the real batch-dispatch shape documented in the section comment above. A separate helper
    from _tool_use_line, not an added kwarg on it: this file's ~25 existing _tool_use_line call
    sites were all individually re-verified by hand for this round (.sdlc/plans/2531.md's own
    Seventh fix round section names each one and its exact before/after), and every one of them
    keeps its exact prior, tested shape (no message.id at all) untouched by adding a new, separate
    helper instead of changing the signature of the one they already use."""
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": message_id, "role": "assistant", "model": "claude-sonnet-5",
                        "content": [{"type": "tool_use", "id": tool_use_id, "name": name,
                                     "input": {}}],
                        "usage": usage}}


def test_filter_calls_no_longer_excludes_a_call_sitting_exactly_on_a_sub_second_dispatch_boundary():
    """Unit-level pin of the exact mechanism _filter_calls's own docstring now describes: a call
    whose (already whole-second) ts EQUALS a sub-second-precise since_ts, unfloored, used to
    string-compare as LESS than it ("2026-09-18 10:00:54" < "2026-09-18 10:00:54.234") and be
    wrongly excluded -- watched RED against the pre-fix comparison directly (bare `>=`/`<` against
    the unfloored since_ts/until_ts below) before trusting this green, per AGENTS.md 'run the
    control': reverting _floor_to_whole_second to an identity function reproduces exactly this
    failure, confirmed by hand for this round."""
    calls = [{"ts": "2026-09-18 10:00:54", "model": "m", "usage": {"input_tokens": 1}}]
    since_ts = "2026-09-18 10:00:54.234"    # sub-second-precise, from goal_windows
    until_ts = None
    assert ocr._filter_calls(calls, since_ts, until_ts) == calls, (
        "a call sitting exactly on its own window's since_ts must be INCLUDED, not excluded by a "
        "granularity mismatch between the call's whole-second ts and a sub-second boundary")


def test_floor_to_whole_second_strips_fractional_seconds_and_passes_none_through():
    assert ocr._floor_to_whole_second("2026-09-18 10:00:54.234") == "2026-09-18 10:00:54"
    assert ocr._floor_to_whole_second("2026-09-18 10:00:54") == "2026-09-18 10:00:54"
    assert ocr._floor_to_whole_second(None) is None


def test_build_report_a_batch_dispatch_sharing_one_message_id_is_not_silently_lost_from_every_goals_window(tmp_path):
    """THE round-7 regression, reproduced with the real shape from the section comment above (3
    goal-slots sharing one message.id, not 2 -- the round's own instruction). Watched RED against
    the pre-fix code first (confirmed by hand: with _filter_calls's flooring reverted to a bare,
    unfloored comparison, this test's own key assertion -- 'the shared call's usage must land in
    exactly one goal's window' -- failed with total_calls == 0 across all three, reproducing the
    reported bug exactly; restoring the fix turns it green)."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gs2543", description="#2543 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_2543")
    _write_meta(sess, "gs2544", description="#2544 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_2544")
    _write_meta(sess, "gs2531", description="#2531 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_2531")
    shared_usage = {"input_tokens": 2, "cache_creation_input_tokens": 553,
                     "cache_read_input_tokens": 581856, "output_tokens": 7045}
    _write_transcript(proj / "sess1.jsonl", [
        _batch_tool_use_line("toolu_2543", "2026-09-18T10:00:18.175Z", "msg_shared", shared_usage),
        _batch_tool_use_line("toolu_2544", "2026-09-18T10:00:33.734Z", "msg_shared", shared_usage),
        _batch_tool_use_line("toolu_2531", "2026-09-18T10:00:54.234Z", "msg_shared", shared_usage),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}
    assert set(by_goal) == {2543, 2544, 2531}

    # The whole-session scope sees the merged call regardless -- dedup_calls collapses the 3 raw
    # lines (one shared message.id) to ONE call, unaffected by this round's fix (see that
    # function's own docstring for why its ts=MIN policy was kept, not changed).
    assert report["orchestrator"]["calls"] == 1
    shared_volume = 2 + 553 + 581856 + 7045
    assert report["orchestrator"]["volume_total"] == shared_volume

    windows = {g: by_goal[g]["orchestrator_window"] for g in (2543, 2544, 2531)}
    total_calls = sum(w["calls"] for w in windows.values())
    total_volume = sum(w["volume_total"] for w in windows.values())
    assert total_calls == 1, (
        f"the ONE shared merged call must appear in EXACTLY one goal's own window -- never zero "
        f"(the reported bug: silently excluded from all three) and never more than one (double "
        f"counting); got {total_calls} across #2543={windows[2543]['calls']}, "
        f"#2544={windows[2544]['calls']}, #2531={windows[2531]['calls']}")
    assert total_volume == shared_volume, (
        "the shared call's own usage must be counted exactly once across all three goals' windows")

    # Deterministic attribution, pinned exactly, not just "somewhere": dedup_calls's own ts=MIN
    # policy (unchanged this round) coincides exactly with the group's EARLIEST goal-slot -- #2543,
    # the first dispatched -- so once _filter_calls compares like-for-like granularity, the merged
    # call lands there, not in #2544 or #2531.
    assert windows[2543]["calls"] == 1 and windows[2543]["volume_total"] == shared_volume
    assert windows[2544]["calls"] == 0 and windows[2544]["volume_total"] == 0
    assert windows[2531]["calls"] == 0 and windows[2531]["volume_total"] == 0


def test_build_report_a_batch_dispatch_of_two_goal_slots_still_conserves_the_shared_calls_usage(tmp_path):
    """Narrower, 2-goal-slot version of the same regression -- proves the fix is not an artifact of
    the 3-goal-slot shape specifically. Same real-data usage payload, a shorter 12-second gap
    (matching nothing in particular -- just a second, independent gap size from the 3-goal test
    above, so neither test's own numbers could pass by an accidental coincidence of the other's)."""
    home = tmp_path
    proj = home / ".claude" / "projects" / "-x"
    sess = proj / "sess1"
    _write_meta(sess, "gsA", description="#400 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_400")
    _write_meta(sess, "gsB", description="#500 goal-slot — full SDLC to merge", spawnDepth=1,
                toolUseId="toolu_500")
    shared_usage = {"input_tokens": 2, "cache_creation_input_tokens": 553,
                     "cache_read_input_tokens": 581856, "output_tokens": 7045}
    _write_transcript(proj / "sess1.jsonl", [
        _batch_tool_use_line("toolu_400", "2026-09-18T11:00:00.000Z", "msg_shared_2", shared_usage),
        _batch_tool_use_line("toolu_500", "2026-09-18T11:00:12.500Z", "msg_shared_2", shared_usage),
    ])
    report = ocr.build_report(str(tmp_path / ".sdlc"), "sess1", home=home)
    by_goal = {row["goal"]: row for row in report["goals"]}
    windows = {g: by_goal[g]["orchestrator_window"] for g in (400, 500)}
    total_calls = sum(w["calls"] for w in windows.values())
    assert total_calls == 1, (
        f"got {total_calls}: #400={windows[400]['calls']}, #500={windows[500]['calls']}")
    shared_volume = 2 + 553 + 581856 + 7045
    assert sum(w["volume_total"] for w in windows.values()) == shared_volume
    assert windows[400]["calls"] == 1 and windows[500]["calls"] == 0, (
        "same deterministic attribution as the 3-goal test above: dedup_calls's ts=MIN coincides "
        "with the group's earliest goal-slot, #400")
