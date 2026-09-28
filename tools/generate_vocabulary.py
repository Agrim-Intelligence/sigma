#!/usr/bin/env python3
"""Generate contract/vocabulary.json from ledger.py constants.

This script reads KINDS, EVENT_KINDS, EVENT_FIELDS, PHASE_KINDS, GATE_KINDS,
VERDICTS, REASON_CLASSES, and RETRO_GRADES from ledger.py, and severity_order
from pipeline.py's _ORDER, and emits a single authoritative vocabulary.json
file. It reads only core sources. The output is deterministic (sorted keys)
and must never be hand-edited.

Usage:
    python3 tools/generate_vocabulary.py > contract/vocabulary.json
"""
import importlib.util
import json
import pathlib
import sys

# The repo root is 2 levels up from this file (when in repo root):
# __file__ is /path/to/sigma/tools/generate_vocabulary.py
# .parent = /path/to/sigma/tools
# .parent.parent = /path/to/sigma (repo root)
repo_root = pathlib.Path(__file__).parent.parent
ledger_path = repo_root / "skills" / "agrim-loop" / "scripts" / "ledger.py"

if not ledger_path.exists():
    print(f"Error: ledger.py not found at {ledger_path}", file=sys.stderr)
    sys.exit(1)

# severity_order is not one of ledger.py's own record kinds: it is the report card's own
# `_ORDER` in skills/agrim-loop/scripts/pipeline.py, loaded by path like ledger.py below. The
# generator reads only core sources, so it runs unchanged in the public core (#2584).
pipeline_path = repo_root / "skills" / "agrim-loop" / "scripts" / "pipeline.py"
pipeline_spec = importlib.util.spec_from_file_location("pipeline", pipeline_path)
pipeline_module = importlib.util.module_from_spec(pipeline_spec)
try:
    pipeline_spec.loader.exec_module(pipeline_module)
except Exception as e:
    print(f"Error: could not load pipeline.py: {e}", file=sys.stderr)
    sys.exit(1)

# Use importlib to load ledger.py directly
spec = importlib.util.spec_from_file_location("ledger", ledger_path)
ledger = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(ledger)
except Exception as e:
    print(f"Error: could not load ledger.py: {e}", file=sys.stderr)
    sys.exit(1)

# Read VERSION file
version_file = repo_root / "contract" / "VERSION"
if not version_file.exists():
    print(f"Error: {version_file} does not exist yet", file=sys.stderr)
    sys.exit(1)

version_str = version_file.read_text().strip()
# Extract major.minor from VERSION (e.g., "1.0.0" -> "1.0")
parts = version_str.split(".")
if len(parts) < 2:
    print(f"Error: VERSION must be in format X.Y.Z, got {version_str}", file=sys.stderr)
    sys.exit(1)
contract_version = f"{parts[0]}.{parts[1]}"

# Verify all required constants exist
required_constants = {
    "KINDS": ledger.KINDS,
    "EVENT_KINDS": ledger.EVENT_KINDS,
    "EVENT_FIELDS": ledger.EVENT_FIELDS,
    "PHASE_KINDS": ledger.PHASE_KINDS,
    "GATE_KINDS": ledger.GATE_KINDS,
    "VERDICTS": ledger.VERDICTS,
    "REASON_CLASSES": ledger.REASON_CLASSES,
    "RETRO_GRADES": ledger.RETRO_GRADES,
    "ENTRY_SCHEMAS": ledger.ENTRY_SCHEMAS,
}

for name, value in required_constants.items():
    if not value:
        print(f"Error: ledger.{name} is empty or missing", file=sys.stderr)
        sys.exit(1)

# Verify merge-armed is in KINDS
if "merge-armed" not in ledger.KINDS:
    print("Error: ledger.KINDS must include 'merge-armed'", file=sys.stderr)
    sys.exit(1)

# Build the vocabulary object
# Convert EVENT_FIELDS tuples to lists for JSON serialization
event_fields_as_lists = {}
for kind, fields in ledger.EVENT_FIELDS.items():
    event_fields_as_lists[kind] = list(fields) if isinstance(fields, tuple) else fields

vocabulary = {
    "contract_version": contract_version,
    "entries_kinds": list(ledger.KINDS),
    "event_kinds": list(ledger.EVENT_KINDS),
    "event_fields": event_fields_as_lists,
    "entry_schemas": ledger.ENTRY_SCHEMAS,
    "phase_kinds": list(ledger.PHASE_KINDS),
    "gate_kinds": list(ledger.GATE_KINDS),
    "verdicts": list(ledger.VERDICTS),
    "reason_classes": list(ledger.REASON_CLASSES),
    "retro_grades": list(ledger.RETRO_GRADES),
    "severity_order": dict(pipeline_module._ORDER),
}

# Emit with deterministic formatting, as LF bytes: a text-mode stdout would write CRLF on Windows
# through the documented `> contract/vocabulary.json` redirect (#2623).
json_output = json.dumps(vocabulary, sort_keys=True, indent=2)
sys.stdout.buffer.write((json_output + "\n").encode("utf-8"))
