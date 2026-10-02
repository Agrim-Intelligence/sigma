#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""List every local and configured holder of a repository's name, and print the owner handover sequence.

USAGE:
  handover_check.py check --old OWNER/NAME [--new OWNER/NAME] [--repo-id N] [--clone PATH ...]
                    [--scan-root DIR ...] [--max-depth N] [--max-repos N] [--claude-config DIR]
                    [--offline] [--json FILE]
  handover_check.py sequence --old OWNER/NAME --new OWNER/NAME [--throwaway OWNER/NAME]
EXIT: 0 = no blocking finding; 1 = at least one blocking finding; 2 = refusal (one stderr line).

`check` is STRICTLY read-only: it reads git configuration, `.sdlc/config.json` files, tracked text,
Claude plugin records, the process list and (unless `--offline`) names-only GitHub REST lists through
the injected `run`. Its one write is the `--json` evidence file, created once, 0600, outside every
repository. `sequence` is pure text and runs nothing.

Not covered, and said so in docs/name-handover.md: other machines, collaborators' clones, the board's
linked repository, schedulers that set GH_REPO, Codex marketplace records. A repository root found by
`--scan-root` is not descended into (its linked worktrees share its git directory); the depth cap is
silent, the repository-count cap is reported as a blocking `truncated` finding.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

SCHEMA = "sigma.handover-check/v1"
HEARTBEAT_FRESH_SECONDS = 600
GIT_TIMEOUT = 60
PS_TIMEOUT = 20
LSOF_TIMEOUT = 10
_NOT_A_REPO = "fatal: not a git repository (or any of the parent directories)"
#: Removed from every git child's environment: each can point git at another repository, or stop
#: its discovery early, so a path inside a work tree would read as outside one.
_GIT_SCRUB = ("GIT_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES", "GIT_INDEX_FILE",
              "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
              "GIT_NAMESPACE")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$")

_URL_RE = re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$",
                     re.I)
_SCP_RE = re.compile(r"^(?:[^@/\s]+@)?github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", re.I)
_WRITER_RE = re.compile(r"(?:^|/)(?:watch_daemon|loop)\.py$")
_PS_ROW_RE = re.compile(r"^\s*(\d+)\s+(\S+)\s+(.*)$")
_GREP_RE = re.compile(r"([^\0\n]+)\0(\d+)\0([^\n]*)\n")
_HTTP_RE = re.compile(r"HTTP (\d{3})")
_WORKFLOW_RE = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$")

#: kind, endpoint suffix, key holding the list in the response (None = the response is the list).
_REST = (("rest-secret", "actions/secrets", "secrets"),
         ("rest-variable", "actions/variables", "variables"),
         ("rest-org-secret", "actions/organization-secrets", "secrets"),
         ("rest-org-variable", "actions/organization-variables", "variables"),
         ("rest-hook", "hooks", None),
         ("rest-deploy-key", "keys", None),
         ("rest-environment", "environments", "environments"))
#: Config keys whose value names a remote, whose URL is then classified.
_REMOTE_KEYS = ("work.remote", "ledger.remote", "knowledge_graph.sync.remote")
_HOLDER_KINDS = frozenset(["remote-url", "remote-pushurl", "url-rewrite", "config-repo",
                           "config-upstream", "config-remote"])


