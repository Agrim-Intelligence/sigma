"""#2150 / #2151: `record review`'s detail. The routing rule in the loop skill itself says to pass the merge stop
line as the detail (#2150), and the detail is redacted before it is posted: absolute paths, mentions and blocker
phrasing (#2151). Every test drives the gesture the docs give, `loop.py record <sdlc> <goal> review "<line>"`, and
reads the note the local source wrote. Blocker phrases are built from parts so no fixture reads as a real edge."""
import importlib.util
import json
import pathlib
import re
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "sigma-loop" / "scripts"
BLOCK = "blocked" + " by"          # built, never written whole, so no file here reads as an edge
NEEDS = "ne" + "eds"
U = "/" + "Users"                   # built, so the public-text guard sees no workspace path


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(autouse=True)
def _no_codex_host(monkeypatch):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)


def _note_for(detail, also_log=False):
    """Run the documented gesture and return the awaiting-merge text the goal's timeline received (with
    `also_log`, plus the goal's action-log file text)."""
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d) / ".sdlc"
        (base / "goals").mkdir(parents=True)
        (base / "state").mkdir()
        (base / "config.json").write_text(json.dumps({"work": {"enabled": True}, "action_log": {"enabled": True}}))
        (base / "state" / "STATE.md").write_text("iteration: 0\nrun_iteration: 0\nlast_run: none\n")
        goal = str(base / "goals" / "0001.md")
        pathlib.Path(goal).write_text("---\nid: 0001\nstatus: in_progress\n---\nx\n")
        lp = _load("loop")
        wt = base / "work" / "0001"
        wt.mkdir(parents=True)
        lp.work._save(str(base), goal, {"worktree": str(wt), "branch": "sdlc/x", "base": "main",
                                        "remote": "origin", "pr": "7"})
        assert lp.main(["loop.py", "record", str(base), goal, "review", detail]) == 0
        text = (base / "journey" / "0001.md").read_text()
        if also_log:
            text += "\n" + "\n".join(p.read_text() for p in (base / "state" / "log").glob("*.jsonl"))
        return text


# --- #2151: redaction, one test per clause ------------------------------------------------------------------------

@pytest.mark.parametrize("path", [U + "/someone/proj/.sdlc", "/home/runner/work/r/r/.sdlc/state",
                                  "C:\\Us" + "ers\\someone\\proj", "D:/a/r/r/.sdlc", "~/proj/.sdlc"])
def test_an_absolute_path_becomes_a_placeholder(path):
    note = _note_for(f"PR #7 clean, but stopped in {path} (read 403)")
    assert "[path]" in note and "someone" not in note and "runner" not in note and "/.sdlc" not in note, note
    assert "PR #7 clean, but stopped in [path]" in note, note


def test_a_url_and_a_slash_word_are_not_paths():
    note = _note_for("see https://example.com/a/b and read/write and and/or")
    assert "https://example.com/a/b" in note and "read/write" in note and "[path]" not in note, note


def test_a_mention_is_neutralised():
    note = _note_for("ask @octo-cat and @some-org/some-team about it; mail a@b.example is not one")
    assert "@octo-cat" not in note and "@some-org" not in note, note
    assert note.count("[mention]") == 2 and "a@b.example" in note, note


def test_blocker_phrasing_cannot_make_an_edge():
    bs = _load("blocker_scan")
    detail = f"{BLOCK} #91, {NEEDS} #92, depends" + " on #93, follow-up" + " to #94, after #95"
    note = _note_for(detail)
    assert not bs._BLOCK_RE.search(note), note
    for n in ("91", "92", "93", "94", "95"):
        assert "#" + n in note, note                 # the number survives; only the trigger phrase goes


def test_redaction_runs_before_the_cap_and_the_note_stays_one_line():
    # A long path shrinks to `[path]`, so the text after it must survive; capped first, it would be cut off.
    note = _note_for(U + "/someone/" + "a/" * 300 + " tail-marker")
    why = note.split("Why the loop did not merge it: ")[1].splitlines()[0]
    assert why == "[path] tail-marker", note
    direct = _load("loop")._awaiting_merge_note("7", U + "/someone/" + "a/" * 300 + " tail-marker")
    assert direct.endswith("Why the loop did not merge it: [path] tail-marker"), direct   # the seam itself
    long = _note_for("z" * 600).split("Why the loop did not merge it: ")[1].splitlines()[0]
    assert len(long) == 399 + 0 and long.endswith("…") or len(long) <= 400, long


