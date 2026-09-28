#!/usr/bin/env python3
"""Where a finding about SIGMA ITSELF goes, when the only board in reach is the adopter's (#1551).

`handoff.create_tracked_issue` is the one place the kit ever opens an issue on its own behalf, and
it opens it on the one board it knows about -- the adopter's. That is right for a finding about the
adopter's product and wrong for a finding about the kit, and the wrongness is not tidiness:

  * a team adopts Sigma to organise THEIR work, and their board then starts carrying issues
    about a dependency's internals, competing for their attention and their priorities;
  * the same act hides the finding from the only people who could fix it -- a real defect, correctly
    identified by a real run, sitting permanently where nobody who maintains the kit will see it.

Observed live: two issues about kit internals on an adopter's product board, both raised
automatically by their loop.

--------------------------------------------------------------------------------------------------
THE ROUTE, AND WHY IT IS NOT A NEW MECHANISM

Where there is no access, THE LEDGER IS THE MECHANISM. That is already this epic's answer for a
cross-repo ask (`cross_repo._raise_to`, `feature_propagate._surface_held`), and it is a tier-2
fallback rather than a failure. So a withheld finding travels exactly the way every other blocked
cross-repo ask travels: `ledger.safe_append(kind="note", to=<the operator>)`, plus one stderr line.

Three destinations, in order:

  1. UPSTREAM, when `ledger.handoff.upstream_repo` names one AND `cross_repo.check_access` measures
     `granted` for it. Opt-in by configuration, because filing on somebody else's repository
     unasked is the very act this module exists to stop -- the direction is not what makes it
     acceptable, the configured consent is.
  2. THE LEDGER, otherwise -- addressed to the operator, naming what could not be filed and why.
     This is the default for every adopter who configures nothing, which is what makes "never on the
     adopter's board" true out of the box rather than true only when set up.
  3. NEVER THE ADOPTER'S BOARD.

The FULL text is spilled to `.sdlc/state/withheld/<goal>.md` before any of that, because a ledger
`why` is capped at `ledger.FREE_TEXT_CAP` (200 chars) and a finding whose evidence was truncated to
200 characters HAS been silently dropped, whatever the ledger line says. The note names the path.

AND TWO GATES SIT IN FRONT OF DESTINATION 1, BECAUSE MOVING THE PROBLEM IS NOT SOLVING IT. Every
write on this path is append-only, and `create_tracked_issue`'s own file-time duplicate search
(#1204) lives inside the branch this route SKIPS -- so before these gates existed, a finding
re-derived on a later pass appended to the spill, added a ledger note, and opened ANOTHER issue on
the maintainers' tracker, every pass, forever. That is the same unbounded issue creation this whole
goal exists to end, relocated one board to the left.

  * an EXACT REPEAT (`fingerprint`, recorded per goal in `.sdlc/state/withheld/<goal>.json`) writes
    nothing at all and reports where the first one went;
  * `_UPSTREAM_PER_GOAL_CAP` bounds what the fingerprint cannot see -- the same defect in slightly
    different words on each pass.

Past either gate the finding is still spilled and still ledgered, so nothing is dropped; only the
outbound write stops, and it says why. An UNREADABLE record is not an empty one: it stops the write
and keeps everything else, the same asymmetry `cross_repo` draws between `denied` and `unknown`.

WHY `granted` AND NOTHING ELSE. `cross_repo.check_access` is three-valued and this module honours
that vocabulary unchanged: `denied` and `unknown` are both "do not write", and `unknown` is never a
soft `granted`. A drifted `gh` account yields `unknown`, so a misconfigured machine can never file
into a stranger's repository through this path. The verdict is MEASURED here rather than read from
`cross_repo.recorded`, and that is deliberate rather than an oversight of the epic's "never re-ask"
rule: `recorded()` answers about the repos a UNIT names, the upstream kit repo is never one of them,
and `recorded()` returning `None` means NO DECISION -- never an implied tier and never an implied
grant. Reading a decision that was never made would be exactly the mistake that module is shaped
around. One read-only `gh api repos/<slug>` on the rare kit-finding path is the honest price.

WHAT THE UPSTREAM WRITE MAY CARRY, AND THE PROMISE IT IS ALLOWED TO MAKE. It is the one write in
this module that leaves the adopter's control, so three rules bind it that bind nothing else here.

  * IT ADDS NOTHING IDENTIFYING OF ITS OWN -- no repo, no goal, no board in the wrapper. That is
    true and it was never the real exposure: the caller's `title`, `why` and `body` pass THROUGH,
    and an ordinary finding carries a repo slug, a goal id, a board URL, a local absolute path and
    two handles in its own prose.
  * SO THE MECHANICAL IDENTIFIERS ARE MASKED (`_masks`): the reporting repo and owner, links other
    than ones pointing at the destination repo, absolute local paths, `@handles`, and the goal id.
  * AND THE REST IS SAID OUT LOUD ON THE ISSUE (`FORWARDING_NOTICE`), because prose is not
    de-identifiable and an absolute claim would be false at exactly the moment an adopter reads it
    to decide whether to enable an outbound write. Honest and useful beats absolute and false; a
    test refuses the absolute wording anywhere in this file, in the config template, or on the issue.

Its text is also redacted through `ledger`'s own secret scrubber before it goes, and THAT pass is
the one place in this module that does not fail open: an unavailable scrubber refuses the write
rather than publishing unredacted text into a repository somebody else owns, and the finding falls
back to the ledger -- which was always the fallback, and loses nothing.

--------------------------------------------------------------------------------------------------
DETECTION IS STRUCTURAL, AND THE FAILURE DIRECTION IS NOT SYMMETRIC

`area:*` labels are adopter-configured and cannot be relied on -- both observed cases carried one,
but that was luck, not signal.

The signal that IS structural: THE KIT'S FILES LIVE OUTSIDE THE PROJECT TREE. The plugin is
installed elsewhere (`~/.claude/plugins/...`), so a path in the finding's own evidence that resolves
to a real file under the plugin's install root, in a part of the tree the project does not own, is
about the kit. Both halves are required and both are measured against the filesystem, never guessed:

    exists under plugin_root()   AND   its top directory is one of SIGNATURE_DIRS
                                 AND   the project does not own that top directory

THE ELIGIBLE SET IS THE PLUGIN'S SIGNATURE SUBTREES, AND NOTHING ELSE -- `skills/`, `hooks/`,
`.claude-plugin/`. That restriction is the correction of a measured defect, not a refinement. The
kit ships 1137 tracked files; 985 of them sit under top-level names an ordinary repository routinely
has of its own (a private package 755, `tests` 140, `docs` 75, `examples` 9, `evals` 4, `.github` 2) against
138 under the three named above, and each of the 985 was a live collision for an adopter who did not
have that directory YET. The sharpest was the
most ordinary finding a YOUNG repo produces:

    "CI never runs on PRs. Add .github/workflows/ci.yml so src/main.py is tested."
    -> withheld from their board, and with an upstream configured, filed on the KIT's tracker

A young repo is precisely the one without `.github/` yet, so the rule failed hardest exactly where
it was least affordable. The three signature directories are the ones a project does not have
because they exist to hold a plugin, and they are the ones real kit findings actually cite.

THE RESIDUALS, BOTH DIRECTIONS, STATED RATHER THAN IMPLIED:

  * THE SAFE ONE, now much larger: a genuine kit finding citing `docs/label-model.md`,
    `tests/test_docs.py` or a private package's path is filed on the adopter's board, exactly as it is today.
    Reach traded for the guarantee, deliberately;
  * THE DAMAGING ONE, narrowed but NOT proven absent: a project that means to create a file at
    exactly a kit path under `skills/`, `hooks/` or `.claude-plugin/`, and does not yet have that
    top directory, still has that finding taken from it. Conjunct 3 is what keeps it to that, and
    it is why conjunct 3 survives conjunct 2 rather than being replaced by it: `skills/` and
    `hooks/` are names a repository can perfectly well take for itself, and one that has taken a
    name owns everything under it.

THE ONE REPOSITORY WHERE THAT NEEDS CARE IS THIS ONE -- Sigma developing Sigma, where the
kit's files genuinely ARE the project's files. There are two independent covers, because a
classifier that withholds this repo's own findings from this repo's own board is a self-inflicted
outage, and one cover is not enough for that:

  1. `self_hosted()` short-circuits `classify` to `PROJECT` before any token is looked at. It is
     TRUE ONLY WHEN THE TWO TREES OVERLAP -- a vendored plugin, or a checkout run against its own
     `skills/` -- and it is worth saying plainly that this is NOT the layout of an ordinary
     development machine: with the plugin installed at `~/.claude/plugins/cache/...` and the repo
     at `~/work/sigma`, neither contains the other and this cover does not fire at all;
  2. conjunct 3 of `_is_kit_path` -- the project owns the top directory. In that ordinary layout
     this is the cover that actually holds, and it holds robustly: the Sigma checkout has its
     own `skills/`, `hooks/` and `.claude-plugin/`, which is exactly what conjunct 3 asks about.

Both are pinned, including the real installed layout, which the first version of this passage
credited to (1) when (2) was doing the work.

THE FAILING DIRECTION IS A REQUIREMENT, NOT A PREFERENCE. A wrong guess that files a kit bug on an
adopter's board is TODAY'S behaviour. A wrong guess that WITHHOLDS a real project finding is worse
than what we have now. So `PROJECT` is the default and every uncertainty resolves to it:

  * a bare filename with no separator (`loop.py`, `work.py`) is NOT evidence -- it names no path,
    and `decompose_goal._META_BODY` (a template about the ADOPTER's own goal) is full of them;
  * a path that exists in neither tree is not evidence;
  * a `..` component is dropped rather than resolved -- a traversal is not a location;
  * anything raised anywhere in here is caught and answered `PROJECT`, with the reason said out
    loud. `classify` never raises, and `test_classify_falls_towards_filing_locally_when_it_breaks`
    is what holds that to the direction rather than to the words.

The cost of that direction, stated rather than hidden: a finding about the kit that cites no path
at all -- "`loop.py record done` mishandles the label" -- is filed locally, exactly as it is today.
This narrows the leak from every kit finding to those with no structural evidence; it does not
close it, and closing it by widening the signal would trade a real project finding for it.

`ledger.handoff.kit_findings: "file-locally"` is the escape hatch for a repo that WANTS them local
(a fork's maintainer, most obviously). Only that exact literal disables the routing -- a typo lands
on the default, because nothing should reach the mode that writes to a board by misspelling
something else, the same rule `work.unit_completion` states for itself.

Module shape follows `feature_propagate.py`: zero third-party dependencies, siblings loaded by file
path, every ledger write fail-open, and nothing here may break a filing.
"""
import hashlib
import importlib.util
import json
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ledger = _load("ledger")            # the `to`-addressed transport that already exists
state = _load("state")              # `unsafe_goal_reason`, the shared path-component guard
work = _load("work")                # `stem`: the one goal -> filename rule in this plugin

