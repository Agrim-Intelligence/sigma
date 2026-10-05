# SPDX-License-Identifier: MIT
"""#1917 — an idempotent label create must never RECOLOUR a label that already exists.

Every `gh label create` Sigma issued carried `--force`. On a MISSING label that flag changes
nothing; on an EXISTING one `gh` uses it to overwrite the label's colour and description. None of
the four sites passed `--description`, so the flag's only effect on an existing label was the
repaint — it bought nothing and cost an adopter their board.

MEASURED, on a live board (a throwaway board) rather than argued: `priority:P1`
was created `#d93f0b` and `priority:P2` `#fbca04`, one `/sigma-scope` plan was filed, and both came
back `#d4c5f9`. `priority:P0` and `priority:P3` survived only because that plan happened not to
name them. Nothing in the flow reports it; without a colour snapshot taken beforehand there is no
way to notice.

WHY A DEDICATED FILE. The defect is one property held at four sites in three different skills
(`sigma-loop`'s `sources.py` and `triage.py`, and `sigma-init`'s adopter-facing label template), and
it recurs by someone reaching for the obvious flag at a fifth. Split across `test_sources.py` and
`test_triage.py` the property is invisible; here the behavioural proof and the structural guard that
stops the next site sit next to each other.

THE FAKE MODELS `gh`, NOT THE FIX. `_LabelStore.run` reproduces the real CLI's semantics from both
sides — an existing name without `--force` FAILS (`gh`'s own message, pinned verbatim in
`skills/sigma-define/scripts/define.py`'s docstring and `tests/test_define.py`), and with `--force`
the colour is overwritten. So these tests are red against the pre-fix tree for the RIGHT reason: the
production code asked GitHub to repaint, and a fake that only recorded argv could never show that.
All four behavioural tests were run against the unfixed tree and each failed on the colour
assertion, not on a call count.
"""
import importlib.util
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"

import gqlfake


