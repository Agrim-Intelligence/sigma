#!/usr/bin/env python3
"""Slice-level parallelism: run the independent parts of one goal at the same time, safely.

THE PROBLEM. A goal is planned as slices and the loop runs them strictly one after another, even when
three of them touch nothing in common. Sequential is not merely slow: a long goal spends ONE session's
context on work that had no reason to share a window, so the last slice starts on a flushed one.

WHY A DECLARED MANIFEST, NEVER INFERENCE. Concurrency is safe only where the blast radius is stated.
Two slices that each declare which files they touch can be PROVEN disjoint; a slice that declares
nothing cannot be, so this module treats it as conflicting with everything and it runs alone. Guessing
the radius would trade a slow run for two workers silently overwriting each other — a much worse bug
than waiting.

WHERE THE BOUNDARY IS. A python script cannot spawn a subagent — only the agent can. So this module
COMPUTES and ADVISES: it reads the manifest, works out which slices may run together, and renders a
dispatch plan. `/agrim-loop`'s prose does the dispatching, one **subagent** per slice, with
`isolation: worktree` whenever the slice declares files so two of them cannot fight over one checkout.

AND WHERE IT STOPS BEING HONEST TO AUTOMATE. A slice too large for one subagent's context needs a real
session, and the truthful answer is to PRINT the `claude --worktree <name>` line for a human to start.
The kit never shells out to an unattended `claude -p`: that is uncapped spend, and it would put a
second worker on one `.sdlc`, which every state file in this kit assumes never happens.

Default OFF (`parallel.enabled`): with no `parallel` block, and with no manifest beside the plan, the
loop behaves exactly as it did before this module existed. Zero deps.
"""
import importlib.util
import json
import pathlib
import posixpath
import shlex
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent

