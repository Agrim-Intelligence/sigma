#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The launch GO/NO-GO checker (#331, #429): computes the verdict docs/launch/decision-rule.md
defines from main's commit and the live REST blocker list, so relabelling a finding, re-flagging a
dimension, editing a file in a checkout or setting one of the git or gh variables listed under GIT
and GH ENVIRONMENT does not change it. What it still trusts is listed under TRUST ROOTS.

THE RULE (docs/launch/decision-rule.md, "The rule"). GO if and only if ALL of:
  1. every GATING dimension in docs/launch/scorecard.json scores >= 3, with at least one evidence
     entry, and every evidence entry of it is a valid link (see EVIDENCE);
  2. zero open issues carry the `launch:blocker` label (pull requests are not issues and are
     dropped), as read live over REST (see WHERE THE BLOCKERS COME FROM);
  3. docs/launch/definition.json has `status: "signed"`, a `signed_by` that is a plain login
     (letters, digits, `-`; at most 39) and a `signed_on` that is a real YYYY-MM-DD date no later
     than today on the checking machine;
  4. the benchmark results file the scorecard names (`benchmark_results`) is under
     docs/launch/evidence/ and is a non-empty regular file at main (see MAIN).

WHY THE GATING SET IS PINNED HERE. Which dimensions exist, what they are called and which of them
gate are part of the pre-registered rule, not properties the scorecard's author may set: a
scorecard that marks a gating dimension informational (or the reverse), renames, drops or adds a
dimension, or carries a key the schema does not know (a typo such as `waived`) is MALFORMED
(exit 2), never silently re-weighted. Changing the set is a change to decision-rule.md, merged by
the owner, and tests/test_readiness_decide.py keeps that page's tables and the constants below in
sync.

MAIN. Everything is read from ONE commit: the one this checkout's `refs/remotes/origin/main`
names. That exact ref -- never the short name `origin/main`, which a local branch, a tag or
`refs/origin/main` would shadow -- is read once (`git show-ref --verify`) and peeled to a commit,
and every later read names that commit id. A symbolic `refs/remotes/origin/main` is refused: it
would make a local branch main. The scorecard, the definition, every repository-path evidence
entry and the benchmark file are read from that commit's tree (`git ls-tree`, `git cat-file
blob`); the working tree is never read, so an edited, untracked, deleted or symlinked file in the
checkout changes nothing. Nor is the checker's own folder imported from: Python puts a script's
folder first on sys.path, so before importing anything but the builtins `sys` and `posix`, every
sys.path entry that is that folder (compared by device and inode) is removed, and an untracked
tools/readiness/json.py, a json/ package or a sourceless json.pyc beside this file is never
imported. The checker never fetches. Live, it compares that commit with main as the
REST API reports it (`gh api repos/<OWNER>/<NAME>/git/ref/heads/main`) and refuses when they
differ, naming both: `git fetch origin`, then rerun. Offline, refs/remotes/origin/main is taken as
this checkout last fetched it.

GIT. Every git call is `git --no-replace-objects --literal-pathspecs -c core.commitGraph=false
-c protocol.allow=never` plus `-c protocol.<p>.allow=never` for file, git, ssh, http, https and ext,
then `-C <repo_root>`, with
every `GIT_*` variable removed from its environment and GIT_CONFIG_NOSYSTEM=1,
GIT_CONFIG_GLOBAL=<os.devnull>, GIT_NO_REPLACE_OBJECTS=1, GIT_NO_LAZY_FETCH=1 and
GIT_TERMINAL_PROMPT=0 added. So an inherited GIT_DIR or GIT_CONFIG_*, a global or system config, a
replace ref or a partial clone's lazy fetch cannot change what is read, and an object missing from
a partial clone is refused rather than fetched. `core.commitGraph=false` on the command line beats
the checkout's own config, so per git-config(1) git does not read a commit-graph file for a
commit's tree (no forged-graph fixture was run). Global config being off also switches off a global
`safe.directory`, so a checkout another user owns is refused. A git older than 2.32 (which ignores
GIT_CONFIG_GLOBAL) is refused.

EVIDENCE. An evidence entry counts only if it is EXACTLY one of:
  * an `http://` or `https://` URL with a host and no whitespace anywhere in the entry (URLs are
    checked for shape only; the checker never fetches them);
  * a canonical relative path, `/`-separated, with no whitespace, NUL or other control character,
    no `.` or `..` component, not absolute, that is a non-empty regular file (not a symlink, a
    directory or a submodule) at main.
Anything else (`TODO`, `n/a`, ` x `, a path that is not at main or is empty there, `../x`) is
NO-GO, and the reason names the dimension and the bad entry. `benchmark_results` null or blank is
NO-GO (not named); a path outside docs/launch/evidence/ (or absolute, or with `..`) is MALFORMED.

