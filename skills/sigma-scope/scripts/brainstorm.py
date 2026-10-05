"""Target resolution for `/sigma-scope` (issue #916): given the raw invocation string, work out
which of the 4 recognized forms it is and resolve a concrete target to brainstorm about.

    1. inline free text        -- "how can I add a UI for editing config.json"
    2. a local markdown file   -- "check the ui.md file and help me plan it"
    3. a fuzzy issue reference -- "check the story issue where we added a config.json UI"
    4. a direct issue number   -- "#916"

THE RETURN SHAPE IS THE INTERFACE (schema `brainstorm-target/v1`). This module is the FIRST of six
sub-issues under the `sigma-scope` epic (#902); #920 (SKILL.md, the orchestration layer that
decides when to ask a clarifying question) depends directly on the shape below, and #917/#918 build
their own pieces against the same corpus this module already assembles. Documenting it precisely
here, once, is the contract:

    {
        "schema": "brainstorm-target/v1",
        "raw": <str>,                  # the exact invocation, unmodified
        "form": "text" | "file" | "issue_number" | "issue_reference" | "ambiguous" | "unresolved",
        "confident": <bool>,           # True: a caller may act on this result directly.
                                        # False: exactly one of "ambiguous"/"unresolved" -- a caller
                                        # MUST surface a clarifying question (ambiguous) or a legible
                                        # failure (unresolved) rather than silently guessing.
        "text": <str | None>,          # populated whenever the free-text reading is live:
                                        # form == "text" (the ONLY live reading) or
                                        # form == "ambiguous" (one of two live readings)
        "file": {"path": str, "content": str} | None,          # form == "file"
        "issue": {"ref": str, "title": str, "body": str,
                   "score": float | None} | None,              # form in ("issue_number",
                                                                 # "issue_reference"); score is None
                                                                 # for a direct-number fetch (nothing
                                                                 # was scored) and the winning cosine
                                                                 # similarity for a fuzzy resolution
        "candidates": [{"ref": str, "title": str, "score": float,
                         "evidence": [str, ...]}, ...],         # runners-up for form == "issue_reference"
                                                                 # (excludes the winner), or the weak
                                                                 # match(es) for form == "ambiguous"
                                                                 # (alongside `text`); always [] otherwise
        "degraded": [str, ...],        # fail-soft notices ("no_mirror", "no_goals", "file_not_found",
                                        # "issue_not_found", "corpus_error", ...) -- informational, never
                                        # itself a reason to distrust `confident`
        "error": <str | None>,         # set only when form == "unresolved": a human-legible reason
    }

DISAMBIGUATING FORM 1 FROM FORM 3 -- the design question this issue owns. "A UI for config.json"
could be a brand-new idea (form 1) or a paraphrase of an issue already filed (form 3); guessing
wrong either way is worse than asking. So this module never collapses that judgment call into a
single guess: it scores the invocation against the live issue corpus (see FUZZY MATCHING below) and
reports one of three outcomes --
    * no plausible match at all (best score is 0, or below `_AMBIGUOUS_FLOOR`)      -> "text", confident
    * a strong, singular match (best score >= dup_threshold)                        -> "issue_reference", confident
    * something in between -- real overlap, not enough to commit to                 -> "ambiguous", NOT
      confident, carrying BOTH `text` (the free-text reading) AND `candidates` (the existing-issue
      reading) so #920's clarifying-question step can ask rather than silently pick one.

FUZZY MATCHING REUSES `backlog_check.py`'s machinery rather than re-deriving TF-IDF: `_tokens`,
`_doc_tokens`, `_idf`, `_vector`, `_cosine`, `_build_corpus`, `_num`, `_DEFAULTS` are all loaded from
the sibling skill via the same `importlib.util.spec_from_file_location` pattern `backlog_check.py`
itself already uses to load ITS OWN siblings (`mirror`, `sources`, `scrub`) and to load
`sigma-velocity`'s `velocity.py` across a skill-directory boundary -- an established, tested
convention (see tests/test_import_boundary.py's note on this exact pattern), not a new one invented
here. #917 (board-wide dedup analysis) is expected to reuse the SAME primitives for a different
question (checking a resolved target against the WHOLE board) rather than a second, independent
cosine-similarity implementation.

`_build_corpus`'s docs are ALREADY SCRUBBED (title/body pass through `scrub.scrub()` inside
`backlog_check.py` before this module ever sees them) -- unlike `backlog_check`'s own `_finding`
objects, which deliberately omit title/body to stay secret-safe for a park/proceed DECISION that
never needed them, `issue`/`candidates` here DO carry the (already-scrubbed) title, because a later
caller needs enough surface to ask a human "did you mean issue #N: <title>?". A CONFIDENTLY resolved
`issue`'s `body` is included too (candidates' bodies are not, to keep the weak-match list small); a
live single-issue fetch (the direct-issue-number path when the issue isn't in the local mirror yet)
returns RAW, unscrubbed title/body, matching `sources.GitHubSource.fetch_title_body`'s own existing
convention -- this is the caller's own board content held in memory for its own reasoning, not
persisted or relayed elsewhere the way a mirror or a posted comment is.

FAIL-OPEN, LIKE THE REST OF THIS TOOLCHAIN: `resolve_target` never raises. A markdown file that's
referenced but doesn't exist, an issue number that can't be found anywhere (local corpus AND a live
fetch both miss), or a corpus that fails to build at all -- each degrades to a legible result
(`form == "unresolved"` with `error` set, or a bare `text` fallback) rather than a crash or a raised
exception reaching a caller that wasn't expecting one.

    python3 brainstorm.py <sdlc_dir> "<invocation text>"      # prints a brainstorm-target/v1 JSON pack
"""
import importlib.util
import json
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
_LOOP_SCRIPTS = _HERE.parent.parent / "sigma-loop" / "scripts"


