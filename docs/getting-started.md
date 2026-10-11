# Getting started

Install and first run are in the [README Quickstart](../README.md#quickstart). This page covers the two ways to start and upgrading from the plugin's previous name.

## Two ways to start: drop-in or vision-first

Sigma meets you where you are — both on the **same spine**, so you can move between them anytime.

### Drop-in (default)
Install, `/sigma-init`, and start running goals against your existing repo. A thin `.sdlc/project.md`
(stack + verify command) is all the context you need. Near-zero setup; nothing to author up front.

### Vision-first (opt-in)
Starting a new product, or want top-down grounding? Run **`/sigma-vision`** (or `/sigma-init --vision`)
to externalize a tiered **north-star** into `.sdlc/context/north-star.md` — **Vision → Strategy
(+ non-goals) → Design → Architecture**. Then every goal is grounded in it: `/sigma-context` recalls
the north-star first, and `sigma-plan-review`'s **alignment check** asks the reviewer to send back any plan that contradicts
your strategy or advances a stated non-goal (**FIX-FIRST**) — a review instruction, not a code check.
The one-pass draft is the lean default; when you want to externalize a tier
properly, `/sigma-vision` loads an optional **deep-elicitation guide** per tier (on demand, never bloat)
— and the **Architecture** tier drafts its rules straight from the codebase for you to approve.

> **Progressive disclosure is the seam:** a drop-in project can add a north-star later; a vision-first
> project just starts running goals once the tiers are filled. No north-star → the alignment check is a
> no-op, and drop-in behaves exactly as before.

## Upgrading a repo adopted under the plugin's previous name?

Install Sigma and carry on. Sigma reads the schema ids, markers, environment variables and config
key that the previous name's 1.4.x releases wrote. When you want the files themselves renamed, run
the one-shot migration. It is a dry run by default, `--apply` writes, and it is safe to re-run (a
unit record the old plugin wrote after the conversion is refused and left for
`feature_sync.py repair`; see docs/upgrading.md):

```
python3 <installed-sigma>/skills/sigma-doctor/scripts/migrate.py .sdlc            # lists every change
python3 <installed-sigma>/skills/sigma-doctor/scripts/migrate.py .sdlc --apply    # writes and prints what it changed
```

The old plugin cannot read Sigma's spellings, so migrate once the whole team runs Sigma. See
[docs/upgrading.md](upgrading.md) for what it reads, what it rewrites and what it leaves alone.

Sigma runs fully next to the old plugin and replaces it: nothing is refused while both are
installed, and each run prints one notice naming the uninstall command. Cut over with
[Switching over from the previous plugin](upgrading.md#switching-over-from-the-previous-plugin):
install Sigma, run it, **stop the old plugin on the repository**, migrate, then uninstall it. The
old plugin cannot read Sigma's registry, so `--apply` waits for that step (or for an explicit
`--replace-old-plugin`).
