"""Where a finding about SIGMA ITSELF goes (#1551) — and, far more importantly, where a finding
about the ADOPTER'S OWN PROJECT still goes.

The two directions are not symmetric and this file is written around that asymmetry. Filing a kit
bug on an adopter's board is today's behaviour, so a classifier that misses one costs nothing new.
WITHHOLDING a real project finding is worse than what we have now, so a classifier that over-reaches
costs the adopter work they will never see. Every ambiguous case below is therefore asserted to come
back `project`, and `test_classify_falls_towards_filing_locally_when_it_breaks` holds the direction
against the CODE rather than against the docstring.

The board-level assertions are made against the RECORDED `gh` ARGV of a real `GitHubSource`, not
against a report field or a stand-in's outcome string: "no issue was filed on their board" is a
claim about what was executed, and only the argv can answer it."""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
S = ROOT / "skills" / "agrim-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


upstream = _mod("upstream")
handoff = _mod("handoff")
ledger = _mod("ledger")
cross_repo = _mod("cross_repo")
sources = _mod("sources")

#: A real path under the installed plugin that exists in no adopter's tree. Asserted to exist below
#: (`test_the_fixture_path_is_real`) so this file can never silently stop testing anything by
#: naming a file that was renamed.
KIT_PATH = "skills/agrim-loop/scripts/loop.py"

ON = {"ledger": {"enabled": True, "actor": "amy"}}


def _config(**over):
    cfg = {"ledger": {"enabled": True, "actor": "amy", "handoff": {}},
           "discovery": {"source": "github",
                         "github": {"repo": "acme/widget", "assignee": "amy"}}}
    cfg["ledger"]["handoff"].update(over)
    return cfg


def _project(tmp_path, config=None):
    sdlc = tmp_path / ".sdlc"
    (sdlc / "state").mkdir(parents=True, exist_ok=True)
    (sdlc / "config.json").write_text(json.dumps(config or ON))
    return sdlc


class Gh:
    """A recording `gh` runner in BOTH shapes the two consumers use — `sources.GitHubSource` calls
    it as `run(args) -> stdout` and `cross_repo`/`upstream` call it as `run(args) -> (rc, out, err)`
    — so one recorder can hold the whole argv trail of a filing. `triple` is the second face."""

    def __init__(self, replies=None, fail=()):
        self.calls = []
        self.replies = replies or {}
        self.fail = fail

    def _answer(self, args):
        joined = " ".join(str(a) for a in args)
        for needle in self.fail:
            if needle in joined:
                raise RuntimeError("gh: HTTP 404: Not Found (simulated) for %s" % needle)
        for needle, reply in self.replies.items():
            if needle in joined:
                return reply
        return ""

    def __call__(self, args):
        self.calls.append([str(a) for a in args])
        return self._answer(args)

    def triple(self, args):
        self.calls.append([str(a) for a in args])
        try:
            return 0, self._answer(args), ""
        except RuntimeError as exc:
            return 1, "", str(exc)

    def matching(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def _github_source(sdlc, gh):
    return sources.GitHubSource(_config(), run=gh, sdlc_dir=str(sdlc))


def _addressed(sdlc, who="amy"):
    return ledger.addressed_to(ledger.read_all(sdlc), who)


def _withheld_notes(sdlc, who="amy"):
    """Only the notes THIS routing wrote. `create_tracked_issue` also writes its own bookkeeping
    note per call, in the finding's own area — pre-existing, unrelated, and one per call whatever
    the routing decided, so counting all of them would measure the wrong thing."""
    return [e for e in _addressed(sdlc, who) if e.get("area") == upstream.WITHHELD_DIRNAME]


# ------------------------------------------------------------------ the fixtures are real

def test_the_fixture_path_is_real():
    """A renamed kit file would turn every `kit` assertion below into a vacuous `project` one."""
    assert (upstream.plugin_root() / KIT_PATH).is_file()


def test_the_granted_verdict_is_cross_repos_own_word():
    """One vocabulary, not two: `upstream.GRANTED` is compared against a verdict `cross_repo`
    produces, so a rename there that missed this module would be a silent permanent `unknown`."""
    assert upstream.GRANTED == cross_repo.GRANTED and upstream.UNKNOWN == cross_repo.UNKNOWN


def test_the_repo_shape_is_cross_repos_own_pattern():
    """Copied, not imported (see the constant's own comment) — so it is pinned, not trusted. A
    tightening there must not leave this one quietly looser."""
    assert upstream._REPO_RE.pattern == cross_repo._REPO_RE.pattern


def test_a_newline_never_smuggles_a_second_argument_into_a_gh_call(tmp_path):
    cfg = _config(upstream_repo="Agrim-Intelligence/sigma\nrm -rf /")
    assert upstream.upstream_repo(cfg) is None


def test_the_plugin_root_is_the_tree_that_holds_the_manifest():
    root = upstream.plugin_root()
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / "skills").is_dir() and (root / "hooks").is_dir()


# ------------------------------------------------------------------ classify

def test_a_path_into_the_plugins_install_path_is_a_kit_finding(tmp_path):
    verdict = upstream.classify(_project(tmp_path), f"{KIT_PATH} drops sdlc:goal on a closed issue")
    assert verdict["origin"] == upstream.KIT and verdict["evidence"] == [KIT_PATH]


def test_an_absolute_path_into_the_install_path_is_a_kit_finding(tmp_path):
    absolute = str(upstream.plugin_root() / KIT_PATH)
    verdict = upstream.classify(_project(tmp_path), f"see {absolute}:1749 for the label handling")
    assert verdict["origin"] == upstream.KIT and verdict["evidence"] == [absolute]


def test_the_line_number_is_not_part_of_the_path(tmp_path):
    """`path.py:1749` is how every agent cites evidence; a token carrying the `:1749` resolves to
    nothing and the finding would read as the project's."""
    verdict = upstream.classify(_project(tmp_path), f"{KIT_PATH}:1749 mishandles the label")
    assert verdict["evidence"] == [KIT_PATH]


def test_a_path_that_also_exists_in_the_project_tree_is_the_projects_own(tmp_path):
    """BOTH halves of the test are load-bearing. A repo that happens to carry the same relative
    path owns its own copy of it, and this is the general form of the self-hosting case."""
    sdlc = _project(tmp_path)
    theirs = tmp_path / KIT_PATH
    theirs.parent.mkdir(parents=True)
    theirs.write_text("their own file")
    assert upstream.classify(sdlc, f"{KIT_PATH} is wrong")["origin"] == upstream.PROJECT


def test_a_young_repos_own_ci_finding_is_never_taken_from_it(tmp_path, monkeypatch):
    """B1, THE DEFECT THIS INVERTS. "There is no CI, add `.github/workflows/ci.yml`" is the most
    ordinary finding a YOUNG repository produces — and a young repository is precisely the one
    without a `.github/` yet. Under the shipped rule that path resolved in the kit, was absent from
    their tree, and was withheld from their own board; with an upstream configured, their CI bug
    was filed on the KIT's tracker. The eligible set is now the plugin's signature subtrees, so it
    is theirs whether or not they have the directory.

    The install tree is PLANTED (#2584): the signature dirs plus a real CI workflow file, so the
    collision is real on any install, including the public core, which ships no `.github/`."""
    sdlc = _project(tmp_path)
    collision = ".github/workflows/ci.yml"
    plugin = tmp_path.parent / (tmp_path.name + "-installed-plugin")
    for rel in [d + "/x" for d in upstream.SIGNATURE_DIRS] + [collision]:
        (plugin / rel).parent.mkdir(parents=True, exist_ok=True)
        (plugin / rel).write_text("x\n")
    monkeypatch.setattr(upstream, "plugin_root", lambda: plugin)
    assert (upstream.plugin_root() / collision).is_file()           # the collision is real
    assert not (tmp_path / ".github").exists()                      # and they are that young repo
    verdict = upstream.classify(
        sdlc, "CI never runs on PRs. Add .github/workflows/ci.yml so src/main.py is tested.")
    assert verdict["origin"] == upstream.PROJECT and verdict["evidence"] == []


