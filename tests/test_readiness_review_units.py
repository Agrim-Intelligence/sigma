"""Hermetic controls for the review-unit tool (#352).

Every test builds its own temporary repository (local identity, no signing, no hooks) and runs the
tool the way the documentation prints it: ``review_units.py <repo> --sha SHA [--json PATH]``.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "readiness" / "review_units.py"
COMMITTED = ROOT / "docs" / "launch" / "review-units.json"
DOC = ROOT / "docs" / "launch" / "seeded-defects.md"
FROZEN = "a5c615062313791a86fca8d8b14d7a4ad0713f7f"


def _tool():
    spec = importlib.util.spec_from_file_location("readiness_review_units", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    done = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    for key, value in (("user.email", "test@example.invalid"), ("user.name", "Test"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false"),
                       ("core.quotePath", "true")):
        _git(repo, "config", key, value)
    return repo


def _write(repo, rel, text):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _commit(repo, message="fixture"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _scaffold(repo, ru, skip=()):
    """A stub for every Tier A path the module itself names, so only the tested path varies."""
    for rel in ru.TIER_A_FILES:
        if rel in skip:
            continue
        _write(repo, rel, "#!/bin/sh\ntrue\n" if rel.endswith(".sh") else "x = 1\n")
    _write(repo, "hooks/hooks.json", "{}\n")


def _numbered(count):
    return "".join("v%d = 1\n" % i for i in range(count))


def _cli(*args):
    return subprocess.run([sys.executable, str(TOOL), *[str(a) for a in args]],
                          text=True, capture_output=True, timeout=30)


# The issue's 22 named paths, written out again here so a typo in the tool's constant is a failure.
ISSUE_TIER_A = {
    "install.sh",
    "skills/agrim-loop/scripts/work.py", "skills/agrim-loop/scripts/sources.py",
    "skills/agrim-loop/scripts/feature_rebase.py", "skills/agrim-loop/scripts/ledger.py",
    "skills/agrim-loop/scripts/sync.py", "skills/agrim-loop/scripts/scrub.py",
    "skills/agrim-loop/scripts/watch_daemon.py", "skills/agrim-loop/scripts/supervise_daemon.py",
    "skills/agrim-loop/scripts/run_with_timeout.py",
    "skills/agrim-loop/scripts/slack_commands_listen.py",
    "skills/agrim-loop/scripts/slack_client.py", "skills/agrim-loop/scripts/channel_notify.py",
    "skills/agrim-loop/scripts/upstream.py", "skills/agrim-loop/scripts/reconcile.py",
    "skills/agrim-loop/scripts/blockers.py", "skills/agrim-loop/scripts/state.py",
    "skills/agrim-loop/scripts/gh_session.py", "skills/agrim-loop/scripts/loop.py",
    "skills/agrim-init/scripts/board_setup.py", "skills/agrim-init/scripts/init_flow.py",
    "skills/agrim-init/scripts/setup_wizard.py",
}


# ---- T1: line count, tracked listing, tiers --------------------------------------------------

def test_count_lines_by_newline_not_splitlines():
    ru = _tool()

    assert ru.count_lines(b"a\nb") == 2
    assert ru.count_lines(b"a\nb\n") == 2
    assert ru.count_lines("a b\n".encode("utf-8")) == 1
    assert ru.count_lines(b"a") == 1
    assert ru.count_lines(b"") == 0


def test_tier_a_files_is_22_unique():
    ru = _tool()

    assert len(ru.TIER_A_FILES) == 22
    assert len(set(ru.TIER_A_FILES)) == 22
    assert set(ru.TIER_A_FILES) == ISSUE_TIER_A
    # The constant alone is not the claim: the named paths must also sort into Tier A, every one.
    assert ru.classify(list(ru.TIER_A_FILES)) == (sorted(ISSUE_TIER_A), [])


def test_tier_b_population_excludes_tier_a_tests_and_other_suffixes():
    ru = _tool()
    tier_b = [
        "skills/agrim-loop/scripts/other.py", "tools/readiness/baseline.py", "evals/run.sh",
        "skills/agrim-doctor/scripts/doctor.py",
    ]
    outside = [
        "skills/agrim-loop/tests/test_x.py", "skills/y/tests/helpers/z.py", "tests/test_top.py",
        "tools/readme.md", "tools/data.json", "contract/validate.py", "examples/hello/a.py",
        "docs/x.py", "setup.py", "tools/tests.sh.txt",
    ]
    hooks = ["hooks/session_start.sh", "hooks/hooks.json", "hooks/gate_state.py"]

    tier_a, got_b = ru.classify(sorted(ISSUE_TIER_A) + hooks + tier_b + outside)

    assert tier_a == sorted(ISSUE_TIER_A | set(hooks))
    assert got_b == sorted(tier_b)


def test_missing_tier_a_path_raises_usage_error_naming_it():
    ru = _tool()
    missing = "skills/agrim-loop/scripts/state.py"
    paths = [p for p in ISSUE_TIER_A if p != missing] + ["tools/a.py"]

    with pytest.raises(ru.UsageError) as caught:
        ru.classify(paths)

    assert missing in str(caught.value)


def test_non_ascii_path_with_space_is_read_via_z(tmp_path):
    ru = _tool()
    repo = _repo(tmp_path)
    odd = "tools/café file.py"
    _write(repo, odd, "one\ntwo\n")
    _write(repo, "tools/plain.py", "x = 1\n")
    _write(repo, "tools/target.py", "y = 2\n")
    os.symlink("target.py", str(repo / "tools" / "link.py"))
    sha = _commit(repo)

    paths = ru.tracked_files(repo, sha)

    assert paths == sorted([odd, "tools/plain.py", "tools/target.py"])
    assert ru.read_blob(repo, sha, odd) == b"one\ntwo\n"
    assert ru.count_lines(ru.read_blob(repo, sha, odd)) == 2


# ---- T2: splitting ---------------------------------------------------------------------------

def _def_block(name, lines):
    """A top-level def of exactly ``lines`` lines."""
    return "def %s():\n" % name + "    x = 1\n" * (lines - 1)


def _step_method(name, body_lines, indent, kw="def"):
    """An indented ``# step`` comment, a def, then a body: 2 + body_lines lines."""
    return ("%s# step %s\n%s%s %s(self):\n" % (indent, name, indent, kw, name)
            + ("%s    x = 1\n" % indent) * body_lines)


