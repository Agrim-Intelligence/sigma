#!/usr/bin/env python3
"""Tests for contract vocabulary generation from ledger.py."""
import json
import pathlib
import subprocess
import sys

import pytest


def test_vocabulary_generated_from_ledger():
    """Test that the vocabulary generator produces output matching ledger.py constants.

    This test MUST be run before the generator is implemented to verify the
    red control fires. After implementation, it verifies no drift between
    ledger.py and the generated vocabulary.json.
    """
    repo_root = pathlib.Path(__file__).parent.parent
    generator = repo_root / "tools" / "generate_vocabulary.py"

    # Run the generator
    result = subprocess.run(
        [sys.executable, str(generator)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )

    assert result.returncode == 0, f"Generator failed: {result.stderr}"

    # Parse the output
    vocab = json.loads(result.stdout)

    # Import ledger to get the real constants
    sys.path.insert(0, str(repo_root / "skills" / "sigma-loop" / "scripts"))
    import ledger

    # Verify all KINDS are present (including "merge-armed")
    assert set(vocab["entries_kinds"]) == set(ledger.KINDS), \
        f"entries_kinds mismatch: {set(vocab['entries_kinds']) ^ set(ledger.KINDS)}"
    assert "merge-armed" in vocab["entries_kinds"], \
        "entries_kinds must include 'merge-armed'"

    # Verify all EVENT_KINDS are present
    assert set(vocab["event_kinds"]) == set(ledger.EVENT_KINDS), \
        f"event_kinds mismatch: {set(vocab['event_kinds']) ^ set(ledger.EVENT_KINDS)}"

    # Verify EVENT_FIELDS match (convert tuples to lists for JSON comparison)
    expected_event_fields = {
        kind: list(fields) if isinstance(fields, tuple) else fields
        for kind, fields in ledger.EVENT_FIELDS.items()
    }
    assert vocab["event_fields"] == expected_event_fields, \
        f"event_fields mismatch"
    assert vocab["entry_schemas"] == ledger.ENTRY_SCHEMAS

    # Verify other constants
    assert set(vocab["phase_kinds"]) == set(ledger.PHASE_KINDS)
    assert set(vocab["gate_kinds"]) == set(ledger.GATE_KINDS)
    assert set(vocab["verdicts"]) == set(ledger.VERDICTS)
    assert set(vocab["reason_classes"]) == set(ledger.REASON_CLASSES)
    assert set(vocab["retro_grades"]) == set(ledger.RETRO_GRADES)

    # Verify contract_version is extracted correctly
    version_file = repo_root / "contract" / "VERSION"
    if version_file.exists():
        version_str = version_file.read_text().strip()
        parts = version_str.split(".")
        expected_contract_version = f"{parts[0]}.{parts[1]}"
        assert vocab["contract_version"] == expected_contract_version


def test_vocabulary_generator_is_deterministic():
    """Test that running the generator twice produces identical byte-for-byte output.

    This verifies that json.dumps with sort_keys=True produces stable output.
    """
    repo_root = pathlib.Path(__file__).parent.parent
    generator = repo_root / "tools" / "generate_vocabulary.py"

    # Run the generator twice
    result1 = subprocess.run(
        [sys.executable, str(generator)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )
    result2 = subprocess.run(
        [sys.executable, str(generator)],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
    )

    assert result1.returncode == 0
    assert result2.returncode == 0

    # Output must be byte-identical
    assert result1.stdout == result2.stdout, \
        "Generator output is not deterministic (sort_keys may not be working)"


def test_on_disk_vocabulary_matches_generator():
    """The committed vocabulary files are exactly what the generator prints (#2623).

    The tests above compare the generator's stdout to ledger.py, never to the files a consumer
    actually reads, so a hand-edit, a VERSION bump without regenerating, or a partial vendor run
    all passed. The file is read as bytes BEFORE anything runs. A downstream reader's vendored copy
    of this directory is that reader's to pin (#2580 moved its half of this check to the private
    side's own glue tests, where the copy lives); this test pins the core's own file.

    Line endings are a contract, not a platform accident: the generator writes LF bytes whatever
    the platform's stdout mode, and each directory's .gitattributes pins vocabulary.json to LF on
    checkout. A CRLF file on disk is drift.
    """
    repo_root = pathlib.Path(__file__).parent.parent
    paths = [
        repo_root / "contract" / "vocabulary.json",
    ]
    for path in paths:
        assert path.is_file(), f"{path.relative_to(repo_root)} is missing"
        attributes = path.parent / ".gitattributes"
        directives = [
            line.split() for line in (attributes.read_text() if attributes.is_file() else "").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert ["vocabulary.json", "text", "eol=lf"] in directives, (
            f"{attributes.relative_to(repo_root)} must pin vocabulary.json to LF"
        )
    on_disk = {path: path.read_bytes() for path in paths}

    result = subprocess.run(
        [sys.executable, str(repo_root / "tools" / "generate_vocabulary.py")],
        capture_output=True,
        cwd=str(repo_root),
    )
    assert result.returncode == 0, f"Generator failed: {result.stderr.decode(errors='replace')}"
    generated = result.stdout
    assert generated, "generator printed nothing"
    entries_kinds = json.loads(generated)["entries_kinds"]
    assert isinstance(entries_kinds, list) and entries_kinds, "generator printed no entries_kinds list"

    for path, content in on_disk.items():
        assert content == generated, (
            f"{path.relative_to(repo_root)} differs from the generator's output; regenerate with "
            "`python3 tools/generate_vocabulary.py > contract/vocabulary.json`"
        )


def _load_by_path(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_needs_only_core_sources(tmp_path):
    """The generator reads only the core (#2584, S1-G11): copied with `skills/`, `contract/` and
    itself -- exactly what the public snapshot holds -- it prints the committed file's bytes."""
    import shutil
    repo_root = pathlib.Path(__file__).parent.parent
    for d in ("skills", "contract"):
        shutil.copytree(repo_root / d, tmp_path / d,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (tmp_path / "tools").mkdir()
    shutil.copy2(repo_root / "tools" / "generate_vocabulary.py", tmp_path / "tools")
    result = subprocess.run([sys.executable, str(tmp_path / "tools" / "generate_vocabulary.py")],
                            capture_output=True, cwd=str(tmp_path))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout == (repo_root / "contract" / "vocabulary.json").read_bytes()


def test_severity_order_is_the_pipelines_own():
    """`severity_order` is generated from the report card's own `_ORDER` -- the one source."""
    repo_root = pathlib.Path(__file__).parent.parent
    pipeline = _load_by_path("_pipeline_2584", repo_root / "skills" / "sigma-loop" / "scripts" / "pipeline.py")
    result = subprocess.run([sys.executable, str(repo_root / "tools" / "generate_vocabulary.py")],
                            capture_output=True, text=True, cwd=str(repo_root))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["severity_order"] == dict(pipeline._ORDER)


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
