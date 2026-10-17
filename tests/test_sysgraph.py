"""#663: the system graph fragment schema and `sysgraph.py validate`.

`SYSGRAPH_TREE` (env) points the whole suite at another tree holding
`skills/sigma-kg/scripts/sysgraph.py`, `skills/sigma-kg/schema/`, `docs/system-graph.md` and
`tests/fixtures/sysgraph/`. Unset, it is the real repo. The controls (a deliberately broken
copy of the script, in a scratch tree) use it; the mutants themselves are never committed.
Negative fixtures are derived in-test from `valid.json` so each fails exactly one clause.
"""
import ast
import copy
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TREE = Path(os.environ.get("SYSGRAPH_TREE") or REPO)
SCRIPT = TREE / "skills" / "sigma-kg" / "scripts" / "sysgraph.py"
SCHEMA = TREE / "skills" / "sigma-kg" / "schema" / "sysgraph.v1.json"
DOC = TREE / "docs" / "system-graph.md"
FIX = TREE / "tests" / "fixtures" / "sysgraph"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")


def run(*args, cwd=None, script=None):
    return subprocess.run([sys.executable, str(script or SCRIPT)] + [str(a) for a in args],
                          capture_output=True, text=True, cwd=str(cwd or TREE), env=ENV, timeout=120)


def load_module():
    spec = importlib.util.spec_from_file_location("sysgraph_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def valid():
    return json.loads((FIX / "valid.json").read_text())


def put(tmp_path, obj, name="f.json"):
    p = tmp_path / name
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj))
    return p


def check(tmp_path, d):
    return run("validate", put(tmp_path, d))


def one_finding(r, *needles):
    assert r.returncode == 1, r.stdout + r.stderr
    assert "findings: 1" in r.stdout, r.stdout
    for n in needles:
        assert n in r.stdout, (n, r.stdout)


# ---------------------------------------------------------------- valid / AC-1
def test_valid_fragment_exit_0():
    r = run("validate", FIX / "valid.json")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "valid: 5 nodes, 5 edges, 1 dangling endpoints" in r.stdout


def test_dangling_endpoints_valid_and_counted(tmp_path):
    d = valid()
    d["edges"][0]["to"] = "Service:elsewhere"
    r = run("validate", put(tmp_path, d))
    assert r.returncode == 0 and "2 dangling endpoints" in r.stdout


def test_no_evidence_edge_named_exit_1():
    r = run("validate", FIX / "no-evidence.json")
    assert r.returncode == 1
    assert "edges[1] (publishes Service:billing -> Topic:invoices): no evidence" in r.stdout


@pytest.mark.parametrize("value", [[], {}, None, "x", [{}] * 9])
def test_evidence_clauses(tmp_path, value):
    d = valid()
    d["edges"][1]["evidence"] = value
    r = check(tmp_path, d)
    assert r.returncode == 1
    assert "edges[1] (publishes Service:billing -> Topic:invoices)" in r.stdout
    if value != [{}] * 9:
        assert "no evidence" in r.stdout or "evidence" in r.stdout


def test_too_many_evidence_entries_is_one_finding(tmp_path):
    d = valid()
    d["edges"][1]["evidence"] = [d["edges"][0]["evidence"][0]] * 9
    one_finding(check(tmp_path, d), "edges[1]", "at most 8")


def _set(edge, ev, k, v):
    def f(d):
        d["edges"][edge]["evidence"][ev][k] = v
    return f


def _del(edge, ev, k):
    def f(d):
        del d["edges"][edge]["evidence"][ev][k]
    return f