class Refused(Exception):
    """A loud stop: exit 2, one stderr line with a code, nothing on stdout."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, detail


# ------------------------------------------------------------------------------ running things

def _real_run(args, input_text=None, timeout=60):
    """-> (rc, stdout, stderr). A timeout is rc 124, a binary that cannot be run rc 127. git children
    get `_GIT_SCRUB` removed, `GIT_DISCOVERY_ACROSS_FILESYSTEM=1` and `LC_ALL=C`."""
    args = [str(a) for a in args]
    env = None
    if args and args[0] == "git":
        env = {k: v for k, v in os.environ.items() if k not in _GIT_SCRUB}
        env["GIT_DISCOVERY_ACROSS_FILESYSTEM"] = "1"
        env["LC_ALL"] = "C"
    feed = {"input": input_text} if input_text is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env, **feed)
    except subprocess.TimeoutExpired:
        return 124, "", "%s: timed out after %ss" % (args[0], timeout)
    except OSError:
        return 127, "", "%s: cannot be run (is it installed?)" % args[0]
    return proc.returncode, proc.stdout, proc.stderr


def _platform_is_windows():
    return os.name == "nt"


def _tilde(text):
    """Human output shows $HOME paths as ~/..."""
    home = os.environ.get("HOME", "")
    if len(home) > 1:
        text = text.replace(home + os.sep, "~" + os.sep)
        if text == home:
            text = "~"
    return text


# ------------------------------------------------------------------------------ names

def _check_slug(value, what):
    if not _REPO_RE.match(value or ""):
        raise Refused("bad-slug", "%s is not OWNER/NAME" % what)
    return value


def _url_slug(url):
    """-> 'owner/name' for a GitHub remote URL (https, ssh://, scp-like SSH), else None."""
    url = (url or "").strip()
    for rx in (_URL_RE, _SCP_RE):
        m = rx.match(url)
        if m:
            return "%s/%s" % (m.group(1), m.group(2))
    return None


def _any_slug(value):
    """-> 'owner/name' from a plain slug or a URL."""
    value = (value or "").strip()
    if value.endswith(".git"):
        value = value[:-4]
    if _REPO_RE.match(value):
        return value
    return _url_slug(value)


def _class(slug, old, new):
    if not slug:
        return "absent"
    low = slug.lower()
    if low == old.lower():
        return "old"
    if new and low == new.lower():
        return "new"
    return "other"


def _finding(kind, location, field, value_class, blocking, note=""):
    return {"kind": kind, "location": location, "field": field, "value_class": value_class,
            "blocking": bool(blocking), "note": note}


class Sink(object):
    """The findings, without duplicates (a global config is read once per repository)."""

    def __init__(self):
        self.items, self._seen = [], set()

    def add(self, finding):
        key = (finding["kind"], finding["location"], finding["field"], finding["value_class"],
               finding["note"])
        if key not in self._seen:
            self._seen.add(key)
            self.items.append(finding)


# ------------------------------------------------------------------------------ git helpers

def _refuse_inside_work_tree(path, code, run):
    """Refuse `path` inside ANY git work tree, failing closed: the only accepted answer is git's own
    `not a git repository (or any of the parent directories)`."""
    try:
        probe = os.path.realpath(os.path.abspath(str(path)))
        while not os.path.isdir(probe) and os.path.dirname(probe) != probe:
            probe = os.path.dirname(probe)
    except (OSError, ValueError):
        raise Refused("work-tree-unknown", "cannot resolve the path")
    rc, _out, err = run(["git", "-C", probe, "rev-parse", "--show-toplevel"], None, GIT_TIMEOUT)
    if rc == 0:
        raise Refused(code, "the path resolves inside a git work tree; keep it outside every repository")
    if rc == 128 and (err or "").startswith(_NOT_A_REPO):
        return
    raise Refused("work-tree-unknown", "cannot tell whether the path is inside a git work tree "
                  "(git exit %d)" % rc)


def _git_ids(path, run):
    """-> (common_dir, top_level) as real paths, or None when `path` is not a readable work tree."""
    rc, out, _err = run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir",
                         "--show-toplevel"], None, GIT_TIMEOUT)
    lines = out.splitlines()
    if rc == 0 and len(lines) >= 2:
        return os.path.realpath(lines[0]), os.path.realpath(lines[1])
    rc, out, _err = run(["git", "-C", path, "rev-parse", "--git-common-dir", "--show-toplevel"],
                        None, GIT_TIMEOUT)
    lines = out.splitlines()
    if rc == 0 and len(lines) >= 2:
        return os.path.realpath(os.path.join(path, lines[0])), os.path.realpath(lines[1])
    return None


def _worktree_roots(top, run):
    rc, out, _err = run(["git", "-C", top, "worktree", "list", "--porcelain"], None, GIT_TIMEOUT)
    roots = []
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = os.path.realpath(line[len("worktree "):])
                if os.path.isdir(path):
                    roots.append(path)
    return roots


