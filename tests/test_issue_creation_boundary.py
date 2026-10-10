"""Issue-creation call-site guard (#462): `GitHubSource.create_dependency` must stay the ONE real
place this repo ever runs `gh issue create` — every non-comment issue this codebase creates on its
own behalf goes through it (via `handoff.create_tracked_issue`), so it stays labeled and assigned
instead of orphaned. A future call site that opens an issue some OTHER way (a second, divergent
implementation) is exactly the hardened-sibling-divergence pattern this repo's own retrospectives
keep flagging (the alignment-collect `+++`-misparse leak and its unfixed risk-detect.sh twin are the
canonical example) — this guard exists so that shape can't quietly reappear here.

WHY AST, NOT REGEX — the identical argument tests/test_import_boundary.py's own docstring makes for
a different guard ("Grepping for the substring ... would false-positive on this very sentence"):
`skills/sigma-init/scripts/sdlc_init.py:196` prints a human-facing instruction telling an operator to
run `gh issue create --label sdlc:goal ...` by hand during `sigma-init --github --demo` setup — a
plain `ast.Constant` string argument to `print()`, never a `List`/`Tuple` literal (confirmed by
inspection, not assumed — see test_ignores_prose_mentions_of_issue_create below, which plants the
same shape as a fixture). A substring/regex scan over that file's text would false-positive on it. So
this checker parses every file with `ast` and only ever inspects real `ast.List`/`ast.Tuple` literal
nodes — a comment, docstring, or plain string constant produces none, regardless of what text it
contains.

WHAT COUNTS AS A MATCH. The one real call site (`sources.py:347`, inside
`GitHubSource.create_dependency`) builds `args = ["issue", "create", *self._repo_args(), ...]` and
runs it via `self._run(args)` (which itself prepends the `gh` binary — never part of this literal
list). So a match is: an `ast.List` or `ast.Tuple` node whose first two elements are both
`ast.Constant` string literals, `.value == "issue"` then `.value == "create"`, in that order — the
exact literal shape the real site uses today. The enclosing `FunctionDef`/`ClassDef` is tracked via a
simple stack-based visitor (the same technique tests/test_vocabulary_coverage.py already uses for its
own Call-node scan), so a match can be reported as "which function, in which class" and checked
against the allowlist below.

ALLOWLIST (#895 slice 3a: now TWO entries) — the first: `skills/sigma-loop/scripts/sources.py`, class `GitHubSource`, method
`create_dependency` — the helper's own internal call. Anything else that matches is a violation.

The second entry is `skills/sigma-loop/scripts/gh_api.py`, no class, function `create_issue`: since #895
slice 3a the REST helper carries its own literal `["issue", "create", ...]` fallback argv (the GraphQL
fallback `gh issue create`). The entry is PER FUNCTION, so a non-literal or a second literal anywhere
else in gh_api.py (or any other file) is still a violation. The other half of the guarantee is no longer
"the literal lives in one place" but "only create_dependency CALLS the helper": see
test_only_create_dependency_calls_create_issue, which finds callers of `create_issue` / `_issue_create`
by AST and pins them to `GitHubSource.create_dependency`.

NAMED LIMITATION, stated rather than silently assumed away (matching this repo's own established
documentation style — see tests/test_vocabulary_coverage.py's "NAMED LIMITATION" passage and
tests/test_import_boundary.py's "DYNAMIC IMPORTS ... ARE DELIBERATELY NOT COVERED" passage for the
same move applied to their own guards): this catches a literal `["issue", "create", ...]`
construction, the shape every real call site in this codebase uses today (confirmed by the plan's own
exhaustive call-site audit, sigma-core-changes-plan.md section 7). A future call site that builds
its args list through a helper function, string concatenation, or `*`-unpacking from a variable would
evade this specific check. Scoped honestly, not oversold.

SCOPE: every `.py` file under `skills/` and `hooks/` — not the private package, which contains no
issue-creation code of any kind (confirmed by the same call-site audit), so including it would just
be dead scope, unlike test_import_boundary.py's own guard which genuinely needs the private package
for its different purpose. Reuses test_import_boundary.py's own directory-skip logic (`__pycache__`,
`*.egg-info`, and the content-based `_is_virtualenv` check) rather than reimplementing a weaker
name-based one — this repo's own established lesson (stated explicitly in that file's docstring) is
that a name-based skip has already shipped bugs twice. Re-typed here rather than imported, for the
same reason tests/test_import_boundary.py gives for reimplementing it instead of
the private side's licence guard's identical helper: this repo has no shared test-helper module (no
conftest.py, and no test file imports another's functions).
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The two directories this guard scans — the plugin, matching test_import_boundary.py's own scope
#: for skills/hooks (see module docstring for why the private package is out of scope here).
_SCAN_DIRS = ("skills", "hooks")

#: Allowlisted (relpath-under-"skills"-POSIX, class, function) entries: create_dependency's own
#: internal call, plus (slice 3a) the REST helper's own fallback argv. Nothing else may open an issue this way.
_ALLOWED_IN_SKILLS = frozenset({
    ("sigma-loop/scripts/sources.py", "GitHubSource", "create_dependency"),
    # #895 slice 3a: the REST helper's own GraphQL-fallback argv, per FUNCTION (class None).
    ("sigma-loop/scripts/gh_api.py", None, "create_issue"),
})
_GH_API_CREATE = ("sigma-loop/scripts/gh_api.py", None, "create_issue")
#: The only enclosing (relpath, class, function) allowed to CALL the REST create helper or its wrapper.
#: The chain is create_dependency -> _create_issue -> _issue_create -> gh_api.create_issue; each link is
#: called only by the one before it (asserted below), so the entry point stays create_dependency.
_ALLOWED_CREATE_CALLERS = frozenset({
    ("sigma-loop/scripts/sources.py", "GitHubSource", "create_dependency"),
    ("sigma-loop/scripts/sources.py", "GitHubSource", "_issue_create"),
    ("sigma-loop/scripts/sources.py", "GitHubSource", "_create_issue"),
})
_CREATE_NAMES = frozenset({"create_issue", "_issue_create", "_create_issue"})


def _is_virtualenv(directory):
    """A directory IS a virtualenv only if it carries BOTH `pyvenv.cfg` and a launcher dir (`bin/`
    POSIX, `Scripts/` Windows) — never by name alone. Same rule as
    tests/test_import_boundary.py's `_is_virtualenv` — see this file's module docstring for why it
    is reimplemented here rather than imported."""
    return ((directory / "pyvenv.cfg").is_file()
            and ((directory / "bin").is_dir() or (directory / "Scripts").is_dir()))


def _owned_py_files(root):
    """Every .py file under `root` this repo owns — identical logic to
    tests/test_import_boundary.py's `_owned_py_files` (see that file's docstring for the full
    argument: one structural exclusion for a real virtualenv, two by-name exclusions
    (`__pycache__`, `*.egg-info`) safe only because neither is a plausible module name)."""
    out = []
    for path in sorted(root.rglob("*.[pP][yY]")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or any(part.endswith(".egg-info") for part in rel.parts):
            continue
        if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1])) for i in range(len(rel.parts) - 1)):
            continue
        out.append(path)
    return out


def _is_issue_create_list(node):
    """True for an `ast.List`/`ast.Tuple` node whose first two elements are the literal strings
    "issue" then "create", in that order — the exact shape `sources.py`'s real call site uses today
    (`["issue", "create", *self._repo_args(), ...]`). See module docstring: NAMED LIMITATION."""
    if not isinstance(node, (ast.List, ast.Tuple)):
        return False
    elts = node.elts
    if len(elts) < 2:
        return False
    first, second = elts[0], elts[1]
    return (isinstance(first, ast.Constant) and first.value == "issue"
            and isinstance(second, ast.Constant) and second.value == "create")


class _Visitor(ast.NodeVisitor):
    """Walks a module tracking the enclosing FunctionDef/ClassDef as a simple stack (the same
    technique tests/test_vocabulary_coverage.py already uses for its own Call-node scan), collecting
    every issue-create-shaped List/Tuple as (class_or_None, function_or_None, lineno)."""

    def __init__(self):
        self.matches = []
        self._class_stack = []
        self._func_stack = []

    def visit_ClassDef(self, node):
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def _visit_func(self, node):
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def _record(self, node):
        if _is_issue_create_list(node):
            cls = self._class_stack[-1] if self._class_stack else None
            fn = self._func_stack[-1] if self._func_stack else None
            self.matches.append((cls, fn, node.lineno))

    def visit_List(self, node):
        self._record(node)
        self.generic_visit(node)

    def visit_Tuple(self, node):
        self._record(node)
        self.generic_visit(node)


class _CallVisitor(_Visitor):
    """Same enclosing-scope stack, but collects CALLS of the REST create helper / its source wrapper
    (`gh_api.create_issue(...)`, `self._issue_create(...)`, or a bare imported name)."""

    def visit_List(self, node):          # calls only: the list-literal matches belong to _Visitor
        self.generic_visit(node)

    visit_Tuple = visit_List

    def visit_Call(self, node):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else None)
        if name in _CREATE_NAMES:
            cls = self._class_stack[-1] if self._class_stack else None
            fn = self._func_stack[-1] if self._func_stack else None
            self.matches.append((cls, fn, node.lineno, name))
        self.generic_visit(node)


def _create_callers(root, allowed=frozenset()):
    """One formatted entry per call of `create_issue` / `_issue_create` under `root` outside `allowed`."""
    out = []
    for path in _owned_py_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"), filename=str(path))
        except (SyntaxError, ValueError):
            out.append(f"{rel}: unparseable")
            continue
        v = _CallVisitor()
        v.visit(tree)
        out += [f"{rel}:{ln}: calls the REST issue-create helper outside create_dependency "
                f"({cls}.{fn})" for cls, fn, ln, _n in v.matches if (rel, cls, fn) not in allowed]
    return sorted(out)


def _file_matches(path, root):
    """(relpath, class_or_None, function_or_None, lineno) for every issue-create-shaped match in one
    file, or a single `(relpath, None, None, None)` sentinel (lineno=None) when the file is
    unparseable — shared by `_raw_matches` and `_violations` below so the two can never drift on what
    counts as a match."""
    rel = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except (SyntaxError, ValueError):
        return [(rel, None, None, None)]
    visitor = _Visitor()
    visitor.visit(tree)
    return [(rel, cls, fn, lineno) for cls, fn, lineno in visitor.matches]


def _raw_matches(root):
    """Every issue-create-shaped match under `root`, with NO allowlist applied — pure, and proven
    against the real tree by test_create_dependencys_own_list_is_found_but_suppressed (the "found"
    half of proving the allowlist actually discriminates, not just "matches nothing")."""
    out = []
    for path in _owned_py_files(root):
        out += [m for m in _file_matches(path, root) if m[3] is not None]
    return out


def _violations(root, allowed=frozenset()):
    """The guard itself, as a pure function — proven against planted fixtures (module docstring:
    NAMED LIMITATION) as well as the real tree, mirroring test_import_boundary.py's own
    `_boundary_violations(root, banned)`. One formatted entry per issue-create-shaped List/Tuple NOT
    in `allowed`; an unparseable file is reported as a violation too (fail loud, never silently
    skipped — matching test_import_boundary.py's own choice)."""
    violations = []
    for path in _owned_py_files(root):
        for rel, cls, fn, lineno in _file_matches(path, root):
            if lineno is None:
                violations.append(f"{rel}: unparseable")
                continue
            if (rel, cls, fn) in allowed:
                continue
            where = f"{cls}.{fn}" if cls and fn else (fn or cls or "<module>")
            violations.append(f"{rel}:{lineno}: opens an issue outside create_dependency ({where})")
    return sorted(violations)


# --------------------------------------------------------------------------- non-vacuous by construction


def test_py_sources_are_non_empty():
    """Mirrors tests/test_import_boundary.py's own non-vacuous check: a renamed or emptied skills/ or
    hooks/ must fail loudly instead of passing with silently-zero coverage."""
    for name in _SCAN_DIRS:
        assert _owned_py_files(ROOT / name), (
            f"{name}/ has no owned .py files today — if it was renamed or emptied this test would "
            "otherwise pass with zero real coverage"
        )


# --------------------------------------------------------------------------- the real tree


def test_skills_and_hooks_only_open_issues_through_create_dependency():
    """The actual regression guard (plan section 7, done_when clause 4): every issue-create-shaped
    call site under skills/ and hooks/ today is GitHubSource.create_dependency's own — and it would
    fail if a future call site opened an issue outside it."""
    violations = []
    violations += [f"skills/{v}" for v in _violations(ROOT / "skills", _ALLOWED_IN_SKILLS)]
    violations += [f"hooks/{v}" for v in _violations(ROOT / "hooks")]
    assert not violations, (
        "an issue-create-shaped call site exists outside GitHubSource.create_dependency — every "
        "issue this codebase opens on its own behalf must go through handoff.create_tracked_issue, "
        "never a second, divergent implementation:\n  " + "\n  ".join(violations)
    )


def test_gh_api_create_issue_fallback_argv_is_allowlisted():
    """The raw matcher DOES find gh_api.create_issue's own fallback literal (so the allowlist entry is
    load-bearing, not decoration), and the allowlisted checker suppresses exactly that match."""
    root = ROOT / "skills"
    raw = _raw_matches(root)
    assert any((rel, cls, fn) == _GH_API_CREATE for rel, cls, fn, _ in raw), (
        f"the matcher no longer finds gh_api.create_issue's fallback argv: {raw}")
    assert not any(rel == "sigma-loop/scripts/sources.py" for rel, _, _, _ in raw), (
        "sources.py grew a literal issue-create list again; create_dependency now goes via gh_api")
    violations = _violations(root, _ALLOWED_IN_SKILLS)
    assert not any("create_issue" in v or "create_dependency" in v for v in violations), violations
    # and the entry is per FUNCTION: dropping it makes the real tree fail
    only_cd = frozenset(e for e in _ALLOWED_IN_SKILLS if e != _GH_API_CREATE)
    assert any("gh_api.py" in v for v in _violations(root, only_cd))


def test_only_create_dependency_calls_create_issue():
    """The callers of `gh_api.create_issue` and `GitHubSource._issue_create` are exactly
    `GitHubSource.create_dependency` (so the feature-label layer-2 and label pre-creation live in one place)."""
    assert _create_callers(ROOT / "skills", _ALLOWED_CREATE_CALLERS) == []
    found = set()
    for path in _owned_py_files(ROOT / "skills"):
        v = _CallVisitor()
        v.visit(ast.parse(path.read_text(encoding="utf-8-sig", errors="replace")))
        found |= {(path.relative_to(ROOT / "skills").as_posix(), c, f, n) for c, f, _, n in v.matches}
    src = "sigma-loop/scripts/sources.py"
    assert found == {(src, "GitHubSource", "create_dependency", "_create_issue"),
                     (src, "GitHubSource", "_create_issue", "_issue_create"),
                     (src, "GitHubSource", "_issue_create", "create_issue")}, found


def test_a_second_caller_of_create_issue_is_flagged(tmp_path):
    (tmp_path / "second.py").write_text(
        "class Other:\n    def sneak(self):\n        return gh_api.create_issue(run, 't', 'b')\n"
        "    def sneak2(self):\n        return self._issue_create('t', 'b')\n", encoding="utf-8")
    out = _create_callers(tmp_path)
    assert len(out) == 2 and all("second.py" in v for v in out), out


# --------------------------------------------------------------------------- the checker, on fixtures


def test_flags_an_issue_create_list_outside_the_allowlisted_method(tmp_path):
    """done_when clause 1: plant a fake file with an issue-create-shaped call OUTSIDE the allowlisted
    method — proves the guard isn't vacuous. The real call site is `["issue", "create", ...]` (no
    leading "gh" — `_run_gh` prepends the binary separately), so this fixture uses that same
    zero-"gh" shape rather than testing a stricter matcher than the one that ships."""
    (tmp_path / "leak.py").write_text(
        "class Sneaky:\n"
        "    def open_one(self):\n"
        "        args = ['issue', 'create', '--title', 'x']\n"
        "        return self._run(args)\n",
        encoding="utf-8",
    )
    violations = _violations(tmp_path)
    assert len(violations) == 1
    assert "leak.py" in violations[0] and "Sneaky.open_one" in violations[0]


def test_ignores_prose_mentions_of_issue_create(tmp_path):
    """done_when clause 2: a comment, a docstring, and (mirroring the real sdlc_init.py:196 case) a
    plain string constant passed to print() must produce zero violations — proves no false positive
    on prose. Same shape of proof test_import_boundary.py's own
    test_ignores_comment_and_docstring_mentions already gives for its different guard."""
    (tmp_path / "clean.py").write_text(
        '"""Tell a human: run `gh issue create --label sdlc:goal --title ...` by hand."""\n'
        "# args = ['issue', 'create'] -- intentionally commented out, not a real list literal\n"
        "def demo_instructions():\n"
        "    print('run: gh issue create --label sdlc:goal --title \\'Demo goal\\'')\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_flags_an_unparseable_file(tmp_path):
    """Matches test_import_boundary.py's own choice: ast.parse raising SyntaxError must surface as a
    normal violation, not an uncaught traceback that aborts the whole scan."""
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    assert _violations(tmp_path) == ["broken.py: unparseable"]


def test_does_not_flag_a_two_element_list_of_unrelated_strings(tmp_path):
    """A plain two-string list that isn't "issue" then "create" must never be flagged — the matcher
    checks VALUES, not merely "a two-element list of constants"."""
    (tmp_path / "clean.py").write_text(
        "args = ['status', 'view']\n",
        encoding="utf-8",
    )
    assert _violations(tmp_path) == []


def test_flags_a_module_level_issue_create_list_with_no_enclosing_function():
    """A match with no enclosing FunctionDef/ClassDef at all must still be reported (as "<module>"),
    not silently dropped by the stack-based visitor when both stacks are empty."""
    matches = _Visitor()
    matches.visit(ast.parse("args = ['issue', 'create', '--title', 'x']\n"))
    assert matches.matches == [(None, None, 1)]
