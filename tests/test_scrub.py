"""The shared secret-shaped redactor used by the board mirror + cross-check (scrub.py). Location-only
capture rule: a match becomes a typed placeholder, never the value. Deterministic, $0."""
import pathlib, importlib.util, re, time
import pytest

S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, S / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_scrub_redacts_shape_tokens_and_never_emits_the_value():
    scrub = _mod("scrub").scrub
    for secret in ("AK" "IAABCDEFGHIJKLMNOP", "AS" "IAABCDEFGHIJKLMNOP", "ghp_" + "a" * 30,
                   "ey" "JhbGciOiJIUzI1.eyJzdWIiOiIxMjM.SflKxwRJSMeKKF2QT4"):
        out = scrub("prefix " + secret + " suffix")
        assert secret not in out and "REDACTED" in out


def test_scrub_catches_secret_glued_to_a_word_char():
    # the reason the shape patterns carry NO \b anchor: a secret glued to a preceding char must not slip
    scrub = _mod("scrub").scrub
    assert "AK" "IAABCDEFGHIJKLMNOP" not in scrub("id=AK" "IAABCDEFGHIJKLMNOP")


def test_scrub_key_value_assignment_redacts_value_keeps_key():
    scrub = _mod("scrub").scrub
    out = scrub('password: "hunter2xyz"')
    assert "hunter2xyz" not in out and "password" in out


def test_scrub_does_not_over_redact_ordinary_prose():
    # bare "token" is deliberately not keyworded — it must survive in ordinary text
    scrub = _mod("scrub").scrub
    assert scrub("we spent a lot of token budget today") == "we spent a lot of token budget today"


def test_scrub_empty_is_passthrough():
    scrub = _mod("scrub").scrub
    assert scrub("") == "" and scrub(None) is None


def test_commit_secret_hits_returns_only_rule_and_column_and_exempts_known_fixture():
    module = _mod("scrub")
    synthetic = "AKIA" + "Z" * 16
    fixture = "AKIA" + "IOSFODNN7EXAMPLE"
    assert module.commit_secret_hits("x=" + synthetic) == [("aws-key", 3)]
    assert module.commit_secret_hits("x=" + fixture) == []
    assert module.commit_secret_hits('SECRET = "' + fixture + '"') == []


# --- the unterminated private-key fallback (#534) -----------------------------------------------
# The well-formed BEGIN..END form belongs to the FIRST pattern; everything below pins the SECOND one,
# the fallback for a key whose END marker never arrived (truncated at the source, or a body that was
# cut mid-capture). It used to replace the header alone and let every following body line through.
# `_B64_LINE` is non-secret filler shaped like a real PEM body line (a 64-char base64 run).
_B64_LINE = "MIIBVwIBADANBgkq" + "hkiG9w0BAQEFAASCATkwggI1AgEAAoIBAQDBn4t3sQ2K9xVq"


