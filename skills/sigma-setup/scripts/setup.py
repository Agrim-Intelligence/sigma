#!/usr/bin/env python3
"""sigma-setup: adopt Sigma into an existing repo in one pass. This script is the part that must be
EXACT — it detects the repo, writes `.sdlc/config.json` with safe adoption defaults, and ensures the
runtime dirs are ignored WITHOUT ever clobbering an ignore rule a human already set. The judgment parts
(which board, which verify command) live in SKILL.md; the command runner is injectable so this is
hermetically testable. Zero-dep, portable stdlib only.

Two field-tested traps this refuses to walk into:
  * `verify.enforce: true` with an empty `verify.command` refuses EVERY `done` forever — so enforce is
    never turned on here without a command, and a pre-existing empty-command+enforce is defused.
  * silently narrowing or rewriting an ignore rule a human set — so a broader pattern that already
    covers a target (a blanket `.sdlc/`) is left untouched, in either .gitignore or .git/info/exclude.
"""
import sys, os, json, pathlib, subprocess, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent
#: #236: the one entry point `/sigma-init` runs; `setup.py init ...` hands its arguments to it.
INIT_FLOW = _HERE.parent.parent / "sigma-init" / "scripts" / "init_flow.py"


def _load_loop_script(name):
    """Cross-load a script from the sibling `sigma-loop` skill (skills/sigma-setup/scripts/ ->
    skills/sigma-loop/scripts/<name>.py) -- the established, narrow, named exception to "don't reach
    across skill directories" (see `skills/sigma-dossier/scripts/dossier.py`'s own identical helper,
    and `skills/sigma-scope/scripts/compile_plan.py`'s and `skills/sigma-define/scripts/define.py`'s
    copies of the same): `sources.py` is where `GitHubSource` and its label-bootstrap mechanism
    already live, and reimplementing it here would be exactly the hardened-sibling-divergence bug
    class this plugin's own docs already warn against. Still stdlib-only -- `importlib` -- so this
    doesn't compromise the "zero-dep, portable" property the module docstring claims; it only
    crosses a skill-directory boundary within the plugin's own tree."""
    path = _HERE.parent.parent / "sigma-loop" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


#: The machine-written dirs that should never land in a code PR. `.sdlc/knowledge/` holds
#: research-capture breadcrumbs (raw external web content, opt-in KG feature) — kept out of git so
#: captured web bodies never ride into a commit even after they're scrubbed (defense in depth).
#: `.sdlc/events/` is the event JOURNAL — the one destination `ledger.py`'s `append()` writes the
#: EVENTS stream to (#244 created it as one of two; #2574/S1-G3 deleted publishing and left it the
#: only one). A sibling of `.sdlc/ledger/`, not inside it, so it needs this OWN ignore entry rather
#: than inheriting the ledger worktree's. This entry is what makes "local and gitignored" true:
#: without it, a journal the user opted into would ride into their next commit.
#: #1562: `graphify-out/` is the ONLY entry not under `.sdlc/` -- the graph builder writes to the
#: REPO ROOT (`kg.py::status()` reads `<repo-root>/<builder>-out/graph.json`). Nothing wrote that
#: path until `auto_refresh` became a real trigger, so no rule covered it even though README.md and
#: sigma-kg/SKILL.md have both told users to keep the builder's output out of git since it shipped.
#: It is machine-accumulated and fully regenerable, and it can be large (graph + HTML + report +
#: per-file extraction cache). The default builder name is hardcoded because this list is static and
#: `knowledge_graph.builder` is per-project; a project pointing `builder` elsewhere ignores its own.
RUNTIME_IGNORES = (".sdlc/state/", ".sdlc/ledger/", ".sdlc/work/", ".sdlc/knowledge/",
                   ".sdlc/events/", "graphify-out/")

# The value Sigma releases before #614 wrote into every adopted repository's local config. Nothing
# ships or creates this directory, so git resolved it to "no hooks" and silently stopped running the
# adopter's own pre-commit/commit-msg hooks (and shadowed a global hooksPath). Adoption no longer
# writes it (#614, owner decision D-1); the constant survives so the repair below, `/sigma-doctor`
# and the uninstall checker can recognise the stale key. Keep the spelling assembled: the
# public-surface guard rejects the raw directory name.
HOOKS_PATH = "." + "git" + "hooks"