def _load(name, base=_LOOP_SCRIPTS):
    spec = importlib.util.spec_from_file_location(name, base / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


backlog_check = _load("backlog_check")   # carries mirror/sources/scrub as its own already-loaded attrs

SCHEMA = "brainstorm-target/v1"

# Real overlap below this cosine score is treated as no signal at all (plain free text) rather than
# manufacturing an ambiguous reading out of one incidental shared word -- the corpus is already
# stopword-filtered and idf-weighted, so a genuinely coincidental shared term scores far below this.
_AMBIGUOUS_FLOOR = 0.15

# Directories a markdown-file search must never descend into: vendored/generated trees that can
# legitimately contain a same-named .md the user did not mean (a node_modules/.git copy of a
# dependency's own README.md, generated build output, ...).
_SKIP_DIR_NAMES = frozenset({"node_modules", "__pycache__", ".git", ".venv", "venv"})

# Form 4: a direct issue number is the WHOLE invocation (optionally wrapped in a few common verbs/
# nouns and trailing punctuation) -- "#916", "issue #916", "check #916.", "look at #916" all count;
# "check the story issue where we added ... about #916 config" does not (real text follows the
# number), so it falls through to the fuzzy path instead, exactly as intended.
_DIRECT_ISSUE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:check\s+|look\s+at\s+|go\s+to\s+|open\s+|see\s+)*"
    r"(?:issue\s+)?#(\d+)\s*[.!]?\s*$",
    re.IGNORECASE,
)

# Form 2: the invocation IS a file reference -- a `something.md` token (a bare filename, "ui.md", or
# a relative path, "notes/plan.md") with only _FILE_DIRECTIVE_WORDS around it: "notes/plan.md",
# "look at notes/plan.md please", "check the ui.md file and help me plan it". A filename merely
# MENTIONED inside an idea leaves other words behind and stays text/fuzzy, the same whole-invocation
# rule _DIRECT_ISSUE_RE gives form 4 -- a false match silently swaps the user's idea for the file's
# content. See _locate_markdown_file for how a bare filename gets located on disk.
_MD_REF_RE = re.compile(r"([\w][\w./-]*\.md)\b", re.IGNORECASE)
# ponytail: a closed word list, not a parser -- a reference worded any other way ("scope ui.md")
# resolves as text, which SKILL.md step 4 still confirms; add the word here if a real one recurs.
_FILE_DIRECTIVE_WORDS = frozenset(
    "please check look at go to open see the file and help me plan it".split())


def _result(raw, form, confident, text=None, file=None, issue=None, candidates=None,
            degraded=None, error=None):
    return {
        "schema": SCHEMA, "raw": raw, "form": form, "confident": bool(confident),
        "text": text, "file": file, "issue": issue,
        "candidates": list(candidates or []),
        "degraded": sorted(set(degraded or [])),
        "error": error,
    }


