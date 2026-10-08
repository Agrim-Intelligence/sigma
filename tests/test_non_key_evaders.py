"""#453's non-key leak-gate ledger, exercised through the documented gesture."""
import base64
import importlib.util
import json
import pathlib

import pytest

from test_leak_scan import OWNER, _git, _run, _scratch, _write


ROOT = pathlib.Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tools" / "goal433_attack.py"
VALUE = "Zq81" + "Xw7Pk3Lm9Rt2"
HF = "hf_" + "A1b2C3d4" * 5
DOCKER_AUTH = base64.b64encode(("agent:" + VALUE).encode()).decode()

_spec = importlib.util.spec_from_file_location("goal433_attack", HARNESS)
_attack = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_attack)
# Tests intentionally consume the harness's one authoritative #453 ledger.
LEDGER = tuple(row[:5] for row in _attack._non_key_ledger(ROOT, OWNER))


def test_ledger_is_complete_and_stably_classified():
    assert len(LEDGER) == len({row[0] for row in LEDGER}) == 21
    assert sum(row[1] == "fixed-red" for row in LEDGER) == 12
    assert sum(row[1] == "documented-oos" for row in LEDGER) == 9


@pytest.mark.parametrize("case,kind,expected,rel,text", LEDGER, ids=[row[0] for row in LEDGER])
def test_non_key_ledger_uses_the_documented_gate(case, kind, expected, rel, text, tmp_path):
    repo = _scratch(tmp_path)
    _write(repo, rel, text + "\n")
    proc = _run(repo)
    if kind == "fixed-red":
        assert proc.returncode == 1, (case, proc.stdout, proc.stderr)
        line = 0 if expected == "secret-file" else 1
        assert f"{rel}:{line}: {expected}" in proc.stdout
    else:
        assert proc.returncode == 0, (case, proc.stdout, proc.stderr)


def test_oos_boundaries_are_explicit_in_the_gate_contract():
    contract = (ROOT / "tools" / "leak_scan.py").read_text(encoding="utf-8")
    for boundary in {row[2] for row in LEDGER if row[1] == "documented-oos"}:
        assert boundary in contract, boundary


def test_symlink_secret_name_is_red_without_following_target(tmp_path):
    repo = _scratch(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("clean", encoding="utf-8")
    (repo / "id_rsa").symlink_to(outside)
    _git(repo, "add", "-A")
    proc = _run(repo)
    assert proc.returncode == 1 and "id_rsa:0: secret-file" in proc.stdout


def test_docker_auth_does_not_turn_arbitrary_base64_into_a_credential(tmp_path):
    proc = _run(_scratch(tmp_path, json.dumps({"auths": {"registry.example": {"auth": base64.b64encode(b"plain text").decode()}}}), "config.json"))
    assert proc.returncode == 0, proc.stdout


def test_docker_auth_detects_valid_config_when_registry_and_auth_members_are_reordered(tmp_path):
    """Removing generic valid-JSON auth positions must miss this legitimate Docker credential."""
    text = json.dumps({"auths": {
        "first.registry": {"identitytoken": "ignored", "auth": DOCKER_AUTH},
        "second.registry": {"auth": DOCKER_AUTH, "email": "agent@example.invalid"},
    }}, indent=2)
    repo = _scratch(tmp_path)
    _write(repo, "config.json", text + "\n")
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout
    assert len([line for line in proc.stdout.splitlines()
                if line.startswith("config.json:") and line.endswith("config-credential")]) == 2


def test_malformed_docker_json_only_accepts_an_auth_member_nested_under_auths(tmp_path):
    valid = DOCKER_AUTH
    unrelated = '{"auths": {}, "other": {"auth": "%s"}' % valid
    nested = '{"auths": {"registry.example": {"auth": "%s"}' % valid
    unrelated_repo = _scratch(tmp_path / "unrelated", unrelated, "config.json")
    nested_repo = _scratch(tmp_path / "nested", nested, "config.json")
    assert _run(unrelated_repo).returncode == 0
    proc = _run(nested_repo)
    assert proc.returncode == 1, proc.stdout
    assert "config.json:2: config-credential" in proc.stdout


# Every #453 closure has a live scratch-copy mutation control.  A passing ledger alone is not
# evidence that its asserted branch is the branch that caught the plant.
def _mutate(repo, rel, old, new):
    path = repo / rel
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, "mutation seam must be unique: %r" % old
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize("case,rel,old,new", [
    ("G01", "tools/leak_scan.py", "id_(?:rsa|", "id_(?:"),
    ("G02", "tools/leak_scan.py", "pem|", ""),
    ("G04", "tools/leak_scan.py", "(?:\\.bak)?", ""),
    ("G05", "tools/leak_scan.py", "p8|", ""),
    ("G06", "tools/leak_scan.py", "|\\.git-credentials", ""),
    ("G13", "tools/leak_scan.py", "|(?<=-I)/(?:Users|home)/", ""),
    ("G14", "tools/leak_scan.py", "|(?<![\\w.~-])/var/home/", ""),
    ("G15", "tools/leak_scan.py", "|(?<![\\w.~-])\\\\{1,2}/(?:Users|home)\\\\{1,2}/", ""),
    ("G18", "tools/leak_scan.py", "                      re.I)", "                      0)"),
    ("H02", "skills/sigma-loop/scripts/scrub.py", '    ("huggingface-token", re.compile(r"(?<![\\w-])hf_[A-Za-z0-9]{20,}")),\n', ""),
    ("H03", "tools/leak_scan.py", 'return (low == "credentials" or base.startswith((".env", ".yarnrc"))',
     'return (base.startswith((".env", ".yarnrc"))'),
    ("H04", "tools/leak_scan.py", "def _docker_auth_positions(text):", "def _docker_auth_positions(text):\n    return []\n\n# disabled\ndef _unused_docker_auth_positions(text):"),
], ids=["G01", "G02", "G04", "G05", "G06", "G13", "G14", "G15", "G18", "H02", "H03", "H04"])
def test_each_non_key_closure_turns_clean_when_its_implementation_is_mutated(
        case, rel, old, new, tmp_path):
    """Break exactly the production branch protecting this ledger row, then run the real gesture."""
    _case, kind, _expected, planted_rel, text = next(row for row in LEDGER if row[0] == case)
    repo = _scratch(tmp_path, text + "\n", planted_rel)
    assert _run(repo).returncode == 1, case
    _mutate(repo, rel, old, new)
    proc = _run(repo)
    assert proc.returncode == 0, (case, proc.stdout, proc.stderr)
