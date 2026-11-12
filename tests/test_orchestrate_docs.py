"""The sigma-orchestrate skill, its references and docs/orchestrate.md stay consistent (#2145)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "sigma-orchestrate"
SKILL = SKILL_DIR / "SKILL.md"
DOC = ROOT / "docs" / "orchestrate.md"
FILES = [SKILL, DOC] + sorted((SKILL_DIR / "references").glob("*.md"))
ARGS = ("--max-seats", "--name", "--where", "--model", "--max-opus", "--no-merge", "--dry-run", "--yes")
DECISION_OPEN = "Standing decision by the person who started this batch"


def _text(path):
    return path.read_text(encoding="utf-8")


def test_frontmatter_names_the_skill_and_its_scripts_grant():
    head = _text(SKILL).split("---")[1]
    assert re.search(r"^name: sigma-orchestrate$", head, re.M)
    assert re.search(r"^description: .+/sigma-orchestrate\.$", head, re.M)
    assert "Bash(python3 *)" in head


def test_every_argument_is_in_the_skill_and_the_docs():
    for arg in ARGS:
        assert arg in _text(SKILL), arg
        assert arg in _text(DOC), arg


def test_host_agnostic():
    """No hook is load-bearing: the contract's own banned words stay out of the skill and its docs."""
    for path in FILES:
        text = _text(path)
        for word in ("PreToolUse", "PostToolUse", ".mdc", "hook", "Hook"):
            assert word not in text, f"{path.name} names {word}"


def test_every_kit_script_it_names_exists():
    scripts = ROOT / "skills" / "sigma-loop" / "scripts"
    named = set()
    for path in FILES:
        named |= set(re.findall(r"sigma-loop/scripts/(\w+\.py)", _text(path)))
    assert {"promote.py", "deps.py", "goal_cost.py", "unpark.py", "handoff.py", "loop.py", "dag.py"} <= named
    for name in named:
        assert (scripts / name).is_file(), name


def test_the_launch_prompt_names_the_standing_decision_once_and_launch_points_at_it():
    follow = _text(SKILL_DIR / "references" / "follow-ups.md")
    assert follow.count(DECISION_OPEN) == 1
    assert "follow-ups.md" in _text(SKILL_DIR / "references" / "launch.md")


def test_no_follow_up_issue_is_filed_by_the_decision():
    follow = _text(SKILL_DIR / "references" / "follow-ups.md")
    assert "Do not open a new issue for a follow-up" in follow
    assert "At most 2 added slices per goal" in follow


def test_the_skill_is_listed_where_skills_are_listed():
    assert "/sigma-orchestrate" in _text(ROOT / "README.md")
    assert "/sigma-orchestrate" in _text(ROOT / "docs" / "features.md")
    assert (ROOT / "evals" / "skills" / "cards" / "sigma-orchestrate.json").is_file()


# --- #2157: what a real dry run found in the text around the planner -------------------------------------------

_CALL = re.compile(r"scripts/(deps|promote|goal_cost|loop|unpark|handoff)\.py\"? [^\n`]*")


def test_every_kit_script_call_spells_the_sdlc_argument_as_a_path_variable():
    calls = 0
    for path in FILES:
        for m in _CALL.finditer(_text(path)):
            calls += 1
            assert '"$S"' in m.group(0), f"{path.name}: {m.group(0)}"
            assert not re.search(r"\s\.sdlc(\s|$)", m.group(0)), f"{path.name}: {m.group(0)}"
    assert calls >= 10


def test_dag_calls_pass_the_sdlc_directory_by_its_flag():
    for path in FILES:
        for m in re.finditer(r"scripts/dag\.py\"? [^\n]*", _text(path)):
            assert '--sdlc "$S"' in m.group(0), f"{path.name}: {m.group(0)}"


def test_the_path_variable_is_defined_and_the_preflight_says_where_a_worktree_gets_its_config():
    text = _text(SKILL)
    assert 'S="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/.sdlc"' in text
    assert "main checkout" in text.split("## 1. Preflight")[1].split("## 2.")[0]


def test_held_by_another_run_covers_an_open_pr_and_the_planner_input_carries_it():
    step2 = _text(SKILL).split("## 2. Resolve")[1].split("## 3.")[0]
    assert "open pull request that links the issue" in step2 and "closing reference" in step2
    assert "open_prs" in step2 and "open_prs" in (SKILL_DIR / "scripts" / "waves.py").read_text()


def test_step_two_classifies_upper_tiers_with_the_dry_run_probe_and_defines_agent_ready():
    step2 = _text(SKILL).split("## 2. Resolve")[1].split("## 3.")[0]
    assert "promote.py" in step2 and "--dry-run" in step2 and "upper tier" in step2
    assert "**Agent-ready**" in step2 and "`sdlc:goal`" in step2 and "done" in step2


def test_the_claim_fallback_names_the_missing_codeowners_case_and_the_hot_files():
    step3 = _text(SKILL).split("## 3. Facts")[1].split("## 4.")[0]
    assert "no area mapping" in step3 and "own text mentions" in step3 and '["*"]' in step3
    assert "go-ahead screen" in step3 and "lockfile" in step3


def test_the_cost_line_and_the_read_only_claim_are_honest():
    text = _text(SKILL)
    assert "no data for these goals" in text
    assert ".sdlc/state/gh-calls.jsonl" in text


def test_run_md_says_the_last_marker_line_of_the_approve():
    run = _text(SKILL_DIR / "references" / "run.md")
    assert "last marker line of the latest trusted approve" in run
    assert "first line of the latest trusted approve" not in run


def test_the_deferred_docs_agree_with_the_parser_about_list_shaped_lines():
    for path in (DOC, SKILL_DIR / "references" / "follow-ups.md"):
        assert "`- ` or `* `" in _text(path), path.name
    assert "never skipped" in (SKILL_DIR / "scripts" / "deferred.py").read_text()
