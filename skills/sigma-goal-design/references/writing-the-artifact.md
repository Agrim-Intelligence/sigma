# Writing the artifact — the schema, and every field's rules

The detail behind step 5 of [`../SKILL.md`](../SKILL.md): the full `.sdlc/design/<n>.md` heading
schema (a contract with `goal-review`'s own step 1), the copyable artifact skeleton, the required
`<n>-in-brief.md` sibling and its own stricter rules, and what each header field (`Sweep`, `Lane`,
`Budget`, `Premise check`, `Out of scope`, id numbering) means and why it is mandatory.

---

## 5. Write the artifact

Write the design to `.sdlc/design/<n>.md` (`<n>` is the Dossier's or the target goal's own issue
number) — a new directory, mirroring `.sdlc/research/` and `.sdlc/plans/`; `mkdir -p .sdlc/design`
first, it does not exist until the first design pass on a repo.

**The headings are a contract, not a suggestion.** `goal-review`'s own step 1 reads this file
section by section — "the blast radius, components, doubts/blockers, detailed design and
slice-count estimate" — so an artifact that invents its own layout makes its consumer guess. Write
exactly these, in this order, and put nothing the consumer needs outside them:

```markdown
# Design: <target title>
**Target**: #<n> · **Path**: dossier | retrofit · **Mode**: full | lane · **Lane**: small | medium | large · **Budget**: <n> · **Sweep**: converged | capped · **Date**: <YYYY-MM-DD>

> **Incomplete sweep — seed closure was NOT reached.** This pass ended on the <n>-round budget, not
> on a quiet round. <k> seed(s) are still unswept (<name each>), so the mapping below is partial.
<!-- the blockquote above is written ONLY when Sweep is `capped`; on `converged`, omit it entirely -->

**Reading this as a person?** Start with [`<n>-in-brief.md`](<n>-in-brief.md) — the same design in
one page, in plain language. This file is the machine-readable record: one row per verified site
and every query run, so the sweep can be re-run rather than trusted.

## Intent
<the source's own intent in one paragraph, as verified against the repo — not copied; where a
premise below was falsified, say so in one clause and state the corrected one>

## Premise check
- **PC-1.** <the material claim, as the source states it> — **verified** | **falsified** |
  **unverifiable**: <the evidence — `file:line`, or the query that found nothing>

## Queries run
- `<the exact command>`        <!-- §2's verbatim record; this is the re-scan guarantee -->

## Seeds
| ID | Seed | Found in round | Swept in round | Status | Carried as |
| --- | --- | --- | --- | --- | --- |
| S-1 | `<the symbol the source's own intent named>` | 0 | 1 | swept | BR-3, BR-4 |
| S-2 | `<the symbol round 2's sweep surfaced>` | 2 | — | unswept | D-6 |
<!-- round 0 = a seed the intent/`sigma-research` §1 gave you, not one a sweep surfaced.
     `Swept in round` is `—` exactly when Status is `unswept`. `Carried as` names what the seed
     BECAME: the `BR-n` rows a swept seed produced (or `none` if it produced no site), and the
     `D-n` carrying an unswept one. Every seed gets a row; the count of `unswept` rows is the
     number the capped banner states (§2). -->

## Blast radius
| ID | Component | Site (file:line, or a line range) | What it is | Why affected |
| --- | --- | --- | --- | --- |
| BR-1 | … | … | … | … |

## Components
| Component | What it is | What this intent does to it |
| --- | --- | --- |

## Out of scope
- **X-1.** (round <r>) `<the hit, or the group rule and its count>` — <why it is out of scope:
  the intent's change need not touch it, and it would not break if that change shipped>

## Doubts
- **D-1.** <the doubt, and what would settle it>

## Blockers
- **B-1.** <what must be resolved before implementation could start>

## Detailed design
<§4's shape, sequencing and tradeoffs>

## Slice-count estimate
| Slice | Covers | Depends on |
| --- | --- | --- |
| 1 | … | — |

## Handoff
- **Reading this as a person?** Start with [`<n>-in-brief.md`](<n>-in-brief.md) — the same design
  in one page. This file is the machine-readable record: one row per verified site and every
  query, so the sweep can be re-run rather than trusted.
- `goal-review` on this install: <found at `skills/sigma-goal-review/SKILL.md` | not present>
- <if not present: who confirms this design and applies `sdlc:designed` instead — §6>
```

**`.sdlc/design/<n>-in-brief.md` is a required sibling, not an optional courtesy** (#2064). It is
what a person reads to decide whether to confirm — the artifact above is what `goal-review` and
`compile_plan.py` read to build the ticket tree, and the two audiences need different documents, not
one document with a summary bolted on. Measured on a real adopter repo: a design pass produced this
file entirely on its own initiative, unprompted by anything in an earlier version of this section,
and it was exactly right — plain English, one page, no file:line citations. That it happened once
by chance is the defect this fixes: nothing pinned its existence, its content, or whether the next
pass would bother.

Write it in the same commit as the main artifact, same directory, and hold it to rules the main
artifact is deliberately exempt from:

- **Every codename gets a plain-English gloss on first use, then the gloss.** `os` becomes "the
  story house" (or whatever plain noun the target's own domain suggests), stated once, used
  thereafter — never the shorthand alone. A reader who has never opened any of the repos this sweep
  touched must be able to finish the page.
- **No `BR-n` / `D-n` / `B-n` ids, no `file:line`, no query text.** Those are the machine artifact's
  own citation system, built so a claim can be re-checked — exactly the property a person deciding
  whether to confirm does not need and jargon they should never have to parse. State the finding in
  a sentence; the citation lives one file away for whoever wants to verify it.
- **One page.** A sentence or two per point, not a paragraph. If a finding needs three paragraphs to
  state plainly, the finding is really two findings, or belongs in the main artifact only.
- **Fixed shape:** what was checked (one line — the scope, and how many sites); what it found,
  split cleanly into what needs no ruling and what does; what a person has to decide, as plain
  questions, not `open_*` ids; the plan, as a table of what depends on what; and any correction to a
  claim the source got wrong, stated once and named as a correction.
- **When `Sweep` is `capped`, this file says so in its own first line too** — the rule below about
  the comment applies here for the identical reason: the reader who stops after one line of THIS
  file must not be the one left thinking the mapping is complete.
- **It is never read by any script.** `goal-review` reads only `.sdlc/design/<n>.md`; nothing in
  `compile_plan.py` looks for this file's existence or content. It exists for exactly one reader, and
  writing to please a parser is precisely the failure mode that produced the unreadable version of
  it in the first place.

`Doubts` and `Blockers` stay two headings, matching §3's own five-item list — "none" under either
is a real answer and must be written, because an absent heading and an empty one are not the same
claim. The same holds for `Premise check` and `Out of scope`: both are written on every artifact,
and where there is genuinely nothing to say the answer is a single `- None.` bullet, which takes no
id. The `Slice-count estimate` rows are what `goal-review` step 4b turns into `compile_plan.py`
`issues[]` entries: `Covers` becomes the body and `Depends on` becomes the `blocked_by` edges.
(#1922 settled the half that was a defect — `goal-review` demanding a priority these rows never
carried — by omitting it, and is closed; whether they should carry one at all is the residual
question, tracked by nothing open.) `Handoff` is where §6's `goal-review`-presence statement goes; it is a heading in the
schema precisely so that mandated statement is not a deviation from "write exactly these". The
`| --- |` separator rows are part of the skeleton, not decoration — copy a table without one and it
renders on GitHub as a run of literal pipes, not a table. And a `Site` is a `file:line` **or a line
range** (`models.py:8-13`); most real ones are ranges.

**`Sweep` is written on every artifact, and `capped` gets the banner** (#1952). `converged` means
both halves of §2's stopping rule fired — seed closure AND a quiet round, the latter judged over
`## Out of scope` as written rather than over a scope call held in the author's head (#1975).
`capped` means the round
budget ran out first, and it is not a softer way of saying the same thing: the mapping is partial by
this document's own admission. Omitting the field is not an option, for the same reason
`goal-review`'s `(0 open questions carried)` count is written even when it is zero — an absent value
is **indistinguishable** from `converged`, which is exactly the failure #1952 measured. On `capped`
the blockquote under the header is mandatory and says how many seeds are unswept and which; on
`converged` there is no blockquote at all. `goal-review` reads this field before anything else (its
§1) and carries `capped` onto the Epic body it compiles, so a partial mapping reaches the ticket the
team actually reads and not only a file whose reach depends on the repo's own ignore rules (the
publishing step below is where that is measured, never assumed).

