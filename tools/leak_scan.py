#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The publish leak gate: shipped files carry no home path, no secret-shaped string or file, and no URL
into this repository's own (private until published) GitHub owner (#277, the gate #231's acceptance
names).

WHAT IS SCANNED. EVERY path git tracks (`git ls-files`) -- `.sdlc/`, `tests/` and all. A plugin
install copies the whole tracked tree (README "every tracked file is shipped surface";
`tests/test_self_contained.py` pins the same), so no directory is exempt.
  * A SYMLINK ships as its target PATH, so that path text is what is scanned (`os.readlink`); the
    link is never followed, so a link to `/etc/hosts` never makes the gate read the host's file.
  * Text is decoded as UTF-8, UTF-16 (a BOM, or BOM-less with NULs on one byte parity) or, failing
    UTF-8, Latin-1 -- then scanned like any other text.
  * A file the gate CANNOT scan is a finding, named, never a silent skip: `opaque-binary` (NUL bytes
    that are not UTF-16), `oversize` (over `MAX_BYTES`), `unreadable` (an OS error). Each exits 1.
  * `ALLOW_PATHS` -- `{path: reason}` for a vendored or opaque file the gate may not read. A reason is
    mandatory: an empty one REFUSES (exit 2). Empty today: nothing is vendored or opaque.
A single line opts out of one rule with an in-file marker that must carry a reason, e.g.
    ... # leak-scan: allow home-path the planted fixture the home rule must see
The marker suppresses exactly <rule> on exactly that line. A marker naming no known rule, or with no
reason, is itself a finding (`allow-marker-malformed`), so a typo cannot silently disarm a line; the
summary counts the allow-marked lines. Fixtures that need a TOKEN-shaped value build it from
fragments at run time instead -- the marker is for the rare literal that must stay literal.

