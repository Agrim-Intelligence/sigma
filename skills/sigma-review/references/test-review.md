# Test review (#1964)

A separate reviewer judging AI-written tests is reported to find problems in about 30% of PRs
(Software Testing Weekly #329). Sigma's version adds no reviewer of its own. The reviewer
`reviewer.py resolve` already picked writes a criterion-to-test map, and Python checks that map
against the acceptance record and the verify evidence.

## What the reviewer sees

Every code-review and pr-review brief carries `## Test-trust facts`. It lists the recorded
acceptance criteria with ids, the red-green proof, the flake verdict, and the diff-revert kills,
survivors and survivors on changed lines. Each fact reads `present`, `absent` or `unreachable`.
With `review.test_review: true` the brief also asks for the map. The key defaults to off, and the
template ships `false`.

## What the reviewer writes (key on)

Put these first in the output, so a capped receipt still keeps them:

```
TEST-MAP AC-1: tests/test_x.py::test_a, tests/test_x.py::TestK::test_m
TEST-MAP AC-2: NONE
TEST-MAP AC-3: UNTESTABLE: a README sentence; no test can prove prose
SURVIVOR-OK tests/test_x.py::test_b: pins unchanged behaviour the change relies on
```

Ids are pytest node ids relative to the repository root. Name only a test that would fail if the
criterion were not met.

## What blocks

`python3 "${CLAUDE_SKILL_DIR}/../sigma-loop/scripts/review_tests.py" check .sdlc <goal> --review <file>`
prints one `BLOCKING:` line per finding, then `TEST-REVIEW: block|pass|partial - ...`. It exits 1
on `block`. These are blocking findings:
- a criterion with no `TEST-MAP` line, mapped `NONE`, mapped `UNTESTABLE` with no reason, mapped
  only to tests that do not exist, or given two conflicting lines;
- two criteria with the same id;
- a diff-revert survivor on a line the goal changed, unless the reviewer accepted it with
  `SURVIVOR-OK <id>: <why>`.

`partial` means the survivor clause was not judged, and the line says why: no evidence, a
diff-revert that measured nothing, or evidence older than the field. It does not refuse, and it
is never printed as a pass.

## Where it is enforced (key on)

- `work.py record-review --verdict APPROVE --review-output <file>` refuses a blocking review. The
  lever is to record `--verdict SEND-BACK` with the findings. A missing `--review-output` is refused
  too.
- Every PR route goes through the review-result writer. There, a blocking `approve` or `unblock` is
  written as `block`, with the findings ahead of the reviewer's text. On the subagent route,
  `record-subagent-review --reason` must be the reviewer's full output:
  `--reason "$(cat <file>)"` keeps its backticks from running.
- `post-review`, asked to approve evidence whose result is a block, posts the block. This holds
  with the key off too.

#1964's acceptance record names this gesture test_review.py (the planned name). It ships as `review_tests.py`
because a `test_*.py` script matches `tamper_scan._TEST_PATH`, and diff-revert's
`production_changed` would then call a change to it test-only.

## How the open issues were decided

- **#535 criterion ids.** A bullet starting `AC-<n>:` keeps its id. Every other bullet takes
  `AC-<position>`, which is #535's own rule for bullets without ids. When #535 lands, no format
  here changes. EARS lint stays #535's.
- **#568 mutation.** Not duplicated. The survivor clause reads `diff_revert`, which reverts the whole
  production diff and needs no mutmut. #568's per-mutant survivors and kill-rate floors would feed the
  same facts section when it lands.
- **What counts as a survivor on a changed line.** A survivor whose own function (first decorator to
  last line) overlaps a `git diff -U0` hunk against the fork, on either side: a deleted assertion
  or decorator is judged where it was in the fork, so deleting a whole test marks no neighbour.
  A file that is new since the fork counts as changed throughout. Tests
  that failed to collect at the fork never stayed green, so they are excluded. If the goal changed
  no production file, nothing was reverted and nothing is flagged. Records under `.sdlc/` and
  tests are not production; docs are, because a test may read them.

## UNMEASURED

- **The real catch rate against the "30% of PRs" figure.** No goal has run with
  `review.test_review` on, so Sigma has no number of its own. The controls prove each clause
  blocks the input it targets; they say nothing about how often real reviews carry such defects.
- **The false-block rate**, of the survivor rule above all.
- **A real host end to end.** Only fixtures and fake reviewer text have driven the gate.
- **Reviewer honesty.** On the subagent route the orchestrator relays the TEST-MAP text, as it does
  the verdict (#537). On the inline route the maker writes the map itself.
- **Evidence freshness.** The check reads the last `loop.py verify`. `state.done_refusal` refuses a
  tree changed since then, and this check does not re-check it.
- **Limits.** For pytest ids, test existence is an AST lookup. Any other id needs a file on a test
  path (`tamper_scan._TEST_PATH`). An inherited method reads as missing, so map the class that defines it. A key switched on between
  the brief and the result blocks every criterion; a fresh generation clears that.
