"""#961: the commit gate stops refusing non-literal credential-assignment values.

`work.py commit` refuses an added staged line the anchored `credential-assignment` rule matches. That
rule takes any 4+ character run after a credential-named key, so a call, an env read, a type
annotation and a boolean were refused as credential values and green goals parked at their last
step. The fix is a gate-only post-filter in `scrub.commit_secret_findings` (the shared rule table is
unchanged), a closed set of code-file suffixes that alone get the expression and type exemptions, and
an OPERATOR-only content allowlist, `work.allow_secret_content`, keyed by rule plus exact path or
exact line hash.

BOOTSTRAP. This file is committed through the INSTALLED gate, which still has the bug, so no line
here may hold a credential keyword, an optional quote, `:` or `=`, then 4+ characters. Every sample
line is built at run time from split fragments (`K`, `PW` and `SC` below), never written out.

Every test is red first on the unchanged code, by an assertion: each starts with the nearest
non-secret twin that must now pass (the old gate refuses it), then asserts the literal shapes still
hit. No test prints or logs, so the red proof can attribute every failure."""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

import test_work as tw

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "sigma-loop" / "scripts"


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


S = _mod("scrub")

RULE = "credential-assignment"
K, PW, SC = "tok" + "en", "pass" + "word", "sec" + "ret"
MIX = "Ab1Cd2Ef3Gh"                  # a mixed run: refused by alternation and by letter-and-digit
L30 = "xyz" * 10                     # 30 letters: only the 24-run clause can refuse it
WEAK = "Summer" + "2024"             # one alternation: only the letter-and-digit clause refuses it
UPS = "QX7RZ4" + "KW9P"              # upper-snake: only the alternation clause refuses it
LD = "abcdef" + "12"                 # one alternation: only the letter-and-digit clause refuses it
WORD = "letmein" + "please"          # letters only: no clause refuses it
DOT = "Pa" + "ss.wo" + "rd1"         # dotted, one alternation: no clause refuses it
R1 = "const " + K + " = uiToken();"
R2 = K + ' = os.environ["X"]'
R3 = "def guard(" + K + ": str) -> bool:"
R4 = '"' + K + '": True,'
R5 = K + ' = "' + "hunter2" * 2 + '"'
COMMITTED = "committed on sdlc/0001-x"
CODE_PATHS = ("a.py", "a.ts", "a.kt", "src/env.py", "lib/x.go", "a.rs", "a.java", "a.rb", "a.h")
NON_CODE_PATHS = ("config.yml", ".env", "prod.env", "app.properties", "a.json", ".npmrc", "run.sh",
                  "Dockerfile", "README.md", "notes.txt", "my.cnf", "Makefile", "a.xml", "x.tfvars",
                  ".py", "a.py.bak", "a.PY")


def _gate(monkeypatch, tmp_path, line, name="a.py"):
    """`[(rule, column)]` for ONE staged added line, judged where production judges it.

    `_added_secret_hits` is where the path reaches the filter, so a code exemption is exercised with a
    code path. Its one-argument call is a shape the unchanged code already has, so on that code the
    answer is a hit and the first failure is an assertion, never a TypeError."""
    monkeypatch.setattr(tw.work, "_staged_added_rows", tw._fake_rows({name: [(1, line)]}))
    found = tw.work._added_secret_hits(tmp_path)
    return [(rule, column) for rule, _line, column in found.get(name, [])]


def _refused(monkeypatch, tmp_path, line, name="a.py"):
    return any(rule == RULE for rule, _column in _gate(monkeypatch, tmp_path, line, name))


def _sha(line):
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def _cfg(entries):
    return {"work": {"enabled": True, "allow_secret_content": entries}}


def _commit(monkeypatch, tmp_path, cfg, rows):
    """`work.commit` over fake staged rows `{path: [(line, text)]}`; returns (result, runner)."""
    d = tw._sdlc(tmp_path) if not (tmp_path / ".sdlc").exists() else str(tmp_path / ".sdlc")
    goal = tw._started(d)
    monkeypatch.setattr(tw.work, "_staged_added_rows", tw._fake_rows(rows))
    run = tw._staged(list(rows))
    return tw.work.commit(d, cfg, goal, run=run, message="test: gate"), run


def _norm(text):
    return " ".join(text.split())


# --- the value filter (scrub.py), judged through work.py's own entry -------------------------------