def _load_config(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_corpus_safe(sdlc_dir, config, degraded):
    """`_build_corpus` wrapped so a structurally broken `.sdlc` (e.g. `goals/` replaced by a plain
    file) degrades this module's result rather than propagating an exception -- the same fail-open
    contract `backlog_check.cross_check`'s own outer try/except gives its callers."""
    try:
        docs, corpus_degraded = backlog_check._build_corpus(sdlc_dir, config)
        degraded.extend(corpus_degraded)
        return docs
    except Exception:
        degraded.append("corpus_error")
        return []


# ---------------------------------------------------------------------------- form 4: direct issue number


def _detect_direct_issue_number(invocation):
    m = _DIRECT_ISSUE_RE.match(invocation or "")
    return m.group(1) if m else None


def _fetch_issue_live(number, config, run):
    gh = (config.get("discovery") or {}).get("github") or {}
    repo_args = ["--repo", gh["repo"]] if gh.get("repo") else []
    raw = (run or backlog_check.sources._run_gh)(
        ["issue", "view", str(number), *repo_args, "--json", "number,title,body"])
    data = json.loads(raw or "{}")
    if not isinstance(data, dict) or "number" not in data:
        return None
    return {"ref": str(data.get("number")), "title": data.get("title") or "",
            "body": data.get("body") or "", "score": None}


def _resolve_issue_number(raw, number, sdlc_dir, config, run, degraded):
    docs = _build_corpus_safe(sdlc_dir, config, degraded)
    doc = next((d for d in docs if d["ref"] == number), None)
    if doc is not None:
        issue = {"ref": doc["ref"], "title": doc.get("title", ""), "body": doc.get("body", ""),
                  "score": None}
        return _result(raw, "issue_number", True, issue=issue, degraded=degraded)
    try:
        issue = _fetch_issue_live(number, config, run)
    except Exception:
        issue = None
    if issue is not None:
        return _result(raw, "issue_number", True, issue=issue, degraded=degraded)
    degraded.append("issue_not_found")
    return _result(
        raw, "unresolved", False, degraded=degraded,
        error=f"issue #{number} could not be found (not in the local corpus, and a live fetch "
              "failed or is unavailable)",
    )


# ---------------------------------------------------------------------------- form 2: markdown file


def _detect_markdown_reference(invocation):
    m = _MD_REF_RE.search(invocation or "")
    if m is None:
        return None
    # the reference is the match's whole whitespace-free token, whatever wraps it ("`ui.md`",
    # "docs\ui.md"); every word outside that token must be a directive word
    head = re.sub(r"\S+\Z", "", invocation[:m.start()])
    tail = re.sub(r"\A\S+", "", invocation[m.end():])
    words = set(re.findall(r"\w+", (head + " " + tail).lower()))
    return m.group(1) if words <= _FILE_DIRECTIVE_WORDS else None


def _locate_markdown_file(ref, search_root):
    """A literal path (has a `/`) is tried directly under `search_root` first. Either way, a bare-
    filename fallback searches the tree for a file with that exact basename, skipping vendored/
    generated directories (`_SKIP_DIR_NAMES`) and any hidden directory. Deterministic when more than
    one match exists: shallowest path first, then lexical -- so the same invocation always resolves
    to the same file."""
    root = pathlib.Path(search_root)
    candidate = pathlib.Path(ref)
    direct = candidate if candidate.is_absolute() else root / candidate
    if direct.is_file():
        return direct
    if not root.is_dir():
        return None
    basename = candidate.name
    matches = []
    for p in root.rglob(basename):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in rel_parts[:-1]):
            continue
        matches.append(p)
    if not matches:
        return None
    matches.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return matches[0]


def _read_file(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve_markdown_file(raw, md_ref, sdlc_dir, search_root, degraded):
    root = search_root if search_root is not None else pathlib.Path(sdlc_dir).resolve().parent
    path = _locate_markdown_file(md_ref, root)
    if path is None:
        degraded.append("file_not_found")
        return _result(raw, "unresolved", False, degraded=degraded,
                        error=f"referenced file {md_ref!r} could not be located under {root}")
    content = _read_file(path)
    if content is None:
        degraded.append("file_unreadable")
        return _result(raw, "unresolved", False, degraded=degraded,
                        error=f"referenced file {path} exists but could not be read")
    return _result(raw, "file", True, file={"path": str(path), "content": content}, degraded=degraded)


# ---------------------------------------------------------------------------- forms 1 & 3: text / fuzzy issue


def _fuzzy_candidates(invocation, docs, top_k):
    """Score `invocation` against every doc in `docs` via `backlog_check`'s own TF-IDF cosine
    (identical primitives, not a re-derivation) and return the top `top_k`, highest score first.
    The invocation is tokenized like a TITLE (`_doc_tokens(invocation, "")`, the same 3x weighting
    `_build_corpus` gives every issue's title) since it is itself a short, descriptive paraphrase --
    not a body of prose."""
    if not docs:
        return []
    idf = backlog_check._idf(docs)
    qtokens = backlog_check._doc_tokens(invocation, "")
    if not qtokens:
        return []
    qvec = backlog_check._vector(qtokens, idf)
    scored = []
    for d in docs:
        s = backlog_check._cosine(qvec, backlog_check._vector(d["tokens"], idf))
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: (-x[0], backlog_check._ref_key(x[1]["ref"])))
    out = []
    for s, d in scored[:top_k]:
        ev = [backlog_check.scrub(t) for t in
              sorted(set(qtokens) & set(d["tokens"]), key=lambda t: (-idf.get(t, 0.0), t))[:6]]
        out.append({"ref": d["ref"], "title": d.get("title", ""), "score": round(float(s), 4),
                     "evidence": ev})
    return out