WHAT COUNTS AS "UNKNOWN". Anything that cannot be read is never GO:
  * definition.json not tracked at main -> NO-GO, reason `definition missing` (exit 1) -- the
    launch definition goal may simply not have landed yet; that is a legitimate "not ready";
  * scorecard.json not tracked at main; either file at main as anything but a regular file of at
    most 1 MiB; unparsable; a duplicated JSON key; wrong schema; an unknown key; a score outside
    0-4; a duplicated/unknown/missing/renamed dimension; a flipped gating flag -> MALFORMED
    (exit 2);
  * the blocker list cannot be fetched or parsed, gh exits 0 with no output, or an entry is not an
    object with an integer `number`, a `state` of open/closed (any case) and a `labels` list that
    includes `launch:blocker` -> MALFORMED (exit 2). A failed read is never "zero blockers".
  * main that cannot be read -- git missing or older than 2.32, <repo_root> not the top of a git
    work tree, no refs/remotes/origin/main, a symbolic one or one that is not a commit, an object
    missing from this clone -- and, live, a REST read of main that fails, is malformed or names
    another commit, or a gh configured with `http_unix_socket` -> REFUSED (exit 2);
  * an unscored (null) gating dimension -> NO-GO (it has not reached 3);
  * any other unreadable input (a path the OS rejects, JSON nested too deep) -> MALFORMED (exit 2).

WHERE THE BLOCKERS COME FROM. Without --blockers-json, after every local read, two checks and one
read-only REST call: `gh config get http_unix_socket` and `gh config get -h <host>
http_unix_socket` must both succeed and print nothing (a configured socket can answer for any
host), the REST read of main must match (see MAIN), and then
    gh api --hostname=<host> "repos/<OWNER>/<NAME>/issues?state=open&labels=launch:blocker&per_page=100" --paginate
