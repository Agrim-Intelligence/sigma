"""#801 slice 1: a ratchet on direct `gh issue|pr|project|label` call sites.

DETECTION AND REPORTING ONLY, like the rest of slice 1: this stops the number of direct call sites
GROWING; it migrates none of them and says nothing about cloud-session support (unmeasured).
Helper for new code: skills/sigma-loop/scripts/gh_api.py. See docs/cloud-sessions.md.

DOCUMENTED INVOCATION (the controls were run on exactly this string):
    $HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py
generic form: python -m pytest tests/test_no_direct_gh.py

THE ONE COUNTING RULE. A "site" is an `ast.List`/`ast.Tuple` literal under skills/**/*.py or
hooks/**/*.py matching exactly one of:
  (1) ["gh", N, <anything>, ...]  with N a string constant in {issue, pr, project, label};
  (2) [N, <verb>, ...]            with N in that set and <verb> a real gh verb (VERBS below) given
                                  as a string constant (the sources convention, argv WITHOUT "gh":
                                  `source._run(["issue","view",...])`);
  (3) ["gh", X, ...]              where X is NOT a string constant (name, *args, call, f-string): the
                                  wrapper / variable-noun shape.
The VERBS filter on rule 2 is a deliberate deviation from "any string constant": without it two
non-gh tuples are counted (doctor.py `("project", "local")` scope names; ledger.py
`("pr", "checks_total")` data keys), so a baseline would carry noise that teaches people to bump it.
Cost: a gh verb missing from VERBS is a (documented) gap; add it to VERBS and regenerate.

NEVER counted: ["gh","api",...] (already REST), which("gh") and any bare "gh" that is not the first
element of a list/tuple, `X = "gh"`, ["gh","repo"|"run"|"auth"|...] (any other string second element),
docstrings and comments (not list literals).

NOT COVERED (documented gaps; each pinned by a "documented gap" test below, so widening is a
conscious edit): string-form / shell-string calls (`"issue view".split()`); argv built incrementally
(`args = ["issue"]; args += ["edit"]`, `.append`/`.extend`); lists whose first element is a variable
(`[cmd, ...]`); a call that passes its arguments to a wrapper as positional strings
(`_gh("issue", "view")`); skill prose (.md, .tmpl); SHELL SCRIPTS (skills/**/*.sh, hooks/*.sh) -- 9
files today, a grep found no `gh` reference in any of them, but nothing keeps that true. The ratchet
therefore guarantees "no new list-literal gh issue|pr|project|label call", not "no new gh call".

BASELINE is {path: max_count} generated from the tree by the rule above. Three assertions: a file
outside BASELINE/EXEMPT with a site fails (new file); a BASELINE file over its count fails (count up);
a BASELINE file UNDER its count, including 0, fails (stale entry: the ratchet only goes down).

MEASURED 2026-10-09 (#801 slice 1), via `scan()` over the worktree: TOTAL 101 sites in 21 files
(rule 1: 35, rule 2: 59, rule 3: 7); the scan took 0.41 s wall time (O(files) AST parse of
skills/ + hooks/, 21 of which have sites; measured once on one machine, not a ceiling guarantee).
The dossier's 95 was a grep-derived FLOOR, not a target; the ratchet counts 101.
No `.sh` file under skills/ or hooks/ mentions `gh` at all (grep, 9 files).
MEASURED 2026-10-09 (#895 slice 2a), via the same `scan()`: sources.py 35 -> 28 (its 7 issue reads
moved to `gh_api.read_issue`, whose fallback argv is built inside the EXEMPT helper), TOTAL 94.
MEASURED 2026-10-10 (#895 slice 2b), via the same `scan()`: TOTAL 94 -> 84. The 10 `issue view` reads
moved to `gh_api.read_issue`: auto_unpark 3 -> 2, blockers 2 -> 1, promote 2 -> 1, unpark 3 -> 2,
reconcile 4 -> 1, triage 6 -> 4, brainstorm 1 -> 0 (entry removed). Every `issue list` site is slice 2c.
MEASURED 2026-10-10 (#895 slice 2c), via the same `scan()` (printed, not typed): TOTAL 84 -> 74. The 10
`issue list` list-literal sites moved to `gh_api.list_issues_gh` (whose fallback argv is built inside the
EXEMPT helper): auto_unpark 2 -> 0 and reconcile 1 -> 0 (entries removed), doctor 13 -> 7, assign 2 -> 1
(the remaining one is the `issue edit` write). status.py keeps its 3 by design. doctor.py's raising
wrapper is built on `_gh_runner`, so it adds no `["gh", *args]` literal.
MEASURED 2026-10-10 (#895 slice 3a), via the same `scan()` (printed, not typed): TOTAL 74 -> 57. The 17 issue
WRITE list-literal sites (comment, create, edit body, close, add/remove label, add-assignee) moved to the
`gh_api` REST write helpers, whose fallback argv is built inside the EXEMPT helper: dossier, blockers,
promote, unpark and assign entries removed (1, 1, 1, 2, 1 -> 0), triage 4 -> 1 (the `label create` stays),
sources 28 -> 20. Still open on #895: note(), every `label create`, lifecycle label swaps, PR/project writes.
MEASURED 2026-10-10 (#895 slice 4a-1), via the same `scan()` (printed, not typed): TOTAL 57 -> 50. Seven
`gh pr view` READ sites moved to `gh_api.view_pr_gh` / `gh_api.pr_for_branch_gh` (whose ONE `pr view`
fallback argv is built inside the EXEMPT helper): work.py 14 -> 9 (merge_rights, _comment_directive,
post_review, _open_pr_refusal, _pr_merged), doctor 7 -> 6 (_stray_commits_after_merge), rebase_brief
1 -> 0 (pr_description; entry removed). The adapters bind `_GH = "gh"` once, so they add no
`["gh", *args]` site. Still open on #895 (PR): work.py R1 merge gate, R4 reviewDecision, R5 sibling list,
R9 design-PR list, W1-W5 writes; doctor R10 (plus 5 non-PR); verify_merge 3 (ready/create/merge).
MEASURED 2026-10-10 (#895 slice 4a-2 PR A), via the same `scan()` (printed, not typed): TOTAL 50 -> 48.
The merge gate R1 (`gh pr view --json mergeable,mergeStateStatus,statusCheckRollup,headRefOid`) moved to
`gh_api.view_pr_gh` + `gh_api.pr_check_rollup_gh`, and doctor's landing-PR row R10 (`gh pr list --head
feature/<unit>`) to `gh_api.open_pr_for_branch_gh` + `pr_check_rollup_gh` (fallback argv built inside the
EXEMPT helper): work.py 9 -> 8, doctor 6 -> 5. Still open on #895 (PR): work.py R4 reviewDecision (its
`reviewDecision` half stays GraphQL by decision), R5 sibling list, R9 design-PR list, W1-W5 writes;
verify_merge 3 (ready/create/merge).

This is a deterministic AST test: no probabilistic concurrency, so the AGENTS.md "performance
boundary" rule does not apply.
"""
import ast
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOUNS = {"issue", "pr", "project", "label"}
VERBS = {
    "view", "list", "create", "edit", "comment", "close", "reopen", "delete", "merge", "ready", "diff",
    "checks", "review", "status", "checkout", "develop", "lock", "unlock", "pin", "unpin", "transfer",
    "clone", "copy", "archive", "unarchive", "link", "unlink", "mark-template", "item-add", "item-archive",
    "item-create", "item-delete", "item-edit", "item-list", "field-create", "field-delete",
    "field-list", "update-branch", "revert", "edit-labels",
}
EXEMPT = {"skills/sigma-loop/scripts/gh_api.py"}
INVOCATION = "$HOME/.sigma-venv312/bin/python -m pytest tests/test_no_direct_gh.py"
GENERIC_INVOCATION = "python -m pytest tests/test_no_direct_gh.py"