def _assert_tiles(pieces, n, cap):
    assert pieces, "no pieces"
    assert pieces[0][0] == 1 and pieces[-1][1] == n
    for (_s1, e1), (s2, _e2) in zip(pieces, pieces[1:]):
        assert s2 == e1 + 1
    assert all(1 <= s <= e and e - s + 1 <= cap for s, e in pieces)


def test_big_file_splits_at_def_boundaries():
    ru = _tool()
    raw = "".join(_def_block("f%d" % i, 50) for i in range(140)).encode("utf-8")
    notes = []

    pieces = ru.split_file("tools/big.py", raw, warn=notes.append)

    assert ru.count_lines(raw) == 7000
    assert pieces == [(1, 3000), (3001, 6000), (6001, 7000)]
    assert notes == []


def test_exactly_3000_is_one_unit_3001_splits():
    ru = _tool()
    at_cap = "".join(_def_block("f%d" % i, 50) for i in range(60)).encode("utf-8")
    over = at_cap + b"def tail(): pass\n"

    assert ru.count_lines(at_cap) == 3000 and ru.count_lines(over) == 3001
    assert ru.split_file("tools/at_cap.py", at_cap) == [(1, 3000)]
    assert ru.split_file("tools/over.py", over) == [(1, 3000), (3001, 3001)]


def test_pieces_tile_with_preamble_decorator_and_comment_hoist():
    ru = _tool()
    lead = "# lead %d a\n# lead %d b\n@deco\ndef g%d():\n" + "    x = 1\n" * 36   # 40 lines
    text = _numbered(30) + "".join(lead % (i, i, i) for i in range(6))
    raw = text.encode("utf-8")

    pieces = ru.split_file("tools/lead.py", raw, cap=100)

    # Cuts land on the first comment line above a decorator, not on the decorator or the def.
    assert pieces == [(1, 70), (71, 150), (151, 230), (231, 270)]
    _assert_tiles(pieces, 270, 100)

    # A comment separated from its def by a blank line is not hoisted.
    detached = _numbered(10) + "# detached\n\n" + _def_block("a", 20) + _def_block("b", 30)
    pieces = ru.split_file("tools/detached.py", detached.encode("utf-8"), cap=30)

    assert pieces == [(1, 12), (13, 32), (33, 62)]
    _assert_tiles(pieces, 62, 30)


