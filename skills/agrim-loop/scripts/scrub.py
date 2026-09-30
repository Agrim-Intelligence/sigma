"""Secret-shaped redaction shared by the scripts/ backlog collectors (mirror + cross-check).

Location-only capture rule: never emit a matched secret VALUE — each match becomes a typed
placeholder, not the value. THIS file is the one pattern set: `hooks/research_capture.py` loads it by
path for web-research excerpts (the same way `hooks/time_track.py` loads its sibling scripts), and
the publish leak gate `tools/leak_scan.py` imports `SHAPE_RULES` below as its own secret rules — so
there is no second copy to keep in sync. Best-effort (pattern-based, not a guarantee) — which is why
anything this feeds (the board mirror, `.sdlc/knowledge/`) is ALSO gitignored (defense in depth)."""
import re

#: `SHAPE_RULES` — every single-regex secret shape the publish leak gate detects, `(name, rx)`, with
#: the gate's contract: the secret VALUE is group 1 when the rx has a group, else the whole match.
#: `tools/leak_scan.py::_scrub_rules` IMPORTS this tuple (and Refuses when it is absent); it is never
#: copied there, and `scrub()` applies it via `_SECRET_PATTERNS` below. The gate keeps its own
#: post-filters (auth length / digit+letter, credential-assignment digit+letter,
#: `SECRET_FIXTURE_VALUES`) — a gate's false-positive budget differs from a redactor's.
#:
#: Two edits vs. the gate's original text (#2718 D2): `url-password`'s scheme is bounded
#: `[a-z][a-z0-9+.-]{0,31}://` — unbounded it rescanned the hyphen run from every word boundary
#: (measured: 6,802 ms on a 100 KB `the-quick-brown-fox-` slug, 17,306 ms on `a-` x 50,000; bounded,
#: the whole scrub() on the same inputs ~36-45 ms) and the research hook scrubs WHOLE web bodies; and
#: `gitlab-token` is `{20,}` not `{20}`, so a longer token's tail no longer survives a redaction.
#: Exact-length rules (`npm-token` `{36}`, `google-key` `{35}`) redact only the first n characters
#: of a longer run and leave its tail — the same behaviour as the gate, which matches this regex.
#: Order: the specific `sk-` prefixes (anthropic, openai) before the generic `sk-key` so the label
#: names the provider (they cannot overlap: `sk-[A-Za-z0-9]{20,}` stops at the hyphen).
#:
#: GATE-ONLY, deliberately not here: the KEY_WINDOW proximity key-body rule (a verdict over a
#: window, not a span a redactor can replace; built for source-code spellings incl. concatenated
#: bodies); secret-shaped FILENAMES (a path rule, meaningless on text); unreadable/opaque content
#: (file-level; would redact legitimate base64 in transcripts); config-file unquoted credentials
#: (keyed on the file's suffix, which runtime text has no notion of).
#: DEFERRED (follow-up issue, filed at #2718's retro): SSH2 `---- BEGIN SSH2 ENCRYPTED PRIVATE KEY
#: ----` (its unterminated form needs its own body walk — SSH2 bodies carry `Comment:` headers the
#: closed header list halts on; half of it would be a silent half-guarantee), PuTTY
#: `PuTTY-User-Key-File-N:` (no END marker; a plain regex would eat prose up to a later
#: `Private-MAC:`), base64-wrapped PEM (`LS0tLS1CRUdJTi...` needs the gate's decode check to tell a
#: wrapped private key from a wrapped public certificate). #2722 residuals: URL passwords are IN
#: (`url-password`); re-encoded secrets and entropy detection are OUT — the gate has no rule for
#: them, so there is nothing to share.
SHAPE_RULES = (
    ("slack-token", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("openai-key", re.compile(r"(?<![\w-])sk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}")),
    ("sk-key", re.compile(r"(?<![\w-])sk-[A-Za-z0-9]{20,}")),
    ("google-key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("stripe-key", re.compile(r"(?<![\w-])[sr]k_live_[0-9a-zA-Z]{16,}")),
    ("npm-token", re.compile(r"(?<![\w-])npm_[A-Za-z0-9]{36}")),
    ("pypi-token", re.compile(r"(?<![\w-])pypi-AgE[A-Za-z0-9_-]{40,}")),
    ("gitlab-token", re.compile(r"(?<![\w-])glpat-[A-Za-z0-9_-]{20,}")),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/[s]ervices/(T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{16,})")),
    ("url-password", re.compile(r"(?i)\b[a-z][a-z0-9+.-]{0,31}://[^\s/:@'\"]*:([^\s/'\"]{6,})@"
                                 r"(?:[a-z0-9_.-]+\.[a-z]{2,}|\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9_-]+)(?![\w.-])")),
    ("credential-assignment", re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|access[_-]?token|"
        r"auth[_-]?token|bot[_-]?token|secret[_-]?access[_-]?key|password|passwd|pwd|secret|token)"
        r"[\"']?\s*[:=]\s*[\"']([^\"'\s]{12,})[\"']")),
)


def _replacement(name, rx):
    """The `re.sub` replacement for a SHAPE_RULES entry: the typed placeholder for a whole-match rule;
    for a grouped rule a callable that rewrites ONLY group 1's span, so the scheme and host of a URL,
    the webhook host path, and an assignment's key and quotes survive around the placeholder."""
    label = "[REDACTED:%s]" % name
    if not rx.groups:
        return label
    return lambda m: m.group(0)[:m.start(1) - m.start(0)] + label + m.group(0)[m.end(1) - m.start(0):]

#: NO \b anchors on the shape tokens — the prefixes (AKIA / gh[pousr]_ / eyJ…) are specific enough to
#: stand alone, and a leading \b would let a secret GLUED to a preceding word char (e.g. "id=AKIA…")
#: slip through. Bare "token" is deliberately absent (it over-redacts ordinary prose like "token
#: budget"); a real `token: <value>` assignment is caught by the key:value rule instead.
_SECRET_PATTERN_SPECS = (
    ("private-key", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY(?: BLOCK)?-----.*?-----END[ A-Z]*PRIVATE KEY(?: BLOCK)?-----", re.DOTALL),
     "[REDACTED:private-key]"),
    #: Fallback for a key whose END marker never arrives — truncated at the source, or cut mid-capture.
    #: It has to consume the BODY, not just the header: replacing the header alone published every line
    #: underneath it. Whitespace is `(?:\\[rn]|\s)` because the dominant carriers serialize first —
    #: json.dumps()/str() turn the key's newlines into literal \n ESCAPES, and a real-whitespace pattern
    #: stops dead at the first one. Proc-Type:/DEK-Info: are the RFC1421 encrypted-PEM headers that sit
    #: between header and body; that list is closed and colon-anchored (Subject:/Version:/Comment: are
    #: deliberately absent — they open ordinary prose). The body is a run of long base64 lines, so
    #: `{16,}` stops at short prose and a document merely DISCUSSING the marker keeps its text; the
    #: `{1,15}` tail then takes a mid-line truncation fragment ONLY at end-of-input.
    #:
    #: WHAT THIS DOES AND DOES NOT COVER — the residual is NOT bounded to a fixed number of characters:
    #: consumption walks recognized headers and long base64 runs and STOPS at the first sub-16-char run
    #: or any non-base64 byte inside the body. Everything from that gap onward SURVIVES, however much
    #: of it there is. So a CANONICAL unterminated key — a clean body cut short at the source, which is
    #: the case this fallback exists for — is consumed in full; a MANGLED body (a foreign byte or a
    #: short run partway down) is redacted only as far as the gap, and complete key lines after it are
    #: published. Terminated keys never reach here at all: the DOTALL BEGIN..END pattern above owns
    #: them outright. The same stop rule is why an UNRECOGNIZED header line (e.g. `Comment:`) between
    #: BEGIN and the body halts consumption immediately and leaves the whole body — accepted on
    #: purpose, because the alternative is open-ended header matching that would eat ordinary prose.
    #: `(?: BLOCK)?` admits the PGP armor header (`-----BEGIN PGP PRIVATE KEY BLOCK-----`); its
    #: UNTERMINATED form stops here too, at its `Version:`/`Comment:` armor lines (same accepted
    #: cost, pinned by test) — the terminated form is owned outright by the DOTALL pattern above.
    #: Both costs are pinned by test so the next reader meets the real behavior, not a comforting
    #: bound. Widening the run rule, or opening the header list, is a deliberate decision with its own
    #: false-positive price — not a typo to fix in passing.
    #: No possessive/atomic groups: those are 3.11+, and CI still runs 3.10.
    ("private-key", re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY(?: BLOCK)?-----"
                r"(?:(?:\\[rn]|\s)*(?:Proc-Type|DEK-Info):[^\n]{0,120})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{16,})*"
                r"(?:(?:\\[rn]|\s)*[A-Za-z0-9+/=]{1,15}(?=(?:\\[rn]|\s)*$))?"),
     "[REDACTED:private-key]"),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    ("gh-token", re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}"), "[REDACTED:gh-token]"),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    ("auth", re.compile(r"(?i)\b(?:bearer|basic|digest)\s+[A-Za-z0-9+/=._\-]{8,}"), "[REDACTED:auth]"),
    #: The gate's shapes, before the generic key:value rule so each keeps its provider label.
    *((name, rx, _replacement(name, rx)) for name, rx in SHAPE_RULES),
    ("credential-assignment", re.compile(r"(?i)\b(api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|"
                r"access[_-]?token|authorization|token|secret|password|passwd|pwd)\b[\"']?\s*[:=]\s*"
                r"[\"']?[^\s\"'<>&]{4,}"),
     r"\1: [REDACTED]"),
)

