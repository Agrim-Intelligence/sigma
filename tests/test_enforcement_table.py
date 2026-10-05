"""`docs/enforcement.md` is generated, and the registries it is generated from are complete (#2740).

Three things are pinned here. (1) The committed table byte-equals what
`skills/sigma-doctor/scripts/enforcement_table.py` prints, so a gate, a hook or a template default
that changes without the doc regenerating goes red. (2) Every module-level function in
`skills/sigma-loop/scripts/{work,state,loop}.py` whose name follows the gate convention is listed in
that module's `ENFORCEMENT_GATES` or `ENFORCEMENT_EXEMPT`, so a new gate cannot ship undocumented.
(3) The README's feature-table rows that name a control link to the table, and a row the table
marks `advice` does not call itself a gate.

This proves only what it enumerates: a gate whose function name does not follow the convention is
invisible to it. Registering such a gate is still required; this check just cannot notice when you
forget.

Regenerate after any change to a registry, `hooks/hooks.json`, the config template, or the
generator itself:

    python3 skills/sigma-doctor/scripts/enforcement_table.py > docs/enforcement.md
"""
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEN = ROOT / "skills" / "sigma-doctor" / "scripts" / "enforcement_table.py"

#: Exactly what the generator reads (plus the doc it must match and the README it checks).
_CORE = (
    "skills/sigma-loop/scripts/work.py",
    "skills/sigma-loop/scripts/state.py",
    "skills/sigma-loop/scripts/loop.py",
    "hooks/hooks.json",
    "skills/sigma-init/templates/config.json.tmpl",
    "docs/enforcement.md",
    "README.md",
)


def _load():
    spec = importlib.util.spec_from_file_location("enforcement_table", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _core_copy(tmp_path):
    for rel in _CORE:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return tmp_path


def _replace_once(path, old, new):
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path.name}: {old!r} not found -- str.replace would fail silently"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------- Task 1: registries + structure


def test_the_real_tree_has_no_structural_problems():
    gen = _load()
    assert gen.problems(ROOT) == []
    # Non-vacuity: the net sees the real gates, and every module registered something.
    assert len(gen.gate_candidates(ROOT)) >= 15
    registries = gen.load_registries(ROOT)
    assert set(registries) == {"work.py", "state.py", "loop.py"}
    for module, (gates, _exempt) in registries.items():
        assert gates, f"{module}: empty ENFORCEMENT_GATES"


def test_a_planted_unregistered_gate_fails(tmp_path):
    gen = _load()
    tmp = _core_copy(tmp_path)
    assert gen.problems(tmp) == [], "the copy is incomplete; a red below would not be the plant's"
    work = tmp / "skills/sigma-loop/scripts/work.py"
    work.write_text(work.read_text(encoding="utf-8") + "\n\ndef _planted_refusal():\n    return None\n",
                    encoding="utf-8")
    found = gen.problems(tmp)
    assert len(found) == 1, found
    assert "work.py" in found[0] and "_planted_refusal" in found[0], found


def test_a_registered_gate_that_was_renamed_fails(tmp_path):
    gen = _load()
    tmp = _core_copy(tmp_path)
    _replace_once(tmp / "skills/sigma-loop/scripts/work.py",
                  "def _dirty_root_refusal(", "def _dirty_root_check(")
    found = gen.problems(tmp)
    assert any("_dirty_root_refusal" in p for p in found), found


def test_hooks_json_and_hook_facts_match_both_ways(tmp_path):
    gen = _load()
    tmp = _core_copy(tmp_path / "a")
    hooks_path = tmp / "hooks/hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    data["hooks"]["Stop"].append({"hooks": [
        {"type": "command", "command": 'bash "${CLAUDE_PLUGIN_ROOT}/hooks/planted_hook.sh"'}]})
    hooks_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    found = gen.problems(tmp)
    assert any("planted_hook.sh" in p for p in found), found

    tmp = _core_copy(tmp_path / "b")
    hooks_path = tmp / "hooks/hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert "SessionStart" in data["hooks"]
    del data["hooks"]["SessionStart"]
    hooks_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    found = gen.problems(tmp)
    assert any("session_start.sh" in p for p in found), found