(never GraphQL). OWNER/NAME and <host> are always passed explicitly, so gh never resolves them
itself. ORIGIN names a repository when <repo_root>'s ONLY remote is `origin`, with exactly one URL
as this checkout's own config writes it (`git config --get-all remote.origin.url`, so no
`url.<x>.insteadOf` rewrite applies), of the form https://HOST/OWNER/NAME,
ssh://[user@]HOST[:port]/OWNER/NAME or [user@]HOST:OWNER/NAME (`.git` optional). Without --repo,
origin must name one, else MALFORMED (several remotes, none, several URLs or any other URL shape) --
the checker never guesses which repository's blockers count. With --repo OWNER/NAME the host is
github.com, and when origin names a repository --repo must be that one (host github.com, OWNER/NAME
compared case-insensitively; origin's spelling is used) or the checker is REFUSED naming both: a
fork or mirror whose main is the same commit has its own blocker list. When origin cannot name a
repository, --repo is the OPERATOR'S ASSERTION, which the checker cannot verify; an
`info: --repo OWNER/NAME is the operator's assertion: ...` line says so. OWNER and NAME must each
start with a letter, digit or `_`, contain only letters, digits, `_`, `.`, `-`, and be at most 100
characters.

GH ENVIRONMENT. gh runs with every `GIT_*` variable, GH_REPO, GH_HOST, GH_FORCE_TTY and
CLICOLOR_FORCE removed from its environment, and with GH_PAGER and PAGER pinned to the empty string
and NO_COLOR=1. Measured with gh 2.98.0: GH_FORCE_TTY makes gh treat its piped stdout as a
terminal and run `gh api` output through GH_PAGER, gh config's `pager` or PAGER, which can print
anything; an empty GH_PAGER switches the pager off even then; CLICOLOR_FORCE colours JSON even
with NO_COLOR set. The token variables are kept because they only authenticate. Every other
variable gh reads (GH_CONFIG_DIR, GH_TOKEN, proxy and certificate variables among them) is
inherited and is a trust root.
With --blockers-json PATH, a JSON list of REST issue objects is read instead: the offline form. A
file cannot establish that no open `launch:blocker` issue exists, so an offline run is NEVER GO --
it adds the reason `blocker list read offline from <PATH>; only the live REST read can establish
that no open launch:blocker issue exists`. An object carrying a `pull_request` object is a pull
request and is dropped; a `pull_request` that is not an object is MALFORMED.
The checker writes nothing anywhere.

TRUST ROOTS (stated, not defended). The checker trusts: this file; the `python3`, `git` and `gh`
that PATH resolves (`.` or the checkout root on PATH runs whatever `gh`/`git` it finds there), and
the interpreter's own environment (PYTHONPATH, PYTHONHOME, site customisation), which loads code
before this file's first line runs; the checkout's whole `.git`
directory -- its config and every file that config includes (a local `protocol.<name>.allow` for a
remote helper is not pinned, and origin's URL is whatever that config says), its object store
(`git cat-file` does not re-hash what it reads) and its refs (offline, refs/remotes/origin/main is
taken as last fetched); gh's config directory (GH_CONFIG_DIR or its default) and stored
credentials, and every gh variable not removed or pinned above; the network's proxies and
certificate authorities and the environment variables that choose them (HTTPS_PROXY, SSL_CERT_FILE
and the like); and --repo when origin cannot name the repository.

USAGE
    python3 tools/readiness/decide.py <repo_root> [--blockers-json PATH] [--repo OWNER/NAME]

OUTPUT: one line per reason the verdict is not GO, then `info:` lines (informational dimensions,
`info: main is refs/remotes/origin/main at <sha>`, an `info: --repo ... is the operator's
assertion` line when origin cannot name the repository, and where the blockers were read from),
then a final line `GO` or `NO-GO`. A refusal prints `decide.py: REFUSED: <why>` (the environment,
the repository's state, the REST read of main, a --repo that is not origin's repository) and a
malformed input `decide.py: MALFORMED: <detail>` (the scorecard, the definition, the blocker list,
origin's URL, an argument), both on stderr with nothing on stdout.

EXIT: 0 = GO; 1 = NO-GO; 2 = refused, malformed input or a usage error -- never reported as a
verdict. `--help` prints this usage and exits 2, so no script can read it as GO.

Stdlib only. CI runs it on Linux with Python 3.10, 3.11, 3.12 and 3.13 and on macOS with Python
3.12; Python 3.9 was measured by hand, not CI-proven. It refuses to run on Windows (exit 2). The
external programs are `git` 2.32 or later, always, and `gh`, only when --blockers-json is omitted.
"""
import sys


def _drop_own_folder_from_import_path():
    """Remove from sys.path every entry that is this file's own folder (compared by device and
    inode, so another spelling, a symlink or `''` for the current directory is caught too), before
    any module that could be shadowed is imported. Python puts a script's folder first on sys.path,
    so an untracked tools/readiness/json.py, a json/ package or a sourceless json.pyc would
    otherwise replace the stdlib module and could print GO (post-PR review 1). Only `sys` and the
    builtin `posix` (`nt` on Windows) are used here: a builtin is found before sys.path is searched,
    so neither can be shadowed. An entry that cannot be stat'ed is kept (nothing can be imported
    from it); this folder itself not being stat-able is a refusal."""
    posix = __import__("posix" if "posix" in sys.builtin_module_names else "nt")
    seps = "/" if posix.__name__ == "posix" else "/\\"
    cut = max(__file__.rfind(sep) for sep in seps)
    here = __file__[:cut] if cut > 0 else ("/" if cut == 0 else ".")

    def ident(path):
        st = posix.stat(path or ".")
        return st.st_dev, st.st_ino

    try:
        own = ident(here)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"decide.py: REFUSED: cannot stat this checker's own folder: {exc}\n")
        raise SystemExit(2)
    keep = []
    for entry in sys.path:
        try:
            same = isinstance(entry, str) and ident(entry) == own
        except (OSError, ValueError):
            same = False
        if not same:
            keep.append(entry)
    sys.path[:] = keep


_drop_own_folder_from_import_path()

# Every import below runs only after the scrub above.
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import urllib.parse

SCORECARD = "docs/launch/scorecard.json"
DEFINITION = "docs/launch/definition.json"
EVIDENCE_DIR = "docs/launch/evidence/"
SCORECARD_SCHEMA = "launch-scorecard/v1"
DEFINITION_SCHEMA = "launch-definition/v1"
PASS_SCORE = 3
MIN_SCORE, MAX_SCORE = 0, 4
CARD_KEYS = frozenset({"schema", "benchmark_results", "dimensions"})
DIM_KEYS = frozenset({"id", "name", "gating", "score", "evidence"})

#: Pinned by docs/launch/decision-rule.md ("Dimensions"). id -> (name, gating). Kept in sync by a
#: test.
DIMENSIONS = {
    "D1": ("Correctness", True),
    "D2": ("Skills", True),
    "D3": ("Outcomes and cost", True),
    "D5": ("Five properties", True),
    "D6": ("Platform", True),
    "D7": ("Onboarding", True),
    "D8": ("Docs", True),
    "D9": ("Upgrade, coexistence, uninstall", True),
    "D10": ("Security, privacy, exposure", True),
    "D11": ("Operations", True),
    "D12": ("Market", False),  # informational; blocks only through B3
    "D13": ("Legal and naming", True),
}

BLOCKER_LABEL = "launch:blocker"
DEFAULT_HOST = "github.com"
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
REPO_RE = re.compile(rf"({SEGMENT})/({SEGMENT})")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
SCP_RE = re.compile(r"(?:[A-Za-z0-9._~-]+@)?([A-Za-z0-9][A-Za-z0-9.-]*):(?!/)(.+)")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LOGIN_RE = re.compile(r"[A-Za-z0-9-]{1,39}")
OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SIZE_RE = re.compile(r"[0-9]+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

MAIN_REF = "refs/remotes/origin/main"
#: The scorecard and the definition are a few KiB; the cap only bounds what a hostile blob costs.
MAX_INPUT_BYTES = 1 << 20
#: The first git that honours GIT_CONFIG_GLOBAL (git-config(1)); older ones read global config.
MIN_GIT = (2, 32)
PROTOCOLS = ("file", "git", "ssh", "http", "https", "ext")
#: Every git call starts so: no replace refs, literal paths, and no transport at all (a partial
#: clone's lazy fetch is a transport). A local `protocol.<p>.allow=always` beats the catch-all
#: `protocol.allow=never`, so each built-in protocol is pinned by name as well.
#: `core.commitGraph=false` on the command line beats the checkout's own config, so (git-config(1))
#: git does not read a commit-graph file for a commit's tree; no forged-graph fixture was run.
GIT_ARGV = ("git", "--no-replace-objects", "--literal-pathspecs", "-c", "core.commitGraph=false",
            "-c", "protocol.allow=never",
            *(arg for name in PROTOCOLS for arg in ("-c", f"protocol.{name}.allow=never")))
#: Removed from gh's environment: GH_REPO and GH_HOST would choose the repository; GH_FORCE_TTY
#: makes gh treat a pipe as a terminal, so `gh api` output goes through a pager that can print
#: anything; CLICOLOR_FORCE colours JSON even with NO_COLOR set (both measured, real gh 2.98.0).
GH_DROPPED_ENV = ("GH_REPO", "GH_HOST", "GH_FORCE_TTY", "CLICOLOR_FORCE")
#: Pinned in gh's environment: an empty GH_PAGER beats gh config's `pager` and PAGER even under a
#: forced terminal (measured); PAGER is emptied too, and NO_COLOR asks for no colour.
GH_PINNED_ENV = {"GH_PAGER": "", "PAGER": "", "NO_COLOR": "1"}
REGULAR_MODES = ("100644", "100755")
OFFLINE_REASON = ("blocker list read offline from {!r}; only the live REST read can establish "
                  "that no open " + BLOCKER_LABEL + " issue exists")


class Malformed(Exception):
    """An input the checker cannot turn into a verdict (exit 2, `MALFORMED`)."""


class Refused(Exception):
    """An environment or repository state the checker will not compute a verdict in (exit 2,
    `REFUSED`)."""


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _load_json(path, what):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Malformed(f"{what} not found: {path}")
    except (OSError, ValueError) as exc:
        raise Malformed(f"{what} unreadable: {path}: {exc}")
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise Malformed(f"{what} is not valid JSON: {path}: {exc or type(exc).__name__}")


# -- git: scrubbed, pinned, read-only -----------------------------------------------------------

def _git_env():
    """os.environ without any GIT_* variable, plus the pins: no system or global config, no replace
    refs, no lazy fetch, no prompt."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    return env


def _git(root, *args):
    """Run one read-only git command in root; return the CompletedProcess (bytes)."""
    try:
        return subprocess.run([*GIT_ARGV, "-C", str(root), *args], capture_output=True,
                              timeout=60, env=_git_env())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Refused(f"git cannot be run: {exc}")


def _fatal(proc):
    """git's own reason for a failure: its first `fatal:` line, else its first line of stderr."""
    lines = [line.strip() for line in proc.stderr.decode("utf-8", "replace").splitlines()
             if line.strip()]
    fatal = [line for line in lines if line.startswith("fatal:")]
    return (fatal or lines or [f"git exited {proc.returncode}"])[0]


def check_git(root):
    """Refuse a git older than MIN_GIT, and a root that is not the top of its git work tree."""
    proc = _git(root, "--version")
    text = proc.stdout.decode("utf-8", "replace").strip()
    found = re.match(r"git version ([0-9]+)\.([0-9]+)", text)
    if proc.returncode != 0 or not found:
        raise Refused(f"cannot tell which git this is (`git --version` printed {text!r})")
    if (int(found.group(1)), int(found.group(2))) < MIN_GIT:
        raise Refused(f"{text} is older than 2.32, the first git that honours GIT_CONFIG_GLOBAL; "
                      "on it the checker cannot switch off global git config")
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        why = _fatal(proc)
        if "dubious ownership" in why:
            why += ("; the checker ignores global git config, where safe.directory lives, so a "
                    "checkout another user owns is refused")
        raise Refused(f"{root} is not the top of a git work tree ({why})")
    top = os.fsdecode(proc.stdout.strip())
    try:
        same = pathlib.Path(top).resolve() == root.resolve()
    except (OSError, ValueError):
        same = False
    if not same:
        raise Refused(f"{root} is not the top of its git work tree ({top})")


def resolve_main(root):
    """The commit id refs/remotes/origin/main names: that exact ref, not symbolic, peeled once."""
    proc = _git(root, "symbolic-ref", "-q", MAIN_REF)
    if proc.returncode == 0:
        target = proc.stdout.decode("utf-8", "replace").strip()
        raise Refused(f"{MAIN_REF} is a symbolic ref (to {target}); the checker reads only a ref "
                      "that git fetch wrote")
    if proc.returncode != 1:
        raise Refused(f"cannot read {MAIN_REF} ({_fatal(proc)})")
    proc = _git(root, "show-ref", "--verify", "--hash", MAIN_REF)
    oid = proc.stdout.decode("ascii", "replace").strip() if proc.returncode == 0 else ""
    if not OID_RE.fullmatch(oid):
        raise Refused(f"no {MAIN_REF} in this checkout; git fetch origin, then rerun")
    proc = _git(root, "rev-parse", "--verify", "-q", "--end-of-options", oid + "^{commit}")
    commit = proc.stdout.decode("ascii", "replace").strip() if proc.returncode == 0 else ""
    if not OID_RE.fullmatch(commit):
        raise Refused(f"{MAIN_REF} ({oid}) is not a commit")
    return commit


def _tree_entry(root, commit, rel):
    """(mode, kind, oid, size) of rel in commit's tree, or None when it is not there."""
    proc = _git(root, "ls-tree", "-l", "-z", commit, "--", rel)
    if proc.returncode != 0:
        raise Refused(f"cannot read {rel} at {MAIN_REF} ({commit}): {_fatal(proc)}")
    entries = [e for e in proc.stdout.split(b"\0") if e]
    if not entries:
        return None
    meta, _, path = entries[0].partition(b"\t")
    fields = meta.decode("ascii", "replace").split()
    if len(entries) != 1 or len(fields) != 4 or path != rel.encode("utf-8"):
        return None
    return tuple(fields)


def _blob_size(root, commit, rel, entry):
    """The size git reports for a blob entry. A partial clone that lacks the blob reports none
    (git 2.55 prints `BAD`; 2.39 fails): the object is not here, and it is never fetched."""
    size = entry[3]
    if not SIZE_RE.fullmatch(size):
        raise Refused(f"cannot read {rel} at {MAIN_REF} ({commit}): git reports no size for blob "
                      f"{entry[2]} ({size}); it is not in this clone's object store")
    return int(size)


def at_main(root, commit, rel):
    """None when rel is a non-empty regular file at commit, else why it is not."""
    entry = _tree_entry(root, commit, rel)
    if entry is None:
        return "is not tracked at origin/main"
    mode, kind, _, _ = entry
    if kind != "blob" or mode not in REGULAR_MODES:
        return "is not a regular file at origin/main (a symlink or directory)"
    if _blob_size(root, commit, rel, entry) == 0:
        return "is empty at origin/main"
    return None


def read_json_at_main(root, commit, rel, what):
    """The JSON value of rel at commit, or None when rel is not tracked there. Raises Malformed
    (not a regular file, over MAX_INPUT_BYTES, not JSON) or Refused (cannot be read)."""
    entry = _tree_entry(root, commit, rel)
    if entry is None:
        return None
    mode, kind, oid, _ = entry
    where = f"{what} at {MAIN_REF} ({commit})"
    if kind != "blob" or mode not in REGULAR_MODES:
        raise Malformed(f"{where} is not a regular file (a symlink, directory or submodule): {rel}")
    size = _blob_size(root, commit, rel, entry)
    if size > MAX_INPUT_BYTES:
        raise Malformed(f"{where} is {size} bytes, over the {MAX_INPUT_BYTES}-byte cap")
    proc = _git(root, "cat-file", "blob", oid)
    if proc.returncode != 0:
        raise Refused(f"cannot read {rel} at {MAIN_REF} ({commit}): {_fatal(proc)}")
    try:
        return json.loads(proc.stdout.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise Malformed(f"{where} is not valid JSON: {exc or type(exc).__name__}")


# -- the scorecard, the benchmark and the definition --------------------------------------------

def _path_shape_problem(entry):
    if CONTROL_RE.search(entry):
        return "contains a NUL or control character"
    rel = pathlib.PurePosixPath(entry)
    if "\\" in entry or rel.is_absolute() or re.match(r"^[A-Za-z]:", entry):
        return "is not a relative /-separated repository path"
    if ".." in entry.split("/"):
        return "escapes the repository (`..`)"
    if str(rel) != entry or "." in entry.split("/"):
        return "is not a canonical path (no `.`, `//` or trailing `/`)"
    return None


def evidence_problem(entry, tracked):
    """None if `entry` is a valid evidence link, else why it is not. tracked(rel) is at_main."""
    if not entry or any(ch.isspace() for ch in entry):
        return "is not a link: is empty or contains whitespace"
    low = entry.lower()
    if low.startswith(("http://", "https://")):
        try:
            host = urllib.parse.urlsplit(entry).hostname
        except ValueError:
            return "is not a link: is a URL that cannot be parsed"
        return None if host else "is not a link: is a URL with no host"
    if "://" in entry:
        return "is not a link: is a URL that is not http(s)"
    shape = _path_shape_problem(entry)
    if shape:
        return "is not a link: " + shape
    return tracked(entry)


def check_scorecard(card, tracked):
    """Validate the scorecard and return (reasons, infos). Raises Malformed. tracked(rel) returns
    None when rel is a non-empty regular file at main, else why not (at_main)."""
    if not isinstance(card, dict) or card.get("schema") != SCORECARD_SCHEMA:
        raise Malformed(f"scorecard schema must be {SCORECARD_SCHEMA!r}")
    extra = sorted(set(card) - CARD_KEYS)
    if extra:
        raise Malformed(f"scorecard has unknown key(s) {extra} (allowed: {sorted(CARD_KEYS)})")
    dims = card.get("dimensions")
    if not isinstance(dims, list):
        raise Malformed("scorecard `dimensions` must be a list")
    reasons, infos, seen = [], [], set()
    for i, dim in enumerate(dims):
        if not isinstance(dim, dict):
            raise Malformed(f"dimension #{i} is not an object")
        did, name = dim.get("id"), dim.get("name")
        if not isinstance(did, str) or did not in DIMENSIONS:
            raise Malformed(f"dimension #{i}: unknown id {did!r} (the rule pins {sorted(DIMENSIONS)})")
        if did in seen:
            raise Malformed(f"dimension {did} appears twice")
        seen.add(did)
        extra = sorted(set(dim) - DIM_KEYS)
        if extra:
            raise Malformed(f"dimension {did}: unknown key(s) {extra} (allowed: {sorted(DIM_KEYS)})")
        pinned_name, pinned_gating = DIMENSIONS[did]
        if name != pinned_name:
            raise Malformed(f"dimension {did}: `name` is {name!r}, but the rule pins "
                            f"{pinned_name!r} (change docs/launch/decision-rule.md, not the scorecard)")
        gating = dim.get("gating")
        if gating is not pinned_gating:
            raise Malformed(f"dimension {did}: `gating` is {gating!r}, but the rule pins "
                            f"{pinned_gating} (change docs/launch/decision-rule.md, not the scorecard)")
        score = dim.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int)):
            raise Malformed(f"dimension {did}: score {score!r} is not an integer or null")
        if score is not None and not MIN_SCORE <= score <= MAX_SCORE:
            raise Malformed(f"dimension {did}: score {score} is outside {MIN_SCORE}-{MAX_SCORE}")
        evidence = dim.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            raise Malformed(f"dimension {did}: `evidence` must be a list of strings")
        label = f"{did} {name}"
        if not gating:
            shown = "unscored" if score is None else f"score {score}"
            infos.append(f"info: {label} is informational ({shown}); it blocks only through B3")
        elif score is None:
            reasons.append(f"{label}: not scored (gating; needs >= {PASS_SCORE} with evidence)")
        elif score < PASS_SCORE:
            reasons.append(f"{label}: score {score} < {PASS_SCORE} (gating)")
        elif not evidence:
            reasons.append(f"{label}: score {score} has no evidence link (gating)")
        else:
            for entry in evidence:
                problem = evidence_problem(entry, tracked)
                if problem:
                    reasons.append(f"{label}: evidence {entry!r} {problem} (gating)")
    missing = sorted(set(DIMENSIONS) - seen)
    if missing:
        raise Malformed(f"scorecard is missing dimension(s) {missing}")
    if "benchmark_results" not in card:
        raise Malformed("scorecard has no `benchmark_results` key (use null until results exist)")
    return reasons + check_benchmark(card["benchmark_results"], tracked), infos


