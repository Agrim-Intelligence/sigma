#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Preflight: everything the loop needs from git and `gh`, checked up front (#229).

WHY THIS EXISTS. `work.enabled` ships ON. In a repository with no `origin` remote the first goal
died at `work.py start` with git's raw `fatal: 'origin' does not appear to be a git repository`;
`gh` missing, logged out, or holding a token without `workflow` surfaced later still, one goal at a
time. This module asks each question once, at `/agrim-init` and in `/agrim-doctor`, and answers a
failure with the exact command -- once per host, because Claude Code, Codex and Cursor each run a
command in a different place -- and with what Sigma does meanwhile.

THE CHECKS, in order; a check whose prerequisite failed is SKIPPED and says so, never guessed:
    git          git installed; the directory is a work tree; it has a first commit
    remote       `work.remote` (default origin) exists -- or which remotes do
    base         `work.base` (else the current branch) exists locally AND on the remote
    gh-installed `gh` on PATH
    gh-auth      `gh auth status --hostname <host>` succeeds
    scopes       repo + workflow (work on), read:org (owner is an organization), project (board on)
Each result is {"id", "name", "ok", "detail", "commands", "interactive", "meanwhile", "note"}.
`ok` is True, False, or None -- None is CANNOT VERIFY (a fine-grained or app token reports no
scopes; the owner's type could not be read; `ls-remote` timed out). None is never printed as OK.

NOTHING HERE IS SECRET-BEARING. `gh auth status` masks the token itself; this module never passes
`--show-token`, and never prints gh's raw output -- only the facts parsed out of it (logged in or
not, the scope NAMES, the token's kind from its public prefix).

BOUNDED. Every call goes through one runner, `runner(argv, cwd, timeout) -> (rc, text)`, injectable
for tests (no network in tests). The real runner starts each call in its own process group and
kills the WHOLE group on overrun (POSIX killpg, Windows `taskkill /T /F`): `git ls-remote` over ssh
can leave a grandchild holding the pipe, which is the hang `agrim-loop/scripts/run_with_timeout.py`
measured. The bound is not a new constant: it is the fleet's existing per-call bound
`SIGMA_WATCH_CALL_TIMEOUT` (watch_daemon.py), else that bound's own default (120s). Git and gh run with
`GIT_TERMINAL_PROMPT=0` / `GH_PROMPT_DISABLED=1` and stdin closed, so nothing waits on a prompt. Cost:
at most 9 subprocess calls, 3 of them network (`ls-remote`, `gh auth status`, `gh api users/<o>`);
constant per repository, independent of repository size.

CLI (the gestures /agrim-init, /agrim-doctor and work.py print):
    preflight.py check      [repo_root] [--sdlc <dir>]   # report; exit 1 on a blocking failure
    preflight.py local-only <sdlc_dir>                   # work.enabled -> false (no worktree/PR)
    preflight.py use-remote <sdlc_dir> <remote>          # work.remote -> an existing remote
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
HERE = str(pathlib.Path(__file__).resolve())
#: The portable placeholder for text that lands in committed config (verify_detect's convention).
SCRIPT = "<sigma>/skills/agrim-init/scripts/preflight.py"

#: The default of `SIGMA_WATCH_CALL_TIMEOUT` (watch_daemon.py): the bound this codebase already
#: applies to one hung git/gh call. Reused, not re-chosen.
DEFAULT_TIMEOUT = 120.0

HOSTS = ("Claude Code", "Codex", "Cursor")

#: Windows' CREATE_NEW_PROCESS_GROUP, defined by the stdlib only on Windows (run_with_timeout.py).
_NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _load_sibling(name):
    path = _HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"preflight_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_vd = _load_sibling("verify_detect")          # printable / _q / python_command / _atomic_write_json
printable = _vd.printable


def _legacy():
    path = _HERE.parent.parent / "agrim-loop" / "scripts" / "legacy.py"
    spec = importlib.util.spec_from_file_location("preflight_legacy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call_timeout(env=None):
    """Seconds one git/gh call may take: the fleet's existing per-call bound
    `SIGMA_WATCH_CALL_TIMEOUT` (read through legacy.getenv, so its previous spelling still works),
    else its default. A non-numeric or non-positive value is ignored, never trusted."""
    try:
        raw = _legacy().getenv("SIGMA_WATCH_CALL_TIMEOUT", environ=env) or ""
    except Exception:                                    # noqa: BLE001 - fall back to the default
        raw = (env if env is not None else os.environ).get("SIGMA_WATCH_CALL_TIMEOUT") or ""
    try:
        value = float(str(raw).strip())
    except ValueError:
        return DEFAULT_TIMEOUT
    return value if value > 0 else DEFAULT_TIMEOUT


def _kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()


def real_runner(argv, cwd=None, timeout=None):
    """Run argv (no shell) -> (rc, combined stdout+stderr). 127: not on PATH; 124: timed out (the
    whole process tree was killed); 126: could not start."""
    timeout = call_timeout() if timeout is None else timeout
    exe = shutil.which(argv[0])
    if not exe:
        return 127, f"{argv[0]}: not found on PATH"
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GH_PROMPT_DISABLED="1",
               GH_NO_UPDATE_NOTIFIER="1")
    group = {"creationflags": _NEW_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen([exe, *argv[1:]], cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env, **group)
    except OSError as exc:
        return 126, f"{argv[0]}: could not start ({exc})"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        return 124, f"{argv[0]} {argv[1] if len(argv) > 1 else ''}: timed out after {timeout:g}s"
    return proc.returncode, out or ""


# ---------------------------------------------------------------- parsing (pure)

def parse_remote_url(url):
    """-> (host, owner, repo) from an ssh/scp/https remote URL, or (None, None, None)."""
    url = (url or "").strip()
    m = (re.match(r"^[\w.+-]+@([^:/]+):(?:\d+/)?([^/]+)/([^/]+?)(?:\.git)?/?$", url)
         or re.match(r"^(?:ssh|https?|git)://(?:[^@/]+@)?([^:/]+)(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$",
                     url))
    return (m.group(1).lower(), m.group(2), m.group(3)) if m else (None, None, None)


def token_kind(prefix_text):
    """The kind of a (masked) token from its public prefix: classic | fine-grained | app | unknown."""
    t = (prefix_text or "").strip()
    if t.startswith("github_pat_"):
        return "fine-grained"
    if t.startswith("ghs_"):
        return "app"
    if t.startswith(("ghp_", "gho_", "ghu_")):
        return "classic"
    return "unknown"


def parse_auth_status(text):
    """`gh auth status` text -> {"token_kind", "scopes"}; scopes is a set, or None when gh printed no
    scopes (`none`, or no scopes line at all). With several accounts, the ACTIVE one's block is read
    (`Active account: true`); older gh prints no such marker, so its first account is read."""
    lines = (text or "").splitlines()
    starts = [i for i, ln in enumerate(lines) if re.search(r"(?i)\b(logged in to|failed to log in)", ln)]
    blocks = [lines[s:e] for s, e in zip(starts, starts[1:] + [len(lines)])] or [lines]
    active = [b for b in blocks if any(re.search(r"(?i)active account:\s*true", ln) for ln in b)]
    block = active[0] if active else blocks[0]
    kind, scopes = "unknown", None
    for ln in block:
        m = re.search(r"(?i)\btoken:\s*(\S+)", ln)
        if m:
            kind = token_kind(m.group(1))
        m = re.search(r"(?i)\bscopes:\s*(.*)$", ln)
        if m:
            raw = m.group(1).strip()
            items = {s.strip().strip("'\"").strip() for s in raw.split(",")}
            items.discard("")
            scopes = None if raw.lower() in ("", "none", "'none'") else items
    return {"token_kind": kind, "scopes": scopes}


#: A scope and the broader scopes that include it (GitHub's documented scope hierarchy).
_IMPLIED_BY = {"read:org": ("read:org", "write:org", "admin:org"), "repo": ("repo",),
               "workflow": ("workflow",), "project": ("project",)}


def missing_scopes(have, need):
    return [s for s in need if not any(h in have for h in _IMPLIED_BY.get(s, (s,)))]


# ---------------------------------------------------------------- the checks

def _cfg_block(cfg, name):
    value = cfg.get(name) if isinstance(cfg, dict) else None
    return value if isinstance(value, dict) else {}


def requirements(config):
    """What this config needs from git and gh: work (worktree+PR per goal), github (issue discovery),
    board (project mirroring)."""
    work = _cfg_block(config, "work")
    disc = _cfg_block(config, "discovery")
    gh_disc = _cfg_block(disc, "github")
    github = disc.get("source") == "github"
    return {"work": bool(work.get("enabled")), "github": github,
            # the board mirrors github issues only: `project.enabled` means nothing in local-goals
            # mode (the template ships it true there), exactly as doctor has always gated it
            "board": github and bool(_cfg_block(gh_disc, "project").get("enabled")),
            "remote": (str(work.get("remote") or "").strip() or "origin"),
            "base": str(work.get("base") or "").strip(),
            "repo": str(gh_disc.get("repo") or "").strip()}


def _chk(cid, name, ok, detail="", commands=(), interactive=False, meanwhile="", note=""):
    return {"id": cid, "name": name, "ok": ok, "detail": detail, "commands": list(commands),
            "interactive": interactive, "meanwhile": meanwhile, "note": note}


def _skipped(cid, name, because):
    return _chk(cid, name, None, f"skipped: {because}", note="skipped")


def _install_hint(tool):
    url = {"gh": "https://cli.github.com", "git": "https://git-scm.com/downloads"}[tool]
    if sys.platform == "darwin":
        return [f"brew install {tool}"], url
    if os.name == "nt":
        return [f"winget install --id {'GitHub.cli' if tool == 'gh' else 'Git.Git'}"], url
    return [], url


def check_git(repo, runner, which, timeout):
    name = "git repository"
    if not which("git"):
        cmds, url = _install_hint("git")
        return _chk("git", name, False, f"git is not installed (not on PATH); see {url}", cmds,
                    meanwhile="Sigma cannot cut worktrees, commit, or read a base without git.")
    rc, out = runner(["git", "rev-parse", "--is-inside-work-tree"], repo, timeout)
    if rc != 0 or out.strip() != "true":
        return _chk("git", name, False, f"{printable(repo)} is not inside a git repository",
                    ["git init", 'git commit --allow-empty -m "initial commit"'],
                    meanwhile="/agrim-init writes nothing here until this is a repository; the loop "
                              "needs one to cut a worktree and record a commit.")
    rc, _ = runner(["git", "rev-parse", "--verify", "-q", "HEAD"], repo, timeout)
    if rc != 0:
        return _chk("git", name, False, "the repository has no commit yet, so no branch exists to "
                    "cut goals from", ['git commit --allow-empty -m "initial commit"'],
                    meanwhile="`work.py start` has no base branch to cut a goal's worktree from.",
                    note="no-commit")
    return _chk("git", name, True, "a git work tree with at least one commit")


def check_remote(repo, want, runner, timeout):
    """-> (check, remotes, url)."""
    name = f"git remote '{want}'"
    rc, out = runner(["git", "remote"], repo, timeout)
    remotes = sorted({r.strip() for r in out.splitlines() if r.strip()}) if rc == 0 else []
    if want in remotes:
        rc, url = runner(["git", "remote", "get-url", want], repo, timeout)
        return _chk("remote", name, True, printable(url.strip()) if rc == 0 else ""), remotes, \
            (url.strip() if rc == 0 else "")
    others = ("other remotes here: " + ", ".join(printable(r) for r in remotes)) if remotes \
        else "this repository has no remote at all"
    cmds = [f"git remote add {want} <url-of-your-repository>"]
    check = _chk("remote", name, False, f"no remote named '{printable(want)}' ({others})", cmds,
                meanwhile=f"with work.enabled on, `work.py start` refuses every goal with this message "
                          f"before cutting a worktree (it fetches {want}/<base> first); planning, "
                          "research and local-goals discovery are unaffected.",
                note="no-remote")
    check["remotes"] = remotes
    return check, remotes, ""


def check_base(repo, remote, base, runner, timeout):
    if not base:
        rc, out = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo, timeout)
        base = out.strip() if rc == 0 else ""
        if not base or base == "HEAD":
            return _chk("base", "base branch", False, "HEAD is detached and work.base is empty, so "
                        "there is no base branch to cut goals from",
                        ["git switch <your-integration-branch>"],
                        meanwhile="`work.py start` cuts from the current branch and cannot name one.")
    name = f"base branch '{printable(base)}' on '{printable(remote)}'"
    rc, _ = runner(["git", "rev-parse", "--verify", "-q", f"refs/heads/{base}"], repo, timeout)
    local = rc == 0
    rc, out = runner(["git", "ls-remote", "--heads", remote, base], repo, timeout)
    if rc == 0 and out.strip():
        return _chk("base", name, True, "exists on the remote")
    if rc == 0:
        cmds = [f"git push -u {remote} {base}"] if local else [f"git switch -c {base}",
                                                                 f"git push -u {remote} {base}"]
        return _chk("base", name, False, f"'{printable(base)}' is not on '{printable(remote)}'"
                    + ("" if local else " and does not exist locally either"), cmds,
                    meanwhile=f"`work.py start` fetches {remote}/{base} and refuses each goal until "
                              "it exists there.")
    first = printable((out.strip().splitlines() or ["no output"])[0])
    return _chk("base", name, None, f"cannot verify: `git ls-remote` failed ({first})",
                ["git ls-remote --heads " + f"{remote} {base}"],
                meanwhile="Sigma assumes nothing: the first `work.py start` will show git's own error.")


def check_gh_installed(which):
    if which("gh"):
        return _chk("gh-installed", "gh installed", True, "gh is on PATH")
    cmds, url = _install_hint("gh")
    return _chk("gh-installed", "gh installed", False, f"the GitHub CLI `gh` is not on PATH; "
                f"install it from {url}, then re-run this check (it names the next step)", cmds,
                meanwhile="Sigma runs nothing that needs gh: `work.py pr` cannot open a pull request and "
                          "github discovery cannot read issues; local goals still plan, edit and "
                          "verify in their worktree.")


def _proxy_block(raw):
    """gh_session's classifier for a Claude Code Remote proxy block (#78), loaded fail-open."""
    try:
        path = _HERE.parent.parent / "agrim-loop" / "scripts" / "gh_session.py"
        spec = importlib.util.spec_from_file_location("preflight_gh_session", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.proxy_session_block(raw)
    except Exception:                                    # noqa: BLE001 - a can't-tell is not a crash
        return None


def check_gh_auth(host, runner, timeout):
    """-> (check, parsed-status or None)."""
    rc, out = runner(["gh", "auth", "status", "--hostname", host], None, timeout)
    if rc == 0:
        return _chk("gh-auth", "gh auth", True, f"logged in to {printable(host)}"), \
            parse_auth_status(out)
    proxy = _proxy_block(out)
    if proxy:
        return _chk("gh-auth", "gh auth", False, proxy, meanwhile="every gh call fails in this "
                    "session whatever the token; local work continues."), None
    if rc == 124:
        return _chk("gh-auth", "gh auth", None, f"cannot verify: {printable(out.strip())}"), None
    return _chk("gh-auth", "gh auth", False, f"gh is not logged in to {printable(host)} (or its "
                "token is invalid)", [f"gh auth login -h {host} -s repo,workflow,read:org"],
                interactive=True,
                meanwhile="Sigma runs nothing that needs gh: no pull request, no issue discovery."), None


def check_owner_type(owner, host, runner, timeout):
    """'Organization' | 'User' | None (could not tell)."""
    if not owner:
        return None
    argv = ["gh", "api", f"users/{owner}", "--jq", ".type"]
    if host and host != "github.com":
        argv[2:2] = ["--hostname", host]
    rc, out = runner(argv, None, timeout)
    value = out.strip()
    return value if rc == 0 and value in ("Organization", "User") else None


def check_scopes(status, need, host, org, owner):
    name = "gh token scopes"
    have, kind = status["scopes"], status["token_kind"]
    sso = (f"if {printable(owner)} enforces SAML SSO, also authorize the token for it: "
           "https://github.com/settings/tokens -> Configure SSO -> Authorize"
           if org == "Organization" else "")
    if have is None and kind != "classic":
        return _chk("scopes", name, None,
                    f"cannot verify: a {kind if kind != 'unknown' else 'non-classic'} token reports "
                    "no scopes. Sigma needs it to grant Contents: write, Pull requests: write and "
                    "Workflows: write" + (", Projects: write" if "project" in need else "")
                    + (", and access to the organization" if org == "Organization" else "")
                    + " on this repository -- check at https://github.com/settings/personal-access-"
                      "tokens" + (f"; {sso}" if sso else ""),
                    [f"gh auth login -h {host} -s {','.join(need)}"], interactive=True,
                    meanwhile="Sigma runs, and the first call the token cannot make fails with "
                              "GitHub's own error.")
    missing = missing_scopes(have or set(), need)
    if missing:
        return _chk("scopes", name, False,
                    f"missing {', '.join(missing)} (token has: "
                    f"{', '.join(sorted(printable(s) for s in have)) if have else 'no scopes'})",
                    [f"gh auth refresh -s {','.join(missing)} -h {host}"], interactive=True,
                    meanwhile=_scope_meanwhile(missing),
                    note=sso)
    if org is None and missing_scopes(have or set(), ["read:org"]):
        who = f"'{printable(owner)}'" if owner else "the repository owner"
        return _chk("scopes", name, None,
                    f"has {', '.join(need)}; cannot verify whether read:org is needed ({who} could "
                    "not be identified as a user or an organization)",
                    [f"gh auth refresh -s read:org -h {host}"], interactive=True,
                    meanwhile="if the owner is an organization, org-level gh calls may be refused.")
    return _chk("scopes", name, True, "has " + ", ".join(need))


_SCOPE_EFFECT = {
    "repo": "GitHub refuses the push and the pull request",
    "workflow": "GitHub rejects any push that adds or changes .github/workflows/* (every other "
                "push goes through)",
    "read:org": "organization-level gh calls are refused",
    "project": "board mirroring is refused; the loop runs, the board does not move",
}


def _scope_meanwhile(missing):
    return "; ".join(f"without {s}, {_SCOPE_EFFECT[s]}" for s in missing if s in _SCOPE_EFFECT) + "."


def preflight(repo, config=None, runner=None, which=None, timeout=None, network=True):
    """All checks this config makes relevant, in order, for the repository at `repo`. `network=False`
    runs only the local checks (git, remote, gh installed) and marks the rest skipped -- for a
    caller that has not been asked to spend a network round-trip (doctor's `cheap_only`)."""
    runner = runner or real_runner
    which = which or shutil.which
    timeout = call_timeout() if timeout is None else timeout
    req = requirements(config or {})
    repo = str(repo)
    out = [check_git(repo, runner, which, timeout)]
    if not (req["work"] or req["github"]):
        return out
    git_ok = out[0]["ok"] is True
    remote_name = req["remote"]
    url = ""
    if git_ok:
        remote, _remotes, url = check_remote(repo, remote_name, runner, timeout)
        out.append(remote)
        out.append(_skipped("base", "base branch", f"no remote '{remote_name}'") if not remote["ok"]
                   else check_base(repo, remote_name, req["base"], runner, timeout) if network
                   else _skipped("base", "base branch", "network checks not run here"))
    else:
        out.append(_skipped("remote", f"git remote '{remote_name}'", "not a git repository"))
        out.append(_skipped("base", "base branch", "not a git repository"))
    out.append(check_gh_installed(which))
    if out[-1]["ok"] is not True:
        out.append(_skipped("gh-auth", "gh auth", "gh is not installed"))
        out.append(_skipped("scopes", "gh token scopes", "gh is not installed"))
        return out
    if not network:
        out.append(_skipped("gh-auth", "gh auth", "network checks not run here"))
        out.append(_skipped("scopes", "gh token scopes", "network checks not run here"))
        return out
    host, owner, _repo = parse_remote_url(url)
    if req["repo"] and "/" in req["repo"]:
        owner = req["repo"].split("/", 1)[0]
    host = host or "github.com"
    auth, status = check_gh_auth(host, runner, timeout)
    out.append(auth)
    if auth["ok"] is not True:
        out.append(_skipped("scopes", "gh token scopes", "gh auth did not pass"))
        return out
    org = check_owner_type(owner, host, runner, timeout)
    need = ["repo"] + (["workflow"] if req["work"] else []) \
        + (["read:org"] if org == "Organization" else []) + (["project"] if req["board"] else [])
    scopes = check_scopes(status, need, host, org, owner)
    have = status["scopes"]
    scopes.update(have=sorted(have) if have is not None else None, need=need, host=host,
                  missing=missing_scopes(have, need) if have is not None else None)
    out.append(scopes)
    return out


# ---------------------------------------------------------------- rendering

_WHERE = {
    "Claude Code": ("run it in your own terminal -- it is interactive (browser or device code), so "
                    "the agent must not run it for you",
                    "the agent may run it once you say yes, or type it with the `!` prefix"),
    "Codex": ("run it in a terminal outside the Codex sandbox (it needs your browser and keyring)",
              "run it in a terminal outside the Codex sandbox, or let Codex run it once you approve"),
    "Cursor": ("run it in Cursor's integrated terminal (View > Terminal)",
               "run it in Cursor's integrated terminal (View > Terminal)"),
}


def _commands_text(check):
    return ", then ".join(f"`{c}`" for c in check["commands"])


def failure_lines(check, indent="  "):
    """One failing (or cannot-verify) check: the fact, one remediation line per host, meanwhile."""
    label = "CANNOT VERIFY" if check["ok"] is None else "FAIL"
    lines = [f"{indent}[{label}] {check['name']}: {check['detail']}"]
    if check["commands"]:
        for host in HOSTS:
            where = _WHERE[host][0 if check["interactive"] else 1]
            lines.append(f"{indent}  {host + ':':<12} {where}: {_commands_text(check)}")
    if check.get("note") and check["note"] not in ("skipped", "no-remote", "no-commit"):
        lines.append(f"{indent}  {'Also:':<12} {check['note']}")
    if check["meanwhile"]:
        lines.append(f"{indent}  {'Meanwhile:':<12} {check['meanwhile']}")
    return lines


def gesture(verb_args):
    return f"{_vd.python_command()} {_vd._q(HERE)} {verb_args}"


def decision_lines(sdlc_dir, remotes=(), why="no-remote"):
    """The printed DECISION for 'work is on but it cannot push': keep it on and fix the cause, or run
    local-only. Codex/Cursor relay this verbatim (no interactive question there); Claude Code's
    SKILL.md asks the user. Nothing here flips the setting -- only the gesture does."""
    where = printable(_vd._q(os.path.abspath(str(sdlc_dir))))
    cfg = printable(os.path.join(os.path.abspath(str(sdlc_dir)), "config.json"))
    cause = "this repository has no usable remote" if why == "no-remote" else "gh is not installed"
    lines = [f"  DECISION: work.enabled is ON, but {cause}, so no goal can be pushed or opened as a PR.",
             "    Keep ON  = one worktree + branch + PR per goal (needs a pushed remote and gh);",
             "               fix the cause with the commands above.",
             "    Turn OFF = the loop edits this checkout directly: no worktree, no branch, no push,",
             "               no PR; committing is yours, and your checkout is where goals run.",
             "    Turn it off with: " + printable(gesture(f"local-only {where}")),
             f"    or put this in {cfg} yourself: " + json.dumps({"work": {"enabled": False}})]
    for r in remotes:
        lines.append(f"    or use the existing remote '{printable(r)}': "
                     + printable(gesture(f"use-remote {where} {_vd._q(r)}")))
    return lines


def report_lines(checks, sdlc_dir=None, prefix="agrim-init"):
    bad = [c for c in checks if c["ok"] is not True and c.get("note") != "skipped"]
    skipped = [c for c in checks if c.get("note") == "skipped"]
    if not bad:
        head = f"{prefix}: preflight OK - " + ", ".join(c["name"] for c in checks if c["ok"])
        return [head] + [f"  (skipped {c['name']}: {c['detail'][9:]})" for c in skipped]
    lines = [f"{prefix}: preflight - {len(bad)} problem(s) the loop would otherwise hit at the first "
             "goal:"]
    for c in bad:
        lines += failure_lines(c)
    for c in skipped:
        lines.append(f"  (skipped {c['name']}: {c['detail'][9:]})")
    return lines


def blocking(checks):
    return [c for c in checks if c["ok"] is False]


# ---------------------------------------------------------------- the one message work.py raises

def no_remote_message(repo, remote, sdlc_dir, runner=None, timeout=None):
    """None when `remote` exists in `repo`; else the SAME failure block + decision /agrim-init
    prints, for `work.py start` to raise instead of git's raw `fatal: '<remote>' ...`."""
    runner = runner or real_runner
    timeout = call_timeout() if timeout is None else timeout
    check, remotes, _url = check_remote(str(repo), remote, runner, timeout)
    if check["ok"]:
        return None
    return "\n".join([f"preflight: {check['name']} is missing (checked because the fetch failed)"]
                     + failure_lines(check) + decision_lines(sdlc_dir, remotes))


# ---------------------------------------------------------------- config gestures

def _config_path(sdlc_arg):
    sdlc = pathlib.Path(os.path.abspath(sdlc_arg))
    if sdlc.is_symlink():
        raise ValueError(f"{printable(sdlc)} is a symlink; run this against the real .sdlc directory")
    path = sdlc / "config.json"
    if not path.is_file():
        raise ValueError(f"no config.json under {printable(sdlc)} -- run /agrim-init first")
    return sdlc, path


def set_local_only(sdlc_arg):
    _sdlc, path = _config_path(sdlc_arg)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    work = cfg.get("work") if isinstance(cfg.get("work"), dict) else {}
    work["enabled"] = False
    work["_enabled_why"] = ("set false by the user at preflight (preflight.py local-only): no usable "
                            "remote/gh, so the loop edits this checkout directly -- no worktree, "
                            "branch, push or PR. Turn it back on once a remote is pushed and gh is "
                            "logged in.")
    cfg["work"] = work
    _vd._atomic_write_json(path, cfg)
    return "work.enabled = false (the loop edits this checkout directly; no worktree, branch, push or PR)"


def set_remote(sdlc_arg, name, runner=None):
    sdlc, path = _config_path(sdlc_arg)
    runner = runner or real_runner
    rc, out = runner(["git", "remote"], str(sdlc.parent), call_timeout())
    remotes = {r.strip() for r in out.splitlines() if r.strip()} if rc == 0 else set()
    if name not in remotes:
        raise ValueError(f"no remote named '{printable(name)}' in {printable(sdlc.parent)} "
                         f"(remotes: {', '.join(sorted(printable(r) for r in remotes)) or 'none'})")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    work = cfg.get("work") if isinstance(cfg.get("work"), dict) else {}
    work["remote"] = name
    cfg["work"] = work
    _vd._atomic_write_json(path, cfg)
    return f"work.remote = {json.dumps(name)}"


USAGE = ("usage: preflight.py check [repo_root] [--sdlc <dir>] | local-only <sdlc_dir> | "
         "use-remote <sdlc_dir> <remote>")


def main(argv):
    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if args else 2
    verb = args[0]
    try:
        if verb == "local-only" and len(args) == 2:
            print("preflight: " + set_local_only(args[1]))
            return 0
        if verb == "use-remote" and len(args) == 3:
            print("preflight: " + set_remote(args[1], args[2]))
            return 0
    except (ValueError, OSError) as exc:
        print(f"preflight: REFUSED -- {printable(exc)}", file=sys.stderr)
        return 2
    if verb == "check":
        rest = args[1:]
        sdlc = None
        if "--sdlc" in rest:
            i = rest.index("--sdlc")
            if i + 1 >= len(rest):
                print(USAGE, file=sys.stderr)
                return 2
            sdlc = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        repo = rest[0] if rest else "."
        sdlc = sdlc or os.path.join(repo, ".sdlc")
        try:
            config = json.loads(pathlib.Path(sdlc, "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {"work": {"enabled": True}}          # no config yet: check what init will ship
        checks = preflight(repo, config)
        for line in report_lines(checks, sdlc, prefix="sigma"):
            print(line)
        return 1 if blocking(checks) else 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
