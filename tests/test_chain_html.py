"""`chain.py html`: the whole plan of one chain as one offline page, from the STORED plan record (`dag_html.run_plan`).

The page draws every member of the record (deep ones read from prose included), every edge with its tag, and every
question the sheet asks, numbered as the sheet numbers them and in the sheet's own words (the page reads them out of
`chain.render_sheet`, it re-words nothing). No test reaches the network.

Every guard below was run red once against its own deliberate break; a test's docstring names it as `control:`.
Controls: edge tag taken from nothing; a question badge dropped; the page refusing nothing without a record; the
`http` scrub off; the ignored-output guard off; a verdict colour collapsed into another; the closed toggle removed;
the member/marker space removed; the closed tag moved back under the title; the load-time fit replaced by the old
clipped start; the map page's one-line pointer dropped; the sheet's numbering replaced by the record's order."""
import html as htmllib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import test_chain_sheet as tcs  # noqa: E402
import test_chain_waits_1902 as tw  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
NOW = tcs.NOW + 300


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / (name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dh = _mod("dag_html")
chain = tcs._chain()


def _page(base, goal=1, out=None, titles=None, now=NOW):
    out = out or pathlib.Path(base).parent / "pages"
    lines, code = dh.run_plan(base, goal, out_dir=str(out), titles_path=titles, now=now)
    assert code == 0, lines
    return (pathlib.Path(out) / ("chain-plan-%d.html" % goal)).read_text()


def _deep(tmp_path):
    base, _p = tw._plan(tmp_path, "live", tw._deep_chain(), [])
    return base


def _sheet_questions(base, goal=1):
    """The decision lines of the sheet: {number: first line}, read from the printed sheet."""
    text = chain.sheet(base, goal, now=NOW)
    block = text.split("Decisions (")[1].split("Writes that follow approval:")[0]
    return {int(m.group(1)): m.group(0).strip() for m in re.finditer(r"^  (\d+)\. .*$", block, re.M)}


def _put(base, rec, goal=1):
    p = pathlib.Path(base) / "state" / "chains" / ("c%d.json" % goal)
    p.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")


def _rec(base, goal=1):
    return json.loads((pathlib.Path(base) / "state" / "chains" / ("c%d.json" % goal)).read_text())


def _paths(page):
    return {(int(a), int(b)): cls for cls, a, b in
            re.findall(r'<path class="e ([a-z]+)[^"]*" data-a="(-?\d+)" data-b="(-?\d+)"', page)}


def _node_classes(page):
    return {int(n): cls for cls, n in re.findall(r'<g class="n (c-[a-z]+)[^"]*" data-n="(-?\d+)"', page)}


# ------------------------------------------------------------------ the whole plan is drawn

def test_a_deep_unconfirmed_edge_is_drawn_dashed(tmp_path):
    """control: take the edge class from nothing (always `explicit`): the three prose edges are solid, test red."""
    page = _page(_deep(tmp_path))
    assert _paths(page) == {(2, 1): "unconfirmed", (3, 2): "unconfirmed", (4, 3): "unconfirmed"}
    assert set(_node_classes(page)) == {1, 2, 3, 4}                 # the deep members are all there
    assert ".e.unconfirmed{stroke-dasharray" in page


def test_an_explicit_edge_is_drawn_solid_beside_an_unconfirmed_one(tmp_path):
    issues = [tw._goal(1, blocked_by=[2], assignees=["pat"], comments=[tw._c("Blocked by: #5 added", "pat", "NONE")]),
              tw._goal(2), tw._goal(5)]
    base, _p = tw._plan(tmp_path, "live", issues, [])
    assert _paths(_page(base)) == {(2, 1): "explicit", (5, 1): "unconfirmed"}


def test_every_member_done_and_external_of_the_record_is_on_the_page(tmp_path):
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    rec["externals"] = [{"ref": "#99", "kind": "pull-request", "needed_by": [1]},
                        {"ref": "other/repo#5", "kind": "other-repository", "needed_by": [3]}]
    _put(base, rec)
    page = _page(base)
    nodes = _node_classes(page)
    assert {r["number"] for r in rec["members"]} <= set(nodes)
    assert {n for n, c in nodes.items() if c == "c-closed"} == set(rec["done"]) == {4}
    outside = {n for n, c in nodes.items() if c == "c-outside"}
    assert len(outside) == 2 and all(n < 0 for n in outside)        # numbers that are not issues get a negative key
    assert "other/repo#5" in page and "#99" in page
    assert len(nodes) == len(rec["members"]) + 1 + 2


def test_closed_prerequisites_sit_behind_the_toggle(tmp_path):
    """control: drop the toggle: no `zcl` control, and the closed nodes are never hideable."""
    base, _fk = tcs._scene(tmp_path)
    page = _page(base)
    assert 'id="zcl"' in page and "show 1 closed" in page
    assert re.search(r'<g class="n c-closed cl" data-n="4"', page)
    assert ".n.cl{display:none}" in page


def test_verdicts_get_distinct_colours_and_legend_entries(tmp_path):
    """control: give two of them one class (checkpoint drawn as parked): the distinct-class assertion is red."""
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    verdicts = {1: "armed", 2: "checkpoint", 3: "not-a-goal", 5: "awaiting-owner", 6: "parked", 7: "armed"}
    tmpl = rec["members"][0]
    rec["members"] = [dict(tmpl, number=n, verdict=v, spend=(n == 7), waits_on=[], depth=0)
                      for n, v in verdicts.items()]
    rec["edges"], rec["answers"], rec["done"], rec["cross_unit"] = [], {}, [], []
    _put(base, rec)
    page = _page(base)
    cls = _node_classes(page)
    assert cls[2] == "c-check" and cls[3] == "c-notgoal" and cls[5] == "c-awaiting" and cls[6] == "c-parked"
    assert cls[7] == "c-paid"                                       # a paid step, whatever its verdict says
    assert len({cls[n] for n in (2, 3, 5, 6, 7)}) == 5
    legend = page.split('<div class="legend">')[1].split("</header>")[0]
    for word in ("checkpoint", "not a goal", "awaiting owner", "parked", "paid gate"):
        assert re.search(r'<i class="sw c-[a-z]+"></i>%s \(1\)' % word, legend), word


def test_a_member_and_its_marker_read_with_a_space(tmp_path):
    """control: write the number and the marker with no space between (`#2ready now`): red."""
    page = _page(_deep(tmp_path))
    node = re.search(r'<g class="n [^"]*" data-n="1".*?</g>', page, re.S).group(0)
    text = re.sub(r"<title>.*?</title>", "", node)
    text = htmllib.unescape(re.sub(r"<[^>]+>", "", text))
    assert re.search(r"#1 +after #2", text), text


def test_the_closed_tag_clears_the_title(tmp_path):
    """control: put the tag back at the old foot (`y + 61`), or shrink the node to its old 68: both red."""
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    rec["closed_gates"] = [{"prerequisite": 4, "dependents": [1], "followups": [9], "question": False}]
    _put(base, rec)
    titles = tmp_path / "titles.json"
    titles.write_text(json.dumps({"1": "A goal whose title is long enough to take two lines in a node"}))
    page = _page(base, titles=str(titles))
    node = re.search(r'<g class="n [^"]*" data-n="1".*?</g>', page, re.S).group(0)
    height = int(re.search(r'<rect [^>]*height="(\d+)"', node).group(1))
    top = float(re.search(r'<rect [^>]*y="([\d.]+)"', node).group(1))
    tag = float(re.search(r'<text class="mk" x="[\d.]+" y="([\d.]+)" text-anchor="end">✓ 1 closed', node).group(1))
    lines = [float(y) for y in re.findall(r'<text x="[\d.]+" y="([\d.]+)">', node.split('<g class="qb')[0])]
    assert len(lines) == 2
    assert tag - max(lines) >= 18 and tag <= top + height - 4        # the old gap was 13: it read as touching


def test_the_page_opens_fitted_not_clipped():
    """A weak string-level guard (the real check is the screenshot of a wide chain): the old start() zoomed the
    graph to a 0.8 floor and cut a wide chain at the right. control: restore `start()`: red."""
    assert "function start()" not in dh.JS and "0.8" not in dh.JS
    assert dh.JS.rstrip().endswith("fit();\n})();")


# ------------------------------------------------------------------ the questions

def test_the_questions_are_numbered_and_worded_as_the_sheet_does(tmp_path):
    """control: number them by the record's order instead of the sheet's (here: reversed): red."""
    base, _fk = tcs._scene(tmp_path)
    page = _page(base)
    want = _sheet_questions(base)
    assert sorted(want) == [1, 2, 3]
    items = dict(re.findall(r'<li class="q[^"]*" data-q="(\d+)"[^>]*>\s*<b>(.*?)</b>', page, re.S))
    assert {int(k): htmllib.unescape(v) for k, v in items.items()} == want
    assert "3 questions before anything is armed" in page


def test_grouped_questions_follow_the_sheet(tmp_path):
    """Two not-a-goal members share one grouped question; the page carries the sheet's number for it."""
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    tmpl = rec["members"][0]
    rec["members"] = [dict(tmpl, number=n, verdict=v, spend=False, waits_on=[], depth=0, kind=None, labels=[])
                      for n, v in ((1, "armed"), (2, "not-a-goal"), (3, "not-a-goal"))]
    rec["edges"], rec["answers"], rec["done"], rec["cross_unit"], rec["closed_gates"] = [], {}, [], [], []
    _put(base, rec)
    want = _sheet_questions(base)
    assert any("applies to" in v or "group:" in v for v in want.values())
    page = _page(base)
    items = {int(k): htmllib.unescape(v) for k, v in
             re.findall(r'<li class="q[^"]*" data-q="(\d+)"[^>]*>\s*<b>(.*?)</b>', page, re.S)}
    assert items == want
    first = next(n for n, v in want.items() if "group:" in v)
    badges = re.findall(r'<g class="qb[^"]*" data-q="%d" data-n="(\d+)"' % first, page)
    assert sorted(badges) == ["2", "3"]                             # a group badges every member it covers
    assert "applies to 2 questions" in htmllib.unescape(page)


def test_a_question_badge_sits_on_its_node(tmp_path):
    """control: leave the badges out of the node markup: no `qb` group is found and the test is red."""
    base, _fk = tcs._scene(tmp_path)
    page = _page(base)
    want = _sheet_questions(base)
    dep = next(n for n, v in want.items() if "[dependency:2]" in v)
    cap = next(n for n, v in want.items() if "[cap:c1]" in v)
    node2 = re.search(r'<g class="n [^"]*" data-n="2".*?</g></g>', page, re.S).group(0)
    assert re.search(r'<g class="qb[^"]*" data-q="%d" data-n="2"' % dep, node2)
    assert re.search(r'<text[^>]*>%d</text>' % dep, node2)
    node1 = re.search(r'<g class="n [^"]*" data-n="1".*?</g></g>', page, re.S).group(0)
    assert re.search(r'<g class="qb[^"]*" data-q="%d" data-n="1"' % cap, node1)    # a question on the whole chain


def test_an_answered_question_shows_its_recorded_answer(tmp_path):
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    rec["answers"] = {"cap:c1": "7"}
    _put(base, rec)
    page = _page(base)
    cap = next(n for n, v in _sheet_questions(base).items() if "[cap:c1]" in v)
    item = re.search(r'<li class="q[^"]*" data-q="%d".*?</li>' % cap, page, re.S).group(0)
    assert "answered" in item.split(">")[0] and "[answered: 7]" in htmllib.unescape(item)
    assert re.search(r'<g class="qb done" data-q="%d"' % cap, page)
    assert "2 open of 3" in page or "2 open" in page


def test_the_page_holds_the_sheets_unconfirmed_block_and_writes(tmp_path):
    page = htmllib.unescape(_page(_deep(tmp_path)))
    assert "Unconfirmed waits (3)" in page and "#1 waits on" in page
    assert "Writes that follow approval" in page and "record each answer in .sdlc/state/chains/c1.json" in page


# ------------------------------------------------------------------ the verb, its refusals, its limits

def test_no_plan_record_is_refused_naming_the_command(tmp_path):
    """control: delete the up-front record check: the shorter `chain.py plan first` of `_read_record` is all that is
    left, it does not name this sdlc dir and goal, and the exact-command assertion is red."""
    base = tcs._repo(tmp_path, "")
    out = tmp_path / "pages"
    lines, code = dh.run_plan(base, 1, out_dir=str(out), now=NOW)
    assert code == 2 and "chain.py plan %s 1" % base in " ".join(lines)
    assert not out.exists()
    lines, code = dh.run_plan(base, 1, now=NOW)
    assert code == 2 and not (pathlib.Path(base) / "state" / "dag-html").exists()


def test_a_broken_record_is_refused(tmp_path):
    base, _fk = tcs._scene(tmp_path)
    (pathlib.Path(base) / "state" / "chains" / "c1.json").write_text("{not json")
    lines, code = dh.run_plan(base, 1, out_dir=str(tmp_path / "pages"), now=NOW)
    assert code == 2 and "chain.py plan" in lines[0]


def test_the_page_has_no_http_and_no_external_script(tmp_path):
    """control: switch the `http` scrub off and give a title a link: the word appears and the test is red."""
    base, _fk = tcs._scene(tmp_path)
    titles = tmp_path / "titles.json"
    titles.write_text(json.dumps({"1": "see https://example.test/x and http things", "2": "<script>alert(1)</script>"}))
    page = _page(base, titles=str(titles))
    assert "http" not in page.lower().replace("ht​tp", "")
    assert "<script src" not in page and "<link" not in page and " src=" not in page and "@import" not in page
    assert "<script>alert(1)</script>" not in page


def test_output_inside_a_checkout_must_be_ignored(tmp_path):
    """control: make `check_out` return at once: the tracked-directory output is written and the test is red."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True)
    (repo / ".gitignore").write_text(".sdlc/state/\n")
    base, _fk = tcs._scene(repo)
    assert dh.run_plan(base, 1, now=NOW)[1] == 0                                    # the default is ignored
    lines, code = dh.run_plan(base, 1, out_dir=str(repo / "docs" / "maps"), now=NOW)
    assert code == 2 and "not ignored" in lines[0] and not (repo / "docs" / "maps").exists()


def test_the_same_record_gives_the_same_bytes(tmp_path):
    base, _fk = tcs._scene(tmp_path)
    a = _page(base)
    b = _page(base, out=tmp_path / "again")
    assert a == b


def test_cli_documented_gesture_writes_a_page_and_makes_no_github_call(tmp_path):
    """The gesture the docs give, run as a subprocess with a `gh` first on PATH that records any call."""
    base, _fk = tcs._scene(tmp_path)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    mark = tmp_path / "gh-called"
    (bindir / "gh").write_text("#!/bin/sh\necho called >> %s\nexit 1\n" % mark)
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ["PATH"], GH_TOKEN="invalid")
    r = subprocess.run([sys.executable, str(S / "chain.py"), "html", base, "1", "--out", str(tmp_path / "o")],
                       env=env, capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "o" / "chain-plan-1.html").is_file() and "chain-plan-1.html" in r.stdout and not mark.exists()
    r = subprocess.run([sys.executable, str(S / "chain.py"), "html", base, "9", "--out", str(tmp_path / "p")],
                       env=env, capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 2 and "chain.py plan" in r.stderr and not (tmp_path / "p").exists()
    r = subprocess.run([sys.executable, str(S / "chain.py"), "html", base], env=env, capture_output=True,
                       text=True, cwd=str(tmp_path))
    assert r.returncode == 2 and "usage" in r.stderr


# ------------------------------------------------------------------ the sheet names the page; the map page names the plan

def test_the_sheet_names_the_page_command_and_changes_nothing(tmp_path, capsys):
    """The sheet stays a reading: no file is written, and its last line names the command and the path."""
    base, _fk = tcs._scene(tmp_path)
    before = tcs._tree(base)
    assert chain.main(["chain.py", "sheet", base, "1"], now=NOW) == 0
    out = capsys.readouterr().out
    last = out.rstrip("\n").splitlines()[-1]
    assert "chain.py html" in last and "chain-plan-1.html" in last
    assert tcs._tree(base) == before
    assert out.rstrip("\n").splitlines()[-2] == "Nothing has been changed yet."
    assert chain.sheet(base, 1, now=NOW) == tcs.GOLDEN          # the library text is unchanged: only the verb adds a line


def test_the_map_page_says_prose_waits_are_not_in_it_and_names_the_plan_page():
    """control: drop the line from the map page's header: red."""
    model = {"goal": 1, "nodes": {1: {"n": 1, "m": "ready now", "after": [], "why": "", "life": "goal", "prio": None,
                                      "unit": None, "depth": 0, "waits": [], "frees": [], "t": None}},
             "edges": [], "stops": [], "forecast": "a run can finish all 1 of 1", "notes": [], "reason": None,
             "finish": 1, "open": 1, "closed": 0, "outside": 0, "cycles": [], "depth": 0, "decisions": 0,
             "over_cap": False, "titled": False, "built_at": 0, "age": 0, "fresh": (True, "ok"), "mode": None}
    page = dh.render_page(model, "o/r")
    assert "not in the map" in page and "chain.py html" in page


def test_a_sheet_the_page_cannot_read_is_refused_not_guessed(tmp_path, monkeypatch):
    """control: skip the unrecognised line instead of raising: the page is written from a half-read sheet."""
    base, _fk = tcs._scene(tmp_path)
    real = dh.chain.render_sheet
    monkeypatch.setattr(dh.chain, "render_sheet", lambda *a, **k: real(*a, **k).replace(
        "Writes that follow approval:", "A stray line in the block\nWrites that follow approval:"))
    out = tmp_path / "pages"
    lines, code = dh.run_plan(base, 1, out_dir=str(out), now=NOW)
    assert code == 3 and "not recognised" in lines[0] and not out.exists()


def test_an_incomplete_plan_says_so_at_the_top(tmp_path):
    """control: drop the notice: an INCOMPLETE plan reads like a whole one."""
    base, _fk = tcs._scene(tmp_path)
    rec = _rec(base)
    rec["complete"], rec["stops"] = False, ["a wait the plan could not read"]
    _put(base, rec)
    page = _page(base)
    assert "This plan is INCOMPLETE: a wait the plan could not read" in page
    rec["complete"], rec["stops"] = True, []
    _put(base, rec)
    assert "INCOMPLETE" not in _page(base, out=tmp_path / "again")