def check_benchmark(bench, tracked):
    """Return the reasons the benchmark clause fails. Raises Malformed."""
    if bench is None or (isinstance(bench, str) and not bench.strip()):
        return ["benchmark results: not named in the scorecard"]
    if not isinstance(bench, str):
        raise Malformed("`benchmark_results` must be a relative path string or null")
    if any(ch.isspace() for ch in bench) or _path_shape_problem(bench):
        raise Malformed(f"`benchmark_results` must be a path inside the repository: {bench!r}")
    if not bench.startswith(EVIDENCE_DIR) or len(bench.split("/")) <= 3:
        raise Malformed(f"`benchmark_results` must be a file under {EVIDENCE_DIR}: {bench!r}")
    problem = tracked(bench)
    return [f"benchmark results: {bench} {problem}"] if problem else []


def check_definition(data, today=None):
    """The reasons the definition clause fails; data is the definition at main, or None."""
    if data is None:
        return [f"definition missing: {DEFINITION} is not tracked at origin/main"]
    if not isinstance(data, dict) or data.get("schema") != DEFINITION_SCHEMA:
        raise Malformed(f"definition schema must be {DEFINITION_SCHEMA!r}")
    status = data.get("status")
    if status != "signed":
        return [f"definition not signed: status is {status!r}"]
    reasons = []
    signed_by = data.get("signed_by")
    if not (isinstance(signed_by, str) and LOGIN_RE.fullmatch(signed_by)):
        reasons.append(f"definition not signed: status is 'signed' but signed_by is {signed_by!r} "
                       "(needs a plain login: letters, digits, '-', at most 39)")
    signed_on = data.get("signed_on")
    date = None
    if isinstance(signed_on, str) and DATE_RE.fullmatch(signed_on):
        try:
            date = datetime.date.fromisoformat(signed_on)
        except ValueError:
            date = None
    today = today or datetime.date.today()
    if date is None:
        reasons.append(f"definition not signed: status is 'signed' but signed_on is "
                       f"{signed_on!r} (needs a real YYYY-MM-DD date)")
    elif date > today:
        reasons.append(f"definition not signed: status is 'signed' but signed_on is "
                       f"{signed_on!r}, later than today ({today.isoformat()})")
    return reasons


