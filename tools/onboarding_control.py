#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The end-to-end onboarding control (#237): fresh repository -> /agrim-init -> one goal `done`,
in local-goals mode and in github mode, following the README Quickstart TEXT.

WHY. AGENTS.md: "Run the control, or the check is decoration." The Quickstart once shipped in a
state that could not reach `done` (verify.enforce on, verify.command empty) and nothing caught it,
because nothing ran the documented gesture end to end. This script runs it.

WHAT IT READS FROM THE README, AND WHY THAT MATTERS. The commands are PARSED from the README's
`## Quickstart` section and the verify gestures under "What /agrim-init will ask you" -- the init
script path (the Codex/Cursor script-form line), the `/agrim-init` flags (the Claude Code line),
the `verify_detect.py confirm .sdlc <n> <id>` gesture, and the `claude plugin ...` / `codex plugin
...` install lines. A README that drifts from the shipped scripts (a renamed script, a renamed
verb, a dropped line) therefore turns this control red; a README the control does not read could
drift silently. Everything else comes from what /agrim-init PRINTS: the `[ask]` lines (answered
with the flag each names), the verify candidate number and id, and the `Next:` line's
`loop.py next` command.

WHAT IT DOES NOT DO (see docs/onboarding-control.md for the owner runbook of each):
  * no model session. `/agrim-loop` is a model turn; this control drives the SAME scripts the
    agrim-loop skill tells the agent to run (`loop.py start/next/agent-start`, `work.py`,
    `phase_report.py`, `loop.py verify`, `loop.py record`), with the "work" itself scripted. Token
    cost is therefore N/A: `phase_report.py end` runs and prints its honest `cost: unavailable`
    line, which is recorded as-is.
  * no real GitHub. github mode runs against the stateful fake `gh` from
    tests/test_public_bootstrap_control.py (extracted by `ast`, one source for both) and a local
    bare `origin`. Every gh call is recorded; a call the fake does not model fails the run.
  * no write outside its own temp directory, except the OPTIONAL `--install` step, which runs the
    README's `claude plugin` / `codex plugin` lines into an ISOLATED profile (HOME,
    CLAUDE_CONFIG_DIR, CODEX_HOME all pointed inside the temp dir) and records a hash of the real
    profile's plugin surface before and after, so "untouched" is measured, not assumed.

USAGE
    python3 tools/onboarding_control.py [--mode local|github|both] [--sigma DIR] [--readme FILE]
                                        [--install none|claude|codex|all] [--from-install]
                                        [--json FILE] [--workdir DIR] [--keep]

EXIT: 0 = every requested mode reached `done` and every assertion held; 1 = a step or an
assertion failed (the JSON names the first failing step); 2 = bad arguments or a missing
precondition (`git` / `make` not found) -- a precondition gap is never reported as green.

