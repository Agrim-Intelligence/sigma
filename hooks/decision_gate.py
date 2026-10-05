#!/usr/bin/env python3
"""PreToolUse decision gate — the one guardrail in this kit that is NOT a prompt.

Every other phase gate here is discipline a model is asked to follow, and a model can talk itself
past discipline. This one refuses: an edit that assigns a registered invariant a violating value is
DENIED before it happens, by a script that does not negotiate.

Reads a tool call on stdin. For Edit/Write/MultiEdit/NotebookEdit it matches the target file against
`.sdlc/decisions.json` and:

  * DENY   when the changed text assigns a protected param of an `invariant` decision a value that
           violates its typed constraint;
  * ASK    on the same violation of a `recipe` decision, on any edit inside a decision declaring
           `caution_on_touch`, and on any edit to the registry itself (changing an invariant is a
           supersession, never a silent rewrite);
  * ALLOW  (silent) otherwise.

Matching is SCOPED, which is what keeps it from crying wolf: a param name is only checked inside
files matching its OWN decision's protected_paths, only in assignment shape (`name = <literal>` /
`name: <literal>`), with comments stripped. Prose mentions and same-named tokens elsewhere never
trip it, and a new value that still SATISFIES the constraint is allowed.

OFF until an ADOPTED repo (`.sdlc/config.json` present) authors `.sdlc/decisions.json` — no registry,
no behavior, and no hook without the adoption marker. `init` writes the skeleton and refuses otherwise. Set
`gates.decision_gate.enabled: false` in `.sdlc/config.json` to disable without deleting the registry
(useful mid-refactor).

Fail-OPEN everywhere: any internal error allows the call. A gate that wedges you on its own bug is
worse than a missed check — `decision_gate.py check` is the backstop that catches what the hook let
through. Zero-dep: the registry is JSON, not YAML, because this kit takes no dependencies.
"""
import ast
import fnmatch
import json
import math
import os
import re
import sys
from pathlib import Path

REGISTRY_REL = ".sdlc/decisions.json"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
OPS = ("eq", "ge", "le", "in")

#: #139 site f. No goal exists in this hook's call chain — it only ever sees a bare PreToolUse
#: payload on stdin, no goal id anywhere in it — so every `gate{decision}` event this module emits
#: carries this fixed sentinel instead. Visually distinct from every real goal identity this kit
#: uses (a `.md` path or a bare issue number), and — since nothing downstream (ledger.py's
#: open_claims/handoff_key/etc.) reads the `goal` field on the EVENTS stream — carries no risk of
#: colliding with real lease/handoff logic, which only ever looks at the ENTRIES stream.
_NO_GOAL = "(decision-gate)"

#: A literal on the right-hand side of an assignment. Anything else (an expression, a name, a call)
#: is deliberately NOT matched — the gate only judges values it can actually read.
_LITERAL = re.compile(r"(-?\d+\.\d+|-?\d+|True|False|true|false|\"[^\"]*\"|'[^']*')")

#: #272: the id-format contract `sigma-decide` documents and `validate()` enforces at authoring
#: time — no internal whitespace, no colons. Both characters break the structured `decision_id`
#: event field's own downstream contract (a single bounded token, safe to `" | ".join()` across
#: multiple violating decisions and unambiguous to split back apart) even though the field itself
#: no longer NEEDS to parse an id out of prose the way the old free-text read side did.
_BAD_ID_CHARS = re.compile(r"[\s:]")


def project_dir():
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and (Path(env) / REGISTRY_REL).exists():
        return Path(env)
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / REGISTRY_REL).exists():
            return parent
    return None


def load_registry(root):
    try:
        return json.loads((Path(root) / REGISTRY_REL).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def enabled(root):
    """Within an adopted repo, authoring a registry is the opt-in — a config flag you had to discover
    would make the feature silently do nothing (hook mode separately needs `.sdlc/config.json`). `enabled: false` turns it off without deleting the file."""
    try:
        cfg = json.loads((Path(root) / ".sdlc" / "config.json").read_text(encoding="utf-8"))
        gate = (cfg.get("gates") or {}).get("decision_gate") or {}
        return gate.get("enabled") is not False
    except Exception:
        return True


def matches(rel, patterns):
    """Glob match with the `**` semantics people actually expect.

    `fnmatch` has no concept of path segments, so `src/**/*.py` fails to match `src/a.py` — the
    pattern demands a second slash. Every glob dialect a user has met (gitignore, pathlib, shell
    globstar) treats `**` as *zero* or more directories, so a rule written that way would silently
    protect the nested files and not the top-level ones. Trying the collapsed form covers the
    zero-directory case."""
    for pat in patterns or []:
        if not isinstance(pat, str):
            continue
        collapsed = pat.replace("/**/", "/")
        if collapsed.startswith("**/"):
            collapsed = collapsed[3:]
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, collapsed):
            return True
    return False


