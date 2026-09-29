"""#230: the `sdlc:*` and `priority:*` labels exist BEFORE the first pick, and a label write that
fails is reported -- never printed as "ensured".

Two halves, deliberately:

* SUBPROCESS tests drive the documented gestures exactly as a human types them
  (`python3 "$SETUP" labels .sdlc` from `skills/agrim-setup/references/public-repo.md`,
  `loop.py start <sdlc>`, `sdlc_init.py <target> --github`) against a stateful fake `gh` put first
  on PATH -- the same PATH-installed-fake pattern `test_public_bootstrap_control.py` uses. The fake
  models only what these gestures call: `label create`, and REST `api repos/<o>/<r>/labels|issues`
  GETs. Any other call is appended to an UNHANDLED log that every such test asserts is empty, so
  a call the product swallows cannot leave a test green while quietly testing nothing. The fake
  never answers GraphQL: an `api graphql` call lands in UNHANDLED, which is how "must not use
  GraphQL" is enforced here. Skipped on Windows only because a shebang script is not an
  executable `gh` there; the in-process tests below carry the same logic on every OS.

* IN-PROCESS tests inject `run=` into `GitHubSource` (the repo's own convention; `conftest.py`
  refuses any un-injected live `gh`).

THE CONTROL (AGENTS.md "run the control on the gesture the docs give"): the fake's
`refuse_label_create` models a token without label-write permission -- `gh` exits 1 with GitHub's
own 403 text. Measured RED against the pre-#230 code: `setup.py labels` exited 0 and printed
"ensured on acme/app", and `loop.py start` exited 0 having written nothing.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SETUP = ROOT / "skills" / "agrim-setup" / "scripts" / "setup.py"
LOOP_DIR = ROOT / "skills" / "agrim-loop" / "scripts"
LOOP = LOOP_DIR / "loop.py"
SDLC_INIT = ROOT / "skills" / "agrim-init" / "scripts" / "sdlc_init.py"
REPO = "acme/app"

#: Spelled out so this file stands alone (never imports sources.py to learn its own vocabulary).
LIFECYCLE = ("sdlc:goal", "sdlc:in-progress", "sdlc:parked", "sdlc:blocked", "sdlc:blocking",
             "sdlc:needs-confirmation", "sdlc:needs-label", "sdlc:designed", "sdlc:needs-unit",
             "sdlc:needs-triage")
PRIORITY = ("priority:P0", "priority:P1", "priority:P2", "priority:P3")
ALL_LABELS = LIFECYCLE + PRIORITY

posix_only = pytest.mark.skipif(os.name == "nt", reason="PATH fake `gh` is a shebang script")


_FAKE_GH = r'''
import json, os, sys

STATE = os.environ["FAKE_GH_STATE"]
LOG = os.environ["FAKE_GH_LOG"]
UNHANDLED = os.environ["FAKE_GH_UNHANDLED"]


def load():
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def unhandled(argv, why):
    with open(UNHANDLED, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "why": why}) + "\n")
    sys.stderr.write("fake gh: unhandled (%s): %r\n" % (why, argv))
    sys.exit(1)


def fields(argv):
    out, i = {}, 0
    while i < len(argv):
        if argv[i] == "-f" and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("="); out[k] = v; i += 2; continue
        i += 1
    return out


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


argv = sys.argv[1:]
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps(argv) + "\n")
s = load()
repo = s["repo"]
if argv[:2] == ["label", "create"]:
    name = argv[2]
    if flag(argv, "--repo") not in (None, repo):
        unhandled(argv, "wrong repo")
    if "--force" in argv:
        unhandled(argv, "--force repaints an existing label (#1917)")
    if s.get("refuse_label_create"):
        sys.stderr.write("HTTP 403: Resource not accessible by integration "
                         "(https://api.github.com/repos/%s/labels)\n" % repo)
        sys.exit(1)
    if name.casefold() in {n.casefold() for n in s["labels"]}:
        sys.stderr.write('label with name "%s" already exists; use `--force` to update its color '
                         'and description\n' % name)
        sys.exit(1)
    s["labels"][name] = {"color": flag(argv, "--color") or "", "description": flag(argv, "--description") or ""}
    save(s); sys.exit(0)
if argv and argv[0] == "api":
    endpoint = argv[1].replace("{owner}/{repo}", repo)
    if endpoint == "graphql":
        unhandled(argv, "graphql is not allowed for label bootstrap")
    method = (flag(argv, "--method") or "GET").upper()
    f = fields(argv)
    if method != "GET":
        unhandled(argv, "non-GET api")
    per_page, page = int(f.get("per_page", "30")), int(f.get("page", "1"))
    if endpoint == "repos/%s/labels" % repo:
        if s.get("refuse_label_list"):
            sys.stderr.write("HTTP 404: Not Found\n"); sys.exit(1)
        items = [{"name": n, "color": v["color"]} for n, v in s["labels"].items()]
    elif endpoint == "repos/%s/issues" % repo:
        want = set(f["labels"].split(",")) if f.get("labels") else set()
        items = [{"number": int(n), "labels": [{"name": l} for l in i["labels"]], "assignees": [],
                  "body": "", "title": i.get("title", "")}
                 for n, i in sorted(s["issues"].items(), key=lambda kv: int(kv[0]))
                 if i.get("state", "open") == "open" and want.issubset(set(i["labels"]))]
    else:
        unhandled(argv, "unmodeled endpoint")
    print(json.dumps(items[(page - 1) * per_page: page * per_page])); sys.exit(0)
unhandled(argv, "unmodeled verb")
'''


def _world(tmp_path, cfg=None, labels=None, issues=None, refuse=False, git_remote=None):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!%s\n%s" % (sys.executable, _FAKE_GH), encoding="utf-8")
    gh.chmod(0o755)
    state = tmp_path / "gh_state.json"
    state.write_text(json.dumps({"repo": REPO, "labels": labels or {}, "issues": issues or {},
                                 "refuse_label_create": refuse}), encoding="utf-8")
    log = tmp_path / "gh_log.jsonl"; log.write_text("", encoding="utf-8")
    unh = tmp_path / "gh_unhandled.jsonl"; unh.write_text("", encoding="utf-8")
    repo_dir = tmp_path / "repo"; repo_dir.mkdir()
    sdlc = repo_dir / ".sdlc"; sdlc.mkdir()
    if cfg is not None:
        (sdlc / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    py_dir = str(pathlib.Path(sys.executable).resolve().parent)
    env = {"PATH": os.pathsep.join([str(bin_dir), py_dir, "/usr/bin", "/bin"]),
           "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "PYTHONDONTWRITEBYTECODE": "1", "FAKE_GH_STATE": str(state), "FAKE_GH_LOG": str(log),
           "FAKE_GH_UNHANDLED": str(unh)}
    if git_remote:
        subprocess.run(["git", "init", "-q", str(repo_dir)], env=env, check=True)
        subprocess.run(["git", "-C", str(repo_dir), "remote", "add", "origin", git_remote], env=env,
                       check=True)
    return {"repo_dir": repo_dir, "sdlc": sdlc, "env": env, "state": state, "log": log, "unhandled": unh}


def _run(world, argv):
    return subprocess.run([sys.executable, *[str(a) for a in argv]], cwd=str(world["repo_dir"]),
                          env=world["env"], capture_output=True, text=True)


def _labels(world):
    return json.loads(world["state"].read_text(encoding="utf-8"))["labels"]


def _calls(world):
    return [json.loads(l) for l in world["log"].read_text(encoding="utf-8").splitlines() if l.strip()]


def _github_cfg(repo=REPO):
    return {"discovery": {"source": "github", "github": {"repo": repo}}}


# ---------------------------------------------------------------------------- setup.py labels


@posix_only
def test_setup_labels_creates_all_fourteen_on_a_fresh_repo(tmp_path):
    w = _world(tmp_path, cfg=_github_cfg())
    p = _run(w, [SETUP, "labels", w["sdlc"]])
    assert p.returncode == 0, p.stdout + p.stderr
    assert set(_labels(w)) == set(ALL_LABELS)
    for name in ALL_LABELS:
        assert "%s: created" % name in p.stdout
    assert "ensured on %s" % REPO in p.stdout
    assert w["unhandled"].read_text() == ""


@posix_only
def test_setup_labels_refused_write_exits_nonzero_naming_the_label_never_ensured(tmp_path):
    """THE CONTROL, on the documented gesture: a token without label-write permission."""
    w = _world(tmp_path, cfg=_github_cfg(), refuse=True)
    p = _run(w, [SETUP, "labels", w["sdlc"]])
    out = p.stdout + p.stderr
    assert p.returncode != 0, out
    assert "sdlc:goal: FAILED" in out and "403" in out
    assert "priority:P0: FAILED" in out
    assert "ensured" not in out.replace("NOT ensured", "")
    assert w["unhandled"].read_text() == ""


@posix_only
def test_setup_labels_is_idempotent_and_never_recolours(tmp_path):
    existing = {n: {"color": "123456", "description": "mine"} for n in ALL_LABELS}
    w = _world(tmp_path, cfg=_github_cfg(), labels=existing)
    p = _run(w, [SETUP, "labels", w["sdlc"]])
    assert p.returncode == 0, p.stdout + p.stderr
    for name in ALL_LABELS:
        assert "%s: existed" % name in p.stdout
    assert _labels(w) == existing                               # untouched, not recoloured
    assert not any(c[:2] == ["label", "create"] for c in _calls(w))   # zero writes when all exist
    assert w["unhandled"].read_text() == ""


@posix_only
def test_setup_labels_existing_labels_do_not_fail_under_a_read_only_token(tmp_path):
    """A triage-role token can apply labels but not create them: when every label already exists
    there is nothing to write, so the refusal must not fire."""
    existing = {n: {"color": "123456", "description": ""} for n in ALL_LABELS}
    w = _world(tmp_path, cfg=_github_cfg(), labels=existing, refuse=True)
    p = _run(w, [SETUP, "labels", w["sdlc"]])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "FAILED" not in p.stdout + p.stderr


# ---------------------------------------------------------------------------- loop.py start


@posix_only
def test_loop_start_refuses_when_a_required_label_cannot_be_created(tmp_path):
    w = _world(tmp_path, cfg=_github_cfg(), refuse=True)
    p = _run(w, [LOOP, "start", w["sdlc"], "--session-pid", str(os.getpid())])
    out = p.stdout + p.stderr
    assert p.returncode != 0, out
    assert "sdlc:goal: FAILED" in out
    assert "ensured" not in out.replace("NOT ensured", "")
    assert w["unhandled"].read_text() == ""


@posix_only
def test_loop_start_creates_missing_labels_in_github_mode(tmp_path):
    w = _world(tmp_path, cfg=_github_cfg(), labels={"sdlc:goal": {"color": "abcdef", "description": ""}})
    p = _run(w, [LOOP, "start", w["sdlc"], "--session-pid", str(os.getpid())])
    assert p.returncode == 0, p.stdout + p.stderr
    assert set(_labels(w)) == set(ALL_LABELS)
    assert _labels(w)["sdlc:goal"]["color"] == "abcdef"
    assert w["unhandled"].read_text() == ""


@posix_only
def test_loop_start_in_local_goals_mode_makes_no_gh_call(tmp_path):
    w = _world(tmp_path, cfg={"discovery": {"source": "local-goals"}})
    p = _run(w, [LOOP, "start", w["sdlc"], "--session-pid", str(os.getpid())])
    assert p.returncode == 0, p.stdout + p.stderr
    assert _calls(w) == []


# ---------------------------------------------------------------------------- init --github


@posix_only
def test_init_github_creates_labels_before_the_first_pick(tmp_path):
    """Acceptance: fresh repo, github mode, one issue with no labels. After init all fourteen
    labels exist; labelling the issue `sdlc:goal` makes the pick query return it."""
    w = _world(tmp_path, issues={"1": {"labels": [], "title": "t"}},
               git_remote="https://github.com/%s.git" % REPO)
    p = _run(w, [SDLC_INIT, w["repo_dir"], "--github"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert set(_labels(w)) == set(ALL_LABELS)
    assert "ensured on %s" % REPO in p.stdout
    assert w["unhandled"].read_text() == ""

    s = json.loads(w["state"].read_text()); s["issues"]["1"]["labels"] = ["sdlc:goal"]
    w["state"].write_text(json.dumps(s))
    pick = _run(w, ["-c", "import sys; sys.path.insert(0, %r); import sources; "
                          "print(sources.GitHubSource(%r).next_pending())"
                          % (str(LOOP_DIR), _github_cfg())])
    assert pick.stdout.strip() == "1", pick.stdout + pick.stderr


@posix_only
def test_init_github_exits_nonzero_when_a_label_write_fails(tmp_path):
    w = _world(tmp_path, refuse=True, git_remote="https://github.com/%s.git" % REPO)
    p = _run(w, [SDLC_INIT, w["repo_dir"], "--github"])
    out = p.stdout + p.stderr
    assert p.returncode != 0, out
    assert "sdlc:goal: FAILED" in out
    assert "ensured" not in out.replace("NOT ensured", "")


@posix_only
def test_init_github_without_a_github_remote_says_so_and_writes_nothing(tmp_path):
    w = _world(tmp_path)
    p = _run(w, [SDLC_INIT, w["repo_dir"], "--github"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "labels not created" in p.stdout + p.stderr
    assert _calls(w) == []


# ---------------------------------------------------------------------------- in-process


def _sources():
    sys.path.insert(0, str(LOOP_DIR))
    spec = importlib.util.spec_from_file_location("sources_230", LOOP_DIR / "sources.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _loop():
    sys.path.insert(0, str(LOOP_DIR))
    spec = importlib.util.spec_from_file_location("loop_230", LOOP_DIR / "loop.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _fake_run(existing, calls, refuse=False, list_fails=False):
    def run(args):
        calls.append(list(args))
        if args[:2] == ["label", "create"]:
            if refuse:
                e = RuntimeError("gh label create failed: HTTP 403: Resource not accessible")
                e.hint = "HTTP 403: Resource not accessible by integration"; raise e
            if args[2] in existing:
                raise RuntimeError('label with name "%s" already exists' % args[2])
            existing[args[2]] = args; return ""
        if args[0] == "api" and args[1].endswith("/labels"):
            if list_fails:
                raise RuntimeError("HTTP 500")
            page = int(next(a for a in args if str(a).startswith("page=")).split("=")[1])
            return json.dumps([{"name": n} for n in existing] if page == 1 else [])
        raise AssertionError("unexpected gh call %r" % (args,))
    return run


def test_report_classifies_created_existed_and_failed_per_label():
    src_mod = _sources()
    calls = []
    s = src_mod.GitHubSource(_github_cfg(), run=_fake_run({"sdlc:goal": 1}, calls))
    res = {r["label"]: r["outcome"] for r in s.ensure_labels_report()}
    assert set(res) == set(ALL_LABELS)
    assert res["sdlc:goal"] == "existed"
    assert all(v == "created" for k, v in res.items() if k != "sdlc:goal")
    assert not any(c[:2] == ["api", "graphql"] for c in calls)


def test_report_falls_back_to_create_when_the_list_read_fails():
    src_mod = _sources()
    s = src_mod.GitHubSource(_github_cfg(), run=_fake_run({"sdlc:goal": 1}, [], list_fails=True))
    res = {r["label"]: r["outcome"] for r in s.ensure_labels_report()}
    assert res["sdlc:goal"] == "existed" and res["priority:P3"] == "created"


def test_report_failed_label_carries_its_reason_and_keeps_the_hot_path_armed():
    src_mod = _sources()
    s = src_mod.GitHubSource(_github_cfg(), run=_fake_run({}, [], refuse=True))
    res = s.ensure_labels_report()
    assert all(r["outcome"] == "failed" and "403" in r["reason"] for r in res)
    lines, failed = src_mod.render_label_report(REPO, res)
    assert failed and not any(l.startswith("labels ensured") for l in lines)
    assert s._labels_ready is False       # a failed bootstrap never marks the labels ready


def test_priority_labels_are_defined_once_on_the_source():
    src_mod = _sources()
    names = [n for n, _c, _d in src_mod.GitHubSource._PRIORITY_LABELS]
    assert names == list(PRIORITY)


def test_done_names_the_empty_goal_label_when_no_issue_carries_it(tmp_path, capsys):
    lp = _loop()
    base = tmp_path / ".sdlc"; base.mkdir()
    (base / "config.json").write_text(json.dumps({"discovery": {"source": "local-goals"}}))
    lp.state.start_run(str(base))

    class EmptyGithub:
        goal_label = "sdlc:goal"
        def next_pending(self, skip=()): return None
        def read_degraded(self): return False
        def goal_label_census(self): return 0

    kind, payload = lp._next(str(base), EmptyGithub(), lp.state.load_config(str(base)))
    assert (kind, payload) == ("DONE", None)
    assert "0 issues carry sdlc:goal — label one to start" in capsys.readouterr().err


def test_done_stays_quiet_about_the_label_when_goals_exist_but_none_are_pickable(tmp_path, capsys):
    lp = _loop()
    base = tmp_path / ".sdlc"; base.mkdir()
    (base / "config.json").write_text(json.dumps({"discovery": {"source": "local-goals"}}))
    lp.state.start_run(str(base))

    class BusyGithub:
        goal_label = "sdlc:goal"
        def next_pending(self, skip=()): return None
        def read_degraded(self): return False
        def goal_label_census(self): return 1

    lp._next(str(base), BusyGithub(), lp.state.load_config(str(base)))
    assert "carry sdlc:goal" not in capsys.readouterr().err


def test_goal_label_census_is_one_bounded_rest_read():
    src_mod = _sources()
    calls = []

    def run(args):
        calls.append(list(args))
        assert args[0] == "api" and args[1] == "repos/%s/issues" % REPO
        return "[]"
    assert src_mod.GitHubSource(_github_cfg(), run=run).goal_label_census() == 0
    assert len(calls) == 1 and "per_page=1" in calls[0]


def test_goal_label_census_is_free_when_the_pick_already_saw_a_labelled_issue():
    """The DONE path must not add a read when the pick's own goal-label query was non-empty
    (every labelled issue was merely ineligible): `test_feature_scope`'s per-terminal call budget
    depends on it."""
    src_mod = _sources()
    calls = []
    s = src_mod.GitHubSource(_github_cfg(), run=lambda a: calls.append(a) or "[]")
    s._last_goal_raw_count = 2
    assert s.goal_label_census() == 2 and calls == []
