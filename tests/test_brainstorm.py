"""Target resolution for /sigma-scope (brainstorm.py, issue #916): given the raw invocation
string, resolve which of the 4 forms it is (inline free text / local markdown file / fuzzy issue
reference / direct issue number) and return a single, documented shape (`brainstorm-target/v1`,
see the module docstring) later callers (#917 dedup, #918 plan compilation, #920 SKILL.md) can rely
on. Hermetic: no real `gh`/network — github-mode fixtures write directly to the board mirror file
backlog_check.py already reads, and any live-fetch fallback goes through an injected `run`."""
import json
import os
import pathlib
import importlib.util
import tempfile

import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-scope" / "scripts"


def _mod(name="brainstorm"):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# --- fixture helpers (re-typed rather than imported from test_backlog_check.py -- this repo keeps
# no shared test-helper module; see tests/test_import_boundary.py's module docstring for why) ------

def _rec(number, title, body=""):
    return {"number": number, "title": title, "body_excerpt": body, "labels": [],
            "state": "open", "closed_at": None, "updated_at": "2026-08-01T00:00:00Z",
            "content_hash": "x"}


def _github_sdlc(tmp_path, records, backlog_check_cfg=None):
    base = tmp_path / ".sdlc"
    (base / "state").mkdir(parents=True)
    cfg = {"discovery": {"source": "github", "github": {"repo": "example/example"}},
           "backlog_check": backlog_check_cfg or {}}
    (base / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (base / "state" / "board-mirror.ndjson").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return base


def _local_sdlc(tmp_path):
    base = tmp_path / ".sdlc"
    (base / "goals").mkdir(parents=True)
    (base / "state").mkdir()
    return base


# ---------------------------------------------------------------------------- shape / contract


def test_result_shape_carries_every_documented_key():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _local_sdlc(pathlib.Path(d))
        out = bs.resolve_target("a brand new idea nobody has filed yet", sdlc_dir=str(base))
    for key in ("schema", "raw", "form", "confident", "text", "file", "issue",
                "candidates", "degraded", "error"):
        assert key in out, f"missing documented key: {key}"
    assert out["schema"] == bs.SCHEMA


# ---------------------------------------------------------------------------- form 1: free text


def test_free_text_with_no_plausible_match_resolves_as_text():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _local_sdlc(pathlib.Path(d))
        invocation = "how can I add a UI for editing config.json"
        out = bs.resolve_target(invocation, sdlc_dir=str(base))
    assert out["form"] == "text"
    assert out["confident"] is True
    assert out["text"] == invocation
    assert out["issue"] is None and out["file"] is None
    assert out["candidates"] == []
    assert out["error"] is None


def test_free_text_against_a_github_corpus_with_zero_overlap_still_resolves_as_text():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [
            _rec(10, "Improve the render farm queue scheduler", "Batches idle workers overnight."),
        ])
        out = bs.resolve_target("could we add dark mode to the settings page", sdlc_dir=str(base))
    assert out["form"] == "text"
    assert out["confident"] is True


# ---------------------------------------------------------------------------- form 2: markdown file


def test_markdown_file_reference_is_located_and_read():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        docs_dir = root / "docs"
        docs_dir.mkdir()
        (docs_dir / "ui.md").write_text("# UI notes\nsome plan content\n", encoding="utf-8")
        base = _local_sdlc(root)
        out = bs.resolve_target("check the ui.md file and help me plan it",
                                 sdlc_dir=str(base), search_root=str(root))
    assert out["form"] == "file"
    assert out["confident"] is True
    assert out["file"]["content"] == "# UI notes\nsome plan content\n"
    assert out["file"]["path"].endswith("ui.md")
    assert out["issue"] is None


