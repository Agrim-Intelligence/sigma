#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Measure mutation kill rate per Tier A Python module and write the evidence JSON (#360).

USAGE
    mutation_sample.py <clone> --units docs/launch/review-units.json --venv <venv-dir> --out <file.json>
        [--module-timeout 900] [--budget 10800] [--max-load 10] [--load-wait-max 3600]
        [--only PATH,PATH]

<clone> is a FROZEN, CLEAN git clone: mutmut edits source files in place, so never point this at a
working checkout. <venv-dir> is a throwaway virtualenv holding mutmut 2.x, coverage and pytest; it is
never part of the repository's dependencies or of the verify path.

One module at a time, cheapest first, each under its own wall-clock timeout, the whole run under one
overall cap. The result file is rewritten after every module, and a re-run skips modules already
recorded, so the sample survives a killed shell and can be spread over several sittings.

A kill rate is only ever a number a mutmut run printed. A module that timed out, had no tests, or was
never reached is `absent` with a reason code, never a fabricated number.

EXIT: 0 = finished (see `complete` and the not-run-budget entries in the file); 1 = failed; 2 = bad
      arguments. A run killed part-way writes no final state: run the same command again to resume.
"""
import argparse
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "readiness-mutation/v1"
VENV_PLACEHOLDER = "<sigma-ops>/readiness/venv-mutmut"
SURVIVOR_CAP = 60
#: Absent reasons that a re-run does NOT retry: each one is a measured fact about the module.
FINAL_REASONS = {"timeout", "no-tests", "baseline-failed", "no-mutants", "not-python"}


HOW_TO_READ = (
    "kill_rate is killed / (killed + survived) over the mutants mutmut generated on lines the scoped "
    "test files execute (coverage-scoped), so it measures how strongly THOSE TESTS check THAT MODULE, "
    "not how well the module is tested overall. A low rate does not by itself mean a defect: survivors "
    "include equivalent mutants that change no behaviour, and tests outside the scoped files may kill "
    "them. Timeout, suspicious and skipped mutants are reported in counts and are not in the rate "
    "(rate_range_all_mutants is [only killed count, killed + timeout + suspicious count], over killed + "
    "timeout + suspicious + survived; skipped mutants are in neither). A rate over few mutants is thin evidence: read it with "
    "counts. `verdict` compares the rate with mutation.py's default 0.8 and is NOT a decision; the owner "
    "sets the threshold. A module with no number is absent and says why; it is never recorded as 0. "
    "To retry a timeout, run the same command with a larger --module-timeout: a timeout recorded with "
    "a smaller allowance is retried.")


def _mutation():
    here = Path(__file__).resolve().parent.parent.parent / "skills" / "sigma-loop" / "scripts"
    spec = importlib.util.spec_from_file_location("sigma_mutation", here / "mutation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tier_a_modules(units_path):
    """-> (python modules in first-seen order, non-python paths). One entry per FILE: the units list
    cuts a large file into line ranges, but mutation runs on whole modules."""
    data = json.loads(Path(units_path).read_text(encoding="utf-8"))
    py, other = [], []
    for unit in data["tier_a"]:
        for f in unit["files"]:
            bucket = py if f["path"].endswith(".py") else other
            if f["path"] not in bucket:
                bucket.append(f["path"])
    return py, other


def select_tests(root, module):
    """-> (test files relative to root, the rule that chose them).

    The issue's rule (`grep -l <module> tests/test_*.py`) selects 100-200 files for the large modules,
    which is the whole suite per mutant. So: files named for the module first; failing that, the files
    that reference it as `<stem>.py` or by import, most references first, at most five. The rule used
    and the files are recorded per module, so a reader can judge the number."""
    stem = Path(module).stem
    tests = sorted(Path(root, "tests").glob("test_*.py"))
    named = [t for t in tests if t.name.startswith("test_" + stem.lstrip("_"))]
    if named:
        return [str(t.relative_to(root)) for t in named], "filename"
    pat = re.compile(r"%s\.py|import\s+%s\b|from\s+%s\s+import" % ((re.escape(stem),) * 3))
    scored = []
    for t in tests:
        n = len(pat.findall(t.read_text(encoding="utf-8", errors="replace")))
        if n:
            scored.append((-n, t.name, t))
    scored.sort()
    return [str(t.relative_to(root)) for _, _, t in scored[:5]], "references(max5)"


def _sha(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True, check=True).stdout.strip()


def _clean_tracked(root):
    p = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                       capture_output=True, text=True, check=True)
    return p.stdout.strip() == ""


def _restore(root):
    """Undo whatever mutmut left on disk, then drop its caches."""
    subprocess.run(["git", "-C", str(root), "checkout", "--", "."], capture_output=True)
    # mutmut keeps `<file>.bak` while a mutant is applied; a killed run can leave it behind.
    for p in [Path(root, ".mutmut-cache"), *Path(root).glob(".coverage*"), *Path(root).rglob("*.py.bak")]:
        if p.name == ".coveragerc":
            continue                    # the repository's own tracked config, not an artefact
        if p.is_dir():
            subprocess.run(["rm", "-rf", str(p)])
        elif p.exists():
            p.unlink()


def _sh(argv, root, timeout):
    p = subprocess.Popen(argv, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        text, _ = p.communicate(timeout=timeout)
    except BaseException:               # a timeout, Ctrl-C or SIGTERM: never leave the group running
        try:
            os.killpg(p.pid, 9)
        except (OSError, AttributeError):
            p.kill()
        p.communicate()
        raise
    return p.returncode, text or ""


def _compact_diff(text):
    """The mutated lines only: mutmut's diff header, hunk marker, and the `-`/`+` lines. The three
    context lines each side say nothing about the mutant, and the evidence file is not the place to
    copy neighbouring source."""
    keep = [l for l in text.strip().splitlines()
            if l.startswith(("--- ", "+++ ", "@@")) or (l[:1] in "+-" and not l.startswith(("---", "+++")))]
    return "\n".join(keep)


def _survivors(root, venv, ids, sh=_sh):
    shown = []
    for mid in ids[:SURVIVOR_CAP]:
        try:
            _, text = sh([str(Path(venv, "bin", "mutmut")), "show", str(mid)], root, 60)
        except subprocess.TimeoutExpired:
            text = "(mutmut show timed out)"
        shown.append({"id": mid, "diff": _compact_diff(text)})
    return {"total": len(ids), "shown": len(shown), "items": shown}


def measure(root, module, venv, timeout, mutation=None, sh=_sh):
    """One module -> its evidence entry. Never raises: any failure is `absent` with a reason code."""
    mutation = mutation or _mutation()
    started = time.monotonic()
    files, rule = select_tests(root, module)

    def entry(verdict, code, reason, **kw):
        base = {"verdict": verdict, "kill_rate": None, "killed": None, "survived": None,
                "tests_used": files, "test_rule": rule, "reason_code": code, "reason": reason,
                "seconds": round(time.monotonic() - started, 1)}
        base.update(kw)
        return base

    if not files:
        return entry(mutation.ABSENT, "no-tests", "no test file names or imports this module")
    py = str(Path(venv, "bin", "python"))
    pytest_argv = [py, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *files]
    try:
        _restore(root)
        try:
            # The repository's .coveragerc writes one data file per process (parallel, patch =
            # subprocess); `combine` merges them into the single .coverage mutmut reads.
            code, text = sh([py, "-m", "coverage", "run", "-m", "pytest",
                             "-x", "-q", "-p", "no:cacheprovider", *files], root, timeout)
            if code == 0:
                code, text = sh([py, "-m", "coverage", "combine", "--keep"], root, 120)
        except subprocess.TimeoutExpired:
            return entry(mutation.ABSENT, "timeout", "baseline test run exceeded the module timeout")
        if code != 0:
            tail = " ".join(text.strip().splitlines()[-2:])[:200]
            return entry(mutation.ABSENT, "baseline-failed",
                         "the scoped tests do not pass unmutated: " + tail)
        left = max(30, timeout - (time.monotonic() - started))
        r = mutation.run(root, [module], timeout=left, tool=str(Path(venv, "bin", "mutmut")),
                         runner=shlex.join(pytest_argv))
        if r["verdict"] == mutation.ABSENT:
            reason, code_ = r["reason"], r.get("code") or "crash"
            return entry(mutation.ABSENT, code_, reason, counts=r.get("counts", {}))
        c = r.get("counts", {})
        everything = sum(c.get(k, 0) for k in ("killed", "timeout", "suspicious", "survived"))
        rng = None
        if everything:
            # Lowest: only `killed` counts as a kill. Highest: timeouts and suspicious mutants count
            # as kills too. The headline rate (survived and killed only) lies between them.
            rng = [round(c.get("killed", 0) / everything, 4),
                   round((c.get("killed", 0) + c.get("timeout", 0) + c.get("suspicious", 0))
                         / everything, 4)]
        surv = {"total": 0, "shown": 0, "items": []}
        if r["survived"]:
            _, results = sh([str(Path(venv, "bin", "mutmut")), "results"], root, 120)
            surv = _survivors(root, venv, mutation.parse_survivor_ids(results), sh)
        return entry(r["verdict"], None, r["reason"], kill_rate=r["kill_rate"], killed=r["killed"],
                     survived=r["survived"], counts=r["counts"], survivors=surv,
                     coverage_scoped=True, rate_range_all_mutants=rng)
    except Exception as exc:            # noqa: BLE001 - nothing measured is `absent`, never a number
        return entry(mutation.ABSENT, "crash", "%s: %s" % (type(exc).__name__, exc))
    finally:
        _restore(root)


def _venv_facts(venv):
    py = str(Path(venv, "bin", "python"))
    out = subprocess.run([py, "-c", "import sys,importlib.metadata as m;"
                          "print(m.version('mutmut'));print(sys.version.split()[0])"],
                         capture_output=True, text=True, check=True).stdout.split()
    return out[0], out[1]


def _scrub(text, root):
    for raw, repl in ((str(root), "<clone>"), (str(Path.home()), "~")):
        text = text.replace(raw, repl)
    # Any other user's home that appears in a traceback tail or a source line.
    return re.sub(r"/(?:Users|home)/[^/\s\"'\\]+", "<home>", text)


def _write(path, doc, root):
    text = _scrub(json.dumps(doc, indent=1, sort_keys=False) + "\n", root)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def sample(root, units, venv, out, module_timeout=900, budget=10800, max_load=10.0,
           load_wait_max=3600, only=None, measure_fn=measure, loadavg=None, sleep=time.sleep,
           clock=time.monotonic, facts=None):
    """Run the sample; returns the evidence document. See the module docstring."""
    mutation = _mutation()
    root, venv, out, units = (str(Path(x).resolve()) for x in (root, venv, out, units))
    if not _clean_tracked(root):
        raise SystemExit("mutation_sample: the clone has tracked modifications; use a frozen clean clone")
    loadavg = loadavg or (lambda: os.getloadavg()[0])
    py_mods, other = tier_a_modules(units)
    if only:
        py_mods = [m for m in py_mods if m in only]
    sizes = {m: sum(1 for _ in Path(root, m).open(encoding="utf-8", errors="replace"))
             if Path(root, m).exists() else 0 for m in py_mods}
    order = sorted(py_mods, key=lambda m: (sizes[m], m))
    sha = _sha(root)
    mutmut_v, py_v = facts or _venv_facts(venv)
    prior = {}
    if Path(out).exists():
        prior = json.loads(Path(out).read_text(encoding="utf-8"))
        if prior.get("sha") != sha:
            raise SystemExit("mutation_sample: %s measured a different commit; remove it to start over" % out)
    modules = prior.get("modules", {})
    used = prior.get("budget", {}).get("used_seconds", 0.0)
    prior_timeout = prior.get("budget", {}).get("module_timeout_seconds", module_timeout)
    for p in other:
        modules[p] = {"verdict": mutation.ABSENT, "kill_rate": None, "killed": None, "survived": None,
                      "tests_used": [], "reason_code": "not-python", "seconds": 0.0,
                      "reason": "not a Python file; mutmut mutates Python only"}
    t0 = clock()
    stopped_for_load = False

    def doc(complete):
        return {
            "schema": SCHEMA, "sha": sha, "sha12": sha[:12], "complete": complete,
            "mutmut": mutmut_v, "python": py_v, "venv": VENV_PLACEHOLDER,
            "budget": {
                "module_timeout_seconds": module_timeout, "total_seconds": budget,
                "max_load_1min": max_load, "used_seconds": round(used + (clock() - t0), 1),
                "deviation_from_issue": "the issue allows 60 minutes per module and no overall cap; "
                                        "this run used the per-module timeout and total cap above on a "
                                        "shared machine, sequentially, under nice",
            },
            "how_to_read": HOW_TO_READ,
            "modules": dict(sorted(modules.items())),
            "summary": {
                "measured": sorted(m for m, e in modules.items() if e.get("kill_rate") is not None),
                "absent": {m: e["reason_code"] for m, e in sorted(modules.items())
                           if e.get("kill_rate") is None},
            },
        }

    for module in order:
        done = modules.get(module)
        if done and done.get("reason_code") == "timeout" and \
                done.get("allowed_seconds", prior_timeout) < module_timeout:
            done = None                 # timed out under a smaller allowance than this run offers
        if done and (done.get("kill_rate") is not None or done.get("reason_code") in FINAL_REASONS):
            continue
        waited = 0.0
        while loadavg() > max_load and waited < load_wait_max:
            sleep(60)
            waited += 60
            if used + (clock() - t0) >= budget:
                break
        if used + (clock() - t0) >= budget:
            break
        if loadavg() > max_load:        # still loaded after the bound: stop rather than add to it
            stopped_for_load = True
            break
        remaining = budget - (used + (clock() - t0))
        allowed = min(module_timeout, max(60, remaining))
        modules[module] = dict(measure_fn(root, module, venv, allowed), allowed_seconds=round(allowed, 1))
        _write(out, doc(False), root)
    for module in order:
        done = modules.get(module)
        if not done or (done.get("kill_rate") is None and done.get("reason_code") not in FINAL_REASONS):
            modules[module] = {
                "verdict": mutation.ABSENT, "kill_rate": None, "killed": None, "survived": None,
                "tests_used": [], "reason_code": "not-run-budget", "seconds": 0.0,
                "reason": ("the machine stayed above the load limit for %ss; stopped rather than add "
                           "to it" % load_wait_max) if stopped_for_load else
                          "the overall wall-clock cap was reached before this module was reached"}
    complete = all(modules.get(m, {}).get("reason_code") != "not-run-budget" for m in order)
    final = doc(complete)
    _write(out, final, root)
    return final


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("clone")
    ap.add_argument("--units", required=True)
    ap.add_argument("--venv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--module-timeout", type=int, default=900)
    ap.add_argument("--budget", type=int, default=10800)
    ap.add_argument("--max-load", type=float, default=10.0)
    ap.add_argument("--load-wait-max", type=int, default=3600)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    # A terminated sample must still run measure()'s `finally` (restore the mutated clone).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    only = [x for x in a.only.split(",") if x] or None
    d = sample(a.clone, a.units, a.venv, a.out, a.module_timeout, a.budget, a.max_load,
               a.load_wait_max, only)
    n = sum(1 for e in d["modules"].values() if e.get("reason_code") == "not-run-budget")
    print("mutation_sample: %d measured, %d absent, %d not run (cap)" % (
        len(d["summary"]["measured"]), len(d["summary"]["absent"]) - n, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
