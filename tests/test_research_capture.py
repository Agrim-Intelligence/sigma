"""The research-capture hook (PostToolUse) auto-collects WebSearch/WebFetch into the KG corpus —
but ONLY for a project that opted in (.sdlc/config.json -> knowledge_graph.enabled). It ships
globally, so the critical property is: no-op (and never error) for every project that didn't opt in."""
import io, json, os, shutil, subprocess, pathlib, importlib.util, tempfile
import pytest

HOOK = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "research_capture.py"


def _mod():
    spec = importlib.util.spec_from_file_location("research_capture", HOOK)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


# --- pure breadcrumb builder ---

def test_breadcrumb_for_websearch():
    out = _mod().build_breadcrumb("WebSearch", {"query": "rust async runtime"}, "some results")
    assert out is not None
    path, md = out
    assert path.startswith(".sdlc/knowledge/research/web/") and path.endswith(".md")
    assert "rust async runtime" in md and "source: websearch" in md and "some results" in md


def test_breadcrumb_for_webfetch_uses_url():
    path, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"}, {"text": "body"})
    assert "https://example.com/x" in md and "source: webfetch" in md


def test_no_breadcrumb_for_non_web_tool():
    assert _mod().build_breadcrumb("Bash", {"command": "ls"}, "out") is None


def test_no_breadcrumb_for_empty_subject():
    rc = _mod()
    assert rc.build_breadcrumb("WebSearch", {}, "resp") is None          # failed/empty call -> no junk
    assert rc.build_breadcrumb("WebFetch", {"url": "   "}, "resp") is None


def test_heading_has_no_embedded_newline():
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "line1\nline2"}, "r")
    heading = md.split("---\n\n", 1)[1].splitlines()[0]
    assert "\n" not in heading and "line1 line2" in heading


# --- security: never persist raw web bodies (secret-shaped substrings are redacted) ---

_SECRETS = {
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "gh": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "pw_plain": "password: hunter2superlong",
    "pw_json": '"api_key":"sk-livesecretvalue0123456789"',
    "bearer": "Bearer abc123def456ghi789",
}


def test_secret_shaped_body_is_redacted():
    body = "intro text " + " and ".join(_SECRETS.values()) + " trailer"
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"}, body)
    for secret in ("AKIAIOSFODNN7EXAMPLE", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                   "hunter2superlong", "sk-livesecretvalue0123456789", "abc123def456ghi789"):
        assert secret not in md, "leaked secret substring: %s" % secret
    assert "[REDACTED" in md              # something was actually redacted, not just dropped by the cap


def test_secret_glued_to_preceding_word_char_is_redacted():
    # a secret with NO delimiter before it must still be caught — a leading \b would let it slip through
    for glued, secret in [("requestid=AKIAIOSFODNN7EXAMPLE&next", "AKIAIOSFODNN7EXAMPLE"),
                          ("prefix-ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
                          ("x" + _SECRETS["jwt"], _SECRETS["jwt"])]:
        _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"}, glued)
        assert secret not in md, "glued secret leaked: %s" % secret


def test_authorization_basic_header_is_redacted():
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"},
                                    "Authorization: Basic dXNlcjpwYXNzd29yZA== rest of page")
    assert "dXNlcjpwYXNzd29yZA==" not in md and "[REDACTED" in md


def test_secret_straddling_the_excerpt_boundary_is_redacted():
    # the secret sits fully inside the first 400 chars, so the cap alone would KEEP it — only
    # scrub-before-cap removes it. Proves the scrub (not the truncation) is doing the work here.
    body = "x" * 380 + "AKIAIOSFODNN7EXAMPLE" + " and more text after the key"  # key at chars 380-399
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "q"}, body)
    assert "AKIAIOSFODNN7EXAMPLE" not in md


def test_common_word_token_is_not_over_redacted():
    # "token" is the most common word in captured research — it must survive as prose, and only a real
    # assignment (token: <value>) gets redacted
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "q"},
                                    "token management and token economics are hard topics")
    assert "token management" in md and "token economics" in md and "[REDACTED" not in md
    _, md2 = _mod().build_breadcrumb("WebSearch", {"query": "q"}, "config has token: s3cretValue123 inside")
    assert "s3cretValue123" not in md2 and "[REDACTED" in md2


def test_credential_in_url_subject_is_scrubbed():
    # a pre-signed URL / query param carries the secret in the SUBJECT, which lands in the frontmatter,
    # the heading, AND the slugified filename — all three must be scrubbed
    path, md = _mod().build_breadcrumb(
        "WebFetch", {"url": "https://s3.amazonaws.com/b/o?X-Amz-Credential=AKIAIOSFODNN7EXAMPLE&sig=x"}, "body")
    assert "AKIAIOSFODNN7EXAMPLE" not in md
    assert "akiaiosfodnn7example" not in path.lower()   # not smuggled through the filename slug either


