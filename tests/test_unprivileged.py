"""The unprivileged-reader seam (`tests/unprivileged.py`) proved on its own (#2225).

A permission-denial test (mode 000, `chmod(0)`) proves nothing when the suite runs as root: root has
CAP_DAC_OVERRIDE and reads the file anyway, so the test is either red for the wrong reason or green for
no reason. The seam runs ONLY the denied read as an unprivileged uid when the suite is root, and as the
current user otherwise. These tests show it denies, that it can still reach what it should, that the
drop really happened, and that it fails LOUDLY (never skips) when it cannot do its job.
"""
import os
import subprocess

import pytest

import unprivileged


def _denied_and_readable(root):
    denied = root / "denied.txt"
    denied.write_text("secret\n")
    readable = root / "readable.txt"
    readable.write_text("open\n")
    unprivileged.hand_over(root)
    denied.chmod(0o000)
    return denied, readable


def test_a_mode_000_file_is_denied_to_the_reader():
    with unprivileged.tree() as root:
        denied, _ = _denied_and_readable(root)
        got = unprivileged.call(None, "Path(p).read_text()", p=str(denied))
    assert got.get("raised") == "PermissionError", got


def test_a_readable_sibling_in_the_same_tree_reads_back():
    """So the denial above is the file's mode, not the reader failing to reach the tree at all."""
    with unprivileged.tree() as root:
        _, readable = _denied_and_readable(root)
        got = unprivileged.call(None, "Path(p).read_text()", p=str(readable))
    assert got == {"ok": "open\n"}, got


def test_the_reader_is_not_root_when_the_suite_is():
    with unprivileged.tree():
        got = unprivileged.call(None, "__import__('os').geteuid()")
    if os.geteuid() == 0:
        uid, _ = unprivileged.identity()
        assert got == {"ok": uid} and uid != 0, got
    else:
        assert unprivileged.identity() is None and got == {"ok": os.geteuid()}, got


def test_a_script_is_loaded_and_called_in_the_reader(tmp_path):
    with unprivileged.tree() as root:
        script = root / "target_mod.py"
        script.write_text("def double(x):\n    return [x, x]\n")
        unprivileged.hand_over(root)
        got = unprivileged.call(script, "mod.double(n)", n=3)
    assert got == {"ok": [3, 3]}, got


def test_root_with_no_uid_to_drop_to_fails_loudly(monkeypatch):
    """No `nobody` and no ordinary user: a root suite cannot deny a read, so the seam must fail, with the
    remedy, rather than skip or carry on as root."""
    monkeypatch.setattr(unprivileged.os, "geteuid", lambda: 0)
    monkeypatch.setattr(unprivileged.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    monkeypatch.setattr(unprivileged.pwd, "getpwall", lambda: [])
    with pytest.raises(pytest.fail.Exception) as info:
        unprivileged.identity()
    assert "cannot drop to an unprivileged uid" in str(info.value) and unprivileged.REMEDY in str(info.value)


def test_a_drop_the_kernel_refuses_fails_loudly(monkeypatch):
    """C-A3: the uid exists but the drop itself fails (a user namespace without it, a seccomp policy)."""
    monkeypatch.setattr(unprivileged, "identity", lambda: (65534, 65534))

    def refused(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(unprivileged.subprocess, "run", refused)
    with pytest.raises(pytest.fail.Exception) as info:
        unprivileged.run(["true"])
    assert "cannot drop to an unprivileged uid" in str(info.value) and unprivileged.REMEDY in str(info.value)


def test_a_reader_that_cannot_reach_the_script_fails_loudly(tmp_path, monkeypatch):
    """R2: the drop succeeds but the dropped uid cannot reach what it must run (a checkout or interpreter
    under a 0700 home, a TMPDIR inside one). Without this preflight an adapted test would fail with an
    unrelated `PermissionError` and no remedy. Under root a 0700 root-owned folder is unreachable to the
    reader; as an ordinary user the same is shown by the probe's own verdict on a path that does not exist."""
    if os.geteuid() == 0:
        hidden = tmp_path / "root-only"            # pytest's tmp tree: root-owned, 0700 above it
        hidden.mkdir(mode=0o700)
        target = hidden / "target_mod.py"
        target.write_text("X = 1\n")
    else:
        target = tmp_path / "does-not-exist" / "target_mod.py"
    with unprivileged.tree():
        with pytest.raises(pytest.fail.Exception) as info:
            unprivileged.call(target, "mod.X")
    assert "cannot reach" in str(info.value) and unprivileged.REMEDY in str(info.value)


def test_the_tree_is_removed_even_with_a_mode_000_entry_left_in_it():
    with unprivileged.tree() as root:
        _denied_and_readable(root)
        (root / "sub").mkdir()
        (root / "sub").chmod(0o000)
    assert not root.exists()


def test_run_drops_privileges_for_a_plain_command():
    with unprivileged.tree():
        out = unprivileged.run(["id", "-u"], capture_output=True, text=True, timeout=60)
    expect = unprivileged.identity()[0] if os.geteuid() == 0 else os.geteuid()
    assert out.returncode == 0 and out.stdout.strip() == str(expect), out
    assert isinstance(out, subprocess.CompletedProcess)