def _rel(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except Exception:
        return Path(path).as_posix()


def _contained_glob(root, pat):
    """`root.glob(pat)` filtered to results that actually resolve inside `root`.

    Glob patterns are not sandboxed to the base path they're joined against -- confirmed by
    execution: `Path(root).glob("../secret.json")` from a subdirectory escapes `root` outright, and
    `_rel()` falls back to a raw path string when `.relative_to()` fails, so an unfiltered result
    would also disclose the escaping path. A `protected_paths` entry containing `../` must not let
    either `check()` or `validate()` read (or name) a file outside the intended project root.
    Anything that fails to resolve under `root` is silently dropped, exactly as if the pattern had
    matched nothing there -- the two callers already treat "no files matched" as an unremarkable,
    in-scope case (see e.g. `validate()`'s "protected file does not exist yet" skip)."""
    root_resolved = Path(root).resolve()
    for path in root.glob(pat):
        try:
            path.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        yield path


def _changed_text(tool_name, ti):
    if tool_name in ("Write", "NotebookEdit"):
        return ti.get("content") or ti.get("new_source") or ""
    if tool_name == "Edit":
        return ti.get("new_string") or ""
    if tool_name == "MultiEdit":
        return "\n".join((e.get("new_string") or "") for e in ti.get("edits", []))
    return ""


def _norm(tok):
    if tok in ("true", "false"):
        tok = tok.capitalize()
    return ast.literal_eval(tok)


def satisfies(op, actual, expected):
    """True when `actual` still meets the constraint. Fail-open on any comparison error — a gate
    that denies because it could not compare is worse than one that misses."""
    try:
        if op == "eq":
            if isinstance(expected, bool) or isinstance(actual, bool):
                return actual == expected
            if isinstance(expected, float) or isinstance(actual, float):
                return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-12)
            return actual == expected
        if op == "ge":
            return actual >= expected
        if op == "le":
            return actual <= expected
        if op == "in":
            return actual in expected
    except Exception:
        return True
    return True


def _in_string(code, idx):
    """True when position `idx` sits inside a quoted span.

    Without this, `doc = "set timeout: 120 here"` reads as an assignment of 120 and DENIES the edit.
    A false deny costs far more than a miss: it is the thing that teaches people to click through
    the gate, and a gate that gets clicked through protects nothing. Naive (no escape handling),
    which keeps it erring toward allow."""
    quote = None
    for ch in code[:idx]:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
    return quote is not None


def _value_span(code, start):
    """The text of the assigned value, bounded at its OWN end -- not wherever `_LITERAL` next
    happens to notice a literal-shaped token -- so a check against a single value never reads
    past it into an arithmetic expression's later operand or a JSON collection's contents.

    Stops at the first unescaped `,`, `}`, `]`, or `;` (JSON/YAML-flavored config never use a
    `;` at all, so it is a no-op there; Python's is the dialect that actually needs it -- `;` is
    ITS statement separator, the same role a newline already plays, so a value must stop there
    too or a second statement on the same line -- `max_iterations = 999; other_call()` -- gets
    read as part of the first value and the whole span stops being literal-shaped, silently
    abstaining on a real violation instead of catching it), or at end of line. Quote-aware so a
    comma/brace/bracket/semicolon INSIDE a string value doesn't end the span early; naive on
    bracket nesting past the first close, same trade-off `_in_string` already documents (no
    escape handling) -- it only ever makes the span SHORTER than the true value, which still
    can't accidentally reassemble into a false literal match, so it errs toward abstention,
    never a false deny/allow."""
    idx, quote = start, None
    while idx < len(code):
        ch = code[idx]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in ",}];":
            break
        idx += 1
    return code[start:idx]


