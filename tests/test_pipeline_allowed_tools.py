"""#1957: `allowed-tools` on the three Dossier-pipeline skills, and the one property it can keep.

This defect class has surfaced three times. #1911: `agrim-goal-design` mandated a comment its grant
could not post. #1927: that fix left the claim "every command below appears in the list" still
false, because step 2 named no inventory command at all. #1957: the note insisted `find`/`ls`/`wc`/
`cat` were withheld deliberately and that the steps named only covered commands, and the first
real-codebase run reached for `wc -l`, `awk`, `gh project field-list` and `gh label list` anyway.

Three recurrences of one defect says the problem was never an individual entry. `Bash(python3 *)`
sits in all three pipeline grants and is unrestricted code execution -- it reads any file, writes
any file, reaches the network and shells out -- so nothing beside it removes a capability, and no
pipeline skill may describe its grant as a boundary.

The decision, taken once and stated in all three files: the list **declares intent and spends
prompts; it is not a sandbox**. The half of that which IS checkable is checked here rather than
asserted in prose a fourth time -- every command a skill's own steps name is covered by that
skill's own grant, so following the file end to end costs no approval prompt.

Scope note on the scan below: it starts at each file's first `## ` heading. The preamble above it
is where a file DISCUSSES its grant, naming on purpose the commands that are deliberately not in
it; a check that read the discussion as instructions could only ever be satisfied by deleting the
discussion.

#1993: the scan used to strip every fenced code block before looking for uncovered commands, on the
theory that a fence "may be an artifact TEMPLATE rather than commands to run" -- true for exactly
two tagged shapes (`markdown`, `json`; see `_TEMPLATE_FENCE_TAGS`), but nearly every REAL command in
these three files is written inside a bare or `bash`-tagged fence, which the exemption was quietly
also covering. Its own control never exercised that path, so a fourth `allowed-tools` recurrence
hiding inside a fence -- exactly where the pipeline's real commands live -- would have reported
`clean`. Fixed here: only a tagged-template fence is dropped; everything else is scanned.
"""
import pathlib
import re

from skill_corpus import skill_corpus

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The three stages of `docs/dossier-pipeline.md`. The decision is theirs jointly -- one of them
#: reverting to a boundary story is the drift this file exists to catch.
PIPELINE = ("agrim-dossier", "agrim-goal-design", "agrim-goal-review")

#: The one sentence all three carry verbatim, so a reader who lands on any of them gets the same
#: posture rather than three compatible-sounding paraphrases.
DECISION = "declares intent and spends prompts; it is not a sandbox"

#: Shell verbs a reader could plausibly be told to run. If one is the first word of a backticked
#: span in a step, that skill's own grant has to cover it.
COMMAND_WORDS = frozenset((
    "awk", "cat", "chmod", "cp", "curl", "cut", "diff", "echo", "find", "gh", "git", "grep",
    "head", "jq", "ls", "mkdir", "mv", "printf", "pytest", "python", "python3", "rg", "rm",
    "sed", "sort", "tail", "tee", "touch", "tr", "uniq", "wc", "xargs",
))


def _skill(name):
    """The whole skill an agent actually reads: SKILL.md plus every `references/*.md` beside it
    (#2107/#2108 split `agrim-goal-design`/`agrim-goal-review` into body + references/*.md — a raw
    SKILL.md-only read would silently stop seeing any command that moved into a reference file,
    reporting a false `clean` rather than failing loud). `skill_corpus()` on an unsplit skill (no
    reference files) returns the SKILL.md text unchanged, so this is a no-op for `agrim-dossier`."""
    return skill_corpus(name)


def _frontmatter(text):
    return text.split("---", 2)[1]


def _steps(text):
    """The body from its first `## ` heading on -- the instructions, not the grant discussion."""
    body = text.split("---", 2)[2]
    match = re.search(r"^## ", body, re.M)
    assert match, "this skill has no `## ` section at all -- re-check this pin"
    return body[match.start():]


