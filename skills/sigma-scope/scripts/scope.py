#!/usr/bin/env python3
"""scope.py (#920, wave 3 of epic #902) -- sigma-scope's own thin orchestration glue, named after
the skill itself (matching `sigma-loop/scripts/loop.py`'s own precedent: a skill's PRIMARY driver
script is named after the skill, distinct from its function-named siblings brainstorm/dedup/
compile_plan/assign). This is the ONE genuinely mechanical gap the orchestration layer needs
closed, and nothing more -- everything else this issue is about (target confirmation, clarifying
questions, drafting the plan's actual content, the assignment/execution-path decision) is agentic
and lives in `skills/sigma-scope/SKILL.md`'s own prose instead, not here.

THE GAP: `compile_plan.py`'s own CLI (`main()`) printed human text ONLY when this module shipped
-- `assign.py` did not exist yet when #918 shipped, so its CLI was never given a reason to emit a
machine-readable report. (#1919 has since added a `--json` arm to that CLI, for `sigma-goal-review`,
so the two now OVERLAP: both hand back the same report dict as JSON. `--json` prints and lets the
caller redirect; this module writes `--report <path>` itself and keeps stdout authoritative when
that write fails. Read that as overlap, not as a distinction -- nothing below is load-bearing
against the flag, and if the two are ever collapsed, the next paragraph is the requirement that
has to survive.) `assign.py`'s own `execute` CLI, once #919 shipped, needs exactly that report as
a REAL JSON FILE to chain against (`--report <path>`), matching the file-based-artifact convention
this whole toolchain already uses (`brainstorm.py`/`dedup.py`/`assign.py resolve` all print a single
JSON line; `/sigma-triage`'s own plan.json/active.json are real files a later step re-reads). Between
compiling the plan and deciding assignment sits a genuine human-input pause (the clarifying-
questions + assignment-choice steps this skill's own SKILL.md drives) -- so whatever calls
`compile_plan.compile_plan()` MUST persist its report to disk before that pause, and whatever later
calls `assign.execute()` must reload it after. A file is structurally required here, not a nicety.

NOT a re-derivation: this module calls the REAL, already-tested `compile_plan.compile_plan()`
function exactly once and does nothing else to its return value beyond serializing it -- no
validation, no re-ordering, no new business logic. Neither `compile_plan.py` nor `assign.py` is
modified by this file; both stay exactly as #918/#919 shipped them.

    python3 scope.py <sdlc_dir> --plan <plan.json> [--actionable] [--report <path>]
        # runs compile_plan.compile_plan() once, always prints the report as ONE JSON line to
        # stdout (matching brainstorm.py/dedup.py/assign.py resolve's own convention), and ALSO
        # writes the identical JSON to <path> when --report is given, so `assign.py execute
        # --report <path>` has a real file to read. The SAME --plan file is handed to `assign.py
        # execute --plan <path>` too -- this module never rewrites or moves it.
"""
import importlib.util
import json
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent


def _load_sibling(name):
    """Cross-load a script from THIS SAME skill's scripts/ dir (skills/sigma-scope/scripts/) --
    the same `importlib.util.spec_from_file_location` pattern every sibling in this family already
    uses to load ACROSS skill directories; here it stays within one, but the mechanism is identical
    so a reader who has seen any other module in this family recognizes it immediately."""
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_loop_script(name):
    """Cross-load a script from the sibling sigma-loop skill -- mirrors `compile_plan.py`'s own
    `_load_loop_script` byte-for-byte (the established, narrow, named exception to "don't reach
    across skill directories" this whole epic already relies on)."""
    path = _HERE.parent.parent / "sigma-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


compile_plan = _load_sibling("compile_plan")
ledger = _load_loop_script("ledger")   # ledger._config() -- the same config loader every CLI here uses


def compile_and_report(sdlc_dir, plan, *, actionable=False, config=None, source=None):
    """Run `compile_plan.compile_plan()` exactly once and return its report dict, unmodified.
    `config` defaults to `ledger._config(sdlc_dir)` (byte-identical to `compile_plan.py`'s own CLI,
    and to every sibling CLI in this family) when the caller doesn't already have one loaded.

    Raises `ValueError` for a structurally invalid plan -- passed straight through from
    `compile_plan.compile_plan()` itself (see its own docstring): a caller can trust that a raise
    here means nothing was created, exactly as that function promises."""
    cfg = config if config is not None else ledger._config(sdlc_dir)
    return compile_plan.compile_plan(sdlc_dir, cfg, plan, source=source, goal_label=actionable)


USAGE = "usage: scope.py <sdlc_dir> --plan <plan.json> [--actionable] [--report <path>]"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    import sys

    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    sdlc_dir = argv[1]
    rest = argv[2:]
    plan_path, report_path, actionable = None, None, False
    i = 0
    while i < len(rest):
        if rest[i] == "--plan" and i + 1 < len(rest):
            plan_path = rest[i + 1]
            i += 2
        elif rest[i] == "--report" and i + 1 < len(rest):
            report_path = rest[i + 1]
            i += 2
        elif rest[i] == "--actionable":
            actionable = True
            i += 1
        else:
            i += 1
    if not plan_path:
        print(USAGE, file=sys.stderr)
        return 2

    try:
        plan = json.loads(pathlib.Path(plan_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"scope.py: could not read plan {plan_path!r}: {exc}", file=sys.stderr)
        return 2

    try:
        report = compile_and_report(sdlc_dir, plan, actionable=actionable)
    except ValueError as exc:
        print(f"scope.py: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report, ensure_ascii=False, sort_keys=True)
    print(payload)
    if report_path:
        try:
            report_file = pathlib.Path(report_path)
            report_file.parent.mkdir(parents=True, exist_ok=True)
            report_file.write_text(payload, encoding="utf-8")
        except OSError as exc:
            print(f"scope.py: could not write --report to {report_path!r}: {exc} "
                  "(the report above on stdout is still the authoritative result -- "
                  "compile_plan already ran, nothing here needs re-running)", file=sys.stderr)
            return 1
    return 1 if (report["failed"] or report["skipped"]) else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