def test_only_the_plugins_signature_subtrees_are_eligible_evidence(tmp_path):
    """847 of the kit's 984 tracked files sit under top-level names an ordinary repo has of its
    own, and every one of them was a live collision. Eligibility is the three that exist to hold a
    plugin — asserted against the real install tree, in BOTH directions, so neither half can rot
    into a name that no longer exists or quietly re-admit one that was removed."""
    sdlc = _project(tmp_path)
    for name in upstream.SIGNATURE_DIRS:
        assert (upstream.plugin_root() / name).is_dir(), name
    for eligible in ("skills/agrim-loop/scripts/loop.py", "hooks/decision_gate.py",
                     ".claude-plugin/plugin.json"):
        assert (upstream.plugin_root() / eligible).is_file(), eligible
        assert upstream.classify(sdlc, eligible)["origin"] == upstream.KIT, eligible
    for excluded in ("tests/test_docs.py", "docs/label-model.md", "contract/README.md",
                     "evals/run.py", "examples"):
        assert (upstream.plugin_root() / excluded).exists(), excluded
        assert upstream.classify(sdlc, excluded)["origin"] == upstream.PROJECT, excluded


def test_a_signature_directory_the_project_owns_is_still_never_taken_from_it(tmp_path):
    """`skills/` and `hooks/` are names a repository can perfectly well take for itself, so the
    project-owns-it conjunct survives the signature-set conjunct rather than being replaced by it —
    which is the whole of what keeps the remaining damaging residual to one narrow shape."""
    sdlc = _project(tmp_path)
    assert upstream.classify(sdlc, KIT_PATH)["origin"] == upstream.KIT
    (tmp_path / "skills").mkdir()
    assert upstream.classify(sdlc, KIT_PATH)["origin"] == upstream.PROJECT


def test_one_unresolvable_token_costs_itself_and_not_the_finding(tmp_path):
    """`~nosuchuser1234/notes.md` makes `expanduser` raise. It used to discard the whole evidence
    set wholesale — safe, but it threw away real evidence sitting beside it."""
    sdlc = _project(tmp_path)
    verdict = upstream.classify(sdlc, f"~nosuchuser1234/notes.md and {KIT_PATH}")
    assert verdict["origin"] == upstream.KIT and verdict["evidence"] == [KIT_PATH]


def test_a_bare_filename_is_never_evidence(tmp_path):
    """`loop.py record done` — the exact wording of one of the two observed cases. It names no
    path, so it is filed locally: the leak this narrows, honestly, rather than closing it by
    widening the signal until a project finding gets caught in it. `decompose_goal._META_BODY` is
    full of bare `handoff.py`/`work.py`/`loop.py` mentions about the ADOPTER's own goal."""
    verdict = upstream.classify(_project(tmp_path), "loop.py record done drops the label")
    assert verdict["origin"] == upstream.PROJECT and verdict["evidence"] == []


def test_the_decompose_template_is_not_a_kit_finding(tmp_path):
    """The real body of the real caller, rendered — a meta-issue about the adopter's OWN oversized
    goal. Withholding it would strand that goal's decomposition forever."""
    body = _mod("decompose_goal").render_meta_body(4242, 5)
    assert upstream.classify(_project(tmp_path), "Decompose #4242", "oversized goal",
                             body)["origin"] == upstream.PROJECT


def test_a_path_absent_from_the_kit_is_not_evidence_even_under_a_signature_dir(tmp_path):
    """Conjunct 1 on its own terms. `skills/` IS a signature directory and the adopter does not have
    one — so conjuncts 2 and 3 both pass, and only "does this actually exist in the kit?" stands
    between an invented path and a withheld finding."""
    sdlc = _project(tmp_path)
    assert not (upstream.plugin_root() / "skills" / "no" / "such" / "file.py").exists()
    assert upstream.classify(sdlc, "skills/no/such/file.py")["origin"] == upstream.PROJECT


def test_a_path_in_neither_tree_is_never_evidence(tmp_path):
    verdict = upstream.classify(_project(tmp_path), "src/checkout/session.ts double-charges")
    assert verdict["origin"] == upstream.PROJECT and verdict["evidence"] == []


def test_a_traversal_is_never_resolved(tmp_path):
    """`..` is a direction, not a location — resolving one would let a finding reach outside both
    roots and be measured against whatever happened to be there.

    `skills/../<kit path>` is the shape that actually EXERCISES the guard: it resolves to a real
    file under the install root, so dropping the guard would flip this to `kit`. The leading-`..`
    form resolves to nothing here and would answer `project` either way, which is why it cannot
    stand alone as the test for this rule."""
    for token in (f"skills/../{KIT_PATH}", f"../../{KIT_PATH}"):
        assert upstream.classify(_project(tmp_path), token)["origin"] == upstream.PROJECT


def test_an_ordinary_word_that_happens_to_name_a_kit_directory_is_not_evidence(tmp_path):
    """`hooks`, `tests`, `docs`, `skills` are all real directories at the install root AND ordinary
    English words. Without the separator rule they would each be "a path that exists under the
    plugin and not in this project" — and a sentence like this one, about the adopter's own test
    suite, would be withheld from their board."""
    verdict = upstream.classify(_project(tmp_path),
                                "the hooks and tests under docs are stale in skills too")
    assert verdict["origin"] == upstream.PROJECT and verdict["evidence"] == []


def test_a_url_is_not_a_path(tmp_path):
    """A github.com URL naming a kit file is real kit evidence and is still answered `project`.
    Stated as the deliberate direction rather than left as a surprise: a URL is not a location on
    this disk, and inventing a resolution for one is how a project finding gets withheld."""
    url = f"https://github.com/Agrim-Intelligence/sigma/blob/main/{KIT_PATH}"
    assert upstream.classify(_project(tmp_path), url)["origin"] == upstream.PROJECT


def test_the_self_hosting_repo_is_short_circuited_before_any_token_is_looked_at():
    """Sigma developing Sigma. An ABSOLUTE plugin path is used deliberately: the relative
    form would also answer `project` here through the path test alone, so it could not tell the
    short-circuit from the accident."""
    sdlc = upstream.plugin_root() / ".sdlc"
    verdict = upstream.classify(sdlc, str(upstream.plugin_root() / KIT_PATH))
    assert verdict["origin"] == upstream.PROJECT and verdict["self_hosted"] is True


def test_the_real_installed_layout_still_files_this_repos_own_findings_locally(tmp_path, monkeypatch):
    """THE LAYOUT AN ACTUAL DEVELOPMENT MACHINE HAS: the plugin under `~/.claude/plugins/cache/...`
    and the Sigma checkout somewhere else entirely, so neither tree contains the other and
    `self_hosted()` is FALSE. The self-hosting guarantee still holds — but through conjunct 3 (the
    project owns `skills/`), not through the short-circuit. Neither self-hosting test pinned this,
    and the docstring credited the wrong cover for it."""
    installed = tmp_path / "home" / ".claude" / "plugins" / "cache" / "sigma"
    (installed / "skills" / "agrim-loop" / "scripts").mkdir(parents=True)
    (installed / "skills" / "agrim-loop" / "scripts" / "loop.py").write_text("the installed copy")
    checkout = tmp_path / "work" / "sigma"
    (checkout / "skills" / "agrim-loop" / "scripts").mkdir(parents=True)
    (checkout / "skills" / "agrim-loop" / "scripts" / "loop.py").write_text("the checkout")
    sdlc = _project(checkout)
    monkeypatch.setattr(upstream, "plugin_root", lambda: installed)

    assert upstream.self_hosted(sdlc) is False              # the short-circuit does NOT fire
    verdict = upstream.classify(sdlc, f"{KIT_PATH} drops the label")
    assert verdict["origin"] == upstream.PROJECT            # and it is still theirs anyway
    assert verdict["self_hosted"] is False


