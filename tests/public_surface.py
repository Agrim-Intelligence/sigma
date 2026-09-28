"""The public surface: the one list of files the three boundary guards scan (#2584, S1-G11).

Imported as a plain sibling module (`import public_surface`) -- pytest puts tests/ on `sys.path`
for its own test modules, and a guard run as a script has its own directory there too.

TWO MODES, chosen by whether `<root>/tools/public-manifest.txt` exists:
  - MONOREPO (the manifest exists): the manifest's selection of the files `git add -A` would commit
    -- `git ls-files --cached --others --exclude-standard`, minus paths that no longer exist -- so
    a goal's new, not-yet-committed file is scanned during verify, exactly as it would be committed.
  - PUBLIC TREE (no manifest): every tracked (`--cached`) path that exists. In the public snapshot,
    which the builder commits before any guard runs, that is exactly the copied set.
Losing the manifest in the monorepo fails toward MORE scanning, never less: the whole repository,
private side included, is scanned and the guards go loudly red.

REFUSALS (`SurfaceError`), never a silent half-answer: a root that is not its own git toplevel
(it could otherwise read an enclosing checkout); and, in the manifest, a glob (`*`, `?`, `[`), a
negation (`!`), a leading `/`, a `\\`, an empty, `.` or `..` segment, a duplicate, and an entry that
selects nothing -- each named by its line.

MANIFEST SYNTAX: one path per line, relative to the root; blank lines and `#` lines are ignored. A
line ending in `/` selects every candidate under that directory, at a segment boundary (`a/` never
selects `ab/x`); any other line must equal one candidate file.

Its git calls are short-lived, synchronous and read-only; none is backgrounded. The dependency
points one way: private tooling (the snapshot builder) may load this core helper by path; no core
test imports a private tool. S2-G3 (#2589) deletes the monorepo mode together with the manifest.
"""
import os
import pathlib
import subprocess

#: Where the allowlist lives, relative to the root.
MANIFEST = ("tools", "public-manifest.txt")

_GLOB_CHARS = ("*", "?", "[")

#: Variables that would point a git call at some other repository than `root`.
_GIT_REDIRECTS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")


class SurfaceError(Exception):
    """The surface cannot be computed honestly: a bad manifest, or a root git does not own."""


def parse_manifest(text):
    """The manifest's entries, in order. Refuses bad syntax, naming the line."""
    entries, seen = [], {}
    for number, raw in enumerate(text.splitlines(), 1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        def refuse(why):
            raise SurfaceError("public manifest line %d: %r %s" % (number, entry, why))
        if any(c in entry for c in _GLOB_CHARS):
            refuse("is a glob; list directories (ending in /) and files only")
        if entry.startswith("!"):
            refuse("is a negation; there are none -- leave the path out instead")
        if entry.startswith("/"):
            refuse("is absolute; entries are relative to the repository root")
        if "\\" in entry:
            refuse("holds a backslash; separators are /")
        segments = entry[:-1].split("/") if entry.endswith("/") else entry.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            refuse("holds an empty, . or .. segment")
        if entry in seen:
            refuse("repeats line %d" % seen[entry])
        seen[entry] = number
        entries.append((number, entry))
    return [entry for _n, entry in entries]


def select(entries, candidates, numbers=None):
    """The sorted candidates the entries select. Refuses an entry that selects nothing."""
    chosen = set()
    for i, entry in enumerate(entries):
        if entry.endswith("/"):
            hit = [c for c in candidates if c.startswith(entry)]
        else:
            hit = [c for c in candidates if c == entry]
        if not hit:
            where = "line %d: " % numbers[i] if numbers else ""
            raise SurfaceError("public manifest %s%r selects nothing (directories end with /)"
                               % (where, entry))
        chosen.update(hit)
    return sorted(chosen)


def _git(root, *args):
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECTS}
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          env=env)


def _ls_files(root, *flags):
    proc = _git(root, "ls-files", "-z", *flags)
    if proc.returncode != 0:
        raise SurfaceError("git ls-files failed in %s: %s" % (root, proc.stderr.strip()))
    return sorted({p for p in proc.stdout.split("\0") if p and (root / p).exists()})


def public_files(root):
    """The public surface of `root`, as sorted posix relpaths (see the module docstring)."""
    root = pathlib.Path(root)
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or pathlib.Path(top.stdout.strip()).resolve() != root.resolve():
        raise SurfaceError("%s is not its own git toplevel (git says %r); refusing to read "
                           "another checkout's files" % (root, (top.stdout or top.stderr).strip()))
    manifest = root.joinpath(*MANIFEST)
    if not manifest.is_file():
        return _ls_files(root, "--cached")
    text = manifest.read_text(encoding="utf-8")
    entries = parse_manifest(text)
    numbers = [n for n, raw in enumerate(text.splitlines(), 1)
               if raw.strip() and not raw.strip().startswith("#")]
    return select(entries, _ls_files(root, "--cached", "--others", "--exclude-standard"), numbers)


def plant(root, files, manifest=None):
    """For the guards' own planted tests: write `files` ({relpath: text}) under `root`, and the
    manifest when given, then `git init` (idempotent) and `git add` them, so `root` is a real git
    tree whose surface these files are."""
    root = pathlib.Path(root)
    written = dict(files)
    if manifest is not None:
        written["/".join(MANIFEST)] = manifest
    for rel, text in written.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for args in (("-c", "init.defaultBranch=main", "init", "-q"),
                 ("-c", "core.excludesFile=", "add", "-f", "--", *written)):
        proc = _git(root, *args)
        if proc.returncode != 0:
            raise SurfaceError("git %s failed in %s: %s" % (args[-1], root, proc.stderr.strip()))
    return root
