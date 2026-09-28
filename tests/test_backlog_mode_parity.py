"""Both backlog modes must reach the same place. Goals are local files under .sdlc/goals/ OR GitHub
issues labelled `sdlc:goal` — the SDLC is identical either way, so a phase that only knows how to
record itself to the filesystem silently loses its audit trail for every github-mode team.

These pin the mode-aware seams of the phases added on top of the original seven. The failure they
guard is quiet by nature: nothing errors, the work just becomes invisible to everyone not sitting at
the author's working copy."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _skill(name):
    return (ROOT / "skills" / name / "SKILL.md").read_text()


def test_research_records_the_lane_in_both_modes():
    """A GitHub issue has no frontmatter, so `lane: auto` -> `lane: small` has nowhere to land.
    Without the github branch the lane is computed and then dropped on the floor."""
    t = _skill("agrim-research")
    assert "frontmatter" in t                              # local: the goal file
    assert "issue" in t and "no frontmatter" in t          # github: the phase note instead


def test_research_note_carries_what_a_teammate_needs():
    """The dossier is a local working file in both modes; the note is the only thing a shared
    backlog ever sees."""
    t = _skill("agrim-research")
    assert "loop.py" in t and "note" in t
    assert "issue timeline" in t and ".sdlc/journey/" in t
    assert "blocking question" in t.lower()


def test_plan_review_records_rejections_where_the_audit_trail_lives():
    """Accepted findings show up in the revised plan; the reasoning for OVERRULING a reviewer exists
    nowhere else."""
    t = _skill("agrim-plan-review")
    assert "loop.py" in t and "note" in t
    assert "journey" in t and "github" in t.lower()


def test_align_reads_both_backlogs():
    t = _skill("agrim-align")
    assert ".sdlc/goals/*.md" in t and "status: done" in t          # local
    assert "sdlc:goal" in t and "closed" in t                       # github
    assert "sdlc:parked" in t                                       # abandoned work still spent effort


def test_align_files_nothing_to_a_shared_backlog():
    """Drift is a question about direction, not about any one issue — there is no card to move, and
    a scout that writes to a team's backlog unasked stops being trusted the first time it's wrong."""
    t = _skill("agrim-align")
    assert "files nothing" in t
    assert "agrim-radar" in t                                        # same restraint, stated


def test_align_due_counter_is_not_blind_in_github_mode():
    """.sdlc/goals/ stays empty when goals are issues, so a done-file tally alone would never fire."""
    src = (ROOT / "skills" / "agrim-status" / "scripts" / "status.py").read_text()
    assert "max(done, iteration)" in src
    assert "github mode" in src and "iteration" in src


def test_local_only_surfaces_are_genuinely_local_in_both_modes():
    """Not everything needs a github branch: standing docs and plans are files whichever backlog is
    in use. Pinning this stops a well-meaning 'add github support' pass from inventing one.

    Scoped to `hygiene()` and its own helpers specifically (not the whole doctor.py module): #389
    added a DIFFERENT, explicitly github-gated code path (`check()`'s `_dependency_marker_scan`,
    reachable only behind `disc.get("source") == "github"`) that legitimately calls `gh issue` to
    catch a dependency marker left only as a comment. That is not a backlog opinion leaking into
    hygiene — hygiene's own source must still never reference it."""
    for name in ("agrim-doctor", "agrim-retro"):
        t = _skill(name)
        assert ".sdlc/" in t, f"{name} lost its .sdlc path reference"
    import importlib.util, inspect
    spec = importlib.util.spec_from_file_location(
        "doctor", ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py")
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)
    hygiene_src = "".join(inspect.getsource(fn) for fn in
                          (doctor.hygiene, doctor._standing_docs, doctor._stale_paths,
                           doctor._dangling_links, doctor._detail,
                           # the resolver pair sits in hygiene's call chain too, and both had
                           # escaped this pin -- `_unresolved_reason` is where the verdict is now
                           # decided, `_under` where the path is (#600)
                           doctor._unresolved_reason, doctor._under))
    assert "gh issue" not in hygiene_src     # hygiene scans files; it has no backlog opinion
