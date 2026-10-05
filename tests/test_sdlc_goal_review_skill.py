"""#1828: the feature-ification hook is pure SKILL.md prose -- `sigma-goal-review`'s step 5 invokes
`sigma-define`'s own already-tested scripts (`define.py open/declare`, `brainstorm.py`'s
`resolve_target`) plus the already-tested `sources.append_to_body`, with no new Python anywhere in
the tree. Every primitive this step composes already has its own full suite
(`tests/test_define.py`, `tests/test_sources.py`, `tests/test_features.py`,
`tests/test_sdlc_define_integration.py`, `tests/test_brainstorm.py`) unmodified by this change, so
the only thing left to pin is that the OPERATIONAL CONTRACT actually documents the composition --
the same reasoning `tests/test_sdlc_loop_skill_merge_doc.py` and `test_design_check.py`'s
`test_sdlc_goal_design_skill_exists_and_documents_the_handoff` already apply to their own skills'
prose. Stable anchors only (structural claims, script names, ordering) -- not prose wording."""
import importlib.util
import pathlib

from skill_corpus import skill_corpus

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SKILL_DIR = _ROOT / "skills" / "sigma-goal-review"
#: #2108 split the file into a body + references/*.md — this is "the skill", not just the file
#: (see tests/skill_corpus.py's own docstring: the subject was never the FILE, it was the SKILL).
SKILL = skill_corpus("sigma-goal-review")
FRONTMATTER = SKILL.split("---", 2)[1]
#: The always-attached BODY alone, for a pin that is specifically about content that stayed there.
#: `## 6. Handoff` moved nowhere (#2108 kept the whole short section in the body), so a pin scoped
#: to "the Handoff section" must read this, never the whole corpus: `SKILL.split("## 6. Handoff",
#: 1)[-1]` on the corpus would capture the body's tail PLUS all four reference files appended after
#: it, silently widening "the Handoff section" to "everything" -- the same body-only exception
#: `tests/skill_corpus.py`'s own docstring already carves out for `test_emit_prose.py` and
#: `test_sdlc_loop_skill_session_pid.py`.
_BODY = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

#: One numbered step of the ORIGINAL body relocated wholesale, verbatim, into its own reference
#: file at #2108 — each still carries its original heading (or, for step 1/2/4/5's lettered
#: sub-steps, its original `###`/`**a.`-style markers) as the first content, in unchanged relative
#: order, so every scoped `.split(marker, 1)` extraction below keeps working unchanged against the
#: correct constant. `SKILL` (the whole corpus, body-first) is still right for anything that is
#: either simple `"phrase" in SKILL` membership or an `.index()` ORDERING check across headings
#: that stayed in the body (see AGENTS.md's "relocation, not rewrite" — headings and short spine
#: paragraphs stayed put; only the branch-specific procedure moved).
READING_TARGET = (_SKILL_DIR / "references" / "reading-the-target.md").read_text(encoding="utf-8")
ADJUDICATION = (_SKILL_DIR / "references" / "adjudication.md").read_text(encoding="utf-8")
CONFIRM = (_SKILL_DIR / "references" / "confirm.md").read_text(encoding="utf-8")
FEATURE = (_SKILL_DIR / "references" / "feature-ification.md").read_text(encoding="utf-8")