def test_self_hosted_is_true_for_a_plugin_vendored_inside_the_project(tmp_path, monkeypatch):
    monkeypatch.setattr(upstream, "plugin_root", lambda: tmp_path / "vendor" / "sigma")
    assert upstream.self_hosted(_project(tmp_path)) is True


def test_classify_falls_towards_filing_locally_when_it_breaks(tmp_path, monkeypatch):
    """THE FAILURE-DIRECTION TEST. Anything at all going wrong inside the classifier must produce
    `project`, because withholding on a guess is the one outcome worse than filing on one. Asserted
    against a real raise, not against the docstring that promises it."""
    def boom():
        raise OSError("the plugin root is not readable")
    monkeypatch.setattr(upstream, "plugin_root", boom)
    verdict = upstream.classify(_project(tmp_path), f"{KIT_PATH} is broken")
    assert verdict["origin"] == upstream.PROJECT and verdict["evidence"] == []
    assert "could not be measured" in verdict["why"]


def test_a_project_answer_never_carries_evidence(tmp_path):
    """`evidence` is what tells a measured verdict from a defaulted one, so it must be empty on
    every `project` answer or a reader cannot make that distinction at all."""
    for text in ("loop.py", "src/x.ts", f"../{KIT_PATH}", ""):
        assert upstream.classify(_project(tmp_path), text)["evidence"] == []


def test_a_trailing_slash_is_not_part_of_the_directory(tmp_path):
    """`skills/agrim-loop/` is how a directory is written in prose."""
    verdict = upstream.classify(_project(tmp_path), "everything under skills/agrim-loop/ is stale")
    assert verdict["origin"] == upstream.KIT and verdict["evidence"] == ["skills/agrim-loop"]


def test_a_tilde_path_is_expanded_before_it_is_measured(tmp_path, monkeypatch):
    """`~/.claude/plugins/...` is the single most likely way an agent writes the install path, and
    unexpanded it resolves to nothing and reads as the project's."""
    monkeypatch.setenv("HOME", str(upstream.plugin_root().parent))
    token = "~/%s/%s" % (upstream.plugin_root().name, KIT_PATH)
    assert upstream.classify(_project(tmp_path), token)["origin"] == upstream.KIT


def test_the_scan_is_bounded_and_overflowing_it_files_locally(tmp_path):
    """`--body-file` takes a file of any size. The bound can only lose evidence, and losing
    evidence answers `project` — the direction this module fails in anyway."""
    sdlc = _project(tmp_path)
    filler = "x " * upstream._SCAN_CHARS
    assert upstream.classify(sdlc, filler + KIT_PATH)["origin"] == upstream.PROJECT
    assert upstream.classify(sdlc, KIT_PATH + " " + filler)["origin"] == upstream.KIT


# ------------------------------------------------------------------ route

def test_route_writes_nothing_at_all_for_a_project_finding(tmp_path):
    """An ordinary filing must be byte-identical to what it was before this module existed: no
    ledger line, no spill, no `gh` call, nothing."""
    sdlc = _project(tmp_path, _config())
    gh = Gh()
    report = upstream.route(sdlc, _config(), "12", "Checkout double-charges",
                            "src/checkout/session.ts retries the capture", None, run=gh.triple)
    assert report["withheld"] is False and report["origin"] == upstream.PROJECT
    assert ledger.read_all(sdlc) == [] and gh.calls == []
    assert not (sdlc / "state" / upstream.WITHHELD_DIRNAME).exists()


def test_a_withheld_finding_reaches_the_operator_through_the_ledger(tmp_path):
    """RETRIEVED, not merely written: read back off disk through the same `addressed_to` query the
    operator's own inbox uses."""
    sdlc = _project(tmp_path, _config())
    upstream.route(sdlc, _config(), "12", "Label handling",
                   f"{KIT_PATH} drops sdlc:goal on a closed issue", None, run=Gh().triple)
    mine = _addressed(sdlc, "amy")
    assert len(mine) == 1 and mine[0]["kind"] == "note"
    assert "withheld from this board" in mine[0]["why"]
    assert "needs forwarding by hand" in mine[0]["why"]


def test_the_withheld_note_is_a_note_and_never_a_handoff(tmp_path):
    """`backlog_check._ledger_signals` reads a `handoff` as a real block, so raising a withheld
    finding that way would PARK the very goal that found it."""
    sdlc = _project(tmp_path, _config())
    upstream.route(sdlc, _config(), "12", "t", f"{KIT_PATH} is wrong", None, run=Gh().triple)
    assert ledger.outstanding(ledger.read_all(sdlc)) == []


def test_the_full_text_survives_where_the_ledger_cap_would_have_cut_it(tmp_path):
    """`ledger._sanitize_free_text` caps `why` at 200 chars, so a finding whose evidence was only
    ever in the ledger HAS been silently dropped — whatever the ledger line says."""
    sdlc = _project(tmp_path, _config())
    body = "EVIDENCE-" + ("x" * 4000) + "-TAIL"
    report = upstream.route(sdlc, _config(), "12", "Label handling",
                            f"{KIT_PATH} drops the label", body, run=Gh().triple)
    assert len(_addressed(sdlc)[0]["why"]) <= ledger.FREE_TEXT_CAP
    spilled = pathlib.Path(report["spilled"]).read_text(encoding="utf-8")
    assert "-TAIL" in spilled and body in spilled


def test_a_deep_absolute_sdlc_dir_does_not_eat_the_ledger_line(tmp_path):
    """`withheld_path` builds off the `sdlc_dir` it was GIVEN and `project_root` resolves, so on a
    symlinked checkout (macOS `/var` -> `/private/var`, which `tmp_path` is) the two spellings of
    one directory do not relativise and the whole absolute path lands in the 200-char line, cutting
    off the one fact anybody has to act on. Found by running the shipped code, not by reading it."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    given = pathlib.Path(str(sdlc).replace("/private/var/", "/var/", 1))
    report = upstream.route(given, cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=_granted().triple)
    assert upstream._short(given, report["spilled"]) == ".sdlc/state/withheld/12.md"
    why = _addressed(sdlc)[0]["why"]
    assert len(why) <= ledger.FREE_TEXT_CAP
    assert "filed upstream as Agrim-Intelligence/sigma#77" in why
    assert "state/withheld/12.md" in why and "/private/var/" not in why


def test_an_untitled_finding_gets_a_title_from_its_own_first_line(tmp_path):
    """`handoff track` has no required `--title`, so every untitled finding would otherwise arrive
    on the maintainers' tracker under the same six words."""
    cfg = _upstream_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "12", None,
                   f"{KIT_PATH} drops sdlc:goal on a closed issue", None, run=gh.triple)
    posted = gh.matching("api", "-X", "POST", "repos/Agrim-Intelligence/sigma/issues")[0]
    title = posted[posted.index("-f") + 1]
    assert title == f"title={KIT_PATH} drops sdlc:goal on a closed issue"


