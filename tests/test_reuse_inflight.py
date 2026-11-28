"""#2283: `reuse.py inflight` searches the changed files of other goals' unmerged remote branches for the goal's
recorded research queries, lists what those branches declare as new in their research dossiers and plans, and is
run by `check-research --at-research`.

Real temporary git repositories (a bare remote and a clone) back the behaviour tests. An injected
`run(argv, timeout=...)` backs the ones that must see the exact git command or force a failure, and an injected
clock or `now` backs the time ones. The verb never fetches: only the fixtures fetch. Every empty-case test asserts
the whole output, so an extra or missing line is red."""
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_reuse import (_project, _dossier as _full_dossier, _cli, _row, HEADER, SEPARATOR, QUERIES,  # noqa: E402
                        ROWS, _reuse, REUSE)
from test_reuse_rerun import _spy, _proc, _LOCATION  # noqa: E402

GOAL = "2283"
NEEDLE_LINE = "    return 'needle'"
NEW_FILE = "def f():\n" + NEEDLE_LINE + "\n"
SUMMARY = re.compile(r"inflight: searched (\d+) of (\d+) goal branch\(es\) \(cap (\d+)\); (\d+) hit\(s\); "
                     r"(\d+) declared addition\(s\); remote refs last fetched (.+)")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
NEW_ROW = _row("RU-1", "a brand new capability", "new", "—", "no hit for either query")
REUSE_ROW = _row("RU-2", "an old capability", "reuse", "src/app.py:3", "the one resolver every gate uses")


def _g(cwd, *args, env=None, check=True):
    base = {k: v for k, v in os.environ.items() if k not in _LOCATION}
    base.update(env or {})
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
                           "-C", str(cwd), *args], check=check, capture_output=True, text=True, env=base).stdout.strip()


def _doc(rows, queries=()):
    """A `## Reuse` section the way a dossier or plan carries it: the given query lines, then a table."""
    return "# Research: x\n\n## Reuse\n**Source:** grep\n" + "\n".join(queries) + "\n\n" + "\n".join(
        [HEADER, SEPARATOR] + list(rows)) + "\n"


def _recorded(queries):
    return ["- pattern: `%s` paths: `%s`" % q for q in queries]


def _config(w, **work):
    (w.proj / ".sdlc" / "config.json").write_text(json.dumps({"work": work}), encoding="utf-8")


def _world(tmp, queries=(("needle", "."),), record=True, dossier=True, goal=GOAL):
    """A bare remote and a clone `proj` on `main` (src/base.py holds the needle), the remote's HEAD set, a recorded
    dossier for the goal (untracked under proj/.sdlc) and a work record whose branch is sdlc/<goal>."""
    rem, proj = tmp / "remote.git", tmp / "proj"
    rem.mkdir()
    proj.mkdir()
    _g(rem, "init", "-q", "--bare", "-b", "main")
    _g(proj, "init", "-q", "-b", "main")
    _g(proj, "remote", "add", "origin", str(rem))
    (proj / "src").mkdir()
    (proj / "src" / "base.py").write_text("needle in main\n", encoding="utf-8")
    _g(proj, "add", "src/base.py")
    _g(proj, "commit", "-qm", "base")
    _g(proj, "push", "-q", "-u", "origin", "main")
    _g(proj, "fetch", "-q", "origin")
    _g(proj, "remote", "set-head", "origin", "main")
    w = types.SimpleNamespace(tmp=tmp, proj=proj, rem=rem, sdlc=str(proj / ".sdlc"), m=_reuse(), goal=goal)
    (proj / ".sdlc" / "research").mkdir(parents=True)
    if dossier:
        (proj / ".sdlc" / "research" / (goal + ".md")).write_text(
            "# Research: x\n\n## Reuse\n**Source:** grep\n**Queries:**\n" + "\n".join(_recorded(queries)) + "\n",
            encoding="utf-8")
    if record:
        (proj / ".sdlc" / "state" / "work").mkdir(parents=True)
        (proj / ".sdlc" / "state" / "work" / (goal + ".json")).write_text(
            json.dumps({"remote": "origin", "base": "main", "branch": "sdlc/" + goal}), encoding="utf-8")
    return w


def _push_branch(w, name, files=None, delete=(), date="2021-01-01T00:00:00Z", keep_local=False):
    """Create branch `name` from `main` with `files` ({path: str|bytes}), push it, fetch, drop the local branch so
    only the remote-tracking ref remains (the shape another goal's branch has on this machine)."""
    proj = w.proj
    _g(proj, "checkout", "-q", "-b", name, "main")
    paths = []
    for path, content in (files or {}).items():
        target = proj / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        paths.append(path)
    if paths:
        _g(proj, "add", "-f", "--", *paths)
    for path in delete:
        _g(proj, "rm", "-q", "--", path)
    env = {"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    _g(proj, "commit", "-q", "--allow-empty", "-m", "work on " + name, env=env)
    _g(proj, "push", "-q", "origin", name)
    if not keep_local:
        _g(proj, "checkout", "-q", "main")
        _g(proj, "branch", "-q", "-D", name)
    _g(proj, "fetch", "-q", "origin")
    return _g(proj, "rev-parse", "refs/remotes/origin/" + name)


def _inf(w, **kw):
    return w.m.inflight(w.sdlc, w.goal, **kw)


def _parse(lines):
    """(body, (k, n, cap, h, d, age)); asserts the last line is the summary and that `h` is the number of printed
    INFLIGHT lines, the one definition of a hit."""
    match = SUMMARY.fullmatch(lines[-1])
    assert match, lines
    k, n, cap, h, d = (int(match.group(i)) for i in range(1, 6))
    body = lines[:-1]
    assert h == len([line for line in body if line.startswith("INFLIGHT ")]), lines
    assert d == len([line for line in body if line.startswith("DECLARED ")]), lines
    return body, (k, n, cap, h, d, match.group(6))


def _subcommand(argv):
    i = 1
    while i < len(argv):
        if argv[i] == "-C":
            i += 2
        elif argv[i].startswith("-"):
            i += 1
        else:
            return argv[i]
    return None


def _grep_line(branch, q, path, line, text=NEEDLE_LINE):
    return "INFLIGHT %s Q%d %s:%d: %s" % (branch, q, path, line, text)


# ------------------------------------------------------------------------------------------- the empty cases

def test_inflight_with_no_goal_branches_says_so(tmp_path):
    w = _world(tmp_path)
    one = ["inflight: no remote goal branches under origin/sdlc/"]
    assert _inf(w) == one
    assert w.m.inflight(w.sdlc, "5") == one                      # a legacy goal (no dossier) gets the same one line
    _push_branch(w, "sdlc/" + GOAL, {"src/own.py": NEW_FILE})            # only the goal's own branch: nothing else
    assert _inf(w) == one
    proc = _cli(w.tmp, "inflight", w.sdlc, GOAL)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, one[0] + "\n", "")