def test_the_four_issue_rows_pass_the_gate_and_scrub_still_redacts_them(monkeypatch, tmp_path):
    for row in (R1, R2, R3, R4):
        assert _gate(monkeypatch, tmp_path, row) == []
    # scrub() is untouched: the redactor output measured on 5f25b99, rebuilt around the fragment key
    assert S.scrub(R1) == "const " + K + ": [REDACTED]"
    assert S.scrub(R2) == K + ": [REDACTED]"
    assert S.scrub(R3) == "def guard(" + K + ": [REDACTED] -> bool:"
    assert S.scrub(R4) == '"' + K + ": [REDACTED]"


def test_the_quoted_literal_row_is_refused_once_by_credential_assignment(monkeypatch, tmp_path):
    """TD-1: both same-named rules match a quoted literal at column 1; the gate reports it once."""
    assert _gate(monkeypatch, tmp_path, R5) == [(RULE, 1)]


def test_quoted_values_stay_refused_whatever_they_hold(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, K + " = uiToken()") == []
    for value in ('"uiToken()"', '"True"', '"bool"', '"[REDACTED]"', '"ab12cd"',
                  "'" + "hunter2" * 2 + "'", '"********"'):
        assert _refused(monkeypatch, tmp_path, K + " = " + value), value


def test_expression_shaped_values_with_a_provider_prefix_stay_refused(monkeypatch, tmp_path):
    """The run after each prefix is letters only, so only the prefix clause refuses it."""
    assert _gate(monkeypatch, tmp_path, K + " = settings.x") == []
    for prefix in ("ghp_", "github_pat_", "sk_live_", "AKIA", "eyJ", "xox"):
        for tail in (".x", "(", "[0]"):
            assert _refused(monkeypatch, tmp_path, K + " = " + prefix + "abcdefgh" + tail), (prefix, tail)


def test_unquoted_high_entropy_values_stay_refused_bare_or_dotted(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, K + " = cfg[0]") == []
    for line in (K + " = " + MIX + "abc", K + " = " + "Ab1" * 8 + ".b.c", K + ": " + WEAK,
                 K + "=" + WEAK + "."):
        assert _refused(monkeypatch, tmp_path, line)


def test_every_segment_of_a_dotted_value_is_judged_not_only_its_head(monkeypatch, tmp_path):
    """Each later segment is secret-like by exactly one clause; the last value has no secret-like
    segment at all, so only the whole-run expression match refuses it."""
    assert _gate(monkeypatch, tmp_path, K + " = cfg.section.name") == []
    for value in ("SG." + MIX + ".x", "hvs." + L30, "sk.eyJabcdefgh.x", "dp.st.dev." + L30,
                  "cfg." + UPS + ".x", "SG." + LD + ".x", "Pa.ss@word!"):
        assert _refused(monkeypatch, tmp_path, K + " = " + value), value


def test_a_secret_like_literal_anywhere_after_the_separator_keeps_the_hit(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, K + ' = os.environ.get("APP_CRED_NAME", "")') == []
    assert _gate(monkeypatch, tmp_path, K + " = data.get('" + K + "')") == []
    for line in (K + ' = os.getenv("X", "' + MIX + '")',
                 K + " = SecretStr('" + MIX + "')",
                 K + ' = SecretStr("' + L30 + '")',
                 K + ' = base64.b64decode("abc")',
                 K + ' = os.getenv("X", "' + WEAK + '")',
                 "const " + K + " = process.env.X || `" + WEAK + "`;",
                 "const " + K + ' = null || "' + WEAK + '";',
                 K + " = cfg.get(x) or " + MIX):
        assert _refused(monkeypatch, tmp_path, line)


def test_bare_identifiers_and_numbers_stay_refused(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, '"' + K + '": None,') == []
    for line in (K + " = myToken", "const " + K + " = await fetchIt();", K + " = 12345678",
                 K + ": Summer"):
        assert _refused(monkeypatch, tmp_path, line)


def test_type_names_pass_only_after_a_colon_and_only_from_the_closed_set(monkeypatch, tmp_path):
    for line in (K + ": bool", K + ": string;", K + ": string|null;", K + ": SecretStr"):
        assert _gate(monkeypatch, tmp_path, line) == []
    for line in (K + " = string", K + ": Strong", K + ": stringly", K + ": str=None",
                 K + ': str="' + WORD + '"'):
        assert _refused(monkeypatch, tmp_path, line)


