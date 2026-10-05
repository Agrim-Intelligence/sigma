"""risk-detect.sh (Slice 3): the read-only, jq-free collector that names which conditional-risk
categories the current change touches, so the loop's Research/Review phases can surface the matching
/sdlc-<risk>-check. Guards its three principles: correct detection, FAIL-OPEN, and SECRET-SAFETY (it
scans diff bodies but must emit only {category,file,line,pattern_id} — never the matched value)."""
import json, os, re, subprocess, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "risk-detect.sh"


# #2751 / sigma#145 / sigma#244: `test_detects_each_category` and the untracked header-leak test went
# red intermittently (6/40 and 5/30 stressed `-n 8` whole-file runs on macOS, sigma#244) with the
# CONTENT scan empty while the NAME scan was intact. Root cause (sigma#244): Homebrew bash 5.x links
# libintl, whose setlocale() calls into CoreFoundation, which is not fork-safe. The per-command
# `LC_ALL=C grep` inside the process-substitution subshell made that forked, never-exec'd bash call
# setlocale() twice per untracked file (set, then restore in dispose_temporary_env); under load it
# SIGSEGV'd (26 macOS crash reports, one stack) and, the script being FAIL-OPEN, the dead subshell
# read as "nothing detected". The fix pins the locale ONCE in the unforked main shell. The
# deterministic control is `test_locale_is_pinned_once_and_never_changed_in_a_forked_shell` below;
# the git-backed category tests stay integration tests and are NOT the system of record for this
# race (they only catch it probabilistically). The helpers below still name the failing layer on a
# red: the fixture (`_git`, `_assert_untracked`), the script (`_Out.why`, which now carries the
# script's own "content scan incomplete" stderr line), or the classifier (`_classify`, no git).


def _git(repo, *args):
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert p.returncode == 0, "fixture setup failed: git %s -> rc=%d\nstdout=%r\nstderr=%r" % (
        " ".join(args), p.returncode, p.stdout, p.stderr)
    return p.stdout


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    assert _git(tmp_path, "rev-parse", "--is-inside-work-tree").strip() == "true"
    return tmp_path


def _assert_untracked(repo, *paths):
    """Per-fixture precondition: every planted path is visible to the same `ls-files --others
    --exclude-standard` the script walks, so a red on the assertion below it is the script's, not a
    global gitignore's (the #537 note further down records that hazard)."""
    listed = [x for x in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if x]
    # rc-tolerant on purpose: `--get-all` exits 1 on a host with no global excludes configured.
    excl = subprocess.run(["git", "-C", str(repo), "config", "--show-origin", "--get-all", "core.excludesFile"],
                          capture_output=True, text=True)
    for path in paths:
        assert path in listed, "fixture path %r not listed as untracked; listing=%r; core.excludesFile -> rc=%d %r" % (
            path, listed, excl.returncode, excl.stdout)


class _Out(dict):
    """The parsed JSON, plus `.why`: rc, stdout, stderr, and the fixture's own git view, captured
    at run time. The script is FAIL-OPEN (every git call 2>/dev/null, always exit 0), so 'git
    failed' and 'nothing detected' both look like `matched == []`; `.why` is the only record of
    which one happened (#2751 / sigma#145)."""
    why = ""


def _run(project_dir):
    p = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    views = []
    for args in (("status", "--porcelain=v1", "--untracked-files=all"),
                 ("ls-files", "--others", "--exclude-standard")):
        # un-asserted: the non-git test expects these to fail, and they are evidence, not a gate
        g = subprocess.run(["git", "-C", str(project_dir), *args], capture_output=True, text=True)
        views.append("git %s -> rc=%d stdout=%r stderr=%r" % (" ".join(args), g.returncode, g.stdout, g.stderr))
    why = "script rc=%d\nstdout=%r\nstderr=%r\n%s" % (p.returncode, p.stdout, p.stderr, "\n".join(views))
    assert p.returncode == 0, why
    try:
        out = _Out(json.loads(p.stdout))
    except ValueError as exc:
        raise AssertionError("script stdout is not JSON (%s)\n%s" % (exc, why))
    out.why = why
    return out