def test_class_shaped_like_sources_splits_at_methods():
    ru = _tool()
    text = (_numbered(100) + "class Big:\n" + "    a = 1\n" * 49
            + "".join(_step_method("m%d" % i, 38, "    ") for i in range(100)))
    raw = text.encode("utf-8")
    notes = []

    pieces = ru.split_file("skills/big.py", raw, warn=notes.append)

    assert ru.count_lines(raw) == 4150
    assert pieces == [(1, 2990), (2991, 4150)]
    _assert_tiles(pieces, 4150, 3000)
    lines = text.split("\n")
    assert all(lines[start - 1].startswith("    # step ") for start, _end in pieces[1:])
    assert notes == []   # a blank-line fallback cut would also tile: it must not have been needed


def test_class_descent_counts_async_methods_and_nested_classes():
    ru = _tool()
    outer = "".join(_step_method("a%d" % i, 38, "    ", kw="async def") for i in range(90))
    inner = "    class Inner:\n" + "".join(_step_method("m%d" % i, 38, "        ") for i in range(80))
    text = _numbered(20) + "class Outer:\n" + outer + inner
    raw = text.encode("utf-8")
    notes = []

    pieces = ru.split_file("skills/outer.py", raw, warn=notes.append)

    assert ru.count_lines(raw) == 6822
    assert pieces == [(1, 2981), (2982, 5942), (5943, 6822)]
    _assert_tiles(pieces, 6822, 3000)
    lines = text.split("\n")
    assert all(lines[start - 1].lstrip().startswith("# step ") for start, _end in pieces[1:])
    assert notes == []


def test_def_over_cap_and_no_def_file_fall_back():
    ru = _tool()

    def big_def(blank_lines):
        body = ["def big():"] + ["    x = 1"] * 3489
        for index in blank_lines:
            body[index] = ""
        return _def_block("small", 10) + "\n".join(body) + "\n"

    cases = [
        # (path, text, expected pieces, fallback cuts): the cut goes after the last blank line inside
        # the window (file line 2010 here), else it is a hard cut at the cap.
        ("tools/big_def.py", big_def([499, 1999]), [(1, 10), (11, 2010), (2011, 3500)], 1),
        ("tools/big_def_no_blank.py", big_def([]), [(1, 10), (11, 3010), (3011, 3500)], 1),
        ("tools/run.sh", "\n".join("" if i in (2500, 5400) else "echo %d" % i
                                   for i in range(1, 7001)) + "\n",
         [(1, 2500), (2501, 5400), (5401, 7000)], 2),
        ("tools/run_no_blank.sh", "echo hi\n" * 7000,
         [(1, 3000), (3001, 6000), (6001, 7000)], 2),
    ]
    for path, text, expected, cuts in cases:
        raw = text.encode("utf-8")
        notes = []
        pieces = ru.split_file(path, raw, warn=notes.append)

        assert pieces == expected, path
        _assert_tiles(pieces, ru.count_lines(raw), 3000)
        assert len(notes) == cuts, (path, notes)   # every fallback cut says so, a def cut does not


def test_unparseable_python_falls_back():
    ru = _tool()
    raw = ("def broken(:\n" + "    x = 1\n" * 6999).encode("utf-8")
    notes = []

    pieces = ru.split_file("tools/broken.py", raw, warn=notes.append)

    assert pieces == [(1, 3000), (3001, 6000), (6001, 7000)]
    assert notes


# ---- T3: packing and numbering ---------------------------------------------------------------

def _paths_of(units):
    return [[f["path"] for f in unit["files"]] for unit in units]


