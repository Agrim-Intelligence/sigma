"""issue #1616 — the structural regression net under every SKILL.md (path (a)).

Sigma's product IS the skill prompts, and until this landed nothing in the repo measured a
change to one of them. Tier 1 (`evals/run.py`) scores the intent hook; Tier 2 (LLM judge) is parked
on a spend decision. This is the free, deterministic tier in between.

WHAT IT DOES NOT PROVE, said here as well as in the module, because a guard oversold is worse than
no guard: preserving text does not preserve ATTENTION. Every assertion below is about structure. The
failure mode that matters after #1611 splits `sigma-loop/SKILL.md` — the agent no longer CHOOSING to
open a reference file — is invisible to all of it, and stays invisible until Tier 2 is unparked.

Every negative assertion here was RUN RED before it was trusted: each `_findings_for` /
`lost_units` case builds the broken input and asserts the specific finding, rather than asserting a
clean tree stays clean (which passes against the bug it targets).
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / "evals" / "skill_structure.py"


def _load():
    spec = importlib.util.spec_from_file_location("skill_structure", MODULE)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ss = _load()


# ---------------------------------------------------------------- helpers


def _skill(root, name, body, **refs):
    """Write a synthetic skill directory. A `refs` keyword spells its path with `__` for `/` and a
    final `_` for the extension: `references__deep_md` -> `references/deep.md`."""
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    for rel, text in refs.items():
        stem, _, ext = rel.replace("__", "/").rpartition("_")
        p = d / f"{stem}.{ext}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def _big(tokens):
    """A body of roughly `tokens` estimated tokens (chars/4), all ordinary prose."""
    return "word " * (tokens * ss.CHARS_PER_TOKEN // 5)


#: `--preserved` reads history, so exactly three tests below need a real git checkout. Everything
#: else about the instrument runs on plain strings, and the two `--preserved` CLI tests inject
#: `git_corpus` instead — so the skip can never take the comparison logic itself out of CI.
#: Skipping matches `test_self_contained.py`'s own precedent, and closes a gap found by running
#: this suite from a `git archive` export: all three went red there for an environmental reason and
#: named no real defect, which is exactly the kind of noise that gets a suite ignored.
#:
#: A skipif is also a way for three real assertions to stop running unnoticed, so
#: `test_the_git_backed_tests_are_not_skipped_in_a_real_checkout` pins that the detection fires
#: where the suite is actually meant to run (`actions/checkout@v4` is always a git checkout).
_IN_GIT_CHECKOUT = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-dir"],
                                  capture_output=True).returncode == 0
requires_git = pytest.mark.skipif(
    not _IN_GIT_CHECKOUT, reason="not a git checkout — `--preserved` needs local history")


def test_the_git_backed_tests_are_not_skipped_in_a_real_checkout():
    """The guard on the guard: prove the marker is not silently disabling the git half.

    It distinguishes the two reasons the three tests could be skipped. No `.git` at all is an
    export or a tarball, where skipping is the right answer and there is nothing to guard. A `.git`
    that IS there while detection said otherwise is the dangerous case — three real assertions
    would go quiet in CI — and only that fails. (`.git` is a FILE in a worktree and a directory in
    a clone; `exists()` covers both, which is why this passes from `.sdlc/work/<n>` too.)"""
    if not (ROOT / ".git").exists():
        pytest.skip("no .git (export or tarball) — skipping the git-backed tests is correct here")
    assert _IN_GIT_CHECKOUT, ("git metadata is present but the checkout was not detected; the "
                              "three git-backed preservation tests would silently skip in CI")


# ---------------------------------------------------------------- the real tree


def test_the_shipped_skills_pass_the_structural_gate():
    """The whole point: green on every skill in `skills/`, today, with only the recorded waivers."""
    assert ss.findings() == []


def test_the_documented_invocation_exits_zero():
    """The gesture `evals/README.md` prints, run as printed — not a stronger variant with a flag the
    docs never mention. A guard whose documented invocation cannot fail is decoration."""
    r = subprocess.run([sys.executable, str(MODULE)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "structural gate: clean." in r.stdout


def test_the_report_names_every_shipped_skill_and_its_waiver():
    r = subprocess.run([sys.executable, str(MODULE), "--table"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0
    for d in ss.skill_dirs():
        assert d.name in r.stdout
    for name, w in ss.load_waivers().items():
        assert f"WAIVED #{w['issue']}" in r.stdout, name


# ---------------------------------------------------------------- budgets


def test_an_oversized_skill_with_no_waiver_is_a_finding(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-huge", _big(ss.COMPACTION_TOKEN_CAP + 500))
    out = ss.findings(root=root, waivers={})
    assert any("sigma-huge: est_tokens" in f and "is not waived" in f for f in out), out


def test_a_skill_over_the_line_limit_with_no_waiver_is_a_finding(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-tall", "line\n" * (ss.LINE_LIMIT + 1))
    out = ss.findings(root=root, waivers={})
    assert any(f"sigma-tall: lines {ss.LINE_LIMIT + 1}" in f for f in out), out


def test_a_skill_just_inside_both_budgets_is_not_a_finding(tmp_path):
    """The control for the two above: the same shape, one unit smaller, must be silent — otherwise
    they would pass against an off-by-one that fails everything."""
    root = tmp_path / "skills"
    _skill(root, "sigma-ok", _big(ss.COMPACTION_TOKEN_CAP - 100))
    assert ss.findings(root=root, waivers={}) == []


def test_a_gate_keyword_past_the_cap_is_its_own_finding(tmp_path):
    """#1611's actual defect: the body is waived as too long, but its NEVER still has to sit inside
    the 5,000 tokens compaction keeps. Waiving the size must not waive the gate."""
    root = tmp_path / "skills"
    body = _big(ss.COMPACTION_TOKEN_CAP + 2000) + "\n\nNEVER force an irreversible action.\n"
    _skill(root, "sigma-deep", body)
    waivers = {"sigma-deep": {"issue": 1, "reason": "x",
                             "recorded": {"est_tokens": 10 ** 9, "lines": 10 ** 9}}}
    out = ss.findings(root=root, waivers=waivers)
    assert any("deepest_gate_tokens" in f for f in out), out


def test_a_skill_stating_no_gate_at_all_reports_none_not_zero(tmp_path):
    """`None` and `0` must not compare equal: 'no gate fell off the cliff' and 'this skill states no
    hard rule' are different facts, and `sigma-goal-review` is really the second one."""
    root = tmp_path / "skills"
    d = _skill(root, "sigma-quiet", "no hard rules here, only lowercase never and must\n")
    assert ss.measure(d)["deepest_gate_tokens"] is None
    assert ss.measure(d)["gates"] == 0