# --- sigma#244: the forked-shell locale crash, pinned deterministically ----------------------------

_LOCALE_ASSIGN = re.compile(r"^(\++) (export )?(LC_[A-Z]+|LANG|LANGUAGE)=")


def _header_leak_fixture(tmp_path):
    # the untracked fixture whose ONLY sensitive signal comes from the content scan (the name
    # "notes.txt" matches no glob), so a dead content-scan subshell is visible as a missing category
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text('++ aws_key = "AK' 'IALEAK0000000000FAKE"\napi_key = "TR' 'IGGER9SECRETVALUE"\n')
    _assert_untracked(repo, "notes.txt")
    return repo


def test_locale_is_pinned_once_and_never_changed_in_a_forked_shell(tmp_path):
    """Deterministic seam control for sigma#244 (the probabilistic reproduction lives outside the
    suite: a stressed `-n 8` loop, 11/70 red before the fix). Any locale-variable assignment that bash
    executes in a forked, not-exec'd process -- a per-command `LC_ALL=C cmd` in a process
    substitution, command substitution or pipeline -- calls setlocale() there, which on macOS with a
    libintl-linked bash reaches CoreFoundation after fork and can SIGSEGV. xtrace prints every such
    assignment as its own line, at `+` depth > 1 inside a subshell, so this fails on ANY spelling of
    one (LC_ALL/LC_CTYPE/LANG, temporary or exported), on every run, on every OS."""
    repo = _header_leak_fixture(tmp_path)
    p = subprocess.run(["bash", "-x", str(SCRIPT)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)})
    assert p.returncode == 0, p.stderr
    trace = p.stderr.splitlines()
    locale = [(i, l) for i, l in enumerate(trace) if _LOCALE_ASSIGN.match(l)]
    # exactly one pin: the unforked main shell's own `export LC_ALL=C` (which bash traces as two
    # lines, the builtin and its assignment), all at depth 1 and before the first forked line
    assert [l for _, l in locale] == ["+ export LC_ALL=C", "+ LC_ALL=C"], locale
    first_fork = next(i for i, l in enumerate(trace) if l.startswith("++"))
    assert all(i < first_fork for i, _ in locale), (locale, trace[first_fork])
    # non-vacuity: the trace reached the exact seam the crash reports name (the untracked-file loop,
    # inside the content-scan process substitution) and the content hit landed
    assert any(l.startswith("++") and "grep -Iq" in l for l in trace), "untracked loop never traced"
    assert "sensitive" in json.loads(p.stdout)["matched"], p.stdout


def test_a_dead_content_scan_is_reported_not_silent(tmp_path):
    """sigma#244: a content-scan subshell that dies mid-stream used to be indistinguishable from
    "nothing detected". The script stays FAIL-OPEN (exit 0, valid JSON) but must now say so on
    stderr. The mutant kills that subshell exactly where the observed SIGSEGV did."""
    repo = _header_leak_fixture(tmp_path)
    text = SCRIPT.read_text(encoding="utf-8")
    anchor = '    [ -f "$PROJECT_DIR/$f" ] || continue\n'
    assert text.count(anchor) == 1, "the untracked-loop anchor moved; retarget this mutant"
    mutant = tmp_path / "mutant.sh"
    mutant.write_text(text.replace(anchor, "    sh -c 'kill -KILL $PPID'\n" + anchor), encoding="utf-8")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    dead = subprocess.run(["bash", str(mutant)], capture_output=True, text=True, env=env)
    assert dead.returncode == 0 and "sensitive" not in json.loads(dead.stdout)["matched"], \
        "the mutant did not reproduce the silent-empty symptom: %r" % (dead.stdout,)
    assert "content scan incomplete" in dead.stderr, dead.stderr
    live = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env)
    assert live.stderr == "" and "sensitive" in json.loads(live.stdout)["matched"], (live.stdout, live.stderr)


