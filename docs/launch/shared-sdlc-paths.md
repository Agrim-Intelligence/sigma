# Shared `.sdlc` paths: Sigma and the predecessor plugin

Goal #347, readiness dimension D9 (upgrade, coexistence, uninstall). Policy from #314: Sigma runs alongside the
predecessor plugin and replaces it, with a notice and no refusal, so both plugins write one `.sdlc/` for a while.
"Compatible" therefore means: either plugin can write a shared path without destroying what the other holds there.

## What was scanned

| | |
| --- | --- |
| Predecessor | its installed plugin copy, `<predecessor-install>`, version **1.4.28** |
| Why that directory | the owner named the installed plugin copy as the thing to read; the scan picked, of the installed set, the latest by version order (the older ones, including 1.4.24, where the setup wizard was seen writing into this repository, were not scanned) |
| Files hashed | 1700 |
| sha256 before the scan | `712a8b6cfac320050be9dd241f21cba42b123afe8d54c7527c4c31384f4a5bf6` |
| sha256 after the scan | `712a8b6cfac320050be9dd241f21cba42b123afe8d54c7527c4c31384f4a5bf6` |
| Changed | no (the tool exits 2 without writing anything if the two differ) |
| Sigma tree | base commit `4c8562f` (the PR adds only tools, docs and tests, so `skills/` and `hooks/`, the scanned trees, are those of this commit) |

The hash is `sha256` over one line per file, `relative path NUL sha256(file bytes)`, files sorted globally by relative path, a symlink
hashed by its target text. The predecessor is read only: it is parsed, never imported, and the sample runs execute a
temporary copy. Nothing is written under the predecessor's directory.

## Result

60 paths have a writer on both sides, 4 have one writer, 31 are directories (their files carry their own rows).

| Verdict | Count | Meaning |
| --- | ---: | --- |
| same-format | 13 | equal key sets and schema in a file each side's own CLI wrote (one fixed command list; values and other command paths not compared) |
| additive | 3 | same schema; one side writes extra keys |
| different-format | 3 | schema-id spelling differs, or neither key set contains the other; see Findings |
| identical-writer | 10 | no sample reached it, but every writer function is the same code on both sides apart from brand spellings (weaker than a sample: helpers are not compared) |
| unsampled | 31 | no sample and the writers differ: compatibility is **unverified**, not shown |
| one-writer | 4 | only one side writes it |
| directory | 31 | directory creation only |

Cross-run: Sigma then the predecessor then Sigma, and the reverse, on one scratch repository with the same ten-step
command list as the solo runs: no step's exit code differed from its solo run. That is exit codes only. It is not proof
that no state was damaged, and it does not cover `work.enabled`, the ledger, features or GitHub mode.

## Findings

- **different-format with two writers, corruption demonstrated: `.sdlc/features/index.json`.** Sigma's registry index
  carries schema id `sigma/features@1`; the predecessor's reads none of it. The predecessor's own `feature_sync.py fold`
  then replaced an `index.json` holding one Sigma unit with an empty registry in its own schema id, exiting 0, and Sigma read
  zero units. Filed as **#514** (`launch:blocker`, `readiness`, class B1 candidate, filed queued, never promoted by this goal; the owner confirms
  the class, and its labels may have moved since). Not claimed: only this CLI path was run. The predecessor's module-level `write_unit` also overwrote a
  Sigma-schema unit shard when called directly (`.sdlc/features/units/*.json`, verdict different-format, corruption not
  probed); #514 covers both.
- **different-format, corruption not demonstrated: `.sdlc/config.json`.** The key sets differ, each side having keys the
  other lacks. Each side's `setup.py configure`, run over a config the other side wrote, lost no top-level key, in either
  order. Not filed.
- **31 unsampled paths** are not conflicts found; they are paths this run could not judge. One follow-up,
  **#515** (`launch:next`, queued), extends the scenario. Until then their guard entries say `compatible: false` and cite it.

## What a static scan cannot see

- Paths assembled at run time from data (a config value, an environment variable, a directory listing).
- A destination handed through a helper whose parameter name the resolver does not follow. `growth_audit.py` keeps only
  `.sdlc/`-prefixed patterns; this tool relaxes that and maps the common parameter names (`features_dir`, `goals_dir`,
  `state_dir`, `sdlc`), and pins one writer it could not reach (`write_unit`, verified to exist by name). A writer built
  from other names is not found.