Stdlib only. POSIX only (a `make` test target and a `#!python` fake gh); on Windows it exits 2.
"""
import ast
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_GH_SOURCE = pathlib.Path("tests") / "test_public_bootstrap_control.py"
FAKE_REPO = "acme/onboarding-demo"
SCHEMA = "sigma.onboarding-control/1"
#: The one trivial goal the control files, and the file its "work" writes. The Makefile's test
#: target checks that file, so the confirmed verify command genuinely fails before the work and
#: passes after it -- evidence that depends on the change, not a `true`.
GOAL_TITLE = "Add hello.txt"
WORK_FILE = "hello.txt"
MAKEFILE = "test:\n\ttest -s hello.txt\n"
#: The work each known goal takes. Local mode with --demo queues the demo goal too (its own
#: frontmatter verify_command); a goal the control does not know is a red, never guessed at.
DEMO_FILE = "sigma-demo.md"


class Red(Exception):
    """A failed step: the control is red at `step`."""

    def __init__(self, step, detail):
        super().__init__(f"{step}: {detail}")
        self.step, self.detail = step, detail


# ------------------------------------------------------------------------------ README parsing

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)


def _section(text, heading):
    m = re.search(r"(?m)^## " + re.escape(heading) + r"\s*$", text)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(r"(?m)^## ", rest)
    return rest[:end.start()] if end else rest


def _lines(block):
    out = []
    for raw in block.splitlines():
        line = raw.split("  #", 1)[0].strip()          # a trailing `# comment` is prose
        if line and not line.startswith("#"):
            out.append(line)
    return out


def parse_quickstart(text, sigma=ROOT):
    """-> dict of the gestures the control runs, or raises Red("readme", ...) naming what is
    missing. Pure over README text (plus existence checks against `sigma`), so a drift control can
    feed it a doctored copy and run the very code the real run does."""
    qs = _section(text, "Quickstart")
    if qs is None:
        raise Red("readme", "no `## Quickstart` section")
    lines = [l for block in _FENCE.findall(qs) for l in _lines(block)]
    out = {"claude_install": [l for l in lines if l.startswith("claude plugin ")],
           "codex_install": [l for l in lines if l.startswith("codex plugin ")]}
    # The first-run pair lives in the `### Claude Code` subsection; a later `/agrim-init` (the
    # "Adopting into an existing repo" line) must not stand in for it if it drifts.
    m = re.search(r"(?ms)^### Claude Code\s*$(.*?)(?=^### |\Z)", qs)
    first = [l for block in _FENCE.findall(m.group(1) if m else "") for l in _lines(block)]
    init = [l for l in first if l.startswith("/agrim-init")]
    if not init:
        raise Red("readme", "the Quickstart's `### Claude Code` block has no `/agrim-init` line")
    out["init_flags"] = init[0].split()[1:]
    if not any(l.split()[0] == "/agrim-loop" for l in first):
        raise Red("readme", "the Quickstart's `### Claude Code` block has no `/agrim-loop` line")
    scripts = [re.search(r"(skills/agrim-init/scripts/[\w.-]+\.py)", l) for l in lines
               if "init_flow" in l or "agrim-init/scripts/" in l]
    scripts = [m.group(1) for m in scripts if m]
    if not scripts:
        raise Red("readme", "the Quickstart names no skills/agrim-init/scripts/*.py init script")
    out["init_script"] = scripts[0]
    for key in ("claude_install",):
        if not any("marketplace add <SIGMA_REPO>" in l for l in out[key]) or \
                not any(re.search(r"\binstall sigma@sigma\b", l) for l in out[key]):
            raise Red("readme", "the Quickstart's `claude plugin` lines are not "
                      "`marketplace add <SIGMA_REPO>` + `install sigma@sigma`")
    asks = text[text.find("### What `/agrim-init` will ask you"):] if \
        "### What `/agrim-init` will ask you" in text else ""
    confirm = [l for block in _FENCE.findall(asks) for l in _lines(block)
               if "verify_detect.py" in l and "<n>" in l and "<id>" in l]
    if not confirm:
        raise Red("readme", "no `verify_detect.py ... <n> <id>` confirm gesture under "
                  "\"What /agrim-init will ask you\"")
    out["verify_confirm"] = confirm[0]
    for rel in [out["init_script"]] + [t for t in shlex.split(confirm[0]) if t.endswith(".py")]:
        if not (pathlib.Path(sigma) / rel).is_file():
            raise Red("readme", f"the README names {rel}, which {sigma} does not ship")
    return out


# ------------------------------------------------------------------------------ plumbing

class Run:
    """One mode's run: an ordered step log with durations, and the assertions made."""

    def __init__(self, mode):
        self.mode, self.steps, self.assertions, self.failed = mode, [], [], None
        self.t0 = time.monotonic()

    def step(self, name, argv, cwd, env, ok_rc=(0,), stdin=None):
        t = time.monotonic()
        proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), env=env, input=stdin,
                              capture_output=True, text=True)
        secs = round(time.monotonic() - t, 3)
        rec = {"step": name, "argv": [str(a) for a in argv], "rc": proc.returncode,
               "seconds": secs, "stdout_tail": proc.stdout[-600:], "stderr_tail": proc.stderr[-600:]}
        self.steps.append(rec)
        if ok_rc is not None and proc.returncode not in ok_rc:
            raise Red(name, f"exit {proc.returncode}: {(proc.stderr or proc.stdout).strip()[-400:]}")
        return proc

    def timed(self, name, fn):
        t = time.monotonic()
        try:
            return fn()
        finally:
            self.steps.append({"step": name, "seconds": round(time.monotonic() - t, 3)})

    def result(self, extra=None):
        out = {"mode": self.mode, "ok": self.failed is None and all(a["ok"] for a in self.assertions),
               "failed_step": self.failed, "seconds": round(time.monotonic() - self.t0, 3),
               "steps": self.steps, "assertions": self.assertions}
        if extra:
            out.update(extra)
        if out["ok"] is False and out["failed_step"] is None:
            out["failed_step"] = "assert:" + next(a["name"] for a in self.assertions if not a["ok"])
        return out


