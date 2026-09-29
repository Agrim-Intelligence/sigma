"""#236: `/agrim-init` is the ONE entry point -- `skills/agrim-init/scripts/init_flow.py` -- and
`/agrim-setup` is its alias.

The flow integrates the sibling goals, it does not re-implement them: preflight (#229), mode, the
verify command (#228), and in github mode the labels (#230), the board OFFER (#235), `assignee: @me`
and the ledger question; then a one-screen summary naming the next command.

Every subprocess test drives the documented gesture (`python3 init_flow.py <repo> [flags]`) against a
PATH-installed fake `gh` that logs every call -- the pattern `test_label_bootstrap.py` uses. A GitHub
`origin` must never reach the network, so `GIT_ALLOW_PROTOCOL=file` makes any https transport fail
fast (preflight reports the base branch CANNOT VERIFY), and pushes are redirected to a local bare
repository with `url.<bare>.pushInsteadOf` (so `git remote get-url origin` still reads the GitHub URL
mode detection needs). Skipped on Windows only where a shebang `gh` is needed; the in-process tests
below run everywhere.

THE CONTROL (AGENTS.md): `test_old_github_gesture_cannot_pick_the_labelled_issue` runs the OLD
`sdlc_init.py --github` gesture on a fresh repo and shows `loop.py next` does not pick the issue a
human labelled `sdlc:goal` (the config still says local-goals); the new flow's test picks it.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "agrim-init" / "scripts"
FLOW = SCRIPTS / "init_flow.py"
SDLC_INIT = SCRIPTS / "sdlc_init.py"
SETUP = ROOT / "skills" / "agrim-setup" / "scripts" / "setup.py"
LOOP_DIR = ROOT / "skills" / "agrim-loop" / "scripts"
LOOP = LOOP_DIR / "loop.py"
PREFLIGHT = SCRIPTS / "preflight.py"
REPO = "acme/app"
GITHUB_URL = "https://github.com/%s.git" % REPO

LABELS = ("sdlc:goal", "sdlc:in-progress", "sdlc:parked", "sdlc:blocked", "sdlc:blocking",
          "sdlc:needs-confirmation", "sdlc:needs-label", "sdlc:designed", "sdlc:needs-unit",
          "sdlc:needs-triage", "priority:P0", "priority:P1", "priority:P2", "priority:P3")

posix_only = pytest.mark.skipif(os.name == "nt", reason="PATH fake `gh` is a shebang script")


_FAKE_GH = r'''
import json, os, re, sys

STATE, LOG, UNHANDLED = (os.environ["FAKE_GH_STATE"], os.environ["FAKE_GH_LOG"],
                         os.environ["FAKE_GH_UNHANDLED"])


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
        if argv[i] in ("-f", "-F", "--raw-field", "--field") and i + 1 < len(argv):
            k, _, v = argv[i + 1].partition("="); out[k] = v; i += 2; continue
        i += 1
    return out


def flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def issue_json(n, i):
    return {"number": int(n), "title": i.get("title", ""), "body": i.get("body", ""),
            "state": i.get("state", "open"), "labels": [{"name": l} for l in i["labels"]],
            "assignees": [{"login": a} for a in i.get("assignees", [])],
            "html_url": "https://github.com/%s/issues/%s" % (s["repo"], n)}


argv = sys.argv[1:]
with open(LOG, "a", encoding="utf-8") as f:
    f.write(json.dumps(argv) + "\n")
s = load()
repo = s["repo"]
if argv[:2] == ["auth", "status"]:
    print("github.com\n  Logged in to github.com account fake (keyring)\n  - Active account: true\n"
          "  - Token: gho_****\n  - Token scopes: 'read:org', 'repo', 'workflow'")
    sys.exit(0)
if argv[:2] == ["label", "create"]:
    if flag(argv, "--repo") not in (None, repo) or "--force" in argv:
        unhandled(argv, "label create: wrong repo or --force")
    if s.get("refuse_label_create"):
        sys.stderr.write("HTTP 403: Resource not accessible by integration\n"); sys.exit(1)
    name = argv[2]
    if name in s["labels"]:
        sys.stderr.write('label with name "%s" already exists\n' % name); sys.exit(1)
    s["labels"][name] = {"color": flag(argv, "--color") or ""}
    save(s); sys.exit(0)
if argv[:2] == ["issue", "edit"]:
    i = s["issues"][argv[2]]
    for k in ("--add-label",):
        for lab in (flag(argv, k) or "").split(","):
            if lab and lab not in i["labels"]:
                i["labels"].append(lab)
    for lab in (flag(argv, "--remove-label") or "").split(","):
        if lab in i["labels"]:
            i["labels"].remove(lab)
    save(s); sys.exit(0)
if argv[:2] == ["issue", "list"]:
    want = flag(argv, "--label")
    print(json.dumps([issue_json(n, i) for n, i in sorted(s["issues"].items())
                      if i.get("state", "open") == (flag(argv, "--state") or "open")
                      and (not want or want in i["labels"])])); sys.exit(0)
if argv[:2] == ["issue", "view"]:
    i = s["issues"][argv[2]]
    if flag(argv, "--jq") == ".state":
        print(i.get("state", "open").upper()); sys.exit(0)
    print(json.dumps(issue_json(argv[2], i))); sys.exit(0)
if argv[:3] == ["api", "graphql", "-f"] and argv[3].startswith("query="):
    doc = argv[3][len("query="):]
    names = sorted(s["labels"])
    m = re.search(r"issue\(number: (\d+)\) \{ id \}", doc)
    if m:
        print(json.dumps({"data": {"repository": {"issue": {"id": "I_%s" % m.group(1)}}}})); sys.exit(0)
    found = re.findall(r'(a\d+): label\(name: "([^"]+)"\)', doc)
    if found:
        repo_d = {alias: ({"id": "L_%d" % names.index(n), "name": n} if n in names else None)
                  for alias, n in found}
        print(json.dumps({"data": {"repository": repo_d}})); sys.exit(0)
    if doc.startswith("mutation"):
        for verb, node, ids in re.findall(r'(addLabelsToLabelable|removeLabelsFromLabelable)\(input: '
                                          r'\{labelableId: "I_(\d+)", labelIds: \[([^\]]*)\]', doc):
            i = s["issues"][node]
            for lid in re.findall(r'"L_(\d+)"', ids):
                lab = names[int(lid)]
                if verb.startswith("add") and lab not in i["labels"]:
                    i["labels"].append(lab)
                elif verb.startswith("remove") and lab in i["labels"]:
                    i["labels"].remove(lab)
        save(s); print(json.dumps({"data": {}})); sys.exit(0)
    unhandled(argv, "unmodelled graphql")
if argv[:2] in (["issue", "comment"],):
    s.setdefault("comments", []).append(argv); save(s); sys.exit(0)
if argv[:2] == ["issue", "close"]:
    s["issues"][argv[2]]["state"] = "closed"; save(s); sys.exit(0)
if argv and argv[0] == "api":
    endpoint = argv[1].replace("{owner}/{repo}", repo).split("?")[0]
    method = (flag(argv, "--method") or flag(argv, "-X") or "GET").upper()
    if endpoint == "graphql" or method != "GET":
        unhandled(argv, "only REST GETs are modelled for api")
    if endpoint == "user":
        print("fake" if flag(argv, "--jq") else json.dumps({"login": "fake"})); sys.exit(0)
    if endpoint == "users/" + repo.split("/")[0]:
        print("Organization" if flag(argv, "--jq") else json.dumps({"type": "Organization"}))
        sys.exit(0)
    f = fields(argv)
    per_page, page = int(f.get("per_page", "30")), int(f.get("page", "1"))
    if endpoint == "repos/%s/labels" % repo:
        items = [{"name": n, "color": v.get("color", "")} for n, v in s["labels"].items()]
    elif endpoint == "repos/%s/issues" % repo:
        want = set(f["labels"].split(",")) if f.get("labels") else set()
        who = f.get("assignee")
        items = [issue_json(n, i) for n, i in sorted(s["issues"].items(), key=lambda kv: int(kv[0]))
                 if i.get("state", "open") == f.get("state", "open")
                 and want.issubset(set(i["labels"]))
                 and (not who or who in i.get("assignees", []))]
    elif endpoint.startswith("repos/%s/issues/" % repo) and endpoint.count("/") == 4:
        n = endpoint.rsplit("/", 1)[1]
        if n not in s["issues"]:
            sys.stderr.write("HTTP 404: Not Found\n"); sys.exit(1)
        print(json.dumps(issue_json(n, s["issues"][n]))); sys.exit(0)
    else:
        unhandled(argv, "unmodelled endpoint")
    print(json.dumps(items[(page - 1) * per_page: page * per_page])); sys.exit(0)
unhandled(argv, "unmodelled verb")
'''


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env)


def _world(tmp_path, issues=None, origin=GITHUB_URL, refuse=False, commit=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!%s\n%s" % (sys.executable, _FAKE_GH), encoding="utf-8")
    gh.chmod(0o755)
    state = tmp_path / "gh_state.json"
    state.write_text(json.dumps({"repo": REPO, "labels": {}, "issues": issues or {},
                                 "refuse_label_create": refuse}), encoding="utf-8")
    log = tmp_path / "gh_log.jsonl"
    log.write_text("", encoding="utf-8")
    unh = tmp_path / "gh_unhandled.jsonl"
    unh.write_text("", encoding="utf-8")
    py_dir = str(pathlib.Path(sys.executable).resolve().parent)
    env = {"PATH": os.pathsep.join([str(bin_dir), py_dir, "/usr/bin", "/bin"]),
           "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
           "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0",
           "PYTHONDONTWRITEBYTECODE": "1", "FAKE_GH_STATE": str(state), "FAKE_GH_LOG": str(log),
           "FAKE_GH_UNHANDLED": str(unh),
           **{k: os.environ[k] for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME") if k in os.environ}}
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main", env=env)
    _git(repo, "config", "user.email", "t@t.test", env=env)
    _git(repo, "config", "user.name", "t", env=env)
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(bare), env=env)
    if origin:
        _git(repo, "remote", "add", "origin", origin, env=env)
        if origin.startswith("https://"):
            _git(repo, "config", "url.%s.pushInsteadOf" % bare.as_uri(), origin, env=env)
    if commit:
        (repo / "README.md").write_text("hello\n")
        _git(repo, "add", "README.md", env=env)
        _git(repo, "commit", "-q", "-m", "initial", env=env)
        if origin:
            _git(repo, "push", "-q", "origin", "main", env=env)
    return {"repo": repo, "sdlc": repo / ".sdlc", "env": env, "state": state, "log": log,
            "unhandled": unh, "bare": bare}


def _run(w, argv):
    return subprocess.run([sys.executable, *[str(a) for a in argv]], cwd=str(w["repo"]),
                          env=w["env"], capture_output=True, text=True, timeout=120)


def _calls(w):
    return [json.loads(l) for l in w["log"].read_text(encoding="utf-8").splitlines() if l.strip()]


def _cfg(w):
    return json.loads((w["sdlc"] / "config.json").read_text(encoding="utf-8"))


def _gh_state(w):
    return json.loads(w["state"].read_text(encoding="utf-8"))


def _cmd_file(w, command):
    path = w["repo"].parent / "verify-cmd.txt"
    path.write_text(command + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- the example goal (no scaffold)


def test_the_scaffolded_example_goal_is_never_picked():
    text = (SCRIPTS.parent / "templates" / "goals" / "0001-example.md.tmpl").read_text(encoding="utf-8")
    front = text.split("\n---", 1)[0]
    assert "\nstatus: proposed" in front and "status: pending" not in front


@posix_only
def test_a_fresh_init_leaves_nothing_pickable_without_demo(tmp_path):
    w = _world(tmp_path, origin=None)
    p = _run(w, [FLOW, w["repo"], "--mode", "local-goals", "--local-only", "--no-verify"])
    assert p.returncode == 0, p.stdout + p.stderr
    nxt = _run(w, [LOOP, "next", ".sdlc"])
    assert "0001-example" not in nxt.stdout, nxt.stdout
    assert nxt.stdout.strip().startswith("DONE"), nxt.stdout + nxt.stderr


# ---------------------------------------------------------------- local-goals end to end


@posix_only
def test_local_goals_flow_reaches_done(tmp_path):
    w = _world(tmp_path, origin=None)
    p = _run(w, [FLOW, w["repo"], "--mode", "local-goals", "--local-only", "--demo", "--no-verify"])
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    for section in ("1/5 preflight", "2/5 mode", "3/5 verify", "4/5 github", "5/5 summary"):
        assert section in p.stdout, p.stdout
    assert "local-goals" in p.stdout and "/agrim-loop" in p.stdout
    cfg = _cfg(w)
    assert cfg["discovery"]["source"] == "local-goals"
    assert cfg["work"]["enabled"] is False and cfg["work"].get("_enabled_why")
    assert _calls(w) == []                                # local-goals mode: no gh call at all
    goal = _run(w, [LOOP, "next", ".sdlc"]).stdout.strip()
    assert goal.endswith("0000-demo.md"), goal
    demo = w["sdlc"] / "goals" / "0000-demo.md"
    demo.write_text(demo.read_text().replace("verify_command: python3 ",
                                             "verify_command: %s " % sys.executable)
                    .replace("verify_command: python ", "verify_command: %s " % sys.executable))
    (w["repo"] / "sigma-demo.md").write_text("Sigma ran this goal.\n")
    v = _run(w, [LOOP, "verify", ".sdlc", ".sdlc/goals/0000-demo.md"])
    assert v.returncode == 0, v.stdout + v.stderr
    d = _run(w, [LOOP, "record", ".sdlc", ".sdlc/goals/0000-demo.md", "done"])
    assert d.returncode == 0, d.stdout + d.stderr
    assert "status: done" in demo.read_text()


@posix_only
def test_local_only_choice_stops_the_work_enabled_nag(tmp_path):
    w = _world(tmp_path, origin=None)
    assert _run(w, [FLOW, w["repo"], "--mode", "local-goals", "--local-only", "--no-verify"]).returncode == 0
    s = _run(w, [LOOP, "start", ".sdlc", "--session-pid", str(os.getpid())])
    assert "work.enabled is off" not in s.stderr, s.stderr
    # CONTROL: the same config without the recorded decision still warns -- and names init.
    cfg = _cfg(w)
    del cfg["work"]["_enabled_why"]
    (w["sdlc"] / "config.json").write_text(json.dumps(cfg))
    s = _run(w, [LOOP, "start", ".sdlc", "--session-pid", str(os.getpid())])
    assert "work.enabled is off" in s.stderr and "/agrim-init" in s.stderr, s.stderr
    assert "/agrim-setup" not in s.stderr


# ---------------------------------------------------------------- github end to end


def _labelled_issue():
    return {"1": {"labels": [], "title": "Add a greeting", "assignees": ["fake"]}}


@posix_only
def test_github_flow_makes_only_label_writes_offers_the_board_and_reaches_done(tmp_path):
    w = _world(tmp_path, issues=_labelled_issue())
    cmd = _cmd_file(w, '"%s" -c "import sys; sys.exit(0)"' % sys.executable)
    p = _run(w, [FLOW, w["repo"], "--yes", "--verify-command-file", cmd])
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    cfg = _cfg(w)
    assert cfg["discovery"]["source"] == "github"            # --yes: detected from origin
    assert cfg["discovery"]["github"]["repo"] == REPO
    assert cfg["discovery"]["github"]["assignee"] == "@me"
    assert cfg["ledger"]["enabled"] is False                 # --yes never turns the ledger on
    assert not (cfg["discovery"]["github"].get("project") or {}).get("number")
    assert set(_gh_state(w)["labels"]) == set(LABELS)
    # the exact gh calls: preflight reads + REST label list + label writes; nothing else
    kinds = []
    for c in _calls(w):
        if c[:2] == ["auth", "status"]:
            kinds.append("auth")
        elif c[0] == "api" and c[1] == "users/acme":
            kinds.append("owner")
        elif c[0] == "api" and c[1] == "repos/%s/labels" % REPO:
            kinds.append("labels-get")
        elif c[:2] == ["label", "create"]:
            kinds.append("label-create")
        else:
            kinds.append("OTHER %r" % c)
    assert [k for k in kinds if k.startswith("OTHER")] == [], kinds
    assert kinds.count("label-create") == len(LABELS)
    assert not any("project" in " ".join(c) or "graphql" in c for c in _calls(w))
    assert "OFFER" in p.stdout and "board_setup.py" in p.stdout and "--board yes" in p.stdout
    assert w["unhandled"].read_text() == ""

    # a human triages the issue; the loop picks it, verify passes, done is recorded
    s = _gh_state(w)
    s["issues"]["1"]["labels"] = ["sdlc:goal"]
    w["state"].write_text(json.dumps(s))
    nxt = _run(w, [LOOP, "next", ".sdlc"])
    assert nxt.stdout.strip() == "1", nxt.stdout + nxt.stderr
    v = _run(w, [LOOP, "verify", ".sdlc", "1"])
    assert v.returncode == 0, v.stdout + v.stderr
    d = _run(w, [LOOP, "record", ".sdlc", "1", "done"])
    assert d.returncode == 0, d.stdout + d.stderr
    assert _gh_state(w)["issues"]["1"]["state"] == "closed"
    assert w["unhandled"].read_text() == ""                  # every loop call was modelled, too


@posix_only
def test_github_rerun_is_idempotent(tmp_path):
    w = _world(tmp_path)
    first = _run(w, [FLOW, w["repo"], "--mode", "github", "--ledger", "no", "--board", "no",
                     "--no-verify"])
    assert first.returncode == 0, first.stdout + first.stderr
    before = _cfg(w)
    w["log"].write_text("")
    again = _run(w, [FLOW, w["repo"]])                        # answers are remembered
    assert again.returncode == 0, again.stdout + again.stderr
    assert _cfg(w) == before
    assert not any(c[:2] == ["label", "create"] for c in _calls(w))
    assert "OFFER" not in again.stdout                        # the declined board stays declined
    # ...because every answer was REMEMBERED, not because the github step was skipped
    assert "[ok] mode: github" in again.stdout and "board: declined" in again.stdout, again.stdout
    assert "[ask]" not in again.stdout
    assert any(c[:2] == ["api", "repos/%s/labels" % REPO] for c in _calls(w))   # labels re-checked


@posix_only
def test_github_label_failure_exits_nonzero_with_the_resume_command(tmp_path):
    w = _world(tmp_path, refuse=True)
    p = _run(w, [FLOW, w["repo"], "--mode", "github", "--ledger", "no", "--no-verify"])
    assert p.returncode == 1, p.stdout + p.stderr
    assert "sdlc:goal: FAILED" in p.stdout + p.stderr
    assert "Resume:" in p.stdout and "init_flow.py" in p.stdout


@posix_only
def test_github_mode_without_a_repo_is_refused(tmp_path):
    w = _world(tmp_path, origin=None)
    p = _run(w, [FLOW, w["repo"], "--mode", "github"])
    assert p.returncode == 2, p.stdout + p.stderr
    assert "--repo" in p.stdout + p.stderr
    cfg = _cfg(w) if (w["sdlc"] / "config.json").exists() else {}
    assert (cfg.get("discovery") or {}).get("source") != "github"


@posix_only
def test_unanswered_questions_are_printed_with_their_flags(tmp_path):
    """Codex / Cursor: no interactive question -- the flow prints each open one with its flag."""
    w = _world(tmp_path)
    p = _run(w, [FLOW, w["repo"]])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "[ask] mode" in p.stdout and "--mode github" in p.stdout
    assert "default: github" in p.stdout                       # origin is GitHub
    assert _cfg(w)["discovery"]["source"] == "local-goals"      # nothing chosen for the user
    assert not any(c[:2] == ["label", "create"] for c in _calls(w))


@posix_only
def test_no_remote_is_a_work_question_not_a_failure(tmp_path):
    w = _world(tmp_path, origin=None)
    p = _run(w, [FLOW, w["repo"], "--mode", "local-goals", "--no-verify"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert "DECISION" in p.stdout and "[ask] work" in p.stdout and "--local-only" in p.stdout
    assert _cfg(w)["work"]["enabled"] is True                   # nothing flipped for the user
    # a blocking problem that is NOT that open question still fails the run, with the resume line
    (tmp_path / "nocommit").mkdir()
    w2 = _world(tmp_path / "nocommit", origin=None, commit=False)
    p = _run(w2, [FLOW, w2["repo"], "--mode", "local-goals", "--local-only", "--no-verify"])
    assert p.returncode == 1 and "Resume:" in p.stdout, p.stdout + p.stderr


# ---------------------------------------------------------------- the control


@posix_only
def test_old_github_gesture_cannot_pick_the_labelled_issue(tmp_path):
    issues = {"1": {"labels": ["sdlc:goal"], "title": "Add a greeting", "assignees": ["fake"]}}
    w = _world(tmp_path, issues=issues)
    old = _run(w, [SDLC_INIT, w["repo"], "--github"])
    assert old.returncode == 0, old.stdout + old.stderr
    nxt = _run(w, [LOOP, "next", ".sdlc"])
    assert nxt.stdout.strip() != "1", nxt.stdout
    assert "0001-example" not in nxt.stdout                    # nor the placeholder
    # ...the new flow, on the same repository, makes the same issue pickable
    new = _run(w, [FLOW, w["repo"], "--mode", "github", "--ledger", "no", "--board", "no",
                   "--no-verify"])
    assert new.returncode == 0, new.stdout + new.stderr
    assert _run(w, [LOOP, "next", ".sdlc"]).stdout.strip() == "1"


# ---------------------------------------------------------------- board consent


def _load_flow():
    spec = importlib.util.spec_from_file_location("init_flow_under_test", FLOW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_board_is_created_only_on_an_explicit_yes(tmp_path):
    flow = _load_flow()
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text(json.dumps(
        {"discovery": {"source": "github", "github": {"repo": REPO}}}))
    calls = []
    flow.BOARD_RUNNER = lambda argv: calls.append(argv) or (0, "[ok] board created\n")
    lines, ok = flow.board_step(str(tmp_path), str(sdlc), answer=None, repo=REPO)
    assert calls == [] and ok and any("OFFER" in l for l in lines)
    lines, ok = flow.board_step(str(tmp_path), str(sdlc), answer="no", repo=REPO)
    assert calls == [] and ok
    lines, ok = flow.board_step(str(tmp_path), str(sdlc), answer="yes", repo=REPO)
    assert ok and len(calls) == 1 and calls[0][-1] == "--yes" and "create" in calls[0]
    flow.BOARD_RUNNER = lambda argv: (1, "[FAIL] project scope missing\n")
    lines, ok = flow.board_step(str(tmp_path), str(sdlc), answer="yes", repo=REPO)
    assert not ok and any("FAIL" in l for l in lines)


def test_yes_never_answers_board_verify_or_work():
    flow = _load_flow()
    answers = flow.resolve_answers({"yes": True}, previous={}, detected_mode="github",
                                   remote_ok=False)
    assert answers["mode"] == "github" and answers["ledger"] == "no"
    assert answers.get("board") is None and answers.get("verify") is None
    assert answers.get("work") is None


# ---------------------------------------------------------------- setup.py configure


def test_configure_refuses_github_mode_with_no_repo(tmp_path):
    sdlc = tmp_path / ".sdlc"
    sdlc.mkdir()
    (sdlc / "config.json").write_text("{}")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    p = subprocess.run([sys.executable, str(SETUP), "configure", str(sdlc)],
                       capture_output=True, text=True)
    assert p.returncode == 2, p.stdout + p.stderr
    assert json.loads((sdlc / "config.json").read_text()) == {}


def test_configure_without_an_sdlc_dir_refuses_instead_of_crashing(tmp_path):
    p = subprocess.run([sys.executable, str(SETUP), "configure", str(tmp_path / ".sdlc"),
                        "--repo", REPO], capture_output=True, text=True)
    assert p.returncode == 2, p.stdout + p.stderr
    assert "Traceback" not in p.stderr and "/agrim-init" in p.stderr
    assert not (tmp_path / ".sdlc").exists()


def test_setup_init_is_an_alias_of_the_flow():
    p = subprocess.run([sys.executable, str(SETUP), "init", "--help"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "init_flow.py" in p.stdout


# ---------------------------------------------------------------- doctor points at init


def test_doctor_work_row_points_at_init():
    text = (ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py").read_text(encoding="utf-8")
    assert "(or run /agrim-setup)" not in text
    assert '"work": {"enabled": true}  (or run /agrim-init)' in text


def test_the_pre_236_github_flag_means_github_mode():
    """`/agrim-init --github` (README, muscle memory) now SAYS github mode too -- the old scaffolder
    flag created labels but left the backlog local, which is the bug this goal closes."""
    flow = _load_flow()
    opts, target, err = flow.parse(["--github"])
    assert err is None and opts["mode"] == "github" and opts["github-templates"] is True
    assert flow.parse(["--github", "--mode", "local-goals"])[2]          # a contradiction is refused