def _assignment_matches(code, name):
    """Yield the end index of each place `name` appears in assignment/key shape in one line of
    `code` -- `name = <value>`, `name: <value>`, or JSON's `"name": <value>` -- skipping a
    same-named mention that is prose (a string literal that merely contains this text), and
    CONTINUING past a rejected mention instead of giving up on the rest of the line, so a real
    assignment later on the same line as an irrelevant same-named mention is still found.

    An optional quote is allowed directly after `name` (no whitespace before it -- that is how
    every dialect this gate reads actually places one): JSON/YAML's `"name":` and a quoted Python
    dict key both close the quote right against the name, never with a gap.
    """
    pattern = re.compile(rf"(?<![\w.]){re.escape(name)}([\"'])?\s*[=:]\s*")
    for m in pattern.finditer(code):
        quote = m.group(1)
        # `name` sitting inside its OWN tight quote pair (JSON's `"name":`) always reads as
        # "inside a string" at name's own position -- that pair IS the key, not prose. Check one
        # position earlier instead: is the pair ITSELF nested inside some OTHER, already-open
        # string? That is the only case still worth excluding.
        if quote and m.start() > 0 and code[m.start() - 1] == quote:
            check_idx = m.start() - 1
        else:
            check_idx = m.start()
        if not _in_string(code, check_idx):
            yield m.end()


def violations(changed, protected_params):
    """Assignments in `changed` that break a constraint: [(name, actual_value, param_spec)]."""
    found = []
    for line in changed.splitlines():
        code = line.split("#", 1)[0]          # naive comment strip; errs toward NOT blocking
        for p in protected_params or []:
            name = p.get("name")
            if not name or p.get("op") not in OPS:
                continue
            for end in _assignment_matches(code, name):
                span = _value_span(code, end).strip()
                # fullmatch, not search: the value's ENTIRE own span must be one literal token, or
                # this is an expression / collection the gate can't judge (#1783) -- an unanchored
                # search would grab just a leading or embedded literal-shaped substring and treat
                # THAT as the whole value, silently wrong rather than correctly abstaining.
                lit = _LITERAL.fullmatch(span)
                if not lit:
                    continue                       # not a literal — nothing this gate can judge
                try:
                    actual = _norm(lit.group(1))
                except Exception:
                    continue
                if not satisfies(p["op"], actual, p.get("value")):
                    found.append((name, actual, p))
    return found


def evaluate(tool_name, ti, registry, root):
    """Pure decision logic — returns (permissionDecision, reason|None, decision_id|None). No I/O, so
    it is testable without a hook harness or a filesystem.

    #272: `decision_id` is the DENIED decision's own registry `id`, returned as a real value —
    never reconstructed later by parsing `reason`. It is populated only on `deny` (the only verdict
    `_emit_decision_event` ever instruments — see that function's own docstring for why `ask`/
    `allow` are not); every other return path carries `None`. When more than one active invariant
    is violated by the same edit, the ids are joined with the same ` | ` separator `reason` already
    uses for its own multi-violation messages, de-duplicated, in violation order — the documented id
    format (`sigma-decide`: no internal whitespace, no colons) is exactly what keeps that join
    unambiguous to a reader who wants to split it back apart."""
    if tool_name not in EDIT_TOOLS:
        return "allow", None, None
    fp = ti.get("file_path") or ti.get("filePath") or ti.get("notebook_path")
    if not fp:
        return "allow", None, None
    rel = _rel(fp, root)

    # The registry protects itself. Changing a recorded invariant is a supersession — a decision the
    # user makes deliberately — never a silent edit by the agent that is about to be bound by it.
    if rel == REGISTRY_REL:
        return "ask", ("This edits the decision registry itself. Changing a recorded decision is a "
                       "supersession: add a NEW entry with `supersedes` set and flip the old one to "
                       "`status: superseded` — never rewrite or delete in place. Confirm this is an "
                       "approved change."), None

    # Defensive on shape as well as on errors: `evaluate` is the pure entry point and is called
    # directly (by `check`, by tests, by anything embedding this), so it cannot rely on main()'s
    # catch-all to keep a hand-edited registry from raising.
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        return "allow", None, None
    active = [d for d in decisions
              if isinstance(d, dict) and d.get("status", "active") == "active"
              and matches(rel, d.get("protected_paths"))]
    if not active:
        return "allow", None, None

    changed = _changed_text(tool_name, ti)
    deny, ask = [], []
    deny_ids = []
    for d in active:
        ident = f"{d.get('id', '?')} {d.get('title', '')}".strip()
        if d.get("caution_on_touch"):
            # Guards a PATH with no params to check. Without this a path-only decision falls through
            # to allow SILENTLY, which makes it impossible to guard code that is dangerous to touch
            # at all (anything that spends money, deploys, or migrates).
            ask.append(f"{ident}: {d.get('statement', '')}".strip())
        for name, actual, p in violations(changed, d.get("protected_params")):
            msg = (f"{ident}: {name}={actual!r} violates {p['op']} {p.get('value')!r}. "
                   f"{d.get('statement', '')}").strip()
            why = d.get("rationale")
            if why:
                msg += f" (Why: {why})"
            if d.get("class") == "invariant":
                deny.append(msg)
                if d.get("id"):
                    deny_ids.append(str(d["id"]))
            else:
                ask.append(msg)

    if deny:
        decision_id = " | ".join(dict.fromkeys(deny_ids)) or None
        return "deny", (" | ".join(deny) + " — This is a recorded INVARIANT. If it genuinely needs "
                        f"to change, supersede it in {REGISTRY_REL} first; that is a decision, not "
                        "an edit."), decision_id
    if ask:
        return "ask", " | ".join(ask), None
    return "allow", None, None


