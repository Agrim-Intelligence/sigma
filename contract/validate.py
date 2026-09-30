#!/usr/bin/env python3
"""Validate record files against the contract vocabulary.

Validates JSON/JSONL files against the contract's vocabulary and schema rules.
Supports validation of:
  - entries.jsonl (ledger entries)
  - events.jsonl (journal events)
  - verify.json
  - witness.jsonl
  - plans.json
  - goal_frontmatter.md
  - config.json

Usage:
    python3 contract/validate.py <file>
    python3 contract/validate.py contract/golden/entries.jsonl
    python3 contract/validate.py .sdlc/ledger/entries/*.jsonl
"""
import json
import pathlib
import re
import sys

#: The two record streams. Mirrors ledger.py's own ENTRIES/EVENTS names.
ENTRIES, EVENTS = "entries", "events"


def load_vocabulary(vocab_path=None):
    """Load the vocabulary.json file."""
    if vocab_path is None:
        vocab_path = pathlib.Path(__file__).parent / "vocabulary.json"

    if not vocab_path.exists():
        raise FileNotFoundError(f"vocabulary.json not found at {vocab_path}")

    vocab = json.loads(vocab_path.read_text())

    # Add witness vocabulary (witness has its own kind vocabulary separate from entries/events)
    if "witness_kinds" not in vocab:
        vocab["witness_kinds"] = ["mutation", "assertion"]

    return vocab


def validate_entries_line(line_dict, vocab):
    """Validate a single entry line."""
    errors = []

    # Check required fields for entries (uses "ts" not "timestamp")
    required_fields = {"id", "actor", "ts", "kind"}
    missing = required_fields - set(line_dict.keys())
    if missing:
        errors.append(f"missing required fields: {missing}")

    # Entry schemas are deliberately additive: historical kinds without a schema retain the
    # permissive compatibility contract, while `merged` has a stable identity for replay safety.
    schema = vocab.get("entry_schemas", {}).get(line_dict.get("kind"), {})
    required = set(schema.get("required", ()))
    missing_schema_fields = required - set(line_dict)
    if missing_schema_fields:
        errors.append(f"missing required fields: {missing_schema_fields}")
    for field, rule in schema.get("fields", {}).items():
        value = line_dict.get(field)
        if value is None:
            continue  # the required-field error above is the useful failure for absent values
        if rule.get("type") == "string" and not isinstance(value, str):
            errors.append(rule.get("error", f"{field} must be a string"))
            continue
        pattern = rule.get("pattern")
        if pattern is not None and (not isinstance(value, str) or not re.fullmatch(pattern, value)):
            errors.append(rule.get("error", f"{field} does not match its required pattern"))

    return errors