def test_schema_and_empty_on_clean_repo(tmp_path):
    out = _run(_repo(tmp_path))
    assert out["schema"] == "risk-detect/v1" and out["matched"] == [] and out["hits"] == [], out.why


def test_detects_each_category(tmp_path):
    repo = _repo(tmp_path)
    (repo / "db").mkdir(); (repo / "src").mkdir()
    (repo / "db" / "001_migration.sql").write_text("ALTER TABLE users ADD COLUMN age int;\n")
    (repo / "src" / "routes.js").write_text('app.post("/pay", handler)\n')
    (repo / "auth.py").write_text("api_key = configure()\n")
    _assert_untracked(repo, "db/001_migration.sql", "src/routes.js", "auth.py")
    out = _run(repo)
    assert set(out["matched"]) == {"migration", "contract", "sensitive"}, out.why
    # hits are location-only: exactly the four keys, nothing resembling a value
    for h in out["hits"]:
        assert set(h.keys()) == {"category", "file", "line", "pattern_id"}, out.why


def test_matched_is_sorted_and_deduped(tmp_path):
    repo = _repo(tmp_path)
    (repo / "a_migration.sql").write_text("CREATE TABLE t (id int);\n")
    (repo / "b_migration.sql").write_text("DROP TABLE t;\n")   # second migration file
    _assert_untracked(repo, "a_migration.sql", "b_migration.sql")
    out = _run(repo)
    assert out["matched"] == ["migration"], out.why           # deduped, single category


def test_planted_secret_value_never_in_output(tmp_path):
    repo = _repo(tmp_path)
    SECRET = "AK" "IAZZZZ0000EXAMPLE1"
    (repo / "config.env").write_text("AWS_SECRET=%s\npassword: hunter2plaintext\n" % SECRET)
    _assert_untracked(repo, "config.env")
    out = _run(repo)
    assert "sensitive" in out["matched"], out.why
    blob = json.dumps(out)
    assert SECRET not in blob and "hunter2plaintext" not in blob, out.why   # location only, never the value


