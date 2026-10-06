"""Every copyable Python gesture in shipped documentation resolves (#339)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "tests/fixtures/documented_gestures_allowlist.json"
# The path may be wrapped in double quotes: `python3 "${CLAUDE_SKILL_DIR}/../sigma-x/scripts/y.py" verb ...`
# is the dominant documented form in skills/, and the unquoted-only pattern never saw it (D2-b, #307).
# `rest` runs to the end of the snippet (or a pipe/`;`/`&&`) so a flag after a quoted argument is seen too.
GESTURE = re.compile(r"python3?\s+(?P<q>\")?(?P<path>[^\s\"'`]*?(?P<script>[A-Za-z0-9_]+\.py))\"?(?P<rest>(?:[ \t][^|;&`]*)?)")
FLAG = re.compile(r"--[a-z][a-z0-9-]*")
VERB = re.compile(r"^[a-z][a-z0-9-]*$")
# `>` is excluded too: `<org>/sigma-onboarding-...` is a repository name, not a command
# verbs whose --key value pairs are free-form event fields, not a declared flag set (the verb is still checked)
FREE_FORM_FLAGS = {("loop.py", "log")}
SLASH = re.compile(r"(?<![\w/.>-])/(sigma-[a-z0-9-]+)")


@dataclass(frozen=True)
class Gesture:
    path: str
    script: str
    rest: tuple[str, ...]
    source: Path
    line: int
    text: str
    quoted: bool = False


def extract(text: str, source_path: Path) -> list[Gesture]:
    """Extract per-line copyable Python gestures from fences and inline code spans."""
    out = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        snippets = [line] if in_fence else re.findall(r"`([^`\n]+)`", line)
        if not in_fence and line.count("`") % 2 == 1:
            # an inline span that opens here and closes on a later line: its first line is still a gesture
            snippets.append(line.rsplit("`", 1)[1])
        for snippet in snippets:
            for match in GESTURE.finditer(snippet):
                out.append(Gesture(match.group("path"), match.group("script"),
                                   tuple(match.group("rest").split()), source_path, line_no, line, bool(match.group("q"))))
    return out


def sources(root=ROOT):
    tracked = {Path(x) for x in subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()}
    selected = {Path("README.md"), Path("AGENTS.md")}
    selected |= {p for p in tracked if p.parts[:1] == ("docs",) and p.suffix == ".md"}
    selected |= {p for p in tracked if len(p.parts) >= 2 and p.parts[0] == "skills" and p.name == "SKILL.md"}
    selected |= {p for p in tracked if len(p.parts) >= 3 and p.parts[0] == "skills" and "references" in p.parts and p.suffix == ".md"}
    return sorted(selected)


def scripts(root=ROOT):
    tracked = {Path(x) for x in subprocess.check_output(["git", "ls-files"], cwd=root, text=True).splitlines()}
    return sorted(p for p in tracked if p.suffix == ".py" and (
        (len(p.parts) >= 3 and p.parts[0] == "skills" and p.parts[2] == "scripts") or
        p.parts[:1] in {("tools",), ("hooks",), ("evals",)}))


def resolve(gesture: Gesture, root=ROOT) -> Path:
    candidates = [p for p in scripts(root) if p.name == gesture.script]
    skill_match = re.search(r"sigma-[a-z0-9-]+", gesture.path)
    if skill_match:
        candidates = [p for p in candidates if len(p.parts) > 1 and p.parts[1] == skill_match.group()]
    elif "${CLAUDE_SKILL_DIR}" in gesture.path and gesture.source.parts[:1] == ("skills",):
        candidates = [p for p in candidates if len(p.parts) > 1 and p.parts[1] == gesture.source.parts[1]]
    if not candidates:
        raise AssertionError(f"missing script {gesture.script}")
    if len(candidates) != 1:
        raise AssertionError("ambiguous script %s: %s" % (gesture.script, ", ".join(map(str, candidates))))
    return root / candidates[0]


@lru_cache(maxsize=None)
def help_or_source(script: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="gesture339-") as td:
        env = {"PATH": os.environ.get("PATH", ""), "HOME": td, "PYTHONDONTWRITEBYTECODE": "1",
               "GH_TOKEN": "invalid", "GITHUB_TOKEN": "invalid", "SIGMA_CLAUDE_CMD": "true"}
        try:
            run = subprocess.run([sys.executable, str(script), "--help"], cwd=td, env=env,
                                 stdin=subprocess.DEVNULL, text=True, capture_output=True, timeout=30)
            help_text = run.stdout + run.stderr
        except (OSError, subprocess.TimeoutExpired):
            help_text = ""
    return help_text + "\n" + script.read_text(encoding="utf-8")


def verb(tokens):
    for token in tokens:
        if token.startswith("--"):
            return None
        if VERB.fullmatch(token) and token not in {"."}:
            return token
    return None


def allowlist():
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    assert isinstance(data, list) and all(set(x) >= {"file", "line_text_substring", "reason"} and x["reason"] for x in data)
    return data


def allowed(gesture, entries, used):
    for index, entry in enumerate(entries):
        if entry["file"] == gesture.source.as_posix() and entry["line_text_substring"] in gesture.text:
            used.add(index); return True
    return False


def validate(gesture: Gesture, *, root=ROOT, corpus=None) -> list[str]:
    """Return the diagnostic(s) a documented gesture would produce."""
    where = f"{gesture.source}:{gesture.line}"
    try:
        script = resolve(gesture, root)
    except AssertionError as exc:
        return [f"{where}: {exc}"]
    corpus = help_or_source(script) if corpus is None else corpus
    failures = []
    candidate = verb(gesture.rest)
    if candidate and not re.search(r"(?<![A-Za-z0-9-])" + re.escape(candidate) + r"(?![A-Za-z0-9-])", corpus):
        failures.append(f"{where}: unknown verb {candidate} for {script.relative_to(root)}")
    if (gesture.script, candidate) in FREE_FORM_FLAGS:
        return failures
    for flag in FLAG.findall(" ".join(gesture.rest)):
        # some scripts parse flags into a name set (`{"session-pid", ...}`), so the bare quoted name counts too
        if flag not in corpus and f'"{flag[2:]}"' not in corpus:
            failures.append(f"{where}: unknown flag {flag} for {script.relative_to(root)}")
    return failures


def stale_entries(entries, used):
    return [entries[i] for i in range(len(entries)) if i not in used]


def test_extract_is_pure_and_finds_verb_and_flags():
    found = extract("`python3 skills/sigma-loop/scripts/loop.py start .sdlc --session-pid 7`", Path("skills/x/SKILL.md"))
    assert len(found) == 1 and found[0].script == "loop.py" and verb(found[0].rest) == "start"
    assert FLAG.findall(" ".join(found[0].rest)) == ["--session-pid"]


def test_extract_ignores_prose_and_reads_fences_and_inline_spans():
    found = extract("python3 lost.py nope --lost\n```sh\npython3 kept.py run --good\n```\n`python kept2.py --also-good`", Path("docs/example.md"))
    assert [(item.script, item.line) for item in found] == [("kept.py", 3), ("kept2.py", 5)]


def test_bad_documented_flag_is_detected_with_its_source_location():
    source = Path("skills/sigma-triage/SKILL.md")
    copied = (ROOT / source).read_text(encoding="utf-8") + "\n```sh\npython3 skills/sigma-loop/scripts/triage.py survey .sdlc --no-such-flag\n```\n"
    gesture = next(item for item in extract(copied, source) if "--no-such-flag" in item.rest)
    failures = validate(gesture)
    assert failures == [f"{source}:{gesture.line}: unknown flag --no-such-flag for skills/sigma-loop/scripts/triage.py"]


def test_stale_allowlist_entry_is_detected():
    entries = [{"file": "docs/example.md", "line_text_substring": "old", "reason": "historical exception"}]
    assert stale_entries(entries, set()) == entries


def test_documented_python_gestures_resolve_to_existing_verbs_and_flags():
    entries, used, failures = allowlist(), set(), []
    checked = quoted = 0
    for source in sources():
        text = (ROOT / source).read_text(encoding="utf-8")
        for item in extract(text, source):
            if allowed(item, entries, used):
                continue
            checked += 1
            quoted += item.quoted
            failures.extend(validate(item))
    stale = stale_entries(entries, used)
    # The documented per-line grammar intentionally ignores prose and multiline shell fragments;
    # keep a floor so a regex regression cannot silently reduce this corpus to zero.
    assert checked >= 25, checked
    # 228 quoted `python3 "${CLAUDE_SKILL_DIR}/..."` gestures measured when the quoted form was added (D2-b);
    # without the floor, reverting the regex to the unquoted-only form would silently stop checking them.
    assert quoted >= 190, quoted
    assert not failures, "\n".join(failures)
    assert not stale, "stale documented-gesture allowlist: " + repr(stale)


def test_documented_slash_commands_ship():
    missing = []
    for source in sources():
        for name in SLASH.findall((ROOT / source).read_text(encoding="utf-8")):
            if not (ROOT / "skills" / name / "SKILL.md").is_file():
                missing.append(f"{source}: /{name}")
    assert not missing, "missing documented skills: " + ", ".join(missing)


def test_bad_verb_on_a_quoted_documented_gesture_is_detected():
    """The dominant documented form quotes the script path; an unquoted-only pattern never saw it (D2-b)."""
    source = Path("skills/sigma-loop/SKILL.md")
    line = '`python3 "${CLAUDE_SKILL_DIR}/scripts/loop.py" nosuchverbxyz .sdlc "<goal>" --no-such-flag-xyz`'
    found = extract(line, source)
    assert len(found) == 1 and found[0].quoted and found[0].script == "loop.py"
    failures = validate(found[0])
    assert any("unknown verb nosuchverbxyz" in f for f in failures), failures
    # a flag AFTER a quoted argument is read too
    assert any("unknown flag --no-such-flag-xyz" in f for f in failures), failures


def test_every_quoted_script_path_in_shipped_docs_is_parsed():
    """A quoted path containing a space cannot be one gesture token, so it would be skipped silently."""
    unparsed = []
    for source in sources():
        in_fence = False
        for line_no, line in enumerate((ROOT / source).read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            for snippet in ([line] if in_fence else re.findall(r"`([^`\n]+)`", line)):
                unparsed += [f"{source}:{line_no}" for _ in re.findall(r'python3?\s+"[^"]*\s[^"]*\.py"', snippet)]
    assert not unparsed, "quoted script path with whitespace is not checked: " + ", ".join(unparsed)