def validate_events_line(line_dict, vocab):
    """Validate a single event line."""
    errors = []

    kind = line_dict["kind"]

    # Common metadata fields present in all records
    common_fields = {"id", "ts", "actor", "goal"}
    missing_common = common_fields - set(line_dict)
    if missing_common:
        errors.append(f"missing required fields: {missing_common}")

    # Check that only whitelisted fields are present for this kind
    allowed_fields = set(vocab["event_fields"].get(kind, [])) | common_fields
    actual_fields = set(line_dict.keys()) - {"kind"}
    unknown = actual_fields - allowed_fields
    if unknown:
        errors.append(f"unknown fields for kind '{kind}': {unknown}")

    if kind == "merge_observed":
        if not isinstance(line_dict.get("observation_key"), str) or not re.fullmatch(r"[0-9a-f]{64}", line_dict["observation_key"]):
            errors.append("observation_key must be 64 lowercase hexadecimal characters")
        if line_dict.get("subject_kind") not in ("goal", "branch"):
            errors.append("subject_kind must be goal or branch")
        if not isinstance(line_dict.get("subject"), str) or not 1 <= len(line_dict["subject"]) <= 256:
            errors.append("subject must be a non-empty bounded string")
        if not isinstance(line_dict.get("pr"), int) or line_dict["pr"] <= 0:
            errors.append("pr must be positive")
        if not isinstance(line_dict.get("merge_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", line_dict["merge_sha"]):
            errors.append("merge_sha must be a 40 or 64 lowercase hexadecimal SHA")
    elif kind == "review_posted":
        for field in ("observation_key", "brief_hash"):
            if not isinstance(line_dict.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", line_dict[field]):
                errors.append(f"{field} must be 64 lowercase hexadecimal characters")
        if line_dict.get("verdict") not in ("approve", "block", "unblock"):
            errors.append("verdict must be approve, block, or unblock")
        if not isinstance(line_dict.get("evidence_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", line_dict["evidence_id"]):
            errors.append("evidence_id must be a 16-128 character URL-safe identifier")
        if not isinstance(line_dict.get("comment_id"), int) or line_dict["comment_id"] <= 0:
            errors.append("comment_id must be positive")
        if not isinstance(line_dict.get("pr"), int) or line_dict["pr"] <= 0:
            errors.append("pr must be positive")
        if not isinstance(line_dict.get("head_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", line_dict["head_sha"]):
            errors.append("head_sha must be a 40 or 64 lowercase hexadecimal SHA")
    elif kind == "ci_observed":
        checks, total, truncated = line_dict.get("checks"), line_dict.get("checks_total"), line_dict.get("checks_truncated")
        if not isinstance(checks, list) or len(checks) > 50:
            errors.append("checks must be a list of at most 50 items")
        if not isinstance(total, int) or not 0 <= total <= 100000 or not isinstance(truncated, bool):
            errors.append("invalid checks_total or checks_truncated")
        elif total < len(checks) or truncated != (total > 50):
            errors.append("inconsistent check truncation")
        if not isinstance(line_dict.get("observation_key"), str) or not re.fullmatch(r"[0-9a-f]{64}", line_dict["observation_key"]):
            errors.append("observation_key must be 64 lowercase hexadecimal characters")
        if not isinstance(line_dict.get("pr"), int) or line_dict["pr"] <= 0:
            errors.append("pr must be positive")
        if not isinstance(line_dict.get("head_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", line_dict["head_sha"]):
            errors.append("head_sha must be a 40 or 64 lowercase hexadecimal SHA")
        if line_dict.get("gate_verdict") not in ("pass", "warn", "block"):
            errors.append("gate_verdict must be pass, warn or block")
        if isinstance(checks, list):
            for item in checks:
                if not (isinstance(item, dict) and set(item) == {"name", "conclusion"}
                        and isinstance(item["name"], str) and 1 <= len(item["name"]) <= 256
                        and item["conclusion"] in ("pass", "fail", "pending")):
                    errors.append("checks must contain name/conclusion observations")
                    break

    return errors


def stream_of(file_path):
    """The stream a path belongs to, or None when the path does not name one.

    Real ledger files are `<stream>/<actor>-<host>.<pid>.jsonl`, so the PARENT DIRECTORY carries
    the stream; the golden files are `golden/<stream>.jsonl`, so the stem carries it there. Both
    are derived from the path the DOCUMENTED gesture already passes
    (`python3 contract/validate.py contract/golden/entries.jsonl`) -- no new flag, so the
    invocation the README prescribes is the one that can now fail. Anything else (a `.json`
    config, an ad-hoc dump) names no stream and keeps the kind-only behaviour.
    """
    if file_path is None:
        return None
    path = pathlib.Path(file_path)
    for name in (path.parent.name, path.stem):
        if name in (ENTRIES, EVENTS):
            return name
    return None


def validate_line(line_dict, vocab, file_path=None):
    """Validate a single line, determining its type and validating accordingly."""
    errors = []

    # Check kind field exists
    if "kind" not in line_dict:
        errors.append("missing 'kind' field")
        return errors

    kind = line_dict["kind"]

    # Determine if this is an entry, event, or witness based on kind -- and, when the path names
    # a stream, that the kind actually belongs to THAT stream. Dispatching on `kind` alone let a
    # well-formed `merged` entry validate inside events.jsonl, so the per-stream kind membership
    # the README documents ("the entries stream carries 10 record kinds") was never enforced.
    stream = stream_of(file_path)
    if kind in vocab["entries_kinds"]:
        if stream == EVENTS:
            errors.append(f"'{kind}' is an entries kind and cannot appear in the events stream")
            return errors
        errors.extend(validate_entries_line(line_dict, vocab))
    elif kind in vocab["event_kinds"]:
        if stream == ENTRIES:
            errors.append(f"'{kind}' is an events kind and cannot appear in the entries stream")
            return errors
        errors.extend(validate_events_line(line_dict, vocab))
    elif kind in vocab.get("witness_kinds", []):
        # Witness records have their own kind vocabulary and are valid
        pass
    else:
        # Unknown kind - this is an error
        errors.append(f"unknown kind: '{kind}'")

    return errors


def validate_file(file_path, vocab):
    """Validate a single file against the vocabulary."""
    file_path = pathlib.Path(file_path)

    if not file_path.exists():
        return [f"file not found: {file_path}"]

    errors = []

    try:
        if file_path.suffix == ".jsonl":
            # JSONL format - one JSON object per line
            with open(file_path, "r") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as e:
                        errors.append(f"line {line_num}: invalid JSON: {e}")
                        continue

                    line_errors = validate_line(obj, vocab, file_path)
                    if line_errors:
                        errors.append(f"line {line_num}: {'; '.join(line_errors)}")

        elif file_path.suffix == ".md" and file_path.parent.name == "acceptance":
            # Use the same bounded parser as the producer/PR gate, never a looser golden check.
            import importlib.util
            script = pathlib.Path(__file__).resolve().parents[1] / "skills/agrim-loop/scripts/acceptance.py"
            spec = importlib.util.spec_from_file_location("acceptance", script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.read(file_path.parent.parent, file_path.stem)

        elif file_path.suffix == ".json":
            # JSON format - single object
            try:
                obj = json.loads(file_path.read_text())
            except json.JSONDecodeError as e:
                errors.append(f"invalid JSON: {e}")
                return errors

            # For .json files, just check minimal structure
            # (verify.json, plans.json, config.json, etc.)
            if "kind" in obj:
                line_errors = validate_line(obj, vocab, file_path)
                if line_errors:
                    errors.append(f"root: {'; '.join(line_errors)}")
            # Other .json files are valid as long as they're valid JSON

    except Exception as e:
        errors.append(f"error reading file: {e}")

    return errors


def main():
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h"):
        print("Usage: python3 contract/validate.py <file>")
        return
    if len(sys.argv) < 2:
        print("Usage: python3 contract/validate.py <file>", file=sys.stderr)
        sys.exit(2)

    vocab_path = pathlib.Path(__file__).parent / "vocabulary.json"

    try:
        vocab = load_vocabulary(vocab_path)
    except Exception as e:
        print(f"Error loading vocabulary: {e}", file=sys.stderr)
        sys.exit(2)

    # Validate each file
    total_errors = []
    for file_arg in sys.argv[1:]:
        errors = validate_file(file_arg, vocab)
        if errors:
            total_errors.extend([f"{file_arg}: {e}" for e in errors])

    if total_errors:
        for error in total_errors:
            print(error, file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
