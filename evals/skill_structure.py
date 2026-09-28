#!/usr/bin/env python3
"""Tier-0 STRUCTURAL gate over every `skills/*/SKILL.md` — free, deterministic, no LLM, no network.

WHAT THIS IS AND IS NOT (read this before trusting a green run — issue #1616).

Tier 1 (`evals/run.py`) scores the intent hook. Tier 2 (LLM judge) is parked. Neither says anything
about the SKILL.md prose, which is the actual product. This file is the third, cheapest tier: it
asserts mechanically checkable STRUCTURE, and it deliberately makes no behavioural claim.

It catches four things:

  1. THE COMPACTION CLIFF. Claude Code re-attaches only the FIRST 5,000 TOKENS of each skill after a
     conversation is summarised. Everything past that is silently discarded — the loop keeps running
     with its guardrails gone. So: every skill fits the cap, and (for a waived skill that does not)
     its uppercase MUST/NEVER/ALWAYS gates still fall inside the kept prefix.
  2. AN UNREACHABLE REFERENCE FILE. Every `*.md` beside a SKILL.md must be reachable from it by
     following links (transitively — SKILL.md -> AUTOWATCH.md -> channels/.../README.md counts), and
     every local `*.md` link must resolve. This is what a split into `references/` needs in order to
     be verifiable at all.
  3. INSTRUCTION TEXT LOST IN A RESTRUCTURE — `--preserved`, below. Not a standing assertion; see
     "WHY PRESERVATION IS A TOOL, NOT A BASELINE".
  4. A GESTURE THAT ONLY RUNS FROM THE KIT'S OWN CHECKOUT (#2733). `python3 skills/agrim-loop/
     scripts/x.py` is relative to this repository's root; in a buyer's repository the plugin lives
     in the plugin cache, `skills/` is not a directory, and the documented command dies with
     `can't open file`. Every SKILL.md and `references/**/*.md` must spell its gestures
     `python3 "${CLAUDE_SKILL_DIR}/../<skill>/scripts/<x>.py"`. No allowlist, on purpose.

WHAT IT CANNOT CATCH, STATED PLAINLY. Preserving text does not preserve ATTENTION. After SKILL.md is
split, the failure mode that actually matters is the agent no longer CHOOSING to open a reference
file. Nothing here measures that; only a behavioural (Tier-2, LLM-judge) eval can, and that is
parked. A skill can pass every check in this file and still have got worse.

THE TOKEN ESTIMATOR, AND WHY THIS ONE.
`estimate_tokens()` is `ceil(len(text) / 4)`. There is no tokenizer dependency here on purpose: the
kit is stdlib-only and a BPE table is not worth carrying for a threshold this far from its boundary.
The divisor was CALIBRATED, not guessed — measured against `tiktoken`'s `cl100k_base` over all 39
shipped SKILL.md files (2026-09-03: 62,951 words, 400,524 chars, 102,080 real tokens):

    corpus chars/token   3.924        per-skill range 3.607 - 4.243
    chars/4  error       mean -1.4%   range -9.8% .. +6.1%
    words*1.33 error     mean -18.6%  range -28.2% .. -10.9%   <- NOT USED

That second row matters: `words x 1.33`, the estimator #1611 and #1616 both quote, UNDER-COUNTS this
corpus by roughly a fifth, because markdown full of backticked paths and identifiers tokenizes much
denser than plain English. `agrim-loop` is 17,221 real cl100k tokens; `words x 1.33` calls it 13,563.
Every figure this module reports is therefore LARGER than the issue's, and the direction of that
error is the safe one.

`cl100k_base` is OpenAI's tokenizer, not Claude's — it is a proxy for a proxy, and the 5,000 cap is
Claude's. That is acceptable here ONLY because the verdict does not depend on it: the largest passing
skill is 3,837 estimated tokens and the smallest breaching one is 10,909, a factor of 2.84 apart, so
nothing sits anywhere near the threshold. `chars/4`, `chars/3.8`, `words*1.33`, `words*1.2` and real
`cl100k` all name the SAME three breaching skills. `test_the_verdict_survives_every_estimator` pins
that, and turns red if a skill ever lands close enough to the cap for the proxy to become
load-bearing — which is the point at which this heuristic stops being good enough and should be
re-derived rather than argued with.

WHY PRESERVATION IS A TOOL, NOT A BASELINE.
"No instruction text lost across a restructure" needs a before-state. A committed snapshot of the
current prose would be the obvious shape and is the wrong one: this corpus normalizes to 4,120 units
across 39 files, the repo merges tens of commits a day, and skill prose is edited constantly.
A snapshot would be legitimately red most days, and a gate that is red for good reasons is a gate
somebody turns off. So preservation ships as `--preserved <git-ref>`: a comparison run AT restructure
time, against the pre-restructure commit, answering exactly "concatenate + diff" with no standing
tax. What is pinned in CI instead is the INSTRUMENT — including a dress rehearsal of #1611 on the
real `agrim-loop` corpus (`tests/test_skill_structure.py`), so the tool is known to work before the
restructure it exists for is written, not after.

WAIVERS. `skill_budget_waivers.json` records the skills that breach TODAY, each with the measurement
frozen at the time of waiver. Three ratchets keep it from becoming permanent scenery: a waived skill
may not exceed its recorded numbers (it can shrink, never grow), a waiver whose skill no longer
breaches is a FAILURE and must be deleted, and the number of waivers is itself capped by a test.
Nothing here can silently absorb a new breach.

USAGE
    python3 evals/skill_structure.py                    # report + gate (exit 1 on a finding)
    python3 evals/skill_structure.py --table            # report only, never fails
    python3 evals/skill_structure.py --preserved <ref> [skill ...]
                                                        # what instruction text this tree lost
                                                        # relative to <ref> (exit 1 if any)
"""
from __future__ import annotations