**`Lane` is written on every artifact too, and for the reason `Sweep` is** (#1954). It records the
size this pass MEASURED the target at (§2), and it is the only thing that lets a reader tell a
proportionate artifact from a lazy one: a three-page design over a one-file change and a one-page
design over a six-component change are both failures, and they are indistinguishable without it.
Write the lane the pass actually ran at, never the one the target's own text claimed — the same rule
`Mode` already carries one line to its left. `small` with a one-row slice table is §4's light path;
`medium` is what an unsized target resolves to, and it is not a way of declining to size one.

**`Budget` is written on every artifact, and it is the FETCHED number, never a hand-typed one**
(#2032). §2 already says to call `goal_design.py sweep-budget` before sweeping at all — this field
is where that call's answer is recorded, so a reader (and `goal-review`, which re-runs the same
verb) can tell a genuine ceiling from a number remembered out of habit. Write exactly what the verb
returned: `3` on an unconfigured `full`-mode install, whatever `goal_design.rounds` resolved to
where the operator raised it, or the fixed lane figure (`small`=1, `medium`=2, `large`=3) under
`mode: lane`, where the config key is inert. Omitting the field or writing a number the verb did
not produce is the same failure `Sweep`'s own omission is — indistinguishable from an honest one
until someone checks, which `goal-review` step 1 now does.

**Doubts and Blockers carry ids — `D-1` and `B-1`, upward, in document order** (#1955). Three
consumers cite these lists and none of them can invent an id for itself: `goal-review` §2a has to
bucket **every** doubt and blocker in writing and names each by its id, this document's own
`Blockers` entries name the slice they gate (see `Depends on` below), and `compile_plan.py` already
RECOGNISES the shape — `_DESIGN_ARTIFACT_ID_RE`, `^(?:BR|D|B)-\d+$` — to refuse a `blocked_by` key
that is really one of them. Number from 1 in the order the items appear, and **never renumber**: a
later pass that renumbers invalidates every citation already written against the old numbers, in
comments nothing here can reach. An `open_*` id inherited from the Dossier (§1) stays in the item's
own text — the `D-n` is additional, never a replacement. Under either heading, "none" is written as
a single `- None.` bullet and takes no id. The live run had to mint `D-1`…`D-8` and `B-1` for
itself, which is a scheme two runs would disagree about.

**Blast-radius rows name their component, and a long table may be grouped** (#1955). The
`Component` cell names a row of the `## Components` table below it, or `—` where a site belongs to
none: that is the only cross-check between the two tables, and it is what lets the reader of thirty
rows find the six that bear on them. Order the rows so each component's are contiguous. Past **20**
rows you may additionally split the table under `### <Component>` sub-headings, each repeating the
header and its `| --- |` separator row; the columns never change, so `goal-review` still reads one
shape. Those `###` sub-headings are the one **exception** to "write exactly these, in this order"
above — nothing else may be added. The run that motivated this produced 33 rows, about 90 lines, in
a single flat table whose only lever was the `Components` table above it.

**`Seeds` is what makes `converged` and `capped` checkable at all** (#2028). §2's stopping rule is
defined over the seed set — "every seed has been swept" — and until this heading existed the artifact
had no place to put one, so the set lived in the pass's head and both verdicts were claims about
something nobody else could see. This table is that record, and it is written on **every artifact**:
on a `converged` one it is the evidence for the strongest sentence in the document, and on a `capped`
one it is the continuation list §2 hands the next reader. Its consumer is `goal-review` step 1, which
reads it before the premise check and compares the count of `unswept` rows against the banner's own
number — the one arithmetic check the artifact offers without re-running a query — and reports a
mismatch, or a missing table, as a schema violation rather than adjudicating it.

Say what it buys and no more. The table **does not raise the budget** and does not lengthen the
sweep; it does not make the seed set correct, only visible and disputable, the way `Out of scope`
does for the scope call below. Nothing in this kit executes it. And it cannot show **a seed the pass
never held** — a symbol nobody thought to sweep leaves no row, exactly as an unsurfaced hit leaves no
exclusion. What it does buy is that the two questions the budget argument turns on — which seeds are
still open, and whether the last round was still finding new ones — are answerable from the document
instead of from the banner's own say-so.

**`Out of scope` is what makes the `converged` verdict auditable** (#1975). #1952 made a capped
sweep loud in four places and that worked — but it fixed the REPORTING and not the JUDGEMENT beneath
it: `converged` still rested on the author's own after-the-fact call about what counted as in scope,
with nothing recording the call and nothing able to check it. This heading is that record. Every
entry carries `X-n`, the **round** it was excluded in, the hit (or a group rule and its count), and
the reason — and per §2 a round is quiet only once every hit it returned is either a blast-radius
row or one of these. The round number is the load-bearing field: #1975's own scenario is a pass that
quiets its final round by excluding one file, and only the round makes that legible to a reader.
**Be honest about the guarantee** — this makes the call **visible and disputable**, not correct, and
nothing in this kit executes it; the check that does not rest on trusting the pass is re-running a
recorded query from `## Queries run` and comparing its file list against blast radius ∪ this
section. `goal-review` 2a adjudicates these the way it adjudicates Doubts, and an exclusion it
disagrees with is a blast-radius row that is missing — which is 2b's own REJECT ground, not a note.

**`Premise check` is where a source claim that did not survive contact with the code goes** (#1976).
§1 has always said to treat every claim in the source as a hypothesis; this is the heading that
records the verdict, so it stops being invisible. On the live re-run the Dossier claimed
`/sigma-doctor` stops at the repo boundary, the sweep found six places where it crosses, and with no
slot for that the correction went into `Intent` as prose — where `goal-review`, which adjudicates
`Doubts` and `Blockers` **by id**, could not see that the framing had moved at all. Each material
claim gets `PC-n`, one of `verified` / `falsified` / `unverifiable`, and its evidence. A falsified
row obliges more than a note: `## Intent` states the corrected premise and says it moved, and the
slices below are derived from the corrected intent — if the source's problem statement is wrong,
every slice derived from it inherits the error, and this stage is the last one that can see the
original claim beside the code that disproves it.

**`Depends on` carries slice ids from this same table, and nothing else** (#1956). One or more of
this table's own `Slice` values, comma-separated — the cell is read by splitting on commas and
stripping each token — or `—` when there are none. Never a `Blockers` id (`B-1`), never a `Doubts`
id (`D-3`), never a `Blast radius` id (`BR-7`), never a `Premise check` id (`PC-2`), never an
`Out of scope` id (`X-5`), never a `Seeds` id (`S-2`), never an issue number, never prose. The
column is machine-read: `goal-review` step 4b turns each token into a `compile_plan.py` `blocked_by` key, and
a `blocked_by` key may only name another issue **in the same plan** — a Blocker is a paragraph in
this document, not a ticket, so there is nothing for such a key to resolve to. A real pass wrote
`3, and B-1` in this cell; the `B-1` was patched away by hand between the two stages, silently, and
nothing recorded that it had ever been claimed.

**A slice that also waits on a Blocker records that under `Blockers`, not here.** Write the edge
the other way round: the Blockers entry names the slice it gates and what that slice is waiting
for. That is `goal-review` §2a's own operational test for its **blocking** bucket — *name the slice
that cannot start, and say what it is waiting for* — so a dependency written that way is
**adjudicated** at Stage 2 (resolved, open-not-blocking, or a REJECT) instead of being dropped, and
keeping the column pure costs nothing.

**Then link it from the source issue's own comments** — `loop.py note` on the retrofit path, a plain
comment on the Dossier path. Four things about that comment, in order:

- **When `Sweep` is `capped`, that is the comment's FIRST line** — ahead of the site count, ahead of
  everything. Not a caveat at the end: the reader who stops after one line is exactly the reader who
  has to learn that the mapping is partial (#1952).
- **It stands on its own; never post a bare path — but "stands on its own" means the IN-BRIEF
  content, never a compressed dump of the machine artifact** (#2064). The comment IS (or opens with,
  verbatim) `<n>-in-brief.md`'s content: what was checked, what it found, what needs a human
  decision, the plan. What it must NOT carry is what an earlier version of this rule asked for —
  "every doubt and blocker in full" — because Doubts and Blockers are written in the main artifact's
  own citation register (`file:line`, `os`/`op`/`oi`-style shorthand, cross-references to ids defined
  nowhere in the comment itself), and reproducing that register in the one place most readers will
  ever look does not satisfy self-sufficiency, it just relocates the unreadable version. A comment
  that names the site count and links the two files, with the in-brief's own plain sentences filling
  the body, has lost nothing a reader needs and gained the property that a reader who never clicks
  through still understood the design. This is the rule `sigma-research` already applies to its own
  note — carry the substance, don't make the reader open a working copy — and it matters more here
  because on a shared backlog the comment may be the only half anyone reads before confirming.
  **Both files are markdown LINKS to their GitHub blob, never a bare filename in a code span** — a
  reader on the issue should reach either file in one click, not read a path and go find it
  themselves. Once the branch/PR/local-only decision below is made, the blob URL is knowable; write
  it, not `` `.sdlc/design/<n>.md` `` on its own.
- **Measure whether the file can reach the remote at all — do not assume it.** ("The file" below
  means both siblings — `<n>.md` and `<n>-in-brief.md` land or stay local together; they are one
  directory and one commit, never split.) `sigma-setup`'s
  `RUNTIME_IGNORES` deliberately leaves `.sdlc/design/` trackable, so every repo `/sigma-setup`
  touched already tracks it — **and Sigma's own now does too** (#1953: its blanket `.sdlc/` rule
  became `.sdlc/*` plus an explicit `!.sdlc/design/`, because a negation under an excluded directory
  is inert). What remains is a repo that set a blanket rule of its own, and that is the one place an
  adopter's experience can differ from what these docs demonstrate — so measure it, and infer it
  from neither direction. Run `git check-ignore -v
  .sdlc/design/<n>.md`, and read its exit code the right way round: **exit 0 means the path IS
  ignored** and prints the matching rule (`.gitignore:8:.sdlc/`, then the path), while **exit 1
  means it is NOT ignored** and prints nothing at all — silence plus non-zero is the good case
  here, and only 128 is a real failure. Anything that reads non-zero as "the command failed"
  concludes the exact opposite of the truth. If it is ignored: say so in the comment ("artifact is
  local-only in this repo"), and stop. Never `git add -f` past a repo's own ignore rule.
- **If it is tracked, it lands the way everything else lands.** On the goal's own `sdlc/*` branch,
  through `work.py commit` and a pull request — never a direct commit to the base branch, which is
  what AGENTS.md's "Nobody commits directly to a feature branch" rule exists to prevent. And where
  §6 below has already told you to skip `work.py` entirely (a "Design #N" meta-issue with no code
  changes of its own), there is no branch to carry the file: it stays local, the comment is the
  whole deliverable, and `goal-review`'s reviewer reads the file from its own checkout — the link
  is for humans, never for the pipeline. **The Dossier path is branchless too, for a different
  reason** — a Dossier is `story`-labelled and never `sdlc:goal` (`sigma-dossier`: the loop must
  never try to execute one), so no `sdlc/*` worktree is ever cut for it and `work.py commit`
  answers `not started`. Cut one yourself with `work.py start .sdlc <n>` before committing, or take
  the local-only route above and let the comment be the whole deliverable — and say in the comment
  which of the two you did. **On a first cut that command only creates a worktree, but re-running it
  on a goal that already has one is a RESUME, and a resume is no longer side-effect-free** (#2009):
  if that worktree has fallen behind its base it is rebased, and if it cannot be, the command exits
  4 having already parked or released the goal. That is the right behaviour for the loop; just do
  not re-run it here expecting a no-op. What is never an option on any of these three paths is the one the
  validation run took: a direct commit to the base branch.

