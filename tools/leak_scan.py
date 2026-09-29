#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The publish leak gate: shipped files carry no home path, no secret-shaped string, and no URL into
this repository's own (private until published) GitHub owner (#277, the gate #231's acceptance names).

WHAT IS SCANNED. EVERY file git tracks (`git ls-files`), at any path -- `.sdlc/`, `tests/` and all.
A plugin install copies the whole tracked tree (README "every tracked file is shipped surface";
`tests/test_self_contained.py` pins the same), so no directory is exempt. The only files not read:
  * binary (a NUL byte in the first 8 KiB) or non-UTF-8 -- skipped and COUNTED in the summary;
  * larger than `MAX_BYTES` -- skipped, counted, and each one NAMED on stderr (never silent);
  * `ALLOW_PATHS` -- vendored files, each with a written reason. Empty today: nothing is vendored.
A single line opts out of one rule with an in-file marker that must carry a reason, e.g.
    ... # leak-scan: allow home-path the planted fixture the home rule must see
The marker suppresses exactly <rule> on exactly that line. A marker naming no known rule, or with no
reason, is itself a finding (`allow-marker-malformed`), so a typo cannot silently disarm a line.
Fixtures that need a TOKEN-shaped value build it from fragments at run time instead -- the marker is
for the rare literal that must stay literal (a planted home path in a test's prose, say).

THE RULES.
  * home-path: `/Users/<name>`, `/home/<name>`, `C:\\Users\\<name>` (followed by a separator, a
    non-word character, or end of line), `~<name>/` / `~<name>` at end of line, and the encoded
    `-Users-<name>-` form agent hosts use for per-project directories, where <name> is
    not a placeholder (`you`, `me`, `user`, `USER`, `<...>`, `$USER`, `alice`, `bob`, `nosuch*`, any
    one-letter name, ...; `dev` is NOT one -- it is a real account name on many machines).
    PEM private keys are matched over the whole file and need a real body (40+ base64 chars).
  * secret shapes: ONE source of truth, `skills/agrim-loop/scripts/scrub.py`, loaded by path and
    never copied: its `SHAPE_RULES` (named there) plus the redactor-only shapes in `_SECRET_PATTERNS`
    (PEM private keys, AWS `AKIA`/`ASIA`, classic GitHub `gh[pousr]_`, JWT, bearer/basic auth),
    named from their `[REDACTED:<name>]` label. Its generic unquoted `key: value` pattern is applied
    as `credential-unquoted` ONLY in config-like files (`CONFIG_SUFFIXES`, `.env*`): in prose and
    code it would fire on every `token: str` annotation. Missing tables REFUSE (exit 2): a gate with
    no rules must not print 0 findings. Post-filters (a gate's false-positive budget is not a
    redactor's): a credential / auth value must carry a digit and a letter and not be a
    kebab/snake identifier or a `<placeholder>` / `${VAR}`.
  * origin-owner URL: a link into this checkout's own `origin` owner's OTHER repositories, in every
    spelling GitHub accepts -- `github.com/<owner>/<repo>`, `git@github.com:<owner>/<repo>.git`,
    `ssh://git@github.com/<owner>/...`, `https://<anything>@github.com/<owner>/...`,
    `raw.githubusercontent.com/<owner>/<repo>/...`, `api.github.com/repos/<owner>/<repo>`. Allowed:
    this repository itself (the CI badge) and the documented public slug `_MARKETPLACE_REPO` in
    `skills/agrim-doctor/scripts/doctor.py` (read by `ast`, never imported). No origin, or a
    non-GitHub one: the rule is skipped and the summary says so.

OUTPUT. One line per finding, `<path>:<line>: <rule>` -- the LOCATION only, never the matched value,
then `leak_scan: <n> finding(s) over <m> file(s)`. EXIT 0 = none; 1 = findings; 2 = cannot scan (not
a git checkout, scrub rules missing, a bad argument).

USAGE
    python3 tools/leak_scan.py            # scans the checkout this file lives in

Cost: one `git ls-files`, one `git remote get-url`, then each rule once over each tracked text file.
MEASURED on this tree (610 tracked files, 15.4 MB, Apple M-series): 3.5-4.1 s wall on Python 3.10
and 3.12, nearly all of it regex time (credential-assignment ~0.9 s, the home-path rules ~0.8 s,
each lookbehind-led rule ~0.2 s). Linear in bytes, one file at a time, so memory is bounded by
the largest file (`MAX_BYTES`); 10x the tree is ~40 s -- a publish/CI gate, never a hook. Stdlib
only; Linux, macOS, Windows.
"""
import ast
import bisect
import importlib.util
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_BYTES = 4 * 1024 * 1024
#: Vendored/third-party files exempt from the scan, `{path: reason}`. A reason is mandatory.
ALLOW_PATHS = {}
CONFIG_SUFFIXES = (".json", ".jsonc", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env",
                   ".properties", ".tmpl")

#: Names that stand in for "some user" in documentation, never a real account.
_PLACEHOLDER_USERS = {"you", "me", "user", "username", "runner", "alice", "bob", "someone", "name",
                      "example", "shared", "Shared", "USER", "USERNAME", "jane",
                      "john", "me_", "yourname", "your-name", "your_name", "someuser", "ubuntu"}
_HOME = re.compile(r"(?:(?<![\w.~-])/(?:Users|home)/|(?<![\w])[A-Za-z]:\\{1,2}Users\\{1,2})"
                   r"([A-Za-z0-9._-]+)(?![\w-])")
#: A home path as agent hosts encode it into a directory name (`~/.claude/projects/-Users-<name>-src`).
_ENCODED = re.compile(r"(?<![\w-])-(?:Users|home)-([A-Za-z0-9._]+)-")
_TILDE = re.compile(r"(?:^|(?<=[\s`'\"=:]))~([a-z_][a-z0-9_-]{1,31})(?=/|[`'\")]*[ \t]*$)", re.M)

#: A kebab/snake identifier (`feature-classify-tier1`): words, each at most two trailing digits.
_IDENTIFIER = re.compile(r"[A-Za-z]+\d{0,2}(?:[-_.][A-Za-z]+\d{0,2})+")
_ALLOW = re.compile(r"leak-scan:[ \t]*allow\b(.*)$", re.M)


def _load(rel, name):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scrub_rules():
    """-> (rules, config_rules): `[(name, rx)]` from scrub.py's `SHAPE_RULES` + `_SECRET_PATTERNS`,
    and the generic unquoted key:value rule applied to config files only. Refuses (SystemExit 2)
    when either table is absent or empty."""
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
    rules, config_rules = list(shape), []
    shape_rx = {rx.pattern for _, rx in shape}
    for rx, repl in patterns:
        if rx.pattern in shape_rx:
            continue
        label = re.fullmatch(r"\[REDACTED:([\w-]+)\]", repl) if isinstance(repl, str) else None
        if label:
            rules.append((label.group(1), rx))
        else:
            config_rules.append(("credential-unquoted", rx))
    return rules, config_rules


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
    m = re.search(r"github\.com[:/]+([A-Za-z0-9-]+)/([\w.-]+?)(?:\.git)?/?$", proc.stdout.strip())
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
    return re.compile(r"(?:github\.com[/:]|githubusercontent\.com/|api\.github\.com/repos/)"
                      + re.escape(name) + r"/(?!(?:" + keep + r")(?:\.git)?(?![\w.-]))[\w.-]+",
                      re.I)


def _placeholder(name):
    """A documentation stand-in (`you`, `$USER`, `<user>`, `nosuchuser42`), not a real account."""
    return (name in _PLACEHOLDER_USERS or len(name) == 1  # `/home/u/...`: a one-letter stand-in
            or name.startswith(("$", "<", "%", ".", "nosuch"))
            or not name.strip("."))


def _is_real_value(value):
    value = value.strip("\"'`,;")
    if not value or value.startswith(("<", "${", "$", "{{", "%", "/")) or re.search(r"[?&]", value):
        return False  # a placeholder, a template, or a URL path/query -- not a credential
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


def scan_text(text, rules, owner, config=False, config_rules=()):
    """-> [(line number, rule name)] for one file's text. Pure; the tests drive it directly.
    `rules` is `_scrub_rules()[0]`; `config_rules` its second half, applied only when `config`.

    Each rule runs ONCE over the whole text and a match is attributed to the line it starts on --
    per-line matching made ~5.4M regex calls on this tree; whole-text makes ~15k. PEM blocks span lines anyway, and count only with a real key body (a base64 run of 40+),
    never for prose that merely names the marker."""
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
    for name, rx in [*rules, *(config_rules if config else ())]:
        for m in rx.finditer(text):
            if name == "private-key" and not re.search(r"[A-Za-z0-9+/]{40,}", m.group(0)):
                continue
            if name == "credential-assignment" and not _is_real_value(m.group(1)):
                continue
            if name == "auth" and not _is_real_value(m.group(0).split(None, 1)[1]):
                continue
            if name == "credential-unquoted" and not _is_real_value(
                    re.split(r"[:=]", m.group(0), 1)[1].strip()):
                continue
            hit(m.start(), name)
    if owner:
        for m in _owner_url(owner).finditer(text):
            hit(m.start(), "origin-owner-url")
    known = {n for n, _ in rules} | {n for n, _ in config_rules} | {"home-path", "origin-owner-url"}
    allows = {}
    for m in _ALLOW.finditer(text):
        n = bisect.bisect_right(starts, m.start())
        allow, malformed = _allowed(m.group(0))
        if malformed or not allow <= known:
            hit(m.start(), "allow-marker-malformed")
        else:
            allows[n] = allow
    found = []
    for n in sorted(hits):
        found += [(n, h) for h in dict.fromkeys(hits[n]) if h not in allows.get(n, ())]
    return found


def _is_config(rel):
    base = rel.rsplit("/", 1)[-1]
    return base.startswith(".env") or base.endswith(CONFIG_SUFFIXES)


def main(argv):
    if len(argv) > 1:
        print("usage: python3 tools/leak_scan.py   (no arguments)", file=sys.stderr)
        return 2
    listed = _git(["ls-files", "-z"])
    if listed.returncode:
        print(f"leak_scan: REFUSED: {ROOT} is not a git checkout: {listed.stderr.strip()}",
              file=sys.stderr)
        return 2
    rules, config_rules = _scrub_rules()
    owner = origin_owner()
    findings, scanned, skipped = [], 0, 0
    for rel in sorted(p for p in listed.stdout.split("\0") if p):
        if rel in ALLOW_PATHS:
            continue
        path = ROOT / rel
        try:
            data = path.read_bytes()
        except (IsADirectoryError, FileNotFoundError):
            skipped += 1
            continue
        if len(data) > MAX_BYTES:
            print(f"leak_scan: {rel}: skipped (large, {len(data)} bytes > {MAX_BYTES})",
                  file=sys.stderr)
            skipped += 1
            continue
        if b"\0" in data[:8192]:
            skipped += 1
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        scanned += 1
        findings += [(rel, n, rule) for n, rule in
                     scan_text(text, rules, owner, _is_config(rel), config_rules)]
    for rel, n, rule in findings:
        print(f"{rel}:{n}: {rule}")
    note = "" if owner else "; origin-owner-url rule skipped (no GitHub origin)"
    print(f"leak_scan: {len(findings)} finding(s) over {scanned} file(s) "
          f"({skipped} binary/unreadable/large skipped{note})")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