def test_booleans_and_null_words_pass_only_as_the_whole_value(monkeypatch, tmp_path):
    for line in (K + " = true;", K + ": null,", K + " = undefined", "def f(self, " + K + "=None):"):
        assert _gate(monkeypatch, tmp_path, line) == []
    for line in (K + " = Trueish", K + " = None-x", K + "=True,abcdef"):
        assert _refused(monkeypatch, tmp_path, line)


def test_sigma_redaction_markers_pass_only_unquoted_and_exact(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, K + ": [REDACTED]") == []
    assert _gate(monkeypatch, tmp_path, K + " = [REDACTED:gh-token],") == []
    for line in (K + ' = "[REDACTED]"', K + " = [REDACTED]x", K + " = [NOT-REDACTED]"):
        assert _refused(monkeypatch, tmp_path, line)


def test_the_filter_leaves_the_rule_table_and_other_rules_unchanged(monkeypatch, tmp_path):
    assert _gate(monkeypatch, tmp_path, K + ' = os.getenv("X")') == []
    table = json.dumps([[name, rx.pattern, rx.flags, repl if isinstance(repl, str) else None]
                        for name, rx, repl in S._SECRET_PATTERN_SPECS])
    assert hashlib.sha256(table.encode("utf-8")).hexdigest() == (
        "9463920c6ccf0f154bcb70b5213c1b379b10282ba4f37d97223bec44822c0006")
    pair = [rx for name, rx in S.COMMIT_SHAPE_RULES if name == RULE]
    assert len(pair) == 2
    assert sorted(m.start(1) == m.start() for m in (rx.search(R5) for rx in pair)) == [False, True]
    line = K + " = cfg.get(" + tw._key() + ")"
    assert "aws-key" in {rule for rule, _column in _gate(monkeypatch, tmp_path, line)}
    quoted = [rx for name, rx in S.SHAPE_RULES if name == RULE]
    assert len(quoted) == 1 and quoted[0].search(R5)
    assert _gate(monkeypatch, tmp_path, R5) == [(RULE, 1)]


# --- the commit gesture and the operator's allowlist (work.py) ------------------------------------

def test_documented_commit_lands_the_four_rows_and_refuses_the_literal_in_real_git(tmp_path):
    repo, d, committed = tw._real_repo(tmp_path)
    (repo / "rows.py").write_text("\n".join((R1, R2, R3, R4)) + "\n")
    assert tw.work.commit(d, tw.ON, "0001-x.md", message="test: rows") == COMMITTED
    assert committed() == {"rows.py"}
    (repo / "rows.py").write_text("\n".join((R1, R2, R3, R4, R5)) + "\n")
    out = tw.work.commit(d, tw.ON, "0001-x.md", message="test: literal")
    assert out.startswith("REFUSED")
    assert out.count(RULE + " at rows.py:5:1") == 1
    assert "hunter2" not in out
    # The path reaches the filter through git's own raw names: the same dotted line is a code
    # expression in `rows.py` and a literal in `config.yml`.
    repo, d, _committed = tw._real_repo(tmp_path / "second")
    line = PW + ": " + DOT
    (repo / "rows.py").write_text(line + "\n")
    assert tw.work.commit(d, tw.ON, "0001-x.md", message="test: dotted code") == COMMITTED
    (repo / "config.yml").write_text(line + "\n")
    out = tw.work.commit(d, tw.ON, "0001-x.md", message="test: dotted config")
    assert out.startswith("REFUSED") and RULE + " at config.yml:1:1" in out


def test_a_path_entry_clears_credential_assignment_only_in_that_exact_path(monkeypatch, tmp_path):
    cfg = _cfg([{"rule": RULE, "path": "a.py", "reason": "fixture rows"}])
    line = K + " = myToken"
    out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, line)]})
    assert out == COMMITTED
    for name in ("b.py", "./a.py", "A.py"):
        out, _run = _commit(monkeypatch, tmp_path, cfg, {name: [(1, line)]})
        assert out.startswith("REFUSED") and RULE + " at " + name + ":1:1" in out, name
    out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, "x = " + tw._key())]})
    assert out.startswith("REFUSED") and "aws-key at a.py:1:" in out


def test_a_fully_allowlisted_commit_makes_the_same_calls_as_a_clean_one(monkeypatch, tmp_path):
    """BR-20: an allowlisted hit is gone BEFORE the ordinary-path early return, so the commit issues
    exactly the three calls a clean one does."""
    cfg = _cfg([{"rule": RULE, "path": "a.py", "reason": "fixture rows"}])
    out, run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, K + " = myToken")]})
    assert out == COMMITTED
    assert run.calls == ["git add -A", "git diff --cached --name-only", "git commit -m test: gate"]


