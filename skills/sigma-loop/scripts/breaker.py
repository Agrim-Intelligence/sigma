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

THE SCRUB IS A heuristic, AND NAMED AS ONE. `_LEAK` recognises code by SHAPE (a `def name(`, a
line-start import, a line-start `return` with an operator or bracket, a one-line `if x: return y`,
`self.attr`, diff lines, absolute paths), case-sensitively, so ordinary capitalised prose such as
"Return 0 on success" survives. It is the second line of defence; the first is that `brief()` never
receives the implementation. It has residual gaps in both directions, pinned by tests rather than
hidden: bare assignments, bare calls, `raise` and `assert` lines survive the scrub; and lowercase
prose that reads like a statement ("return nothing", "Returns the count; return 1 on failure") is
blanked. A blanked criterion is never silent: `criterion_verdicts` reports it as scrubbed to empty
and `brief` counts it in its NOTE line.

PER-CRITERION VERDICTS. `criterion_verdicts(items)` returns one `{"criterion", "actionable",
"reason"}` dict per item, in order, over the SCRUBBED text. Criterion ids and PROCESS/DOC/MEASURED
tag classification are NOT shipped here: that classification waits on issue 535, so a verdict
carries no id key and no positional id is minted.

OUTPUT CONTRACT (what a breaker must emit; `validate_output(text, stem)` enforces it, pure, nothing
is executed or written). Zero to `_MAX_BLOCKS` blocks, each exactly:

    === FILE: tests/acceptance/<stem>/test_acc_<s>_<suffix>.py ===
    <python source>
    === END FILE ===