def test_secret_inside_json_response_is_redacted():
    # WebFetch often returns a dict; it gets json.dumps'd, so redaction must survive that serialization
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"},
                                    {"text": "token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ok"})
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in md and "[REDACTED" in md


# --- unterminated / oversized private keys (#534) ---
# `_PEM_LINE` is non-secret filler shaped like a real PEM body line (a 64-char base64 run).
_PEM_LINE = "MIIBVwIBADANBgkqhkiG9w0BAQEFAASCATkwggI1AgEAAoIBAQDBn4t3sQ2K9xVq"


def test_unterminated_private_key_in_a_json_response_is_redacted():
    # the dominant carrier: a dict response is json.dumps'd, so the key's newlines arrive as literal
    # \n ESCAPES rather than real ones — a fallback that only walked real whitespace stopped dead at
    # the first escape and published the entire body.
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"},
                                    {"text": "-----BEGIN PRIVATE KEY-----\n" + _PEM_LINE + "\n" + _PEM_LINE})
    assert _PEM_LINE not in md and "[REDACTED:private-key]" in md


def test_terminated_key_straddling_the_old_pre_slice_boundary_leaves_no_body():
    # a key big enough that its END marker falls beyond the 4000-char pre-slice the hook used to take
    # BEFORE scrubbing: truncating first manufactured an unterminated key out of a well-formed one,
    # and the header-only fallback then let its body through into the excerpt.
    body = ("intro\n-----BEGIN PRIVATE KEY-----\n" + "\n".join([_PEM_LINE] * 70)
            + "\n-----END PRIVATE KEY-----\n")
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com"}, body)
    assert _PEM_LINE not in md and "[REDACTED:private-key]" in md


def test_excerpt_is_capped_far_below_the_raw_body():
    _, md = _mod().build_breadcrumb("WebSearch", {"query": "q"}, "x" * 10000)
    excerpt = md.split("---\n\n", 1)[1].split("\n\n", 1)[1]   # everything after the "# heading" line
    assert len(excerpt) < 600 and "x" * 4001 not in md        # not the raw 4000-char dump


def test_enabled_must_be_strict_true():
    rc = _mod()
    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp) / ".sdlc"; base.mkdir()
        (base / "config.json").write_text('{"knowledge_graph":{"enabled":"false"}}')
        assert rc._kg_enabled(tmp) is False             # a stringy value must not opt in
        (base / "config.json").write_text('{"knowledge_graph":{"enabled":true}}')
        assert rc._kg_enabled(tmp) is True


# --- end-to-end gating + fail-open (the hook ships globally) ---

def _run(project_dir, payload_bytes):
    return subprocess.run(["python3", str(HOOK)], input=payload_bytes,
                          capture_output=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})


def _project(tmp, enabled):
    base = pathlib.Path(tmp) / ".sdlc"; base.mkdir(parents=True)
    (base / "config.json").write_text(json.dumps({"knowledge_graph": {"enabled": enabled}}))
    return tmp


def _web_payload():
    return json.dumps({"tool_name": "WebSearch", "tool_input": {"query": "kubernetes hpa"},
                       "tool_response": "results about hpa"}).encode()


def test_writes_breadcrumb_when_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        webdir = pathlib.Path(tmp) / ".sdlc" / "knowledge" / "research" / "web"
        files = list(webdir.glob("*.md"))
        assert files and "kubernetes hpa" in files[0].read_text()


def test_planted_secret_never_reaches_disk():
    # the security guarantee, end to end: a secret in the raw web response must not land in the file
    SECRET = "AKIAIOSFODNN7EXAMPLE"
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        payload = json.dumps({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/leak"},
                              "tool_response": "here is a key %s in the page body" % SECRET}).encode()
        proc = _run(tmp, payload)
        assert proc.returncode == 0
        files = list((pathlib.Path(tmp) / ".sdlc" / "knowledge" / "research" / "web").glob("*.md"))
        assert files, "breadcrumb should still be written (KG enabled)"
        disk = files[0].read_text()
        assert SECRET not in disk and "[REDACTED" in disk


def test_noop_when_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, False)
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        assert not (pathlib.Path(tmp) / ".sdlc" / "knowledge").exists()   # nothing written


def test_noop_when_no_sdlc_project():
    # a project that never ran /agrim-init must be completely untouched
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run(tmp, _web_payload())
        assert proc.returncode == 0
        assert not (pathlib.Path(tmp) / ".sdlc").exists()