def test_a_title_is_single_line_bounded_and_never_empty():
    assert upstream._heading("  a  title ", "why") == "a title"
    assert upstream._heading(None, "the" + chr(10) + "finding") == "the finding"
    assert upstream._heading("", "") == "Sigma finding"
    assert len(upstream._heading("x" * 500, "")) == upstream._HEADING_CHARS


def test_two_findings_on_one_goal_do_not_erase_each_other(tmp_path):
    sdlc = _project(tmp_path, _config())
    for n in ("first", "second"):
        upstream.route(sdlc, _config(), "12", n, f"{KIT_PATH} — {n}", None, run=Gh().triple)
    spilled = upstream.withheld_path(sdlc, "12").read_text(encoding="utf-8")
    assert "first" in spilled and "second" in spilled


def test_a_spill_that_cannot_be_written_still_reaches_the_operator(tmp_path, monkeypatch):
    sdlc = _project(tmp_path, _config())
    monkeypatch.setattr(upstream, "withheld_path", lambda *a: (_ for _ in ()).throw(OSError("ro")))
    report = upstream.route(sdlc, _config(), "12", "t", f"{KIT_PATH} is wrong", None,
                            run=Gh().triple)
    assert report["spilled"] is None and report["withheld"] is True
    assert len(_addressed(sdlc)) == 1


def test_an_unsafe_goal_never_becomes_a_spill_path(tmp_path):
    sdlc = _project(tmp_path, _config())
    with pytest.raises(ValueError):
        upstream.withheld_path(sdlc, "../../etc/passwd")
    with pytest.raises(ValueError):
        upstream.withheld_path(sdlc, "  ")


def test_the_escape_hatch_files_a_kit_finding_locally(tmp_path):
    cfg = _config(kit_findings="file-locally")
    sdlc = _project(tmp_path, cfg)
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None, run=Gh().triple)
    assert report["withheld"] is False and ledger.read_all(sdlc) == []


def test_a_typo_in_the_escape_hatch_keeps_the_safe_default(tmp_path):
    """Only the exact literal disables the routing — nothing should reach the mode that writes to
    somebody's board by misspelling something else."""
    cfg = _config(kit_findings="file_locally")
    assert upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                          run=Gh().triple)["withheld"] is True


# ------------------------------------------------------------------ upstream

def _upstream_cfg(**over):
    return _config(upstream_repo="Agrim-Intelligence/sigma", **over)


def _granted(number=77):
    return Gh(replies={"api user": "amy",
                       "api repos/Agrim-Intelligence/sigma": json.dumps(
                           {"permissions": {"push": True}}),
                       "repos/Agrim-Intelligence/sigma/issues": json.dumps(
                           {"number": number,
                            "html_url": "https://github.com/alice/kit/issues/%s"
                                        % number})})


def test_a_granted_upstream_repo_receives_the_finding(tmp_path):
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    gh = _granted()
    report = upstream.route(sdlc, cfg, "12", "Label handling",
                            f"{KIT_PATH} drops the label", "the detail", run=gh.triple)
    posted = gh.matching("api", "-X", "POST", "repos/Agrim-Intelligence/sigma/issues")
    assert len(posted) == 1 and report["issue"] == "77"
    assert report["withheld"] is True and report["verdict"] == upstream.GRANTED
    why = _addressed(sdlc)[0]["why"]
    assert "filed upstream as Agrim-Intelligence/sigma#77" in why
    assert len(why) <= ledger.FREE_TEXT_CAP


#: The reviewer's recorded POST body, verbatim — an ORDINARY finding, not a contrived one. Every
#: identifier in it crossed the boundary before B2: repo slug, goal id, board URL, a local absolute
#: path, two handles. The secret half was already correct and is asserted alongside so a regression
#: in either pass is caught by the same test.
LEAKY_FINDING = (
    "While working on goal ACME-421 in acme-corp/widgets, %s mishandles the label; our own "
    "lib/pay.py workaround is at /Users/bob/secretproj/lib/pay.py. Contact @bob-acme or "
    "bob.smith@acme-corp.example. "
    "token: ghp_%s . Board https://github.com/orgs/acme-corp/projects/9 tracks it." % (KIT_PATH, "a" * 30))


def _adopter_cfg():
    cfg = _upstream_cfg()
    cfg["discovery"]["github"]["repo"] = "acme-corp/widgets"
    return cfg


def test_the_upstream_wrapper_adds_no_identifying_metadata_of_its_own(tmp_path):
    """The NARROW claim, honestly labelled: the template `upstream_body` wraps the finding in names
    nothing about the caller. This was never the real risk — see the test below for that — but it
    is what M12 pins, so it stays."""
    cfg = _upstream_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "4242", "Label handling",
                   f"{KIT_PATH} drops the label", "detail", run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    assert "acme/widget" not in posted and "4242" not in posted
    assert KIT_PATH in posted


def test_the_adopters_mechanical_identifiers_never_cross_the_boundary(tmp_path):
    """B2, THE REAL RISK. `upstream_body` adds nothing — and passes the caller's own `title`/`why`/
    `body` through verbatim, which is where the repo slug, the goal id, the board URL, the local
    absolute path and the handles actually were. Asserted on the recorded POST argv, against the
    reviewer's own measured sample."""
    cfg = _adopter_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "ACME-421", None, LEAKY_FINDING, None,
                   run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    for identifier in ("acme-corp/widgets", "acme-corp", "ACME-421", "@bob-acme",
                       "bob.smith@acme-corp.example",
                       "/Users/bob/secretproj/lib/pay.py",
                       "https://github.com/orgs/acme-corp/projects/9"):
        assert identifier not in posted, identifier
    assert "ghp_" + "a" * 30 not in posted and "[REDACTED" in posted   # the secret half, still
    assert KIT_PATH in posted                                          # the finding still readable


def test_an_email_address_is_masked_whole_rather_than_cut_in_half(tmp_path):
    """`_HANDLE_RE` refuses an `@` preceded by a word character, precisely so it cannot cut an
    address in two — which leaves the whole address intact unless something else takes it. It is as
    mechanical an identifier as a repo slug, and a promise naming handles but not addresses would
    be true only by careful reading."""
    cfg = _adopter_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "ACME-421", None,
                   f"{KIT_PATH} — ask bob.smith@acme-corp.example", None, run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    assert "bob.smith@acme-corp.example" not in posted and "acme-corp" not in posted
    assert "[email removed]" in posted
    # NOT half-masked: no fragment of the address may survive under a replacement that reads as
    # though it had been handled — the exact shape an earlier mask ordering produced.
    assert "bob.smith" not in posted and "@[" not in posted