def test_every_template_gates_key_has_a_row(tmp_path):
    gen = _load()
    tmp = _core_copy(tmp_path)
    tmpl = tmp / "skills/sigma-init/templates/config.json.tmpl"
    data = json.loads(tmpl.read_text(encoding="utf-8"))
    data["gates"]["planted_key"] = True
    tmpl.write_text(json.dumps(data, indent=2), encoding="utf-8")
    found = gen.problems(tmp)
    assert any("gates.planted_key" in p for p in found), found


def test_generator_refuses_on_a_structural_problem(tmp_path, capsys):
    gen = _load()
    tmp = _core_copy(tmp_path)
    work = tmp / "skills/sigma-loop/scripts/work.py"
    work.write_text(work.read_text(encoding="utf-8") + "\n\ndef _planted_refusal():\n    return None\n",
                    encoding="utf-8")
    rc = gen.main([], root=tmp)
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "REFUSED" in err and "_planted_refusal" in err, err


def test_help_prints_usage_without_rendering(tmp_path, capsys):
    """`--help` / `-h` (#2736): exit 0, "usage" and the regenerate command on stdout, nothing on
    stderr. The tree is planted with a structural problem so a help path that still ran
    `problems()` or `render()` would refuse (rc 2) or emit the table -- either fails here."""
    gen = _load()
    tmp = _core_copy(tmp_path)
    work = tmp / "skills/sigma-loop/scripts/work.py"
    work.write_text(work.read_text(encoding="utf-8") + "\n\ndef _planted_refusal():\n    return None\n",
                    encoding="utf-8")
    for flag in ("--help", "-h"):
        rc = gen.main([flag], root=tmp)
        out, err = capsys.readouterr()
        assert rc == 0, (flag, rc, err)
        assert "usage" in out.lower(), (flag, out)
        assert "python3 skills/sigma-doctor/scripts/enforcement_table.py > docs/enforcement.md" in out, out
        assert "GENERATED" not in out and "| Control |" not in out, out
        assert err == "", err


def test_unknown_argument_prints_usage_to_stderr_and_renders_nothing(tmp_path, capsys):
    """`--check > docs/enforcement.md` used to overwrite the doc silently: an unrecognised argument
    now exits 2 with usage on stderr and NOTHING on stdout. The tree is left intact so a path that
    ignored the argument would render the table -- which fails here."""
    gen = _load()
    tmp = _core_copy(tmp_path)
    for argv in (["--check"], ["extra"], ["--help", "extra"]):
        rc = gen.main(argv, root=tmp)
        out, err = capsys.readouterr()
        assert rc == 2, (argv, rc)
        assert out == "", (argv, out)
        assert "usage" in err.lower() and argv[0] in err, (argv, err)


#: The two spellings of "this hook consults gate_state.adopted_root" (#2737): a Python hook's own
#: `_adopted()` wrapper, and a shell hook's `gate_state.py --adopted` spawn.
_ADOPTED_CALL = re.compile(r"\b_adopted\(\)|gate_state\.py\"? --adopted")


def test_hook_rows_that_check_adoption_say_so():
    """The post-PR review found three HOOK_FACTS rows describing pre-#2737 behaviour (a gate "always
    on" that is now inert outside an adopted repo). Every hook whose SOURCE calls the adopted-repo
    check must have a row whose text says `adopted`; a hook that drops the check must drop the word.
    Reads the hook files, so a fact that goes stale against the code goes red here."""
    gen = _load()
    checked = {}
    for fact in gen.HOOK_FACTS:
        if not fact.get("control"):
            continue
        script = fact["id"].split()[0]
        source = (ROOT / "hooks" / script).read_text(encoding="utf-8")
        text = fact["mechanism"] + " " + fact.get("condition", "")
        calls = bool(_ADOPTED_CALL.search(source))
        checked[script] = calls
        assert calls == ("adopted" in text), (
            f"hooks/{script}: source {'calls' if calls else 'does not call'} the adopted-repo check "
            f"but its HOOK_FACTS row {'omits' if calls else 'claims'} `adopted`: {text!r}")
    # Non-vacuity: the four #2737 hooks are seen calling it, and at least one hook does not.
    assert {s for s, c in checked.items() if c} >= {
        "issue_field_gate.py", "decision_gate.py", "plan_gate.sh", "completion_gate.sh"}, checked
    assert any(not c for c in checked.values()), checked


