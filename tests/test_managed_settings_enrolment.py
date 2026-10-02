"""#423: deleting `.sdlc/managed-settings.json` must not turn org policy off.

A checkout that has once held a valid policy file is ENROLLED. If the file later disappears the
reader says `enrolled-policy-missing` (a refusing status), never `not-adopted`. Hermetic: tmp_path
only, no network, no host hook. The deletion controls go through `read()`, `gated_check()` and the
two real gates (`work.effective_review_mode`, `work.effective_hard_plan_gate_locked`) plus the
journal switch, and the lever is run as the exact argv the docs give.

Each marker is proven load-bearing on its own: the state marker and the git-dir marker are removed
one at a time. The residual limit (deleting every marker) is pinned, not hidden."""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-loop" / "scripts"
CLI = SCRIPTS / "managed_settings.py"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ms = _load("managed_settings")
work = _load("work")
ledger = _load("ledger")

OK = {"version": 1, "status": "ok", "refreshed_at": "2999-01-01T00:00:00Z",
      "locked": {"work.require_review": "approval", "gates.hard_plan_gate": {"enabled": True}}}


def _checkout(tmp_path, git=True):
    root = tmp_path / "proj"
    root.mkdir()
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    sdlc = root / ".sdlc"
    sdlc.mkdir()
    return root, sdlc


def _put(sdlc, payload=OK):
    f = sdlc / ms.MANAGED_SETTINGS_FILENAME
    f.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return f


def _enrol(sdlc):
    ms.read(str(sdlc))                      # the first valid read is what enrols


def _git_marker(root):
    return [p for p in (root / ".git").glob("sigma-managed-enrolled-*.json")]


def test_never_enrolled_absent_file_is_not_adopted_and_allowed(tmp_path):
    root, sdlc = _checkout(tmp_path)
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED
    out = ms.gated_check(str(sdlc), {}, "work.require_review")
    assert out["status"] == ms.STATUS_NOT_ADOPTED and out["allowed"] is True


