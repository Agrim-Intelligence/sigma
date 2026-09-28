"""audit-collect.sh — the /agrim-audit evidence collector. Mirrors alignment-collect.sh's contract:
read-only, reproducible, fail-open, secret-safe, zero-dep, renders no verdict. Where alignment-collect
walks COMMITS over a window and asks what changed, this walks the tracked FILE SET and asks what
exists."""
import json, pathlib, subprocess, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "agrim-audit" / "scripts" / "audit-collect.sh"


def _run(cwd, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=str(cwd),
                          capture_output=True, text=True)


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


def _repo(d):
    """A tiny git repo with one source file, one paired test, and a doc."""
    r = pathlib.Path(d)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "src").mkdir()
    (r / "src" / "pay.py").write_text("def charge():\n    pass\n", encoding="utf-8")
    (r / "tests").mkdir()
    (r / "tests" / "test_pay.py").write_text("def test_charge():\n    pass\n", encoding="utf-8")
    (r / "README.md").write_text("docs\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    return r


def test_non_git_tree_degrades_and_still_emits_valid_json():
    """Fail-open: the collector must never be the reason an audit does not run."""
    with tempfile.TemporaryDirectory() as d:
        p = _run(d)
        assert p.returncode == 0, p.stderr
        out = json.loads(p.stdout)
        assert out["schema"] == "audit-collect/v1"
        assert "no_git" in out["degraded"]
        assert out["files"] == []


def test_emits_valid_json_with_the_stable_envelope():
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        out = json.loads(_run(r).stdout)
        assert out["schema"] == "audit-collect/v1"
        for key in ("window", "degraded", "totals", "files"):
            assert key in out, f"missing envelope key {key}"
        assert out["window"]["churn_days"] == 180        # documented default


def test_reproducible_for_the_same_tree_and_window():
    """Same state + same resolved window => byte-identical."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        a = _run(r, "--churn-days", "90").stdout
        b = _run(r, "--churn-days", "90").stdout
        assert a == b and a.strip()


def test_renders_no_verdict():
    """Facts only — judging belongs to the skill. A collector that scores invites trusting the
    score over the code."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        raw = _run(r).stdout.lower()
        for word in ("severity", "critical", "verdict", "violation", "recommend"):
            assert word not in raw, f"collector rendered a judgement: {word!r}"


def test_measures_source_files_and_pairs_them_with_tests():
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        out = json.loads(_run(r).stdout)
        by_path = {f["path"]: f for f in out["files"]}
        assert "src/pay.py" in by_path, "source file not measured"
        assert "README.md" not in by_path, "docs must not be measured as source"
        assert by_path["src/pay.py"]["has_test"] is True, "test_pay.py should pair with pay.py"
        assert by_path["src/pay.py"]["lines"] == 2
        assert out["totals"]["source_files"] >= 1


def test_untested_source_is_flagged_as_such():
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        (r / "src" / "lonely.py").write_text("x = 1\n", encoding="utf-8")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "add lonely")
        out = json.loads(_run(r).stdout)
        by_path = {f["path"]: f for f in out["files"]}
        assert by_path["src/lonely.py"]["has_test"] is False


def test_short_stem_does_not_falsely_pair_with_an_unrelated_test():
    """A bare substring check would pair `src/e.py` with `tests/test_pay.py` (both contain "e"),
    silently reporting untested code as tested — the one direction where being wrong is dangerous,
    because nobody re-checks a file the pack says is covered."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        (r / "src" / "e.py").write_text("x = 1\n", encoding="utf-8")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "add e")
        out = json.loads(_run(r).stdout)
        by_path = {f["path"]: f for f in out["files"]}
        assert by_path["src/e.py"]["has_test"] is False


def test_counts_markers_without_emitting_their_text():
    """Secret safety: counts leave, content never does."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        (r / "src" / "pay.py").write_text(
            "# TODO: refactor\n# ponytail: global lock\nSECRET_LITERAL = 'hunter2'\n",
            encoding="utf-8")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "markers")
        raw = _run(r).stdout
        out = json.loads(raw)
        by_path = {f["path"]: f for f in out["files"]}
        assert by_path["src/pay.py"]["markers"] == 2
        assert "hunter2" not in raw and "refactor" not in raw, "file content leaked into the pack"


def test_ranks_by_score_and_caps_the_pack():
    """The bound is the cost posture: a pack that grows with the repo defeats its purpose."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        for i in range(60):
            (r / "src" / f"m{i:02d}.py").write_text("x = 1\n" * (i + 1), encoding="utf-8")
        _git(r, "add", "-A"); _git(r, "commit", "-qm", "many")
        out = json.loads(_run(r).stdout)
        assert len(out["files"]) <= 40, "pack must be capped"
        scores = [f["score"] for f in out["files"]]
        assert scores == sorted(scores, reverse=True), "files must be ranked by score descending"


def test_source_definition_stays_in_sync_with_alignment_collect():
    """Three collectors now classify paths. If "what counts as source" drifts between them, two
    audits of the same repo disagree about what they even looked at. Mirrors the precedent in
    tests/test_risk_detect.py, which pins risk-detect.sh's patterns to alignment-collect.sh's."""
    import re
    align = (ROOT / "skills" / "agrim-align" / "scripts" / "alignment-collect.sh").read_text()
    audit = SCRIPT.read_text()
    pat = re.compile(r'^SOURCE_EXTS="([^"]+)"', re.M)
    a, b = pat.search(align), pat.search(audit)
    assert a and b, "could not locate SOURCE_EXTS in both collectors"
    assert a.group(1).split() == b.group(1).split()


def test_window_since_is_reported_even_when_history_is_younger_than_the_window():
    """Caught by running on the real repo, not by a fixture: with history younger than --churn-days
    there is no commit BEFORE the cutoff, so the naive lookup returns "" and the pack reports a
    window with no start. The bounds exist so a reader can see which span produced the numbers — an
    empty one silently defeats that, and every young repo hits it."""
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        out = json.loads(_run(r, "--churn-days", "180").stdout)
        assert out["window"]["since"], "window.since must never be empty"
        assert out["window"]["until"]
        # the effective window really does start at the first commit, so say so
        first = subprocess.run(["git", "log", "--reverse", "--format=%cI"], cwd=str(r),
                               capture_output=True, text=True).stdout.splitlines()[0]
        assert out["window"]["since"] == first
