"""#1542: each zone-aware phase rule is pinned by its own test (text greps, not behaviour)."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
Z = 'python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/zones.py"'
BLOCK = ("Locate first: `rg --files` for paths, `rg -n` for symbols or claims. Read only the relevant lines "
         "in a bounded range, widening only when the boundary is unclear; never dump whole files or broad "
         "search output. Save bulky test or diagnostic output to a scratch file and inspect its exit status "
         "and focused matches or a bounded tail. Do not hide failures; keep secrets out of the scratch file. "
         "Guidance, not a measured savings claim.")
BLOCK_FILES = ["skills/sigma-%s/SKILL.md" % s for s in
               ("research", "plan", "plan-review", "implement", "review", "verify")] + [
    "skills/sigma-loop/references/running.md", "skills/sigma-loop/references/landing.md"]


def _flat(rel):
    return " ".join((ROOT / rel).read_text(encoding="utf-8").split())


PLAN, RESEARCH = "skills/sigma-plan/SKILL.md", "skills/sigma-research/SKILL.md"
PR, IMPL = "skills/sigma-plan-review/SKILL.md", "skills/sigma-implement/SKILL.md"
REVIEW, AXES = "skills/sigma-review/SKILL.md", "skills/sigma-review/references/axes.md"
DOC = "docs/change-zones.md"


def test_plan_lists_files_under_the_heading_check_reads():
    assert "List them under `## Files`;" in _flat(PLAN)


def test_plan_follows_the_classify_rule_per_file():
    assert "follow the rule `%s classify <path>` prints for each" % Z in _flat(PLAN)


def test_plan_names_the_fallback_when_classify_cannot_answer():
    assert "(`unknown`, `unreachable` or could not run: follow the surrounding code)" in _flat(PLAN)


def test_plan_never_restructures_unilaterally_and_legacy_needs_intent():
    assert ("Never restructure an existing file unilaterally (legacy: only on `intent: yes`, a non-drafted "
            "acceptance criterion naming it,") in _flat(PLAN)


def test_plan_extends_legacy_at_a_seam_and_defers_the_rest():
    # The closing parenthesis pins the seam rule inside the legacy clause: files outside any zone keep
    # "follow the surrounding code" and are not held to a seam.
    assert ("else extend it by sprout or wrap at one seam, sending dropped improvements to retro's "
            "`Deferred roots`).") in _flat(PLAN)


def test_research_reconcile_now_needs_a_criterion_naming_the_legacy_path():
    assert ("in an existing file `%s classify <path>` marks legacy, only where a non-drafted acceptance "
            "criterion names the path, `check`'s `intent: yes`, else sprout or wrap at one seam") % Z in _flat(RESEARCH)


def test_research_fallback_keeps_the_default():
    assert "`unknown`, `unreachable` or could not run keeps the default)" in _flat(RESEARCH)


def test_plan_review_runs_check_on_the_plan():
    assert 'Run `%s check --goal "<goal>" --plan <plan>`' % Z in _flat(PR)


def test_plan_review_rejects_on_verdict_over_quoting_it():
    assert "FIX-FIRST on `verdict: over`, quoting it." in _flat(PR)


def test_plan_review_holds_each_legacy_row_to_a_seam_edit():
    # Conditional on intent: a refactor goal whose criterion names the file (`intent: yes`) passes.
    assert ("Each legacy row without `intent: yes` must be a seam edit (sprout or wrap), else "
            "FIX-FIRST.") in _flat(PR)


def test_plan_review_never_rejects_a_bare_unknown_verdict():
    assert "A bare `verdict: unknown` never rejects." in _flat(PR)


def test_plan_review_structural_rule_yields_to_the_legacy_seam():
    assert ("**Structural over patchwork**, except in a legacy file without `intent: yes`, where the seam "
            "patch is the scope.") in _flat(PR)


def test_implement_refactor_stops_at_a_legacy_row_without_intent():
    assert ('Fix it now, unless `%s check --goal "$goal" --diff` prints the file as a legacy row without '
            "`intent: yes`: there change only what the goal needs.") % Z in _flat(IMPL)


def test_implement_keeps_its_rule_with_no_row_or_no_run():
    assert "With no such row, or if it could not run, this rule holds." in _flat(IMPL)


def test_review_axis6_follows_the_classify_rule_with_fallback():
    assert ("does it break the rule `zones.py classify` prints per file (`unknown`, `unreachable` or could "
            "not run: follow the surrounding code)?") in _flat(REVIEW)


def test_review_reports_unrequested_legacy_restructure_on_over():
    assert ('On `verdict: over` from `%s check --goal "<goal>" --diff`, report `unrequested legacy '
            "restructure`.") % Z in _flat(REVIEW)


def test_review_never_reports_a_new_files_difference():
    assert "or a new file's difference from legacy neighbours." in _flat(REVIEW)


def test_review_architecture_rules_bind_in_every_zone():
    assert "**north-star Architecture Rules** (numbered, binding in every zone)" in _flat(REVIEW)


def test_axes_row6_follows_the_classify_rule():
    assert "Plus: does the change break the rule `zones.py classify` prints for each file?" in _flat(AXES)


def test_old_follow_the_patterns_wording_is_gone():
    old = {PLAN: "follow the codebase's existing patterns**", REVIEW: "diverge from the surrounding patterns",
           AXES: "does it match the surrounding patterns", IMPL: "while the context is in your head",
           RESEARCH: "(default — carrying a known structural defect"}
    for rel, phrase in old.items():
        assert phrase not in _flat(rel), (rel, phrase)


def test_every_inspection_block_copy_carries_the_new_text():
    for rel in BLOCK_FILES:
        assert BLOCK in _flat(rel), rel


def test_change_zones_doc_says_the_effect_is_unmeasured_naming_1544():
    t = _flat(DOC)
    assert "The effect of these phase rules on model behaviour is UNMEASURED" in t
    assert "issue #1544 is the deferred baseline" in t


def test_change_zones_doc_states_the_interactive_limit_and_its_workaround():
    t = _flat(DOC)
    assert "zones therefore do not bind interactive runs" in t
    assert "pass `--base <remote>/<base>` to both verbs" in t