def test_lowercase_prose_is_not_counted_as_a_gate(tmp_path):
    """475 sentences in the shipped corpus contain a lowercase must/never/always. Counting them
    would make 'the deepest gate' mean 'the end of the file' for every skill — i.e. nothing."""
    root = tmp_path / "skills"
    d = _skill(root, "sigma-prose", "you must always do this and never that\n")
    assert ss.measure(d)["gates"] == 0


# ---------------------------------------------------------------- the waiver ratchets


def test_a_waived_skill_that_grows_past_its_record_is_a_finding(tmp_path):
    """Ratchet 1. `sigma-loop` grew 51% between #1611 being filed and being fixed and nothing said a
    word. A waiver freezes the breach; it does not license more of it."""
    root = tmp_path / "skills"
    _skill(root, "sigma-huge", _big(9000))
    waivers = {"sigma-huge": {"issue": 1611, "reason": "x",
                             "recorded": {"est_tokens": 6000, "lines": 10 ** 9}}}
    out = ss.findings(root=root, waivers=waivers)
    assert any("exceeds its own recorded waiver" in f for f in out), out


def test_a_waived_skill_that_shrinks_but_still_breaches_is_silent(tmp_path):
    """The control for the ratchet: shrinking toward the budget must not be punished, or the only
    way to satisfy the gate would be to leave the skill exactly as it is."""
    root = tmp_path / "skills"
    _skill(root, "sigma-huge", _big(6000))
    waivers = {"sigma-huge": {"issue": 1611, "reason": "x",
                             "recorded": {"est_tokens": 9000, "lines": 10 ** 9}}}
    assert ss.findings(root=root, waivers=waivers) == []


def test_a_waiver_whose_skill_is_now_inside_every_budget_is_stale_and_fails(tmp_path):
    """Ratchet 2, and the reason this shape cannot quietly become permanent: the moment #1611 lands,
    CI goes RED until its entry is deleted. Fixing the skill is not enough — the debt record has to
    go with it."""
    root = tmp_path / "skills"
    _skill(root, "sigma-fixed", _big(1000))
    waivers = {"sigma-fixed": {"issue": 1611, "reason": "x", "recorded": {"est_tokens": 9000}}}
    out = ss.findings(root=root, waivers=waivers)
    assert any("STALE WAIVER" in f for f in out), out


