"""Bidirectional pipeline report card (zero-dep, deterministic, $0).

A project may declare its delivery pipeline in `.sdlc/pipeline.json` — ordered
stages, the artifacts each produces, and the checks that hold on each stage:

    {"stages": [
       {"name": "extract",   "produces": ["data/raw/*.json"],
        "checks": {"forward": [{"name": "raw is valid", "run": "sh checks/raw.sh"}]}},
       {"name": "transform", "produces": ["data/out/*.json"],
        "checks": {"forward": [...],
                   "reverse": [{"name": "every output row traces to a raw row",
                                "run": "sh checks/trace.sh"}]}}]}

Two directions per stage, one card for the whole pipeline:
- FORWARD  = survivorship — the stage produced what it promised and its forward
  checks hold ("nothing dropped").
- REVERSE  = provenance — the stage's declared reverse checks hold against its
  UPSTREAM stage ("nothing invented; upstream honored"). Reverse findings
  localize a fault to the edge between two stages instead of a downstream symptom.

Honesty rule: a lane with NO declared instrument reads ABSENT — never PASS.
Check contract: a check is any command; exit 0 = PASS, exit 2 = WARN, any other
exit = FAIL. Deterministic commands only — the card itself never calls a network
or an LLM, and it NEVER gates the run it examines (it reports; the loop decides).

    python3 pipeline.py card .sdlc [--json out.json] [--compare prior.json]

`--compare` diffs a prior card signal-by-signal: regressed / improved /
still-failing (still-failing across runs is the recurrence signal — systemic,
not incidental; feed it to the backlog, not to a one-off fix).
"""
import glob, importlib.util, json, os, pathlib, subprocess, sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


ledger = _load("ledger")            # team record (config-gated, default OFF; every call is fail-open)
shell_policy = _load("shell_policy")

PASS, WARN, FAIL, ABSENT = "PASS", "WARN", "FAIL", "ABSENT"
#: The source of contract/vocabulary.json's "severity_order": tools/generate_vocabulary.py
#: generates that key from this constant (#2584), and
#: tests/test_pipeline.py::test_severity_order_matches_the_contract pins the file to it (#298).
_ORDER = {PASS: 0, ABSENT: 1, WARN: 2, FAIL: 3}
_CHECK_TIMEOUT_SECS = 300           # one hung check must not hang the card


def load_pipeline(sdlc_dir):
    p = pathlib.Path(sdlc_dir) / "pipeline.json"
    if not p.exists():
        return None
    try:
        spec = json.loads(p.read_text())
    except ValueError:          # invalid JSON (json.JSONDecodeError is a ValueError) -> no pipeline
        return None
    # A top-level JSON array/scalar has no .get() at all; a dict missing/misshaping "stages" is the
    # same "not really declared" case sibling readers already treat as absent (discover(), etc).
    return spec if isinstance(spec, dict) and isinstance(spec.get("stages"), list) else None


def _run_check(check, cwd):
    """(status, detail) for one declared check command."""
    if not shell_policy.repository_shell_commands_allowed(cwd):
        return FAIL, shell_policy.refusal_message()
    try:
        proc = subprocess.run(check.get("run", ""), shell=True, cwd=cwd,
                              capture_output=True, text=True, timeout=_CHECK_TIMEOUT_SECS)
    except subprocess.TimeoutExpired:
        return FAIL, f"timed out after {_CHECK_TIMEOUT_SECS}s"
    status = PASS if proc.returncode == 0 else (WARN if proc.returncode == 2 else FAIL)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    return status, (tail[-1][:200] if tail else f"exit {proc.returncode}")