def test_big_file_splits_at_def_boundaries_and_three_small_files_pack(tmp_path):
    ru = _tool()
    files = {
        "tools/a_small.py": _numbered(500),
        "tools/b_big.py": "".join(_def_block("f%d" % i, 50) for i in range(140)),
        "tools/c_small.py": _numbered(500),
        "tools/d_small.py": _numbered(500),
    }
    repo = _repo(tmp_path)
    for rel, text in files.items():
        _write(repo, rel, text)
    sha = _commit(repo)

    units = ru.units_from_paths(repo, sha, sorted(files), "B")

    # The big file sits between the small ones in path order and still does not split their pack.
    assert [unit["unit"] for unit in units] == ["B01", "B02", "B03", "B04"]
    assert units[0]["lines"] == 1500
    assert units[0]["files"] == [{"path": "tools/%s_small.py" % name, "start": 1, "end": 500}
                                 for name in "acd"]
    assert [(u["files"], u["lines"]) for u in units[1:]] == [
        ([{"path": "tools/b_big.py", "start": 1, "end": 3000}], 3000),
        ([{"path": "tools/b_big.py", "start": 3001, "end": 6000}], 3000),
        ([{"path": "tools/b_big.py", "start": 6001, "end": 7000}], 1000),
    ]
    assert all(unit["lines"] <= 3000 for unit in units)


def test_units_never_span_directories():
    ru = _tool()
    measured = {"tools/a/x1.py": (500, None), "tools/a/x2.py": (500, None),
                "tools/b/y.py": (500, None), "tools/b/sub/z.py": (500, None)}

    units = ru.units_from_measured(measured, "B")

    assert _paths_of(units) == [["tools/a/x1.py", "tools/a/x2.py"], ["tools/b/sub/z.py"],
                                ["tools/b/y.py"]]


def test_next_fit_closes_at_cap():
    ru = _tool()

    def packed(sizes):
        measured = {"tools/f%d.py" % i: (n, None) for i, n in enumerate(sizes)}
        return _paths_of(ru.units_from_measured(measured, "B"))

    # Next fit: the 1000 goes with the 1300, although first fit would put it with the 1800.
    assert packed([1800, 1300, 1000]) == [["tools/f0.py"], ["tools/f1.py", "tools/f2.py"]]
    # A unit may fill to exactly the cap; one line more closes it.
    assert packed([2000, 1000, 1]) == [["tools/f0.py", "tools/f1.py"], ["tools/f2.py"]]
    assert packed([2000, 1001]) == [["tools/f0.py"], ["tools/f1.py"]]


def test_ids_are_sequential_after_sorting_and_width():
    ru = _tool()

    def numbered(count):
        names = ["tools/d%03d/f.py" % i for i in range(count)]
        measured = {name: (10, None) for name in reversed(names)}   # inserted out of order
        units = ru.units_from_measured(measured, "B")
        return [unit["unit"] for unit in units], [paths[0] for paths in _paths_of(units)], names

    ids, first_paths, names = numbered(99)
    assert ids == ["B%02d" % i for i in range(1, 100)]
    assert first_paths == names

    ids, first_paths, names = numbered(100)
    assert ids == ["B%03d" % i for i in range(1, 101)] and ids[-1] == "B100"
    assert first_paths == names


# ---- T4: the draw ----------------------------------------------------------------------------

def _counts():
    """120 files of 50 to 150 lines: none is 2% of the population."""
    return {"tools/f%03d.py" % i: 50 + (i * 37) % 101 for i in range(120)}