import json
import math
import pathlib
import re
import subprocess
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SKILLS_DIR = ROOT / "skills"
WAIVERS_FILE = HERE / "skill_budget_waivers.json"

#: Claude Code keeps the first 5,000 tokens of each skill when it re-attaches after a compaction.
COMPACTION_TOKEN_CAP = 5000
#: "Keep SKILL.md under 500 lines" — Claude Code's own skill-authoring guidance.
LINE_LIMIT = 500
#: Calibrated against cl100k over this corpus; see the module docstring for the measurement.
CHARS_PER_TOKEN = 4

#: Printed on every clean run, next to the pass. The operator re-parked the LLM-judge tier on
#: 2026-09-03 (issue #1616) on the explicit condition that this limit is stated rather than assumed
#: away, so it travels with the verdict instead of living only in a document.
STRUCTURE_ONLY_NOTICE = (
    "  Structure only. This is not evidence that any skill still WORKS: preserving text does not\n"
    "  preserve attention, and nothing here can tell whether an agent still chooses to open a\n"
    "  reference file it was pointed at. Only the Tier-2 behavioural eval could, and it is parked."
)

#: Gate keywords, UPPERCASE ONLY and on a word boundary. Lowercase `must`/`never`/`always` are
#: ordinary prose here (475 sentences contain one) — matching them would make "the deepest gate"
#: mean "the end of the file" for every skill, i.e. nothing. Uppercase is the kit's own convention
#: for a hard rule, and it is the set #1611 measured, so these numbers are comparable to its.
GATE_KEYWORDS = re.compile(r"(?<![A-Za-z])(MUST|NEVER|ALWAYS)(?![A-Za-z])")

#: `{a,b,c}` shell-style alternation. `agrim-vision/SKILL.md` links its four reference files as
#: `${CLAUDE_SKILL_DIR}/references/{vision,strategy,design,architecture}.md`, and a reachability
#: check that cannot read that form reports four orphans that are not orphans.
_BRACES = re.compile(r"\{([^{}]*,[^{}]*)\}")

#: A markdown link whose target ends in `.md`.
_MD_LINK = re.compile(r"\]\(\s*(?:<([^>]+\.md)>|([^)\s]+\.md))\s*\)")
#: `${CLAUDE_SKILL_DIR}/references/vision.md` — the kit's own way of naming a sibling file.
_SKILL_DIR_REF = re.compile(r"\$\{CLAUDE_SKILL_DIR\}/([A-Za-z0-9_.\-/]+\.md)")


# ---------------------------------------------------------------- measurement


