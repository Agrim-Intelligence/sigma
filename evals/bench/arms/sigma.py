"""The clean plugin export loaded by the Sigma arm: three trees of a pinned commit, never the repository."""
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile

try:
    from .common import ArmRefusal
except ImportError:  # loaded by path rather than as a package
    from common import ArmRefusal


EXPORT_PATHS = (".claude-plugin", "skills", "hooks")
FORBIDDEN_COMPONENTS = frozenset({"evals", "tests"})
GIT_TIMEOUT_SECONDS = 120


def _git(repo, *args):
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False,
                              timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArmRefusal("git could not run: %s" % exc) from exc


def resolve_commit(repo, revision):
    """The full sha of ``revision`` in ``repo``; the export is pinned to it, never to a moving name."""
    done = _git(repo, "rev-parse", "--verify", "--quiet", revision + "^{commit}")
    sha = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode or len(sha) != 40:
        raise ArmRefusal("--sigma-commit %r does not name a commit in the repository" % revision)
    return sha


def extract_tar(fileobj, dest):
    """Write the three export trees from a tar stream.

    Everything is validated BEFORE anything is written, so a refusal leaves ``dest`` untouched.
    Links, devices, absolute and ``..`` paths, any entry outside the three trees, and any path with an
    ``evals`` or ``tests`` component are refused.  Executable bits are preserved (hooks need them).
    """
    with tarfile.open(fileobj=fileobj, mode="r:") as tar:
        members = tar.getmembers()
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith("/") or ".." in parts or not parts:
                raise ArmRefusal("export entry %r escapes the export directory" % member.name)
            if not (member.isreg() or member.isdir()):
                raise ArmRefusal("export entry %r is a link or special file" % member.name)
            if parts[0] not in EXPORT_PATHS:
                raise ArmRefusal("export entry %r is outside %s" % (member.name, ", ".join(EXPORT_PATHS)))
            if FORBIDDEN_COMPONENTS & set(parts):
                raise ArmRefusal("export entry %r contains an evals or tests path" % member.name)
        dest = Path(dest)
        for member in members:
            target = dest.joinpath(*PurePosixPath(member.name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
    return len([m for m in members if m.isreg()])


def export_plugin(repo, commit, dest):
    """``git archive <commit> .claude-plugin skills hooks`` into a fresh ``dest``; returns its facts."""
    sha = resolve_commit(repo, commit)
    done = _git(repo, "archive", "--format=tar", sha, "--", *EXPORT_PATHS)
    if done.returncode:
        raise ArmRefusal("git archive failed for %s: %s" % (
            sha, done.stderr.decode("utf-8", "replace").strip()[:200]))
    dest = Path(dest)
    if dest.exists():
        raise ArmRefusal("export directory already exists")
    files = extract_tar(io.BytesIO(done.stdout), dest)
    return {"commit": sha, "files": files,
            "top_level": sorted(path.name for path in dest.iterdir())}
