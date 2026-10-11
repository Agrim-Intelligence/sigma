#!/usr/bin/env python3
"""Layer-2 control (#1058, slice 3 of #808): prove each planted regression turns its gates red.

    python3 evals/regression/control.py run [--regression ID] [--changed-files FILE|-] [--strict]

Extracts `git archive HEAD` into a temp directory (the working tree is never read or written, and
uncommitted changes are NOT tested), runs each needed live gate once on the unmutated copy, then for
every selected catalogue row applies its mutation to a fresh copy and demands each live
`must_be_red_by` gate go red. The temp directory is removed in `finally`. No network, no model,
no `gh`: every gate in gates.json is a local command.

Red means a gate that ran and failed: nonzero, and for a pytest gate exactly 1. Pytest 5 (nothing
selected) is `gate-vacuous`, pytest 2/3/4, a signal or a launch failure is `gate-broken`, a run past
`--gate-timeout` is `gate-timeout`: none of them proves detection.

Exit codes: 0 every live gate red; 1 a gate stayed green (`FAIL stays-green <row> <gate>`); 2 refused
or bad arguments (including an unknown --regression, or the mutator's own 2); 3 a gate is already red
on the unmutated copy (`BASELINE-RED <gate>`, nothing mutated); 4 a gate is `pending` and --strict;
6 gate-vacuous / gate-broken / gate-timeout; 10, 11, 12 the mutator's anchor-missing,
anchor-ambiguous, mutation-noop, passed through. All rows always run; the exit is the first of
2, 10, 11, 12, 1, 6, 4 that occurred. `--record` (the evidence matrix) is #1059, not here.

Cost: serial. Wall time = each distinct live gate once on the baseline + each row's gates (about 80 s
for the live set on one laptop, python 3.13; CI not measured), plus one tree copy per row. Rows are
independent copies, so at 100x rows they could run in parallel: that is the ceiling. A SIGKILL
leaves one `sigma-regression-*` directory under the system temp dir; the next run removes those
that carry this tool's marker, belong to this user and are over a day old. POSIX only.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

PREFIX = "sigma-regression-"
MARKER_NAME = ".sigma-regression-control"
MARKER_TEXT = "sigma-regression-control/1"
STALE_SECONDS = 86400
SEVERITY = (2, 10, 11, 12, 1, 6, 4)


def _env():
    keep = {k: v for k, v in os.environ.items() if not (k.startswith(("GIT_", "SIGMA_")) or k == "PYTHONPATH")}
    return {**keep, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "-p no:cacheprovider"}


def _rmtree(path):
    def retry(fn, p, *_):
        try:
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            fn(p)
        except OSError:
            pass
    shutil.rmtree(path, **{"onexc" if sys.version_info >= (3, 12) else "onerror": retry})


def sweep_stale(tmp=None):
    """Remove leftover control directories: real dirs of ours, marker with our token, older than a day."""
    tmp = tmp or tempfile.gettempdir()
    for name in os.listdir(tmp):
        path = os.path.join(tmp, name)
        try:
            st = os.lstat(path)
            marker = os.path.join(path, MARKER_NAME)
            if (name.startswith(PREFIX) and os.path.isdir(path) and not os.path.islink(path)
                    and st.st_uid == os.getuid() and not os.path.islink(marker)
                    and time.time() - os.lstat(marker).st_mtime > STALE_SECONDS
                    and open(marker, encoding="utf-8").read() == MARKER_TEXT):
                _rmtree(path)
        except OSError:
            continue


def _extract(repo, dest):
    proc = subprocess.Popen(["git", "-C", repo, "archive", "--format=tar", "HEAD"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        tar.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    if proc.wait() != 0:
        raise OSError("git archive HEAD failed: " + proc.stderr.read().decode(errors="replace").strip())


def _run_gate(argv, cwd, timeout):
    """(exit code, kind): kind is None, 'timeout' or 'launch'. A timeout kills the gate's own process group."""
    argv = [sys.executable if a == "python3" and i == 0 else a for i, a in enumerate(argv)]
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, start_new_session=True)
    except OSError:
        return -1, "launch"
    try:
        proc.communicate(timeout=timeout)
        return proc.returncode, None
    except subprocess.TimeoutExpired:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return -1, "timeout"


