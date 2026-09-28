"""Skill prompt hygiene regression guard (#1613).

Filed from a static prompt review of all 35 (now 39) skills that found three things: a dead file
reference, an ungated `agrim-velocity` (fixed alongside this test — see its SKILL.md), and the need
for a guard so neither class of defect, nor #1612's listing-budget overflow, recurs unnoticed.

**The SKILL.md 5,000-token / 500-line limit is deliberately NOT re-asserted here.** #1613 asked
for it, but by the time this landed, #1616 (closed) had already built a calibrated, waiver-backed
gate for exactly that budget — `evals/skill_structure.py` + `tests/test_skill_structure.py`,
whose `test_the_shipped_skills_pass_the_structural_gate` already runs `ss.findings() == []`
against the real corpus, ratchets two currently-waived skills (`agrim-goal-design`,
`agrim-goal-review` — both grew past the cap AFTER #1611 shipped, tracked by #1616's own waiver
file) and is calibrated against a real tokenizer rather than this file's cruder chars/4 guess.
A second, uncalibrated copy of that measurement here would be pure duplication with a real chance
of disagreeing with the one that is actually trusted — worse than no second check at all.

What IS new here, none of it covered elsewhere:

1. `test_no_description_exceeds_the_listing_cap` — #1612's per-description hard cap.
2. `test_total_description_chars_stay_under_the_listing_budget` — the enforced
   whole-listing budget. The stale expected-failure marker was removed with the trim.
3. `test_backticked_repo_paths_resolve` — the dead-reference class of bug itself. Run red before
   #1613's fix (see the commit that added this file): the one real defect,
   `.github/CRITICAL_INSIGHT_TEMPLATE.md` cited from `skills/agrim-loop/references/progress.md`,
   was the ONLY finding out of 133 backticked spans that looked like a same-repo path — the other
   132 were exactly the false-positive shapes #1613 named (a `.sdlc/*` runtime path, a target
   project's own `CLAUDE.md`/`config.json`, a bare script resolved under a sibling skill's
   `scripts/`) plus a few more of the same kind this implementation had to discover for itself
   (a home-directory path, a runtime output directory, a schema/pack identifier, a grep-output
   example) — which is why the check below is a narrow WHITELIST of shapes that plausibly claim to
   be this repo's own shipped content, not a blacklist of exclusions layered onto "check
   everything".
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"


def _mod(name, subdir="agrim-loop"):
    spec = importlib.util.spec_from_file_location(name, SKILLS_DIR / subdir / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


frontmatter = _mod("frontmatter")


# ---------------------------------------------------------------- what ships


def _tracked_files():
    """Repo-relative POSIX paths git is actually tracking, or None when that can't be determined.

    Same shape, and the same reason (#1821, recurring as #2002), as test_docs.py's and
    evals/skill_structure.py's own `_tracked_files()`/`_tracked_md_files()`: only the index answers
    "what ships" — a raw filesystem walk also reads vendored `node_modules` READMEs and this very
    goal's own worktree scratch under `.sdlc/work/`, and a guard whose coverage depends on where it
    happens to be checked out is not a guard."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return {f for f in out.stdout.split("\0") if f} if out.returncode == 0 else None


TRACKED = _tracked_files()


def _all_files():
    if TRACKED is not None:
        return TRACKED
    return {str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("*") if p.is_file()}


def _skill_md_files():
    """Every shipped `skills/*/SKILL.md`, repo-relative POSIX paths, sorted."""
    all_files = _all_files()
    return sorted(p for p in all_files if re.match(r"^skills/[^/]+/SKILL\.md$", p))


def _skill_prompt_files():
    """Every SKILL.md plus every `references/*.md` beside it — the corpus an agent actually reads
    (see `tests/skill_corpus.py`), which is where #1613's own dead reference now lives: #1611 moved
    it out of `SKILL.md` proper and into `references/progress.md`."""
    all_files = _all_files()
    return sorted(p for p in all_files
                  if re.match(r"^skills/[^/]+/(SKILL\.md|references/[^/]+\.md)$", p))


def _description(rel_path):
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    return frontmatter.get(text, "description") or ""


# ---------------------------------------------------------------- #1612: listing budget

#: Claude Code's skill-listing budget scales at ~1% of the model's context window; at a 200k
#: window that is ~2,000 tokens, ~8,000 chars at the chars/4 estimator this repo already uses
#: elsewhere (see evals/skill_structure.py's calibration note). Past it, descriptions are DROPPED
#: silently, starting with the least-invoked skills — a correctness budget, not tidiness (#1612).
DESCRIPTION_LISTING_BUDGET_CHARS = 8000

#: The hard per-description cap Claude Code's listing UI itself enforces (#1612).
MAX_SINGLE_DESCRIPTION_CHARS = 1536


def test_no_description_exceeds_the_listing_cap():
    offenders = [(p, len(_description(p))) for p in _skill_md_files()
                 if len(_description(p)) > MAX_SINGLE_DESCRIPTION_CHARS]
    assert not offenders, (
        f"description(s) over the {MAX_SINGLE_DESCRIPTION_CHARS}-char listing cap: {offenders}")


def test_total_description_chars_stay_under_the_listing_budget():
    skills = _skill_md_files()
    total = sum(len(_description(p)) for p in skills)
    assert total <= DESCRIPTION_LISTING_BUDGET_CHARS, (
        f"{total} chars across {len(skills)} descriptions > "
        f"{DESCRIPTION_LISTING_BUDGET_CHARS}-char listing budget")


# ---------------------------------------------------------------- #1613: dead references

#: Five filenames that show up constantly as a CONSUMING project's own convention ("the repo's
#: `CLAUDE.md`") rather than a claim about anything in this tree — named explicitly in #1613.
GENERIC_MENTIONS = frozenset({"CLAUDE.md", "AGENTS.md", "TEAM.md", "config.json", "package.json"})

#: A backticked span whose text starts with one of these (after stripping one leading "/") is
#: claiming to be part of THIS repo's own shipped tree, the same shape as the one real defect
#: (`.github/CRITICAL_INSIGHT_TEMPLATE.md`). Everything else a skill prompt mentions in backticks —
#: `.sdlc/*` runtime state that is gitignored on every subpath but four (see .gitignore), a target
#: project's own `project.md` / `docs/CONTRACTS/`, a `~/...` user-machine path, a `graphify-out/`
#: build artifact, a schema/pack identifier, a `<placeholder>` tail — is describing something that
#: is never meant to exist in this tree, and treating it as a candidate is exactly how #1613's own
#: two prior scans got 3-false-positives-for-every-1-real-finding.
RECOGNIZED_OWN_TREE_PREFIXES = ("skills/", "docs/", "tests/", ".github/", "hooks/", "references/", "../")

_BACKTICK_SPAN = re.compile(r"`([^`\n]+)`")


def _candidate_kind(span):
    """None if `span` isn't worth checking; otherwise which rule made it a candidate."""
    if not span or any(c.isspace() for c in span):
        return None
    if any(c in span for c in "$<>*|~"):
        return None  # shell interpolation, a templated <placeholder>, a glob, a `~` home path
    stripped = span[1:] if span.startswith("/") else span
    if stripped.startswith(RECOGNIZED_OWN_TREE_PREFIXES) and not span.endswith("/"):
        return "own-tree"
    if "/" not in span:
        if span in GENERIC_MENTIONS:
            return "generic"  # never checked — see GENERIC_MENTIONS
        if span.endswith(".py"):
            return "bare-script"  # bare filenames resolve under ANY skill's scripts/ (#1613)
    return None


def _normalize(rel_posix):
    parts = []
    for part in rel_posix.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part and part != ".":
            parts.append(part)
    return "/".join(parts)


def _resolves(span, file_dir, skill_dir, all_files, basename_index):
    """Skill-dir-relative BEFORE repo-root (#1613's own ordering requirement) — tried against both
    the referencing file's own directory and its skill's top-level directory, since real prompts
    use both bases (`../SKILL.md` from a `references/*.md` file; `references/vision.md` written
    inside another `references/*.md` file of the SAME skill, meaning "relative to my skill root")."""
    stripped = span[1:] if span.startswith("/") else span
    for base in (file_dir, skill_dir, pathlib.PurePosixPath(".")):
        if _normalize(f"{base.as_posix()}/{stripped}") in all_files:
            return True
    if "/" not in span and span in basename_index:
        return True
    return False


def _dead_references():
    all_files = _all_files()
    basename_index = {}
    for f in all_files:
        basename_index.setdefault(f.rsplit("/", 1)[-1], []).append(f)

    problems = []
    for rel in _skill_prompt_files():
        text = (ROOT / rel).read_text(encoding="utf-8")
        rel_p = pathlib.PurePosixPath(rel)
        file_dir = rel_p.parent
        skill_dir = pathlib.PurePosixPath("skills") / rel_p.parts[1]
        for m in _BACKTICK_SPAN.finditer(text):
            span = m.group(1)
            if _candidate_kind(span) in (None, "generic"):
                continue
            if not _resolves(span, file_dir, skill_dir, all_files, basename_index):
                problems.append(f"{rel}: `{span}` does not resolve")
    return problems


def test_backticked_repo_paths_resolve():
    """A SKILL.md that tells the agent to follow a specific file's format/content must itself be
    able to find that file. `.github/CRITICAL_INSIGHT_TEMPLATE.md`, cited from
    `skills/agrim-loop/references/progress.md` for the exact shape of a 🔒 Critical Insight, named
    nothing that exists in the shipped tree on two separate static-prompt-review scans
    (2026-08-11, 2026-08-24) — this is what would have caught it before a third."""
    problems = _dead_references()
    assert not problems, "dead reference(s):\n" + "\n".join(problems)
