#!/usr/bin/env python3
"""Regression mutation engine (#1056, slice 1 of #808): break one named span of a COPY of the tree.

Each regression is an anchored edit of one named function body or one named table, resolved with
`ast` and never by line number, so an unrelated edit above the span cannot move it.

Gestures:

    python3 evals/regression/mutators.py apply <regression> --form <form> --root <dir>
    python3 evals/regression/mutators.py anchors

`apply` prints the changed repo-relative paths on stdout. Only the one file the anchor names is
rewritten (double-context: one file a phase loads), and only the named lines of it. Point `--root`
at a throwaway copy: the engine mutates whatever tree it is told.

Exit codes: 0 applied, 2 unknown regression/form or bad arguments, 10 anchor missing (stderr
`anchor-missing`, plus best-effort `candidates:` from an UNCOMMITTED rename visible in
`git diff HEAD`; after a commit the diff is empty and the hint is empty), 11 anchor ambiguous,
12 mutation changed nothing (`mutation-noop`). Every failure is a named nonzero exit, never a pass.

Cost: one `ast.parse` of the target file per call, plus one phase-budget run for double-context.
No lock, no state, no network, no model, no background process.
"""
import argparse
import ast
import json
import pathlib
import re
import subprocess
import sys

CODES = {"anchor-missing": 10, "anchor-ambiguous": 11, "mutation-noop": 12}
WORK = "skills/sigma-loop/scripts/work.py"
SCRUB = "skills/sigma-loop/scripts/scrub.py"

# regression -> form -> (file, symbol, kind, arg)
MUTATORS = {
    "skip-plan-review": {"code": (WORK, "_plan_review_refusal", "body", 'return ""')},
    "drop-review-recording": {"code": (WORK, "_review_post_request", "body", "return None")},
    "disable-secret-scan": {
        "drop-pattern": (SCRUB, "SHAPE_RULES", "drop-element", "huggingface-token"),
        "drop-redactor-only": (SCRUB, "_SECRET_PATTERN_SPECS", "drop-element", "authorization-header"),
        "whole-table": (SCRUB, "SHAPE_RULES", "value", "()"),
        "commit-noop": (WORK, "_row_hits", "body", "return None"),
    },
    "double-context": {"words": ("evals/phase_context_budget.py", "--json", "append-words", None)},
}


class MutatorError(Exception):
    def __init__(self, kind, detail):
        super().__init__(detail)
        self.kind, self.code = kind, CODES[kind]


def _write_text(path, text):
    path.write_text(text, encoding="utf-8")


def _find(tree, symbol, path):
    """The one node named `symbol`: a def anywhere, or a module-level assignment."""
    hits = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == symbol]
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == symbol for t in targets):
            hits.append(n)
    if not hits:
        raise MutatorError("anchor-missing", f"{path}::{symbol} not found{_candidates(path)}")
    if len(hits) > 1:
        raise MutatorError("anchor-ambiguous", f"{path}::{symbol} found {len(hits)} times")
    return hits[0]


def _candidates(path, root=None):
    root = root or _candidates.root
    try:
        diff = subprocess.run(["git", "-C", str(root), "diff", "HEAD", "--unified=0", "--", str(path)],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    names = re.findall(r"^-\s*(?:async\s+)?(?:def\s+(\w+)|(\w+)\s*=)", diff, re.M)
    names = [a or b for a, b in names]
    return f"\ncandidates: {', '.join(names)}" if names else ""


_candidates.root = "."


def _span(node, entry):
    """(start, end) 1-based inclusive line span of the entry tuple inside a table assignment."""
    value = node.value
    elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else []
    hits = [e for e in elts if isinstance(e, ast.Tuple) and e.elts and isinstance(e.elts[0], ast.Constant)
            and e.elts[0].value == entry]
    if not hits:
        raise MutatorError("anchor-missing", f"entry {entry!r} not in table")
    if len(hits) > 1:
        raise MutatorError("anchor-ambiguous", f"entry {entry!r} found {len(hits)} times in table")
    return hits[0].lineno, hits[0].end_lineno


def _edit(lines, node, kind, arg, symbol):
    if kind == "body":
        first = node.body[0]
        doc = isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
            and isinstance(first.value.value, str)
        rest = node.body[1:] if doc else node.body
        if not rest:
            raise MutatorError("mutation-noop", f"{symbol} has no body after the docstring")
        indent = " " * rest[0].col_offset
        start = rest[0].lineno
        # decorators/comments between docstring and first statement stay; the body runs to the def end
        lines[start - 1:node.end_lineno] = [f"{indent}{arg}\n"]
    elif kind == "value":
        lines[node.lineno - 1:node.end_lineno] = [f"{symbol} = {arg}\n"]
    else:  # drop-element
        start, end = _span(node, arg)
        lines[start - 1:end] = []


def _double_context(root):
    gate = root / "evals" / "phase_context_budget.py"
    run = subprocess.run([sys.executable, str(gate), "--json"], cwd=str(root), capture_output=True, text=True)
    try:
        phases = json.loads(run.stdout)["phases"]
    except (ValueError, KeyError):
        raise MutatorError("anchor-missing", f"{gate.name} --json gave no phases (exit {run.returncode})")
    for name in sorted(phases):
        files = phases[name]["files"]
        if files and all((root / f).is_file() for f in files):
            words = phases[name]["words"]
            if words <= 0:
                raise MutatorError("mutation-noop", f"phase {name} measured zero words")
            target = root / files[0]
            _write_text(target, target.read_text(encoding="utf-8") + "\n" + "filler " * words + "\n")
            return [files[0]]
    raise MutatorError("anchor-missing", "no phase made only of real file paths")


def apply(regression, form, root):
    """Apply one mutation under `root`; return the changed repo-relative paths."""
    root = pathlib.Path(root)
    _candidates.root = root
    file, symbol, kind, arg = MUTATORS[regression][form]
    if kind == "append-words":
        return _double_context(root)
    path = root / file
    if not path.is_file():
        raise MutatorError("anchor-missing", f"{file} not found{_candidates(file)}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    _edit(lines, _find(ast.parse(text), symbol, file), kind, arg, symbol)
    new = "".join(lines)
    if new == text:
        raise MutatorError("mutation-noop", f"{file}::{symbol} already in the mutated state")
    _write_text(path, new)
    return [file]


def anchors():
    return [f"{r} {f} {file}::{sym}" + (f"#{arg}" if kind == "drop-element" else "")
            for r, forms in MUTATORS.items() for f, (file, sym, kind, arg) in forms.items()
            if kind != "append-words"] + [f"double-context words {MUTATORS['double-context']['words'][0]}::phase"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mutators.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("regression")
    a.add_argument("--form", required=True)
    a.add_argument("--root", required=True)
    sub.add_parser("anchors")
    args = ap.parse_args(argv)
    if args.cmd == "anchors":
        print("\n".join(anchors()))
        return 0
    if args.regression not in MUTATORS or args.form not in MUTATORS[args.regression]:
        print(f"mutators.py: unknown regression/form {args.regression!r} {args.form!r}", file=sys.stderr)
        return 2
    try:
        print("\n".join(apply(args.regression, args.form, args.root)))
    except MutatorError as exc:
        print(f"mutators.py: {exc.kind}: {exc}", file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
