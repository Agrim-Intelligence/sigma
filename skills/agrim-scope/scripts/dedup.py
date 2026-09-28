"""agrim-scope's board-dedup check (#917): "does ANYTHING on the board already cover this rough
idea" search, run at BRAINSTORM time -- BEFORE any issue exists for the idea, often against a
sentence or two of free text, a pasted file's content, or an already-filed issue's own text.

This is a DIFFERENT calling shape from `backlog_check.cross_check()` (agrim-loop/scripts, reused
directly here rather than reimplemented): `cross_check` asks "is THIS SPECIFIC, already-filed goal a
duplicate of another specific goal" at PICK time -- both sides of the comparison are full, structured
issue bodies already in the corpus (title-weighted, `goal_ref` must resolve to a real doc or the
whole check degrades). `find_candidates` below asks the earlier, softer question: "does anything on
the live board already cover THIS, roughly", where the query text is never itself a corpus member and
is almost always shorter and less structured than a real issue body.

REUSED, NOT REIMPLEMENTED (verified by reading backlog_check.py in full before writing this): the
TF-IDF/cosine primitives (`_tokens` via `_doc_tokens`, `_idf`, `_vector`, `_cosine`, `_shared_terms`,
`_ref_key`), the corpus builder (`_build_corpus` -- github mirror or local goal files, exactly like
`cross_check`'s own corpus), the recently-closed window (`_closed_window_days` + `_within_window`),
and the numeric-config coercion helper (`_num`). NOT reused: `_finding`'s `confident` flag and
`decide()` -- those encode an ACTION verdict (park vs proceed), which is explicitly out of scope
here. Per the issue's own design lean, this module only SURFACES ranked candidates; whether/how to
act on them (ask the user, block, proceed anyway) is the SKILL.md orchestration layer's job (#920).

THRESHOLD, why it differs from `cross_check`'s `dup_threshold` (0.72): that number is tuned for two
FULL, TITLE-WEIGHTED issue bodies compared to each other. Measured against realistic fixtures here
(see test_dedup.py), a short, paraphrased brainstorm-idea sentence compared to a real filed issue's
title+body scores meaningfully LOWER even for an unmistakable duplicate -- 0.72 would silently miss
it. Reusing 0.72 as-is would therefore trade a worse failure mode (a duplicate gets silently planned
a second time) for a better-sounding one (fewer false positives); the issue explicitly asks for the
opposite trade. Two thresholds, empirically calibrated against the same fixtures:
  - `_DEFAULT_THRESHOLD` (0.18) -- the surfacing FLOOR. Below it, a candidate isn't mentioned at all;
    measured "genuinely unrelated" pairs (shared only a common, low-idf word) scored well under this.
  - `_DEFAULT_DUPLICATE_THRESHOLD` (0.45) -- at/above this, a candidate is labeled `"duplicate"`
    rather than `"related"`. A candidate that clears the floor but not this line is still SURFACED,
    not dropped -- a related-but-distinct piece of work is exactly the kind of thing the issue says a
    human may want planned alongside something similar, so silently filtering it out would just move
    the false-negative risk this whole module exists to shrink. `strength` is a descriptive banding
    of the score, not a verdict; the caller decides what to do with either label.
Both are config-overridable (`dedup.threshold` / `dedup.duplicate_threshold` / `dedup.top_k` in
config.json) for a project that finds the calibration off for its own corpus.

NOT wired in: `cross_check`'s opt-in dense/embedding channel (`backlog_check._dense_channel`). That
channel's on-disk cache is keyed by (embedder identity, a corpus doc's own content) and its identity
check assumes the QUERY side is itself a stable corpus member with a `ref` -- neither is true for a
not-yet-filed rough idea. Lexical-only is the deliberate scope here; a future slice could add a
text-keyed variant if lexical recall proves insufficient in practice.

SECRET-SAFE: the query text is free-form user input, never pre-scrubbed by anything upstream (unlike
every corpus doc, which `_build_corpus` already scrubs) -- so it is scrubbed here before tokenizing,
the same treatment `_build_corpus` already gives local-mode goal bodies. Findings carry issue refs +
shared, scrubbed index TERMS only -- never the raw query text or a corpus title/body. FAIL-OPEN: no
corpus / bad sdlc_dir / any error -> an empty candidate list with a machine-readable `degraded[]`,
never a raise.

    python3 dedup.py <sdlc_dir> <text>        # free text on the argv
    python3 dedup.py <sdlc_dir> @<path>       # or a file's content, read from disk
"""
import importlib.util, json, pathlib

_HERE = pathlib.Path(__file__).resolve().parent
_BACKLOG_CHECK_PATH = _HERE.parent.parent / "agrim-loop" / "scripts" / "backlog_check.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


backlog_check = _load(_BACKLOG_CHECK_PATH, "backlog_check")
scrub = _load(_BACKLOG_CHECK_PATH.parent / "scrub.py", "scrub").scrub

SCHEMA = "brainstorm-dedup/v1"
_DEFAULT_THRESHOLD = 0.18            # surfacing floor -- see module docstring for the calibration
_DEFAULT_DUPLICATE_THRESHOLD = 0.45  # at/above -> "duplicate" label instead of "related"
_DEFAULT_TOP_K = 8                   # matches backlog_check's own top_k default