def test_inflight_for_a_legacy_goal_says_so(tmp_path):
    """Branches exist and the goal has no recorded queries: the no-queries line, then the summary, no hit."""
    w = _world(tmp_path, dossier=False)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    lines = _inf(w)
    body, (k, n, cap, h, d, age) = _parse(lines)
    assert body == ["inflight: no recorded queries for goal " + GOAL]
    assert (k, n, h, d) == (1, 1, 0, 0)
    proc = _cli(w.tmp, "inflight", w.sdlc, GOAL)
    assert proc.returncode == 0 and proc.stdout.splitlines()[0] == body[0] and proc.stderr == ""


def test_inflight_with_branches_and_no_queries_lists_declared_additions_and_says_so(tmp_path):
    """The issue's two print-only rules conflict here; the declared pass still runs (the plan's D15)."""
    w = _world(tmp_path, dossier=False)
    _push_branch(w, "sdlc/9", {".sdlc/plans/9.md": _doc([NEW_ROW]), "src/new.py": NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == ["inflight: no recorded queries for goal " + GOAL,
                    "DECLARED sdlc/9 RU-1 new: a brand new capability"]
    assert (k, n, h, d) == (1, 1, 0, 1)


def test_inflight_a_goal_with_only_bad_queries_is_not_a_no_queries_goal(tmp_path):
    w = _world(tmp_path, queries=())
    (w.proj / ".sdlc" / "research" / (GOAL + ".md")).write_text(
        "# Research: x\n\n## Reuse\n**Source:** grep\n**Queries:**\n- not a query\n", encoding="utf-8")
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == ["inflight: Q1 skipped: not of the form - pattern: `<regex>` paths: `<path> [<path> ...]`"]
    assert (k, n, h) == (1, 1, 0)


# ----------------------------------------------------------------------------------------------- hits, scope

def test_inflight_finds_a_match_on_another_goal_branch(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == [_grep_line("sdlc/9", 1, "src/new.py", 2)]
    assert (k, n, h) == (1, 1, 1)
    proc = _cli(w.tmp, "inflight", w.sdlc, GOAL)
    assert proc.returncode == 0 and _grep_line("sdlc/9", 1, "src/new.py", 2) in proc.stdout.splitlines()


def test_inflight_searches_only_files_the_branch_changed(tmp_path):
    """main holds `needle in main` in src/base.py. The branch leaves it alone, and main then moves on, so a
    two-dot diff would list src/base.py as changed by the branch and report main's line."""
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    (w.proj / "src" / "base.py").write_text("needle in main\nmore\n", encoding="utf-8")
    _g(w.proj, "commit", "-qam", "main moves on")
    _g(w.proj, "push", "-q", "origin", "main")
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == [_grep_line("sdlc/9", 1, "src/new.py", 2)]
    assert not any("base.py" in line for line in body)


def test_inflight_does_not_grep_anything_under_dot_sdlc(tmp_path):
    w = _world(tmp_path)
    prose = "the other goal's prose about needle\n"
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": prose, ".sdlc/plans/9.md": prose,
                               ".sdlc/other/notes.md": prose, "src/x.py": "needle\n"})
    body, _ = _parse(_inf(w))
    assert body == ["INFLIGHT sdlc/9 Q1 src/x.py:1: needle"]


def test_inflight_a_branch_that_changed_only_a_dossier_or_plan_is_not_grepped(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": _doc([NEW_ROW]), ".sdlc/plans/9.md": "needle\n"})
    run, calls = _spy(w.m)
    body, (k, n, cap, h, d, age) = _parse(_inf(w, run=run))
    assert body == ["DECLARED sdlc/9 RU-1 new: a brand new capability"]
    assert (k, n, h, d) == (1, 1, 0, 1)
    assert not any(_subcommand(argv) == "grep" for argv, _ in calls)    # a grep with no paths searches the tree


def test_inflight_searches_a_non_ascii_changed_file(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"é.txt": "needle\n"})
    assert _parse(_inf(w))[0] == ["INFLIGHT sdlc/9 Q1 é.txt:1: needle"]


def test_inflight_a_deleted_dossier_prints_no_could_not_be_sized_line(tmp_path):
    w = _world(tmp_path)
    (w.proj / ".sdlc" / "research" / "5.md").write_text(_doc([NEW_ROW]), encoding="utf-8")
    _g(w.proj, "add", "-f", ".sdlc/research/5.md")
    _g(w.proj, "commit", "-qm", "dossier on main")
    _g(w.proj, "push", "-q", "origin", "main")
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE}, delete=[".sdlc/research/5.md"])
    body, _ = _parse(_inf(w))
    assert body == [_grep_line("sdlc/9", 1, "src/new.py", 2)]