def test_scrub_redacts_the_body_of_an_unterminated_private_key():
    scrub = _mod("scrub").scrub
    out = scrub("intro\n-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert _B64_LINE not in out and "[REDACTED:private-key]" in out
    assert "intro" in out                       # only the key is consumed, not what preceded it


def test_scrub_redacts_an_unterminated_encrypted_pem_including_its_headers():
    # RFC1421 encrypted PEM puts Proc-Type:/DEK-Info: BETWEEN the header and the body, so a fallback
    # that consumed base64 runs alone would stop at them and leave the whole body underneath.
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\n"
                "DEK-Info: AES-128-CBC,1B35E2A9F0C4D7E8\n\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert _B64_LINE not in out and "DEK-Info" not in out
    assert "[REDACTED:private-key]" in out


def test_scrub_redacts_a_short_final_fragment_of_a_truncated_key():
    # the exact artifact of a capped capture: the last body line is cut mid-run, leaving a <16-char
    # remainder at end-of-input. Consumed only THERE — never mid-document (see the residual pin below).
    scrub = _mod("scrub").scrub
    assert scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\nSHORTFRAG") == "[REDACTED:private-key]"


def test_scrub_leaves_prose_following_a_bare_private_key_header():
    # a document merely DISCUSSING the marker keeps its text: the guard against a consume-to-EOF fallback
    scrub = _mod("scrub").scrub
    assert (scrub("-----BEGIN PRIVATE KEY----- is the marker format used by PEM files.")
            == "[REDACTED:private-key] is the marker format used by PEM files.")


def test_scrub_terminated_key_is_still_owned_by_the_first_pattern():
    # ordering guard: the DOTALL BEGIN..END pattern must stay ABOVE the fallback, so a well-formed key
    # is matched as one unit and the greedy body run can never eat an intervening END marker.
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n-----END PRIVATE KEY-----\ntrailer")
    assert out == "[REDACTED:private-key]\ntrailer"


# --- ACCEPTED LIMITATIONS of the fallback (#534) ------------------------------------------------
# Consumption walks recognized headers and long base64 runs and STOPS at the first sub-16-char run or
# non-base64 byte inside the body; everything from that gap onward survives, and it is NOT bounded to
# a fixed number of characters. The three tests below pin that honestly — including the part that
# stings — so the next reader meets the real behavior instead of a comforting bound. All three are
# pins on CURRENT behavior, not red-first: each documents an exposure being accepted, and each must
# be revisited deliberately if the run rule or the header list is ever widened.

def test_scrub_accepted_limitation_a_short_run_mid_document_stops_consumption():
    """A <16-char base64 run that is NOT at end-of-input stops the walk. Consuming it would eat the
    ordinary short words that follow a header mid-document. Here the remainder happens to be small —
    the next test covers the case where it is not, which is the one that matters."""
    scrub = _mod("scrub").scrub
    out = scrub("--" "---BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\nSHORTFRAG\nmore prose here.")
    assert _B64_LINE not in out                 # consumed up to the gap...
    assert "SHORTFRAG" in out                   # ...and the walk stops there


def test_scrub_accepted_limitation_a_mangled_body_publishes_everything_after_the_gap():
    """THE RESIDUAL IS UNBOUNDED, and this is the test that says so. A foreign byte partway down the
    body splits a line into two sub-16-char runs, the walk stops at the first of them, and every
    COMPLETE key line after that point is published — 128 bytes of body here, arbitrarily more in a
    longer key. So the fallback's real guarantee is narrower than "an unterminated key is redacted":
    it fully consumes a CANONICAL unterminated key (a clean body cut short at the source — the case
    it exists for, pinned above), and a MANGLED body only as far as the gap."""
    scrub = _mod("scrub").scrub
    mangled = _B64_LINE[:8] + "!" + _B64_LINE[8:]        # one non-base64 byte, mid-line
    out = scrub("--" "---BEGIN PRIVATE KEY-----\n" + _B64_LINE + "\n" + mangled
                + "\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert out.startswith("[REDACTED:private-key]")      # header + PRE-gap body ARE consumed
    assert out.count(_B64_LINE) == 2                     # the two POST-gap lines SURVIVE — accepted


def test_scrub_accepted_limitation_an_unrecognized_header_line_stops_consumption():
    """The header list is closed on purpose: RFC1421 defines only Proc-Type and DEK-Info, and OpenSSH
    keys carry no headers at all, so matching `Word:` open-endedly would eat ordinary prose. The cost
    is that an unrecognized header between BEGIN and the body — `Comment:` is the one seen in the
    wild — halts the walk and leaves the entire body behind it."""
    scrub = _mod("scrub").scrub
    out = scrub("--" "---BEGIN PRIVATE KEY-----\nComment: my key\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert out.startswith("[REDACTED:private-key]")      # the marker is still replaced...
    assert out.count(_B64_LINE) == 2                     # ...but the body survives — accepted


def test_research_capture_hook_uses_scrub_py_itself():
    """The hook used to carry an inlined copy of these patterns, held in sync by a parity test; now it
    path-loads THIS file (#2718), so the drift guard is identity: the module the hook exposes IS
    scrub.py, its `_scrub` wrapper delegates to it, and no `_SECRET_PATTERNS = (` table is spelled
    in the hook's own source."""
    rc_path = pathlib.Path(__file__).resolve().parent.parent / "hooks" / "research_capture.py"
    spec = importlib.util.spec_from_file_location("research_capture", rc_path)
    rc = importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)
    assert pathlib.Path(rc._SCRUB.__file__).resolve() == (S / "scrub.py").resolve()
    planted = "see " + "ghp_" + "A" * 36 + " and " + "npm_" + "A" * 36
    assert rc._scrub(planted) == rc._SCRUB.scrub(planted) and "A" * 36 not in rc._scrub(planted)
    assert rc._SECRET_PATTERNS is rc._SCRUB._SECRET_PATTERNS
    assert "_SECRET_PATTERNS = (" not in rc_path.read_text(encoding="utf-8"), "the hook re-inlined a copy"


# --- the gate's secret shapes, shared with tools/leak_scan.py (#2718) ---------------------------
# Every single-regex shape the publish leak gate detects must also be redacted at runtime, from ONE
# pattern set (scrub.py's `SHAPE_RULES`). Fixture hygiene: tests/ is on the public surface the gate
# scans, so every token below is built by concatenation and never spelled whole on one source line
# (a URL is split at `://`, `github_pat_` after `github_`, PEM bodies only via `_B64_LINE`).


def _tok(n, seed="2718"):
    """Deterministic ALPHANUMERIC filler (no `_`/`-`: the slack-webhook path segment and sk-key
    bodies are `[A-Za-z0-9]` only, so a `_` would break the very shape under test)."""
    import random
    rng = random.Random(seed + str(n))
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


#: (id, planted text, value that must vanish, context substrings that must survive)
_SHAPE_TABLE = [
    ("H1-1 gh-token", "ghp_" + "A" * 36, "ghp_" + "A" * 36, ()),
    ("H1-2 github-pat", "github_" + "pat_11ABCDEFG0123456789_" + "a" * 59,
     "github_" + "pat_11ABCDEFG0123456789_" + "a" * 59, ()),
    ("H1-3 slack-token", "xo" + "xb-1234567890123-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
     "xo" + "xb-1234567890123-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", ()),
    ("H1-4 anthropic-key", "sk-ant-api03-" + "A" * 93 + "-AbCdEfAA", "sk-ant-api03-" + "A" * 93 + "-AbCdEfAA", ()),
    ("H1-5 google-key", "AIzaSy" + "A" * 33, "AIzaSy" + "A" * 33, ()),
    ("H1-6 stripe-key", "sk_live_" + "A" * 24, "sk_live_" + "A" * 24, ()),
    ("H1-7 npm-token", "npm_" + "A" * 36, "npm_" + "A" * 36, ()),
    ("H1-8a gitlab-token 20", "glpat-" + "A" * 20, "glpat-" + "A" * 20, ()),
    ("H1-8b gitlab-token 30", "glpat-" + "A" * 30, "glpat-" + "A" * 30, ()),
    ("H1-9 url-password", "https:" + "//user:S3cretPassw0rd@example.com/r.git", "S3cretPassw0rd",
     ("https://", "example.com")),
    ("G-1 openai-key", "sk-" + "proj-" + _tok(40), "sk-" + "proj-" + _tok(40), ()),
    ("G-2 sk-key", "sk-" + _tok(24), "sk-" + _tok(24), ()),
    ("G-3 pypi-token", "pypi-" + "AgE" + _tok(60), "pypi-" + "AgE" + _tok(60), ()),
    ("G-4 slack-webhook", "https://hooks.slack.com/" + "services" + "/T0123ABCD/B0456EFGH/" + _tok(24),
     "T0123ABCD/B0456EFGH/" + _tok(24), ("hooks.slack.com",)),
    ("G-5 credential-assignment", "AWS_SECRET_ACCESS_KEY = '" + "Q" + _tok(39) + "'", "Q" + _tok(39),
     ("AWS_SECRET_ACCESS_KEY",)),
    ("G-6 url-password ip", "postgres://svc:" + _tok(14) + "@192.0.2.7:5432/db", _tok(14),
     ("postgres://", "192.0.2.7")),
]


@pytest.mark.parametrize("text,value,survivors", [row[1:] for row in _SHAPE_TABLE],
                         ids=[row[0] for row in _SHAPE_TABLE])
def test_every_gate_shape_value_is_redacted_by_scrub(text, value, survivors):
    """The VALUE must vanish (for a grouped rule that is group 1: the password, the webhook path,
    the quoted assignment value) — not merely the whole planted string, which a partial redaction
    would also satisfy. The context around a grouped value survives."""
    out = _mod("scrub").scrub("t " + text + " end")
    assert value not in out, out
    # The TAIL must vanish too: `value not in out` alone goes green when only a prefix was replaced
    # (an exact-length `{n}` rule leaves the rest of a longer run standing beside the placeholder).
    assert value[-10:] not in out, out
    assert "[REDACTED" in out, out
    for keep in survivors:
        assert keep in out, (keep, out)
    assert out.startswith("t ") and out.endswith(" end"), out
    if not survivors:
        # the planted text IS the value: nothing of it may remain around the one placeholder
        assert re.fullmatch(r"t \[REDACTED:[a-z-]+\] end", out), out


def test_long_gitlab_token_leaves_no_tail():
    """H1-8b's control (#2718 review round 1): with the gate's original `{20}` this returned
    `t [REDACTED:gitlab-token]AAAAAAAAAA end` -- the 10-char tail the `{20,}` widening exists to
    stop. Recorded red against `{20}`, green against `{20,}`."""
    out = _mod("scrub").scrub("t " + "glpat-" + "A" * 30 + " end")
    assert out == "t [REDACTED:gitlab-token] end", out
    assert "AAAA" not in out, out


def test_specific_sk_prefixes_keep_specific_labels():
    # the specific prefixes run before the generic ones, so the label names the provider
    scrub = _mod("scrub").scrub
    assert "[REDACTED:openai-key]" in scrub("k " + "sk-" + "proj-" + _tok(40))
    assert "[REDACTED:anthropic-key]" in scrub("k " + "sk-ant-api03-" + "A" * 93 + "-AbCdEfAA")
    out = scrub("k " + "github_" + "pat_11ABCDEFG0123456789_" + "a" * 59)
    assert "[REDACTED:github-pat]" in out and "gh-token" not in out


def test_pgp_private_key_block_is_redacted():
    # K-1: the gate's `_KEY_HEADER` already knows the `(?: BLOCK)?` form; the redactor must too
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PGP PRIVATE KEY BLOCK-----\n" + _B64_LINE + "\n" + _B64_LINE + "\n"
                + "-----END PGP PRIVATE KEY BLOCK-----\nafter")
    assert _B64_LINE not in out and "[REDACTED:private-key]" in out
    assert out.endswith("\nafter"), out


def test_unterminated_pgp_block_stops_at_its_armor_header():
    """ACCEPTED LIMITATION (D3): an UNTERMINATED PGP block carries `Version:`/`Comment:` armor
    headers, which the closed header list of the fallback walk does not recognise — the marker is
    replaced and the body survives, the same cost as the `Comment:` case pinned above. The
    terminated form (previous test) is fully redacted."""
    scrub = _mod("scrub").scrub
    out = scrub("-----BEGIN PGP PRIVATE KEY BLOCK-----\nVersion: GnuPG v2\n\n" + _B64_LINE + "\n" + _B64_LINE + "\n")
    assert out.startswith("[REDACTED:private-key]"), out    # the marker is still replaced...
    assert out.count(_B64_LINE) == 2                          # ...but the body survives — accepted


def test_benign_lookalikes_survive_unchanged():
    scrub = _mod("scrub").scrub
    for benign in ("npm_config_cache", "task-runner sk-learn desk-top",
                   "see https://example.com/a-b-c for the-quick-brown-fox",
                   "token budget and token economics", "git clone https://github.com/org/repo.git"):
        assert scrub(benign) == benign


def test_url_password_is_linear_on_hyphenated_text():
    """D2 timing guard. The gate's `url-password` scheme was `[a-z][a-z0-9+.-]*://`, which rescans
    the whole hyphen run from every word boundary: measured 6.8 s on a 100 KB `the-quick-brown-fox-`
    slug and 17.3 s on `a-` x 50,000 (dossier D1). Bounded to `{0,31}`, the WHOLE scrub() including
    every rule measured ~36-45 ms on the same inputs. The 2.0 s ceiling is ~40x headroom over the
    fix and ~3x below the defect, so it cannot flake green on a loaded machine and cannot pass the
    unbounded regex."""
    scrub = _mod("scrub").scrub
    for text in ("the-quick-brown-fox-" * 5000, "a-" * 50000):
        t0 = time.perf_counter()
        assert scrub(text) == text
        assert time.perf_counter() - t0 < 2.0, "url-password scheme is quadratic again"


# --- unquoted env-style secrets (#629) ----------------------------------------------------------
# Every value is built at runtime, never spelled whole on one source line, so the leak gate stays quiet.
def _fake():
    return "Zq" + "9x" * 6 + "Lm"


@pytest.mark.parametrize("key", ["GITHUB_TOKEN", "DB_PASSWORD", "auth_token", "BOT_TOKEN", "app_secret",
                                 "DB_PASSWD", "OPENAI_API_KEY", "x-api-key", "AWS_SECRET_ACCESS_KEY",
                                 "AUTH", "GCP_CREDENTIAL", "SERVICE_CREDENTIALS", "Github_Token", "password"])
@pytest.mark.parametrize("sep", ["=", ": ", " = ", " : ", ":"])
@pytest.mark.parametrize("quote", ["", '"', "'"])
def test_scrub_env_style_names_redact_value_and_keep_key(key, sep, quote):
    scrub = _mod("scrub").scrub
    value = _fake()
    out = scrub("env: " + key + sep + quote + value + quote + " done")
    assert value not in out, out
    assert key in out, out


def test_scrub_quoted_value_with_spaces_and_escapes_leaves_no_tail():
    scrub = _mod("scrub").scrub
    for text in ('DB_PASS' 'WORD="hunt' 'er two words"', "DB_PASS" "WORD='hunt" "er two words'",
                 'DB_PASS' 'WORD="hun' r'\"ter two words"', '{"auth_to' 'ken": "hunt' 'er two words"}'):
        out = scrub(text)
        assert "hunt" not in out and "words" not in out and "two" not in out, out


def test_scrub_unterminated_quote_redacts_to_end_of_line_not_beyond():
    scrub = _mod("scrub").scrub
    out = scrub('DB_PASS' 'WORD="hunter two\nnext line stays')
    assert "hunter" not in out and "two" not in out and "next line stays" in out, out


def test_scrub_json_and_header_forms():
    scrub = _mod("scrub").scrub
    v = _fake()
    for text in ('{"GITHUB_TO' 'KEN":"%s"}' % v, "Authoriz" "ation: " + v, "curl -H 'X-Api-" "Key: %s'" % v):
        assert v not in scrub(text), text


def test_scrub_still_leaves_ordinary_prose_and_metrics_alone():
    scrub = _mod("scrub").scrub
    for text in ("token budget is fine", "we author the docs", "max_tokens=4096 and tokens: 12000",
                 "the authority of the secretary"):
        assert scrub(text) == text


def test_scrub_adversarial_long_inputs_run_in_linear_time():
    scrub = _mod("scrub").scrub
    n = 200_000
    for text in ("token" * (n // 5), "auth " * (n // 5), "pass" "word" + " " * n, "pass" "word=" + "a" * n,
                 'pass' 'word="' + "\\" * n, "TOKEN_" * (n // 6), "sec" "ret=" * (n // 7), "a" * n):
        t = time.perf_counter()
        scrub(text)
        assert time.perf_counter() - t < 5, text[:12]


def test_scrub_authorization_scheme_word_leaves_no_tail_and_empty_quotes_are_left_alone():
    scrub = _mod("scrub").scrub
    v = _fake()
    for text in ("Authoriz" "ation: Tok" "en " + v, "Authoriz" "ation: Api" "Key " + v, "SECRET_KEY_" "BASE=" + v):
        assert v not in scrub(text), text
    assert scrub('gh_auth=""') == 'gh_auth=""'


def test_scrub_values_with_url_chars_nested_json_other_separators_and_open_ended_auth_schemes():
    scrub = _mod("scrub").scrub
    v = _fake()
    cases = ["PASS" "WORD=ab&cd<" + v + ">", "PASS" "WORD=<" + v + ">", "?to" "ken=ab&" + v,
             '{"pass' 'word":\\"' + v + '\\"}', "PASS" "WORD => " + v, "PASS" "WORD := " + v,
             '"pass' 'word" => "' + v + '"', "Authoriz" "ation: NTLM " + v, "Authoriz" "ation: Negotiate " + v]
    for text in cases:
        out = scrub(text)
        assert v not in out and "cd<" not in out, text


def test_scrub_backslash_run_after_a_key_is_linear():
    scrub = _mod("scrub").scrub
    for text in ("pass" "word=" + "\\" * 200_000, "pass" "word=\\" * 30_000):
        t = time.perf_counter()
        scrub(text)
        assert time.perf_counter() - t < 5


def test_the_commit_gate_does_not_widen_with_the_redactor():
    """The wide #629 rules are redactor-only: ordinary code that assigns or sends a credential-named
    variable must not become a commit-gate hit (a gate's false-positive budget differs)."""
    module = _mod("scrub")
    for line in ("my_to" "ken = fetch_it()", "GITHUB_TO" "KEN=" + _fake(), 'headers = {"Authoriz" "ation": "x " + y}'):
        assert module.commit_secret_hits(line) == [], line
    assert {n for n, _ in module.COMMIT_SHAPE_RULES}.isdisjoint({"authorization-header", "credential-assignment-suffix"})


def test_scrub_triple_quotes_and_subscript_keys():
    scrub = _mod("scrub").scrub
    v = _fake()
    for text in ('pass' 'word = """' + v + '"""', "pass" "word = \'\'\'" + v + "\'\'\'",
                 "os.environ['TO" "KEN'] = '" + v + "'", 'os.environ["TO' 'KEN"]="' + v + '"'):
        assert v not in scrub(text), text
