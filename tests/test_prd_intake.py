"""#822: the PRD door into the Dossier pipeline (`skills/sigma-prd-intake`).

`intake.py` is the deterministic half: the model drafts `answers.json` (each answer with a verbatim
PRD quote); the adapter validates, hashes, maps and files. No network: local-goals config or an
injected `gh` runner. Fixtures are copied to tmp_path because a PRD under `tests/` is (correctly)
refused by the repo-source guard.
"""
import importlib.util
import json
import pathlib
import re
import shlex
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "prd_intake"
LOOP = ROOT / "skills" / "sigma-loop" / "scripts"
DOSSIER = ROOT / "skills" / "sigma-dossier" / "scripts"
INTAKE = ROOT / "skills" / "sigma-prd-intake" / "scripts"
SKILL_MD = ROOT / "skills" / "sigma-prd-intake" / "SKILL.md"


def _mod(name, base):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dossier = _mod("dossier", DOSSIER)
blocker_scan = _mod("blocker_scan", LOOP)


_INTAKE = []


def _intake():
    if not _INTAKE:
        _INTAKE.append(_mod("intake", INTAKE))
    return _INTAKE[0]


# ------------------------------------------------------------------------------ fixtures / helpers

CONTRA = ["Maximum upload size is 10 MB per file.", "Uploads are limited to 25 MB per file."]
VAGUE = "The experience must be fast and intuitive."


def _prd(tmp_path, name="messy.md"):
    d = tmp_path / "prd"
    d.mkdir(exist_ok=True)
    dst = d / name
    shutil.copy(FIX / name, dst)
    return dst


def _outcome(name="Shared customer notes", **over):
    o = {
        "name": name,
        "section": "Requirements",
        "answers": {
            "title": "Shared customer notes",
            "problem": "Customer context is lost because notes live in personal documents.",
            "why_now": "Leadership wants it before quarterly planning.",
            "who": "Support agents and their team leads.",
            "outcome": "Agents attach files to notes and the whole team sees them.",
            "constraints": "Upload limit is 10 MB in one line and 25 MB in another.",
            "non_goals": "Billing, SSO and mobile apps.",
            "open_experience": "'fast and intuitive' has no measurable target.",
        },
        "status": {"constraints": "contradiction", "open_experience": "vague"},
        "cites": {
            "title": "Customer support agents and their team leads use the product every day.",
            "problem": "Support teams lose track of customer context because notes live in "
                       "personal documents.",
            "why_now": "Leadership wants this fixed before the next quarterly planning cycle.",
            "who": "Customer support agents and their team leads use the product every day.",
            "outcome": "Notes are visible to every agent on the same team.",
            "constraints": list(CONTRA),
            "non_goals": "Billing, SSO and mobile apps are not part of this release.",
            "open_experience": VAGUE,
        },
    }
    o.update(over)
    return o


