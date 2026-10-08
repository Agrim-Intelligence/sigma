"""The #453 follow-up harness exercises #433 key-body and #453 non-key ledgers."""
import json
import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tools" / "goal433_attack.py"
OWNER = "Agrim-Intelligence"
PUBLIC_SLUG = OWNER.lower() + "/public-demo"
OTHER_PUBLIC_SLUG = OWNER.lower() + "/other-public"


def _attack_module():
    spec = importlib.util.spec_from_file_location("goal433_attack_under_test", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), text=True, capture_output=True, check=True)


def _repo(tmp_path):
    """Minimal checked-out scanner contract; the harness must supply the remaining scratch state."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "skills" / "sigma-loop" / "scripts").mkdir(parents=True)
    (repo / "skills" / "sigma-doctor" / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "tools" / "leak_scan.py", repo / "tools" / "leak_scan.py")
    shutil.copy2(ROOT / "skills" / "sigma-loop" / "scripts" / "scrub.py",
                 repo / "skills" / "sigma-loop" / "scripts" / "scrub.py")
    (repo / "tests").mkdir()
    for name in ("test_leak_scan.py", "test_key_body.py", "test_key_body_guards.py"):
        shutil.copy2(ROOT / "tests" / name, repo / "tests" / name)
    (repo / "skills" / "sigma-doctor" / "scripts" / "doctor.py").write_text(
        '_MARKETPLACE_REPO = %r\n' % PUBLIC_SLUG, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/" + OWNER.lower() + "/demo.git")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    return repo


def test_attack_harness_owns_the_147_key_body_and_21_non_key_ledgers(tmp_path):
    repo = _repo(tmp_path)
    out = ".sdlc/evidence/goal433-attack.json"
    proc = subprocess.run([sys.executable, str(HARNESS), "--repo", str(repo), "--origin-owner", OWNER,
                           "--allowed-public-slug", PUBLIC_SLUG, "--json-out", out],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    evidence = json.loads((repo / out).read_text(encoding="utf-8"))
    assert evidence["schema"] == "sigma.goal433-attack/v3"
    assert evidence["base_revision"] == _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert evidence["origin_owner"] == OWNER
    assert len(evidence["implementation_sha256"]) == 64
    assert evidence["totals"] == {"attempts": 147, "unclassified": 0}
    assert len(evidence["cases"]) == len({row["id"] for row in evidence["cases"]}) == 147
    assert {row["classification"] for row in evidence["cases"]} == {"fixed-red", "clean-control", "documented-oos"}
    assert {row["outcome"] for row in evidence["cases"]} <= {"red", "clean"}
    for row in evidence["cases"]:
        assert row["path"] and row["expected"] is not None
        assert isinstance(row["expected"], list)
        if row["classification"] == "fixed-red":
            assert row["expected"] and row["outcome"] == "red"
        else:
            assert row["expected"] == [] and row["outcome"] == "clean"
    non_key = {row["id"]: row for row in evidence["cases"] if row["id"].startswith(("G", "H"))}
    assert len(non_key) == 21
    assert non_key["G01"]["expected"] == ["keys/id_rsa: 0: secret-file"]
    assert non_key["H04"]["expected"] == ["config.json: 1: config-credential"]
    assert non_key["G09"]["boundary"] == "Cyrillic/confusable"
    assert non_key["G09"]["expected"] == []
    rendered = (repo / out).read_text(encoding="utf-8")
    assert "Zq81" not in rendered and "hf_" not in rendered


def test_attack_harness_owns_its_key_body_inventory_without_test_dependencies():
    source = HARNESS.read_text(encoding="utf-8")
    assert "test_key_body" not in source
    assert 'repo / "tests"' not in source
    attack = _attack_module()
    assert len(attack._key_body_inventory()) == 126


def test_attack_harness_refuses_a_public_slug_outside_the_origin_owner(tmp_path):
    repo = _repo(tmp_path)
    proc = subprocess.run([sys.executable, str(HARNESS), "--repo", str(repo), "--origin-owner", OWNER,
                           "--allowed-public-slug", "other/public", "--json-out", ".sdlc/evidence/x.json"],
                          text=True, capture_output=True)
    assert proc.returncode != 0
    assert "REFUSED [allowed-public-slug]" in proc.stderr


def test_attack_harness_wires_the_allowed_slug_into_its_scanner_fixture(tmp_path):
    repo = _repo(tmp_path)
    proc = subprocess.run([sys.executable, str(HARNESS), "--repo", str(repo), "--origin-owner", OWNER,
                           "--allowed-public-slug", OTHER_PUBLIC_SLUG, "--json-out", "x.json"],
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    assert ('"allowed_public_slug": "%s"' % OTHER_PUBLIC_SLUG) in (repo / "x.json").read_text(encoding="utf-8")


def test_corrected_owner_oos_rows_interpolate_the_requested_owner():
    """The documented OOS proof must exercise the real repository owner, not Acme-Co."""
    rows = {case: content for case, _kind, _boundary, _rel, content, _expected
            in _attack_module()._non_key_ledger(ROOT, OWNER)}
    assert rows["G17"] == "https://github.com%2FAgrim-Intelligence%2Finternal-ops"
    assert rows["G19"] == "Agrim-Intelligence/internal-ops"
    assert rows["G17b"] == rows["G17"]
    assert rows["G19b"] == rows["G19"]
    assert all("Acme-Co" not in rows[case] for case in ("G17", "G19", "G17b", "G19b"))


@pytest.mark.parametrize("disposition,bad_expected", [
    ("fixed-red", []),
    ("documented-oos", ["bad: 1: home-path"]),
])
def test_attack_harness_mutation_refuses_rows_with_contradictory_expectations(
        disposition, bad_expected, monkeypatch):
    """Each fixed-vs-OOS mutation must make the harness reject its own ledger."""
    attack = _attack_module()
    rows = list(attack._NON_KEY_LEDGER)
    index = next(i for i, row in enumerate(rows) if row[1] == disposition)
    case, _old, boundary, rel, content, _expected = rows[index]
    rows[index] = (case, disposition, boundary, rel, content, bad_expected)
    monkeypatch.setattr(attack, "_NON_KEY_LEDGER", tuple(rows))
    with pytest.raises(SystemExit, match=r"REFUSED \[non-key-ledger\]"):
        attack._non_key_ledger(ROOT, OWNER)