def build_card(sdlc_dir, repo_root=None):
    """The card as plain data. repo_root is where produces-globs and checks resolve
    (defaults to the parent of .sdlc — the project root)."""
    root = str(repo_root or pathlib.Path(sdlc_dir).resolve().parent)
    spec = load_pipeline(sdlc_dir)
    if spec is None:
        return None
    stages = []
    for i, stage in enumerate(spec["stages"]):
        signals = []
        produces = stage.get("produces") or []
        if produces:
            missing = [g for g in produces if not glob.glob(str(pathlib.Path(root) / g))]
            signals.append({"direction": "forward", "name": "declared artifacts present",
                            "status": FAIL if missing else PASS,
                            "detail": f"missing: {missing}" if missing else f"{len(produces)} glob(s) satisfied"})
        checks = stage.get("checks") or {}
        for direction in ("forward", "reverse"):
            declared = checks.get(direction) or []
            for check in declared:
                status, detail = _run_check(check, root)
                signals.append({"direction": direction, "name": check.get("name", check.get("run", "check")),
                                "status": status, "detail": detail})
            if not declared and not (direction == "forward" and produces):
                # Honest absence: an uninstrumented lane must never read as green.
                # (Stage 0 has no upstream, so its reverse lane is legitimately n/a.)
                if direction == "reverse" and i == 0:
                    continue
                signals.append({"direction": direction, "name": f"{direction} instrumentation",
                                "status": ABSENT, "detail": "no declared checks for this lane"})
        stages.append({"stage": stage.get("name", f"stage-{i}"), "signals": signals})
    return {"pipeline": spec.get("name", "pipeline"), "stages": stages,
            "gating": "none — diagnostic report; never gates the run it examines",
            "verdict": _verdict(stages)}


def _verdict(stages):
    failing = [s["stage"] for s in stages
               if any(x["status"] == FAIL for x in s["signals"])]
    blocked = [{"stage": s["stage"], "direction": x["direction"], "reason": x["detail"]}
               for s in stages for x in s["signals"] if x["status"] == ABSENT]
    actions = ([{"action": "declare_checks", "stage": b["stage"], "direction": b["direction"]}
                for b in blocked]
               + [{"action": "fix_stage", "stage": name} for name in failing])
    worst = PASS
    for s in stages:
        for x in s["signals"]:
            if _ORDER[x["status"]] > _ORDER[worst]:
                worst = x["status"]
    return {"overall": worst,
            "done_when": "no FAIL signals and no ABSENT lanes across all stages",
            "clean": worst in (PASS, WARN) and not blocked,
            "failing_stages": failing, "blocked_lanes": blocked, "next_actions": actions}


def compare_cards(prior, current):
    """Signal-level diff of two card payloads: regressed / improved / still_failing.
    still_failing (FAIL in both) is the recurrence signal — a systemic issue, not an
    incident. Measured in cards, not wall-clock."""
    def index(card):
        return {(s["stage"], x["direction"], x["name"]): x["status"]
                for s in card.get("stages", []) for x in s.get("signals", [])}
    prior_ix, current_ix = index(prior), index(current)
    delta = {"regressed": [], "improved": [], "still_failing": []}
    for key, now in sorted(current_ix.items()):
        before = prior_ix.get(key)
        if before is None:
            continue                       # newly-instrumented lane: no epoch verdict yet
        row = {"stage": key[0], "direction": key[1], "signal": key[2],
               "before": before, "now": now}
        if now == FAIL and before == FAIL:
            delta["still_failing"].append(row)
        elif _ORDER.get(now, 1) > _ORDER.get(before, 1):
            delta["regressed"].append(row)
        elif _ORDER.get(now, 1) < _ORDER.get(before, 1):
            delta["improved"].append(row)
    delta["recurrence_count"] = len(delta["still_failing"])
    return delta


def render(card, delta=None):
    icon = {PASS: "+", WARN: "!", FAIL: "x", ABSENT: "·"}
    lines = [f"# Pipeline report card — {card['pipeline']}", card["gating"], ""]
    for s in card["stages"]:
        worst = PASS
        for x in s["signals"]:
            if _ORDER[x["status"]] > _ORDER[worst]:
                worst = x["status"]
        lines.append(f"## {s['stage']}  [{worst}]")
        for x in s["signals"]:
            lines.append(f"  {icon[x['status']]} {x['direction']:<7} {x['name']}: "
                         f"{x['status']} — {x['detail']}")
        lines.append("")
    v = card["verdict"]
    lines.append(f"verdict: {v['overall']} · failing={v['failing_stages'] or 'none'} · "
                 f"blocked lanes={len(v['blocked_lanes'])}")
    if delta is not None:
        lines.append(f"delta: regressed={len(delta['regressed'])} improved={len(delta['improved'])} "
                     f"still-failing (recurrence)={delta['recurrence_count']}")
        for row in delta["regressed"]:
            lines.append(f"  REGRESSED [{row['stage']}/{row['direction']}] {row['signal']}: "
                         f"{row['before']} -> {row['now']}")
        for row in delta["still_failing"]:
            lines.append(f"  STILL FAILING [{row['stage']}/{row['direction']}] {row['signal']}")
    return "\n".join(lines)