# Why 101, not 95. All 101 were READ, not trusted by number:
#  * rule 3 (7 sites) are wrapper / variable-noun argv a literal-noun grep cannot match:
#    doctor.py:1239 (`lambda args: doctor_run(["gh", *args])`), board_setup.py:180,
#    cross_repo.py:242, feature_owner.py:251, ledger.py:897, status.py:162 and :167 (each builds
#    `["gh", *args]` for a subprocess/run seam).
#  * The dossier's 95 was not reproduced exactly (its grep is not in the repo): a plain
#    `"gh", "<noun>"` line grep over skills/ + hooks/ gives 37 lines, of which 3 are not call
#    sites (doctor.py:59 is a len-2 equality test, doctor.py:1234 a docstring, work.py:3452 is the
#    2nd line of the ONE list starting at 3451), i.e. 35 rule-1 sites. Rule 2 (59) and rule 3 (7)
#    are what a literal grep cannot see. 101 >= 95, so no scanner blind spot of that size.
#  * rule 2 (59 sites) all carry sources-convention argv (no "gh"): `source._run([...])`,
#    `self._gh_json([...])`, `run_([...])`; every one was read and is a real gh issue/label/project
#    argv. The two non-gh tuples the plain rule would have counted (doctor.py `("project", "local")`,
#    ledger.py `("pr", "checks_total")`) are excluded by the VERBS filter.
BASELINE = {
    "skills/sigma-define/scripts/define.py": 2,
    "skills/sigma-doctor/scripts/board_migrate.py": 2,
    "skills/sigma-doctor/scripts/doctor.py": 5,
    "skills/sigma-init/scripts/board_setup.py": 1,
    "skills/sigma-loop/scripts/cross_repo.py": 1,
    "skills/sigma-loop/scripts/feature_owner.py": 1,
    "skills/sigma-loop/scripts/ledger.py": 1,
    "skills/sigma-loop/scripts/sources.py": 20,
    "skills/sigma-loop/scripts/triage.py": 1,
    "skills/sigma-loop/scripts/work.py": 8,
    "skills/sigma-rebase/scripts/verify_merge.py": 3,
    "skills/sigma-status/scripts/status.py": 3,
}


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_site(elts):
    if not elts:
        return False
    first = _str(elts[0])
    if first == "gh":
        if len(elts) < 2:
            return False
        second = _str(elts[1])
        if second is None:                                   # rule 3: wrapper / variable noun
            return True
        return second in NOUNS and len(elts) >= 3            # rule 1
    return first in NOUNS and len(elts) >= 2 and _str(elts[1]) in VERBS      # rule 2


