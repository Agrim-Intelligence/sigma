"""Fail-closed policy for shell strings supplied by repository configuration.

Repository files are input, not an operator authorization.  The only supported
opt-in is a Git-local value, intentionally absent from clones and commits:

    git -C <trusted-project> config --local sigma.allowRepositoryShellCommands true

The setting is project-scoped (so linked goal worktrees share the operator's
decision) and every read failure means "no".  This module deliberately has no
configuration-file fallback: accepting one would let an untrusted repository
authorize its own shell command.
"""

import pathlib
import subprocess


_KEY = "sigma.allowRepositoryShellCommands"


def repository_shell_commands_allowed(path):
    """Whether the operator made the documented Git-local opt-in for ``path``.

    `git config --local` reads metadata that is neither versioned nor copied by
    `git clone`. A non-repository path, missing Git, malformed boolean and all
    subprocess failures are refusals; this predicate must never turn an
    uncertain trust decision into execution permission.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(pathlib.Path(path)), "config", "--local", "--type=bool", _KEY],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def refusal_message():
    """One public recovery gesture, kept out of repository-controlled data."""
    return ("REFUSED: repository-configured shell command requires explicit operator trust; "
            "run `git -C <trusted-project> config --local "
            "sigma.allowRepositoryShellCommands true` after inspecting the project")