def _read_git_config(top, run):
    """-> (entries, ok); entries are (origin, key, value) for remote urls and url rewrites."""
    rc, out, _err = run(["git", "-C", top, "config", "--null", "--show-origin", "--get-regexp",
                         r"^(remote\..+\.(url|pushurl)|url\..+\.(insteadof|pushinsteadof))$"],
                        None, GIT_TIMEOUT)
    if rc == 1:
        return [], True
    if rc != 0:
        return [], False
    parts = out.split("\0")
    entries = []
    for i in range(0, len(parts) - 1, 2):
        origin, pair = parts[i], parts[i + 1]
        key, _, value = pair.partition("\n")
        entries.append((origin, key, value))
    return entries, True


def _config_location(origin, common, top):
    """A setting kept in the repository's own config is located at the checkout; any other file
    (a global or included one) is located at that file."""
    if not origin.startswith("file:"):
        return origin
    path = origin[len("file:"):]
    path = os.path.realpath(path if os.path.isabs(path) else os.path.join(top, path))
    if path == common or path.startswith(common + os.sep):
        return top
    return path


# ------------------------------------------------------------------------------ discovery

def _walk(root, max_depth, max_repos, sink):
    """-> (repository paths, truncated). Depth and repository caps; no symlinks; `.git` marks a
    repository and is never entered; a repository root is not descended into."""
    found, truncated = [], False
    root = os.path.realpath(root)
    base = len(root.rstrip(os.sep).split(os.sep))

    def onerror(exc):
        sink.add(_finding("unreadable", str(getattr(exc, "filename", None) or root), "walk", "absent",
                          True, "cannot read: %s" % (getattr(exc, "strerror", None) or "error")))

    for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
        dirnames.sort()
        if ".git" in dirnames or ".git" in filenames:
            if len(found) >= max_repos:
                truncated = True
                break
            found.append(dirpath)
            dirnames[:] = []
            continue
        depth = len(dirpath.rstrip(os.sep).split(os.sep)) - base
        if depth >= max_depth:
            dirnames[:] = []
    return found, truncated


# ------------------------------------------------------------------------------ the check

def _json_doc(args, findings, rest, truncated):
    blocking = sum(1 for f in findings if f["blocking"])
    return {"schema": SCHEMA, "old": args.old, "new": args.new, "repo_id": args.repo_id,
            "rest": rest, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "findings": findings,
            "counts": {"blocking": blocking, "informational": len(findings) - blocking},
            "caps": {"max_depth": args.max_depth, "max_repos": args.max_repos, "truncated": truncated}}


def _dig(doc, dotted):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _text_re(old):
    return re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(old) + r"(?![A-Za-z0-9_-])", re.I)


def _process_cwd(pid, run):
    """The working directory of a process, read-only; any failure -> None."""
    try:
        proc_path = "/proc/%s/cwd" % pid
        if os.path.isdir("/proc/self"):
            return os.readlink(proc_path)
    except OSError:
        return None
    try:
        rc, out, _err = run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], None, LSOF_TIMEOUT)
    except OSError:
        return None
    if rc != 0:
        return None
    for line in out.splitlines():
        if line.startswith("n") and len(line) > 1:
            return line[1:]
    return None