def estimate_tokens(text: str) -> int:
    """Estimated tokens for `text`. Deliberately a character heuristic — see the module docstring
    for the calibration against a real tokenizer and for why no tokenizer is imported."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def line_count(text: str) -> int:
    return len(text.splitlines())


def gate_positions(text: str):
    """[(keyword, estimated token offset of its first character)] for every uppercase gate."""
    return [(m.group(1), estimate_tokens(text[: m.start()])) for m in GATE_KEYWORDS.finditer(text)]


def measure(skill_dir: pathlib.Path) -> dict:
    """Every number this gate reasons about, for one skill directory."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    gates = gate_positions(text)
    return {
        "skill": skill_dir.name,
        "words": len(text.split()),
        "chars": len(text),
        "est_tokens": estimate_tokens(text),
        "lines": line_count(text),
        "gates": len(gates),
        # None (not 0) when a skill states no uppercase gate at all: "no gate fell off the cliff"
        # and "there is no gate" are different facts and must not compare equal.
        "deepest_gate_tokens": max((t for _, t in gates), default=None),
    }


def skill_dirs(root: pathlib.Path | None = None):
    # `root: pathlib.Path = SKILLS_DIR` looked identical and is NOT: a default argument value is
    # bound once, at def-time, into the function's own `__defaults__` -- a later `SKILLS_DIR = ...`
    # (a test's `monkeypatch.setattr(ss, "SKILLS_DIR", tmp_path)` included) rebinds the MODULE
    # attribute but never reaches back into an already-defined function's frozen default. `None`
    # plus a fresh read of the module global inside the body is what makes the override live.
    # Found live: with the real tree finally fully clean (#2107/#2108), `tests/test_skill_structure
    # .py::test_the_gate_exits_nonzero_on_a_finding`/`test_table_mode_never_fails` -- which believed
    # they were scanning an isolated synthetic `agrim-huge` skill via exactly this monkeypatch -- were
    # actually still scanning the real `skills/` tree the whole time, and had only ever passed
    # because that real tree happened to carry a real, unwaived-by-the-test's-OWN-second-monkeypatch
    # finding of its own (the pre-fix oversized `agrim-goal-review`/`agrim-goal-design`).
    if root is None:
        root = SKILLS_DIR
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def measure_all(root: pathlib.Path | None = None) -> dict:
    if root is None:
        root = SKILLS_DIR
    return {d.name: measure(d) for d in skill_dirs(root)}


# ---------------------------------------------------------------- references


def expand_braces(text: str, rounds: int = 4) -> str:
    """Every alternative of every `{a,b}` group, appended to the text. Cheap, bounded, and only ever
    ADDS strings, so it can create a false 'reachable', never a false orphan."""
    out = [text]
    for _ in range(rounds):
        nxt = []
        for t in out:
            m = _BRACES.search(t)
            if not m:
                nxt.append(t)
                continue
            for option in m.group(1).split(","):
                nxt.append(t[: m.start()] + option.strip() + t[m.end() :])
        if nxt == out:
            break
        out = nxt
    return "\n".join(out)


def _tracked_md_files(skill_dir: pathlib.Path):
    """Every `*.md` under `skill_dir`, git-tracked ones only where that answer is available.

    A raw `rglob("*.md")` also reads whatever happens to be sitting in the working tree — on any
    machine that has run `npm install` for the autowatch channel, this directory holds 149+
    vendored `node_modules` READMEs the kit neither owns nor may edit, and they trip every check
    below (dead links, reachability, the preservation diff) on a developer's disk while a fresh
    clone and CI stay green. This is the identical defect class `test_docs.py`'s own
    `_tracked_files()` already closed for its sweep (#1821, recurring as #2002) — only the INDEX
    answers "what ships", and scanning it is what closes the class rather than the one directory
    a fix happened to be filed against.

    FAILS OPEN to the raw walk when `git ls-files` cannot answer. Two real cases, not one:
    `skill_dir` outside `ROOT` entirely (this repo's own tests build synthetic skill directories
    under pytest's `tmp_path` precisely so a check can be exercised without touching real skills —
    `git ls-files` at `ROOT` could never answer for a path it does not contain, and asking would be
    the wrong question, not a failure to answer it), and not a git checkout at all (this script
    also runs against a flat plugin-cache install,
    `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`, a plain copied directory with no
    `.git` anywhere). Neither case has an index to consult, so the raw walk is the only available
    answer — and neither is expected to carry stray `node_modules` in the first place."""
    try:
        rel = skill_dir.relative_to(ROOT).as_posix()
    except ValueError:
        return sorted(skill_dir.rglob("*.md"))
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", rel],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return sorted(skill_dir.rglob("*.md"))
    if out.returncode != 0:
        return sorted(skill_dir.rglob("*.md"))
    return sorted(ROOT / p for p in out.stdout.split("\0") if p and p.endswith(".md"))


