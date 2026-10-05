#!/usr/bin/env python3
"""PostToolUse research-capture hook (optional KG feature). For every WebSearch/WebFetch, append a
provenance breadcrumb so external research auto-accumulates into the knowledge corpus — but ONLY when
the current project opted in via .sdlc/config.json -> knowledge_graph.enabled.

This hook ships in the plugin and fires on web tools in EVERY project, so it is fail-open and
side-effect-only: it never blocks or errors a tool call, and it is a fast no-op for any project that
did not opt in (no .sdlc, KG disabled, missing/garbage config). Breadcrumbs land under
.sdlc/knowledge/research/web/<microsecond-stamp>-<slug>.md (stamp = collision-safe)."""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

_WEB_TOOLS = {"WebSearch", "WebFetch"}

#: How much of the (scrubbed) response we keep — a provenance summary, never the raw page.
_EXCERPT_CHARS = 400

#: Secret-shaped substrings are redacted before anything reaches disk — the same location-only rule
#: the risk collectors follow: NEVER write the matched substring, each match becomes a typed
#: placeholder. The patterns live in ONE place, `skills/sigma-loop/scripts/scrub.py` (the publish leak
#: gate imports the same `SHAPE_RULES`), and this hook loads that file by path exactly as
#: `hooks/time_track.py` loads its sibling scripts — an inlined copy here drifted once already and
#: needed a parity test to hold it (#2718). Loaded LAZILY: a project that did not opt in must stay a
#: fast no-op that never compiles the ~20 regexes. `_scrub` and `_SECRET_PATTERNS` stay plain module
#: attributes because `ledger.py` / `actionlog.py` / `upstream.py` cross-load this hook and call
#: `mod._scrub(text)`; on a load failure `_scrub` RAISES (they degrade on their own terms) and no
#: breadcrumb is written — there is deliberately no fallback pattern copy (that would be the third).
_SCRUB_PATH = Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "scrub.py"
_SCRUB_MOD = None      # memo: the loaded scrub module
_SCRUB_ERR = None      # memo: the first load failure, re-raised on every later call (no retry storm)


def _load_scrub():
    """scrub.py, loaded once per process by path. A concurrent first call from two threads would
    double-load benignly (two identical module objects, last writer wins) — no lock: this hook is a
    per-call subprocess, and its in-process importers call sequentially."""
    global _SCRUB_MOD, _SCRUB_ERR
    if _SCRUB_MOD is not None:
        return _SCRUB_MOD
    if _SCRUB_ERR is not None:
        raise RuntimeError("scrub.py unavailable: %s" % _SCRUB_ERR)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_research_capture_scrub", _SCRUB_PATH)
        if spec is None or spec.loader is None:
            raise ImportError("no loader for %s" % _SCRUB_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:                        # missing file, syntax error, anything: memoise it
        _SCRUB_ERR = "%s: %s" % (type(exc).__name__, exc)
        raise RuntimeError("scrub.py unavailable: %s" % _SCRUB_ERR) from exc
    _SCRUB_MOD = mod
    return mod


def _scrub(text):
    """Redact secret-shaped substrings, never emitting the matched value (location-only capture rule).
    Raises RuntimeError when scrub.py cannot be loaded — never a silent unredacted passthrough."""
    return _load_scrub().scrub(text)


def __getattr__(name):
    # `_SCRUB` / `_SECRET_PATTERNS` resolve at attribute-access time so importing this module stays
    # load-free; on a load failure `_SECRET_PATTERNS` is the empty tuple and `_SCRUB` raises.
    if name == "_SCRUB":
        return _load_scrub()
    if name == "_SECRET_PATTERNS":
        try:
            return _load_scrub()._SECRET_PATTERNS
        except RuntimeError:
            return ()
    raise AttributeError(name)


def _slug(text, n=48):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:n] or "untitled").strip("-")


def _kg_enabled(project_dir):
    # strict: only an explicit boolean true opts in — a stringy "false"/"no" must NOT enable.
    try:
        cfg = json.loads((Path(project_dir) / ".sdlc" / "config.json").read_text(encoding="utf-8"))
        return (cfg.get("knowledge_graph") or {}).get("enabled") is True
    except Exception:
        return False


def build_breadcrumb(tool_name, tool_input, tool_response):
    """Pure: (relative_path, markdown) for a web tool with a subject, else None.

    Security: we persist a provenance breadcrumb — source, subject, and a SHORT, scrubbed excerpt —
    never the raw response body. Raw web bodies can carry tokens/PII; dumping any of it verbatim into
    a git-tracked dir is the leak this closes. The excerpt is scrubbed of secret-shaped substrings and
    THEN capped at `_EXCERPT_CHARS`; `.sdlc/knowledge/` is also gitignored by /sigma-setup, so even a
    scrubbed breadcrumb stays local unless the adopter deliberately commits it (defense in depth)."""
    if tool_name not in _WEB_TOOLS:
        return None
    raw = tool_input.get("query", "") if tool_name == "WebSearch" else tool_input.get("url", "")
    subject = (raw or "").strip()
    if not subject:
        return None                                     # skip failed/empty web calls — no junk breadcrumbs
    subject = _scrub(subject)                            # a credential in a URL query param / pasted query
                                                         # must not land verbatim in frontmatter/heading/slug
    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="microseconds")
    stamp = now.strftime("%Y-%m-%dT%H%M%S-%f")          # collision-safe to the microsecond
    heading = subject.replace("\n", " ").replace("\r", " ")[:200]  # one-line, can't break the markdown
    body = tool_response if isinstance(tool_response, str) else json.dumps(tool_response, ensure_ascii=False)
    # Scrub the WHOLE body, then cap — the order is load-bearing and matches mirror.py:46's own
    # `scrub(body)[:_EXCERPT_CHARS].strip()`. Truncating first can cut a well-formed key in half and
    # hand the scrubber a BEGIN with no END, i.e. manufacture the degraded input out of a sound one.
    # scrub() is a fixed set of linear-scan regexes over a body this hook already holds in memory.
    excerpt = _scrub(body)[:_EXCERPT_CHARS].strip()
    md = ("---\n"
          f"source: {tool_name.lower()}\n"
          f"subject: {json.dumps(subject, ensure_ascii=False)}\n"
          f"captured_at: {ts}\n"
          "contributor: sigma\n"
          "---\n\n"
          f"# {tool_name}: {heading}\n\n"
          f"{excerpt}\n")
    return f".sdlc/knowledge/research/web/{stamp}-{_slug(subject)}.md", md


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
        if _kg_enabled(project_dir):
            try:
                out = build_breadcrumb(data.get("tool_name", ""),
                                       data.get("tool_input") or {},
                                       data.get("tool_response", ""))
            except RuntimeError as exc:             # scrub.py could not be loaded: say so, write nothing
                print("research_capture: scrub.py unavailable, no breadcrumb written: %s" % exc,
                      file=sys.stderr)
                out = None
            if out is not None:
                rel, md = out
                dest = Path(project_dir) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(md, encoding="utf-8")
    except Exception:
        pass  # fail-open: never disrupt the session
    sys.exit(0)


if __name__ == "__main__":
    main()