def test_a_link_to_the_destination_repo_is_the_one_url_kept(tmp_path):
    """The one link class that is about the kit rather than about the reporter, and the only one
    that makes a cross-reference possible at all."""
    cfg = _adopter_cfg()
    # #2730: the destination moves WITH the link (`upstream.py` keeps a URL only when it starts
    # `https://github.com/<upstream_repo>/`), to a fictional slug no shipped test spells otherwise.
    cfg["ledger"]["handoff"]["upstream_repo"] = "alice/kit"
    gh = Gh(replies={"api user": "amy",
                     "api repos/alice/kit": json.dumps({"permissions": {"push": True}}),
                     "repos/alice/kit/issues": json.dumps(
                         {"number": 77, "html_url": "https://github.com/alice/kit/issues/77"})})
    upstream.route(_project(tmp_path, cfg), cfg, "ACME-421", None,
                   f"{KIT_PATH} — see https://github.com/alice/kit/issues/1 and "
                   "https://acme-corp.example/internal/runbook", None, run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST", "repos/alice/kit/issues")[0])
    assert "https://github.com/alice/kit/issues/1" in posted
    assert "acme-corp.example" not in posted and "[link removed]" in posted


def test_the_issue_says_plainly_that_the_prose_is_not_deidentified(tmp_path):
    """Prose is not de-identifiable, so the promise that survives has to be the one that can be
    kept — and it has to be on the ISSUE, where the maintainer deciding what to quote reads it."""
    cfg = _adopter_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "ACME-421", None, LEAKY_FINDING, None,
                   run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    assert upstream.FORWARDING_NOTICE in posted


def test_no_surviving_text_promises_more_than_the_masks_deliver():
    """The sentence an adopter reads before enabling an outbound write must be true. It said the
    issue "carries NOTHING that identifies you" while forwarding their prose verbatim; a false
    promise at that moment is worse than no promise, so the absolute wording may not come back —
    in the module, in the adopter-facing config template, or on the issue itself."""
    banned = ("CARRIES NOTHING THAT IDENTIFIES", "carries NOTHING that identifies",
              "nothing that identifies you")
    module = (S / "upstream.py").read_text(encoding="utf-8")
    template = (ROOT / "skills" / "agrim-init" / "templates"
                / "config.json.tmpl").read_text(encoding="utf-8")
    for text, where in ((module, "upstream.py"), (template, "config.json.tmpl"),
                        (upstream.FORWARDING_NOTICE, "the issue body")):
        for phrase in banned:
            assert phrase not in text, f"{where} still promises the absolute: {phrase!r}"
    assert "not de-identifiable" in upstream.FORWARDING_NOTICE


def test_a_short_goal_id_is_left_alone(tmp_path):
    """A one- or two-character goal is a bare number that occurs in ordinary prose; masking it
    would damage the finding to hide nothing, since an issue number names no repository."""
    cfg = _adopter_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "12", None,
                   f"{KIT_PATH} drops 12 of the 30 labels", None, run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    assert "12 of the 30 labels" in posted


def test_an_unknown_verdict_never_writes_upstream(tmp_path):
    """A drifted `gh` account: `discovery.github.assignee` pins `amy`, `gh` answers `bob`. The
    identity is unconfirmed, so no verdict survives — and `unknown` is not a soft `granted`."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    gh = _granted()
    gh.replies["api user"] = "bob"
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None, run=gh.triple)
    assert gh.matching("api", "-X", "POST") == []
    assert report["verdict"] == upstream.UNKNOWN and report["issue"] is None
    assert "could NOT be filed upstream" in _addressed(sdlc)[0]["why"]
    assert any("no confirmed write access" in w for w in report["warnings"])


def test_a_denied_verdict_never_writes_upstream(tmp_path):
    cfg = _upstream_cfg()
    gh = Gh(replies={"api user": "amy"}, fail=("api repos/Agrim-Intelligence/sigma",))
    report = upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=gh.triple)
    assert gh.matching("api", "-X", "POST") == [] and report["issue"] is None
    assert report["verdict"] == cross_repo.DENIED


def test_an_upstream_repo_that_is_not_a_repo_is_never_asked_about(tmp_path):
    cfg = _upstream_cfg()
    cfg["ledger"]["handoff"]["upstream_repo"] = "sigma"
    gh = _granted()
    report = upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=gh.triple)
    assert upstream.upstream_repo(cfg) is None and report["upstream"] is None and gh.calls == []


def test_a_refused_upstream_write_is_ledgered_and_never_dropped(tmp_path):
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    gh = _granted()
    gh.fail = ("-X POST",)
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None, run=gh.triple)
    assert report["issue"] is None and len(_addressed(sdlc)) == 1
    assert any("could not file this kit finding" in w for w in report["warnings"])


def test_an_upstream_answer_with_no_issue_number_is_not_a_filing(tmp_path):
    cfg = _upstream_cfg()
    gh = _granted()
    gh.replies["repos/Agrim-Intelligence/sigma/issues"] = "{}"
    report = upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=gh.triple)
    assert report["issue"] is None and report["warnings"]


# ------------------------------------------------------------------ handoff, on the recorded argv

def _file(sdlc, why, gh, body=None, cfg=None, **over):
    kwargs = {"same_area": True, "immediately_actionable": False, "blocks_goal": False}
    kwargs.update(over)
    return handoff.create_tracked_issue(sdlc, cfg or _config(), "12", "coordination", why,
                                        body=body, source=_github_source(sdlc, gh), **kwargs)


def test_a_kit_finding_is_never_filed_on_the_adopters_board(tmp_path):
    """THE HEADLINE. Asserted on the argv: nothing that would open an issue on `acme/widget` was
    ever executed — not the create, not the narrative comment on the goal."""
    sdlc = _project(tmp_path, _config())
    gh = Gh()
    report = _file(sdlc, f"{KIT_PATH} drops sdlc:goal when it records a closed issue done", gh)
    assert gh.matching("issue", "create") == []
    assert gh.matching("issue", "comment") == []
    assert report["issue"] is None and report["routing"]["origin"] == upstream.KIT


def test_the_withheld_finding_still_reaches_a_human(tmp_path):
    sdlc = _project(tmp_path, _config())
    report = _file(sdlc, f"{KIT_PATH} drops sdlc:goal on a closed issue", Gh())
    mine = _addressed(sdlc, "amy")
    assert any("withheld from this board" in e["why"] for e in mine)
    assert any(w.startswith("withheld from this board") for w in report["warnings"])


def test_withholding_is_not_a_failed_filing(tmp_path):
    """#1203's exit-code contract: `issue_attempted` must stay False, or both CLI verbs exit 1 and
    a deliberate, successful routing decision reads as a broken `gh`."""
    sdlc = _project(tmp_path, _config())
    report = _file(sdlc, f"{KIT_PATH} drops the label", Gh())
    assert report["issue_attempted"] is False


def test_a_project_finding_is_filed_on_the_adopters_board_exactly_as_before(tmp_path):
    """The regression that matters most: the classifier widening would be invisible without this."""
    sdlc = _project(tmp_path, _config())
    gh = Gh(replies={"issue create": "https://github.com/acme/widget/issues/61"})
    report = _file(sdlc, "src/checkout/session.ts retries the capture and double-charges", gh)
    created = gh.matching("issue", "create")
    assert len(created) == 1 and "--repo" in created[0]
    assert report["issue"] == "61" and report["routing"]["origin"] == upstream.PROJECT


def test_the_self_hosting_repo_still_files_its_own_kit_findings_normally(tmp_path, monkeypatch):
    """Sigma developing Sigma — a classifier that withheld this repo's own findings from
    this repo's own board would be a self-inflicted outage."""
    sdlc = _project(tmp_path, _config())
    monkeypatch.setattr(handoff._upstream(), "plugin_root", lambda: tmp_path)
    gh = Gh(replies={"issue create": "https://github.com/acme/widget/issues/61"})
    report = _file(sdlc, f"{KIT_PATH} drops sdlc:goal on a closed issue", gh)
    assert len(gh.matching("issue", "create")) == 1 and report["issue"] == "61"
    assert report["routing"]["self_hosted"] is True


def test_a_cross_area_kit_handoff_is_withheld_too_and_says_it_is_not_blocked(tmp_path):
    """`hand_off()` pins `blocks_goal=True`. Withheld, the "Blocked by" marker never lands, so the
    goal is NOT actually blocked — and the pre-existing guard must say so rather than leave the
    caller believing it is."""
    sdlc = _project(tmp_path, _config())
    gh = Gh()
    report = _file(sdlc, f"{KIT_PATH} drops the label", gh, blocks_goal=True,
                   immediately_actionable=True)
    assert gh.matching("issue", "create") == []
    assert any("NOT actually blocked" in w for w in report["warnings"])


def test_hand_off_withholds_a_kit_dependency_and_still_records_it_for_the_owner(tmp_path):
    """`hand_off()` is `create_tracked_issue` with all three axes pinned, so it inherits the
    routing. Its own documented degraded path applies unchanged: no issue could be opened, and the
    ledger entry is written regardless, because a hand-off nobody can see is the bug it exists to
    fix."""
    sdlc = _project(tmp_path, _config())
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("/engine/ @eng-owner" + chr(10))
    gh = Gh()
    report = handoff.hand_off(sdlc, _config(), "12", "engine", f"{KIT_PATH} drops the label",
                              source=_github_source(sdlc, gh))
    assert gh.matching("issue", "create") == [] and report["issue"] is None
    kinds = {e["kind"]: e for e in ledger.read_all(sdlc)}
    assert kinds["handoff"]["to"] == "eng-owner" and not kinds["handoff"].get("issue")
    assert "withheld from this board" in kinds["note"]["why"] and kinds["note"]["to"] == "amy"


def test_a_local_backlog_with_no_issue_seam_still_withholds_and_records(tmp_path):
    """A source that cannot open issues never reached the classifier's decision before; the routing
    runs ahead of the source check so the ledger note is written either way."""
    sdlc = _project(tmp_path, _config())

    class NoIssues:
        pass

    report = handoff.create_tracked_issue(sdlc, _config(), "12", "coordination",
                                          f"{KIT_PATH} drops the label", same_area=True,
                                          immediately_actionable=False, blocks_goal=False,
                                          source=NoIssues())
    assert report["routing"]["withheld"] is True and _addressed(sdlc)


def test_the_body_is_classified_too_not_just_the_summary(tmp_path):
    """`handoff.py track --body-file` is how a rich finding carries its evidence; a classifier
    reading only `--why` would miss every one of them."""
    sdlc = _project(tmp_path, _config())
    gh = Gh()
    _file(sdlc, "the label handling is wrong", gh, body=f"Evidence: {KIT_PATH} line 1749.")
    assert gh.matching("issue", "create") == []


def test_the_rendered_template_is_never_what_gets_classified(tmp_path):
    """Classification runs over the CALLER's own words, so this function can never end up judging
    its own boilerplate — and adding a kit path to a template can never start withholding every
    filing that uses it."""
    sdlc = _project(tmp_path, _config())
    gh = Gh(replies={"issue create": "https://github.com/acme/widget/issues/61"})
    monkey = handoff._tracked_issue_body
    try:
        handoff._tracked_issue_body = lambda *a: f"see {KIT_PATH}"
        _file(sdlc, "src/checkout/session.ts double-charges", gh)
    finally:
        handoff._tracked_issue_body = monkey
    assert len(gh.matching("issue", "create")) == 1


def test_the_ledger_line_leads_with_where_the_finding_went(tmp_path):
    """THE CAP DELETES, IT DOES NOT SHORTEN. `ledger` truncates `why` to FREE_TEXT_CAP from the
    head, so a line that opens with the reasoning loses the destination — which is the only part
    anybody has to act on. Pinned with a deep checkout path and eight evidence paths, the shape
    that actually overflows."""
    deep = tmp_path / ("nested/" * 12)
    deep.mkdir(parents=True)
    cfg = _upstream_cfg()
    sdlc = _project(deep, cfg)
    evidence = " ".join("skills/agrim-loop/scripts/%s" % f for f in
                        ("loop.py", "work.py", "ledger.py", "handoff.py", "sources.py",
                         "triage.py", "mirror.py", "state.py"))
    upstream.route(sdlc, cfg, "12", "Label handling", evidence, None, run=_granted().triple)
    why = _addressed(sdlc)[0]["why"]
    assert len(why) <= ledger.FREE_TEXT_CAP
    assert "filed upstream as Agrim-Intelligence/sigma#77" in why


def test_overflowing_evidence_is_counted_not_silently_dropped(tmp_path):
    report = upstream._routed(evidence=["a", "b", "c", "d"])
    assert upstream._evidence_clause(report) == "a, b (+2 more)"


def test_the_full_evidence_list_is_always_in_the_spill_file(tmp_path):
    """What the ledger line cannot afford to name must exist somewhere retrievable, or the cap is
    a silent drop after all."""
    sdlc = _project(tmp_path, _config())
    paths = ["skills/agrim-loop/scripts/%s" % f for f in
             ("loop.py", "work.py", "ledger.py", "handoff.py")]
    report = upstream.route(sdlc, _config(), "12", "t", " ".join(paths), None, run=Gh().triple)
    spilled = pathlib.Path(report["spilled"]).read_text(encoding="utf-8")
    assert all(one in spilled for one in paths)


def test_the_upstream_route_is_reachable_end_to_end_from_a_filing(tmp_path):
    """`upstream_run` threaded through `create_tracked_issue`: one filing, one kit finding, an issue
    on the KIT's tracker and nothing at all on the adopter's."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    board, kit = Gh(), _granted()
    report = handoff.create_tracked_issue(
        sdlc, cfg, "12", "coordination", f"{KIT_PATH} drops sdlc:goal on a closed issue",
        same_area=True, immediately_actionable=False, blocks_goal=False,
        source=_github_source(sdlc, board), upstream_run=kit.triple)
    assert board.matching("issue", "create") == []
    assert len(kit.matching("api", "-X", "POST", "repos/Agrim-Intelligence/sigma/issues")) == 1
    assert report["routing"]["issue"] == "77" and report["issue"] is None


def test_a_spill_outside_the_project_tree_is_still_named_in_full(tmp_path):
    """`_short` relativises for the cap's sake; a path it cannot relativise is reported whole
    rather than silently blanked."""
    assert upstream._short(tmp_path / ".sdlc", "/elsewhere/withheld/12.md") == \
        "/elsewhere/withheld/12.md"
    assert upstream._short(tmp_path / ".sdlc", None) == "(not written)"


def test_a_runner_that_dies_filed_nothing_and_says_so(tmp_path):
    def boom(_args):
        raise RuntimeError("gh is not installed")
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    gh = _granted()
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=lambda a: boom(a) if "-X" in a else gh.triple(a))
    assert report["issue"] is None and len(_addressed(sdlc)) == 1
    assert any("could not be run" in w for w in report["warnings"])


def test_an_unparseable_upstream_answer_is_not_a_filing(tmp_path):
    cfg = _upstream_cfg()
    gh = _granted()
    gh.replies["repos/Agrim-Intelligence/sigma/issues"] = "<html>502</html>"
    report = upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=gh.triple)
    assert report["issue"] is None and report["warnings"]


def test_an_access_check_that_raises_is_never_a_grant(tmp_path, monkeypatch):
    cfg = _upstream_cfg()
    monkeypatch.setattr(upstream._cross_repo(), "identity",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    report = upstream.route(_project(tmp_path, cfg), cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                            run=_granted().triple)
    assert report["verdict"] == upstream.UNKNOWN and report["issue"] is None


def test_a_malformed_config_is_survived_rather_than_raised_through(tmp_path, capsys):
    """`ledger.settings` is `(config or {}).get("ledger")` — correct for `None` and an
    `AttributeError` for every other non-mapping, which is exactly the hand-edited shape a knob
    read has to survive. The finding is still classified, still withheld, still spilled and still
    on the console; only the ledger write, which genuinely needs a usable config, is lost — and it
    says so rather than going quiet."""
    for n, config in enumerate(("not a config", ["ledger"], 7)):
        # a distinct goal per iteration: the same finding on the same goal is a REPEAT, which the
        # withheld record now correctly declines to write a second time
        report = upstream.route(_project(tmp_path), config, "g%d" % n, "t",
                                f"{KIT_PATH} is wrong", None, run=Gh().triple)
        assert report["withheld"] is True and report["spilled"]
    assert "could not be recorded in the ledger" in capsys.readouterr().err


def test_route_never_raises_and_a_crashed_route_files_locally(tmp_path, monkeypatch, capsys):
    """THE OUTER GUARD, and the direction it answers in. `never raises` has to be total, and a
    routing decision that crashed has decided nothing — so the finding goes where it would have
    gone before this module existed, which is here."""
    monkeypatch.setattr(upstream, "classify",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    report = upstream.route(_project(tmp_path), _config(), "12", "t", f"{KIT_PATH} is wrong",
                            None, run=Gh().triple)
    assert report["withheld"] is False and "boom" in report["why"]
    assert "could not run" in capsys.readouterr().err


def test_a_ledger_that_cannot_be_written_still_says_so_on_the_console(tmp_path, capsys):
    sdlc = _project(tmp_path, _config())
    # `upstream.ledger`, not this file's `ledger`: every module here is loaded by file path, so
    # each import is a distinct instance and patching the wrong one patches nothing.
    monkeypatch_target = upstream.ledger.safe_append
    try:
        upstream.ledger.safe_append = lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
        report = upstream.route(sdlc, _config(), "12", "t", f"{KIT_PATH} is wrong", None,
                                run=Gh().triple)
    finally:
        upstream.ledger.safe_append = monkeypatch_target
    err = capsys.readouterr().err
    assert report["entry"] is None and "could not be recorded in the ledger" in err
    assert "withheld from this board" in err


def test_a_secret_in_the_finding_is_never_published_upstream(tmp_path):
    """The finding's own text was written about the kit but passed through nobody's review on the
    way to a repository this project does not own."""
    cfg = _upstream_cfg()
    gh = _granted()
    upstream.route(_project(tmp_path, cfg), cfg, "12", "Label handling",
                   f"{KIT_PATH} is wrong", "run it with token=hunter2secretvalue and ghp_"
                   + "a" * 30, run=gh.triple)
    posted = " ".join(gh.matching("api", "-X", "POST",
                                  "repos/Agrim-Intelligence/sigma/issues")[0])
    assert "hunter2secretvalue" not in posted and "ghp_" + "a" * 30 not in posted
    assert "[REDACTED" in posted


def test_nothing_is_published_when_it_could_not_be_redacted(tmp_path, monkeypatch):
    """THE ONE PLACE THAT DOES NOT FAIL OPEN. Failing open here would mean publishing unredacted
    text into somebody else's repository; the ledger was always the fallback and loses nothing."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    monkeypatch.setattr(upstream.ledger, "_scrub_module", lambda: None)
    gh = _granted()
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None, run=gh.triple)
    assert gh.matching("api", "-X", "POST") == [] and report["issue"] is None
    assert any("redaction pass is unavailable" in w for w in report["warnings"])
    assert len(_addressed(sdlc)) == 1