def propose_goals(sdlc_dir, card):
    """The feedback circle: turn the card's FAILING signals into `proposed` goal files a
    human can groom. Proposing is safe (it only writes files nobody auto-runs — discovery
    skips `proposed` until a human edits it to `pending`); each proposal auto-wires
    `verify_command` to the failing check, so the fixed goal is machine-verifiable.
    Dedup: one deterministic id per (stage, direction, signal); an existing file with
    that id — whatever its status — is never overwritten. Returns the created paths."""
    import hashlib
    goals_dir = pathlib.Path(sdlc_dir) / "goals"
    goals_dir.mkdir(parents=True, exist_ok=True)
    check_cmds = {}
    spec = load_pipeline(sdlc_dir) or {"stages": []}
    for stage in spec["stages"]:
        for direction in ("forward", "reverse"):
            for check in (stage.get("checks") or {}).get(direction) or []:
                check_cmds[(stage.get("name"), direction, check.get("name"))] = check.get("run", "")
    created = []
    for s in card.get("stages", []):
        for x in s.get("signals", []):
            if x["status"] != FAIL:
                continue
            key = f"{s['stage']}/{x['direction']}/{x['name']}"
            gid = "auto-" + hashlib.sha256(key.encode()).hexdigest()[:10]
            path = goals_dir / f"{gid}.md"
            if path.exists():
                continue        # recurrence rides --compare's still_failing, not duplicate files
            cmd = check_cmds.get((s["stage"], x["direction"], x["name"]), "")
            fm = [f"id: {gid}", f"title: fix {key}", "status: proposed", "source: detector",
                  f"done_when: the '{x['name']}' check passes for stage '{s['stage']}'"]
            if cmd:
                fm.append(f"verify_command: {cmd}")
            path.write_text("---\n" + "\n".join(fm) + "\n---\n"
                            f"Detected by the pipeline report card: {key} is FAILING "
                            f"({x['detail']}).\nPromote to `status: pending` to let the loop work it.\n")
            created.append(str(path))
    return created


def _candidate_file(c):
    """The file a candidate is about, stripped of its trailing `:line` — shared by
    `propose_from_discovery` (dedup id) and `_emit_scan_events` (the `file` field), so the two
    never drift on what "the file" means for one candidate."""
    ev = c.get("evidence") or []
    first = ev[0] if ev else ""
    return first.rsplit(":", 1)[0] if ":" in first else first


def propose_from_discovery(sdlc_dir, candidates):
    """Turn discovery-scan candidates (tech-debt / test-gap) into `proposed` goal files — the same inert,
    human-promotes-to-`pending` contract as propose_goals (discovery.py skips `proposed`). The dedup id
    is per (category, file), NOT per marker count, so a changed count never spawns a duplicate; an
    existing file with that id — whatever its status — is never overwritten. Returns the created paths.

    Fail-open per candidate: one malformed entry (e.g. a non-dict item in a hand-mocked or corrupted
    candidates list) is skipped, never fatal to the rest — matching `ledger.read_all`'s own "one bad
    line is skipped" convention, and required for `discover()`'s own "never raises" guarantee."""
    import hashlib
    goals_dir = pathlib.Path(sdlc_dir) / "goals"
    goals_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for c in candidates or []:
        try:
            f = _candidate_file(c)
            cat = c.get("category", "")
            ev = c.get("evidence") or []
            gid = "disc-" + hashlib.sha256((cat + "/" + f).encode()).hexdigest()[:10]
            path = goals_dir / f"{gid}.md"
            if path.exists():
                continue
            title = c.get("title") or (f"address {cat} in {f}")
            locs = "".join("\n- " + e for e in ev)
            path.write_text("---\n"
                            f"id: {gid}\ntitle: {title}\nstatus: proposed\nsource: discovery\n"
                            f"done_when: the {cat} in {f} is resolved\n---\n"
                            f"Detected by the discovery scan (category: {cat}, priority: "
                            f"{c.get('priority', '')}). Locations:{locs}\n"
                            "Promote to `status: pending` to let the loop work it.\n")
            created.append(str(path))
        except Exception:            # noqa: BLE001 - one bad candidate must not blind the rest
            continue
    return created


#: Sentinel `goal` for every `scan` event: discovery runs before any goal exists, exactly like
#: decision_gate's `_NO_GOAL` for site f — a different domain, deliberately a different string, so
#: an events-stream reader filtering by `goal` can tell the two apart at a glance.
_NO_GOAL = "(discovery-scan)"