# --- registry audit: the backstop for whatever the hook let through -----------------------------

def check(root):
    """Scan files already on disk for violations of the active registry. Answers the first question
    anyone asks after authoring one — 'does my code actually satisfy this?' — which the hook cannot,
    since it only ever sees edits going forward. Returns [(rel_path, decision_id, name, value)]."""
    root = Path(root)
    registry = load_registry(root)
    out = []
    for d in registry.get("decisions", []) or []:
        if not isinstance(d, dict) or d.get("status", "active") != "active" \
                or not d.get("protected_params"):
            continue
        # pathlib.glob DOES understand `**` as zero-or-more, so patterns behave here as written.
        for pat in d.get("protected_paths") or []:
            for path in sorted(_contained_glob(root, pat)):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for name, actual, p in violations(text, d["protected_params"]):
                    out.append((_rel(str(path), root), d.get("id", "?"), name, actual))
    return out


def _appears_in_assignment_shape(text, name):
    """True if `name` appears anywhere in `text` in assignment/key shape -- reusing the exact same
    per-line, comment-stripped, quote-aware scan `violations()` uses. Used by `validate()` to ask
    whether a protected_params entry can EVER be seen, regardless of whether the value present
    right now happens to violate anything."""
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        for _ in _assignment_matches(code, name):
            return True
    return False


def validate(registry, root=None):
    """Structural problems that would make a decision silently unenforceable — the failure mode of a
    registry is being quietly wrong, not loudly broken."""
    problems = []
    seen = set()
    decisions = registry.get("decisions")
    if not isinstance(decisions, list):
        return ["`decisions` must be a list"]
    root = Path(root) if root is not None else None
    for i, d in enumerate(decisions):
        ident = d.get("id") or f"#{i}"
        if not d.get("id"):
            problems.append(f"{ident}: missing `id`")
        elif d["id"] in seen:
            problems.append(f"{ident}: duplicate `id`")
        elif _BAD_ID_CHARS.search(str(d["id"])):
            # #272: caught at authoring time, not at read time. A whitespace- or colon-bearing id
            # is silently mis-recoverable from the OLD free-text `why` field (truncates to its
            # first word, or anchors at the wrong colon) — `validate` is the one place a human
            # authoring a decision sees this BEFORE it is ever attached to a real denial.
            #
            # str()-coerced: nothing in the registry format or `load_registry()` enforces `id` to
            # be a JSON string (`"id": 42` is legal JSON), and `evaluate()` already treats ids as
            # str-coercible (`deny_ids.append(str(d["id"]))`). A bare number has no whitespace/colon
            # so it validates cleanly either way, but `_BAD_ID_CHARS.search()` itself requires
            # str/bytes and raises TypeError on a raw int/float/bool/None/list/dict id -- that must
            # surface as a reported PROBLEM, never an unhandled crash of the CLI's `validate` path.
            problems.append(f"{ident}: `id` {d['id']!r} must not contain whitespace or a colon "
                            "(ids are carried as a single bounded token in the decision_id event "
                            "field)")
        seen.add(d.get("id"))
        if d.get("class") not in ("invariant", "recipe"):
            problems.append(f"{ident}: `class` must be 'invariant' or 'recipe'")
        if not d.get("protected_paths"):
            problems.append(f"{ident}: no `protected_paths` — it can never match an edit")
        if not d.get("protected_params") and not d.get("caution_on_touch"):
            problems.append(f"{ident}: no `protected_params` and no `caution_on_touch` — "
                            f"it guards a path but checks nothing, so it will never fire")
        for p in d.get("protected_params") or []:
            if p.get("op") not in OPS:
                problems.append(f"{ident}: param {p.get('name')!r} has op {p.get('op')!r}, "
                                f"expected one of {', '.join(OPS)}")
        # A registry entry can be well-formed (valid paths, valid op) and still be silently
        # unenforceable if the param's name never appears in assignment shape anywhere in the
        # file(s) it protects -- the exact failure mode #1532 found live in this repo's own
        # registry. Only checked when `root` is given (validate() stays usable with no filesystem
        # at all, matching every existing call site) and only against protected_paths that already
        # resolve to a real file -- a decision authored before its target file exists is a
        # different, not-yet-in-scope case, not a defect.
        if root is not None and d.get("protected_params"):
            files = []
            for pat in d.get("protected_paths") or []:
                if isinstance(pat, str):
                    files.extend(f for f in sorted(_contained_glob(root, pat)) if f.is_file())
            # Two protected_paths patterns can resolve to the same file (e.g. `["*.json",
            # "cfg.json"]`) -- de-dupe so the "never appears in" message below doesn't name one
            # file twice (code review, #1532).
            files = list(dict.fromkeys(files))
            if files:
                texts = []
                for f in files:
                    try:
                        texts.append(f.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError):
                        continue
                for p in d.get("protected_params") or []:
                    name = p.get("name")
                    if (name and p.get("op") in OPS and texts
                            and not any(_appears_in_assignment_shape(t, name) for t in texts)):
                        problems.append(
                            f"{ident}: param {name!r} never appears in assignment shape in "
                            f"{', '.join(sorted(_rel(str(f), root) for f in files))} — it can "
                            f"never fire")
    return problems