def _env(root, bin_dir, extra=None):
    py_dir = str(pathlib.Path(sys.executable).parent)
    env = {"PATH": os.pathsep.join([str(bin_dir), py_dir, "/usr/local/bin", "/usr/bin", "/bin"]),
           "HOME": str(root / "home"), "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull, "PYTHONDONTWRITEBYTECODE": "1",
           "GIT_AUTHOR_NAME": "Onboarding Control", "GIT_AUTHOR_EMAIL": "control@example.com",
           "GIT_COMMITTER_NAME": "Onboarding Control", "GIT_COMMITTER_EMAIL": "control@example.com",
           "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
    env.update(extra or {})
    (root / "home").mkdir(exist_ok=True)
    return env


def _git(args, cwd, env):
    proc = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.returncode:
        raise Red("setup", f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc


def _py_argv(line, sigma, subs):
    """A README/printed `python3 <script> ...` line -> argv: the interpreter is THIS one (so a
    3.10 CI leg tests 3.10), a relative skills/ path is resolved against the Sigma directory
    ("Paths above are relative to the Sigma plugin directory"), and each `<placeholder>` in
    `subs` is replaced. Nothing is passed through a shell."""
    toks = shlex.split(line)
    if toks and re.fullmatch(r"python3?|py", toks[0]):
        toks[0] = sys.executable
    out = []
    for t in toks:
        for k, v in subs.items():
            t = t.replace(k, v)
        if t.startswith("skills/") and t.endswith(".py"):
            t = str(pathlib.Path(sigma) / t)
        out.append(t)
    return out


def _frontmatter(path):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    head = text[3:].split("\n---", 1)[0] if text.startswith("---") else ""
    return dict(re.findall(r"(?m)^(\w+):\s*\"?(.*?)\"?\s*$", head))


def _asks(output):
    return sorted(set(re.findall(r"\[ask\] (\w+):", output)))


def _answer_flags(asks, mode):
    """Each `[ask]` line names the flag that answers it; the control's answers."""
    table = {"mode": ["--mode", "github" if mode == "github" else "local-goals"],
             "work": ["--local-only"] if mode == "local" else [],
             "ledger": ["--ledger", "no"], "board": ["--board", "no"]}
    flags = []
    for a in asks:
        if a in table:
            flags += table[a]
    if mode == "github" and "mode" in asks:
        flags += ["--repo", FAKE_REPO]
    return flags


def _candidate(init_out):
    """The first verify candidate init printed: (n, id). The printed confirm gesture carries both."""
    m = re.search(r"verify_detect\.py\S*\s+confirm\s+\S+\s+(\d+)\s+([0-9a-f]{12})", init_out)
    if not m:
        raise Red("verify confirm", "init printed no `verify_detect.py confirm <sdlc> <n> <id>` line")
    return m.group(1), m.group(2)


def _loop_next_line(init_out):
    m = re.search(r"Next: .*\(Codex/Cursor: (.+?)\)\.?\s*$", init_out, re.M)
    if not m:
        raise Red("init", "init printed no `Next: ... (Codex/Cursor: <loop.py next ...>)` line")
    return m.group(1)


# ------------------------------------------------------------------------------ the flow

def _init_and_verify(run, qs, sigma, repo, env, mode):
    """README flags, then the [ask] answers, then the README's confirm gesture."""
    init = [sys.executable, pathlib.Path(sigma) / qs["init_script"], "."] + qs["init_flags"]
    out = run.step("init (README flags)", init, repo, env, ok_rc=(0, 1))
    answered, flags = set(), []
    # Each run can open questions the last one could not reach (github mode's board/ledger appear
    # once the mode is answered): answer every open one with the flag it names, re-run, repeat.
    for _ in range(3):
        asks = [a for a in _asks(out.stdout) if a != "verify"]
        new = [a for a in asks if a not in answered]
        if not asks and out.returncode == 0:
            break
        if not new:
            raise Red("init", f"questions still open after answering them: {asks} "
                      f"(exit {out.returncode})")
        answered.update(new)
        flags += _answer_flags(new, mode)
        out = run.step("init (answers: " + ",".join(new) + ")", init + flags, repo, env, ok_rc=(0, 1))
    else:
        raise Red("init", "init still asking after three rounds of answers")
    if out.returncode != 0:
        raise Red("init", f"exit {out.returncode}: {out.stdout.strip()[-300:]}")
    second = out
    n, ident = _candidate(second.stdout)
    argv = _py_argv(qs["verify_confirm"], sigma, {"<n>": n, "<id>": ident})
    run.step("verify confirm (README gesture)", argv, repo, env)
    cfg = json.loads((repo / ".sdlc" / "config.json").read_text(encoding="utf-8"))
    run.assertions.append(_check("verify confirmed: enforce ON with a command",
                                 bool(cfg["verify"].get("enforce")) and bool(cfg["verify"].get("command")),
                                 cfg["verify"]))
    return second.stdout


def _check(name, ok, detail=None):
    return {"name": name, "ok": bool(ok), "detail": detail}


def check_local(obs):
    """Assertions over one local goal's observations. Pure, so each can be broken once in a test."""
    ev = obs.get("evidence") or {}
    return [
        _check("record done exited 0", obs.get("record_rc") == 0, obs.get("record_rc")),
        _check("goal frontmatter status is done", obs.get("status") == "done", obs.get("status")),
        _check("verify evidence exists and passed",
               ev.get("verify_state") == "pass" and ev.get("exit") == 0, ev.get("verify_state")),
        _check("verify evidence ran a non-empty command", bool(ev.get("command")), ev.get("command")),
        _check("the loop made no gh call in local-goals mode", obs.get("gh_calls") == [],
               obs.get("gh_calls")),
    ]


def check_github(obs):
    """#232 done-means-merged, as the fake records it. Pure, like check_local."""
    return [
        _check("the review gate ran (a sigma:block parked the merge)",
               str(obs.get("blocked_merge", "")).startswith("PARK:"), obs.get("blocked_merge")),
        _check("merge passed the review gate once approved",
               "review gate passed" in str(obs.get("merge", "")), obs.get("merge")),
        _check("record done REFUSED while the PR is open", obs.get("early_done_rc") == 4,
               obs.get("early_done_rc")),
        _check("issue still open before the PR merged", obs.get("issue_before_merge") == "open",
               obs.get("issue_before_merge")),
        _check("PR open before the human merge", obs.get("pr_before_merge") == "OPEN",
               obs.get("pr_before_merge")),
        _check("reconcile recorded done only after the merge",
               re.fullmatch(r"\d+ done \(PR #\d+ merged\)", str(obs.get("reconcile", ""))) is not None,
               obs.get("reconcile")),
        _check("issue closed after done", obs.get("issue_final") == "closed", obs.get("issue_final")),
        _check("PR merged", obs.get("pr_final") == "MERGED", obs.get("pr_final")),
        _check("the change is on origin/main", obs.get("remote_file") == "hi\n", obs.get("remote_file")),
        _check("verify evidence passed in the goal worktree",
               (obs.get("evidence") or {}).get("verify_state") == "pass",
               (obs.get("evidence") or {}).get("verify_state")),
        _check("every gh call was one the fake models", obs.get("unhandled") == "",
               obs.get("unhandled")),
    ]


def _stub_gh(bin_dir, log):
    """Local mode: a `gh` that logs and fails -- so any gh call local-goals mode makes is seen."""
    path = bin_dir / "gh"
    path.write_text(f"#!{sys.executable}\nimport json,sys\n"
                    f"open({str(log)!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\nsys.exit(1)\n",
                    encoding="utf-8")
    path.chmod(0o755)


def _gh_calls(log):
    if not log.is_file():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


def _summarise_gh(calls):
    """`gh <verb> <sub>` (or `gh api <endpoint>`, numbers folded to N) -> call count."""
    counts = {}
    for argv in calls:
        if argv[:1] == ["api"]:
            rest, i = [], 1
            while i < len(argv):
                if argv[i] in ("-X", "--method", "-f", "-F", "--jq", "-H", "--input"):
                    i += 2
                    continue
                if not argv[i].startswith("-"):
                    rest.append(argv[i])
                i += 1
            key = "api " + re.sub(r"\d+", "N", (rest or [""])[0].split("?")[0])
        else:
            key = " ".join(argv[:2])
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _local_goal_work(goal_path, repo):
    fm = _frontmatter(goal_path)
    if DEMO_FILE in fm.get("done_when", "") or DEMO_FILE in fm.get("verify_command", ""):
        (repo / DEMO_FILE).write_text("Sigma ran this goal.\n", encoding="utf-8")
        return DEMO_FILE
    if fm.get("title") == GOAL_TITLE:
        (repo / WORK_FILE).write_text("hi\n", encoding="utf-8")
        return WORK_FILE
    raise Red("work", f"the loop picked a goal the control does not know: {goal_path}")


def run_local(sigma, readme_text, root, qs=None):
    run = Run("local-goals")
    goals = []
    try:
        qs = qs or run.timed("readme parse", lambda: parse_quickstart(readme_text, sigma))
        repo, bin_dir = root / "local" / "repo", root / "local" / "bin"
        repo.mkdir(parents=True)
        bin_dir.mkdir()
        gh_log = root / "local" / "gh_calls.jsonl"
        _stub_gh(bin_dir, gh_log)
        env = _env(root / "local", bin_dir)
        _git(["init", "-q", "-b", "main"], repo, env)
        (repo / "Makefile").write_text(MAKEFILE, encoding="utf-8")
        _git(["add", "-A"], repo, env)
        _git(["commit", "-qm", "fresh repository"], repo, env)
        init_out = _init_and_verify(run, qs, sigma, repo, env, "local")
        (repo / ".sdlc" / "goals" / "0002-onboarding-hello.md").write_text(
            f'---\nid: "0002"\ntitle: {GOAL_TITLE}\nstatus: pending\n---\n\n'
            f"Create {WORK_FILE} with one line. Filed by the onboarding control.\n", encoding="utf-8")
        run.steps.append({"step": "file goal (.sdlc/goals/0002-onboarding-hello.md)", "seconds": 0})
        init_gh = len(_gh_calls(gh_log))       # init's own preflight may ask gh; the loop may not
        pid = str(os.getpid())
        loop = pathlib.Path(sigma) / "skills" / "agrim-loop" / "scripts"
        run.step("loop start", [sys.executable, loop / "loop.py", "start", ".sdlc", "--session-pid", pid],
                 repo, env)
        nxt = _py_argv(_loop_next_line(init_out), sigma, {}) + ["--session-pid", pid]
        for _ in range(5):
            goal = run.step("loop next", nxt, repo, env).stdout.strip()
            if not goal or goal == "DONE":
                break
            if not (repo / goal).is_file():
                raise Red("loop next", f"`next` printed {goal!r}, not a goal file")
            obs = _drive_local_goal(run, loop, repo, env, goal, pid)
            goal = obs["goal"]
            obs["gh_calls"] = _gh_calls(gh_log)[init_gh:]
            goals.append(obs)
            for a in check_local(obs):
                a["name"] = f"{goal}: {a['name']}"
                run.assertions.append(a)
            if obs["record_rc"] != 0:
                raise Red("record done", f"{goal}: {obs['record_err'].strip()[-300:]}")
        run.assertions.append(_check("the control's own goal reached done",
                                     any(g["work"] == WORK_FILE and g["status"] == "done" for g in goals),
                                     [(g["goal"], g["status"]) for g in goals]))
    except Red as red:
        run.failed = red.step
        run.steps.append({"step": "RED", "at": red.step, "detail": red.detail})
    return run.result({"goals": goals, "gh_calls": _summarise_gh(_gh_calls(root / "local" / "gh_calls.jsonl"))})


def _drive_local_goal(run, loop, repo, env, goal, pid):
    """The agrim-loop skill's per-goal gestures with work.enabled off (--local-only)."""
    py = sys.executable
    name = pathlib.Path(goal).name
    run.step(f"agent-start {name}", [py, loop / "loop.py", "agent-start", ".sdlc", goal, "--pid", pid],
             repo, env)
    run.step(f"phase_report start {name}", [py, loop / "phase_report.py", "start", ".sdlc", goal,
                                            "implement", "--model", "haiku", "--pid", pid], repo, env)
    work = _local_goal_work(repo / goal, repo)
    end = run.step(f"phase_report end {name}", [py, loop / "phase_report.py", "end", ".sdlc", goal,
                                                "implement", "--pid", pid], repo, env)
    # loop.py verify's exit is recorded, not fatal: `record done` is the gate that decides, and the
    # control asserts at the gate -- so the empty-command trap reads red AT `record done`.
    ver = run.step(f"loop verify {name}", [py, loop / "loop.py", "verify", ".sdlc", goal], repo, env,
                   ok_rc=None)
    rec = run.step(f"record done {name}", [py, loop / "loop.py", "record", ".sdlc", goal, "done"],
                   repo, env, ok_rc=None)
    ev_path = repo / ".sdlc" / "state" / "verify" / (pathlib.Path(goal).stem + ".json")
    evidence = json.loads(ev_path.read_text(encoding="utf-8")) if ev_path.is_file() else None
    return {"goal": pathlib.Path(goal).name, "work": work, "verify_rc": ver.returncode, "record_rc": rec.returncode,
            "record_err": rec.stderr + rec.stdout, "status": _frontmatter(repo / goal).get("status"),
            "evidence": {k: (evidence or {}).get(k) for k in ("command", "exit", "verify_state")}
            if evidence else None,
            "cost_line": next((l for l in end.stdout.splitlines() if "cost" in l), "")}


# ------------------------------------------------------------------------------ github mode

def _fake_gh_body(sigma):
    """The fake gh from tests/test_public_bootstrap_control.py, extracted by `ast` (never
    imported -- that module needs pytest), so the control and that test share one fake."""
    src = pathlib.Path(sigma) / FAKE_GH_SOURCE
    if not src.is_file():
        src = ROOT / FAKE_GH_SOURCE
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "_FAKE_GH_BODY" for t in node.targets):
            return ast.literal_eval(node.value)
    raise Red("setup", f"no _FAKE_GH_BODY in {src}")


def run_github(sigma, readme_text, root, qs=None):
    run = Run("github")
    obs = {}
    base = root / "github"
    state_path, log_path, unhandled = base / "gh_state.json", base / "gh_log.jsonl", base / "gh_unhandled.jsonl"
    try:
        qs = qs or run.timed("readme parse", lambda: parse_quickstart(readme_text, sigma))
        bin_dir, remote, repo = base / "bin", base / "remote.git", base / "repo"
        bin_dir.mkdir(parents=True)
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\n" + _fake_gh_body(sigma), encoding="utf-8")
        gh.chmod(0o755)
        log_path.write_text("", encoding="utf-8")
        unhandled.write_text("", encoding="utf-8")
        state_path.write_text(json.dumps({
            "repo": FAKE_REPO, "remote_git_dir": str(remote), "labels": {}, "label_seq": 0,
            "issues": {}, "issue_seq": 1, "prs": {}, "pr_seq": 100, "projects": [],
            "login": "onboarding-bot", "default_branch": "main", "allow_auto_merge": True,
            "viewer_permission": "ADMIN", "refuse_label_create": False}), encoding="utf-8")
        env = _env(base, bin_dir, {"FAKE_GH_STATE": str(state_path), "FAKE_GH_LOG": str(log_path),
                                   "FAKE_GH_UNHANDLED": str(unhandled)})
        _git(["init", "-q", "--bare", "-b", "main", str(remote)], base, env)
        _git(["clone", "-q", str(remote), str(repo)], base, env)
        (repo / "Makefile").write_text(MAKEFILE, encoding="utf-8")
        _git(["add", "-A"], repo, env)
        _git(["commit", "-qm", "fresh repository"], repo, env)
        _git(["push", "-q", "-u", "origin", "main"], repo, env)
        init_out = _init_and_verify(run, qs, sigma, repo, env, "github")
        # init git-ignores its runtime dirs and scaffolds .sdlc/: commit + push, so the goal's
        # worktree (cut from origin/main) has the config and Makefile -- what an adopter does.
        _git(["add", "-A"], repo, env)
        _git(["commit", "-qm", "adopt sigma"], repo, env)
        _git(["push", "-q", "origin", "main"], repo, env)

        def gh_(args, name):
            return run.step(name, [gh] + args, repo, env)

        def state():
            return json.loads(state_path.read_text(encoding="utf-8"))

        gh_(["issue", "create", "--repo", FAKE_REPO, "--label", "sdlc:goal,priority:P2", "--assignee",
             "@me", "--title", GOAL_TITLE, "--body", f"Create {WORK_FILE} with one line."],
            "file goal (gh issue create, labelled sdlc:goal, assigned @me)")
        pid = str(os.getpid())
        loop = pathlib.Path(sigma) / "skills" / "agrim-loop" / "scripts"
        py = sys.executable
        run.step("loop start", [py, loop / "loop.py", "start", ".sdlc", "--session-pid", pid], repo, env)
        nxt = _py_argv(_loop_next_line(init_out), sigma, {}) + ["--session-pid", pid]
        goal = run.step("loop next", nxt, repo, env).stdout.strip()
        if goal != "1":
            raise Red("loop next", f"expected issue 1, got {goal!r}")
        run.step("agent-start", [py, loop / "loop.py", "agent-start", ".sdlc", goal, "--pid", pid], repo, env)
        run.step("work start", [py, loop / "work.py", "start", ".sdlc", goal, "--session-pid", pid], repo, env)
        run.step("phase_report start", [py, loop / "phase_report.py", "start", ".sdlc", goal, "implement",
                                        "--model", "haiku", "--pid", pid], repo, env)
        (repo / ".sdlc" / "work" / goal / WORK_FILE).write_text("hi\n", encoding="utf-8")
        end = run.step("phase_report end", [py, loop / "phase_report.py", "end", ".sdlc", goal,
                                            "implement", "--pid", pid], repo, env)
        obs["cost_line"] = next((l for l in end.stdout.splitlines() if "cost" in l), "")
        run.step("loop verify", [py, loop / "loop.py", "verify", ".sdlc", goal], repo, env, ok_rc=None)
        ev = repo / ".sdlc" / "state" / "verify" / f"{goal}.json"
        obs["evidence"] = json.loads(ev.read_text(encoding="utf-8")) if ev.is_file() else None
        run.step("work commit", [py, loop / "work.py", "commit", ".sdlc", goal, "--message",
                                 f"sdlc: {GOAL_TITLE.lower()}"], repo, env)
        run.step("work pr", [py, loop / "work.py", "pr", ".sdlc", goal], repo, env)
        pr = next(iter(state()["prs"]), None)
        if pr is None:
            raise Red("work pr", "no PR was opened")
        gh_(["pr", "comment", pr, "--repo", FAKE_REPO, "--body", "sigma:block no test for this"],
            "reviewer comments sigma:block")
        obs["blocked_merge"] = run.step("work merge (blocked)", [py, loop / "work.py", "merge", ".sdlc",
                                                                 goal], repo, env).stdout.strip()
        gh_(["pr", "comment", pr, "--repo", FAKE_REPO, "--body", "sigma:approve"],
            "reviewer comments sigma:approve")
        obs["merge"] = run.step("work merge", [py, loop / "work.py", "merge", ".sdlc", goal],
                                repo, env).stdout.strip()
        obs["early_done_rc"] = run.step("record done (PR still open)", [py, loop / "loop.py", "record",
                                        ".sdlc", goal, "done"], repo, env, ok_rc=None).returncode
        run.step("record review", [py, loop / "loop.py", "record", ".sdlc", goal, "review"], repo, env)
        obs["issue_before_merge"] = state()["issues"][goal]["state"]
        obs["pr_before_merge"] = state()["prs"][pr]["state"]
        gh_(["pr", "merge", pr, "--repo", FAKE_REPO, "--squash"], "human merges the PR")
        obs["reconcile"] = run.step("loop reconcile-merges", [py, loop / "loop.py", "reconcile-merges",
                                                              ".sdlc"], repo, env).stdout.strip()
        obs["issue_final"] = state()["issues"][goal]["state"]
        obs["pr_final"] = state()["prs"][pr]["state"]
        shown = subprocess.run(["git", "--git-dir", str(remote), "show", f"main:{WORK_FILE}"],
                               capture_output=True, text=True)
        obs["remote_file"] = shown.stdout if shown.returncode == 0 else None
    except Red as red:
        run.failed = red.step
        run.steps.append({"step": "RED", "at": red.step, "detail": red.detail})
    obs["unhandled"] = unhandled.read_text(encoding="utf-8") if unhandled.is_file() else ""
    if run.failed is None:
        run.assertions += check_github(obs)
    calls = _gh_calls(log_path)
    ev = obs.get("evidence") or {}
    obs["evidence"] = {k: ev.get(k) for k in ("command", "exit", "verify_state")} if ev else None
    return run.result({"observations": obs, "gh_calls": _summarise_gh(calls), "gh_call_count": len(calls)})


# ------------------------------------------------------------------------------ host install

def _surface_hash(home):
    """A hash of the real profiles' plugin surface: every file's relative path, size and mtime
    under ~/.claude/plugins, ~/.claude/settings.json and ~/.codex/{config.toml,plugins}. Session
    logs elsewhere under ~/.claude churn constantly and are not what a plugin install writes."""
    h = hashlib.sha256()
    for rel in (".claude/plugins", ".claude/settings.json", ".codex/config.toml", ".codex/plugins"):
        p = pathlib.Path(home) / rel
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.is_dir() else []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f"{f.relative_to(home)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def _codex_bin():
    found = shutil.which("codex")
    bundled = pathlib.Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    return found or (str(bundled) if bundled.is_file() else None)


def host_install(qs, sigma, root, which):
    """The README's CLI install lines, run into an isolated profile. Read-only detection first:
    a host CLI that is absent is `skipped`, never green."""
    real_home = os.path.expanduser("~")
    before = _surface_hash(real_home)
    out = {"real_profile_surface_hash_before": before}
    iso = root / "iso"
    for host, lines, binary in (("claude", qs["claude_install"], shutil.which("claude")),
                                ("codex", qs["codex_install"], _codex_bin())):
        if which not in (host, "all"):
            continue
        if not binary:
            out[host] = {"status": "skipped", "why": f"no `{host}` CLI on this machine"}
            continue
        home, cfg = iso / host / "home", iso / host / "cfg"
        home.mkdir(parents=True)
        cfg.mkdir(parents=True)
        env = dict(os.environ, HOME=str(home), CLAUDE_CONFIG_DIR=str(cfg), CODEX_HOME=str(cfg))
        rec = {"binary": binary, "steps": []}
        for line in lines:
            argv = [binary] + [t.replace("<SIGMA_REPO>", str(sigma)) for t in shlex.split(line)[1:]]
            t = time.monotonic()
            proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=300)
            rec["steps"].append({"line": line, "rc": proc.returncode,
                                 "seconds": round(time.monotonic() - t, 3),
                                 "tail": (proc.stdout + proc.stderr).strip()[-300:]})
        # <plugin root>/skills/agrim-init/scripts/init_flow.py, in the host's plugin cache
        installed = sorted(str(p.parents[3]) for p in cfg.rglob("init_flow.py")
                           if "cache" in p.parts)
        rec["installed_path"] = installed[0] if installed else None
        rec["status"] = "ok" if installed and all(s["rc"] == 0 for s in rec["steps"]) else "red"
        out[host] = rec
    out["real_profile_surface_hash_after"] = _surface_hash(real_home)
    out["real_profile_untouched"] = out["real_profile_surface_hash_after"] == before
    return out