def _mod(name, where):
    spec = importlib.util.spec_from_file_location(name, where / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# The LIVE modules the prose makes claims about -- pinned so a change to either fails this suite
# rather than quietly turning the skill's instructions into fiction.
blocker_scan = _mod("blocker_scan", _ROOT / "skills" / "sigma-loop" / "scripts")
compile_plan = _mod("compile_plan", _ROOT / "skills" / "sigma-scope" / "scripts")


def test_documents_the_feature_ification_step():
    assert "Feature-ification" in SKILL


def test_the_ask_is_exactly_once():
    assert "exactly once" in SKILL


def test_feature_ification_sits_after_confirm_and_before_handoff():
    # Ordering is the point: it must be unreachable from a REJECT (step 3 returns before step 4
    # even runs) and it must not be folded into the Handoff section that follows it.
    confirm_at = SKILL.index("## 4. On CONFIRM")
    feature_at = SKILL.index("## 5. Feature-ification")
    handoff_at = SKILL.index("## 6. Handoff")
    assert confirm_at < feature_at < handoff_at


def test_documents_the_epic_level_ticket_resolution_for_both_4b_and_4c():
    # "the Epic" is report["epic"] when 4b sliced, or <n> itself when 4c did not -- contract §3's
    # own correction (Spec and Epic are one ticket), applied to WHICH ticket this step targets.
    assert 'report["epic"]' in SKILL
    assert "if 4c applied" in SKILL


def test_documents_the_sdlc_define_scripts_invoked():
    assert "define.py open" in SKILL
    assert "sigma-define/scripts/define.py" in SKILL and "declare .sdlc" in SKILL
    assert "brainstorm.py" in SKILL
    assert "resolve_target" in SKILL


def test_documents_why_define_py_stamp_does_not_apply_to_already_filed_tickets():
    assert "does **not** apply here" in SKILL
    assert "append_to_body" in SKILL


def test_documents_declaring_every_ticket_explicitly_not_relying_on_self_heal_alone():
    # the epic never carries sdlc:goal and is never picked, so attach_at_pick's self-heal --
    # real, and correctly relied upon for children -- cannot be the epic's only path to a label.
    assert "attach_at_pick" in SKILL
    assert "never picked" in SKILL


def test_documents_the_unattended_default_is_decline():
    assert "treat it as decline" in SKILL
    assert "no deletion path" in SKILL


# ================================================================ 2026-09-08: the retrofit-path gate
# Reported on the 2026-09-08 team call: Sigma used to run a picked goal end to end with no human
# touchpoint, and started stopping mid-run to ask "should I proceed" once the Dossier pipeline
# shipped. Traced to here -- step 5's ask fired on BOTH of goal-review's own two paths (the file's
# own §-top distinction, lines 9-15), when only the Product path (a human runs /sigma-goal-review
# directly, before /sigma-loop is ever involved) should ever ask anything. On the Tech-side retrofit
# path the loop is already running unattended by construction, and feature-ification is a
# product-tier decision, not the "genuine technical decision" AGENTS.md reserves the loop's one
# legitimate stop for -- so that path must never reach AskUserQuestion at all, mechanically, not by
# the model guessing whether anyone is watching the transcript.


def test_feature_ification_step5_gates_the_ask_on_which_path_this_pass_is_on():
    section = FEATURE.split("## 5. Feature-ification", 1)[-1].split("## 6. Handoff", 1)[0]
    assert "ONLY on the Product path" in section
    assert "Tech-side retrofit path" in section
    assert "skip the ask entirely" in section


def test_retrofit_path_reaches_deferred_without_ever_asking():
    section = FEATURE.split("## 5. Feature-ification", 1)[-1].split("## 6. Handoff", 1)[0]
    # the path check must be READ BEFORE the ask is described, not after -- an instruction that
    # asks first and disclaims second is the exact bug this pins against
    gate_at = section.index("Tech-side retrofit path")
    ask_at = section.index('"Promote `<epic>`')
    assert gate_at < ask_at, "the path check must come before the ask, not after it"


def test_accept_and_decline_are_now_scoped_to_the_product_path_only():
    # Accept/Decline require a human to have actually been asked, which the retrofit path never
    # does -- both must say so, or a reader could believe either is reachable from that path.
    section = FEATURE.split("## 5. Feature-ification", 1)[-1].split("## 6. Handoff", 1)[0]
    assert "**Accept** (Product path only)" in section
    assert "**Decline** (Product path only" in section


def test_deferred_is_reached_two_ways_retrofit_always_or_product_path_unattended():
    section = FEATURE.split("## 5. Feature-ification", 1)[-1].split("## 6. Handoff", 1)[0]
    assert "reached two ways" in section
    assert "the Retrofit path (always" in section


def test_documents_no_base_override_is_needed():
    # work.base is already "main" in this repo's own config, so define.py open's own default
    # precedence resolves correctly with no --base flag -- pinned so a future config change to
    # work.base is forced to reconsider this line rather than silently going stale.
    assert '"main"' in SKILL


def test_handoff_section_no_longer_lists_1828_as_undone():
    handoff = _BODY.split("## 6. Handoff", 1)[-1]
    assert "#1828" not in handoff


def test_handoff_section_still_lists_1827_untouched():
    # #1827 (the Originates-from-Story marker) is separately tracked and still open -- this issue
    # must not silently absorb or remove that bullet while touching the section around it.
    handoff = _BODY.split("## 6. Handoff", 1)[-1]
    assert "#1827" in handoff
    assert "Originates from Story #N" in handoff


# ==================================================================== #1918: the decline path
# §5a routed the no-human case to "treat it as decline and say why in the outcome comment (step g)"
# while §5g said "On decline (step a), no comment is needed" -- the same path told both to comment
# and not to. §5g's only template was a PROMOTION string, which cannot express a decline at all.


def test_step_g_no_longer_says_a_decline_needs_no_comment():
    assert "no comment is needed" not in SKILL


def test_step_g_runs_on_every_answer_including_both_declines():
    g = FEATURE.split("**g. Comment the outcome", 1)[-1]
    assert "three answers" in SKILL
    assert "*On accept, after f:*" in g
    assert "*On an explicit human decline:*" in g
    assert "no human available" in g.lower()


def test_step_g_carries_a_decline_template_not_only_a_promotion_one():
    g = FEATURE.split("**g. Comment the outcome", 1)[-1]
    assert "feature-ification: promoted to feature:<name>" in g
    assert "feature-ification: declined" in g
    assert "feature-ification: deferred" in g


def test_the_unattended_decline_is_recorded_as_deferred_not_as_a_refusal():
    """The two are different facts and a later reader acts on them differently -- "asked and said
    no" is settled, "nobody was there to ask" is an open question that has to be re-put."""
    g = FEATURE.split("**g. Comment the outcome", 1)[-1]
    deferred = g.split("feature-ification: deferred", 1)[-1]
    assert "no human available on this pick" in deferred
    assert "/sigma-define" in deferred


def test_every_decline_template_names_the_recovery_gesture():
    g = FEATURE.split("**g. Comment the outcome", 1)[-1]
    for template in ("feature-ification: declined", "feature-ification: deferred"):
        assert "/sigma-define" in g.split(template, 1)[-1].split("```", 1)[0]


# ============================================================ #1919: the report is read as JSON
# Steps 4b/4e are written against report["epic"] / report["issues"], but the mandated invocation was
# the CLI, which printed prose in topological order with the warnings on stderr.


def test_the_compile_plan_invocation_asks_for_json():
    assert "--actionable --json" in SKILL


def test_the_skill_says_why_the_prose_output_could_not_be_used():
    assert "topological" in SKILL
    assert "created 's1' as #3" in SKILL          # the actual prose shape it cannot parse


def test_the_skill_still_tells_the_reader_to_read_stderr():
    """The JSON must not become an excuse to stop looking at the stream that says why a child did
    not land -- the flag deliberately leaves those lines where they were."""
    assert "read them too" in SKILL


def test_the_skill_says_where_the_plan_json_and_its_report_live():
    assert ".sdlc/state/goal-review/" in SKILL
    assert "RUNTIME_IGNORES" in SKILL


def test_the_json_flag_the_skill_mandates_actually_exists_in_the_cli():
    """The prose claim and the code, checked against each other -- not two documents agreeing by
    hand. `--json` is parsed by `main`, and the usage line advertises it."""
    import inspect
    source = inspect.getsource(compile_plan.main)
    assert '"--json"' in source
    assert "--json" in compile_plan._USAGE


# ===================================================== #1920: the phantom-blocker hazard is warned
# blocker_scan's check-time TRIGGERS match ordinary English within 40 characters of a #N, and a
# confident finding makes blockers.resolve mutate a THIRD, unrelated issue.


def _warning_block():
    return CONFIRM.split("**Warning — a hand-authored body", 1)[-1].split("Then run the control", 1)[0]


def test_the_child_body_instruction_carries_an_explicit_phantom_blocker_warning():
    assert "phantom blocker" in SKILL
    assert "#1920" in SKILL


def test_the_warning_names_every_live_trigger_word():
    """Pinned against the LIVE tuple: adding a trigger to `blocker_scan.TRIGGERS` without naming it
    here leaves the skill teaching a safe phrasing that is no longer safe."""
    block = _warning_block()
    for trigger in blocker_scan.TRIGGERS:
        assert f"`{trigger}`" in block, f"{trigger!r} is a live trigger the warning does not name"


def test_the_warning_states_the_live_proximity_window():
    assert "40 characters" in _warning_block()
    assert blocker_scan._BLOCK_RE.pattern.count("{0,40}") == 1


def test_the_warning_states_the_consequence_is_a_write_to_a_third_issue():
    block = _warning_block()
    assert "blockers.resolve" in block
    assert "third, unrelated ticket" in block


def test_the_warning_points_at_the_blocked_by_edge_as_the_correct_channel():
    assert "`blocked_by` edge, never as a sentence" in SKILL


def test_the_skill_supplies_a_runnable_control_and_a_way_to_see_it_fail():
    assert "_BLOCK_RE" in SKILL and "strip_unpark_qa" in SKILL
    assert "Prove it can fail before you trust a `clean`" in SKILL


# ---- the control itself, run for real: the safe shapes must be clean, the named unsafe one must not


def _scan(text):
    return blocker_scan._BLOCK_RE.findall(blocker_scan.strip_unpark_qa(text))


def _real(text):
    """A placeholder-free copy, so the live regex sees the `#N` the reader will actually write."""
    return text.replace("#N", "#1826").replace("<n>", "1826")


def _pointer_bullet():
    """The bullet that tells the author what to write and what never to write. Both halves are read
    OUT OF THE PROSE rather than restated here: a control that scans strings the test itself holds
    would keep passing while the skill drifted to recommending something unsafe -- which is exactly
    what this control did on its first attempt, and why it is written this way now."""
    marker = "- **Write the pointers in that same shape**"
    # `str.split` on a missing separator returns the WHOLE text, which would silently widen this
    # to the warning paragraph above it -- and its `Tracks #N` examples scan clean, so both
    # controls would keep passing with the bullet deleted. Seen happen; hence the assert.
    assert marker in SKILL, "the pointer-shape bullet these controls read is gone"
    body = CONFIRM.split(marker, 1)[-1]
    assert "- **Express a real dependency" in body
    return body.split("- **Express a real dependency", 1)[0]


def _recommended_shapes():
    import re
    head = _pointer_bullet().split("Never", 1)[0]
    return [f for f in re.findall(r"`([^`]+)`", head) if "#" in f or f.startswith("Design:")]


def _forbidden_forms():
    import re
    tail = _pointer_bullet().split("Never", 1)[-1]
    return re.findall(r'\*"([^"]+)"\*', tail)


def test_control_every_pointer_shape_the_skill_recommends_mints_no_edge():
    """The skill tells you to write these lines into every child body. Run the LIVE regex over the
    shapes the PROSE names, so rewording the recommendation into something unsafe fails here."""
    shapes = _recommended_shapes()
    assert len(shapes) >= 2, f"could not read the recommended shapes out of the skill: {shapes!r}"
    for shape in shapes:
        assert _scan(_real(shape)) == [], f"the skill recommends {shape!r}, which mints an edge"


def test_control_every_never_write_this_form_the_skill_lists_really_does_match():
    """The deliberately-red half, also read out of the prose. Without it the assertion above proves
    only that a regex exists -- a control never seen fail proves nothing (`AGENTS.md`). It doubles
    as a drift check on the list: a phrasing that has fallen out of the vocabulary would be
    teaching superstition."""
    forms = _forbidden_forms()
    assert len(forms) >= 5, f"could not read the forbidden forms out of the skill: {forms!r}"
    for form in forms:
        assert _scan(_real(form)), f"{form!r} is listed as unsafe but the live regex misses it"


def test_control_the_unsafe_example_the_skill_names_really_does_match():
    """The one worked example the reader is told to paste in to watch the control go red."""
    unsafe = "this needs the model field from #3"
    assert unsafe in SKILL, "the skill no longer shows the example this control is calibrated on"
    assert _scan(unsafe) == [("needs", "3")]


def test_control_the_marker_compile_plan_writes_itself_is_supposed_to_match():
    """The one #N form that SHOULD register -- the skill says so, and it is why a real dependency
    goes through the edge rather than the prose."""
    assert _scan("**Blocked by:** #7") == [("Blocked by", "7")]
    assert "is SUPPOSED to match" in SKILL


# ---- the same hazard, on the COMMENT half: 2c mandates open items be written into an outcome
# comment, and the check-time scan's haystack is the body excerpt PLUS the goal's comment text
# (`backlog_check._goal_comment_text` -> `_explicit_blockers(extra_text=...)`, one shared
# `_blocker_haystack`). A rule scoped to bodies leaves the path 2c newly created uncovered.


def test_control_a_naturally_worded_open_item_line_mints_an_edge():
    """Run red before the rule was written: this is 2c's own instruction followed literally -- one
    line per open item, naming the slice it bears on -- and the LIVE regex reads it as a confident
    dependency on a third ticket."""
    line = "open: naming of the writer -- bears on #1831, which is waiting on #1830 landing first."
    assert _scan(line) == [("waiting on", "1830")]


def test_the_warning_covers_comment_text_and_not_only_bodies():
    block = _warning_block()
    assert "comment" in block.lower(), "the warning is scoped to bodies; comments are scanned too"
    assert "_goal_comment_text" in block


def test_the_open_item_lines_in_the_outcome_comment_carry_the_wording_rule():
    marker = "- **Name every open item in step 4d's outcome comment**"
    assert marker in SKILL, "2c's open-item bullet these controls read is gone"
    bullet = ADJUDICATION.split(marker, 1)[-1].split("- **Where a slice cannot be FINISHED", 1)[0]
    assert "phantom" in bullet.lower()


# ============================================================== #1921: the adjudication rubric
# A full-mode goal-design is SUPPOSED to surface open doubts, so the healthy Stage 1 output was the
# case the binary CONFIRM/REJECT handled worst.


def test_the_binary_criterion_that_could_not_express_an_open_question_is_gone():
    assert "the doubts/blockers are genuinely resolved" not in SKILL


def test_the_three_buckets_are_named_and_defined():
    for bucket in ("**resolved**", "**open, not blocking**", "**blocking**"):
        assert bucket in SKILL


def test_open_but_not_blocking_does_not_bar_confirm():
    assert "do NOT bar CONFIRM" in SKILL


def test_blocking_has_an_operational_test_not_a_feeling():
    assert "name the slice that cannot start" in SKILL


def test_the_skill_explains_why_the_verdict_stays_binary():
    """Grounded in the mechanism, not in taste: the overlay is a presence check with no third
    value, so a third verdict would exist only in prose."""
    assert "Why there is no third verdict" in SKILL
    assert '"sdlc:designed" in names' in SKILL
    assert "mark_designed" in SKILL


def test_open_items_are_carried_rather_than_dropped():
    assert "### 2c. Carrying the open items" in SKILL
    assert "open questions carried" in SKILL
    assert "0 open questions carried" in SKILL


def test_the_outcome_comment_template_carries_the_count():
    d = CONFIRM.split("**d. Comment on `#<n>`", 1)[-1].split("## 5.", 1)[0]
    assert "goal-review: CONFIRMED (<k> open questions carried)" in d


# ================================================================= #1922: priority is not invented


def test_priority_is_omitted_rather_than_invented():
    assert "**Omit `priority`" in SKILL
    assert "Do not derive one either" in SKILL


def test_the_default_the_skill_names_is_the_live_one():
    """Pinned against `compile_plan.DEFAULT_PRIORITY` itself -- a change to the constant has to come
    back through this prose rather than silently outdating it."""
    assert f'`"{compile_plan.DEFAULT_PRIORITY}"`' in SKILL
    assert compile_plan.DEFAULT_PRIORITY == compile_plan.handoff.DEFAULT_PRIORITY


def test_sequencing_is_routed_to_blocked_by_edges_not_to_priority():
    assert "sequencing belongs in `blocked_by` edges" in SKILL


# ============================== #2027: and the rule is now enforced, not merely written down
# The 2026-09-01 validation run (story #2017) wrote `"priority": "P2"` into all six children anyway.
# Prose the model can skip is not a control, so the mandated invocation now carries the flag that
# makes `compile_plan.py` refuse the plan outright.


def test_the_compile_plan_invocation_forbids_priority():
    """The INVOCATION, not the file. A file-wide search stays green when the flag is stripped out
    of the command and left behind in the prose that explains it -- which is exactly what the
    control found on the first run of this test, and exactly the state the rule was already in."""
    import re
    blocks = [b for b in re.findall(r"```[a-z]*\n(.*?)```", SKILL, re.S)
              if "compile_plan.py" in b and "--plan" in b]
    assert blocks, "the skill no longer prints a compile_plan.py invocation at all"
    for block in blocks:
        assert "--forbid-priority" in block, block


def test_the_forbid_priority_flag_the_skill_mandates_actually_exists_in_the_cli():
    """The prose claim and the code, checked against each other -- not two documents agreeing by
    hand. Same shape as the `--json` pin one block up."""
    import inspect
    source = inspect.getsource(compile_plan.main)
    assert '"--forbid-priority"' in source
    assert "--forbid-priority" in compile_plan._USAGE


def test_the_backstop_directory_is_the_one_the_skill_mandates():
    """The flag is belt; the plan's own path is braces. Cross-pinned so moving the directory in one
    file fails here rather than silently disarming the backstop."""
    assert "/".join(compile_plan._DOSSIER_PLAN_DIR) in SKILL
    assert "mkdir -p .sdlc/" + "/".join(compile_plan._DOSSIER_PLAN_DIR) in SKILL


def test_the_priority_bullet_itself_says_the_rule_is_enforced_and_how_it_fails():
    """Scoped to the bullet, not the whole file -- `refus` already appears four times elsewhere, so
    a file-wide search would pass without a word of this ever being written. Exit 2 with zero
    tickets is the CORRECT output of a plan that broke the rule; an adopter meeting it for the
    first time has to be able to tell it from a tool failure."""
    bullet = CONFIRM.split("**Omit `priority`", 1)[-1].split("**Warning \u2014 a hand-authored body", 1)[0]
    assert "--forbid-priority" in bullet, bullet
    assert "refus" in bullet, bullet
    assert "exit 2" in bullet, bullet


# ============================================ #1911 (comment): the skill's own allowed-tools


def test_allowed_tools_grants_the_gh_auth_gestures_the_steps_actually_run():
    assert "Bash(gh auth switch *)" in FRONTMATTER
    assert "Bash(gh auth status *)" in FRONTMATTER
    assert "gh auth switch --user" in SKILL


def test_allowed_tools_grants_gh_label_list_and_the_skill_uses_it():
    assert "Bash(gh label list *)" in FRONTMATTER
    assert "gh label list --search" in SKILL


def test_allowed_tools_grants_mkdir_for_the_scratch_directory():
    assert "Bash(mkdir *)" in FRONTMATTER
    assert "mkdir -p .sdlc/state/goal-review" in SKILL


def test_the_label_precondition_runs_before_any_body_marker_is_stamped():
    """The body marker is the half that does not self-heal: a body declaring a unit whose label does
    not exist sets the goal aside with sdlc:needs-label on any repository, adopted or not."""
    e = FEATURE.split("**e. Confirm the label exists", 1)[-1]
    assert e.index("gh label list") < e.index("append_to_body")
    assert "sdlc:needs-label" in e


def test_the_blanket_python3_grant_is_named_rather_than_dressed_up():
    assert "blanket\ngrant" in SKILL or "blanket grant" in SKILL
    assert "not a sandbox" in SKILL


# ========================= #1956: a non-slice token in `Depends on` is adjudicated, never dropped
# A real design pass wrote `3, and B-1` in the slice table's `Depends on` cell -- a slice edge AND a
# Blockers id. Step 4b turns that column into `compile_plan.py` `blocked_by` keys, which name
# SIBLING ISSUES IN THE SAME PLAN and nothing else, so `B-1` had nothing to resolve to and was
# patched away by hand between the two stages with nothing recorded. `sigma-goal-design` §5 now
# forbids it at the source; this end is what happens when one arrives anyway.

_SEAM_MARKER = "- **`Depends on` → `blocked_by`, and a token that is not a slice id"


def _seam_bullet():
    """Read out of the prose, with the marker's presence asserted first -- a `str.split` on a
    missing separator returns the WHOLE text and would silently widen every assertion below to the
    entire skill, which is how `_pointer_bullet` above once passed against a deleted bullet."""
    assert _SEAM_MARKER in SKILL, "the `Depends on` seam rule is gone from step 4b"
    body = CONFIRM.split(_SEAM_MARKER, 1)[-1]
    assert "- **Omit `priority`" in body, "the seam rule moved out of the plan-construction group"
    return body.split("- **Omit `priority`", 1)[0]


def test_the_seam_is_named_as_a_schema_violation_rather_than_a_nuisance():
    seam = _seam_bullet()
    assert "schema violation" in seam
    assert "goal-design" in seam, "the seam does not point at the schema it violates"


def test_the_seam_routes_through_the_same_three_buckets_the_verdict_already_uses():
    """Not a fourth rule: a `B-n` in that cell is a pre-filled claim that a slice cannot start,
    which is §2a's own operational test, so it is adjudicated by §2a's buckets and one of them is
    written down. Every bucket has to be reachable or the reviewer has nowhere to put the case."""
    seam = _seam_bullet()
    for bucket in ("blocking", "resolved", "open, not blocking"):
        assert bucket in seam, f"the seam offers no route for the {bucket!r} adjudication"
    assert "REJECT" in seam, "the blocking route does not say the verdict it forces"


def test_the_seam_forbids_both_ways_of_making_it_go_away():
    """Dropping it is the observed bug; inventing a plan key for it is the plausible over-correction
    -- it would file a real, pickable ticket for a design blocker that has no scope."""
    flat = __import__("re").sub(r"\s+", " ", _seam_bullet())
    assert "never drop" in flat.lower() or "never dropped" in flat.lower()
    assert "never invent a plan key" in flat


def test_control_the_refusal_the_seam_quotes_is_the_one_the_compiler_really_raises():
    """The strongest pin available here: the error text is not restated by hand, it is run. A change
    to `_validate_and_order`'s message that left this prose behind would be teaching the reader to
    look for a string that no longer exists."""
    import re as _re
    quoted = [q for q in _re.findall(r"`([^`]+)`", _seam_bullet()) if q.startswith("compile_plan:")]
    assert quoted, "the seam no longer quotes the refusal it is warning about"

    class _Src:
        created = []

        def create_dependency(self, *a, **k):                       # pragma: no cover - never called
            raise AssertionError("a structurally invalid plan reached the source")

    plan = {"epic": None, "issues": [
        {"key": "3", "title": "Third slice", "body": "b"},
        {"key": "4", "title": "Fourth slice", "body": "b", "blocked_by": ["3", "B-1"]}]}
    try:
        compile_plan.compile_plan("/irrelevant/.sdlc", {}, plan, source=_Src())
    except ValueError as exc:
        real = str(exc)
    else:                                                            # pragma: no cover
        raise AssertionError("compile_plan accepted a design-artifact id as a blocked_by key")
    for fragment in quoted:
        assert fragment in real, f"the skill quotes {fragment!r}; the real refusal is {real!r}"


def test_2c_bounds_the_blocked_by_edge_to_an_item_another_slice_actually_carries():
    """2c told the reviewer to express an open item as a `blocked_by` edge, full stop -- but a
    `blocked_by` key names a SIBLING in the same plan, so an open item with no slice of its own has
    no edge that can be written at all. Following it literally is what produced the seam."""
    marker = "- **Where a slice cannot be FINISHED"
    assert marker in SKILL, "2c's edge bullet is gone"
    bullet = ADJUDICATION.split(marker, 1)[-1].split("- **Lead the outcome comment", 1)[0]
    flat = __import__("re").sub(r"\s+", " ", bullet)
    assert "another slice" in flat.lower(), "2c still implies an edge can name a non-slice"
    assert "comment" in flat.lower(), "2c does not say what carries an item with no edge to write"


# ================================================== #1952: a capped sweep has to survive Stage 2

def test_step_1_reads_the_sweep_field_first():
    """`goal-design` §5 makes `Sweep` a mandatory header field. If Stage 2 does not read it, the
    fact that a mapping is partial dies in a file that is gitignored on most repos."""
    step1 = READING_TARGET.split("## 1. Read the target", 1)[-1].split("## 2.", 1)[0]
    assert "`Sweep`" in step1, "step 1 never reads the sweep outcome"
    assert "capped" in step1 and "converged" in step1


def test_a_capped_sweep_is_neither_a_reject_nor_a_footnote():
    step1 = READING_TARGET.split("## 1. Read the target", 1)[-1].split("## 2.", 1)[0]
    assert "not a REJECT" in step1, "step 1 does not say what a capped sweep does to the verdict"
    assert "2a" in step1, "the unswept seeds are not routed through the adjudication"


def test_a_missing_sweep_field_is_not_read_as_converged():
    step1 = READING_TARGET.split("## 1. Read the target", 1)[-1].split("## 2.", 1)[0]
    assert "missing" in step1.lower(), "an artifact with no Sweep field has no stated handling"


def test_the_capped_fact_is_carried_onto_the_epic_body():
    """'in the Epic body that goal-review compiles, so it survives into the ticket the team
    actually reads' -- #1952's own ask, and the half a gitignored artifact cannot deliver."""
    b = CONFIRM.split("**b. If the design's own slice-count estimate", 1)[-1].split("**c. If", 1)[0]
    assert "capped" in b, "the epic body never carries a capped sweep"
    assert "first line" in b.lower(), "the capped statement is not placed first in the epic body"


def test_the_outcome_comment_carries_the_sweep_state_alongside_the_count():
    d = CONFIRM.split("**d. Comment on `#<n>`", 1)[-1].split("## 5.", 1)[0]
    assert "goal-review: CONFIRMED (<k> open questions carried)" in d, \
        "the pinned count template moved -- re-check this pin"
    assert "sweep:" in d, "the outcome comment states the count but not the sweep outcome"


def test_every_copy_of_the_outcome_comment_template_carries_the_sweep_marker():
    """#1952 puts the marker on the outcome comment -- and this file states that comment's template
    TWICE: 2c's "lead with the count" bullet and 4d's runnable command. Updating only one leaves a
    reader who follows 2c writing the marker-less form, which is the same two-copies-disagree
    defect #1957 was filed for. Control: drop `[sweep:` from either copy and this goes red quoting
    it. Index-based rather than line-based so a re-wrap at column 100 is not a false red."""
    flat = __import__("re").sub(r"\s+", " ", SKILL)
    where = [m.start() for m in __import__("re").finditer(r"goal-review: CONFIRMED \(", flat)]
    assert len(where) >= 2, "the skill no longer states the template twice -- re-check this pin"
    bare = [flat[i:i + 150] for i in where if "sweep:" not in flat[i:i + 150]]
    assert not bare, "outcome-comment template copies with no sweep marker: %r" % bare


# ================================================== #1955: the adjudication can name its items

def test_the_adjudication_names_each_item_by_its_id():
    """2a must bucket EVERY doubt and blocker in writing; with no ids it had nothing to name them
    by, and the live pass invented D-1..D-8 for itself. `goal-design` §5 now mandates them."""
    a = ADJUDICATION.split("### 2a. Adjudicate", 1)[-1].split("### 2b.", 1)[0]
    assert "D-" in a and "B-" in a, "the buckets still have no way to name an item"
    assert "id" in a.lower()


def test_an_artifact_with_no_ids_is_reported_rather_than_worked_around():
    a = ADJUDICATION.split("### 2a. Adjudicate", 1)[-1].split("### 2b.", 1)[0]
    assert "invent" in a.lower(), \
        "nothing stops a reviewer inventing ids again, which is what made two runs disagree"


def test_the_carried_open_items_are_named_by_id_too():
    c = ADJUDICATION.split("### 2c. Carrying the open items", 1)[-1].split("## 3.", 1)[0]
    assert "by id" in c.lower(), "an open item is carried into the comment without its id"


# ------------------------------------------ #1954: a one-slice design still has to produce a ticket

def _step_4c():
    return CONFIRM.split("**c. If the design concludes", 1)[-1].split("**d. Comment on", 1)[0]


def test_4c_distinguishes_no_epic_from_no_ticket():
    """The measured defect. 4c was written for the retrofit path, where `#<n>` IS already the tech
    goal, and said flatly "skip 4b entirely; `sdlc:designed` alone is the whole output". Applied to
    a Dossier -- `story`-labelled and never `sdlc:goal`, so no loop may pick it -- that leaves a
    CONFIRMED design on a ticket nothing can execute. The light path #1954 asks for lands exactly
    there, so the light path was unreachable by construction."""
    c = _step_4c()
    flat = " ".join(c.split()).replace("*", "")
    assert "not the same as no ticket" in flat, \
        "4c still treats 'one coherent unit' as producing nothing at all"
    assert "Retrofit path" in c and "Dossier path" in c, \
        "4c does not split by path, which is the only thing that makes the two cases different"


def test_4c_dossier_branch_produces_one_pickable_ticket():
    c = _step_4c()
    dossier = c.split("Dossier path", 1)[-1]
    flat = " ".join(dossier.split()).replace("*", "")
    # `report["issues"]`, not the bare label: `sdlc:goal` also appears in this branch's own
    # NEGATIVE clause ("`#<n>` is `story`-labelled and never `sdlc:goal`"), so a pin on the token
    # alone stayed green with the affirmative half deleted -- caught by running that control.
    assert 'report["issues"]' in flat, \
        "the Dossier branch never names the report key that carries the created ticket"
    assert "one-row plan" in flat or "single `issues[]` entry" in flat, \
        "the Dossier branch does not say what plan to hand the compiler"
    assert "omit `epic`" in flat.lower(), "the Dossier branch does not say to omit the epic object"


def test_4c_cites_the_live_condition_that_makes_the_epic_absent():
    """CROSS-FILE, and the reason 'no Epic' is structural rather than a judgement call: the wrapper
    is created only for a plan carrying more than one issue, so a one-row plan CANNOT produce one.
    If that condition moved, 4c's instruction would silently start creating Epics again."""
    import inspect
    source = inspect.getsource(compile_plan.compile_plan)
    assert "if epic_data and len(items) > 1:" in source, \
        "the live epic-or-not condition moved -- re-check this pin"
    flat = " ".join(_step_4c().split())
    assert "more than one issue" in flat, "4c asserts the Epic is absent without saying why"
    assert 'report["epic"]` comes back `null' in flat, \
        "4c does not connect its instruction to the report key 4b already branches on"


def test_the_epic_level_resolution_keys_off_the_report_not_the_branch():
    """`<epic>` used to resolve as "report['epic'] if 4b ran, else <n> if 4c applied". Once 4c's
    Dossier branch RUNS 4b and gets a null epic back, that reading is ambiguous for the exact case
    #1954 added. The report is the unambiguous source."""
    section = SKILL.split("## 5. Feature-ification", 1)[-1]
    flat = " ".join(section[:700].split())
    assert "where a plan produced one" in flat, "the resolution still keys off which branch ran"
    assert "null" in flat, "the resolution never mentions the null-epic case it has to cover"


# ------------------------------------------------------- #1953: who can actually read the artifact

def test_the_skill_no_longer_claims_the_artifact_is_gitignored_on_most_repos():
    """False in both directions once measured: `setup.RUNTIME_IGNORES` omits `.sdlc/design/`
    deliberately, so every `/sigma-setup` repo tracks it, and Sigma's own does too since #1953.
    The reason to carry `capped` onto the Epic survives -- the tickets reach everyone with board
    access and the artifact reaches only whoever has the repo -- so the argument is kept and the
    false premise it rested on is dropped."""
    assert "gitignored on most repos" not in SKILL
    flat = " ".join(SKILL.split())          # both claims wrap mid-phrase in the source
    for anchor in ("board access", "repo checked out"):
        assert anchor in flat, \
            "the reason for carrying capped onto the Epic was dropped along with the false premise"


# ============ #1975 / #1976: Stage 2 reads the two sections Stage 1 now writes
# The producer end is pinned by `tests/test_sdlc_goal_design_skill.py`. These are the consumer end,
# and they are the half that decides whether either section is load-bearing or decoration: a
# heading nobody adjudicates is a heading a pass can leave empty. #1976's whole finding is that a
# falsified premise went into `Intent` as prose because this stage adjudicates BY ID and had no id
# to read; #1975's is that `converged` rested on a scope call nothing recorded and nothing checked.

def _step_1():
    return READING_TARGET.split("## 1. Read the target", 1)[1].split("## 2. Assemble", 1)[0]


def _flat(text):
    return " ".join(text.split()).replace("*", "")


#: The two paragraphs #1975 and #1976 added to step 1, each read out on its own. Scoped rather than
#: searched across the whole of step 1 because that section has carried `is not a REJECT` and
#: `never read the silence` since #1952: asserting either against the whole step passes against the
#: very defect the assertion's own message names. Control: with `step 3's REJECT is where it goes`,
#: `which is 2b's REJECT ground` and `never read the silence as "the source was right"` each
#: deleted from their paragraph, the unscoped versions of the three pins below stayed GREEN.
_PREMISE_MARKER = "**`Premise check` is read as a claim about the SOURCE"
_EXCLUSION_MARKER = "**`Out of scope` is where you check the verdict"


def _premise_block():
    """The #1976 paragraph alone. The marker is asserted rather than assumed, for the reason
    `test_sdlc_goal_design_skill._exclusion_rule` records: `str.split` on a missing separator
    returns the WHOLE text, which silently widens every assertion below back to step 1."""
    step1 = _step_1()
    assert _PREMISE_MARKER in step1, "the #1976 premise-check block is gone"
    return step1.split(_PREMISE_MARKER, 1)[1].split(_EXCLUSION_MARKER, 1)[0]


def _exclusion_block():
    """The #1975 paragraph alone -- same reasoning as `_premise_block`."""
    step1 = _step_1()
    assert _EXCLUSION_MARKER in step1, "the #1975 exclusions block is gone"
    return step1.split(_EXCLUSION_MARKER, 1)[1]


def test_step_1_reads_both_new_sections_out_of_the_artifact():
    """Pinned on the ENUMERATION, not on the section as a whole. Step 1 opens with one bullet
    listing what is read out of `.sdlc/design/<n>.md`, and that bullet is the half
    `tests/test_sdlc_goal_design_skill.py::CONSUMER_READS` couples the producer's schema to; a
    mention further down in a paragraph satisfies "the words appear" without the coupling holding.
    Control: deleting the two names from the bullet while leaving the paragraphs below intact
    passed the earlier version of this pin, which searched the whole of step 1."""
    bullet = _step_1().split("- The design write-up in full:", 1)
    assert len(bullet) == 2, "step 1's reading list moved -- re-check this pin"
    reading = _flat(bullet[1].split("\n- ", 1)[0]).lower()
    assert "premise check" in reading, "step 1's reading list never opens the premise check"
    assert "out of scope" in reading, "step 1's reading list never opens the exclusions"
    assert "seeds" in reading, "step 1's reading list never opens the seed record (#2028)"


def test_a_falsified_premise_is_checked_against_the_slices_not_only_the_intent():
    """The issue's own consequence -- "every slice derived from it inherits the error". A design
    that corrects its Intent and leaves the slices built on the disproved claim is the failure this
    section exists to catch, and annotating is not rebuilding."""
    flat = _flat(_premise_block())
    assert "rebuilt on the corrected premise" in flat, \
        "the review checks the correction was noted, not that the slices were rebuilt on it"
    assert "annotated" in flat, "nothing distinguishes a rebuilt slice from an annotated one"
    assert "REJECT" in flat, "a slice built on a disproved premise has no stated consequence"


def test_a_missing_premise_check_is_reported_rather_than_read_as_good_news():
    """The same argument the `Sweep` field already carries: an absent value is indistinguishable
    from the reassuring one, so silence is never read as "the source was right"."""
    flat = _flat(_premise_block())
    assert "schema violation" in flat, "an artifact with no premise check passes unremarked"
    assert "never read the silence" in flat or "never read as" in flat, \
        "the review may still take an absent premise check as the good news"


def test_the_exclusions_of_the_round_the_sweep_ended_on_are_read_first():
    """`converged` is an assertion about exactly that round, and #1975's scenario is a pass quieting
    it by excluding one file. Reading the exclusions in document order buries the ones that matter."""
    flat = _flat(_exclusion_block())
    assert "round the sweep ended on" in flat, \
        "the review reads exclusions without weighting the round that produced the verdict"
    assert "FIRST" in _exclusion_block(), \
        "the ordering is stated without being an instruction"


def test_an_exclusion_the_reviewer_disagrees_with_is_a_missing_blast_radius_row():
    """The consequence is what makes the adjudication real. Without it, disagreeing with an
    exclusion is a comment; with it, it is an incomplete mapping, which 2b already rejects."""
    flat = _flat(_exclusion_block())
    assert "blast-radius row that is missing" in flat, \
        "disagreeing with an exclusion has no stated consequence"
    assert "REJECT" in flat, "the consequence is named without being routed to a verdict"


def test_a_missing_out_of_scope_section_is_a_schema_violation_too():
    """The hole the rest of #1975 leaves open. Every other route to a clean-looking artifact is now
    written down and disputable; simply OMITTING the heading was not, and step 1 already treats a
    missing `Sweep` field and a missing `Premise check` that way. An absent section is the absence
    of the record `converged` is defined over, not a sweep that excluded nothing."""
    flat = _flat(_exclusion_block())
    assert "schema violation" in flat, \
        "an artifact that simply omits the exclusions passes unremarked"
    assert "- None." in flat, \
        "nothing says an empty section still has to be written, so absence stays ambiguous"


def test_the_review_can_check_the_accounting_without_trusting_the_pass():
    """Every other check here is the reviewer reading what the design said about itself. This one
    is not: the queries are recorded verbatim, so the accounting is re-derivable."""
    flat = _flat(_exclusion_block())
    assert "Queries run" in flat, "the review has no way to check the exclusions independently"
    assert "re-run" in flat, "nothing tells the reviewer how to re-derive the accounting"


def test_both_new_lists_go_through_the_same_three_buckets_by_id():
    """2a's three buckets are the mechanism this stage already has for "a judgement offered up for
    disagreement". Reusing it costs nothing; inventing a fourth treatment is how items get dropped."""
    buckets = ADJUDICATION.split("### 2a.", 1)[1].split("### 2b.", 1)[0]
    flat = _flat(buckets)
    assert "X-n" in flat, "the exclusions are never bucketed"
    assert "PC-n" in flat and "unverifiable" in flat, \
        "an unverifiable premise is never bucketed"
    assert "falsified" in flat and "not bucketed" in flat, \
        "a settled premise is put through buckets meant for open items"


def test_a_falsified_premise_is_carried_onto_the_ticket_that_still_states_it():
    """2c's own argument, applied one item further: the correction has to land where the wrong
    claim lives. `#<n>`'s BODY still says what the sweep disproved, and a reader who takes the
    issue at its word after this review has been given no reason not to."""
    carrying = ADJUDICATION.split("### 2c.", 1)[1].split("## 3. On REJECT", 1)[0]
    flat = _flat(carrying)
    assert "FALSIFIED" in carrying, "a falsified premise is not carried onto the ticket"
    assert "PC-n" in flat, "it is carried without an id, so nothing can be matched back"
    assert "not carried in the count" in flat, \
        "a falsified premise is folded into the open-question count it is not one of"


def test_the_epic_body_never_restates_a_premise_the_design_falsified():
    """The propagation stop. Every child ends up pointing back at `Story #<n>`, whose body still
    carries the original claim; an Epic that repeats it puts the error on the one surface the team
    actually reads."""
    confirm = CONFIRM.split("## 4. On CONFIRM", 1)[1].split("## 5. Feature-ification", 1)[0]
    marker = "**Never restate a premise the design falsified"
    assert marker in confirm, "the epic body may restate a premise the design disproved"
    # The last assertion is scoped to THIS bullet. Step 4 has carried its own `phantom-blocker`
    # reference since #1956, so asserting the word against the whole of `## 4. On CONFIRM` passes
    # against the defect it names. Control: with `Worded per the phantom-blocker warning below`
    # deleted from this bullet, the unscoped version stayed GREEN.
    flat = _flat(confirm.split(marker, 1)[1].split("\n\n- **", 1)[0])
    assert "corrected" in flat, "the rule forbids without saying what to write instead"
    assert "phantom-blocker" in flat, \
        "new body text is mandated without the wording rule every other body here carries"


# ============================================ #2028: the capped count is checked, not just carried
# `Sweep: capped` states how many seeds are unswept, and until #2028 the artifact held no other
# record of them -- so the number was the pass's own say-so and this stage carried it forward
# unexamined onto the epic and the outcome comment. `## Seeds` is now that record, which makes the
# banner's count the one thing in the whole document a reviewer can check WITHOUT re-running a
# query. This stage is where that check belongs.

_SEEDS_MARKER = "**`Seeds` is where you check the count `Sweep` reports"


def _seeds_block():
    """The #2028 paragraph alone -- same reasoning as `_premise_block`: the marker's presence is
    asserted, because `str.split` on a missing separator silently widens this to all of step 1."""
    step1 = _step_1()
    assert _SEEDS_MARKER in step1, "the #2028 seeds block is gone"
    return step1.split(_SEEDS_MARKER, 1)[1].split(_PREMISE_MARKER, 1)[0]


def test_the_banner_count_is_checked_against_the_table_that_now_records_it():
    """The one arithmetic check this stage can make against the design without opening the repo.
    Scoped to the #2028 block, not to step 1 as a whole: step 1 has carried `unswept` since #1952,
    so an unscoped pin passes against the very defect its own message names."""
    flat = _flat(_seeds_block())
    # Pinned on the INSTRUCTION, not the bare word. The block spells `swept` or `unswept` when it
    # describes a row and `its unswept seeds` when it describes the capped outcome, so a bare
    # `"unswept" in flat` is satisfied twice over with the count instruction itself deleted.
    # Control: with `rows marked `unswept`` cut to `rows`, the bare version stayed GREEN.
    assert "the rows marked `unswept`" in flat, \
        "the block never says which rows the count is over"
    assert "schema violation" in flat, \
        "a banner disagreeing with its own table has no stated consequence here"
    assert "not adjudicat" in flat.lower(), \
        "a count mismatch reads as something the reviewer can weigh away rather than report"


def test_a_missing_seeds_table_is_reported_rather_than_read_as_converged():
    """Exactly the failure mode a missing `Sweep` and a missing `Out of scope` already have named
    for them. Silence about the seed set is the cheapest possible route back to `converged` meaning
    nothing, which is the state #2028 found."""
    flat = _flat(_seeds_block())
    # Scoped to the `Seeds` sentence. The same paragraph says "exactly as you would a missing
    # `Sweep`", so a bare `"missing" in flat` passes with the Seeds half deleted -- control: with
    # `a missing `Seeds`` changed to `an unhelpful `Seeds``, the bare version stayed GREEN.
    assert "a missing `Seeds`" in flat, "an absent Seeds heading has no stated reading"
    assert "never read the silence" in flat.lower(), \
        "an artifact with no seed record still reads as one that swept everything"


def test_the_block_does_not_turn_a_capped_sweep_into_a_reject():
    """The rule one level up is explicit that `capped` is a sanctioned outcome, and #2028 changes
    the RECORD, never the verdict. A reviewer reading this block must not come away thinking a
    correctly-recorded capped sweep is now rejectable -- that is a product decision nobody made."""
    flat = _flat(_seeds_block())
    assert "still not a REJECT" in flat, \
        "the block leaves a correctly-capped sweep looking newly rejectable"


# ======================================= #2032: the Budget field is checked, not just carried
# `**Budget**` in the artifact is SELF-REPORTED -- the design pass calls `sweep-budget` and writes
# what it returns, but until this check existed nothing compared that write against a fresh call.
# A pass that skipped the verb and wrote `3` from memory produced an artifact indistinguishable
# from a correct one, which is exactly the failure #2032 exists to end. This is the same shape as
# the #2028 Seeds check just above it -- a mechanical re-derivation, reported as a schema
# violation rather than adjudicated -- applied to a number instead of a table.

_BUDGET_MARKER = "**`Budget` is a claim you check by RE-RUNNING the verb"


def _budget_block():
    """The #2032 paragraph alone -- same reasoning as `_seeds_block`/`_premise_block`: the marker's
    presence is asserted, because `str.split` on a missing separator silently widens this to the
    rest of step 1."""
    step1 = _step_1()
    assert _BUDGET_MARKER in step1, "the #2032 budget-check block is gone"
    return step1.split(_BUDGET_MARKER, 1)[1].split(_PREMISE_MARKER, 1)[0]


def test_step_1_reruns_sweep_budget_rather_than_trusting_the_field():
    flat = _flat(_budget_block())
    assert "goal_design.py" in flat, "the block never names the engine script"
    assert "sweep-budget" in flat, "the block never names the verb"
    assert "self-report" in flat.lower(), \
        "the block does not say why the artifact's own claim is not enough on its own"


def test_the_budget_check_uses_the_artifacts_own_mode_and_lane():
    block = _budget_block()
    assert "--mode" in block and "--lane" in block, \
        "the re-run command is missing the arguments that make it comparable to the artifact"
    assert "Mode" in block and "Lane" in block, \
        "the block does not say to read Mode/Lane off the artifact's own header line"


def test_a_budget_mismatch_is_a_schema_violation_never_adjudicated():
    flat = _flat(_budget_block())
    assert "schema violation" in flat, "a Budget mismatch has no stated consequence here"
    assert "do not adjudicate it" in flat.lower() or "never adjudicat" in flat.lower(), \
        "a Budget mismatch reads as something the reviewer can weigh rather than report"


def test_a_missing_budget_field_is_reported_rather_than_read_as_the_default():
    """The same failure mode `Sweep`/`Seeds`/`Premise check`/`Out of scope` already have a stated
    reading for: silence about the field must not be read as the good, unconfigured case."""
    flat = _flat(_budget_block())
    assert "no" in flat.lower() and "budget" in flat.lower()
    assert "never read the silence" in flat.lower(), \
        "an artifact with no Budget field still reads as if the default quietly applied"


def test_goal_review_points_at_the_real_goal_design_engine_script():
    """CROSS-FILE. The command this block tells a reviewer to run has to resolve to a script that
    actually exists and actually carries the verb, or following this file end to end fails on its
    first real command."""
    block = _budget_block()
    assert "../sigma-goal-design/scripts/goal_design.py" in block, \
        "the block does not point at the sibling skill's engine script"
    root = pathlib.Path(__file__).resolve().parent.parent
    script = root / "skills" / "sigma-goal-design" / "scripts" / "goal_design.py"
    assert script.is_file(), "goal-review points at a goal_design.py that does not exist on disk"
    assert "def sweep_budget(" in script.read_text(encoding="utf-8"), \
        "goal_design.py no longer defines the function the sweep-budget verb wraps"


# ================================================ #2164/#2266: the feature-priority ask, at step f
# Slice 3 of epic #2161 (design: .sdlc/design/2154.md) wired the same priority question sigma-define
# asks at its own `declare` step (slice #2163) in here too, right after THIS section's own step f
# (`declare`) succeeds -- one consistent ask regardless of which path created the feature.
# #2266 (epic #2260, slice 6 of .sdlc/design/2253.md) changed WHAT a real answer does: once
# priority is data on the unit, exact-matching it onto every member's own `priority:` label erases
# the per-issue tiers the comparator reads, so the ask now records one value on the unit's OWN
# registry entry (`define.py set-priority`) -- never a write to `<epic>` or any child. A skip (or
# no interactive host) must still leave today's feature-ification flow byte-identical.


def _priority_block():
    marker = "**Then, once `declare` reports `declared` on every row, ask about feature priority**"
    assert marker in SKILL, "the feature-priority ask this section is supposed to add is gone"
    return FEATURE.split(marker, 1)[-1].split("**g. Comment the outcome", 1)[0]


def test_feature_priority_ask_sits_after_step_f_and_before_step_g():
    # Ordering is the point (design §"Wiring"): the ask must land before step g's outcome comment,
    # not after it -- kept consistent with `/sigma-define`'s own placement of the identical question,
    # even though `set-priority` itself needs nothing from step f's `declare` call.
    # Scoped to FEATURE (references/feature-ification.md, #2108): the pointer paragraph SKILL.md's
    # own body now carries under "## 5." happens to use the same three words "ask about feature
    # priority" in its navigation prose, which would make an unscoped `SKILL.index(...)` find that
    # earlier, unrelated occurrence in the body instead of the real one inside step 5f.
    f_at = FEATURE.index("**f. Declare the label, on every ticket")
    priority_at = FEATURE.index("ask about feature priority")
    g_at = FEATURE.index("**g. Comment the outcome on `<epic>`")
    assert f_at < priority_at < g_at


def test_the_priority_question_text_is_present_verbatim():
    # Same convention, same wording, so a user sees one consistent ask regardless of which path
    # (sigma-define directly, slice #2163, or this feature-ification step) created the feature.
    flat = " ".join(_priority_block().split())
    assert 'Give `feature:<name>` a priority? (P0-P4, or skip)' in flat


def test_the_priority_ask_uses_the_askuserquestion_convention():
    flat = " ".join(_priority_block().split())
    assert "AskUserQuestion" in flat
    assert "plain conversation otherwise" in flat


def test_the_priority_ask_calls_the_same_set_priority_verb_sdlc_define_calls():
    block = _priority_block()
    assert "sigma-define/scripts/define.py" in block and "set-priority .sdlc" in block
    assert "--unit <name> --priority <P>" in block


def test_the_set_priority_call_matches_step_d_s_relative_path_pattern():
    # step d's `define.py open` call establishes the pattern; step f's own `declare` call already
    # reuses it once; this is its third use, for the same script, same relative path.
    open_call = FEATURE.split("**d. Open the unit:**", 1)[-1].split("```", 2)[1]
    priority_block = _priority_block()
    set_call = priority_block.split("```", 2)[1]
    open_prefix = open_call.split("define.py", 1)[0]
    set_prefix = set_call.split("define.py", 1)[0]
    assert open_prefix == set_prefix, "the set-priority call does not match step d's own relative-path prefix"


def test_the_priority_ask_records_on_the_unit_not_a_member_issue():
    # #2266's whole point, checked as an operational claim rather than trusted from the heading:
    # the block must say the write lands on the unit's own registry entry, and must not describe
    # the old stamp-every-member behaviour it replaced.
    flat = " ".join(_priority_block().split())
    assert "registry entry" in flat
    assert "never a write to" in flat or "never touches a member issue" in flat
    assert "exact-matches its" not in flat, "the old stamp-every-member phrasing is still here"


def test_the_priority_ask_reports_its_outcome_fields_not_bump_priority_per_issue_outcomes():
    flat = " ".join(_priority_block().split())
    for field in ("ok", "changed", "written"):
        assert field in flat, f"{field!r} outcome field missing from the reported vocabulary"
    # The old bump-priority per-issue vocabulary must not still be described as what this call
    # reports -- it reports one unit-level outcome now, not a row per member issue.
    for stale in ("already-at-or-above", "excluded-needs-confirmation"):
        assert stale not in flat, f"{stale!r} is bump-priority's own vocabulary, not set-priority's"


def test_bump_priority_is_never_invoked_from_this_ask():
    # #2266: "the verb itself can stay... it just stops being the answer to 'prioritise this
    # feature'" -- it stays available elsewhere (branching-model.md §16, define.py's own module
    # docstring), but this block must never invoke it with the `.sdlc` CLI shape that would make
    # it the thing a real answer to THIS question calls.
    block = _priority_block()
    assert 'scripts/define.py" bump-priority .sdlc' not in block


def test_skipping_the_priority_ask_leaves_todays_flow_byte_identical():
    flat = " ".join(_priority_block().split())
    assert "Skipping the question" in flat
    assert "no interactive host" in flat
    assert "byte-identical to today's feature-ification flow" in flat


def test_the_priority_block_mints_no_phantom_blocker():
    """Runnable control (AGENTS.md: run it, don't just read it): the live regex, over a
    placeholder-substituted copy of the new prose, must find zero hits."""
    block = _priority_block()
    real = block.replace("#N", "#1826").replace("<n>", "1826")
    hits = blocker_scan._BLOCK_RE.findall(blocker_scan.strip_unpark_qa(real))
    assert hits == [], f"the new feature-priority prose mints a phantom blocker: {hits!r}"
