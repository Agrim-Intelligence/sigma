---
name: sigma-kg
description: Build, refresh, or query the optional project knowledge graph. Use for graph requests or /sigma-kg.
allowed-tools: Bash(python3 *), Bash(graphify *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-kg

Detailed selection triggers: [selection](references/selection.md).

**Optional. Only acts when `.sdlc/config.json` → `knowledge_graph.enabled` is `true`.** The graph
accumulates two things, to keep enhancing the project's learnings:

- **External research** — auto-captured from every `WebSearch` / `WebFetch` into
  `.sdlc/knowledge/research/web/` (the `research_capture` hook does this whenever KG is enabled).
- **Internal analysis** — durable findings + lessons you write as markdown to
  `.sdlc/knowledge/analysis/` during Research / Plan-Review / Retrospective.

At `scope: full` the **code** is graphed too; at `scope: research` the code is skipped.

1. **Check status:** `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" status .sdlc`
   If it reports *disabled*, tell the user how to turn it on (set `knowledge_graph.enabled: true`;
   optionally `scope: "research"` to skip code) and stop.
2. **Get the plan:** `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" plan .sdlc .`
   It prints the corpus path, whether the code is included, and the builder.
3. **Build / refresh** with the builder (default `graphify`) over the planned inputs:
   - `scope: research` → run the builder on `.sdlc/knowledge/` only.
   - `scope: full` → run the builder on `.sdlc/knowledge/` **and** the repo code, merged into one graph.
   - On re-runs use the builder's incremental update. For graphify you may invoke the `/graphify`
     skill on those paths, or the `graphify` CLI directly.
   - If the builder isn't installed, say so (for graphify: `pip install graphifyy`) and stop — the
     SDLC keeps working without it (graceful degradation).
4. **Query** the graph to retrieve learnings: e.g. `graphify query "<question>"`. graphify saves the
   answer back into the graph — that closes the enhancement loop, so each query makes the next better.
   A query that comes up empty is logged as a **gap** (`kg.py gap log "<q>"`, done automatically by
   `/sigma-context`); review the backlog of what the graph doesn't know yet with
   `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" gap list .sdlc`.

5. **Maintain** (periodic, report-only) so the corpus self-cleans instead of bloating into noise:
   `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" maintain .sdlc .` audits the corpus — analysis notes
   citing a repo path that no longer exists (**stale**), byte-identical notes (**duplicates**), size
   vs a threshold, and the web-capture set vs its derived bound (below). It only *proposes*:
   **archive, don't delete**, and **a trim of the shared analysis notes always needs your approval**.
   Since #2702 every proposal carries an `apply:` line — the exact, quoted command — and nothing runs
   it but you. Two shapes: when `analysis/` is git-tracked (the shared ops-branch worktree, where a
   `.git` *file* sits in it) a tracked note gets `git rm` plus one commit — the commit IS the archive,
   history keeps the note (`git -C .sdlc/knowledge/analysis log --all -- <note>`) and every synced
   machine shrinks; a plain-file or not-yet-committed note gets `mv -n` into `knowledge/archive/analysis/`
   (declines silently if that name is already archived — the note stays live and is re-proposed next
   run; move or remove the archived copy). Paths are relative to where you ran `maintain`.

## Web-capture retention (`web_retention_enabled`, opt-in)

`research/web/` gains a file per `WebSearch`/`WebFetch` and nothing pruned it — one machine measured
2,814 files in 40 days, 11.5 MB on disk. With `knowledge_graph.web_retention_enabled: true`, every
recorded goal (the same hop as `auto_refresh`, but independent of it) runs `kg.py retain` first:
captures older than `web_retention_days` (default 90; `0` = off) are archived, and then, oldest first,
enough more to bring the live set under a byte bound **derived from this machine** —
`web_retention_disk_share` (default `0.0001`) of the total size of the disk holding `.sdlc/knowledge`,
counted as disk occupancy: 49 MB on a 494 GB disk, 10 MB on a 100 GB laptop. Archive, never delete:
files move to `.sdlc/knowledge/archive/research/web/` under the same names, the count is printed on
the console whenever anything moved, a second pass moves nothing, and a name already in the archive
is skipped, never overwritten — a skip is reported on every pass until you move or remove the
archived copy (that is the lever; usually a `cp` back where a `mv` was meant). A malformed value
refuses and moves nothing. Undo is `mv` back. Hand run:
`python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" retain .sdlc` — typing the verb is the opt-in.

Two limits, stated: the archive stays inside `.sdlc/knowledge/` (the gitignored tree), so a **cold**
build still reads it once — graphify's cache keys on content, so a rename is not re-billed — and if
you want it out of the graph, deleting it is your gesture, not Sigma's. And occupancy bounds the
count too: at 4 KiB per file the default share keeps ~12k live captures on a 494 GB disk; a 100k-file
directory needs a ≥ 4 TB disk at that share, so lower `web_retention_disk_share` there.

## Automatic refresh (`auto_refresh`)

When `knowledge_graph.auto_refresh` is `true` you do not run any of the above by hand. `loop.py
record` — the one call both `/sigma-loop` and `/sigma-goal` end a goal with, on every host — invokes
`kg.py refresh` once the outcome is recorded with a `--retro-grade`, i.e. at the end of
Retrospective. It runs `<builder> extract <corpus> --out <repo root>`, so the graph lands exactly
where step 1's `status` and `/sigma-context`'s gate look for it.

Two things to know before turning it on:

- **It needs an LLM backend, and Sigma deliberately does not choose one for you.** Picking a
  backend is the builder's job; doing it here would mean silently selecting something that bills
  you. With none configured the refresh fails on *every* goal — it says so on the console each time,
  and `/sigma-doctor` carries a standing `knowledge graph auto-refresh is working` row. For graphify,
  set one of `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`,
  `MOONSHOT_API_KEY` or `OLLAMA_BASE_URL`.
- **Cost is front-loaded, not per-goal.** The first build extracts the whole corpus; graphify keeps a
  per-file content cache, so each later refresh re-bills only the documents that changed — normally
  the single note Retrospective just wrote. A run is capped at 15 minutes
  (`knowledge_graph.refresh_timeout_seconds`) so a cold build can never wedge a completed goal.

`scope: full` is **not** auto-refreshed — it needs a separate code extract plus a graph merge, so
`refresh` declines it by name rather than building half a graph. Run `/sigma-kg` by hand there.

**When it is not working, it says so where you are (#2704).** With `auto_refresh` on and the graph
never built — or older than the newest corpus document — session start (Claude Code hook) and every
`loop.py next` pick (Sigma's own Python, so Cursor and Codex too) print one line, at most once a
day per repo: the state, the prerequisite it can name without guessing (the builder missing from
PATH, else the last refresh's own recorded words, else that no refresh was ever attempted here), the
measured corpus size, and that the first build's cost was not measured. `kg.py refresh` records its
last real outcome in `.sdlc/state/kg-refresh.json`; the `/sigma-doctor` row quotes that same record
instead of guessing at the cause. Nothing is printed when the graph is fresh, `auto_refresh` is off,
or the graph is disabled. `python3 "${CLAUDE_SKILL_DIR}/scripts/kg.py" warn .sdlc` prints the line
by hand (empty when nothing is due).

## First build

The first build is the expensive one: every document is extracted once, and only changed documents
are re-billed afterwards. Before running it, state what is measured and only that:

1. **Size — measured, by `status` (step 1).** Its line ends `; <N> documents, <size>)`: every `.md`
   under `.sdlc/knowledge/`, the exact set the builder is handed. Say those two numbers to the user.
2. **Time and cost — not measured.** Sigma has not run a builder end to end against a real
   corpus, so it has no figure to give and does not invent one: as of 2026-09-26 this kit's own
   repository carried a 3,246-document, 2.3 MB corpus that auto-refresh had never built, and no
   record of why (nothing had recorded the builder's words until this change). Say "not measured" until you have a number of your own.
3. **Then run it** (`/sigma-kg` step 3, or `<builder> extract .sdlc/knowledge --out .` for graphify
   at `scope: research`), note the wall-clock and whatever spend the builder or backend reports, and
   record both in the analysis note for the goal that did it — that is the measurement the next
   person gets to quote.

## Sharing notes across machines (`sync`)

Analysis notes are written by Retrospective **after** the goal's PR has merged, so they have no PR
to ride and would otherwise stay on the machine that wrote them. With `knowledge_graph.sync.enabled:
true` (strict, and `knowledge_graph.enabled` must be true too) they share the ledger's ops-branch
pattern: `.sdlc/knowledge/analysis/` becomes a git worktree on a dedicated branch (default
`sdlc-knowledge`, remote `origin`) that is never merged into any code branch and stays gitignored
on every code branch. Only `analysis/**/*.md` leaves the machine — never `research/`, `gaps.md`, a
graph build, or any non-note file dropped beside the notes: the worktree's own `.gitignore` ignores
everything and re-includes only `*.md` (plus its two scaffold files), so git enforces the claim.

- **Bootstrap once per clone:** `python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/sync.py" bootstrap
  .sdlc --channel knowledge`. Existing plain notes are carried in; a note that already exists on the
  branch under the same name is unioned line-wise and reported. A second run is a no-op.
  `/sigma-doctor` carries a `knowledge notes sync initialized` row while sync is on.
- **Trigger:** `loop.py record … --retro-grade <grade>` — the same gate as `auto_refresh` — publishes
  this machine's notes, pulls everyone else's, then runs the refresh, so the rebuild reads the union.
  Same-named notes EDITED on two machines union-merge (`*.md merge=union`) rather than conflict;
  a note deleted on one machine and edited on another is resolved automatically by keeping the
  edit. See "When a note conflicts" below for the one shape neither covers.
- **A pulled note reads as a stale graph until this machine's next `record`.** Pull and rebuild
  happen in the same hop, so a teammate's note lands in *your* graph at your next goal end, not
  theirs. Nothing reads the graph mid-goal across machines, so this is the agreed scope.
- **Publish refuses a secret-shaped note**, names the file, never prints the match, and stages
  nothing — at bootstrap over your existing notes exactly as at every later `record`. The refused
  note stays local (pull keeps working unless the branch also changed that same note). Edit the
  note and re-run `sync.py publish .sdlc --channel knowledge`. The recogniser is the shared
  `scrub.py` table. The gate inspects what publish is about to stage; a note you `git commit` by
  hand inside the worktree is already past it and is pushed as committed. Deleting a note ships
  as a deletion and is never refused.
- **A failed push is retried at the next `record`** (the branch is pushed whenever it is ahead of
  the remote, not only when a new note was staged); `sync.py publish .sdlc --channel knowledge` is the
  polite manual lever. Two goals ending at once on one machine do not contend: the second defers.

### When a note conflicts

Two shapes resolve themselves: two machines editing the same note (union-merge, both texts kept)
and one machine deleting a note another has edited (the edit is kept — during the rebase publish
takes the surviving side and continues). What does not resolve itself is a conflict where BOTH sides
changed the content of a file that has no union rule — in practice a hand-edited `.gitattributes` —
or any other shape the resolver does not recognise. Publish or pull then
reports `note conflict in <file> could not be resolved automatically`, aborts the rebase (nothing is
lost, your commits are intact) and says so on every record until you resolve it by hand, inside the
worktree:

```
cd .sdlc/knowledge/analysis
git fetch origin sdlc-knowledge && git rebase origin/sdlc-knowledge
# edit the named file, then: git add <file> && git rebase --continue
python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/sync.py" publish .sdlc --channel knowledge
```

An embedded git repository under `analysis/` is never published (it would ship as a bare gitlink,
not a note); publish skips it, names it in its outcome on every record, and the notes beside it
still ship — move it out of the corpus to silence the line.

Keep `.sdlc/knowledge/research/` and the builder's output (e.g. `graphify-out/`) out of git — they're
machine-accumulated. `.sdlc/knowledge/analysis/` is shared through the ops branch when `sync.enabled`
is on; otherwise commit it by hand if you want curated learnings versioned.