def test_a_line_hash_entry_clears_only_that_line_and_never_a_provider_rule(monkeypatch, tmp_path):
    first, second = K + " = myToken", PW + " = otherName"
    cfg = _cfg([{"rule": RULE, "line_sha256": _sha(first), "reason": "a name, not a value"}])
    out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, first)]})
    assert out == COMMITTED
    out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, first), (2, second)]})
    assert out.startswith("REFUSED") and RULE + " at a.py:2:1" in out and "a.py:1:" not in out
    keyed = K + " = " + tw._key()
    cfg = _cfg([{"rule": RULE, "line_sha256": _sha(keyed), "reason": "a name, not a value"}])
    out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, keyed)]})
    assert out.startswith("REFUSED") and "aws-key at a.py:1:" in out and RULE + " at a.py" not in out


def test_the_content_allowlist_accepts_only_the_credential_assignment_rule(monkeypatch, tmp_path):
    line = K + " = myToken"
    out, _run = _commit(monkeypatch, tmp_path, _cfg([{"rule": RULE, "path": "a.py", "reason": "r"}]),
                        {"a.py": [(1, line)]})
    assert out == COMMITTED
    for entry in ({"rule": "aws-key", "path": "a.py", "reason": "r"},
                  {"rule": "*", "path": "a.py", "reason": "r"},
                  {"rule": "credential-*", "path": "a.py", "reason": "r"},
                  {"path": "a.py", "reason": "r"}):
        out, _run = _commit(monkeypatch, tmp_path, _cfg([entry]), {"a.py": [(1, line)]})
        assert out.startswith("REFUSED"), entry


def test_a_malformed_content_allowlist_never_crashes_and_never_allows(monkeypatch, tmp_path):
    line = K + " = myToken"
    out, _run = _commit(monkeypatch, tmp_path, _cfg([{"rule": RULE, "path": "a.py", "reason": "r"}]),
                        {"a.py": [(1, line)]})
    assert out == COMMITTED
    configs = [_cfg("a.py"), _cfg({"a.py": True}), _cfg([None, 7]),
               _cfg([{"rule": RULE, "path": "a.py"}]),
               _cfg([{"rule": RULE, "path": "a.py", "reason": ""}]),
               _cfg([{"rule": RULE, "path": "a.py", "line_sha256": _sha(line), "reason": "r"}]),
               _cfg([{"rule": RULE, "reason": "r"}]),
               _cfg([{"rule": RULE, "line_sha256": _sha(line)[:40], "reason": "r"}]),
               _cfg([{"rule": RULE, "line_sha256": _sha(line).upper(), "reason": "r"}]),
               {"work": True}, {}]
    for cfg in configs:
        out, _run = _commit(monkeypatch, tmp_path, cfg, {"a.py": [(1, line)]})
        assert out.startswith("REFUSED"), cfg
    out, _run = _commit(monkeypatch, tmp_path, _cfg([{"rule": RULE, "path": "a.py"}]),
                        {"a.py": [(1, line)]})
    assert "ignored" in out


def test_the_content_refusal_puts_remove_or_park_before_the_operator_allowlist_and_no_hash(
        monkeypatch, tmp_path):
    out, _run = _commit(monkeypatch, tmp_path, tw.ON, {"a.py": [(1, K + " = myToken")]})
    assert "work.allow_secret_content" in out
    for needle in (RULE, '"path"', "line_sha256", "line-hash", "record parked"):
        assert needle in out, needle
    worktree = str(tmp_path / ".sdlc" / "work" / "0001-x")
    assert shlex.quote(os.path.join(worktree, "a.py")) in out
    assert (out.index("Remove the literal") < out.index("record parked") < out.index("OPERATOR ruling")
            < out.index("line_sha256") < out.index('"path"'))
    assert "an agent never writes one itself" in out
    assert "AND future" in out
    assert "by hand" not in out and "preferred" not in out
    assert re.search(r"[0-9a-f]{64}", out) is None
    out, _run = _commit(monkeypatch, tmp_path, tw.ON, {"a.py": [(1, "x = " + tw._key())]})
    assert "record parked" in out and "line_sha256" not in out


