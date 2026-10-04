#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Drive Sigma's scripted end-to-end flow against a throwaway GitHub repository (goal 351). THE ONLY WRITER.

    blast_radius_drive.py drive REPO --run always|off --workdir DIR --baseline-out FILE [--unrelated N]

Run by an operator, once per `--run`, under the egress hook:

    python3 tools/readiness/egress_capture.py run --log LOG -- \\
        python3 tools/readiness/blast_radius_drive.py drive OWNER/sigma-drill-NAME --run always \\
        --workdir SCRATCH/run1 --baseline-out SCRATCH/baseline1.json

then `blast_radius.py assert` (see docs/launch/blast-radius.md). No model is called: this is the sequence
`tools/onboarding_control.py` scripts against a fake `gh`, pointed at the real repository (setup commit
and push when the repository is empty, init in github mode, one goal issue with no unit, loop and work
scripts, verify, commit, PR, an approve comment, `work.py merge`). It never merges by hand, never touches
any issue but the one goal it files, and never creates, deletes, edits or changes the visibility of a
repository. The repository must be PRIVATE, named `OWNER/sigma-drill-*`, not a fork, not archived; the
drill refuses from CI and refuses a workdir that already exists. The token comes from `gh auth token` at
run time into the child environment only; nothing writes it anywhere.