_CROSS_REPO = None


def _cross_repo():
    """#1472's module, loaded on FIRST USE and cached -- `handoff` imports this module on every
    filing and only the rare kit-finding path ever needs the access check. Returns None if it cannot
    be loaded, which degrades to "no verdict", which writes nothing upstream: the safe direction.
    Same helper, same reasoning, as `feature_propagate._cross_repo`."""
    global _CROSS_REPO
    if _CROSS_REPO is None:
        try:
            _CROSS_REPO = _load("cross_repo")
        except Exception:                 # noqa: BLE001 - a missing consumer must not break a filing
            return None
    return _CROSS_REPO


# --------------------------------------------------------------------------- the vocabulary

#: What a finding is ABOUT. TWO, not three -- and the asymmetry is the whole design: `PROJECT` is
#: the default AND the answer to every uncertainty, because withholding a real project finding is
#: worse than today's behaviour while filing a kit finding locally merely IS today's behaviour.
KIT, PROJECT = "kit", "project"

#: The literal that turns the routing off. Exact-match only -- see the module docstring.
FILE_LOCALLY = "file-locally"

#: The ONE access verdict that may write upstream. Named here so this module's own comparison reads
#: as a rule rather than as a string literal; `tests/test_upstream.py` pins it to `cross_repo`'s own
#: constant, so the two can never drift into two vocabularies.
GRANTED, UNKNOWN = "granted", "unknown"

#: Where a withheld finding's full text is spilled, under `<sdlc_dir>/state/`.
WITHHELD_DIRNAME = "withheld"

#: How many evidence paths the LEDGER line names before it stops counting. `ledger` caps `why` at
#: `FREE_TEXT_CAP` (200) from the HEAD, so anything after the cap is not shortened but DELETED --
#: an unbounded evidence list would push the one actionable fact (where the finding went) off the
#: end of the only line the operator reads. The overflow is counted rather than dropped silently,
#: the same rule and the same reason as `feature_propagate._CLAUSE_CAP`.
_EVIDENCE_CAP = 2

