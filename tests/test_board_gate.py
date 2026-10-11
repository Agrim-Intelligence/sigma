"""#895 slice 5: ONE board gate, `GitHubSource.board_active`, for when GraphQL is unavailable.

Documented gesture (nothing exported; tests set their own env via monkeypatch):
    env -i PATH=/opt/homebrew/bin:/usr/bin:/bin HOME=$HOME $HOME/.sigma-venv312/bin/python -m pytest tests/test_board_gate.py

tests/conftest.py's autouse `_hermetic_graphql_capability_env` deletes CLAUDE_CODE_REMOTE and
SIGMA_GH_GRAPHQL for every test, so outside this file `board_active == project_enabled` and the
existing boardfake suites run byte-identically. The byte-identity evidence is those suites passing
UNCHANGED; the twin test below proves the board still mirrors when GraphQL is on.

Honesty: nothing here ran in a real Claude Code cloud session. The claim rests on recording fakes and
AST rules, all deterministic (no probabilistic race, nothing to collapse across a speed boundary).
"""
import ast, importlib.util, io, json, pathlib, sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parent
SKILLS = ROOT / "skills"
SOURCES_PY = SKILLS / "sigma-loop" / "scripts" / "sources.py"
NOTICE_ATTR = "_sigma_board_graphql_noticed"

from test_github_project import project_world, _cfg   # noqa: E402  (stock in-memory `gh project` fake)


def _load(path, name="sources"):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _src():
    return _load(SOURCES_PY)


@pytest.fixture(autouse=True)
def _fresh_notice(monkeypatch):
    monkeypatch.delattr(sys, NOTICE_ATTR, raising=False)
    yield
    if hasattr(sys, NOTICE_ATTR):
        delattr(sys, NOTICE_ATTR)


def _is_board_argv(a):
    a = [str(x) for x in a]
    # label transitions also ride `api graphql` (other slices' business); only Projects v2 is the board
    return a[:1] == ["project"] or (a[:2] == ["api", "graphql"] and "rojectV2" in " ".join(a))


def _tripwire_world(**kw):
    """The stock fake, wrapped so ANY board argv raises: a skipped board makes none."""
    inner = project_world(**kw)
    seen = []

    def run(a):
        if _is_board_argv(a):
            seen.append(list(a))
            raise AssertionError("board call with GraphQL unavailable: %r" % (list(a),))
        return inner(a)

    run.board_calls = seen
    run.calls = inner.calls
    return run


# ---------------------------------------------------------------- structural (AST)

def _class_methods(tree, cls="GitHubSource"):
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {f.name: f for f in node.body if isinstance(f, ast.FunctionDef)}


def _str(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None


def _has_board_argv(fn):
    """R2: a list literal that is an `api graphql` argv or a `gh project` argv ("project" first)."""
    for n in ast.walk(fn):
        if isinstance(n, (ast.List, ast.Tuple)) and n.elts:
            head = [_str(e) for e in n.elts[:2]]
            if head[0] == "project" or head == ["api", "graphql"]:
                return True
    return False


def _refs(fn, names):
    return any(isinstance(n, ast.Attribute) and n.attr in names for n in ast.walk(fn))


# Methods allowed to hold a board argv without gating. An entry needs a comment naming why.
#   _graphql: the GENERIC `api graphql` carrier; label/reconcile paths use it, not only the board.
ALLOWLIST = {"_graphql": "generic graphql carrier shared with label transitions and reconcile reads"}


def _ungated_board_functions(tree):
    methods = _class_methods(tree)
    callers = {name: set() for name in methods}
    for name, fn in methods.items():
        for n in ast.walk(fn):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
                    and n.attr in methods and n.attr != name):
                callers[n.attr].add(name)
    memo = {}

    def gated(f, stack=()):
        if f in memo:
            return memo[f]
        if _refs(methods[f], {"board_active", "graphql_available"}):
            memo[f] = True
            return True
        if f in stack:
            return True                      # cycle: decided by the callers outside it
        cs = callers[f]
        ok = bool(cs) and all(gated(c, stack + (f,)) for c in cs)
        if not stack:
            memo[f] = ok
        return ok

    return sorted(f for f, fn in methods.items()
                  if _has_board_argv(fn) and f not in ALLOWLIST and not gated(f))


