# Mapping to the codebase — depth, sizing, sweep and the budget

The detail behind step 2 of [`../SKILL.md`](../SKILL.md): sizing the target before sweeping it,
resolving `mode` on each of the two paths, the exhaustive-over-seeds stopping rule, the
`Every hit is accounted for` scope-exclusion accounting, and fetching the sweep-round budget from
the engine rather than assuming or remembering it.

---

## 2. Map to the codebase — depth per `goal_design.mode`

**Size the target before you sweep it — on BOTH modes** (#1954). Classify it the way
`discovery.py`'s own `LANES` (`small`/`medium`/`large`, `DEFAULT_LANE = "medium"` on anything
unsized) already sizes an ordinary goal, and write the answer into the artifact's `Lane` field (§5).
`discovery.py lane` will not answer this for you: it reads a goal FILE's frontmatter, and a Dossier
is an issue number, which that function returns `DEFAULT_LANE` for by its own admission. So this is
your judgement in its vocabulary — made from the target's own text, and revised once the first sweep
round has told you more than that text could.

**`mode` is a CEILING on the pass, never a floor under it.** The measured lane is what the target
earns; `mode` only bounds how far the pass may go looking for it. That is `agrim-goal`'s own Lane
routing — *ceremony proportional to the work* — applied one stage up, and without it a `--quiet`
flag on a `full`-configured repo buys the identical treatment as a six-component schema change,
which is the whole of #1954. An unsized target resolves to `medium`: more rigour rather than less,
exactly as `DEFAULT_LANE` already decides it for an ordinary goal.

- **`full`** (the default): the ceiling is the comprehensive pass — sweep every file/component the
  intent plausibly touches, trace callers, and do not stop at the first plausible answer, the way
  `agrim-research`'s own blast-radius sweep treats a `large`-lane goal. On a target that measures
  `small`, run the pass that lane earns and keep the round budget in reserve, so a target that turns
  out bigger than it looked is never cut off mid-sweep.
- **`lane`**: the same sizing, with the ceiling brought down onto it — and the budget scales with it
  too (one round on a `small` lane, below). That is the whole remaining difference between the two
  modes: not how the pass is sized, but whether an under-sized target gets cut off and has to say
  `capped`.

