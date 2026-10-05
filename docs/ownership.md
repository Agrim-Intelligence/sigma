# Area ownership — who a hand-off is addressed to

Host-agnostic. This is the contract for **area** ownership: the question *"who owns `area:web-ui`?"*,
asked whenever a hand-off, an ownership hold, or a bus-factor metric needs a name.

It is **not** the same as unit ownership. `docs/branching-model.md` §12 covers `entry.owner` — who
owns a *unit of work*, and who a divergence about that unit is addressed to. That is per-unit and
lives in the registry. This document is per-**area** and lives in the repository. A person can own
an area and no units, or a unit in someone else's area.

---

## 1. Two rosters, and which one wins

Ownership resolves through `skills/sigma-loop/scripts/owners.py`, in this order:

1. **`ledger.owners` in `.sdlc/config.json`** — an explicit `area -> login` map. If the area is a
   key here, this wins outright and nothing else is consulted.
2. **`.github/CODEOWNERS`** (also `CODEOWNERS`, `docs/CODEOWNERS`) — the area name is resolved as a
   **directory**: `owners.py` tries `<area>/x`, `<area>/`, then `src/<area>/x` against the rules.
3. **The `*` catch-all**, and only if nothing more specific matched — so a hand-off is not addressed
   to whoever owns `*` merely because they own everything else.

**Why both exist.** CODEOWNERS is the roster the host already enforces on every pull request, so it
should be the source of truth wherever it can be. The override exists for one case: **an area
vocabulary that does not match the directory layout.** Say `area:web-ui` is the label for work
inside `frontend/`: `owners.py` looks for a directory called `web-ui/`, finds none, and would fall
through to `*` — answering with the catch-all owner for a front-end change. The override says so
explicitly.

The rule that follows: **override only the areas whose names are not directories.** Every area that
CODEOWNERS can resolve on its own is left out of the map deliberately, so there is one roster to
change and not two that can disagree.

## 2. A core-only checkout

A core-only checkout has **one** area: everything, owned by CODEOWNERS' `*` catch-all rule. It needs
no `ledger.owners` override at all, and this document names no handles — the owner is whoever your
CODEOWNERS says, so a new area invented next month is owned correctly the moment it exists, with
nothing to remember.

Add areas only when your repository genuinely has more than one owner. When you do, give each area
a directory rule in CODEOWNERS where you can, and override only the area names that are not
directories (§1).

**A trap worth naming, because it is silent.** Onboarding — `sigma-wizard`, `sigma-setup`,
`sigma-init`, `sigma-doctor` — reads like its own area and is **not** one. It is core: those are
Sigma's own skills. Splitting them out would hand a core skill's review to another area's owner
and nothing would report the mistake, because a wrong-but-valid owner looks exactly like a right
one.

## 3. It is ADVISORY, and that is a choice

CODEOWNERS here auto-assigns reviewers. **No branch protection rule requires those approvals.** With
more than one owner, a binding rule means either can be blocked waiting on the other, and Sigma's own
merge handler can stall on a pull request it opened itself.

To make it binding: enable *Require review from Code Owners* on `main` — **and grant Sigma's
actor a bypass first**, or every automated merge waits on a human. The same trap is documented for
`feature/*` in `docs/branching-model.md` §13, for the same reason and with a sharper consequence
there: protecting `feature/*` in the ordinary way turns rebase upkeep's direct-commit detection off
entirely.

## 4. Changing it

Ownership is two files and they must move together:

- **A path changes hands** → edit `.github/CODEOWNERS`. Last matching rule wins, so order is
  load-bearing; the `*` catch-all stays first.
- **An area name changes, or a new area appears whose name is not a directory** → add it to
  `ledger.owners`. If its name *is* a directory CODEOWNERS covers, do not add it.

Verify by resolving, not by reading — the resolution has three layers and eyeballing one of them
proves nothing:

```bash
python3 - <<'PY'
import sys, json; sys.path.insert(0, "skills/sigma-loop/scripts")
import owners
config = json.load(open(".sdlc/config.json"))
for area in ("doctor", "loop", "ledger", "web"):
    print(area, "->", owners.owner_of(".", area, config))
PY
```

**`.sdlc/config.json` is gitignored**, so `ledger.owners` is per-checkout and does not travel with
the repository; `.github/CODEOWNERS` does. A teammate cloning fresh gets the CODEOWNERS half and
none of the override half — which is one more reason to keep the override as small as possible.

## 5. What consumes this

- **Hand-offs** (`handoff.py`) — `owners.owner_of()` picks the single recipient; the first listed
  owner wins, because a hand-off needs one assignee and CODEOWNERS lists the primary first.
- **Ownership holds** — a unit whose owner is someone else holds rather than proceeding, and names
  the remedy that clears it.
- **`/sigma-doctor`** — reports "no CODEOWNERS and no `ledger.owners`", because with neither, every
  hand-off resolves to `(unowned)` and the bus-factor metrics discard silently and stay empty
  forever.
- **`/sigma-scope`** — offers the CODEOWNERS owner as one of the assignment options.
