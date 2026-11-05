"""#2139 C through the REAL pick: `chain.py run` with no injected pick runs `loop.py next-batch` (its own sweep, its
#1129 cooldown and its eligibility rule included), and a cycle whose sweep freed the goal is `running`, exit 0.

No process is started. `subprocess.run` is replaced for the test: a `gh` argv is answered by the chain's in-memory
fake (an `-i` read gets gh's include shape, a status line and a blank line before the body); the pick argv (its
second element ends `loop.py`) runs a FRESH `loop` module's `main` in this process with stdout and stderr captured
and `os.environ` restored afterwards (a child's environment never flows back to its parent); anything else goes to
the `subprocess.run` it replaced, the suite's own live-`gh` guard.

(a) interval 0, the goal's explicit blocker closed before the first cycle: the chain's own sweep lifts it, the pick
holds it back (cooldown), the cycle is `running`, the next one claims it.
(b) the default interval, the interval file stamped just now for the PICK's session (so neither the chain's sweep nor
the pick's own may run): only the chain's one forced sweep can lift it. Removing that forced sweep, or the skip signal
`_default_sweep` hands back, leaves the goal labelled and the cycle stuck.

Not reproduced: real `gh` headers and pagination, and the label listing's lag after a write.
"""
import contextlib
import io
import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import resource_mode  # noqa: E402,F401
import test_chain_arm as tca  # noqa: E402
import test_chain_run_freed_2139 as tfr  # noqa: E402

NOW = tca.NOW
SESSION = "4242"


def _route(monkeypatch, fk):
    """Route `subprocess.run`: `gh` to the fake, the pick to an in-process `loop.py`, the rest to the guard."""
    real = subprocess.run
    seen = {"gh": 0, "pick": 0, "failed": []}

    def fake_run(argv, *a, **kw):
        cmd = [str(x) for x in argv] if not isinstance(argv, (str, bytes)) else None
        if cmd and os.path.basename(cmd[0]) == "gh":
            seen["gh"] += 1
            args = cmd[1:]
            try:
                out = fk(args) or ""
            except Exception as exc:                                    # noqa: BLE001 - gh's own failure shape
                seen["failed"].append(" ".join(args[:2]))
                return subprocess.CompletedProcess(cmd, 1, "", str(getattr(exc, "hint", None) or exc))
            if "-i" in args:
                out = "HTTP/2.0 200 OK\n\n" + out
            return subprocess.CompletedProcess(cmd, 0, out, "")
        if cmd and len(cmd) > 1 and cmd[1].endswith("loop.py"):
            seen["pick"] += 1
            loop = tca._mod("loop")
            env = dict(os.environ)
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = loop.main(["loop.py"] + cmd[2:])
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
            finally:
                os.environ.clear()
                os.environ.update(env)
            return subprocess.CompletedProcess(cmd, rc or 0, out.getvalue(), err.getvalue())
        return real(argv, *a, **kw)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def _pick_session_key():
    """The interval file's session key of the pick `chain.py run --session-pid 4242` starts (`auto_unpark._session_key`
    in that process: the run id `loop.py` derives from the session pid, then the pid)."""
    return "%s|%s" % (tca._mod("state").derive_run_id(SESSION), SESSION)


@pytest.mark.parametrize("case", ["a-interval-0", "b-default-interval"])
def test_the_real_pick_frees_then_claims_the_goal(tmp_path, monkeypatch, capsys, case):
    monkeypatch.delenv("SIGMA_RUN_ID", raising=False)
    ch, base, fk = tfr._armed(tmp_path, tfr._goal_waits_on_two(), interval=0 if case == "a-interval-0" else None)
    tfr._close(fk, 2)                                               # the explicit blocker closed before cycle 1
    if case == "b-default-interval":
        tfr._stamp(base, session=_pick_session_key())
    seen = _route(monkeypatch, fk)
    argv = ["chain.py", "run", base, "1", "--session-pid", SESSION]
    capsys.readouterr()
    rc = ch.main(argv, run=fk, now=NOW + 1)
    out = capsys.readouterr().out
    assert seen["pick"] == 1, seen
    assert rc == 0, out
    assert any(line.startswith(tfr.FREED) for line in out.splitlines()), out
    assert tca._rec(base)["run"]["state"] == "running"
    rc2 = ch.main(argv, run=fk, now=NOW + 2)
    out2 = capsys.readouterr().out
    assert seen["pick"] == 2, seen
    assert rc2 == 0 and "claimed: #1" in out2, out2
    assert "sdlc:blocked" not in fk._names(1), fk._names(1)