def count_sites(source):
    tree = ast.parse(source)
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.List, ast.Tuple)) and _is_site(n.elts))


def scan(root=ROOT):
    out = {}
    for sub in ("skills", "hooks"):
        for path in sorted((root / sub).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in EXEMPT:
                continue
            n = count_sites(path.read_text(encoding="utf-8"))
            if n:
                out[rel] = n
    return out


def test_no_new_file_with_direct_gh_sites():
    new = {p: n for p, n in scan().items() if p not in BASELINE}
    assert not new, (
        "new direct `gh issue|pr|project|label` call site(s) in a file outside the baseline: %r -- "
        "use skills/sigma-loop/scripts/gh_api.py instead (or, if this is a migration artefact, "
        "justify it in review). Run: %s" % (new, INVOCATION))


def test_no_baseline_file_gained_sites():
    now = scan()
    up = {p: (BASELINE[p], now.get(p, 0)) for p in BASELINE if now.get(p, 0) > BASELINE[p]}
    assert not up, ("direct gh call sites went UP (baseline, now): %r -- route the new call through "
                    "gh_api.py instead of adding to the baseline" % up)


def test_no_stale_baseline_entry():
    now = scan()
    down = {p: (BASELINE[p], now.get(p, 0)) for p in BASELINE if now.get(p, 0) < BASELINE[p]}
    assert not down, ("baseline is stale (baseline, now): %r -- lower the baseline to N (or to 0 / "
                      "delete the entry): the ratchet only goes down" % down)


def test_pr_view_fallback_argv_lives_only_in_gh_api():
    """#895 4a-1/4a-2: the migrated PR reads' `pr view` / `pr list` argv is built ONLY inside gh_api (its
    one fallback each). The migrated callers carry none; work.py keeps exactly ONE unmigrated `gh pr view`
    read (R4 reviewDecision, GraphQL-only by decision), so a migrated site cannot quietly come back."""
    helper = (ROOT / "skills/sigma-loop/scripts/gh_api.py").read_text(encoding="utf-8")
    assert '["pr", "view", ref,' in helper
    assert '["pr", "list", "--repo", repo,' in helper
    for rel in ("skills/sigma-rebase/scripts/rebase_brief.py", "skills/sigma-doctor/scripts/doctor.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert '"pr", "view"' not in text and '"pr", "list"' not in text, rel
    work = (ROOT / "skills/sigma-loop/scripts/work.py").read_text(encoding="utf-8")
    assert work.count('"gh", "pr", "view"') == 1


def test_exempt_is_exactly_the_helper_and_it_exists():
    assert EXEMPT == {"skills/sigma-loop/scripts/gh_api.py"}
    for rel in EXEMPT:
        assert (ROOT / rel).is_file(), "exempt helper %s is missing or renamed" % rel


def test_documented_invocation_is_consistent():
    assert GENERIC_INVOCATION in INVOCATION
    for rel in ("tests/test_no_direct_gh.py", "docs/cloud-sessions.md", "AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert INVOCATION in text and GENERIC_INVOCATION in text, rel


# ---- THE ONE COUNTING RULE, pinned on synthetic source -------------------------------------------

def test_rule_counts_covered_shapes():
    assert count_sites('x = ["gh","issue","view"]') == 1
    assert count_sites('x = ("gh","pr","list")') == 1
    assert count_sites('src._run(["issue","list"])') == 1
    assert count_sites('x = ("project", "item-edit", "--id")') == 1
    assert count_sites('f(g(["gh","label","create","x"]))') == 1
    assert count_sites('x = ["gh", *args]') == 1
    assert count_sites('x = ["gh", noun, "view"]') == 1


def test_rule_excludes_api_and_other_nouns_and_bare_gh():
    for src in ('["gh","api","repos/x"]', 'which("gh")', 'X = "gh"', '["gh","repo","view"]',
                '["gh","run","list"]', '["gh","auth","status"]', '["gh","--version"]',
                '("project", "local")', '("pr", "checks_total")',   # non-gh tuples: why VERBS exists
                '"""["gh","pr","view"]"""', '# ["gh","pr","view"]', 'x = "gh issue list"'):
        assert count_sites(src) == 0, src


def test_documented_gap_shapes_are_not_detected():
    """DOCUMENTED GAP -- NOT DETECTED. Pinned so widening the scanner is a conscious edit."""
    assert count_sites('"issue view".split()') == 0
    assert count_sites('args = ["issue"]\nargs += ["edit"]') == 0
    assert count_sites('args = []\nargs.append("issue")') == 0
    assert count_sites('x = [cmd, "issue", "view"]') == 0
    assert count_sites('_gh("issue", "view")') == 0
    assert count_sites('subprocess.run("gh issue list", shell=True)') == 0


def test_scan_wall_time_is_recorded_and_bounded():
    t0 = time.perf_counter()
    scan()
    assert time.perf_counter() - t0 < 30      # generous hang guard; the measured time is in the docstring