#: THE ONLY TOP-LEVEL DIRECTORIES WHOSE CONTENTS MAY BE READ AS KIT EVIDENCE -- the plugin's
#: SIGNATURE subtrees, the ones no ordinary project has because they exist to hold a plugin.
#:
#: Measured, not chosen by taste (`git ls-files`, this tree, 1137 tracked files). 138 sit under these
#: three. 985 sit under top-level names an ordinary repository routinely has of its own -- a
#: private package 755, `tests` 140, `docs` 75, `examples` 9, `evals` 4, `.github` 2 -- and every one of those was a
#: live collision for an adopter who did not have that directory YET. The sharpest was the youngest
#: repo's most ordinary finding: "there is no CI, add `.github/workflows/ci.yml`" -- a path that
#: exists in the kit, is absent from a repo that has no `.github/`, and was therefore taken off that
#: repo's own board and, with an upstream configured, filed on the KIT's tracker instead.
#: Restricting the eligible set to these three costs only reach (a genuine kit finding citing
#: `docs/label-model.md` is now filed locally) and buys back the entire damaging direction. The
#: counts move as the tree does; what does not move is that the three named here exist to hold a
#: plugin and the six above do not.
SIGNATURE_DIRS = ("skills", "hooks", ".claude-plugin")

#: `owner/name` -- CHARACTER-FOR-CHARACTER `cross_repo._REPO_RE`, which is the pattern that decides
#: whether that module will ask GitHub about a repo at all. Copied rather than imported because that
#: module is loaded lazily (see `_cross_repo`) and this is read on the config path before any of it
#: is needed; `tests/test_upstream.py` pins the two to be equal, so a tightening there cannot leave
#: this one quietly looser. `\A`/`\Z`, not `^`/`$`: the latter pair matches at a newline, so
#: `"owner/name\nrm -rf /"` would pass a `$`-anchored check and reach a shell-quoted `gh` argument.
_REPO_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\Z")

#: A path-ish run of characters. Deliberately excludes `:` so `path/to/x.py:1749` yields the path
#: and drops the line number, and excludes quotes/backticks/parens so a path cited inside prose,
#: markdown or a code span arrives clean without a stripping pass per punctuation style.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./~-]+")

#: An absolute POSIX path, or a `~`-rooted one. Every kit path this module relies on as EVIDENCE is
#: cited relatively, so masking absolutes costs nothing a maintainer needed and removes the single
#: most reliable way an adopter's machine, user and directory layout cross a repository boundary.
_ABS_PATH_RE = re.compile(r"(?<![\w~:/])~?/(?:[A-Za-z0-9._~-]+/)+[A-Za-z0-9._~-]*[A-Za-z0-9_~-]")

#: An email address. Masked BEFORE `_HANDLE_RE`, which deliberately refuses to match an `@` preceded
#: by a word character so it cannot cut an address in half -- leaving the whole address intact, which
#: is worse than either alternative. It is as mechanical an identifier as a repo slug and belongs in
#: the same pass; a promise that named handles and omitted addresses would be true only by reading.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

#: A GitHub-shaped `@handle`. 39 characters is GitHub's own login ceiling.
_HANDLE_RE = re.compile(r"(?<![\w/])@[A-Za-z0-9][A-Za-z0-9-]{0,38}\b")

#: Shortest goal id worth masking. A one- or two-character goal is a bare number that occurs in
#: ordinary prose ("2 of the 3 labels"), and masking it would damage the finding to hide nothing --
#: an issue number on its own identifies no repository.
_GOAL_MASK_MIN = 3

#: HOW MANY UPSTREAM ISSUES ONE GOAL MAY EVER OPEN. The exact-repeat index below stops a finding
#: recurring verbatim; this stops the shape it cannot see -- the same defect reported in slightly
#: different words on each pass of a long run. Beyond the cap the finding is still spilled in full
#: and still ledgered to the operator, so nothing is dropped; only the outbound write stops, and it
#: says why. Unbounded creation on somebody else's tracker is the exact problem this goal exists to
#: end, and relocating it from the adopter's board to the maintainers' would not be ending it.
_UPSTREAM_PER_GOAL_CAP = 3

#: Schema tag on the per-goal record. Present-and-matching is the only shape that counts as history;
#: see `_history`.
INDEX_SCHEMA = "sigma/withheld@1"

#: How long the upstream issue's own title may be. GitHub accepts far more; a tracker reads better
#: with one line, and the whole finding is in the body directly underneath either way.
_HEADING_CHARS = 120

#: How much of the finding's own text is carried upstream. The upstream POST is the one write in
#: this module that leaves the adopter's control entirely, so what it carries is bounded as well as
#: redacted.
_UPSTREAM_CHARS = 20_000

#: How much of one text is scanned for evidence. `handoff track --body-file` accepts a file of any
#: size -- a pasted log, a whole diff -- and this runs on the pick path of every filing, so the scan
#: is bounded rather than trusted to stay small. Truncation can only ever LOSE evidence, which
#: answers `project`, which is the direction this whole module fails in anyway.
_SCAN_CHARS = 64_000


def _note(message):
    """One stderr line, never an exception -- the same shape and reason as `cross_repo._note`."""
    try:
        sys.stderr.write(message)
    except Exception:                     # noqa: BLE001 - a diagnostic must never break a filing
        pass


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _settings(config):
    """`ledger.handoff`, the block `handoff.py` already reads its own knobs from.

    `_mapping` on the way IN as well as on the way out: `ledger.settings` is `(config or {}).get(
    "ledger")`, which reduces `None` correctly and raises `AttributeError` on any other non-mapping
    -- a hand-edited config is exactly the shape this has to survive, and a knob read that raises
    would take the whole filing with it. Same reducer, same reasoning, as `cross_repo._mapping`."""
    return _mapping(_mapping(ledger.settings(_mapping(config))).get("handoff"))


def upstream_repo(config):
    """The repo kit findings may be filed on, or None. `owner/name` or nothing -- a value that is
    not a repo name is not a repo, and nothing is asked about it."""
    raw = _settings(config).get("upstream_repo")
    raw = raw.strip() if isinstance(raw, str) else ""
    return raw if _REPO_RE.match(raw) else None


def routing_enabled(config):
    """False only on the exact literal `file-locally`. See the module docstring."""
    return _settings(config).get("kit_findings") != FILE_LOCALLY


# --------------------------------------------------------------------------- the two roots


def plugin_root():
    """Where the kit itself is installed -- `skills/agrim-loop/scripts/` up to the tree that holds
    `skills/`, `hooks/` and `.claude-plugin/`. The same walk `ledger._scrub_module` and
    `actionlog._scrub_module` already make to reach `hooks/` from this directory (they count four
    `.parent`s from `__file__`; this counts three from the directory, which is the same place)."""
    return _HERE.parent.parent.parent


