"""Fail-closed policy for shell strings supplied by repository configuration.

Repository files are input, not an operator authorization.  The only supported
opt-in is a Git-local value, intentionally absent from clones and commits:

    git -C <trusted-project> config --local sigma.allowRepositoryShellCommands true

The setting is project-scoped (so linked goal worktrees share the operator's
decision) and every read failure means "no".  This module deliberately has no
configuration-file fallback: accepting one would let an untrusted repository
authorize its own shell command.

#2066 adds one narrower source, for the VERIFY PATH only (`loop.py verify` and
`loop.py red`): an operator-owned environment value, `SIGMA_TRUSTED_VERIFY_SHA256`,
equal to `approval_value(<origin owner/name>, <command>)` for every command that
path is about to run.  `verify_decision` asks the Git-local opt-in first, then the
environment value; `repository_shell_commands_allowed` never reads the environment,
so no other repository command is ever granted by it.  Sigma cannot tell whether a
person or an agent exported the value: it belongs in the environment's own settings
(docs/cloud-sessions.md), and every doubt below is a refusal.

#2085 adds one more reason, `goal-command`, to `verify_decision`: the approval covers the repository's own
`verify.command` and the goal adds a command of its own, which one approval value can never cover. It only changes
which refusal is named. `goal_command_conflict` is the same question asked once at RECORD time
(`acceptance.py record`); it can only REFUSE, never grant, and fails open on every doubt, because verify time
stays the authority.
"""

import collections
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess


_KEY = "sigma.allowRepositoryShellCommands"

TRUSTED_VERIFY_ENV = "SIGMA_TRUSTED_VERIFY_SHA256"
_DOMAIN = "sigma-verify-approval/v1\n"
_VALUE = re.compile(r"[0-9a-f]{64}")
_IDENTITY = re.compile(r"\A[a-z0-9_.-]+/[a-z0-9_.-]+\Z")
#: Committed files a host loads into the process environment declaratively. A tracked one that names
#: TRUSTED_VERIFY_ENV means the repository may have approved its own command, so the value is refused. Matched
#: without regard to case: on a case-insensitive filesystem (macOS by default) the host opens `.Claude/...` too.
#: A tripwire, not a boundary: a committed env block can already run code (PATH, BASH_ENV), and the mise list is
#: taken from its documentation, not exercised.
HOST_ENV_PATHS = (
    ".claude/settings.json", ".claude/settings.local.json",                  # Claude Code `env`
    ".devcontainer.json", ".devcontainer/devcontainer.json",
    ".devcontainer/*/devcontainer.json",                                     # containerEnv / remoteEnv
    ".vscode/settings.json",                                                 # terminal.integrated.env.*
    ".env", ".envrc",                                                        # dotenv loaders, direnv
    "mise.toml", ".mise.toml", "mise.local.toml", ".mise.local.toml", "mise.*.toml", ".mise.*.toml",
    "mise/config.toml", ".mise/config.toml", ".config/mise.toml", ".config/mise/config.toml",
    ".config/mise/conf.d/*.toml", ".rtx.toml",                               # mise [env]
    ".codex/config.toml",                                                    # Codex shell_environment_policy
)
#: Every directory on the way to a listed path. One committed as a symlink or gitlink hides its files from the
#: index, so such an entry is refused rather than looked through.
_HOST_ENV_PARENTS = tuple(sorted({"/".join(p.split("/")[:i]) for p in HOST_ENV_PATHS
                                  for i in range(1, p.count("/") + 1)}))
