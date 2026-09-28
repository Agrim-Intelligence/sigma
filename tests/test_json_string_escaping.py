"""json_string / jesc (F28, issue #354): every jq-free collector hand-rolls its own JSON-string
escaper because there's no jq dependency to lean on. json_string is duplicated VERBATIM across five
scripts (discovery-scan.sh, risk-detect.sh, alignment-collect.sh, completion_gate.sh,
audit-collect.sh); jesc, its
awk-side counterpart used inside the diff-body scanners, is duplicated across two of those four
(risk-detect.sh, alignment-collect.sh). Before this fix json_string escaped only `\\ " \\n \\t` and
jesc escaped only `\\ "` -- any OTHER C0 control byte (\\r, backspace, form feed, ...) in a value went
out RAW, producing invalid JSON per RFC 8259 (every byte U+0000-U+001F must be escaped). LOW severity
and latent (git C-quotes control bytes in the `file` field most call sites carry) but genuinely
reachable -- e.g. alignment-collect.sh's `subject` field is a raw, uncensored commit message.

These tests exercise the escaper FUNCTIONS directly (extracted from each script's source) instead of
round-tripping through the full collector pipeline: most call sites only ever see git-derived paths
or fixed constants, so there is no reliable, portable way to force a raw control byte through git's
own plumbing into every field. Feeding the function the byte directly is also exactly the issue's own
suggested verification ("a test feeding a \\r-containing value asserting valid JSON out") and pins the
CONTRACT regardless of whether today's plumbing happens to expose the gap on any given call site.

Also pins the issue's other ask -- "four call sites, keep them in lockstep" -- with a parity check
that the four json_string bodies (and, separately, the two jesc bodies) stay byte-identical, so a
future fix to one copy doesn't quietly leave the others behind."""
import json, os, re, subprocess, pathlib, textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent

JSON_STRING_SITES = {
    "discovery-scan.sh":    ROOT / "skills" / "agrim-loop" / "scripts" / "discovery-scan.sh",
    "risk-detect.sh":       ROOT / "skills" / "agrim-loop" / "scripts" / "risk-detect.sh",
    "alignment-collect.sh": ROOT / "skills" / "agrim-align" / "scripts" / "alignment-collect.sh",
    "completion_gate.sh":   ROOT / "hooks" / "completion_gate.sh",
    "audit-collect.sh":     ROOT / "skills" / "agrim-audit" / "scripts" / "audit-collect.sh",
}
JESC_SITES = {
    "risk-detect.sh":       ROOT / "skills" / "agrim-loop" / "scripts" / "risk-detect.sh",
    "alignment-collect.sh": ROOT / "skills" / "agrim-align" / "scripts" / "alignment-collect.sh",
}

# Exercises everything in one string: CR/backspace/form-feed (new short-form escapes), a generic C0
# byte the fallback must \u00XX-encode (0x01, SOH), and the pre-existing \n, \t, ", \ handling (must
# not regress).
_PROBE = "line1\rline2\x08\x0cend\x01tail\n\tok\"quote\\slash"


def _extract_bash_func(path, name):
    text = path.read_text(encoding="utf-8")
    m = re.search(r"^%s\(\) \{\n(?:.*\n)*?^\}\n" % re.escape(name), text, re.MULTILINE)
    assert m, "could not locate %s() in %s" % (name, path)
    return m.group(0)


def _extract_awk_func(path, name):
    # Brace-COUNTED, not regex-bounded: jesc's fixed body nests a for-loop block, so a naive
    # non-greedy "up to the first }" regex would truncate the function mid-body. Starts from the
    # BEGINNING of the "function ..." line (not the match position) so the returned text carries its
    # own real leading indentation on every line -- textwrap.dedent needs that to normalize the two
    # copies, which legitimately sit at different embedding depths (4 vs 6 spaces, matching each
    # file's own surrounding awk block, same as that file's pre-existing `emit()` sibling).
    text = path.read_text(encoding="utf-8")
    m = re.search(r"function\s+%s\([^)]*\)\s*\{" % re.escape(name), text)
    assert m, "could not locate awk function %s() in %s" % (name, path)
    line_start = text.rfind("\n", 0, m.start()) + 1
    depth, i = 0, m.end() - 1
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[line_start:i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting %s from %s" % (name, path))


def _run_json_string(path, value):
    src = _extract_bash_func(path, "json_string")
    p = subprocess.run(["bash", "-c", src + '\njson_string "$1"', "_", value],
                        capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


def _run_jesc(path, value):
    src = _extract_awk_func(path, "jesc")
    env = dict(os.environ); env["V"] = value
    p = subprocess.run(["awk", src + '\nBEGIN { printf "%s", jesc(ENVIRON["V"]) }', "/dev/null"],
                        capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return '"' + p.stdout + '"'          # jesc returns the bare escaped body; callers add the quotes


def test_json_string_escapes_cr_and_other_c0_validly():
    for name, path in JSON_STRING_SITES.items():
        out = _run_json_string(path, _PROBE)
        decoded = json.loads(out)        # raises on invalid JSON -- this is the bug, unfixed
        assert decoded == _PROBE, "%s: round-trip mismatch: %r" % (name, out)


def test_json_string_still_escapes_the_original_four_characters():
    # non-regression: the pre-fix behavior (\ " \n \t) must survive unchanged
    for name, path in JSON_STRING_SITES.items():
        out = _run_json_string(path, 'a\\b"c\nd\te')
        assert json.loads(out) == 'a\\b"c\nd\te', name


def test_json_string_copies_stay_in_lockstep():
    bodies = {name: _extract_bash_func(path, "json_string") for name, path in JSON_STRING_SITES.items()}
    first_name, first_body = next(iter(bodies.items()))
    for name, body in bodies.items():
        assert body == first_body, (
            "json_string in %s has drifted from %s -- the four copies must stay byte-identical "
            "(F28: 'keep them in lockstep')" % (name, first_name))


def test_jesc_escapes_cr_and_other_c0_validly():
    for name, path in JESC_SITES.items():
        out = _run_jesc(path, _PROBE)
        decoded = json.loads(out)
        assert decoded == _PROBE, "%s: round-trip mismatch: %r" % (name, out)


def test_jesc_still_escapes_backslash_and_quote():
    for name, path in JESC_SITES.items():
        out = _run_jesc(path, 'a\\b"c')
        assert json.loads(out) == 'a\\b"c', name


def test_jesc_copies_stay_in_lockstep():
    # dedented, not raw-byte, comparison: the two copies live at different embedding depths (each
    # matches its own file's surrounding awk-block indent), so only the DEDENTED body -- the code
    # modulo a constant left margin -- is expected to be identical.
    bodies = {name: textwrap.dedent(_extract_awk_func(path, "jesc")) for name, path in JESC_SITES.items()}
    first_name, first_body = next(iter(bodies.items()))
    for name, body in bodies.items():
        assert body == first_body, (
            "jesc in %s has drifted from %s -- the two copies must stay in lockstep (modulo "
            "indentation) (F28: 'keep them in lockstep')" % (name, first_name))