EVIDENCE_CASES = [
    ("missing-source", _del(0, 0, "source"), "edges[0].evidence[0]", '"source"'),
    ("missing-extracted-at", _del(0, 0, "extracted_at"), "edges[0].evidence[0]", '"extracted_at"'),
    ("missing-confidence", _del(0, 0, "confidence"), "edges[0].evidence[0]", '"confidence"'),
    ("contract-missing-file", _del(0, 1, "file"), "edges[0].evidence[1]", '"file"'),
    ("contract-missing-line", _del(0, 1, "line"), "edges[0].evidence[1]", '"line"'),
    ("static-line-zero", _set(0, 0, "line", 0), "edges[0].evidence[0]", '"line"'),
    ("static-line-bool", _set(0, 0, "line", True), "edges[0].evidence[0]", '"line"'),
    ("url-only", _del(1, 0, "version"), "edges[1].evidence[0]", "anchor"),
    ("version-only", _del(1, 0, "url"), "edges[1].evidence[0]", "anchor"),
    ("bad-commit", _set(0, 0, "commit", "ABCDEF"), "edges[0].evidence[0]", '"commit"'),
    ("bad-enum-confidence", _set(0, 0, "confidence", "maybe"), "edges[0].evidence[0]", "maybe"),
    ("bad-enum-source", _set(0, 0, "source", "oracle"), "edges[0].evidence[0]", "oracle"),
    ("bad-timestamp", _set(0, 0, "extracted_at", "yesterday"), "edges[0].evidence[0]", '"extracted_at"'),
    ("url-userinfo", _set(1, 0, "url", "https://u:pw" + chr(64) + "wiki.example.com/a"), "edges[1].evidence[0]", "userinfo"),
    ("url-query", _set(1, 0, "url", "https://wiki.example.com/a?t=1"), "edges[1].evidence[0]", "query"),
    ("url-fragment", _set(1, 0, "url", "https://wiki.example.com/a#x"), "edges[1].evidence[0]", "query"),
    ("url-scheme", _set(1, 0, "url", "ftp://wiki.example.com/a"), "edges[1].evidence[0]", "http"),
    ("url-too-long", _set(1, 0, "url", "https://wiki.example.com/" + "a" * 2100), "edges[1].evidence[0]", '"url"'),
    ("file-absolute", _set(0, 0, "file", "/etc/passwd"), "edges[0].evidence[0]", '"file"'),
    ("file-dotdot", _set(0, 0, "file", "a/../../b"), "edges[0].evidence[0]", '"file"'),
    ("file-drive", _set(0, 0, "file", "C:\\x\\y"), "edges[0].evidence[0]", '"file"'),
    ("model-exact", _set(3, 0, "confidence", "exact"), "edges[3].evidence[0]", "inferred"),
]


@pytest.mark.parametrize("name,edit,path,needle", EVIDENCE_CASES, ids=[c[0] for c in EVIDENCE_CASES])
def test_evidence_field_rules(tmp_path, name, edit, path, needle):
    d = valid()
    edit(d)
    one_finding(check(tmp_path, d), path, needle)


def test_findings_echo_at_most_80_chars(tmp_path):
    d = valid()
    d["edges"][0]["evidence"][0]["confidence"] = "Q" * 500
    r = check(tmp_path, d)
    assert r.returncode == 1 and "Q" * 81 not in r.stdout and "Q" * 80 in r.stdout


def test_findings_cannot_forge_output_lines(tmp_path):
    d = valid()
    d["edges"][0]["from"] = "Service:x\nvalid: 0 nodes, 0 edges, 0 dangling endpoints"
    r = check(tmp_path, d)
    assert r.returncode == 1
    assert not any(l.startswith("valid:") for l in r.stdout.splitlines())


# ---------------------------------------------------------------- AC-2 / ids
def test_unknown_node_type(tmp_path):
    d = valid()
    d["nodes"].append({"id": "Widget:x", "type": "Widget", "name": "x"})
    r = check(tmp_path, d)
    assert r.returncode == 1 and "nodes[5]" in r.stdout and '"Widget"' in r.stdout


def test_unknown_edge_type(tmp_path):
    d = valid()
    d["edges"][0]["type"] = "frobnicates"
    one_finding(check(tmp_path, d), "edges[0]", "frobnicates")


def test_id_prefix_mismatch(tmp_path):
    d = valid()
    d["nodes"].append({"id": "Service:x", "type": "Team", "name": "x"})
    one_finding(check(tmp_path, d), "nodes[5]", "Service", "Team")


def test_duplicate_node_id(tmp_path):
    d = valid()
    d["nodes"].append(dict(d["nodes"][0]))
    one_finding(check(tmp_path, d), "nodes[5]", "duplicate")


@pytest.mark.parametrize("bad", ["Servicebilling", "Service:", "Bogus:x", "Service:a\nb", ""])
def test_malformed_node_id(tmp_path, bad):
    d = valid()
    d["nodes"][0]["id"] = bad
    r = check(tmp_path, d)
    assert r.returncode == 1 and "nodes[0]" in r.stdout