def test_every_board_call_function_is_gated():
    """Red-before-change (no function references `board_active`). A new board-argv method must
    reference `board_active`, or every in-class caller must (transitively)."""
    tree = ast.parse(SOURCES_PY.read_text(encoding="utf-8"))
    assert _ungated_board_functions(tree) == []


def test_structural_rule_flags_a_new_ungated_board_method():
    """Characterisation of the rule itself: green on a gated-only-via-caller helper, red on a new
    ungated method that fires a board argv after `_ensure_board()`."""
    base = SOURCES_PY.read_text(encoding="utf-8")
    probe = ("\n    def _probe_ungated(self):\n        self._ensure_board()\n"
             "        return self._run(['project', 'item-list', '1'])\n")
    tree = ast.parse(base)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GitHubSource")
    extra = ast.parse("class X:" + probe.replace("\n    ", "\n    ")).body[0].body
    cls.body.extend(extra)
    assert "_probe_ungated" in _ungated_board_functions(tree)


def test_project_enabled_read_only_inside_board_active():
    """Raw `.project_enabled` loads (and getattr(..., "project_enabled")) in skills/ appear only in
    `board_active` and the `_ensure_board` first line; the `__init__` assignment is a Store."""
    allowed = {("sources.py", "board_active"), ("sources.py", "_ensure_board")}
    bad = []
    for path in SKILLS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        enclosing = {}
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef):
                for n in ast.walk(fn):
                    enclosing.setdefault(id(n), fn.name)
        for n in ast.walk(tree):
            hit = (isinstance(n, ast.Attribute) and n.attr == "project_enabled" and isinstance(n.ctx, ast.Load)) or \
                  (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
                   and len(n.args) > 1 and _str(n.args[1]) == "project_enabled")
            if hit and (path.name, enclosing.get(id(n))) not in allowed:
                bad.append("%s:%s in %s" % (path.name, n.lineno, enclosing.get(id(n))))
    assert bad == []


# ---------------------------------------------------------------- runtime (recording fake)

def _drive_everything(gh):
    gh.mark_in_progress("5")
    gh.mark_qc("5")
    gh.complete("5")
    gh.park("9", "needs a human")
    assert gh._set_board_status("5", "In Progress") is False
    assert gh.set_board_phase("5", "P5 IMPLEMENT", [("P5 IMPLEMENT", "BLUE")]) is False
    assert gh._ensure_board() is False
    gh.next_pending()


def test_unavailable_graphql_makes_no_board_call(monkeypatch, capsys):
    """Red-before-change. CLAUDE_CODE_REMOTE=1: no board argv, no raise, labels still flow."""
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "1")
    src = _src()
    issues = [{"number": 5, "labels": [{"name": "sdlc:goal"}]}]
    run = _tripwire_world(issues=issues)
    gh = src.GitHubSource(_cfg(project={"enabled": True, "number": 3}), run=run)
    _drive_everything(gh)
    assert run.board_calls == []
    assert capsys.readouterr().err.count("GraphQL is unavailable") == 1


def test_sigma_gh_graphql_off_also_skips(monkeypatch):
    monkeypatch.setenv("SIGMA_GH_GRAPHQL", "off")
    gh = _src().GitHubSource(_cfg(project={"enabled": True}), run=_tripwire_world())
    assert gh.board_active is False and gh._ensure_board() is False


def test_graphql_available_board_argv_unchanged(monkeypatch):
    """Twin. SIGMA_GH_GRAPHQL=on (conftest clears the ambient env; see module docstring): the board
    still mirrors. Byte-identity is evidenced only by the existing boardfake suites passing
    unchanged; no captured fixture is claimed."""
    monkeypatch.setenv("SIGMA_GH_GRAPHQL", "on")
    run = project_world(projects=[], issues=[{"number": 5, "labels": [{"name": "sdlc:goal"}]}])
    gh = _src().GitHubSource(_cfg(project={"enabled": True}), run=run)
    assert gh.board_active is True
    gh.mark_in_progress("5")
    assert any(c[:2] == ["project", "create"] for c in run.calls)
    assert any(c[:2] == ["project", "item-edit"] for c in run.calls)