def _resolve_fuzzy_or_text(raw, sdlc_dir, config, top_k, degraded):
    docs = _build_corpus_safe(sdlc_dir, config, degraded)
    cfg = config.get("backlog_check") or {}
    dup_th = backlog_check._num(cfg, "dup_threshold", backlog_check._DEFAULTS["dup_threshold"])
    k = top_k if top_k is not None else backlog_check._num(cfg, "top_k", backlog_check._DEFAULTS["top_k"])
    candidates = _fuzzy_candidates(raw, docs, k)   # _fuzzy_candidates itself no-ops on an empty corpus
    if not candidates or candidates[0]["score"] < _AMBIGUOUS_FLOOR:
        return _result(raw, "text", True, text=raw, degraded=degraded)
    best = candidates[0]
    if best["score"] >= dup_th:
        winner_doc = next((d for d in docs if d["ref"] == best["ref"]), None)
        issue = {"ref": best["ref"], "title": best["title"],
                  "body": winner_doc.get("body", "") if winner_doc else "", "score": best["score"]}
        return _result(raw, "issue_reference", True, issue=issue, candidates=candidates[1:],
                        degraded=degraded)
    # real, but inconclusive, overlap: carry BOTH readings rather than guessing between them
    return _result(raw, "ambiguous", False, text=raw, candidates=candidates, degraded=degraded)


# ---------------------------------------------------------------------------- entry point


def resolve_target(invocation, sdlc_dir=".sdlc", config=None, run=None, search_root=None, top_k=None):
    """The single entry point: raw invocation string -> a `brainstorm-target/v1` dict (see the module
    docstring for the full shape). `sdlc_dir` locates the corpus (github mirror or local goal files,
    whichever `config`'s `discovery.source` selects -- same convention as `backlog_check.cross_check`).
    `run` is an injectable `gh` command runner (default `sources._run_gh`) used ONLY for the direct-
    issue-number live-fetch fallback, so tests stay hermetic. `search_root` overrides where a
    markdown-file reference is searched for (default: `sdlc_dir`'s parent directory, i.e. the repo
    root `.sdlc` normally sits in)."""
    raw = invocation or ""
    degraded = []
    config = config if config is not None else _load_config(sdlc_dir)
    if not isinstance(config, dict):
        # A malformed `config` (caller error, or a `.sdlc/config.json` that parsed to something
        # other than an object) must degrade this whole resolution, not just the corpus build below
        # -- every downstream helper assumes `config.get(...)` works. Normalizing ONCE here, rather
        # than guarding every individual `.get()` call downstream, keeps the fail-open contract in
        # one place instead of scattered across every function that happens to touch `config`.
        degraded.append("bad_config")
        config = {}

    number = _detect_direct_issue_number(raw)
    if number is not None:
        return _resolve_issue_number(raw, number, sdlc_dir, config, run, degraded)

    md_ref = _detect_markdown_reference(raw)
    if md_ref is not None:
        return _resolve_markdown_file(raw, md_ref, sdlc_dir, search_root, degraded)

    return _resolve_fuzzy_or_text(raw, sdlc_dir, config, top_k, degraded)


USAGE = "usage: brainstorm.py <sdlc_dir> <invocation text>"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) < 3:
        print(USAGE, file=__import__("sys").stderr)
        return 2
    print(json.dumps(resolve_target(argv[2], sdlc_dir=argv[1]), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    sys.exit(_load("timing_store").timed_main(main, sys.argv, "brainstorm"))
