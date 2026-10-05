"""The plugin/product import boundary, core direction, enforced (spec §1.1 rule 1, issue #97).

skills/ and hooks/ are the plugin; the private package (banned by `_BANNED_PRIVATE`) is the
product. Neither may `import` the other as a Python package — THE
ALLOWED COUPLING IS FILE FORMATS ONLY. The product's ingest is specified to read ledger/*/*.jsonl,
goal frontmatter, config.json and state/* straight off disk (see the design spec's §1.1 and
ledger.py's own "an older reader ignores a field it does not know" contract): reading a path is
fine, importing the private package, `import skills` or `import hooks` is not.

WHERE THE OTHER DIRECTION WENT (#2584, S1-G11). The private package's own check -- it never
imports the plugin -- and its two planted probes in the private package's real web/ and api/
directories now live on the private side, beside the code they police, so this file names no
private directory and runs unchanged in a public snapshot of the core. The checker's REVERSE
fixture tests (`_BANNED_PLUGIN`) stay: they pin `_boundary_violations` itself, on planted trees.

WHY AST, NOT TEXT. Grepping for the substring "import <private package>" would false-positive on
prose like this docstring's and would false-negative on `import <it> as x` or
`import os, <it>, sys`. (A repo-wide grep, unlike this checker, would also trip on
test_self_contained.py's legitimate prose comment — the brief for this file calls that out by name
— but this guard itself never walks tests/, only the plugin and the private package, so that file
was never actually at risk from THIS checker specifically.) So this checker parses every file with
`ast` and inspects only `ast.Import`/`ast.ImportFrom` nodes: real import statements, never comments
or docstrings. Verified empirically (not assumed): a docstring plus a commented-out import of the
private package produces zero Import/ImportFrom nodes — see
test_ignores_comment_and_docstring_mentions.

WHAT COUNTS AS A MATCH. A plain import, a submodule import, an aliased submodule import (Import),
a from-import of the package and a from-import of a submodule (ImportFrom) are all caught by
comparing the dotted target's FIRST segment — `"<pkg>.ingest".split(".", 1)[0] == "<pkg>"` —
against the banned name. An `as`-alias only renames the LOCAL binding, never the thing imported, so
it needs no separate handling. A merely similarly-named module (the banned name plus a suffix,
`skills_extra`) is NOT caught, because the comparison is exact-segment, not substring/prefix — see
test_does_not_flag_a_module_whose_name_merely_starts_with_the_banned_name.

ONLY ABSOLUTE IMPORTS COUNT (`ImportFrom.level == 0`). `from . import x` and even
`from .<banned name> import x` (level=1, module == the banned name, confirmed via ast.dump) resolve
a name INSIDE the current package, never the top-level package of the same name, so they can never
cross this boundary no matter what text follows the dots — see
test_ignores_relative_import_named_like_the_banned_module. An import is matched only by the actual
absolute target of a real import statement, first segment, exact — never a substring, and never a
directory name. (The directory SKIP is a separate mechanism from import matching; see THE DIRECTORY
SKIP below for exactly what it does and does not exclude by name — it is not all structural, and
saying otherwise would be the exact false claim the licence guard's own docstring records shipping
and having to correct.)

DYNAMIC IMPORTS — `importlib.import_module("<pkg>")`, `__import__("<pkg>")` — ARE DELIBERATELY
NOT COVERED. Both take an arbitrary expression as their argument in the general case (a variable, a
built-up string, a config value); a check that only catches the literal-string special case would
miss the general one while advertising coverage it doesn't have. The literal case is also the least
likely form of accidental coupling — nobody reaches for `importlib.import_module` by accident when
a plain `import` already does the job and IS caught here. Staying scoped to
`ast.Import`/`ast.ImportFrom` also keeps this checker structurally clear of
skills/sigma-loop/scripts/*.py's legitimate, unrelated `importlib.util.spec_from_file_location(name,
path)` calls, used to load SIBLING scripts by file path rather than by package name (discovery.py,
handoff.py, loop.py, sources.py, state.py, sync.py, watch.py, work.py all do this today) — that
call produces no Import/ImportFrom node at all, so it is never a special case this guard has to
carve out; see test_ignores_the_spec_from_file_location_sibling_loading_pattern, and, against the
real tree, test_the_plugin_does_not_import_the_private_package passing today.

THE DIRECTORY SKIP, THE SAME APPROACH AS THE LICENCE GUARD — worth being exact about which part is
which. That guard's module docstring records two holes a near-identical guard shipped: a name-based
skip (`env`/`build`/`dist`, at any depth — also plausible module names) and a `.gitignore`-based
skip (which only relocated the same name heuristic). Its fix, mirrored here, is exactly ONE
structural (content-based) exclusion — a directory IS a virtualenv only if it carries BOTH
`pyvenv.cfg` and a `bin/`/`Scripts/` launcher dir, both required, so one planted `pyvenv.cfg`
can't veto a real module (`_is_virtualenv`) — plus TWO exclusions that ARE by name: `__pycache__`
and anything ending `.egg-info`. Those two are safe to match by name, unlike `env`/`build`/`dist`,
because neither is a plausible module name: `__pycache__` is RESERVED by CPython (PEP 3147 writes
bytecode there) and the dot in `*.egg-info` makes it unimportable as a Python package — and both are
gitignored at the repo root, so their contents cannot be committed without `git add -f`. Do not
extend this by-name list to a merely conventional name (`node_modules`, `.tox`) without checking
both halves first — reserved-or-unimportable, AND gitignored.

This logic is reimplemented here (`_is_virtualenv`, `_owned_py_files`) rather than imported from
the licence guard, and the real reason is narrower than "every guard file defines its own helpers":
this repo has no shared test-helper MODULE to import from (no conftest.py). The one exception, as
of #462, is tests/test_backlog_check.py importing test_handoff.py's `FakeSource` for a single
behavioral proof (reusing one specific, already-tested double rather than a second, divergent copy
— the same hardened-sibling-divergence argument this file makes elsewhere) — not a shared helper
library, and not a reason for THIS guard to import its own directory-skip logic from a sibling
either. The licence guard factors its scan into named helpers the same way this file does;
tests/test_self_contained.py does NOT — it inlines its scan directly in the test bodies and
deliberately shares a module-level SKIP_DIRS constant instead, precisely so its own pinning test
reads the SAME object the scan uses (see that file's comment: a private copy in an earlier version
"pinned nothing at all"). So test_self_contained.py is not a second example of "defines its own
scan helpers" — it demonstrates the opposite convention, inlining the scan and sharing a constant —
and citing it as such would misstate the very convention it demonstrates.

test_modules_named_like_build_trees_are_still_checked and
test_a_lone_pyvenv_cfg_cannot_hide_a_module below pin the same two holes shut for THIS checker, on
planted fixtures, so the coverage does not depend on either tree happening to contain a
build/dist/env directory today.

MESSAGES ARE SORTED, NEVER A RAW SET. A violation's hit names are joined via
`", ".join(sorted(hits))`, never an f-string over the set directly: CPython randomizes string
hashing per process (confirmed here — a set of the banned names printed in three different orders
across three separate `python3 -c` invocations), so an unsorted join would make failure messages,
and any test asserting on them, flaky across runs.

NON-VACUOUS BY CONSTRUCTION. skills/ and hooks/ already carry real .py files today, so
test_the_plugin_does_not_import_the_private_package has live coverage; the private package has few
by comparison (#95's package skeleton), so — matching the pattern the licence guard settled on —
the checker is factored into a pure function (`_boundary_violations`) and proven against planted
tmp_path fixtures for BOTH directions, so its correctness never depends on what either tree
currently contains. The tree tests themselves also assert `_owned_py_files` is non-empty for every
root they scan, so a renamed or emptied directory fails loudly instead of passing with zero signal
— `_boundary_violations(Path("/does/not/exist"), ...)` returns `[]`, not an error.

KNOWN RESIDUE, stated so nobody mistakes this for airtight (mirrors the licence guard's own note —
checked directly for this file, not assumed true by similarity): `rglob` does not follow symlinked
directories, so a symlinked subdirectory of the private package would hide that tree from this
guard; and planting BOTH a `pyvenv.cfg` and a `bin/` inside a real module still silences it — the
same mechanism that makes the legitimate case work at all (test_a_real_virtualenv_is_skipped
below), verified directly against this file's own `_boundary_violations` rather than assumed from
the licence guard's note. Both are two-artifact, diff-visible moves — the veto this guard closed
was a one-file one.

A further residue specific to this checker: `importlib.util.spec_from_file_location` (see DYNAMIC
IMPORTS above) is out of scope everywhere, including ACROSS the plugin/product boundary itself. That
paragraph credits the call as legitimate sibling-loading inside skills/sigma-loop/scripts/ — true
there — but a future private-side ingest module could use the identical call to load, say,
skills/sigma-loop/scripts/frontmatter.py by file path, and this guard would not flag it: the call
produces no Import/ImportFrom node regardless of which side of the boundary its target file sits on.
That IS the code coupling spec §1.1 rule 1 forbids. This guard does not attempt to detect it.

A THIRD residue, named here because this file is the AST guard and TypeScript cannot be parsed by
it: `.ts`/`.tsx` under the private package's api/ and web/ directories get no import-boundary check
at all from this file — `ast.parse` cannot read them, and no regex scanner is built here (issue
#296 deliberately stops short of one; a regex import-matcher for TS is real tooling, not five lines,
and untested tooling is worse than an honest gap). The licence guard's `.ts`/`.tsx` marker
convention proves the WALK reaches those two directories; it says nothing about what those files
import. E17.S1, once ESLint and real TS source exist, owns this residue — that story gets a real
parser (the TS compiler API or an ESLint rule) for free, at the point there is something real to
point it at.
"""
import ast
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The plugin — both directories are scanned for a real import of the private package.
_PLUGIN_DIRS = ("skills", "hooks")