**Who resolves `mode`, on which path — and why `design-check` is not the place to ask.** On the
**retrofit** path it is already resolved for you: `design_check` picked `full` or `lane` and
`design_goal.render_meta_body` wrote the matching depth instructions into the "Design #N"
meta-issue's own body, so read it there. On the **Product** path nothing in the code resolves it,
and `loop.py design-check` will not tell you: it returns `OFF` at its first line whenever
`goal_design.enabled` is not `true` — the gate-inside-the-guard, before `mode` is read at all — and
even with the gate on it resolves `mode` late, only once it has decided to park-and-file a retrofit
meta-issue, which is not something that happens on this path. An agent that invokes it to verify
what depth it should be running at **learns nothing** (#1958).

So on the Product path, read the configured value yourself, with the same precedence
`design_check` applies — absent resolves to `'full'`, anything that is not `'full'` or `'lane'`
falls back to `'lane'`:

```
python3 -c "
import sys; sys.path.insert(0, '${CLAUDE_SKILL_DIR}/../agrim-loop/scripts')
import state
mode = ((state.load_config('.sdlc').get('goal_design') or {}).get('mode') or 'full')
print(mode if mode in ('full', 'lane') else 'lane')
"
```

`load_config` raises `ConfigMissing` where there is no `.sdlc/config.json` at all — there is no
configured value in that case, so the answer is `full`. And `goal_design.enabled` does **not** gate
this read: `enabled` turns the retrofit GATE on and nothing else (contract §6a), while this axis is
how deep the pass goes, which applies on the Product path whether or not that gate is on. Write
whichever value you resolved into the artifact's `Mode` field (§5) — the depth this pass actually
ran at, never the depth it meant to.

**Sweep with commands the grant already covers.** Inventory files with `git ls-files` (scope it with
a pathspec — `git ls-files -- 'skills/**'`), search with `git grep -n`, `grep -rn` or `rg -n`, and
where a hit is not enough read the file whole with `python3`. `find`, `ls`, `wc` and `cat` are NOT
granted — see the frontmatter note above for why the answer is a narrower instruction rather than a
wider list — so a pass that reaches for one is prompted on its first command on a host that enforces
the grant. The tracked set `git ls-files` returns is the right inventory anyway: anything untracked
is scratch or ignored, not code this design has to hold against.

Record every query you actually ran, verbatim — the same "coverage is guaranteed by re-running the
query" discipline `agrim-research` §2 uses, not by trusting today's list was complete.

For each site found, name what it is and why it's affected. Don't silently drop something uncertain
— list it under doubts (next step) instead.

**Every hit is accounted for — on the blast radius, or under `## Out of scope` with its reason**
(#1975). The stopping rule below turns on the phrase *no new **in-scope** site*, and #1952 made the
outcome of that judgement loud in four places without ever saying who makes it or how anyone could
check it. The re-run's own pass wrote the problem down: *"I could have kept round 3 quiet by ruling
`hooks/session_start.sh` out of scope. Nothing checks that call."* A pass that wants a clean result
can produce one by narrowing its own scope judgement after the fact, and the artifact then says
`converged` with exactly the authority an honest one has.

The answer is not a second self-assessed number — a confidence score on the scope call imports the
same problem one level up. It is to put the call **on the page**, the way an unswept seed already
becomes a Doubt:

- **The test a hit is judged against, stated once.** A hit is IN scope when the change this intent
  implies would have to touch it, **or** when it would break if that change shipped as designed.
  Everything else is out — and the exclusion entry says which half of that test the hit failed.
- **The judgement is written before it is acted on.** Every file a recorded search query returned
  that is not already a blast-radius row is EITHER added to the blast radius OR written under
  `## Out of scope` as `X-n`, carrying **the round it was excluded in**, the hit, and the reason.
  The round number is what makes the scenario above visible: a reader can see that the round which
  produced the quiet signal was the round carrying the exclusions.
- **A round with unaccounted-for hits is not quiet — it is unrecorded**, and the pass may not stop
  on it. This is the whole mechanism. `converged` no longer means "I judged the last round quiet";
  it means "every hit of the last round is on this page, and none of them was in scope".
- **Grouping keeps it bounded.** One `X-n` may cover a group of files under a stated rule with a
  count (for example *"the 827 files under `frontend/` — a different lane of this repo, untouched
  by this intent"*), because a broad query otherwise makes the section unwritable. The rule is the claim a
  reviewer disagrees with; a group whose rule is wrong is wrong as a whole, which is exactly the
  granularity a reviewer wants. The accounting is over **search** queries (`git grep`, `grep -rn`,
  `rg`) — an inventory query like `git ls-files` establishes the universe a search runs over, not a
  set of candidate sites.
- **What this buys, and what it does not.** It makes a wrong scope call **visible and disputable**;
  it does not make it right, and nothing in this kit executes the check. What makes it more than
  another honour-system claim is that `## Queries run` records every query verbatim: blast radius ∪
  `## Out of scope` is checkable against those queries by re-running one and comparing file lists,
  which is the same re-scan guarantee §5 already sells and the only part of this that does not
  depend on trusting the pass's own account. It still cannot catch a hit the pass never surfaced at
  all — that is a coverage limit, answered by the seed set and the round budget, not by this.

**Stopping rule — `full` is exhaustive over the SEEDS, not over the repo.** "Every file the intent
plausibly touches" is unbounded on any real codebase, so the completion criterion is the seed set,
which is finite and written down. Extract the seeds from the target's own text the way
`agrim-research` §1 does (the modules, functions, fields, endpoints, commands and behaviours it
names), sweep each one, and add to the seed set only the new symbols a sweep actually surfaced.
Stop when **both** hold:

1. **Seed closure** — every seed has been swept, and every symbol a sweep surfaced is itself either
   swept or written down under Doubts with the reason it was not. **This is judged against the
   `## Seeds` table you are writing as you go (§5), never against memory**: every seed the pass ever
   held is a row there, carrying the round it entered in, the round it was swept in, and `swept` or
   `unswept`. A seed that lives only in your head cannot be closed over — the next reader cannot
   tell it was ever considered from one you never thought of, and both look identical to a
   `converged` claim.
2. **A quiet round** — the most recent sweep round added no new in-scope site. **A site is new
   when it is a file or component not already on your blast-radius list** — one row each, per §3 —
   and never merely a new grep line inside a file that list already carries. Read the other way
   round, a query that re-hits a file you already have counts as productive and the sweep is
   unbounded on any codebase whose files answer to more than one query; read this way the test runs
   over a finite set that only grows, so it terminates. One such round is the signal; a second
   confirming round buys nothing and is not required. **A round is quiet over the exclusions AS
   WRITTEN**, never over a scope call held in your head: every hit it returned is a blast-radius row
   or an `X-n` entry before the round can be called quiet at all (#1975).

**Fetch the budget from the engine — never assume it, and never write it from memory** (#2032). Once
you know `mode` and the `Lane` you measured (§2, above), ask the engine what the ceiling is:

```
python3 "${CLAUDE_SKILL_DIR}/scripts/goal_design.py" sweep-budget .sdlc --mode <mode> --lane <lane>
```

This prints `{"rounds": <n>, "mode": ..., "lane": ..., "rounds_config_ignored": ...}`. `rounds` is
the number of sweep rounds you may spend before you are required to stop and report `capped` —
engine-owned data behind a CLI verb, the identical shape `dossier.py`'s own `followups()` already
uses, so the number a pass obeys is **read back**, never remembered. Write it into the artifact's
`**Budget**` header field verbatim (§5). **Do not skip the call and write `3` from memory**: this is
exactly the hole the #2032 adversarial review found — an artifact whose `Budget` field was typed
rather than fetched is indistinguishable from a correct one to a reader, which is precisely why
`goal-review` step 1 now runs this same verb itself and compares its answer against what you wrote.
A pass that writes a number the verb did not return fails that check, whatever the number is.

Under `mode: full`, an optional `goal_design.rounds` (a positive integer) in `.sdlc/config.json`
raises the ceiling above the unconfigured default of **3** — an operator who wants a design pass to
"scan the whole product" sets this, rather than the pass inventing its own reserve, and a genuinely
converging sweep still stops on its own quiet round long before a raised ceiling is ever reached.
There is no `"unbounded"` value: each round is real Anthropic spend, so "opt into more" means a
number the operator chose, never an unbounded loop. An absent key resolves to 3, byte-identical to
every install before this issue.

Under `mode: lane`, `goal_design.rounds` is **inert** — the ceiling stays fixed at one round per
measured lane (`small`=1, `medium`=2, `large`=3) regardless of what the config says, and the verb
reports `rounds_config_ignored: true` plus a stderr note naming `mode: full` as the escape hatch
when a value was set anyway. Nothing in the three real design passes on record ever needed lane's
own ceiling moved; `full` is where "raise the budget" belongs.

**Reaching the budget is a legitimate outcome, not a failure** — counted as the rounds whose
queries you recorded, against the number the verb above returned. It is a **different** outcome
from converging, and the artifact has to say which one happened. Ending on the cap means
**seed closure was not reached**, and the mapping below it is partial, so stop and record that in
all four of these rather than one:

1. the artifact's `**Sweep**` header field — `capped`, and it is never omitted (§5);
2. the banner directly under that header, naming how many seeds are still unswept and which (§5);
3. the `## Seeds` table — every seed still unswept carries `unswept` in its `Status` cell and names
   the `D-n` that carries it in `Carried as` (§5);
4. a `Doubts` entry per unswept seed, saying why it was not swept.

**The banner's count IS the table's count.** The `<k>` the banner states is the number of `## Seeds`
rows marked `unswept` — one number, written twice, so a reader who only skims the top gets the same
fact as one who reads the table. A banner that disagrees with its own table is a **schema
violation**, not a rounding difference, and `goal-review` step 1 reports it as one: it is the single
arithmetic check the whole artifact offers a reviewer who has not re-run a query.

**The unswept rows ARE the continuation list**, and that is what ending on the cap leaves behind —
not an apology, a starting point. Whoever picks this up next works those rows, in that order, rather
than re-deriving the seed set from the intent. Say so plainly, and stop overclaiming in the same
breath. **Nothing in this kit re-runs a design pass automatically**: there is no resume entry point,
and a second pass on the same `<n>` overwrites this file. The table is what makes a continuation
*possible*; it does not perform one, and it does not make the sweep longer.

Then repeat it as the FIRST line of the handoff comment (§5), because on a shared backlog the
comment is the half most people read. The run that motivated this (#1952) spent the full three
rounds (the whole budget of that era, before #2032) and 24 queries on 33 sites, was still adding
in-scope files in round three, and recorded the whole fact as one bullet at the bottom of a 31KB
document — above which a tidy slice table read as finished work. **A design that is honestly
incomplete and says so at the top is useful; one that is incomplete and looks finished is worse
than none.** If the FINAL round of your budget — whatever it was fetched as, not necessarily round
three — was still turning up *new components* (not just new sites in components you already have),
that is itself a finding: record it under Blockers as "the intent's radius exceeds one design
pass", because a target that cannot be mapped inside the budget needs splitting before it is
designed, not a longer sweep. **The budget is where the two modes differ, and it is the only place
they do.** Under `full` it is `goal_design.rounds` if the operator set one, else the reserve of 3,
whatever the lane measured — the reserve that lets a target bigger than it looked finish rather
than end partial, and the ceiling an operator raises when they want a pass to scan the whole
product. Under `lane` the config key is inert and the budget scales with the measured lane instead
— one round on `small`, two on `medium`, three on `large` — so an under-sized target ends on the
cap and says `capped` instead of quietly growing into a pass the operator did not ask for.