- The destination resolver (`growth_audit.py`'s) misses `os.replace` onto a path, `shutil.copy`, `rename`, and a
  `Path(a, "state", name)` built with commas (measured by a post-PR reviewer). A backstop covers part of that: a new
  function (nested, in an `if`/`try` block, async or a method included) that holds the predecessor-written file's own name as ONE string constant inside it and
  makes a call from a fixed write-ish list (`write_text`, `write_bytes`, `open`, `replace`, `rename`, `copy`, `copy2`, `copyfile`,
  `copyfileobj`, `copytree`, `move`, `mkdir`, `touch`, `dump`, `write`, `writelines`, `symlink`, `link`, `fdopen`,
  `mkstemp`) is refused, whatever the write method among those. It does NOT see:
  a write at module level or in a lambda; a name held in a module constant, class attribute or returned by a helper; a name
  built by concatenation, an f-string or `join`; a full-path string (the match is on the bare file name); a write call not
  on that list (`symlink_to`, `truncate`, `sqlite3.connect`, a logging file handler); any wildcard-named file such as
  `*.lock`; or a directory distinction (it cannot tell two files of the same name apart).
- Skipped by design: any file under a path component named `tests`, `*.pyw` and extensionless Python scripts.
- Only `*.py` is scanned. A shell script under `hooks/` that writes a `.sdlc` path is not seen here (the write-surface
  inventory, `docs/launch/write-surface.md`, tracks shell write sites separately).
- A file written by a subprocess the Python only names, and any tree outside `skills/` and `hooks/` (the predecessor also
  ships a separate analytics tree and an install script; neither was scanned).
- Markdown files are compared by heading count only; JSON by keys and schema kind, never values.
- The scenario samples one fixed command list, with `work.enabled` off, no ledger and local goals.

## Reproduce

```sh
python3 tools/readiness/shared_paths.py scan --sigma . --predecessor-dir <predecessor-install> \
  --json docs/launch/evidence/shared-sdlc-paths-<sha>.json --table table.md --fixture-out tests/fixtures/predecessor_written_paths.json \
  --finding-issue '#514' --followup-issue '#515'
```

`--predecessor-dir` is the owner's installed copy; never a path inside a repository. Add `--no-samples` to skip the
scenario runs (verdicts then stop at identical-writer or unsampled).

## The guard

**Contract.** a best-effort static scan that detects the named write forms below. It does NOT detect: a path built at run time or from config, environment or a directory listing; a write through a helper module the resolver cannot follow; a subprocess or shell write; a shell script under `hooks/`; a destination held in a module constant, class attribute, helper return, local alias, concatenation, f-string or `join`; a write call outside the fixed list; a wildcard-named file through the backstop; a second write inside an already vetted function; anything under a `tests` path component, `*.pyw` or an extensionless script; and any tree outside `skills/` and `hooks/`. A passing check means every site the scan can resolve is vetted. It does not mean Sigma has no new writer.

`tests/fixtures/predecessor_written_paths.json` lists every path the predecessor writes (from this scan) and, for each
one Sigma also writes, an entry: `compatible: true` with a reason, or `compatible: false` with the filed issue, plus
`vetted_writers`, the `file::function` sites Sigma writes it from. The check scans Sigma only and needs no predecessor:

```sh
python3 tools/readiness/shared_paths.py check --sigma . --fixture tests/fixtures/predecessor_written_paths.json
```

It exits 2, naming the path and the site, when Sigma writes a predecessor-written path from a site that is not vetted, when
the path has no entry, or when an entry lacks its reason or issue. A new writer function on an already-vetted path is
refused too. An entry with `compatible: false` also passes the check once its sites are vetted: it records a known problem, it does not
block the site. A passing check means every site the scan can resolve is vetted, not that Sigma has no new writer. What it does not
see: a second write inside an already vetted function; a writer the scan cannot resolve
(above); a reason that is true in form and wrong in content (a reason is not verified). If a vetted function is renamed or
moved the check refuses: vet the new site by hand and edit its entry, do not regenerate the whole list.

Control, run on exactly that gesture: in a scratch copy, append to `skills/agrim-init/scripts/setup_wizard.py` a new
function that writes `state/setup-wizard-dismissed.json`, then run the command above with `--sigma <scratch>`. It exits 2;
on the unmodified copy it exits 0 (`tests/test_shared_sdlc_paths.py` runs both).

## Table

| Path pattern | Class | Verdict | Corruption path | Predecessor writes | Sigma writes | Readers (P / S) |
| --- | --- | --- | --- | --- | --- | --- |
| `.sdlc/config.json` | two-writer | different-format | not-demonstrated | sdlc_init.py, setup.py | sdlc_init.py, setup.py, verify_detect.py | 20 / 32 |
| `.sdlc/context` | directory | directory | - | sdlc_init.py | sdlc_init.py | 0 / 0 |
| `.sdlc/context/north-star.md` | two-writer | unsampled | - | sdlc_init.py | sdlc_init.py | 3 / 3 |
| `.sdlc/events` | directory | directory | - | assign.py, comment_watch.py, drift_watch.py +5 | assign.py, comment_watch.py, drift_watch.py +5 | 1 / 3 |
| `.sdlc/events/*-*.*.jsonl` | two-writer | unsampled | - | assign.py, comment_watch.py, drift_watch.py +5 | assign.py, comment_watch.py, drift_watch.py +5 | 1 / 1 |
| `.sdlc/features` | directory | directory | - | feature_registry.py | feature_registry.py | 14 / 17 |
| `.sdlc/features/index.json` | two-writer | different-format | demonstrated | feature_registry.py | feature_registry.py | 0 / 1 |
| `.sdlc/features/units/*.json` | two-writer | different-format | not-probed | feature_registry.py | feature_registry.py | 0 / 0 |
| `.sdlc/goals` | directory | directory | - | pipeline.py, sdlc_init.py, sources.py | pipeline.py, sdlc_init.py, sources.py | 6 / 7 |
| `.sdlc/goals/*.md` | two-writer | same-format | - | pipeline.py, sdlc_init.py | pipeline.py, sdlc_init.py | 4 / 4 |
| `.sdlc/journey` | directory | directory | - | sources.py | sources.py | 0 / 0 |
| `.sdlc/journey/*.md` | two-writer | same-format | - | sources.py | sources.py | 1 / 1 |
| `.sdlc/knowledge` | directory | directory | - | kg.py | kg.py | 1 / 3 |
| `.sdlc/knowledge/analysis` | directory | directory | - | kg.py | kg.py | 1 / 1 |
| `.sdlc/knowledge/analysis/*.md` | two-writer | unsampled | - | kg.py | kg.py | 0 / 1 |
| `.sdlc/knowledge/analysis/goal-*.md` | two-writer | unsampled | - | kg.py | kg.py | 0 / 1 |
| `.sdlc/knowledge/analysis/issue-*.md` | two-writer | unsampled | - | kg.py | kg.py | 0 / 1 |
| `.sdlc/knowledge/gaps.md` | two-writer | unsampled | - | kg.py | kg.py | 3 / 3 |
| `.sdlc/knowledge/radar` | directory | directory | - | radar.py | radar.py | 0 / 0 |
| `.sdlc/knowledge/radar/ledger.md` | two-writer | unsampled | - | radar.py | radar.py | 2 / 2 |
| `.sdlc/ledger` | directory | directory | - | ledger.py | ledger.py | 0 / 0 |
| `.sdlc/ledger/*` | directory | directory | - | assign.py, comment_watch.py, drift_watch.py +5 | assign.py, comment_watch.py, drift_watch.py +5 | 4 / 5 |
| `.sdlc/ledger/*/*-*.*.jsonl` | two-writer | unsampled | - | assign.py, comment_watch.py, drift_watch.py +5 | assign.py, comment_watch.py, drift_watch.py +5 | 3 / 3 |
| `.sdlc/ledger/TEAM.md` | two-writer | unsampled | - | assign.py, comment_watch.py, drift_watch.py +5 | assign.py, comment_watch.py, drift_watch.py +5 | 2 / 2 |
| `.sdlc/plans` | directory | directory | - | assign.py | assign.py | 0 / 0 |
| `.sdlc/plans/*-plan.md` | two-writer | unsampled | - | assign.py | assign.py | 0 / 0 |
| `.sdlc/state` | directory | directory | - | agent_watch.py, assign.py, autowatch.py +13 | agent_watch.py, assign.py, autowatch.py +18 | 0 / 3 |
| `.sdlc/state/STATE.md` | two-writer | same-format | - | assign.py, loop.py, phase_report.py +1 | assign.py, loop.py, phase_report.py +1 | 4 / 7 |
| `.sdlc/state/STATE.md.lock` | two-writer | same-format | - | assign.py, loop.py, phase_report.py +1 | assign.py, loop.py, phase_report.py +1 | 0 / 3 |
| `.sdlc/state/agent-watch-cursor.json` | two-writer | identical-writer | - | agent_watch.py | agent_watch.py | 0 / 3 |
| `.sdlc/state/agents/*` | directory | directory | - | loop.py, slack_commands_listen.py | loop.py, slack_commands_listen.py | 7 / 11 |
| `.sdlc/state/agents/*/*.active` | two-writer | same-format | - | loop.py, slack_commands_listen.py | loop.py, slack_commands_listen.py | 7 / 11 |
| `.sdlc/state/autowatch-spend.json` | two-writer | unsampled | - | autowatch.py | autowatch.py | 1 / 4 |
| `.sdlc/state/channel-notify-cursor.json` | two-writer | unsampled | - | channel_notify.py | channel_notify.py | 0 / 3 |
| `.sdlc/state/claims` | directory | directory | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 0 / 4 |
| `.sdlc/state/claims/*.claimed` | two-writer | identical-writer | - | loop.py | loop.py | 1 / 4 |
| `.sdlc/state/claims/*.lock` | two-writer | unsampled | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 1 / 4 |
| `.sdlc/state/claims/slack-cmd-*.lock` | two-writer | unsampled | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 1 / 4 |
| `.sdlc/state/comment-watch-cursor.json` | two-writer | identical-writer | - | comment_watch.py | comment_watch.py | 0 / 3 |
| `.sdlc/state/drift.meta.json` | two-writer | identical-writer | - | drift_watch.py | drift_watch.py | 1 / 4 |
| `.sdlc/state/embeddings.json` | two-writer | unsampled | - | backlog_check.py | backlog_check.py | 1 / 4 |
| `.sdlc/state/feature-judge-spend.json` | two-writer | identical-writer | - | feature_judge.py | feature_judge.py | 1 / 4 |
| `.sdlc/state/feature-judge-spend.lock` | two-writer | identical-writer | - | feature_judge.py | feature_judge.py | 0 / 3 |
| `.sdlc/state/inbox.md` | two-writer | unsampled | - | loop.py, watch.py | loop.py, watch.py | 3 / 6 |
| `.sdlc/state/landing` | directory | directory | - | cross_repo.py | cross_repo.py | 0 / 3 |
| `.sdlc/state/landing/*.json` | two-writer | identical-writer | - | cross_repo.py | cross_repo.py | 1 / 4 |
| `.sdlc/state/lease-renew` | directory | directory | - | ledger.py | - | 0 / 3 |
| `.sdlc/state/lease-renew/*.json` | one-writer | one-writer | - | ledger.py | - | 1 / 3 |
| `.sdlc/state/ledger-delivery-attempt` | two-writer | unsampled | - | loop.py | loop.py | 1 / 4 |
| `.sdlc/state/ledger-pull-deferrals` | one-writer | one-writer | - | slack_commands_listen.py, sync.py | - | 1 / 3 |
| `.sdlc/state/log` | directory | directory | - | actionlog.py, loop.py, phase_report.py +1 | actionlog.py, loop.py | 3 / 7 |
| `.sdlc/state/log/*.jsonl` | two-writer | additive | - | actionlog.py, loop.py, phase_report.py +1 | actionlog.py, loop.py | 2 / 5 |
| `.sdlc/state/phase` | directory | directory | - | phase_report.py | phase_report.py | 0 / 3 |
| `.sdlc/state/phase-boundary-stop` | directory | directory | - | loop.py, state.py | - | 0 / 3 |
| `.sdlc/state/phase-boundary-stop/*.json` | one-writer | one-writer | - | loop.py, state.py | - | 1 / 3 |
| `.sdlc/state/phase-end-*.lock` | two-writer | same-format | - | phase_report.py, state.py | phase_report.py, state.py | 0 / 3 |
| `.sdlc/state/phase/*.json` | two-writer | additive | - | phase_report.py | phase_report.py | 1 / 4 |
| `.sdlc/state/propagation` | directory | directory | - | feature_propagate.py | feature_propagate.py | 0 / 3 |
| `.sdlc/state/propagation/*.json` | two-writer | identical-writer | - | feature_propagate.py | feature_propagate.py | 1 / 4 |
| `.sdlc/state/reconcile.meta.json` | two-writer | unsampled | - | loop.py | loop.py | 1 / 4 |
| `.sdlc/state/review-queue.md` | two-writer | same-format | - | state.py | state.py | 2 / 5 |
| `.sdlc/state/run_stop` | directory | directory | - | loop.py, state.py | loop.py, state.py | 0 / 3 |
| `.sdlc/state/run_stop/*.json` | two-writer | unsampled | - | loop.py, state.py | loop.py, state.py | 1 / 4 |
| `.sdlc/state/sessions` | directory | directory | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 2 / 6 |
| `.sdlc/state/sessions/*-*.active` | two-writer | unsampled | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 4 / 9 |
| `.sdlc/state/sessions/*.active` | two-writer | additive | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 4 / 9 |
| `.sdlc/state/sessions/locks` | directory | directory | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 0 / 3 |
| `.sdlc/state/sessions/locks/*.lock` | two-writer | same-format | - | assign.py, loop.py, slack_commands_listen.py | assign.py, loop.py, slack_commands_listen.py | 0 / 3 |
| `.sdlc/state/setup-wizard-cache.json` | two-writer | same-format | - | setup_wizard.py | setup_wizard.py | 1 / 4 |
| `.sdlc/state/setup-wizard-dismissed.json` | two-writer | same-format | - | setup_wizard.py | setup_wizard.py | 1 / 4 |
| `.sdlc/state/slack-commands.heartbeat.json` | two-writer | unsampled | - | slack_commands_listen.py | slack_commands_listen.py | 1 / 5 |
| `.sdlc/state/slack-commands.log` | two-writer | unsampled | - | slack_commands_listen.py | slack_commands_listen.py | 0 / 3 |
| `.sdlc/state/slack-commands.pid` | two-writer | unsampled | - | slack_commands_listen.py | slack_commands_listen.py | 1 / 4 |
| `.sdlc/state/supervisor.log` | two-writer | unsampled | - | supervise_daemon.py | supervise_daemon.py | 0 / 3 |
| `.sdlc/state/supervisor.run.out` | two-writer | unsampled | - | supervise_daemon.py | supervise_daemon.py | 1 / 4 |
| `.sdlc/state/supervisor.tail` | two-writer | unsampled | - | supervise_daemon.py | supervise_daemon.py | 0 / 3 |
| `.sdlc/state/time` | directory | directory | - | timing_store.py | timing_store.py | 1 / 4 |
| `.sdlc/state/time/*` | directory | directory | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 4 / 7 |
| `.sdlc/state/time/*/*.jsonl` | two-writer | same-format | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 1 / 4 |
| `.sdlc/state/time/.last-prune` | two-writer | same-format | - | loop.py, timing_store.py | loop.py, timing_store.py | 2 / 5 |
| `.sdlc/state/time/_sessions/*` | directory | directory | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 2 / 5 |
| `.sdlc/state/time/_sessions/*/*.jsonl` | two-writer | same-format | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 2 / 5 |
| `.sdlc/state/time/_sessions/*/open.json` | two-writer | unsampled | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 2 / 5 |
| `.sdlc/state/time/_sessions/*/turns.jsonl` | two-writer | unsampled | - | loop.py, time_track.py, timing_store.py | loop.py, time_track.py, timing_store.py | 2 / 5 |
| `.sdlc/state/unit-tracking` | directory | directory | - | loop.py | loop.py | 0 / 3 |
| `.sdlc/state/unit-tracking/*.attempt` | two-writer | identical-writer | - | loop.py | loop.py | 1 / 4 |
| `.sdlc/state/verify/*.json` | two-writer | unsampled | - | state.py, work.py | state.py, work.py | 2 / 6 |
| `.sdlc/state/withheld` | directory | directory | - | upstream.py | upstream.py | 0 / 3 |
| `.sdlc/state/withheld/*.md` | two-writer | identical-writer | - | upstream.py | upstream.py | 0 / 3 |
| `.sdlc/state/witness` | directory | directory | - | loop.py, witness.py | loop.py, witness.py | 0 / 4 |
| `.sdlc/state/witness/*.jsonl` | two-writer | unsampled | - | loop.py, witness.py | loop.py, witness.py | 1 / 5 |
| `.sdlc/state/work` | directory | directory | - | work.py | work.py | 0 / 6 |
| `.sdlc/state/work/*.json` | two-writer | unsampled | - | work.py | work.py | 1 / 8 |
| `.sdlc/state/worktree-locks` | directory | directory | - | loop.py, work.py | - | 0 / 3 |
| `.sdlc/state/worktree-locks/*.lock` | one-writer | one-writer | - | loop.py, work.py | - | 0 / 3 |
