"""#1059 -- matrix.py check: every non-pending (row, gate) pair needs real evidence at a real commit.
Fixtures are tiny git repositories; the documented gesture is run as a subprocess against one."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "regression" / "matrix.py"
spec = importlib.util.spec_from_file_location("matrix_under_test", SCRIPT)
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)

ROW = {"id": "r1/c", "regression": "r1", "form": "c", "mutation": "t.txt::x", "must_be_red_by": ["g", "p"]}
GATES = [{"id": "g", "argv": ["python3", "g.py"], "status": "live", "pending_reason": ""},
         {"id": "p", "argv": ["python3", "p.py"], "status": "pending", "pending_reason": "later"}]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def _red(sha, **over):
    e = {"row": "r1/c", "gate": "g", "status": "red", "run_id": "20260101T000000Z-abc123", "date": "2026-01-01",
         "commit": sha, "pin": matrix.pin(ROW, GATES[0])}
    return {**e, **over}


PENDING = {"row": "r1/c", "gate": "p", "status": "pending"}


@pytest.fixture
def repo(tmp_path):
    reg = tmp_path / "evals" / "regression"
    reg.mkdir(parents=True)
    (reg / "catalogue.json").write_text(json.dumps({"rows": [ROW]}))
    (reg / "gates.json").write_text(json.dumps({"gates": GATES}))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "x")
    return tmp_path


def _write(repo, entries):
    (repo / "evals" / "regression" / "matrix.json").write_text(json.dumps({"rows": entries}))


def _check(repo, *extra):
    r = subprocess.run([sys.executable, str(SCRIPT), "check", "--root", str(repo), *extra],
                       capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


def test_clean_matrix_passes(repo):
    _write(repo, [_red(_head(repo)), PENDING])
    assert _check(repo) == (0, "", "")


def test_missing_row_is_named(repo):
    _write(repo, [_red(_head(repo))])
    code, _, err = _check(repo)
    assert code == 1 and "missing-row: catalogue row r1/c / gate p" in err


def test_no_evidence_is_named(repo):
    for field in ("run_id", "date", "commit", "pin"):
        _write(repo, [_red(_head(repo), **{field: ""}), PENDING])
        code, _, err = _check(repo)
        assert code == 1 and "no-evidence: r1/c / g" in err, field


def test_commit_must_be_a_forty_hex_object_in_the_repository(repo):
    head = _head(repo)
    for bad, kind in (("abc123", "not 40 lowercase hex"), (head.upper(), "not 40 lowercase hex"),
                      (head + "\n", "not 40 lowercase hex"), ("0" * 40, "not a commit in this repository"),
                      (_git(repo, "rev-parse", "HEAD^{tree}"), "not a commit in this repository")):
        _write(repo, [_red(bad), PENDING])
        code, _, err = _check(repo)
        assert code == 1 and "bad-commit" in err and kind in err, bad


def test_row_must_have_existed_at_the_commit(repo):
    cat = repo / "evals" / "regression" / "catalogue.json"
    cat.write_text(json.dumps({"rows": [{**ROW, "id": "other/c"}]}))
    _git(repo, "commit", "-qam", "without the row")
    old = _head(repo)
    cat.write_text(json.dumps({"rows": [ROW]}))
    _git(repo, "commit", "-qam", "with the row")
    _write(repo, [_red(old), PENDING])
    code, _, err = _check(repo)
    assert code == 1 and f"row-absent-at-commit: r1/c / g did not exist at {old}" in err


def test_pending_rules(repo):
    head = _head(repo)
    _write(repo, [_red(head), {**PENDING, "commit": head}])
    assert "pending-with-evidence" in _check(repo)[2]
    _write(repo, [_red(head), {"row": "r1/c", "gate": "g", "status": "pending"}, PENDING])
    assert "stale-pending" in _check(repo)[2] and "duplicate-entry" in _check(repo)[2]
    _write(repo, [_red(head), _red(head, gate="p", pin=matrix.pin(ROW, GATES[1]))])
    assert "red-on-pending: r1/c / p" in _check(repo)[2]


def test_duplicate_unknown_and_stale_entries(repo):
    head = _head(repo)
    _write(repo, [_red(head), _red(head), PENDING])
    assert "duplicate-entry: r1/c / g" in _check(repo)[2]
    _write(repo, [_red(head), PENDING, _red(head, row="gone/c")])
    assert "unknown-pair: gone/c / g" in _check(repo)[2]
    _write(repo, [_red(head, pin="0" * 16), PENDING])
    assert "stale-evidence: r1/c / g" in _check(repo)[2]
    _write(repo, [_red(head, date="2026-13-45"), PENDING])
    assert "bad-date: r1/c / g" in _check(repo)[2]
    _write(repo, [_red(head, status="green"), PENDING])
    assert "bad-status: r1/c / g" in _check(repo)[2]


def test_empty_or_unreadable_inputs_exit_two(repo):
    assert _check(repo)[0] == 2  # no matrix.json
    (repo / "evals" / "regression" / "catalogue.json").write_text(json.dumps({"rows": []}))
    _write(repo, [])
    assert _check(repo)[0] == 2


def test_shallow_clone_is_a_loud_note_not_a_pass(repo, tmp_path_factory):
    head = _head(repo)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    _write(repo, [_red(head), PENDING])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "matrix")
    shallow = tmp_path_factory.mktemp("shallow") / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow)], check=True,
                   capture_output=True)
    shallow_head = _git(shallow, "rev-parse", "HEAD")
    assert shallow_head != head
    code, out, err = _check(shallow)
    assert code == 0 and "UNVERIFIED: shallow clone, 1 recorded commit(s)" in out
    # the format check still bites in a shallow clone
    _write(shallow, [_red("abc"), PENDING])
    assert _check(shallow)[0] == 1
    # and the opt-out says what it skipped
    _write(shallow, [_red(head), PENDING])
    code, out, _ = _check(shallow, "--skip-objects")
    assert code == 0 and "UNVERIFIED: --skip-objects" in out


def test_git_env_is_not_inherited(repo, monkeypatch, tmp_path):
    _write(repo, [_red(_head(repo)), PENDING])
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("GIT_DIR", str(other))
    assert _check(repo)[0] == 0


def test_documented_gesture_fake_commit_exits_one_real_exits_zero(repo):
    """The README's gesture, verbatim: `python3 evals/regression/matrix.py check --root <copy>`."""
    _write(repo, [_red(_head(repo)), PENDING])
    assert _check(repo)[0] == 0
    _write(repo, [_red("1" * 40), PENDING])
    assert _check(repo)[0] == 1


def test_committed_matrix_passes_on_this_tree():
    """Format, coverage, pins and statuses of the committed matrix. Object checks are skipped on purpose:
    CI checkouts are shallow and the recorded commit is an ancestor (or a squashed-away branch commit), so
    existence is proved by the fixture tests above and by running the check on a full clone."""
    r = subprocess.run([sys.executable, str(SCRIPT), "check", "--skip-objects"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