def test_line_hash_prints_the_gate_hash_without_echoing_the_line(monkeypatch, tmp_path):
    for name in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID", "SIGMA_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    line = "  " + K + " = myToken  "
    f = tmp_path / "rows.py"
    f.write_bytes(("first\n" + line + "\nthird\n").encode("utf-8"))

    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPTS / "work.py"), "line-hash", *args],
                              cwd=str(tmp_path), capture_output=True)

    proc = cli(str(f), "2")
    assert proc.returncode == 0
    assert proc.stdout == (_sha(line) + "\n").encode("ascii")
    assert b"myToken" not in proc.stdout + proc.stderr
    crlf = tmp_path / "crlf.py"
    crlf.write_bytes(("first\r\n" + line + "\r\nthird\r\n").encode("utf-8"))
    assert cli(str(crlf), "2").stdout == proc.stdout
    for args in ((str(f), "0"), (str(f), "9"), (str(f), "x"), (str(tmp_path / "missing.py"), "1")):
        bad = cli(*args)
        assert bad.returncode == 2 and bad.stdout == b"", args


def test_line_hash_round_trips_into_an_allowlist_entry_that_clears_the_gate(monkeypatch, tmp_path):
    line = K + " = myToken"
    expected = _sha(line)
    out, _run = _commit(monkeypatch, tmp_path, _cfg([{"rule": RULE, "line_sha256": expected,
                                                      "reason": "a name, not a value"}]),
                        {"a.py": [(2, line)]})
    assert out == COMMITTED
    f = tmp_path / "a.py"
    f.write_text("x = 1\n" + line + "\n")
    printed = tw.work.line_hash(str(f), 2)
    assert printed == expected
    out, _run = _commit(monkeypatch, tmp_path, _cfg([{"rule": RULE, "line_sha256": printed,
                                                      "reason": "a name, not a value"}]),
                        {"a.py": [(2, line)]})
    assert out == COMMITTED


