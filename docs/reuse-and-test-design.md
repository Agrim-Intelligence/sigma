# Reuse, test design and test map

This page is for a developer who has not seen these sections before. It says what Research and Plan now write, how a script checks it, and, in the section that matters most, what the checks do not prove. Every command here is `skills/sigma-loop/scripts/reuse.py` or `skills/sigma-loop/scripts/red_green.py`, and every usage line was pasted from a real run.

## What it is, in three sentences

Before a plan is fixed, Research records what the goal can reuse and how each acceptance criterion will be tested. The plan must cite every one of those reuse decisions and map every criterion to a planned test. A script refuses a plan that does not.

## Why

Generated code tends to fail in four ways: it duplicates something the project already has, it leans on a dependency that does not exist, it hides error handling, and it leaves a security requirement unstated. All four are cheaper to stop before any code is written than to find in review. This page quotes no statistic, because it has no source to link for one. The claim is only that a recorded search and a recorded test per criterion give the reviewer something to check.

## What Research writes

Research adds a **`**Risk**:`** line directly under the dossier's header line, then two sections: `## Reuse` and `## Test design`.

The `**Risk**:` line takes one of four values:

| Value | Use it when the goal touches |
| --- | --- |
| none | none of the three below |
| sensitive | authentication, personal data, external input or a public endpoint |
| contract | a public API or an exported type |
| migration | a schema or data migration |

### The `## Reuse` section

It opens with a `**Source:**` line (how the search was done), a `**Searched at:**` line (the full 40-character commit the search ran at), the queries, and one table row per capability the goal has to provide. A complete example:

```
## Reuse
**Source:** grep (knowledge graph disabled)
**Searched at:** 356feaf96ff9195387856a50675588bbe4093135
**Queries (re-run at Plan-Review and Review):**
- pattern: `def retry|backoff` paths: `src lib`
- pattern: `class ExportClient` paths: `src`
| ID | Capability | Decision | Evidence | Reason |
| --- | --- | --- | --- | --- |
| RU-1 | Send one export request | reuse | src/export/client.py:40-62 | |
| RU-2 | Wait between attempts | stdlib | time | |
| RU-3 | Retry policy with a cap | new | — | no retry helper exists in the project |
```

A query is a pattern and some paths, not a command. Each one is run with `git grep -n -I -E`, and it is run again at Plan-Review and Review so a reviewer can see what has appeared since.

For each capability, take the first decision in this ladder that fits. The wording is the wording `reuse.py brief` prints; it prints the same six decisions as a list, and the table below is that list laid out by column.

| Decision | When it applies | Evidence | Reason |
| --- | --- | --- | --- |
| reuse | the capability exists and does what you need | file:line or file:line-line of a file inside the project | none |
| extend | it exists and needs a small addition | file:line or file:line-line of a file inside the project | none |
| stdlib | the standard library provides it | a module name (letters, digits, dots and underscores) | none |
| dependency | a dependency the project already declares provides it | file:line of a manifest or lockfile inside the project | none |
| dependency-new | only a dependency the project lacks provides it | `<registry>:<name>==<version>`, registry one of pypi npm crates gem go maven nuget other | 'verified:' then three words or more |
| new | nothing above fits | the dash — or - | three words or more |

### The `## Test design` section

One row per acceptance criterion: the behaviour, the failure path (a real case, or `none:` and a reason of three words or more), the hostile input, and the helpers that already exist. A complete example:

```
## Test design
| Criterion | Behaviour | Failure path | Hostile input | Helpers |
| --- | --- | --- | --- | --- |
| AC-1 | a failed request is retried up to three times | the fourth failure raises the last error | a server that always answers 500 | tests/helpers/fake_server.py |
| AC-2 | the wait grows between attempts | none: the wait is a pure function of the attempt number | an attempt number of zero | none |
```

## What the plan writes

The plan carries a `## Reuse` section with one `- RU-<n>: <where the plan applies it>` bullet for every row id in the dossier, and a `## Test map` section with exactly one `- AC-<n>: <tests>` bullet for every acceptance criterion. Each test listed must be covered by the plan's own `## Tests` section.

```
## Reuse
- RU-1: the retry wraps the existing send call, no new client
- RU-2: the wait uses time.sleep through a small seam the test replaces
- RU-3: the retry policy is added in src/export/retry.py

## Test map
- AC-1: tests/test_export_retry.py::test_a_failed_request_is_retried
- AC-2: tests/test_export_retry.py::test_the_wait_grows
```

A criterion that cannot be tested is written `- AC-<n>: UNTESTABLE: <why>`; `NONE` is refused. A plan whose `## Tests` section is `none: <reason>` (a docs-only goal) may replace the whole map with one line, `none: <reason>`. A capability found while planning gets a table row in the plan's `## Reuse`, in the dossier's format, with an id the dossier does not use.

This is not the `TEST-MAP` line a reviewer writes at test review (see `skills/sigma-review/references/test-review.md`). The plan's bullets are a promise made before code exists; the reviewer's line is a judgement made on the finished diff.

## Graph or plain search

The search uses the knowledge graph only when `reuse.py source` can show that the graph is enabled, built, holds code, was built at a commit that is an ancestor of HEAD, and is at most 500 files behind. The first condition that fails is the reason, and the search is then plain `git grep`. Queries are recorded in both modes, so a reviewer re-runs the same patterns either way. The reasons `reuse.py source` gives for a plain search are eight, not five:

