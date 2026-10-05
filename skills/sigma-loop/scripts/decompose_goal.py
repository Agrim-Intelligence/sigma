"""The `file`-mode meta-goal decompose_check (loop.py, #522) files when a flagged parent goal is
too large to implement directly: the body template for the new "Decompose #N" issue -- itself a
normal SDLC goal, protected by the same plan-review/budget/claims machinery every goal already gets
-- plus the comment marker that makes re-filing idempotent.

Lives beside goal_size.py (the classifier) rather than inside it: DECOMPOSED_FROM_MARKER /
DECOMPOSE_OF_MARKER there are read by BOTH loop.py and backlog_check.py, hence that module's own
"single source of truth" framing. DECOMPOSE_FILED_MARKER and the template below are read only by
decompose_check's own `file`-mode branch -- one feature's own home, not force-fit into the
classifier module it merely depends on (for the two body-marker constants, reused below so the
template's own worked examples can never drift from what the classifier's guard actually checks)."""
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


goal_size = _load("goal_size")

# Comment marker decompose_check posts on the PARENT issue once a meta-issue is filed, so a later
# run (this loop, or a concurrent one) can tell a decomposition was already filed without re-filing
# a duplicate. Checked as a bare substring against every comment body -- deliberately not
# first-line-only like goal_size's own two BODY markers, since a comment (unlike a goal body) has
# no "first line is a deliberate declaration" convention to anchor to.
DECOMPOSE_FILED_MARKER = "sigma:decompose-filed"


def filed_marker_comment(issue_number):
    """The exact narrative + machine marker decompose_check posts on the parent once meta-issue
    #<issue_number> is filed -- one place so the idempotency check (DECOMPOSE_FILED_MARKER above)
    and the text actually posted can never drift out of sync."""
    return (f"Too large to implement as one goal — decomposition filed as #{issue_number}. "
            f"<!-- {DECOMPOSE_FILED_MARKER}=#{issue_number} -->")


_META_BODY = """<!-- {decompose_of}#{parent} -->
Decompose #{parent} into independently implementable sub-issues. This goal creates ISSUES only —
never implement any of #{parent}'s own work here.

## Step 0 — reconcile first (every check a DIRECT timeline read, never a search)

- #{parent} is already CLOSED -> obsolete: comment here saying so, run `loop.py verify`, `record
  done`, stop.
- #{parent}'s own comments already contain a "decomposed into ..." summary, OR this goal's own
  timeline (or a sibling decompose-goal's) already shows children created — each `handoff.py track`
  call posts its own narrative on the filing goal's timeline, and that narrative IS the child
  ledger -> already done: same exit.
- Another OPEN `sdlc:decompose` issue also targets #{parent} (list issues labelled `sdlc:decompose`,
  then read each candidate's first line for `{decompose_of}#{parent}`) with a LOWER issue number
  than this one -> defer to it: same exit. Lower-number-wins is the tie-break: without it, two
  concurrent duplicates each see the other and both abort (a mutual-abort deadlock) — the
  lowest-numbered open decompose-goal always proceeds, every other one defers.

## Step 1 — research

Research #{parent} fully: its title, body, and comments, and the code paths it actually touches.

## Step 2 — plan the split

Plan 2..{max_children} children, each independently implementable AND independently verifiable on
its own, each with a distinct, specific title. Add a dependency edge between two children only
where one genuinely cannot start before the other lands. Plan-review applies as normal to this
plan — the independent reviewer judges the split itself, not just this template's prose.

## Step 3 — implement: create each child (never `gh issue create` directly)

Create each child in dependency order (blockers first, so their issue numbers exist before a
sibling references them) via:

    handoff.py track <sdlc_dir> <THIS issue> --area <#{parent}'s area> --why "<one line>" \\
        --queue actionable --assignee same-area --blocks no --priority <#{parent}'s priority> \\
        --label model:<predicted tier> --title "<child title>" --body-file <tmp>

    (the second argument is THIS goal's OWN issue number, never #{parent} -- `track` posts each
    child's narrative on THAT goal's timeline, and step 0 above reads it back as the child ledger;
    interpolating {parent} there would file every child from the PARENT instead and step 0 would
    never see its own already-created children)

Child body: FIRST line `<!-- {decomposed_from}#{parent} -->`; near the top a `Blocked by
#<sibling>` line for each real dependency; then the child's own content.

## Step 4 — verify (outcome-check)

Confirm every child appears in `gh issue list --label sdlc:goal --assignee <configured assignee>
--state open`; fix or `record parked` (saying exactly what failed) for anything missing or
unassigned. Comment on #{parent}: "decomposed into #A, #B, #C — see this goal for the plan." Run
`loop.py verify` before `record done`.

No code changes happen in this goal -> no worktree, no branch, no PR: skip `work.py` entirely and
record the outcome directly.
"""


def render_meta_body(parent, max_children):
    """The full body for the "Decompose #<parent>" meta-issue decompose_check's `file` mode files —
    itself a normal SDLC goal (its own plan-review/budget/claims apply); this function only renders
    the prose, it never files anything itself."""
    return _META_BODY.format(parent=parent, max_children=max_children,
                              decompose_of=goal_size.DECOMPOSE_OF_MARKER,
                              decomposed_from=goal_size.DECOMPOSED_FROM_MARKER)
