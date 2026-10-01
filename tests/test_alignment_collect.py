"""alignment-collect.sh (Slice 7): the read-only, jq-free, deterministic collector that gathers FACTS
from git history + .sdlc/ artifacts over an N-day window into one evidence pack (schema
alignment-collect/v1). It renders NO verdicts — agrim-align judges the pack. Guards its three
principles: correct facts, FAIL-OPEN (missing dep/non-git → minimal JSON + degraded[] code, exit 0),
and SECRET-SAFETY (the hard-stop scan reads diff bodies but emits ONLY {commit,file,line,pattern_id} —
never the matched substring)."""
import json, os, re, shutil, stat, subprocess, pathlib

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "skills" / "agrim-align" / "scripts" / "alignment-collect.sh"


def _git(repo, *a, **kw):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, **kw)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    return tmp_path


def _commit(repo, name, content, msg):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _run(project_dir, since=3650):
    p = subprocess.run(["bash", str(SCRIPT), "--since-days", str(since)], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)})
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)          # ALWAYS valid JSON


def _run_raw(project_dir, since=3650, path=None):
    """Like `_run` but asserts nothing and returns the raw CompletedProcess — needed whenever a
    call is expected to (or might) fail: `_run`'s own `assert returncode == 0` would fire first and
    hide the real failure text (goal #423 F21/#339-class regression check)."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir)}
    if path is not None:
        env["PATH"] = path
    return subprocess.run(["bash", str(SCRIPT), "--since-days", str(since)], capture_output=True, text=True, env=env)


def _gnu_stat_shim(tmp_path):
    """A PATH dir holding ONLY a fake `stat`, prepended (not substituted) onto the real PATH so
    git/find/awk/sort/python3 all still resolve normally — only `stat` is faked. Reproduces GNU
    coreutils' actual, documented split (confirmed against `05d2289`'s own commit message and
    goal #423's issue body, not assumed): `-f FORMAT` is filesystem-status mode on GNU (takes no
    FORMAT arg), so it prints a `  File: ...`-shaped block to STDOUT and exits 1; `-c FORMAT` is the
    real GNU form and exits 0 with the correct epoch. This is the ONLY way to exercise the GNU
    failure shape deterministically on a BSD/macOS dev machine (real `stat -f` is correct there) or
    in this repo's own CI (not proven to run on a GNU userland either) without a Linux container."""
    d = tmp_path / "gnu-stat-shim"
    d.mkdir()
    shim = d / "stat"
    shim.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -f)\n"
        "    printf '  File: \"%s\"\\n  Size: 0 Blocks: 0 IO Block: 4096 regular file\\n' \"$3\"\n"
        "    exit 1\n"
        "    ;;\n"
        "  -c)\n"
        "    python3 -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' \"$3\"\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exit 1\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(d) + os.pathsep + os.environ["PATH"]


