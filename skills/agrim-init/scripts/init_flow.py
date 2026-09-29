#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The ONE entry point a new user runs after installing Sigma (#236): `/agrim-init` (and its alias
`/agrim-setup`) run this, on every host.

WHY. Before #236 there were three doors: `sdlc_init.py` wrote files and asked nothing, `/agrim-setup`
adopted GitHub by model judgment around `setup.py` (and set github mode with no repository), and the
wizard was a Claude-Code-only session hook. `sdlc_init.py --github` created labels on GitHub but left
`discovery.source: local-goals`, so the loop never read the issues it had just made pickable -- and
the first thing it picked on every fresh repository was the scaffolded "Example goal".

WHAT. One flow, in this order, integrating the sibling goals rather than re-implementing them:
  1. preflight (#229, `preflight.py`)          git / remote / base / gh / auth / scopes
  2. mode                                      local-goals or github (github by default when `origin`
                                               is a GitHub owner/repo)
  3. verify command (#228, `verify_detect.py`) confirm by number+id, your own command, or decline
  4. github mode only: labels (#230, `setup.py labels`), assignee `@me`, the board OFFER (#235,
     `board_setup.py create`), and the ledger yes/no
  5. a one-screen summary and the next command

QUESTIONS ARE FLAGS, SO EVERY HOST RUNS THE SAME FLOW. Claude Code asks the user and re-runs with the
answers; Codex and Cursor relay the printed `[ask]` lines, each carrying the exact flag. An answer is
remembered in `.sdlc/state/init.json` (runtime, git-ignored), so a bare re-run repeats the same
decisions: idempotent, and the second run writes no label and changes no config key.

`--yes` ANSWERS ONLY WHAT IS SAFE TO DEFAULT: the mode (the detected one) and the ledger (no). It never
answers the board (creates external state -- #235 "never unasked"), the verify command (only the user
knows what proves their repo -- #228), or a `work.enabled` flip (#229 "nothing flips it silently").

EXIT: 0 = every attempted step passed (open `[ask]` questions allowed); 1 = a step FAILED, or preflight
found a blocking problem -- the last line is `Resume: <the exact command>`; 2 = refused before anything
was written (not a git repository, another plugin active, github mode with no repository, bad flags).

Stdlib only. Siblings are loaded by path (the `sdlc_init._preflight` idiom); the agrim-setup and
board CLIs are shelled out to, as `sdlc_init.py` already does.
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
SETUP_SCRIPT = _HERE.parent.parent / "agrim-setup" / "scripts" / "setup.py"
BOARD_SETUP = _HERE / "board_setup.py"
LOOP_SCRIPT = _HERE.parent.parent / "agrim-loop" / "scripts" / "loop.py"
SYNC_SCRIPT = _HERE.parent.parent / "agrim-loop" / "scripts" / "sync.py"
ANSWERS = pathlib.Path("state") / "init.json"
MODES = ("local-goals", "github")

USAGE = """usage: init_flow.py [target_dir] [options]
  --mode local-goals|github      the backlog: goal files in .sdlc/goals/, or GitHub issues
  --repo OWNER/NAME              github mode's repository (default: read from `origin`)
  --local-only                   work.enabled off: the loop edits this checkout (no worktree/PR)
  --work on                      work.enabled on: a worktree + branch + PR per goal
  --verify N:ID                  confirm detected candidate N (the id printed beside it)
  --verify-command-file FILE     your own verify command, one line in FILE
  --no-verify                    decline: verify.enforce stays off, the reason is recorded
  --board yes|no                 github mode: create + pin a Projects board (yes), or decline
  --ledger yes|no                github mode: the team ledger on or off
  --yes                          accept the safe defaults (mode = detected, ledger = no); never
                                 answers --board, --verify or a work flip
  --demo --vision --codex --cursor   the opt-in scaffolds (see sdlc_init.py)
  --github-templates             also copy the .github/ issue templates + workflows
  --github                       shorthand: --mode github --github-templates (the pre-#236 flag)
  --ignore-scope tracked|local   where the runtime dirs are git-ignored (default tracked)
/agrim-init runs this; /agrim-setup is an alias (`setup.py init ...`)."""

_VALUE = {"--mode", "--repo", "--work", "--verify", "--verify-command-file", "--board", "--ledger",
          "--ignore-scope"}
_BOOL = {"--local-only", "--no-verify", "--yes", "--demo", "--vision", "--codex", "--cursor",
         "--github-templates", "--github"}

#: Test seam: `board_step`'s runner, `(argv) -> (rc, output)`. None = a real subprocess.
BOARD_RUNNER = None


def _load(name, path=None):
    spec = importlib.util.spec_from_file_location("init_flow_" + name, str(path or _HERE / f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_si = _load("sdlc_init")
_pf = _si._preflight()
_vd = _si._verify_detect()


def parse(argv):
    """-> (opts dict, target, error). Unknown or malformed flags are an error, never ignored."""
    opts, pos, i = {}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            opts["help"] = True
        elif a in _BOOL:
            opts[a[2:]] = True
        elif a in _VALUE:
            if i + 1 >= len(argv):
                return opts, None, f"{a} needs a value"
            opts[a[2:]] = argv[i + 1]
            i += 1
        elif a.startswith("--"):
            return opts, None, f"unknown option {a}"
        else:
            pos.append(a)
        i += 1
    if opts.pop("github", False):
        # the pre-#236 `/agrim-init --github` meant "GitHub": now it also SAYS github mode
        if opts.get("mode", "github") != "github":
            return opts, None, "--github contradicts --mode " + opts["mode"]
        opts["mode"], opts["github-templates"] = "github", True
    for key, allowed in (("mode", MODES), ("board", ("yes", "no")), ("ledger", ("yes", "no")),
                         ("work", ("on",)), ("ignore-scope", ("tracked", "local"))):
        if key in opts and opts[key] not in allowed:
            return opts, None, f"--{key} takes one of: {' | '.join(allowed)}"
    if "verify" in opts and ":" not in opts["verify"]:
        return opts, None, "--verify takes N:ID, exactly as printed beside a candidate"
    if sum(k in opts for k in ("verify", "verify-command-file", "no-verify")) > 1:
        return opts, None, "choose one of --verify, --verify-command-file, --no-verify"
    if opts.get("local-only") and opts.get("work"):
        return opts, None, "choose one of --local-only, --work on"
    if len(pos) > 1:
        return opts, None, "one target directory at most"
    return opts, (pos[0] if pos else "."), None


def detect_github_repo(target):
    """`owner/name` when `origin` is a GitHub-shaped remote (never a gitlab/bitbucket host), else "".
    Local: one `git remote get-url`; no network."""
    rc, url = _pf.real_runner(["git", "remote", "get-url", "origin"], str(target), _pf.call_timeout())
    if rc != 0:
        return ""
    host, owner, name = _pf.parse_remote_url(url.strip())
    if not host or _pf.is_non_github(host):
        return ""
    return f"{owner}/{name}"


def resolve_answers(opts, previous, detected_mode, remote_ok):
    """The answers this run acts on: a flag, else the remembered answer, else (`--yes`, safe
    questions only) the default, else None (asked). `remote_ok` is accepted for the caller's
    symmetry and deliberately unused: a missing remote never flips `work.enabled` by default."""
    del remote_ok
    yes = bool(opts.get("yes"))
    work = "off" if opts.get("local-only") else opts.get("work") or previous.get("work")
    return {
        "mode": opts.get("mode") or previous.get("mode") or (detected_mode if yes else None),
        "repo": opts.get("repo") or previous.get("repo"),
        "ledger": opts.get("ledger") or previous.get("ledger") or ("no" if yes else None),
        "board": opts.get("board") or previous.get("board"),
        "work": work,
        "verify": None,
    }


def _read_json(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _vd._atomic_write_json(path, data)


def _child(d, key):
    if not isinstance(d.get(key), dict):
        d[key] = {}
    return d[key]


def apply_config(sdlc, answers, repo):
    """Write the answered decisions into config.json; -> the lines saying what was set. Only
    answered keys are touched, and a value already equal is not rewritten (idempotent)."""
    path = pathlib.Path(sdlc) / "config.json"
    cfg = _read_json(path)
    before = json.dumps(cfg, sort_keys=True)
    lines = []
    disc = _child(cfg, "discovery")
    if answers["mode"] == "github":
        disc["source"] = "github"
        gh = _child(disc, "github")
        gh["repo"] = repo
        for key, value in (("goal_label", "sdlc:goal"), ("in_progress_label", "sdlc:in-progress"),
                           ("parked_label", "sdlc:parked")):
            gh.setdefault(key, value)
        if gh.get("assignee") is None:                 # never clobber a real choice (setup.py #2255)
            gh["assignee"] = "@me"
        # The loop FINDS OR CREATES a board on its first github-mode pick while `project.enabled`
        # is on (the template ships it on). So a board the user has not said yes to is switched
        # off here, with the reason -- otherwise "no board without a yes" would hold for init and
        # then break at the first `loop.py next`. A pinned board is the user's; never touched.
        proj = _child(gh, "project")
        if not proj.get("number"):
            proj["enabled"] = answers["board"] == "yes"
            proj["_enabled_why"] = ("on: board_setup.py creates/pins it (--board yes at /agrim-init)"
                                    if proj["enabled"] else
                                    "off until you say yes: /agrim-init --board yes creates and pins "
                                    "a board (the loop would otherwise create one on its first pick)")
        lines.append(f"  [ok] mode: github - issues labelled sdlc:goal on {repo}, "
                     f"assignee {gh['assignee']}")
    elif answers["mode"] == "local-goals":
        disc["source"] = "local-goals"
        lines.append("  [ok] mode: local-goals - goal files in .sdlc/goals/")
    work = _child(cfg, "work")
    if answers["work"] == "off":
        work["enabled"] = False
        work["_enabled_why"] = ("set false at /agrim-init (--local-only): the loop edits this "
                                "checkout directly -- no worktree, branch, push or PR.")
        lines.append("  [ok] work: local-only (the loop edits this checkout; no worktree, branch or PR)")
    elif answers["work"] == "on":
        work["enabled"] = True
        work["_enabled_why"] = "set true at /agrim-init (--work on): a worktree + branch + PR per goal."
        lines.append("  [ok] work: a worktree + branch + PR per goal")
    if answers["ledger"] in ("yes", "no"):
        _child(cfg, "ledger")["enabled"] = answers["ledger"] == "yes"
    if json.dumps(cfg, sort_keys=True) != before:
        _write_json(path, cfg)
    return lines


def board_step(target, sdlc, answer, repo):
    """#235, github mode only. -> (lines, ok). None: print the OFFER (and the flag that answers it);
    "no": nothing is created; "yes": `board_setup.py create <sdlc> --yes`, the only path that creates
    a board. A pinned board is reported, never re-created."""
    cfg = _read_json(pathlib.Path(sdlc) / "config.json")
    proj = ((cfg.get("discovery") or {}).get("github") or {}).get("project") or {}
    if isinstance(proj, dict) and proj.get("number"):
        return [f"  [ok] board: project #{proj['number']} is pinned"], True
    if answer == "no":
        return ["  [ok] board: declined - nothing created (pin one later: board_setup.py create)"], True
    cmd = [_vd.python_command(), str(BOARD_SETUP), "create", os.path.abspath(str(sdlc))]
    if answer == "yes":
        rc, out = (BOARD_RUNNER or _run_board)(cmd + ["--yes"])
        lines = ["  " + l for l in (out or "").splitlines()]
        if rc != 0:
            return lines + [f"  [FAIL] board: board_setup.py exited {rc} (its resume command is above)"], False
        return lines + ["  [ok] board: created and pinned"], True
    offer = _si.board_offer(target, True)
    if not offer:
        offer = [f"  OFFER: create a GitHub Project board for {repo} and pin it. Preview: "
                 + " ".join(_vd._q(c) for c in cmd)]
    return (["  " + l for l in offer]
            + ["  [ask] board: re-run with --board yes to create it now, or --board no to decline"]), True


def _run_board(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _capture(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def verify_step(target, sdlc, opts):
    """#228. -> (lines, ok, asked)."""
    vd_argv = None
    if opts.get("verify"):
        n, _, ident = opts["verify"].partition(":")
        vd_argv = ["verify_detect.py", "confirm", sdlc, n, ident]
    elif opts.get("verify-command-file"):
        vd_argv = ["verify_detect.py", "set", sdlc, "--command-file", opts["verify-command-file"]]
    elif opts.get("no-verify"):
        vd_argv = ["verify_detect.py", "decline", sdlc]
    if vd_argv:
        rc, out = _capture(_vd.main, vd_argv)
        lines = ["  " + l for l in out.splitlines()]
        if rc != 0:
            return lines + ["  [FAIL] verify: nothing stored (see above)"], False, False
        return lines + ["  [ok] verify: settled"], True, False
    verify = _read_json(pathlib.Path(sdlc) / "config.json").get("verify") or {}
    if verify.get("command"):
        return [f"  [ok] verify: `{_vd.printable(verify['command'])}` "
                f"(enforce {'ON' if verify.get('enforce') else 'OFF'})"], True, False
    if str(verify.get("_why") or "").startswith("enforce OFF: the user declined"):
        return ["  [ok] verify: declined earlier - enforce OFF (the reason is in verify._why)"], True, False
    lines = ["  " + l for l in _si.verify_report(target)]
    return lines + ["  [ask] verify: re-run with --verify N:ID (a candidate above), "
                    "--verify-command-file FILE (your own), or --no-verify"], True, True


def labels_step(sdlc):
    proc = subprocess.run([sys.executable, str(SETUP_SCRIPT), "labels", sdlc],
                          capture_output=True, text=True)
    lines = ["  " + l.strip() for l in (proc.stdout + proc.stderr).splitlines() if l.strip()]
    if proc.returncode != 0:
        return lines + ["  [FAIL] labels: a required label could not be created (see above)"], False
    return lines + ["  [ok] labels: every sdlc:* and priority:P0-P3 label exists"], True


def _has_pending_goal(sdlc):
    for path in sorted((pathlib.Path(sdlc) / "goals").glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "\nstatus: pending" in text.split("\n---", 1)[0] + "\n":
            return True
    return False


def resume_command(target, argv):
    return " ".join([_vd.python_command(), _vd._q(str(pathlib.Path(__file__).resolve())),
                     _vd._q(os.path.abspath(str(target)))]
                    + [_vd._q(a) for a in argv if a != target])


def main(argv):
    args = argv[1:]
    opts, target, err = parse(args)
    if opts.get("help"):
        print(USAGE)
        return 0
    if err:
        print(f"agrim-init: {err}\n{USAGE}", file=sys.stderr)
        return 2
    if not pathlib.Path(target).is_dir():
        print(f"agrim-init: target directory does not exist: {target}", file=sys.stderr)
        return 2
    refusal = _si.git_refusal(target)                    # #229: before anything is written
    if refusal:
        print("\n".join(refusal), file=sys.stderr)
        return 2
    sdlc = os.path.abspath(os.path.join(str(target), ".sdlc"))
    try:
        coexist = _si._coexist()
    except Exception as exc:                             # noqa: BLE001 - same posture as sdlc_init
        coexist = None
        print(f"agrim-init: warning: cannot check for a second plugin ({type(exc).__name__})",
              file=sys.stderr)
    if coexist is not None and not coexist.gate(sdlc, "agrim-init"):
        return 2

    previous = _read_json(pathlib.Path(sdlc) / ANSWERS)
    detected = detect_github_repo(target)
    answers = resolve_answers(opts, previous, "github" if detected else "local-goals", None)
    repo = answers["repo"] or detected
    if answers["mode"] == "github" and not repo:
        print("agrim-init: REFUSED - github mode needs a repository, and `origin` is not a GitHub "
              "remote. Re-run with --repo OWNER/NAME (or add the GitHub remote first). Nothing "
              "was written.", file=sys.stderr)
        return 2

    # scaffold (skip-if-exists) -----------------------------------------------------------------
    if opts.get("ignore-scope") == "local":
        subprocess.run([sys.executable, str(SETUP_SCRIPT), "ignore", str(target), "--scope", "local"],
                       capture_output=True, text=True)
    try:
        created, skipped = _si.scaffold(target)
    except _si.RuntimeIgnoreWriteFailed as exc:
        print(f"agrim-init: [FAIL] scaffold: {exc}\nResume: {resume_command(target, args)}")
        return 1
    if coexist is not None:
        coexist.write_owner(sdlc)
    print(f"agrim-init: scaffold - {len(created)} created, {len(skipped)} kept "
          f"(target: {pathlib.Path(target).resolve()})")
    for c in created:
        print(f"  + .sdlc/{c}")
    extras = {"--" + k for k in ("demo", "vision", "codex", "cursor") if opts.get(k)}
    if answers["mode"] == "github":
        extras.add("--github")                           # only for the demo's gh-issue hint
    _si.scaffold_extras(target, extras)
    if opts.get("github-templates"):
        gcreated, _ = _si.scaffold_github(target)
        print(f"\nagrim-init: .github/ scaffolding - {len(gcreated)} created")
    if "config.json" in created:                         # #228: `_why` from the repo as it now is
        _vd.write_verify(sdlc, None, _vd.unconfirmed_why(_vd.detect(target)))

    mode_lines = apply_config(sdlc, answers, repo)
    remember = {k: v for k, v in answers.items() if v is not None and k != "verify"}
    if answers["mode"] == "github":
        remember["repo"] = repo
    if remember != previous:
        _write_json(pathlib.Path(sdlc) / ANSWERS, remember)

    failed, asked = [], []
    cfg = _read_json(pathlib.Path(sdlc) / "config.json")

    # 1 preflight --------------------------------------------------------------------------------
    print("\nagrim-init: 1/5 preflight")
    checks = _pf.preflight(str(pathlib.Path(target).resolve()), cfg,
                           runner=_si.PREFLIGHT_RUNNER, which=_si.PREFLIGHT_WHICH)
    for line in _si.preflight_report(target, checks=checks):
        print("  " + line)
    work_on = bool((cfg.get("work") or {}).get("enabled"))
    remote = next((c for c in checks if c["id"] == "remote"), {})
    if work_on and remote.get("ok") is False and answers["work"] is None:
        asked.append("work")
        print("  [ask] work: fix the remote above, or re-run with --local-only")
    # A missing remote while that very choice is still open is a question (the DECISION above),
    # not a failure; every other blocking check fails the run.
    if [c for c in _pf.blocking(checks) if not (c["id"] == "remote" and "work" in asked)]:
        failed.append("preflight")

    # 2 mode -------------------------------------------------------------------------------------
    print("\nagrim-init: 2/5 mode")
    if answers["mode"] is None:
        asked.append("mode")
        default = "github" if detected else "local-goals"
        why = f"origin is {detected}" if detected else "origin is not a GitHub repository"
        print(f"  [ask] mode: local-goals (goal files) or github (issues)? default: {default} ({why}).")
        print("        Re-run with --mode github or --mode local-goals (--yes takes the default).")
    for line in mode_lines:
        print(line)

    # 3 verify -----------------------------------------------------------------------------------
    print("\nagrim-init: 3/5 verify")
    lines, ok, ask = verify_step(target, sdlc, opts)
    print("\n".join(lines))
    if not ok:
        failed.append("verify")
    if ask:
        asked.append("verify")

    # 4 github -----------------------------------------------------------------------------------
    print("\nagrim-init: 4/5 github")
    if answers["mode"] != "github":
        print("  [skip] " + ("local-goals mode: no labels, no board, no ledger question"
                             if answers["mode"] else "mode not chosen yet"))
    else:
        lines, ok = labels_step(sdlc)
        print("\n".join(lines))
        if not ok:
            failed.append("labels")
        cfg = _read_json(pathlib.Path(sdlc) / "config.json")
        print(f"  [ok] assignee: {((cfg.get('discovery') or {}).get('github') or {}).get('assignee')}"
              " (the loop picks issues assigned to you)")
        lines, ok = board_step(target, sdlc, answers["board"], repo)
        print("\n".join(lines))
        if not ok:
            failed.append("board")
        elif answers["board"] is None and not any("is pinned" in l for l in lines):
            asked.append("board")
        if answers["ledger"] == "yes":
            print("  [ok] ledger: on. Bootstrap its ops branch when ready (it pushes a branch): "
                  + " ".join([_vd.python_command(), _vd._q(str(SYNC_SCRIPT)), "bootstrap", _vd._q(sdlc)]))
        elif answers["ledger"] == "no":
            print("  [ok] ledger: off")
        else:
            asked.append("ledger")
            print("  [ask] ledger: the team ledger (claims + hand-offs on an ops branch)? "
                  "re-run with --ledger yes or --ledger no (--yes: no)")

    # 5 summary ----------------------------------------------------------------------------------
    cfg = _read_json(pathlib.Path(sdlc) / "config.json")
    verify = cfg.get("verify") or {}
    print("\nagrim-init: 5/5 summary")
    print(f"  mode:   {(cfg.get('discovery') or {}).get('source')}"
          + (f" ({repo})" if (cfg.get("discovery") or {}).get("source") == "github" else ""))
    print(f"  work:   {'PR per goal' if (cfg.get('work') or {}).get('enabled') else 'local-only (this checkout)'}")
    print(f"  verify: {('`' + _vd.printable(verify['command']) + '`') if verify.get('command') else 'none (enforce OFF)'}")
    if failed:
        print(f"  result: FAILED at {', '.join(failed)} - everything written so far is kept")
        print(f"Resume: {resume_command(target, args)}")
        return 1
    if asked:
        print(f"  open:   {', '.join(asked)} - answer the [ask] lines above (same command + the flag)")
    loop = " ".join([_vd.python_command(), _vd._q(str(LOOP_SCRIPT)), "next", _vd._q(sdlc)])
    if (cfg.get("discovery") or {}).get("source") == "github":
        print("Next: label an issue `sdlc:goal` (assigned to you), then /agrim-loop "
              f"(Codex/Cursor: {loop}).")
    elif _has_pending_goal(sdlc):
        print(f"Next: /agrim-loop (Codex/Cursor: {loop}).")
    else:
        print("Next: re-run with --demo to queue a demo goal first (or add .sdlc/goals/NNNN-*.md with "
              f"status: pending), then /agrim-loop (Codex/Cursor: {loop}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