# -- the blocker list ---------------------------------------------------------------------------

def _decode_pages(text):
    """`gh api --paginate` prints one JSON array per page, concatenated. Decode all of them."""
    decoder, items, idx = json.JSONDecoder(object_pairs_hook=_unique_pairs), [], 0
    text = text.strip()
    while idx < len(text):
        page, end = decoder.raw_decode(text, idx)
        if not isinstance(page, list):
            raise ValueError("a page is not a JSON list")
        items.extend(page)
        idx = end
        while idx < len(text) and text[idx].isspace():
            idx += 1
    return items


def repo_from_url(url):
    """(host, OWNER/NAME) from a remote URL of a supported shape, or None."""
    scp = SCP_RE.fullmatch(url)
    if scp:
        host, path = scp.groups()
    else:
        try:
            parts = urllib.parse.urlsplit(url)
            host = parts.hostname
        except ValueError:
            return None
        if (parts.scheme.lower() not in ("https", "http", "ssh") or not host or parts.query
                or parts.fragment):
            return None
        path = parts.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-len(".git")]
    if not host or not HOST_RE.fullmatch(host) or not REPO_RE.fullmatch(path):
        return None
    return host.lower(), path


def origin_repo(root):
    """((host, OWNER/NAME), None) when the checkout's only remote, `origin`, has exactly one URL of
    a supported shape, as its own config writes it (global and system config are off, and no
    insteadOf rewrite applies); else (None, why it cannot name a repository)."""
    proc = _git(root, "remote")
    if proc.returncode != 0:
        return None, "the checkout's remotes cannot be listed"
    remotes = proc.stdout.decode("utf-8", "replace").split()
    if remotes != ["origin"]:
        return None, f"the checkout's remotes are {remotes} (need exactly one, named origin)"
    proc = _git(root, "config", "--get-all", "remote.origin.url")
    urls = [] if proc.returncode != 0 else \
        [u for u in proc.stdout.decode("utf-8", "replace").splitlines() if u.strip()]
    if len(urls) != 1:
        return None, f"origin has {len(urls)} URLs (need exactly one)"
    found = repo_from_url(urls[0].strip())
    if found is None:
        # The URL itself is not echoed: it may carry a credential (https://user:token@host/...).
        return None, ("origin's URL is not https://HOST/OWNER/NAME, ssh://HOST/OWNER/NAME or "
                      "HOST:OWNER/NAME")
    return found, None


