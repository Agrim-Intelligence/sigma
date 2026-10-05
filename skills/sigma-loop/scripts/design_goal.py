"""`loop.py`'s `design_check` (#1825) park-and-defer body: the "this issue IS a design meta-issue"
first-line marker, the "already filed" comment marker, and the body template for the new
"Design #N" issue `design_check` files when a flagged goal lacks `sdlc:designed` -- itself a normal
SDLC goal, protected by the same plan-review/budget/claims machinery every goal already gets.

Mirrors `decompose_goal.py`'s shape exactly (same module split rationale: the marker `design_check`
reads for its OWN self-exemption lives here, beside the template, rather than in `goal_size.py` --
`DESIGN_OF_MARKER` has exactly one reader today (`design_check`, plus `decompose_check`'s one added
OR-branch recognizing it -- see loop.py), never `backlog_check.py`, so it does not belong in
`goal_size.py`'s "read by both" framing the way `DECOMPOSED_FROM_MARKER`/`DECOMPOSE_OF_MARKER` do.

`goal-design` (`docs/dossier-pipeline.md` §5) is deliberately a two-path
mechanism: a Product path (an approved Dossier, run directly via `/sigma-goal-design`) and this
Tech-side retrofit path (an existing goal picked up by `sigma-loop` with no `sdlc:designed` overlay).
This module is the retrofit path's own filing half -- see `skills/sigma-goal-design/SKILL.md` for
the actual codebase-mapping work a human or a later pick does against the meta-issue this renders."""
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# This issue IS the meta-goal designing #N -- pure bookkeeping, never itself design-worthy.
# `design_check` reads this for its own self-exemption; `decompose_check` reads it too (one added
# OR-branch alongside its own two markers) so a freshly-filed "Design #N" issue is never misread as
# an oversized goal needing decomposition when it is later picked.
DESIGN_OF_MARKER = "sigma:design-of="

# Comment marker design_check posts on the FLAGGED issue once a "Design #N" meta-issue is filed, so
# a later run (this loop, or a concurrent one) can tell a design pass was already filed without
# re-filing a duplicate. Checked as a bare substring against every comment body -- deliberately not
# first-line-only like DESIGN_OF_MARKER above, mirroring DECOMPOSE_FILED_MARKER's own reasoning: a
# comment has no "first line is a deliberate declaration" convention to anchor to.
DESIGN_FILED_MARKER = "sigma:design-filed"


def filed_marker_comment(issue_number):
    """The exact narrative + machine marker `design_check` posts on the flagged goal once meta-issue
    #<issue_number> is filed -- one place so the idempotency check (DESIGN_FILED_MARKER above) and
    the text actually posted can never drift out of sync."""
    return (f"Not yet designed — a codebase-mapping design pass filed as #{issue_number}. "
            f"<!-- {DESIGN_FILED_MARKER}=#{issue_number} -->")


# `mode` picks the DEPTH of the design pass this meta-issue instructs, never whether design_check
# acts -- once `goal_design.enabled`, the retrofit path always park-and-files (there is no
# log-only/park-only rung the way `goal_decompose.mode` has three). Kept short (well under
# `goal_size.classify`'s thresholds -- 3 `## ` sections, nowhere near the 6-section/1200-word/
# 150-line/4-checkbox/2-phase flags) so this template is never itself mis-flagged as oversized when
# picked as its own goal.
_MODE_INSTRUCTIONS = {
    "full": ("Run the FULL blast-radius mapping: search every file/component #{goal} plausibly "
             "touches, trace callers, and surface every doubt or blocker you find -- do not stop "
             "at the first plausible answer."),
    "lane": ("Run a LANE-SIZED check: classify #{goal} the way `discovery.py`'s own "
             "small/medium/large lane concept already sizes an ordinary goal, then map the "
             "codebase and blast radius proportionately to that lane -- a small-lane goal gets a "
             "correspondingly lighter pass, not the full sweep."),
}

_META_BODY = """<!-- {design_of}#{goal} -->
Produce a codebase-mapping design for #{goal} — map to codebase, blast radius, components, and
doubts/blockers, per `docs/dossier-pipeline.md` §5. This goal produces a DESIGN
ARTIFACT only — never implement any of #{goal}'s own work here.

## Step 0 — reconcile first (every check a DIRECT timeline read, never a search-API query)

- #{goal} is already CLOSED, already carries `sdlc:designed`, or its own comments already contain
  a linked design write-up -> already done: comment here saying so, run `loop.py verify`, `record
  done`, stop.
- Another OPEN issue titled `Design #{goal}: ...` exists with a LOWER issue number than this one
  (`gh issue list --search "Design #{goal}: in:title"`, then confirm its first line carries
  `{design_of}#{goal}` — the title alone is a filter, the marker is the proof) -> defer to it: same
  exit. Lower-number-wins is the tie-break, mirroring `decompose_goal.py`'s own reasoning: without
  it, two concurrent duplicates each see the other and both abort. (No dedicated label scopes this
  search the way `sdlc:decompose` scopes decompose's own — #1825 deliberately did not mint one; see
  its PR for why.)

## Step 1 — run the design pass

Invoke `/sigma-goal-design` (or follow its `SKILL.md` directly) against #{goal}. {mode_instructions}
Produce: the codebase mapping, the blast radius, the touched components, an explicit doubts/
blockers list, a detailed design write-up, and a slice-count estimate. Write it to
`.sdlc/design/{goal}.md` and link it from #{goal}'s own comments.

## Step 2 — hand off (this goal does NOT write `sdlc:designed`)

`sdlc:designed` is written only once `goal-review` (contract §7) confirms the design — that
skill may not exist yet on your install. Until it does: a human reviews `.sdlc/design/{goal}.md`
and, once satisfied, applies `sdlc:designed` to #{goal} by hand, then restores `sdlc:goal` on it if
this design pass parked it (`/sigma-unpark`, never a raw label edit — see `docs/label-model.md`).
Comment on #{goal} naming the design artifact's path. Run `loop.py verify` before `record done`.

No code changes happen in this goal -> no worktree, no branch, no PR: skip `work.py` entirely and
record the outcome directly.
"""


def render_meta_body(goal, mode):
    """The full body for the "Design #<goal>" meta-issue `design_check` files -- itself a normal
    SDLC goal (its own plan-review/budget/claims apply); this function only renders the prose, it
    never files anything itself. `mode` is `"full"` or `"lane"` (already validated/normalized by the
    caller); an unrecognized value here would be a caller bug, not a runtime input, so it is not
    re-validated -- mirrors `decompose_goal.render_meta_body`'s own "caller already normalized this"
    contract for `max_children`."""
    instructions = _MODE_INSTRUCTIONS[mode].format(goal=goal)
    return _META_BODY.format(goal=goal, design_of=DESIGN_OF_MARKER, mode_instructions=instructions)