def _emit_decision_event(data, root, reason, decision_id):
    """#139 site f: `gate{decision, verdict:"block"}`, on `deny` ONLY — `evaluate()` runs on every
    Edit/Write/MultiEdit/NotebookEdit tool call once a repo authors a registry, and `ask`/`allow`
    are the overwhelming majority of outcomes (silence is the documented, desired steady state);
    instrumenting those would flood the events stream with near-zero-value noise.

    Lazily imports ledger.py (a sibling of loop.py/work.py/slices.py/pipeline.py in
    skills/sigma-loop/scripts/ — a DIFFERENT directory from this hook, not shipped as a package) so
    a checkout where those scripts are absent or broken can never break this otherwise
    self-contained module — `project_dir()`/`load_registry()`/`enabled()` all already swallow their
    own errors for exactly this reason. The caller (main()'s hook branch) wraps this ENTIRE call in
    its own local try/except, ahead of the always-reached `_emit(decision, reason)` — see that call
    site for why: this is the one journal call in the whole plan where a raise, left unguarded,
    could silently turn a real `deny` into what the harness reads as an `allow`.

    `why` carries the edited file's repo-relative path, then `evaluate()`'s full combined `reason`
    string (built from every violating decision's `ident` = f"{d.get('id')} {d.get('title')}", plus
    the violated param/value/statement/rationale, joined with " | " — see evaluate() ~:214/:221-226).
    Re-parsing it to extract just the `ident` would couple this to evaluate()'s internal message
    format for no reader benefit; the full text is what a human debugging a denial actually wants,
    and the file path (not otherwise present in `reason`) says WHERE it fired.

    #272: `decision_id` is `evaluate()`'s OWN structured id, passed straight through — never
    re-derived from `why` here. That is the whole point: `why`'s free text stays exactly as it was
    (still the human-readable message), and a reader that wants the id no longer has to regex it out
    of prose, which is unrecoverable in two ways a contract alone cannot fully close (an id with
    internal whitespace truncates to its first word; a path component containing a colon anchors the
    read at the wrong colon and returns a silently-wrong non-NULL id). `ledger.append()` classifies
    `gate.decision_id` as a bounded-id field (`EVENT_BOUNDED_ID_FIELDS`), so it gets its own
    BOUNDED_ID_CAP scrub+cap — never `why`'s FREE_TEXT_CAP=200, which a sufficiently deep `rel`
    prefix could otherwise push the id itself past."""
    import importlib.util
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("filePath") or ti.get("notebook_path")
    rel = _rel(fp, root) if fp else "?"
    spec = importlib.util.spec_from_file_location(
        "ledger", Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "ledger.py")
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    ledger.safe_append(str(Path(root) / ".sdlc"), "gate", _NO_GOAL, stream=ledger.EVENTS,
                       gate="decision", verdict="block", why=f"{rel}: {reason}",
                       decision_id=decision_id)


