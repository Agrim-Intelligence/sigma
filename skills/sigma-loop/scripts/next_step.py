"""`loop.py next-step <dir> <goal>`: a goal's next procedural steps, printed from its record (#2005, FR-61).

The step-banner rules (once `sigma-implement`'s `## Announce each step`) and the slice-wave procedure (once
`references/running.md` 3b) live here, so a phase agent pays for them only when it runs this, not on every
load of its skill. The skills carry one pointer line each (design 2000.md D-34, BR-16).

Read-only: no file, action-log row, note or heartbeat is written. Every predicate is the one its writer
uses -- the route from `work.route_of`/`work.classify_blocker`, phases from `phase_gate.format_status`, the
slice switches from `work.enabled`/`slices.parallel`/`slices.manifest_path` -- so this cannot disagree with
the verbs it points at. Cost: three reads of ONE goal's action log, O(rows of that goal), no cross-goal scan.

Commands are printed with absolute script paths (host-agnostic: Codex has no `CLAUDE_SKILL_DIR`); `<sdlc>`
and `<goal>` are echoed as given."""
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
_MODEL = (_HERE.parent.parent / "sigma-model" / "scripts").resolve()


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _route(work, sdlc_dir, config, goal):
    route = work.route_of(sdlc_dir, goal)
    if route:
        return f"route: {route['route']} profile={route['profile']}"
    blocked = work.classify_blocker(sdlc_dir, config, goal)
    if not blocked:
        return (f'route: unclassified -> classify it now: python3 "{_HERE / "loop.py"}" classify "{sdlc_dir}" '
                f'"{goal}" "trivial|small|standard|heavy" --rationale "<why>"')
    kind, reason = blocked
    if kind == "legacy":
        return ("route: legacy -- plan-review approved before the lean route; finishes under the 1.0.x rules "
                "(FR-33); do not classify")
    return f"route: unclassified -- {reason}; the goal runs on the legacy rules"


def _banner(sdlc_dir, goal):
    return [
        "step banner (print each plan step's as you start it):",
        f'  python3 "{_HERE / "phase_report.py"}" step "{sdlc_dir}" "{goal}" implement --num <n> --total <m> '
        '--phase-model --brief "<what>"',
        "  --phase-model only when this phase agent runs the step; a separately dispatched step passes "
        "--model <step-tier> --host-model <exact-host-model-id> instead.",
        "  The sdlc dir is the MAIN checkout's absolute .sdlc (else the tier reads unrecorded). "
        "Brief <= 88 chars. It writes nothing; skip it rather than block a step.",
    ]


def _per_slice(work, sdlc_dir, goal, prog):
    """#2006 (D14): a lean multi-PR goal ships one PR per slice -- the sequence, never the in-one-PR wave."""
    wp = _HERE / "work.py"
    return [
        f"slices: one pull request per slice, in manifest order ({', '.join(prog['ids'])}). For each slice:",
        f'  python3 "{wp}" slice "{sdlc_dir}" "{goal}"   (cuts the next slice branch; refuses while its PR is open)',
        "  implement it test-first and review it, then:",
        f'  python3 "{wp}" pr "{sdlc_dir}" "{goal}"   (Refs #N on every slice but the last, Closes #N on the last)',
        "  merge it (work.py merge) or record review; never `record done` before the last slice has merged.",
    ]


def _between(work, sdlc_dir, config, goal):
    """#2006 (D14): printed REGARDLESS of the implement mark -- the local record's current slice is merged, so the
    next gesture is the next slice. A local read only (display); the gates read GitHub."""
    prog = work.slice_progress(sdlc_dir, config, goal)
    if not prog or not prog["current"] or prog["current"] not in prog["merged"] or not prog["k"]:
        return []
    return [f"between slices: slice {prog['current']} merged ({prog['k']} of {prog['n']}) per the local record; "
            f'next: python3 "{_HERE / "work.py"}" slice "{sdlc_dir}" "{goal}", then start implement again']


def _slices(work, slices, sdlc_dir, config, goal):
    if not work.enabled(config):
        tail = ", slices in manifest order" if slices.manifest_path(sdlc_dir, goal).is_file() else ""
        return ["slices: work.enabled is off -- run the goal as one unit" + tail]
    prog = work.slice_progress(sdlc_dir, config, goal)
    if prog:
        return _per_slice(work, sdlc_dir, goal, prog)
    if not slices.manifest_path(sdlc_dir, goal).is_file() or not slices.parallel(config)[0]:
        return ["slices: none declared (or parallel.enabled off) -- run the goal as one unit"]
    loop = _HERE / "loop.py"
    return [
        "slices: run the wave, don't queue it. Plan it:",
        f'  python3 "{_HERE / "slices.py"}" plan "{sdlc_dir}" "{goal}"',
        "  On Codex add --host codex --goal-worktree <absolute-path> --tier <tier-or-off> --effort <effort> "
        "(using the goal worktree from `work.py start`). Before every Codex slice dispatch, resolve its goal or "
        "resolve-step tier with its effort and pass the returned model ID and effort to the subagent:",
        f'  python3 "{_MODEL / "predict.py"}" host-model codex "<tier-or-off>" "{sdlc_dir}" --effort "<effort>"',
        "  ONE wave at a time, one fresh subagent per slice. `isolation: worktree` is a requirement, not proof: "
        "Codex slices sharing a checkout run sequentially.",
        "  Per slice, log it (<tier>: the goal's tier, or the step's resolve-step tier) and register its pid:",
        f'  python3 "{loop}" log "{sdlc_dir}" "{goal}" agent_dispatch --thread <slice-id> --role slice '
        "--phase implement --model <tier>",
        f'  python3 "{loop}" agent-start "{sdlc_dir}" "{goal}" --pid $PPID --thread <slice-id>',
        f'  Land the wave, logging each slice (python3 "{loop}" log "{sdlc_dir}" "{goal}" agent_done '
        "--thread <slice-id> --role slice --result <...>), then re-run plan for the next wave.",
        "  Never dispatch a slice with unattended `claude -p` or `codex exec` (a second worker on one .sdlc "
        "breaks its state). A `dispatch: session` slice: print its interactive command for the human; never "
        "start it yourself.",
    ]


def render(sdlc_dir, config, goal):
    """The next-step text for one goal. Raises ValueError, printing nothing, for an unsafe goal."""
    work, state = _load("work"), _load("state")
    reason = state.unsafe_goal_reason(work.stem(goal))
    if reason:
        raise ValueError(f"unsafe goal {goal!r}: {reason}")
    phase_gate = _load("phase_gate")
    lines = [_route(work, sdlc_dir, config, goal), phase_gate.format_status(sdlc_dir, config, goal)]
    lines += _between(work, sdlc_dir, config, goal)
    # implement counts as ended only while its LATEST start/end row is an end: a re-start after a
    # send-back brings the banner and the slice procedure back. `actionlog.read_goal` is the public
    # reader `phase_gate` itself wraps (malformed rows are skipped there, never a crash).
    marks = [r.get("state") for r in _load("actionlog").read_goal(sdlc_dir, goal)
             if r.get("kind") == "phase" and r.get("phase") == "implement" and r.get("state") in ("start", "end")]
    if not (marks and marks[-1] == "end"):
        lines += _banner(sdlc_dir, goal) + _slices(work, _load("slices"), sdlc_dir, config, goal)
    return "\n".join(lines)