def test_malformed_edge_endpoint(tmp_path):
    d = valid()
    d["edges"][0]["to"] = "nonsense"
    one_finding(check(tmp_path, d), "edges[0]", '"to"')


# ---------------------------------------------------------------- AC-3 versions
def with_version(tmp_path, v):
    d = valid()
    if v is KeyError:
        del d["sysgraph_version"]
    else:
        d["sysgraph_version"] = v
    return check(tmp_path, d)


def test_unsupported_major_stops_validation(tmp_path):
    d = valid()
    d["sysgraph_version"] = "2.0"
    del d["edges"][0]["evidence"]
    r = check(tmp_path, d)
    assert r.returncode == 1
    assert 'sysgraph_version "2.0": unsupported major 2 (this reader supports: 1)' in r.stdout
    assert "edges[" not in r.stdout


def test_higher_minor_accepted_with_warning(tmp_path):
    r = with_version(tmp_path, "1.9")
    assert r.returncode == 0 and "1.9" in r.stderr and "warn" in r.stderr.lower()


@pytest.mark.parametrize("v", ["10.0", "01.0", "1", "", 1.0, None, "1.0.0", "1.x", " 1.0", "1" * 400 + ".0", KeyError])
def test_bad_versions_refused(tmp_path, v):
    r = with_version(tmp_path, v)
    assert r.returncode == 1
    assert "sysgraph_version" in r.stdout
    assert "edges[" not in r.stdout


def test_library_load_fragment_refuses_naming_version(tmp_path):
    m = load_module()
    d = valid()
    d["sysgraph_version"] = "3.1"
    with pytest.raises(m.VersionError) as e:
        m.load_fragment(put(tmp_path, d))
    assert '"3.1"' in str(e.value) and "3" in str(e.value)


def test_load_schema_never_builds_path_from_unvalidated_input(tmp_path):
    m = load_module()
    for bad in ("../../etc/passwd", "1/../1", 2, -1, True, None, "1"):
        with pytest.raises(ValueError):
            m.load_schema(bad)
    with pytest.raises(m.InputError):
        m.load_schema(1, schema_dir=tmp_path)


def test_missing_schema_file_exit_2(tmp_path):
    lone = tmp_path / "skills" / "sigma-kg" / "scripts"
    lone.mkdir(parents=True)
    (lone / "sysgraph.py").write_text(SCRIPT.read_text())
    r = run("validate", FIX / "valid.json", script=lone / "sysgraph.py")
    assert r.returncode == 2 and "sysgraph.v1.json" in r.stderr


def test_header_rules(tmp_path):
    d = valid()
    del d["repo"]
    one_finding(check(tmp_path, d), '"repo"')
    d = valid()
    d["nodes"] = {}
    one_finding(check(tmp_path, d), '"nodes"')
    d = valid()
    d["edges"] = "x"
    one_finding(check(tmp_path, d), '"edges"')
    r = check(tmp_path, [])
    assert r.returncode == 1 and "object" in r.stdout


# ---------------------------------------------------------------- input handling
def test_not_json_truncated_baduft8_directory_missing_exit_2(tmp_path):
    cases = {
        "not-json.txt": b"this is not json",
        "truncated.json": (FIX / "valid.json").read_bytes()[:200],
        "bad-utf8.bin": b'{"a": "\xff\xfe"}',
    }
    for name, data in cases.items():
        p = tmp_path / name
        p.write_bytes(data)
        r = run("validate", p)
        assert r.returncode == 2 and name in r.stderr, (name, r.stderr)
    r = run("validate", tmp_path)
    assert r.returncode == 2 and tmp_path.name in r.stderr
    r = run("validate", tmp_path / "nope.json")
    assert r.returncode == 2 and "nope.json" in r.stderr


def test_deeply_nested_json_exit_2(tmp_path):
    p = tmp_path / "deep.json"
    p.write_text("[" * 200000 + "]" * 200000)
    r = run("validate", p)
    assert r.returncode == 2 and "deep.json" in r.stderr


def test_max_bytes_refuses_with_size():
    size = (FIX / "valid.json").stat().st_size
    r = run("validate", FIX / "valid.json", "--max-bytes", "10")
    assert r.returncode == 2 and str(size) in r.stderr