def default_repo(root):
    """(host, OWNER/NAME) origin names (origin_repo). Raises Malformed when it names none."""
    found, why = origin_repo(root)
    if found is None:
        raise Malformed(f"--repo not given and {why}; pass --repo OWNER/NAME")
    return found


def repo_for(root, repo):
    """(host, OWNER/NAME, note) for an explicit --repo. When origin names a repository, --repo must
    be that one (host github.com; OWNER/NAME compared case-insensitively) and origin's spelling is
    used; else Refused naming both. When origin names none, --repo is the operator's assertion and
    `note` says so."""
    found, why = origin_repo(root)
    if found is None:
        return DEFAULT_HOST, repo, (f"info: --repo {repo} is the operator's assertion: {why}, so "
                                    "origin cannot name the repository whose blockers count")
    host, slug = found
    if host != DEFAULT_HOST or slug.lower() != repo.lower():
        raise Refused(f"--repo names {DEFAULT_HOST}/{repo}, but this checkout's only remote, "
                      f"origin, names {host}/{slug}; a fork or mirror whose main is the same "
                      "commit has its own blocker list. Pass --repo naming origin's repository, or "
                      "omit it")
    return host, slug, None


def _gh_env():
    """os.environ without any GIT_* variable or GH_DROPPED_ENV, plus GH_PINNED_ENV (no pager, no
    colour); token variables are kept."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("GIT_") and k.upper() not in GH_DROPPED_ENV
           and k.upper() not in GH_PINNED_ENV}
    env.update(GH_PINNED_ENV)
    return env


def _gh(run, root, env, args):
    return run(["gh", *args], cwd=str(root), env=env, capture_output=True, text=True, timeout=120)


def _last_line(proc):
    return ((proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"])[0]


def check_gh_socket(run, root, env, host):
    """Refuse a gh whose requests go to a local socket (`http_unix_socket`, for every host or for
    this one): whatever listens there can answer for GitHub. A failing read of it is refused too."""
    for args, scope in ((["config", "get", "http_unix_socket"], "for every host"),
                        (["config", "get", "-h", host, "http_unix_socket"], f"for {host}")):
        what = "gh " + " ".join(args)
        try:
            proc = _gh(run, root, env, args)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise Refused(f"`{what}` cannot be run ({exc}); cannot tell whether gh sends its "
                          "requests to a local http_unix_socket")
        if proc.returncode != 0:
            raise Refused(f"`{what}` failed (exit {proc.returncode}: {_last_line(proc)}); cannot "
                          "tell whether gh sends its requests to a local http_unix_socket")
        if (proc.stdout or "").strip():
            raise Refused(f"gh sends its requests to a local socket (http_unix_socket is set "
                          f"{scope}), which can answer for {host}; unset it, then rerun")


def check_remote_main(run, root, env, host, slug, commit):
    """Refuse unless main, read over REST, is one commit whose sha is this checkout's main."""
    where = f"{host}/{slug}"
    try:
        proc = _gh(run, root, env, ["api", f"--hostname={host}",
                                    f"repos/{slug}/git/ref/heads/main"])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Refused(f"cannot read main of {where} over REST: {exc}")
    if proc.returncode != 0:
        raise Refused(f"cannot read main of {where} over REST (gh exit {proc.returncode}): "
                      f"{_last_line(proc)}")
    text = (proc.stdout or "").strip()
    if not text:
        raise Refused(f"gh exited 0 with no output for main of {where}; an empty read is not main")
    try:
        data = json.loads(text, object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise Refused(f"main of {where} over REST is not valid JSON: {exc or type(exc).__name__}")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict) or obj.get("type") != "commit":
        raise Refused(f"main of {where} over REST is not a ref to one commit")
    sha = obj.get("sha")
    if not isinstance(sha, str) or not OID_RE.fullmatch(sha):
        raise Refused(f"main of {where} over REST names no commit sha")
    if sha != commit:
        raise Refused(f"main of {where} is {sha} over REST, but this checkout's {MAIN_REF} is "
                      f"{commit}; git fetch origin, then rerun")