# `host-model` owns tier selection, but this dispatch boundary still rejects a
# malformed or stale CLI result before it becomes an executable command.
_CODEX_MODELS = {
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
}
_CODEX_EFFORTS = {"low", "medium", "high"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def validate_codex_model_pair(pair):
    """Copy a concrete model pair only when it is safe to render in a Codex command."""
    if not isinstance(pair, dict) or set(pair) != {"model", "effort"}:
        raise ValueError("host-model resolver returned an invalid result")
    model, effort = pair["model"], pair["effort"]
    if (not isinstance(model, str) or model not in _CODEX_MODELS
            or not isinstance(effort, str) or effort not in _CODEX_EFFORTS):
        raise ValueError("host-model resolver returned an invalid result")
    return {"model": model, "effort": effort}


def resolve_codex_model(tier, sdlc_dir):
    """Resolve a portable tier through the sibling CLI; never import a different skill's Python."""
    predict_py = _HERE.parent.parent / "agrim-model" / "scripts" / "predict.py"
    try:
        proc = subprocess.run([sys.executable, str(predict_py), "host-model", "codex",
                               str(tier or ""), str(sdlc_dir)], capture_output=True, text=True,
                              timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"host-model resolver could not run: {exc}") from None
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        detail = detail.splitlines()[-1] if detail else f"exit {proc.returncode}"
        raise ValueError(detail)
    pairs = {}
    for token in (proc.stdout or "").strip().split():
        key, separator, value = token.partition("=")
        if not separator or not key or not value or key in pairs:
            raise ValueError("host-model resolver returned an invalid result")
        pairs[key] = value
    return validate_codex_model_pair(pairs)


ledger = _load("ledger")            # team record (config-gated, default OFF; every call is fail-open)
state = _load("state")              # unsafe_goal_reason() — the shared goal-path validator
owners = _load("owners")            # _glob_match() — the CODEOWNERS-correct glob primitive, post-#355

#: Manifest suffix, written beside the goal's plan so the two are groomed together.
SUFFIX = ".slices.json"

DEFAULT_SIZE = "small"
DEFAULT_STATUS = "pending"
DONE = "done"
LARGE = "large"

#: The two dispatch modes. `subagent` is the automatable one (fresh isolated context, optional
#: worktree); `session` is the honest escape hatch a human starts — see the module docstring.
SUBAGENT = "subagent"
SESSION = "session"

#: Three concurrent slices is the default ceiling: enough to pay for the coordination, small enough
#: that a wave's diffs are still reviewable as one landing.
DEFAULT_MAX_CONCURRENT = 3


# --------------------------------------------------------------------------- config


def settings(config):
    return (config or {}).get("parallel") or {}


def parallel(config):
    """-> (enabled, max_concurrent). `is True` on purpose: a truthy string or a stray 1 must not
    silently start several workers against one checkout. A non-numeric cap falls back to the default
    rather than raising — a typo in config must not break a run that would otherwise be sequential."""
    block = settings(config)
    try:
        cap = int(block.get("max_concurrent", DEFAULT_MAX_CONCURRENT))
    except (TypeError, ValueError):
        cap = DEFAULT_MAX_CONCURRENT
    return (block.get("enabled") is True, max(1, cap))


def _config(sdlc_dir):
    """Tolerant read: `plan` is advisory output and has to work in a directory that has no config yet
    (or a half-written one), so an unreadable file reads as "no opinion", not as a failure."""
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------- load


def goal_stem(goal):
    """The goal's identity as a filename part. Same rule as loop.py's evidence path: a `.md` goal is a
    PATH (take its stem), a github goal is an issue NUMBER (take it as-is)."""
    text = str(goal)
    return pathlib.Path(text).stem if text.endswith(".md") else text


def manifest_path(sdlc_dir, goal):
    """Raises `ValueError` for an unsafe `goal` — same validator, same reasoning as
    `state.unsafe_goal_reason`'s own docstring (independent review of #486/PR #487 found this
    exact unguarded pattern repeated across several `stem(goal)`-into-path call sites)."""
    stem = goal_stem(goal)
    reason = state.unsafe_goal_reason(stem)
    if reason:
        raise ValueError(f"unsafe goal {goal!r} for the slice manifest: {reason}")
    return pathlib.Path(sdlc_dir) / "plans" / f"{stem}{SUFFIX}"


def _as_list(value):
    """A single string where a list belongs is the commonest hand-edit slip; accept it rather than
    reporting a type error the author cannot act on. Blanks are dropped — an empty files entry would
    otherwise read as a declared radius that matches nothing."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


def _normalise(item, index, path):
    if not isinstance(item, dict):
        raise ValueError(f"{path}: slice #{index} is a {type(item).__name__}, expected an object")
    return {
        # #141: `id` lands in the `slice` journal event (ledger.py `EVENT_BOUNDED_ID_FIELDS
        # ["slice"]`) verbatim HERE — but not any more once it reaches `ledger.append()`. A PR
        # review BLOCKED #249 by proving "declared safe by convention" (this comment's own former
        # wording) was never actually enforced: an agent-authored plan `id` could carry anything,
        # including a secret shape, with nothing here or in ledger.py checking its length or
        # shape. `append()` now scrub+caps every bounded-identifier field (SHORT: BOUNDED_ID_CAP,
        # not the prose cap) at the one chokepoint every write path already funnels through — this
        # module still does no enforcement of its own, on purpose; that's ledger.py's job.
        "id": str(item.get("id") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "needs": _as_list(item.get("needs")),
        "files": _as_list(item.get("files")),
        "size": str(item.get("size") or DEFAULT_SIZE).strip() or DEFAULT_SIZE,
        "status": str(item.get("status") or DEFAULT_STATUS).strip() or DEFAULT_STATUS,
    }


def load(sdlc_dir, goal):
    """The manifest as a normalised list, `[]` when there is none (the goal simply runs whole).

    A broken manifest RAISES and names the file: a slice plan that silently reads as empty would run
    the whole goal in one session while its author believed it was parallelised."""
    path = manifest_path(sdlc_dir, goal)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path}: not valid JSON ({exc})") from None
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of slices, got {type(raw).__name__}")
    return [_normalise(item, i, path) for i, item in enumerate(raw)]


# --------------------------------------------------------------------------- validate


def _cycles(slices):
    """Every dependency cycle, each as its ordered members.

    Reported by MEMBERS rather than as "a cycle exists" because the fix is to break one edge, and you
    cannot choose which edge without the names. A cycle is recorded once however many entry points
    reach it."""
    known = {s["id"] for s in slices}
    graph = {}
    for s in slices:
        graph.setdefault(s["id"], [])
        graph[s["id"]] += [n for n in s["needs"] if n in known]
    colour, found, seen = {}, [], set()

    def walk(node, path):
        colour[node] = 1                                    # 1 = on the current stack
        path.append(node)
        for nxt in graph.get(node, []):
            if colour.get(nxt) == 1:                        # back-edge: the cycle is the tail of path
                cycle = path[path.index(nxt):]
                key = frozenset(cycle)
                if key not in seen:
                    seen.add(key)
                    found.append(cycle)
            elif colour.get(nxt, 0) == 0:
                walk(nxt, path)
        path.pop()
        colour[node] = 2                                    # 2 = fully explored

    for s in slices:
        if colour.get(s["id"], 0) == 0:
            walk(s["id"], [])
    return found


def validate(slices):
    """-> human-readable problems, in manifest order; empty means the manifest is safe to schedule.

    Everything here makes the schedule WRONG rather than merely ugly: a missing or duplicated id makes
    a dependency ambiguous, an unknown `needs` can never become done (that slice would never run), and
    a cycle means no frontier ever opens."""
    problems = []
    ids = [s["id"] for s in slices]
    for index, s in enumerate(slices):
        if not s["id"]:
            problems.append(f'slice #{index} has no "id"')
    reported = set()
    for sid in ids:
        if sid and ids.count(sid) > 1 and sid not in reported:
            reported.add(sid)
            problems.append(f'duplicate slice id "{sid}"')
    known = {sid for sid in ids if sid}
    for s in slices:
        for need in s["needs"]:
            if need not in known:
                problems.append(f'slice "{s["id"]}" needs unknown slice "{need}"')
    for cycle in _cycles(slices):
        problems.append("dependency cycle: " + " -> ".join(cycle + [cycle[0]]))
    return problems


# --------------------------------------------------------------------------- graph


def frontier(slices):
    """The runnable set right now: not done, and every declared dependency already done. A `needs`
    that names nothing real is never satisfied, which keeps that slice out of the frontier instead of
    letting it run early — `validate` is where the author is told why."""
    done = {s["id"] for s in slices if s["status"] == DONE}
    return [s for s in slices if s["status"] != DONE and all(n in done for n in s["needs"])]


def fan_out(slices):
    """-> {id: how many slices transitively wait on it}.

    TRANSITIVE, not direct: the point of the ordering is to keep the critical path moving, and a slice
    with one dependent that itself unblocks five matters far more than a leaf with one dependent.
    Cycle-safe (a visited set), so a malformed manifest still produces a number instead of hanging."""
    dependents = {s["id"]: set() for s in slices}
    for s in slices:
        for need in s["needs"]:
            if need in dependents:
                dependents[need].add(s["id"])
    out = {}
    for sid in dependents:
        seen, stack = set(), list(dependents[sid])
        while stack:
            node = stack.pop()
            if node in seen or node == sid:
                continue
            seen.add(node)
            stack.extend(dependents.get(node, ()))
        out[sid] = len(seen)
    return out


# --------------------------------------------------------------------------- conflicts


def _literal_prefix(pattern):
    """The leading path segments before the first wildcard — `engine/**/loader.py` -> `engine/`.

    fnmatch alone cannot see that `engine/**` and `engine/graph.py` reach the same file from opposite
    directions, and a false "disjoint" here is two workers editing one file."""
    head = []
    for part in str(pattern).strip().rstrip("/").split("/"):
        if any(ch in part for ch in "*?["):
            break
        head.append(part)
    return "/".join(head) + "/"


def _normpath(p):
    """The same file, spelled two ways (`engine/graph.py` vs `./engine/graph.py` vs
    `engine\\graph.py`), must compare equal — a purely lexical comparison sees them as disjoint,
    and two slices declaring the same file under different spellings would then be dispatched as
    concurrent worktree subagents editing the same real file: a merge conflict at best, a silently
    dropped edit at worst — exactly what the declared-files manifest exists to prevent. Unify
    separators BEFORE normpath: posixpath only recognizes `/` — a `\\` would pass through untouched
    otherwise, on any platform (this collapses `.`/`..`/redundant slashes purely lexically, same as
    every other path in a manifest; it never touches the filesystem)."""
    return posixpath.normpath(str(p).replace("\\", "/"))


def _overlap(a, b):
    """Could glob `a` and glob `b` match one real file between them?

    Each side is tried both as the "path" and as the "pattern" via `owners._glob_match` (#442: this
    used to be plain `fnmatch.fnmatch`, whose `*` matches `/` too — the identical over-match
    `owners._matches` was fixed for, F30/#355, PR #440 — so a segment-scoped glob like `engine/*`
    over-matched a deep descendant like `engine/a/b/c.py`; sharing `_glob_match` instead of a second
    independent copy of the same fix keeps the two matchers from drifting apart again). A bare `*` is
    kept as its own "matches everything" case, exactly like `owners._matches`'s: `_glob_match`'s `*`
    is deliberately segment-scoped, so without this a declared blast radius of `*` (a slice that
    could touch anything, anywhere) would stop conflicting with a plain nested file — the dangerous
    direction, an under-detected conflict, not the safe one."""
    a, b = _normpath(a), _normpath(b)
    if a == "*" or b == "*":
        return True
    return (owners._glob_match(a, b) or owners._glob_match(b, a)
            or _literal_prefix(a).startswith(_literal_prefix(b))
            or _literal_prefix(b).startswith(_literal_prefix(a)))


def conflicts(a, b):
    """True when two slices could touch the same file, so they must not run in one wave.

    A slice with NO declared files conflicts with EVERYTHING. That is the whole safety property: an
    unknown blast radius is not something you may parallelise, and the cost of being wrong (a lost
    edit) is far higher than the cost of one extra wave."""
    files_a, files_b = a.get("files") or [], b.get("files") or []
    if not files_a or not files_b:
        return True
    return any(_overlap(pa, pb) for pa in files_a for pb in files_b)


# --------------------------------------------------------------------------- schedule


def schedule(slices, max_concurrent=DEFAULT_MAX_CONCURRENT):
    """-> ordered waves; each wave is a mutually non-conflicting slice of the frontier, capped.

    Within a wave, widest fan-out first (ties broken by manifest order) so a wave is never spent on
    leaves while the critical path waits. Fully deterministic — the same manifest always yields the
    same waves, which is what makes the plan reviewable before anything is dispatched.

    A frontier that never opens (a cycle, or a dependency on something absent) STOPS rather than
    looping: the remaining slices are simply not in the plan, and `validate` names the reason."""
    cap = max(1, int(max_concurrent or 1))
    order = {s["id"]: i for i, s in enumerate(slices)}
    fan = fan_out(slices)
    done = {s["id"] for s in slices if s["status"] == DONE}
    remaining = [s for s in slices if s["status"] != DONE]
    waves = []
    while remaining:
        ready = [s for s in remaining if all(n in done for n in s["needs"])]
        if not ready:
            break
        ready.sort(key=lambda s: (-fan.get(s["id"], 0), order.get(s["id"], 0)))
        wave = []
        for candidate in ready:
            if len(wave) >= cap:
                break
            if any(conflicts(candidate, picked) for picked in wave):
                continue                                    # not now — it leads the next wave instead
            wave.append(candidate)
        waves.append(wave)
        taken = {s["id"] for s in wave}
        done |= taken
        remaining = [s for s in remaining if s["id"] not in taken]
    return waves


# --------------------------------------------------------------------------- dispatch


def dispatch(slice_obj):
    """How this slice should be run. `large` means "will not fit one subagent's context", and the only
    honest answer to that is a real session a human starts — see the module docstring."""
    return SESSION if (slice_obj.get("size") or DEFAULT_SIZE) == LARGE else SUBAGENT


def needs_worktree(slice_obj):
    """A slice that declares files gets its own checkout, so a concurrent sibling cannot see (or
    stomp) its half-finished edits. A slice that declares none runs alone anyway, so a worktree would
    buy nothing and only cost a checkout."""
    return bool(slice_obj.get("files"))


def worktree_name(goal, slice_obj):
    return f"{goal_stem(goal)}-{slice_obj.get('id', '')}"


def session_command(goal, slice_obj, host="claude", goal_worktree=None, manifest=None,
                    tier=None, sdlc_dir=".sdlc", model_pair=None):
    """The exact line a human runs. Printed, never executed: an unattended `claude -p` is uncapped
    spend and would put a second worker on one `.sdlc`."""
    if host == "codex":
        worktree = pathlib.Path(goal_worktree).resolve() if goal_worktree else None
        manifest_path = pathlib.Path(manifest).resolve() if manifest else None
        if not worktree or not worktree.is_dir() or not manifest_path or not manifest_path.is_file():
            return ("REFUSED: Codex session needs an existing goal worktree and readable slice "
                    "manifest; rerun plan with --goal-worktree <absolute-path> and start one "
                    "slice at a time")
        try:
            if model_pair is None:
                model_pair = resolve_codex_model(tier, sdlc_dir)
            else:
                model_pair = validate_codex_model_pair(model_pair)
        except ValueError as exc:
            return f"REFUSED: Codex session needs a resolvable --tier: {exc}"
        prompt = (f"Continue Sigma goal {goal_stem(goal)} slice {slice_obj.get('id', '')}. "
                  f"Read the slice manifest at {manifest_path} in the main checkout and the goal "
                  "before editing. Work only in this goal worktree. Do not commit; leave changes "
                  "for the Sigma parent to verify and land. This slice runs alone.")
        config = shlex.quote(f'model_reasoning_effort="{model_pair["effort"]}"')
        return (f"codex --model {shlex.quote(model_pair['model'])} -c {config} -C "
                f"{shlex.quote(str(worktree))} {shlex.quote(prompt)}")
    return f"claude --worktree {worktree_name(goal, slice_obj)}"


# --------------------------------------------------------------------------- render


def render(plan, goal, host="claude", goal_worktree=None, manifest=None, tier=None,
           sdlc_dir=".sdlc", model_pair=None):
    """The dispatch plan a human reads before approving it, and the agent reads before acting on it.
    Every line names the one decision it carries: what runs, how, isolated or not."""
    total = sum(len(wave) for wave in plan)
    widest = max((len(wave) for wave in plan), default=0)
    lines = [f"# Slice plan — {goal_stem(goal)}", ""]
    if not plan:
        lines += ["No runnable slices: every slice is done, or nothing is unblocked "
                  "(run `slices.py check` for why).", ""]
        return "\n".join(lines)
    if host == "codex":
        lines += [f"{total} slice(s) in {len(plan)} wave(s); the widest wave contains {widest} slice(s).",
                  "Run Codex slices sequentially unless each has a distinct worktree and a verified "
                  "landing path.", "Dispatch one wave at a time, land it, then compute the next.", ""]
    else:
        lines += [f"{total} slice(s) in {len(plan)} wave(s); the widest wave runs {widest} concurrently.",
                  "Dispatch one wave at a time, land it, then compute the next.", ""]
    for number, wave in enumerate(plan, start=1):
        lines.append(f"## Wave {number} — {len(wave)} slice(s)")
        for s in wave:
            mode = dispatch(s)
            isolation = ("isolation: worktree" if needs_worktree(s)
                         else "no worktree (declares no files — runs alone)")
            lines.append(f"  - {s['id']} · {s['title'] or '(untitled)'}")
            lines.append(f"      dispatch: {mode}   ·   {isolation}")
            if mode == SESSION:
                lines.append("      too large for one subagent's context — start it yourself:")
                lines.append(f"      {session_command(goal, s, host=host, goal_worktree=goal_worktree, manifest=manifest, tier=tier, sdlc_dir=sdlc_dir, model_pair=model_pair)}")
        lines.append("")
    if host == "codex":
        lines += ["Codex subagents sharing a checkout run sequentially. Never dispatch a slice with",
                  "unattended `codex exec` or `claude -p`; a second worker can corrupt .sdlc state.", ""]
    else:
        lines += ["Run a wave's `subagent` slices concurrently; never dispatch one with an unattended",
                  "`claude -p` — uncapped spend, and a second worker on one .sdlc breaks its state.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


#: #541: the one flag this module's own CLI (`schedule --max N`) ever hands a real value to.
_VALUE_FLAGS = frozenset({"max", "host", "goal-worktree", "tier"})
_KNOWN_FLAGS = _VALUE_FLAGS


def _flags(argv):
    """`--name value` / bare `--flag` (-> `"true"`) scanner, plus `--name=value` (unambiguous for
    ANY flag) and unconditional-consume for this module's own known value-taking flags
    (`_VALUE_FLAGS`) -- #541: a value that itself starts with '--' used to be silently swallowed:
    the flag landed on the `"true"` sentinel and the value's own text was misparsed as a SECOND,
    garbage flag. A flag name is never legitimately whitespace-bearing -- that shape is always
    leaked prose from a value the old heuristic failed to consume, never a flag a caller meant to
    pass, so it is dropped instead of kept as a nonsense key. Deliberately a copy of ledger.py's
    parser rather than an import: this module stays usable and testable without pulling the ledger
    (and its git/gh surface) in behind it."""
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            name, eq, value = token[2:].partition("=")
            if eq:                                       # --name=value: always unambiguous
                if " " not in name:                       # same never-a-real-flag rule as below
                    out[name] = value
            elif name in _VALUE_FLAGS:                    # known value-taking flag: consume unconditionally
                if i + 1 < len(argv):
                    out[name] = argv[i + 1]
                    i += 2
                    continue
                out[name] = "true"                        # nothing left to consume
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]
                i += 2
                continue
            elif " " not in name:
                out[name] = "true"
            # else: whitespace in `name` -- never a real flag; drop rather than keep a nonsense key
        i += 1
    return out


def _load_or_report(sdlc_dir, goal):
    """-> (slices, error). A malformed manifest is reported at the CLI edge, never as a traceback."""
    try:
        return load(sdlc_dir, goal), None
    except ValueError as exc:
        return None, str(exc)


USAGE = ("usage: slices.py plan <sdlc_dir> <goal> [--max N] [--host claude|codex] "
         "[--goal-worktree PATH] [--tier haiku|sonnet|opus|fable|off] | frontier <sdlc_dir> <goal> | "
         "check <sdlc_dir> <goal>")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 4 and argv[1] in ("plan", "frontier", "check"):
        verb, sdlc_dir, goal = argv[1], argv[2], argv[3]
        slices, error = _load_or_report(sdlc_dir, goal)
        if error:
            print(f"slices: {error}", file=sys.stderr)
            return 1
        problems = validate(slices)

        if verb == "check":
            for problem in problems:
                print(f"slices: {problem}", file=sys.stderr)
            if problems:
                return 1
            print(f"ok — {len(slices)} slice(s), no problems")
            return 0

        if verb == "frontier":
            for s in frontier(slices):
                print(s["id"])
            return 0

        if problems:
            print(f"slices: {manifest_path(sdlc_dir, goal)} cannot be scheduled:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        if not slices:
            print(f"no slice manifest at {manifest_path(sdlc_dir, goal)} — "
                  "the goal runs as one unit, exactly as before")
            return 0
        config = _config(sdlc_dir)
        enabled, cap = parallel(config)
        flags = _flags(argv[4:])
        unknown_flags = sorted(set(flags) - _KNOWN_FLAGS)
        if unknown_flags:
            print(f"slices: unknown option(s): {', '.join('--' + flag for flag in unknown_flags)}",
                  file=sys.stderr)
            return 2
        host = flags.get("host", "claude")
        if host not in ("claude", "codex"):
            print("slices: --host must be claude or codex", file=sys.stderr)
            return 2
        tier = flags.get("tier")
        model_pair = None
        if host == "codex":
            try:
                # Validate before advisory events or a command are emitted. A corrupted override
                # must never become an executable Codex dispatch.
                model_pair = resolve_codex_model(tier, sdlc_dir)
            except ValueError as exc:
                print(f"slices: Codex --tier could not resolve: {exc}", file=sys.stderr)
                return 2
        if host == "codex" and not _load("work").enabled(config):
            print("slices: Codex slices require work.enabled and a goal worktree; "
                  "run this goal as one unit", file=sys.stderr)
            return 2
        goal_worktree = flags.get("goal-worktree")
        if goal_worktree and not pathlib.Path(goal_worktree).is_dir():
            print("slices: --goal-worktree must name an existing directory", file=sys.stderr)
            return 2
        if host == "codex" and goal_worktree:
            # A plausible directory is not enough: Codex must stay in THIS goal's worktree.
            # work.root is the existing work-record/derived-path resolver used by verification.
            work = _load("work")
            expected = pathlib.Path(work.root(sdlc_dir, goal, config=config)).resolve()
            given = pathlib.Path(goal_worktree).resolve()
            if given != expected or (work.enabled(config) and expected == work.project_root(sdlc_dir)):
                print("slices: --goal-worktree must name this goal's existing worktree "
                      "(the same root work.py verify uses)", file=sys.stderr)
                return 2
        if flags.get("max", "").isdigit():
            cap = max(1, int(flags["max"]))
        if not enabled:
            print('slices: parallelism is off (config: "parallel": {"enabled": true}) — '
                  "showing the plan anyway; run the waves in order until you turn it on",
                  file=sys.stderr)
        plan = schedule(slices, cap)
        # issue #1013: this call site IS unconditional once a slice manifest exists -- gated on
        # .sdlc/plans/<goal-stem>.slices.json's EXISTENCE (see the `if not slices:` guard above), NOT on
        # `parallel.enabled` (that flag only gates whether waves run CONCURRENTLY, checked separately, a few
        # lines below this loop). No plan has ever declared slices in this repo, which is the real, sole
        # reason `slice` events are zero here. See the research dossier for #1013 (retro decision emitters) §5.
        # Site g (#139): one `slice` event per planned slice, across every wave. Each field
        # computation (`s["id"]`, `dispatch(s)`, `len(s["files"])`) runs in THIS frame, not inside
        # `safe_append`'s own try/except — Python evaluates call arguments before the call happens
        # — so each slice gets its own guard: one malformed manifest entry must never break the
        # rest of the plan, matching `ledger.read_all`'s own "one bad line is skipped" convention.
        for wave_index, wave in enumerate(plan, start=1):
            for s in wave:
                try:
                    ledger.safe_append(sdlc_dir, "slice", goal, config=config, stream=ledger.EVENTS,
                                       slice=s["id"], wave=wave_index, mode=dispatch(s),
                                       files_declared=len(s["files"]))
                except Exception:    # noqa: BLE001 - fail-open; a journal write must never break planning
                    continue
        print(render(plan, goal, host=host, goal_worktree=goal_worktree,
                     manifest=manifest_path(sdlc_dir, goal), tier=tier, sdlc_dir=sdlc_dir,
                     model_pair=model_pair), end="")
        return 0

    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