#: The two banned-root-name sets, one per direction.
_BANNED_PRIVATE = frozenset({"insight"})
_BANNED_PLUGIN = frozenset({"skills", "hooks"})

#: Derived, never spelled (#2580): the banned private root.
_P = next(iter(_BANNED_PRIVATE))


def _is_virtualenv(directory):
    """A directory IS a virtualenv only if it carries BOTH `pyvenv.cfg` and a launcher dir (`bin/`
    POSIX, `Scripts/` Windows) — never by name alone (a real module could be called `env`), and
    never by `pyvenv.cfg` alone (a one-file veto anyone could drop into a real module). Same rule
    as the licence guard's `_is_virtualenv` — see this file's module docstring for why it is
    reimplemented here rather than imported."""
    return ((directory / "pyvenv.cfg").is_file()
            and ((directory / "bin").is_dir() or (directory / "Scripts").is_dir()))


def _owned_py_files(root):
    """Every .py file under `root` this repo owns. ONE exclusion is structural — a virtualenv, by
    content, see `_is_virtualenv` — and TWO are by name, `__pycache__` and `*.egg-info`, safe to
    match by name only because neither is a plausible module name (reserved/unimportable, and both
    gitignored — see the module docstring for the full argument). No git, no ignore rules, and no
    skip for a name a real module COULD plausibly have (`env`, `build`, `dist` are still walked).
    Mirrors the licence guard's `_owned_py_files`.
    """
    out = []
    # "*.[pP][yY]": pathlib's glob is case-sensitive on POSIX regardless of the
    # filesystem, so a plain "*.py" misses LOUD.PY on the Linux CI runner too.
    for path in sorted(root.rglob("*.[pP][yY]")):
        rel = path.relative_to(root)
        if "__pycache__" in rel.parts or any(part.endswith(".egg-info") for part in rel.parts):
            continue
        if any(_is_virtualenv(root.joinpath(*rel.parts[:i + 1])) for i in range(len(rel.parts) - 1)):
            continue
        out.append(path)
    return out