def _granted_prefixes(text):
    """`Bash(git *)` -> ('git',); `Bash(gh issue *)` -> ('gh', 'issue')."""
    prefixes = []
    for entry in re.findall(r"Bash\(([^)]+)\)", _frontmatter(text)):
        words = entry.replace("*", " ").split()
        if words:
            prefixes.append(tuple(words))
    return prefixes


#: #1993: a fence is not, by itself, proof its content is a template rather than an instruction to
#: run. Confirmed by inspecting every fence in all three pipeline files: the two tags that appear
#: are `markdown` (`agrim-goal-design`'s `.sdlc/design/<n>.md` artifact shape) and `json`
#: (`agrim-dossier`'s example answers payload) -- both genuinely data/documentation, never a command.
#: Every OTHER fence -- bare, or tagged `bash` -- wraps a literal command in these files, and that
#: is where nearly all of the pipeline's real commands actually live. So only a fence tagged with
#: one of these two is dropped; everything else is scanned exactly like the surrounding prose.
_TEMPLATE_FENCE_TAGS = frozenset(("markdown", "json"))


def _fence_segments(section):
    """(prose, bare_fence_lines) -- prose has EVERY fence removed (a single-backtick check has no
    business running over a code or data block); bare_fence_lines carries, separately, every line
    from a fence NOT tagged `_TEMPLATE_FENCE_TAGS`, because that content is a literal command with
    no backtick-quoting of its own -- nothing that only looks for `` `spans` `` would ever see it
    otherwise (#1993)."""
    prose, bare = [], []
    in_fence, tag = False, None
    for line in section.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence, tag = True, stripped[3:].strip().lower()
            else:
                in_fence, tag = False, None
            continue
        if not in_fence:
            prose.append(line)
        elif tag not in _TEMPLATE_FENCE_TAGS:
            bare.append(line)
    return "\n".join(prose), bare


def _uncovered(text):
    granted = _granted_prefixes(text)
    prose, bare_lines = _fence_segments(_steps(text))
    # Two candidate shapes, checked by the identical rule below: a `single-backtick span` in
    # ordinary prose (the scan's original reach), and a WHOLE LINE that survived a bare/`bash`
    # fence -- a fenced command is not additionally backtick-quoted, so only the line itself names
    # it (#1993).
    candidates = re.findall(r"`([^`]{1,200})`", prose, re.S) + bare_lines
    out = []
    for span in candidates:
        words = span.split()
        # A BARE utility name is a mention, not an instruction. An instruction to RUN something
        # carries at least a flag, an argument or a subcommand, so require two words.
        if len(words) < 2 or words[0] not in COMMAND_WORDS:
            continue
        if not any(len(words) >= len(p) and tuple(words[:len(p)]) == p for p in granted):
            flat = " ".join(words)
            if flat not in out:
                out.append(flat)
    return out


# ----------------------------------------------------------------- the premise of the decision

def test_every_pipeline_grant_carries_the_blanket_python3_entry():
    """This is WHY the decision is what it is. If a stage ever drops the blanket grant, the
    boundary question genuinely reopens for it and the shared wording below has to be re-taken
    rather than inherited."""
    for name in PIPELINE:
        assert "Bash(python3 *)" in _frontmatter(_skill(name)), \
            "%s no longer blanket-grants python3 -- re-take the #1957 decision for it" % name


# ----------------------------------------------------------------- the decision, stated once

def test_all_three_pipeline_skills_state_the_same_decision():
    """Whitespace-normalised, for the reason `test_the_blanket_python3_grant_is_named_rather_than_
    dressed_up` already reads its own anchor two ways: all three files wrap at column 100, so where
    an editor broke the line is not part of the claim. The words and their order are."""
    for name in PIPELINE:
        flat = re.sub(r"\s+", " ", _skill(name))
        assert DECISION in flat, \
            "%s does not carry the #1957 decision verbatim" % name