# Public only to the commit boundary: rule and regular expression, never a matching span/value.
# `scrub()` below still owns the text-redaction behaviour.
COMMIT_SHAPE_RULES = tuple((name, rx) for name, rx, _replacement_ in _SECRET_PATTERN_SPECS)
_SECRET_PATTERNS = tuple((rx, replacement) for _name, rx, replacement in _SECRET_PATTERN_SPECS)

# These are public synthetic values deliberately used in Sigma's own redaction tests.  The list is
# exact rather than prefix/path based: it cannot become a general bypass for a real credential.
COMMIT_FIXTURE_VALUES = frozenset({"AKIAIOSFODNN7EXAMPLE"})


def commit_secret_hits(text):
    """Return value-free `(rule, column)` hits for one added source line.

    The commit gate must say why and where it refused without echoing a credential.  Matches in the
    exact synthetic fixture list are excluded so the repository can maintain its redaction controls.
    """
    hits = []
    for name, rx in COMMIT_SHAPE_RULES:
        for match in rx.finditer(text):
            if match.group(0) in COMMIT_FIXTURE_VALUES:
                continue
            hits.append((name, match.start() + 1))
    return hits


def scrub(text):
    """Redact secret-shaped substrings, never emitting the matched value. Returns text unchanged when
    empty/None-ish (callers pass strings)."""
    if not text:
        return text
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text
