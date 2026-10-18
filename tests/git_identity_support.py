"""#1572: the one shared, monkeypatch-scoped git committer identity for tests that override HOME. No test lives
here, and nothing here runs at import time.

Why `GIT_CONFIG_GLOBAL` and not a `.gitconfig` under HOME: git reads `GIT_CONFIG_GLOBAL` INSTEAD of
`$HOME/.gitconfig`, so under the documented no-identity gesture (`GIT_CONFIG_GLOBAL=/dev/null`) a HOME `.gitconfig` is
invisible and the commit still refuses with `Committer identity unknown`. Pointing `GIT_CONFIG_GLOBAL` at a file the
test owns is the only identity source that survives that gesture without setting `GIT_AUTHOR_*`/`GIT_COMMITTER_*`
(process-wide identity env breaks `tests/test_ledger_single_branch.py`)."""
import ast
import pathlib
import subprocess

IDENTITY_VARS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL")


def scoped_git_identity(root, monkeypatch, name="t", email="t@example.com") -> pathlib.Path:
    """Give every git this test runs (in-process and in child processes) the identity `name <email>`, scoped to the
    test by `monkeypatch`.

    1. writes `root/"git-identity.gitconfig"` containing only a `[user]` name/email (creates `root` if needed);
    2. `monkeypatch.setenv("GIT_CONFIG_GLOBAL", <that file>)`;
    3. self-verifies with `git config --global --get user.email` in the inherited env and raises AssertionError naming
       `GIT_CONFIG_GLOBAL` and `git >= 2.32` when git does not answer `email` -- a git that ignores the variable is
       refused loudly, never trusted weakly;
    4. returns the file path.

    It does not touch HOME (the caller owns it), never sets `GIT_AUTHOR_*`/`GIT_COMMITTER_*`, and does not set
    `GIT_CONFIG_NOSYSTEM`/`GIT_CONFIG_SYSTEM` (for `user.*` the global file already outranks system config)."""
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "git-identity.gitconfig"
    path.write_text("[user]\n\tname = %s\n\temail = %s\n" % (name, email))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    got = subprocess.run(["git", "config", "--global", "--get", "user.email"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
    if got != email:
        raise AssertionError("scoped_git_identity: git ignored GIT_CONFIG_GLOBAL=%s (user.email: got %r, want %r);"
                             " this needs git >= 2.32" % (path, got, email))
    return path


# --------------------------------------------------------------------------- the scanners


def _const(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_environ(node):
    """`os.environ` (blind spot: a bare `environ` from `from os import environ` is not matched)."""
    return (isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name)
            and node.value.id == "os")


def _setenv_name(node):
    """The constant first argument of a `<x>.setenv(VAR, ...)` call, else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setenv"
            and node.args):
        return _const(node.args[0])
    return None


def identity_env_offenders(source):
    """Every process-global git identity write in `source`, as sorted `(lineno, VAR)` pairs, VAR in IDENTITY_VARS.

    Flagged forms: `<x>.setenv(VAR, ...)` (in a conftest a fixture is suite-wide, so even monkeypatch counts),
    `os.environ[VAR] = ...`, `os.environ.update({VAR: ...})`, `os.environ.update(VAR=...)`,
    `os.environ.setdefault(VAR, ...)`, `os.putenv(VAR, ...)`. Not flagged: a local env dict handed to a subprocess
    (`env = dict(os.environ, VAR=...)`, `env.update(...)` on a local)."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Call):
            func = node.func
            var = _setenv_name(node)
            if var is not None:
                names.append(var)
            elif isinstance(func, ast.Attribute) and func.attr == "putenv" and isinstance(func.value, ast.Name) \
                    and func.value.id == "os" and node.args:
                names.append(_const(node.args[0]))
            elif isinstance(func, ast.Attribute) and _is_environ(func.value):
                if func.attr == "setdefault" and node.args:
                    names.append(_const(node.args[0]))
                elif func.attr == "update":
                    for arg in node.args:
                        if isinstance(arg, ast.Dict):
                            names.extend(_const(k) for k in arg.keys if k is not None)
                    names.extend(kw.arg for kw in node.keywords)
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_environ(target.value):
                    names.append(_const(target.slice))
        found.update((node.lineno, n) for n in names if n in IDENTITY_VARS)
    return sorted(found)


def home_override_offenders(sources):
    """The sorted names, from a `{name: source}` mapping, of files that call `<x>.setenv("HOME", ...)` without a
    scoped identity source anywhere in the file.

    Accepted identity sources: a `scoped_git_identity(...)` call (bare name or attribute), a
    `<x>.setenv("GIT_CONFIG_GLOBAL", ...)`, or `<x>.setenv` of BOTH `GIT_COMMITTER_NAME` and `GIT_COMMITTER_EMAIL`.
    Not accepted: a `.gitconfig` write (invisible under `GIT_CONFIG_GLOBAL=/dev/null`), a lone committer variable.
    Blind spots: a `GIT_CONFIG_GLOBAL` set to `os.devnull` is accepted, and the file it names is not checked for a
    `[user]` section."""
    offenders = []
    for name, source in sources.items():
        home, scoped, committer = False, False, set()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "scoped_git_identity") or \
                    (isinstance(func, ast.Attribute) and func.attr == "scoped_git_identity"):
                scoped = True
            var = _setenv_name(node)
            if var == "HOME":
                home = True
            elif var == "GIT_CONFIG_GLOBAL":
                scoped = True
            elif var in ("GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
                committer.add(var)
        if home and not scoped and committer != {"GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}:
            offenders.append(name)
    return sorted(offenders)