def _decisions_git_shim(tmp_path):
    """A PATH dir holding ONLY a fake `git`, prepended (mirrors `_gnu_stat_shim`'s own idiom) so every
    OTHER git invocation the collector makes still resolves to the real binary further down PATH —
    only the ONE exact invocation DECISIONS_JSON's `git log ... --name-only -z --format='' --
    '.sdlc/decisions.json'` makes (identified the same way the shim identifies it: `--name-only`
    present AND the last argument is exactly that literal pathspec — no other call site in the script
    matches both) is intercepted, answering with a fabricated NUL-delimited path list carrying a
    control byte plus a duplicate plus a plain path.

    Needed because `.sdlc/decisions.json` is a FIXED literal pathspec (#1266's own issue text) — on a
    REAL repo, `--name-only`'s reported path for a commit matching that exact pathspec is always that
    same plain ASCII string, so a real commit can never exercise the control-byte gap this collector
    fix closes. Faking git's raw output stream is the only way to reach it, the same way
    `_gnu_stat_shim` is the only way to reach the GNU `stat -f` shape without a Linux container."""
    d = tmp_path / "decisions-git-shim"
    d.mkdir()
    shim = d / "git"
    real_git = shutil.which("git")
    assert real_git, "test host has no git — cannot build a faithful shim"
    shim.write_text(
        "#!/bin/sh\n"
        "has_name_only=0\n"
        "last=\"\"\n"
        "for a in \"$@\"; do\n"
        "  last=\"$a\"\n"
        "  if [ \"$a\" = \"--name-only\" ]; then has_name_only=1; fi\n"
        "done\n"
        "if [ \"$has_name_only\" = 1 ] && [ \"$last\" = \".sdlc/decisions.json\" ]; then\n"
        "  printf 'a\\tb\\rc.json\\0a\\tb\\rc.json\\0dir/plain.json\\0'\n"
        "  exit 0\n"
        "fi\n"
        f"exec \"{real_git}\" \"$@\"\n"
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(d) + os.pathsep + os.environ["PATH"]


def _repo_with_fresh_plan(tmp_path):
    """A repo shaped to exercise the `d1` plan-freshness dimension — the only caller of
    `date_mtime_epoch()` (line ~338) — so the mtime read is not just reachable but load-bearing for
    the assertions: `.sdlc/plans/p.md` (mtime = now) + one commit made right after it."""
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "plans").mkdir(parents=True)
    (repo / ".sdlc" / "plans" / "p.md").write_text("# plan\n")
    _commit(repo, "a.py", "x = 1\n", "feat: a")
    return repo


#: Every external binary alignment-collect.sh actually shells out to (verified by grepping the
#: script itself, not assumed) — `test`/`printf` are bash builtins and need no entry.
_ALIGNMENT_COLLECT_DEPS = ("awk", "cat", "date", "find", "git", "grep", "head", "sort", "tr", "wc")


def _path_without_python3(tmp_path):
    """A curated PATH dir, symlinked (`test_py_runner.py`'s own `_bindir` idiom) to the REAL every
    OTHER binary this script needs — but no `python3`. Proves the missing-interpreter case degrades
    with a `no_python3` code (goal #423 fold item 2).

    NOT simply "the real PATH minus any directory containing python3": on this dev machine that
    also removes `sort` (it lives in the same directory as a `python3` on PATH), silently breaking
    `degraded_json()` itself for an unrelated reason and masking the real assertion behind a
    misleading empty `degraded: []` — caught by actually running this before trusting it, not
    assumed correct because it read right."""
    d = tmp_path / "no-python3-bin"
    d.mkdir()
    (d / "bash").symlink_to(shutil.which("bash"))
    for name in _ALIGNMENT_COLLECT_DEPS:
        real = shutil.which(name)
        assert real, f"test host has no {name!r} — cannot build a faithful PATH"
        (d / name).symlink_to(real)
    return str(d)


def test_minimal_pack_on_non_git_is_valid_and_degraded(tmp_path):
    out = _run(tmp_path)                 # never ran git init
    assert out["schema"] == "alignment-collect/v1"
    assert out["degraded"] == ["no_git"] and out["window"]["commit_count"] == 0
    for d in ("d1", "d2", "d3", "d4", "d5", "d6", "d7"):
        assert d in out["dimensions"]    # minimal pack still carries the full shape


def test_gathers_window_facts_and_classifies_paths(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "def f():\n    return 1\n", "feat: add a.py")
    _commit(repo, "test_a.py", "def test_f(): assert True\n", "test: cover a")
    out = _run(repo)
    assert out["window"]["commit_count"] == 2
    d = out["dimensions"]
    assert d["d1"]["commits_with_source"] == 2
    assert d["d2"]["tests_touched_with_source_pct"] == 50   # 1 of 2 source commits touched a test
    assert d["d4"]["net_lines_added_window"] > 0


def test_churn_hotspots_preserve_internal_whitespace_in_path(tmp_path):
    # F27: the churn_hotspots awk step used `$1=""` to strip the `uniq -c` count, which rebuilds
    # the record with a single-space OFS and collapses consecutive internal spaces/tabs in the
    # path. Pin that a path with a double space survives verbatim (not collapsed to one space).
    repo = _repo(tmp_path)
    path = "a  b.py"   # two consecutive spaces
    _commit(repo, path, "x = 1\n", "add path with double space")
    out = _run(repo)
    files = [h["file"] for h in out["dimensions"]["d3"]["churn_hotspots"]]
    assert path in files, f"expected verbatim {path!r} in churn_hotspots, got {files!r}"