def test_markdown_file_reference_with_a_literal_relative_path_is_used_directly():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "notes").mkdir()
        (root / "notes" / "plan.md").write_text("draft plan\n", encoding="utf-8")
        base = _local_sdlc(root)
        out = bs.resolve_target("look at notes/plan.md please",
                                 sdlc_dir=str(base), search_root=str(root))
    assert out["form"] == "file"
    assert out["file"]["content"] == "draft plan\n"


@pytest.mark.parametrize("invocation", [
    "notes/ui-ideas.md",                                       # the whole invocation is the path
    r"notes\ui-ideas.md",                                      # ...with Windows separators
    "check the notes/ui-ideas.md file and help me plan it",   # README.md's documented gesture
])
def test_an_invocation_that_is_a_file_reference_resolves_as_file(invocation):
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "notes").mkdir()
        (root / "notes" / "ui-ideas.md").write_text("ideas\n", encoding="utf-8")
        base = _local_sdlc(root)
        out = bs.resolve_target(invocation, sdlc_dir=str(base))   # default search root: .sdlc's parent
    assert out["form"] == "file"
    assert out["file"]["content"] == "ideas\n"


def test_a_filename_mentioned_inside_an_idea_stays_free_text(capsys):
    """Observed 2026-09-15 through SKILL.md step 1's own gesture (`brainstorm.py .sdlc "<raw
    invocation text>"`): an idea that only NAMES a file sitting at the repo root came back as `file`
    with that file's content and `text` empty -- the idea itself was gone. The file exists here on
    purpose, so an anywhere-in-the-sentence match resolves it instead of failing to find it."""
    bs = _mod()
    invocation = ("Token optimization findings from this session's measured analysis: (1) bound "
                  "orchestrator/agent run length, (2) pin model tier on every dispatch, (3) slim "
                  "always-loaded instructions (AGENTS.md + output-contract) and Sigma skill "
                  "descriptions. File them as prioritized goals.")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "AGENTS.md").write_text("# Agent rules\n", encoding="utf-8")
        base = _local_sdlc(root)
        rc = bs.main(["brainstorm.py", str(base), invocation])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["form"] == "text" and out["confident"] is True
    assert out["text"] == invocation
    assert out["file"] is None


# ---------------------------------------------------------------------------- form 4: direct issue number


def test_direct_issue_number_resolves_from_the_local_corpus():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [
            _rec(42, "Add a config.json editor UI", "Users want an in-app editor for config.json."),
        ])
        out = bs.resolve_target("#42", sdlc_dir=str(base))
    assert out["form"] == "issue_number"
    assert out["confident"] is True
    assert out["issue"]["ref"] == "42"
    assert out["issue"]["title"] == "Add a config.json editor UI"
    assert out["candidates"] == []


def test_direct_issue_number_accepts_a_light_wrapper_phrase():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [_rec(7, "Some issue")])
        out = bs.resolve_target("issue #7", sdlc_dir=str(base))
    assert out["form"] == "issue_number" and out["issue"]["ref"] == "7"


def test_direct_issue_number_falls_back_to_a_live_fetch_when_not_mirrored():
    bs = _mod()

    def fake_run(args, binary="gh"):
        assert args[:3] == ["issue", "view", "99"]
        return json.dumps({"number": 99, "title": "Live-fetched title", "body": "Live body"})

    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [])   # empty mirror -> not in local corpus
        out = bs.resolve_target("#99", sdlc_dir=str(base), run=fake_run)
    assert out["form"] == "issue_number"
    assert out["confident"] is True
    assert out["issue"] == {"ref": "99", "title": "Live-fetched title", "body": "Live body", "score": None}


# ---------------------------------------------------------------------------- form 3: fuzzy issue reference


_ISSUE_TITLE = "Add a UI for editing config.json"
_ISSUE_BODY = "We keep hand-editing config.json in production. Ship a small settings editor UI."


