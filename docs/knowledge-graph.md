# Knowledge graph

The optional, self-improving project memory. Back to the [README](../README.md).

## Self-improving knowledge graph (optional, off by default)

Sigma can accumulate a **knowledge graph** of what it learns, so research and analysis compound
across runs instead of evaporating — and it gets *sharper* over time, not noisier. It's **opt-in**
(`knowledge_graph.enabled: false` by default) and built by an external tool (default **graphify**,
`pip install graphifyy`) — the core stays zero-dep.

**Write side — what feeds it** (two objectives: *enhance the learnings* and *build a knowledge base
around the code*):
- **External research** — every `WebSearch` / `WebFetch` is auto-captured to
  `.sdlc/knowledge/research/web/` by a fail-open hook (only when KG is enabled; a hard no-op otherwise).
  The breadcrumb is a *scrubbed summary* — source + subject + a short excerpt with secret-shaped
  substrings redacted, never the raw page — and `.sdlc/knowledge/` is gitignored, so captures stay local.
- **Internal analysis** — durable findings and Retrospective **lessons** you write to
  `.sdlc/knowledge/analysis/`.
- **The code** — graphed too, but only at `scope: full`.

Turn it on in `.sdlc/config.json`:
```json
"knowledge_graph": {
  "enabled": true,
  "scope": "full",
  "builder": "graphify",
  "auto_refresh": false
}
```
`scope` is **`full`** (code + external research + internal analysis) or **`research`** (skip code —
internal analysis + external research only). Then **`/sigma-kg`** builds, refreshes, and queries it;
a query does not save anything by itself. Saving the answer back into the graph is a separate step,
`graphify save-result` (the `/graphify` skill's query flow calls it), which files the answer under
`graphify-out/memory/` for the next build to read; that is what makes each query improve the next. The bare
`graphify query` command only prints (and appends to graphify's own query log, outside the graph). Checked against the installed graphify's help and source on 2026-10-14.) The builder is a **soft dependency**: if it isn't installed, `/sigma-kg` says so and the rest
of the SDLC runs unaffected.

`auto_refresh: true` rebuilds the graph at the end of each Retrospective **without being asked** —
`loop.py record` invokes the builder itself whenever a goal is recorded with a `--retro-grade`, so it
works the same on Claude Code, Cursor or Codex. It needs an **LLM backend for the builder**, and
Sigma never picks one for you (that would silently bill you): with none configured every refresh
fails and says so, `/sigma-doctor` carries a standing row for it that quotes the builder's last recorded words, and a graph that was never built (or has fallen behind the corpus) is announced at most once a day, by whichever fires first — session start or a `loop.py next` pick — on every host. For graphify set one of
`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY` or
`OLLAMA_BASE_URL`. The **first** build extracts the whole corpus; graphify caches per file, so every
later refresh re-bills only what changed — usually the one note Retrospective just wrote. `scope:
full` is not auto-refreshed (it needs a code extract plus a merge) and declines by name instead.

### The self-improving loop

A graph that only grows rots into noise. Sigma closes the loop so it stays useful:

- **Find gaps** — a query (or a recall) that comes up empty is logged as a **gap** (`kg.py gap log`,
  done automatically by `/sigma-context`). The graph tracks **what it doesn't know yet**; review it with
  `kg.py gap list`.
- **Prune itself** — `kg.py maintain` reports **stale** notes (citing a repo path that no longer
  exists), **duplicates**, and corpus size vs a threshold, each with the exact **`apply:` command**
  to archive it. Report-only, **archive-not-delete**, and a trim of the shared analysis notes always
  needs your approval — so the corpus self-cleans instead of bloating. The local web captures are
  the one set that may be bounded **automatically**, and only if you opt in
  (`web_retention_enabled: true`): by age and by a byte bound derived from the disk they sit on,
  archived under `.sdlc/knowledge/archive/`, never deleted, the count printed whenever anything moves.
- **Fill the gaps** — when the backlog empties but gaps remain (and budget allows), `/sigma-loop` can
  promote the oldest gap into a fill-goal (research → write analysis → refresh → `gap resolve`),
  budget-gated and parking anything that needs you. The graph **fills what it didn't know.**

The cycle: **enrich → find gaps → prune → fill → repeat** — cleaner and denser every run.

### Context recall — never lose the thread

The **read side** is **`/sigma-context`**: a pre-flight that, before a goal runs, pulls the **relevant
slice** of project memory back into context — retrieval by **relevance, not recency** — so a crucial
earlier finding isn't missed just because the context window flushed. It's gated on the KG (a no-op
when disabled), and `/sigma-loop` + `/sigma-goal` run it automatically at the start of each goal. It
assembles a short, **cited** brief from the **north-star** (vision-first) + the **graph**
(`graphify query`) + **past issues / 🔒 Critical Insights** + the **conventions** (`.sdlc/project.md` +
governing `CLAUDE.md`).

For on-demand pull *during* a run, expose the graph as a live tool: run **`python -m graphify.serve
graphify-out/graph.json`** from graphify's own Python environment (its source defines that entry point; it needs the
`mcp` extra, `pip install "graphifyy[mcp]"`, and a default graphify install raises an ImportError saying so; the server
was read, not started), or use the `/graphify <path> --mcp`
skill flag, or add that server to your Claude Code config. There is no `graphify --mcp` command: the installed graphify
answers `unknown command '--mcp'` (checked 2026-10-14). With the server up, the
agent can query it whenever it hits unfamiliar code, keeping the working window small while the full
history stays a query away. The full closed loop: **record** (issues / journey) → **ingest**
(`/sigma-kg`) → **recall** (`/sigma-context` + MCP) → run.

> Keep `.sdlc/knowledge/research/` and the builder's output (`graphify-out/`) out of git — they're
> machine-accumulated. Commit `.sdlc/knowledge/analysis/` to version your curated learnings.