def project_root(sdlc_dir):
    """The adopter's tree. Same rule as `handoff.project_root`/`work.project_root` -- borrowed
    rather than re-derived, because two opinions about where the project is would be two answers to
    the one question this module asks."""
    return pathlib.Path(sdlc_dir).resolve().parent


def _under(root, path):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def self_hosted(sdlc_dir):
    """Is the kit the project? True for Sigma developing Sigma, and for any checkout where
    one root contains the other -- a vendored plugin inside the project tree answers the same way,
    and for the same reason: its files ARE that project's files."""
    try:
        plugin, project = plugin_root().resolve(), project_root(sdlc_dir)
    except Exception:                     # noqa: BLE001 - an unresolvable root is not a licence
        return False                      # to withhold; see `classify`'s own fail-safe
    return _under(project, plugin) or _under(plugin, project)


# --------------------------------------------------------------------------- the classifier


def _tokens(text):
    """Every path-SHAPED token in `text`. A token with no separator is not a path -- see the module
    docstring for why that exclusion is load-bearing rather than tidy."""
    out = []
    for raw in _TOKEN_RE.findall(text[:_SCAN_CHARS] if isinstance(text, str) else ""):
        token = raw.rstrip(".")           # a path at the end of a sentence
        while token.endswith("/"):
            token = token[:-1]
        if "/" not in token:
            continue
        if ".." in token.split("/"):      # a traversal is not a location
            continue
        out.append(token)
    return out


def _is_kit_path(token, plugin, project):
    """Is this one token kit evidence? THREE conjuncts, every one of which fails towards `project`.

      1. it resolves to something that EXISTS under the plugin's install root;
      2. its top directory is one of `SIGNATURE_DIRS` -- a directory that exists to hold a plugin,
         not one an ordinary repository has;
      3. the project does not own that top directory ANYWAY.

    (1) alone calls every kit path evidence in a self-hosting checkout. (1)+(3) alone was the
    shipped rule and it withheld an adopter's OWN finding whenever they did not happen to have the
    directory yet -- "add `.github/workflows/ci.yml`", from the young repo that by definition has no
    `.github/`. (2) is what makes the eligible set 137 paths instead of 984; see `SIGNATURE_DIRS`.
    (3) is kept on top of (2) rather than replaced by it, because `hooks/` and `skills/` are names a
    repository can perfectly well take for itself, and a repo that has taken one owns everything
    underneath it.

    PER-TOKEN, NOT PER-FINDING. An unresolvable token (`~nosuchuser/notes.md`, whose `expanduser`
    raises) costs itself and nothing else; before this guard it discarded the whole evidence set
    wholesale. Both directions were safe -- this one is also correct."""
    try:
        if token.startswith("~"):
            token = str(pathlib.Path(token).expanduser())
        if token.startswith("/"):
            # An absolute path answers for itself. `//host/path` (a URL whose scheme `_TOKEN_RE`
            # has already split off) lands here too, exists nowhere, and is refused by the same
            # line as any other absent path.
            here = pathlib.Path(token).resolve()
            if not (here.exists() and _under(plugin, here)) or _under(project, here):
                return False
            parts = here.relative_to(plugin).parts
        else:
            if not (plugin / token).exists():
                return False
            parts = tuple(token.split("/"))
        top = parts[0] if parts else ""
        return top in SIGNATURE_DIRS and not (project / top).exists()
    except Exception:                     # noqa: BLE001 - one bad token is not the whole finding
        return False


def classify(sdlc_dir, *texts):
    """Is this finding about the kit or about the project? -> a verdict dict. NEVER raises, and
    every failure answers `PROJECT` -- see the module docstring on the failing direction.

    -> `{"origin", "self_hosted", "evidence", "why"}`. `evidence` is the sorted set of tokens that
    decided it, and it is empty on every `PROJECT` answer, so a reader can always tell a measured
    verdict from a defaulted one."""
    try:
        if self_hosted(sdlc_dir):
            return {"origin": PROJECT, "self_hosted": True, "evidence": [],
                    "why": "the kit is installed inside this project tree, so its files are this "
                           "project's files and every finding about them is a local one"}
        plugin, project = plugin_root().resolve(), project_root(sdlc_dir)
        # DEDUPED BEFORE it is measured, not after: `_is_kit_path` is two `stat` calls, and a body
        # citing one file forty times would otherwise pay for it forty times.
        candidates = {token for text in texts for token in _tokens(text)}
        evidence = sorted(t for t in candidates if _is_kit_path(t, plugin, project))
    except Exception as exc:              # noqa: BLE001 - a classifier that dies files locally
        return {"origin": PROJECT, "self_hosted": False, "evidence": [],
                "why": "the finding's origin could not be measured (%s), so it is treated as this "
                       "project's -- withholding on a guess is the one failure worse than filing "
                       "on one" % exc}
    if not evidence:
        return {"origin": PROJECT, "self_hosted": False, "evidence": [],
                "why": "nothing in this finding points into the plugin's install path"}
    return {"origin": KIT, "self_hosted": False, "evidence": evidence,
            "why": "the evidence points into the plugin's install path and at nothing in this "
                   "project: %s" % ", ".join(evidence)}


# --------------------------------------------------------------------------- the full text


def withheld_path(sdlc_dir, goal):
    """The one place a goal becomes a spill path, and therefore the one place that refuses.
    `work.stem` is BORROWED, never re-derived -- the identical argument `cross_repo.decision_path`
    makes for the landing record."""
    goal_stem = work.stem(goal)
    if not str(goal_stem).strip():
        raise ValueError("a withheld finding needs a goal to be filed under, and %r reduces to "
                         "nothing" % (goal,))
    reason = state.unsafe_goal_reason(goal_stem)
    if reason:
        raise ValueError("unsafe goal %r for the withheld record: %s" % (goal, reason))
    return pathlib.Path(sdlc_dir) / "state" / WITHHELD_DIRNAME / (str(goal_stem) + ".md")


def index_path(sdlc_dir, goal):
    """The spill file's machine-readable sibling: what this goal has already withheld, and where it
    went. `.json` beside the `.md`, from the one function that turns a goal into a path."""
    return withheld_path(sdlc_dir, goal).with_suffix(".json")