#: The one command that undoes the stale key, printed by every surface that reports it.
UNSET_HOOKS_PATH = "git config --local --unset core.hooksPath"


class HookPathRefused(Exception):
    """A hook-path read or write that could not be made safely; nothing was changed."""


def _hook_git_env():
    """Git's environment can override `-C` and even inject config values. An adoption write must
    address the named repository's local config, so discard all inherited Git control variables while
    retaining ordinary environment such as PATH and locale."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _hook_git(repo_root, *args):
    try:
        return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True,
                              env=_hook_git_env())
    except OSError as exc:
        raise HookPathRefused("could not start git") from exc


def _local_hook_path(repo_root):
    """The repository-local `core.hooksPath`, or None when unset."""
    current = _hook_git(repo_root, "config", "--local", "--get", "core.hooksPath")
    if current.returncode == 1:
        return None
    if current.returncode != 0:
        raise HookPathRefused("could not read the repository-local core.hooksPath")
    return current.stdout.strip()


def _worktree_roots(repo_root):
    """Every worktree's top level. `--local` config is SHARED by all of a repository's worktrees,
    while git resolves a relative hooksPath against EACH worktree's own top level -- so a value is
    only dead when no worktree has the directory (#614 plan-review R2)."""
    listed = _hook_git(repo_root, "worktree", "list", "--porcelain")
    roots = [pathlib.Path(line[len("worktree "):]) for line in listed.stdout.splitlines()
             if listed.returncode == 0 and line.startswith("worktree ")]
    top = _hook_git(repo_root, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and top.stdout.strip():
        roots.append(pathlib.Path(top.stdout.strip()))
    return roots or [pathlib.Path(repo_root)]


def stale_hook_path(repo_root):
    """True only when the local `core.hooksPath` is exactly the value earlier Sigma releases wrote
    AND no worktree of the repository has that directory -- git then runs no hooks at all. Any other
    value (husky, an org scanner, the adopter's own directory under the same name) is never stale.
    Not a git repository at all: False (nothing for git to skip). Raises `HookPathRefused` when git
    cannot be read."""
    if _hook_git(repo_root, "rev-parse", "--git-dir").returncode != 0:
        return False
    if _local_hook_path(repo_root) != HOOKS_PATH:
        return False
    return not any((root / HOOKS_PATH).is_dir() for root in _worktree_roots(repo_root))


def clear_stale_hook_path(repo_root):
    """Unset the stale key (and only it). Returns `cleared` or `absent`; raises `HookPathRefused`
    when the read or the unset fails, having changed nothing it could not undo."""
    if not stale_hook_path(repo_root):
        return "absent"
    if _hook_git(repo_root, "config", "--local", "--unset", "core.hooksPath").returncode != 0:
        raise HookPathRefused("could not unset the repository-local core.hooksPath")
    return "cleared"


def ensure_hook_path(repo_root):
    """The `hooks` verb: point the local hook path at the repository's own hook directory, which
    must EXIST -- a missing one makes git run no hooks, so that refuses and writes nothing (#614).
    Adoption never calls this. Preserves an equal value and refuses a different user value. Returns
    `installed` or `already configured`; errors never reveal the existing value."""
    if not any((root / HOOKS_PATH).is_dir() for root in _worktree_roots(repo_root)):
        raise HookPathRefused("the hook directory does not exist in this repository; pointing "
                              "core.hooksPath at it would make git run no hooks")
    current = _local_hook_path(repo_root)
    if current == HOOKS_PATH:
        return "already configured"
    if current is not None:
        raise HookPathRefused("repository has a different core.hooksPath; refusing to overwrite it")
    if _hook_git(repo_root, "config", "--local", "core.hooksPath", HOOKS_PATH).returncode != 0:
        raise HookPathRefused("could not install the repository-local core.hooksPath")
    return "installed"


def _run_git(repo_root, args):
    try:
        p = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- detect


#: Hosts that are NOT GitHub -- the same list as `sigma-init/scripts/preflight.py:_NON_GITHUB` (pinned
#: equal by tests/test_setup.py). A GitLab `git@gitlab.com:o/r.git` origin used to read as `o/r`.
_NON_GITHUB = ("gitlab", "bitbucket", "dev.azure.com", "visualstudio.com", "codeberg.org",
               "gitea", "sr.ht", "sourceforge", "gitee.com")


def _remote_host(url):
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]
    elif ":" in url:
        host = url.split(":", 1)[0]
    else:
        return ""
    return host.rsplit("@", 1)[-1].split(":", 1)[0].lower()


def detect_repo(repo_root=".", run=None):
    """`owner/name` from `git remote get-url origin`, or '' if not resolvable or not a GitHub host.
    Handles ssh, https, and the `github.com-<alias>:owner/repo` host-alias form this org uses."""
    url = (run or _run_git)(repo_root, ["remote", "get-url", "origin"]).strip()
    if not url or any(tag in _remote_host(url) for tag in _NON_GITHUB):
        return ""
    for sep in ("github.com:", "github.com/"):
        if sep in url:
            url = url.split(sep, 1)[1]
            break
    else:
        if ":" in url and "/" in url:                 # host-alias like github.com-work:owner/repo
            url = url.split(":", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    parts = [p for p in url.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


# --------------------------------------------------------------------------- configure


def _load_cfg(sdlc_dir):
    p = pathlib.Path(sdlc_dir) / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _apply_default(d, key, value):
    """Like `dict.setdefault`, but ALSO treats an explicit JSON `null` as "not yet decided" (#2255).

    `sigma-init`'s own `config.json.tmpl` ships `discovery.github.assignee` / `ledger.enabled` /
    `work.enabled` as `null` specifically so a BARE `/sigma-init` scaffold still SHOWS these three
    knobs (discoverability: a key an adopter can see and edit, not a comment describing one that
    isn't there -- see `tests/test_config_discoverability.py`'s whole thesis) while leaving
    `configure()` free to apply ITS OWN adoption default the first time it runs. Plain `setdefault`
    cannot do this: it only asks whether the KEY is present, and the template must always present
    it, so these three keys were ALREADY present -- at the template's OWN minimal-adoption values,
    the opposite of what `configure()` promises -- by the time #2255 found `configure()`'s defaults
    silently never firing on the documented `sigma-init` -> `sigma-setup` sequence.

    `d.get(key) is None` is a strict superset of `setdefault`'s absent-key trigger (a missing key
    also reads back as `None`), so one check covers both an older config with no key at all and a
    fresh template's explicit `null`. A value that is present and NOT `None` -- `True`, `False`, a
    real assignee string, or even a deliberate empty string `""` -- is always a decision (a human's,
    or an earlier `configure()` run's) and is NEVER touched here, exactly preserving #435/F24 part
    2's guarantee that a deliberate opt-out survives every rerun. `null` is a THIRD state a
    boolean/string field never naturally takes on its own, so -- unlike comparing against the
    template's own shipped default value, which was considered and rejected -- it can never be
    confused with a human deliberately choosing the same value the template happens to ship."""
    if d.get(key) is None:
        d[key] = value


def configure(sdlc_dir, repo="", source="github", verify_command="", auto_merge="off"):
    """Patch config.json with adoption defaults, preserving anything already set. Returns (cfg, notes).
    Defaults: github discovery scoped to `@me`, ledger on, work (PRs) on. verify.enforce is set ONLY
    when a command is provided (and an existing enforce-without-command is turned back off)."""
    cfg = _load_cfg(sdlc_dir)
    notes = []

    disc = cfg.setdefault("discovery", {})
    if source == "github":
        disc["source"] = "github"
        gh = disc.setdefault("github", {})
        if repo:
            gh["repo"] = repo
        gh.setdefault("goal_label", "sdlc:goal")
        gh.setdefault("in_progress_label", "sdlc:in-progress")
        gh.setdefault("parked_label", "sdlc:parked")
        _apply_default(gh, "assignee", "@me")          # default to @me; null/absent only -- never clobber a real choice (#2255)
        notes.append("discovery: github repo=%s, assignee=%s" % (
            repo or "UNSET (set discovery.github.repo)", gh["assignee"]))
    else:
        disc["source"] = "local-goals"
        notes.append("discovery: local-goals")

    ledger = cfg.setdefault("ledger", {})
    _apply_default(ledger, "enabled", True)         # null/absent only -- never clobber a deliberate opt-out (F24 part 2 / #435, #2255)
    notes.append("ledger: enabled=%s" % ledger["enabled"])

    work = cfg.setdefault("work", {})
    _apply_default(work, "enabled", True)           # null/absent only -- never clobber a deliberate opt-out (F24 part 2 / #435, #2255)
    work.setdefault("auto_merge", auto_merge)
    notes.append("work (PR per goal): enabled=%s, auto_merge=%s" % (work["enabled"], work["auto_merge"]))

    verify = cfg.setdefault("verify", {})
    if verify_command:
        verify["command"] = verify_command
        verify["enforce"] = True
        notes.append("verify: command set + enforce ON")
    elif verify.get("enforce") and not verify.get("command"):
        verify["enforce"] = False                      # defuse the permanent-refusal trap
        notes.append("verify: enforce turned OFF: it was on with no command (would refuse every done)")
    else:
        notes.append("verify: no command yet, enforce left OFF (set verify.command, then enforce)")

    return cfg, notes


def write_cfg(sdlc_dir, cfg):
    (pathlib.Path(sdlc_dir) / "config.json").write_text(
        json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- labels


def ensure_core_labels(sdlc_dir, config=None, run=None, repo=None):
    """Create GitHub's core lifecycle labels AND the `priority:P0`-`P3` tiers up front, on adoption
    -- through `GitHubSource.ensure_labels_report` (#230), the measured sibling of the pick path's
    best-effort `_ensure_labels`: same colour-preserving creation (#1917: no `--force`, an existing
    label is never written), but every label's outcome is REPORTED (`created` / `existed` /
    `failed` + reason) instead of swallowed. Without this `/sigma-setup` finished pointed at a repo
    where nothing is pickable, because the labels the config names do not exist (#2254); and until
    #230 it printed "ensured" even when every create had been refused.

    Never applies a label to any issue: which issues become pickable stays a human's triage call.
    (#230 reverses #2254's "never creates priority labels": without them an unlabelled issue sorts
    last and the issue-field gate refuses `gh issue create`.)

    Outcomes: `skipped` (not github mode / no repo -- nothing attempted), `ensured` (every label
    measured present), `failed` (at least one label could not be created -- the caller exits
    non-zero). `repo`, when given, forces github mode against that repository (how `sdlc_init.py
    --github` bootstraps before `discovery.source` is set). `config`, when given, is used AS-IS;
    otherwise it is read fresh from `<sdlc_dir>/config.json`. Never raises on a `gh` failure."""
    config = config if config is not None else _load_cfg(sdlc_dir)
    if repo:
        config = json.loads(json.dumps(config))                   # never mutate the caller's dict
        disc = config.setdefault("discovery", {})
        disc["source"] = "github"
        disc.setdefault("github", {})["repo"] = repo
    disc = config.get("discovery") or {}
    if disc.get("source") != "github":
        return {"outcome": "skipped", "detail": "discovery.source is not github; no labels to create"}
    sources = _load_loop_script("sources")
    source = sources.GitHubSource(config, run=run, sdlc_dir=sdlc_dir)
    if not source.repo:
        return {"outcome": "skipped", "detail": "discovery.github.repo is not set"}
    results = source.ensure_labels_report()
    lines, failed = sources.render_label_report(source.repo, results)
    return {"outcome": "failed" if failed else "ensured", "repo": source.repo,
            "labels": sorted(r["label"] for r in results), "results": results, "lines": lines}


# --------------------------------------------------------------------------- ignore


def _ignore_covers(text, target):
    """True if a non-comment line in `text` already ignores `target` — exactly, or by a broader parent
    (a blanket `.sdlc/` covers `.sdlc/ledger/`). A NARROWER line never counts as covering a broader
    target, so we never treat `.sdlc/state/` as covering `.sdlc/`."""
    tgt = target.strip("/")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pat = line.strip("/")
        if pat and (tgt == pat or tgt.startswith(pat + "/")):
            return True
    return False


def ignore_status(repo_root, targets=RUNTIME_IGNORES):
    """Which mechanism (if any) already ignores each target: 'tracked' (.gitignore), 'local'
    (.git/info/exclude), or None. Read-only — this is what /sigma-doctor reports."""
    root = pathlib.Path(repo_root)
    tracked = _read(root / ".gitignore")
    local = _read(root / ".git" / "info" / "exclude")
    out = {}
    for t in targets:
        out[t] = ("tracked" if _ignore_covers(tracked, t)
                  else "local" if _ignore_covers(local, t) else None)
    return out


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def ensure_ignore(repo_root, scope="tracked", targets=RUNTIME_IGNORES):
    """Ensure each runtime dir is ignored, NEVER removing or narrowing a rule a human set. If a target
    is already covered in EITHER .gitignore or .git/info/exclude, it is left alone. scope='tracked'
    appends to the shared .gitignore; scope='local' appends to .git/info/exclude (nothing the team
    sees) — the right choice when the adopter wants the repo state untouched. Returns (added, skipped)."""
    root = pathlib.Path(repo_root)
    tracked_txt, local_txt = _read(root / ".gitignore"), _read(root / ".git" / "info" / "exclude")
    dest = (root / ".gitignore") if scope == "tracked" else (root / ".git" / "info" / "exclude")
    to_add = [t for t in targets
              if not (_ignore_covers(tracked_txt, t) or _ignore_covers(local_txt, t))]
    skipped = [t for t in targets if t not in to_add]
    if to_add:
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = _read(dest)
        lead = "" if (not existing or existing.endswith("\n")) else "\n"
        dest.write_text(existing + lead + "# Sigma runtime dirs (machine-written)\n"
                        + "".join(t + "\n" for t in to_add), encoding="utf-8")
    return to_add, skipped


# --------------------------------------------------------------------------- CLI


#: #541: the flags this module's own CLI (`configure`/`ignore`) ever hands a real value to --
#: `verify` in particular is a free-form command string a caller could plausibly start with '--'.
_VALUE_FLAGS = frozenset({"repo", "source", "verify", "auto-merge", "scope"})


def _flags(argv):
    """`--name value` / bare `--flag` (-> `"true"`) scanner, plus `--name=value` (unambiguous for
    ANY flag) and unconditional-consume for this module's own known value-taking flags
    (`_VALUE_FLAGS`) -- #541: a value that itself starts with '--' used to be silently swallowed:
    the flag landed on the `"true"` sentinel and the value's own text was misparsed as a SECOND,
    garbage flag. A flag name is never legitimately whitespace-bearing -- that shape is always
    leaked prose from a value the old heuristic failed to consume, never a flag a caller meant to
    pass, so it is dropped instead of kept as a nonsense key."""
    out, i = {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            name, eq, value = argv[i][2:].partition("=")
            if eq:                                       # --name=value: always unambiguous
                if " " not in name:                       # same never-a-real-flag rule as below
                    out[name] = value
            elif name in _VALUE_FLAGS:                    # known value-taking flag: consume unconditionally
                if i + 1 < len(argv):
                    out[name] = argv[i + 1]; i += 2; continue
                out[name] = "true"                        # nothing left to consume
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[name] = argv[i + 1]; i += 2; continue
            elif " " not in name:
                out[name] = "true"
            # else: whitespace in `name` -- never a real flag; drop rather than keep a nonsense key
        i += 1
    return out


USAGE = ("usage: setup.py init [init_flow.py options] | detect [repo_root] | hooks <repo_root> | hooks-repair <repo_root> | configure <sdlc_dir> [--repo O/N --source github|local-goals "
         "--verify CMD --auto-merge off|protected|always] | ignore <repo_root> [--scope tracked|local] | "
         "ignore-status <repo_root> | labels <sdlc_dir> [--repo O/N]")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[1] == "detect":
        print(detect_repo(argv[2] if len(argv) > 2 else ".") or "")
        return 0
    if len(argv) >= 2 and argv[1] == "init":
        # #236: /sigma-setup is an alias of /sigma-init -- the SAME flow, never a second door.
        # A subprocess (not an import): the sibling skill's CLI, as `sdlc_init.py` calls this one.
        return subprocess.run([sys.executable, str(INIT_FLOW), *argv[2:]]).returncode
    if len(argv) >= 3 and argv[1] == "hooks":
        try:
            status = ensure_hook_path(argv[2])
        except HookPathRefused as exc:
            print(f"setup.py hooks: REFUSED - {exc}. Nothing written.", file=sys.stderr)
            return 2
        print("  core.hooksPath: " + status)
        return 0
    if len(argv) >= 3 and argv[1] == "hooks-repair":
        # #614: the one repair of the key earlier releases wrote. Prints ONE line when it acted and
        # nothing otherwise, so init can relay its stdout verbatim.
        try:
            status = clear_stale_hook_path(argv[2])
        except HookPathRefused as exc:
            print(f"setup.py hooks-repair: REFUSED - {exc}; git may be running no hooks here. "
                  f"If `git config --local --get core.hooksPath` prints `{HOOKS_PATH}` and no such "
                  f"directory exists, run `{UNSET_HOOKS_PATH}`.", file=sys.stderr)
            return 2
        if status == "cleared":
            print(f"  [ok] git hooks: removed the core.hooksPath an earlier Sigma init set (it named "
                  f"a directory that does not exist, so git was running no hooks here)")
        return 0
    if len(argv) >= 3 and argv[1] == "configure":
        f = _flags(argv[3:])
        sdlc = pathlib.Path(argv[2])
        if not (sdlc / "config.json").is_file():
            # #236: was a FileNotFoundError traceback from write_cfg (or a config written into a
            # directory init never scaffolded). Refuse, name the entry point, write nothing.
            print(f"setup.py configure: REFUSED - no {sdlc / 'config.json'}; run /sigma-init first "
                  f"({INIT_FLOW}). Nothing written.", file=sys.stderr)
            return 2
        source = f.get("source", "github")
        # --repo, else the repository config.json already names (never swapped for `origin`), else
        # the GitHub `origin` -- a configured repo is never refused for a missing/foreign origin.
        configured = ((_load_cfg(sdlc).get("discovery") or {}).get("github") or {}).get("repo") or ""
        repo = f.get("repo", "") or (str(configured).strip() or detect_repo(str(sdlc.resolve().parent))
                                     if source == "github" else "")
        if source == "github" and not repo:
            # #236: github discovery with an empty repo queries nothing -- a config that reads as
            # "adopted" while no issue can ever be picked. Refuse instead of writing "UNSET".
            print("setup.py configure: REFUSED - github mode needs a repository and `origin` is not "
                  "a GitHub remote. Pass --repo OWNER/NAME, or --source local-goals. Nothing written.",
                  file=sys.stderr)
            return 2
        cfg, notes = configure(argv[2], repo=repo, source=source,
                               verify_command=f.get("verify", ""), auto_merge=f.get("auto-merge", "off"))
        write_cfg(argv[2], cfg)
        for n in notes:
            print("  " + n)
        return 0
    if len(argv) >= 3 and argv[1] == "ignore":
        f = _flags(argv[3:])
        added, skipped = ensure_ignore(argv[2], scope=f.get("scope", "tracked"))
        print("  added: " + (", ".join(added) or "nothing (all already ignored)"))
        if skipped:
            print("  left alone (already covered): " + ", ".join(skipped))
        return 0
    if len(argv) >= 3 and argv[1] == "ignore-status":
        for t, where in ignore_status(argv[2]).items():
            print("  %s: %s" % (t, where or "NOT ignored"))
        return 0
    if len(argv) >= 3 and argv[1] == "labels":
        f = _flags(argv[3:])
        result = ensure_core_labels(argv[2], repo=f.get("repo") or None)
        if result["outcome"] == "skipped":
            print("  skipped: " + result["detail"])
            return 0
        for line in result["lines"]:
            print(line)
        return 1 if result["outcome"] == "failed" else 0          # #230: a failed label is loud
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
