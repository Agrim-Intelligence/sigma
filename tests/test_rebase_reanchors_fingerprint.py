"""The loop's OWN rebase must not invalidate the evidence it just accepted.

`work.py merge` calls `state.done_refusal` (work.py:3396), then rebases a BEHIND PR
(`_reconcile_behind`, work.py:3406), then lands. The rebase rewrites files -- and `rebase()`'s
CHANGELOG union-merge rescue rewrites CHANGELOG.md *deterministically*, by design, because that is
"the single most frequent conflict in this repo". So the content fingerprint moves AFTER the gate
passed and BEFORE `record done` runs, and `record done` then exits 4 on a PR that has ALREADY
merged: the untested content ships and only the bookkeeping breaks, which is the worst of both.

The gate exists to catch an UNVERIFIED edit. A rebase the loop performed itself is not that -- it is
a known transformation of verified content onto a moved base. So the loop re-anchors its own
rebase rather than refusing it. An edit made by anything else still refuses, which is the control.
"""
import importlib.util, json, pathlib, subprocess

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, S / (name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, check=True)
    g("init", "-q", "-b", "main")
    g("config", "user.email", "t@example.com"); g("config", "user.name", "t")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n")
    (root / "code.py").write_text("x = 1\n")
    g("add", "-A"); g("commit", "-qm", "base")
    return root, g


def _evidence(sdlc, goal, root, base_ref, files_fp):
    d = sdlc / "state" / "verify"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{goal}.json").write_text(json.dumps({
        # no "run": this suite exercises the CONTENT gate, not the run-attribution one
        "exit": 0, "at": 1.0, "root": str(root), "verify_state": "verified",
        "content": files_fp}))


def test_a_rebase_that_rewrites_changelog_invalidates_the_evidence(tmp_path):
    """The regression, reproduced: this is what `merge()` does to itself on every BEHIND PR."""
    state = _load("state")
    root, g = _repo(tmp_path)
    sdlc = root / ".sdlc"; sdlc.mkdir()
    (root / "code.py").write_text("x = 2\n")          # the goal's own change
    fp = state.content_fingerprint(str(root), "HEAD", ".sdlc")
    _evidence(sdlc, "77", root, "HEAD", fp)
    assert state.done_refusal(str(sdlc), "77") is None, "clean tree must pass"

    # what rebase()'s CHANGELOG union-merge rescue does, deterministically
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### theirs\n### mine\n")
    refusal = state.done_refusal(str(sdlc), "77")
    assert refusal and "CHANGELOG.md" in refusal, refusal


def test_reanchor_restores_the_gate_after_the_loops_own_rebase(tmp_path):
    """The fix: re-anchoring makes the loop's own rebase invisible to the gate again."""
    state = _load("state")
    root, g = _repo(tmp_path)
    sdlc = root / ".sdlc"; sdlc.mkdir()
    (root / "code.py").write_text("x = 2\n")
    _evidence(sdlc, "77", root, "HEAD", state.content_fingerprint(str(root), "HEAD", ".sdlc"))
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### theirs\n### mine\n")
    assert state.done_refusal(str(sdlc), "77") is not None

    assert state.reanchor_content(str(sdlc), "77") is True
    assert state.done_refusal(str(sdlc), "77") is None, "re-anchor did not restore the gate"


def test_reanchor_does_not_launder_an_edit_made_after_it(tmp_path):
    """THE CONTROL. Re-anchoring is not an amnesty: an edit after the re-anchor still refuses, or
    the gate would be defeated by anything that rebases."""
    state = _load("state")
    root, g = _repo(tmp_path)
    sdlc = root / ".sdlc"; sdlc.mkdir()
    (root / "code.py").write_text("x = 2\n")
    _evidence(sdlc, "77", root, "HEAD", state.content_fingerprint(str(root), "HEAD", ".sdlc"))
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n\n### merged\n")
    state.reanchor_content(str(sdlc), "77")
    assert state.done_refusal(str(sdlc), "77") is None

    (root / "code.py").write_text("x = 999\n")        # an UNVERIFIED edit, after the re-anchor
    refusal = state.done_refusal(str(sdlc), "77")
    assert refusal and "code.py" in refusal, refusal


def test_reanchor_is_a_no_op_when_there_is_no_content_block(tmp_path):
    """Legacy evidence (all 333 files on this repo's disk) carries no `content`. Re-anchoring must
    not invent one -- that would turn an ungated goal into a falsely-gated one."""
    state = _load("state")
    root, _ = _repo(tmp_path)
    sdlc = root / ".sdlc"; sdlc.mkdir()
    d = sdlc / "state" / "verify"; d.mkdir(parents=True)
    (d / "77.json").write_text(json.dumps({"exit": 0, "at": 1.0, "root": str(root), "verify_state": "verified"}))
    assert state.reanchor_content(str(sdlc), "77") is False
    assert "content" not in json.loads((d / "77.json").read_text())


def test_the_rebase_path_actually_calls_the_reanchor():
    """Structural, deliberately: the unit tests above prove `reanchor_content` WORKS, and would all
    still pass if nothing ever called it. `_reconcile_behind` is the only site that must, and a
    behavioural test of it needs a live GitHub gate. So this pins the call itself -- the reviewer's
    own finding on this branch was two guards that no test noticed."""
    src = (S / "work.py").read_text()
    start = src.index("def _reconcile_behind(")
    end = src.index("\ndef ", start + 10)
    body = src[start:end]
    assert "state.reanchor_content(sdlc_dir, goal)" in body, \
        "_reconcile_behind rebases without re-anchoring — record done will exit 4 after the merge"
    # and it must come AFTER the success check, not before: re-anchoring a failed rebase would
    # anchor to a conflicted tree.
    assert body.index('if not out.startswith("rebased")') < body.index("state.reanchor_content"), \
        "the re-anchor runs before the rebase is known to have succeeded"
