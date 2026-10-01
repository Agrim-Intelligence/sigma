"""tools/readiness/decide.py (#331): the launch GO/NO-GO checker.

Hermetic: every test passes --blockers-json, injects a fake `run` in place of `gh`, or puts a stub
`gh` first on PATH. No network. Fixture repositories are real git work trees with a local
`refs/remotes/origin/main`, because nothing is GO unless main can be verified through git.
"""
import copy
import datetime
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "readiness" / "decide.py"
RULE = ROOT / "docs" / "launch" / "decision-rule.md"
SHIPPED_CARD = ROOT / "docs" / "launch" / "scorecard.json"
ORIGIN = "https://example.invalid/acme/widget.git"
QUERY = "issues?state=open&labels=launch:blocker&per_page=100"


def _tool():
    spec = importlib.util.spec_from_file_location("readiness_decide_331", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dz = _tool()
BENCH = "docs/launch/evidence/benchmark-results.json"
PROOF = "docs/launch/evidence/proof.md"


def _passing_card():
    card = json.loads(SHIPPED_CARD.read_text(encoding="utf-8"))
    card["benchmark_results"] = BENCH
    for dim in card["dimensions"]:
        dim["score"] = 3
        dim["evidence"] = ["https://example.invalid/evidence/" + dim["id"]]
    return card


def _git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + os.devnull,
                    "-C", str(root), *args], check=True, capture_output=True)


def _git_repo(root, remotes=("origin",), commit=True, url=ORIGIN):
    """Make root a git work tree; commit everything and point origin/main at it."""
    _git(root, "init", "-q")
    for name in remotes:
        _git(root, "remote", "add", name, url if name == "origin" else
             f"https://example.invalid/{name}/repo.git")
    if commit:
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--allow-empty", "-m", "fixture")
        _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def _repo(tmp_path, card=None, status="signed", bench=True, definition=True, files=None,
          git=True, **git_kw):
    """A launch checkout. With git (the default) everything is committed and is origin/main."""
    root = tmp_path / "repo"
    (root / "docs" / "launch" / "evidence").mkdir(parents=True)
    (root / "docs" / "launch" / "scorecard.json").write_text(
        json.dumps(card if card is not None else _passing_card()), encoding="utf-8")
    if definition:
        (root / "docs" / "launch" / "definition.json").write_text(json.dumps(
            {"schema": "launch-definition/v1", "status": status, "signed_by": "owner",
             "signed_on": "2026-09-30"}), encoding="utf-8")
    if bench:
        (root / BENCH).write_text("{}", encoding="utf-8")
    for rel, text in (files or {}).items():
        (root / rel).write_text(text, encoding="utf-8")
    if git:
        _git_repo(root, **git_kw)
    return root


def _issue(number, state="open", **extra):
    return {"number": number, "state": state, "labels": [{"name": "launch:blocker"}], **extra}


def _blockers(tmp_path, items):
    path = tmp_path / "blockers.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def _run(root, blockers, capsys, extra=()):
    code = dz.main([str(root), "--blockers-json", blockers, *extra])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


def _dim(card, did):
    return next(d for d in card["dimensions"] if d["id"] == did)


# -- the eight cases the issue names ---------------------------------------------------------

def test_all_gating_pass_no_blockers_signed_is_go(tmp_path, capsys):
    code, lines, err = _run(_repo(tmp_path), _blockers(tmp_path, []), capsys)
    assert (code, lines[-1], err) == (0, "GO", "")