def test_a_tilde_not_followed_by_a_slash_is_not_a_path():
    assert "~5 minutes" in _note_for("about ~5 minutes")


def test_redaction_also_covers_the_ledger_and_action_log_text():
    lp = _load("loop")
    assert lp._redact_detail("in " + U + "/someone/x/.sdlc " + BLOCK + " #5") == "in [path] … #5"
    assert lp._redact_detail("") == "" and lp._redact_detail(None) == ""


def test_the_action_log_gets_the_redacted_detail_too():
    text = _note_for("stopped in " + U + "/someone/proj/.sdlc, ask @octo-cat", also_log=True)
    assert "someone" not in text and "@octo-cat" not in text and text.count("[path]") >= 2 and "recorded" in text, text


def test_the_blocker_clause_reuses_the_shared_pattern():
    src = (S / "loop.py").read_text()
    body = src[src.index("def _redact_detail"):src.index("def _awaiting_merge_note")]
    assert "_BLOCK_RE" in body and "TRIGGERS" not in body and "re.compile" not in body


# --- #2150: the routing rule itself ------------------------------------------------------------------------------

def test_the_loop_skill_routing_rule_passes_the_stop_line_to_record_review():
    text = (ROOT / "skills" / "sigma-loop" / "SKILL.md").read_text()
    flat = " ".join(text.split())
    m = re.search(r"any other line → \*\*`record review[^*]*\*\*[^.;]*", flat)
    assert m, "the routing rule for any other stop line must name `record review`"
    rule = m.group(0)
    assert 'record review "<that line>"' in rule and "detail" in rule, rule


# --- #2164: more path shapes, placeholders that render ------------------------------------------------------------

def _why(detail):
    return _load("loop")._redact_detail(detail)


def test_a_file_url_is_redacted():
    out = _why("opened file://" + U + "/someone/x.html and file:///" + "etc/hosts then file://host/share/a")
    assert out == "opened [path] and [path] then [path]", out


def test_a_unc_style_path_is_redacted_but_a_url_is_not():
    out = _why("copied //fileserver/team/dir/a.txt, see https://example.com//a/b and a//b")
    assert out == "copied [path], see https://example.com//a/b and a//b", out


def test_a_single_segment_absolute_path_is_redacted():
    out = _why("wrote /tmp, then /etc. and (" + U + ") but not /or, /sigma-loop or /tmpfoo")
    assert out == "wrote [path], then [path]. and ([path]) but not /or, /sigma-loop or /tmpfoo", out


def test_home_style_references_are_redacted():
    out = _why("in $HOME/proj, ${HOME}, $USER and ~someone/x and %USERPROFILE%\\x; $HOMEWARD and $5 stay")
    assert out == "in [path], [path], [path] and [path] and [path]; $HOMEWARD and $5 stay", out


def test_placeholders_render_in_github_markdown():
    out = _why("in " + U + "/someone/x, ask @octo-cat")
    assert out == "in [path], ask [mention]" and "<" not in out, out


def test_ordinary_prose_versions_fractions_and_repo_links_are_untouched():
    text = ("1/2 done, v1.0.30 and 3/4 of 2026/11, 5 tests/s, read [the doc](docs/a.md) or ./docs/b.md and "
            "../c/d.md, use `loop.py`, email a@b.example, cost ~5%, $5, 100%, 50% of $PATHWAY")
    assert _why(text) == text


def test_redaction_of_the_new_shapes_runs_before_the_cap():
    long = "file://" + U + "/someone/" + "a/" * 300 + " tail-marker"
    direct = _load("loop")._awaiting_merge_note("7", long)
    assert direct.endswith("Why the loop did not merge it: [path] tail-marker"), direct