def test_deleting_the_file_after_enrolment_refuses(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    assert ms.read(str(sdlc))[0] == ms.STATUS_OK
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING
    out = ms.gated_check(str(sdlc), {}, "work.require_review")
    assert out["allowed"] is False and out["status"] == ms.STATUS_ENROLLED_POLICY_MISSING
    assert "unenroll" in out["reason"] and ms.MANAGED_SETTINGS_FILENAME in out["reason"]
    assert ms.STATUS_ENROLLED_POLICY_MISSING in ms.REFUSING_STATUSES


def test_declared_project_id_does_not_rescue_a_deleted_file(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    cfg = {ms.CONFIG_KEY: {"project_id": "p1"}}
    assert ms.gated_check(str(sdlc), cfg, "gates.hard_plan_gate")["allowed"] is False


def test_declared_project_id_alone_never_enrolled_is_unchanged(tmp_path):
    """A fresh clone of an enrolled repo has never held policy: not an outage."""
    root, sdlc = _checkout(tmp_path)
    cfg = {ms.CONFIG_KEY: {"project_id": "p1"}}
    assert ms.gated_check(str(sdlc), cfg, "gates.hard_plan_gate")["allowed"] is True


def test_git_marker_alone_keeps_the_refusal(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    (sdlc / "state" / "managed-enrolled.json").unlink()
    assert _git_marker(root)
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING


def test_state_marker_alone_keeps_the_refusal(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    for m in _git_marker(root):
        m.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING


def test_deleting_the_whole_sdlc_state_dir_and_the_file_still_refuses(tmp_path):
    import shutil
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    shutil.rmtree(sdlc / "state")
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING


def test_residual_deleting_every_marker_reads_not_adopted(tmp_path):
    """The documented limit: a writer of the checkout who removes the file AND every marker (or runs
    the lever) is not bound. Pinned so the docs' claim and the code cannot drift."""
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    (sdlc / "state" / "managed-enrolled.json").unlink()
    for m in _git_marker(root):
        m.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED


def test_restoring_the_file_clears_the_refusal(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING
    _put(sdlc)
    assert ms.read(str(sdlc))[0] == ms.STATUS_OK


@pytest.mark.parametrize("payload", [{"version": 1, "status": "access-revoked"},
                                     {"version": 1, "status": "locked-key-unverifiable"}])
def test_valid_non_ok_files_also_enrol(tmp_path, payload):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc, payload)
    _enrol(sdlc)
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING


@pytest.mark.parametrize("payload", ["{not json", "[]", {"version": 9, "status": "ok"},
                                     {"version": 1, "status": "bogus"}])
def test_garbage_does_not_enrol(tmp_path, payload):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc, payload)
    _enrol(sdlc)
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED


def test_a_valid_file_never_read_does_not_enrol(tmp_path):
    """The lazy-enrolment limit: delivery then deletion before any read leaves no memory."""
    root, sdlc = _checkout(tmp_path)
    _put(sdlc).unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED


def test_marker_is_written_once(tmp_path):
    root, sdlc = _checkout(tmp_path)
    _put(sdlc)
    _enrol(sdlc)
    m = sdlc / "state" / "managed-enrolled.json"
    first = m.stat().st_mtime_ns
    _enrol(sdlc)
    assert m.stat().st_mtime_ns == first


def test_no_git_still_enrols_through_the_state_marker(tmp_path):
    root, sdlc = _checkout(tmp_path, git=False)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING


def test_two_sdlc_dirs_under_one_git_dir_do_not_share_enrolment(tmp_path):
    root, sdlc = _checkout(tmp_path)
    _put(sdlc)
    _enrol(sdlc)
    other = root / "sub" / ".sdlc"
    other.mkdir(parents=True)
    assert ms.read(str(other))[0] == ms.STATUS_NOT_ADOPTED       # not under the git dir's parent
    assert ms.read(str(sdlc))[0] == ms.STATUS_OK


def test_a_linked_worktree_gets_its_own_git_dir_marker(tmp_path):
    root, sdlc = _checkout(tmp_path)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b.c", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", str(wt)], check=True,
                   capture_output=True)
    wsdlc = wt / ".sdlc"
    wsdlc.mkdir()
    f = _put(wsdlc)
    _enrol(wsdlc)
    f.unlink()
    (wsdlc / "state" / "managed-enrolled.json").unlink()
    assert list((root / ".git" / "worktrees").glob("*/sigma-managed-enrolled-*.json"))
    assert ms.read(str(wsdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED        # the main checkout never enrolled


def test_enrolment_never_raises_on_an_unwritable_location(tmp_path, monkeypatch):
    root, sdlc = _checkout(tmp_path)
    _put(sdlc)
    monkeypatch.setattr(ms.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    assert ms.read(str(sdlc))[0] == ms.STATUS_OK


# ------------------------------------------------------------------------------ the real gates

def test_the_merge_and_pr_gates_and_journal_refuse_after_deletion(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    mode, refusal = work.effective_review_mode(str(sdlc), {"work": {"require_review": "off"}})
    assert refusal and "unenroll" in refusal
    on, locked, refusal = work.effective_hard_plan_gate_locked(str(sdlc), {})
    assert refusal and "unenroll" in refusal
    assert ledger.journal_on(str(sdlc), {"journal": {"enabled": True}}) is False


def test_the_gates_still_proceed_for_a_never_enrolled_checkout(tmp_path):
    root, sdlc = _checkout(tmp_path)
    _mode, refusal = work.effective_review_mode(str(sdlc), {"work": {"require_review": "off"}})
    assert refusal is None
    assert work.effective_hard_plan_gate_locked(str(sdlc), {})[2] is None


# ------------------------------------------------------------------------------ the lever

def _cli(cwd, *args):
    return subprocess.run([sys.executable, str(CLI), *args], cwd=cwd, capture_output=True, text=True)


def test_the_documented_lever_unenroll_clears_the_refusal(tmp_path):
    """The gesture is the one the docs and the refusal give: `unenroll .sdlc` from the checkout root."""
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    assert ms.read(str(sdlc))[0] == ms.STATUS_ENROLLED_POLICY_MISSING
    line = next(l for l in (ROOT / "README.md").read_text().splitlines()
                if l.startswith("python3 ") and "managed_settings.py unenroll" in l)
    argv = line.split()                                 # copied out of the docs, then run
    argv[0] = sys.executable
    assert argv[1].startswith("<installed-sigma>/skills/agrim-loop/scripts/"), argv
    argv[1] = str(CLI)                                  # only the install prefix differs
    r = subprocess.run(argv, cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert ms.read(str(sdlc))[0] == ms.STATUS_NOT_ADOPTED
    again = _cli(root, "unenroll", ".sdlc")                      # idempotent and polite
    assert again.returncode == 0


def test_status_subcommand_reports_the_status(tmp_path):
    root, sdlc = _checkout(tmp_path)
    r = _cli(root, "status", ".sdlc")
    assert r.returncode == 0 and "not-adopted" in r.stdout


def test_the_refusal_text_and_the_docs_name_the_same_gesture(tmp_path):
    root, sdlc = _checkout(tmp_path)
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    reason = ms.gated_check(str(sdlc), {}, "work.require_review")["reason"]
    readme = (ROOT / "README.md").read_text()
    assert "managed_settings.py unenroll" in reason and "managed_settings.py unenroll" in readme


# ------------------------------------------------------------------------------ doctor parity

def _doctor():
    spec = importlib.util.spec_from_file_location(
        "doctor_423", ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_doctor_names_an_enrolled_missing_policy_file(tmp_path):
    d = _doctor()
    root, sdlc = _checkout(tmp_path)
    assert d._managed_settings_state(str(sdlc), {}) == "absent"
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    state = d._managed_settings_state(str(sdlc), {})
    assert "MISSING" in state and "unenroll" in state and len(state) <= 100
    assert d._managed_settings_adopted(str(sdlc), {}) is True


def test_doctor_journal_row_explains_the_refusal(tmp_path):
    d = _doctor()
    root, sdlc = _checkout(tmp_path)
    (sdlc / "config.json").write_text(json.dumps({"journal": {"enabled": True}}))
    f = _put(sdlc)
    _enrol(sdlc)
    f.unlink()
    row = {n: s for n, s, _ in d.features(str(sdlc))}["journal (local event records)"]
    assert row.startswith("off") and "managed settings" in row, row
