"""Slice 7 of the decision rubric (#997): the recorded permission is bound to a class, an optional
action digest and a durable use counter; `scrub_marker` quotes a first-line marker."""
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

import test_spend_approval as base

sa = base.sa
FakeSource, CFG, BODY = base.FakeSource, base.CFG, base.BODY


@pytest.fixture(autouse=True)
def _tmp_sdlc(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sdlc").mkdir()


def _digest(action):
    return hashlib.sha256(" ".join(action.split()).encode("utf-8")).hexdigest()[:12]


def test_non_spend_class_is_refused_loudly():
    src = FakeSource()
    out = sa.check(".sdlc", "77", "drop the table", CFG, src, hardstop_class="destructive")
    assert out.startswith("DENIED") and "destructive" in out
    assert src.notes == []


def test_class_must_also_be_listed_in_config():
    cfg = {"spend_approval": {"enabled": True, "approvers": ["Alice"], "classes": []}}
    out = sa.check(".sdlc", "77", "render", cfg, FakeSource(), hardstop_class="spend")
    assert out.startswith("DENIED")


def test_spend_class_and_no_class_are_both_approved_alike():
    assert sa.check(".sdlc", "77", "render", CFG, FakeSource(), hardstop_class="spend") == \
        "APPROVED campaign-7"


def test_no_class_is_byte_identical():
    src = FakeSource()
    assert sa.check(".sdlc", "77", "render", CFG, src) == "APPROVED campaign-7"
    audit = src.notes[0][1]
    assert "class" not in audit and "digest" not in audit
    assert sa.check(".sdlc", "77", "render", {}, FakeSource()) == "OFF"


def test_digest_mismatch_is_denied():
    body = "sigma:spend-approved=campaign-7:" + _digest("render clips") + "\nbody\n"
    assert sa.check(".sdlc", "77", "render clips", CFG, FakeSource(body=body)) == "APPROVED campaign-7"
    out = sa.check(".sdlc", "77", "render MORE clips", CFG,
                   FakeSource(body=body.replace("campaign-7", "campaign-8")))
    assert out.startswith("DENIED") and "digest" in out


def test_counter_survives_comment_delete():
    src = FakeSource()
    assert sa.check(".sdlc", "77", "render", CFG, src).startswith("APPROVED")
    src.comments.clear()                                  # the audit comment is deleted
    out = sa.check(".sdlc", "77", "render", CFG, src)
    assert out.startswith("DENIED") and "already used" in out
    state = json.loads(pathlib.Path(".sdlc/state/spend-approval-used.json").read_text())
    assert "77:campaign-7" in state


def _state():
    return json.loads(pathlib.Path(".sdlc/state/spend-approval-used.json").read_text())


def test_counter_is_capped_and_an_evicted_entry_still_refuses_reuse(monkeypatch):
    """B6 (#1263). The file is the single-use record that survives a deleted audit comment, so the cap may
    not forget: the oldest full entries are folded into short hash tombstones, and a tombstoned key still
    refuses. Only a tombstone pushed past MAX_TOMBSTONES is dropped (the issue-side used-marker is then the
    only record)."""
    monkeypatch.setattr(sa, "MAX_USED", 4, raising=False)
    monkeypatch.setattr(sa, "MAX_TOMBSTONES", 6, raising=False)
    sources = {}
    for goal in range(1, 9):                                  # 8 grants: 4 full + 4 folded
        sources[str(goal)] = FakeSource()
        assert sa.check(".sdlc", str(goal), "render", CFG, sources[str(goal)]).startswith("APPROVED")
    state = _state()
    full = [k for k in state if k != "__tombstones__"]
    assert len(full) == 4 and len(state["__tombstones__"]) == 4
    folded = [str(g) for g in range(1, 9) if "%d:campaign-7" % g not in state]
    assert len(folded) == 4
    for goal in folded:                                       # the audit comment is gone: only the file remembers
        sources[goal].comments.clear()
        out = sa.check(".sdlc", goal, "render", CFG, sources[goal])
        assert out.startswith("DENIED") and "already used" in out, (goal, out)


def test_counter_tombstones_are_themselves_capped(monkeypatch):
    monkeypatch.setattr(sa, "MAX_USED", 2, raising=False)
    monkeypatch.setattr(sa, "MAX_TOMBSTONES", 3, raising=False)
    for goal in range(1, 12):
        assert sa.check(".sdlc", str(goal), "render", CFG, FakeSource()).startswith("APPROVED")
    state = _state()
    assert len([k for k in state if k != "__tombstones__"]) == 2
    assert len(state["__tombstones__"]) == 3


def test_a_counter_file_from_before_the_cap_still_refuses(tmp_path):
    state_dir = pathlib.Path(".sdlc/state")
    state_dir.mkdir(parents=True)
    (state_dir / "spend-approval-used.json").write_text(
        json.dumps({"77:campaign-7": {"at": "2026-01-01T00:00:00Z", "class": "spend"}}))
    src = FakeSource()
    src.comments.clear()
    out = sa.check(".sdlc", "77", "render", CFG, src)
    assert out.startswith("DENIED") and "already used" in out


def test_unwritable_counter_denies(tmp_path):
    (tmp_path / ".sdlc" / "state").write_text("not a directory")
    out = sa.check(".sdlc", "77", "render", CFG, FakeSource())
    assert out.startswith("DENIED")


def test_scrub_quotes_a_first_line_marker():
    body, changed = sa.scrub_marker("sigma:spend-approved=x\nmore\n")
    assert changed and body == "`sigma:spend-approved=x`\nmore\n"
    plain = "hello\nsigma:spend-approved=x\n"
    assert sa.scrub_marker(plain) == (plain, False)
    assert sa.scrub_marker("") == ("", False)


def test_documented_gesture_denies_other_class(tmp_path):
    (tmp_path / "w").mkdir()
    d = base._sdlc(tmp_path / "w", {"enabled": True, "approvers": ["alice"]})
    cmd = base._skill_gesture().replace("${CLAUDE_SKILL_DIR}", str(base.S.parent))
    cmd = cmd.replace('"$goal"', "77").replace(" .sdlc ", f" {d} ")
    import shlex
    argv = shlex.split(cmd)[1:] + ["--class", "destructive"]
    r = subprocess.run([sys.executable, *argv], capture_output=True, text=True, cwd=tmp_path,
                       input="drop it")
    assert r.returncode == 3 and r.stdout.split()[0] == "DENIED" and "destructive" in r.stdout, \
        (r.stdout, r.stderr)