def _imported_root_modules(source, filename):
    """Parse `source` and return the set of top-level module names it imports via a real
    `import`/`from ... import` statement — see the module docstring for exactly what counts as a
    match and why dynamic `importlib.import_module`/`__import__` calls are out of scope.
    """
    roots = set()
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def _boundary_violations(root, banned):
    """The guard, as a pure function tested against planted fixtures (see module docstring) rather
    than only against whatever `root` currently contains. Returns one sorted entry per offending
    file, relative to `root`: "relpath: imports x, y", or "relpath: unparseable" when `ast.parse`
    itself cannot read the file (invalid syntax, an embedded NUL byte, ...) — reported as a normal
    violation rather than an uncaught traceback, because failing loud beats a raw exception, and
    silently skipping the file would be exactly the kind of hole this file exists to close.
    """
    violations = []
    for path in _owned_py_files(root):
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        try:
            hits = _imported_root_modules(text, str(path)) & banned
        except (SyntaxError, ValueError):
            violations.append(f"{rel}: unparseable")
            continue
        if hits:
            violations.append(f"{rel}: imports {', '.join(sorted(hits))}")
    return sorted(violations)


# --------------------------------------------------------------------------- the tree


def test_the_plugin_does_not_import_the_private_package():
    """done_when, direction 1 (spec §1.1 rule 1): the plugin never imports the product as a Python
    package. The ALLOWED coupling is reading the product's output files by path (there were none
    when this was written — its ingest was still a stub, issue #98); an `import` or a from-import of
    the private package or any of its submodules is not."""
    for name in ("skills", "hooks"):
        assert name in _PLUGIN_DIRS, (
            f"{name}/ was dropped from _PLUGIN_DIRS — it would silently stop being scanned at all"
        )
        assert _owned_py_files(ROOT / name), (
            f"{name}/ has no owned .py files today — if it was renamed or emptied this test would "
            "otherwise pass with zero real coverage (_boundary_violations on a missing/empty "
            "directory returns [] with no error)"
        )
    violations = []
    for name in _PLUGIN_DIRS:
        violations += [f"{name}/{v}" for v in _boundary_violations(ROOT / name, _BANNED_PRIVATE)]
    assert not violations, (
        f"skills/ or hooks/ must never import {_P} — the contract with the private package is file "
        "formats, never imports (spec §1.1 rule 1):\n  " + "\n  ".join(violations)
    )