def test_fail_open_on_garbage_stdin():
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        proc = _run(tmp, b"garbage{{ not json")
        assert proc.returncode == 0          # never errors the tool call


def test_hook_wired_in_hooks_json():
    hooks = json.loads((pathlib.Path(__file__).resolve().parent.parent / "hooks" / "hooks.json").read_text())
    post = hooks["hooks"].get("PostToolUse", [])
    assert any("WebSearch" in h.get("matcher", "") and "WebFetch" in h.get("matcher", "")
               and "research_capture.py" in json.dumps(h) for h in post)


# --- the gate's secret shapes, through the hook path (#2718) --------------------------------------
# The hook path-loads scrub.py (one shared `SHAPE_RULES`, no inlined copy), so every shape the leak
# gate detects must vanish from the breadcrumb too. Fixture hygiene: tests/ is scanned by that gate,
# so every token is built by concatenation and never spelled whole on one source line.


def _tok(n, seed="2718"):
    """Deterministic ALPHANUMERIC filler (no `_`/`-`: webhook path segments are `[A-Za-z0-9]`)."""
    import random
    rng = random.Random(seed + str(n))
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


#: (id, planted text, value that must vanish) -- the same table as tests/test_scrub.py, hardcoded
_SHAPE_TABLE = [
    ("H1-1 gh-token", "ghp_" + "A" * 36, "ghp_" + "A" * 36),
    ("H1-2 github-pat", "github_" + "pat_11ABCDEFG0123456789_" + "a" * 59,
     "github_" + "pat_11ABCDEFG0123456789_" + "a" * 59),
    ("H1-3 slack-token", "xo" + "xb-1234567890123-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
     "xo" + "xb-1234567890123-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
    ("H1-4 anthropic-key", "sk-ant-api03-" + "A" * 93 + "-AbCdEfAA", "sk-ant-api03-" + "A" * 93 + "-AbCdEfAA"),
    ("H1-5 google-key", "AIzaSy" + "A" * 33, "AIzaSy" + "A" * 33),
    ("H1-6 stripe-key", "sk_live_" + "A" * 24, "sk_live_" + "A" * 24),
    ("H1-7 npm-token", "npm_" + "A" * 36, "npm_" + "A" * 36),
    ("H1-8a gitlab-token 20", "glpat-" + "A" * 20, "glpat-" + "A" * 20),
    ("H1-8b gitlab-token 30", "glpat-" + "A" * 30, "glpat-" + "A" * 30),
    ("H1-9 url-password", "https:" + "//user:S3cretPassw0rd@example.com/r.git", "S3cretPassw0rd"),
    ("G-1 openai-key", "sk-" + "proj-" + _tok(40), "sk-" + "proj-" + _tok(40)),
    ("G-2 sk-key", "sk-" + _tok(24), "sk-" + _tok(24)),
    ("G-3 pypi-token", "pypi-" + "AgE" + _tok(60), "pypi-" + "AgE" + _tok(60)),
    ("G-4 slack-webhook", "https://hooks.slack.com/" + "services" + "/T0123ABCD/B0456EFGH/" + _tok(24),
     "T0123ABCD/B0456EFGH/" + _tok(24)),
    ("G-5 credential-assignment", "AWS_SECRET_ACCESS_KEY = '" + "Q" + _tok(39) + "'", "Q" + _tok(39)),
    ("G-6 url-password ip", "postgres://svc:" + _tok(14) + "@192.0.2.7:5432/db", _tok(14)),
]
_SHAPE_IDS = [row[0] for row in _SHAPE_TABLE]
_SHAPE_PARAMS = [row[1:] for row in _SHAPE_TABLE]


@pytest.mark.parametrize("text,value", _SHAPE_PARAMS, ids=_SHAPE_IDS)
def test_every_gate_shape_value_is_redacted_in_the_breadcrumb(text, value):
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"}, "intro " + text + " tail")
    assert value not in md, md
    # The TAIL must vanish too: `value not in md` alone goes green when only a prefix was replaced
    # (an exact-length `{n}` rule leaves the rest of a longer run standing beside the placeholder).
    assert value[-10:] not in md, md
    assert "[REDACTED" in md and "intro" in md


def test_long_gitlab_token_leaves_no_tail_in_the_breadcrumb():
    """H1-8b's control (#2718 review round 1): with the gate's original `{20}` the breadcrumb kept
    `[REDACTED:gitlab-token]AAAAAAAAAA` -- the 10-char tail the `{20,}` widening exists to stop.
    Recorded red against `{20}`, green against `{20,}`."""
    _, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/x"},
                                    "intro " + "glpat-" + "A" * 30 + " tail")
    assert "intro [REDACTED:gitlab-token] tail" in md, md
    assert "AAAA" not in md, md


