#!/usr/bin/env python3
"""Meter Claude Code transcripts with Sigma's single transcript-pricing engine.

Examples:
    python3 evals/bench/meter.py --paths /tmp/session.jsonl
    python3 evals/bench/meter.py --session-id <uuid> --projects-dir ~/.claude/projects

The output is one JSON object.  An incomplete price is never presented as a
total: ``cost_usd`` is null whenever at least one turn was unpriced, while
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


def meter_transcripts(paths, rates=None):
    """Aggregate ``phase_report.price_transcript`` results for exact paths."""
    tokens_in = tokens_out = turns = unpriced_turns = 0
    models = []
    priced_part = 0.0
    priced_any = False
    transcripts = [str(pathlib.Path(path)) for path in paths]
    for path in transcripts:
        priced = phase_report.price_transcript(path, rates=rates)
        if priced is None:
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
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": priced_part if unpriced_turns == 0 else None,
        "cost_usd_priced_part": priced_part,
        "unpriced_turns": unpriced_turns,
        "turns": turns,
        "models": models,
        "transcripts": transcripts,
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