def fingerprint(title, why, body):
    """The identity of a finding, for repeat detection. Whitespace-collapsed and case-folded, with
    an explicit separator between the three fields so `("a", "b")` can never hash as `("ab", "")`.

    EXACT-REPEAT, AND SAID SO RATHER THAN IMPLIED. There is no TF-IDF here and `dedup.py`'s engine
    is not reused: that engine scores a candidate against a CORPUS OF ISSUES, and on this path there
    is no board to draw one from -- the whole point is that nothing was filed. What this catches is
    the real and common shape, a loop re-deriving the same finding on a later pass and re-filing it.
    What it does not catch -- the same defect reworded -- is what `_UPSTREAM_PER_GOAL_CAP` is for."""
    parts = [" ".join(str(v or "").split()).casefold() for v in (title, why, body)]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _history(sdlc_dir, goal):
    """-> `(record, readable)`. `readable` is False when a record exists and could not be parsed.

    THE TWO FAILURES ARE NOT THE SAME FAILURE and reading them alike is how a dedup index becomes a
    dedup illusion. ABSENT is the ordinary first-run state and means "nothing has been withheld
    here"; UNREADABLE means "this goal's history cannot be established", and treating that as an
    empty history would re-open every finding it had already filed, which is precisely the
    unbounded-creation failure the index exists to stop. So an unreadable record still spills and
    still ledgers -- nothing is lost -- and refuses only the outbound write. The same asymmetry
    `cross_repo` draws between `denied` and `unknown`, applied to a file instead of an API."""
    empty = {"schema": INDEX_SCHEMA, "findings": {}, "upstream": []}
    try:
        # `index_path` is INSIDE the guard, not above it. It resolves a goal to a path and refuses
        # an unsafe one by raising -- and this function is called before the spill, so letting that
        # escape took the whole route down to its outer guard, which answers `withheld=False`: a
        # broken path would have put the kit finding straight back on the adopter's board. Every
        # failure in here has to land on "history unknown", which stops only the outbound write.
        path = index_path(sdlc_dir, goal)
        if not path.exists():
            return empty, True
        got = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                     # noqa: BLE001 - a corrupt record is not an empty one
        return empty, False
    if not isinstance(got, dict) or got.get("schema") != INDEX_SCHEMA:
        return empty, False
    got.setdefault("findings", {})
    got.setdefault("upstream", [])
    if not isinstance(got["findings"], dict) or not isinstance(got["upstream"], list):
        return empty, False
    return got, True


def _remember(sdlc_dir, goal, record, report, now=None):
    """Persist what this pass withheld, best-effort. A record that cannot be written costs the NEXT
    pass its dedup -- which is a repeat, not a loss -- and must never cost this filing."""
    try:
        record["findings"][report["digest"]] = {"at": now or _stamp(),
                                                "upstream": report["duplicate_of"]}
        if report["issue"]:
            record["upstream"].append("%s#%s" % (report["upstream"], report["issue"]))
        path = index_path(sdlc_dir, goal)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:              # noqa: BLE001 - never break a filing over a record
        _note("sigma: upstream: this goal's withheld-findings record could not be updated "
              "(%s); a later identical finding will be treated as new.\n" % exc)


def _stamp():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spill(sdlc_dir, goal, title, why, body, evidence):
    """Persist the finding IN FULL, appended. -> the path, or None with the reason on stderr.

    Appended rather than replaced because one goal can find more than one thing, and the second
    finding must not erase the first. This runs BEFORE any routing attempt: a spill that only
    happened on the failure path would be missing exactly when the ledger `why` was the only other
    copy. The evidence list is written here too, because the ledger line can only afford to name
    `_EVIDENCE_CAP` of them and the rest would otherwise exist nowhere."""
    try:
        path = withheld_path(sdlc_dir, goal)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n## %s\n\n**Evidence:** %s\n\n%s\n\n%s\n"
                         % (title or "(untitled finding)", ", ".join(evidence) or "-", why or "",
                            body or ""))
        return str(path)
    except Exception as exc:              # noqa: BLE001 - never break a filing over a spill
        _note("sigma: upstream: the withheld finding's full text could not be written for %s "
              "(%s); the ledger note carries the summary alone.\n" % (goal, exc))
        return None


def _named(sdlc_dir, resolve, goal):
    """`resolve(sdlc_dir, goal)` as a short, printable name -- or a placeholder when it refuses.

    NAMING A FILE IN A WARNING MUST NOT BE ABLE TO FAIL THE ROUTE. `withheld_path` raises on an
    unsafe goal, so an unguarded `index_path(...)` inside a warning string took the whole route down
    to its outer guard, which answers `withheld=False` -- and a message about not writing to the
    adopter's board became the reason for writing to it. Caught by
    `test_a_record_that_cannot_be_written_costs_a_repeat_and_never_the_filing`, which is the shape
    that exercises it."""
    try:
        return _short(sdlc_dir, str(resolve(sdlc_dir, goal)))
    except Exception:                     # noqa: BLE001 - a name is never worth a filing
        return "this goal's withheld record"


def _short(sdlc_dir, path):
    """The spill path as an operator would type it -- relative to the project root when it is
    inside it. Not cosmetic: an absolute path in a deep checkout is most of `ledger.FREE_TEXT_CAP`
    on its own, and every character it takes is one the destination loses.

    `.resolve()` ON BOTH SIDES, and it is load-bearing rather than tidy: `withheld_path` builds off
    the `sdlc_dir` it was GIVEN while `project_root` resolves, so on macOS (`/var` -> `/private/var`)
    and anywhere else with a symlinked checkout the two spellings of the same directory do not
    `relative_to` each other, the whole absolute path lands in the ledger line, and it eats the
    destination. Measured by running the real code, not reasoned about -- it is exactly what
    `test_a_deep_absolute_sdlc_dir_does_not_eat_the_ledger_line` reproduces."""
    if not path:
        return "(not written)"
    try:
        return str(pathlib.Path(path).resolve().relative_to(project_root(sdlc_dir)))
    except Exception:                     # noqa: BLE001 - outside the tree, or unresolvable
        return str(path)


# --------------------------------------------------------------------------- the upstream file


