"""Author-blind test authorship (issue #1936) -- a breaker that never saw the implementation.

WHY. When one context writes both the implementation and its tests, the tests reflect that context's
own blind spots: they test what it THINKS it built, not what could actually go wrong. If the author
never considered negative ages, its own test will not either. This repo already enforces
maker != checker for CODE REVIEW (`work.require_review`, author-blind subagents,
`review.independent`); it does not enforce it for TEST AUTHORSHIP, which is where the correctness
claim actually originates.

ISOLATION IS BY CONSTRUCTION, NOT BY POLICY, AND THAT DISTINCTION IS THE WHOLE MODULE. This has
already failed here once: a maker subagent in this repo messaged its own independent reviewer asking
it to trust the maker's self-reported numbers. The reviewer correctly refused -- but THE ATTEMPT
HAPPENED, which is the proof that policy alone is not the guarantee. So the guarantee here is
structural:

  * `brief()` is the breaker's ONLY input, and it is BUILT from the acceptance list and the public
    interface -- never from the diff, the maker's notes, or the implementation source. There is no
    parameter through which implementation content could arrive, so there is nothing for a maker to
    persuade anyone to pass along.
  * `scrub()` then re-checks the assembled brief for implementation leakage and REMOVES it, so a
    hand-edited acceptance list that quotes the code cannot smuggle it through.
  * `no_channel()` states the spawn topology requirement the orchestrator must satisfy, and is
    asserted against a real brief in the tests rather than asserted in prose.

A VAGUE ACCEPTANCE LIST IS A PLAN-PHASE DEFECT, NOT A TEST FAILURE. "Give the breaker only the
requirement" quietly assumes the requirement is precise. Real goals often say "add age validation",
and a breaker handed that either guesses (noise) or peeks at the implementation (independence gone).
So an acceptance list too vague to attack is reported AGAINST THE GOAL, at plan time -- which is
where it is cheap -- rather than as a failure against an implementation that may be perfectly fine.
"""
import re

#: A `done_when`/`judged_when` bullet. The two headings this repo actually uses.
_SECTION = re.compile(r"^##+\s*(done_when|judged_when)\s*$", re.I | re.M)
_BULLET = re.compile(r"^\s*[-*]\s*\[[ xX]?\]\s*(.+?)\s*$", re.M)

#: Words that make a criterion UNATTACKABLE -- they name an intention, not an observable. A breaker
#: handed only these has nothing to probe and will either invent noise or go looking at the code.
_VAGUE = re.compile(r"^\s*(add|support|handle|improve|update|fix|make|ensure|clean up|refactor)\b"
                     r"[^.]{0,40}$", re.I)

#: A criterion is attackable when it names something OBSERVABLE: a value, a boundary, a state, an
#: error, a count, or a comparison. This is a heuristic and says so -- see `actionable`'s docstring.
_CONCRETE = re.compile(r"\b(returns?|raises?|rejects?|refus\w+|equals?|must|never|always|"
                        r"greater|less|boundary|empty|null|none|negative|zero|"
                        r"exit|status|error|count|between|at least|at most)\b"
                        r"|[<>=]=?|\b\d+\b", re.I)

#: An absolute filesystem path in a brief is a CHANNEL, whatever it points at. A breaker attacks an
#: acceptance list and a public interface; it has no business being told where anything lives on
#: disk. Dropping these by shape -- rather than matching a list of known scratch directories --
#: means a path nobody thought to register is closed too, which is what "by construction" has to
#: mean if it means anything. Found by the maker-attempts-to-reach-the-breaker control, which
#: initially FAILED: `no_channel` detected the leaked path correctly, but `brief` had emitted it.
_PATH = re.compile(r"(?:^|\s)(?:/[\w.\-]+){2,}|(?:^|\s)~/[\w.\-/]+")

#: Markers that mean implementation content has leaked into the brief. Any of these and the line is
#: dropped: the breaker must never see the diff or the maker's reasoning.
_LEAK = re.compile(r"^\s*(diff --git|index [0-9a-f]{7,}|[-+]{3} [ab]/|@@ )"
                    r"|^\s*[-+](?![-+])"
                    r"|\b(def |class |import |return |self\.)", re.M)