# ------------------------------------------------------------------------------ main

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--mode", choices=("local", "github", "both"), default="both")
    ap.add_argument("--sigma", default=str(ROOT), help="the Sigma checkout to run (default: this one)")
    ap.add_argument("--readme", help="the README to follow (default: <sigma>/README.md)")
    ap.add_argument("--install", choices=("none", "claude", "codex", "all"), default="none")
    ap.add_argument("--from-install", action="store_true",
                    help="run the scripts from the isolated Claude Code install, not --sigma")
    ap.add_argument("--json", help="write the result JSON here (default: stdout only)")
    ap.add_argument("--workdir", help="parent for the temp directory")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = ap.parse_args(argv[1:])
    if os.name == "nt":
        print("onboarding_control: POSIX only (a make test target, a #! fake gh)", file=sys.stderr)
        return 2
    for tool in ("git", "make"):
        if not shutil.which(tool):
            print(f"onboarding_control: precondition missing: `{tool}` not on PATH", file=sys.stderr)
            return 2
    sigma = pathlib.Path(args.sigma).resolve()
    readme = pathlib.Path(args.readme or sigma / "README.md")
    text = readme.read_text(encoding="utf-8")
    root = pathlib.Path(tempfile.mkdtemp(prefix="sigma-onboarding-", dir=args.workdir))
    result = {"schema": SCHEMA, "sigma": str(sigma), "readme": str(readme),
              "python": sys.version.split()[0], "platform": sys.platform,
              "tokens": "N/A: script-level control, no model session ran",
              "workdir": str(root), "modes": {}}
    t0 = time.monotonic()
    try:
        try:
            qs = parse_quickstart(text, sigma)
            result["readme_gestures"] = qs
        except Red as red:
            result["modes"]["readme"] = {"ok": False, "failed_step": red.step, "detail": red.detail}
            qs = None
        run_from = sigma
        if qs and args.install != "none":
            result["host_install"] = host_install(qs, sigma, root, args.install)
            inst = (result["host_install"].get("claude") or {}).get("installed_path")
            if args.from_install:
                if not inst:
                    result["modes"]["install"] = {"ok": False, "failed_step": "install",
                                                  "detail": "--from-install but no Claude Code install"}
                    qs = None
                else:
                    run_from = pathlib.Path(inst)
        result["scripts_from"] = str(run_from)
        if qs:
            if args.mode in ("local", "both"):
                result["modes"]["local"] = run_local(run_from, text, root, qs)
            if args.mode in ("github", "both"):
                result["modes"]["github"] = run_github(run_from, text, root, qs)
    finally:
        result["seconds"] = round(time.monotonic() - t0, 3)
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
    hi = result.get("host_install") or {}
    result["ok"] = bool(result["modes"]) and all(m.get("ok") for m in result["modes"].values()) and \
        all((hi.get(h) or {}).get("status", "ok") in ("ok", "skipped") for h in ("claude", "codex")) and \
        hi.get("real_profile_untouched", True)
    blob = json.dumps(result, indent=2)
    if args.json:
        pathlib.Path(args.json).write_text(blob + "\n", encoding="utf-8")
    for name, m in result["modes"].items():
        print(f"onboarding-control: {name}: {'GREEN' if m.get('ok') else 'RED at ' + str(m.get('failed_step'))}"
              f" ({m.get('seconds', 0)}s)")
    for h in ("claude", "codex"):
        if h in hi:
            print(f"onboarding-control: install {h}: {hi[h].get('status')}")
    if hi:
        print(f"onboarding-control: real profile untouched: {hi.get('real_profile_untouched')}")
    if not args.json:
        print(blob)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