THE RULES (a finding's line is 0 when the rule is about the whole file).
  * home-path: `/Users/<name>`, `/home/<name>`, the same behind WSL's `/mnt/<drive>/Users/` and
    macOS's `/System/Volumes/Data/`, `C:\\Users\\<name>` / `C:/Users/<name>` (any case: Windows paths
    are case-insensitive), `~<name>/` / `~<name>` at end of line, and the encoded `-Users-<name>-`
    form agent hosts use for per-project directories, where <name> is not a placeholder (`you`,
    `me`, `user`, `USER`, `<...>`, `$USER`, `alice`, `bob`, `nosuch*`, any one-letter name, ...;
    `dev` is NOT one -- it is a real account name on many machines).
  * secret shapes: ONE source of truth, `skills/agrim-loop/scripts/scrub.py`, loaded by path and
    never copied: its `SHAPE_RULES` (named there) plus the redactor-only shapes in `_SECRET_PATTERNS`
    (PEM private keys, AWS `AKIA`/`ASIA`, classic GitHub `gh[pousr]_`, JWT, bearer/basic auth),
    named from their `[REDACTED:<name>]` label. Missing tables REFUSE (exit 2): a gate with no rules
    must not print 0 findings. PEM private keys need a real body (40+ base64 chars).
  * config-credential (the gate's own, in config-like files only -- `_is_config`: `.env*`, `.ini`,
    `.cfg`, `.conf`, `.toml`, `.yml`/`.yaml`, `.json`, `.properties`, `.tmpl`, `.npmrc`/`.pypirc`/
    `.yarnrc*`/`.netrc`, `Dockerfile*`/`*dockerfile` (incl. `ENV`/`ARG KEY value`), `.sh`/`.bash`/
    `.zsh`): a KEY that is a whole
    identifier ENDING in a credential word (`GITHUB_TOKEN`, `db.password`, `aws_secret_access_key`,
    `_authToken`, ...; `_CRED_KEY`) assigned a value, quoted or not. scrub.py's generic key:value
    redactor is NOT used here: its leading `\\b` cannot match after `_`, so it misses every
    prefixed key. In prose and code the rule would fire on every `token: str` annotation.
  * key-body: a private key body that lost its header or its END (#433). Three triggers:
    (1) BLOCKS. Adjacent whole lines of 40+ base64 characters (`=` padding; outer spaces, tabs and
    a CR ignored) count when there are 3 or more with at most ONE lacking upper case, lower case or a
    digit, or exactly 2, both mixed, followed directly by a 28-39 character line (an EC P-256 body
    is 64, 64, 36). Lengths count the base64 payload, never the `=` padding: a 28-character line
    ending in `=` is a 27-character tail, and a 40-character line ending in `=` is under the 40
    floor (measured: a real legacy-encrypted EC body re-wrapped at 72 columns, 72, 72 and 28 with
    `=`, is clean header-less; at 64 columns, 64, 64 and 44, it is flagged). A counted block is
    exempt only when its first line is inside a whole public span: a standalone `-----BEGIN
    <label>-----` line whose label names neither PRIVATE nor SECRET (any case), then only blank, `Key:
    value`, base64 or `...` lines, then `-----END <same label>-----`. A body under a public header with
    no END, or glued to a certificate, is flagged.
    (2) HEADER ANCHOR. A block that does not count, every line mixed (one line is enough), is a
    finding when a line naming `BEGIN <label>` with PRIVATE or SECRET in the label -- a header, or
    prose naming one -- sits above it with at most `_GAP` = 8 blank or `Key: value` lines between
    (each may carry a unified diff's leading `+`): a one-line Ed25519 body or a truncated RSA paste
    under its header, also inside a diff.
    (3) DER FIRST LINE. A line that starts, after indentation and at most two runs of 1-3 symbols
    (`"`, `# `, `> `, `// `, ` * `, `- "`) or any run of `+` and `/` (base64 characters: a diff's `+`,
    a `//` with no space), with base64 or base64url whose first 24 characters decode
    to a private key's DER header (`SEQUENCE { INTEGER 0|1, SEQUENCE | INTEGER | OCTET STRING`:
    PKCS8 v1/v2, PKCS1 / DSA, SEC1), or with OpenSSH's `openssh-key-v1` constant; anything may follow
    it and no span exempts it. It is the only reach of a header-less one-line Ed25519 key, which is
    otherwise 48 random bytes, the same as a sha384 digest. Explicit EC PARAMETERS (public, and
    shaped like PKCS8 v2) are excluded by their OID.
    A finding's line is the block's first line, or the DER line. A trailing allow marker waives a DER
    line (it stays a DER line). There is NO designed waiver for a header-less multi-line body. A marker
    on one of its lines makes that line non-base64, which splits the block, and what remains is judged
    by the same rules (measured through the gesture): a SHORT body (3 lines, or the 64, 64, 36 P-256
    shape) split below the thresholds goes clean, whichever line carries the marker; a longer body is
    still flagged on its remaining lines (6 lines, marker on line 1: flagged at line 2; on line 4 or
    6: at line 1); a marker on the line above the body waives nothing. That is a side effect of the
    block rule, not a per-block waiver, and the summary's `allow-marked line(s)` count still counts
    the marker. The documented, reliable waiver is `ALLOW_PATHS`, for a whole file.
    False positives, MEASURED against a3c913c's rule: 0 -> 0 findings on the staged tree (826 files:
    821 at origin/main f69d3e1, this change's 2 new test files and its 3 `.sdlc` phase documents); on
    a generated 97-file corpus 34 -> 17 with 1 new (a P-256 body); the CHANGELOG lists the wider
    populations.
  * secret-file: a tracked path whose NAME is a credential container, whatever it holds: `id_rsa` /
    `id_dsa` / `id_ecdsa` / `id_ed25519`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`, `*.keystore`,
    `*.ppk`, `.netrc` / `_netrc`, `.pgpass`, `credentials.json`, `service-account*.json`, and `.env` /
    `.env.<x>` other than an example (`.example`, `.sample`, `.template`, `.dist`, `.defaults`).
  * Post-filters (a gate's false-positive budget is not a redactor's): a credential / auth value
    must carry a digit and a letter and not be a kebab/snake identifier, a `<placeholder>`, a
    `$VAR` / `${VAR}` reference or a `your-...` / `changeme` / `example` stand-in.
  * origin-owner URL: a link into this checkout's own `origin` owner's OTHER repositories, in these
    spellings -- `github.com/<owner>/<repo>` (also `www.`, `gist.`, `codeload.` and `:443`),
    `git@github.com:<owner>/<repo>.git`, `ssh://git@github.com/<owner>/...`,
    `ssh://git@ssh.github.com:443/<owner>/...`, `https://<anything>@github.com/<owner>/...`,
    `raw.githubusercontent.com/<owner>/<repo>/...` (any `*.githubusercontent.com`),
    `api.github.com/repos/<owner>/<repo>`. Allowed: this repository itself (the CI badge) and the
    documented public slug `_MARKETPLACE_REPO` in `skills/agrim-doctor/scripts/doctor.py` (read by
    `ast`, never imported). No origin, or a non-GitHub one: the rule is skipped and the summary says
    so.

OUT OF SCOPE -- not detected, and nothing here claims they are: URL-encoded home paths
(`%2FUsers%2F<name>`); `/root/` and UNC `\\\\wsl$\\...\\home\\<name>` paths; a bare `<owner>/<repo>`
slug outside a URL (`gh repo clone <owner>/<repo>`), `<owner>.github.io`, `github.com/orgs/<owner>/`
and a percent-encoded GitHub URL; e-mail addresses; bidi and zero-width characters (including one
spliced into a token); base64-wrapped, split or concatenated tokens; credentials made of letters
only (the digit+letter post-filter drops them); `key: value` credentials in prose and code files
(only quoted `key = "value"` is caught there, by scrub.py's `credential-assignment`); UTF-32 text
(measured: without a byte-order mark, or big-endian with one, it is reported `opaque-binary`;
little-endian WITH one is mis-decoded as UTF-16 and nothing in it is seen). For `key-body` (each
planted and run through the gesture, #433):
hex bodies, and base64url bodies other than a base64url DER first line; a PEM base64-wrapped into one
string; a key inside a JSON or shell string, or after other text on its line (`key: <base64>`);
PuTTY `.ppk`; a header-less slice of fewer than 3 lines (2 lines with no 28-39 tail); any slice
with no DER start inside a matching public span; an encrypted PKCS8, PKCS12 or PGP-private body
from a lone first line; a body wrapped below 24 characters (in every shape); a body wrapped below 40
whose first line is missing; any non-DER body wrapped below 40, header and END included (no line
reaches the 40 floor, nor scrub.py's `private-key` 40-character run); an all-lower-case body; a
body indented with U+00A0 no-break spaces (7 key kinds planted header-less: all clean); more
than 8 armor lines, or a line that is not armor, between header and body; a non-DER body under a
lower-case `begin` header (it does not anchor; a DER first line or a counted block under it is
still caught: EC, RSA, Ed25519 and P-224 bodies measured). KNOWN AMBIGUITIES (findings, not misses;
waive with `ALLOW_PATHS`): 3+ raw base64 digests in a row; generic wrapped base64 (a binary MIME
attachment, a 76-wide CSS data URI: a3c913c flags both too); a header-less public DER body (an XML
`<X509Certificate>`, a CMP message); any other public DER that opens `02 01 00|01 30|02|04` after its
SEQUENCE; a certificate with prose inside its BEGIN/END, or whose BEGIN or END line also carries code
(a Go backtick or a Python triple-quoted literal), or an SSH2 public block whose `Comment:` continues
with a trailing backslash: none is a span, so the body is a finding.

OUTPUT. One line per finding, `<path>:<line>: <rule>` -- the LOCATION only, never the matched value,
then `leak_scan: <n> finding(s) over <m> file(s) (<u> unscannable, <a> allow-marked line(s))`.
EXIT 0 = none; 1 = findings (an unscannable file is one); 2 = cannot scan (git missing, not a git
checkout, scrub rules missing, an `ALLOW_PATHS` entry with no reason, a bad argument).

USAGE
    python3 tools/leak_scan.py            # scans the checkout this file lives in

Cost: one `git ls-files`, one `git remote get-url`, then each rule once over each tracked text file.
MEASURED on the staged tree (826 files: 821 at origin/main f69d3e1, this change's 2 new test files
and its 3 `.sdlc` phase documents; Apple M-series, CPU seconds, minimum of 3 runs at load 4-6):
5.49 s on Python 3.9.6 and 5.05 s on 3.12.13 (a3c913c's gate: 5.57 and 5.11 s), nearly all of it
regex time; `key-body` takes 0.16 s of it (0.26 s before #433; one run) and adds no finding here. One
file at a time, so memory is bounded by the largest file (`MAX_BYTES`); 10x the tree would be ~50-55
s (extrapolated, not measured) -- a publish/CI gate, never a hook. Every `key-body` regex was fuzzed
with single lines of 16 KB, 64 KB and 1 MB (26 line shapes x 8 contexts, worst 0.27 s at 1 MB) and
grows linearly; the worst single 4 MB file found for it (one line of repeated `BEGIN ` words above a
one-line body) took 1.1 s in `key-body` and 2.6 s for the whole file (a3c913c: 0.05 and 1.1 s).
NOT linear, and not changed by #433: scrub.py's `private-key` rule on a file of repeated private
BEGIN headers, each followed by 8 blank lines and one base64 line, with no END: 0.5, 1.9 and 7.6 s
at 0.125, 0.25 and 0.5 MB (4x per doubling), and a 4 MB file did not finish in 150 s.
Stdlib only; Linux, macOS, Windows.
"""
import ast
import base64
import bisect
import importlib.util
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_BYTES = 4 * 1024 * 1024
#: Vendored/opaque files exempt from the scan, `{path: reason}`. A reason is mandatory.
ALLOW_PATHS = {}
CONFIG_SUFFIXES = (".json", ".jsonc", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env",
                   ".properties", ".tmpl", ".sh", ".bash", ".zsh", ".npmrc", ".pypirc", ".envrc",
                   ".netrc")

#: Names that stand in for "some user" in documentation, never a real account.
_PLACEHOLDER_USERS = {"you", "me", "user", "username", "runner", "alice", "bob", "someone", "name",
                      "example", "shared", "Shared", "USER", "USERNAME", "jane",
                      "john", "me_", "yourname", "your-name", "your_name", "someuser", "ubuntu"}
#: `(?<![\w.~-])` keeps a URL path (`example.com/home/x`) out; the fixed-width lookbehinds admit the
#: two real prefixes a home directory sits under (WSL's `/mnt/<drive>`, macOS's data volume).
_HOME = re.compile(r"(?:(?<![\w.~-])/(?:Users|home)/"
                   r"|(?<=/mnt/[A-Za-z])/(?i:users)/"
                   r"|(?<=/System/Volumes/Data)/(?:Users|home)/"
                   r"|(?<![\w])[A-Za-z]:(?:\\{1,2}|/)(?i:users)(?:\\{1,2}|/))"
                   r"([A-Za-z0-9._-]+)(?![\w-])")
#: A home path as agent hosts encode it into a directory name (`~/.claude/projects/-Users-<name>-src`).
_ENCODED = re.compile(r"(?<![\w-])-(?:Users|home)-([A-Za-z0-9._]+)-")
_TILDE = re.compile(r"(?:^|(?<=[\s`'\"=:]))~([a-z_][a-z0-9_-]{1,31})(?=/|[`'\")]*[ \t]*$)", re.M)

#: A config KEY: a whole identifier (`[A-Za-z0-9_.-]`, never starting mid-identifier) that ENDS in a
#: credential word -- so `GITHUB_TOKEN`, `db.password`, `_authToken` match and `token_budget` does not.
_CRED_KEY = (r"[A-Za-z0-9_.-]*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key"
             r"|private[_-]?key|credential)s?")
_CRED_VALUE = r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s\"',;#}\]]+)"
_CONFIG_CRED = re.compile(r"(?i)(?<![A-Za-z0-9_.$-])[\"']?" + _CRED_KEY + r"[\"']?[ \t]*[:=][ \t]*"
                          + _CRED_VALUE)
#: Dockerfile's space form, `ENV KEY value` / `ARG KEY value`.
_DOCKER_CRED = re.compile(r"(?im)^[ \t]*(?:ENV|ARG)[ \t]+" + _CRED_KEY + r"[ \t]+" + _CRED_VALUE
                          + r"[ \t]*$")
_PLACEHOLDER_VALUE = re.compile(r"(?i)(?:your[-_ ].*|.*[-_]here|changeme|change[-_]me|example|"
                                r"placeholder|dummy|redacted|x{3,}|\*{3,}|\.{3,}|none|null)")
#: A private key body that lost its header or its END (see `key-body`). Per line, ONE alternative matches:
#:  b = a CANDIDATE: a whole line of 40+ base64 chars (`=` padding, outer spaces/tabs and a CR allowed,
#:      nothing else); a BLOCK is a run of adjacent candidate lines.
#:  k = the start of a private key's first line: indentation, up to two marker runs of 1-3 symbols (a quote,
#:      `# `, `> `, `// `, ` * `, `- "`), then `M` (0x30, a DER SEQUENCE) and base64 or base64url, or OpenSSH's
#:      constant; anything may follow. `_der_private` then reads the DER header.
_BODY_LINE = re.compile(r"(?m)^(?:[ \t]*(?P<b>[A-Za-z0-9+/]{40,}={0,2})[ \t]*\r?$"
                        r"|[ \t]*(?:[^A-Za-z0-9\s]{1,3}[ \t]*){0,2}"
                        r"(?P<k>M[A-Za-z0-9+/_-]{23,}|b3BlbnNzaC1rZXktdjEA))")
#: The short last line of a P-256 SEC1 body (64, 64, 36 chars).
_B64_TAIL = re.compile(r"(?m)[ \t]*[A-Za-z0-9+/]{28,39}={0,2}[ \t]*\r?$")
#: A PEM/armor BEGIN label. Bounded (`{1,64}`) and dash-free so a line of `-----BEGIN ` repeated cannot
#: go quadratic: a lazy `(.*?)` here took 31.5 s on 0.22 MB (measured, #433 research).
_PEM_BEGIN = re.compile(r"BEGIN ([^\r\n-]{1,64}?) ?-{4,5}")
_PEM_BEGIN_LINE = re.compile(r"(?m)^[ \t]*-{4,5} ?BEGIN ([^\r\n-]{1,64}?) ?-{4,5}[ \t]*\r?$")
_PEM_END_LINE = re.compile(r"[ \t]*-{4,5} ?END ([^\r\n-]{1,64}?) ?-{4,5}[ \t]*\r?$")
_BLANK_OR_ARMOR = re.compile(r"\+?[ \t]*(?:[A-Za-z][A-Za-z0-9 -]*:[^\r\n]*)?\r?$")  # blank, or `Key: value`
_SPAN_LINE = re.compile(r"[ \t]*(?:(?:[A-Za-z0-9+/=]+|\.{3}|\u2026)[ \t]*|[A-Za-z][A-Za-z0-9 -]*:[^\r\n]*)?\r?$")
_LONE_CR = re.compile(r"\r(?!\n)")
_OPENSSH_KEY = "b3BlbnNzaC1rZXktdjEA"            # base64 of `openssh-key-v1\0`, the first 20 chars of every one
#: DER (version, next tag) after the outer SEQUENCE of a private key: PKCS8 v1 (0, SEQUENCE), PKCS1 / DSA
#: (0, INTEGER), SEC1 (1, OCTET STRING), PKCS8 v2 (1, SEQUENCE).
_DER_PRIVATE = {(0, 0x30), (0, 0x02), (1, 0x04), (1, 0x30)}
_GAP = 8                                         # blank / `Key: value` lines (a diff's `+` allowed) header->body
_SECRET_FILE = re.compile(
    r"(?i)^(?:id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?|.+\.(?:pem|key|p12|pfx|jks|keystore|ppk)"
    r"|[._]netrc|\.pgpass|credentials\.json|service[-_]?account.*\.json"
    r"|\.env(?:\.(?!(?:example|sample|template|dist|defaults)$)[^/]+)?)$")

#: A kebab/snake identifier (`feature-classify-tier1`): words, each at most two trailing digits.
_IDENTIFIER = re.compile(r"[A-Za-z]+\d{0,2}(?:[-_.][A-Za-z]+\d{0,2})+")
_ALLOW = re.compile(r"leak-scan:[ \t]*allow\b(.*)$", re.M)
FILE_RULES = ("secret-file", "opaque-binary", "oversize", "unreadable")


def _load(rel, name):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scrub_rules():
    """-> `[(name, rx)]` from scrub.py's `SHAPE_RULES` + the labelled `_SECRET_PATTERNS`. scrub.py's
    unlabelled generic key:value redactor is skipped (see `config-credential` in the module
    docstring). Refuses (SystemExit 2) when either table is absent or empty."""
    rel = "skills/agrim-loop/scripts/scrub.py"
    try:
        mod = _load(rel, "sigma_scrub_for_leak_scan")
        shape = tuple(mod.SHAPE_RULES)
        patterns = tuple(mod._SECRET_PATTERNS)
    except (OSError, AttributeError, SyntaxError, TypeError) as exc:
        print(f"leak_scan: REFUSED: cannot load SHAPE_RULES/_SECRET_PATTERNS from {rel}: {exc}",
              file=sys.stderr)
        raise SystemExit(2)
    if not shape or not patterns:
        print(f"leak_scan: REFUSED: SHAPE_RULES or _SECRET_PATTERNS in {rel} is empty",
              file=sys.stderr)
        raise SystemExit(2)
    rules = list(shape)
    shape_rx = {rx.pattern for _, rx in shape}
    for rx, repl in patterns:
        if rx.pattern in shape_rx:
            continue
        label = re.fullmatch(r"\[REDACTED:([\w-]+)\]", repl) if isinstance(repl, str) else None
        if label:
            rules.append((label.group(1), rx))
    return rules


def _git(args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True)


def _public_slug():
    """`_MARKETPLACE_REPO` from doctor.py, read by `ast` (never imported), or None."""
    try:
        tree = ast.parse((ROOT / "skills/agrim-doctor/scripts/doctor.py").read_text("utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "_MARKETPLACE_REPO" for t in node.targets) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def origin_owner():
    """(owner, {allowed repository names}) of a GitHub `origin`, or None (no origin / not GitHub)."""
    proc = _git(["remote", "get-url", "origin"])
    if proc.returncode:
        return None
    m = re.search(r"github\.com(?::443)?[:/]+([A-Za-z0-9-]+)/([\w.-]+?)(?:\.git)?/?$",
                  proc.stdout.strip())
    if not m:
        return None
    allowed = {m.group(2)}
    slug = _public_slug()
    if slug and "/" in slug and slug.split("/")[0].lower() == m.group(1).lower():
        allowed.add(slug.split("/", 1)[1])
    return m.group(1), allowed


def _owner_url(owner):
    name, allowed = owner
    keep = "|".join(re.escape(r) for r in sorted(allowed))
    return re.compile(r"(?:github\.com(?::443)?[/:]|githubusercontent\.com/|api\.github\.com/repos/)"
                      + re.escape(name) + r"/(?!(?:" + keep + r")(?:\.git)?(?![\w.-]))[\w.-]+",
                      re.I)


def _placeholder(name):
    """A documentation stand-in (`you`, `$USER`, `<user>`, `nosuchuser42`), not a real account."""
    return (name in _PLACEHOLDER_USERS or len(name) == 1  # `/home/u/...`: a one-letter stand-in
            or name.startswith(("$", "<", "%", ".", "nosuch"))
            or not name.strip("."))


def _is_real_value(value):
    value = value.strip().strip("\"'`,;").strip()
    if not value or value.startswith(("<", "${", "$", "{{", "%", "/")) or re.search(r"[?&]", value):
        return False  # a placeholder, a template, or a URL path/query -- not a credential
    if _PLACEHOLDER_VALUE.fullmatch(value):
        return False
    return bool(re.search(r"\d", value) and re.search(r"[A-Za-z]", value)
                and not _IDENTIFIER.fullmatch(value))


def _allowed(line):
    """-> (set of rule names this line's marker allows, malformed?)."""
    m = _ALLOW.search(line)
    if not m:
        return set(), False
    words = m.group(1).split()
    if len(words) < 2 or not re.fullmatch(r"[a-z][a-z0-9-]+", words[0]):
        return set(), True
    return {words[0]}, False


def _private_label(label):
    return "PRIVATE" in label.upper() or "SECRET" in label.upper()


def _der_private(tok):
    """True for the first line of a private key: OpenSSH's constant, or a DER header `SEQUENCE { INTEGER
    0|1, SEQUENCE | INTEGER | OCTET STRING ...` in the first 24 bytes. A certificate, CSR, CRL, SPKI,
    PKCS7 or DH parameters open with SEQUENCE / OID / a long INTEGER instead."""
    if tok.startswith(_OPENSSH_KEY):
        return True
    if tok[:1] != "M" or len(tok) < 24:         # 0x30 (SEQUENCE) is always `M` in base64
        return False
    head = base64.urlsafe_b64decode(tok[:24])
    if head[0] != 0x30:                         # `M` also spans 0x31-0x33
        return False
    if head[1] < 0x80:
        i = 2
    elif head[1] in (0x81, 0x82, 0x83):
        i = 2 + (head[1] & 0x7F)
    else:
        return False
    if head[i:i + 4] == b"\x02\x01\x01\x30" and head[i + 5:i + 13] == b"\x06\x07\x2a\x86\x48\xce\x3d\x01":
        return False                            # explicit ECParameters (public) look like PKCS8 v2
    return head[i] == 0x02 and head[i + 1] == 0x01 and (head[i + 2], head[i + 3]) in _DER_PRIVATE


def _public_spans(text):
    """[(first offset, end offset)] of the interior of every `-----BEGIN <label>-----` ..
    `-----END <label>-----` span whose label names no PRIVATE/SECRET key and whose lines are all blank,
    `Key: value` or base64. Each scan stops at the first other line (a BEGIN line is one), so the
    scans never overlap and the pass is linear."""
    spans = []
    for m in _PEM_BEGIN_LINE.finditer(text):
        if _private_label(m.group(1)):
            continue
        first = pos = m.end() + 1
        while pos <= len(text):
            eol = text.find("\n", pos)
            eol = len(text) if eol < 0 else eol
            if _SPAN_LINE.fullmatch(text, pos, eol):
                pos = eol + 1
                continue
            end = _PEM_END_LINE.fullmatch(text, pos, eol)
            if end and end.group(1) == m.group(1):
                spans.append((first, pos))
            break
    return spans


def _anchored(text, first):
    """True when a PRIVATE/SECRET BEGIN header is named on a line above `first`, with at most `_GAP`
    blank or `Key: value` lines between (prose that names the header counts: shape (b))."""
    pos = first
    for _ in range(_GAP + 1):
        if pos == 0:
            return False
        start = text.rfind("\n", 0, pos - 1) + 1
        if any(_private_label(h.group(1)) for h in _PEM_BEGIN.finditer(text, start, pos - 1)):
            return True
        if not _BLANK_OR_ARMOR.fullmatch(text, start, pos - 1):
            return False
        pos = start
    return False


def _key_bodies(text):
    """Start offsets of private-key bodies whose header or END is missing (see `key-body`): one per block
    that counts, at its first line, plus one per DER-recognised first line."""
    cands, out = [], []
    for m in _BODY_LINE.finditer(text):
        tok = (m.group("b") or m.group("k")).lstrip("+/")   # a diff's `+`, a `//`: base64 too, so `b` kept them
        if m.group("b"):
            cands.append(m)
        if _der_private(tok):
            out.append(m.start())
    blocks = []
    for m in cands:
        if blocks and m.start() == blocks[-1][-1].end() + 1:   # begins right after the previous line's newline
            blocks[-1].append(m)
        else:
            blocks.append([m])
    spans = None
    for blk in blocks:
        n = len(blk)
        bad = sum(not (re.search("[A-Z]", m.group("b")) and re.search("[a-z]", m.group("b"))
                       and re.search(r"\d", m.group("b"))) for m in blk)
        counted = (n >= 3 and bad <= 1) or (n == 2 and bad == 0
                                            and _B64_TAIL.match(text, blk[-1].end() + 1))
        if counted:
            if spans is None:
                spans = _public_spans(text)
            i = bisect.bisect_right(spans, (blk[0].start(), len(text) + 1)) - 1
            counted = i < 0 or not spans[i][0] <= blk[0].start() < spans[i][1]
        if counted or (bad == 0 and _anchored(text, blk[0].start())):
            out.append(blk[0].start())
    return sorted(set(out))


def scan_text(text, rules, owner, config=False, stats=None):
    """-> [(line number, rule name)] for one file's text. Pure; the tests drive it directly.
    `rules` is `_scrub_rules()`; `config` adds `config-credential`. `stats`, when a dict, gets
    `allowed` += the number of lines a valid allow marker covers.

    Each rule runs ONCE over the whole text and a match is attributed to the line it starts on --
    per-line matching made ~5.4M regex calls on this tree; whole-text makes ~15k. PEM blocks span
    lines anyway, and count only with a real key body (a base64 run of 40+), never for prose that
    merely names the marker. A lone CR ends a line too (it is turned into a newline first, same
    length, so every rule's line numbers agree; so `\\r\\r\\n` counts as 2 lines, as Python's
    `splitlines` does, where `grep -n` counts 1; a side effect: an empty line then follows every
    line, so in a `\\r\\r\\n` file a header-less body is caught only by a DER first line: an
    encrypted PKCS8 or legacy-encrypted EC body is missed, as a3c913c missed it). `key-body` reports
    a counted block, a header anchor or a DER line (see the module docstring)."""
    if "\r" in text:
        text = _LONE_CR.sub("\n", text)         # a lone CR ends a line too; same length
    starts = [0] + [m.end() for m in re.finditer("\n", text)]
    hits = {}

    def hit(pos, name):
        hits.setdefault(bisect.bisect_right(starts, pos), []).append(name)

    for m in _HOME.finditer(text):
        if not _placeholder(m.group(1).rstrip(".")):
            hit(m.start(), "home-path")
    for rx in (_TILDE, _ENCODED):
        for m in rx.finditer(text):
            if not _placeholder(m.group(1)):
                hit(m.start(), "home-path")
    for name, rx in rules:
        for m in rx.finditer(text):
            if name == "private-key" and not re.search(r"[A-Za-z0-9+/]{40,}", m.group(0)):
                continue
            if name == "credential-assignment" and not _is_real_value(m.group(1)):
                continue
            if name == "auth" and not _is_real_value(m.group(0).split(None, 1)[1]):
                continue
            hit(m.start(), name)
    if config:
        for rx in (_CONFIG_CRED, _DOCKER_CRED):
            for m in rx.finditer(text):
                if _is_real_value(m.group(1)):
                    hit(m.start(), "config-credential")
    for pos in _key_bodies(text):
        hit(pos, "key-body")
    if owner:
        for m in _owner_url(owner).finditer(text):
            hit(m.start(), "origin-owner-url")
    known = {n for n, _ in rules} | {"home-path", "origin-owner-url", "config-credential",
                                     "key-body"}
    allows = {}
    for m in _ALLOW.finditer(text):
        n = bisect.bisect_right(starts, m.start())
        allow, malformed = _allowed(m.group(0))
        if malformed or not allow <= known:
            hit(m.start(), "allow-marker-malformed")
        else:
            allows[n] = allow
    if stats is not None:
        stats["allowed"] = stats.get("allowed", 0) + len(allows)
    found = []
    for n in sorted(hits):
        found += [(n, h) for h in dict.fromkeys(hits[n]) if h not in allows.get(n, ())]
    return found


def _is_config(rel):
    base = rel.rsplit("/", 1)[-1]
    low = base.lower()
    return (base.startswith((".env", ".yarnrc")) or low.endswith(CONFIG_SUFFIXES)
            or low.startswith("dockerfile") or low.endswith("dockerfile"))


def _decode(data):
    """-> text, or None for opaque binary. UTF-16 by BOM or by NUL parity; UTF-8; else Latin-1."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    head = data[:8192]
    if b"\0" in head:
        even, odd, half = head[0::2].count(0), head[1::2].count(0), len(head) // 4
        codec = "utf-16-le" if odd > half and not even else "utf-16-be" if even > half and not odd \
            else None
        try:
            return data.decode(codec) if codec else None
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _scan_path(rel, rules, owner, stats):
    """-> [(line, rule)] for one tracked path, including the whole-file rules (line 0)."""
    path = ROOT / rel
    if os.path.islink(path):                # git ships the link's target PATH: scan that, never follow
        return scan_text(os.readlink(path), rules, owner, stats=stats)
    found = [(0, "secret-file")] if _SECRET_FILE.match(rel.rsplit("/", 1)[-1]) else []
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            print(f"leak_scan: {rel}: not scanned (oversize, {size} bytes > {MAX_BYTES})",
                  file=sys.stderr)
            return found + [(0, "oversize")]
        data = path.read_bytes()
    except IsADirectoryError:                           # a submodule's gitlink: git ships no content
        print(f"leak_scan: {rel}: not scanned (a submodule, no content shipped)", file=sys.stderr)
        return found
    except OSError as exc:
        print(f"leak_scan: {rel}: not scanned (unreadable: {exc.__class__.__name__})",
              file=sys.stderr)
        return found + [(0, "unreadable")]
    text = _decode(data)
    if text is None:
        print(f"leak_scan: {rel}: not scanned (opaque binary)", file=sys.stderr)
        return found + [(0, "opaque-binary")]
    return found + scan_text(text, rules, owner, _is_config(rel), stats)


def main(argv):
    if len(argv) > 1:
        print("usage: python3 tools/leak_scan.py   (no arguments)", file=sys.stderr)
        return 2
    unexplained = sorted(p for p, why in ALLOW_PATHS.items() if not str(why or "").strip())
    if unexplained:
        print(f"leak_scan: REFUSED: ALLOW_PATHS entries with no reason: {', '.join(unexplained)}",
              file=sys.stderr)
        return 2
    try:
        listed = _git(["ls-files", "-z"])
    except OSError as exc:
        print(f"leak_scan: REFUSED: cannot run git: {exc}", file=sys.stderr)
        return 2
    if listed.returncode:
        print(f"leak_scan: REFUSED: {ROOT} is not a git checkout: {listed.stderr.strip()}",
              file=sys.stderr)
        return 2
    rules = _scrub_rules()
    owner = origin_owner()
    findings, scanned, stats = [], 0, {"allowed": 0}
    for rel in sorted(p for p in listed.stdout.split("\0") if p):
        if rel in ALLOW_PATHS:
            continue
        scanned += 1
        findings += [(rel, n, rule) for n, rule in _scan_path(rel, rules, owner, stats)]
    for rel, n, rule in findings:
        print(f"{rel}:{n}: {rule}")
    unscannable = sum(rule in FILE_RULES[1:] for _, _, rule in findings)
    note = "" if owner else "; origin-owner-url rule skipped (no GitHub origin)"
    print(f"leak_scan: {len(findings)} finding(s) over {scanned} file(s) "
          f"({unscannable} unscannable, {stats['allowed']} allow-marked line(s){note})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