def fetch_blockers(run, root, env, host, slug):
    argv = ["api", f"--hostname={host}",
            f"repos/{slug}/issues?state=open&labels={BLOCKER_LABEL}&per_page=100", "--paginate"]
    try:
        proc = _gh(run, root, env, argv)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Malformed(f"could not read open {BLOCKER_LABEL} issues: {exc}")
    if proc.returncode != 0:
        raise Malformed(f"could not read open {BLOCKER_LABEL} issues from {host}/{slug} (gh exit "
                        f"{proc.returncode}): {_last_line(proc)}")
    if not (proc.stdout or "").strip():
        raise Malformed(f"gh exited 0 with no output for the {BLOCKER_LABEL} list; an empty read "
                        "is not zero blockers")
    try:
        return _decode_pages(proc.stdout)
    except (ValueError, RecursionError) as exc:
        raise Malformed(f"gh returned unparsable JSON for the blocker list: {exc}")


def open_blockers(items):
    """Issue numbers from a list of REST issue objects, pull requests dropped. Raises Malformed."""
    if not isinstance(items, list):
        raise Malformed("the blocker list must be a JSON list of issue objects")
    numbers = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise Malformed(f"blocker entry #{i} is not an object")
        num = item.get("number")
        if isinstance(num, bool) or not isinstance(num, int):
            raise Malformed(f"blocker entry #{i} has no integer `number`")
        state = item.get("state")
        if not isinstance(state, str) or state.lower() not in ("open", "closed"):
            raise Malformed(f"blocker entry #{i} (#{num}) has state {state!r}; "
                            "expected open or closed")
        labels = item.get("labels")
        if not isinstance(labels, list) or not all(
                isinstance(lb, dict) and isinstance(lb.get("name"), str) for lb in labels):
            raise Malformed(f"blocker entry #{i} (#{num}) has no `labels` list of objects with a "
                            "`name`")
        if BLOCKER_LABEL not in {lb["name"].lower() for lb in labels}:
            raise Malformed(f"blocker entry #{i} (#{num}) does not carry {BLOCKER_LABEL}; the list "
                            "is not the blocker list")
        if "pull_request" in item:
            if not isinstance(item["pull_request"], dict):
                raise Malformed(f"blocker entry #{i} (#{num}) has a `pull_request` that is not an "
                                "object")
            continue  # a pull request, not an issue
        if state.lower() == "open":
            numbers.append(num)
    return sorted(set(numbers))