def test_board_active_false_when_project_disabled(monkeypatch):
    monkeypatch.setenv("SIGMA_GH_GRAPHQL", "on")
    assert _src().GitHubSource(_cfg(project={"enabled": False}), run=project_world()).board_active is False


def test_one_notice_per_process_across_instances(monkeypatch, capsys):
    """Red-before-change. Two instances AND two separate loads of sources.py (the real shape: each
    consumer loads its own copy), plus the status consumer: exactly one stderr line."""
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "1")
    cfg = _cfg(project={"enabled": True})
    a, b = _src(), _src()
    for gh in (a.GitHubSource(cfg, run=project_world()), a.GitHubSource(cfg, run=project_world()),
               b.GitHubSource(cfg, run=project_world())):
        assert gh.board_active is False
        assert gh.board_active is False
    err = capsys.readouterr().err
    assert err.count("GraphQL is unavailable") == 1
    assert err.count("sdlc:* labels remain the source of truth") == 1
    # a status.py run in the same process adds none (its segment is stdout-only)
    status = _load(SKILLS / "sigma-status" / "scripts" / "status.py", "status")
    status._board_skipped_segment(pathlib.Path("/nonexistent-sdlc"))
    assert "GraphQL is unavailable" not in capsys.readouterr().err


def test_board_active_fails_open_if_probe_raises(monkeypatch):
    src = _src()
    gh = src.GitHubSource(_cfg(project={"enabled": True}), run=project_world())

    def boom(*a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(src.gh_api, "graphql_available", boom)
    assert gh.board_active is True        # today's behaviour


def test_board_queue_falls_back_to_label_queue(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "1")
    gh = _src().GitHubSource(_cfg(project={"enabled": True}), run=_tripwire_world())
    assert gh._board_queue() is None


# ---------------------------------------------------------------- external readers

def _read(name):
    return (SKILLS / "sigma-loop" / "scripts" / name).read_text(encoding="utf-8")


def test_promote_unpark_no_false_board_failed_warning(monkeypatch):
    """Red-before-change. Board off for GraphQL reasons (config intent still on): promote must not
    print the false 'board card did not move' hint, and unpark/auto_unpark must ask `board_active`."""
    import test_promote as tp
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "1")
    p = tp._mod("promote")
    run = tp._runner(views={"5": tp._view("sdlc:needs-confirmation")})
    src = p.sources.GitHubSource(tp._board_config(), run=run)
    assert src.project_enabled is True and src.board_active is False
    result = p.promote(".sdlc", tp._board_config(), ["5"], source=src)
    assert result["results"][0]["outcome"] == "promoted"
    assert "board card did not move" not in result["results"][0]["detail"]
    for name in ("promote.py", "unpark.py", "auto_unpark.py"):
        assert "project_enabled" not in _read(name), name
    assert "source.board_active" in _read("unpark.py")


def test_auto_unpark_emits_no_set_status_with_board_off(monkeypatch):
    """Red-before-change. With the board off the plan carries no `set-status`, so
    `triage._execute_action` never raises on a False return."""
    import test_auto_unpark as ta
    monkeypatch.setenv("CLAUDE_CODE_REMOTE", "1")
    au = ta._mod("auto_unpark")
    cfg = ta._config(project={"enabled": True, "number": 1, "owner": "acme"})
    run = ta._label_aware_sweep_runner(
        by_label={"sdlc:blocked": json.dumps([ta._blocked_issue(42, body="blocked by #7")]),
                  "sdlc:blocking": "[]"},
        states={"7": "CLOSED"})
    src = au.sources.GitHubSource(cfg, run=run)
    actions, _ = au.compute_unpark_actions(".sdlc", cfg, src,
                                           [ta._blocked_issue(42, body="blocked by #7")], run=run)
    assert [a["action"] for a in actions] == ["swap-label"]