def test_plan_gate_row_qualifies_the_sentinel_by_org_lock():
    """`hooks/plan_gate.sh` consults `.allow-direct-edits` only when unlocked (#2138); its row may
    not say it `honours` the sentinel without saying `org-locked`. `completion_gate.sh` reads the
    sentinel unconditionally, so this pins the asymmetry rather than a blanket word."""
    gen = _load()
    facts = {f["id"]: f for f in gen.HOOK_FACTS}
    plan = (ROOT / "hooks" / "plan_gate.sh").read_text(encoding="utf-8")
    assert '[ "$org_lock" = unlocked ] && [ -f "$PROJECT/.sdlc/.allow-direct-edits" ]' in plan
    assert "org-locked" in facts["plan_gate.sh"]["mechanism"], facts["plan_gate.sh"]["mechanism"]
    stop = (ROOT / "hooks" / "completion_gate.sh").read_text(encoding="utf-8")
    assert "org_lock" not in stop
    assert "org-locked" not in facts["completion_gate.sh"]["mechanism"]


# ---------------------------------------------------------------- Task 2: render + committed doc

DOC = ROOT / "docs" / "enforcement.md"
REGENERATE = "python3 skills/sigma-doctor/scripts/enforcement_table.py > docs/enforcement.md"


def _tables(text):
    """Each maximal run of `|`-lines in `text`, as a list of lines."""
    out, run = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("|"):
            run.append(line)
        elif run:
            out.append(run)
            run = []
    return out


def _controls_table(text):
    for table in _tables(text):
        if table[0].startswith("| Control |"):
            return table
    raise AssertionError("no Controls table rendered")