def test_the_upstream_body_names_the_plugin_version_or_says_it_does_not_know():
    assert upstream._version() in (json.loads(
        (upstream.plugin_root() / ".claude-plugin" / "plugin.json").read_text())["version"],
        "unknown")
    assert "Sigma" in upstream.upstream_body("why", "body")


def test_the_cli_line_does_not_read_as_a_failed_filing(tmp_path, capsys, monkeypatch):
    """`track` printing "(no issue opened)" and nothing else makes a deliberate routing decision
    look identical to a broken `gh` on the one line a caller actually reads."""
    sdlc = _project(tmp_path, _config())
    monkeypatch.setattr(handoff.sources, "get_source",
                        lambda *a, **k: _github_source(sdlc, Gh()))
    rc = handoff.main(["handoff.py", "track", str(sdlc), "12", "--area", "coordination",
                       "--why", f"{KIT_PATH} drops the label", "--queue", "queued",
                       "--assignee", "same-area", "--blocks", "no"])
    out = capsys.readouterr()
    assert rc == 0 and "withheld (a Sigma finding)" in out.out
    assert "raised in the ledger for you to forward" in out.out


def test_an_ordinary_filing_prints_exactly_what_it_always_did(tmp_path, capsys, monkeypatch):
    sdlc = _project(tmp_path, _config())
    gh = Gh(replies={"issue create": "https://github.com/acme/widget/issues/61"})
    monkeypatch.setattr(handoff.sources, "get_source", lambda *a, **k: _github_source(sdlc, gh))
    handoff.main(["handoff.py", "track", str(sdlc), "12", "--area", "coordination",
                  "--why", "src/checkout/session.ts double-charges", "--queue", "queued",
                  "--assignee", "same-area", "--blocks", "no"])
    out = capsys.readouterr().out
    assert "withheld" not in out and "as #61" in out