def test_inflight_a_control_character_in_a_branch_or_file_name_prints_on_one_line(tmp_path):
    """A control character cannot be in a real branch name, so that half runs through an injected `run`."""
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE, "a\nb.py": "needle\n"})
    body, _ = _parse(_inf(w))
    assert body == ["inflight: sdlc/9 skipped 1 file name(s) with control characters",
                    _grep_line("sdlc/9", 1, "src/new.py", 2)]

    def hostile(argv, timeout):
        if _subcommand(argv) == "for-each-ref":
            return _proc(argv, 0, b"refs/remotes/origin/sdlc/5\x1b[31mX\n")
    run, _ = _spy(w.m, hostile)
    lines = _inf(w, run=run)
    assert not any(CONTROL.search(line) for line in lines), lines
    assert lines[0] == "inflight: sdlc/5 [31mX does not resolve"
    assert lines[-1].startswith("inflight: searched 0 of 1 goal branch(es)")


def test_inflight_a_branch_name_starting_with_a_dash_is_never_an_option(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/--output=x", {"src/new.py": NEW_FILE})
    _push_branch(w, "sdlc/-n", {"src/other.py": NEW_FILE})
    run, calls = _spy(w.m)
    body, (k, n, cap, h, d, age) = _parse(_inf(w, run=run))
    assert (k, n, h) == (2, 2, 2)
    assert not (w.proj / "x").exists() and not (w.tmp / "x").exists()
    for argv, _ in calls:
        assert not any(a.startswith("-") and "output" in a for a in argv), argv


def test_inflight_a_local_branch_named_like_a_remote_ref_does_not_shadow_it(tmp_path):
    """`rev-parse --verify origin/sdlc/shadow` resolves the LOCAL branch of that name; the full refname does not."""
    w = _world(tmp_path)
    _push_branch(w, "sdlc/shadow", {"src/new.py": NEW_FILE})
    _g(w.proj, "checkout", "-q", "-b", "origin/sdlc/shadow", "main")
    (w.proj / "src" / "decoy.py").write_text("nothing\n", encoding="utf-8")
    _g(w.proj, "add", "src/decoy.py")
    _g(w.proj, "commit", "-qm", "decoy")
    _g(w.proj, "checkout", "-q", "main")
    assert _parse(_inf(w))[0] == [_grep_line("sdlc/shadow", 1, "src/new.py", 2)]


# ------------------------------------------------------------------------------------ own branch, prefix, base

def test_inflight_skips_the_goals_own_branch(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/" + GOAL, {"src/own.py": NEW_FILE})
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == [_grep_line("sdlc/9", 1, "src/new.py", 2)] and (k, n) == (1, 1)


def test_inflight_own_branch_name_comes_from_the_work_record(tmp_path):
    """The record's `branch` is the own branch even when it is not `<prefix><stem>`; the stem-named branch is then
    another branch. With no record, HEAD names it (the root checkout's HEAD is the base branch, so there it skips
    nothing: this clone is checked out on the goal branch)."""
    w = _world(tmp_path)
    (w.proj / ".sdlc" / "state" / "work" / (GOAL + ".json")).write_text(
        json.dumps({"remote": "origin", "base": "main", "branch": "sdlc/custom-name"}), encoding="utf-8")
    _push_branch(w, "sdlc/custom-name", {"src/own.py": NEW_FILE})
    _push_branch(w, "sdlc/" + GOAL, {"src/stem.py": NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == [_grep_line("sdlc/" + GOAL, 1, "src/stem.py", 2)] and (k, n) == (1, 1)


def test_inflight_own_branch_falls_back_to_head_when_there_is_no_record(tmp_path):
    w = _world(tmp_path, record=False)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    _push_branch(w, "sdlc/mine", {"src/own.py": NEW_FILE}, keep_local=True)      # stays checked out: HEAD
    assert _g(w.proj, "rev-parse", "--abbrev-ref", "HEAD") == "sdlc/mine"
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body[-1:] == [_grep_line("sdlc/9", 1, "src/new.py", 2)] and not any("sdlc/mine" in b for b in body)
    assert (k, n) == (1, 1)


def test_inflight_skips_the_prefix_stem_branch_when_there_is_no_record(tmp_path):
    w = _world(tmp_path, record=False)
    for name in ("sdlc/" + GOAL, "sdlc/%s-2" % GOAL, "sdlc/%s-notes" % GOAL, "sdlc/9"):
        _push_branch(w, name, {"src/%s.py" % name.split("/")[1]: NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert (k, n) == (2, 2)
    assert sorted(line.split()[1] for line in body if line.startswith("INFLIGHT ")) == ["sdlc/%s-notes" % GOAL, "sdlc/9"]


def test_inflight_own_branch_name_helper_agrees_with_the_work_record_path(tmp_path):
    work = _reuse()._load("work")
    m = _reuse()
    sdlc = tmp_path / ".sdlc"
    for goal in ("7", "7.md", "a/b", "..", "x y", "", "-x"):
        try:
            expected = work.record_path(str(sdlc), goal).name[:-len(".json")]
        except ValueError:
            expected = None
        assert m._goal_stem(str(sdlc), goal) == expected, goal


def test_inflight_reports_searched_of_total(tmp_path):
    """k and n differ here (one branch does not resolve), so a summary that prints `k of k` is red; the default cap
    is asserted unpatched, as the literal the issue gives."""
    w = _world(tmp_path)
    for name, date in (("a", "04"), ("b", "03"), ("c", "02"), ("d", "01")):
        _push_branch(w, "sdlc/" + name, {"src/%s.py" % name: NEW_FILE}, date="2021-01-%sT00:00:00Z" % date)

    def fail(argv, timeout):
        if _subcommand(argv) == "rev-parse" and "refs/remotes/origin/sdlc/c^{commit}" in argv:
            return _proc(argv, 1)
    run, _ = _spy(w.m, fail)
    lines = _inf(w, run=run)
    assert lines[-1].startswith("inflight: searched 3 of 4 goal branch(es) (cap %d); 3 hit(s); 0 declared " % w.m.MAX_BRANCHES)
    assert w.m.MAX_BRANCHES == 50 and "(cap 50)" in lines[-1]          # the default, unpatched


def test_inflight_caps_at_50_branches_newest_first(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_BRANCHES", 2)
    _push_branch(w, "sdlc/b", {"src/b.py": NEW_FILE}, date="2021-01-03T00:00:00Z")
    _push_branch(w, "sdlc/c", {"src/c.py": NEW_FILE}, date="2021-01-01T00:00:00Z")
    _push_branch(w, "sdlc/a", {"src/a.py": NEW_FILE}, date="2021-01-02T00:00:00Z")
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert [line.split()[1] for line in body] == ["sdlc/b", "sdlc/a"]            # the oldest is not searched
    assert (k, n, cap) == (2, 3, 2)


def test_inflight_caps_changed_files_and_says_so(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_CHANGED_FILES", 1)
    _push_branch(w, "sdlc/9", {"src/b.py": NEW_FILE, "src/a.py": NEW_FILE})
    body, _ = _parse(_inf(w))
    assert body == ["inflight: sdlc/9 changed 2 files; searched the first " + str(w.m.MAX_CHANGED_FILES),
                    _grep_line("sdlc/9", 1, "src/a.py", 2)]


@pytest.mark.parametrize("prefix, found", [("sdlc", ["sdlc/1", "sdlcx/4"]), ("goal-", ["goal-5"])])
def test_inflight_a_prefix_is_a_plain_name_start(tmp_path, prefix, found):
    """`work.py` builds prefix + stem, so `goal-` is a legal prefix and `sdlc` (no slash) a name start, as it is
    for work.py: both are filtered in Python, never in the for-each-ref pattern."""
    w = _world(tmp_path)
    _config(w, branch_prefix=prefix)
    for name in ("sdlc/1", "sdlcx/4", "goal-5", "wip/6"):
        _push_branch(w, name, {"src/%s.py" % name.replace("/", "_"): NEW_FILE})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert sorted(line.split()[1] for line in body) == sorted(found) and (k, n) == (len(found), len(found))


def test_inflight_no_branch_line_names_the_prefix(tmp_path):
    w = _world(tmp_path)
    _config(w, branch_prefix="goal-")
    assert _inf(w) == ["inflight: no remote goal branches under origin/goal-"]


def test_inflight_an_empty_prefix_is_refused_in_one_line(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    _config(w, branch_prefix="")
    assert _inf(w) == ["inflight: branch_prefix is not usable"]


@pytest.mark.parametrize("work, remote, line", [
    ({"branch_prefix": "--upload-pack=x"}, None, "inflight: branch_prefix is not usable"),
    ({"remote": "a b"}, None, "inflight: remote is not usable"),
    ({}, "--output=x", "inflight: remote is not usable"),
])
def test_inflight_an_unsafe_remote_or_prefix_is_refused_in_one_line(tmp_path, work, remote, line):
    w = _world(tmp_path)
    _config(w, **work)
    assert _inf(w, remote=remote) == [line]


def test_inflight_an_empty_remote_is_treated_like_an_absent_one(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    _config(w, remote="")
    assert _parse(_inf(w))[0] == [_grep_line("sdlc/9", 1, "src/new.py", 2)]
    assert _parse(_inf(w, remote=""))[0] == [_grep_line("sdlc/9", 1, "src/new.py", 2)]


def test_inflight_an_unresolvable_base_is_one_line_then_the_summary(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    lines = _inf(w, base="nosuch")
    assert lines[0] == "inflight: base origin/nosuch does not resolve; no branch was searched"
    body, (k, n, cap, h, d, age) = _parse(lines)
    assert len(body) == 1 and (k, n, h) == (0, 1, 0)


def test_inflight_the_base_note_does_not_read_rerun_and_is_absent_from_the_one_line_case(tmp_path):
    w = _world(tmp_path, record=False)
    assert _inf(w) == ["inflight: no remote goal branches under origin/sdlc/"]
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    lines = _inf(w)
    assert lines[0] == "inflight: no work record for goal %s; using origin/main" % GOAL
    assert not any(line.startswith("rerun:") for line in lines)


def test_inflight_says_when_a_checkpoint_prefix_is_configured(tmp_path):
    w = _world(tmp_path)
    _config(w, checkpoint_prefix="wip/")
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    lines = _inf(w)
    assert lines[-2] == "inflight: work.checkpoint_prefix wip/ is set; branches under it are not searched"
    assert SUMMARY.fullmatch(lines[-1])


def test_inflight_the_remote_note_is_printed_when_the_base_is_on_another_remote(tmp_path):
    """Injected `run` only: there is no real second remote here, so this is untested against one."""
    w = _world(tmp_path)
    sha = _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})

    def fake(argv, timeout):
        if _subcommand(argv) == "for-each-ref":
            assert argv[-1] == "refs/remotes/upstream/"
            return _proc(argv, 0, b"refs/remotes/upstream/sdlc/9\n")
        if _subcommand(argv) == "rev-parse" and "refs/remotes/upstream/sdlc/9^{commit}" in argv:
            return _proc(argv, 0, (sha + "\n").encode())
    run, _ = _spy(w.m, fake)
    lines = _inf(w, remote="upstream", run=run)
    assert lines[0] == "inflight: base origin/main is not on remote upstream"
    assert _parse(lines)[0][1:] == [_grep_line("sdlc/9", 1, "src/new.py", 2)]


def test_inflight_with_base_and_a_different_remote_does_not_double_prefix(tmp_path, monkeypatch):
    w = _world(tmp_path)
    sha = _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    main_sha = _g(w.proj, "rev-parse", "refs/remotes/origin/main")
    monkeypatch.setattr(w.m, "base_ref", lambda *a, **k: pytest.fail("base_ref called for --base with --remote"))

    def fake(argv, timeout):
        if _subcommand(argv) == "for-each-ref":
            return _proc(argv, 0, b"refs/remotes/upstream/sdlc/9\n")
        if _subcommand(argv) == "rev-parse" and "upstream/main^{commit}" in argv:
            return _proc(argv, 0, (main_sha + "\n").encode())
        if _subcommand(argv) == "rev-parse" and "refs/remotes/upstream/sdlc/9^{commit}" in argv:
            return _proc(argv, 0, (sha + "\n").encode())
    run, calls = _spy(w.m, fake)
    lines = _inf(w, remote="upstream", base="main", run=run)
    assert _parse(lines)[0] == [_grep_line("sdlc/9", 1, "src/new.py", 2)]
    assert not any("origin/upstream" in a for argv, _ in calls for a in argv)


# ------------------------------------------------------------------------------------------ queries and errors

def test_inflight_numbers_queries_the_way_rerun_does(tmp_path):
    w = _world(tmp_path, queries=())
    (w.proj / ".sdlc" / "research" / (GOAL + ".md")).write_text(
        "# Research: x\n\n## Reuse\n**Source:** grep\n**Queries:**\n- pattern: `zzz` paths: `.`\n- broken\n"
        "- pattern: `needle` paths: `.`\n", encoding="utf-8")
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    body, _ = _parse(_inf(w))
    assert body[0].startswith("inflight: Q2 skipped: ")
    assert body[1:] == [_grep_line("sdlc/9", 3, "src/new.py", 2)]
    assert w.m.rerun(w.sdlc, GOAL, ref="refs/remotes/origin/sdlc/9")[0].startswith("ERROR Q2")   # rerun's Q2 too


def test_inflight_a_control_character_in_a_recorded_pattern_is_one_line_and_exit_zero(tmp_path):
    w = _world(tmp_path, queries=())
    long = "x" * 201
    (w.proj / ".sdlc" / "research" / (GOAL + ".md")).write_text(
        "# Research: x\n\n## Reuse\n**Source:** grep\n**Queries:**\n- pattern: `a\x00b` paths: `.`\n"
        "- pattern: `%s` paths: `.`\n- pattern: `needle` paths: `.`\n" % long, encoding="utf-8")
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    run, calls = _spy(w.m)
    body, _ = _parse(_inf(w, run=run))
    assert body == ["inflight: Q1 skipped: pattern contains a control character",
                    "inflight: Q2 skipped: pattern is 201 characters; 1 to 200 allowed",
                    _grep_line("sdlc/9", 3, "src/new.py", 2)]
    assert not any("\x00" in a for argv, _ in calls for a in argv)
    proc = _cli(w.tmp, "inflight", w.sdlc, GOAL)
    assert proc.returncode == 0 and "Traceback" not in proc.stderr and "Q1 skipped" in proc.stdout


def test_inflight_says_when_more_queries_are_recorded_than_it_runs(tmp_path):
    w = _world(tmp_path, queries=[("zzz%d" % n, ".") for n in range(26)])
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    body, _ = _parse(_inf(w))
    assert body == ["inflight: only the first 25 of 26 queries run"]


def test_inflight_a_diff_failure_skips_the_branch_and_continues(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/a", {"src/a.py": NEW_FILE}, date="2021-01-03T00:00:00Z")
    _push_branch(w, "sdlc/b", {"src/b.py": NEW_FILE}, date="2021-01-02T00:00:00Z")
    _push_branch(w, "sdlc/c", {"src/c.py": NEW_FILE}, date="2021-01-01T00:00:00Z")      # after the failing one
    sha_b = _g(w.proj, "rev-parse", "refs/remotes/origin/sdlc/b")

    def fail(argv, timeout):
        if _subcommand(argv) == "diff" and any(a.endswith("..." + sha_b) for a in argv):
            return _proc(argv, 128, b"", b"fatal: bad object\nmore")
    run, _ = _spy(w.m, fail)
    body, (k, n, cap, h, d, age) = _parse(_inf(w, run=run))
    assert body == [_grep_line("sdlc/a", 1, "src/a.py", 2), "inflight: sdlc/b diff failed (fatal: bad object)",
                    _grep_line("sdlc/c", 1, "src/c.py", 2)]
    assert (k, n) == (2, 3)


def test_inflight_a_value_error_from_git_does_not_escape(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/a", {"src/a.py": NEW_FILE})

    def boom(argv, timeout):
        if _subcommand(argv) == "diff":
            return ValueError("embedded null byte")
    run, _ = _spy(w.m, boom)
    body, (k, n, *_rest) = _parse(_inf(w, run=run))
    assert body == ["inflight: sdlc/a diff failed (ValueError)"] and (k, n) == (0, 1)


def test_inflight_a_value_error_from_git_grep_does_not_escape(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/a", {"src/a.py": NEW_FILE})

    def boom(argv, timeout):
        if _subcommand(argv) == "grep":
            return ValueError("embedded null byte")
    run, _ = _spy(w.m, boom)
    body, (k, n, *_rest) = _parse(_inf(w, run=run))
    assert body == ["inflight: sdlc/a Q1 grep failed (ValueError)"] and (k, n) == (1, 1)


def test_inflight_counts_only_branches_whose_resolve_and_diff_succeeded(tmp_path):
    w = _world(tmp_path)
    for name, date in (("a", "04"), ("b", "03"), ("c", "02"), ("d", "01")):
        _push_branch(w, "sdlc/" + name, {"src/%s.py" % name: NEW_FILE}, date="2021-01-%sT00:00:00Z" % date)
    sha_c = _g(w.proj, "rev-parse", "refs/remotes/origin/sdlc/c")

    def fail(argv, timeout):
        if _subcommand(argv) == "rev-parse" and "refs/remotes/origin/sdlc/b^{commit}" in argv:
            return _proc(argv, 1)
        if _subcommand(argv) == "diff" and any(a.endswith("..." + sha_c) for a in argv):
            return _proc(argv, 1, b"", b"fatal")
    run, _ = _spy(w.m, fail)
    body, (k, n, *_rest) = _parse(_inf(w, run=run))
    assert (k, n) == (2, 4)
    assert "inflight: sdlc/b does not resolve" in body and "inflight: sdlc/c diff failed (fatal)" in body


def test_inflight_a_failed_branch_listing_is_one_line_then_the_summary(tmp_path):
    w = _world(tmp_path)

    def fail(argv, timeout):
        if _subcommand(argv) == "for-each-ref":
            return _proc(argv, 128, b"", b"fatal: broken")
    run, _ = _spy(w.m, fail)
    body, (k, n, *_rest) = _parse(_inf(w, run=run))
    assert body == ["inflight: could not list remote goal branches (fatal: broken)"] and (k, n) == (0, 0)


# ------------------------------------------------------------------------------------------- declared additions

def test_inflight_lists_what_another_branch_declares_as_new(tmp_path):
    w = _world(tmp_path)
    ext = _row("RU-3", "widen it", "extend", "src/app.py:3", "it already reads the file")
    dep = _row("RU-4", "call the registry", "dependency-new", "pypi:requests==2.32.3", "verified: pinned on the registry")
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": _doc([NEW_ROW, REUSE_ROW, ext, dep])})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == ["DECLARED sdlc/9 RU-1 new: a brand new capability", "DECLARED sdlc/9 RU-3 extend: widen it",
                    "DECLARED sdlc/9 RU-4 dependency-new: call the registry"] and d == 3


def test_inflight_ignores_a_branch_dossier_without_reuse(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": "# Research: x\n\n## Goal\nnothing\n"})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body == [] and (k, n, d) == (1, 1, 0)


def test_inflight_lists_a_new_row_from_another_branchs_plan_table(tmp_path):
    w = _world(tmp_path)
    plan = ("# Plan\n\n## Reuse\n- RU-1: a bullet about a brand new thing\n- RU-2: another bullet\n\n"
            + "\n".join([HEADER, SEPARATOR, NEW_ROW]) + "\n")
    _push_branch(w, "sdlc/9", {".sdlc/plans/9.md": plan})
    assert _parse(_inf(w))[0] == ["DECLARED sdlc/9 RU-1 new: a brand new capability"]


def test_inflight_ignores_a_dossier_outside_the_research_and_plans_directories(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"docs/research/9.md": _doc([NEW_ROW]), ".sdlc/notes/9.md": _doc([NEW_ROW]),
                               ".sdlc/research/9.txt": _doc([NEW_ROW])})
    assert _parse(_inf(w))[0] == []


def test_inflight_a_dossier_outside_the_two_directories_is_not_read(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"docs/research/9.md": _doc([NEW_ROW]), "src/new.py": NEW_FILE})
    run, calls = _spy(w.m)
    _inf(w, run=run)
    assert not any(_subcommand(argv) in ("show", "cat-file") for argv, _ in calls)


def test_inflight_skips_an_oversized_dossier_on_another_branch(tmp_path):
    w = _world(tmp_path)
    head = _doc([NEW_ROW])
    exact = head + "x" * (w.m.MAX_BLOB_BYTES - len(head.encode()))
    assert len(exact.encode()) == w.m.MAX_BLOB_BYTES
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": exact + "x"}, date="2021-01-02T00:00:00Z")
    _push_branch(w, "sdlc/8", {".sdlc/research/8.md": exact}, date="2021-01-01T00:00:00Z")
    run, calls = _spy(w.m)
    body, _ = _parse(_inf(w, run=run))
    assert body == ["inflight: sdlc/9 .sdlc/research/9.md is too large to read",
                    "DECLARED sdlc/8 RU-1 new: a brand new capability"]      # exactly the limit is read
    shown = [argv[-1] for argv, _ in calls if _subcommand(argv) == "show"]
    assert len(shown) == 1 and shown[0].endswith(":.sdlc/research/8.md")


def test_inflight_a_dossier_that_cannot_be_sized_is_a_different_line_from_too_large(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": _doc([NEW_ROW])})

    def fail(argv, timeout):
        if _subcommand(argv) == "cat-file":
            return _proc(argv, 128, b"", b"fatal")
    run, calls = _spy(w.m, fail)
    body, _ = _parse(_inf(w, run=run))
    assert body == ["inflight: sdlc/9 .sdlc/research/9.md could not be sized"]
    assert not any(_subcommand(argv) == "show" for argv, _ in calls)


def test_inflight_prints_quoted_text_on_one_line_cut_to_200_characters(tmp_path):
    w = _world(tmp_path)
    cap = "a" * 100 + "\t\x1b[31m" + "b" * 400
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": _doc([_row("RU-1", cap, "new", "—", "no hit for either")])})
    lines = _parse(_inf(w))[0]
    assert lines == ["DECLARED sdlc/9 RU-1 new: " + ("a" * 100 + "  [31m" + "b" * 400)[:200]]
    assert len(lines[0]) - len("DECLARED sdlc/9 RU-1 new: ") == 200 and not CONTROL.search(lines[0])


def test_inflight_replaces_line_separators_and_bidi_overrides_in_quoted_text(tmp_path):
    w = _world(tmp_path)
    assert w.m._one_line("a\u2028b\u2029c\u202ed\u2069e\x1bf\x85g") == "a b c d e f g"
    cap = "ab\u202ecd\u2066ef\u202bgh"
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": _doc([_row("RU-1", cap, "new", "—", "no hit for either")])})
    assert _parse(_inf(w))[0] == ["DECLARED sdlc/9 RU-1 new: ab cd ef gh"]


def test_inflight_declared_decisions_match_the_validators_decisions(tmp_path):
    m = _reuse()
    assert set(m.DECLARED_DECISIONS) == {"extend", "dependency-new", "new"}
    assert set(m.DECLARED_DECISIONS) <= set(m.DECISIONS)


def test_inflight_prints_at_most_the_per_file_cap_of_declared_rows_and_says_so(tmp_path):
    w = _world(tmp_path)
    rows = [_row("RU-%d" % n, "cap %d" % n, "new", "—", "no hit for either") for n in range(1, 15001)]
    text = _doc(rows)
    assert len(text.encode()) <= w.m.MAX_BLOB_BYTES
    _push_branch(w, "sdlc/9", {".sdlc/research/9.md": text})
    start = time.monotonic()
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert time.monotonic() - start < 30
    assert d == w.m.MAX_DECLARED_PER_FILE == 25
    assert body[-1] == "inflight: sdlc/9 .sdlc/research/9.md declares 15000 additions; printed the first 25"


def test_inflight_reads_at_most_the_per_branch_cap_of_plan_files_and_says_so(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_PLAN_FILES_PER_BRANCH", 2)
    _push_branch(w, "sdlc/9", {".sdlc/plans/%d.md" % n: _doc([_row("RU-%d" % n, "cap %d" % n, "new", "—",
                                                                     "no hit for either")]) for n in (1, 2, 3)})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert body[0] == "inflight: sdlc/9 carries 3 research or plan files; read the first 2"
    assert body[1:] == ["DECLARED sdlc/9 RU-1 new: cap 1", "DECLARED sdlc/9 RU-2 new: cap 2"]


def test_inflight_stops_appending_at_the_output_cap_and_still_prints_the_summary(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_OUTPUT_LINES", 3)
    _push_branch(w, "sdlc/9", {"src/many.py": "needle\n" * 10})
    lines = _inf(w)
    assert lines[:3] == ["INFLIGHT sdlc/9 Q1 src/many.py:%d: needle" % n for n in (1, 2, 3)]
    assert lines[3] == "inflight: output stopped at 3 lines"
    assert len(lines) == 5 and _parse(lines)[1][3] == 3


def test_inflight_truncates_a_query_at_the_hit_cap_and_says_so(tmp_path, monkeypatch):
    w = _world(tmp_path)
    monkeypatch.setattr(w.m, "MAX_HITS_PER_QUERY", 2)
    _push_branch(w, "sdlc/9", {"src/many.py": "needle\n" * 5})
    body, (k, n, cap, h, d, age) = _parse(_inf(w))
    assert h == 2 and body[-1] == "inflight: sdlc/9 Q1 truncated at 2 hits"


def test_inflight_stops_the_declared_pass_when_the_deadline_passes(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {".sdlc/plans/1.md": _doc([NEW_ROW]),
                               ".sdlc/plans/2.md": _doc([_row("RU-2", "second", "new", "—", "no hit at all")])})
    clock = [0.0]

    def jump(argv, timeout):
        if _subcommand(argv) == "show":
            clock[0] += 500

    run, _ = _spy(w.m, jump)
    body, (k, n, cap, h, d, age) = _parse(_inf(w, run=run, clock=lambda: clock[0]))
    assert body == ["DECLARED sdlc/9 RU-1 new: a brand new capability", "inflight: stopped after 120 seconds"]


# ---------------------------------------------------------------------------------------- limits, age, no network

@pytest.mark.parametrize("which", ["clock", "now"])
def test_inflight_stops_after_the_time_limit_and_says_so(tmp_path, which):
    """The jump is keyed to the first grep (the first branch's work), not to a call count."""
    w = _world(tmp_path)
    for name, date in (("a", "03"), ("b", "02"), ("c", "01")):
        _push_branch(w, "sdlc/" + name, {"src/%s.py" % name: NEW_FILE}, date="2021-01-%sT00:00:00Z" % date)
    t = [1000.0]
    jumped = []

    def jump(argv, timeout):
        if _subcommand(argv) == "grep" and not jumped:
            jumped.append(1)
            t[0] += 200

    run, _ = _spy(w.m, jump)
    lines = _inf(w, run=run, **{which: lambda: t[0]})
    body, (k, n, cap, h, d, age) = _parse(lines)
    assert body == [_grep_line("sdlc/a", 1, "src/a.py", 2), "inflight: stopped after %d seconds" % w.m.MAX_SECONDS]
    assert (k, n) == (1, 3)


@pytest.mark.parametrize("delta, text", [(5 * 60 + 10, "5 minute(s) ago"), (3 * 3600 + 5, "3 hour(s) ago"),
                                         (2 * 86400 + 5, "2 day(s) ago"), (10, "0 minute(s) ago")])
@pytest.mark.parametrize("plain", [False, True])
def test_inflight_prints_the_fetch_age(tmp_path, delta, text, plain):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    fetch_head = w.proj / ".git" / "FETCH_HEAD"
    os.utime(fetch_head, (1_700_000_000, 1_700_000_000))
    at = 1_700_000_000 + delta
    lines = _inf(w, now=float(at) if plain else (lambda: at))
    assert _parse(lines)[1][5] == text


def test_inflight_prints_the_age_of_a_linked_worktrees_own_fetch_head(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    wt = w.tmp / "wt"
    _g(w.proj, "worktree", "add", "-q", "-b", "sdlc/%s" % GOAL, str(wt), "main")
    (wt / ".sdlc" / "research").mkdir(parents=True)
    (wt / ".sdlc" / "research" / (GOAL + ".md")).write_text(
        (w.proj / ".sdlc" / "research" / (GOAL + ".md")).read_text(encoding="utf-8"), encoding="utf-8")
    os.utime(w.proj / ".git" / "FETCH_HEAD", (1_700_000_000, 1_700_000_000))
    own = pathlib.Path(_g(wt, "rev-parse", "--absolute-git-dir")) / "FETCH_HEAD"
    own.write_text("", encoding="utf-8")
    os.utime(own, (1_700_007_200, 1_700_007_200))
    lines = w.m.inflight(str(wt / ".sdlc"), GOAL, now=lambda: 1_700_007_200 + 3 * 3600)
    assert _parse(lines)[1][5] == "3 hour(s) ago"                    # the newer of the two, not the common one


def test_inflight_prints_never_when_nothing_was_fetched(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    (w.proj / ".git" / "FETCH_HEAD").unlink()
    assert _parse(_inf(w))[1][5] == "never"


def test_inflight_runs_no_network_command(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE, ".sdlc/plans/9.md": _doc([NEW_ROW])})
    run, calls = _spy(w.m)
    _inf(w, run=run)
    seen = {_subcommand(argv) for argv, _ in calls}
    assert {"for-each-ref", "diff", "grep"} <= seen                  # the record is not empty
    assert seen <= {"for-each-ref", "rev-parse", "diff", "grep", "cat-file", "show", "symbolic-ref"}, seen
    assert not seen & {"fetch", "pull", "ls-remote", "push", "clone"}


def _snapshot(root):
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(pathlib.Path(root).rglob("*"))
            if p.is_file()}


def test_inflight_writes_nothing(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE, ".sdlc/plans/9.md": _doc([NEW_ROW])})
    before = _snapshot(tmp_path)
    assert any(name.endswith("FETCH_HEAD") for name in before)
    time.sleep(0.05)
    assert _inf(w) and _snapshot(tmp_path) == before


def test_inflight_exit_is_zero_with_hits(tmp_path):
    w = _world(tmp_path)
    _push_branch(w, "sdlc/9", {"src/new.py": NEW_FILE})
    proc = _cli(w.tmp, "inflight", w.sdlc, GOAL)
    assert proc.returncode == 0 and any(line.startswith("INFLIGHT ") for line in proc.stdout.splitlines())


@pytest.mark.parametrize("args", [(), ("only-one",), ("a", "b", "c"), ("a", "b", "--bogus"), ("a", "b", "--remote"),
                                  ("a", "b", "--base", "x", "--base", "y"), ("a", "b", "--remote", "x", "--remote", "y")])
def test_inflight_usage_errors_exit_two(tmp_path, args):
    proc = _cli(tmp_path, "inflight", *args)
    assert (proc.returncode, proc.stdout) == (2, "") and proc.stderr.startswith("usage: reuse.py check-research ")
    assert "inflight" in proc.stderr.splitlines()[0] or "inflight" in proc.stderr


# ----------------------------------------------------------------------------- check-research --at-research

def _research_world(tmp, failing=False):
    """test_reuse's project (goal 7, a passing dossier) plus a bare remote holding another goal's branch whose
    src/other.py matches the dossier's `reuse` query."""
    sdlc, root, head = _project(tmp, git=True)
    rem = tmp / "remote.git"
    rem.mkdir()
    _g(rem, "init", "-q", "--bare", "-b", "main")
    _g(root, "remote", "add", "origin", str(rem))
    _g(root, "push", "-q", "-u", "origin", "main")
    _g(root, "checkout", "-q", "-b", "sdlc/9")
    (root / "src" / "other.py").write_text("reuse\n", encoding="utf-8")
    _g(root, "add", "src/other.py")
    _g(root, "commit", "-qm", "other goal")
    _g(root, "push", "-q", "origin", "sdlc/9")
    _g(root, "checkout", "-q", "main")
    _g(root, "branch", "-q", "-D", "sdlc/9")
    _g(root, "fetch", "-q", "origin")
    _g(root, "remote", "set-head", "origin", "main")
    kw = {"rows": ROWS[:5] + [_row("RU-6", decision="new", evidence="—", reason="no hit")]} if failing else {}
    (sdlc / "research" / "7.md").write_text(_full_dossier(searched_at=head, **kw), encoding="utf-8")
    return sdlc, root


HIT = "INFLIGHT sdlc/9 Q2 src/other.py:1: reuse"


def test_check_research_at_research_prints_inflight_lines_and_keeps_its_exit_code(tmp_path):
    """On the gesture the docs give: `check-research <sdlc_dir> <goal> --at-research`, as a real subprocess."""
    sdlc, _ = _research_world(tmp_path)
    proc = _cli(tmp_path, "check-research", sdlc, "7", "--at-research")
    out = proc.stdout.splitlines()
    assert (proc.returncode, proc.stderr) == (0, "")
    assert out[0].startswith("reuse.py: ## Reuse and ## Test design for goal 7 are well formed")
    assert out[1] == "In-flight work:" and HIT in out and SUMMARY.fullmatch(out[-1])

    other = tmp_path / "failing"
    other.mkdir()
    sdlc, _ = _research_world(other, failing=True)
    proc = _cli(other, "check-research", sdlc, "7", "--at-research")
    assert proc.returncode == 2 and "decision 'new' needs a reason" in proc.stderr
    assert proc.stdout.splitlines()[0] == "In-flight work:" and HIT in proc.stdout.splitlines()


def test_check_research_without_the_flag_does_not_run_inflight(tmp_path, monkeypatch, capsys):
    sdlc, _ = _research_world(tmp_path)
    m = _reuse()
    calls = []
    monkeypatch.setattr(m, "inflight", lambda *a, **k: calls.append(a) or [])
    seen = []
    real = m._run_git
    monkeypatch.setattr(m, "_run_git", lambda argv, timeout=10: seen.append(argv) or real(argv, timeout=timeout))
    assert m.main(["check-research", str(sdlc), "7"]) == 0
    assert "In-flight work:" not in capsys.readouterr().out
    assert calls == [] and not any(_subcommand(argv) == "for-each-ref" for argv in seen)
    proc = _cli(tmp_path, "check-research", sdlc, "7")
    assert "In-flight work:" not in proc.stdout and proc.returncode == 0


@pytest.mark.parametrize("failing, code", [(False, 0), (True, 2)])
def test_check_research_at_research_survives_an_inflight_failure(tmp_path, monkeypatch, capsys, failing, code):
    sdlc, _ = _research_world(tmp_path, failing=failing)
    m = _reuse()

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(m, "inflight", boom)
    assert m.main(["check-research", str(sdlc), "7", "--at-research"]) == code
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["In-flight work:", "inflight: could not run (RuntimeError)"]


def test_check_research_at_research_gives_a_legacy_goal_the_block_too(tmp_path):
    sdlc, _, _ = _project(tmp_path, git=True)
    proc = _cli(tmp_path, "check-research", sdlc, "7", "--at-research")
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["In-flight work:", "inflight: no remote goal branches under origin/sdlc/"]
