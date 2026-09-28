The #486 path-traversal fix (6 chokepoints) guarded every site that builds a filesystem path from a raw \`goal\` argument using \`work.stem()\`'s reduction, except two sites that were confirmed SAFE BY CONSTRUCTION during that review and deliberately left unguarded:

- \`sources.py\`'s \`note()\` (~line 62) — uses unconditional \`Path(goal).stem\`
- \`review_context.py\`'s \`_dossier()\` (~line 96) — same

Both are safe because \`Path(...).stem\` cannot produce a path-separator-bearing traversal segment the way \`work.stem()\`'s own reduction chain can. This invariant is currently only known from the #486 review's own notes — not documented in the code, and not pinned by a regression test, so a future edit to either function (e.g. "helpfully" swapping in \`work.stem()\` for consistency, or changing the stem logic) could silently reintroduce the exact class of bug #486 fixed elsewhere, with nothing to catch it.

**Fix:** a one-line comment at each site stating the invariant (why \`Path(goal).stem\` is safe here and \`work.stem()\` is not needed), plus a non-vacuous regression test per site proving a \`../\`-bearing goal cannot escape \`.sdlc/journey/\` (via \`note()\`) or the research-dossier lookup (via \`_dossier()\`).

model:bulk — documentation + two small pinning tests against already-safe, already-understood code; no behavior change.