def test_fuzzy_reference_resolves_confidently_to_the_matching_issue():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [
            _rec(20, _ISSUE_TITLE, _ISSUE_BODY),
            _rec(21, "Improve the render farm queue scheduler", "Batches idle workers overnight."),
        ], backlog_check_cfg={"dup_threshold": 0.4})
        invocation = "check the story issue where we added a story about a config.json settings editor UI"
        out = bs.resolve_target(invocation, sdlc_dir=str(base))
    assert out["form"] == "issue_reference"
    assert out["confident"] is True
    assert out["issue"]["ref"] == "20"
    assert out["issue"]["score"] >= 0.4
    assert all(c["ref"] != "20" for c in out["candidates"])   # winner isn't repeated as a runner-up


def test_fuzzy_reference_candidates_are_capped_at_top_k():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        records = [_rec(n, _ISSUE_TITLE + f" variant {n}", _ISSUE_BODY) for n in range(1, 13)]
        base = _github_sdlc(pathlib.Path(d), records, backlog_check_cfg={"dup_threshold": 0.999, "top_k": 3})
        out = bs.resolve_target("a config.json settings editor UI story", sdlc_dir=str(base))
    # dup_threshold is unreachable on purpose here -> stays ambiguous, but top_k still caps the list
    assert out["form"] == "ambiguous"
    assert len(out["candidates"]) <= 3


# ---------------------------------------------------------------------------- ambiguity: form 1 vs form 3


def test_weak_overlap_is_reported_as_ambiguous_not_silently_guessed():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [
            _rec(30, "Redesign the onboarding checklist widget",
                 "A checklist widget for new-user onboarding, plus a settings toggle."),
        ], backlog_check_cfg={"dup_threshold": 0.72})
        # shares only "settings"/"widget"-adjacent vocabulary -- real but weak overlap, well under 0.72
        invocation = "maybe add a settings widget somewhere in the app"
        out = bs.resolve_target(invocation, sdlc_dir=str(base))
    assert out["form"] == "ambiguous"
    assert out["confident"] is False
    assert out["text"] == invocation                 # the free-text reading is still carried
    assert out["candidates"], "the candidate existing-issue reading must also be carried"
    assert out["issue"] is None                       # neither reading has been committed to


# ---------------------------------------------------------------------------- malformed / unresolvable


def test_nonexistent_markdown_file_fails_gracefully_not_a_crash():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        base = _local_sdlc(root)
        out = bs.resolve_target("check the missing.md file and help me plan it",
                                 sdlc_dir=str(base), search_root=str(root))
    assert out["form"] == "unresolved"
    assert out["confident"] is False
    assert "file_not_found" in out["degraded"]
    assert out["error"] and "missing.md" in out["error"]


def test_nonexistent_issue_number_fails_gracefully_not_a_crash():
    bs = _mod()

    def failing_run(args, binary="gh"):
        raise RuntimeError("gh issue view 9999 failed: no such issue")

    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [_rec(1, "Unrelated issue")])
        out = bs.resolve_target("#9999", sdlc_dir=str(base), run=failing_run)
    assert out["form"] == "unresolved"
    assert out["confident"] is False
    assert "issue_not_found" in out["degraded"]
    assert out["error"] and "9999" in out["error"]


def test_a_non_dict_config_is_normalized_instead_of_crashing():
    """A structurally malformed `config` (not even a dict -- a caller error) must not propagate an
    AttributeError out of resolve_target the first time anything calls `config.get(...)`; it is
    normalized to `{}` once, up front, same fail-open contract as the rest of the SDLC toolchain."""
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _local_sdlc(pathlib.Path(d))
        out = bs.resolve_target("some free-form idea about nothing in particular",
                                 sdlc_dir=str(base), config=["not", "a", "dict"])
    assert out["form"] == "text"
    assert out["confident"] is True
    assert "bad_config" in out["degraded"]