def _adopted():
    """#2737: hook mode acts only inside an adopted repository — `.sdlc/config.json` at or above
    `$CLAUDE_PROJECT_DIR`/cwd, the one definition in gate_state.py. Accepted behaviour change: a
    repo holding only `decisions.json` (no config.json) is no longer gated. Imported lazily because
    the tests load this file via importlib with `hooks/` off sys.path; an import failure reads as
    not adopted, i.e. allow — this file's own fail-open contract. `check`/`validate` are untouched."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate_state
        return gate_state.adopted_root() is not None
    except Exception:
        return False


def _emit(decision, reason):
    if decision == "allow":
        sys.exit(0)
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision}}
    if reason:
        out["hookSpecificOutput"]["permissionDecisionReason"] = reason
    print(json.dumps(out))
    sys.exit(0)


def init(root):
    """`init [root]`: write the skeleton registry, but only inside an adopted repository (#622).

    The hook is inert without `.sdlc/config.json`, so writing a registry elsewhere would hand the
    author a gate that is off. Refuses (exit 2, nothing written) when `gate_state.adopted_root()` finds
    none, naming the adoption command. Create-only via open(..., "x"): an existing registry is never
    touched. `adopted_root` walks upward and returns a realpath, so the path written is printed."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gate_state
    found = gate_state.adopted_root(str(root))
    if found is None:
        print("decision_gate.py: refusing to write a registry: this repository is not adopted "
              "(no .sdlc/config.json). Run /sigma-init first; the decision gate does nothing without it.",
              file=sys.stderr)
        return 2
    target = Path(found) / REGISTRY_REL
    try:
        with open(target, "x", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": 1, "decisions": []}, indent=2) + "\n")
    except FileExistsError:
        print(f"decision_gate.py: {target} already exists; not overwriting it", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"decision_gate.py: could not write {target}: {e}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
    return 0


def _note_if_unadopted(root):
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import gate_state
        if gate_state.adopted_root(str(root)) is None:
            print("  [NOTE] not an adopted repository (no .sdlc/config.json): the edit-time hook is "
                  "inert until /sigma-init; `check` and `validate` still work.")
    except Exception:
        pass


def main(argv):
    if len(argv) >= 2 and argv[1] == "init":
        return init(argv[2] if len(argv) > 2 else ".")
    if len(argv) >= 2 and argv[1] in ("check", "validate"):
        root = argv[2] if len(argv) > 2 else "."
        if not (Path(root) / REGISTRY_REL).exists():
            print(f"no registry at {REGISTRY_REL} — the gate is off (see /sigma-decide)")
            return 0
        if argv[1] == "validate":
            problems = validate(load_registry(root), root)
            for p in problems:
                print(f"  [PROBLEM] {p}")
            print(f"decision registry: {'valid' if not problems else str(len(problems)) + ' problem(s)'}")
            _note_if_unadopted(root)
            return 1 if problems else 0
        found = check(root)
        for rel, ident, name, actual in found:
            print(f"  [VIOLATION] {rel}: {name}={actual!r} breaks {ident}")
        print(f"decision registry: {len(found)} violation(s) in code already on disk"
              + ("." if not found else " — the hook only guards NEW edits; these predate it."))
        return 1 if found else 0

    # hook mode: a tool call on stdin
    try:
        if not _adopted():
            _emit("allow", None)     # #2737: inert outside an adopted repo (config.json, not just a registry)
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        root = project_dir()
        if root is None or not enabled(root):
            _emit("allow", None)
            return 0
        decision, reason, decision_id = evaluate(data.get("tool_name", ""), data.get("tool_input") or {},
                                                 load_registry(root), root)
        # #139 site f. The journal call is wrapped in its OWN local try/except, never merged into
        # the outer `except Exception: sys.exit(0)` below — that outer handler prints nothing, byte-
        # identical to a genuine allow, so a raise from the emit call left unguarded here would
        # silently convert a real deny into what the harness reads as an allow. `_emit(...)` always
        # runs regardless of whether the emit above succeeded.
        if decision == "deny":
            try:
                _emit_decision_event(data, root, reason, decision_id)
            except Exception:           # noqa: BLE001 - a journal write must never turn a deny into a silent allow
                pass
        _emit(decision, reason)
    except Exception:
        sys.exit(0)      # fail open: never block real work because the gate itself errored
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
