"""#1058 -- control.py run: baseline, mutate a fresh copy, demand red, discard. Fixture gates read the
mutated file, so a green gate means "the mutation did not land", never "the gate always passes"."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "regression" / "control.py"
PY = sys.executable
READ = "'BROKEN' in open('target.txt').read()"
RED_IF_BROKEN = [PY, "-c", f"import sys;sys.exit(1 if {READ} else 0)"]
ALWAYS_GREEN = [PY, "-c", "pass"]
ALWAYS_RED = [PY, "-c", "import sys;sys.exit(1)"]

# toy mutation engine: same CLI as evals/regression/mutators.py; the regression name picks the behaviour
MUTATORS = '''import sys
exit_for = {"r-missing": 10, "r-ambiguous": 11, "r-noop": 12, "r-refuse": 2}
reg, root = sys.argv[2], sys.argv[sys.argv.index("--root") + 1]
if reg in exit_for:
    sys.stderr.write("mutators.py: toy refusal\\n"); sys.exit(exit_for[reg])
open(root + "/target.txt", "a").write("BROKEN\\n")
'''


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path, rows, gates, extra=None):
    repo = tmp_path / "repo"
    reg = repo / "evals" / "regression"
    reg.mkdir(parents=True)
    (repo / "target.txt").write_text("fine\n")
    (reg / "mutators.py").write_text(MUTATORS)
    (reg / "gates.json").write_text(json.dumps({"gates": [
        {"id": g, "argv": a, "status": s, "pending_reason": "later" if s == "pending" else ""}
        for g, (a, s) in gates.items()]}))
    (reg / "catalogue.json").write_text(json.dumps({"rows": [
        {"id": f"{r}/c", "regression": r, "form": "c", "mutation": "target.txt::x", "must_be_red_by": by,
         "must_be_red_in_live": []} for r, by in rows.items()]}))
    for name, text in (extra or {}).items():
        (repo / name).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    return repo


def _run(tmp_path, repo, *args, timeout=1):
    tmp = tmp_path / "tmp"
    tmp.mkdir(exist_ok=True)
    env = {**os.environ, "TMPDIR": str(tmp)}
    r = subprocess.run([PY, str(SCRIPT), "run", "--repo", str(repo), "--gate-timeout", str(timeout), *args],
                       capture_output=True, text=True, env=env, input="")
    return r, tmp


def _left(tmp):
    return [p.name for p in tmp.iterdir() if p.name.startswith("sigma-regression-")]


def _hash(repo):
    h = hashlib.sha256()
    for p in sorted(repo.rglob("*")):
        if ".git" in p.relative_to(repo).parts or not p.is_file():
            continue
        h.update(str(p.relative_to(repo)).encode() + p.read_bytes())
    return h.hexdigest()


def _load():
    spec = importlib.util.spec_from_file_location("control_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_red_exits_zero_and_cleans_up(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (RED_IF_BROKEN, "live")})
    r, tmp = _run(tmp_path, repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RED r1/c g" in r.stdout and "BASELINE ok g" in r.stdout
    assert _left(tmp) == []


def test_gate_that_stays_green_exits_one_naming_row_and_gate(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g", "dull"]}, {"g": (RED_IF_BROKEN, "live"), "dull": (ALWAYS_GREEN, "live")})
    r, tmp = _run(tmp_path, repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "stays-green r1/c dull" in r.stdout and "stays-green r1/c g" not in r.stdout
    assert _left(tmp) == []


def test_baseline_red_exits_three_before_any_mutation(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g", "bad"]}, {"g": (RED_IF_BROKEN, "live"), "bad": (ALWAYS_RED, "live")})
    r, _ = _run(tmp_path, repo)
    assert r.returncode == 3 and "BASELINE-RED bad" in r.stdout
    assert "RED r1/c" not in r.stdout  # nothing was mutated


def test_pytest_exit_five_is_gate_vacuous(tmp_path):
    t = f"if not ({READ}):\n    def test_ok():\n        pass\n"
    argv = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "t_gate.py"]
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (argv, "live")}, {"t_gate.py": t})
    r, _ = _run(tmp_path, repo, timeout=60)
    assert r.returncode == 6 and "gate-vacuous r1/c g" in r.stdout, r.stdout + r.stderr


def test_pytest_crash_exits_are_not_red(tmp_path):
    t = f"if {READ}:\n    raise RuntimeError('collect')\ndef test_ok():\n    pass\n"
    argv = [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "t_gate.py"]
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (argv, "live")}, {"t_gate.py": t})
    r, _ = _run(tmp_path, repo, timeout=60)
    assert r.returncode == 6 and "gate-broken r1/c g" in r.stdout, r.stdout + r.stderr
    # control: a real assertion failure (pytest exit 1) is red
    t = f"def test_ok():\n    assert not ({READ})\n"
    repo = _repo(tmp_path / "b", {"r1": ["g"]}, {"g": (argv, "live")}, {"t_gate.py": t})
    r, _ = _run(tmp_path / "b", repo, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_mutator_exit_codes_pass_through(tmp_path):
    for reg, code in (("r-missing", 10), ("r-ambiguous", 11), ("r-noop", 12), ("r-refuse", 2)):
        sub = tmp_path / reg
        repo = _repo(sub, {reg: ["g"]}, {"g": (RED_IF_BROKEN, "live")})
        r, tmp = _run(sub, repo)
        assert r.returncode == code, (reg, r.stdout, r.stderr)
        assert _left(tmp) == []


def test_pending_prints_and_strict_exits_four(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g", "p"]}, {"g": (RED_IF_BROKEN, "live"), "p": (ALWAYS_GREEN, "pending")})
    r, _ = _run(tmp_path, repo)
    assert r.returncode == 0 and "PENDING r1/c p: later" in r.stdout
    assert "RED r1/c g" in r.stdout and "p (exit" not in r.stdout
    r, _ = _run(tmp_path, repo, "--strict")
    assert r.returncode == 4 and "PENDING r1/c p" in r.stdout


def test_failure_outranks_strict_pending(tmp_path):
    repo = _repo(tmp_path, {"r1": ["dull", "p"]}, {"dull": (ALWAYS_GREEN, "live"), "p": (ALWAYS_GREEN, "pending")})
    r, _ = _run(tmp_path, repo, "--strict")
    assert r.returncode == 1


def test_working_tree_untouched_after_forced_failure(tmp_path):
    repo = _repo(tmp_path, {"r1": ["dull"]}, {"dull": (ALWAYS_GREEN, "live")})
    (repo / "scratch.txt").write_text("uncommitted\n")
    (repo / "ignored.log").write_text("ignored\n")
    before = _hash(repo)
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    r, tmp = _run(tmp_path, repo)
    assert r.returncode == 1  # forced failure: the gate cannot go red
    assert _hash(repo) == before
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == status
    assert "BROKEN" not in (repo / "target.txt").read_text() and _left(tmp) == []


def test_changed_files_selects_rows(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"], "r2": ["g"]}, {"g": (RED_IF_BROKEN, "live")})
    cat = repo / "evals" / "regression" / "catalogue.json"
    data = json.loads(cat.read_text())
    data["rows"][1]["mutation"] = "other.py::f"
    cat.write_text(json.dumps(data))
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "y")
    r, _ = _run(tmp_path, repo, "--changed-files", "-")  # stdin empty
    assert r.returncode == 0 and "0 rows selected" in r.stdout
    listing = tmp_path / "changed.txt"
    listing.write_text("other.py\n")
    r, _ = _run(tmp_path, repo, "--changed-files", str(listing))
    assert r.returncode == 0 and "RED r2/c g" in r.stdout and "r1/c" not in r.stdout
    listing.write_text("evals/regression/gates.json\n")
    r, _ = _run(tmp_path, repo, "--changed-files", str(listing))
    assert "RED r1/c g" in r.stdout and "RED r2/c g" in r.stdout


def test_unknown_regression_exits_two(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (RED_IF_BROKEN, "live")})
    r, _ = _run(tmp_path, repo, "--regression", "typo")
    assert r.returncode == 2 and "typo" in r.stderr
    r, _ = _run(tmp_path, repo, "--regression", "r1")  # regression name selects its rows
    assert r.returncode == 0 and "RED r1/c g" in r.stdout


def test_gate_timeout_kills_group_and_exits_six(tmp_path):
    pidfile = tmp_path / "grandchild.pid"
    script = ("import os,subprocess,sys,time\n"
              f"if {READ}:\n"
              "    c = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])\n"
              f"    open({str(pidfile)!r}, 'w').write(str(c.pid))\n"
              "    time.sleep(60)\n")
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": ([PY, "-c", script], "live")})
    t0 = time.time()
    r, tmp = _run(tmp_path, repo, timeout=2)
    assert r.returncode == 6 and "gate-timeout r1/c g" in r.stdout, r.stdout + r.stderr
    assert time.time() - t0 < 30 and _left(tmp) == []
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)  # only the exact pid this test saw; never reached when the group kill works
        raise AssertionError("grandchild survived the group kill")


def test_stale_sweep_only_removes_marked_old_dirs(tmp_path):
    mod = _load()
    tmp = tmp_path / "t"
    tmp.mkdir()
    old = time.time() - 3 * 86400

    def make(name, marker, age):
        d = tmp / name
        d.mkdir()
        (d / "f").write_text("x")
        if marker:
            m = d / mod.MARKER_NAME
            m.write_text(mod.MARKER_TEXT)
            os.utime(m, (age, age))
        return d

    stale = make("sigma-regression-old", True, old)
    unmarked = make("sigma-regression-nomark", False, old)
    fresh = make("sigma-regression-new", True, time.time())
    foreign = make("other-dir", True, old)
    wrong = make("sigma-regression-wrongtoken", True, old)
    (wrong / mod.MARKER_NAME).write_text("not-ours")
    os.utime(wrong / mod.MARKER_NAME, (old, old))
    target = tmp_path / "victim"
    target.mkdir()
    (target / "keep").write_text("k")
    (tmp / "sigma-regression-link").symlink_to(target)
    mod.sweep_stale(str(tmp))
    assert not stale.exists()
    assert unmarked.exists() and fresh.exists() and foreign.exists() and wrong.exists()
    assert (target / "keep").exists() and (tmp / "sigma-regression-link").is_symlink()


def test_documented_double_context_gesture_goes_red_on_real_tree(tmp_path):
    gates = {g["id"]: g for g in json.loads((ROOT / "evals/regression/gates.json").read_text())["gates"]}
    assert gates["phase-context-budget"]["argv"] == ["python3", "evals/phase_context_budget.py"]  # the documented gesture
    r, tmp = _run(tmp_path, ROOT, "--regression", "double-context/words", timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BASELINE ok phase-context-budget" in r.stdout  # green on the unmutated copy first
    assert "RED double-context/words phase-context-budget" in r.stdout and _left(tmp) == []


# --- #1059: --record -------------------------------------------------------------------------------

def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()


def _entries(path):
    return {(e["row"], e["gate"]): e for e in json.loads(path.read_text())["rows"]}


def test_record_writes_only_observed_red(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"], "r2": ["green"], "r3": ["g", "later"], "r-missing": ["g", "later"]},
                 {"g": (RED_IF_BROKEN, "live"), "green": (ALWAYS_GREEN, "live"), "later": (ALWAYS_GREEN, "pending")})
    matrix = tmp_path / "matrix.json"
    r, tmp = _run(tmp_path, repo, "--record", "--matrix", str(matrix), "--run-id", "run-7")
    assert r.returncode == 10, r.stdout + r.stderr  # r-missing's mutator failure (10) outranks r2 stays-green (1); --record leaves the exit code unchanged
    got = _entries(matrix)
    red = got[("r1/c", "g")]
    assert red["status"] == "red" and red["run_id"] == "run-7" and red["commit"] == _head(repo)
    assert len(red["date"]) == 10 and len(red["pin"]) == 16
    assert got[("r3/c", "later")] == {"row": "r3/c", "gate": "later", "status": "pending"}
    assert got[("r3/c", "g")]["status"] == "red"
    assert ("r2/c", "green") not in got, "a gate seen green must never be recorded"
    # a mutator failure records no live-gate evidence but keeps the pending entry, so check never sees a hole
    assert ("r-missing/c", "g") not in got and got[("r-missing/c", "later")]["status"] == "pending"
    assert "RECORDED 2 red of" in r.stdout
    assert _left(tmp) == []


def test_record_drops_evidence_of_a_gate_that_stays_green(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"], "r2": ["green"]},
                 {"g": (RED_IF_BROKEN, "live"), "green": (ALWAYS_GREEN, "live")})
    matrix = tmp_path / "matrix.json"
    stale = {"status": "red", "run_id": "old", "date": "2020-01-01", "commit": "a" * 40, "pin": "b" * 16}
    matrix.write_text(json.dumps({"rows": [
        {"row": "r2/c", "gate": "green", **stale}, {"row": "gone/c", "gate": "g", **stale},
        {"row": "r1/c", "gate": "g", **stale}]}))
    r, _ = _run(tmp_path, repo, "--record", "--matrix", str(matrix), "--regression", "r2")
    assert r.returncode == 1
    got = _entries(matrix)
    assert ("r2/c", "green") not in got, "stale red evidence for a gate now green must be removed"
    assert ("gone/c", "g") not in got, "pairs absent from the tested catalogue are pruned"
    assert got[("r1/c", "g")]["run_id"] == "old", "an unselected pair keeps its entry"


def test_record_never_writes_without_flag_or_on_baseline_red(tmp_path):
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (RED_IF_BROKEN, "live")})
    matrix = tmp_path / "matrix.json"
    r, _ = _run(tmp_path, repo, "--matrix", str(matrix))
    assert r.returncode == 0 and not matrix.exists()
    bad = _repo(tmp_path / "bad", {"r1": ["g"]}, {"g": (ALWAYS_RED, "live")})
    r, tmp = _run(tmp_path / "bad", bad, "--record", "--matrix", str(matrix))
    assert r.returncode == 3 and not matrix.exists()
    r, _ = _run(tmp_path, repo, "--record", "--matrix", str(matrix), "--regression", "nope")
    assert r.returncode == 2 and not matrix.exists()
    assert not list(tmp_path.glob("matrix.json.tmp.*"))


def test_record_git_env_is_not_inherited(tmp_path, monkeypatch):
    repo = _repo(tmp_path, {"r1": ["g"]}, {"g": (RED_IF_BROKEN, "live")})
    head = _head(repo)  # before GIT_DIR is poisoned: the test's own git call must see the fixture
    other = tmp_path / "elsewhere"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    matrix = tmp_path / "matrix.json"
    r, _ = _run(tmp_path, repo, "--record", "--matrix", str(matrix))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _entries(matrix)[("r1/c", "g")]["commit"] == head, "GIT_DIR must not redirect rev-parse"
