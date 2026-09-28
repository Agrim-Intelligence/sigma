#!/usr/bin/env python3
"""agrim-setup: adopt Sigma into an existing repo in one pass. This script is the part that must be
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
import sys, json, pathlib, subprocess, importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load_loop_script(name):
    """Cross-load a script from the sibling `agrim-loop` skill (skills/agrim-setup/scripts/ ->
    skills/agrim-loop/scripts/<name>.py) -- the established, narrow, named exception to "don't reach
    across skill directories" (see `skills/agrim-dossier/scripts/dossier.py`'s own identical helper,
    and `skills/agrim-scope/scripts/compile_plan.py`'s and `skills/agrim-define/scripts/define.py`'s
    copies of the same): `sources.py` is where `GitHubSource` and its label-bootstrap mechanism
    already live, and reimplementing it here would be exactly the hardened-sibling-divergence bug
    class this plugin's own docs already warn against. Still stdlib-only -- `importlib` -- so this
    doesn't compromise the "zero-dep, portable" property the module docstring claims; it only
    crosses a skill-directory boundary within the plugin's own tree."""
    path = _HERE.parent.parent / "agrim-loop" / "scripts" / f"{name}.py"
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
#: agrim-kg/SKILL.md have both told users to keep the builder's output out of git since it shipped.
#: It is machine-accumulated and fully regenerable, and it can be large (graph + HTML + report +
#: per-file extraction cache). The default builder name is hardcoded because this list is static and
#: `knowledge_graph.builder` is per-project; a project pointing `builder` elsewhere ignores its own.
RUNTIME_IGNORES = (".sdlc/state/", ".sdlc/ledger/", ".sdlc/work/", ".sdlc/knowledge/",
                   ".sdlc/events/", "graphify-out/")


def _run_git(repo_root, args):
    try:
        p = subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- detect


def detect_repo(repo_root=".", run=None):
    """`owner/name` from `git remote get-url origin`, or '' if not resolvable. Handles ssh, https, and
    the `github.com-<alias>:owner/repo` host-alias form this org uses."""
    url = (run or _run_git)(repo_root, ["remote", "get-url", "origin"]).strip()
    if not url:
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

    `agrim-init`'s own `config.json.tmpl` ships `discovery.github.assignee` / `ledger.enabled` /
    `work.enabled` as `null` specifically so a BARE `/agrim-init` scaffold still SHOWS these three
    knobs (discoverability: a key an adopter can see and edit, not a comment describing one that
    isn't there -- see `tests/test_config_discoverability.py`'s whole thesis) while leaving
    `configure()` free to apply ITS OWN adoption default the first time it runs. Plain `setdefault`
    cannot do this: it only asks whether the KEY is present, and the template must always present
    it, so these three keys were ALREADY present -- at the template's OWN minimal-adoption values,
    the opposite of what `configure()` promises -- by the time #2255 found `configure()`'s defaults
    silently never firing on the documented `agrim-init` -> `agrim-setup` sequence.

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


def ensure_core_labels(sdlc_dir, config=None, run=None):
    """Create GitHub's core lifecycle labels up front, on adoption -- the same idempotent,
    colour-preserving mechanism (`GitHubSource.ensure_labels`, #1917: no `--force`, so an existing
    label's colour is never repainted) `loop.py`'s own pick path already calls before every
    claim/park. Without this, `/agrim-setup` finishes with a fully-wired config pointed at a real
    repo, but a repo where NOTHING is pickable yet -- because none of the labels the config just
    referenced by name actually exist -- discoverable only later via a `/agrim-doctor` warning most
    adopters never think to check right after setup. Found live adopting Sigma into a real repo
    (issue #2254).

    Deliberately does NOT create `priority:P<n>` labels and NEVER applies a label to any issue --
    `GitHubSource._LABEL_COLORS` (the table `ensure_labels` iterates) already excludes both, so this
    stays correct by construction rather than by a scoping check written here: a board keeping
    priority on a Projects-v2 field only, by deliberate repo policy, is untouched, and deciding
    which issues become pickable stays a human's triage call, not a mechanical setup step.

    No-op (`outcome: "skipped"`) when `discovery.source` isn't `"github"` or no
    `discovery.github.repo` is configured yet -- mirrors `configure()`'s own local-goals/no-repo
    early-outs. Never raises: `ensure_labels()`'s own per-label `except Exception: pass` is
    unchanged, so a `gh` failure here is silent and best-effort, exactly like every other call site.
    `config`, when given, is used AS-IS (so a caller can pass the just-written in-memory `cfg` from
    `configure()` without a round-trip through disk); otherwise it's read fresh from
    `<sdlc_dir>/config.json`."""
    config = config if config is not None else _load_cfg(sdlc_dir)
    disc = config.get("discovery") or {}
    if disc.get("source") != "github":
        return {"outcome": "skipped", "detail": "discovery.source is not github; no labels to create"}
    sources = _load_loop_script("sources")
    source = sources.GitHubSource(config, run=run, sdlc_dir=sdlc_dir)
    if not source.repo:
        return {"outcome": "skipped", "detail": "discovery.github.repo is not set"}
    source.ensure_labels()
    labels = sorted({getattr(source, attr) for attr, _ in source._LABEL_COLORS})
    return {"outcome": "ensured", "repo": source.repo, "labels": labels}


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
    (.git/info/exclude), or None. Read-only — this is what /agrim-doctor reports."""
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


USAGE = ("usage: setup.py detect [repo_root] | configure <sdlc_dir> [--repo O/N --source github|local-goals "
         "--verify CMD --auto-merge off|protected|always] | ignore <repo_root> [--scope tracked|local] | "
         "ignore-status <repo_root> | labels <sdlc_dir>")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[1] == "detect":
        print(detect_repo(argv[2] if len(argv) > 2 else ".") or "")
        return 0
    if len(argv) >= 3 and argv[1] == "configure":
        f = _flags(argv[3:])
        cfg, notes = configure(argv[2], repo=f.get("repo", ""), source=f.get("source", "github"),
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
        result = ensure_core_labels(argv[2])
        if result["outcome"] == "skipped":
            print("  skipped: " + result["detail"])
        else:
            print("  ensured on %s: %s" % (result["repo"], ", ".join(result["labels"])))
        return 0
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
