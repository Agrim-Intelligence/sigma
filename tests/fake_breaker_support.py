"""#853: builds a FAKE breaker CLI for the runner and verb tests. Not a test file: nothing here runs at import.

`make_fake(bin_dir)` writes an executable named `cursor-agent` (so `review.host: cursor` routes to it). Each
invocation appends one JSON line to `$FAKE_SIDECAR` describing what it saw (cwd, listing, HOME, the python
and pytest environment variables, stdin, argv, whether `$FAKE_ATTEMPTS` existed at start), then behaves per
`$FAKE_MODE`: `blocks` prints the file `$FAKE_OUT`, `empty` prints nothing, `exit1` exits 1, `hang` starts a
grandchild (its pid goes to `$FAKE_CHILD`) and sleeps past any timeout."""
import json
import pathlib
import re
import stat
import sys

SCRIPT = '''#!%(python)s
import json, os, subprocess, sys, time
stdin = sys.stdin.read()
attempts = os.environ.get("FAKE_ATTEMPTS")
row = {"cwd": os.path.realpath(os.getcwd()), "listing": sorted(os.listdir(".")), "home": os.environ.get("HOME"),
       "pythonpath": os.environ.get("PYTHONPATH"), "venv": os.environ.get("VIRTUAL_ENV"),
       "pytest_vars": sorted(k for k in os.environ if k.startswith("PYTEST_")),
       "bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE"), "stdin": stdin, "argv": sys.argv[1:],
       "attempts_existed": os.path.exists(attempts) if attempts else None}
with open(os.environ["FAKE_SIDECAR"], "a") as fh:
    fh.write(json.dumps(row) + "\\n")
mode = os.environ.get("FAKE_MODE", "blocks")
if mode == "blocks":
    sys.stdout.write(open(os.environ["FAKE_OUT"]).read())
elif mode == "exit1":
    sys.exit(1)
elif mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    open(os.environ["FAKE_CHILD"], "w").write(str(child.pid))
    time.sleep(120)
'''


def make_fake(bin_dir, name="cursor-agent"):
    bin_dir = pathlib.Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(SCRIPT % {"python": sys.executable})
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def runs(sidecar):
    """Every invocation the fake recorded, oldest first."""
    try:
        return [json.loads(line) for line in pathlib.Path(sidecar).read_text().splitlines() if line.strip()]
    except OSError:
        return []


def blocks(stem, files):
    """Breaker output text for {name_suffix: source}: `test_acc_<s>_<suffix>.py` blocks."""
    s = re.sub(r"[^A-Za-z0-9]", "_", stem)
    return "".join("=== FILE: tests/acceptance/%s/test_acc_%s_%s.py ===\n%s=== END FILE ===\n" % (stem, s, name, src)
                   for name, src in files.items())