def _config_section(config):
    return (config or {}).get("dedup") or {}


def _candidate(ref, score, state, strength, evidence, source, closed_at=None):
    c = {"ref": str(ref), "score": round(float(score), 4), "state": state, "strength": strength,
         "source": source, "evidence": list(evidence)[:8]}
    if closed_at:
        c["closed_at"] = closed_at
    return c


def _pack(candidates, degraded):
    return {"schema": SCHEMA, "candidates": list(candidates), "degraded": sorted(set(degraded))}


def find_candidates(sdlc_dir, text, config=None, title="", exclude_refs=None, now=None, run=None,
                     velocity_measure=None, records=None):
    """The whole check. `text` is the resolved target's own text (free-form; a `title` may optionally
    be supplied separately for the same title-weighting `_doc_tokens` already gives real issues).
    `exclude_refs` drops specific corpus refs from consideration -- for when `text` IS an existing
    issue's own body (one of the 4 invocation forms #916 resolves), so that issue doesn't trivially
    "duplicate" itself.

    `records` (#1204, optional): a caller-supplied corpus -- board-mirror-shaped records, same shape
    `mirror.build_records`/`mirror.fetch_dependency_records` produce -- that BYPASSES
    `backlog_check._build_corpus` (and so never touches the cached, possibly narrower
    `board-mirror.ndjson` on disk) entirely. `None` (the default) preserves the exact prior
    behavior -- the normal `_build_corpus` path every existing caller (agrim-scope's brainstorm)
    already relies on. An explicit `[]` is a REAL empty corpus, not "unset" -- it degrades to
    `no_corpus` like any other empty corpus, it does not fall back to `_build_corpus`. handoff.py's
    file-time duplicate search is the first caller to pass this: its corpus
    (`mirror.fetch_dependency_records`) is deliberately NOT assignee-restricted and covers more than
    one label, unlike the cached mirror `_build_corpus` reads by default.

    Returns a pack: {schema, candidates: [...], degraded: [...]}. `candidates` is ranked (score desc,
    `_ref_key` tiebreak) and capped at `top_k` -- empty when nothing clears the surfacing floor.
    FAIL-OPEN: any error -> {candidates: [], degraded: ["error"]}, never a raise (this sits on a
    conversational, human-facing hot path -- the same contract `cross_check` gives the loop's own
    pick hot-path)."""
    text = text or ""
    if not text.strip():
        return _pack([], ["empty_text"])
    exclude = {str(r) for r in (exclude_refs or [])}
    try:
        config = config if config is not None else backlog_check._load_config(sdlc_dir)
        cfg = _config_section(config)
        threshold = backlog_check._num(cfg, "threshold", _DEFAULT_THRESHOLD)
        dup_threshold = backlog_check._num(cfg, "duplicate_threshold", _DEFAULT_DUPLICATE_THRESHOLD)
        top_k = int(backlog_check._num(cfg, "top_k", _DEFAULT_TOP_K))

        if records is not None:
            docs, degraded = backlog_check._docs_from_records(records), []
        else:
            docs, degraded = backlog_check._build_corpus(sdlc_dir, config)
        if not docs:
            return _pack([], degraded or ["no_corpus"])

        qtok = backlog_check._doc_tokens(title, scrub(text))
        if not qtok:
            return _pack([], degraded + ["no_tokens"])

        idf = backlog_check._idf(docs)
        qvec = backlog_check._vector(qtok, idf)
        qterms = set(qtok)
        qdoc = {"tokens": qtok}
        window = backlog_check._closed_window_days(config, run=run, velocity_measure=velocity_measure)

        scored = []
        for d in docs:
            ref = d["ref"]
            if ref in exclude:
                continue
            recent_close = d["completed"] and backlog_check._within_window(d, window, now)
            if not (d["open"] or recent_close):
                continue                        # neither a live candidate nor a recently-closed one
            if not (qterms & set(d["tokens"])):
                continue                        # cheap candidate-gen prefilter, mirrors cross_check
            s = backlog_check._cosine(qvec, backlog_check._vector(d["tokens"], idf))
            if s >= threshold:
                scored.append((s, d))
        scored.sort(key=lambda x: (-x[0], backlog_check._ref_key(x[1]["ref"])))

        candidates = []
        for s, d in scored[:top_k]:
            ev = backlog_check._shared_terms(qdoc, d, idf)
            strength = "duplicate" if s >= dup_threshold else "related"
            candidates.append(_candidate(d["ref"], s, "open" if d["open"] else "closed", strength,
                                          ev, d["source"], d.get("closed_at")))
        return _pack(candidates, degraded)
    except Exception:
        return _pack([], ["error"])


USAGE = "usage: dedup.py <sdlc_dir> <text|@file>"


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    import sys
    if len(argv) < 3:
        print(USAGE, file=sys.stderr)
        return 2
    arg = argv[2]
    text = pathlib.Path(arg[1:]).read_text(encoding="utf-8") if arg.startswith("@") else arg
    print(json.dumps(find_candidates(argv[1], text), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
