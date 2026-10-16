# Change zones

A change zone tells an agent how carefully to touch a file: leave legacy code almost alone, and
design modern code freely. You declare the zones; **nothing infers them**, and `/sigma-vision` may
only suggest candidate paths, never write the key.

## The key

`design.zones` in `.sdlc/config.json` is a list of entries, absent by default:

```json
{ "design": { "zones": [
  { "glob": "src/*",         "zone": "legacy" },
  { "glob": "src/payments/*", "zone": "modern" }
] } }
```

Each entry has exactly two keys, `glob` (a non-empty string) and `zone` (`legacy` or `modern`).
A malformed entry is refused by name (`design.zones[1]`), exit 2, empty stdout.

- **Glob semantics.** `fnmatch` on the repo-relative path with forward slashes, case-sensitive
  (`fnmatch.fnmatchcase`). `*` crosses `/` (so `src/*` matches `src/a/b/c.py`), `?` is one character,
  `[abc]` a set.
- **Last match wins.** Entries are tried from the end; the zone of the last entry that matches is
  used, which is the last match. Put the broad entry first and the exceptions after it. No match is `undeclared`.

## Print each file's zone and rule

```
python3 skills/sigma-loop/scripts/zones.py classify src/legacy/a.py
```

One row per path, tab-separated: `<path>`, `<zone>`, `<state>`, `<rule>`. The zone is `legacy`,
`modern`, `undeclared`, or `unreachable` (the config could not be read, which is not the same as
undeclared). The state is `new` when the path is absent at the fork point of the goal branch (the
merge-base with `<remote>/<base>` from the goal's work record; `--base REF` pins another ref),
`existing` when present, and `unknown` when the fork point cannot be resolved (no record, detached
HEAD, shallow history, unresolvable ref, a failing `git cat-file --batch-check`, or a path inside a
submodule). A rename counts as `new`. A directory argument is classified by its own name only.

The rule printed, in this precedence:

| Case | Rule |
|---|---|
| state `unknown` | `unknown: merge-base unresolved; apply no zone rule` |
| zone `unreachable` | `unreachable: config unreadable; apply no zone rule` |
| any path that is `new`, or zone `modern` | `follow sound design: structure for the forces the goal names` |
| `legacy` and `existing` | `legacy: change only what the goal needs; extend by sprout or wrap at one seam` |
| `undeclared` and `existing` | `follow the surrounding code` |

An unreachable config prints ahead of the unknown fallback. Paths may be relative (joined to the
current directory), absolute, or contain `./`; a path outside the checkout, or one containing a
newline, is refused. A path that starts with a dash needs `--`: `zones.py classify -- -odd.py`.
It is read-only apart from the kit-wide script-time record, runs a constant number of git
processes whatever the path count, and uses no network and no `gh`.

Measured (macOS, git 2.55, Python 3.12, `--base main`): 1,000 paths take 0.20 s wall in one run (all
new paths, each in its own directory, in a one-file repo), and 20,000 such paths take 2.6 s. The
number of git processes is constant, but wall time still grows with the path count (roughly
linearly across those two points); a de-duplication that scanned a list instead of a hash made the
20,000 case 12.6 s. Not measured: a large real repository, a networked filesystem, or more than
20,000 paths.

## Sprout and wrap

In legacy code, extend by **sprout** or **wrap** at one seam rather than editing through it.

- **Sprout:** add a new function or module beside the legacy code and call it from one seam, for
  example a new `billing/tax_v2.py` called from the single line in `billing/legacy_calc.py`.
- **Wrap:** leave the legacy function untouched and put a new function around it that handles the
  new case, for example `price_with_discount()` calling the legacy `price()`.

## Per-machine caveat

`.sdlc/config.json` is gitignored, so the map is **per-machine**: another clone has its own copy, or
none, and reads `undeclared` for everything. A stale map misclassifies silently, so review it when
the legacy boundary moves. Nothing infers zones from the code or the history; if you want them
everywhere, declare them on every machine.