def test_gate_shape_in_the_subject_is_redacted_everywhere():
    # the subject lands in the frontmatter, the heading AND the slugified filename
    token = "npm_" + "A" * 36
    path, md = _mod().build_breadcrumb("WebFetch", {"url": "https://example.com/?k=" + token}, "body")
    assert token not in md and token.lower() not in path.lower(), (path, md)


def test_all_shapes_redacted_end_to_end_through_the_real_hook():
    """Through the REAL subprocess (`python3 hooks/research_capture.py`), one breadcrumb per shape --
    one run per shape rather than one payload with all 15, because the 400-char excerpt cap would
    otherwise drop the later shapes unread and the assertion would go green without redacting them.
    A dict `tool_response` forces the json.dumps path."""
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, True)
        for sid, text, value in _SHAPE_TABLE:
            payload = json.dumps({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/" + sid[:4]},
                                  "tool_response": {"text": "page says " + text + " and more"}}).encode()
            proc = _run(tmp, payload)
            assert proc.returncode == 0, (sid, proc.stderr)
        files = sorted((pathlib.Path(tmp) / ".sdlc" / "knowledge" / "research" / "web").glob("*.md"))
        assert len(files) == len(_SHAPE_TABLE), files
        disk = "\n".join(f.read_text(encoding="utf-8") for f in files)
        for sid, _, value in _SHAPE_TABLE:
            assert value not in disk, sid
            assert value[-10:] not in disk, sid   # no residual tail either (H1-8b's blind spot)
        assert disk.count("[REDACTED") >= len(_SHAPE_TABLE), disk


def _hook_without_skills(tmp):
    """A plugin layout holding ONLY the hook (no `skills/`): scrub.py cannot be path-loaded."""
    hooks = pathlib.Path(tmp) / "plugin" / "hooks"; hooks.mkdir(parents=True)
    shutil.copy(HOOK, hooks / "research_capture.py")
    return hooks / "research_capture.py"


def _mod_at(path):
    spec = importlib.util.spec_from_file_location("research_capture_copy", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_hook_without_scrub_writes_no_breadcrumb_and_exits_zero():
    """Load failure fails OPEN for the session (exit 0) but CLOSED for the write: no breadcrumb is
    ever written unscrubbed, and stderr says why. No fallback copy of the patterns exists (that
    would be the third copy). In-process, `_scrub` raises so ledger/actionlog take their degrade
    path, and `_SECRET_PATTERNS` reads as the empty tuple."""
    with tempfile.TemporaryDirectory() as tmp:
        hook = _hook_without_skills(tmp)
        project = pathlib.Path(tmp) / "proj"; project.mkdir()
        _project(project, True)
        payload = json.dumps({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/x"},
                              "tool_response": "body with " + "ghp_" + "A" * 36}).encode()
        proc = subprocess.run(["python3", str(hook)], input=payload, capture_output=True,
                              env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)})
        assert proc.returncode == 0
        assert not (project / ".sdlc" / "knowledge").exists(), "an unscrubbed breadcrumb was written"
        assert b"scrub.py unavailable" in proc.stderr, proc.stderr
        rc = _mod_at(hook)
        with pytest.raises(RuntimeError, match="scrub.py unavailable"):
            rc._scrub("x")
        assert rc._SECRET_PATTERNS == ()


def test_disabled_project_never_loads_scrub(monkeypatch):
    """The lazy contract: a project that did not opt in must stay a fast no-op that never compiles
    the ~20 regexes. In-process, the memo globals stay unset after `main()`; through the real
    subprocess, a no-skills copy on a DISABLED project exits 0 with EMPTY stderr (no load attempted,
    so no `scrub.py unavailable` line)."""
    rc = _mod()
    with tempfile.TemporaryDirectory() as tmp:
        _project(tmp, False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", tmp)
        monkeypatch.setattr("sys.stdin", io.StringIO(_web_payload().decode()))
        with pytest.raises(SystemExit) as e:
            rc.main()
        assert e.value.code == 0
        assert vars(rc).get("_SCRUB_MOD") is None and vars(rc).get("_SCRUB_ERR") is None
        assert not (pathlib.Path(tmp) / ".sdlc" / "knowledge").exists()
    with tempfile.TemporaryDirectory() as tmp:
        hook = _hook_without_skills(tmp)
        project = pathlib.Path(tmp) / "proj"; project.mkdir()
        _project(project, False)
        proc = subprocess.run(["python3", str(hook)], input=_web_payload(), capture_output=True,
                              env={**os.environ, "CLAUDE_PROJECT_DIR": str(project)})
        assert proc.returncode == 0 and proc.stderr == b"", proc.stderr
