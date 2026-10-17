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
newline, carriage return or tab (the output is tab-separated), is refused; so is a `--base` value
that starts with a dash. A backslash is an ordinary name byte on POSIX, never a separator. A path that starts with a dash needs `--`: `zones.py classify -- -odd.py`.
It is read-only apart from the kit-wide script-time record, runs a constant number of git
processes whatever the path count, and uses no network and no `gh`.

Measured (macOS, git 2.55, Python 3.12, `--base main`): 1,000 paths take 0.20 s wall in one run (all
new paths, each in its own directory, in a one-file repo), and 20,000 such paths take 2.6 s. The
number of git processes is constant, but wall time still grows with the path count (roughly
linearly across those two points); a de-duplication that scanned a list instead of a hash made the
20,000 case 12.6 s. Not measured: a large real repository, a networked filesystem, or more than
20,000 paths.

## Limits of the `new` reading

Newness is a byte-for-byte lookup of the path you pass in git's tree at the fork point. Two
spellings of a file that is tracked therefore print `new` although the file exists:

- **Unicode normalisation (macOS).** macOS can open a file by a different Unicode spelling than the
  one git stored, for example the decomposed (NFD) spelling of a tracked precomposed (NFC) name
  such as `cafe` plus a combining accent for `café`. Git does not equate them, so the row prints `new`.
- **Case (case-insensitive filesystems, the macOS default).** `readme.py` opens `Readme.py` on such
  a filesystem, but git tracks `Readme.py`, so the row for `readme.py` prints `new`.

In both cases the safe reading is that `new` for such a spelling may be wrong; pass the spelling git
tracks (`git ls-files` shows it). The zone glob match is case-sensitive for the same reason. This is
a documented limit, not handled; a test pins the current behaviour so a change is noticed.

## Measure legacy change and refactor intent

```
python3 skills/sigma-loop/scripts/zones.py check --goal 1541 --diff
```

`check` answers one question: how much of the legacy code that already existed at the fork point
does this goal change, and did the goal's acceptance record say it meant to? Use `--diff` for the
**working tree** against the fork point (uncommitted Implement-time edits count, so a half-done
restructure never reads `within`) or `--plan FILE` for a plan before any edit. Exactly one of the
two is required, plus `--goal N` (a bad goal, `--base` starting with a dash, an unreadable or
over-256-KiB plan, or a bad `design.restructure_lines` is refused: exit 2, empty stdout).
The fork point comes from the goal's work record (`.sdlc/state/work/<goal>.json`, which must be for
the checked-out branch); `--base REF` pins another ref.

One row per legacy file, tab-separated: `<path>`, `legacy`, `<changed|binary|->`, `<restructure|->`,
`intent: yes|no|unknown`, for example `src/a.py<TAB>legacy<TAB>41<TAB>restructure<TAB>intent: no`.
Then `verdict: over`, `verdict: within` or `verdict: unknown`; all three exit 0. Only files that
existed at the fork point and whose zone is `legacy` get a row; new, modern and undeclared files, and
untracked files, never do.

- **Threshold.** `design.restructure_lines` (an integer >= 1, default 40; a `0`, a negative, a
  string, a boolean, a float or `null` is refused). A file is a `restructure` when its changed lines are
  **strictly** greater than the threshold: 40 is not, 41 is.
- **Counting.** Changed lines are **added plus deleted**, so one edited line counts 2, a deletion
  counts its lines, a rename counts once and is keyed on the old path, a mode-only change is 0, a
  binary file reads `binary` and is a restructure, and a submodule bump counts 2 (it can print a
  legacy row for the submodule path; paths inside a submodule are not read). Counting options are
  pinned against your own git diff configuration.
- **Intent** comes from the goal's acceptance record in the main checkout, read through the shared
  reader. A record with `drafted: true` (criteria the agent wrote itself) never grants intent: `no`.
  With `drafted: false` the intent is `yes` when a criterion has a backticked, whitespace-free path span
  that names the file or one of its directories, matched on a **component boundary** (`src/leg` does not
  match `src/legacy/a.py`; a trailing `/` and a leading `./` are ignored). A missing, unreadable,
  malformed or undated record (written before this existed) reads `intent: unknown`: the change is
  **forward-only**, nothing back-fills older records, and the honest verdict is `unknown`, never `within`.
- **Verdict (`--diff`).** `over` if any row is a restructure with `intent: no`; else `unknown` if any
  row is `intent: unknown` or the fork point, config or a git call could not be resolved (the reason is
  on stderr); else `within`. A diff that touches no legacy file is `within` even with no record.
- **Verdict (`--plan`).** The paths are the backticked spans in the plan's `## Files` (or
  `## File structure`) section that are files at the fork point; NEW or EDIT words are ignored. A plan
  naming an existing legacy file without intent reads `over`, even for a one-line seam edit, because a
  plan has no line count; the fix is to declare the intent. A plan with no such section reads `unknown`.

Documented gaps, each pinned by a test: **negation** is not parsed, so a criterion such as
`src/legacy/a.py` stays unchanged still grants `intent: yes`; a span containing whitespace never matches
(a file with spaces gets intent only through its directory); a suffix such as `:12` or `::test` makes a
span never match, which fails toward `over`; matching is **case-sensitive**; `drafted: false` is the
recorder's own claim, not proof; untracked files are not seen by `--diff`; and a criterion that names a
top-level directory (`tests/`, `docs`, `src`) grants intent for that whole tree by the directory rule.
A binary file that is only stat-stale (same bytes as the fork) is compared by content and dropped, never
read as a restructure. A changed path containing a newline cannot go through the line-based lookup:
it is skipped, the verdict cannot be `within` (`over` still wins when a measured row is over), and one line on stderr names it; the other
rows still print.

It reads only, through `git diff-index` (never `git diff`, which rewrites a stale `.git/index`), and
runs a constant number of git processes, including one `merge-base`, one `diff-index` and one
`cat-file` (eight in total under `GIT_TRACE`, with the `rev-parse` and `symbolic-ref` lookups; a diff
with binary files adds one more `cat-file` and one `hash-object`). Measured
(macOS, git 2.55, Python 3.9 system interpreter): `check --goal 1541 --diff` in this repository with
a small diff and no zones declared took 0.22 s wall (median of 3); a fixture with 1,500 changed legacy
files printed 1,500 rows in 490 to 560 ms (5 runs) on a loaded machine (load average about 13) and
0.68 s median earlier on a quieter one; an independent run under swap pressure took 1.12 to 1.28 s, so
plan for roughly a second rather than the quiet-machine figure. Not measured: beyond 1,500 changed files, a
networked filesystem, or a repository with large history. The ceilings are the 10 s git timeout (a
timeout reads `unknown`), a 256 KiB plan and a 32 KiB acceptance record.

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
