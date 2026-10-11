"""#1057 -- the gate set and catalogue: the schema checker rejects each documented bad shape."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "regression" / "catalogue.py"


def _check(root):
    return subprocess.run([sys.executable, str(SCRIPT), "check", "--root", str(root)],
                          capture_output=True, text=True)


def _copy(tmp_path):
    d = tmp_path / "evals" / "regression"
    d.mkdir(parents=True)
    for n in ("gates.json", "catalogue.json", "mutators.py"):
        shutil.copy(ROOT / "evals" / "regression" / n, d / n)
    return tmp_path


def _edit(root, name, fn):
    p = root / "evals" / "regression" / name
    data = json.loads(p.read_text())
    fn(data)
    p.write_text(json.dumps(data))


def _bad(tmp_path, name, fn, needle):
    root = _copy(tmp_path / needle)
    _edit(root, name, fn)
    r = _check(root)
    assert r.returncode == 1 and needle in r.stderr, (r.returncode, r.stderr)


def test_real_files_pass():
    r = subprocess.run([sys.executable, str(SCRIPT), "check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_unknown_gate_id_rejected(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"][0]["must_be_red_by"].append("nope"), "unknown-gate")


def test_empty_must_be_red_by_rejected(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"][0].update(must_be_red_by=[]), "empty-must-be-red-by")


def test_only_pending_gate_rejected(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"][0].update(must_be_red_by=["layer1-control"]), "only-pending")


def test_unknown_mutation_rejected(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"][0].update(form="no-such-form"), "unknown-mutation")


def test_wrong_anchor_text_rejected(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"][0].update(mutation="x.py::y"), "anchor-mismatch")


def test_every_mutator_form_has_a_row(tmp_path):
    _bad(tmp_path, "catalogue.json", lambda d: d["rows"].pop(), "uncatalogued-mutation")


def test_pending_needs_reason_and_live_has_none(tmp_path):
    _bad(tmp_path, "gates.json", lambda d: d["gates"][-1].update(pending_reason=""), "pending-without-reason")


def test_duplicate_and_bad_status_rejected(tmp_path):
    _bad(tmp_path, "gates.json", lambda d: d["gates"][1].update(id="phase-context-budget"), "duplicate-gate")
    _bad(tmp_path, "gates.json", lambda d: d["gates"][0].update(status="maybe"), "bad-status")
    _bad(tmp_path, "gates.json", lambda d: d["gates"][0].update(argv=[]), "empty-argv")


def test_malformed_file_exits_2(tmp_path):
    root = _copy(tmp_path)
    (root / "evals" / "regression" / "gates.json").write_text("{")
    assert _check(root).returncode == 2


def test_test_first_not_listed():
    for n in ("gates.json", "catalogue.json"):
        text = (ROOT / "evals" / "regression" / n).read_text()
        assert "test_first" not in text and "phase_record" not in text


def test_live_gate_paths_exist():
    gates = json.loads((ROOT / "evals" / "regression" / "gates.json").read_text())["gates"]
    for g in gates:
        if g["status"] == "live":
            for tok in g["argv"]:
                if tok.endswith(".py"):
                    assert (ROOT / tok).exists(), (g["id"], tok)


def test_wrong_types_exit_2_not_a_traceback(tmp_path):
    for fn in (lambda d: d["rows"].append("x"), lambda d: d["rows"][0].update(must_be_red_by="a"),
               lambda d: d["rows"][0].update(must_be_red_by=[["x"]]),
               lambda d: d["rows"][0].update(must_be_red_in_live=3)):
        root = _copy(tmp_path / str(id(fn)))
        _edit(root, "catalogue.json", fn)
        r = _check(root)
        assert r.returncode == 2 and "Traceback" not in r.stderr, r.stderr