def test_content_line_that_renders_as_a_diff_header_never_leaks_tracked(tmp_path):
    # a line beginning "++ " renders as "+++ " in the outer unified diff; the scanner must treat it as
    # CONTENT (location-only), never misparse it as a "+++ b/path" header that captures the secret value
    # into `file`. Mirrors alignment-collect.sh's already-fixed test_content_line_that_renders_as_a_diff_
    # header_never_leaks (slice #89) — this was the unfixed twin (risk-detect.sh never got that fix).
    # NOTE the second line must ALSO trip a hard-stop: on the buggy awk, the poisoned `file` is only ever
    # EMITTED when a later line matches a pattern, so an assertion that only plants line 1 would pass
    # vacuously even against the bug (nothing gets emitted at all). Line 2 forces the poisoned `file` to
    # surface, so this test actually fails on the pre-fix code.
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text("placeholder\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "init")
    (repo / "notes.txt").write_text(
        '++ aws_key = "AK' 'IALEAK0000000000FAKE"\napi_key = "TR' 'IGGER9SECRETVALUE"\n'
    )
    assert "notes.txt" in _git(repo, "diff", "--name-only").split()   # the rewrite is a tracked change
    out = _run(repo)
    blob = json.dumps(out)
    assert "AK" "IALEAK0000000000FAKE" not in blob, out.why    # value never in output (fails on buggy awk)
    assert "sensitive" in out["matched"], "the second line must still hard-stop (guard must not be vacuous)\n" + out.why
    for h in out["hits"]:
        assert h["file"] == "notes.txt", out.why            # real filename, never the "+++ …" line text


def test_content_line_that_renders_as_a_diff_header_never_leaks_untracked(tmp_path):
    # same shape, but for an UNTRACKED file — risk-detect.sh synthesizes its own diff for these
    # (emit_combined_diff), a code path alignment-collect.sh (committed-history only) doesn't have; the
    # synthesized block must also carry a "diff --git" line so the same inhunk reset fires.
    repo = _repo(tmp_path)
    (repo / "notes.txt").write_text(
        '++ aws_key = "AK' 'IALEAK0000000000FAKE"\napi_key = "TR' 'IGGER9SECRETVALUE"\n'
    )
    _assert_untracked(repo, "notes.txt")
    out = _run(repo)
    blob = json.dumps(out)
    assert "AK" "IALEAK0000000000FAKE" not in blob, out.why
    assert "sensitive" in out["matched"], "the second line must still hard-stop (guard must not be vacuous)\n" + out.why
    for h in out["hits"]:
        assert h["file"] == "notes.txt", out.why


def test_fail_open_on_non_git_dir(tmp_path):
    out = _run(tmp_path)   # never ran git init
    assert out == {"schema": "risk-detect/v1", "matched": [], "hits": []}, out.why


def test_deterministic(tmp_path):
    repo = _repo(tmp_path)
    (repo / "schema.prisma").write_text("model User { id Int }\n")
    (repo / "openapi.yaml").write_text("paths: {}\n")
    _assert_untracked(repo, "schema.prisma", "openapi.yaml")
    first, second = _run(repo), _run(repo)
    assert first == second, "first run:\n%s\nsecond run:\n%s" % (first.why, second.why)


def test_excludes_sdlc_and_docs(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc").mkdir(); (repo / "docs").mkdir()
    (repo / ".sdlc" / "001_migration.sql").write_text("ALTER TABLE x ADD COLUMN y int;\n")
    (repo / "docs" / "auth.md").write_text("Authorization: Bearer xyz\n")
    # the two files ARE visible to a plain untracked walk, so the empty result below is the script's
    # own pathspec exclusion at work, not a fixture that never landed
    plain = _git(repo, "ls-files", "--others", "-z").split("\0")
    assert {".sdlc/001_migration.sql", "docs/auth.md"} <= set(plain), plain
    out = _run(repo)
    assert out["matched"] == [], out.why   # SDLC machinery + docs are not the engineer's source


ALIGN_SCRIPT = SCRIPT.parent.parent.parent / "sigma-align" / "scripts" / "alignment-collect.sh"


def _secret_kv_pattern(text):
    # the key:value trigger words (api_key=, password:, ...) — the alternation between the
    # opening "line ~ /(" and the closing ")[ \t]*[:=]/" that both scripts share verbatim.
    m = re.search(r"line ~ /\(([^)]+)\)\[ \\t\]\*\[:=\]/", text)
    assert m, "could not locate the secret key:value pattern"
    return m.group(1)


def _secret_token_pattern(text):
    # the token-SHAPE alternation (AKIA.../ghp_.../xox.../glpat-.../AIza.../-----BEGIN...KEY-----)
    m = re.search(r"line ~ /\((AKIA[^)]+)\)/\)", text)
    assert m, "could not locate the secret token-shape pattern"
    return m.group(1)


def test_secret_patterns_stay_in_sync_with_alignment_collect():
    # F29: risk-detect.sh and alignment-collect.sh each hard-stop on a secret-shaped line, but the
    # two pattern sets had drifted (alignment-collect alone caught Slack tokens; both missed GitLab
    # and Google). Both scan diff bodies to CLASSIFY, never to redact, so drift here is a detection
    # gap, not a leak — but the two location-only collectors should carry the same set.
    risk_text = SCRIPT.read_text(encoding="utf-8")
    align_text = ALIGN_SCRIPT.read_text(encoding="utf-8")
    assert _secret_kv_pattern(risk_text) == _secret_kv_pattern(align_text)
    assert _secret_token_pattern(risk_text) == _secret_token_pattern(align_text)
    # and the union actually grew to include the previously-missing shapes, not just stayed equal
    for shape in ("xox[baprs]-", "glpat-", "AIza", "AWS_SECRET_ACCESS_KEY"):
        assert shape in risk_text and shape in align_text


# --- #537: dotenv globs were the only PATH-ANCHORED patterns in any of the three lists ----------
# risk-detect.sh's own convention (the comment above the glob lists) is that every pattern is a
# substring glob matched at any depth. `.env*` broke it: `match_globs` matches the WHOLE path, so
# `.env*` only ever hit a repo-root dotenv and `*.env` only a path ENDING in ".env" — a nested
# `backend/.env.local` produced zero sensitive signal while the identical root file flagged.
#
# Every fixture below is FORCE-staged (`git add -A -f`) on purpose. A contributor whose GLOBAL
# gitignore excludes .env files would otherwise see these tests flake for a reason unrelated to
# what they pin — and a plain `git add -A` does NOT rescue them, because add obeys gitignore just
# as `git ls-files --others --exclude-standard` does. `-f` is what makes each fixture a TRACKED
# file, and tracked files are reached by both of the scanner's paths whatever the ignore rules say.

def test_nested_dotenv_signals_as_a_sensitive_path(tmp_path):
    # Asserts the NAME-scan hit specifically. "sensitive" in matched would be vacuous here: any
    # listed keyword in the fixture body makes it pass through the CONTENT scan even on the
    # unfixed script, so the fixture is deliberately keyword-free and the pattern_id is pinned.
    repo = _repo(tmp_path)
    (repo / "backend").mkdir()
    (repo / "backend" / ".env.local").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    assert any(h["file"] == "backend/.env.local" and h["pattern_id"] == "path" for h in out["hits"]), out.why


def test_root_dotenv_still_signals_as_a_sensitive_path(tmp_path):
    # the half that already worked — pinned so the fix is proven to ADD depth, not move the anchor
    repo = _repo(tmp_path)
    (repo / ".env").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    assert any(h["file"] == ".env" and h["pattern_id"] == "path" for h in out["hits"]), out.why


def test_nested_dotenv_example_signals_as_a_sensitive_path(tmp_path):
    # a checked-in template lives beside the real thing and is worth the same surfacing
    repo = _repo(tmp_path)
    (repo / "config").mkdir()
    (repo / "config" / ".env.example").write_text("PLACEHOLDER=1\n")
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    assert any(h["file"] == "config/.env.example" and h["pattern_id"] == "path" for h in out["hits"]), out.why


def test_dotenv_lookalikes_do_not_signal(tmp_path):
    # the over-match guard: widening to any depth must not start flagging files that merely have
    # "env" in the name.
    repo = _repo(tmp_path)
    (repo / "backend").mkdir()
    (repo / "backend" / "env.md").write_text("how to set up your environment\n")
    (repo / "foo.envrc").write_text("layout python\n")
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    assert out["matched"] == [], "%r\n%s" % (out["hits"], out.why)


# --- #537: content-scan keyword gaps (connection strings + one key shape) -----------------------

def test_connection_string_keys_are_sensitive_content(tmp_path):
    repo = _repo(tmp_path)
    (repo / "settings.py").write_text('DATABASE_URL = "postgres://u@h/db"\n')
    (repo / "cache.py").write_text('REDIS_URL="redis://h:6379/0"\n')
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    files = {h["file"] for h in out["hits"] if h["category"] == "sensitive"}
    assert {"settings.py", "cache.py"} <= files, "%r\n%s" % (out["hits"], out.why)


def test_stripe_live_key_shape_is_a_sensitive_token(tmp_path):
    # pattern_id is pinned to "token", so the fixture carries NO key:value trigger word — the kv
    # branch wins the else-if chain and would report "secret" instead.
    repo = _repo(tmp_path)
    (repo / "pay.js").write_text('const K = "sk_live_abcd1234efgh";\n')
    # ...and the converse, pinned: a line carrying BOTH a kv trigger and the key shape reports
    # "secret", not "token", because the kv branch comes first in that else-if chain. Reordering
    # the branches would silently reclassify every such line; this makes it a test failure.
    (repo / "cfg.rb").write_text('api_key = "sk' '_live_abcd1234efgh"\n')
    _git(repo, "add", "-A", "-f")
    out = _run(repo)
    assert any(h["file"] == "pay.js" and h["pattern_id"] == "token" for h in out["hits"]), out.why
    assert any(h["file"] == "cfg.rb" and h["pattern_id"] == "secret" for h in out["hits"]), out.why


# --- #2751: the awk classifier, driven directly (no git, no production seam) -------------------
# The deterministic sibling of the git-backed category and header-leak tests above. Those stay
# integration tests (their only observed red is unexplained, sigma#145); this pair proves the
# classifier itself on every run with a synthesized diff in the exact shape `emit_combined_diff`
# emits for an untracked file, so a red here is the awk's and nowhere else's.

def _classifier_awk():
    text = SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"emit_combined_diff \| awk '(.*?)'\n\)", text, re.S)
    assert m and "inhunk" in m.group(1) and "function emit" in m.group(1), "could not locate the classifier awk"
    return m.group(1)


def _classify(diff_text, program=None):
    p = subprocess.run(["awk", program or _classifier_awk()], input=diff_text,
                       capture_output=True, text=True)
    assert p.returncode == 0, "awk rc=%d stderr=%r" % (p.returncode, p.stderr)
    return [json.loads(l.split("\t", 1)[1]) for l in p.stdout.splitlines() if l]


def _untracked_block(path, *lines):
    # byte-for-byte the header emit_combined_diff prints for an untracked file, then the "+" body
    return "diff --git a/%s b/%s\n+++ b/%s\n@@ -0,0 +1 @@\n" % (path, path, path) + "".join("+%s\n" % l for l in lines)


def test_classifier_categories_without_git():
    diff = (_untracked_block("db/001_migration.sql", "ALTER TABLE users ADD COLUMN age int;")
            + _untracked_block("src/routes.js", 'app.post("/pay", handler)')
            + _untracked_block("cfg.py", "placeholder", "api_key = configure()",
                               'K = "sk_live_abcd1234efgh"', "Authorization: Bearer xyz", "ssn = 1"))
    hits = [(h["category"], h["file"], h["line"], h["pattern_id"]) for h in _classify(diff)]
    assert hits == [
        ("migration", "db/001_migration.sql", 1, "ddl"),
        ("contract", "src/routes.js", 1, "route"),
        ("sensitive", "cfg.py", 2, "secret"),
        ("sensitive", "cfg.py", 3, "token"),
        ("sensitive", "cfg.py", 4, "auth"),
        ("sensitive", "cfg.py", 5, "pii"),
    ], hits


def test_classifier_header_shaped_content_never_becomes_file_without_git():
    fixture = _untracked_block("notes.txt", '++ aws_key = "AK' 'IALEAK0000000000FAKE"',
                               'api_key = "TR' 'IGGER9SECRETVALUE"')
    hits = _classify(fixture)
    blob = json.dumps(hits)
    assert "AK" "IALEAK0000000000FAKE" not in blob, hits
    assert hits, "no hit at all -- the second line must hard-stop, or the guard is untested"
    assert all(h["file"] == "notes.txt" for h in hits), hits
    # Built-in non-vacuity: the fixture must be able to SEE the guard's absence, every run, not only
    # on the day a defect was planted by hand. `str.replace` fails silently, so count first.
    prog = _classifier_awk()
    guard = "!inhunk && /^\\+\\+\\+ /"
    assert prog.count(guard) == 1, "the in-hunk header guard moved; retarget this mutant"
    mutant = prog.replace(guard, "/^\\+\\+\\+ /")
    assert "AK" "IALEAK0000000000FAKE" in json.dumps(_classify(fixture, mutant)), \
        "the mutant did not leak, so this fixture cannot detect the guard's absence"


def test_review_and_research_skills_wire_the_detector():
    # the collector is only useful if the phases invoke it — guard the SKILL prose reference
    skills = SCRIPT.parent.parent.parent
    review = (skills / "sigma-review" / "SKILL.md").read_text(encoding="utf-8")
    research = (skills / "sigma-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "risk-detect.sh" in review, "sigma-review must invoke the risk detector"
    for skill_ref in ("/sigma-security-review", "/sigma-contract-check", "/sigma-migration-check"):
        assert skill_ref in review, "sigma-review must surface %s" % skill_ref
    # research anticipates the same risks so the plan budgets for them
    assert "risk-detect" in research and "/sigma-security-review" in research