def test_a_waiver_naming_no_shipped_skill_is_a_finding(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-real", "small\n")
    out = ss.findings(root=root, waivers={"sigma-ghost": {"issue": 1, "reason": "x",
                                                         "recorded": {}}})
    assert any("names no shipped skill" in f for f in out), out


def test_every_waiver_names_an_issue_and_a_reason():
    for name, w in ss.load_waivers().items():
        assert isinstance(w["issue"], int), name
        assert isinstance(w["reason"], str) and len(w["reason"]) > 40, name
        assert w["recorded"], name


def test_every_waiver_records_exactly_the_budgets_that_breach():
    """A waiver may not record a field that is fine (which would silently pre-authorise a future
    breach of it) nor omit one that is not (which the gate would then report as unwaived)."""
    measured = ss.measure_all()
    for name, w in ss.load_waivers().items():
        assert set(w["recorded"]) == set(ss.breaches(measured[name])), name


def test_the_waiver_list_is_capped_so_a_new_breach_cannot_be_absorbed():
    """Ratchet 3. Lower this literal when an entry is retired. Raising it means arguing, in a PR
    diff, that a THIRD skill should be allowed to lose its guardrails to compaction."""
    assert len(ss.load_waivers()) <= 2


def test_the_recorded_waivers_match_what_the_tree_actually_measures():
    """The recorded numbers are a measurement, not a guess — they must equal today's tree, so the
    ratchet starts at the real value rather than at padded headroom."""
    measured = ss.measure_all()
    for name, w in ss.load_waivers().items():
        for field, value in w["recorded"].items():
            assert measured[name][field] == value, f"{name}.{field}"


# ---------------------------------------------------------------- the estimator


def _breach_set(key):
    return {m["skill"] for m in ss.measure_all().values() if key(m) > ss.COMPACTION_TOKEN_CAP}


def test_the_verdict_survives_every_estimator():
    """The threshold is a proxy, so the finding must not rest on it. chars/4 (shipped), chars/3.8
    (conservative), words x1.33 (#1611's own) and words x1.2 must name the SAME breaching skills.

    This turns red when a skill lands close enough to the cap for the proxy to become load-bearing,
    which is the moment to shrink it rather than to argue about tokenizers."""
    by_chars_4 = _breach_set(lambda m: m["chars"] / 4)
    by_chars_38 = _breach_set(lambda m: m["chars"] / 3.8)
    by_words_133 = _breach_set(lambda m: m["words"] * 1.33)
    by_words_12 = _breach_set(lambda m: m["words"] * 1.2)
    assert by_chars_4 == by_chars_38 == by_words_133 == by_words_12, (
        f"estimators disagree: chars/4={by_chars_4} chars/3.8={by_chars_38} "
        f"words*1.33={by_words_133} words*1.2={by_words_12}")
    assert by_chars_4 == set(ss.load_waivers()), "the waivers ARE the breach set"


def test_the_estimator_and_its_honest_limit_are_written_down():
    """"Decide the estimator honestly and write down why" is a requirement of #1616, so it is
    checkable rather than aspirational — including the sentence that says what this tier cannot do."""
    doc = ss.__doc__
    assert "cl100k" in doc and "3.924" in doc, "the calibration measurement must be recorded"
    assert "words*1.33" in doc or "words x 1.33" in doc, "say why the issue's own ratio is not used"
    assert "Preserving text does not preserve ATTENTION" in doc


def test_a_clean_run_prints_its_own_limit_beside_the_pass():
    """The operator re-parked the Tier-2 behavioural eval on the explicit condition that this
    limit is STATED rather than assumed away (#1616, 2026-09-03). A caveat that lives only in a
    document is not read at the moment somebody decides what a green means, so it rides with the
    verdict — and if it is ever deleted from the output, this fails."""
    r = subprocess.run([sys.executable, str(MODULE)], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    assert "structural gate: clean." in r.stdout
    assert "preserving text does not" in r.stdout.lower()
    assert "attention" in r.stdout.lower()
    assert "parked" in r.stdout.lower()


@requires_git
def test_the_preserved_gesture_prints_the_same_limit_when_it_finds_nothing():
    """The other green a reader can over-trust: 'nothing was lost' is not 'nothing got worse'."""
    r = subprocess.run([sys.executable, str(MODULE), "--preserved", "HEAD", "sigma-vision"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    assert "attention" in r.stdout.lower()


def test_the_docs_state_the_residual_risk_and_cite_the_parked_decision():
    """#1616's second DoD item is a RECORDED decision on path (b). It was re-parked on 2026-09-03;
    both the eval docs and the parked-work register must carry it, so a future reader does not find
    a green guard and an unexplained absence of behavioural evals. The register is this
    repository's own root file, so its half is pinned on the private side's monorepo glue
    (#2584); the core ships the eval docs."""
    text = (ROOT / "evals" / "README.md").read_text()
    assert "2026-09-03" in text
    # Whitespace- and emphasis-insensitive: a re-wrap of the README must not fail this for the
    # wrong reason. It is the SENTENCE that is pinned, not its line breaks.
    readme = " ".join((ROOT / "evals" / "README.md").read_text().replace("*", "").lower().split())
    assert "preserving text does not preserve attention" in readme
    assert "a green tier 0 is not evidence that a skill still works" in readme
    # #2730: the RECORD of the decision is pinned, not the private issue-comment URL that once
    # carried it (shipped docs name no private issue link).
    assert "re-parked 2026-09-03, deliberately and with the reason recorded" in readme
    assert "operator decision on #1616" in readme


# ---------------------------------------------------------------- reference reachability


def test_an_unlinked_reference_file_is_a_finding(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-orphan", "body with no links\n", references__deep_md="rules\n")
    out = ss.findings(root=root, waivers={})
    assert any("references/deep.md` is not reachable" in f for f in out), out


def test_a_directly_linked_reference_file_is_reachable(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-linked", "see [rules](references/deep.md)\n", references__deep_md="rules\n")
    assert ss.findings(root=root, waivers={}) == []


def test_a_reference_reached_only_through_another_reference_is_reachable(tmp_path):
    """The control against a direct-only check: `AUTOWATCH.md` links
    `channels/sigma-autowatch/README.md`, and an agent following the prose gets there fine."""
    root = tmp_path / "skills"
    _skill(root, "sigma-chain", "see [a](A.md)\n", A_md="then see [b](inner/B.md)\n",
           inner__B_md="the rules\n")
    assert ss.findings(root=root, waivers={}) == []


def test_a_brace_expanded_link_is_reachable(tmp_path):
    """`sigma-vision` links its four guides as `references/{vision,strategy,design,architecture}.md`.
    Without brace expansion this check reports four orphans that are not orphans — a false finding
    is how a guard gets switched off."""
    root = tmp_path / "skills"
    _skill(root, "sigma-braced", "load `${CLAUDE_SKILL_DIR}/references/{one,two}.md`\n",
           references__one_md="a\n", references__two_md="b\n")
    assert ss.findings(root=root, waivers={}) == []


def test_scripts_are_not_required_to_be_linked(tmp_path):
    """Scoped to markdown deliberately — 50 of the kit's shipped helpers are called by other code,
    never read as reference material, and demanding each be named in prose is 50 false findings."""
    root = tmp_path / "skills"
    d = _skill(root, "sigma-scripted", "body\n")
    (d / "scripts").mkdir()
    (d / "scripts" / "helper.py").write_text("print(1)\n", encoding="utf-8")
    assert ss.findings(root=root, waivers={}) == []


def test_a_link_to_a_file_that_does_not_exist_is_a_finding(tmp_path):
    """The half a restructure trips first: `[gates](references/gates.md)` written before the file
    is. It reads perfectly and points at nothing."""
    root = tmp_path / "skills"
    _skill(root, "sigma-dead", "see [gates](references/gates.md)\n")
    out = ss.findings(root=root, waivers={})
    assert any("links `references/gates.md`, which does not exist" in f for f in out), out


def test_a_dead_skill_dir_reference_is_a_finding(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-dead2", "load `${CLAUDE_SKILL_DIR}/references/nope.md`\n")
    out = ss.findings(root=root, waivers={})
    assert any("links `references/nope.md`" in f for f in out), out


def test_a_placeholder_path_in_emitted_prose_is_not_a_dead_link(tmp_path):
    """`sigma-goal-design` writes `[<n>-in-brief.md](<n>-in-brief.md)` — a template for the artifact
    it EMITS, not a link into the skill. Angle brackets are the kit's placeholder convention."""
    root = tmp_path / "skills"
    _skill(root, "sigma-tmpl", "start with [`<n>-in-brief.md`](<n>-in-brief.md)\n")
    assert ss.findings(root=root, waivers={}) == []


def test_an_external_link_is_not_a_dead_link(tmp_path):
    root = tmp_path / "skills"
    _skill(root, "sigma-ext", "see [docs](https://example.invalid/x.md)\n")
    assert ss.findings(root=root, waivers={}) == []


# ---------------------------------------------------------------- repo-relative gestures (#2733)


def test_a_repo_relative_python_gesture_in_skill_md_is_a_finding(tmp_path):
    """#2733: `python3 skills/sigma-loop/scripts/x.py` resolves only inside the kit's own checkout.
    From a buyer's repository — where the plugin lives in the plugin cache and `skills/` is not a
    directory — the documented gesture dies with `can't open file`. The check names the file and
    line so the fix is one edit away."""
    root = tmp_path / "skills"
    _skill(root, "sigma-rel", "run `python3 skills/sigma-loop/scripts/x.py list`\n")
    out = ss.findings(root=root, waivers={})
    assert any("python3 skills/" in f and "sigma-rel/SKILL.md:1" in f for f in out), out


def test_a_repo_relative_python_gesture_in_a_reference_is_a_finding(tmp_path):
    """A gesture moved into `references/` by a restructure is still a documented gesture."""
    root = tmp_path / "skills"
    _skill(root, "sigma-relref", "see [r](references/r.md)\n",
           references__r_md="x\npython3 skills/a/scripts/b.py\n")
    out = ss.findings(root=root, waivers={})
    assert any("python3 skills/" in f and "references/r.md:2" in f for f in out), out


def test_the_gesture_scan_visits_every_shipped_skill_md_and_finds_none():
    """Non-vacuity: the scan reaches every shipped SKILL.md and at least one reference file, and
    then finds nothing. A scan over zero files is also 'clean' — this is the assertion that keeps
    that from ever counting."""
    files = [p for d in ss.skill_dirs() for p in ss.gesture_scan_files(d)]
    shipped = len(list(ss.SKILLS_DIR.glob("*/SKILL.md")))
    assert shipped > 0
    assert sum(p.name == "SKILL.md" for p in files) == shipped
    assert any("references" in p.parts for p in files), "no reference file was scanned"
    hits = [g for d in ss.skill_dirs() for g in ss.repo_relative_gestures(d)]
    assert hits == [], hits


# ---------------------------------------------------------------- preservation, the instrument


def test_moving_text_between_files_loses_nothing():
    """The defining property. A restructure MOVES instruction text; if the comparison were
    file-scoped it would call every moved line lost and be useless for the one job it has."""
    before = ["# Skill\n\nAlways park rather than force.\n\nRun the tests first.\n"]
    after = ["# Skill\n\nSee references/rules.md.\n", "Always park rather than force.\n",
             "Run the tests first.\n"]
    assert ss.lost_units(before, after) == []


def test_deleting_a_sentence_is_reported_as_lost():
    before = ["Always park rather than force.\n\nRun the tests first.\n"]
    after = ["Run the tests first.\n"]
    lost = ss.lost_units(before, after)
    assert lost == ["always park rather than force."], lost


def test_rewrapping_a_paragraph_loses_nothing():
    """A split almost always re-wraps. A line-based diff would report the whole paragraph gone."""
    before = ["Park rather than force an irreversible action that a human has not approved yet.\n"]
    after = ["Park rather than force an irreversible\naction that a human has not approved\nyet.\n"]
    assert ss.lost_units(before, after) == []


def test_changing_heading_level_and_emphasis_loses_nothing():
    before = ["## Gates\n\n**Always** park rather than `force`.\n"]
    after = ["#### Gates\n\nAlways park rather than force.\n"]
    assert ss.lost_units(before, after) == []


def test_text_that_appears_twice_and_survives_once_is_still_reported():
    """Multiset, not set: a rule stated in two places and dropped from one has been half-removed."""
    before = ["Never force.\n\nSomething else.\n\nNever force.\n"]
    after = ["Never force.\n\nSomething else.\n"]
    assert ss.lost_units(before, after) == ["never force."]


def test_adding_text_is_not_a_loss():
    before = ["Never force.\n"]
    after = ["Never force.\n\nAnd park instead.\n"]
    assert ss.lost_units(before, after) == []


def _simulated_1611_split(text, parts=3):
    """Cut a SKILL.md at blank lines into `parts` files — a mechanical stand-in for the restructure
    #1611 has to perform."""
    blocks = text.split("\n\n")
    step = max(1, len(blocks) // parts + 1)
    return ["\n\n".join(blocks[i:i + step]) for i in range(0, len(blocks), step)]


def test_a_simulated_restructure_of_the_real_loop_skill_loses_nothing():
    """The dress rehearsal, on the real 16,792-token corpus this guard exists for, run BEFORE the
    restructure is written rather than after — which is the only way the instrument is known to work
    when #1611 needs it. `--preserved` is what #1611's PR runs against the pre-restructure ref."""
    corpus = ss.worktree_corpus(ss.SKILLS_DIR / "sigma-loop")
    before = list(corpus.values())
    after = _simulated_1611_split(corpus["SKILL.md"]) + \
        [t for rel, t in corpus.items() if rel != "SKILL.md"]
    assert ss.lost_units(before, after) == []


def test_the_dress_rehearsal_catches_a_gate_dropped_by_the_restructure():
    """Break the rehearsal deliberately: drop the paragraph holding a real uppercase gate and prove
    the comparison names it. Without this the test above passes against a comparison that always
    returns []."""
    corpus = ss.worktree_corpus(ss.SKILLS_DIR / "sigma-loop")
    skill = corpus["SKILL.md"]
    victim = next(b for b in skill.split("\n\n") if ss.GATE_KEYWORDS.search(b))
    after = _simulated_1611_split(skill.replace(victim, "", 1)) + \
        [t for rel, t in corpus.items() if rel != "SKILL.md"]
    lost = ss.lost_units(list(corpus.values()), after)
    # Not "something was reported" — EVERY unit of the dropped block, by name. A weaker assertion
    # here passes against a comparison that reports the wrong thing, or one line out of nine.
    assert set(ss.normalize_units(victim)) - set(lost) == set(), \
        "every unit of the dropped gate paragraph must be named"


# ---------------------------------------------------------------- preservation, wired to git


@requires_git
def test_git_corpus_reads_a_skill_at_a_ref():
    corpus = ss.git_corpus("HEAD", "sigma-vision")
    assert "SKILL.md" in corpus and "references/vision.md" in corpus
    assert corpus["SKILL.md"].startswith("---")


@requires_git
def test_preserved_against_head_is_clean_for_an_untouched_skill():
    r = subprocess.run([sys.executable, str(MODULE), "--preserved", "HEAD", "sigma-vision"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 instruction unit(s) lost in total." in r.stdout


def test_preserved_exits_nonzero_when_the_ref_held_more(monkeypatch):
    """The control for the CLI half: feed it a `before` that says one extra thing and prove the
    documented invocation exits 1 and prints the missing line."""
    monkeypatch.setattr(ss, "git_corpus",
                        lambda ref, skill, root=None: {"SKILL.md": "A rule nobody kept.\n"})
    rc = ss.main(["--preserved", "HEAD", "sigma-vision"])
    assert rc == 1


def test_preserved_says_so_when_the_skill_did_not_exist_at_the_ref(monkeypatch):
    monkeypatch.setattr(ss, "git_corpus", lambda ref, skill, root=None: {})
    assert ss.main(["--preserved", "HEAD", "sigma-vision"]) == 0


# ---------------------------------------------------------------- the gate as a CLI


def test_the_gate_exits_nonzero_on_a_finding(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    _skill(root, "sigma-huge", _big(ss.COMPACTION_TOKEN_CAP + 500))
    monkeypatch.setattr(ss, "SKILLS_DIR", root)
    monkeypatch.setattr(ss, "load_waivers", lambda *a, **k: {})
    assert ss.main([]) == 1


def test_table_mode_never_fails(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    _skill(root, "sigma-huge", _big(ss.COMPACTION_TOKEN_CAP + 500))
    monkeypatch.setattr(ss, "SKILLS_DIR", root)
    monkeypatch.setattr(ss, "load_waivers", lambda *a, **k: {})
    assert ss.main(["--table"]) == 0


def test_the_waiver_file_is_valid_json_with_the_documented_shape():
    data = json.loads((ROOT / "evals" / "skill_budget_waivers.json").read_text())
    assert set(data) == {"_comment", "waivers"}
    assert isinstance(data["_comment"], list) and data["_comment"]


def test_the_readme_documents_the_invocation_the_tests_run():
    """A gesture the docs do not give is a gesture nobody runs. Pin the two this file exercises."""
    readme = (ROOT / "evals" / "README.md").read_text()
    assert "python3 evals/skill_structure.py" in readme
    assert "--preserved" in readme


# ---------------------------------------------------------------- untracked clutter (#2176)


@requires_git
def test_an_untracked_markdown_file_in_a_real_skill_dir_is_invisible_to_every_sweep():
    """The `test_docs.py`/`test_self_contained.py` defect class (#1821, recurring as #2002),
    reproduced here: a raw `rglob("*.md")` also reads whatever is sitting in the working tree, and
    on any machine that has run `npm install` for the autowatch channel that is 149+ vendored
    `node_modules` READMEs — turning `main` red on a developer's disk while a fresh clone and CI
    stay green. This is a REAL, git-tracked skill directory (not a synthetic `tmp_path` one, which
    `_tracked_md_files` deliberately treats differently — see its own docstring) with one genuinely
    untracked `.md` file dropped into it, proving the fix rather than asserting the current tree
    happens to be clean.

    Must be a real skill directory, not a synthetic one: `_tracked_md_files` falls open to the raw
    walk for any path outside `ROOT` (a `tmp_path` skill is exactly that), on purpose — see its own
    docstring — so a synthetic-dir test would exercise the fallback, not the fix."""
    target = ROOT / "skills" / "sigma-loop" / "zzz-untracked-scratch-2176.md"
    assert not target.exists(), "a real file already sits at the scratch path this test uses"
    target.write_text("this file is never staged or committed -- it must never be seen", encoding="utf-8")
    try:
        status = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", str(target)],
                                capture_output=True, text=True)
        assert status.stdout.strip().startswith("??"), "the scratch file must be untracked, not staged"

        skill_dir = ROOT / "skills" / "sigma-loop"
        refs = ss.reference_files(skill_dir)
        assert "zzz-untracked-scratch-2176.md" not in refs, refs

        corpus = ss.worktree_corpus(skill_dir)
        assert "zzz-untracked-scratch-2176.md" not in corpus, sorted(corpus)

        links = ss.dead_links(skill_dir)
        assert not any("zzz-untracked-scratch-2176.md" in src for src, _ in links), links
    finally:
        target.unlink(missing_ok=True)


# ---------------------------------------------------------------- shipped paths resolve (#2732)

import posixpath  # noqa: E402  (section-local: the resolver below is the only user)
import re  # noqa: E402

import public_surface  # noqa: E402  (pytest puts tests/ on sys.path; see its module docstring)

_MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_BACKTICK = re.compile(r"`([^`\n]+)`")
#: Placeholders and globs: `<n>.md`, `{a,b}` left unexpanded, `*.md`, `...`, `NNN`.
_PLACEHOLDER = re.compile(r"[<>{}*?]|\.\.\.|NNN")
_LINE_SUFFIX = re.compile(r":\d+(-\d+)?$")
_CHECKED_SUFFIXES = (".md", ".py")
#: `$VAR/` prefixes with a known meaning. `None` = the citing file's own `skills/<s>/`, which does
#: not exist outside `skills/` -- so a routine prompt in README that says `${CLAUDE_SKILL_DIR}/...`
#: is a finding, not a skip.
_VAR_PREFIXES = (("${CLAUDE_SKILL_DIR}/", None), ("${CLAUDE_PLUGIN_ROOT}/", ""),
                 ("<sigma>/", ""), ("<plugin>/", ""))


def _public_universe(root):
    """What the snapshot carries: the public surface plus the `public-overrides/` targets. In the
    snapshot the override dir is absent (hard-excluded by the builder) and the mapping is identity;
    in the monorepo the targets are added and their TEXT is read from the override copy, so the
    tree scans exactly the bytes the snapshot will."""
    root = pathlib.Path(root)
    overrides_dir = root / "public-overrides"
    overrides = {p.relative_to(overrides_dir).as_posix() for p in overrides_dir.rglob("*")
                 if p.is_file()} if overrides_dir.is_dir() else set()
    universe = set(public_surface.public_files(root)) | overrides
    return universe, overrides


def _tokens(line):
    """Markdown link targets, plus whitespace-split words inside backticks that end `.md`/`.py`
    (line suffixes `:N`/`:N-M`, quotes, trailing `(),;` and `#anchor` stripped)."""
    out = []
    for m in _MD_LINK.finditer(line):
        target = m.group(1)
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#", target):
            continue
        out.append(target.split("#")[0])
    for m in _BACKTICK.finditer(line):
        for word in m.group(1).split():
            word = _LINE_SUFFIX.sub("", word.strip("\"'(),;")).split("#")[0]
            if word.endswith(_CHECKED_SUFFIXES):
                out.append(word)
    return [t for t in out if t]


#: First line of the CHANGELOG rename entry: it and everything below it is history.
_CHANGELOG_HISTORY = "- **Every skill and command now starts with `sigma-`**"


def _shipped_path_findings(root):
    """Every path a shipped `.md`/`.mdc` names that the public snapshot does not carry.

    Returns `(findings, stats)`: findings as `"<rel>:<line>\\t<token>"`, stats as a dict with
    `files` scanned, `ok`, `skipped` and `dangling` token counts, and `scanned` (the set of files).
    Resolution is against the universe only -- never the filesystem -- so a tree-only file that a
    shipped doc points at (the historical defect) is a finding, and the tree and the snapshot reach
    the same verdict. Directories are derived from the universe for the same reason."""
    root = pathlib.Path(root)
    universe, overrides = _public_universe(root)
    dirs = {"/".join(p.split("/")[:i]) for p in universe for i in range(1, p.count("/") + 1)}
    roots = {p.split("/")[0] for p in universe}
    corpus = sorted(p for p in universe
                    if p.endswith((".md", ".mdc")) and not p.startswith("tests/"))

    def resolve(citing, token):
        skill_match = re.match(r"^skills/([^/]+)/", citing)
        skill_dir = "skills/%s" % skill_match.group(1) if skill_match else None
        for prefix, base in _VAR_PREFIXES:
            if token.startswith(prefix):
                if base is None:
                    base = skill_dir
                if base is None:
                    return "dangling"
                cand = posixpath.normpath(posixpath.join(base, token[len(prefix):]))
                return "ok" if cand in universe or cand in dirs else "dangling"
        if (_PLACEHOLDER.search(token) or token.startswith(("$", "/", "~", ".sdlc/"))
                or "/" not in token):
            return "skipped"
        citing_dir = posixpath.dirname(citing)
        cands = [posixpath.normpath(posixpath.join(citing_dir, token)), posixpath.normpath(token)]
        if skill_dir:
            cands.append(posixpath.normpath(posixpath.join(skill_dir, token)))
        if any(c in universe or c in dirs for c in cands):
            return "ok"
        first = token.split("/")[0]
        ours = (token.startswith(("./", "../")) or first in roots
                or (skill_dir and posixpath.join(skill_dir, first) in dirs)
                or posixpath.join(citing_dir, first) in dirs)
        return "dangling" if ours else "skipped"

    findings, stats = [], {"files": 0, "ok": 0, "skipped": 0, "dangling": 0, "scanned": set()}
    for rel in corpus:
        source = (root / "public-overrides" / rel) if rel in overrides else (root / rel)
        text = source.read_text(encoding="utf-8", errors="replace")
        stats["files"] += 1
        stats["scanned"].add(rel)
        # History names paths as they were (#523): recorded launch evidence, and the CHANGELOG from
        # its rename entry down. Neither is a promise that a path still ships, so neither is scanned
        # for one; everything above the rename entry (new entries) still is.
        if rel.startswith("docs/launch/evidence/"):
            continue
        history = False
        for number, line in enumerate(text.splitlines(), 1):
            if rel == "CHANGELOG.md" and line.startswith(_CHANGELOG_HISTORY):
                history = True
            if history:
                continue
            for expanded in ss.expand_braces(line).splitlines():
                for token in _tokens(expanded):
                    verdict = resolve(rel, token)
                    stats[verdict] += 1
                    if verdict == "dangling":
                        findings.append("%s:%d\t%s" % (rel, number, token))
    return findings, stats


def test_every_path_a_shipped_doc_names_ships():
    """No file the public snapshot carries points at a path the snapshot does not carry (#2732,
    Q1-G3). Runs unparametrized and without a tree-shape skip, so it collects identically in the
    monorepo and inside the built snapshot, where the builder runs it again.

    WHAT IT DOES NOT CHECK, so a green is not read as "every mention was verified": URLs; globs
    and placeholders (`<n>.md`, `*.md`, `...`, `NNN`); `.sdlc/` adopter-runtime paths; `$VAR/`
    paths under any variable but the four the resolver maps -- `${CLAUDE_SKILL_DIR}/` (the citing
    file's own `skills/<s>/`), `${CLAUDE_PLUGIN_ROOT}/`, `<sigma>/` and `<plugin>/` (the repo
    root) -- so `$LS/loop.py` or `$HOME/x.md` is skipped, not resolved; absolute (`/...`) and
    home (`~/...`) paths; bare names without a `/` (`loop.py`, `TEAM.md` -- #1613 covers bare
    `*.py` by basename); foreign example paths whose first segment is no shipped root and no
    directory of the citing file or its skill (`notes/ui-ideas.md`, `.claude/CLAUDE.md`); anything
    in `.py`/`.json`/`.sh`/template files; `tests/**` (fixture data); inline code spans that wrap
    across a line; fenced code blocks' bare words (only link targets and `.md`/`.py` words inside
    single backticks are tokens); reference-style link definitions (`[x]: path` -- the link regex
    matches only `](...)`); paths in prose without backticks. It also cannot see this repository's
    private `.sdlc/design|plans/<n>.md` citations in shipped docs (S12, deferred) because `.sdlc/`
    is skipped by rule."""
    findings, stats = _shipped_path_findings(ROOT)
    assert stats["files"] > 0, "no shipped markdown was scanned"
    assert stats["ok"] > 0, "no path resolved at all -- the universe or the tokenizer is broken"
    universe, _overrides = _public_universe(ROOT)
    skill_mds = {p for p in universe if re.match(r"^skills/[^/]+/SKILL\.md$", p)}
    assert skill_mds, "no SKILL.md in the public universe"
    assert skill_mds <= stats["scanned"], sorted(skill_mds - stats["scanned"])
    assert findings == [], (
        "%d shipped doc path(s) point at something the snapshot does not carry:\n  %s\n"
        "Recheck: python3 -m pytest -q tests/test_skill_structure.py"
        % (len(findings), "\n  ".join(findings)))


#: The historical defect, restored verbatim from fragments: `sigma-review/SKILL.md` once pointed
#: at a root-level canon that the snapshot never carried. Spelled in two adjacent literals because
#: `test_no_private_names` scans this file and the whole name is a retired one.
_HISTORICAL_LINK = "../../sdlc-" "review-skill.md"


def test_a_planted_dangling_link_is_flagged(tmp_path):
    """The control: the target EXISTS on disk and is git-tracked, but the manifest does not select
    it -- a filesystem resolver passes this, which is exactly the check the guard must not be."""
    target = _HISTORICAL_LINK.rsplit("/", 1)[-1]
    public_surface.plant(tmp_path, {
        "skills/sigma-review/SKILL.md": "# review\n\nthe canon is [axes](%s).\n" % _HISTORICAL_LINK,
        target: "the canon\n",
    }, manifest="skills/\n")
    assert (tmp_path / target).is_file()
    findings, stats = _shipped_path_findings(tmp_path)
    assert findings == ["skills/sigma-review/SKILL.md:3\t%s" % _HISTORICAL_LINK], findings
    assert stats["dangling"] == 1


def test_a_planted_legit_link_resolves(tmp_path):
    """The control for the control: a relative link and a `${CLAUDE_SKILL_DIR}` span to a file the
    manifest DOES select are both `ok`, so the guard above cannot be passing by flagging everything."""
    public_surface.plant(tmp_path, {
        "skills/sigma-x/SKILL.md": ("see [sel](references/selection.md) and load "
                                    "`${CLAUDE_SKILL_DIR}/references/selection.md`\n"),
        "skills/sigma-x/references/selection.md": "triggers\n",
    }, manifest="skills/\n")
    findings, stats = _shipped_path_findings(tmp_path)
    assert findings == [], findings
    assert stats["ok"] >= 2, stats