def test_secret_in_a_committed_diff_is_location_only(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "cfg.py", 'password = "hu' 'nter2SUPERSECRET"\napi_key = "AK' 'IA00001111EXAMPLE"\n', "add cfg")
    out = _run(repo)
    hits = out["dimensions"]["d6"]["hits"]
    assert hits, "the hard-stop scan should flag the secret assignment"
    for h in hits:
        assert set(h.keys()) == {"commit", "file", "line", "pattern_id", "category"}
    blob = json.dumps(out)
    assert "hunter2SUPERSECRET" not in blob and "AKIA00001111EXAMPLE" not in blob   # value never emitted


def test_content_line_that_renders_as_a_diff_header_never_leaks(tmp_path):
    # a committed line beginning "++ " renders as "+++ " in the outer diff; the scanner must treat it
    # as CONTENT (location-only), NOT misparse it as a "+++ b/path" header that captures the value.
    # NOTE this test must FAIL against the pre-fix awk: the buggy code poisons `file` with the "+++ …"
    # line but only EMITS it when a LATER added line trips a hard-stop — so the second line is itself a
    # trigger, forcing the poisoned `file` to surface (otherwise the guard is vacuous).
    repo = _repo(tmp_path)
    _commit(repo, "NOTES.md",
            '++ token = "gh' 'p_REALSECRETTOKENLEAK01"\napi_key = "TR' 'IGGER9SECRETVALUE"\n', "add notes")
    out = _run(repo)
    blob = json.dumps(out)
    assert "gh" "p_REALSECRETTOKENLEAK01" not in blob            # value never in the pack (fails on buggy awk)
    assert out["dimensions"]["d6"]["hits"], "both lines should hard-stop (guard must not be vacuous)"
    for h in out["dimensions"]["d6"]["hits"]:
        assert h["file"] == "NOTES.md"                        # real filename, never the "+++ …" line text


def test_test_command_known_reads_verify_command(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc").mkdir()
    (repo / ".sdlc" / "config.json").write_text('{"verify":{"command":"pytest -q"}}')
    _commit(repo, "a.py", "x = 1\n", "add")
    assert _run(repo)["dimensions"]["d2"]["test_command_known"] is True
    # empty command -> not known + degraded code
    (repo / ".sdlc" / "config.json").write_text('{"verify":{"command":""}}')
    _commit(repo, "b.py", "y = 2\n", "add b")
    out = _run(repo)
    assert out["dimensions"]["d2"]["test_command_known"] is False
    assert "no_test_command" in out["degraded"]
    # an unrelated "command" key with NO verify block must not false-positive
    (repo / ".sdlc" / "config.json").write_text('{"hooks":{"command":"echo hi"}}')
    _commit(repo, "c.py", "z = 3\n", "add c")
    assert _run(repo)["dimensions"]["d2"]["test_command_known"] is False


def test_reviews_dir_and_decisions_are_retargeted_to_sdlc(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "reviews").mkdir(parents=True)
    _commit(repo, ".sdlc/decisions.json", '{"decisions":[]}', "chore: seed decisions")
    _commit(repo, "a.py", "x = 1\n", "add a")
    out = _run(repo)
    assert out["dimensions"]["d5"]["reviews_dir_present"] is True
    assert ".sdlc/decisions.json" in out["dimensions"]["d7"]["decisions_added"]


def test_decisions_added_escapes_control_bytes_sorts_and_dedups(tmp_path):
    # #1266: DECISIONS_JSON (d7.decisions_added) was a FOURTH hand-rolled JSON-array escaper --
    # independent of json_string/jesc (F28/#354), json_file_array (#437), and json_hotspots_array/
    # json_outside_plan_array (#1142) -- sharing their identical narrow gap: a bare
    # `gsub(/\\/,...); gsub(/"/,...)` awk pair with no C0-control-byte handling. Round-trips the FULL
    # collector via `_decisions_git_shim` (a real commit can't reach this gap -- see that helper's own
    # docstring). `_run()`'s own `json.loads` raises on invalid JSON if the gap is unfixed -- this is
    # the bug, unfixed.
    repo = _repo(tmp_path)
    _commit(repo, ".sdlc/decisions.json", '{"decisions":[]}', "chore: seed decisions")
    p = _run_raw(repo, path=_decisions_git_shim(tmp_path))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)          # raises on invalid JSON -- this is the bug, unfixed
    decisions = out["dimensions"]["d7"]["decisions_added"]
    # deduped (the shim emits the control-byte path twice) and sorted, control bytes intact verbatim
    assert decisions == ["a\tb\rc.json", "dir/plain.json"]