_HOST_ENV_MAX_BYTES = 1 << 20
_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|U([0-9a-fA-F]{8})|x([0-9a-fA-F]{2}))")
#: The variables that point git at another repository: bounded_run.LOCATION_VARS, duplicated because that module
#: imports this one (a test pins the two equal).
_LOCATION_VARS = frozenset((
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX"))
_GIT_TIMEOUT = 5

Decision = collections.namedtuple("Decision", "source reason detail")


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


_HEAD = "REFUSED: repository-configured shell command requires explicit operator trust"


def refusal_message():
    """One public recovery gesture, kept out of repository-controlled data."""
    return (_HEAD + "; run `git -C <trusted-project> config --local "
            "sigma.allowRepositoryShellCommands true` after inspecting the project")


def approval_value(identity, command):
    """The value an operator sets in TRUSTED_VERIFY_ENV to approve ``command`` in repository ``identity``."""
    return hashlib.sha256((_DOMAIN + identity + "\n" + command).encode("utf-8")).hexdigest()


class _GitFailed(Exception):
    pass


def _git(cwd, *args):
    """git with no inherited GIT_* variable and no global config, so neither a committed `env` block nor an export
    can choose the index, work tree, config or pathspec rules a read uses. System config is admin-owned and kept
    (a container's `safe.directory` usually lives there)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")     # no red control: the reads it guards
                                                                         # are --local or index-only today
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          timeout=_GIT_TIMEOUT, check=False, stdin=subprocess.DEVNULL, env=env)


def _checkout(path):
    """-> (toplevel, main checkout or None), absolute, for ``path``; None when git cannot say."""
    done = _git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    lines = done.stdout.splitlines()
    if done.returncode != 0 or len(lines) != 2:
        return None
    common = pathlib.Path(lines[1])
    return pathlib.Path(lines[0]), (common.parent if common.name == ".git" else None)


def _identity_from_url(url):
    for sep in ("github.com:", "github.com/"):
        if sep in url:
            url = url.split(sep, 1)[1]
            break
    else:
        if ":" in url and "/" in url:        # host-alias, e.g. github.com-work:owner/repo
            url = url.split(":", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    parts = [p for p in url.split("/") if p]
    if len(parts) < 2 or {parts[-2], parts[-1]} & {".", ".."}:
        return None
    identity = "/".join(parts[-2:]).lower()
    return identity if _IDENTITY.match(identity) else None


def repository_identity(path):
    """``owner/name`` of the one local `remote.origin.url` of ``path``, lower-cased, or None. `--local` ignores
    `url.*.insteadOf` and any global, system or environment-injected config; two values are no answer."""
    try:
        done = _git(path, "config", "--local", "--get-all", "remote.origin.url")
    except (OSError, subprocess.SubprocessError):
        return None
    urls = done.stdout.splitlines()
    if done.returncode != 0 or len(urls) != 1:
        return None
    return _identity_from_url(urls[0].strip())


def _vetoed(path):
    """An explicit Git-local `false`, or a value git cannot read as a boolean (exit 128), is the operator's "no"."""
    done = _git(path, "config", "--local", "--type=bool", _KEY)
    if done.returncode == 1:
        return False
    if done.returncode == 0:
        return done.stdout.strip().lower() != "true"
    return True


def _names_variable(text):
    decoded = _ESCAPE.sub(lambda m: chr(int(m.group(1) or m.group(2) or m.group(3), 16)), text)
    return TRUSTED_VERIFY_ENV.casefold() in decoded.casefold()


def _matches(rel, patterns):
    parts = rel.casefold().split("/")
    return any(len(parts) == p.count("/") + 1 and all(map(fnmatch.fnmatchcase, parts, p.casefold().split("/")))
               for p in patterns)


def _pathspecs():
    for rel in HOST_ENV_PATHS + _HOST_ENV_PARENTS:
        magic = "icase,glob" if "*" in rel else "icase,literal"
        yield ":(top,%s)%s" % (magic, rel)
        yield ":(%s)%s" % (magic, rel)


def _sourced_in(cwd, top):
    """The first tracked host-config path in ``cwd``'s repository (toplevel ``top``), listed from the top and
    relative to ``cwd``, that names the variable or is not a plain text file inside ``top``; else None."""
    done = _git(cwd, "ls-files", "-s", "-z", "--full-name", "--", *_pathspecs())
    if done.returncode != 0:
        raise _GitFailed("ls-files")
    shown = _git(cwd, "rev-parse", "--show-prefix")    # git's own spelling of cwd, as `--full-name` lists paths
    if shown.returncode != 0:
        raise _GitFailed("rev-parse")
    prefix = shown.stdout.rstrip("\n")                  # "" at the top, else "dir/sub/"
    real_top = os.path.realpath(top)
    for entry in done.stdout.split("\0"):
        if not entry:
            continue
        meta, rel = entry.split("\t", 1)
        linked = meta.split(" ", 1)[0] in ("120000", "160000")
        local = [rel] + ([rel[len(prefix):]] if prefix and rel.startswith(prefix) else [])
        if linked and any(_matches(r, _HOST_ENV_PARENTS) for r in local):
            return rel
        if not any(_matches(r, HOST_ENV_PATHS) for r in local):
            continue                          # a file under a listed directory that no host loads as env
        full = pathlib.Path(top) / rel
        if linked or os.path.commonpath([real_top, os.path.realpath(full)]) != real_top:
            return rel
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            continue                          # tracked but absent: nothing for a host to load
        if not stat.S_ISREG(st.st_mode) or st.st_size > _HOST_ENV_MAX_BYTES:
            return rel
        try:
            text = full.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return rel
        if _names_variable(text):
            return rel
    return None


def _repository_sourced(path, found, roots):
    """Check the verify tree, its main checkout, and each of ``roots`` with its own main checkout, each listing
    joined to its own toplevel."""
    places = [(path, found)]
    for cwd in roots:
        other = _checkout(cwd)
        if other is None:
            raise _GitFailed("rev-parse")
        places.append((cwd, other))
    seen = set()
    for cwd, (top, main) in places:
        for place, place_top in ((cwd, top), (main, main)):
            if place is None or os.path.realpath(place) in seen:
                continue
            seen.add(os.path.realpath(place))
            hit = _sourced_in(place, place_top)
            if hit:
                return hit
    return None


def environment_approval(path, commands, environ=None, roots=()):
    """-> (reason, detail). Only ("match", identity) approves; every other reason refuses. Nothing spawns until the
    value is well formed. Total: any exception is ("error", None)."""
    try:
        env = os.environ if environ is None else environ
        value = env.get(TRUSTED_VERIFY_ENV)
        if value is None:
            return "unset", None
        if value == "":
            return "empty", None
        if not isinstance(value, str) or not _VALUE.fullmatch(value):
            return "malformed", None
        commands = list(commands)
        if not commands or not all(isinstance(c, str) and c for c in commands):
            return "no-command", None
        located = sorted(name for name in _LOCATION_VARS if name in env)
        if located:
            return "location-variable", ", ".join(located)
        found = _checkout(path)
        if found is None:
            return "no-identity", None
        if _vetoed(path):
            return "vetoed", None
        identity = repository_identity(path)
        if identity is None:
            return "no-identity", None
        sourced = _repository_sourced(path, found, roots)
        if sourced:
            return "repository-sourced", sourced
        if all(approval_value(identity, c) == value for c in commands):
            return "match", identity
        return "mismatch", identity
    except Exception:                        # noqa: BLE001 - any doubt is a refusal
        return "error", None


def _covers_repository_alone(identity, repository_commands, environ):
    """Whether the environment value approves every one of ``repository_commands`` in ``identity`` (and there is
    at least one). Pure hashing, no git call. Any doubt is False, which leaves the reason `mismatch`."""
    try:
        env = os.environ if environ is None else environ
        value = env.get(TRUSTED_VERIFY_ENV)
        repository_commands = list(repository_commands or ())
        return bool(repository_commands) and all(
            approval_value(identity, c) == value for c in repository_commands)
    except Exception:                        # noqa: BLE001 - any doubt keeps the generic reason
        return False


def verify_decision(path, commands, environ=None, roots=(), *, repository_commands=()):
    """Who grants the verify path to run ``commands`` in ``path``: Decision(source, reason, detail), source
    "git-local", "environment" or None (refused). Evaluated once; the refusal is built from the same Decision.

    ``repository_commands`` (#2085) are the commands the repository itself declares. They change only the NAME of a
    refusal: a `mismatch` where the value approves every one of them becomes `goal-command`, because then the
    command that voids the approval is the goal's own. Whenever that reason comes back, some command in
    ``commands`` is outside ``repository_commands`` (a mismatch means one command hashes to something else, and
    every repository command hashes to the value), so no separate "the goal added a command" test is needed here."""
    if repository_shell_commands_allowed(path):
        return Decision("git-local", "git-local", None)
    reason, detail = environment_approval(path, commands, environ, roots)
    if reason == "mismatch" and _covers_repository_alone(detail, repository_commands, environ):
        reason = "goal-command"
    return Decision("environment" if reason == "match" else None, reason, detail)


_CLAUSES = {
    "empty": "it is set but empty",
    "malformed": "it is set but is not exactly one 64-character lowercase hex value",
    "no-command": "no command was declared for it to approve",
    "location-variable": "%s set in the process environment would send git to another repository",
    "no-identity": "this checkout's one local origin URL does not name an owner/name repository",
    "vetoed": "this checkout's git-local sigma.allowRepositoryShellCommands is false or not a boolean",
    "repository-sourced": "the tracked file %s names it, and a repository cannot approve its own command",
    "mismatch": "it does not approve every command about to run in %s (the command changed, or the goal adds its own)",
    "runner-command": "it does not cover a runner command (a `tests.runners` template named by the plan's ## Tests): "
                      "only the git-local grant above does, the approval hashes the verify command(s) alone",
    "error": "it could not be checked",
}


DEFAULT_GOAL_REMEDY = "remove the goal's own verify_command"


def goal_command_text(identity, remedy):
    """THE text of the `goal-command` refusal, built once for both askers: `verify_refusal_message` (verify and
    red) and `goal_command_conflict` (record time). The goal's own fix comes first, ahead of the git-local route,
    because the operator route is not for an unattended run and the goal's fix is. Never the variable's value."""
    return ("%s is not used: it approves only this repository's verify.command, and this goal adds its own "
            "command, so it does not approve every command about to run in %s (one approval value can never "
            "cover two commands). 1. %s (the goal's own command then stops running, and the goal is verified by the "
            "repository's verify.command alone: longer, not weaker, where the goal's command only focused it; a check "
            "the repository's verify.command does not prove belongs in option 2, folded into verify.command). "
            "2. The operator folds the check into "
            "verify.command and re-approves that value, or runs `git -C <trusted-project> config --local %s true` "
            "after inspecting the project (never an unattended run); only the operator sets %s, in the "
            "environment's own settings" % (
                TRUSTED_VERIFY_ENV, json.dumps(str(identity)), remedy or DEFAULT_GOAL_REMEDY, _KEY,
                TRUSTED_VERIFY_ENV))


def verify_refusal_message(decision, goal_remedy=None):
    """refusal_message(), plus one clause naming why the environment value did not grant; unchanged when it is
    unset. Never the variable's value, and never the value to set. For `goal-command` the text leads with
    ``goal_remedy`` (the caller knows where the goal's command lives) rather than with the git-local route."""
    if decision.reason == "goal-command":
        return "%s; %s" % (_HEAD, goal_command_text(decision.detail, goal_remedy))
    clause = _CLAUSES.get(decision.reason)
    if clause is None:
        return refusal_message()
    if "%s" in clause:
        clause %= (json.dumps(str(decision.detail)),)
    return "%s; %s is not used: %s; only the operator sets it, in the environment's own settings" % (
        refusal_message(), TRUSTED_VERIFY_ENV, clause)


def goal_command_conflict(path, commands, repository_commands, environ=None, remedy=None):
    """Record time (#2085): the `goal-command` refusal text if recording ``commands`` would make verify refuse for
    that reason, else None. REFUSAL-ONLY and total: a granted decision, any other reason and every exception
    return None, so it can never grant a command and never wedge `record` (verify stays the authority).

    Cheap before it is exact: nothing spawns unless the variable is a well-formed value AND the repository declares
    a command AND the goal adds one outside it, so an environment without the approval pays no git call."""
    try:
        env = os.environ if environ is None else environ
        value = env.get(TRUSTED_VERIFY_ENV)
        if not isinstance(value, str) or not _VALUE.fullmatch(value):
            return None
        if not repository_commands:
            return None
        if all(c in repository_commands for c in commands):
            return None
        decision = verify_decision(path, commands, env, repository_commands=repository_commands)
        if decision.reason != "goal-command":
            return None
        return "verify_command not recorded: " + goal_command_text(decision.detail, remedy)
    except Exception:                        # noqa: BLE001 - fail open: verify time decides
        return None