`<stem>` is the goal stem (`[A-Za-z0-9_-]+`); `<s>` is the stem with every character outside
`[A-Za-z0-9]` replaced by `_`; `<suffix>` is `[A-Za-z0-9_]+`. Forward slashes only, no `..`, no
absolute path, no nested directory, `.py` only. The `test_acc_<s>_` prefix keeps the basename unique
across the collection (this repo collects in default import mode); sanitising is not injective
(`a-b` and `a_b` collide across goals), which this per-call validator cannot see. Duplicate names
within one call (case-folded) are rejected. Marker lines tolerate trailing spaces and tabs and
CRLF; an indented marker is body text. Only blocks and blank lines may appear outside a block. Each
block must `ast.parse`, define a MODULE-LEVEL `def`/`async def test_*`, and have no module-top
import other than `pytest` or the standard library (relative imports are findings; imports inside
function bodies are allowed; an import in a class body or any top-level `if`/`try`/`with` IS
flagged). Without `sys.stdlib_module_names` (Python 3.9) a non-pytest module-top import is a
finding ("cannot classify imports"), never a crash. Caps are derived, not measured: 7 criteria, two
files each = 14 blocks, 32 KiB per block, 448 KiB total.
"""
import ast
import importlib.util
import pathlib
import re
import sys

#: Criterion ids and PROCESS/DOC/MEASURED tag classification are NOT implemented here: that
#: classification waits on issue 535. Verdicts therefore carry no id and no positional id is minted.

_MODULES = {}


def _load(name):
    """Sibling module by path (same skill), cached so repeated calls do not re-exec it."""
    if name not in _MODULES:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _MODULES[name] = module
    return _MODULES[name]


#: A `done_when`/`judged_when` bullet. The two headings this repo actually uses.
_SECTION = re.compile(r"^##+\s*(done_when|judged_when)\s*$", re.I | re.M)
_BULLET = re.compile(r"^\s*[-*]\s*\[[ xX]?\]\s*(.+?)\s*$", re.M)
#: Lenient fallback: also the space-separated `Done when`, and plain `- text` bullets.
_SECTION_ANY = re.compile(r"^##+\s*done[_ ]when\s*$|^##+\s*judged_when\s*$", re.I | re.M)
_BULLET_ANY = re.compile(r"^[ \t]*[-*][ \t]+(?:\[[ xX]?\][ \t]*)?(.+?)[ \t]*$", re.M)

#: Derived caps for `validate_output`: a derivation, not a measurement.
_MAX_CRITERIA = 7          # pinned to acceptance.criteria's upper bound by a test
_MAX_BLOCKS = 2 * _MAX_CRITERIA
_MAX_BLOCK_BYTES = 32768   # same order as the acceptance record's own 32 KiB limit
_MAX_TOTAL_BYTES = _MAX_BLOCKS * _MAX_BLOCK_BYTES

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
#: dropped: the breaker must never see the diff or the maker's reasoning. A HEURISTIC and a second
#: line of defence (the first is that `brief()` never receives the implementation). CASE-SENSITIVE
#: on purpose (no re.I): capitalised prose ("Return 0 on success") never matches a keyword arm.
#: Code is matched by shape, not by the bare words return/import/class that criteria prose uses.
_LEAK = re.compile(r"^\s*(diff --git|index [0-9a-f]{7,}|[-+]{3} [ab]/|@@ )"
                    r"|^\s*[-+](?![-+])"
                    r"|\bdef\s+\w+\s*\("
                    r"|\basync\s+def\s+\w+\s*\("
                    r"|\bclass\s+\w+\s*[:(]"
                    r"|^[ \t]*import\s+[\w.]+(?:\s+as\s+\w+)?(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*[ \t]*$"
                    r"|^[ \t]*from\s+[\w.]+\s+import\s+\S"
                    r"|^[ \t]*return\b[^\n]*[=<>()\[\]]"
                    r"|^[ \t]*return(?:[ \t]+\w+(?:\.\w+)*)?[ \t]*$"
                    r"|\b(?:if|elif|else|for|while)\b[^\n]*:[ \t]*return\b"
                    r"|;[ \t]*(?:import|return)[ \t]+\w"
                    r"|\bself\.\w+", re.M)


def _scan(body, pattern, bullet):
    """Bullets under every heading matching `pattern`, in order, up to the next heading."""
    items = []
    for match in pattern.finditer(body):
        rest = body[match.end():]
        nxt = re.search(r"^##+\s", rest, re.M)
        block = rest[:nxt.start()] if nxt else rest
        items.extend(b.strip() for b in bullet.findall(block) if b.strip())
    return items


def acceptance(issue_body):
    """The Done when + done_when + judged_when bullets from an issue body, in order. Never raises.

    Reads the LIST, not a paraphrase -- #1936 requires the breaker's input be the goal's own
    acceptance machinery, because a paraphrase is exactly where the author's blind spots re-enter.

    A valid level-two `## Done when` (3-7 items) is read by `acceptance.criteria` itself, so the
    bullets are the same; the underscore headings follow. Anything else (absent, malformed, or a
    count outside 3-7) falls back to a lenient scan that also reads plain `- text` bullets. KNOWN
    GAP: the fallback does not skip fenced code blocks, unlike `acceptance.criteria`."""
    body = issue_body if isinstance(issue_body, str) else ""
    try:
        strict = _load("acceptance").criteria(body)
    except ValueError:
        strict = None
    if strict is not None:
        return list(strict) + _scan(body, _SECTION, _BULLET)
    return _scan(body, _SECTION_ANY, _BULLET_ANY)


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


def criterion_verdicts(items):
    """-> one {"criterion", "actionable", "reason"} dict per item, in order. No id key (waits on 535).

    `criterion` is ALWAYS the scrubbed text, so a verdict cannot re-leak what `scrub` removed. A
    criterion that scrubs to empty is reported with criterion "" and is never actionable.
    `reason` is "" exactly when the criterion is actionable."""
    verdicts = []
    for item in items or ():
        text = scrub(item).strip()
        if not text:
            reason = "criterion scrubbed to empty: it quotes implementation, a path or a diff"
        elif _VAGUE.match(text) or not _CONCRETE.search(text):
            reason = "names nothing observable (e.g. %r)" % text[:60]
        else:
            reason = ""
        verdicts.append({"criterion": text, "actionable": reason == "", "reason": reason})
    return verdicts


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
    cannot pass what the function cannot accept.

    A criterion that scrubs to empty is DROPPED (not an empty bullet) and counted in a NOTE line.
    The list is then re-checked on the survivors: it is refused unless at least one survivor is
    actionable, so pasted-code-plus-vague is refused. Once one actionable survivor exists, the
    verdicts of vague-but-nonempty criteria are advisory: they stay listed. Per-item detail comes
    from `criterion_verdicts(acceptance(body))`."""
    items = acceptance(issue_body)
    ok, reason = actionable(items)
    if not ok:
        return (False, reason)
    verdicts = criterion_verdicts(items)
    survivors = [v for v in verdicts if v["criterion"]]
    withheld = [v for v in verdicts if not v["criterion"]]
    if not any(v["actionable"] for v in survivors):
        detail = "; ".join(v["reason"] for v in verdicts if not v["actionable"])
        return (False, "no criterion survives the scrub as something observable (%s) -- this is a "
                       "PLAN-phase defect against the goal, not a test failure against the "
                       "implementation" % detail)
    lines = ["You have NOT seen the implementation, and must not ask for it.",
             "Attack ONLY these acceptance criteria and this public interface.",
             "", "ACCEPTANCE CRITERIA:"]
    # `*` and not `-` for the bullet: `_LEAK` treats a leading `-`/`+` as a diff line, so a brief
    # written with `-` bullets is flagged as containing implementation content by `no_channel` --
    # its own formatting tripping its own detector. Caught by the maker-attempts-a-channel control,
    # which is exactly the sort of false positive that gets a real guard switched off.
    lines += ["  * %s" % v["criterion"] for v in survivors]
    if withheld:
        lines += ["", "NOTE: %d criteria withheld (scrubbed to empty); see criterion_verdicts"
                  % len(withheld)]
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


_HEADER = re.compile(r"=== FILE: (.+) ===")
_END = "=== END FILE ==="
_STEM = re.compile(r"[A-Za-z0-9_-]+")
_SUFFIX = re.compile(r"[A-Za-z0-9_]+")
_SKIP_WALK = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.expr)


