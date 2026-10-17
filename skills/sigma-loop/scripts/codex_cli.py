"""#1648: the one shared Codex CLI resolver. Standalone library, stdlib only, no side effects.

Order, each rung exact: (a) SIGMA_CODEX_BIN (invalid -> refuse, never fall through), (b) a PATH hit,
(c) the ChatGPT-app copy on darwin. Two different copies (different realpath) refuse as ambiguous; the
lever is SIGMA_CODEX_BIN. The candidate is never executed: stat and X_OK only.
"""
import json
import os
import shutil
import sys
from pathlib import Path

APP_PATH = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
ENV_BIN = "SIGMA_CODEX_BIN"
_LEVER = f"set {ENV_BIN}=<absolute path to the codex binary>"


class CodexCliError(ValueError):
    """`.reason` is one of not-found, override-invalid, ambiguous; `.paths` the paths involved."""

    def __init__(self, reason, message="", paths=()):
        super().__init__(message)
        self.reason = reason
        self.paths = tuple(paths)


def _executable_file(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def resolve(env=None, which=None, app_path=APP_PATH, platform=None):
    env = os.environ if env is None else env
    override = env.get(ENV_BIN)
    if override:
        if not (os.path.isabs(override) and _executable_file(override)):
            raise CodexCliError("override-invalid",
                                f"{ENV_BIN}={override!r} is not an absolute path to an executable file; "
                                f"{_LEVER} or unset it", (override,))
        return override
    which = which or shutil.which
    hit = which("codex", path=env["PATH"]) if "PATH" in env else which("codex")
    on_path = os.path.abspath(hit) if hit else None
    plat = sys.platform if platform is None else platform
    app = app_path if (app_path and plat == "darwin" and _executable_file(app_path)) else None
    if on_path and app:
        if os.path.realpath(on_path) != os.path.realpath(app):
            raise CodexCliError("ambiguous",
                                f"two different Codex CLIs found: {on_path} (PATH) and {app} (ChatGPT app); "
                                f"{_LEVER} to choose one", (on_path, app))
        return on_path
    if on_path or app:
        return on_path or app
    raise CodexCliError("not-found", f"Codex CLI not on PATH and not in the ChatGPT app; install it or {_LEVER}")


def _parts(version):
    try:
        parts = str(version).strip().split("-")[0].split("+")[0].split(".")
        return tuple(int(p) for p in parts[:3]) if len(parts) >= 3 else None
    except ValueError:
        return None


def skew(a, b):
    """True when the first three dotted integers differ, False when equal, None when either is unparsable."""
    pa, pb = _parts(a), _parts(b)
    return None if pa is None or pb is None else pa != pb


def own_version(root=None):
    root = Path(__file__).resolve().parents[3] if root is None else Path(root)
    try:
        version = json.loads((root / ".claude-plugin" / "plugin.json").read_text()).get("version")
        return version if isinstance(version, str) and version else None
    except (OSError, ValueError, AttributeError):
        return None
