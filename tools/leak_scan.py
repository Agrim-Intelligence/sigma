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
  * key-body: a run of three or more consecutive lines, EVERY line of which is a 40+ character base64
    run with upper case, lower case and a digit -- a private key body whose header was stripped. One
    non-qualifying line inside a longer run makes the whole maximal run clean. A certificate or
    public-key body is excluded only when its recognized header is immediately adjacent.
  * secret-file: a tracked REGULAR-FILE path whose NAME is a credential container, whatever it holds
    (a symlink returns early in `_scan_path`, so a link named like one is not flagged): `id_rsa` /
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
(only quoted `key = "value"` is caught there, by scrub.py's `credential-assignment`). Also verified
NOT detected (#434, run through `scan_text`): hex-encoded and base64url key bodies; key bodies
whose lines carry a `#` prefix or are quoted and comma-ended; lone-CR line endings; UTF-32 files;
`/var/home/<name>`, `-I/Users/<name>/...` and JSON-escaped `\\/Users\\/<name>` paths; a Hugging Face
`hf_` token; a docker `config.json` `auth` value. The gate reads working-tree bytes of `git ls-files`
paths, so the index and git history are not scanned.

OUTPUT. One line per finding, `<path>:<line>: <rule>` -- the LOCATION only, never the matched value,
then `leak_scan: <n> finding(s) over <m> file(s) (<u> unscannable, <a> allow-marked line(s))`.
EXIT 0 = none; 1 = findings (an unscannable file is one); 2 = cannot scan (git missing, not a git
checkout, scrub rules missing, an `ALLOW_PATHS` entry with no reason, a bad argument).

USAGE
    python3 tools/leak_scan.py            # scans the checkout this file lives in

Cost: one `git ls-files`, one `git remote get-url`, then each rule once over each tracked text file.
MEASURED on this tree (798 tracked files, Apple M-series, Python 3.9): 5.7-7.6 s wall (two runs, #434),
nearly all of it regex time; not re-measured on 3.10+. Linear in bytes, one file at a time, so memory
is bounded by the largest file (`MAX_BYTES`); 10x the tree is ~60-75 s -- a publish/CI gate, never a hook. Stdlib only; Linux, macOS,
Windows.
"""
import ast
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
#: A private key body with its header stripped: 3+ consecutive long, mixed-case-and-digit base64 lines.
_B64_RUN = re.compile(r"(?m)(?:^[ \t]*[A-Za-z0-9+/]{40,}={0,2}[ \t]*\r?(?:\n|\Z)){3,}")
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


def _key_bodies(text):
    """Start offsets of header-less private-key bodies (see `key-body`)."""
    out = []
    for m in _B64_RUN.finditer(text):
        lines = m.group(0).split()
        if not all(re.search(r"[A-Z]", ln) and re.search(r"[a-z]", ln) and re.search(r"\d", ln)
                   for ln in lines):
            continue
        prefix = text[:m.start()]
        if prefix.endswith("\n"):
            prefix = prefix[:-1]
            if prefix.endswith("\r"):
                prefix = prefix[:-1]
        before = prefix.rsplit("\n", 1)[-1].rstrip("\r").strip()
        if re.fullmatch(r"-----BEGIN (?:CERTIFICATE|(?:[A-Z0-9 ]+ )?PUBLIC KEY)-----", before):
            continue                            # only an adjacent public/certificate header owns it
        out.append(m.start())
    return out


def scan_text(text, rules, owner, config=False, stats=None):
    """-> [(line number, rule name)] for one file's text. Pure; the tests drive it directly.
    `rules` is `_scrub_rules()`; `config` adds `config-credential`. `stats`, when a dict, gets
    `allowed` += the number of lines a valid allow marker covers.

    Each rule runs ONCE over the whole text and a match is attributed to the line it starts on --
    per-line matching made ~5.4M regex calls on this tree; whole-text makes ~15k. PEM blocks span
    lines anyway, and count only with a real key body (a base64 run of 40+), never for prose that
    merely names the marker."""
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