def test_template_registry_and_loop_docs_make_the_content_allowlist_an_operator_ruling():
    template_text = (ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text()
    template = json.loads(template_text)
    assert template["work"].get("allow_secret_content") == []
    comment = _norm(template["work"]["_allow_secret_content"])
    for needle in (RULE, "line-hash", "OPERATOR ruling", "docs/threat-model.md"):
        assert needle in comment, needle
    assert "content scanning is deliberately NOT done" not in _norm(template_text)
    row = next(r for r in tw.work.ENFORCEMENT_GATES if r["function"] == "_secret_refusal")
    assert "work.allow_secret_content" in row["settings"]
    assert "code file" in row["mechanism"] and "operator" in row["mechanism"]
    landing = (SCRIPTS.parent / "references" / "landing.md").read_text()
    paragraph = _norm(next(p for p in landing.split("\n\n") if "work.allow_secret_content" in p))
    assert "docs/threat-model.md" in paragraph and "entry yourself" in paragraph
    assert (paragraph.index("Remove the literal") < paragraph.index("record parked")
            < paragraph.index("OPERATOR ruling"))
    model = (ROOT / "docs" / "threat-model.md").read_text()
    section = _norm(next(s for s in model.split("\n## ") if "work.py commit" in s.splitlines()[0]))
    for needle in ("work.allow_secret_content", "line_sha256", "line-hash", '"path"', "OPERATOR ruling",
                   "never writes one", "code file", "COMMIT_CODE_SUFFIXES", "MAIN checkout"):
        assert needle in section, needle
    assert "do exactly what the refusal prints" in _norm((SCRIPTS.parent / "SKILL.md").read_text())
    unreleased = (ROOT / "CHANGELOG.md").read_text().split("## Unreleased", 1)[1].split("\n## ", 1)[0]
    assert "work.allow_secret_content" in unreleased


# --- the assigned-literal guard, the code-file rule and linear time --------------------------------

def test_a_value_followed_by_an_assigned_quoted_literal_stays_refused(monkeypatch, tmp_path):
    """The WORD is letters only, so the rest-of-line scan passes every negative below and only the
    assigned-literal guard refuses them. The twins pin the guard's comparison exclusions."""
    for line in (PW + ": Optional[str] = None", PW + ": Optional[str] = (None)",
                 K + ' = cfg.x == "' + WORD + '"', K + ' = cfg.x != "' + WORD + '"',
                 K + ' = cfg.x <= "' + WORD + '"', K + ' = cfg.x >= "' + WORD + '"'):
        assert _gate(monkeypatch, tmp_path, line) == []
    q, t3 = '"' + WORD + '"', '"""' + WORD + '"""'
    for line, name in (("const " + PW + ": string = " + q + ";", "a.ts"),
                       ("val " + PW + ": String = " + q, "a.kt"),
                       (PW + ": Optional[str] = " + q, "a.py"),
                       (SC + ": bytes = b" + q, "a.py"),
                       (PW + ": Optional[str]=" + q, "a.py"),
                       (K + " = cfg.x = " + q, "a.py"),
                       (K + " = client.get(name=" + q + ")", "a.py"),
                       (SC + ": bytes = b" + t3, "a.py"),
                       (PW + ": Optional[str] = '''" + WORD + "'''", "a.py"),
                       ("const " + PW + ": string = (" + q + ");", "a.ts"),
                       (K + " = cfg.x = (" + q + ")", "a.py"),
                       (PW + ": Optional[str] = (", "a.py"),
                       (PW + ": Optional[str] = " + chr(92), "a.py"),
                       ("const " + PW + ': string = "ab" + ' + q + ";", "a.ts"),
                       ("const " + PW + ": string | undefined = " + q + ";", "a.ts"),
                       (SC + ": bytes | None = b" + q, "a.py"),
                       (PW + ": Annotated[str, Field()] = " + q, "a.py"),
                       (PW + ": Optional[str]=(" + q + ")", "a.py"),
                       (PW + ": Optional[str]=b" + q, "a.py"),
                       (SC + ": bytes | None = b" + t3, "a.py"),
                       (PW + ": Optional[str] | None = (", "a.py")):
        assert _refused(monkeypatch, tmp_path, line, name), (name, len(line))


def test_only_code_files_get_the_expression_and_type_exemptions(monkeypatch, tmp_path):
    line = PW + ": " + DOT
    assert _gate(monkeypatch, tmp_path, line, "a.py") == []
    for name in CODE_PATHS:
        assert _gate(monkeypatch, tmp_path, line, name) == [], name
    for name in NON_CODE_PATHS:
        assert _refused(monkeypatch, tmp_path, line, name), name
    for name in ("config.yml", "README.md"):
        assert _refused(monkeypatch, tmp_path, R1, name), name
    assert _refused(monkeypatch, tmp_path, K + ": string", "config.yml")
    for name in ("a.json", "config.yml", "README.md"):
        assert _gate(monkeypatch, tmp_path, R4, name) == [], name
    for value in ("false", "null", "[REDACTED]"):
        assert _gate(monkeypatch, tmp_path, K + ": " + value, "config.yml") == [], value
    # The scrub API: a missing path is not a code path (fail closed); a path matches the gate.
    assert S.commit_secret_hits(line) != []
    assert S.commit_secret_hits(R4) == []
    pairs = ([(line, n) for n in CODE_PATHS + NON_CODE_PATHS] + [(R1, "config.yml"), (R1, "README.md"),
             (K + ": string", "config.yml")] + [(R4, n) for n in ("a.json", "config.yml", "README.md")])
    for text, name in pairs:
        assert S.commit_secret_hits(text, path=name) == _gate(monkeypatch, tmp_path, text, name), name
    for name in CODE_PATHS:
        assert S.commit_code_path(name) is True, name
    for name in NON_CODE_PATHS + ("", None):
        assert S.commit_code_path(name) is False, name
    assert isinstance(S.COMMIT_CODE_SUFFIXES, frozenset)


def _scan(line):
    """The old gate's own cost, unchanged by #961: every rule-table pattern's finditer over the line."""
    return [m.start() for _name, rx in S.COMMIT_SHAPE_RULES for m in rx.finditer(line)]


def test_a_pathological_value_run_is_judged_in_linear_time(monkeypatch, tmp_path):
    """Relative, not absolute: both timings run back to back in one process on one line, so a loaded
    machine or `pytest -n auto` stretches them together. A quadratic form measured 590x to 1,020x."""
    assert _gate(monkeypatch, tmp_path, K + " = f(x))") == []
    n = 32768
    for line in (K + " = a(" + ")" * n + "@", K + ": string" + "|string" * (n // 7) + "@",
                 K + " = a." + "b." * (n // 2) + "@"):
        assert _refused(monkeypatch, tmp_path, line)
        scan_best = gate_best = float("inf")
        for _attempt in range(3):
            started = time.perf_counter()
            _scan(line)
            scan_best = min(scan_best, time.perf_counter() - started)
            started = time.perf_counter()
            _gate(monkeypatch, tmp_path, line)
            gate_best = min(gate_best, time.perf_counter() - started)
            if gate_best < 40 * scan_best:
                break
        assert gate_best < 40 * scan_best, (gate_best, scan_best)