def reference_files(skill_dir: pathlib.Path):
    """Every `*.md` under the skill EXCEPT its SKILL.md, as paths relative to the skill dir.

    Scoped to markdown on purpose. `skills/` also ships 61 `.py`/`.sh` helpers, and those are
    invoked by other code — `loop.py` calls `work.py`, nothing 'reads' it as reference material. A
    check that demanded each be named in its SKILL.md would report 50 findings today, none of them
    the failure this exists for, and would be switched off within a week."""
    return sorted(p.relative_to(skill_dir).as_posix() for p in _tracked_md_files(skill_dir)
                  if p.name != "SKILL.md")


def unreachable_references(skill_dir: pathlib.Path):
    """Reference files with NO path of links from SKILL.md.

    Transitive, because reachability is what matters, not adjacency: `AUTOWATCH.md` links
    `channels/sigma-autowatch/README.md`, and a direct-only check would call the README an
    orphan while an agent following the prose reaches it fine."""
    refs = reference_files(skill_dir)
    reached, frontier = {"SKILL.md"}, ["SKILL.md"]
    while frontier:
        text = expand_braces((skill_dir / frontier.pop()).read_text(encoding="utf-8"))
        for rel in refs:
            if rel not in reached and rel in text:
                reached.add(rel)
                frontier.append(rel)
    return [r for r in refs if r not in reached]


def dead_links(skill_dir: pathlib.Path):
    """[(source file, target)] for local `*.md` links inside the skill that resolve to nothing.

    The other half of check 2, and the half a restructure trips first: a split that writes
    `[gates](references/gates.md)` before the file exists reads perfectly and points at nothing.
    Targets containing `<` or `>` are placeholders in prose the skill EMITS (`.sdlc/design/<n>.md`),
    not links to anything in the tree, so they are skipped."""
    out = []
    for src in _tracked_md_files(skill_dir):
        text = expand_braces(src.read_text(encoding="utf-8"))
        for m in _MD_LINK.finditer(text):
            target = m.group(1) or m.group(2)
            if target.startswith(("http://", "https://", "#", "/", "mailto:")) or "<" in target:
                continue
            if not (src.parent / target).exists():
                out.append((src.relative_to(skill_dir.parent).as_posix(), target))
        for m in _SKILL_DIR_REF.finditer(text):
            target = m.group(1)
            if "<" in target:
                continue
            if not (skill_dir / target).exists():
                out.append((src.relative_to(skill_dir.parent).as_posix(), target))
    return out


# ---------------------------------------------------------------- repo-relative gestures


#: The literal that only resolves inside this repository's own checkout (#2733). A gesture the docs
#: give must run from a buyer's repository, where the plugin is a plugin-cache directory and
#: `skills/` does not exist; the portable spelling is `python3 "${CLAUDE_SKILL_DIR}/../<skill>/...`.
REPO_RELATIVE_GESTURE = "python3 skills/"


def gesture_scan_files(skill_dir: pathlib.Path):
    """`SKILL.md` plus every `references/**/*.md` of one skill — the files a reader takes gestures
    from. Existing files only; a skill with no `references/` scans just its SKILL.md.

    Deliberately NOT `_tracked_md_files`: `AUTOWATCH.md`, `SLACK_COMMANDS.md` and the channel READMEs
    carry the same literal today and are fixed in a separate follow-up, so widening this walk is
    that follow-up's job, not a silent side effect of this one."""
    out = [skill_dir / "SKILL.md"]
    refs = skill_dir / "references"
    if refs.is_dir():
        out.extend(sorted(refs.rglob("*.md")))
    return [p for p in out if p.is_file()]


def repo_relative_gestures(skill_dir: pathlib.Path):
    """Yield `(path relative to the skill dir, 1-based line number)` for every line of a scanned
    file that contains `REPO_RELATIVE_GESTURE`. No allowlist: a documented gesture either runs from
    a buyer's repository or it is a finding."""
    for path in gesture_scan_files(skill_dir):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if REPO_RELATIVE_GESTURE in line:
                yield path.relative_to(skill_dir).as_posix(), n


# ---------------------------------------------------------------- preservation


_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
#: A line that opens a new markdown block rather than continuing the previous one.
_BLOCK_START = re.compile(r"^\s{0,3}(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||---\s*$)")
#: Emphasis, heading, code and link punctuation — decoration a restructure is allowed to change.
_DECORATION = re.compile(r"[*_`#>\[\]()]")
_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+")