def _check_path(path, stem, seen):
    """Findings for one block path; `seen` collects case-folded names to catch duplicates."""
    if ("\x00" in path or "\\" in path or path.startswith("/")
            or ".." in path.split("/")):
        return ["path escape (NUL, backslash, absolute or ..): %r" % path]
    if stem is None:
        return []
    prefix = "tests/acceptance/%s/" % stem
    if not path.startswith(prefix):
        return ["path outside %s: %r" % (prefix, path)]
    rest = path[len(prefix):]
    if "/" in rest:
        return ["nested subdirectory not allowed: %r" % path]
    if not rest.endswith(".py"):
        return ["not a .py file: %r" % path]
    base = rest[:-3]
    want = "test_acc_%s_" % re.sub(r"[^A-Za-z0-9]", "_", stem)
    if not (base.startswith(want) and _SUFFIX.fullmatch(base[len(want):])):
        return ["name %r is not collectable/unique: need %s<suffix> with suffix [A-Za-z0-9_]+"
                % (rest, want)]
    found = []
    if rest.casefold() in seen:
        found.append("duplicate file name (case-folded): %r" % rest)
    seen.add(rest.casefold())
    return found


def _module_top_imports(tree):
    """Import/ImportFrom nodes outside function bodies. Iterative: a deep AST cannot recurse."""
    found, stack = [], list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            found.append(node)
        elif not isinstance(node, _SKIP_WALK):
            stack.extend(c for c in ast.iter_child_nodes(node) if not isinstance(c, _SKIP_WALK))
    return found


def _check_source(path, src):
    """Findings for one block's Python source."""
    if len(src.encode("utf-8")) > _MAX_BLOCK_BYTES:
        return ["%s: block exceeds %d bytes (skipped)" % (path, _MAX_BLOCK_BYTES)]
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        return ["%s: does not parse (%s)" % (path, type(exc).__name__)]
    found = []
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
               for n in tree.body):
        found.append("%s: no module-level test_* function" % path)
    stdlib = getattr(sys, "stdlib_module_names", None)
    unclassified = False
    for node in _module_top_imports(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                found.append("%s: relative import at module top" % path)
                continue
            roots = [(node.module or "").split(".")[0]]
        else:
            roots = [a.name.split(".")[0] for a in node.names]
        for root in roots:
            if root == "pytest":
                continue
            if stdlib is None:
                unclassified = True
            elif root not in stdlib:
                found.append("%s: module-top import of %r is not pytest or stdlib" % (path, root))
    if unclassified:
        found.append("%s: cannot classify imports on this Python (no sys.stdlib_module_names)" % path)
    return found


def validate_output(text, stem):
    """-> (ok, findings). Validate a breaker's output against the OUTPUT CONTRACT in the module
    docstring. Pure: parses with `ast.parse`, never executes, writes nothing. ALL findings are
    returned. `text` may be str or bytes."""
    findings = []
    if isinstance(text, (bytes, bytearray)):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError:
            return (False, ["output is not valid UTF-8"])
    text = text if isinstance(text, str) else ""
    if len(text.encode("utf-8")) > _MAX_TOTAL_BYTES:
        return (False, ["output exceeds the total cap of %d bytes (skipped)" % _MAX_TOTAL_BYTES])
    if not text.strip():
        return (False, ["empty output"])
    if not (isinstance(stem, str) and _STEM.fullmatch(stem)):
        findings.append("invalid goal stem %r (need [A-Za-z0-9_-]+)" % (stem,))
        stem = None
    blocks, current = [], None
    for number, raw in enumerate(text.replace("\r\n", "\n").split("\n"), 1):
        line = raw.rstrip(" \t")
        header = _HEADER.fullmatch(line)
        if header:
            if current is not None:
                findings.append("line %d: block not terminated before next header" % number)
            current = (header.group(1), [])
        elif line == _END:
            if current is None:
                findings.append("line %d: END FILE marker outside a block" % number)
            else:
                blocks.append(current)
                current = None
        elif current is not None:
            current[1].append(raw)
        elif raw.strip():
            if raw.startswith("```") or raw.startswith("~~~"):
                findings.append("line %d: stray code fence outside a block" % number)
            else:
                findings.append("line %d: text outside a file block" % number)
    if current is not None:
        findings.append("unterminated block at end of input: %r" % current[0])
    if not blocks and not findings:
        findings.append("no file blocks found")
    if len(blocks) > _MAX_BLOCKS:
        findings.append("too many blocks: %d (max %d)" % (len(blocks), _MAX_BLOCKS))
        blocks = blocks[:_MAX_BLOCKS]
    seen = set()
    for path, body in blocks:
        path_findings = _check_path(path, stem, seen)
        findings.extend(path_findings)
        findings.extend(_check_source(path, "\n".join(body) + "\n"))
    return (not findings, findings)
