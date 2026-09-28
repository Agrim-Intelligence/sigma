# Reading the target and validating its artifact

The detail behind step 1 of [`../SKILL.md`](../SKILL.md): what `.sdlc/design/<n>.md` to read, and
how to check its `Sweep`, `Seeds`, `Budget`, `Premise check` and `Out of scope` fields against the
real repo before trusting any of them.

---

## 1. Read the target

- The design write-up in full: `.sdlc/design/<n>.md` — **its `Sweep` header field first**
  (`goal-design` §5: `converged` or `capped`), then the seeds, the premise check, blast radius,
  components, out of scope, doubts/blockers, detailed design and slice-count estimate `goal-design`
  produced.
- The source issue `#<n>` itself (title + body + comments) — the original intent the design claims
  to map, and (on the retrofit path) the filed "Design #N" meta-issue's own comment thread, which
  names `<n>` and the `mode` (`full`/`lane`) the design was run at.

Treat every claim in the design as a hypothesis to verify against the real repo — the same
discipline `goal-design`'s own SKILL.md applies one stage up, now applied to ITS output rather than
the original intent.

**`Sweep: capped` is not a REJECT, and it is not a footnote either** (#1952). It means the design
pass ended on its three-round budget rather than on seed closure, so the mapping is partial by the
design's own admission (`goal-design` §2) — a sanctioned outcome, which is exactly why it needs
saying out loud rather than being adjudicated away. Two things follow, both mandatory. The unswept
seeds are a Doubt like any other and go through **2a**'s three buckets on their own merits. And the
FACT that the pass was capped is carried onto everything this stage writes — step 4b's epic body and
step 4d's outcome comment — because a reader who has only the tickets is the common case (the
artifact is trackable by `agrim-setup`'s defaults, but a repo that set its own blanket `.sdlc/` rule
still keeps it local, and nobody outside the repo sees it either way). A write-up whose `Sweep` field is **missing** has not
answered the question at all: report that and ask for it, and never read the silence as
`converged`.

**`Seeds` is where you check the count `Sweep` reports** (#2028). `goal-design` §2's stopping rule
is defined over the seed set, and `## Seeds` is where that set is now written down — one row per
seed, with the round it entered in, the round it was swept in, and `swept` or `unswept`. Count the
rows marked `unswept` and compare that number against the one the capped banner states. They are
the same number written twice, so a disagreement is a **schema violation**: report it, and do not
adjudicate it — you cannot tell from here which of the two is right, and picking one silently is how
the run that motivated this lost a fact between two stages. The same for a **missing** `Seeds`
table: report it and ask for it, exactly as you would a missing `Sweep`, and **never read the
silence** as "every seed was swept" — an artifact with no seed record has not answered the question
that `converged` is an answer to.

A correctly-recorded capped sweep is **still not a REJECT**. This check is about the RECORD agreeing
with itself, never about the verdict: a pass that hit the budget, said so, and listed its unswept
seeds did exactly what §2 asks of it, and those seeds go through **2a**'s three buckets on their own
merits like any other Doubt. What the table adds is that you can now see them as a list rather than
taking the banner's word for how many there are — and, where the rounds show the last sweep was
still surfacing new seeds, that is the evidence for `goal-design` §2's own "the intent's radius
exceeds one design pass" Blocker, which is a finding to look for in `Blockers`, not one to invent
here.

**`Budget` is a claim you check by RE-RUNNING the verb, not by reading it** (#2032). `goal-design` §2
requires the artifact's `**Budget**` header field to be the number `goal_design.py sweep-budget`
returned, never a hand-typed one — but that field is self-reported: a pass that skips the call and
writes `3` from memory produces an artifact indistinguishable from a correct one, which is precisely
the failure this whole mechanism exists to end (the adversarial review's own finding, and the reason
this check exists at all). So run the same verb yourself, with the artifact's own `Mode` and `Lane`
header fields as the arguments, and compare:

```
python3 "${CLAUDE_SKILL_DIR}/../agrim-goal-design/scripts/goal_design.py" sweep-budget .sdlc \
    --mode <the artifact's Mode> --lane <the artifact's Lane>
```

The `rounds` this prints and the artifact's `**Budget**` field are the same number by construction,
never two independent measurements — a mismatch is a **schema violation**, exactly like the `Seeds`
count above: report it, and do not adjudicate it. You cannot tell from here whether the artifact was
written before a config change, against the wrong `Mode`/`Lane`, or simply typed — only that the two
disagree. An artifact with **no** `Budget` field is the identical failure `Sweep`'s own omission is:
report it and ask for it, and never read the silence as "the default applied".

**`Premise check` is read as a claim about the SOURCE, not about the design** (#1976). `goal-design`
§1 records each material claim the source made as `PC-n` with one of `verified` / `falsified` /
`unverifiable` and its evidence. A **falsified** row is the one that changes what this review is
for: it says the framing this whole pipeline started from was wrong, and the design's own `Intent`
should now state the corrected premise rather than the source's. Verify that it does — and then
verify the harder half, which is whether the SLICES were rebuilt on the corrected premise or merely
annotated with it. A slice derived from a premise the same document disproves is a mapping error in
the ordinary sense, and step 3's REJECT is where it goes. A row marked `unverifiable` is a Doubt in
everything but name: run it through **2a**'s three buckets. An artifact with **no** `Premise check`
heading has not answered the question at all — report it as a schema violation, exactly as you would
a missing `Sweep`, and never read the silence as "the source was right".

**`Out of scope` is where you check the verdict `Sweep` reports** (#1975). `converged` is the design
asserting its last sweep round found nothing new **in scope**, and until #1975 that word was the
author's own after-the-fact call with nothing recording it — a pass could quiet its final round by
ruling one file out and the artifact would read exactly like an honest one. `goal-design` §2 now
requires every hit of every recorded search query to be either a blast-radius row or an `X-n` entry
carrying **the round it was excluded in**. So: read the exclusions of the round the sweep ended on
FIRST — that is where the pressure to under-scope sits — and put each one through **2a** the way you
put a Doubt through it. **An exclusion you disagree with is a blast-radius row that is missing**,
which makes the mapping incomplete, which is 2b's REJECT ground rather than a note to carry. And the
one check here that does not rest on trusting the pass: re-run a query from `## Queries run` and
compare its file list against blast radius ∪ `## Out of scope`. Anything in neither was never
accounted for, whatever the header says. And an artifact with **no** `## Out of scope` heading at
all is the same schema violation a missing `Premise check` is — not an artifact that excluded
nothing. `goal-design` §5 writes the heading on every artifact and answers an empty one with a
single `- None.` bullet, so an absent section is the absence of the very record `converged` is now
defined over: omitting it is the cheapest way to reach exactly the clean-looking artifact #1975 is
about. Report it and ask for the section; never read the silence as a quiet sweep.