def _paragraphs(text: str):
    """Re-join hard-wrapped lines into blocks, so a re-wrap is invisible to the comparison.

    A restructure that moves a paragraph into a reference file almost always re-wraps it; a
    line-based diff would report every such line as lost and be useless on the one job it has.

    A fence marker is a block BOUNDARY, and this function keeps no open/closed fence state on
    purpose. State was the first version and it was wrong: a split whose cut lands between a
    fence's two markers leaves each half with one unmatched marker, every following line is then
    parsed under the opposite rule, and the comparison reports ~96 phantom losses on a corpus that
    lost nothing. Measured on the real `agrim-loop` corpus — see the dress rehearsal in
    tests/test_skill_structure.py, whose split is deliberately naive so this case is exercised."""
    blocks, cur = [], []
    for line in text.splitlines():
        if _FENCE.match(line):
            if cur:
                blocks.append(" ".join(cur))
                cur = []
            continue
        if not line.strip():
            if cur:
                blocks.append(" ".join(cur))
                cur = []
            continue
        if _BLOCK_START.match(line) and cur:
            blocks.append(" ".join(cur))
            cur = []
        cur.append(line.strip())
    if cur:
        blocks.append(" ".join(cur))
    return blocks


def normalize_units(text: str):
    """`text` as a list of comparable instruction units.

    Blind, on purpose, to everything a restructure may legitimately change: which FILE the text sits
    in (the caller concatenates a whole skill directory), line WRAPPING, heading LEVEL, emphasis,
    and case. Sensitive to the one thing that matters: the words being gone."""
    units = []
    for block in _paragraphs(text):
        for piece in _SENTENCE_END.split(block):
            unit = _DECORATION.sub("", piece)
            unit = re.sub(r"\s+", " ", unit).strip().lower()
            if any(ch.isalnum() for ch in unit):
                units.append(unit)
    return units


def lost_units(before_texts, after_texts):
    """Instruction units present in `before` and missing from `after`, as a sorted list.

    A multiset difference, so text that appears twice and survives once is still reported once."""
    before = Counter(u for t in before_texts for u in normalize_units(t))
    after = Counter(u for t in after_texts for u in normalize_units(t))
    missing = before - after
    return sorted(unit for unit, n in missing.items() for _ in range(n))


def worktree_corpus(skill_dir: pathlib.Path) -> dict:
    return {p.relative_to(skill_dir).as_posix(): p.read_text(encoding="utf-8")
            for p in _tracked_md_files(skill_dir)}


def git_corpus(ref: str, skill: str, root: pathlib.Path = ROOT) -> dict:
    """`{relative path: text}` for every `*.md` of one skill AS OF `ref`. Local git only."""
    rel_dir = f"skills/{skill}"
    listing = subprocess.run(["git", "-C", str(root), "ls-tree", "-r", "--name-only", ref,
                              "--", rel_dir], capture_output=True, text=True)
    if listing.returncode != 0:
        raise SystemExit(f"cannot read {ref}: {listing.stderr.strip()}")
    corpus = {}
    for path in listing.stdout.splitlines():   # not .split(): a path may contain a space
        if not path.endswith(".md"):
            continue
        blob = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"],
                              capture_output=True, text=True)
        if blob.returncode == 0:
            corpus[path[len(rel_dir) + 1:]] = blob.stdout
    return corpus


# ---------------------------------------------------------------- the gate


