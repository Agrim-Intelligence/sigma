"""#462 -- B6: the committed work records (`.sdlc/plans`, `.sdlc/research`, `.sdlc/acceptance`).

These are tracked project history, read by goal stem and never pruned, so
`docs/launch/dispositions/462.json` records them as intentionally unbounded and
`docs/launch/b6-committed-work-records.md` states the ceiling.  A ceiling written in prose guards
nothing, so `records_over_ceiling` below is the guard: it reads `git ls-files` and reports a record
over its per-file cap, or a tree whose average bytes per goal passes the per-goal cap.  Every node
that claims something runs that claim against the real tree, the real scan, or a scratch git repo.

Run it as the doc prescribes: `python3 -m pytest tests/test_b6_462.py`.  It runs wherever the suite
runs (`.github/workflows/ci.yml` runs `pytest tests/`).  Without a `.git` or `git` it fails loudly
rather than reporting a clean tree; in a snapshot that carries no records it reports that nothing was
scanned and the real-tree node skips with that reason.
"""
import importlib.util
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = ROOT / "skills" / "agrim-loop" / "scripts"
FILE = ROOT / "docs" / "launch" / "dispositions" / "462.json"
DOC = ROOT / "docs" / "launch" / "growth-audit.md"
CEILING_DOC = ROOT / "docs" / "launch" / "b6-committed-work-records.md"

#: Per-file caps in bytes.  Largest tracked plan 89,108 and research 25,656; largest pending (not yet
#: committed) plan 92,603 and research 82,252, so 128 KiB clears every real file by 1.4x or more.
PLAN_CAP = 131072
RESEARCH_CAP = 131072
#: Average bytes per goal stem over every tracked record.  Measured mean 11,645; 14,318 counting
#: pending files; the cap is 2.3x the worse of those.
MEAN_PER_GOAL_CAP = 32768
DIRS = ("plans", "research", "acceptance")

#: What the scan produces TODAY.  The issue's list came from an older snapshot: the scan no longer
#: produces `.sdlc/plans/0007-fix-retry.md` or `.sdlc/plans/2521.md` (the prose that named them was
#: reworded, and a pattern the scan does not produce is refused), and now also produces
#: `.sdlc/research/235.md` (a citation in `skills/agrim-init/references/board.md`).
TRACKED = [".sdlc/acceptance/", ".sdlc/acceptance/<goal-stem>.md", ".sdlc/plans/",
           ".sdlc/plans/<epic>-plan.md", ".sdlc/plans/<goal-stem>.md",
           ".sdlc/plans/<goal-stem>.slices.json", ".sdlc/research/", ".sdlc/research/235.md",
           ".sdlc/research/<goal-slug>.md"]
LOCAL = [".sdlc/plans/scope/<slug>.plan.json", ".sdlc/plans/triage/",
         ".sdlc/plans/triage/<UTC-date>-<slug>.json", ".sdlc/plans/triage/<UTC-date>-<slug>.md"]
ISSUE_PATTERNS = set(TRACKED + LOCAL)


def _acceptance_cap():
    spec = importlib.util.spec_from_file_location("acceptance_b6_462", S / "acceptance.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MAX_BYTES


def _cap(directory):
    return {"plans": PLAN_CAP, "research": RESEARCH_CAP, "acceptance": _acceptance_cap()}[directory]


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True)


def _stem(name):
    lead = re.match(r"\d+", name)
    return lead.group() if lead else name.split(".")[0]


def tracked_records(root):
    """[(posix relpath, bytes)] of every tracked regular file under the three record directories.

    Raises, never answers, when `root` is not its own git toplevel (it could read an enclosing
    checkout) or git is unavailable: a guard that cannot look must not say the tree is clean."""
    root = pathlib.Path(root)
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or pathlib.Path(top.stdout.strip()).resolve() != root.resolve():
        raise AssertionError("%s is not its own git toplevel: %r" % (root, top.stdout + top.stderr))
    listed = _git(root, "ls-files", "-z", "--", *[".sdlc/" + d for d in DIRS])
    assert listed.returncode == 0, listed.stderr
    out = []
    for rel in filter(None, listed.stdout.split("\0")):
        path = root / rel
        if path.is_file() and not path.is_symlink():
            out.append((rel, path.stat().st_size))
    return out