def _within(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _scan_repositories(args, run, sink):
    """Discovery plus the per-repository and per-checkout readings. -> (repos, current_top, truncated)."""
    repos = {}      # common dir -> {"roots": [...], "remotes": {name: [url]}}
    order = []

    def add(common, top):
        entry = repos.get(common)
        if entry is None:
            entry = repos[common] = {"roots": [], "remotes": {}}
            order.append(common)
        if top not in entry["roots"]:
            entry["roots"].append(top)

    current_top = None
    ids = _git_ids(os.getcwd(), run)
    if ids:
        current_top = ids[1]
        add(ids[0], ids[1])
        for root in _worktree_roots(ids[1], run):
            add(ids[0], root)
    for clone in args.clone:
        if not os.path.isdir(clone):
            raise Refused("clone-unreadable", "cannot read the clone %s" % _tilde(str(clone)))
        got = _git_ids(clone, run)
        if not got:
            raise Refused("clone-unreadable", "not a readable git work tree: %s" % _tilde(str(clone)))
        add(got[0], got[1])
    truncated = False
    for root in args.scan_root:
        found, cut = _walk(root, args.max_depth, args.max_repos, sink)
        truncated = truncated or cut
        for path in found:
            got = _git_ids(path, run)
            if got:
                add(got[0], got[1])
            else:
                sink.add(_finding("unreadable", path, "git", "absent", True,
                                  "git cannot read this repository"))
    if truncated:
        sink.add(_finding("truncated", os.path.realpath(args.scan_root[-1]), "max-repos", "absent", True,
                          "not every clone was seen"))
    return repos, order, current_top, truncated


def _read_remotes(args, repos, order, run, sink):
    for common in order:
        entry = repos[common]
        top = entry["roots"][0]
        entries, ok = _read_git_config(top, run)
        if not ok:
            sink.add(_finding("unreadable", top, "git config", "absent", True,
                              "git config could not be read"))
            continue
        for origin, key, value in entries:
            m = re.match(r"^remote\.(.+)\.(url|pushurl)$", key)
            if m:
                cls = _class(_url_slug(value), args.old, args.new)
                if m.group(2) == "url":
                    entry["remotes"].setdefault(m.group(1), []).append(value)
                if cls in ("old", "new"):
                    kind = "remote-url" if m.group(2) == "url" else "remote-pushurl"
                    sink.add(_finding(kind, _config_location(origin, common, top), key, cls,
                                      cls == "old"))
                continue
            m = re.match(r"^url\.(.+)\.(insteadof|pushinsteadof)$", key)
            if m:
                classes = (_class(_any_slug(m.group(1)), args.old, args.new),
                           _class(_any_slug(value), args.old, args.new))
                cls = "old" if "old" in classes else ("new" if "new" in classes else None)
                if cls:
                    sink.add(_finding("url-rewrite", _config_location(origin, common, top), key, cls,
                                      cls == "old"))


def _read_sdlc_configs(args, repos, order, sink):
    """-> the set of checkout roots that hold an old or new name."""
    holders = set()
    for common in order:
        entry = repos[common]
        repo_holds = any(f["kind"] in _HOLDER_KINDS and f["value_class"] in ("old", "new")
                         and f["location"] in entry["roots"] for f in sink.items)
        if repo_holds:
            holders.update(entry["roots"])
        for root in entry["roots"]:
            path = os.path.join(root, ".sdlc", "config.json")
            if not os.path.isfile(path):
                continue
            try:
                doc = _load_json(path)
            except (OSError, ValueError):
                sink.add(_finding("unreadable", path, "config", "absent", True,
                                  "cannot be read as JSON"))
                continue
            for kind, dotted in (("config-repo", "discovery.github.repo"),
                                 ("config-upstream", "ledger.handoff.upstream_repo")):
                value = _dig(doc, dotted)
                if isinstance(value, str):
                    cls = _class(_any_slug(value), args.old, args.new)
                    if cls in ("old", "new"):
                        sink.add(_finding(kind, path, dotted, cls, cls == "old"))
                        holders.add(root)
            for dotted in _REMOTE_KEYS:
                name = _dig(doc, dotted)
                if not isinstance(name, str):
                    continue
                classes = [_class(_url_slug(u), args.old, args.new) for u in entry["remotes"].get(name, [])]
                cls = "old" if "old" in classes else ("new" if "new" in classes else None)
                if cls:
                    sink.add(_finding("config-remote", path, dotted, cls, cls == "old",
                                      "resolved through the named remote"))
                    holders.add(root)
            if (_dig(doc, "discovery.github.project.owner") is not None
                    or _dig(doc, "discovery.github.project.number") is not None):
                sink.add(_finding("config-project", path, "discovery.github.project", "absent", False,
                                  "holds no repository name"))
    return holders


def _heartbeats(repos, order, holders, sink):
    now = time.time()
    for common in order:
        for root in repos[common]["roots"]:
            path = os.path.join(root, ".sdlc", "state", "watch.heartbeat")
            try:
                age = max(0, int(now - os.stat(path).st_mtime))
            except OSError:
                continue
            fresh = age <= HEARTBEAT_FRESH_SECONDS
            blocking = fresh and root in holders
            note = "age %ds; %s" % (age, "fresh, a writer may be running here" if fresh else "stale")
            if fresh and not blocking:
                note += "; this checkout holds neither name"
            sink.add(_finding("heartbeat", path, "watch.heartbeat", "absent", blocking, note))


def _tracked_text(args, current_top, run, sink):
    if not current_top:
        return
    rc, out, _err = run(["git", "-C", current_top, "grep", "-n", "-I", "-F", "-i", "--null", "--", args.old],
                        None, 120)
    if rc not in (0, 1):
        sink.add(_finding("unreadable", current_top, "git grep", "absent", True,
                          "tracked text could not be searched"))
        return
    rx = _text_re(args.old)
    counts, order = {}, []
    for path, number, text in _GREP_RE.findall(out):
        if not rx.search(text):
            continue
        if _WORKFLOW_RE.match(path):
            sink.add(_finding("workflow", os.path.join(current_top, path), "line:" + number, "old", True,
                              "the workflow names the old repository"))
            continue
        if path not in counts:
            counts[path] = 0
            order.append(path)
        counts[path] += 1
    for path in order:
        sink.add(_finding("tracked-text", os.path.join(current_top, path), "lines:%d" % counts[path],
                          "old", False, "tracked text holds the old name"))


def _environment(args, sink):
    value = os.environ.get("GH_REPO")
    if not value:
        return
    cls = _class(_any_slug(value.split("/", 1)[1] if value.count("/") == 2 else value), args.old, args.new)
    if cls in ("old", "new"):
        sink.add(_finding("env", "process-environment", "GH_REPO", cls, cls == "old"))


def _marketplaces(args, sink):
    cfg = os.path.realpath(args.claude_config)
    sources = (("plugins/known_marketplaces.json", None), ("settings.json", "extraKnownMarketplaces"))
    for rel, key in sources:
        path = os.path.join(cfg, rel)
        if not os.path.isfile(path):
            continue
        try:
            doc = _load_json(path)
        except (OSError, ValueError):
            sink.add(_finding("unreadable", path, "marketplaces", "absent", True, "cannot be read as JSON"))
            continue
        if key:
            doc = doc.get(key) if isinstance(doc, dict) else None
        if not isinstance(doc, dict):
            continue
        for name in sorted(doc):
            src = doc[name].get("source") if isinstance(doc[name], dict) else None
            if not isinstance(src, dict):
                continue
            slug = _any_slug(str(src.get("repo") or "")) or _url_slug(str(src.get("url") or ""))
            cls = _class(slug, args.old, args.new)
            if cls in ("old", "new"):
                sink.add(_finding("marketplace", path, str(name), cls, cls == "old"))
    base = os.path.join(cfg, "plugins", "marketplaces")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return
    return names, base


def _marketplace_clones(args, sink, run, names, base):
    for name in names:
        clone = os.path.join(base, name)
        if not os.path.exists(os.path.join(clone, ".git")):
            continue
        rc, out, _err = run(["git", "-C", clone, "config", "--get", "remote.origin.url"], None, GIT_TIMEOUT)
        if rc != 0:
            continue
        cls = _class(_url_slug(out.strip()), args.old, args.new)
        if cls in ("old", "new"):
            sink.add(_finding("marketplace", clone, "remote.origin.url", cls, cls == "old"))


def _writers(args, holders, run, sink):
    rows = None
    for argv in (["ps", "-axo", "pid=,etime=,command="], ["ps", "-eo", "pid=,etime=,args="]):
        rc, out, _err = run(argv, None, PS_TIMEOUT)
        if rc == 0:
            rows = out
            break
    if rows is None:
        sink.add(_finding("unreadable", "process-list", "ps", "absent", True,
                          "the process list could not be read; confirm by hand that no writer runs"))
        return
    own = str(os.getpid())
    for line in rows.splitlines():
        m = _PS_ROW_RE.match(line)
        if not m or m.group(1) == own:
            continue
        pid, etime, command = m.group(1), m.group(2), m.group(3)
        script = next((t for t in command.split() if _WRITER_RE.search(t)), None)
        if script is None:
            continue
        where, blocking, note = None, False, ""
        for root in sorted(holders):
            if re.search(re.escape(root) + r"(?=[/\s]|$)", command):
                where = root
                break
        if where is None and holders:
            cwd = _process_cwd(pid, run)
            if cwd is None:
                note = "cwd unreadable; not attributed to a checkout"
            else:
                cwd = os.path.realpath(cwd)
                where = next((r for r in sorted(holders) if _within(cwd, r)), None)
        if where is not None:
            blocking = True
            note = "attributed to the checkout %s" % where
        elif not note:
            note = "not attributed to a checkout holding either name"
        sink.add(_finding("writer", "pid:" + pid, os.path.basename(script), "absent", blocking,
                          "up %s; %s" % (etime, note)))


def _gh_get(run, endpoint):
    """-> (data, why): why is None on success, else 'HTTP 404' style text or 'gh exit N'."""
    rc, out, err = run(["gh", "api", endpoint], None, 60)
    if rc != 0:
        m = _HTTP_RE.search(err or "")
        return None, ("HTTP " + m.group(1)) if m else "gh exit %d" % rc
    try:
        return json.loads(out), None
    except ValueError:
        return None, "unparseable reply"


def _names(data, key):
    items = data.get(key) if (key and isinstance(data, dict)) else data
    names = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            label = item.get("name") or item.get("title") or item.get("id")
            if label is not None:
                names.append(str(label))
    return names


def _rest(args, run, sink):
    repo = args.new or args.old
    for kind, suffix, key in _REST:
        endpoint = "repos/%s/%s?per_page=100" % (repo, suffix)
        place = "repos/%s/%s" % (repo, suffix)
        data, why = _gh_get(run, endpoint)
        if why is not None:
            if why in ("HTTP 403", "HTTP 404"):
                sink.add(_finding("rest-unreadable", place, suffix, "absent", False, why))
            else:
                sink.add(_finding("unreadable", place, suffix, "absent", True, why))
            continue
        for name in _names(data, key):
            sink.add(_finding(kind, place, name, "absent", False, "name only"))
    if args.repo_id is None:
        return
    seen = {}
    for label, slug in (("new", args.new), ("old", args.old)):
        if not slug:
            continue
        data, why = _gh_get(run, "repos/" + slug)
        if why is not None:
            seen[label] = None
            if label == "old":
                sink.add(_finding("rest-unreadable", "repos/" + slug, "id", "absent", False, why))
            continue
        seen[label] = data if isinstance(data, dict) else {}
    if args.new:
        got = seen.get("new")
        if got is None:
            sink.add(_finding("id-mismatch", "repos/" + args.new, "id", "new", True,
                              "the new name does not resolve to a repository"))
        elif got.get("id") != args.repo_id or got.get("private") is not True:
            sink.add(_finding("id-mismatch", "repos/" + args.new, "id", "new", True,
                              "the new name resolves to id %s (private: %s), expected id %s, private"
                              % (got.get("id"), got.get("private"), args.repo_id)))
    got = seen.get("old")
    if got is not None and got.get("id") != args.repo_id:
        sink.add(_finding("old-name-taken", "repos/" + args.old, "id", "old", False,
                          "the old name now resolves to id %s, not %s: every remaining old holder "
                          "reaches that repository" % (got.get("id"), args.repo_id)))


def _write_json_once(path, doc):
    """The only write this tool makes: create-once, 0600, atomic (tmp + link), never an overwrite."""
    data = (json.dumps(doc, indent=2) + "\n").encode("utf-8")
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            raise Refused("json-exists", "the --json file already exists; it is never overwritten")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _validate_names(args, with_new):
    _check_slug(args.old, "--old")
    if args.new is not None:
        _check_slug(args.new, "--new")
        if args.old.split("/")[0].lower() != args.new.split("/")[0].lower():
            raise Refused("owner-differs", "a rename never moves the owner; --old and --new must share it")
        if args.old.lower() == args.new.lower():
            raise Refused("same-name", "--old and --new are the same repository name")


def cmd_check(args, run):
    _validate_names(args, True)
    args.claude_config = args.claude_config or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    for root in args.scan_root:
        try:
            os.listdir(root)
        except OSError:
            raise Refused("scan-root-unreadable", "cannot read the scan root %s" % _tilde(str(root)))
    if args.json:
        _refuse_inside_work_tree(args.json, "json-inside-work-tree", run)
        if os.path.lexists(args.json):
            raise Refused("json-exists", "the --json file already exists; it is never overwritten")
    sink = Sink()
    repos, order, current_top, truncated = _scan_repositories(args, run, sink)
    _read_remotes(args, repos, order, run, sink)
    holders = _read_sdlc_configs(args, repos, order, sink)
    _heartbeats(repos, order, holders, sink)
    _tracked_text(args, current_top, run, sink)
    _environment(args, sink)
    found = _marketplaces(args, sink)
    if found:
        _marketplace_clones(args, sink, run, found[0], found[1])
    _writers(args, holders, run, sink)
    if not args.offline:
        _rest(args, run, sink)
    findings = sink.items
    blocking = sum(1 for f in findings if f["blocking"])
    lines = ["handover_check: old=%s new=%s repo-id=%s rest=%s"
             % (args.old, args.new or "-", args.repo_id if args.repo_id is not None else "-",
                "offline" if args.offline else "ran")]
    for f in findings:
        line = "%s %s %s %s %s" % ("BLOCK" if f["blocking"] else "INFO ", f["kind"], f["location"],
                                   re.sub(r"\s+", "_", f["field"]) or "-", f["value_class"])
        if f["note"]:
            line += " -- " + f["note"]
        lines.append(_tilde(line))
    lines.append("handover_check: %d blocking, %d informational; repositories %d; truncated %s"
                 % (blocking, len(findings) - blocking, len(order), "yes" if truncated else "no"))
    if args.json:
        _write_json_once(args.json, _json_doc(args, findings, "offline" if args.offline else "ran", truncated))
    print("\n".join(lines))
    return 1 if blocking else 0


# ------------------------------------------------------------------------------ sequence

_REHEARSAL = """REHEARSAL (throwaway %(t)s, renamed %(t)s-renamed): do this first, on a repository that is not the real one
  R1. owner: create the throwaway PRIVATE repository %(t)s, then push the export:
      git -C "$OUT" push https://github.com/%(t)s.git main
  R2. python3 tools/verify_public_repo.py --repo %(t)s --report "$REPORT" --expect-visibility private
  R3. in a clone configured for it, the loop claims, opens a PR and records (docs/name-handover.md, Rehearsal)
  R4. steps 1-6 below with the throwaway as the old name and %(t)s-renamed as the new name, then R3 against the renamed one
"""

_SEQUENCE = """SEQUENCE (owner commands, in this order; stop at the first failure)
1. stop every writer: `touch .sdlc/state/watch.stop` in each checkout, end every loop session, and confirm
   none is left (never pause a process with a stop signal):
     ps -axo pid,etime,command | grep -E 'watch_daemon|loop\\.py'
2. list every holder: python3 tools/handover_check.py check --old %(old)s --new %(new)s --scan-root ~/.sigma-ops --json "$JSON"
     (a --json path outside every repository, never used before)
3. record the repository id: gh api repos/%(old)s --jq '.id, .private'
     write the id down as ID
4. owner: gh repo rename %(nname)s -R %(old)s --yes
5. once per repository from step 2:
     git -C "$CLONE" remote set-url origin https://github.com/%(new)s.git
     set discovery.github.repo (and ledger.handoff.upstream_repo if it named %(old)s) to %(new)s in each .sdlc/config.json
     re-add any marketplace recorded as %(old)s
6. gh api repos/%(new)s --jq '.id, .private'
     the id equals ID; then python3 tools/handover_check.py check --old %(old)s --new %(new)s --scan-root ~/.sigma-ops --repo-id "$ID" --json "$JSON"
     must exit 0
7. ONLY THEN, owner: gh repo create %(old)s --public
     git -C "$OUT" push https://github.com/%(old)s.git main
     python3 tools/verify_public_repo.py --repo %(old)s --report "$REPORT" --expect-visibility public
8. check again: python3 tools/handover_check.py check --old %(old)s --new %(new)s --scan-root ~/.sigma-ops --repo-id "$ID" --json "$JSON"
     expect old-name-taken informational and no BLOCK
9. `rm .sdlc/state/watch.stop` in each checkout
"""

_LAST = "release checklist, pin and rollback: #359; this checker and sequence: #397"


def cmd_sequence(args, run):
    _validate_names(args, True)
    throwaway = args.throwaway
    if throwaway is not None:
        _check_slug(throwaway, "--throwaway")
    else:
        throwaway = args.old.split("/")[0] + "/<throwaway>"
    names = {"old": args.old, "new": args.new, "nname": args.new.split("/")[1], "t": throwaway}
    print(_REHEARSAL % names)
    print(_SEQUENCE % names)
    print(_LAST)
    return 0


# ------------------------------------------------------------------------------ entry

def _parser(prog):
    parser = argparse.ArgumentParser(prog=prog, description="List every holder of a repository's name; "
                                     "print the owner handover sequence. `check` is read-only.")
    verbs = parser.add_subparsers(dest="verb", metavar="{check,sequence}")
    verbs.required = True
    chk = verbs.add_parser("check", help="list every local and configured holder of the old name")
    chk.add_argument("--old", required=True, metavar="OWNER/NAME", help="the current repository name")
    chk.add_argument("--new", metavar="OWNER/NAME", help="the name it is renamed to")
    chk.add_argument("--repo-id", type=int, metavar="N", help="the recorded repository id (REST id check)")
    chk.add_argument("--clone", action="append", default=[], metavar="PATH", help="a clone to read (repeatable)")
    chk.add_argument("--scan-root", action="append", default=[], metavar="DIR", help="a directory to walk for clones (repeatable)")
    chk.add_argument("--max-depth", type=int, default=5, metavar="N", help="walk depth cap (default 5)")
    chk.add_argument("--max-repos", type=int, default=2000, metavar="N", help="repository cap (default 2000)")
    chk.add_argument("--claude-config", metavar="DIR", help="Claude config dir (default $CLAUDE_CONFIG_DIR, else ~/.claude)")
    chk.add_argument("--offline", action="store_true", help="make no GitHub REST call")
    chk.add_argument("--json", metavar="FILE", help="also write the findings here (create-once, 0600, outside every repository)")
    seq = verbs.add_parser("sequence", help="print the ordered owner handover sequence")
    seq.add_argument("--old", required=True, metavar="OWNER/NAME", help="the current repository name")
    seq.add_argument("--new", required=True, metavar="OWNER/NAME", help="the name it is renamed to")
    seq.add_argument("--throwaway", metavar="OWNER/NAME", help="the rehearsal repository")
    return parser


def main(argv, run=None):
    run = run or _real_run
    try:
        if _platform_is_windows():
            raise Refused("windows", "this tool is POSIX only")
        args = _parser(os.path.basename(argv[0]) if argv else "handover_check.py").parse_args(argv[1:])
        return cmd_check(args, run) if args.verb == "check" else cmd_sequence(args, run)
    except Refused as exc:
        print("handover_check: REFUSED [%s] %s" % (exc.code, exc.detail), file=sys.stderr)
        return 2
    except Exception as exc:  # a stop is loud and typed, never a traceback over a half-printed report
        print("handover_check: REFUSED [internal] %s" % type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