def test_deterministic(tmp_path):
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "x = 1\n", "add a")
    _commit(repo, "b.py", "y = 2\n", "add b")
    assert _run(repo) == _run(repo)


def test_sdlc_align_skill_wires_the_collector():
    # the collector only helps if the skill runs it — guard the SKILL prose reference + allowed-tools
    skill = (SCRIPT.parent.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "alignment-collect.sh" in skill, "agrim-align must invoke the evidence collector"
    assert "Bash(bash *)" in skill, "allowed-tools must permit the bash collector invocation"
    assert "renders no verdict" in skill.lower() or "no verdict" in skill.lower()


# -- goal #423: date_mtime_epoch() must not be BSD-only (F21/#339-class regression) --------------


def _assert_fresh_plan_fields(out):
    """The concrete `d1` fields a CORRECT epoch read produces for `_repo_with_fresh_plan`'s fixture
    — not just "exit 0, valid JSON" (that alone is satisfied by a BROKEN `date_mtime_epoch` too:
    an empty `p_epoch` makes line 339's `[ -n "$p_epoch" ] || continue` skip the commit silently,
    and the script still exits 0 with `d1` quietly empty). Shared so the shim and no-shim paths are
    pinned to the exact same evidence, not two different-looking assertions that could each pass by
    accident."""
    d1 = out["dimensions"]["d1"]
    assert d1["commits_with_fresh_plan"] == 1
    assert d1["plan_existed_pct"] == 100
    assert d1["per_commit"][0]["plan_present"] is True
    assert d1["per_commit"][0]["plan_path"] == ".sdlc/plans/p.md"


def test_date_mtime_epoch_is_portable_under_a_gnu_stat_shape(tmp_path):
    """RED before the fix (rc=1, stderr `...line 340: File: unbound variable`, stdout empty — the
    exact F21/#339 crash, reproduced without a Linux container via `_gnu_stat_shim`), GREEN after:
    the fix must not merely survive the GNU `-f`-as-filesystem-status shape, it must still read the
    CORRECT epoch through it. The first assertion alone (`returncode == 0`) already reproduces the
    bug report's crash text in its own failure message if this regresses."""
    repo = _repo_with_fresh_plan(tmp_path)
    p = _run_raw(repo, path=_gnu_stat_shim(tmp_path))
    assert p.returncode == 0, f"crashed under a GNU stat shape: stderr={p.stderr!r} stdout={p.stdout!r}"
    out = json.loads(p.stdout)
    _assert_fresh_plan_fields(out)


def test_date_mtime_epoch_matches_on_the_real_local_stat_too(tmp_path):
    """The no-shim path (this machine's real `stat`, unpatched PATH) must read the SAME correct
    epoch as the shimmed-GNU path above — pins that the fix is a genuine no-regression on the
    platform that already worked, not merely proven not to break under the fake (goal #423 plan
    review, A4): the two paths are asserted against the identical concrete fields, not just "did
    not crash", which would hold on both sides of this bug and prove nothing."""
    repo = _repo_with_fresh_plan(tmp_path)
    out = _run(repo)
    _assert_fresh_plan_fields(out)


def _extract_bash_func(path, name):
    """Pull one `name() { ... }` bash function verbatim out of `path`'s source, so it can be sourced
    into an isolated `bash -c` invocation without pulling in the rest of the script (and its git /
    PROJECT_DIR preconditions). Mirrors tests/test_json_string_escaping.py's own helper of the same
    name and shape -- duplicated locally rather than imported, keeping the two test files independent."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{\n(?:.*\n)*?^\}\n" % re.escape(name), text, re.MULTILINE)
    assert m, "could not locate %s() in %s" % (name, path)
    return m.group(0)


def test_json_file_array_escapes_control_bytes_in_a_path():
    # #437: json_file_array() (builds source_files/test_files/doc_files/other_files) was a THIRD
    # hand-rolled JSON-array escaper -- independent of json_string/jesc (F28/#354) -- that escaped
    # only `\` and `"` via a bare awk gsub pair. A literal tab or CR byte in a file path produced
    # invalid JSON per RFC 8259.
    #
    # Exercises json_file_array() directly -- source extracted and run in an isolated `bash -c`
    # (json_file_array now delegates escaping to json_string, so both functions are extracted and
    # sourced together) -- rather than round-tripping a real commit through the full collector like
    # most other tests in this file: HOTSPOT_FILES/churn_hotspots is populated unconditionally for
    # EVERY changed file alongside source_files, and has its own separate, pre-existing, NOT-yet-fixed
    # instance of this same narrow-escape bug (a different hand-rolled awk escaper, out of scope for
    # #437 -- confirmed by probe, not assumed). A full-script round-trip through any real commit whose
    # path has a control byte would therefore still fail post-fix on that sibling bug, regardless of
    # this one -- exactly the "churn_hotspots' sibling commits[].source_files" distinction the issue's
    # own repro already draws.
    json_string_src = _extract_bash_func(SCRIPT, "json_string")
    json_file_array_src = _extract_bash_func(SCRIPT, "json_file_array")
    probe = "a\tb\rc.py\ndir/plain.py"    # one control-byte path + one plain path: escaping + sort + join
    script = json_string_src + "\n" + json_file_array_src + '\njson_file_array label "$1"\n'
    p = subprocess.run(["bash", "-c", script, "_", probe], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    decoded = json.loads(p.stdout)       # raises on invalid JSON -- this is the bug, unfixed
    assert decoded == sorted(probe.split("\n"))


def test_json_file_array_still_escapes_backslash_and_quote_and_handles_empty(tmp_path):
    # non-regression: the pre-fix behavior (\ and ") and the empty-list -> [] short-circuit must
    # survive the rewrite from a pure-awk pipeline to a shell loop delegating to json_string.
    json_string_src = _extract_bash_func(SCRIPT, "json_string")
    json_file_array_src = _extract_bash_func(SCRIPT, "json_file_array")

    def run(list_arg):
        script = json_string_src + "\n" + json_file_array_src + '\njson_file_array label "$1"\n'
        p = subprocess.run(["bash", "-c", script, "_", list_arg], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)

    assert run("") == []
    assert run('a\\b"c.py') == ['a\\b"c.py']


def test_churn_hotspots_and_outside_plan_files_escape_control_bytes_in_a_path(tmp_path):
    # #1142: HOTSPOTS_JSON (d3.churn_hotspots) and OUTSIDE_JSON (d1.files_changed_outside_any_plan)
    # were two MORE hand-rolled JSON-array escapers -- independent of json_string/jesc AND of #437's
    # own (now-fixed) json_file_array -- sharing the identical narrow (`\` and `"` only) gap. Round-
    # trips a REAL commit whose changed-file path carries a raw tab and CR byte (git's `--numstat -z`
    # passes these through unquoted -- confirmed directly against this repo's own commit plumbing)
    # through the FULL collector, now that both sibling gaps #437's own test was blocked by are fixed
    # (its comment names this exact scenario as out of scope for itself). `_run()`'s own `json.loads`
    # raises on invalid JSON if either escaper still has the gap -- this is the bug, unfixed.
    repo = _repo(tmp_path)
    path = "a/b\tc\rd.py"     # recognized source ext (py); appended to HOTSPOT_FILES unconditionally
    _commit(repo, path, "x = 1\n", "add path with tab and cr")   # and to files_changed_outside_any_plan
    out = _run(repo)          # no .sdlc/plans/ dir exists -> every source file counts as "outside plan"
    hotspot_files = [h["file"] for h in out["dimensions"]["d3"]["churn_hotspots"]]
    assert path in hotspot_files, f"expected verbatim {path!r} in churn_hotspots, got {hotspot_files!r}"
    outside_files = out["dimensions"]["d1"]["files_changed_outside_any_plan"]
    assert path in outside_files, f"expected verbatim {path!r} in files_changed_outside_any_plan, got {outside_files!r}"


def test_json_hotspots_array_escapes_control_bytes_and_counts_and_sorts(tmp_path):
    # Function-level probe mirroring test_json_file_array_escapes_control_bytes_in_a_path's own
    # shape (#437) -- the "parity test... extended to cover these two additional call sites, or a
    # new one is added" acceptance criterion for #1142, satisfied via the "new one" branch: these two
    # sites don't duplicate json_string's BODY (they delegate to it, so test_json_string_escaping.py's
    # existing byte-identical-copies lockstep check doesn't apply to them), they need their own direct
    # escaping-contract probe instead, same as json_file_array got in #437.
    json_string_src = _extract_bash_func(SCRIPT, "json_string")
    json_hotspots_src = _extract_bash_func(SCRIPT, "json_hotspots_array")
    probe_a = "a\tb\rc.py"    # control-byte path, appears twice -> changes:2
    probe_b = "dir/plain.py"  # plain path, appears once -> changes:1
    script = (json_string_src + "\n" + json_hotspots_src +
              '\njson_hotspots_array "$1" "$1" "$2"\n')
    p = subprocess.run(["bash", "-c", script, "_", probe_a, probe_b], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    decoded = json.loads(p.stdout)        # raises on invalid JSON -- this is the bug, unfixed
    by_file = {h["file"]: h["changes"] for h in decoded}
    assert by_file == {probe_a: 2, probe_b: 1}


def test_json_outside_plan_array_escapes_control_bytes_sorts_and_dedups():
    # Same rationale as test_json_hotspots_array_..., for OUTSIDE_JSON's new helper. Also pins the
    # `sort -u` dedup this site needs and json_file_array's callers never did (an outside-plan path
    # can recur across many commits in the window and must appear once in the final list).
    json_string_src = _extract_bash_func(SCRIPT, "json_string")
    json_outside_src = _extract_bash_func(SCRIPT, "json_outside_plan_array")
    probe = "a\tb\rc.py\na\tb\rc.py\ndir/plain.py"   # a dup control-byte path + a plain path
    script = json_string_src + "\n" + json_outside_src + '\njson_outside_plan_array "$1"\n'
    p = subprocess.run(["bash", "-c", script, "_", probe], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    decoded = json.loads(p.stdout)
    assert decoded == sorted(set(probe.split("\n")))     # deduped and sorted, control bytes intact
    assert decoded == ["a\tb\rc.py", "dir/plain.py"]

    # empty-list -> [] short-circuit, matching json_file_array's own non-regression contract
    script_empty = json_string_src + "\n" + json_outside_src + '\njson_outside_plan_array "$1"\n'
    p2 = subprocess.run(["bash", "-c", script_empty, "_", ""], capture_output=True, text=True)
    assert p2.returncode == 0, p2.stderr
    assert json.loads(p2.stdout) == []


def test_missing_python3_degrades_with_a_named_code_not_a_confident_zero(tmp_path):
    """goal #423 fold item 2: python3 is a new dependency as of this fix, and a missing
    interpreter must render as a machine-readable `degraded` code, per this file's own header
    contract — not a silent, confident `commits_with_fresh_plan: 0` indistinguishable from "no
    commit really had a fresh plan". Fail-open is still fail-open (exit 0, valid JSON, the commit
    still counted under d1 — the SKIPPED part is only the mtime correlation), it must just say so."""
    repo = _repo_with_fresh_plan(tmp_path)
    out = _run_raw(repo, path=_path_without_python3(tmp_path))
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert "no_python3" in data["degraded"]
    assert data["dimensions"]["d1"]["commits_with_fresh_plan"] == 0   # the correlation could not run…
    assert data["window"]["commit_count"] == 1                       # …but the commit itself was still seen


# --- #1486: the interpreter-spawn guard --------------------------------------------------------
# The defect: `date_mtime_epoch` took ONE path and was called from inside the per-commit loop, once
# per plan per commit. A `bash -x` trace of a 23-commit / 201-plan window on the Sigma repo
# counted 3,689+ `python3 -c` spawns -- ~107s of pure interpreter startup at 29ms each -- to read
# mtimes that cannot change while the script runs. Measured end to end: 126s -> 10s, byte-identical
# output (same md5).
#
# WHY THE POSITIVE CONTROL BELOW IS NOT OPTIONAL: this repo has already shipped a python3-spawn
# counter that silently read zero after the mechanism it counted moved out from under it. A shim
# that counts nothing makes EVERY "spawns are low" assertion pass. So the shim must first be proven
# to count, in the same test run, before any low number from it is believed.

def _counting_python3(tmp_path):
    """A `python3` shim earlier on PATH that tallies invocations, then execs the real interpreter.
    Returns (bin_dir, counter_path)."""
    import sys as _sys
    bindir = tmp_path / "shimbin"
    bindir.mkdir(parents=True, exist_ok=True)
    counter = tmp_path / "spawns.txt"
    shim = bindir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        f'printf "x" >> "{counter}"\n'
        f'exec "{_sys.executable}" "$@"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    counter.write_text("")
    return bindir, counter


def _spawn_count(counter):
    return len(counter.read_text())


def test_the_spawn_counter_itself_actually_counts(tmp_path):
    """POSITIVE CONTROL. Without this, a shim that never fires would make the guard below pass
    while the defect is fully present -- the exact way a spawn counter in this repo has already
    silently zeroed once."""
    bindir, counter = _counting_python3(tmp_path)
    env_path = f"{bindir}{os.pathsep}{os.environ['PATH']}"
    for _ in range(3):
        subprocess.run(["python3", "-c", "pass"], env={**os.environ, "PATH": env_path}, check=True)
    assert _spawn_count(counter) == 3, "the shim does not count; every assertion below is void"


def _repo_with(tmp_path, commits, plans):
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "plans").mkdir(parents=True, exist_ok=True)
    for i in range(plans):
        f = repo / ".sdlc" / "plans" / f"p{i}.md"
        f.write_text(f"plan {i}: src/mod{i}.py\n")
        # STALE ON PURPOSE. The freshness loop `break`s on the first plan within
        # PLAN_FRESHNESS_HOURS of the commit -- so plans that all MATCH make the pre-fix code
        # spawn once per commit and the guard passes against the very defect it exists to catch
        # (caught by running the negative control, which is the only reason this comment exists).
        # The real repo's pack reports commits_with_fresh_plan: 0, i.e. nothing matched and every
        # plan was visited for every commit. Epoch 0 reproduces that worst case, which is also the
        # ordinary one.
        os.utime(f, (0, 0))
    for i in range(commits):
        _commit(repo, f"src/mod{i}.py", f"x = {i}\n", f"feat: change {i}")
    return repo


def test_interpreter_spawns_do_not_scale_with_commits_times_plans(tmp_path):
    """THE GUARD. Doubling the plan count must not change how many interpreters are started.

    Asserted as a RATIO across two repo shapes rather than against a literal ceiling: a literal
    would need re-tuning every time an unrelated dimension adds a python3 call, and would quietly
    stop meaning anything. The shape of the growth is the invariant worth pinning."""
    small = _repo_with(tmp_path / "small", commits=6, plans=10)     #  60 pairs
    big = _repo_with(tmp_path / "big", commits=6, plans=40)         # 240 pairs -- 4x

    counts = []
    for repo in (small, big):
        bindir, counter = _counting_python3(tmp_path / f"shim-{repo.name}")
        p = subprocess.run(
            ["bash", str(SCRIPT), "--since-days", "3650"],
            capture_output=True, text=True,
            env={**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                 "CLAUDE_PROJECT_DIR": str(repo)},
        )
        assert p.returncode == 0, p.stderr
        counts.append(_spawn_count(counter))

    small_n, big_n = counts
    assert small_n > 0, "no interpreter started at all -- shim wiring is wrong, not a pass"
    # 4x the plans. Pre-fix this grew ~4x with them; hoisted, it must not grow at all.
    assert big_n == small_n, (
        f"interpreter spawns still scale with plan count: {small_n} -> {big_n} for 4x the plans"
    )


def test_plan_freshness_still_works_after_the_hoist(tmp_path):
    """The speed change must not have quietly turned d1 into a constant. A plan touched NOW is
    fresh for a commit made now; a plan stamped years back is not -- and the pack must still tell
    them apart, or the optimisation broke the metric it was speeding up."""
    repo = _repo(tmp_path)
    (repo / ".sdlc" / "plans").mkdir(parents=True, exist_ok=True)
    fresh = repo / ".sdlc" / "plans" / "fresh.md"
    fresh.write_text("plan: src/a.py\n")
    _commit(repo, "src/a.py", "x = 1\n", "feat: a")
    assert _run(repo)["dimensions"]["d1"]["commits_with_fresh_plan"] >= 1

    (tmp_path / "stale").mkdir(parents=True, exist_ok=True)
    stale = _repo(tmp_path / "stale")
    (stale / ".sdlc" / "plans").mkdir(parents=True, exist_ok=True)
    old = stale / ".sdlc" / "plans" / "old.md"
    old.write_text("plan: src/a.py\n")
    os.utime(old, (0, 0))                       # epoch 0 -- decades from any commit here
    _commit(stale, "src/a.py", "x = 1\n", "feat: a")
    assert _run(stale)["dimensions"]["d1"]["commits_with_fresh_plan"] == 0


def test_locale_is_pinned_once_and_never_changed_in_a_forked_shell(tmp_path):
    # A per-command `LC_ALL=C sort` in a pipeline / <(...) / $(...) is set-and-restored by a forked,
    # not-exec'd bash whose setlocale() can SIGSEGV (Homebrew bash + libintl/CoreFoundation);
    # fail-open hides it. Deterministic control: xtrace the script; the ONLY locale assignment
    # allowed is the top-level `export LC_ALL=C`, before the first `++` (forked) trace line. The
    # static scan covers the branches this run does not execute. Deliberately fails against a
    # script that still carries `| LC_ALL=C sort`.
    repo = _repo(tmp_path)
    _commit(repo, "a.py", "x = 1\n", "init")
    p = subprocess.run(["bash", "-x", str(SCRIPT), "--since-days", "3650"], capture_output=True, text=True,
                       env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo), "PS4": "+ "})
    assert p.returncode == 0, p.stderr
    lines = p.stderr.splitlines()
    depth = lambda l: len(l) - len(l.lstrip("+"))
    assign = re.compile(r"\b(LC_[A-Z]+|LANG|LANGUAGE)=")
    hits = [(i, l) for i, l in enumerate(lines) if assign.search(l)]
    # bash 5.3 traces the pin as `+ export LC_ALL=C` and then its inner `+ LC_ALL=C`; both depth 1
    assert hits and hits[0][1] == "+ export LC_ALL=C", hits
    assert all(l in ("+ export LC_ALL=C", "+ LC_ALL=C") for _, l in hits), hits
    first_forked = next((i for i, l in enumerate(lines) if depth(l) > 1), len(lines))
    assert hits[-1][0] < first_forked
    after_pin = SCRIPT.read_text().split("\nexport LC_ALL=C\n", 1)[1]
    assert not re.search(r"^[^#\n]*\b(LC_[A-Z]+|LANG)=", after_pin, re.M)