def records_over_ceiling(root):
    """Human-readable findings; an empty list means every record is inside its ceiling."""
    records = tracked_records(root)
    findings = []
    stems = {}
    for rel, size in records:
        directory = rel.split("/")[1]
        if size > _cap(directory):
            findings.append("%s is %d bytes, over the %d byte cap for %s" % (rel, size, _cap(directory), directory))
        stems[_stem(rel.rsplit("/", 1)[1])] = None
    total = sum(size for _, size in records)
    if stems and total > MEAN_PER_GOAL_CAP * len(stems):
        findings.append("records average %d bytes per goal over %d goals, over the %d byte cap"
                        % (total // len(stems), len(stems), MEAN_PER_GOAL_CAP))
    return findings


def _entries():
    """{} while the file is absent, so every node below fails by assertion rather than by error."""
    if not FILE.exists():
        return {}
    return {e["pattern"]: e for e in json.loads(FILE.read_text(encoding="utf-8"))}


def _repo(tmp_path, files):
    """A scratch git repo with `files` {relpath: bytes-length} committed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "-C", str(tmp_path)]
    subprocess.run(git + ["init", "-q"], check=True, capture_output=True)
    for rel, size in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "-m", "seed"], check=True, capture_output=True)
    return tmp_path


def _states(pattern, text):
    """The shipped disposition row for `pattern` carries `text`: the claim the guard below enforces."""
    entry = _entries().get(pattern, {})
    assert text in entry.get("pruner_or_cap", ""), (pattern, text)


def _doc():
    return CEILING_DOC.read_text(encoding="utf-8") if CEILING_DOC.exists() else ""


def _small_goals():
    """Ten tiny goals, so a single capped file is judged against its own cap and not the average."""
    return {".sdlc/plans/%d.md" % n: 1 for n in range(100, 110)}


def test_covers():
    entries = _entries()
    assert set(entries) == ISSUE_PATTERNS
    assert not any("unscanned" in e for e in entries.values())


def test_decisions():
    """Every row says unbounded by decision and names who reclaims it; tracked rows name the guard."""
    entries = _entries()
    assert entries
    for pattern in TRACKED + LOCAL:
        entry = entries.get(pattern, {})
        assert entry.get("decision", "").startswith("intentionally unbounded"), pattern
        assert entry.get("pruner_or_cap", "").startswith("none by decision:"), pattern
        assert "reclaim" in entry["pruner_or_cap"], pattern
        assert "docs/launch/b6-committed-work-records.md" in entry["evidence"], pattern
    for pattern in TRACKED:
        assert "tests/test_b6_462.py" in entries[pattern]["evidence"], pattern
        assert "git" in entries[pattern]["pruner_or_cap"], pattern
    for pattern in LOCAL:
        assert "gitignored" in entries[pattern]["decision"], pattern


def test_real_tree():
    """The repository itself is inside its ceiling, and the guard looked at something."""
    _states(".sdlc/plans/<goal-stem>.md", "reclaim is a reviewed git commit")
    records = tracked_records(ROOT)
    if not records:
        pytest.skip("this tree carries no tracked .sdlc records (a snapshot); nothing was scanned")
    assert len(records) > 0
    assert records_over_ceiling(ROOT) == []


def test_plan_cap(tmp_path):
    _states(".sdlc/plans/<goal-stem>.md", "{:,} B per plan".format(PLAN_CAP))
    ok = _repo(tmp_path / "ok", {".sdlc/plans/1.md": PLAN_CAP, **_small_goals()})
    assert records_over_ceiling(ok) == []
    bad = _repo(tmp_path / "bad", {".sdlc/plans/1.md": PLAN_CAP + 1, **_small_goals()})
    findings = records_over_ceiling(bad)
    assert findings and ".sdlc/plans/1.md" in findings[0]


def test_research_cap(tmp_path):
    _states(".sdlc/research/<goal-slug>.md", "{:,} B per plan or research".format(RESEARCH_CAP))
    ok = _repo(tmp_path / "ok", {".sdlc/research/1.md": RESEARCH_CAP, **_small_goals()})
    assert records_over_ceiling(ok) == []
    bad = _repo(tmp_path / "bad", {".sdlc/research/1.md": RESEARCH_CAP + 1, **_small_goals()})
    findings = records_over_ceiling(bad)
    assert findings and ".sdlc/research/1.md" in findings[0]


def test_acceptance_cap(tmp_path):
    cap = _acceptance_cap()
    _states(".sdlc/acceptance/<goal-stem>.md", "{:,} B per acceptance".format(cap))
    ok = _repo(tmp_path / "ok", {".sdlc/acceptance/1.md": cap, **_small_goals()})
    assert records_over_ceiling(ok) == []
    bad = _repo(tmp_path / "bad", {".sdlc/acceptance/1.md": cap + 1, **_small_goals()})
    findings = records_over_ceiling(bad)
    assert findings and ".sdlc/acceptance/1.md" in findings[0]


def test_mean_cap(tmp_path):
    """Three goals of 30,000 bytes each are under the average; one goal at 100,000 is not."""
    _states(".sdlc/plans/", "{:,} B average per goal".format(MEAN_PER_GOAL_CAP))
    ok = _repo(tmp_path / "ok", {".sdlc/plans/%d.md" % n: 30000 for n in (1, 2, 3)})
    assert records_over_ceiling(ok) == []
    bad = _repo(tmp_path / "bad", {".sdlc/plans/1.md": 100000, ".sdlc/research/1.md": 100000})
    findings = records_over_ceiling(bad)
    assert any("per goal" in f for f in findings) and not any(".sdlc/plans/1.md is" in f for f in findings)


def test_untracked_not_counted(tmp_path):
    """Only history counts: a large untracked or gitignored local file is not a record."""
    assert "Untracked and gitignored files are not counted" in _doc()
    repo = _repo(tmp_path, {".sdlc/plans/1.md": 10, ".gitignore": 0})
    (repo / ".sdlc" / "plans" / "2.md").write_bytes(b"x" * (PLAN_CAP * 2))
    assert records_over_ceiling(repo) == []


def test_not_toplevel(tmp_path):
    """A subdirectory of a repository is not trusted: the guard refuses instead of reading the parent."""
    assert "not its own git toplevel" in _doc()
    repo = _repo(tmp_path, {".sdlc/plans/1.md": PLAN_CAP + 1})
    sub = repo / "sub"
    sub.mkdir()
    with pytest.raises(AssertionError):
        records_over_ceiling(sub)


def test_gitignored():
    """The four local rows are gitignored (so never history); the tracked rows' directories are not."""
    for pattern in LOCAL:
        assert "gitignored" in _entries().get(pattern, {}).get("decision", ""), pattern
    for path in (".sdlc/plans/triage/x.json", ".sdlc/plans/scope/x.plan.json", ".sdlc/plans/reconcile/x.json"):
        assert _git(ROOT, "check-ignore", "-q", path).returncode == 0, path
    for path in (".sdlc/plans/1.md", ".sdlc/research/1.md", ".sdlc/acceptance/1.md",
                 ".sdlc/plans/1.slices.json"):
        assert _git(ROOT, "check-ignore", "-q", path).returncode == 1, path


def test_doc_constants():
    """The doc states the numbers the guard enforces, so they cannot drift apart."""
    text = CEILING_DOC.read_text(encoding="utf-8") if CEILING_DOC.exists() else ""
    for number in (PLAN_CAP, RESEARCH_CAP, MEAN_PER_GOAL_CAP, _acceptance_cap()):
        assert "{:,}".format(number) in text, number
    assert "python3 -m pytest tests/test_b6_462.py" in text
    assert "b6-committed-work-records.md" in DOC.read_text(encoding="utf-8")


def test_no_pruner():
    """SMOKE check of the decision that nothing deletes these records: the one per-goal sweep, which is
    the only pruner of per-goal files, must not name these directories.  It cannot see a future pruner
    written elsewhere; the doc says so and `grep` for deletes under `.sdlc/plans` is the review gesture."""
    spec = importlib.util.spec_from_file_location("goal_state_prune_b6_462", S / "goal_state_prune.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for pattern in TRACKED:
        _states(pattern, "none by decision:")
    families = {family for family, _ in mod.FILE_FAMILIES}
    assert families
    assert not families & set(DIRS)


def _gesture():
    block = re.search(r"```sh\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S).group(1)
    line = next(l for l in block.splitlines() if "tools/readiness/growth_audit.py" in l)
    argv = shlex.split(line)
    assert argv[:2] == ["python3", "tools/readiness/growth_audit.py"]
    return [sys.executable] + argv[1:]


def test_audit(tmp_path):
    for name in ("skills", "hooks", "tools", "contract"):
        if (ROOT / name).exists():
            shutil.copytree(ROOT / name, tmp_path / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "docs" / "launch", tmp_path / "docs" / "launch")
    (tmp_path / ".sdlc" / "state").mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "-c", "commit.gpgsign=false", "-C", str(tmp_path)]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(git + args, check=True, capture_output=True)
    done = subprocess.run(_gesture(), cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    result = json.loads((tmp_path / "docs" / "launch" / "growth-audit.json").read_text())
    unresolved = set(result["b6_disposition"]["unresolved_patterns"])
    assert not unresolved & ISSUE_PATTERNS
    rows = {r["pattern"]: r for r in result["store_measurements"]}
    for pattern in ISSUE_PATTERNS:
        assert rows[pattern]["pruner_or_cap"] == _entries()[pattern]["pruner_or_cap"]
        assert rows[pattern]["decision"] == _entries()[pattern]["decision"]