def _emit_scan_events(sdlc_dir, candidates):
    """One `scan` event per candidate. Fail-open PER CANDIDATE: `category`/`file`/`count` are
    computed as call ARGUMENTS in THIS frame, not inside `safe_append`'s own try/except (Python
    evaluates arguments before the call happens) — so a single malformed candidate (non-dict, from
    a future .sh bug or a hand-mocked fixture) must not take the whole loop down with it."""
    for c in candidates or []:
        try:
            ledger.safe_append(sdlc_dir, "scan", _NO_GOAL, stream=ledger.EVENTS,
                               category=c.get("category", ""), file=_candidate_file(c),
                               count=c.get("count", len(c.get("evidence") or [])))
        except Exception:            # noqa: BLE001 - fail-open; a journal write must never break discovery
            continue


# issue #1013: this call site IS unconditional and real -- the zero-scan-events history on this
# repo is a REACHABILITY gap (discover/discovery-scan.sh has apparently never run here with a
# candidate), not an emission gap. Distinct from `phase`'s genuine prose-dependency and from
# `decision`'s registry-authoring gate -- three different reasons behind the same zero-count
# symptom; see the research dossier for #1013 (retro decision emitters) §5.
def discover(sdlc_dir, repo_root="."):
    """Run the read-only discovery-scan collector over `repo_root` and propose goals from its candidates.
    Fail-open: a missing script / bad JSON / non-git tree yields no proposals (never raises)."""
    script = pathlib.Path(__file__).resolve().parent / "discovery-scan.sh"
    try:
        proc = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                              env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo_root)})
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return []
    candidates = data.get("candidates") or []
    _emit_scan_events(sdlc_dir, candidates)
    return propose_from_discovery(sdlc_dir, candidates)


def _load_sibling(name):
    """Load a peer script module lazily, so importing pipeline.py never drags in the mirror's deps."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).resolve().parent / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


USAGE = ("usage: pipeline.py card <sdlc_dir> [--json out.json] [--compare prior.json] | "
         "propose <sdlc_dir> | discover <sdlc_dir> [repo_root] | mirror <sdlc_dir> [--force] | "
         "crosscheck <sdlc_dir> <goal>")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[1] == "mirror":
        m = _load_sibling("mirror")
        n = m.fetch_and_write(argv[2] if len(argv) > 2 else ".sdlc", force=("--force" in argv))
        print("mirror: skipped (not github mode, fresh, or gh unavailable)" if n is None
              else f"mirror: wrote {n} issue(s)")
        return 0
    if len(argv) >= 4 and argv[1] == "crosscheck":
        bc = _load_sibling("backlog_check")
        print(json.dumps(bc.cross_check(argv[2], argv[3]), ensure_ascii=False, sort_keys=True))
        return 0
    if len(argv) >= 3 and argv[1] == "discover":
        created = discover(argv[2], argv[3] if len(argv) > 3 else ".")
        print(f"proposed {len(created)} discovery goal(s)" + ("".join("\n  " + c for c in created)))
        return 0
    if len(argv) >= 3 and argv[1] == "propose":
        card = build_card(argv[2])
        if card is None:
            print("NO-PIPELINE (declare .sdlc/pipeline.json first)", file=sys.stderr)
            return 3
        created = propose_goals(argv[2], card)
        print(f"proposed {len(created)} goal(s)" + ("".join("\n  " + c for c in created)))
        return 0
    if len(argv) >= 3 and argv[1] == "card":
        card = build_card(argv[2])
        if card is None:
            print("NO-PIPELINE (declare .sdlc/pipeline.json to use the report card)", file=sys.stderr)
            return 3
        delta = None
        if "--compare" in argv:
            prior_path = pathlib.Path(argv[argv.index("--compare") + 1])
            if not prior_path.exists():
                print(f"SKIP: {prior_path} not found", file=sys.stderr)
                return 3
            delta = compare_cards(json.loads(prior_path.read_text()), card)
            card["delta"] = delta
        print(render(card, delta))
        if "--json" in argv:
            out = pathlib.Path(argv[argv.index("--json") + 1])
            out.write_text(json.dumps(card, indent=2))
            print(f"wrote {out}")
        return 0 if not card["verdict"]["failing_stages"] else 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