def _version():
    try:
        manifest = json.loads((plugin_root() / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        return str(manifest.get("version") or "") or "unknown"
    except Exception:                     # noqa: BLE001 - a version is a courtesy, not a gate
        return "unknown"


def _masks(config, goal, repo):
    """The MECHANICAL identifiers of this adopter, as `(pattern, replacement)` pairs.

    WHAT THIS CAN AND CANNOT DO, said here because the sentence an adopter reads before enabling an
    outbound write has to be true. It removes identifiers with a SHAPE -- their repo slug and owner,
    their board and project links, absolute local paths, `@handles`, and the goal's own id. It
    cannot de-identify PROSE: "our own workaround in the payments lib" names a project to anyone who
    knows it, and no pass over free text will ever catch that. The earlier version of this module
    made the absolute claim -- that no identifier of theirs crossed at all -- while passing the
    caller's words through verbatim, which is the worst of the three available positions: worse than
    claiming nothing, because it is read at exactly the moment somebody decides to turn the write
    on. So the promise is now the one that can be kept, and `upstream_body` says the rest out loud.
    `test_no_surviving_text_promises_more_than_the_masks_deliver` refuses the old wording anywhere
    in this file, the adopter-facing template, or the issue body -- including a well-meant quotation
    of it, which is how this very paragraph first failed that guard.

    URLs pointing at the DESTINATION repo survive: they are the one link class that is about the kit
    rather than about the reporter, and they are what makes a cross-reference possible at all."""
    out = []
    slug = _mapping(_mapping(_mapping(config).get("discovery")).get("github")).get("repo")
    slug = slug.strip() if isinstance(slug, str) else ""
    keep = re.escape("https://github.com/%s/" % repo) if repo else None
    out.append((re.compile(r"https?://\S+"),
                lambda m: m.group(0) if keep and re.match(keep, m.group(0), re.I)
                else "[link removed]"))
    # ORDER IS LOAD-BEARING, MOST-STRUCTURED FIRST. A later, narrower mask that fires inside an
    # earlier, wider one leaves a FRAGMENT, which is worse than either outcome on its own: with the
    # owner mask ahead of the address mask, `bob.smith@acme-corp.example` became
    # `bob.smith@[owner removed].example` -- the local part, the shape, and the fact of an address,
    # all still there, under a replacement that reads as though it had been handled. So URLs and
    # addresses are consumed whole before anything is allowed to match inside them.
    out.append((_EMAIL_RE, "[email removed]"))
    out.append((_ABS_PATH_RE, "[local path removed]"))
    if _REPO_RE.match(slug or ""):
        out.append((re.compile(re.escape(slug), re.I), "[repo removed]"))
        out.append((re.compile(r"\b%s\b" % re.escape(slug.split("/")[0]), re.I),
                    "[owner removed]"))
    ref = str(goal or "").strip()
    if len(ref) >= _GOAL_MASK_MIN:
        out.append((re.compile(r"#?\b%s\b" % re.escape(ref)), "[goal removed]"))
    out.append((_HANDLE_RE, "[handle removed]"))
    return out


def _redacted(value, masks=()):
    """Scrub, then de-identify, then cap. -> the safe text, or `None` when it could not be scrubbed.

    THE CAP IS LAST AND MUST STAY LAST -- `ledger._sanitize_free_text`'s rule: capping first
    truncates a secret mid-pattern and publishes an unredacted FRAGMENT, because neither pass ever
    sees the whole shape. The secret scrubber runs before the identity masks so it reads the text as
    written, rather than one an earlier substitution has already cut in half. Newlines are
    deliberately NOT flattened -- that half of ledger's pipeline exists to keep one entry on one line
    in a `.jsonl`, not for safety, and a body is prose that stays prose.

    THE ONE PLACE IN THIS MODULE THAT DOES NOT FAIL OPEN. Everywhere else a missing dependency
    degrades to doing less; here "doing less" would mean publishing unredacted text into a
    repository somebody else owns. So an unavailable or raising scrubber refuses the upstream write
    altogether, and the finding falls back to the ledger -- which loses nothing, because the ledger
    was always the fallback. Same shape as the access check: what cannot be established does not
    write. The identity masks are held to the identical standard: a mask that raises refuses too."""
    try:
        module = ledger._scrub_module()
        if module is None:
            return None
        text = module._scrub(str(value or ""))
        for pattern, replacement in masks:
            text = pattern.sub(replacement, text)
        return text[:_UPSTREAM_CHARS]
    except Exception:                     # noqa: BLE001 - a pass that failed did not redact
        return None


#: The sentence that keeps the promise honest, on the issue itself. It is here rather than only in
#: a docstring because the reader who most needs it is the MAINTAINER deciding what they may quote
#: back, and the second-most is whoever audits an outbound write after the fact.
FORWARDING_NOTICE = (
    "The finding's own words below are forwarded as their author wrote them. Mechanical "
    "identifiers were removed automatically -- the reporting repository and owner, links, absolute "
    "local paths, email addresses, `@handles` and the originating goal id -- but prose is not "
    "de-identifiable, so "
    "the text may still describe the project it came from. Treat it as the reporter's words, not "
    "as anonymised text.")


def upstream_body(why, body, masks=()):
    """What the maintainers read, or None when it could not be made safe to publish.

    THE PROMISE THIS KEEPS IS THE ONE IT CAN KEEP. The wrapper adds no identifying metadata of its
    own -- not the repo, not the goal, not the board -- and that was never really the risk: the
    caller's `title`, `why` and `body` pass THROUGH it, and an ordinary finding carries a repo slug,
    a goal id, a board URL, a local absolute path and two handles in its own prose. `_masks` removes
    the identifiers with a shape; `FORWARDING_NOTICE` states, on the issue, that the prose is not
    de-identified. An absolute promise here would be false at exactly the moment somebody reads it
    to decide whether to enable an outbound write, and a false promise there is worse than none."""
    parts = [_redacted(why, masks), _redacted(body, masks)]
    if any(part is None for part in parts):
        return None
    return "\n".join([
        "Filed automatically by Sigma %s from a run in an adopter's project." % _version(),
        "",
        "The evidence for this finding points into the plugin's own install path, so it is about "
        "the kit rather than that project, and it was routed here instead of onto their board.",
        "",
        FORWARDING_NOTICE,
        "",
        "**What was found:** %s" % (parts[0] or "(not stated)"),
        "",
        parts[1].strip(),
    ]).rstrip() + "\n"


def _heading(title, why):
    """The upstream issue's title. A caller-supplied one wins; otherwise the finding's own first
    line, because `handoff track` has no required `--title` and EVERY untitled finding would
    otherwise arrive on the maintainers' tracker under the same six words, which is a tracker
    nobody can read. Bounded, single-line, and never empty."""
    for candidate in (title, why):
        text = " ".join(str(candidate or "").split())
        if text:
            return text[:_HEADING_CHARS]
    return "Sigma finding"


def _file_upstream(repo, title, why, body, run, masks=()):
    """POST one issue to `repo`. -> `{"issue", "url", "error"}`; never raises.

    `gh api`, not `gh issue create`: the labels, assignee and board fields
    `GitHubSource.create_dependency` applies are the ADOPTER's, and every one of them would either
    fail the create or land somebody else's taxonomy on a repository that never asked for it. The
    same REST shape `feature_propagate._write_remote` already uses to touch a foreign repo."""
    text, heading = upstream_body(why, body, masks), _redacted(_heading(title, why), masks)
    if text is None or heading is None:
        return {"issue": None, "url": None,
                "error": "the redaction pass is unavailable, so nothing was published to a "
                         "repository this project does not own"}
    try:
        code, out, err = run(["api", "-X", "POST", "repos/%s/issues" % repo,
                              "-f", "title=" + heading,
                              "-f", "body=" + text])
    except Exception as exc:              # noqa: BLE001 - a runner that dies filed nothing
        return {"issue": None, "url": None, "error": "the gh call could not be run: %s" % exc}
    if code != 0:
        return {"issue": None, "url": None,
                "error": (err or out or "gh exited %s" % code).strip()}
    try:
        payload = json.loads(out or "null")
    except Exception:                     # noqa: BLE001
        payload = None
    if not isinstance(payload, dict) or not payload.get("number"):
        return {"issue": None, "url": None,
                "error": "gh answered without an issue number, so nothing is known to exist"}
    return {"issue": str(payload["number"]), "url": payload.get("html_url"), "error": None}


def adapt_runner(run):
    """A stdout-only `run(argv) -> stdout` runner (`ledger._run_gh`'s shape, the one every caller in
    this plugin already injects) seen through `cross_repo`'s `(returncode, stdout, stderr)` eyes.

    THIS EXISTS BECAUSE ISOLATION HAS TO SURVIVE A CALLER THAT NEVER HEARD OF IT. The standing idiom
    in this codebase's tests and tooling is `run=<fake>`; `upstream.route` reaches the network when
    it is handed nothing; and those two facts together made a "fake" filing perform a live
    `POST /repos/<upstream>/issues` -- it opened a real issue on this repository during review, with
    the injected runner recording zero calls. Adapting is what makes the idiom keep its promise,
    rather than documenting a second parameter and hoping.

    THE FIDELITY IT LOSES IS LOSS IN THE SAFE DIRECTION. A stdout runner discards the exit code and
    the error text, so a failure arrives as a generic non-zero and `cross_repo._classify_failure`
    reads whatever the exception text says -- an unrecognised failure is `unknown`, `unknown` is
    never a soft `granted`, and `granted` still requires a real permissions payload to come back
    through `stdout`. Nothing here can manufacture a write that the injected runner did not
    explicitly answer for."""
    def runner(args):
        try:
            return 0, run(args), ""
        except Exception as exc:          # noqa: BLE001 - a runner that raised answered non-zero
            return 1, "", str(exc)
    return runner


def _access(config, repo, run):
    """`cross_repo`'s three-valued check, unchanged and un-reinterpreted. -> `(verdict, detail)`.

    ONLY ONE OF THE THREE ACTS. `granted` writes upstream; `denied` and `unknown` are both "do not
    write", so this path never has to re-litigate which non-answer is which -- it inherits the rule
    that `unknown` is not a soft `granted` and gets, for free, the identity pinning that makes a
    drifted `gh` account unable to file into a stranger's repository. `None` (the module could not
    be loaded at all) is not a verdict and is not a grant."""
    cross = _cross_repo()
    if cross is None:
        return UNKNOWN, "the access check module could not be loaded"
    try:
        ident = cross.identity(config, run)
        verdict = cross.check_access(repo, ident, run)
        return verdict.get("verdict"), (verdict.get("detail") or verdict.get("reason") or "")
    except Exception as exc:              # noqa: BLE001 - an unmeasurable grant is not a grant
        return UNKNOWN, "the access check could not be run: %s" % exc


# --------------------------------------------------------------------------- the route


def _routed(**over):
    base = {"origin": PROJECT, "withheld": False, "self_hosted": False, "evidence": [],
            "why": "", "upstream": None, "verdict": None, "issue": None, "url": None,
            "spilled": None, "entry": None, "to": None, "warnings": [],
            "digest": None, "repeat": False, "duplicate_of": None, "capped": False}
    base.update(over)
    return base


def route(sdlc_dir, config, goal, title, why, body, run=None):
    """Decide where this finding goes, and take it there. -> a routing dict.

    `withheld` is the ONE field the caller must honour: True means the adopter's board must not be
    written, and everything else in the dict is the record of what happened instead. It is False on
    every `PROJECT` answer, and on that path this function writes NOTHING anywhere -- no ledger, no
    spill, no stderr -- so an ordinary filing is byte-identical to what it was before this existed.

    NEVER RAISES, AND THE OUTER GUARD IS WHAT MAKES THAT TOTAL rather than aspirational -- the same
    wrapper shape, for the same reason, as `cross_repo.check_at_pick` and
    `feature_propagate.propagate_at_pick`. The inner half is careful about the inputs it expects;
    this catches the ones nobody expected, and answers them the only way this module is allowed to
    answer an unknown: `withheld=False`. A routing decision that crashed has decided nothing, and
    the finding goes where it would have gone before this module existed."""
    try:
        return _route(sdlc_dir, config, goal, title, why, body, run=run)
    except Exception as exc:              # noqa: BLE001 - "never raises" has to be total
        _note("sigma: upstream: the routing check could not run (%s); this finding is filed on "
              "this board, which is where it would have gone anyway.\n" % exc)
        return _routed(why="the routing check could not run: %s" % exc)


def _route(sdlc_dir, config, goal, title, why, body, run=None):
    if not routing_enabled(config):
        return _routed(why="kit_findings is set to %r, so a finding about the kit is filed on this "
                           "board like any other" % FILE_LOCALLY)
    verdict = classify(sdlc_dir, title, why, body)
    if verdict["origin"] != KIT:
        return _routed(**{k: verdict[k] for k in ("origin", "self_hosted", "evidence", "why")})

    report = _routed(origin=KIT, withheld=True, evidence=verdict["evidence"], why=verdict["why"])
    report["digest"] = fingerprint(title, why, body)
    record, readable = _history(sdlc_dir, goal)

    # HAS THIS EXACT FINDING ALREADY BEEN WITHHELD FROM THIS GOAL? Checked BEFORE anything is
    # written, because every write on this path is append-only: a recurrence would add a second
    # spill entry, a second ledger note, and -- with an upstream configured -- a SECOND ISSUE on the
    # maintainers' tracker, every pass, forever. `create_tracked_issue` has had a file-time
    # duplicate search since #1204, but it sits inside the branch this path skips, so the withheld
    # route was the one filing route in the kit with no dedup at all. Unbounded issue creation on
    # somebody else's board is the exact problem this goal exists to end; moving it from the
    # adopter's board to the maintainers' would have relocated it, not solved it.
    prior = record["findings"].get(report["digest"]) if readable else None
    if isinstance(prior, dict):
        report["repeat"] = True
        report["duplicate_of"] = prior.get("upstream")
        report["warnings"].append(
            "this exact finding was already withheld from goal %s%s -- nothing was written again "
            "(it is in %s)" % (goal, " and filed as %s" % report["duplicate_of"]
                               if report["duplicate_of"] else "",
                               _named(sdlc_dir, withheld_path, goal)))
        _note("sigma: upstream: %s\n" % report["warnings"][0])
        return report

    report["spilled"] = _spill(sdlc_dir, goal, title, why, body, verdict["evidence"])

    repo = upstream_repo(config)
    if repo:
        report["upstream"] = repo
        # TWO GATES BEFORE THE ACCESS CHECK IS EVEN ASKED, both of which stop the outbound write
        # while leaving the spill and the ledger untouched -- nothing is dropped, only sent.
        if not readable:
            report["warnings"].append(
                "this goal's withheld-findings record could not be read, so what it has already "
                "filed on %s is unknown -- nothing was sent, and the finding is in the ledger. "
                "Delete %s to start the record over"
                % (repo, _named(sdlc_dir, index_path, goal)))
        elif len(record["upstream"]) >= _UPSTREAM_PER_GOAL_CAP:
            report["capped"] = True
            report["warnings"].append(
                "goal %s has already opened %d issues on %s, which is the cap -- this finding is "
                "recorded in the ledger instead of becoming the next one"
                % (goal, len(record["upstream"]), repo))
        else:
            report["verdict"], detail = _access(config, repo, run)
            if report["verdict"] == GRANTED:
                filed = _file_upstream(repo, title, why, body, run or _cross_repo()._run_gh,
                                       _masks(config, goal, repo))
                report["issue"], report["url"] = filed["issue"], filed["url"]
                if report["issue"]:
                    report["duplicate_of"] = "%s#%s" % (repo, report["issue"])
                if filed["error"]:
                    report["warnings"].append(
                        "could not file this kit finding on %s (%s) -- it is recorded in the "
                        "ledger instead" % (repo, filed["error"]))
            else:
                report["warnings"].append(
                    "this account has no confirmed write access to %s (%s: %s), so the finding is "
                    "recorded in the ledger instead" % (repo, report["verdict"], detail or "-"))

    report["warnings"].insert(0, _summary(report))
    _raise_withheld(sdlc_dir, config, goal, report, run)
    _remember(sdlc_dir, goal, record, report)
    return report


def _where(report):
    """The one fact that needs acting on: where this finding actually went."""
    if report["issue"]:
        return "filed upstream as %s#%s" % (report["upstream"], report["issue"])
    if not report["upstream"]:
        return "no upstream repo is configured, so it needs forwarding by hand"
    if report["capped"]:
        return "the per-goal upstream cap is reached, so it needs forwarding by hand"
    return "it could NOT be filed upstream, so it needs forwarding by hand"


def _evidence_clause(report):
    said = ", ".join(report["evidence"][:_EVIDENCE_CAP]) or "-"
    rest = len(report["evidence"]) - _EVIDENCE_CAP
    return said + (" (+%d more)" % rest if rest > 0 else "")


def _summary(report):
    """The one line a human reads on the CONSOLE, in `create_tracked_issue`'s own `warnings`
    channel -- which both CLI verbs already print to stderr, so this needs no new surfacing
    mechanism. Uncapped, so it leads with the reason: a reader looking at a console has the context
    of the run around it and needs to be told what just happened before being told what to do."""
    return ("withheld from this board: the evidence points into the plugin's install path (%s), so "
            "this is a finding about Sigma itself, not about this project -- %s%s"
            % (", ".join(report["evidence"]) or "-", _where(report),
               "; full text in %s" % report["spilled"] if report["spilled"] else ""))


def _ledger_why(sdlc_dir, report):
    """The same event for the LEDGER, and deliberately NOT the same sentence.

    ORDERED BY ACTIONABILITY, BECAUSE THE CAP DELETES RATHER THAN SHORTENS. `ledger` truncates `why`
    to `FREE_TEXT_CAP` from the head, so whatever is last is simply gone -- and the console wording
    above, which opens with the reasoning, lost exactly the half that says where the finding went
    (caught by `test_a_granted_upstream_repo_receives_the_finding`, not by reading the cap). Here
    the destination comes first, then the retrievable full text, then the evidence, which is the
    only part that also exists in the spill file and can therefore afford to be the part that is
    cut."""
    return ("a finding about Sigma itself was withheld from this board -- %s; full text: %s; "
            "evidence: %s" % (_where(report), _short(sdlc_dir, report["spilled"]),
                              _evidence_clause(report)))


def _raise_withheld(sdlc_dir, config, goal, report, run):
    """One ledger line, addressed to the OPERATOR -- the person running the loop, who is the one
    who can forward this. `kind="note"`, deliberately not `handoff`: `backlog_check._ledger_signals`
    reads a hand-off as a real block, so raising a withheld finding that way would PARK the very
    goal that found it. The identical reasoning `cross_repo._raise_to` states for its own raise.

    `ledger.actor(config, run=None)`, not the caller's `run`: this module's `run` is
    `cross_repo`'s `(rc, out, err)` runner and `actor` wants `ledger`'s stdout-only one, and
    threading a second runner through the whole call chain to reach one cached, config-first,
    never-raising lookup would buy nothing. A configured `ledger.actor` short-circuits it entirely,
    and the fallback chain (`gh api user` -> `$USER` -> `unknown`) never fails."""
    try:
        to = ledger.actor(config, run=None)
        report["to"] = to
        report["entry"] = ledger.safe_append(sdlc_dir, "note", goal, config=config, to=to,
                                             area=WITHHELD_DIRNAME,
                                             why=_ledger_why(sdlc_dir, report))
    except Exception as exc:              # noqa: BLE001 - never break a filing over a ledger write
        _note("sigma: upstream: the withheld finding could not be recorded in the ledger for "
              "%s (%s).\n" % (goal, exc))
    _note("sigma: upstream: %s\n" % _summary(report))


# --------------------------------------------------------------------------- CLI


USAGE = "usage: upstream.py classify <sdlc_dir> <text> [<text> ...]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 4 and argv[1] == "classify":
        verdict = classify(argv[2], *argv[3:])
        print(json.dumps(verdict, indent=2, sort_keys=True))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