def test_corpus_build_failure_degrades_instead_of_crashing():
    """A `config` that IS a dict but has a malformed nested value (`discovery` is a string, not an
    object) makes `backlog_check._build_corpus` raise inside `mirror.is_github_mode`'s own
    `config.get("discovery").get(...)` chain -- `resolve_target` must not propagate that either; it
    degrades to a bare text reading, same fail-open contract as backlog_check.cross_check's own
    outer try/except."""
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _local_sdlc(pathlib.Path(d))
        out = bs.resolve_target("some free-form idea about nothing in particular",
                                 sdlc_dir=str(base), config={"discovery": "not-an-object"})
    assert out["form"] == "text"
    assert out["confident"] is True
    assert "corpus_error" in out["degraded"]


def test_live_fetch_with_a_malformed_response_is_treated_as_not_found():
    bs = _mod()

    def fake_run(args, binary="gh"):
        return json.dumps({"title": "no number field in this payload"})

    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [])
        out = bs.resolve_target("#123", sdlc_dir=str(base), run=fake_run)
    assert out["form"] == "unresolved"
    assert "issue_not_found" in out["degraded"]


def test_markdown_search_skips_vendored_dirs_and_directory_lookalikes():
    """A same-named file sitting inside a vendored/generated directory (node_modules) must not win
    over the real one, and a same-named DIRECTORY (not a file) must never be returned as a match."""
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "node_modules" / "some-pkg").mkdir(parents=True)
        (root / "node_modules" / "some-pkg" / "ui.md").write_text("vendored, must be ignored\n",
                                                                    encoding="utf-8")
        (root / "weird").mkdir()
        (root / "weird" / "ui.md").mkdir()   # a directory that happens to be named like the file
        (root / "docs").mkdir()
        (root / "docs" / "ui.md").write_text("the real one\n", encoding="utf-8")
        base = _local_sdlc(root)
        out = bs.resolve_target("check the ui.md file please", sdlc_dir=str(base), search_root=str(root))
    assert out["form"] == "file"
    assert out["file"]["content"] == "the real one\n"


def test_markdown_search_root_that_does_not_exist_fails_gracefully():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        base = _local_sdlc(root)
        missing_root = root / "does-not-exist-at-all"
        out = bs.resolve_target("check the ui.md file please", sdlc_dir=str(base),
                                 search_root=str(missing_root))
    assert out["form"] == "unresolved"
    assert "file_not_found" in out["degraded"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root reads mode-000 files, so the unreadable-file case cannot arise")
def test_unreadable_markdown_file_fails_gracefully_not_a_crash():
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        (root / "docs").mkdir()
        target = root / "docs" / "ui.md"
        target.write_text("secret plan\n", encoding="utf-8")
        os.chmod(target, 0o000)
        base = _local_sdlc(root)
        try:
            out = bs.resolve_target("check the ui.md file please", sdlc_dir=str(base),
                                     search_root=str(root))
        finally:
            os.chmod(target, 0o644)   # restore so the tmp dir can be cleaned up
    assert out["form"] == "unresolved"
    assert "file_unreadable" in out["degraded"]


def test_invocation_with_only_stopwords_resolves_as_text_against_a_nonempty_corpus():
    """`_fuzzy_candidates`'s own empty-query-token guard: an invocation that tokenizes to nothing
    (stopwords only) must not spuriously match anything, even against a real, non-empty corpus."""
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _github_sdlc(pathlib.Path(d), [_rec(1, _ISSUE_TITLE, _ISSUE_BODY)])
        out = bs.resolve_target("the a an", sdlc_dir=str(base))
    assert out["form"] == "text"
    assert out["confident"] is True


# ---------------------------------------------------------------------------- CLI


def test_cli_main_prints_a_valid_json_pack(capsys):
    bs = _mod()
    with tempfile.TemporaryDirectory() as d:
        base = _local_sdlc(pathlib.Path(d))
        rc = bs.main(["brainstorm.py", str(base), "a free text idea"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == bs.SCHEMA and out["form"] == "text"


def test_cli_main_usage_on_missing_args(capsys):
    bs = _mod()
    rc = bs.main(["brainstorm.py"])
    assert rc == 2
