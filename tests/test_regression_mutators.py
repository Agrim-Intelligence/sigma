"""#1056 -- the regression mutation engine: anchored, copy-only, named exit codes."""
import ast
import difflib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "regression" / "mutators.py"
WORK = "skills/sigma-loop/scripts/work.py"
SCRUB = "skills/sigma-loop/scripts/scrub.py"


def _mut():
    spec = importlib.util.spec_from_file_location("mutators_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree(tmp_path):
    dst = tmp_path / "tree"
    for d in ("skills", "evals", "examples"):
        shutil.copytree(ROOT / d, dst / d, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def _apply(root, regression, form):
    return _run("apply", regression, "--form", form, "--root", str(root))


def _func_src(path, name):
    text = path.read_text()
    node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name)
    return "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def _changed_lines(before, after):
    sm = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines(), autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


def test_every_anchor_resolves_on_real_tree():
    mod = _mut()
    for regression, forms in mod.MUTATORS.items():
        for form, (file, symbol, kind, arg) in forms.items():
            if kind == "append-words":
                continue
            tree = ast.parse((ROOT / file).read_text())
            node = mod._find(tree, symbol, file)
            if kind == "drop-element":
                mod._span(node, arg)


def test_skip_plan_review_returns_empty_string(tmp_path):
    root = _tree(tmp_path)
    assert _apply(root, "skip-plan-review", "code").returncode == 0
    src = _func_src(root / WORK, "_plan_review_refusal")
    assert src.rstrip().endswith('return ""')


def test_drop_review_recording_returns_none(tmp_path):
    root = _tree(tmp_path)
    assert _apply(root, "drop-review-recording", "code").returncode == 0
    assert _func_src(root / WORK, "_review_post_request").rstrip().endswith("return None")


def test_drop_pattern_removes_only_huggingface_entry(tmp_path):
    root = _tree(tmp_path)
    before = (root / SCRUB).read_text()
    assert _apply(root, "disable-secret-scan", "drop-pattern").returncode == 0
    after = (root / SCRUB).read_text()
    assert '"huggingface-token"' not in after and '"slack-token"' in after
    ops = _changed_lines(before, after)
    assert len(ops) == 1 and ops[0][0] == "delete" and ops[0][2] - ops[0][1] == 1


def test_drop_redactor_only_removes_only_authorization_entry(tmp_path):
    root = _tree(tmp_path)
    before = (root / SCRUB).read_text()
    assert _apply(root, "disable-secret-scan", "drop-redactor-only").returncode == 0
    after = (root / SCRUB).read_text()
    ops = _changed_lines(before, after)
    assert len(ops) == 1 and ops[0][0] == "delete"
    assert 'frozenset({"authorization-header"' in after  # the set string is untouched
    ast.parse(after)


def test_redactor_only_set_string_is_not_ambiguous(tmp_path):
    # the string also sits in _REDACTOR_ONLY; a grep-style resolver would report 11
    root = _tree(tmp_path)
    assert _apply(root, "disable-secret-scan", "drop-redactor-only").returncode == 0


def test_whole_table_empties_shape_rules(tmp_path):
    root = _tree(tmp_path)
    assert _apply(root, "disable-secret-scan", "whole-table").returncode == 0
    text = (root / SCRUB).read_text()
    assert "\nSHAPE_RULES = ()\n" in text
    assert '("slack-token"' not in text
    ast.parse(text)


def test_commit_noop_makes_row_hits_return_none(tmp_path):
    root = _tree(tmp_path)
    assert _apply(root, "disable-secret-scan", "commit-noop").returncode == 0
    assert _func_src(root / WORK, "_row_hits").rstrip().endswith("return None")


def _gate(root, *extra):
    return subprocess.run([sys.executable, str(root / "evals" / "phase_context_budget.py"), *extra],
                          cwd=str(root), capture_output=True, text=True)


def test_double_context_appends_measured_words(tmp_path):
    root = _tree(tmp_path)
    phases = json.loads(_gate(root, "--json").stdout)["phases"]
    name = next(n for n in sorted(phases) if all((root / f).is_file() for f in phases[n]["files"]))
    before = phases[name]["words"]
    res = _apply(root, "double-context", "words")
    assert res.returncode == 0 and res.stdout.strip() == phases[name]["files"][0]
    after = json.loads(_gate(root, "--json").stdout)["phases"][name]["words"]
    assert after == 2 * before


def test_double_context_gate_green_before_red_after(tmp_path):
    root = _tree(tmp_path)
    assert _gate(root).returncode == 0
    assert _apply(root, "double-context", "words").returncode == 0
    assert _gate(root).returncode == 1


def test_each_mutation_touches_only_named_span(tmp_path):
    mod = _mut()
    for regression, forms in mod.MUTATORS.items():
        for form in forms:
            root = _tree(tmp_path / f"{regression}-{form}")
            before = {p: p.read_bytes() for p in (root / "skills").rglob("*") if p.is_file()}
            before.update({p: p.read_bytes() for p in (root / "evals").rglob("*") if p.is_file()})
            changed = json.loads(json.dumps(mod.apply(regression, form, root)))
            assert len(changed) == 1
            assert [p for p, b in before.items() if p.read_bytes() != b] == [root / changed[0]]


def test_mutated_files_still_parse(tmp_path):
    mod = _mut()
    for regression, forms in mod.MUTATORS.items():
        for form in forms:
            root = _tree(tmp_path / f"{regression}-{form}")
            for rel in mod.apply(regression, form, root):
                if rel.endswith(".py"):
                    ast.parse((root / rel).read_text())


def test_missing_anchor_exits_10_with_kind_and_candidates(tmp_path):
    root = _tree(tmp_path)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    p = root / WORK
    p.write_text(p.read_text().replace("def _row_hits(", "def _row_hitz("))
    res = _apply(root, "disable-secret-scan", "commit-noop")
    assert res.returncode == 10
    assert "anchor-missing" in res.stderr and "_row_hits" in res.stderr and "candidates:" in res.stderr


def test_ambiguous_anchor_exits_11(tmp_path):
    root = _tree(tmp_path)
    p = root / WORK
    p.write_text(p.read_text() + "\n\ndef _plan_review_refusal():\n    return 'x'\n")
    res = _apply(root, "skip-plan-review", "code")
    assert res.returncode == 11 and "anchor-ambiguous" in res.stderr


def test_noop_mutation_exits_12(tmp_path):
    root = _tree(tmp_path)
    assert _apply(root, "skip-plan-review", "code").returncode == 0
    res = _apply(root, "skip-plan-review", "code")
    assert res.returncode == 12 and "mutation-noop" in res.stderr


def test_anchor_guard_fails_rather_than_skips_on_rename(tmp_path, monkeypatch):
    mod = _mut()
    tree = ast.parse((ROOT / WORK).read_text().replace("def _row_hits(", "def _row_hitz("))
    with pytest.raises(mod.MutatorError) as exc:
        mod._find(tree, "_row_hits", WORK)
    assert exc.value.code == 10


def test_anchors_lists_every_anchor():
    res = _run("anchors")
    assert res.returncode == 0
    mod = _mut()
    assert len(res.stdout.splitlines()) == sum(len(f) for f in mod.MUTATORS.values())
    for needle in ("_plan_review_refusal", "_review_post_request", "SHAPE_RULES#huggingface-token",
                   "_SECRET_PATTERN_SPECS#authorization-header", "_row_hits", "double-context"):
        assert needle in res.stdout


def test_unknown_regression_or_form_exits_2(tmp_path):
    assert _run("apply", "nope", "--form", "code", "--root", str(tmp_path)).returncode == 2
    assert _run("apply", "skip-plan-review", "--form", "nope", "--root", str(tmp_path)).returncode == 2


def test_help_works():
    assert _run("--help").returncode == 0 and _run("apply", "--help").returncode == 0


def test_real_tree_is_never_mutated(tmp_path):
    files = [ROOT / WORK, ROOT / SCRUB, ROOT / "evals/phase_context_budget.py"]
    before = [f.read_bytes() for f in files]
    root = _tree(tmp_path)
    for r, f in (("skip-plan-review", "code"), ("disable-secret-scan", "whole-table"), ("double-context", "words")):
        assert _apply(root, r, f).returncode == 0
    assert before == [f.read_bytes() for f in files]


def test_new_files_pass_commit_secret_hits():
    spec = importlib.util.spec_from_file_location("scrub_t", ROOT / SCRUB)
    scrub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scrub)
    for f in (SCRIPT, Path(__file__)):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            assert not scrub.commit_secret_hits(line), (f.name, n)


def test_new_files_clean_under_leak_scan():
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "leak_scan.py")], cwd=str(ROOT),
                         capture_output=True, text=True)
    out = res.stdout + res.stderr
    for name in ("mutators.py", "test_regression_mutators.py"):
        assert name not in out


def test_write_surface_row_present():
    inv = json.loads((ROOT / "docs" / "launch" / "write-surface.json").read_text())["entries"]
    assert any(e["path"] == "evals/regression/mutators.py" and e["function"] == "_write_text" for e in inv)
