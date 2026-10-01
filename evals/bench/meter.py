#!/usr/bin/env python3
"""Meter Claude Code transcripts with Sigma's single transcript-pricing engine.

Examples:
    python3 evals/bench/meter.py --paths /tmp/session.jsonl
    python3 evals/bench/meter.py --session-id <uuid> --projects-dir ~/.claude/projects

The output is one JSON object.  An incomplete price is never presented as a
total: ``cost_usd`` is null whenever at least one turn was unpriced or an
explicitly requested transcript was unavailable, while
``cost_usd_priced_part`` retains the amount that the rate card can prove.
"""
import argparse
import importlib.util
import json
import pathlib
import sys


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PHASE_REPORT = ROOT / "skills" / "agrim-loop" / "scripts" / "phase_report.py"


def _load_phase_report():
    """Load the shared pricing engine by path, as sibling Sigma scripts do."""
    spec = importlib.util.spec_from_file_location("bench_phase_report", PHASE_REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase_report = _load_phase_report()


def session_paths(session_id, projects_dir):
    """Find a Claude transcript and every dispatched subagent for ``session_id``.

    ``projects_dir`` is supplied by the operator rather than inferred from a
    host-specific home directory.  This keeps benchmark runs portable and
    permits an archived transcript store to be metered without copying it.
    """
    root = pathlib.Path(projects_dir)
    top_level = sorted(path for path in root.glob(f"*/{session_id}.jsonl") if path.is_file())
    subagents = sorted(path for path in root.glob(f"*/{session_id}/subagents/agent-*.jsonl")
                       if path.is_file())
    return top_level + subagents


def _rate_details(rate):
    """Return only rate-card facts needed to recompute a charge, never turn content."""
    if rate is None:
        return None
    details = {
        "effective_from": rate["effective_from"],
        "effective_to": rate["effective_to"],
        "source": rate["source"],
    }
    if rate["usd_per_mtok"] is not None:
        details["usd_per_mtok"] = rate["usd_per_mtok"]
    if rate["usd_per_request"] is not None:
        details["usd_per_request"] = rate["usd_per_request"]
    return details


def _rate_key(model, rate_kind, per_request, rate):
    """Keep distinct price vintages separate in published aggregate evidence."""
    details = _rate_details(rate)
    return (model, rate_kind, per_request,
            None if details is None else tuple(sorted(details.items())))


def usage_by_rate_kind(paths, rates):
    """Return content-free, fully-priced usage and price contributions by rate kind.

    The rows intentionally mirror ``phase_report.price_turn``: a turn enters the breakdown only
    when every rate kind prices.  Otherwise an unpriced turn could leak a partial set of charges
    into an amount a reader mistakes for the transcript total.  A missing request-count field is
    the same confirmed-zero rule as ``price_turn``.  Every rate kind is retained, including a
    zero-unit kind with no applicable card row, so the evidence says what was considered rather
    than silently presenting input/output as the whole bill.
    """
    grouped = {}
    order = []
    for path in paths:
        turns = phase_report._dedup_turns_by_message_id(
            phase_report.iter_assistant_turns(path))
        for turn in turns:
            priced_kinds = []
            for rate_kind, (path_tuple, per_request) in phase_report.RATE_KIND_USAGE.items():
                units = phase_report.usage_value(turn["usage"], path_tuple)
                if units is None and per_request:
                    units = 0
                rate = phase_report.select_rate(rates, turn["model"], rate_kind, turn["ts"])
                cost = phase_report.price_units(units, rate, per_request)
                if cost is None:
                    priced_kinds = None
                    break
                priced_kinds.append((rate_kind, per_request, units, rate, cost))
            if priced_kinds is None:
                continue
            for rate_kind, per_request, units, rate, cost in priced_kinds:
                key = _rate_key(turn["model"], rate_kind, per_request, rate)
                if key not in grouped:
                    grouped[key] = {
                        "model": turn["model"],
                        "rate_kind": rate_kind,
                        "unit": "requests" if per_request else "tokens",
                        "units": 0,
                        "cost_usd": 0.0,
                        "rate": _rate_details(rate),
                    }
                    if rate is None:
                        grouped[key]["zero_unit_rule"] = (
                            "A zero-unit kind costs $0 even when the rate card has no row.")
                    order.append(key)
                grouped[key]["units"] += units
                grouped[key]["cost_usd"] += cost
    # The meter total is rounded to six decimal places; retain more precision in a line item so
    # summing these displayed contributions and applying that same final rounding reproduces it.
    for key in order:
        grouped[key]["cost_usd"] = round(grouped[key]["cost_usd"], 12)
    return [grouped[key] for key in order]


def meter_transcripts(paths, rates=None):
    """Aggregate ``phase_report.price_transcript`` results for exact paths."""
    tokens_in = tokens_out = turns = unpriced_turns = 0
    models = []
    priced_part = 0.0
    priced_any = False
    transcripts = [str(pathlib.Path(path)) for path in paths]
    unavailable_inputs = []
    for path in transcripts:
        priced = phase_report.price_transcript(path, rates=rates)
        if priced is None:
            unavailable_inputs.append(path)
            continue
        tokens_in += priced["tokens_in"]
        tokens_out += priced["tokens_out"]
        turns += priced["turns"]
        unpriced_turns += priced["unpriced_turns"]
        for model in priced["models"]:
            if model not in models:
                models.append(model)
        if priced["cost_usd"] is not None:
            priced_part += priced["cost_usd"]
            priced_any = True

    priced_part = round(priced_part, 6) if priced_any else None
    rate_rows = rates if rates is not None else phase_report.load_rate_rows()
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": (priced_part
                     if unpriced_turns == 0 and not unavailable_inputs else None),
        "cost_usd_priced_part": priced_part,
        "unavailable_inputs": unavailable_inputs,
        "unpriced_turns": unpriced_turns,
        "turns": turns,
        "models": models,
        "transcripts": transcripts,
        "usage_by_rate_kind": usage_by_rate_kind(transcripts, rate_rows),
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--session-id", help="Claude Code session UUID to discover")
    inputs.add_argument("--paths", nargs="+", metavar="JSONL", help="exact transcript paths")
    parser.add_argument("--projects-dir", metavar="DIR", help="Claude projects transcript directory")
    args = parser.parse_args(argv)
    if args.session_id and not args.projects_dir:
        parser.error("--session-id requires --projects-dir")
    if args.paths and args.projects_dir:
        parser.error("--projects-dir is only valid with --session-id")
    return args


def main(argv=None):
    args = parse_args(argv)
    paths = (session_paths(args.session_id, args.projects_dir)
             if args.session_id else args.paths)
    print(json.dumps(meter_transcripts(paths), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