def _verdict(argv, rc, kind):
    """Classify one mutated run: red, stays-green, gate-vacuous, gate-broken or gate-timeout."""
    if kind == "timeout":
        return "gate-timeout"
    if kind == "launch" or rc < 0:
        return "gate-broken"
    if rc == 0:
        return "stays-green"
    if "pytest" in argv:
        return "red" if rc == 1 else "gate-vacuous" if rc == 5 else "gate-broken"
    return "red"


def _select(rows, gates, args, changed):
    chosen = rows
    if args.regression:
        chosen = [r for r in rows if args.regression in (r["id"], r["regression"])]
        if not chosen:
            return None
    if changed is not None:
        def hit(r):
            argv = {a for g in r["must_be_red_by"] for a in gates.get(g, {}).get("argv", [])}
            file = r["mutation"].split("::")[0]
            return any(c == file or c in argv or c.startswith("evals/regression/") for c in changed)
        chosen = [r for r in chosen if hit(r)]
    return chosen


def _control(args, work):
    base = os.path.join(work, "base")
    _extract(args.repo, base)
    reg = os.path.join(base, "evals", "regression")
    gates = {g["id"]: g for g in json.load(open(os.path.join(reg, "gates.json"), encoding="utf-8"))["gates"]}
    rows = json.load(open(os.path.join(reg, "catalogue.json"), encoding="utf-8"))["rows"]
    changed = None
    if args.changed_files:
        src = sys.stdin if args.changed_files == "-" else open(args.changed_files, encoding="utf-8")
        changed = {ln.strip() for ln in src.read().splitlines() if ln.strip()}
    chosen = _select(rows, gates, args, changed)
    if chosen is None:
        sys.stderr.write(f"control.py: REFUSED: no catalogue row or regression named {args.regression!r}\n")
        return 2
    sha = subprocess.run(["git", "-C", args.repo, "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    print(f"control.py: committed tree {sha} (uncommitted changes are not tested); {len(chosen)} rows selected")
    live = sorted({g for r in chosen for g in r["must_be_red_by"] if gates[g]["status"] == "live"})
    bad = False
    for g in live:
        t0 = time.time()
        rc, kind = _run_gate(gates[g]["argv"], base, args.gate_timeout)
        if rc == 0:
            print(f"BASELINE ok {g} ({time.time() - t0:.1f}s)")
        else:
            bad = True
            print(f"BASELINE-RED {g}: {kind or 'exit %d' % rc} on the unmutated copy; a gate already red proves nothing")
    if bad:
        return 3
    seen = set()
    for n, row in enumerate(chosen):
        os.utime(os.path.join(work, MARKER_NAME))
        copy = os.path.join(work, f"row-{n}")
        shutil.copytree(base, copy, symlinks=True)
        m = subprocess.run([sys.executable, os.path.join(copy, "evals", "regression", "mutators.py"), "apply",
                            row["regression"], "--form", row["form"], "--root", copy],
                           cwd=copy, env=_env(), capture_output=True, text=True)
        if m.returncode:
            seen.add(m.returncode)
            print(f"MUTATOR-FAIL {row['id']}: exit {m.returncode} {m.stderr.strip()}")
            continue
        for g in row["must_be_red_by"]:
            if gates[g]["status"] == "pending":
                seen.add(4 if args.strict else 0)
                print(f"PENDING {row['id']} {g}: {gates[g]['pending_reason']}")
                continue
            rc, kind = _run_gate(gates[g]["argv"], copy, args.gate_timeout)
            v = _verdict(gates[g]["argv"], rc, kind)
            if v == "red":
                print(f"RED {row['id']} {g} (exit {rc})")
            else:
                seen.add(1 if v == "stays-green" else 6)
                print(f"FAIL {v} {row['id']} {g}: exit {rc}" + (" under mutation" if v == "stays-green" else ""))
    return next((c for c in SEVERITY if c in seen), 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="control.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--regression")
    ap.add_argument("--changed-files")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--gate-timeout", type=float, default=600)
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    args = ap.parse_args(argv)
    if os.name != "posix":
        sys.stderr.write("control.py: REFUSED: process-group kill needs POSIX; not supported here\n")
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so `finally` runs and the copy is removed
    sweep_stale()
    work = tempfile.mkdtemp(prefix=PREFIX)
    try:
        with open(os.path.join(work, MARKER_NAME), "w", encoding="utf-8") as fh:
            fh.write(MARKER_TEXT)
        return _control(args, work)
    except OSError as exc:
        sys.stderr.write(f"control.py: REFUSED: {exc}\n")
        return 2
    finally:
        _rmtree(work)


if __name__ == "__main__":
    raise SystemExit(main())