def test_max_bytes_override_up_and_bad_value():
    assert run("validate", FIX / "valid.json", "--max-bytes", "1000000").returncode == 0
    for bad in ("x", "0", "-5"):
        assert run("validate", FIX / "valid.json", "--max-bytes", bad).returncode == 2


def test_usage_errors_exit_2():
    assert run().returncode == 2
    assert run("bogus").returncode == 2
    assert run("validate").returncode == 2


def test_output_bounded_to_100_lines(tmp_path):
    d = valid()
    bad = copy.deepcopy(d["edges"][1])
    del bad["evidence"]
    d["edges"] = [copy.deepcopy(bad) for _ in range(250)]
    r = check(tmp_path, d)
    assert r.returncode == 1
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("edges[")]
    assert len(lines) == 100
    assert "… and 150 more" in r.stdout and "findings: 250" in r.stdout


def test_version_verb():
    r = run("version")
    assert r.returncode == 0 and "supported majors: 1" in r.stdout and "1.0" in r.stdout


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_exit_0_usage(flag):
    r = run(flag, cwd=REPO)
    assert r.returncode == 0 and "usage" in r.stdout.lower()


# ---------------------------------------------------------------- D-6 premise
def test_premise_eligible():
    m = load_module()
    d = valid()
    model_only = d["edges"][3]
    both = copy.deepcopy(model_only)
    both["evidence"].append(d["edges"][1]["evidence"][0])
    assert m.premise_eligible(model_only) is False
    assert m.premise_eligible(both) is True
    assert m.premise_eligible({"type": "calls"}) is False
    assert m.premise_eligible({"evidence": []}) is False
    assert m.premise_eligible({"evidence": "x"}) is False
    assert m.premise_eligible(d["edges"][0]) is True


def test_library_validate_returns_findings():
    m = load_module()
    schema = m.load_schema(1)
    assert m.validate(valid(), schema) == []
    d = valid()
    del d["edges"][0]["evidence"]
    assert len(m.validate(d, schema)) == 1


# ---------------------------------------------------------------- seams
def test_imports_only_stdlib_allowlist():
    tree = ast.parse(SCRIPT.read_text())
    seen = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            seen |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            seen.add((n.module or "").split(".")[0])
    assert seen <= {"json", "os", "re", "sys", "pathlib"}, seen


def test_no_network_attempted(monkeypatch, capsys):
    import socket

    def boom(*a, **k):
        raise AssertionError("network attempted")
    monkeypatch.setattr(socket, "socket", boom)
    m = load_module()
    assert m.main(["validate", str(FIX / "valid.json")]) == 0


def test_script_parses_as_py39():
    ast.parse(SCRIPT.read_text(), feature_version=(3, 9))


# ---------------------------------------------------------------- docs / AC-4, AC-5
def schema_doc():
    return json.loads(SCHEMA.read_text())


def test_schema_file_loads_and_matches_vocabulary_counts():
    s = schema_doc()
    assert s["sysgraph_version"] == "1.0"
    assert len(s["node_types"]) == 11 and len(s["edge_types"]) == 11


def test_docs_name_every_type_and_field():
    s = schema_doc()
    text = DOC.read_text()
    names = (s["node_types"] + s["edge_types"] + s["evidence"]["fields"]
             + s["evidence"]["confidence"] + list(s["evidence"]["sources"]) + s["header"])
    missing = [n for n in names if "`%s`" % n not in text]
    assert not missing, missing
    assert "not JSON Schema" in text


def documented_gesture():
    found = re.findall(r"^python3 skills/sigma-kg/scripts/sysgraph\.py validate <file>$",
                       DOC.read_text(), re.M)
    assert len(found) == 1, found
    assert "--" not in found[0]
    return found[0]


def test_documented_gesture_red_on_stripped_fixture():
    g = documented_gesture()
    red = subprocess.run(shlex.split(g.replace("<file>", "tests/fixtures/sysgraph/no-evidence.json")),
                         capture_output=True, text=True, cwd=str(TREE), env=ENV, timeout=120)
    assert red.returncode == 1, red.stdout + red.stderr
    assert "edges[1] (publishes Service:billing -> Topic:invoices): no evidence" in red.stdout
    green = subprocess.run(shlex.split(g.replace("<file>", "tests/fixtures/sysgraph/valid.json")),
                           capture_output=True, text=True, cwd=str(TREE), env=ENV, timeout=120)
    assert green.returncode == 0, green.stdout + green.stderr