def test_one_gating_dimension_at_2_is_nogo_naming_it(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D7")["score"] = 2
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 1 and lines[-1] == "NO-GO"
    assert any(l.startswith("D7 Onboarding: score 2") for l in lines), lines


def test_informational_dimension_at_0_is_still_go(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D12").update(score=0, evidence=[])
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 0 and lines[-1] == "GO"
    assert any(l.startswith("info: D12 Market") for l in lines)


def test_one_open_blocker_is_nogo_naming_its_number(tmp_path, capsys):
    items = [_issue(4242, title="data loss")]
    code, lines, _ = _run(_repo(tmp_path), _blockers(tmp_path, items), capsys)
    assert code == 1 and lines[-1] == "NO-GO"
    assert "open launch:blocker: #4242" in lines


def test_definition_proposed_is_nogo(tmp_path, capsys):
    code, lines, _ = _run(_repo(tmp_path, status="proposed"), _blockers(tmp_path, []), capsys)
    assert code == 1 and lines[-1] == "NO-GO"
    assert any(l.startswith("definition not signed") for l in lines)


def test_missing_benchmark_file_is_nogo(tmp_path, capsys):
    code, lines, _ = _run(_repo(tmp_path, bench=False), _blockers(tmp_path, []), capsys)
    assert code == 1 and lines[-1] == "NO-GO"
    assert any(l.startswith("benchmark results:") and BENCH in l for l in lines)


def test_score_of_5_is_malformed_exit_2(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D1")["score"] = 5
    code, lines, err = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == []
    assert "MALFORMED" in err and "outside 0-4" in err


def test_gating_score_without_evidence_is_nogo(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D8").update(score=4, evidence=[])
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 1 and lines[-1] == "NO-GO"
    assert any(l.startswith("D8 Docs:") and "no evidence" in l for l in lines)


# -- the definition goal may not have landed --------------------------------------------------

def test_missing_definition_is_nogo_exit_1_not_malformed(tmp_path, capsys):
    code, lines, err = _run(_repo(tmp_path, definition=False), _blockers(tmp_path, []), capsys)
    assert (code, lines[-1], err) == (1, "NO-GO", "")
    assert any(l.startswith("definition missing") for l in lines)


# -- steering and malformed inputs ------------------------------------------------------------

@pytest.mark.parametrize("mutate, needle", [
    (lambda c: _dim(c, "D1").update(gating=False), "rule pins"),
    (lambda c: _dim(c, "D12").update(gating=True), "rule pins"),
    (lambda c: c["dimensions"].pop(0), "missing dimension"),
    (lambda c: c["dimensions"].append(copy.deepcopy(c["dimensions"][0])), "twice"),
    (lambda c: c["dimensions"].append({"id": "D99", "name": "x", "gating": True,
                                       "score": 4, "evidence": ["e"]}), "unknown id"),
    (lambda c: _dim(c, "D2").update(score=-1), "outside 0-4"),
    (lambda c: c.update(waived=["D7"]), "unknown key"),
    (lambda c: _dim(c, "D7").update(waived=True), "unknown key"),
    (lambda c: _dim(c, "D7").update(name="Onboarding (lite)"), "rule pins 'Onboarding'"),
    (lambda c: c.update(benchmark_results="docs/launch/scorecard.json"), "docs/launch/evidence/"),
    (lambda c: c.update(benchmark_results="docs/launch/evidence/"), "inside the repository"),
    (lambda c: c.update(benchmark_results="/etc/hosts"), "inside the repository"),
    (lambda c: c.update(benchmark_results="docs/launch/evidence/./b.json"), "inside the repository"),
    (lambda c: _dim(c, "D2").update(score=3.5), "not an integer"),
    (lambda c: _dim(c, "D2").update(score=True), "not an integer"),
    (lambda c: c.update(schema="other"), "schema"),
    (lambda c: c.pop("benchmark_results"), "benchmark_results"),
    (lambda c: c.update(benchmark_results="../outside.json"), "inside the repository"),
])
def test_steering_or_bad_scorecard_is_malformed(tmp_path, capsys, mutate, needle):
    card = _passing_card()
    mutate(card)
    code, lines, err = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == [], (lines, err)
    assert needle in err


def test_missing_scorecard_is_malformed(tmp_path, capsys):
    root = _repo(tmp_path)
    (root / "docs" / "launch" / "scorecard.json").unlink()
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == [] and "scorecard not found" in err


def test_unscored_gating_dimension_is_nogo(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D3")["score"] = None
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 1 and any(l.startswith("D3 Outcomes and cost: not scored") for l in lines)


def test_pull_requests_in_the_blocker_list_are_dropped(tmp_path, capsys):
    items = [_issue(7, pull_request={"url": "u"})]
    code, lines, _ = _run(_repo(tmp_path), _blockers(tmp_path, items), capsys)
    assert code == 0 and lines[-1] == "GO"


def test_bad_blocker_list_is_malformed(tmp_path, capsys):
    code, _, err = _run(_repo(tmp_path), _blockers(tmp_path, {"number": 1}), capsys)
    assert code == 2 and "JSON list" in err


def test_bad_repo_argument_is_malformed(tmp_path, capsys):
    code, _, err = _run(_repo(tmp_path), _blockers(tmp_path, []), capsys, ("--repo", "nope"))
    assert code == 2 and "OWNER/NAME" in err


# -- the live (gh) path, with gh faked --------------------------------------------------------

class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_gh_path_uses_rest_paginated_and_decodes_every_page(tmp_path, capsys):
    calls = []

    def fake(argv, **kw):
        calls.append((argv, kw))
        page1 = json.dumps([_issue(11), _issue(12, pull_request={})])
        page2 = json.dumps([_issue(13)])
        return _Proc(0, page1 + "\n" + page2 + "\n")

    code = dz.main([str(_repo(tmp_path)), "--repo", "acme/widget"], run=fake)
    lines = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "open launch:blocker: #11" in lines and "open launch:blocker: #13" in lines
    assert not any("#12" in l for l in lines)
    (argv, kw), = calls
    assert argv == ["gh", "api", "--hostname=github.com", "repos/acme/widget/" + QUERY,
                    "--paginate"]
    assert "graphql" not in " ".join(argv)


def test_gh_failure_is_malformed_never_zero_blockers(tmp_path, capsys):
    code = dz.main([str(_repo(tmp_path)), "--repo", "acme/widget"],
                   run=lambda argv, **kw: _Proc(1, "", "HTTP 401"))
    out, err = capsys.readouterr()
    assert code == 2 and out == "" and "HTTP 401" in err


# -- the shipped files and the documented gesture ---------------------------------------------

def test_shipped_scorecard_is_valid_and_nothing_is_scored_yet(tmp_path):
    empty = tmp_path / "none.json"
    empty.write_text("[]", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(TOOL), str(ROOT), "--blockers-json", str(empty)],
                          capture_output=True, text=True)
    assert proc.returncode == 1, proc.stderr
    assert proc.stdout.splitlines()[-1] == "NO-GO"
    assert "D1 Correctness: not scored" in proc.stdout


def _rule_table(heading):
    text = RULE.read_text(encoding="utf-8")
    block = text.split(heading, 1)[1].split("\n\n", 2)[1]
    return dict(re.findall(r"^\| (D\d+) \| (.+?) \|$", block, re.M))


def test_decision_rule_page_and_checker_agree():
    gating = _rule_table("Gating dimensions:")
    info = _rule_table("Informational dimensions:")
    assert gating == {d: n for d, (n, g) in dz.DIMENSIONS.items() if g}
    assert info == {d: n for d, (n, g) in dz.DIMENSIONS.items() if not g}
    card = json.loads(SHIPPED_CARD.read_text(encoding="utf-8"))
    assert {d["id"]: d["name"] for d in card["dimensions"]} == {**gating, **info}
    text = RULE.read_text(encoding="utf-8")
    assert re.findall(r"^\| (B\d) \|", text, re.M) == [f"B{i}" for i in range(1, 9)]
    assert "Proposed — merged by the owner = signed" in text
    assert "python3 tools/readiness/decide.py ." in text
    assert "cannot verify on main" in text and "caveat" not in text


def test_rules_the_checker_cannot_see_sit_under_their_own_heading():
    text = RULE.read_text(encoding="utf-8")
    heading = "## Process rules the checker does not enforce"
    assert text.count(heading) == 1
    after = text.split(heading, 1)[1].split("\n## ", 1)[0]
    before = text.split(heading, 1)[0]
    for sentence in ("names its class code", "Removing the label from an open issue",
                     "Nobody may demote"):
        assert sentence in after and sentence not in before, sentence


# -- review block #1: evidence must be a link -------------------------------------------------

@pytest.mark.parametrize("entry", [
    "TODO", "n/a", " x ", "", "docs/launch/evidence/nope.md", "../outside.md",
    "docs/../../outside.md", "/etc/hosts", "https://", "https:// example.com/x",
    "ftp://example.com/x", "docs/launch/evidence", "docs\\launch\\evidence\\proof.md",
    "https://[::1", "docs/launch/evidence/./proof.md", "docs/launch/evidence/proof.md/",
    "docs/a\x00b", "docs/" + "x" * 5000,
])
def test_evidence_entry_that_is_not_a_link_is_nogo_naming_it(tmp_path, capsys, entry):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = [entry]
    root = _repo(tmp_path, card, files={PROOF: "x"})
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert (code, lines[-1], err) == (1, "NO-GO", ""), lines
    assert any(l.startswith("D8 Docs: evidence " + repr(entry)) for l in lines), lines


def test_directory_evidence_is_not_a_file(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = ["docs/launch/evidence"]
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 1 and ("D8 Docs: evidence 'docs/launch/evidence' is not a link: is neither an "
                          "http(s) URL nor an existing file in the repository (gating)") in lines


def test_one_bad_entry_beside_a_good_one_is_still_nogo(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = ["https://example.com/ok", "TODO"]
    code, lines, _ = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code == 1 and any("'TODO' is not a link" in l for l in lines), lines


def test_symlink_evidence_escaping_the_repo_is_nogo(tmp_path, capsys):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = ["docs/launch/evidence/link.md"]
    root = _repo(tmp_path, card, git=False)
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    try:
        (root / "docs" / "launch" / "evidence" / "link.md").symlink_to(tmp_path / "outside.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    _git_repo(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and any("outside the repository" in l for l in lines), lines


@pytest.mark.parametrize("entry", ["https://example.com/evidence/D8",
                                   "http://example.com/x?y=1#z", PROOF])
def test_valid_url_or_tracked_repo_file_is_evidence(tmp_path, capsys, entry):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = [entry]
    root = _repo(tmp_path, card, files={PROOF: "x"})
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert (code, lines[-1]) == (0, "GO"), lines


# -- review block #2 (c): a repository-path evidence entry must be on main, non-empty ----------

def _evidence_case(tmp_path, capsys, git_kw=None, after=None, content="x"):
    card = _passing_card()
    _dim(card, "D8")["evidence"] = [PROOF]
    root = _repo(tmp_path, card, files={PROOF: content} if after is None else None,
                 **(git_kw or {}))
    if after:
        after(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and lines[-1] == "NO-GO", lines
    return [l for l in lines if l.startswith(f"D8 Docs: evidence {PROOF!r}")]


def test_untracked_evidence_file_is_nogo(tmp_path, capsys):
    got = _evidence_case(tmp_path, capsys,
                         after=lambda r: (r / PROOF).write_text("x", encoding="utf-8"))
    assert got == [f"D8 Docs: evidence {PROOF!r} is not tracked at origin/main (gating)"]


def test_empty_evidence_file_is_nogo(tmp_path, capsys):
    got = _evidence_case(tmp_path, capsys, content="")
    assert got == [f"D8 Docs: evidence {PROOF!r} is empty at origin/main (gating)"]


def test_evidence_file_without_origin_main_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, files={PROOF: "x"}, git=False)
    card = _passing_card()
    _dim(card, "D8")["evidence"] = [PROOF]
    (root / "docs" / "launch" / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and any(l.startswith(f"D8 Docs: evidence {PROOF!r} cannot verify on main (")
                             for l in lines), lines


# -- an unnamed benchmark file is NO-GO, never GO ---------------------------------------------

@pytest.mark.parametrize("bench", [None, "", "   "])
def test_unnamed_benchmark_file_is_nogo(tmp_path, capsys, bench):
    card = _passing_card()
    card["benchmark_results"] = bench
    code, lines, err = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert (code, lines[-1], err) == (1, "NO-GO", ""), lines
    assert "benchmark results: not named in the scorecard" in lines


# -- the benchmark file: under evidence/, non-empty, on origin/main ---------------------------

def test_benchmark_tracked_at_origin_main_is_go(tmp_path, capsys):
    code, lines, _ = _run(_repo(tmp_path), _blockers(tmp_path, []), capsys)
    assert (code, lines) == (0, ["info: D12 Market is informational (score 3); it blocks only "
                                 "through B3", "GO"]), lines


def test_untracked_benchmark_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, bench=False)
    (root / BENCH).write_text("{}", encoding="utf-8")
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and f"benchmark results: {BENCH} is not tracked at origin/main" in lines


def test_dirty_benchmark_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path)
    (root / BENCH).write_text('{"edited": true}', encoding="utf-8")
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and any("differs from origin/main" in l for l in lines), lines


def test_empty_benchmark_at_origin_main_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, git=False)
    (root / BENCH).write_text("", encoding="utf-8")
    _git_repo(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and f"benchmark results: {BENCH} is empty at origin/main" in lines


# -- review block #2, BLOCKING 3: main that cannot be read is never GO -------------------------

def _cannot_verify(tmp_path, capsys, root, why):
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert (code, lines[-1], err) == (1, "NO-GO", ""), lines
    got = [l for l in lines if l.startswith("benchmark results:")]
    assert len(got) == 1 and got[0].startswith("benchmark results: cannot verify on main ("), got
    assert why in got[0], got
    assert not any(l.startswith("caveat") for l in lines)


def test_no_origin_main_ref_is_nogo_cannot_verify(tmp_path, capsys):
    root = _repo(tmp_path)
    _git(root, "update-ref", "-d", "refs/remotes/origin/main")
    _cannot_verify(tmp_path, capsys, root, "no origin/main ref")


def test_untracked_never_committed_benchmark_without_origin_main_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, bench=False, commit=False)
    (root / BENCH).write_text("{}", encoding="utf-8")
    _cannot_verify(tmp_path, capsys, root, "no origin/main ref")


def test_not_a_git_work_tree_is_nogo_cannot_verify(tmp_path, capsys):
    _cannot_verify(tmp_path, capsys, _repo(tmp_path, git=False), "is not a git work tree")


def test_git_missing_is_nogo_cannot_verify(tmp_path, capsys, monkeypatch):
    root = _repo(tmp_path)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    _cannot_verify(tmp_path, capsys, root, "git cannot be run")


def test_repo_root_below_the_git_top_level_is_nogo_cannot_verify(tmp_path, capsys):
    root = _repo(tmp_path, git=False)
    _git_repo(tmp_path)
    _cannot_verify(tmp_path, capsys, root, "is not the top of its git work tree")


def test_symlinked_benchmark_without_git_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, bench=False, git=False)
    try:
        (root / BENCH).symlink_to("/etc/hosts")
    except OSError:
        pytest.skip("symlinks unavailable")
    _cannot_verify(tmp_path, capsys, root, "is not a git work tree")


def test_benchmark_committed_as_a_symlink_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path, bench=False, git=False)
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    try:
        (root / BENCH).symlink_to(tmp_path / "outside.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    _git_repo(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and any(l.startswith(f"benchmark results: {BENCH} is not a regular file at "
                                          "origin/main") for l in lines), lines


def test_benchmark_symlinked_in_the_checkout_is_nogo(tmp_path, capsys):
    root = _repo(tmp_path)
    (tmp_path / "copy.json").write_text("{}", encoding="utf-8")
    (root / BENCH).unlink()
    try:
        (root / BENCH).symlink_to(tmp_path / "copy.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and f"benchmark results: {BENCH} is a symlink in this checkout" in lines


# -- a signature needs a signer and a date ----------------------------------------------------

FUTURE = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()


@pytest.mark.parametrize("drop, value", [
    ("signed_by", None), ("signed_by", ""), ("signed_by", "  "), ("signed_by", "Owner Name"),
    ("signed_by", "a@b"), ("signed_by", "x" * 40), ("signed_by", "owner\n"), ("signed_by", 7),
    ("signed_on", None), ("signed_on", "30/09/2026"), ("signed_on", "2026-13-01"),
    ("signed_on", 20260930), ("signed_on", "2026-02-30"), ("signed_on", "2026-09-30\n"),
    ("signed_on", FUTURE), ("signed_on", "٢٠٢٦-09-30"),
])
def test_signed_without_signer_or_date_is_nogo(tmp_path, capsys, drop, value):
    root = _repo(tmp_path, git=False)
    path = root / "docs" / "launch" / "definition.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if value is None:
        del data[drop]
    else:
        data[drop] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    _git_repo(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 1 and any(l.startswith("definition not signed") and drop in l
                             for l in lines), lines


def test_plain_login_and_today_are_a_valid_signature(tmp_path, capsys):
    root = _repo(tmp_path, git=False)
    (root / "docs" / "launch" / "definition.json").write_text(json.dumps(
        {"schema": "launch-definition/v1", "status": "signed", "signed_by": "swapnil-agrim",
         "signed_on": datetime.date.today().isoformat()}), encoding="utf-8")
    _git_repo(root)
    code, lines, _ = _run(root, _blockers(tmp_path, []), capsys)
    assert (code, lines[-1]) == (0, "GO"), lines


# -- the offline blocker list ------------------------------------------------------------------

def test_blocker_state_matches_case_insensitively(tmp_path, capsys):
    items = [_issue(5, "OPEN"), _issue(6, "Closed")]
    code, lines, _ = _run(_repo(tmp_path), _blockers(tmp_path, items), capsys)
    assert code == 1 and "open launch:blocker: #5" in lines
    assert not any("#6" in l for l in lines)


@pytest.mark.parametrize("item", [{"number": 5, "labels": [{"name": "launch:blocker"}]},
                                  _issue(5, "merged"), _issue(5, None)])
def test_blocker_with_unknown_or_missing_state_is_malformed(tmp_path, capsys, item):
    code, lines, err = _run(_repo(tmp_path), _blockers(tmp_path, [item]), capsys)
    assert code == 2 and lines == [] and "state" in err


@pytest.mark.parametrize("item, needle", [
    ({"number": 5, "state": "open"}, "labels"),
    ({"number": 5, "state": "open", "labels": "launch:blocker"}, "labels"),
    ({"number": 5, "state": "open", "labels": ["launch:blocker"]}, "labels"),
    ({"number": 5, "state": "open", "labels": [{"name": "launch:next"}]}, "does not carry"),
    (_issue(5, pull_request=None), "pull_request"),
    (_issue(5, pull_request="yes"), "pull_request"),
    ({"number": 5, "state": "open", "pull_request": {}}, "labels"),
    (_issue(True), "number"),
    (_issue("5"), "number"),
    (["number", 5], "not an object"),
])
def test_blocker_entry_of_the_wrong_shape_is_malformed(tmp_path, capsys, item, needle):
    code, lines, err = _run(_repo(tmp_path), _blockers(tmp_path, [item]), capsys)
    assert code == 2 and lines == [] and needle in err, err


# -- the live (gh) path: an empty read and an ambiguous repository ----------------------------

@pytest.mark.parametrize("out", ["", "  \n"])
def test_gh_exit_0_with_empty_output_is_malformed(tmp_path, capsys, out):
    code = dz.main([str(_repo(tmp_path)), "--repo", "acme/widget"],
                   run=lambda argv, **kw: _Proc(0, out))
    got, err = capsys.readouterr()
    assert code == 2 and got == "" and "no output" in err


@pytest.mark.parametrize("remotes", [(), ("origin", "upstream"), ("upstream",)])
def test_default_repo_needs_exactly_one_origin_remote(tmp_path, capsys, remotes):
    root = _repo(tmp_path, remotes=remotes)
    calls = []
    code = dz.main([str(root)], run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    out, err = capsys.readouterr()
    assert (code, out, calls) == (2, "", []) and "pass --repo" in err


def test_default_repo_outside_git_is_malformed(tmp_path, capsys):
    calls = []
    code = dz.main([str(_repo(tmp_path, git=False))],
                   run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    out, err = capsys.readouterr()
    assert (code, out, calls) == (2, "", []) and "pass --repo" in err


# -- review block #2, BLOCKING 1: the repository is derived, never resolved by gh ---------------

@pytest.mark.parametrize("url, host", [
    ("https://github.com/acme/widget", "github.com"),
    ("https://github.com/acme/widget.git", "github.com"),
    ("https://github.com/acme/widget/", "github.com"),
    ("http://github.com/acme/widget.git", "github.com"),
    ("https://user@GitHub.com/acme/widget.git", "github.com"),
    ("ssh://git@github.com/acme/widget.git", "github.com"),
    ("ssh://git@github.com:22/acme/widget", "github.com"),
    ("git@github.com:acme/widget.git", "github.com"),
    ("git@github.com:acme/widget", "github.com"),
    ("github.com:acme/widget.git", "github.com"),
    ("https://ghe.example.com/acme/widget.git", "ghe.example.com"),
])
def test_default_repo_is_derived_from_origins_url(tmp_path, capsys, url, host):
    calls = []
    root = _repo(tmp_path, url=url)
    code = dz.main([str(root)], run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    assert code == 0, capsys.readouterr()
    assert calls == [["gh", "api", f"--hostname={host}", "repos/acme/widget/" + QUERY,
                      "--paginate"]]


@pytest.mark.parametrize("url", [
    "https://[::1", "file:///srv/acme/widget.git", "/srv/acme/widget.git",
    "https://github.com/acme", "https://github.com/acme/widget/extra",
    "https://github.com/../..", "git@github.com:../..", "https://github.com/acme/widget?x=1",
    "https://github.com/acme/.git", "git@github.com:/acme/widget.git",
    "https://user:s3cret@github.com/acme",
])
def test_origin_url_of_an_unknown_shape_is_malformed(tmp_path, capsys, url):
    calls = []
    root = _repo(tmp_path, url=url)
    code = dz.main([str(root)], run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    out, err = capsys.readouterr()
    assert (code, out, calls) == (2, "", []) and "pass --repo" in err, err
    assert "s3cret" not in err


def test_origin_with_two_urls_is_malformed(tmp_path, capsys):
    root = _repo(tmp_path)
    _git(root, "remote", "set-url", "--add", "origin", "https://github.com/other/repo.git")
    calls = []
    code = dz.main([str(root)], run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    out, err = capsys.readouterr()
    assert (code, out, calls) == (2, "", []) and "2 URLs" in err, err


@pytest.mark.parametrize("argv_tail", [(), ("--repo", "acme/widget")])
def test_gh_repo_and_gh_host_cannot_redirect_the_read(tmp_path, capsys, monkeypatch, argv_tail):
    monkeypatch.setenv("GH_REPO", "cli/cli")
    monkeypatch.setenv("GH_HOST", "evil.example")
    monkeypatch.setenv("GH_TOKEN", "kept-for-auth")
    seen = []

    def fake(argv, **kw):
        seen.append((argv, kw["env"]))
        return _Proc(0, "[]")

    root = _repo(tmp_path, url="https://github.com/acme/widget.git")
    assert dz.main([str(root), *argv_tail], run=fake) == 0, capsys.readouterr()
    (argv, env), = seen
    assert argv[2:4] == ["--hostname=github.com", "repos/acme/widget/" + QUERY]
    assert "GH_REPO" not in env and "GH_HOST" not in env
    assert env["GH_TOKEN"] == "kept-for-auth"


@pytest.mark.skipif(os.name == "nt", reason="stub gh is a POSIX shell script")
def test_documented_gesture_with_gh_repo_set_reads_this_repository(tmp_path):
    """The docs' gesture, `python3 tools/readiness/decide.py .`, with GH_REPO pointing elsewhere
    and a stub `gh` first on PATH that records what it was asked and its GH_REPO."""
    root = _repo(tmp_path, url="git@github.com:acme/widget.git")
    stub_dir, record = tmp_path / "bin", tmp_path / "gh-called.txt"
    stub_dir.mkdir()
    stub = stub_dir / "gh"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" "GH_REPO=${GH_REPO-unset}" > "$RECORD"\n'
                    "echo '[]'\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "GH_REPO": "cli/cli", "GH_HOST": "evil.example", "RECORD": str(record),
           "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run([sys.executable, str(TOOL), "."], cwd=root, env=env,
                          capture_output=True, text=True)
    assert (proc.returncode, proc.stdout.splitlines()[-1:]) == (0, ["GO"]), proc.stderr
    assert record.read_text(encoding="utf-8").splitlines() == [
        "api", "--hostname=github.com", "repos/acme/widget/" + QUERY, "--paginate",
        "GH_REPO=unset"]


# -- review block #2, BLOCKING 2: --repo is exactly OWNER/NAME ---------------------------------

@pytest.mark.parametrize("repo", [
    "../..", "a/.", "a/b\n", "./x", "a/..", ".../x", "-a/b", "a/-b", "a/b/c", "a", "a/",
    "/b", "a/b ", "a b/c", "a/b?x=1", "a" * 101 + "/b", "a/" + "b" * 101, "a/b\x00",
])
def test_repo_argument_that_is_not_owner_name_is_malformed(tmp_path, capsys, repo):
    calls = []
    code = dz.main([str(_repo(tmp_path)), "--repo", repo],
                   run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    out, err = capsys.readouterr()
    assert (code, out, calls) == (2, "", []) and "OWNER/NAME" in err


@pytest.mark.parametrize("repo", ["acme/widget", "Acme_1/wid.get-2", "_a/_b", "a/b.c",
                                  "a" * 100 + "/" + "b" * 100])
def test_valid_repo_argument_is_used_verbatim(tmp_path, capsys, repo):
    calls = []
    code = dz.main([str(_repo(tmp_path)), "--repo", repo],
                   run=lambda argv, **kw: calls.append(argv) or _Proc(0, "[]"))
    assert code == 0, capsys.readouterr()
    assert calls[0][3] == f"repos/{repo}/" + QUERY


# -- review block #2 (a) (b) (e): duplicate keys, unreadable inputs, --help ---------------------

DUP_CARD = ('{"schema": "launch-scorecard/v1", "schema": "launch-scorecard/v1", '
            '"benchmark_results": null, "dimensions": []}')


def test_duplicate_key_in_scorecard_is_malformed(tmp_path, capsys):
    root = _repo(tmp_path)
    (root / "docs" / "launch" / "scorecard.json").write_text(DUP_CARD, encoding="utf-8")
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == [] and "duplicate key 'schema'" in err


def test_duplicate_gating_key_cannot_flip_a_dimension(tmp_path, capsys):
    root = _repo(tmp_path)
    path = root / "docs" / "launch" / "scorecard.json"
    text = path.read_text(encoding="utf-8").replace('"gating": true', '"gating": true, '
                                                     '"gating": false', 1)
    path.write_text(text, encoding="utf-8")
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == [] and "duplicate key 'gating'" in err


def test_duplicate_key_in_definition_is_malformed(tmp_path, capsys):
    root = _repo(tmp_path)
    (root / "docs" / "launch" / "definition.json").write_text(
        '{"schema": "launch-definition/v1", "status": "proposed", "status": "signed", '
        '"signed_by": "owner", "signed_on": "2026-09-30"}', encoding="utf-8")
    code, lines, err = _run(root, _blockers(tmp_path, []), capsys)
    assert code == 2 and lines == [] and "duplicate key 'status'" in err


def test_duplicate_key_in_blocker_list_is_malformed(tmp_path, capsys):
    path = tmp_path / "blockers.json"
    path.write_text('[{"number": 1, "state": "open", "state": "closed", '
                    '"labels": [{"name": "launch:blocker"}]}]', encoding="utf-8")
    code, lines, err = _run(_repo(tmp_path), str(path), capsys)
    assert code == 2 and lines == [] and "duplicate key 'state'" in err


def test_duplicate_key_in_gh_output_is_malformed(tmp_path, capsys):
    out = '[{"number": 1, "state": "open", "state": "closed", "labels": []}]'
    code = dz.main([str(_repo(tmp_path)), "--repo", "acme/widget"],
                   run=lambda argv, **kw: _Proc(0, out))
    got, err = capsys.readouterr()
    assert code == 2 and got == "" and "duplicate key 'state'" in err


@pytest.mark.parametrize("which", ["root-nul", "root-long", "blockers-nul", "blockers-long",
                                   "blockers-deep", "scorecard-deep", "gh-deep"])
def test_unreadable_input_is_malformed_never_a_traceback(tmp_path, capsys, which):
    root, blockers, run = str(_repo(tmp_path)), _blockers(tmp_path, []), None
    if which == "root-nul":
        root = root + "\x00x"
    elif which == "root-long":
        root = str(tmp_path / ("x" * 5000))
    elif which == "blockers-nul":
        blockers = blockers + "\x00x"
    elif which == "blockers-long":
        blockers = str(tmp_path / ("x" * 5000))
    elif which == "blockers-deep":
        pathlib.Path(blockers).write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
    elif which == "scorecard-deep":
        (pathlib.Path(root) / "docs" / "launch" / "scorecard.json").write_text(
            "[" * 200000 + "]" * 200000, encoding="utf-8")
    argv = [root, "--blockers-json", blockers]
    if which == "gh-deep":
        argv = [root, "--repo", "acme/widget"]
        run = lambda argv, **kw: _Proc(0, "[" * 200000 + "]" * 200000)  # noqa: E731
    code = dz.main(argv, run=run) if run else dz.main(argv)
    out, err = capsys.readouterr()
    assert (code, out) == (2, ""), err
    assert err.startswith("decide.py: MALFORMED: ") and "Traceback" not in err


def test_unreadable_benchmark_path_is_never_a_traceback(tmp_path, capsys):
    card = _passing_card()
    card["benchmark_results"] = "docs/launch/evidence/" + "x" * 5000
    code, lines, err = _run(_repo(tmp_path, card), _blockers(tmp_path, []), capsys)
    assert code in (1, 2) and "Traceback" not in err
    assert code == 2 or lines[-1] == "NO-GO"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_exits_2_never_the_go_code(capsys, flag):
    assert dz.main([flag]) == 2
    assert "usage: decide.py" in capsys.readouterr().out


def test_help_via_the_documented_gesture_exits_2():
    proc = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True, text=True)
    assert proc.returncode == 2 and "usage: decide.py" in proc.stdout