# --------------------------------------------------------------------------- the checker, on fixtures


def test_flags_plain_import(tmp_path):
    (tmp_path / "leak.py").write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text(f"import {_P}.ingest\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_aliased_import(tmp_path):
    """The alias only renames the LOCAL binding; the thing actually imported is still the package."""
    (tmp_path / "leak.py").write_text(f"import {_P}.ingest as ii\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_from_import(tmp_path):
    (tmp_path / "leak.py").write_text(f"from {_P} import ingest\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_from_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text(f"from {_P}.ingest import Foo\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_multiple_names_on_one_import_line(tmp_path):
    (tmp_path / "leak.py").write_text(f"import os, {_P}, sys\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_import_inside_a_function(tmp_path):
    """`ast.walk` descends into every nested node; `for node in tree.body` would only look at
    top-level statements and silently stop catching a lazy import like this one — the most likely
    real-world form of accidental coupling (a function that imports the package only when called)."""
    (tmp_path / "leak.py").write_text(
        f"def load():\n    import {_P}\n    return {_P}\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_import_under_type_checking(tmp_path):
    """Same reason as above: an import nested under `if TYPE_CHECKING:` is still a real ast.Import
    node, just not at module top level, so `tree.body` alone would miss it too."""
    (tmp_path / "leak.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        f"    import {_P}\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_ignores_comment_and_docstring_mentions(tmp_path):
    """Proves AST, not substring: test_self_contained.py itself legitimately names the package in
    a comment, and this repo's own docstrings discuss it in prose — neither may trip this guard."""
    (tmp_path / "clean.py").write_text(
        f'"""This module discusses {_P} and imports {_P}, in prose only."""\n'
        f"# from {_P} import something -- intentionally commented out\n"
        "x = 1\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []


def test_ignores_relative_import_named_like_the_banned_module(tmp_path):
    """A relative import named like the banned package is level=1 (confirmed via ast.dump), so it
    resolves inside the CURRENT package, never the top-level one — it must not be flagged
    regardless of what its module text says."""
    (tmp_path / "leak.py").write_text(f"from .{_P} import x\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []


def test_does_not_flag_a_module_whose_name_merely_starts_with_the_banned_name(tmp_path):
    (tmp_path / "leak.py").write_text(f"import {_P}ful\nimport {_P}s\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []


def test_ignores_the_spec_from_file_location_sibling_loading_pattern(tmp_path):
    """skills/sigma-loop/scripts/*.py load sibling scripts via
    importlib.util.spec_from_file_location(name, path) — a runtime call, not an import statement —
    specifically to share code without a package import. This plants the real pattern (see e.g.
    discovery.py) rather than only trusting a read of the real files; the real tree is checked
    too, by test_the_plugin_does_not_import_the_private_package passing today."""
    (tmp_path / "loader.py").write_text(
        "import importlib.util\n"
        "import pathlib\n"
        "_HERE = pathlib.Path(__file__).parent\n"
        "def _load(name):\n"
        "    spec = importlib.util.spec_from_file_location(name, _HERE / f'{name}.py')\n"
        "    m = importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(m)\n"
        "    return m\n"
        "frontmatter = _load('frontmatter')\n",
        encoding="utf-8",
    )
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []


def test_a_file_with_a_bom_still_parses(tmp_path):
    """A BOM (common from Windows editors) must not crash the AST parse — utf-8-sig strips it
    before ast.parse ever sees it; plain utf-8 would leave a stray U+FEFF that ast.parse rejects."""
    (tmp_path / "leak.py").write_bytes(b"\xef\xbb\xbf" + f"import {_P}\n".encode())
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"leak.py: imports {_P}"]


def test_flags_an_unparseable_file(tmp_path):
    """ast.parse raises SyntaxError on invalid syntax (Python-2-only `print` statements, a
    too-new-for-the-running-interpreter construct, ...). That must surface as a normal violation,
    not an uncaught traceback that aborts the whole scan."""
    (tmp_path / "broken.py").write_text("print 'not valid python 3'\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == ["broken.py: unparseable"]


def test_flags_a_file_with_a_null_byte(tmp_path):
    """A NUL byte is unparseable, and WHICH exception it raises is version-dependent.

    Measured: ValueError on 3.9-3.11, SyntaxError on 3.12+ (the tokenizer rewrite). CI runs 3.10
    and 3.12, so both arms are reached across the matrix and catching both is required — but note
    that ON 3.12+ THIS FIXTURE EXERCISES THE SyntaxError ARM, the same one
    test_flags_an_unparseable_file covers, so it does not pin the ValueError arm there. Do not
    read a green 3.12 run as proof that narrowing the except clause is safe: it is not, and on
    3.10 it breaks.
    """
    (tmp_path / "nul.py").write_bytes(f"import {_P}\x00\n".encode())
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == ["nul.py: unparseable"]


def test_flags_reverse_direction_plain_import(tmp_path):
    (tmp_path / "leak.py").write_text("import hooks\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports hooks"]


def test_flags_reverse_direction_from_submodule_import(tmp_path):
    (tmp_path / "leak.py").write_text("from skills.sdlc_loop import scripts\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports skills"]


def test_flags_both_banned_names_in_one_file(tmp_path):
    (tmp_path / "leak.py").write_text("import skills\nimport hooks\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PLUGIN) == ["leak.py: imports hooks, skills"]


# --------------------------------------------------------------------------- structural skip, on fixtures


def test_modules_named_like_build_trees_are_still_checked(tmp_path):
    """The hole the licence guard's docstring records two prior versions shipping:
    `env`, `build`, `dist` are plausible MODULE names too, so a name-based skip can't tell them
    apart from a build artifact. None of these is a real virtualenv (no pyvenv.cfg); all six must
    be caught."""
    for rel in ("env/leak.py", "build/leak.py", "dist/leak.py",
                "ingest/env/leak.py", "metrics/dist/leak.py", "dash/build/leak.py"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"import {_P}\n", encoding="utf-8")
    violations = _boundary_violations(tmp_path, _BANNED_PRIVATE)
    assert len(violations) == 6, f"a module must never be skipped for its NAME; got {violations}"


def test_a_real_virtualenv_is_skipped(tmp_path):
    """The legitimate exclusion, identified by what the directory CONTAINS — not by being named
    `env`, which the test above insists must be checked when it is a real module."""
    venv = tmp_path / "env"
    (venv / "lib").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (venv / "lib" / "vendored.py").write_text(f"import {_P}\n", encoding="utf-8")
    (tmp_path / "mine.py").write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"mine.py: imports {_P}"]


def test_a_lone_pyvenv_cfg_cannot_hide_a_module(tmp_path):
    """A one-file veto: dropping pyvenv.cfg into a real module must NOT silence its sources — only
    pyvenv.cfg PAIRED WITH a launcher dir counts as a virtualenv."""
    mod = tmp_path / "realmod"
    mod.mkdir()
    (mod / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    (mod / "leak.py").write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [
        os.path.join("realmod", "leak.py") + f": imports {_P}"]


def test_case_variant_extensions_are_checked(tmp_path):
    """Mirrors the licence guard's own pin for the identical glob: `rglob("*.py")` would
    miss this file's uppercase extension on a case-sensitive filesystem (every Linux CI runner)."""
    (tmp_path / "LOUD.PY").write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == [f"LOUD.PY: imports {_P}"]


def test_pycache_is_skipped(tmp_path):
    p = tmp_path / "__pycache__" / "leak.py"
    p.parent.mkdir(parents=True)
    p.write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []


def test_egg_info_is_skipped(tmp_path):
    p = tmp_path / f"{_P}.egg-info" / "leak.py"
    p.parent.mkdir(parents=True)
    p.write_text(f"import {_P}\n", encoding="utf-8")
    assert _boundary_violations(tmp_path, _BANNED_PRIVATE) == []