def _write(tmp_path, outcomes, name="answers.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"outcomes": outcomes}), encoding="utf-8")
    return p


def _sdlc(tmp_path, source="local-goals"):
    d = tmp_path / "sdlc"
    d.mkdir(exist_ok=True)
    cfg = {"discovery": {"source": source}}
    if source == "github":
        cfg["discovery"]["github"] = {"repo": "acme/widget"}
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return d


def _plan(tmp_path, outcomes, prd=None, capsys=None, sdlc=None):
    intake = _intake()
    prd = prd or _prd(tmp_path)
    sdlc = sdlc or _sdlc(tmp_path)
    rc = intake.main(["intake.py", "plan", "--prd", str(prd), "--answers",
                      str(_write(tmp_path, outcomes)), "--dir", str(sdlc)])
    return intake, rc, sdlc


def _plan_file(sdlc):
    files = [p for p in (sdlc / "intake").glob("*.json") if not p.name.endswith(".filed.json")]
    assert len(files) == 1, files
    return files[0]


def _runner(fail_nth_create=None):
    calls = []
    state = {"creates": 0}

    def run(args):
        args = list(args)
        rest_create = (len(args) >= 2 and args[0] == "api" and args[1].endswith("/issues")
                       and "POST" in args)
        if rest_create:
            # #895 slice 3: issue creates go REST first. Record the call in `gh issue create`
            # form so the assertions below read one shape, and answer with the REST issue dict.
            fields = [a for a in args if "=" in a and not a.startswith("-")]
            val = lambda k: next(f.split("=", 1)[1] for f in fields if f.startswith(k + "="))  # noqa: E731
            norm = ["issue", "create", "--title", val("title"), "--body", val("body")]
            for f in fields:
                if f.startswith("labels[]="):
                    norm += ["--label", f.split("=", 1)[1]]
            calls.append(norm)
        else:
            calls.append(args)
        if rest_create or (len(args) >= 2 and args[0] == "issue" and args[1] == "create"):
            state["creates"] += 1
            if fail_nth_create == state["creates"]:
                raise RuntimeError("simulated gh failure")
            n = 100 + state["creates"]
            if rest_create:
                return json.dumps({"number": n, "html_url": "https://github.com/acme/widget/issues/%d" % n})
            return "https://github.com/acme/widget/issues/%d\n" % n
        return ""

    run.calls = calls
    return run


def _refusal(capsys):
    out = capsys.readouterr()
    m = re.search(r"REFUSED \[([a-z0-9-]+)\]", out.err)
    return out, (m.group(1) if m else None)


# --------------------------------------------------------------------------- step 1: fixtures

def test_fixtures_have_planted_defects():
    messy = (FIX / "messy.md").read_text(encoding="utf-8")
    for line in CONTRA + [VAGUE]:
        assert line in messy
    flat = (FIX / "flat.txt").read_text(encoding="utf-8")
    assert "\n#" not in flat and not flat.startswith("#") and "- " not in flat
    assert len(flat.strip().splitlines()) == 1


# --------------------------------------------------------------------- step 2: dossier additive

def test_render_block_unchanged_without_source():
    answers = {"title": "T"}
    golden = ("<!-- sigma:dossier-qa:start -->\n## Dossier\n\n- **title** — T\n\n"
              "<!-- sigma:dossier-qa:end -->")
    assert dossier.render_block(answers, (), None) == golden
    assert dossier.render_block(answers, ()) == golden


def test_no_tail_return_dict_unchanged(tmp_path):
    sdlc = _sdlc(tmp_path)
    answers = {q["id"]: "x" for q in dossier.bank()}
    answers["next_step"] = "file and stop"
    res = dossier.file(str(sdlc), dossier.triage._config(str(sdlc)), answers, dossier.bank(),
                       "stop", prd_source={"path": "p.md", "sha256": "a" * 64})
    assert set(res) == {"outcome", "number", "title", "decision", "detail"}
    assert res["outcome"] == "filed"


def test_source_block_carries_path_and_sha():
    src = {"path": "docs/prd.md", "sha256": "ab" * 32, "section": "Requirements",
           "cites": {"problem": "a verbatim PRD sentence of some length"}}
    block = dossier.render_block({"title": "T"}, (), src)
    assert "### Source" in block
    assert "docs/prd.md" in block and "ab" * 32 in block and "Requirements" in block
    assert "a verbatim PRD sentence of some length" in block
    # outside the fence: after DOSSIER_QA_END
    assert block.index(dossier.DOSSIER_QA_END) < block.index("### Source")


def test_source_block_trigger_words_defanged():
    src = {"path": "docs/prd.md", "sha256": "0" * 64, "section": "Needs #5 review",
           "cites": {"problem": "the vendor depends on #7 for the export step"}}
    block = dossier.render_block({"title": "T"}, (), src)
    assert "### Source" in block
    assert blocker_scan._BLOCK_RE.findall(block) == []


# ------------------------------------------------------------------ step 3: skeleton / refusals

def test_refusal_has_empty_stdout(tmp_path, capsys):
    intake = _intake()
    rc = intake.main(["intake.py", "plan", "--prd", str(tmp_path / "nope.md"),
                      "--answers", str(tmp_path / "a.json")])
    out, code = _refusal(capsys)
    assert rc == 2 and out.out == "" and code


# ----------------------------------------------------------------- step 4: any shape (AC-1, AC-5)

@pytest.mark.parametrize("name", ["messy.md", "flat.txt"])
def test_plan_accepts_any_shape(tmp_path, capsys, name):
    prd = _prd(tmp_path, name)
    text = prd.read_text(encoding="utf-8")
    quote = " ".join(text.split())[:60]
    o = _outcome(answers={k: ("Shared customer notes" if k == "title" else "a") for k in
                          ("title", "problem", "why_now", "who", "outcome", "constraints",
                           "non_goals")},
                 status={}, cites={k: quote for k in
                                   ("title", "problem", "why_now", "who", "outcome", "constraints",
                                    "non_goals")})
    intake, rc, sdlc = _plan(tmp_path, [o], prd=prd)
    out = capsys.readouterr()
    assert rc == 0, out.err
    assert re.search(r"sha256 [0-9a-f]{64}", out.out)
    assert "Shared customer notes" in out.out and "->" in out.out
    assert _plan_file(sdlc).exists()


def test_plan_accepts_one_line_prd(tmp_path, capsys):
    prd = tmp_path / "one.txt"
    prd.write_text("Build a shared notes page for the support team please.", encoding="utf-8")
    q = "Build a shared notes page for the support team please."
    ids = ("title", "problem", "why_now", "who", "outcome", "constraints", "non_goals")
    o = _outcome(answers={k: "a" for k in ids}, status={}, cites={k: q for k in ids})
    _, rc, _ = _plan(tmp_path, [o], prd=prd)
    assert rc == 0, capsys.readouterr().err


# ---------------------------------------------------------------------- step 5: the quote guard

def test_answer_without_cite_refused(tmp_path, capsys):
    o = _outcome()
    del o["cites"]["problem"]
    _, rc, _ = _plan(tmp_path, [o])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "uncited-answer" and out.out == ""


def test_invented_quote_refused(tmp_path, capsys):
    o = _outcome()
    o["cites"]["problem"] = "Customers are furious about the missing dark mode toggle."
    _, rc, _ = _plan(tmp_path, [o])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "quote-not-in-prd"


def test_verbatim_quote_accepted(tmp_path, capsys):
    o = _outcome()
    # whitespace-normalised on both sides: a quote spanning the line break still matches
    o["cites"]["why_now"] = ("Leadership wants this fixed before the next quarterly planning "
                             "cycle.")
    _, rc, _ = _plan(tmp_path, [o])
    assert rc == 0, capsys.readouterr().err


def test_one_char_quote_refused(tmp_path, capsys):
    o = _outcome()
    o["cites"]["problem"] = "a"
    _, rc, _ = _plan(tmp_path, [o])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "quote-too-short"


# ------------------------------------------------------------------ step 6: open_ write (AC-3, 9)

def _planned(tmp_path, capsys, outcomes=None):
    _, rc, sdlc = _plan(tmp_path, outcomes or [_outcome()])
    assert rc == 0, capsys.readouterr().err
    capsys.readouterr()
    return json.loads(_plan_file(sdlc).read_text(encoding="utf-8")), sdlc


def test_planted_contradiction_becomes_open_question(tmp_path, capsys):
    plan, _ = _planned(tmp_path, capsys)
    a = plan["outcomes"][0]["answers"]
    assert "open_constraints" in a
    assert CONTRA[0] in a["open_constraints"] or any(
        CONTRA[0] in q["ask"] for q in plan["outcomes"][0]["questions"]
        if q["id"] == "open_constraints")


def test_planted_vague_becomes_open_question(tmp_path, capsys):
    plan, _ = _planned(tmp_path, capsys)
    qs = {q["id"]: q["ask"] for q in plan["outcomes"][0]["questions"]}
    assert "open_experience" in plan["outcomes"][0]["answers"]
    assert VAGUE in qs["open_experience"]


def test_open_block_survives_render(tmp_path, capsys):
    plan, _ = _planned(tmp_path, capsys)
    o = plan["outcomes"][0]
    block = dossier.render_block(o["answers"], o["questions"])
    assert dossier.OPEN_HEADING in block
    assert "open_constraints" in block and "open_experience" in block


def test_silent_bank_id_gets_placeholder_and_open(tmp_path, capsys):
    o = _outcome()
    del o["answers"]["why_now"]
    del o["cites"]["why_now"]
    plan, _ = _planned(tmp_path, capsys, [o])
    a = plan["outcomes"][0]["answers"]
    assert a["why_now"] == "Not stated in the PRD; see open_why_now."
    assert "open_why_now" in a


def test_placeholder_not_counted_as_cited(tmp_path, capsys):
    o = _outcome()
    del o["answers"]["why_now"]
    del o["cites"]["why_now"]
    plan, _ = _planned(tmp_path, capsys, [o])
    # 7 bank ids; constraints (contradiction) + why_now (silent) are placeholders -> 5 real answers
    # plus the vague open_ (status-marked, not real) -> cited_count is exactly the 5 real ones
    assert plan["outcomes"][0]["cited_count"] == 5
    assert "why_now" not in plan["outcomes"][0]["cites"]


def test_all_placeholder_prd_refused(tmp_path, capsys):
    o = _outcome(answers={}, status={}, cites={})
    _, rc, _ = _plan(tmp_path, [o])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "no-cited-answers"


def test_silent_title_falls_back_to_outcome_name(tmp_path, capsys):
    o = _outcome(name="Notes for teams")
    del o["answers"]["title"]
    del o["cites"]["title"]
    plan, _ = _planned(tmp_path, capsys, [o])
    assert plan["outcomes"][0]["answers"]["title"] == "Notes for teams"
    assert "Not stated" not in plan["outcomes"][0]["answers"]["title"]


def test_next_step_matches_decision(tmp_path, capsys):
    plan, sdlc = _planned(tmp_path, capsys)
    o, intake = plan["outcomes"][0], _intake()
    assert o["answers"]["next_step"] == intake.NEXT_STEP == "file and stop"
    res = dossier.file(str(sdlc), {"discovery": {"source": "local-goals"}}, o["answers"],
                       o["questions"], intake.DECISION, apply=False)
    assert res["outcome"] == "would", res


def test_every_open_has_question(tmp_path, capsys):
    plan, sdlc = _planned(tmp_path, capsys)
    o = plan["outcomes"][0]
    opens = [k for k in o["answers"] if k.startswith("open_")]
    assert opens
    qs = {q["id"]: q["ask"] for q in o["questions"]}
    for k in opens:
        assert qs.get(k, "").strip()
    assert dossier.file(str(sdlc), {"discovery": {"source": "local-goals"}}, o["answers"],
                        o["questions"], "stop", apply=False)["outcome"] == "would"


def test_open_questions_not_capped(tmp_path, capsys):
    o = _outcome()
    for i in range(6):
        o["answers"]["open_extra%d" % i] = "gap %d" % i
        o["status"]["open_extra%d" % i] = "silent"
    plan, _ = _planned(tmp_path, capsys, [o])
    opens = [k for k in plan["outcomes"][0]["answers"] if k.startswith("open_")]
    assert len(opens) >= 8
    assert not [k for k in plan["outcomes"][0]["answers"] if k.startswith("followup_")]


def test_followup_over_cap_refused(tmp_path, capsys):
    o = _outcome()
    for i in range(dossier.MAX_FOLLOWUPS + 1):
        o["answers"]["followup_f%d" % i] = "x"
        o["cites"]["followup_f%d" % i] = "Notes are visible to every agent on the same team."
    _, rc, _ = _plan(tmp_path, [o])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "too-many-followups"


def test_duplicate_outcome_names_refused(tmp_path, capsys):
    _, rc, _ = _plan(tmp_path, [_outcome(), _outcome()])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "duplicate-outcome"


def test_too_many_outcomes_refused(tmp_path, capsys):
    outs = [_outcome(name="o%d" % i) for i in range(13)]
    _, rc, _ = _plan(tmp_path, outs)
    out, code = _refusal(capsys)
    assert rc == 2 and code == "too-many-outcomes"


def test_prd_too_large_refused(tmp_path, capsys):
    prd = tmp_path / "big.txt"
    prd.write_text("x" * (_intake().MAX_PRD_BYTES + 1), encoding="utf-8")
    _, rc, _ = _plan(tmp_path, [_outcome()], prd=prd)
    out, code = _refusal(capsys)
    assert rc == 2 and code == "prd-too-large"


# -------------------------------------------------------------------------- step 7: repo guard

def _documented_gesture():
    text = SKILL_MD.read_text(encoding="utf-8")
    lines = [l.strip() for l in text.splitlines()
             if "intake.py plan --prd skills/sigma-loop/scripts/loop.py" in l]
    assert lines, "SKILL.md must show the refused gesture"
    return lines[0]


def test_docs_gesture_matches_skill_md(tmp_path, capsys, monkeypatch):
    line = _documented_gesture().replace("${CLAUDE_SKILL_DIR}", str(ROOT / "skills" /
                                                                    "sigma-prd-intake"))
    argv = shlex.split(line)
    assert argv[0] == "python3"
    monkeypatch.chdir(ROOT)
    rc = _intake().main(argv[1:])
    out, code = _refusal(capsys)
    assert rc == 2 and code == "repo-source" and out.out == ""


def test_untracked_py_in_repo_refused(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    for name in ("new.py", ".env", "ui.ts", "docs/readme.py"):
        p = repo / name
        p.parent.mkdir(exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        rc = _intake().main(["intake.py", "plan", "--prd", name, "--answers", "a.json"])
        out, code = _refusal(capsys)
        assert rc == 2 and code == "repo-source", name


def test_doc_inside_repo_allowed_but_not_under_code_dirs(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "skills").mkdir()
    (repo / "skills" / "x.md").write_text("some text", encoding="utf-8")
    monkeypatch.chdir(repo)
    rc = _intake().main(["intake.py", "plan", "--prd", "skills/x.md", "--answers", "a.json"])
    assert _refusal(capsys)[1] == "repo-source" and rc == 2


def test_outside_repo_prd_passes_guard(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.chdir(repo)
    prd = _prd(tmp_path)
    rc = _intake().main(["intake.py", "plan", "--prd", str(prd), "--answers",
                         str(tmp_path / "missing.json")])
    out, code = _refusal(capsys)
    assert code != "repo-source"


def test_non_git_cwd_applies_allowlist(tmp_path, capsys, monkeypatch):
    (tmp_path / "code.py").write_text("x = 1", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    rc = _intake().main(["intake.py", "plan", "--prd", "code.py", "--answers", "a.json"])
    assert rc == 2 and _refusal(capsys)[1] == "repo-source"


def test_directory_prd_refused(tmp_path, capsys):
    rc = _intake().main(["intake.py", "plan", "--prd", str(tmp_path), "--answers", "a.json"])
    assert rc == 2 and _refusal(capsys)[1] == "prd-is-directory"


def test_symlink_into_repo_code_refused(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "mod.py").write_text("x = 1", encoding="utf-8")
    link = tmp_path / "link.md"
    link.symlink_to(repo / "mod.py")
    monkeypatch.chdir(repo)
    rc = _intake().main(["intake.py", "plan", "--prd", str(link), "--answers", "a.json"])
    assert rc == 2 and _refusal(capsys)[1] == "repo-source"


def test_repo_prd_from_non_repo_cwd_and_sdlc_refused(tmp_path, capsys, monkeypatch):
    """CR1-1: cwd and --dir are both outside any repo; the absolute --prd points into one."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "skills").mkdir()
    (repo / "skills" / "secret.md").write_text("x" * 40, encoding="utf-8")
    (repo / "mod.py").write_text("x = 1", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    for target in (repo / "skills" / "secret.md", repo / "mod.py"):
        rc = _intake().main(["intake.py", "plan", "--prd", str(target), "--answers", "a.json",
                             "--dir", str(elsewhere / ".sdlc")])
        assert rc == 2 and _refusal(capsys)[1] == "repo-source", target


def test_real_repo_file_from_non_repo_cwd_refused(tmp_path, capsys, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc = _intake().main(["intake.py", "plan", "--prd",
                         str(INTAKE / "intake.py"), "--answers", "a.json",
                         "--dir", str(elsewhere / ".sdlc")])
    assert rc == 2 and _refusal(capsys)[1] == "repo-source"


def test_install_root_guards_without_git(tmp_path, capsys, monkeypatch):
    """CR1-1: the plugin install dir (no .git) is a root of its own."""
    plugin = tmp_path / "plugin"
    (plugin / "skills").mkdir(parents=True)
    (plugin / "skills" / "x.md").write_text("y" * 40, encoding="utf-8")
    intake = _intake()
    monkeypatch.setattr(intake, "SELF_ROOT", plugin)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc = intake.main(["intake.py", "plan", "--prd", str(plugin / "skills" / "x.md"),
                      "--answers", "a.json", "--dir", str(elsewhere / ".sdlc")])
    assert rc == 2 and _refusal(capsys)[1] == "repo-source"


@pytest.mark.parametrize("name", ["credentials", "config.json", "key.pem", "notes.md.bak"])
def test_non_text_doc_outside_repo_refused(tmp_path, capsys, monkeypatch, name):
    """CR1-2: the extension allow-list holds wherever the file is."""
    f = tmp_path / "home" / name
    f.parent.mkdir()
    f.write_text("aws_secret_access_key = abcdefghijklmnop", encoding="utf-8")
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    rc = _intake().main(["intake.py", "plan", "--prd", str(f), "--answers", "a.json"])
    assert rc == 2 and _refusal(capsys)[1] == "prd-not-text-doc"


def test_open_answer_without_status_not_cited(tmp_path, capsys):
    """CR1-4: only bank ids and followup_* count as cited answers."""
    o = _outcome(answers={"open_x": "something unresolved"}, status={},
                 cites={"open_x": VAGUE})
    _, rc, _ = _plan(tmp_path, [o])
    assert rc == 2 and _refusal(capsys)[1] == "no-cited-answers"


@pytest.mark.parametrize("flag", ["--repo-read", "--read-code", "--grep"])
def test_repo_read_flags_refused(tmp_path, capsys, flag):
    rc = _intake().main(["intake.py", "plan", "--prd", str(_prd(tmp_path)), "--answers", "a.json",
                         flag, "src"])
    assert rc == 2 and _refusal(capsys)[1] == "repo-read-flag"


# ---------------------------------------------------------------------------- step 8: file

def _filed(tmp_path, capsys, outcomes=None, run=None, source="local-goals", dry=False,
           rerun_with=None):
    outcomes = outcomes or [_outcome(name="Shared notes"), _outcome(name="Note search")]
    sdlc = _sdlc(tmp_path, source)
    intake, rc, _ = _plan(tmp_path, outcomes, sdlc=sdlc)
    assert rc == 0, capsys.readouterr().err
    capsys.readouterr()
    argv = ["intake.py", "file", "--plan", str(_plan_file(sdlc))]
    if dry:
        argv.append("--dry-run")
    rc = intake.main(argv, run=run)
    return intake, rc, sdlc, argv


def _goal_texts(sdlc):
    return {p.name: p.read_text(encoding="utf-8") for p in sorted((sdlc / "goals").glob("*.md"))}


def test_file_dossiers_carry_source_and_hash(tmp_path, capsys):
    _, rc, sdlc, _ = _filed(tmp_path, capsys)
    assert rc == 0, capsys.readouterr().err
    texts = _goal_texts(sdlc)
    sha = json.loads(_plan_file(sdlc).read_text(encoding="utf-8"))["prd"]["sha256"]
    dossiers = [t for t in texts.values() if "dossier-qa:start" in t]
    assert len(dossiers) == 2
    for t in dossiers:
        assert "### Source" in t and sha in t and "messy.md" in t
        assert "cite open_constraints" in t          # refinement 1: cites keyed by answer id


def test_umbrella_lists_every_dossier(tmp_path, capsys):
    _, rc, sdlc, _ = _filed(tmp_path, capsys)
    texts = _goal_texts(sdlc)
    umbrella = [t for t in texts.values() if "dossier-qa:start" not in t]
    assert len(umbrella) == 1
    for n in ("#1", "#2"):
        assert n in umbrella[0]
    assert "Shared notes" in umbrella[0] and "Note search" in umbrella[0]


def test_umbrella_never_goal_labelled(tmp_path, capsys):
    _, rc, sdlc, _ = _filed(tmp_path, capsys)
    umbrella = [t for t in _goal_texts(sdlc).values() if "dossier-qa:start" not in t][0]
    assert "status: proposed" in umbrella and "status: pending" not in umbrella
    assert "Labels: epic" in umbrella


def test_umbrella_body_has_no_phantom_blocker(tmp_path, capsys):
    outs = [_outcome(name="Needs review after login"), _outcome(name="Depends on search")]
    _, rc, sdlc, _ = _filed(tmp_path, capsys, outs)
    umbrella = [t for t in _goal_texts(sdlc).values() if "dossier-qa:start" not in t][0]
    assert blocker_scan._BLOCK_RE.findall(umbrella) == []


def test_dry_run_creates_nothing(tmp_path, capsys):
    _, rc, sdlc, _ = _filed(tmp_path, capsys, dry=True)
    assert rc == 0, capsys.readouterr().err
    assert not (sdlc / "goals").exists() or not list((sdlc / "goals").glob("*.md"))
    run = _runner()
    gh = tmp_path / "gh"
    gh.mkdir()
    _, rc, _, _ = _filed(gh, capsys, source="github", run=run, dry=True)
    assert rc == 0 and run.calls == []


def test_filed_dossier_is_a_normal_dossier(tmp_path, capsys):
    _, rc, sdlc, _ = _filed(tmp_path, capsys)
    plan = json.loads(_plan_file(sdlc).read_text(encoding="utf-8"))
    o = plan["outcomes"][0]
    block = dossier.render_block(o["answers"], o["questions"])
    first = [t for t in _goal_texts(sdlc).values() if "dossier-qa:start" in t][0]
    assert block in first
    src = (INTAKE / "intake.py").read_text(encoding="utf-8")
    assert "sdlc:designed" not in src


def test_umbrella_epic_label_exists_or_minted(tmp_path, capsys):
    run = _runner()
    _, rc, _, _ = _filed(tmp_path, capsys, source="github", run=run)
    assert rc == 0, capsys.readouterr().err
    flat = [" ".join(map(str, c)) for c in run.calls]
    mint = [i for i, c in enumerate(flat) if c.startswith("label create epic")]
    umb = [i for i, c in enumerate(flat) if c.startswith("issue create") and "--label epic" in c]
    assert mint and umb and mint[-1] < umb[-1]
    assert not [c for c in flat if "issue create" in c and "--label sdlc:goal" in c]


# --------------------------------------------------------------------------- step 9: resume

def test_rerun_after_partial_failure(tmp_path, capsys):
    outs = [_outcome(name="One"), _outcome(name="Two"), _outcome(name="Three")]
    run1 = _runner(fail_nth_create=2)
    intake, rc, sdlc, argv = _filed(tmp_path, capsys, outs, run=run1, source="github")
    assert rc == 2 and _refusal(capsys)[1] == "file-failed"
    run2 = _runner()
    rc = intake.main(argv, run=run2)
    assert rc == 0, capsys.readouterr().err
    creates2 = [c for c in run2.calls if c[:2] == ["issue", "create"]]
    titles2 = [c[c.index("--title") + 1] for c in creates2]
    assert len(creates2) == 3                       # Two, Three, umbrella -- not One again
    assert not any(t == "One" for t in titles2)
    rec = json.loads(next(sdlc.glob("intake/*.filed.json")).read_text(encoding="utf-8"))
    assert set(rec["dossiers"]) == {"One", "Two", "Three"} and rec["umbrella"]
    # a third run files nothing
    run3 = _runner()
    assert intake.main(argv, run=run3) == 0
    assert not [c for c in run3.calls if c[:2] == ["issue", "create"]]


def test_replan_new_answers_fresh_resume(tmp_path, capsys):
    intake, rc, sdlc, argv = _filed(tmp_path, capsys, [_outcome(name="One")])
    assert rc == 0
    plan = _plan_file(sdlc)
    old_key = intake._plan_key(plan)
    assert (sdlc / "intake" / (old_key + ".filed.json")).exists()
    o2 = _outcome(name="One")
    o2["answers"]["who"] = "Team leads only."
    _write(tmp_path, [o2])
    assert intake.main(["intake.py", "plan", "--prd", str(tmp_path / "prd" / "messy.md"),
                        "--answers", str(tmp_path / "answers.json"), "--dir",
                        str(sdlc)]) == 0
    capsys.readouterr()
    assert intake._plan_key(plan) != old_key              # same path, new bytes, new key
    assert intake.main(["intake.py", "file", "--plan", str(plan), "--status"]) == 0
    out = capsys.readouterr().out
    assert "One" not in out and "not filed" in out        # fresh resume state, not the old record


# ------------------------------------------------------------------- step 10-12: skill and docs

def test_skill_md_shape():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\nname: sigma-prd-intake\n")
    for needle in ("stdin", "issue number", "fast lane", "scope-to-goals", "EXISTS",
                   "not a sandbox", "per-answer", "epic"):
        assert needle in text, needle


def test_prd_door_documented():
    pipeline = (ROOT / "docs" / "dossier-pipeline.md").read_text(encoding="utf-8")
    walk = (ROOT / "docs" / "how-the-dossier-pipeline-works.md").read_text(encoding="utf-8")
    for doc in (pipeline, walk):
        assert "sigma-prd-intake" in doc
        assert "scope-to-goals compile is not the route for a PRD" in doc
    assert "Stage 0: the PRD door" in pipeline


@pytest.mark.parametrize("rel", ["Skills/sigma-dossier/SKILL.md", "TESTS/x.md", "Hooks/a.txt",
                                 "EVALS/b.md", "Tools/c.md", "skills/d.md"])
def test_code_dir_match_is_case_insensitive(rel):
    """Pure classification: fails on any filesystem (macOS resolves Skills/ to the real skills/)."""
    assert _intake()._in_code_dir(pathlib.PurePosixPath(rel))
    assert not _intake()._in_code_dir(pathlib.PurePosixPath("docs/Skills/x.md"))
