"""Host and installed-plugin preflight for Codex's opt-in supervisor.

No process is started and no cache path is trusted until the enabled Codex
inventory, exact version, and (for Git installs) commit all agree.
"""
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys


def codex_session(env=None):
    env = os.environ if env is None else env
    if env.get("SIGMA_HOST") in ("codex", "claude"):
        return env["SIGMA_HOST"] == "codex"
    return bool(
        env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID")) and not bool(
        env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"))


def detect_host(env=None):
    env = os.environ if env is None else env
    override = env.get("SIGMA_HOST")
    if override:
        if override not in ("codex", "claude"):
            raise ValueError("SIGMA_HOST must be codex or claude")
        return override
    codex = bool(env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID"))
    claude = bool(env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"))
    if codex and claude:
        raise ValueError("both Claude and Codex host markers are present; set SIGMA_HOST")
    return "codex" if codex else "claude"


def codex_environment(env=None):
    child = dict(os.environ if env is None else env)
    for key in tuple(child):
        if key.startswith("CLAUDE"):
            child.pop(key, None)
    child["SIGMA_HOST"] = "codex"
    child["PYTHONDONTWRITEBYTECODE"] = "1"
    return child


def _version(value):
    parts = str(value).split(".")
    if len(parts) < 3 or not all(p.isdigit() for p in parts[:3]):
        raise ValueError("invalid Sigma plugin version")
    return tuple(map(int, parts[:3]))


def _valid_skill(root, entry):
    manifest = root / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Sigma plugin manifest unreadable: {exc}") from exc
    if data.get("name") != "sigmaloop" or data.get("version") != entry.get("version"):
        raise ValueError("Sigma plugin manifest does not match enabled inventory")
    skill = root / "skills" / "sigma-loop" / "SKILL.md"
    if not skill.is_file() or not os.access(skill, os.R_OK):
        raise ValueError("Sigma loop skill is not readable in enabled install")
    return skill.resolve()


def _git_env():
    """Do not let the invoking checkout's Git environment redirect install inspection."""
    child = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    child["GIT_NO_REPLACE_OBJECTS"] = "1"
    return child


def _clean_git_head(root):
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          env=_git_env(), capture_output=True, text=True, timeout=5, check=False)
    if head.returncode or not head.stdout.strip():
        raise ValueError("installed Sigma Git commit is unavailable")
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                             "--untracked-files=all"], env=_git_env(), capture_output=True, text=True,
                            timeout=10, check=False)
    if status.returncode or status.stdout.strip():
        raise ValueError("installed Sigma plugin is modified or dirty")
    tree = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "-z", "HEAD"],
                          env=_git_env(), capture_output=True, timeout=20, check=False)
    if tree.returncode:
        raise ValueError("installed Sigma Git tree is unavailable")
    tracked = {}
    for record in tree.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_name = record.split(b"\t", 1)
            mode, kind, oid = metadata.split()
        except ValueError as exc:
            raise ValueError("installed Sigma Git tree is malformed") from exc
        if kind != b"blob" or mode not in (b"100644", b"100755") or len(oid) != 40:
            raise ValueError("installed Sigma Git tree contains an unsupported entry")
        tracked[os.fsdecode(raw_name)] = (oid.decode("ascii"), mode)
    observed = set()
    for parent, dirs, files in os.walk(root, followlinks=False):
        if pathlib.Path(parent) == root and ".git" in dirs:
            dirs.remove(".git")
        for name in tuple(dirs):
            path = pathlib.Path(parent) / name
            if path.is_symlink():
                observed.add(path.relative_to(root).as_posix())
                dirs.remove(name)
        for name in files:
            path = pathlib.Path(parent) / name
            if pathlib.Path(parent) == root and name == ".git":
                continue
            observed.add(path.relative_to(root).as_posix())
    if observed != set(tracked):
        raise ValueError("installed Sigma plugin has missing or untracked content")
    resolved_root = root.resolve()
    if sys.platform == "win32":
        raise ValueError("Codex Git install mode verification is unsupported on Windows")
    for name, (oid, mode) in tracked.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(resolved_root):
            raise ValueError("installed Sigma plugin has unsafe or missing content")
        executable = bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        if executable != (mode == b"100755"):
            raise ValueError("installed Sigma plugin has modified executable mode")
        size = path.stat().st_size
        digest = hashlib.sha1(f"blob {size}\0".encode("ascii"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != oid:
            raise ValueError("installed Sigma plugin has modified content despite a clean index")
    return head.stdout.strip()


def resolve_install(inventory, codex_home, floor=(1, 0, 0)):
    entries = [e for e in inventory.get("installed", [])
               if isinstance(e, dict) and e.get("pluginId") == "sigmaloop@sigmaloop"]
    if len(entries) != 1:
        raise ValueError("expected exactly one installed sigmaloop@sigmaloop plugin")
    entry = entries[0]
    if not entry.get("installed") or not entry.get("enabled"):
        raise ValueError("Sigma plugin is not installed and enabled in Codex")
    if _version(entry.get("version")) < tuple(floor):
        raise ValueError("enabled Sigma plugin is below required version")
    source = entry.get("source") or {}
    home = pathlib.Path(codex_home).resolve()
    expected_sha = source.get("sha")
    if source.get("source") == "local":
        raw = source.get("path")
        if not raw:
            raise ValueError("local plugin inventory has no path")
        root = pathlib.Path(raw).resolve()
        marketplace_source = entry.get("marketplaceSource") or {}
        if marketplace_source.get("sourceType") != "git":
            return _valid_skill(root, entry)
        if entry.get("marketplaceName") != "sigmaloop":
            raise ValueError("unexpected Sigma marketplace name")
        expected_root = (home / ".tmp" / "marketplaces" / "sigmaloop").resolve()
        if root != expected_root or not root.is_relative_to(home):
            raise ValueError("Git marketplace source is outside the current Codex home")
        _valid_skill(root, entry)
        marketplace_sha = _clean_git_head(root)
        if expected_sha and expected_sha != marketplace_sha:
            raise ValueError("Sigma inventory commit differs from Git marketplace checkout")
        expected_sha = marketplace_sha
    elif source.get("source") != "git":
        raise ValueError("Sigma plugin source cannot be bound to an installed path")
    if not expected_sha:
        raise ValueError("Sigma Git install has no commit")
    candidates = []
    cache = home / "plugins" / "cache"
    resolved_cache = cache.resolve()
    if not resolved_cache.is_relative_to(home):
        raise ValueError("Codex plugin cache is outside the current Codex home")
    for root in cache.glob(f"*/sigmaloop/{entry['version']}"):
        if not root.is_dir() or not root.resolve().is_relative_to(resolved_cache):
            continue
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                env=_git_env(), capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0 and result.stdout.strip() == expected_sha:
            candidates.append(root)
    if len(candidates) != 1:
        raise ValueError("expected exactly one cached Sigma install matching enabled Git SHA")
    root = candidates[0]
    skill = _valid_skill(root, entry)
    # HEAD alone is not a content guarantee: a cache can have tracked edits or
    # injected untracked files while still reporting the inventory's SHA.
    if _clean_git_head(root) != expected_sha:
        raise ValueError("cached Sigma plugin commit changed during validation")
    return skill


def installed_skill(env=None, codex_executable=None):
    env = dict(os.environ if env is None else env)
    executable = codex_executable or shutil.which("codex", path=env.get("PATH"))
    if not executable:
        raise ValueError("codex CLI is not on PATH")
    result = subprocess.run([executable, "plugin", "list", "--json"], env=env,
                            capture_output=True, text=True, timeout=20, check=False)
    if result.returncode:
        raise ValueError(f"codex plugin list failed (exit {result.returncode})")
    try:
        inventory = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("codex plugin inventory is not JSON") from exc
    home = env.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex")
    return executable, resolve_install(inventory, home)


def build_codex_command(executable, repo, skill, final_path=None):
    prompt = (f"Read the installed Sigma skill at {skill} and follow it. "
              "Pick the next eligible goal from the repository's real backlog and .sdlc state. "
              "Run one bounded Sigma loop session, honoring its stop and safety rules. "
              "If you cannot read that file, write CODEX_SIGMA_SKILL_UNAVAILABLE as its own final line "
              "and stop without claiming progress.")
    command = [str(executable), "exec", "--approve-for-me", "--cd", str(repo)]
    if final_path is not None:
        command.extend(["--output-last-message", str(final_path)])
    return command + [prompt]


def lock_file(path):
    """Hold a process-backed nonblocking lock; caller must retain the returned fd."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = path.open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            fd.seek(0)
            if not fd.read(1):
                fd.write(b"0")
                fd.flush()
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        fd.close()
        raise ValueError("Codex supervisor already running or OS lock unavailable")
    return fd