def test_no_pipeline_skill_claims_its_grant_is_an_enforcement_boundary():
    """Each of these was in the tree and each was false, for the identical reason: `python3` is
    granted beside them. They are pinned individually because a reader re-introducing one would be
    restating a claim that was measured wrong, not writing a new one."""
    forbidden = (
        # agrim-goal-design, citing its sibling as the enforceable posture
        "structurally cannot explore",
        # agrim-dossier, on its own omission of Read/Grep/Glob
        "the constraint is enforced here",
        # agrim-dossier, on `ls skills/`
        "general-purpose repo-search back door",
    )
    for name in PIPELINE:
        text = _skill(name)
        for claim in forbidden:
            assert claim not in text, "%s claims enforcement again: %r" % (name, claim)


# ----------------------------------------------------------------- the friction property, measured

def test_every_command_a_pipeline_skill_names_is_covered_by_its_own_grant():
    """The one property `allowed-tools` CAN keep, made mechanical across all three stages rather
    than re-asserted in one of them. Control: put `cat > .sdlc/design/<n>.md` in any step of any of
    the three and this goes red naming that file."""
    offenders = {name: _uncovered(_skill(name)) for name in PIPELINE}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, "commands named in steps that the skill's own grant does not cover: %r" % offenders


def test_the_scan_can_actually_see_an_uncovered_command():
    """The control for the check above, run in-process so it is not a claim about a check nobody
    watched fail. An ungranted command spliced into a real step must be reported."""
    text = _skill("agrim-goal-design")
    assert not _uncovered(text), "baseline is not clean -- the control below proves nothing"
    spiked = text.replace("## 5. Write the artifact",
                          "## 5. Write the artifact\n\nWrite it with `cat > .sdlc/design/<n>.md`.", 1)
    assert "cat > .sdlc/design/<n>.md" in _uncovered(spiked), \
        "the scan cannot see an ungranted command in a step -- it is decoration"


def test_the_scan_can_actually_see_an_uncovered_command_inside_a_fenced_block():
    """#1993: the control above only ever exercised an INLINE backtick span, but nearly every real
    command in these three files is written inside a fenced block, not as a bare span -- which the
    scan used to strip before ever looking. Reproduces the review's own live finding: splice an
    uncovered command (`gh issue list` -- not in `agrim-goal-review`'s grant, which only has `gh
    issue view *`/`gh issue comment *`) into an EXISTING fenced block (step 3's REJECT comment,
    verbatim) and it must be seen too, not just the identical string as prose."""
    text = _skill("agrim-goal-review")
    assert not _uncovered(text), "baseline is not clean -- the control below proves nothing"
    # #2482: the anchor text moved when step 3 gained a design-PR close call folded into the
    # same comment -- re-anchored to the current exact string rather than the pre-#2482 one.
    target = ('python3 "${CLAUDE_SKILL_DIR}/../agrim-loop/scripts/loop.py" note .sdlc <n> '
              '"goal-review: REJECTED -- <findings> -- design PR: <close-design\'s own result>"')
    assert target in text, "step 3's REJECT command moved -- re-anchor this splice point"
    spiked = text.replace(target, target + '\ngh issue list --search "is:open" --json number', 1)
    assert 'gh issue list --search "is:open" --json number' in _uncovered(spiked), \
        "the scan cannot see an ungranted command inside a fenced block -- it is decoration where " \
        "nearly every real command in the pipeline actually lives"


# ----------------------------------------------------------------- the contract says it too

def test_the_contract_does_not_call_the_stage_0_constraint_structural():
    """`docs/dossier-pipeline.md` §4 used to say the no-code-exploration rule was *structural* on
    Claude Code and prose everywhere else. It is prose everywhere: `Bash(python3 *)` is in the
    Stage 0 grant, so a code read is one `python3 -c` away on Claude Code too."""
    contract = (ROOT / "docs" / "dossier-pipeline.md").read_text(encoding="utf-8")
    stage0 = contract[contract.index("## 4. Stage 0"):contract.index("### 4a.")]
    assert "the constraint is *structural*" not in stage0
    assert DECISION in contract, "the contract does not state the #1957 decision at all"