def load_waivers(path: pathlib.Path = WAIVERS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["waivers"]


def breaches(m: dict):
    """Which budgets this measurement exceeds, ignoring any waiver. The single definition of
    'breaching', so the gate and the staleness check cannot drift apart."""
    out = {}
    if m["est_tokens"] > COMPACTION_TOKEN_CAP:
        out["est_tokens"] = m["est_tokens"]
    if m["lines"] > LINE_LIMIT:
        out["lines"] = m["lines"]
    if m["deepest_gate_tokens"] is not None and m["deepest_gate_tokens"] > COMPACTION_TOKEN_CAP:
        out["deepest_gate_tokens"] = m["deepest_gate_tokens"]
    return out


def findings(root: pathlib.Path | None = None, waivers: dict = None):
    """Every structural violation, as human-readable lines. Empty list == the gate passes."""
    if root is None:
        root = SKILLS_DIR
    waivers = load_waivers() if waivers is None else waivers
    out = []
    measured = measure_all(root)

    for name, m in measured.items():
        over = breaches(m)
        waiver = waivers.get(name)
        for field, value in over.items():
            limit = LINE_LIMIT if field == "lines" else COMPACTION_TOKEN_CAP
            if waiver is None:
                out.append(f"{name}: {field} {value} > {limit} and is not waived — shrink it, or "
                           f"record a waiver in {WAIVERS_FILE.name} naming the issue that fixes it")
            elif value > waiver["recorded"].get(field, 0):
                out.append(f"{name}: {field} {value} exceeds its own recorded waiver "
                           f"{waiver['recorded'].get(field)} (issue #{waiver['issue']}) — a waived "
                           f"skill may shrink, never grow")

    for name, waiver in waivers.items():
        if name not in measured:
            out.append(f"waiver for '{name}' names no shipped skill — delete it")
        elif not breaches(measured[name]):
            out.append(f"{name}: STALE WAIVER (issue #{waiver['issue']}) — it is inside every "
                       f"budget now. Delete its entry from {WAIVERS_FILE.name}")

    for d in skill_dirs(root):
        for orphan in unreachable_references(d):
            out.append(f"{d.name}: `{orphan}` is not reachable by any link from SKILL.md")
        for src, target in dead_links(d):
            out.append(f"{src}: links `{target}`, which does not exist")
        for rel, n in repo_relative_gestures(d):
            out.append(f"{d.name}/{rel}:{n}: repo-relative `{REPO_RELATIVE_GESTURE}` gesture "
                       f"resolves only inside the kit's own checkout — use "
                       f'python3 "${{CLAUDE_SKILL_DIR}}/../<skill>/scripts/<x>.py"')
    return out


# ---------------------------------------------------------------- cli


def _table(measured: dict, waivers: dict) -> str:
    rows = sorted(measured.values(), key=lambda m: -m["est_tokens"])
    lines = [f"{'skill':22}{'est_tok':>9}{'lines':>7}{'gates':>7}{'deepest':>9}  note",
             "-" * 72]
    for m in rows:
        over = breaches(m)
        waiver = waivers.get(m["skill"])
        note = ""
        if over and waiver:
            note = f"WAIVED #{waiver['issue']} (ceiling {waiver['recorded']})"
        elif over:
            note = "BREACH " + ", ".join(over)
        deepest = "-" if m["deepest_gate_tokens"] is None else str(m["deepest_gate_tokens"])
        lines.append(f"{m['skill']:22}{m['est_tokens']:9}{m['lines']:7}{m['gates']:7}"
                     f"{deepest:>9}  {note}")
    return "\n".join(lines)


def _preserved(argv) -> int:
    ref = argv[0]
    skills = argv[1:] or [d.name for d in skill_dirs()]
    total = 0
    for skill in skills:
        before = git_corpus(ref, skill)
        if not before:
            print(f"{skill}: not present at {ref} — new skill, nothing to preserve")
            continue
        after = worktree_corpus(SKILLS_DIR / skill)
        lost = lost_units(before.values(), after.values())
        total += len(lost)
        print(f"{skill}: {len(lost)} instruction unit(s) lost since {ref} "
              f"({len(before)} file(s) -> {len(after)})")
        for unit in lost:
            print(f"    LOST: {unit}")
    print(f"\n{total} instruction unit(s) lost in total.")
    if not total:
        print(STRUCTURE_ONLY_NOTICE)
    return 1 if total else 0


def main(argv) -> int:
    if "--preserved" in argv:
        return _preserved(argv[argv.index("--preserved") + 1:])
    waivers = load_waivers()
    measured = measure_all()
    print(_table(measured, waivers))
    print(f"\n{len(measured)} skills, cap {COMPACTION_TOKEN_CAP} est. tokens "
          f"(chars/{CHARS_PER_TOKEN}) and {LINE_LIMIT} lines, {len(waivers)} waiver(s).")
    problems = findings(waivers=waivers)
    for p in problems:
        print(f"  FINDING: {p}")
    if "--table" in argv:
        return 0
    if problems:
        print(f"\nSTRUCTURAL GATE FAILED: {len(problems)} finding(s).")
        return 1
    # The caveat rides with the GREEN line on purpose. A limit that lives only in a doc is a limit
    # nobody reads at the moment they are deciding what a pass means, and the whole risk this tier
    # carries is somebody reading it as coverage of a problem it does not touch.
    print("structural gate: clean.")
    print(STRUCTURE_ONLY_NOTICE)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