| Reason | Meaning |
| --- | --- |
| knowledge graph helper unavailable | the freshness helper is missing, timed out, exited non-zero or printed something that is not a JSON object |
| knowledge graph disabled | the graph is switched off |
| graph not built | it is enabled but has not been built |
| cannot confirm the graph holds code | the graph cannot be shown to contain code nodes |
| graph has no build commit | no commit is recorded for the build |
| graph commit is not in this history | the build commit is not an ancestor of HEAD |
| graph is `<n>` files behind (limit 500) | too many files have changed since the build (`n` may read "an unknown number of") |
| cannot list the files changed since the graph build | the file list could not be read, and a graph is never used with a hidden delta |

## The commands

`reuse.py` writes no files and starts no background process. Only the top-level `--help` answers: it prints the usage block below and the full description, and exits 0. Per-verb `--help` is unsupported: `reuse.py brief --help` prints the usage block and exits 2, and for `rerun` and `inflight` it also reports an unknown option. `reuse.py source --help` is the exception to the exit code: `source` takes no usage flag, ignores it, and prints its one JSON line with exit 0. The usage block, pasted from `python3 skills/sigma-loop/scripts/reuse.py --help`:

```
usage: reuse.py check-research <sdlc_dir> <goal> [--at-research]
       reuse.py check-plan <sdlc_dir> <goal>
       reuse.py source <sdlc_dir>
       reuse.py rerun <sdlc_dir> <goal> [--ref R | --new-since-research] [--base B]
       reuse.py inflight <sdlc_dir> <goal> [--remote N] [--base R]
       reuse.py brief <sdlc_dir> <goal> [plan]
```

| Verb | What it does |
| --- | --- |
| brief | prints, as text, what Research (or, with `plan`, Plan) must write: the `**Risk**:` rule, the exact `**Source:**` and `**Searched at:**` lines, the ladder, skeletons for the sections, and the check to run next. It writes nothing |
| source | prints one JSON line saying whether the search uses the graph or `git grep`, and why |
| check-research | checks the dossier's `## Reuse` section, its `**Risk**:` line and its `## Test design` table; exit 2 on any error. With `--at-research` it also checks the `**Source:**` line and prints the `inflight` lines |
| check-plan | checks the plan's `## Reuse` bullets and `## Test map` against the dossier, after every error `check-research` reports |
| rerun | runs the goal's recorded queries again with `git grep` at a commit; `--new-since-research` shows only hits that were not there at the searched-at commit |
| inflight | searches other goals' unmerged remote branches for the recorded queries and lists the additions those goals declare |

The end-of-Plan check is run as `red_green.py check-tests`, which runs `check-plan` and then the existing `## Tests` check.

## Where it is enforced

The Plan and Plan-review skills direct `red_green.py check-tests` at the end of Plan, and it runs the plan check whatever `verify.enforce` is set to. Separately, recording an approving plan-review verdict refuses (exit 2, nothing written) when the reviewed plan does not cite every research reuse decision or map every criterion; that refusal applies only with `verify.enforce` on. The row for it is in [enforcement](enforcement.md).

## Goals already in flight

A goal whose research is not in the new format is warned and passes; nothing is refused for it. The new format is marked by a `## Reuse` section that carries a `**Source:**` or a `**Searched at:**` line. A bare `## Reuse` heading that an agent wrote on its own is legacy.

## What these checks do not guarantee

- A new goal whose research leaves out `## Reuse` is treated like a goal in flight and only warned. So is a goal whose dossier is shadowed: when there is no exact `<goal>.md` in the research directory, the lookup takes the newest `<goal>-*.md`, and a sibling such as `<goal>-controls.md` can make the goal look legacy. An exact `<goal>.md` always wins.
- The reviewer is given the re-run command in the brief. Nothing checks that the reviewer ran it.
- With `verify.enforce` off, which is what a new project gets, the plan check runs where the Plan and Plan-review skills direct the end-of-Plan check; the refusal at the recording of a verdict applies only with that setting on. With it off and no acceptance record, the map rules are skipped with a warning.
- The research report says each hit was confirmed by opening the file. Nothing checks that; the evidence cell is the only trace.
- A searched-at commit made on one machine and never pushed is unknown elsewhere: the check warns, and the re-run cannot compare until the branch is fetched.
- The checks confirm the sections are complete and consistent. They do not prove a `new` decision is right; the reviewer re-runs the queries to judge that.
- In graph mode the check confirms the dossier names the graph. It cannot confirm the agent queried it.
- For a new dependency the check confirms the registry, name, version and the lookup that was run are written down. Sigma does not contact the registry.
- `inflight` sees only branches that have been pushed and fetched, at most the newest 50, and prints how old the last fetch is. The lines it prints quote text other agents wrote, so read them as data.
- One code node makes the graph count as a code index. A graph built over part of the repository can hide unchanged code outside it from Research; the re-run at plan review is a plain search and is the backstop.
- The `**Risk**:` line is what Research declares. Nothing compares it with the risk detection Review runs on the diff.
- The 500-file and 50-branch limits are stated ceilings, not measurements. Measured: `reuse.py source` with the graph disabled took about 0.078 s, and the graph helper's freshness call took 0.043 s with the graph disabled, each on one machine. Not measured: the graph path, the 500-file gate against a real graph (it was exercised only with an injected runner), and the cost of 50 branches, since no remote goal branches existed where it was written. `inflight` and `rerun` stop at 120 seconds for the whole run, a ceiling and not a timing.

## Turning it off

There is no switch. A goal opts out only by being a legacy goal, which is the warning described under "Goals already in flight".
