"""`architecture_doc.py`: the architecture document linter, its `architecture.system_root` resolver,
the standard page and the config key (#1783, R-03 slice 1).

Every lint-behaviour test runs the DOCUMENTED gesture as a subprocess, `python3
skills/sigma-init/scripts/architecture_doc.py lint <dir>` with no extra flag, from a fresh empty
working directory (so no real `.sdlc/config.json` leaks in) and a clean environment. Config cases
build a throwaway repository with its own `.sdlc/config.json` and run the gesture from inside it,
because the gesture has no flag that could point elsewhere.

`test_each_clause_broken_alone_goes_red_through_documented_gesture` is the control (AGENTS: "run the
control"): every clause and sub-clause of the linter is broken alone by an AST mutation of the real
script, and the documented gesture is seen to change verdict on an input chosen so that only that
clause decides it. `test_mutation_harness_identity_mutant_changes_nothing` guards the harness.

Rules this file keeps (the recorded red/green proof depends on them): every parametrize table is a
literal here, so the collected node ids are the same whether or not the product exists; nothing
prints; nothing skips; a missing product file is an ASSERTION failure, never an import error.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "sigma-init" / "scripts" / "architecture_doc.py"
FIXTURES = ROOT / "tests" / "fixtures" / "architecture_doc"
DOC = ROOT / "docs" / "architecture-doc.md"
TEMPLATE = ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl"
CONFIG_DOC = ROOT / "docs" / "configuration.md"
GESTURE_LINE = "python3 skills/sigma-init/scripts/architecture_doc.py lint <dir>"
FINDING = re.compile(r"^\S.*:\d+: \[[a-z-]+\] ", re.M)
CODE = re.compile(r":\d+: \[([a-z-]+)\] ")
SYSTEM_PYTHON = "/usr/bin/python3"
HEADINGS = ("Context and scope", "Constraints", "Solution strategy", "Building blocks", "Runtime view",
            "Deployment", "Cross-cutting concepts", "Decisions", "Quality requirements",
            "Risks and technical debt", "Technology inventory")


# ------------------------------------------------------------------------------------------ helpers

def load_module():
    """The real module, imported by path, or None while it does not exist (an assertion red)."""
    if not SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location("architecture_doc_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh(base, name):
    """A new, empty directory under `base` (tmp_path), unique per call."""
    n = 0
    while (base / f"{name}-{n}").exists():
        n += 1
    path = base / f"{name}-{n}"
    path.mkdir(parents=True)
    return path


def _env(home):
    return {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(argv, base, cwd):
    """(rc, out, err); a run that overruns the 10 s timeout is killed and returns rc None, so a hang
    is an ASSERTION failure in the caller, never an uncaught TimeoutExpired."""
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=_env(_fresh(base, "home")), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return None, "", "killed: over the 10 s timeout"
    return proc.returncode, proc.stdout, proc.stderr


def run_script(base, args, cwd=None, script=SCRIPT, python=None):
    """Run the script with `args` from `cwd` (default: a fresh empty dir); returns (rc, out, err)."""
    assert Path(script).exists(), "architecture_doc.py missing"
    cwd = cwd if cwd is not None else _fresh(base, "cwd")
    return _run([python or sys.executable, str(script)] + [str(a) for a in args], base, cwd)


def run_gesture(base, target, cwd=None, script=SCRIPT, python=None):
    """The documented gesture, `lint <dir>`, with no flag ever added."""
    return run_script(base, ["lint", target], cwd=cwd, script=script, python=python)


def _fenced_lines(text):
    out, fence = [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            fence = None if fence else s
            continue
        if fence:
            out.append(s)
    return out


def documented_gesture(target, script=SCRIPT):
    """The lint gesture copied out of docs/architecture-doc.md: `python3` becomes this interpreter,
    the script path may be swapped (the mutation control), `<dir>` becomes `target`."""
    assert DOC.exists(), "docs/architecture-doc.md missing"
    assert GESTURE_LINE in _fenced_lines(DOC.read_text(encoding="utf-8")), "gesture not documented"
    assert Path(script).exists(), "architecture_doc.py missing"
    argv = GESTURE_LINE.split()
    assert argv[0] == "python3" and argv[1].endswith("architecture_doc.py") and argv[3] == "<dir>"
    return [sys.executable, str(script), argv[2], str(target)]


def run_documented(base, target, cwd=None, script=SCRIPT):
    argv = documented_gesture(target, script)
    return _run(argv, base, cwd if cwd is not None else _fresh(base, "cwd"))


def codes(out):
    return set(CODE.findall(out))


def fixture(name):
    path = FIXTURES / name / "architecture.md"
    assert path.exists(), f"{path} missing"
    return path.parent


def copy_fixture(base, name, text_edit=None):
    src = fixture(name) / "architecture.md"
    dst = _fresh(base, name)
    text = src.read_text(encoding="utf-8")
    (dst / "architecture.md").write_text(text_edit(text) if text_edit else text, encoding="utf-8")
    return dst


def write_doc(base, text, name="doc", newline=None):
    dst = _fresh(base, name)
    with open(dst / "architecture.md", "w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)
    return dst


def make_repo(base, config):
    """A throwaway repository: `.sdlc/config.json` holding `config` (an object, or raw text when a
    str), and `target/` = a copy of the `clean` fixture."""
    repo = _fresh(base, "repo")
    (repo / ".sdlc").mkdir()
    raw = config if isinstance(config, str) else json.dumps(config)
    (repo / ".sdlc" / "config.json").write_text(raw, encoding="utf-8")
    (repo / "target").mkdir()
    shutil.copyfile(fixture("clean") / "architecture.md", repo / "target" / "architecture.md")
    return repo


def check(result, expect):
    """`expect` is (rc, stdout_must_contain, stdout_must_not_contain, stderr_must_contain), trailing
    parts optional. Every exit-0 expectation also demands stdout holds no finding line."""
    rc, out, err = result
    want = tuple(expect) + ((), (), ())[len(expect) - 1:]
    assert rc == want[0], (rc, out, err)
    for s in want[1]:
        assert s in out, (s, out, err)
    for s in want[2]:
        assert s not in out, (s, out, err)
    for s in want[3]:
        assert s in err, (s, out, err)
    if want[0] == 0:
        assert not FINDING.search(out), out


# ------------------------------------------------------------------------------------- CLI surface

def test_help_and_usage_exit_codes(tmp_path):
    for flag in ("-h", "--help"):
        cwd = _fresh(tmp_path, "cwd")
        rc, out, err = run_script(tmp_path, [flag], cwd=cwd)
        assert rc == 0 and "usage" in out, (rc, out, err)
        assert list(cwd.iterdir()) == [], "help wrote a file"
    rc, out, err = run_script(tmp_path, [])
    assert rc == 2 and "usage" in err, (rc, out, err)
    rc, out, err = run_script(tmp_path, ["frobnicate"])
    assert rc == 2 and "usage" in err, (rc, out, err)
    rc, out, err = run_script(tmp_path, ["lint", str(fixture("clean")), str(fixture("clean"))])
    assert rc == 2 and "usage" in err, (rc, out, err)
    rc, out, err = run_script(tmp_path, ["lint", "--no-such-flag"])
    assert rc == 2 and "usage" in err, (rc, out, err)


def test_headings_verb_prints_the_eleven(tmp_path):
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"
    rc, out, err = run_script(tmp_path, ["headings"])
    assert rc == 0, (rc, out, err)
    assert tuple(out.splitlines()) == HEADINGS
    assert tuple(mod.REQUIRED_HEADINGS) == HEADINGS


# ------------------------------------------------------------------------------ behaviour (gesture)

def test_clean_fixture_lints_clean_via_documented_gesture(tmp_path):
    for name in ("clean", "person_exempt"):
        check(run_documented(tmp_path, fixture(name)), (0,))
    # P-3: the template's visible default counts as unset, so its absent folder is not a finding
    repo = make_repo(tmp_path, {"architecture": {"system_root": "docs/architecture"}})
    rc, out, err = run_documented(tmp_path, repo / "target", cwd=repo)
    check((rc, out, err), (0, (), ("[system-root]",)))


def test_planted_container_without_inventory_row_is_red_via_documented_gesture(tmp_path):
    check(run_documented(tmp_path, fixture("planted_missing_row")),
          (1, ("[container-no-inventory]", "Billing API")))
    fixed = copy_fixture(tmp_path, "planted_missing_row",
                         lambda t: t.replace("| Web UI | TypeScript |", "| Web UI | TypeScript |\n| Billing API | Python |"))
    check(run_documented(tmp_path, fixed), (0,))


def test_missing_heading_and_unparseable_block_named_with_exit_1(tmp_path):
    check(run_gesture(tmp_path, fixture("missing_heading")),
          (1, ("[missing-heading]", "Risks and technical debt")))
    text = (fixture("unbalanced_bracket") / "architecture.md").read_text(encoding="utf-8").splitlines()
    bad = next(i for i, line in enumerate(text, 1) if "see [docs" in line)
    opened = max(i for i, line in enumerate(text[:bad], 1) if line.strip() == "```mermaid")
    rc, out, err = run_gesture(tmp_path, fixture("unbalanced_bracket"))
    check((rc, out, err), (1, ("[unbalanced-bracket]", f":{bad}: ", f"line {opened}")))


@pytest.mark.parametrize("name,code", [
    ("clean", None),
    ("person_exempt", None),
    ("last_bracket", None),
    ("subgraph_title", None),
    ("planted_missing_row", "container-no-inventory"),
    ("substring_row", "container-no-inventory"),
    ("edge_line_container", "container-no-inventory"),
    ("second_container_block", "container-no-inventory"),
    ("bad_keyword_c4", "unsupported-keyword"),
    ("no_keyword", "no-keyword"),
    ("unknown_keyword", "no-keyword"),
    ("unbalanced_bracket", "unbalanced-bracket"),
    ("unbalanced_bracket_quoted", "unbalanced-bracket"),
    ("unbalanced_round", "unbalanced-bracket"),
    ("unbalanced_curly", "unbalanced-bracket"),
    ("unbalanced_quote", "unbalanced-bracket"),
    ("unbalanced_subgraph", "unbalanced-subgraph"),
    ("stray_end", "unbalanced-subgraph"),
    ("missing_heading", "missing-heading"),
    ("fenced_heading", "missing-heading"),
    ("duplicate_heading", "duplicate-heading"),
    ("no_context_diagram", "missing-diagram"),
    ("no_container_diagram", "missing-diagram"),
    ("node_no_technology", "node-no-technology"),
])
def test_each_clause_fixture_fails_only_its_own_clause(tmp_path, name, code):
    rc, out, err = run_gesture(tmp_path, fixture(name))
    assert codes(out) == ({code} if code else set()), (out, err)
    assert rc == (1 if code else 0), (rc, out, err)


# ------------------------------------------------------------------------------ the mutation control
#
# (slug, function, mutant, input, real expectation, mutant expectation). A mutant is a body string
# (the function's body is replaced, signature kept) or ("wrap", body) (the original is renamed
# `<name>__real` and a new function of the original name and arguments, with this body, is added).
# An input is ("fixture", slug), ("line_cap", None), ("edit", key) for `clean` with the EDITS[key]
# replacements, or ("root", kind) for a throwaway repository.

CONTROL_ROWS = [
    ("inventory_lookup", "_check_inventory", "return []", ("fixture", "planted_missing_row"),
     (1, ("[container-no-inventory]", "Billing API")), (0,)),
    ("exact_match", "_name_matches",
     "return any(name.strip().casefold() in r for r in inventory_names)", ("fixture", "substring_row"),
     (1, ("[container-no-inventory]", "Billing API")), (0,)),
    ("person_exempt", "_is_exempt", "return False", ("fixture", "person_exempt"),
     (0,), (1, ("[container-no-inventory]", "Customer", "Payments"))),
    ("last_bracket", "_extract_label_parts",
     'i = label.find("[")\nif i < 0:\n    return (label.strip(), None)\n'
     'return (label[:i].strip(), label[i + 1:].split("]")[0].strip())',
     ("fixture", "last_bracket"), (0,), (1, ("[container-no-inventory]", "Cache"))),
    ("edge_line", "_scan_nodes", ("wrap", "return _scan_nodes__real(line)[:1]"),
     ("fixture", "edge_line_container"), (1, ("Billing API",)), (0,)),
    ("subgraph_title", "_is_excluded_line", "return False", ("fixture", "subgraph_title"),
     (0,), (1, ("[container-no-inventory]", "Platform"))),
    ("c4_keyword", "_is_c4_keyword", "return False", ("fixture", "bad_keyword_c4"),
     (1, ("[unsupported-keyword]",)), (1, ("[no-keyword]",), ("[unsupported-keyword]",))),
    ("known_keyword", "_is_known_keyword", "return True", ("fixture", "unknown_keyword"),
     (1, ("[no-keyword]",)), (0,)),
    ("square_balance", "_square_ok", "return True", ("fixture", "unbalanced_bracket"),
     (1, ("[unbalanced-bracket]",)), (0,)),
    ("quoted_brackets", "_square_ok", ("wrap", "return _square_ok__real(re.sub('\"[^\"]*\"', '', line))"),
     ("fixture", "unbalanced_bracket_quoted"), (1, ("[unbalanced-bracket]",)), (0,)),
    ("round_balance", "_round_ok", "return True", ("fixture", "unbalanced_round"),
     (1, ("[unbalanced-bracket]",)), (0,)),
    ("curly_balance", "_curly_ok", "return True", ("fixture", "unbalanced_curly"),
     (1, ("[unbalanced-bracket]",)), (0,)),
    ("quote_parity", "_quote_ok", "return True", ("fixture", "unbalanced_quote"),
     (1, ("[unbalanced-bracket]",)), (0,)),
    ("stray_end", "_stray_end", "return False", ("fixture", "stray_end"),
     (1, ("[unbalanced-subgraph]",)), (0,)),
    ("unclosed_subgraph", "_unclosed_subgraph", "return False", ("fixture", "unbalanced_subgraph"),
     (1, ("[unbalanced-subgraph]",)), (0,)),
    ("required_heading", "_check_headings", "return []", ("fixture", "missing_heading"),
     (1, ("[missing-heading]",)), (0,)),
    ("duplicate_heading", "_check_duplicate_headings", "return []", ("fixture", "duplicate_heading"),
     (1, ("[duplicate-heading]",)), (0,)),
    ("context_diagram", "_has_context_diagram", "return True", ("fixture", "no_context_diagram"),
     (1, ("[missing-diagram]",)), (0,)),
    ("container_diagram", "_has_container_diagram", "return True", ("fixture", "no_container_diagram"),
     (1, ("[missing-diagram]",)), (0,)),
    ("node_technology", "_check_node_technology", "return []", ("fixture", "node_no_technology"),
     (1, ("[node-no-technology]",)), (0,)),
    ("line_cap", "_check_line_length", "return []", ("line_cap", None),
     (1, ("[line-too-long]",)), (0,)),
    ("root_invalid", "_root_invalid", "return False", ("root", "dotdot_x"),
     (1, ("[system-root]", "architecture.system_root")), (0,)),
    ("root_missing", "_root_missing", "return False", ("root", "nope"),
     (1, ("[system-root]", "architecture.system_root")), (0,)),
    ("key_explicit_presence", "_key_explicit", 'return block.get("system_root") != DEFAULT_SYSTEM_ROOT',
     ("root", "empty_config"), (0,), (1, ("[system-root]", "architecture.system_root"))),
    ("key_explicit_default", "_key_explicit", 'return block.get("system_root") is not None',
     ("root", "default_value"), (0,), (1, ("[system-root]", "architecture.system_root"))),
    ("arch_not_object", "_architecture_block",
     'a = config.get("architecture")\nreturn ((a if isinstance(a, dict) else {}), None)',
     ("root", "arch_string"), (1, ("[system-root]", "architecture.system_root")), (0,)),
    ("refuse_nonstring", "_refuse_nonstring", "return None", ("root", "int5"),
     (1, ("[system-root]",)), (1, (), ("[system-root]",), ("Traceback",))),
    ("refuse_absolute", "_refuse_absolute", "return None", ("root", "absolute"),
     (1, ("[system-root]",)), (0,)),
    ("refuse_dotdot", "_refuse_dotdot", "return None", ("root", "sub_dotdot"),
     (1, ("[system-root]",)), (0,)),
    ("refuse_drive_or_unc", "_refuse_drive_or_unc", "return None", ("root", "drive_dir"),
     (1, ("[system-root]",)), (0,)),
    ("refuse_symlink_escape", "_refuse_symlink_escape", "return None", ("root", "symlink_default"),
     (1, ("[system-root]",)), (0,)),
    ("refuse_url_shape", "_refuse_url_shape", "return None", ("root", "bad_url_space"),
     (1, ("[system-root]",)), (0, ("existence not checked (no network)",))),
    # each keyword of the brace exemption dropped alone
    ("brace_er", "_counts_braces", 'return _first_token(block) not in ("classDiagram", "stateDiagram-v2")',
     ("edit", "er_braces"), (0,), (1, ("[unbalanced-bracket]",))),
    ("brace_class", "_counts_braces", 'return _first_token(block) not in ("erDiagram", "stateDiagram-v2")',
     ("edit", "class_braces"), (0,), (1, ("[unbalanced-bracket]",))),
    ("brace_state", "_counts_braces", 'return _first_token(block) not in ("erDiagram", "classDiagram")',
     ("edit", "state_braces"), (0,), (1, ("[unbalanced-bracket]",))),
    ("sequence_end", "_checks_subgraphs", "return True", ("edit", "sequence_loop"),
     (0,), (1, ("[unbalanced-subgraph]",))),
    ("first_table_only", "_first_table", 'return [l.strip() for l in lines if l.strip().startswith("|")]',
     ("edit", "second_table"), (1, ("[container-no-inventory]", "Billing API")), (0,)),
    ("header_skip", "_body_rows", "return table", ("edit", "header_named"),
     (1, ("[container-no-inventory]", "'Container'")), (0,)),
    ("delimiter_detect", "_is_delimiter", "return True", ("edit", "no_delimiter"),
     (0,), (1, ("[container-no-inventory]", "Web UI"))),
    ("cell_backticks", "_unwrap_code", "return cell", ("edit", "backtick_cells"),
     (0,), (1, ("[container-no-inventory]", "Web UI"))),
    ("comment_balance", "_skip_in_balance", "return False", ("edit", "comment_bracket"),
     (0,), (1, ("[unbalanced-bracket]",))),
    ("comment_scan", "_skip_in_scan", "return False", ("edit", "comment_node"),
     (0,), (1, ("[container-no-inventory]", "Legacy"))),
    ("excluded_click", "_is_excluded_line",
     'return _first_word(line) in ("subgraph", "end", "style", "classDef", "class", "linkStyle", "direction")',
     ("edit", "styling"), (0,), (1, ("[node-no-technology]", "'x'"))),
    ("excluded_classdef", "_is_excluded_line",
     'return _first_word(line) in ("subgraph", "end", "click", "style", "class", "linkStyle", "direction")',
     ("edit", "styling"), (0,), (1, ("[node-no-technology]", "'1,2,3'"))),
    ("excluded_style", "_is_excluded_line",
     'return _first_word(line) in ("subgraph", "end", "click", "classDef", "class", "linkStyle", "direction")',
     ("edit", "styling"), (0,), (1, ("[node-no-technology]", "'4,5,6'"))),
    ("excluded_linkstyle", "_is_excluded_line",
     'return _first_word(line) in ("subgraph", "end", "click", "style", "classDef", "class", "direction")',
     ("edit", "styling"), (0,), (1, ("[node-no-technology]", "'7,8,9'"))),
    ("edge_text", "_strip_edge_text", "return line", ("edit", "edge_text"),
     (0,), (1, ("[node-no-technology]", "'fast'"))),
    ("fence_char", "_closes", "return len(run) >= fence[1] and line.strip() == run", ("edit", "fence_char"),
     (0,), (1, ("[duplicate-heading]",))),
    ("fence_length", "_closes", "return run[0] == fence[0] and line.strip() == run", ("edit", "fence_length"),
     (0,), (1, ("[duplicate-heading]",))),
    ("fence_bare", "_closes", "return run[0] == fence[0] and len(run) >= fence[1]", ("edit", "fence_info"),
     (0,), (1, ("[duplicate-heading]",))),
    ("dedupe", "_unique_nodes", "return found", ("edit", "redefined_node"),
     (0,), (1, ("[container-no-inventory]", "Web UI v2"))),
    ("unclosed_fence", "_check_unclosed_fence", "return []", ("edit", "unclosed_fence"),
     (1, ("[unclosed-fence]",)), (0,)),
    ("br_tags", "_strip_line_breaks", "return label", ("edit", "br_label"),
     (0,), (1, ("[container-no-inventory]", "Web UI<br/>"))),
    # code review 2: each URL shape check alone, on an input only that check rejects
    ("url_cap", "_url_over_cap", "return False", ("root", "url_long"),
     (1, ("[system-root]", "over 2048 characters")), (0, ("existence not checked (no network)",))),
    ("url_ws", "_url_has_blank", "return _has_control(value)", ("root", "url_space"),
     (1, ("[system-root]", "whitespace")), (0, ("existence not checked (no network)",))),
    ("url_control", "_url_has_blank", "return any((ch.isspace() for ch in value))", ("root", "url_ctrl"),
     (1, ("[system-root]", "control character")), (0, ("existence not checked (no network)",))),
    ("url_host", "_url_host_ok", "return True", ("root", "url_badhost"),
     (1, ("[system-root]", "needs a host")), (0, ("existence not checked (no network)",))),
    ("url_path", "_url_path_ok", "return True", ("root", "url_nopath"),
     (1, ("[system-root]", "repository path")), (0, ("existence not checked (no network)",))),
    # a control character in a folder value, and each half of the control-character test
    ("refuse_control", "_refuse_control", "return None", ("root", "newline_folder"),
     (1, ("[system-root]", "control character")), (1, ("does not exist",), ("control character",))),
    ("control_c0", "_has_control", "return any((ord(ch) == 127 for ch in value))", ("root", "newline_folder"),
     (1, ("[system-root]", "control character")), (1, ("does not exist",), ("control character",))),
    ("control_del", "_has_control", "return any((ord(ch) < 32 for ch in value))", ("root", "del_folder"),
     (1, ("[system-root]", "control character")), (1, ("does not exist",), ("control character",))),
    ("config_cap", "_over_read_cap", "return False", ("root", "big_config"),
     (0, (), (), ("read cap",)), (0, (), (), ("invalid JSON",))),
    ("not_a_directory", "_not_a_directory", "return False", ("file_target", None),
     (1, ("[missing-file]", "not a directory")), (1, ("does not exist",), ("not a directory",))),
    ("heading_hashes", "_heading_text", "return raw", ("edit", "closing_hashes"),
     (0,), (1, ("[missing-heading]", "Constraints"))),
    ("unclosed_block_checked", "_keep_unclosed", "return False", ("edit", "unclosed_bad_block"),
     (1, ("[unclosed-fence]", "[no-keyword]")), (1, ("[unclosed-fence]",), ("[no-keyword]",))),
    ("node_trailing_dash", "_node_id",
     'cut = run.rfind("--")\nif cut >= 0:\n    run = run[cut + 2:]\nfor i, ch in enumerate(run):\n'
     '    if ch == "_" or ch.isalpha():\n        return run[i:]\nreturn None',
     ("edit", "dash_run"), (0,), (1, ("[container-no-inventory]", "Ghost"))),
    ("slant_label", "_skip_slant", "return i", ("edit", "slant_label"),
     (0,), (1, ("[container-no-inventory]", "'\"Queue'"))),
    ("exempt_case", "_is_exempt", 'return technology in ("Person", "External system")', ("edit", "exempt_case"),
     (0,), (1, ("[container-no-inventory]", "Customer", "Payments"))),
    ("exempt_person", "_is_exempt", 'return technology.casefold() in ("external system",)',
     ("fixture", "person_exempt"), (0,), (1, ("Customer",), ("Payments",))),
    ("exempt_external", "_is_exempt", 'return technology.casefold() in ("person",)',
     ("fixture", "person_exempt"), (0,), (1, ("Payments",), ("Customer",))),
    ("cell_bold", "_unwrap_bold", "return cell", ("edit", "bold_cells"),
     (0,), (1, ("[container-no-inventory]", "Web UI"))),
    ("cell_link", "_unwrap_link", "return cell", ("edit", "link_cells"),
     (0,), (1, ("[container-no-inventory]", "Web UI"))),
]

# ("edit", key) control inputs: the `clean` fixture with these literal (old, new) replacements, each
# old string present exactly once. PARA sits under Runtime view; NODE is the container diagram.
PARA = "How a request flows.\n"
NODE = "    user --> web\n"
INVENTORY = "| Billing API | Python |\n"
EDITS = {
    "er_braces": [(PARA, PARA + "\n```mermaid\nerDiagram\n    CUSTOMER ||--o{ ORDER : places\n"
                   "    CUSTOMER {\n        string name\n    }\n```\n")],
    "class_braces": [(PARA, PARA + "\n```mermaid\nclassDiagram\n    class Invoice {\n        +total\n    }\n```\n")],
    "state_braces": [(PARA, PARA + "\n```mermaid\nstateDiagram-v2\n    state Billing {\n"
                      "        [*] --> Open\n    }\n```\n")],
    "sequence_loop": [(PARA, PARA + "\n```mermaid\nsequenceDiagram\n    loop Every minute\n"
                       "        A->>B: ping\n    end\n```\n")],
    "second_table": [(INVENTORY, ""),
                     ("| Web UI | TypeScript |\n", "| Web UI | TypeScript |\n\nRetired containers.\n\n"
                      "| Container | Technology |\n| --- | --- |\n" + INVENTORY)],
    "header_named": [(NODE, '    ct["Container [Go]"]\n' + NODE)],
    "no_delimiter": [("| --- | --- |\n", "")],
    "backtick_cells": [("| Web UI | TypeScript |", "| `Web UI` | TypeScript |")],
    "comment_bracket": [(NODE, "    %% see [docs\n" + NODE)],
    "comment_node": [(NODE, '    %% old["Legacy [Perl]"]\n' + NODE)],
    "styling": [(NODE, NODE + '    click api call callback("x")\n    classDef big fill:rgb(1,2,3)\n'
                 "    style web fill:rgb(4,5,6)\n    linkStyle 0 stroke:rgb(7,8,9)\n")],
    "edge_text": [(NODE, "    user -->|uses cache(fast)| web\n")],
    "fence_char": [(PARA, PARA + "\n~~~text\n```\n## Constraints\n~~~\n")],
    "fence_length": [(PARA, PARA + "\n````text\n```\n## Constraints\n````\n")],
    "fence_info": [(PARA, PARA + "\n```text\n```js\n## Constraints\n```\n")],
    "redefined_node": [(NODE, NODE + '    web["Web UI v2 [TypeScript]"]\n')],
    "unclosed_fence": [(INVENTORY, INVENTORY + "\n```mermaid\nflowchart LR\n    A --> B\n")],
    "br_label": [('web["Web UI [TypeScript]"]', 'web["Web UI<br/>[TypeScript]"]')],
    "closing_hashes": [("## Constraints\n", "## Constraints ##\n")],
    "unclosed_bad_block": [(INVENTORY, INVENTORY + "\n```mermaid\nnotakeyword\n")],
    "dash_run": [(NODE, NODE + '    x-["Ghost [Go]"]\n')],
    "slant_label": [(NODE, NODE + '    q[/"Queue [Kafka]"/]\n'), (INVENTORY, INVENTORY + "| Queue | Kafka |\n")],
    "exempt_case": [('    user["Customer [Person]"]\n    ext["Payments [External system]"]\n    web[',
                     '    user["Customer [PERSON]"]\n    ext["Payments [external SYSTEM]"]\n    web[')],
    "bold_cells": [("| Web UI | TypeScript |", "| **Web UI** | TypeScript |")],
    "link_cells": [("| Web UI | TypeScript |", "| [Web UI](web.md) | TypeScript |")],
}


def edited_clean(key):
    text = (fixture("clean") / "architecture.md").read_text(encoding="utf-8")
    for old, new in EDITS[key]:
        assert text.count(old) == 1, (key, old)
        text = text.replace(old, new)
    return text


def build_input(base, spec):
    """(target_dir, cwd) for a control input. cwd None means a fresh empty directory."""
    kind, arg = spec
    if kind == "fixture":
        return fixture(arg), None
    if kind == "line_cap":
        text = (fixture("clean") / "architecture.md").read_text(encoding="utf-8")
        return write_doc(base, text.rstrip("\n") + "\n\n" + "x" * 4097 + "\n", "line-cap"), None
    if kind == "edit":
        return write_doc(base, edited_clean(arg), arg), None
    if kind == "file_target":       # `lint path/to/architecture.md`: a file where a folder belongs
        return fixture("clean") / "architecture.md", None
    assert kind == "root", spec
    value = {"dotdot_x": "../x", "nope": "docs/nope", "default_value": "docs/architecture",
             "int5": 5, "sub_dotdot": "sub/../docs/architecture", "drive_dir": "C:\\x",
             "symlink_default": "docs/architecture",
             "bad_url_space": "https" + "://" + "bad host/x",
             "url_long": "https" + "://" + "example.invalid/" + "r" * 2025,  # 2,049 characters
             "url_space": "https" + "://" + "example.invalid/a b",
             "url_ctrl": "https" + "://" + "example.invalid/a\x01b",
             "url_badhost": "https" + "://" + "exa_mple.invalid/x",
             "url_nopath": "https" + "://" + "example.invalid/",
             "newline_folder": "docs/a\nb", "del_folder": "docs/a\x7fb"}.get(arg)
    config = {"architecture": {"system_root": value}}
    if arg == "empty_config":
        config = {}
    elif arg == "big_config":       # a valid object 100 bytes over the read cap: a capped read cuts it
        config = '{"pad": "' + "x" * (1024 * 1024 + 89) + '"}'
        assert len(config) == 1024 * 1024 + 100
    elif arg == "arch_string":
        config = {"architecture": "docs/architecture"}
    repo = make_repo(base, config)
    if arg == "absolute":
        (repo / "docs" / "architecture").mkdir(parents=True)
        absolute = os.path.realpath(str(repo)) + "/docs/architecture"
        (repo / ".sdlc" / "config.json").write_text(json.dumps({"architecture": {"system_root": absolute}}),
                                                     encoding="utf-8")
    elif arg == "sub_dotdot":
        (repo / "sub").mkdir()
        (repo / "docs" / "architecture").mkdir(parents=True)
    elif arg == "drive_dir":
        (repo / "C:\\x").mkdir()
    elif arg == "symlink_default":
        outside = _fresh(base, "outside")
        (repo / "docs").mkdir()
        os.symlink(str(outside), str(repo / "docs" / "architecture"))
    return repo / "target", repo


def _function(tree, name):
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(found) == 1, f"{name}: expected exactly one module-level function, found {len(found)}"
    return found[0]


def mutate(src, func_name, mutant):
    """(mutant source, the replaced-or-inserted function node, the original function node)."""
    tree = ast.parse(src)
    original = copy.deepcopy(_function(tree, func_name))
    fn = _function(tree, func_name)
    if isinstance(mutant, tuple):
        assert mutant[0] == "wrap", mutant
        real_name = func_name + "__real"
        assert not [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == real_name]
        new = copy.deepcopy(fn)
        new.body = ast.parse(mutant[1]).body
        new.decorator_list = []
        fn.name = real_name
        tree.body.insert(tree.body.index(fn) + 1, new)
        target = new
    else:
        fn.body = ast.parse(mutant).body
        target = fn
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), target, original


def write_mutant(base, source):
    path = _fresh(base, "mutant") / "architecture_doc.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.mark.parametrize("slug,func,mutant,spec,real_expect,mutant_expect", CONTROL_ROWS,
                         ids=[row[0] for row in CONTROL_ROWS])
def test_each_clause_broken_alone_goes_red_through_documented_gesture(
        tmp_path, slug, func, mutant, spec, real_expect, mutant_expect):
    assert SCRIPT.exists(), "architecture_doc.py missing"
    target, cwd = build_input(tmp_path, spec)
    check(run_documented(tmp_path, target, cwd=cwd), real_expect)
    source, _, _ = mutate(SCRIPT.read_text(encoding="utf-8"), func, mutant)
    check(run_documented(tmp_path, target, cwd=cwd, script=write_mutant(tmp_path, source)), mutant_expect)


def test_mutation_harness_identity_mutant_changes_nothing(tmp_path):
    assert SCRIPT.exists(), "architecture_doc.py missing"
    src = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for slug, func, mutant, spec, _real, _mut in CONTROL_ROWS:
        original = _function(tree, func)                                   # (c) exactly once
        # (b) the mutant compiles, ends in a return, and is not a no-op
        source, target, before = mutate(src, func, mutant)
        compile(source, f"<mutant {slug}>", "exec")
        assert isinstance(target.body[-1], ast.Return), slug
        assert ast.dump(target) != ast.dump(before), slug
        # (a) re-inserting the function's own body changes nothing, on this row's input and on clean
        identity, _, _ = mutate(src, func, "\n".join(ast.unparse(s) for s in original.body))
        ident_script = write_mutant(tmp_path, identity)
        for case in (spec, ("fixture", "clean")):
            target_dir, cwd = build_input(tmp_path, case)
            real = run_documented(tmp_path, target_dir, cwd=cwd)
            same = run_documented(tmp_path, target_dir, cwd=cwd, script=ident_script)
            assert real == same, (slug, case, real, same)


# --------------------------------------------------------------------------------- system root

@pytest.mark.parametrize("config", [
    pytest.param({"architecture": {"system_root": "/" + "abs" + "/x"}}, id="abs"),
    pytest.param({"architecture": {"system_root": "docs/../../x"}}, id="dotdot"),
    pytest.param({"architecture": {"system_root": "C:\\x"}}, id="drive"),
    pytest.param({"architecture": {"system_root": "\\\\h\\s"}}, id="unc"),
    pytest.param({"architecture": {"system_root": ""}}, id="empty"),
    pytest.param({"architecture": {"system_root": 5}}, id="int"),
    pytest.param({"architecture": {"system_root": ["a"]}}, id="list"),
    pytest.param({"architecture": {"system_root": {}}}, id="dict"),
    pytest.param({"architecture": "docs/architecture"}, id="arch_not_object"),
    pytest.param({"architecture": {"system_root": "link"}}, id="symlink_escape"),
    pytest.param({"architecture": {"system_root": "docs/nope"}}, id="missing_folder"),
    pytest.param({"architecture": {"system_root": "https" + "://"}}, id="bad_url"),
])
def test_system_root_bad_values_name_the_key(tmp_path, config):
    repo = make_repo(tmp_path, config)
    if config.get("architecture") == {"system_root": "link"}:
        os.symlink(str(_fresh(tmp_path, "outside")), str(repo / "link"))
    rc, out, err = run_gesture(tmp_path, repo / "target", cwd=repo)
    assert rc == 1, (rc, out, err)
    assert "[system-root]" in out and "architecture.system_root" in out, (out, err)


def test_resolve_system_root_return_shape(tmp_path):
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"
    assert mod.Resolved._fields == ("form", "value", "path", "error", "note")
    repo = make_repo(tmp_path, {"architecture": {"system_root": "docs/arch"}})
    got = mod.resolve_system_root(str(repo / ".sdlc"), str(repo))
    assert got.form == "folder" and got.value == "docs/arch" and got.error is None and got.note is None
    assert isinstance(got.path, Path) and got.path.is_absolute()
    real_root = Path(os.path.realpath(str(repo)))
    assert Path(os.path.realpath(str(got.path))).parent.parent == real_root
    other = make_repo(tmp_path, {"architecture": {"system_root": "../x"}})
    bad = mod.resolve_system_root(str(other / ".sdlc"), str(other))
    assert bad.form == "invalid" and bad.path is None and isinstance(bad.error, str)
    assert "architecture.system_root" in bad.error


def test_resolve_system_root_forms(tmp_path):
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"

    def resolve(config):
        repo = make_repo(tmp_path, config)
        if config.get("architecture") == {"system_root": "link"}:
            os.symlink(str(_fresh(tmp_path, "outside")), str(repo / "link"))
        return mod.resolve_system_root(str(repo / ".sdlc"), str(repo))

    unset = resolve({})
    assert (unset.form, unset.value, unset.error, unset.note) == ("folder", "docs/architecture", None, None)
    assert resolve({"architecture": {"system_root": "docs/arch"}}).form == "folder"
    assert resolve({"architecture": {"system_root": "docs/nope"}}).form == "folder"   # existence: lint only
    for url in ("https" + "://" + "example.invalid/org/repo", "ssh" + "://" + "git" + "@example.invalid/repo",
                "git" + "@" + "example.invalid:org/repo.git"):
        got = resolve({"architecture": {"system_root": url}})
        assert (got.form, got.path, got.error) == ("url", None, None), (url, got)
    for value in ("/" + "abs" + "/x", "docs/../../x", "C:\\x", "\\\\h\\s", "", 5, ["a"], {}, "link",
                  "https" + "://", "docs/a\nb", "docs/a\x00b", "docs/a\x7fb"):
        got = resolve({"architecture": {"system_root": value}})
        assert got.form == "invalid" and "architecture.system_root" in (got.error or ""), (value, got)
    got = resolve({"architecture": "docs/architecture"})
    assert got.form == "invalid" and "architecture.system_root" in (got.error or "")


def test_resolve_verb_output(tmp_path):
    repo = make_repo(tmp_path, {"architecture": {"system_root": "docs/architecture"}})
    rc, out, err = run_script(tmp_path, ["resolve"], cwd=repo)
    assert rc == 0 and out.splitlines()[0] == "docs/architecture folder", (rc, out, err)
    repo = make_repo(tmp_path, {"architecture": {"system_root": "../x"}})
    rc, out, err = run_script(tmp_path, ["resolve"], cwd=repo)
    assert rc == 1 and "invalid" in out and "architecture.system_root" in out, (rc, out, err)
    url = "https" + "://" + "example.invalid/org/repo"
    repo = make_repo(tmp_path, {"architecture": {"system_root": url}})
    rc, out, err = run_script(tmp_path, ["resolve"], cwd=repo)
    assert rc == 0 and out.splitlines()[0] == url + " url", (rc, out, err)
    assert "existence not checked (no network)" in out
    # `lint` with no <dir> has no local folder to read for a URL root: exit 2, never a lint
    rc, out, err = run_script(tmp_path, ["lint"], cwd=repo)
    assert rc == 2 and "repository URL" in err and not FINDING.search(out), (rc, out, err)
    # a value holding a control character is printed as JSON, so it cannot break the output line
    repo = make_repo(tmp_path, {"architecture": {"system_root": "docs/a\nb"}})
    rc, out, err = run_script(tmp_path, ["resolve"], cwd=repo)
    assert rc == 1 and out.splitlines()[0] == '"docs/a\\nb" invalid', (rc, out, err)


@pytest.mark.parametrize("raw", [
    pytest.param(None, id="missing"),
    pytest.param("{not json", id="invalid_json"),
    pytest.param("[1, 2]", id="json_list"),
    pytest.param("FIFO", id="fifo"),
])
def test_config_unreadable_falls_back_and_never_claims_checked(tmp_path, raw):
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"
    repo = make_repo(tmp_path, raw if raw is not None else {})
    if raw in (None, "FIFO"):
        (repo / ".sdlc" / "config.json").unlink()
    if raw == "FIFO":       # a named pipe with no writer: opening it would block every run forever
        os.mkfifo(str(repo / ".sdlc" / "config.json"))
    # an explicit <dir>: lints, never a finding for the key, and says the key was not checked
    rc, out, err = run_gesture(tmp_path, repo / "target", cwd=repo)
    assert rc == 0 and not FINDING.search(out), (rc, out, err)
    assert "config unreadable" in err and "architecture.system_root not checked" in err, err
    got = mod.resolve_system_root(str(repo / ".sdlc"), str(repo))
    assert (got.form, got.value, got.note) == ("folder", "docs/architecture", "config unreadable: default used")
    rc, out, err = run_script(tmp_path, ["resolve"], cwd=repo)
    assert rc == 0 and "docs/architecture" in out and "default docs/architecture used" in err, (rc, out, err)
    # no <dir>: the default is linted, and its absence is a finding naming the path and the key
    rc, out, err = run_script(tmp_path, ["lint"], cwd=repo)
    assert rc == 1 and "default docs/architecture used" in err, (rc, out, err)
    missing = [line for line in out.splitlines() if "[missing-file]" in line]
    assert missing and "docs/architecture" in missing[0] and "architecture.system_root" in missing[0], out
    # and once the default folder holds a clean document, no <dir> lints it clean
    (repo / "docs" / "architecture").mkdir(parents=True)
    shutil.copyfile(fixture("clean") / "architecture.md", repo / "docs" / "architecture" / "architecture.md")
    rc, out, err = run_script(tmp_path, ["lint"], cwd=repo)
    assert rc == 0 and not FINDING.search(out), (rc, out, err)


# ------------------------------------------------------------------------------------ parser edges

def test_parser_edge_cases(tmp_path):
    clean = (fixture("clean") / "architecture.md").read_text(encoding="utf-8")
    planted = (fixture("planted_missing_row") / "architecture.md").read_text(encoding="utf-8")
    red = (1, ("[container-no-inventory]", "Billing API"))
    # CRLF line endings
    check(run_gesture(tmp_path, write_doc(tmp_path, clean, newline="\r\n")), (0,))
    check(run_gesture(tmp_path, write_doc(tmp_path, planted, newline="\r\n")), red)
    # a leading UTF-8 BOM in front of the first required heading
    headless = clean.split("\n", 2)[2]
    assert headless.startswith("## Context and scope")
    check(run_gesture(tmp_path, write_doc(tmp_path, "\ufeff" + headless)), (0,))
    # tab-indented node lines
    check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace("    ", "\t"))), (0,))
    check(run_gesture(tmp_path, write_doc(tmp_path, planted.replace("    ", "\t"))), red)
    # a heading only inside a fence is not a heading
    check(run_gesture(tmp_path, fixture("fenced_heading")), (1, ("[missing-heading]", "Risks and technical debt")))
    # an inner, shorter fence line inside a mermaid block is content, not a close
    inner = "````mermaid\nflowchart LR\n    A --> B\n```\n    B --> C\n````\n"
    check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace("How a request flows.\n",
                                                                  "How a request flows.\n\n" + inner))), (0,))
    # a %% comment line before the keyword, and a leading front-matter block
    for extra in ("```mermaid\n%% the request path\nflowchart LR\n    A --> B\n```\n",
                  "```mermaid\n---\ntitle: Request path\n---\nflowchart LR\n    A --> B\n```\n"):
        check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace("How a request flows.\n",
                                                                      "How a request flows.\n\n" + extra))), (0,))
    # trailing spaces on headings, and numbered headings
    check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace("## Constraints", "## Constraints   "))), (0,))

    def numbered(text):
        for n, name in enumerate(HEADINGS, 1):
            text = text.replace(f"## {name}\n", f"## {n}. {name}\n")
        return text
    assert "## 4. Building blocks" in numbered(clean)
    check(run_gesture(tmp_path, write_doc(tmp_path, numbered(clean))), (0,))
    check(run_gesture(tmp_path, write_doc(tmp_path, numbered(planted))), red)


def test_node_extraction_edge_lines_and_subgraph_titles(tmp_path):
    check(run_gesture(tmp_path, fixture("edge_line_container")), (1, ("[container-no-inventory]", "Billing API")))
    for name in ("subgraph_title", "last_bracket"):
        check(run_gesture(tmp_path, fixture(name)), (0,))
    check(run_gesture(tmp_path, fixture("unbalanced_bracket_quoted")), (1, ("[unbalanced-bracket]",)))
    # shape punctuation is not part of the name: cylinder, stadium, subroutine, circle, and the
    # quoted parallelogram and trapezoid forms
    clean = (fixture("clean") / "architecture.md").read_text(encoding="utf-8")
    shapes = ('    db[("Postgres [PostgreSQL]")]\n    st(["Status page [Static HTML]"])\n'
              '    job[["Nightly job [Cron]"]]\n    hub(("Event hub [NATS]"))\n'
              '    q[/"Queue [Kafka]"/]\n    p[\\"Parser [Rust]"\\]\n    t[/"Topic [Kafka]"\\]\n')
    rows = ("| Postgres | PostgreSQL |\n| Status page | Static HTML |\n| Nightly job | Cron |\n| Event hub | NATS |\n"
            "| Queue | Kafka |\n| Parser | Rust |\n| Topic | Kafka |\n")
    with_shapes = clean.replace("    user --> web\n", shapes + "    user --> web\n", 1)
    check(run_gesture(tmp_path, write_doc(tmp_path, with_shapes + rows)), (0,))
    rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, with_shapes))
    named = sorted(re.findall(r"container '([^']*)'", out))
    assert rc == 1 and named == ["Event hub", "Nightly job", "Parser", "Postgres", "Queue", "Status page",
                                 "Topic"], (out, err)
    # a line break before the technology is not part of the name, in every spelling
    for br in ("<br>", "<br/>", "<br />", "<BR/>"):
        check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace(
            'web["Web UI [TypeScript]"]', 'web["Web UI' + br + '[TypeScript]"]'))), (0,))
    # a non-ASCII id is a node (it used to be skipped silently), and so is a node right after an
    # open link `---` (the id is cut where the edge ends)
    for node, name in (('    café["Café API [Go]"]\n', "Café API"),
                       ('    user---q["Queue [Kafka]"]\n', "Queue")):
        rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, clean.replace("    user --> web\n",
                                                                               node + "    user --> web\n")))
        assert rc == 1 and re.findall(r"container '([^']*)'", out) == [name], (node, out, err)


def test_second_container_diagram_is_not_silently_skipped(tmp_path):
    check(run_gesture(tmp_path, fixture("second_container_block")),
          (1, ("[container-no-inventory]", "Event queue"), ("Billing API",)))


def _padded(text, size):
    """`text` with filler lines added before the Risks heading so it is exactly `size` bytes."""
    head, tail = text.split("## Risks and technical debt", 1)
    tail = "## Risks and technical debt" + tail
    room = size - len((head + tail).encode("utf-8"))
    line = "Filler sentence for the size bound.\n"
    filler = line * (room // len(line))
    filler += "x" * (room - len(filler) - 1) + "\n" if room - len(filler) > 0 else ""
    out = head + filler + tail
    assert len(out.encode("utf-8")) == size
    return out


def test_oversize_file_exit_2_and_line_cap_finding(tmp_path):
    """The 10 s bound here is a labelled SMOKE check that the 1 MiB and adversarial-line runs finish;
    it is not a performance claim (the measured numbers live in the module docstring)."""
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"
    clean = (fixture("clean") / "architecture.md").read_text(encoding="utf-8")
    over = write_doc(tmp_path, _padded(clean, mod.READ_CAP + 1))
    rc, out, err = run_gesture(tmp_path, over)
    assert rc == 2 and str(mod.READ_CAP + 1) in (out + err), (rc, out, err)
    started = time.monotonic()
    rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, _padded(clean, mod.READ_CAP)))
    assert rc == 0 and not FINDING.search(out), (rc, out, err)          # read in full, never truncated
    assert time.monotonic() - started < 10                              # SMOKE
    assert mod.LINE_CAP == 4096
    para = "How a request flows.\n"
    rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, clean.replace(para, para + "\n" + "y" * 4097 + "\n")))
    assert rc == 1 and codes(out) == {"line-too-long"} and "4097" in out, (rc, out, err)
    check(run_gesture(tmp_path, write_doc(tmp_path, clean.replace(para, para + "\n" + "y" * 4096 + "\n"))), (0,))
    adversarial = ('["a [b' * 700)[:4096]
    started = time.monotonic()
    rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, clean.replace(
        para, para + "\n```mermaid\nflowchart LR\n" + adversarial + "\n```\n")))
    assert rc == 1 and "[unbalanced-bracket]" in out, (rc, out, err)
    assert time.monotonic() - started < 10                              # SMOKE
    # Pattern blow-ups (code review 1). Each input took the earlier patterns 9.8 s to 45 s, or was
    # killed at the 10 s timeout (rc None); every one now lints in tens of milliseconds, so 5 s is a
    # generous bound, not a performance claim.
    anchor = "    user --> web\n"
    for unit, where, fill in (("~" * 3000 + "`\n", para, False),       # one tilde-then-backtick line
                              ("~" * 4000 + "`\n", para, True),        # ~1 MiB of them
                              ("a" * 4096 + "\n", anchor, True),       # ~1 MiB of identifier runs, no opener
                              (("a-" * 2048)[:4095] + "\n", anchor, True),   # ~1 MiB of hyphenated runs
                              ("## " + " #" * 2000 + "x\n", para, True)):    # ~1 MiB of trailing-hash headings
        cut = clean.index(where) + (len(where) if where == para else 0)
        head, tail = clean[:cut] + ("\n" if where == para else ""), clean[cut:]
        room = mod.READ_CAP - len((head + tail).encode("utf-8"))
        count = room // len(unit) if fill else 1
        started = time.monotonic()
        rc, out, err = run_gesture(tmp_path, write_doc(tmp_path, head + unit * count + tail))
        elapsed = time.monotonic() - started
        assert rc == 0 and not FINDING.search(out), (unit[:12], count, rc, out[:300], err[:300])
        assert elapsed < 5, (unit[:12], count, elapsed)                 # SMOKE, generous


def test_symlink_escape_exit_2(tmp_path):
    outside = _fresh(tmp_path, "outside")
    shutil.copyfile(fixture("clean") / "architecture.md", outside / "architecture.md")
    target = _fresh(tmp_path, "target")
    os.symlink(str(outside / "architecture.md"), str(target / "architecture.md"))
    rc, out, err = run_gesture(tmp_path, target)
    assert rc == 2 and "symlink" in (out + err), (rc, out, err)
    fifo_dir = _fresh(tmp_path, "fifo")
    os.mkfifo(str(fifo_dir / "architecture.md"))
    assert stat.S_ISFIFO(os.stat(str(fifo_dir / "architecture.md")).st_mode)
    rc, out, err = run_gesture(tmp_path, fifo_dir)                      # inside run_script's 10 s timeout
    assert rc == 2 and "not a regular file" in (out + err), (rc, out, err)


def test_nonexistent_dir_and_missing_file_exit_1_naming_path(tmp_path):
    missing = tmp_path / "no-such-dir"
    rc, out, err = run_gesture(tmp_path, missing)
    assert rc == 1 and "[missing-file]" in out and str(missing) in out, (rc, out, err)
    assert "architecture.system_root" not in out                        # `lint <dir>` names the path only
    empty = _fresh(tmp_path, "empty")
    rc, out, err = run_gesture(tmp_path, empty)
    assert rc == 1 and "[missing-file]" in out and str(empty / "architecture.md") in out, (rc, out, err)
    # no <dir>: an explicit system root that does not exist is a missing file naming the key too
    repo = make_repo(tmp_path, {"architecture": {"system_root": "docs/nope"}})
    rc, out, err = run_script(tmp_path, ["lint"], cwd=repo)
    missing_lines = [line for line in out.splitlines() if "[missing-file]" in line]
    assert rc == 1 and missing_lines, (rc, out, err)
    assert "docs/nope" in missing_lines[0] and "architecture.system_root" in missing_lines[0], out


# ----------------------------------------------------------------------- safety and the py3.9 floor

STDLIB_ALLOWED = {"__future__", "json", "os", "re", "stat", "sys", "pathlib", "typing"}
BANNED_ATTRS = {"write_text", "write_bytes", "mkdir", "makedirs", "touch", "unlink", "rename", "rmdir",
                "remove", "symlink_to", "chmod", "rmtree"}


def test_lint_is_read_only_and_stdlib_only_py39(tmp_path):
    """Read-only and stdlib-only by AST; and runnable by the system `/usr/bin/python3`, the
    interpreter the documented `python3` gesture reaches on a stock Mac. That interpreter is 3.9.6
    on macOS, so this proves the 3.9 floor on macOS ONLY; on another host `/usr/bin/python3` may be
    a newer Python and the run proves less."""
    assert SCRIPT.exists(), "architecture_doc.py missing"
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "open":
                modes = [a for a in node.args[1:2]] + [k.value for k in node.keywords if k.arg == "mode"]
                for mode in modes:
                    assert isinstance(mode, ast.Constant) and not set(str(mode.value)) & set("wax+"), \
                        ast.unparse(node)
            if isinstance(fn, ast.Attribute):
                assert fn.attr not in BANNED_ATTRS, ast.unparse(node)
        elif isinstance(node, ast.ExceptHandler):
            assert node.type is not None, "bare except"
            assert not (isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException")), \
                "broad except"
        assert type(node).__name__ != "Match", "match statement (needs Python 3.10)"
    assert imported <= STDLIB_ALLOWED, imported - STDLIB_ALLOWED
    assert os.path.exists(SYSTEM_PYTHON), f"{SYSTEM_PYTHON} missing"
    rc, out, err = run_script(tmp_path, ["-h"], python=SYSTEM_PYTHON)
    assert rc == 0 and "usage" in out, (rc, out, err)
    check(run_gesture(tmp_path, fixture("clean"), python=SYSTEM_PYTHON), (0,))
    check(run_gesture(tmp_path, fixture("planted_missing_row"), python=SYSTEM_PYTHON),
          (1, ("[container-no-inventory]", "Billing API")))


def test_timing_numbers_recorded_in_docstring():
    """This RECORDS that a measurement was written down; it does not PROVE the numbers. Nothing in
    the suite re-measures them; the in-suite bound is the separate SMOKE check in
    test_oversize_file_exit_2_and_line_cap_finding."""
    mod = load_module()
    assert mod is not None, "architecture_doc.py missing"
    doc = mod.__doc__ or ""
    assert re.search(r"Measured \d{4}-\d{2}-\d{2}", doc), "no measurement date"
    sentences = re.split(r"(?<=[.;])\s", " ".join(doc.split()))
    assert any(re.search(r"1 MiB.*?\d+(\.\d+)? ?(ms|s)\b", s) for s in sentences), "no 1 MiB timing"
    assert "4096" in doc or "4,096" in doc
    assert re.search(r"Python 3\.\d+\.\d+", doc) and "best of 5" in doc
    assert "Measured: not yet" not in doc


# --------------------------------------------------------------------------------- docs and config

def test_doc_states_required_content():
    assert DOC.exists(), "docs/architecture-doc.md missing"
    text = DOC.read_text(encoding="utf-8")
    flat = " ".join(text.replace("`", "").split())      # code-span markup does not change a sentence
    for name in HEADINGS:
        assert name in text, name
    assert "Context and scope" in flat and "Building blocks" in flat and "required" in flat
    placement = ("The system document lives at architecture.system_root (default "
                 "docs/architecture/architecture.md). In a monorepo that is one folder; in a multi-repo "
                 "system it is the system repository, and each service keeps its own document in "
                 "docs/architecture/ in its own repository (or services/<name>/docs/ in a monorepo).")
    assert placement in flat, "placement rule (D-2)"
    assert ("keep the numbered architecture rules in the north-star, link this document in one line, "
            "copy neither; nothing can detect copying, so this is a convention") in flat
    assert "existence not checked (no network)" in flat
    assert ("Every flowchart under Building blocks is read as a container diagram, not only the first: "
            "each of its nodes needs a Technology inventory row unless its technology is Person or "
            "External system. An extra flowchart there (a deployment sketch, a sub-flow) will produce "
            "findings; put it under another heading.") in flat, "D-4 sentence"
    assert "not supported" in flat and "not measured on Windows" in flat, "D-15"
    assert "no second skeleton" in flat, "D-16"
    gesture_lines = [s for s in _fenced_lines(text) if "architecture_doc.py lint" in s]
    assert GESTURE_LINE in gesture_lines, gesture_lines
    for line in gesture_lines:
        if line.startswith(GESTURE_LINE.rsplit(" ", 1)[0]):
            assert line == GESTURE_LINE or "--" not in line, line


def test_config_template_has_architecture_key():
    assert TEMPLATE.exists(), f"{TEMPLATE} missing"
    cfg = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    assert "architecture" in cfg, "architecture block missing from config.json.tmpl"
    assert isinstance(cfg["architecture"], dict)
    assert cfg["architecture"].get("system_root") == "docs/architecture"
    assert isinstance(cfg.get("_architecture"), str) and "architecture.system_root" in cfg["_architecture"]
    assert CONFIG_DOC.exists(), f"{CONFIG_DOC} missing"
    rows = [line for line in CONFIG_DOC.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `architecture.system_root`")]
    assert len(rows) == 1, rows
