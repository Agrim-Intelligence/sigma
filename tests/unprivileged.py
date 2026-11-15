"""Run a denied read as someone the denial applies to (#2225).

A permission-denial test (`chmod(0)`, mode 000) proves nothing when the suite runs as root: root holds
CAP_DAC_OVERRIDE and reads the file anyway. This container runs the suite as uid 0, so such tests were red
(the product read what it should not have been able to) or green for no reason (the denial never happened).

The seam runs ONLY the denied read in a child process, as an unprivileged uid when the suite is root and as the
current user otherwise. The rest of the test (fixture building, assertions) stays in the test process.

* `identity()` -> None when not root, else `(uid, gid)` of `nobody`, or the first ordinary user (1000 <= uid < 65534).
* `tree()` -> a fresh folder under the system temp dir, owned by that uid, with a `home/` inside; removed after,
  whatever modes the test left in it. pytest's own `/tmp/pytest-of-root` is 0700: the reader could not enter it.
* `hand_over(path)` -> chown `path` and everything under it to that uid (a no-op when not root).
* `run(argv, ...)` -> `subprocess.run` as that uid, after a reachability preflight.
* `call(script, expr, **vars)` -> load `script` by path in the reader, evaluate `expr` with `mod`, `Path` and `vars`
  in scope, and return `{"ok": value}` or `{"raised": name, "text": message}`.

Root with no uid to drop to, a drop the kernel refuses, or a reader that cannot reach the interpreter, the script or
the tree all end in `pytest.fail` with `REMEDY`: loud, never a skip (AGENTS.md SAFETY, "refuses loudly"). Nothing
here keeps state between calls; every answer is derived afresh.
"""
import contextlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REMEDY = ("run the suite as a non-root user, or give the image an unprivileged user (`nobody`) that can run "
          "%s and read the repository and the system temp dir" % sys.executable)

#: The reader's driver: load the script by path (as the tests' own `_load` helpers do), evaluate the expression.
DRIVER = (
    "import importlib.util, json, os, sys\n"
    "from pathlib import Path\n"
    "script, expr, names = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])\n"
    "scope = {'Path': Path, **names}\n"
    "if script:\n"
    "    sys.path.insert(0, os.path.dirname(script))\n"
    "    name = os.path.splitext(os.path.basename(script))[0]\n"
    "    spec = importlib.util.spec_from_file_location(name, script)\n"
    "    mod = importlib.util.module_from_spec(spec)\n"
    "    sys.modules[name] = mod\n"
    "    spec.loader.exec_module(mod)\n"
    "    scope['mod'] = mod\n"
    "try:\n"
    "    out = {'ok': eval(expr, scope)}\n"
    "except Exception as exc:\n"
    "    out = {'raised': type(exc).__name__, 'text': str(exc)}\n"
    "print('UNPRIVILEGED-RESULT ' + json.dumps(out, default=str))\n"
)

#: The preflight: every path given must be reachable (a file opened, a folder listed) by the reader.
PROBE = (
    "import os, sys\n"
    "for p in sys.argv[1:]:\n"
    "    open(p, 'rb').close() if not os.path.isdir(p) else os.listdir(p)\n"
)


def _fail(why):
    pytest.fail("running as root and cannot drop to an unprivileged uid: %s; %s" % (why, REMEDY), pytrace=False)


def identity():
    """-> None when not root; else (uid, gid) of the uid a denied read is run as."""
    if os.geteuid() != 0:
        return None
    try:
        entry = pwd.getpwnam("nobody")
        return entry.pw_uid, entry.pw_gid
    except KeyError:
        pass
    for entry in sorted(pwd.getpwall(), key=lambda e: e.pw_uid):
        if 1000 <= entry.pw_uid < 65534:
            return entry.pw_uid, entry.pw_gid
    _fail("no `nobody` user and no ordinary user (1000 <= uid < 65534) in the passwd database")


def hand_over(path):
    """chown `path` and everything under it to the reader (a no-op when not root)."""
    who = identity()
    if who is None:
        return
    path = Path(path)
    os.chown(path, *who, follow_symlinks=False)
    if path.is_dir() and not path.is_symlink():
        for top, dirs, files in os.walk(path):
            for name in dirs + files:
                os.chown(os.path.join(top, name), *who, follow_symlinks=False)


def _open_up(root):
    """Give every entry under `root` back its owner's access, so the tree can be removed whatever the test left."""
    os.chmod(root, 0o700)
    for top, dirs, files in os.walk(root):
        for name in dirs:
            os.chmod(os.path.join(top, name), 0o700)
        for name in files:
            p = os.path.join(top, name)
            if not os.path.islink(p):
                os.chmod(p, 0o600)


@contextlib.contextmanager
def tree(prefix="unprivileged-"):
    """-> a fresh folder (with `home/`) the reader owns, under the system temp dir; removed on exit."""
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=tempfile.gettempdir()))
    try:
        (root / "home").mkdir()
        hand_over(root)
        yield root
    finally:
        with contextlib.suppress(OSError):
            _open_up(root)
        shutil.rmtree(root, ignore_errors=True)


def _as_reader(argv, who, **kw):
    if who is not None:
        kw.update(user=who[0], group=who[1], extra_groups=[])
    try:
        return subprocess.run(argv, **kw)
    except PermissionError as exc:            # the kernel refused the drop (or root could not exec as the reader)
        if who is None:
            raise
        _fail("the drop to uid %d was refused: %s" % (who[0], exc))


def _preflight(who, reach, env):
    """The reader must be able to run this interpreter and reach `reach`, or the test would fail for that reason
    and not for the denial it is about."""
    got = _as_reader([sys.executable, "-c", PROBE, *map(str, reach)], who, env=env, capture_output=True,
                     text=True, timeout=120)
    if got.returncode != 0:
        detail = (got.stderr.strip().splitlines() or ["exit %d" % got.returncode])[-1]
        whom = "uid %d" % who[0] if who else "the current user"
        pytest.fail("the unprivileged reader (%s) cannot reach %s: %s; %s"
                    % (whom, ", ".join(map(str, reach)) or sys.executable, detail, REMEDY), pytrace=False)


def _env(env, home):
    base = dict(os.environ if env is None else env)
    for name in [n for n in base if any(w in n.upper() for w in ("TOKEN", "SECRET", "PASSWORD", "KEY", "AUTH", "CREDENTIAL"))]:
        del base[name]                       # the dropped reader needs no credential the parent holds
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    if home is not None:
        base["HOME"] = str(home)
    return base


def run(argv, env=None, home=None, reach=(), **kw):
    """`subprocess.run(argv, **kw)` as the reader (as the current user when not root), after checking it can run
    this interpreter and reach every path in `reach`."""
    who = identity()
    full = _env(env, home)
    _preflight(who, reach, full)
    return _as_reader(argv, who, env=full, **kw)


def call(script, expr, *, env=None, home=None, timeout=120, **names):
    """Load `script` (or nothing, when None) in the reader and evaluate `expr` -> {"ok": value} or
    {"raised": exception name, "text": message}. `names` are passed through JSON (paths as str)."""
    script = "" if script is None else str(script)
    argv = [sys.executable, "-c", DRIVER, script, expr, json.dumps(names, default=str)]
    got = run(argv, env=env, home=home, reach=[script] if script else [], capture_output=True, text=True,
              timeout=timeout)
    for line in got.stdout.splitlines():
        if line.startswith("UNPRIVILEGED-RESULT "):
            return json.loads(line[len("UNPRIVILEGED-RESULT "):])
    raise AssertionError("the reader printed no result (exit %d):\n%s\n%s" % (got.returncode, got.stdout, got.stderr))