Refusals are typed (`REFUSED [code]` on stderr, exit 2, nothing on stdout). EXIT: 0 = the scripted flow ran to
its end (the assertions are `blast_radius.py assert`'s job); 1 = a step failed; 2 = refused. POSIX only.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import blast_radius as br  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
#: Every text the drill generates. None may carry a `#<number>`: a cross-reference writes an event on
#: whichever issue it names, and the drill must not write to the unrelated one.
GOAL_BODY = ("Create the drill file with one line.\n\n## Done when\n- [ ] The drill file contains hi.\n"
             "- [ ] Verification passes when configured.\n- [ ] The goal can be recorded after its PR merges.\n")
APPROVE = "sigma:approve"
NO_TESTS = "The drill writes one text file; shell verification is retained."


def goal_title(run):
    return "Add drill-%s.txt" % run


def work_file(run):
    return "drill-%s.txt" % run


def validate(repo, info, env):
    """Every refusal, `ci` last so it never masks another. `info` is the repository read back over REST."""
    if not br.REPO_RE.fullmatch(repo):
        raise br.Refusal("repo-form", "%r is not OWNER/sigma-drill-NAME" % repo)
    if str(info.get("full_name", "")).lower() != repo.lower():
        raise br.Refusal("repo-mismatch", "GitHub says %r" % info.get("full_name"))
    if info.get("private") is not True:
        raise br.Refusal("repo-not-private", "the drill repository must be private")
    if info.get("fork") or info.get("archived"):
        raise br.Refusal("repo-kind", "a fork or archived repository is refused")
    if env.get("CI"):
        raise br.Refusal("ci", "the drill never runs from CI")


def needs_setup(ls_remote_heads_output):
    """The repository is empty only for the first run: push once then; later runs clone and never push."""
    return not ls_remote_heads_output.strip()


def child_env(oc, root, bin_dir, repo, token, environ):
    """The control's cleared environment plus what a REAL repository needs: the capture hook (or the
    children are invisible to it), the real gh on PATH, GH_REPO, a token and a credential helper."""
    gh = shutil.which("gh")
    py_dir = str(pathlib.Path(sys.executable).parent)
    extra = {"GH_REPO": repo, "GH_TOKEN": token, "GIT_TERMINAL_PROMPT": "0",
             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.https://github.com.helper",
             "GIT_CONFIG_VALUE_0": "!gh auth git-credential",
             "PATH": os.pathsep.join([str(bin_dir), py_dir, str(pathlib.Path(gh).parent) if gh else "",
                                      "/usr/local/bin", "/usr/bin", "/bin"])}
    for key in ("SIGMA_EGRESS_LOG", "PYTHONPATH"):
        if environ.get(key):
            extra[key] = environ[key]
    return oc._env(root, bin_dir, extra)


def _oc():
    spec = importlib.util.spec_from_file_location("onboarding_control", ROOT / "tools" / "onboarding_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def origin_guard(oc, cwd, env, url):
    got = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(cwd), env=env,
                         capture_output=True, text=True).stdout.strip()
    if got != url:
        raise oc.Red("origin guard", "origin is not the drill repository")


def drive(repo, run_mode, workdir, baseline_out, unrelated, rest, environ, sigma=ROOT):
    oc = _oc()
    validate(repo, rest.repo_info(), environ)
    root = pathlib.Path(workdir)
    if root.exists():
        raise br.Refusal("workdir-exists", "the workdir must not exist")
    root.mkdir(parents=True)
    bin_dir, repo_dir, url = root / "bin", root / "repo", "https://github.com/%s.git" % repo
    bin_dir.mkdir()
    gh_out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    env = child_env(oc, root, bin_dir, repo, gh_out, environ)
    run = oc.Run("drill/" + run_mode)
    py, loop = sys.executable, pathlib.Path(sigma) / "skills" / "agrim-loop" / "scripts"
    summary = {"repo": repo, "run": run_mode}
    try:
        oc._git(["clone", "-q", url, str(repo_dir)], root, env)
        origin_guard(oc, repo_dir, env, url)
        default = rest.repo_info().get("default_branch") or "main"
        heads = subprocess.run(["git", "ls-remote", "--heads", "origin", default], cwd=str(repo_dir), env=env,
                               capture_output=True, text=True).stdout
        setup = needs_setup(heads)
        if setup:
            oc._git(["checkout", "-q", "-B", default], repo_dir, env)
            oc._fresh_files(repo_dir, "confirm")
            oc._git(["add", "-A"], repo_dir, env)
            oc._git(["commit", "-qm", "fresh repository"], repo_dir, env)
            pushed = subprocess.run(["git", "push", "-u", "origin", default], cwd=str(repo_dir), env=env,
                                    capture_output=True, text=True)
            if pushed.returncode:
                raise oc.Red("setup push", pushed.stderr.strip()[-300:])
        summary["setup_push"] = setup
        baseline = br.take_baseline(rest, repo, unrelated, setup)
        pathlib.Path(baseline_out).write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        oc.FAKE_REPO = repo                         # the control's --repo for init: the drill, not its fake
        qs = oc.parse_quickstart((pathlib.Path(sigma) / "README.md").read_text(encoding="utf-8"), sigma)
        init_out, _ = oc._init_and_verify(run, qs, sigma, repo_dir, env, "github", "confirm")
        cfg_path = repo_dir / ".sdlc" / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if (cfg.get("discovery", {}).get("github", {}) or {}).get("repo") not in (None, "", repo):
            raise oc.Red("config", "discovery.github.repo names another repository")
        cfg.setdefault("work", {})["auto_merge"] = run_mode
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        fl = subprocess.run(["gh", "issue", "create", "--repo", repo, "--label", "sdlc:goal", "--assignee", "@me",
                             "--title", goal_title(run_mode), "--body", GOAL_BODY],
                            cwd=str(repo_dir), env=env, capture_output=True, text=True)
        if fl.returncode:
            raise oc.Red("file goal", fl.stderr.strip()[-300:])
        goal = fl.stdout.strip().rsplit("/", 1)[-1]
        if not goal.isdigit() or int(goal) == unrelated:
            raise oc.Red("file goal", "no usable goal number")
        summary["goal"] = goal
        pid = str(os.getpid())
        run.step("loop start", [py, loop / "loop.py", "start", ".sdlc", "--session-pid", pid], repo_dir, env)
        nxt = oc._py_argv(oc._loop_next_line(init_out), sigma, {}, step="loop next", cwd=repo_dir) + ["--session-pid", pid]
        if run.step("loop next", nxt, repo_dir, env).stdout.strip() != goal:
            raise oc.Red("loop next", "the loop picked a goal other than the one the drill filed")
        run.step("agent-start", [py, loop / "loop.py", "agent-start", ".sdlc", goal, "--pid", pid], repo_dir, env)
        run.step("work start", [py, loop / "work.py", "start", ".sdlc", goal, "--session-pid", pid], repo_dir, env)
        acc = [py, loop / "acceptance.py", "record", ".sdlc", goal]
        if (cfg.get("verify") or {}).get("command"):
            acc += ["--verify-command", "test -s " + work_file(run_mode)]
        run.step("record acceptance", acc, repo_dir, env)
        src = repo_dir / ".sdlc" / "acceptance" / (goal + ".md")
        dst = repo_dir / ".sdlc" / "work" / goal / ".sdlc" / "acceptance" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        run.step("phase start", [py, loop / "phase_report.py", "start", ".sdlc", goal, "implement",
                                 "--model", "haiku", "--pid", pid], repo_dir, env)
        (repo_dir / ".sdlc" / "work" / goal / work_file(run_mode)).write_text("hi\n", encoding="utf-8")
        run.step("phase end", [py, loop / "phase_report.py", "end", ".sdlc", goal, "implement", "--pid", pid], repo_dir, env)
        run.step("loop verify", [py, loop / "loop.py", "verify", ".sdlc", goal], repo_dir, env)
        run.step("work commit", [py, loop / "work.py", "commit", ".sdlc", goal, "--message",
                                 "sdlc: " + goal_title(run_mode).lower()], repo_dir, env)
        run.step("work pr", [py, loop / "work.py", "pr", ".sdlc", goal, "--no-tests", NO_TESTS], repo_dir, env)
        known = set(baseline["pulls"])
        opened = [str(p["number"]) for p in rest.pulls() if str(p["number"]) not in known]
        if len(opened) != 1:
            raise oc.Red("work pr", "expected exactly one new PR, saw %s" % opened)
        pr = opened[0]
        summary["pr"] = pr
        ap = subprocess.run(["gh", "pr", "comment", pr, "--repo", repo, "--body", APPROVE],
                            cwd=str(repo_dir), env=env, capture_output=True, text=True)
        if ap.returncode:
            raise oc.Red("approve", ap.stderr.strip()[-300:])
        origin_guard(oc, repo_dir, env, url)
        merged = run.step("work merge", [py, loop / "work.py", "merge", ".sdlc", goal], repo_dir, env).stdout.strip()
        summary["merge_line"] = merged[:160]
        outcome = "done" if merged.startswith("PR #") and " merged " in merged else "review"
        run.step("record " + outcome, [py, loop / "loop.py", "record", ".sdlc", goal, outcome], repo_dir, env)
        summary["recorded"] = outcome
    except oc.Red as red:
        run.failed = red.step
        summary["failed"] = {"step": red.step, "detail": red.detail[-300:]}
    summary["steps"] = [{"step": s["step"], "rc": s.get("rc"), "seconds": s["seconds"]} for s in run.steps]
    return summary


def main(argv=None, rest_factory=None, environ=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drive")
    d.add_argument("repo")
    d.add_argument("--run", required=True, choices=("always", "off"))
    d.add_argument("--workdir", required=True)
    d.add_argument("--baseline-out", required=True)
    d.add_argument("--unrelated", type=int, default=1)
    args = ap.parse_args(argv)
    environ = os.environ if environ is None else environ
    try:
        br._refuse_repo(args.repo)
        rest = (rest_factory or br.Rest)(args.repo)
        summary = drive(args.repo, args.run, args.workdir, args.baseline_out, args.unrelated, rest, environ)
    except br.Refusal as exc:
        print("blast_radius_drive.py: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if "failed" in summary else 0


if __name__ == "__main__":
    raise SystemExit(main())