def decide(root, blockers_json=None, repo=None, run=None):
    """Return (lines, verdict). Raises Malformed or Refused. Every local read happens before any
    gh call; run is the gh runner (subprocess.run, resolved at call time)."""
    run = run or subprocess.run
    root = pathlib.Path(root)
    if not root.is_dir():
        raise Malformed(f"repo_root is not a directory: {root}")
    check_git(root)
    commit = resolve_main(root)
    card = read_json_at_main(root, commit, SCORECARD, "scorecard")
    if card is None:
        raise Malformed(f"scorecard not found at {MAIN_REF} ({commit})")
    reasons, notes = check_scorecard(card, lambda rel: at_main(root, commit, rel))
    reasons = check_definition(read_json_at_main(root, commit, DEFINITION, "definition")) + reasons
    notes.append(f"info: main is {MAIN_REF} at {commit}")
    tail = []
    if blockers_json is not None:
        items = _load_json(pathlib.Path(blockers_json), "blockers list")
        tail.append(OFFLINE_REASON.format(blockers_json))
        notes.append(f"info: blockers read offline from {blockers_json!r}")
    else:
        if repo:
            host, slug, note = repo_for(root, repo)
            if note:
                notes.append(note)
        else:
            host, slug = default_repo(root)
        env = _gh_env()
        check_gh_socket(run, root, env, host)
        check_remote_main(run, root, env, host, slug, commit)
        items = fetch_blockers(run, root, env, host, slug)
        notes.append(f"info: blockers read over REST from {host}/{slug}")
    for num in open_blockers(items):
        reasons.append(f"open {BLOCKER_LABEL}: #{num}")
    reasons += tail
    verdict = "NO-GO" if reasons else "GO"
    return reasons + notes, verdict


def main(argv=None, run=None):
    if os.name == "nt":
        print("decide.py: REFUSED: Windows is not supported; the checker refuses to run where "
              "os.name is 'nt' (CI runs it on Linux and macOS only)", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser(prog="decide.py", description=__doc__.split("\n\n")[0],
                                 epilog="Exit: 0 GO, 1 NO-GO, 2 refused, malformed input or "
                                        "usage (including --help).")
    ap.add_argument("repo_root")
    ap.add_argument("--blockers-json", metavar="PATH")
    ap.add_argument("--repo", metavar="OWNER/NAME")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2  # --help and usage errors alike: never the GO code
    try:
        if args.repo is not None and not REPO_RE.fullmatch(args.repo):
            raise Malformed(f"--repo must be OWNER/NAME (each starting with a letter, digit or "
                            f"`_`; letters, digits, `_`, `.`, `-`; at most 100), got {args.repo!r}")
        lines, verdict = decide(args.repo_root, args.blockers_json, args.repo,
                                run=run or subprocess.run)
    except Refused as exc:
        print(f"decide.py: REFUSED: {exc}", file=sys.stderr)
        return 2
    except Malformed as exc:
        print(f"decide.py: MALFORMED: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, RecursionError) as exc:
        print(f"decide.py: MALFORMED: unreadable input: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(verdict)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