def acceptance(issue_body):
    """The done_when + judged_when bullets from an issue body, in order.

    Reads the LIST, not a paraphrase -- #1936 requires the breaker's input be the goal's own
    acceptance machinery, because a paraphrase is exactly where the author's blind spots re-enter."""
    items, body = [], issue_body or ""
    for match in _SECTION.finditer(body):
        rest = body[match.end():]
        nxt = re.search(r"^##+\s", rest, re.M)
        block = rest[:nxt.start()] if nxt else rest
        items.extend(b.strip() for b in _BULLET.findall(block) if b.strip())
    return items


def actionable(items):
    """-> (ok, reason). Is this acceptance list something a breaker can actually attack?

    A HEURISTIC, AND NAMED AS ONE. It cannot judge meaning; it asks whether each criterion mentions
    anything OBSERVABLE -- a value, a boundary, an error, a comparison, a number. "Add age
    validation" mentions none and is unattackable; "rejects a negative age" is attackable. A list
    that passes this is not thereby well-specified, and a list that fails it is genuinely unusable.
    The asymmetry is deliberate: a false PASS costs a noisy breaker run, a false FAIL costs a human
    one sentence of clarification, and only the second is cheap to recover from."""
    if not items:
        return (False, "no done_when/judged_when list at all -- nothing for a breaker to attack")
    unattackable = [i for i in items if _VAGUE.match(i) or not _CONCRETE.search(i)]
    if len(unattackable) == len(items):
        return (False, "no criterion names anything observable (e.g. %r) -- the goal was never "
                       "testable, and this is a PLAN-phase defect against the goal, not a test "
                       "failure against the implementation" % unattackable[0][:60])
    return (True, "")


def scrub(text):
    """Remove any line that looks like implementation content.

    The second line of defence, not the first: `brief()` never has the diff to begin with. This
    exists because an acceptance list is HUMAN-EDITED text that can quote the code -- pasting a
    function body into a done_when bullet would otherwise hand the breaker exactly what it must not
    see, through a channel nobody intended.

    Absolute filesystem paths go too, by SHAPE rather than by a registry of known scratch
    directories: a path nobody remembered to register is closed the same way, which is the only
    reading of "by construction" that survives contact with a maker who wants a channel."""
    return "\n".join(line for line in (text or "").splitlines()
                      if not _LEAK.search(line) and not _PATH.search(line))


def brief(issue_body, interface):
    """-> (ok, brief_or_reason). The breaker's ONLY input: acceptance criteria + public interface.

    THERE IS NO PARAMETER FOR THE DIFF, THE IMPLEMENTATION, OR THE MAKER'S NOTES. That absence is
    the isolation guarantee -- not an instruction to the maker to refrain from sharing them. A maker
    cannot pass what the function cannot accept."""
    items = acceptance(issue_body)
    ok, reason = actionable(items)
    if not ok:
        return (False, reason)
    lines = ["You have NOT seen the implementation, and must not ask for it.",
             "Attack ONLY these acceptance criteria and this public interface.",
             "", "ACCEPTANCE CRITERIA:"]
    # `*` and not `-` for the bullet: `_LEAK` treats a leading `-`/`+` as a diff line, so a brief
    # written with `-` bullets is flagged as containing implementation content by `no_channel` --
    # its own formatting tripping its own detector. Caught by the maker-attempts-a-channel control,
    # which is exactly the sort of false positive that gets a real guard switched off.
    lines += ["  * %s" % scrub(i) for i in items]
    lines += ["", "PUBLIC INTERFACE:", scrub(interface or "(none given)")]
    return (True, "\n".join(lines))


def no_channel(brief_text, scratch_paths=()):
    """-> (ok, findings). Is this brief genuinely free of a maker->breaker channel?

    Checks the two things #1936 asks to be verified BY CONSTRUCTION rather than asserted: that no
    implementation content reached the brief, and that no shared scratch path is named in it. The
    third requirement -- that the ORCHESTRATOR spawns the breaker, never the maker -- is a spawn
    topology this function cannot observe from a string; it is stated in the module docstring and
    must be enforced at the dispatch site."""
    findings = []
    if _LEAK.search(brief_text or ""):
        findings.append("implementation content reached the brief")
    for path in scratch_paths or ():
        if path and str(path) in (brief_text or ""):
            findings.append("brief names a shared scratch path: %s" % path)
    return (not findings, findings)