def test_a_young_repos_ci_finding_reaches_its_own_board_and_not_the_kits(tmp_path):
    """The same defect, measured where it actually bit: through a real filing, with an upstream
    repo configured, on the recorded argv of both boards."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    board, kit = Gh(replies={"issue create": "https://github.com/acme/widget/issues/61"}), _granted()
    report = handoff.create_tracked_issue(
        sdlc, cfg, "12", "coordination",
        "CI never runs on PRs. Add .github/workflows/ci.yml so src/main.py is tested.",
        same_area=True, immediately_actionable=False, blocks_goal=False,
        source=_github_source(sdlc, board), upstream_run=kit.triple)
    assert len(board.matching("issue", "create")) == 1 and report["issue"] == "61"
    assert kit.matching("api", "-X", "POST") == []
    assert report["routing"]["withheld"] is False


# ------------------------------------------------------------------ repeats

def test_the_same_finding_twice_writes_once(tmp_path):
    """THE ELEVATED FINDING. `_duplicate_search` lives inside the branch this path skips, so the
    withheld route was the one filing route in the kit with no dedup at all: every recurrence
    appended to the spill, added a ledger note, and opened ANOTHER issue on the maintainers'
    tracker. Unbounded creation on somebody else's board is the exact problem this goal exists to
    end — relocating it from the adopter's board to the kit's would not have ended it."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    text = f"{KIT_PATH} drops sdlc:goal on a closed issue"
    first = upstream.route(sdlc, cfg, "12", "Label handling", text, None, run=_granted().triple)
    gh = _granted()
    second = upstream.route(sdlc, cfg, "12", "Label handling", text, None, run=gh.triple)

    assert first["issue"] == "77" and second["repeat"] is True
    assert second["withheld"] is True                       # still never the adopter's board
    assert gh.matching("api", "-X", "POST") == []           # and never a second upstream issue
    assert len(_addressed(sdlc)) == 1                       # one ledger note, not two
    assert upstream.withheld_path(sdlc, "12").read_text().count("\n## ") == 1
    assert second["duplicate_of"] == "Agrim-Intelligence/sigma#77"
    assert any("already withheld" in w for w in second["warnings"])


def test_a_repeat_never_touches_the_adopters_board_either(tmp_path):
    """Through the real filing path, on the recorded argv of both boards."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    why = f"{KIT_PATH} drops the label"
    for _ in range(2):
        board, kit = Gh(), _granted()
        handoff.create_tracked_issue(sdlc, cfg, "12", "coordination", why, same_area=True,
                                     immediately_actionable=False, blocks_goal=False,
                                     source=_github_source(sdlc, board), upstream_run=kit.triple)
        assert board.matching("issue", "create") == []
    assert len(_withheld_notes(sdlc)) == 1


def test_a_genuinely_different_finding_on_the_same_goal_still_files(tmp_path):
    """Dedup that swallows a second, real finding would be worse than none."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    upstream.route(sdlc, cfg, "12", "one", f"{KIT_PATH} drops the label", None,
                   run=_granted().triple)
    gh = _granted()
    other = upstream.route(sdlc, cfg, "12", "two", "hooks/decision_gate.py denies a valid edit",
                           None, run=gh.triple)
    assert other["repeat"] is False and len(gh.matching("api", "-X", "POST")) == 1
    assert len(_addressed(sdlc)) == 2