def _target(population):
    return -(-population * 20 // 100)   # ceil(20%)


def test_same_seed_same_sample_different_seed_differs():
    ru = _tool()
    counts = _counts()

    first = ru.draw(counts, 7)

    assert first and set(first) <= set(counts)
    assert ru.draw(counts, 7) == first
    assert ru.draw(dict(reversed(list(counts.items()))), 7) == first   # input order is irrelevant
    assert ru.draw(counts, 8) != first


def test_sample_is_15_to_25_percent_over_many_seeds():
    ru = _tool()
    counts = _counts()
    population = sum(counts.values())

    for seed in range(50):
        drawn = ru.draw(counts, seed)
        total = sum(counts[path] for path in drawn)
        assert drawn, seed
        assert population * 15 <= total * 100 <= population * 25, seed   # the issue's band
        assert population * 20 <= total * 100 <= population * 22, seed   # the tool's own


def test_walk_stops_at_the_first_crossing():
    ru = _tool()
    counts = _counts()
    population = sum(counts.values())

    for seed in range(50):
        drawn = ru.draw(counts, seed)
        total = sum(counts[path] for path in drawn)
        assert drawn, seed
        # The sample is the shortest prefix of the walk that reaches 20%: without its last file it
        # was still short. A walk that ran on to the 22% ceiling would fail the left-hand side.
        assert total - counts[drawn[-1]] < _target(population) <= total, seed


def test_draw_below_15_percent_warns():
    ru = _tool()
    notes = []

    drawn = ru.draw({"tools/small.py": 100, "tools/large.py": 900}, 3, warn=notes.append)

    assert drawn == ["tools/small.py"]   # 10%: the 900-line file would overshoot the ceiling
    assert len(notes) == 1 and "15%" in notes[0]
    notes = []
    assert ru.draw(_counts(), 3, warn=notes.append)
    assert notes == []
    with pytest.raises(ru.UsageError):   # every file larger than the ceiling: an empty draw
        ru.draw({"tools/big.py": 1000}, 3)


def test_empty_population_raises_usage_error():
    ru = _tool()

    with pytest.raises(ru.UsageError):
        ru.draw({}, 1)


# ---- T5: build, validator, CLI ---------------------------------------------------------------

FIRST_KEYS = ["schema", "sha", "seed", "tier_a", "tier_b_population_lines", "tier_b_sample"]
WORK = "skills/agrim-loop/scripts/work.py"


def _units_repo(tmp_path, name="repo", skip=(), tier_b_files=20, tier_b_lines=100):
    """A commit with every Tier A path (work.py is 7,000 lines: three units) and 20 files of 100
    lines in tools/. Returns ``(repo, full_sha)``."""
    ru = _tool()
    repo = _repo(tmp_path, name)
    _scaffold(repo, ru, skip=skip)
    if WORK not in skip:
        _write(repo, WORK, "".join(_def_block("f%d" % i, 50) for i in range(140)))
    for index in range(tier_b_files):
        _write(repo, "tools/f%02d.py" % index, _numbered(tier_b_lines))
    return repo, _commit(repo)


def _json_of(done):
    """The parsed stdout of a CLI run. The exit code is asserted BEFORE the text is parsed, so a
    tool that fails or prints nothing is a failed assertion, not a JSONDecodeError."""
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("{"), "no JSON on stdout: %r" % done.stdout
    return json.loads(done.stdout)


def test_cli_seed_is_derived_from_resolved_sha_and_is_byte_identical(tmp_path):
    repo, sha = _units_repo(tmp_path)

    first = _cli(repo, "--sha", "HEAD")
    payload = _json_of(first)

    assert list(payload) == FIRST_KEYS
    assert payload["schema"] == "readiness-units/v1"
    assert payload["sha"] == sha
    assert payload["seed"] == int(sha[:8], 16)
    assert first.stdout == json.dumps(payload, indent=2) + "\n"
    # work.py is split at its def boundaries; the 17 one-line Tier A files of its directory pack.
    assert [unit["unit"] for unit in payload["tier_a"]] == ["A%02d" % i for i in range(1, 8)]
    assert [unit["lines"] for unit in payload["tier_a"]] == [1, 2, 3, 17, 3000, 3000, 1000]
    assert [(f["start"], f["end"]) for unit in payload["tier_a"][4:] for f in unit["files"]] == [
        (1, 3000), (3001, 6000), (6001, 7000)]
    # Tier B is the 20 tools files only: the Tier A stubs under skills/ are not in its population.
    assert payload["tier_b_population_lines"] == 2000
    sample = [f for unit in payload["tier_b_sample"] for f in unit["files"]]
    assert len(sample) == 4 and all((f["start"], f["end"]) == (1, 100) for f in sample)
    assert all(f["path"].startswith("tools/f") for f in sample)

    # Same bytes again, from the commit: a dirty working tree changes nothing, and a 12-character
    # --sha resolves to the same full sha and the same seed.
    _write(repo, "tools/f00.py", "changed = True\n")
    (repo / WORK).unlink()
    again = _cli(repo, "--sha", "HEAD")
    short = _cli(repo, "--sha", sha[:12])
    assert again.returncode == 0 and short.returncode == 0, again.stderr + short.stderr
    assert again.stdout == first.stdout
    assert short.stdout == first.stdout


def test_cli_writes_only_the_json_path(tmp_path):
    repo, sha = _units_repo(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    target = out / "units.json"
    before = sorted(path.name for path in tmp_path.iterdir())
    assert _git(repo, "status", "--porcelain", "--ignored") == ""

    done = _cli(repo, "--sha", sha, "--json", target)

    assert done.returncode == 0, done.stderr
    assert sorted(path.name for path in out.iterdir()) == ["units.json"]
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert _git(repo, "status", "--porcelain", "--ignored") == ""
    assert len(done.stdout.splitlines()) == 1 and str(target) in done.stdout
    assert not done.stdout.startswith("{")
    shown = _cli(repo, "--sha", sha)
    assert shown.returncode == 0, shown.stderr
    assert target.read_text(encoding="utf-8") == shown.stdout   # the file is the JSON, newline ended

    # Re-running rewrites the same bytes (the recovery for a half-written file).
    target.write_text("{", encoding="utf-8")
    assert _cli(repo, "--sha", sha, "--json", target).returncode == 0
    assert target.read_text(encoding="utf-8") == shown.stdout

    # No mkdir: a missing parent directory is an I/O failure (1), not a precondition (2).
    nowhere = tmp_path / "nowhere" / "units.json"
    failed = _cli(repo, "--sha", sha, "--json", nowhere)
    assert failed.returncode == 1 and failed.stderr.startswith("review_units.py: "), failed.stderr
    assert not nowhere.parent.exists()


def test_missing_tier_a_path_exits_2_and_writes_nothing(tmp_path):
    for index, missing in enumerate((WORK.replace("work", "state"), "install.sh")):
        repo, sha = _units_repo(tmp_path, name="repo%d" % index, skip=(missing,))
        target = tmp_path / ("units%d.json" % index)

        written = _cli(repo, "--sha", sha, "--json", target)
        shown = _cli(repo, "--sha", sha)

        for done in (written, shown):
            assert done.returncode == 2, done.stderr
            assert done.stderr.startswith("review_units.py: ") and missing in done.stderr
            assert done.stdout == ""
        assert not target.exists()


def test_empty_tier_b_exits_2(tmp_path):
    repo, sha = _units_repo(tmp_path, name="none", tier_b_files=0)
    # One file that alone is more than the 22% ceiling: nothing can be drawn.
    lone, lone_sha = _units_repo(tmp_path, name="lone", tier_b_files=1)

    for root, root_sha in ((repo, sha), (lone, lone_sha)):
        target = tmp_path / ("units-%s.json" % root.name)
        done = _cli(root, "--sha", root_sha, "--json", target)

        assert done.returncode == 2, done.stderr
        assert done.stderr.startswith("review_units.py: ") and "Tier B" in done.stderr
        assert done.stdout == ""
        assert not target.exists()


def test_unknown_sha_and_non_repo_exit_2(tmp_path):
    repo, sha = _units_repo(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n", encoding="utf-8")
    cases = [
        (repo, "0123456789abcdef0123456789abcdef01234567"),   # well-formed, not in the repository
        (repo, "no-such-ref"),
        (repo, sha + "^{tree}"),                              # an object, but not a commit
        (plain, "HEAD"),                                      # a directory that is not a repository
        (tmp_path / "missing", "HEAD"),                       # a path that does not exist
    ]

    for where, ref in cases:
        done = _cli(where, "--sha", ref)

        assert done.returncode == 2, (where, ref, done.stderr)
        assert done.stderr.startswith("review_units.py: "), done.stderr
        assert done.stdout == ""


def test_help_exits_0_and_missing_sha_exits_2(tmp_path):
    for flag in ("--help", "-h"):
        helped = _cli(flag)

        assert helped.returncode == 0, helped.stderr
        for word in ("usage", "--sha", "--json", "EXIT: 0 = ok; 1 = failed; 2 = bad arguments"):
            assert word in helped.stdout, (flag, word)
    # --sha is required: with a repository and no --sha, and with nothing at all.
    for args in ((tmp_path,), ()):
        done = _cli(*args)

        assert done.returncode == 2, done.stderr
        assert "--sha" in done.stderr or "repo" in done.stderr
        assert done.stdout == ""


def test_validate_payload_accepts_build_output(tmp_path):
    ru = _tool()
    repo, sha = _units_repo(tmp_path)
    notes = []

    payload = ru.build(repo, sha, warn=notes.append)

    assert list(payload) == FIRST_KEYS and payload["sha"] == sha
    assert payload["seed"] == int(sha[:8], 16)
    assert ru.validate_payload(payload) is None
    assert ru.validate_payload(json.loads(json.dumps(payload))) is None
    assert notes == []
    # A seed passed to build is for tests: the payload carries it and is valid for that seed only.
    one, two = ru.build(repo, sha, seed=1), ru.build(repo, sha, seed=2)
    assert (one["seed"], two["seed"]) == (1, 2)
    assert one["tier_b_sample"] != two["tier_b_sample"]
    assert ru.validate_payload(one, seed=1) is None
    with pytest.raises(ValueError):
        ru.validate_payload(one)


def test_validate_payload_lists_every_problem(tmp_path):
    ru = _tool()
    repo, sha = _units_repo(tmp_path)
    good = ru.build(repo, sha)
    assert list(good) == FIRST_KEYS

    def broken(mutate):
        copy = json.loads(json.dumps(good))
        mutate(copy)
        with pytest.raises(ValueError) as caught:
            ru.validate_payload(copy)
        return str(caught.value)

    def lines_plus_one(p): p["tier_a"][0]["lines"] += 1
    def gap(p): p["tier_a"][5]["files"][0]["start"] += 1
    def duplicate_id(p): p["tier_a"][1]["unit"] = "A01"
    def over_cap(p): p["tier_a"][4]["files"][0]["end"] += 1; p["tier_a"][4]["lines"] += 1
    def shared_path(p): p["tier_b_sample"][0]["files"][0]["path"] = "install.sh"
    def extra_key(p): p["extra"] = 1
    def reordered(p): p["tier_a"], p["tier_b_sample"] = p["tier_b_sample"], p["tier_a"]

    assert "seed" in broken(lambda p: p.update(seed=p["seed"] + 1))
    assert "sha" in broken(lambda p: p.update(sha=p["sha"][:39]))
    assert "schema" in broken(lambda p: p.update(schema="readiness-units/v2"))
    assert "lines" in broken(lines_plus_one)
    assert "gap" in broken(gap)
    assert "unit ids" in broken(duplicate_id)
    assert "3000" in broken(over_cap)
    assert "both tiers" in broken(shared_path)
    assert "keys" in broken(extra_key)
    assert "unit ids" in broken(reordered)

    def two_faults(p):
        p["seed"] += 1
        p["tier_a"][0]["lines"] += 1
    message = broken(two_faults)
    assert "seed" in message and "lines" in message   # every problem, not the first only


# ---- T11: the committed docs/launch/review-units.json for the frozen commit --------------------

def _committed():
    """The committed payload. Existence is asserted first, so a missing file is a failed assertion."""
    assert COMMITTED.exists(), "docs/launch/review-units.json is missing: %s" % COMMITTED
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_review_units_is_structurally_valid():
    # No git history is read here: this is what a shallow checkout (CI) can prove about the file.
    ru = _tool()
    payload = _committed()

    assert ru.validate_payload(payload) is None
    assert list(payload) == FIRST_KEYS
    assert payload["schema"] == "readiness-units/v1"
    assert len(payload["sha"]) == 40 and set(payload["sha"]) <= set("0123456789abcdef")
    assert payload["sha"] == FROZEN and payload["sha"][:12] == "a5c615062313"
    assert payload["seed"] == int(payload["sha"][:8], 16) == 2781222150
    sampled = sum(unit["lines"] for unit in payload["tier_b_sample"])
    population = payload["tier_b_population_lines"]
    assert population * 15 <= sampled * 100 <= population * 25
    for tier, prefix in (("tier_a", "A"), ("tier_b_sample", "B")):
        ids = [unit["unit"] for unit in payload[tier]]
        assert ids == ["%s%02d" % (prefix, number) for number in range(1, len(ids) + 1)]
        assert max(unit["lines"] for unit in payload[tier]) <= 3000
    # Tier A is in full: every one of the 22 named files is covered, and so is hooks/.
    covered = {entry["path"] for unit in payload["tier_a"] for entry in unit["files"]}
    assert set(ru.TIER_A_FILES) <= covered
    assert any(path.startswith("hooks/") for path in covered)


def _seed_off_by_one(p):
    p["seed"] += 1


def _lines_plus_one(p):
    p["tier_a"][0]["lines"] += 1


def _gap_in_a_files_ranges(p):
    for unit in p["tier_a"]:
        for entry in unit["files"]:
            if entry["start"] > 1:
                entry["start"] += 1
                unit["lines"] -= 1
                return
    raise AssertionError("no file of the committed JSON is split, so there is no range to open a gap in")


def _duplicate_id(p):
    p["tier_a"][1]["unit"] = p["tier_a"][0]["unit"]


def _unit_of_3001(p):
    unit = max(p["tier_a"], key=lambda item: item["lines"])
    unit["files"][-1]["end"] += 3001 - unit["lines"]
    unit["lines"] = 3001


def _short_sha(p):
    p["sha"] = p["sha"][:39]


def _extra_top_level_key(p):
    p["extra"] = 1


CORRUPTIONS = {
    "seed-off-by-one": (_seed_off_by_one, "seed is"),
    "lines-plus-one": (_lines_plus_one, "ranges add up"),
    "gap-in-a-files-ranges": (_gap_in_a_files_ranges, "gap or an overlap"),
    "duplicate-id": (_duplicate_id, "unit ids"),
    "unit-of-3001": (_unit_of_3001, "3000-line cap"),
    "39-char-sha": (_short_sha, "sha is not 40"),
    "extra-top-level-key": (_extra_top_level_key, "top-level keys"),
}


@pytest.mark.parametrize("name", ["seed-off-by-one", "lines-plus-one", "gap-in-a-files-ranges", "duplicate-id",
                                  "unit-of-3001", "39-char-sha", "extra-top-level-key"])
def test_structural_validator_rejects_corruptions(name):
    ru = _tool()
    good = _committed()
    assert ru.validate_payload(good) is None   # the copy is sound before it is corrupted
    copy = json.loads(json.dumps(good))
    mutate, expected = CORRUPTIONS[name]
    mutate(copy)

    with pytest.raises(ValueError) as caught:
        ru.validate_payload(copy)

    assert expected in str(caught.value), str(caught.value)


def test_frozen_sha_regeneration_matches_committed_json(tmp_path):
    # Needs the frozen commit itself, which a shallow checkout (CI) does not have: skipped there,
    # while the structural tests above still run. It writes its output under tmp_path, NEVER to
    # docs/launch/review-units.json: that would rewrite a tracked file during the suite.
    here = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", FROZEN + "^{commit}"], capture_output=True)
    if here.returncode != 0:
        pytest.skip("the frozen commit %s is not in this clone (a shallow checkout)" % FROZEN[:12])
    assert COMMITTED.exists(), "docs/launch/review-units.json is missing: %s" % COMMITTED
    target = tmp_path / "ru.json"

    done = subprocess.run([sys.executable, str(TOOL), str(ROOT), "--sha", FROZEN[:12], "--json", str(target)],
                          text=True, capture_output=True, timeout=120)

    assert done.returncode == 0, done.stderr
    assert target.exists(), "the tool wrote no file: %s" % done.stdout
    assert target.read_bytes() == COMMITTED.read_bytes()
    assert len(done.stdout.splitlines()) == 1


def test_doc_quotes_the_numbers_of_the_pinned_units():
    # The protocol page quotes the numbers of the committed JSON; they must not drift from it.
    assert DOC.exists(), "the protocol page is missing: %s" % DOC
    payload = _committed()
    text = DOC.read_text(encoding="utf-8")
    sample = payload["tier_b_sample"]
    sampled = sum(unit["lines"] for unit in sample)
    population = payload["tier_b_population_lines"]
    wanted = ["%d units" % len(payload["tier_a"]),
              "{:,} lines".format(sum(unit["lines"] for unit in payload["tier_a"])),
              "{:,}".format(max(unit["lines"] for unit in payload["tier_a"])),
              "%d files" % len({entry["path"] for unit in sample for entry in unit["files"]}),
              "{:,}".format(sampled), "{:,} lines".format(population),
              "%.2f%%" % (sampled * 100.0 / population)]
    missing = [item for item in wanted if item not in text]
    assert not missing, "the page does not quote the committed numbers: %s" % ", ".join(missing)