def test_committed_table_matches_the_generator():
    """The ONE subprocess in this file: the documented gesture, run exactly as the header says."""
    committed = DOC.read_bytes()
    result = subprocess.run([sys.executable, str(GEN)], capture_output=True, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout, "generator printed nothing"
    assert result.stdout.startswith(b"<!-- GENERATED")
    assert result.stdout == committed, (
        "docs/enforcement.md is out of date -- regenerate it:\n    " + REGENERATE)
    directives = [line.split() for line in (ROOT / "docs" / ".gitattributes").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    assert ["enforcement.md", "text", "eol=lf"] in directives, directives


def test_drift_is_caught_both_ways(tmp_path):
    gen = _load()
    tmp = _core_copy(tmp_path)
    doc = tmp / "docs" / "enforcement.md"
    # Non-vacuity, and the "needs only the core sources" property in-process: the copy renders to
    # the committed bytes.
    assert gen.render(tmp).encode("utf-8") == doc.read_bytes()
    _replace_once(doc, "| off —", "| on —")
    assert gen.render(tmp).encode("utf-8") != doc.read_bytes()
    shutil.copy2(ROOT / "docs" / "enforcement.md", doc)
    assert gen.render(tmp).encode("utf-8") == doc.read_bytes()
    tmpl = tmp / "skills/sigma-init/templates/config.json.tmpl"
    data = json.loads(tmpl.read_text(encoding="utf-8"))
    # #228: `verify.enforce` SHIPS false again (#2741 had it true with an empty command, which
    # refused every `done`). The test only needs SOME value change to prove `gen.render` reacts to
    # a template edit; which direction the flip goes is incidental to what it's proving.
    assert data["verify"]["enforce"] is False
    data["verify"]["enforce"] = True
    tmpl.write_text(json.dumps(data, indent=2), encoding="utf-8")
    assert gen.render(tmp).encode("utf-8") != doc.read_bytes(), \
        "the table reads the template: a default flip must regenerate it"


def test_header_names_the_regenerate_command():
    text = DOC.read_text(encoding="utf-8")
    assert "GENERATED" in text
    assert REGENERATE in text


def test_reviewer_row_is_advice_with_the_d5_wording():
    gen = _load()
    lines = [l for l in _controls_table(gen.render(ROOT)) if "Independent review" in l]
    assert len(lines) == 1, lines
    assert "| advice |" in lines[0]
    assert "cannot prove the maker did not influence the reviewer" in lines[0]


def test_every_advice_row_asks_the_agent():
    gen = _load()
    advice = [l for l in _controls_table(gen.render(ROOT))[2:] if "| advice |" in l]
    assert len(advice) >= 5, advice
    for line in advice:
        assert re.search(r"\bask(s)? the (agent|reviewer) to\b", line), line


def test_render_is_deterministic():
    gen = _load()
    assert gen.render(ROOT) == gen.render(ROOT)


def test_every_rendered_table_has_a_delimiter_row():
    gen = _load()
    tables = _tables(gen.render(ROOT))
    assert len(tables) == 2, [t[0] for t in tables]
    for table in tables:
        header = [c.strip() for c in table[0].strip().strip("|").split("|")]
        assert len(table) >= 2, table[0]
        cells = [c.strip() for c in table[1].strip().strip("|").split("|")]
        assert cells and all(re.fullmatch(r":?-+:?", c) for c in cells), table[1]
        assert len(cells) == len(header), (header, cells)


def test_not_yet_paragraph_names_the_t3_non_controls():
    gen = _load()
    text = gen.render(ROOT)
    for needle in ("action_log.enabled", "agent_watch.enabled", "rollback", "feature_owner.py"):
        assert needle in text, needle


# ---------------------------------------------------------------- Task 3: README

README = ROOT / "README.md"


def _readme_row(text, label):
    lines = [l for l in text.splitlines() if l.startswith(f"| **{label}** |")]
    assert len(lines) == 1, (label, lines)
    return lines[0]


def test_readme_feature_rows_link_to_the_table_and_advice_rows_claim_no_gate():
    gen = _load()
    all_rows = gen.rows(ROOT)
    labels = {r["readme"] for r in all_rows if r["readme"]}
    assert len(labels) >= 12, labels
    assert gen.readme_problems(README.read_text(encoding="utf-8"), all_rows) == []


def test_readme_check_control():
    gen = _load()
    all_rows = gen.rows(ROOT)
    text = README.read_text(encoding="utf-8")
    assert gen.readme_problems(text, all_rows) == []
    # (a) the label renamed back to its pre-#2740 wording: the row goes missing.
    old = _readme_row(text, "Strategy-alignment check")
    renamed = text.replace(old, old.replace("**Strategy-alignment check**", "**Strategy-alignment gate**"))
    assert renamed != text
    found = gen.readme_problems(renamed, all_rows)
    assert any("Strategy-alignment check" in p and "missing" in p for p in found), found
    # (b) an advice row calling itself a gate.
    old = _readme_row(text, "Strategy-alignment check")
    cells = old.split(" | ")
    cells[1] = cells[1] + " gate"
    gated = text.replace(old, " | ".join(cells))
    assert gated != text
    found = gen.readme_problems(gated, all_rows)
    assert any("Strategy-alignment check" in p and "gate" in p for p in found), found
    # (c) a labelled row that lost its link.
    old = _readme_row(text, "Stop gate (opt-in)")
    assert "docs/enforcement.md" in old
    unlinked = text.replace(old, old.replace(" · [enforcement](docs/enforcement.md)", ""))
    assert unlinked != text
    found = gen.readme_problems(unlinked, all_rows)
    assert any("Stop gate (opt-in)" in p and "docs/enforcement.md" in p for p in found), found


def test_readme_defaults_match_the_template():
    text = README.read_text(encoding="utf-8")
    for key in ('`work: {"enabled": true}`', '`ledger: {"enabled": true}`'):
        rows_ = [l for l in text.splitlines() if l.startswith(f"| {key} |")]
        assert len(rows_) == 1, (key, rows_)
        assert "null" in rows_[0], rows_[0]
    budget = [l for l in text.splitlines() if l.startswith("| `budget.max_iterations`")]
    assert len(budget) == 1, budget
    for needle in ("480", "500,000", "20"):
        assert needle in budget[0], (needle, budget[0])
    assert "Everything optional ships OFF" not in text
    assert "won't ship work that fights your strategy" not in text
