# Architecture rules: `doctor.py rules`

Rules with no `Enforced by:` suffix, or `review` only, are listed as an advisory section and never fail
anything; say so rather than nagging. Only a named enforcement that does not hold is **broken**: a `ci:`
path that is not a file inside the repository, or a `decide:` id missing from, or superseded in,
`.sdlc/decisions.json`. A broken rule is one `[MISSING] architecture rule enforcement` row in `check`,
and `rules` exits 1 on it (and on a north-star it could not read).

The suffix may follow a sentence end, dash, comma, colon or `(`, and a spec may be in backticks; several
specs are comma-separated. The check is "the file exists", not "CI runs it and it covers the rule", and
the numbered-list parse is a heuristic over prose. The full grammar and the enforcement to prefer are in
`docs/architecture-doc.md`.
