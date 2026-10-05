import hashlib
import importlib.util
import json, os, subprocess, pathlib, pytest

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "sigma_gate.sh"

# The classifier tests below exercise the gate's INTENT behavior, which only fires
# in an adopted repo (or under the global escape hatch). Pinning the escape hatch
# here keeps them hermetic — independent of the test runner's cwd having .sdlc/.
_GLOBAL_ENV = {**os.environ, "SIGMA_GATE_GLOBAL": "1"}


def _run(prompt):
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True, text=True, env=_GLOBAL_ENV,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON → also tests the invariant


# --- per-repo scoping (0.6): adopted repos get the policy, others a silent no-op ---

def _run_scoped(prompt, project_dir):
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_GATE_GLOBAL"}
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_unadopted_repo_is_a_silent_noop(tmp_path):
    # no .sdlc/ in the project dir → the hook emits a valid, EMPTY envelope
    out = _run_scoped("implement the parser in parser.py", tmp_path)
    assert out["hookSpecificOutput"]["additionalContext"] == ""


def test_adopted_repo_gets_the_full_policy(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    ctx = _run_scoped("implement the parser in parser.py", tmp_path)["hookSpecificOutput"]["additionalContext"]
    assert "GOAL-BASED SDLC" in ctx and "CODE CHANGE" in ctx


def _run_session(prompt, project_dir, session_id):
    env = {k: v for k, v in os.environ.items() if k != "SIGMA_GATE_GLOBAL"}
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"prompt": prompt, "session_id": session_id}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_notification_only_is_silent_and_does_not_consume_first_prompt(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    notification = "<task-notification>Implemented parser.py; task complete.</task-notification>"
    assert _run_session(notification, tmp_path, "session-1") == ""
    assert "GOAL-BASED SDLC" in _run_session("implement parser.py", tmp_path, "session-1")


def test_policy_once_per_session_with_short_reminder_afterwards(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    first = _run_session("implement parser.py", tmp_path, "session-1")
    second = _run_session("fix another bug", tmp_path, "session-1")
    other = _run_session("build a feature", tmp_path, "session-2")
    assert "PLAN-REVIEW" in first and "CODE CHANGE" in first
    assert len(second) < 200 and "PLAN-REVIEW" not in second
    assert "PLAN-REVIEW" in other


def test_notification_embedded_in_real_request_is_not_silent(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    prompt = "<task-notification>Done</task-notification>\nPlease implement the fix."
    assert "GOAL-BASED SDLC" in _run_session(prompt, tmp_path, "session-3")


def test_session_store_failure_preserves_full_policy(tmp_path):
    state = tmp_path / ".sdlc/state"
    state.parent.mkdir()
    state.write_text("not a directory")
    assert "PLAN-REVIEW" in _run_session("implement parser.py", tmp_path, "session-4")


def test_inactive_session_marker_expires_after_retention_window(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    assert "PLAN-REVIEW" in _run_session("implement A", tmp_path, "session-old")
    store = tmp_path / ".sdlc/state/gate-sessions"
    old = 0
    for path in store.iterdir():
        os.utime(path, (old, old))
    assert "PLAN-REVIEW" in _run_session("implement B", tmp_path, "session-old")
    assert len(list(store.glob("*.mark"))) == 1


def test_two_concurrent_first_prompts_inject_full_policy_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    (tmp_path / ".sdlc").mkdir()
    with ThreadPoolExecutor(max_workers=2) as pool:
        contexts = list(pool.map(
            lambda _: _run_session("implement parser.py", tmp_path, "session-race"),
            range(2),
        ))
    assert sum("PLAN-REVIEW" in ctx for ctx in contexts) == 1


@pytest.mark.parametrize("component", [".sdlc", "state"])
def test_symlinked_state_ancestor_cannot_redirect_marker_writes(tmp_path, component):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    if component == ".sdlc":
        (project / ".sdlc").symlink_to(outside, target_is_directory=True)
    else:
        (project / ".sdlc").mkdir()
        (project / ".sdlc/state").symlink_to(outside, target_is_directory=True)
    assert "PLAN-REVIEW" in _run_session("implement A", project, "symlink-test")
    assert "PLAN-REVIEW" in _run_session("implement B", project, "symlink-test")
    assert not (outside / "gate-sessions").exists()


def test_symlinked_prune_stamp_fails_open_without_new_marker(tmp_path):
    store = tmp_path / ".sdlc/state/gate-sessions"
    store.mkdir(parents=True)
    (store / ".last-prune").symlink_to(tmp_path / "outside")
    assert "PLAN-REVIEW" in _run_session("implement A", tmp_path, "unsafe-stamp")
    assert "PLAN-REVIEW" in _run_session("implement B", tmp_path, "unsafe-stamp")
    assert not list(store.glob("*.mark"))


def test_failed_first_delivery_does_not_record_policy_as_delivered(tmp_path):
    (tmp_path / ".sdlc").mkdir()
    spec = importlib.util.spec_from_file_location("gate_state", HOOK.with_name("gate_state.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def failed_delivery(_context):
        raise TimeoutError("simulated hook output lost before delivery")

    with pytest.raises(TimeoutError):
        module.emit_policy({"prompt": "implement A", "session_id": "delivery-test"},
                           "FULL POLICY", str(tmp_path), failed_delivery)
    observed = []
    module.emit_policy({"prompt": "implement B", "session_id": "delivery-test"},
                       "FULL POLICY", str(tmp_path), observed.append)
    assert observed == ["FULL POLICY"]


def test_stale_session_lease_self_heals(tmp_path):
    (tmp_path / ".sdlc/state/gate-sessions").mkdir(parents=True)
    session = "stale-lease"
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()
    lease = tmp_path / ".sdlc/state/gate-sessions" / f".lock-{int(digest[:2], 16) % 64:02d}"
    lease.mkdir()
    os.utime(lease, (0, 0))
    assert "PLAN-REVIEW" in _run_session("implement A", tmp_path, session)
    assert "PLAN-REVIEW" not in _run_session("implement B", tmp_path, session)


def test_global_escape_hatch_restores_always_on(tmp_path):
    # SIGMA_GATE_GLOBAL=1 injects even with no .sdlc/ anywhere (pre-0.6 behavior)
    env = {**os.environ, "SIGMA_GATE_GLOBAL": "1", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    proc = subprocess.run(
        ["bash", str(HOOK)], input=json.dumps({"prompt": "hello"}),
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "GOAL-BASED SDLC" in json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def test_output_is_valid_json_with_policy():
    out = _run("implement a new feature")
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "GOAL-BASED SDLC" in ctx


def test_code_intent_flagged():
    ctx = _run("implement the parser in parser.py")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_question_intent_is_read_only():
    ctx = _run("what does this function do?")["hookSpecificOutput"]["additionalContext"]
    assert "READ-ONLY" in ctx


def test_no_onshot_specifics():
    ctx = _run("hello")["hookSpecificOutput"]["additionalContext"]
    for banned in ("R2", "Temporal", "GPU", "OnShot", "episode"):
        assert banned not in ctx


def _run_raw(stdin_text):
    # Send arbitrary raw stdin (not wrapped in {"prompt": ...}) to pin the
    # always-valid-JSON invariant on the malformed-input paths.
    proc = subprocess.run(
        ["bash", str(HOOK)],
        input=stdin_text, capture_output=True, text=True, env=_GLOBAL_ENV,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON


def test_empty_stdin_is_valid_json():
    out = _run_raw("")
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


def test_garbage_stdin_is_valid_json():
    out = _run_raw("garbage{{{ not json")
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


# --- intent classification accuracy ---

def test_multiline_code_intent_not_downgraded_to_ask():
    # a real code request whose LATER line starts with a question word must stay CODE:
    # the `asky` prefix test is line-oriented, so it must judge only the first line.
    ctx = _run("rename the column\nwhat else")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_bare_add_imperative_is_code_change():
    ctx = _run("add logging to the auth flow")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


def test_write_imperative_is_code_change():
    ctx = _run("write a function that sorts the list")["hookSpecificOutput"]["additionalContext"]
    assert "CODE CHANGE" in ctx


# --- always-valid-JSON invariant: hostile inputs must never break the emit ---

@pytest.mark.parametrize("prompt", [
    'implement "a\\b"',            # embedded quote + backslash
    'refactor\nthen explain it',  # newline
    'fix 100% of %s in the printf',  # percent / format tokens
    '--help',                     # leading dashes (option-injection bait)
    'build the thing $(whoami)',  # shell-ish
    pytest.param('x' * 16384, id='16k-char-prompt'),  # long input — 16KB exercises the long-prompt path;
                                  # 200k was Ubuntu-CI-fragile and a 200k param mangled the failure log
    'naïve café 日本語 🚀 prompt', # unicode
])
def test_prompt_always_emits_valid_json(prompt):
    out = _run(prompt)  # _run already asserts rc==0 and json.loads (raises on invalid JSON)
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


def _run_bytes(stdin_bytes):
    proc = subprocess.run(
        ["bash", str(HOOK)], input=stdin_bytes, capture_output=True, env=_GLOBAL_ENV,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)  # raises if invalid JSON (json.loads accepts bytes)


@pytest.mark.parametrize("raw", [
    b'{"prompt":null}',
    b'{"prompt":123}',
    b'[1,2,3]',
    b'{"no":"prompt"}',
    b'not json at all',
    b'\xff\xfe\x80',                # invalid UTF-8
    b'{"prompt":"x\xed\xa0\x80y"}', # surrogate bytes inside a JSON string
])
def test_malformed_stdin_always_emits_valid_json(raw):
    out = _run_bytes(raw)
    assert "GOAL-BASED SDLC" in out["hookSpecificOutput"]["additionalContext"]


# --- behavioral spec: a labeled prompt corpus pins the intent classifier ---
# The hook IS the deterministic enforcement of "run the spine", so this is the closest test of
# behavior (not just plumbing). "code" -> CODE CHANGE banner; "ask" -> READ-ONLY banner;
# "standard" -> policy only (neither banner).
_CORPUS = [
    # clear code-change (imperative, no leading question)
    ("implement the retry in client.py", "code"),
    ("refactor the auth module", "code"),
    ("add a cache to the fetch helper", "code"),
    ("fix the off-by-one in pagination", "code"),
    ("migrate the config to TOML", "code"),
    ("rewrite this to be async", "code"),
    ("delete the dead helper and update its callers", "code"),
    ("wire up the webhook handler", "code"),
    ("rename the column and adjust the schema", "code"),
    # clear read-only / conversational (leading interrogative, no strong code signal)
    ("what does the loop do when it parks?", "ask"),
    ("how does the plan-review gate work?", "ask"),
    ("why is the hook fail-open?", "ask"),
    ("explain the self-improving knowledge graph", "ask"),
    ("list the skills this plugin ships", "ask"),
    ("show me where the budget is configured", "ask"),
    ("which backlog sources are supported?", "ask"),
    # ambiguous: a question that is really a code request (interrogative + strong signal) -> code
    ("how should I implement the parser in parser.py?", "code"),
    ("can you refactor this function?", "code"),
    # standard: neither interrogative nor codey
    ("hello", "standard"),
    ("good morning", "standard"),
    ("", "standard"),
]


@pytest.mark.parametrize("prompt,expected", _CORPUS)
def test_intent_corpus(prompt, expected):
    ctx = _run(prompt)["hookSpecificOutput"]["additionalContext"]
    assert "GOAL-BASED SDLC" in ctx                       # the policy is always present
    if expected == "code":
        assert "CODE CHANGE" in ctx and "READ-ONLY" not in ctx
    elif expected == "ask":
        assert "READ-ONLY" in ctx and "CODE CHANGE" not in ctx
    else:                                                 # standard: neither banner
        assert "CODE CHANGE" not in ctx and "READ-ONLY" not in ctx


def test_classifier_no_false_code_flag_on_readonly_corpus():
    # aggregate bound: NONE of the read-only prompts may be flagged as a code change (false positives
    # are the costlier error here — they'd force the full spine onto a plain question).
    asks = [p for p, e in _CORPUS if e == "ask"]
    flagged = [p for p in asks if "CODE CHANGE" in _run(p)["hookSpecificOutput"]["additionalContext"]]
    assert flagged == [], f"read-only prompts mis-flagged as code: {flagged}"