def test_the_fingerprint_ignores_formatting_but_not_field_boundaries():
    same = upstream.fingerprint("A Title", "the  why", "body")
    assert upstream.fingerprint("a title", "the why  ", " body ") == same
    assert upstream.fingerprint("A Titlethe why", "", "body") != same


def test_the_per_goal_upstream_cap_bounds_creation_absolutely(tmp_path):
    """The exact-repeat index cannot see the same defect reworded on each pass of a long run. The
    cap can. Past it the finding is still spilled and still ledgered — only the outbound write
    stops, and it says why."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    posts = 0
    for n in range(upstream._UPSTREAM_PER_GOAL_CAP + 2):
        gh = _granted(number=100 + n)
        report = upstream.route(sdlc, cfg, "12", "finding %d" % n,
                                f"{KIT_PATH} is wrong in way {n}", None, run=gh.triple)
        posts += len(gh.matching("api", "-X", "POST"))
        assert report["withheld"] is True and report["spilled"]
    assert posts == upstream._UPSTREAM_PER_GOAL_CAP
    assert report["capped"] is True and any("which is the cap" in w for w in report["warnings"])
    assert len(_addressed(sdlc)) == upstream._UPSTREAM_PER_GOAL_CAP + 2   # nothing was dropped
    assert "the per-goal upstream cap is reached" in _addressed(sdlc)[-1]["why"]


def test_an_unreadable_record_stops_the_write_and_loses_nothing(tmp_path):
    """ABSENT and UNREADABLE are not the same failure. Absent is the ordinary first run; unreadable
    means this goal's history cannot be established, and reading that as an empty history would
    re-file everything it had already sent — the unbounded-creation failure the record exists to
    stop. So it refuses the outbound write only."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    path = upstream.index_path(sdlc, "12")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json")
    gh = _granted()
    report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None, run=gh.triple)
    assert gh.matching("api", "-X", "POST") == [] and report["issue"] is None
    assert report["spilled"] and len(_addressed(sdlc)) == 1
    assert any("could not be read" in w for w in report["warnings"])


def test_a_record_with_a_foreign_schema_is_not_read_as_history(tmp_path):
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    path = upstream.index_path(sdlc, "12")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "something/else@9", "findings": {}}))
    gh = _granted()
    assert upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                          run=gh.triple)["issue"] is None
    assert gh.matching("api", "-X", "POST") == []


def test_a_record_that_cannot_be_written_costs_a_repeat_and_never_the_filing(tmp_path, capsys):
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    saved = upstream.index_path
    try:
        upstream.index_path = lambda *a: (_ for _ in ()).throw(OSError("read-only"))
        report = upstream.route(sdlc, cfg, "12", "t", f"{KIT_PATH} is wrong", None,
                                run=_granted().triple)
    finally:
        upstream.index_path = saved
    assert report["withheld"] is True and report["spilled"] and len(_addressed(sdlc)) == 1
    assert "history could not be established" in capsys.readouterr().err or report["warnings"]


# ------------------------------------------------------------------ isolation (B3)

def _no_network(monkeypatch):
    """Count entries into the real `gh`, rather than raising from inside it. Every call site around
    the access check swallows exceptions by design, so a `pytest.fail` planted there is caught and
    reported as `unknown` -- the tripwire would be silently disarmed by the very fail-safe it is
    meant to be measuring past."""
    reached = []

    def door(args, *rest, **kw):
        reached.append([str(a) for a in args])
        return 1, "", "the real gh must not be reached from a test"

    monkeypatch.setattr(upstream._cross_repo(), "_run_gh", door)
    return reached


def test_injecting_run_alone_can_never_reach_the_network(tmp_path, monkeypatch):
    """B3, THE REGRESSION THAT OPENED A LIVE ISSUE. The standing isolation idiom here is
    `run=<fake>`. `create_tracked_issue` grew a SECOND runner for the upstream write, and a caller
    that injected only `run` — knowing nothing of the second — had its fully-faked filing perform a
    real `POST /repos/<upstream>/issues`. The injected runner recorded zero calls while a genuine
    issue was created on this repository.

    The assertion is on entries into the real `gh` and on what the FAKE saw, in that order: the
    first is the safety property, the second proves the call happened at all so the test cannot
    pass by the route quietly doing nothing."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    reached = _no_network(monkeypatch)
    fake = _granted()                      # stdout-shaped: `run`'s contract, not `upstream_run`'s
    report = handoff.create_tracked_issue(
        sdlc, cfg, "12", "coordination", f"{KIT_PATH} drops sdlc:goal on a closed issue",
        same_area=True, immediately_actionable=False, blocks_goal=False,
        source=_github_source(sdlc, Gh()), run=fake)

    assert reached == [], f"the upstream write reached the real gh: {reached}"
    assert len(fake.matching("api", "-X", "POST",
                             "repos/Agrim-Intelligence/sigma/issues")) == 1
    assert report["routing"]["issue"] == "77"


def test_hand_off_inherits_the_same_isolation(tmp_path, monkeypatch):
    """`hand_off()` passes `run` straight through, so the adaptation has to happen below it."""
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    reached = _no_network(monkeypatch)
    fake = _granted()
    handoff.hand_off(sdlc, cfg, "12", "engine", f"{KIT_PATH} drops the label",
                     source=_github_source(sdlc, Gh()), run=fake)
    assert reached == []
    assert fake.matching("api", "-X", "POST", "repos/Agrim-Intelligence/sigma/issues")


def test_an_explicit_upstream_run_still_wins_over_the_adapted_one(tmp_path, monkeypatch):
    cfg = _upstream_cfg()
    sdlc = _project(tmp_path, cfg)
    reached = _no_network(monkeypatch)
    text_only, triple = Gh(), _granted()
    handoff.create_tracked_issue(
        sdlc, cfg, "12", "coordination", f"{KIT_PATH} drops the label", same_area=True,
        immediately_actionable=False, blocks_goal=False, source=_github_source(sdlc, Gh()),
        run=text_only, upstream_run=triple.triple)
    assert reached == [] and triple.matching("api", "-X", "POST")
    assert text_only.matching("api", "-X", "POST") == []


def test_the_adapter_never_manufactures_a_grant():
    """A stdout runner discards the exit code, so every failure it can express has to land on
    `unknown` -- never on a payload that reads as permission."""
    def raising(_args):
        raise RuntimeError("gh: HTTP 500")
    code, out, err = upstream.adapt_runner(raising)(["api", "user"])
    assert code == 1 and out == "" and "500" in err
    code, out, err = upstream.adapt_runner(lambda a: "amy")(["api", "user"])
    assert (code, out, err) == (0, "amy", "")


# ------------------------------------------------------------------ CLI

def test_cli_classify_prints_the_verdict(tmp_path, capsys):
    sdlc = _project(tmp_path)
    assert upstream.main(["upstream.py", "classify", str(sdlc), f"{KIT_PATH} is wrong"]) == 0
    assert json.loads(capsys.readouterr().out)["origin"] == upstream.KIT


def test_cli_usage(capsys):
    assert upstream.main(["upstream.py"]) == 2
    assert "usage:" in capsys.readouterr().err