def _mod(name, path=None):
    path = path or (SCRIPTS / f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --------------------------------------------------------------------- a fake that behaves like gh


#: `gh`'s own refusal, verbatim — the text `skills/sigma-define/scripts/define.py` already pins and
#: `tests/test_define.py` already reproduces, so the two fakes in this repo cannot drift on what the
#: real CLI does.
_EXISTS = 'label with name "%s" already exists; use `--force` to update its color and description'


class _LabelStore(object):
    """A `gh` runner carrying a repository's real label table: {name: colour}.

    `label create <name> [--color C] [--force]`
      - name absent  -> created at C (or `?random?`, standing in for the colour `gh` invents when
        `--color` is omitted — the shape `skills/sigma-init`'s template hands an adopter);
      - name present, no `--force` -> RAISES, exactly as `gh` does;
      - name present, `--force`    -> colour OVERWRITTEN. This is the defect, and it must not happen.

    Every other call is recorded and returns whatever `respond` says (default ""), so a test can
    assert on the colour table AND on the issue actually being filed.
    """

    def __init__(self, labels=None, respond=None):
        self.labels = dict(labels or {})
        self.calls = []
        self._respond = respond or (lambda args: "")

    def __call__(self, args):
        args = [str(a) for a in args]
        gql = gqlfake.swap(args, labels=set(), calls=self.calls, repo_args=("--repo", "acme/widget"))
        if gql is not None:
            return gql
        self.calls.append(list(args))
        if args[:2] == ["label", "create"]:
            name = args[2]
            colour = args[args.index("--color") + 1] if "--color" in args else "?random?"
            if name in self.labels and "--force" not in args:
                raise RuntimeError("gh label create failed: " + (_EXISTS % name))
            self.labels[name] = colour
            return ""
        return self._respond(args)

    def creates(self):
        return [c[2] for c in self.calls if c[:2] == ["label", "create"]]


def _github(store, **gh):
    return _mod("sources").GitHubSource(
        {"discovery": {"source": "github", "github": {"repo": "acme/widget", **gh}}}, run=store)


# ----------------------------------------------------------------- the behaviour, at each live site


def test_create_dependency_leaves_an_existing_label_at_its_own_colour():
    """The reported defect, reproduced end to end: filing ONE issue that carries `priority:P1`
    repainted `priority:P1`. The colours here are the ones measured on the live board."""
    store = _LabelStore({"priority:P1": "d93f0b", "priority:P2": "fbca04"},
                        respond=lambda a: "https://github.com/acme/widget/issues/7"
                                          if a[:2] == ["issue", "create"] else "")
    number = _github(store).create_dependency("t", "b", None, labels=["priority:P1"])

    assert store.labels["priority:P1"] == "d93f0b", (
        "create_dependency repainted a label it was only asked to ATTACH")
    assert store.labels["priority:P2"] == "fbca04"      # untouched, and stays that way
    assert number == "7", "the hand-off must still be filed"


def test_create_dependency_still_creates_a_label_that_is_genuinely_missing():
    """The other half, and the reason `--force` cannot simply be swapped for "skip if present":
    idempotent creation is the point. A label nobody has made yet is still made, at the kit's
    colour, and still reaches the issue."""
    store = _LabelStore({"priority:P1": "d93f0b"},
                        respond=lambda a: "https://github.com/acme/widget/issues/8"
                                          if a[:2] == ["issue", "create"] else "")
    _github(store).create_dependency("t", "b", None, labels=["priority:P1", "area:brand-new"])

    assert store.labels["area:brand-new"] == "d4c5f9", "a missing label must still be created"
    assert store.labels["priority:P1"] == "d93f0b"
    create = next(c for c in store.calls if c[:2] == ["issue", "create"])
    assert "priority:P1" in create and "area:brand-new" in create, (
        "both labels must still be attached to the issue")


def test_ensure_labels_leaves_an_adopter_recoloured_lifecycle_label_alone():
    """The kit's OWN `sdlc:*` labels are not exempt. `_ensure_labels` runs on the pick path, so an
    adopter who recoloured `sdlc:goal` to match their board had it repainted on every single loop
    start — a repaint they can never win, because the loop reapplies it forever."""
    store = _LabelStore({"sdlc:goal": "ff00ff"})
    _github(store)._ensure_labels()

    assert store.labels["sdlc:goal"] == "ff00ff", (
        "_ensure_labels overwrote a colour its owner chose")
    assert store.labels["sdlc:parked"], "a lifecycle label that was missing must still be seeded"
    assert "sdlc:in-progress" in store.labels
    assert "sdlc:goal" in store.creates(), (
        "the fix must be `let gh refuse an existing name`, not `read the label list and skip` — a "
        "read-then-skip has a TOCTOU window and costs an extra call, and this assertion is what "
        "tells the two apart: the create is still ATTEMPTED, and gh is what declines it")


def test_triage_enact_apply_leaves_an_existing_priority_label_alone():
    """`triage._ensure_arbitrary_labels` is the site `sources.py`'s own chokepoint docstring calls
    out as living in ANOTHER module, and its docstring says it "mirrors `create_dependency`'s own
    per-label loop ... exactly" — including this. Driven through `enact(apply=True)`, the real
    entry point, rather than by calling the private helper, so the test proves the path an operator
    actually takes."""
    triage = _mod("triage")
    store = _LabelStore({"priority:P1": "d93f0b", "sdlc:goal": "0e8a16"},
                        respond=lambda a: json.dumps({"labels": [], "assignees": [], "body": ""})
                                          if a[:2] == ["issue", "view"] else "")
    plan = {"version": 1, "schema": "triage-plan/v1", "generated_at": "2026-01-01T00:00:00Z",
            "slug": "s", "cap": 3, "waves": [], "deferred": [], "edges": [],
            "picked": [{"issue": 10, "priority": "P1", "model": None, "wave": 1}]}

    triage.enact(".sdlc", {"discovery": {"source": "github", "github": {"repo": "acme/widget"}}},
                 plan, apply=True, run=store)

    assert store.labels["priority:P1"] == "d93f0b", (
        "enact --apply repainted a priority label it was only attaching")


# ------------------------------------------------------------------ the guard against a fifth site


#: A python `gh` argv is a bracketed list; the two tokens that identify one are `"label", "create"`.
#: Matching the enclosing brackets (rather than the whole file) is what keeps `--force` on an
#: unrelated call — `git push --force-with-lease`, `finish --force` — out of this.
_ARGV = re.compile(r"\[\s*[\"']label[\"']\s*,\s*[\"']create[\"'][^\]]*\]", re.S)

#: The shell form, as an adopter meets it in a copy-pasteable block.
_SHELL = re.compile(r"gh\s+label\s+create\b[^\n]*--force")


#: Both trees the kit actually ships and the verify command actually covers
#: (`--cov=skills --cov=hooks`). `hooks/` has no `gh label create` today; the guard is for the one
#: that lands there tomorrow, which is the whole reason it exists.
_SHIPPED_ROOTS = ("skills", "hooks")


def _shipped(suffixes):
    return sorted(p for root in _SHIPPED_ROOTS for p in (ROOT / root).rglob("*")
                  if p.is_file() and p.suffix in suffixes)


def test_no_shipped_code_creates_a_label_with_force():
    """The structural half. `--force` on `gh label create` has exactly one effect that a plain
    create does not have — overwriting an EXISTING label's colour and description — and no site in
    this kit wants it, because no site passes a description and every site is reaching for
    idempotent creation. The four found by #1917 are fixed; this is what stops the fifth.

    Verified by re-adding the flag to `create_dependency` and watching this fire, then removing it
    again — a guard never seen red proves nothing (AGENTS.md, "run the control")."""
    offenders = []
    for path in _shipped({".py"}):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _ARGV.finditer(text):
            if "--force" in m.group(0):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, (
        "`gh label create --force` overwrites an existing label's colour — it does not make the "
        "create idempotent, it makes it destructive. Drop the flag; the call site's own "
        "try/except already absorbs gh's \"already exists\" refusal:\n  " + "\n  ".join(offenders))


def test_no_shipped_snippet_tells_an_adopter_to_create_a_label_with_force():
    """The same rule for the blocks we hand people to RUN. `skills/sigma-init/github-templates/
    LABELS.md.tmpl` shipped `for t in epic task bug feature chore; do gh label create "$t" --force;
    done` — and with no `--color` at all, so `gh` invents one: an adopter who already had `bug` and
    `feature` ran the kit's own onboarding snippet and got them repainted to arbitrary colours.
    Worse than the `d4c5f9` flattening #1917 measured, and the kit told them to do it.

    `.py` is deliberately out of scope here — each fixed site carries a comment explaining why the
    flag is absent, and a rule that cannot tell "do this" from "never do this" would ban the
    explanation along with the hazard. `docs/`, `tests/` and `CHANGELOG.md` are out of scope of both
    guards for the same reason: they describe history, and have to be able to name what was
    removed."""
    offenders = [f"{p.relative_to(ROOT)}:{p.read_text(encoding='utf-8', errors='ignore').count(chr(10), 0, m.start()) + 1}"
                 for p in _shipped({".md", ".tmpl", ".sh"})
                 for m in _SHELL.finditer(p.read_text(encoding="utf-8", errors="ignore"))]
    assert not offenders, (
        "a copy-pasteable snippet tells an adopter to recolour labels they already have:\n  "
        + "\n  ".join(offenders))
