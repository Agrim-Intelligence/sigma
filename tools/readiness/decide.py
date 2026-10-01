#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The launch GO/NO-GO checker (#331): computes the verdict docs/launch/decision-rule.md defines,
so nobody can steer the outcome by relabelling a finding or re-flagging a dimension.

THE RULE (docs/launch/decision-rule.md, "The rule"). GO if and only if ALL of:
  1. every GATING dimension in docs/launch/scorecard.json scores >= 3, with at least one evidence
     entry, and every evidence entry of it is a valid link (see EVIDENCE);
  2. zero open issues carry the `launch:blocker` label (pull requests are not issues and are
     dropped);
  3. docs/launch/definition.json has `status: "signed"`, a `signed_by` that is a plain login
     (letters, digits, `-`; at most 39) and a `signed_on` that is a real YYYY-MM-DD date no later
     than today on the checking machine;
  4. the benchmark results file the scorecard names (`benchmark_results`) is under
     docs/launch/evidence/ and is verified on main through git (see MAIN).

WHY THE GATING SET IS PINNED HERE. Which dimensions exist, what they are called and which of them
gate are part of the pre-registered rule, not properties the scorecard's author may set: a
scorecard that marks a gating dimension informational (or the reverse), renames, drops or adds a
dimension, or carries a key the schema does not know (a typo such as `waived`) is MALFORMED
(exit 2), never silently re-weighted. Changing the set is a change to decision-rule.md, merged by
the owner, and tests/test_readiness_decide.py keeps that page's tables and the constants below in
sync.

EVIDENCE. An evidence entry counts only if it is EXACTLY one of:
  * an `http://` or `https://` URL with a host and no whitespace anywhere in the entry (URLs are
    checked for shape only; the checker never fetches them);
  * a canonical relative path, `/`-separated, with no whitespace, no `.` or `..` component, not
    absolute, that names an existing FILE under <repo_root> which still resolves under <repo_root>
    after symlinks are followed, AND is a non-empty regular file (not a symlink) at origin/main.
Anything else (`TODO`, `n/a`, ` x `, a missing, untracked or empty path, `../x`) is NO-GO, and the
reason names the dimension and the bad entry.

MAIN. "At origin/main" is read from git: <repo_root> must be the top of a git work tree that has an
`origin/main` ref. When it is not -- git missing, not a work tree, <repo_root> below the top level,
no origin/main (a single-branch clone, a PR checkout) -- nothing can be verified on main, so every
repository-path evidence entry and the benchmark clause are NO-GO with the reason `cannot verify on
main (<why>)`. There is no offline or checkout-only fallback: a verdict that cannot see main is
never GO. The benchmark file must additionally be a non-empty regular file at origin/main, not a
symlink in the checkout, and byte-identical in the checkout to origin/main. `benchmark_results`
null or blank is NO-GO (not named); a path outside docs/launch/evidence/ (or absolute, or with
`..`) is MALFORMED.

WHAT COUNTS AS "UNKNOWN". Anything that cannot be read is never GO:
  * definition.json absent  -> NO-GO, reason `definition missing` (exit 1) -- the launch definition
    goal may simply not have landed yet; that is a legitimate "not ready", not a broken input;
  * scorecard.json absent, unparsable, a duplicated JSON key, wrong schema, an unknown key, a score
    outside 0-4, a duplicated/unknown/missing/renamed dimension, a flipped gating flag -> MALFORMED
    (exit 2);
  * the blocker list cannot be fetched or parsed, gh exits 0 with no output, or an entry is not an
    object with an integer `number`, a `state` of open/closed (any case) and a `labels` list that
    includes `launch:blocker` -> MALFORMED (exit 2). A failed read is never "zero blockers".
  * an unscored (null) gating dimension -> NO-GO (it has not reached 3).
  * any other unreadable input (a path the OS rejects, JSON nested too deep) -> MALFORMED (exit 2).

WHERE THE BLOCKERS COME FROM. With --blockers-json PATH, a JSON list of REST issue objects -- the
tests always use this, and it is the offline form of the BLOCKER list only (it does not relax
MAIN). An object carrying a `pull_request` object is a pull request and is dropped; a
`pull_request` that is not an object is MALFORMED. Without --blockers-json, one read-only REST call:
    gh api --hostname=<host> "repos/<OWNER>/<NAME>/issues?state=open&labels=launch:blocker&per_page=100" --paginate
(never GraphQL). OWNER/NAME and <host> are always passed explicitly, so gh never resolves them
itself: with --repo OWNER/NAME the host is github.com; without it, <repo_root> must be a git
checkout whose ONLY remote is `origin`, with exactly one URL, of the form https://HOST/OWNER/NAME,
ssh://[user@]HOST[:port]/OWNER/NAME or [user@]HOST:OWNER/NAME (`.git` optional). Several remotes,
none, several URLs, any other URL shape, or no git is MALFORMED -- the checker never guesses which
repository's blockers count. gh runs with GH_REPO and GH_HOST removed from its environment; the
token variables are kept because they only authenticate. OWNER and NAME must each start with a
letter, digit or `_`, contain only letters, digits, `_`, `.`, `-`, and be at most 100 characters.
The checker writes nothing anywhere.

USAGE
    python3 tools/readiness/decide.py <repo_root> [--blockers-json PATH] [--repo OWNER/NAME]

OUTPUT: one line per reason the verdict is not GO (then `info:` lines), then a final line `GO` or
`NO-GO`. A malformed input prints `decide.py: MALFORMED: <detail>` on stderr and nothing on stdout.

EXIT: 0 = GO; 1 = NO-GO; 2 = malformed input or a usage error -- never reported as a verdict.
`--help` prints this usage and exits 2, so no script can read it as GO.

Stdlib only; Python 3.9+; runs on Linux, macOS and Windows (the external programs are `git`,
always, and `gh`, only when --blockers-json is omitted).
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse

SCORECARD = pathlib.Path("docs") / "launch" / "scorecard.json"
DEFINITION = pathlib.Path("docs") / "launch" / "definition.json"
EVIDENCE_DIR = "docs/launch/evidence/"
SCORECARD_SCHEMA = "launch-scorecard/v1"
DEFINITION_SCHEMA = "launch-definition/v1"
PASS_SCORE = 3
MIN_SCORE, MAX_SCORE = 0, 4
CARD_KEYS = frozenset({"schema", "benchmark_results", "dimensions"})
DIM_KEYS = frozenset({"id", "name", "gating", "score", "evidence"})

#: Pinned by docs/launch/decision-rule.md ("Dimensions"). id -> (name, gating). Kept in sync by a
#: test.
DIMENSIONS = {
    "D1": ("Correctness", True),
    "D2": ("Skills", True),
    "D3": ("Outcomes and cost", True),
    "D5": ("Five properties", True),
    "D6": ("Platform", True),
    "D7": ("Onboarding", True),
    "D8": ("Docs", True),
    "D9": ("Upgrade, coexistence, uninstall", True),
    "D10": ("Security, privacy, exposure", True),
    "D11": ("Operations", True),
    "D12": ("Market", False),  # informational; blocks only through B3
    "D13": ("Legal and naming", True),
}

BLOCKER_LABEL = "launch:blocker"
DEFAULT_HOST = "github.com"
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}"
REPO_RE = re.compile(rf"({SEGMENT})/({SEGMENT})")
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}")
SCP_RE = re.compile(r"(?:[A-Za-z0-9._~-]+@)?([A-Za-z0-9][A-Za-z0-9.-]*):(?!/)(.+)")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LOGIN_RE = re.compile(r"[A-Za-z0-9-]{1,39}")
GH_REDIRECT_ENV = ("GH_REPO", "GH_HOST")
REGULAR_MODES = (b"100644", b"100755")


class Malformed(Exception):
    """An input the checker cannot turn into a verdict (exit 2)."""


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _load_json(path, what):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise Malformed(f"{what} not found: {path}")
    except (OSError, ValueError) as exc:
        raise Malformed(f"{what} unreadable: {path}: {exc}")
    try:
        return json.loads(text, object_pairs_hook=_unique_pairs)
    except (ValueError, RecursionError) as exc:
        raise Malformed(f"{what} is not valid JSON: {path}: {exc or type(exc).__name__}")


def _git(root, *args):
    """Run git in root; return CompletedProcess (bytes), or None when git cannot be run."""
    try:
        return subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args],
                              capture_output=True, timeout=60)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def toplevel_problem(root):
    """None when root is the top of a git work tree, else why it is not."""
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc is None:
        return "git cannot be run"
    if proc.returncode != 0:
        return f"{root} is not a git work tree"
    top = proc.stdout.decode("utf-8", "replace").strip()
    try:
        same = pathlib.Path(top).resolve() == root.resolve()
    except (OSError, ValueError):
        same = False
    return None if same else f"{root} is not the top of its git work tree ({top})"


def main_problem(root):
    """None when origin/main can be read from root, else why it cannot."""
    why = toplevel_problem(root)
    if why:
        return why
    ref = _git(root, "rev-parse", "--verify", "-q", "origin/main^{commit}")
    if ref is None or ref.returncode != 0:
        return "no origin/main ref in this checkout"
    return None


def at_main(root, rel):
    """(problem, oid): problem is None when rel is a non-empty regular file at origin/main."""
    proc = _git(root, "ls-tree", "-l", "-z", "origin/main", "--", rel)
    if proc is None or proc.returncode != 0:
        return "could not be read at origin/main", None
    entries = [e for e in proc.stdout.split(b"\0") if e]
    if not entries:
        return "is not tracked at origin/main", None
    meta, _, path = entries[0].partition(b"\t")
    fields = meta.split()
    if len(entries) != 1 or len(fields) != 4 or path != rel.encode("utf-8"):
        return "is not tracked at origin/main", None
    mode, kind, oid, size = fields
    if kind != b"blob" or mode not in REGULAR_MODES:
        return "is not a regular file at origin/main (a symlink or directory)", None
    if size == b"0":
        return "is empty at origin/main", None
    return None, oid


def _path_shape_problem(entry):
    rel = pathlib.PurePosixPath(entry)
    if "\\" in entry or rel.is_absolute() or re.match(r"^[A-Za-z]:", entry):
        return "is not a relative /-separated repository path"
    if ".." in entry.split("/"):
        return "escapes the repository (`..`)"
    if str(rel) != entry or "." in entry.split("/"):
        return "is not a canonical path (no `.`, `//` or trailing `/`)"
    return None


def evidence_problem(entry, root, main_why):
    """None if `entry` is a valid evidence link, else why it is not."""
    if not entry or any(ch.isspace() for ch in entry):
        return "is not a link: is empty or contains whitespace"
    low = entry.lower()
    if low.startswith(("http://", "https://")):
        try:
            host = urllib.parse.urlsplit(entry).hostname
        except ValueError:
            return "is not a link: is a URL that cannot be parsed"
        return None if host else "is not a link: is a URL with no host"
    if "://" in entry:
        return "is not a link: is a URL that is not http(s)"
    shape = _path_shape_problem(entry)
    if shape:
        return "is not a link: " + shape
    try:
        target = root.joinpath(*entry.split("/"))
        inside = target.resolve().is_relative_to(root.resolve())
        if inside and not target.is_file():
            return ("is not a link: is neither an http(s) URL nor an existing file in the "
                    "repository")
    except (OSError, ValueError) as exc:
        return f"is not a link: cannot be read as a repository path ({type(exc).__name__})"
    if not inside:
        return "is not a link: resolves outside the repository"
    if main_why:
        return f"cannot verify on main ({main_why})"
    problem, _ = at_main(root, entry)
    return problem


def check_scorecard(card, root, main_why):
    """Validate the scorecard and return (reasons, infos). Raises Malformed."""
    if not isinstance(card, dict) or card.get("schema") != SCORECARD_SCHEMA:
        raise Malformed(f"scorecard schema must be {SCORECARD_SCHEMA!r}")
    extra = sorted(set(card) - CARD_KEYS)
    if extra:
        raise Malformed(f"scorecard has unknown key(s) {extra} (allowed: {sorted(CARD_KEYS)})")
    dims = card.get("dimensions")
    if not isinstance(dims, list):
        raise Malformed("scorecard `dimensions` must be a list")
    reasons, infos, seen = [], [], set()
    for i, dim in enumerate(dims):
        if not isinstance(dim, dict):
            raise Malformed(f"dimension #{i} is not an object")
        did, name = dim.get("id"), dim.get("name")
        if not isinstance(did, str) or did not in DIMENSIONS:
            raise Malformed(f"dimension #{i}: unknown id {did!r} (the rule pins {sorted(DIMENSIONS)})")
        if did in seen:
            raise Malformed(f"dimension {did} appears twice")
        seen.add(did)
        extra = sorted(set(dim) - DIM_KEYS)
        if extra:
            raise Malformed(f"dimension {did}: unknown key(s) {extra} (allowed: {sorted(DIM_KEYS)})")
        pinned_name, pinned_gating = DIMENSIONS[did]
        if name != pinned_name:
            raise Malformed(f"dimension {did}: `name` is {name!r}, but the rule pins "
                            f"{pinned_name!r} (change docs/launch/decision-rule.md, not the scorecard)")
        gating = dim.get("gating")
        if gating is not pinned_gating:
            raise Malformed(f"dimension {did}: `gating` is {gating!r}, but the rule pins "
                            f"{pinned_gating} (change docs/launch/decision-rule.md, not the scorecard)")
        score = dim.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, int)):
            raise Malformed(f"dimension {did}: score {score!r} is not an integer or null")
        if score is not None and not MIN_SCORE <= score <= MAX_SCORE:
            raise Malformed(f"dimension {did}: score {score} is outside {MIN_SCORE}-{MAX_SCORE}")
        evidence = dim.get("evidence")
        if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
            raise Malformed(f"dimension {did}: `evidence` must be a list of strings")
        label = f"{did} {name}"
        if not gating:
            shown = "unscored" if score is None else f"score {score}"
            infos.append(f"info: {label} is informational ({shown}); it blocks only through B3")
        elif score is None:
            reasons.append(f"{label}: not scored (gating; needs >= {PASS_SCORE} with evidence)")
        elif score < PASS_SCORE:
            reasons.append(f"{label}: score {score} < {PASS_SCORE} (gating)")
        elif not evidence:
            reasons.append(f"{label}: score {score} has no evidence link (gating)")
        else:
            for entry in evidence:
                problem = evidence_problem(entry, root, main_why)
                if problem:
                    reasons.append(f"{label}: evidence {entry!r} {problem} (gating)")
    missing = sorted(set(DIMENSIONS) - seen)
    if missing:
        raise Malformed(f"scorecard is missing dimension(s) {missing}")
    if "benchmark_results" not in card:
        raise Malformed("scorecard has no `benchmark_results` key (use null until results exist)")
    return reasons + check_benchmark(card["benchmark_results"], root, main_why), infos


def check_benchmark(bench, root, main_why):
    """Return the reasons the benchmark clause fails. Raises Malformed."""
    if bench is None or (isinstance(bench, str) and not bench.strip()):
        return ["benchmark results: not named in the scorecard"]
    if not isinstance(bench, str):
        raise Malformed("`benchmark_results` must be a relative path string or null")
    if any(ch.isspace() for ch in bench) or _path_shape_problem(bench):
        raise Malformed(f"`benchmark_results` must be a path inside the repository: {bench!r}")
    if not bench.startswith(EVIDENCE_DIR) or len(bench.split("/")) <= 3:
        raise Malformed(f"`benchmark_results` must be a file under {EVIDENCE_DIR}: {bench!r}")
    if main_why:
        return [f"benchmark results: cannot verify on main ({main_why})"]
    problem, oid = at_main(root, bench)
    if problem:
        return [f"benchmark results: {bench} {problem}"]
    local = root.joinpath(*bench.split("/"))
    if local.is_symlink():
        return [f"benchmark results: {bench} is a symlink in this checkout"]
    if not local.is_file():
        return [f"benchmark results: {bench} is on origin/main but missing from this checkout"]
    local_oid = _git(root, "hash-object", "--", bench)
    if local_oid is None or local_oid.returncode != 0 or local_oid.stdout.strip() != oid:
        return [f"benchmark results: {bench} in this checkout differs from origin/main"]
    return []


def check_definition(root, today=None):
    path = root / DEFINITION
    if not path.exists():
        return ["definition missing: docs/launch/definition.json does not exist"]
    data = _load_json(path, "definition")
    if not isinstance(data, dict) or data.get("schema") != DEFINITION_SCHEMA:
        raise Malformed(f"definition schema must be {DEFINITION_SCHEMA!r}")
    status = data.get("status")
    if status != "signed":
        return [f"definition not signed: status is {status!r}"]
    reasons = []
    signed_by = data.get("signed_by")
    if not (isinstance(signed_by, str) and LOGIN_RE.fullmatch(signed_by)):
        reasons.append(f"definition not signed: status is 'signed' but signed_by is {signed_by!r} "
                       "(needs a plain login: letters, digits, '-', at most 39)")
    signed_on = data.get("signed_on")
    date = None
    if isinstance(signed_on, str) and DATE_RE.fullmatch(signed_on):
        try:
            date = datetime.date.fromisoformat(signed_on)
        except ValueError:
            date = None
    today = today or datetime.date.today()
    if date is None:
        reasons.append(f"definition not signed: status is 'signed' but signed_on is "
                       f"{signed_on!r} (needs a real YYYY-MM-DD date)")
    elif date > today:
        reasons.append(f"definition not signed: status is 'signed' but signed_on is "
                       f"{signed_on!r}, later than today ({today.isoformat()})")
    return reasons


def _decode_pages(text):
    """`gh api --paginate` prints one JSON array per page, concatenated. Decode all of them."""
    decoder, items, idx = json.JSONDecoder(object_pairs_hook=_unique_pairs), [], 0
    text = text.strip()
    while idx < len(text):
        page, end = decoder.raw_decode(text, idx)
        if not isinstance(page, list):
            raise ValueError("a page is not a JSON list")
        items.extend(page)
        idx = end
        while idx < len(text) and text[idx].isspace():
            idx += 1
    return items


def repo_from_url(url):
    """(host, OWNER/NAME) from a remote URL of a supported shape, or None."""
    scp = SCP_RE.fullmatch(url)
    if scp:
        host, path = scp.groups()
    else:
        try:
            parts = urllib.parse.urlsplit(url)
            host = parts.hostname
        except ValueError:
            return None
        if (parts.scheme.lower() not in ("https", "http", "ssh") or not host or parts.query
                or parts.fragment):
            return None
        path = parts.path
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-len(".git")]
    if not host or not HOST_RE.fullmatch(host) or not REPO_RE.fullmatch(path):
        return None
    return host.lower(), path


def default_repo(root):
    """(host, OWNER/NAME) of the checkout's only remote, `origin`. Raises Malformed."""
    why = toplevel_problem(root)
    if why:
        raise Malformed(f"--repo not given and {why}; pass --repo OWNER/NAME")
    proc = _git(root, "remote")
    if proc is None or proc.returncode != 0:
        raise Malformed("--repo not given and the checkout's remotes cannot be listed; "
                        "pass --repo OWNER/NAME")
    remotes = proc.stdout.decode("utf-8", "replace").split()
    if remotes != ["origin"]:
        raise Malformed(f"--repo not given and the checkout's remotes are {remotes} (need exactly "
                        "one, named origin); pass --repo OWNER/NAME")
    proc = _git(root, "remote", "get-url", "--all", "origin")
    urls = [] if proc is None or proc.returncode != 0 else \
        proc.stdout.decode("utf-8", "replace").split()
    if len(urls) != 1:
        raise Malformed(f"--repo not given and origin has {len(urls)} URLs (need exactly one); "
                        "pass --repo OWNER/NAME")
    found = repo_from_url(urls[0])
    if found is None:
        # The URL itself is not echoed: it may carry a credential (https://user:token@host/...).
        raise Malformed("--repo not given and origin's URL is not https://HOST/OWNER/NAME, "
                        "ssh://HOST/OWNER/NAME or HOST:OWNER/NAME; pass --repo OWNER/NAME")
    return found


def fetch_blockers(root, repo, run=subprocess.run):
    host, slug = (DEFAULT_HOST, repo) if repo else default_repo(root)
    argv = ["gh", "api", f"--hostname={host}",
            f"repos/{slug}/issues?state=open&labels={BLOCKER_LABEL}&per_page=100", "--paginate"]
    env = {k: v for k, v in os.environ.items() if k not in GH_REDIRECT_ENV}
    try:
        proc = run(argv, cwd=str(root), env=env, capture_output=True, text=True, timeout=120)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise Malformed(f"could not read open {BLOCKER_LABEL} issues: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise Malformed(f"could not read open {BLOCKER_LABEL} issues from {host}/{slug} (gh exit "
                        f"{proc.returncode}): {detail[0]}")
    if not (proc.stdout or "").strip():
        raise Malformed(f"gh exited 0 with no output for the {BLOCKER_LABEL} list; an empty read "
                        "is not zero blockers")
    try:
        return _decode_pages(proc.stdout)
    except (ValueError, RecursionError) as exc:
        raise Malformed(f"gh returned unparsable JSON for the blocker list: {exc}")


def open_blockers(items):
    """Issue numbers from a list of REST issue objects, pull requests dropped. Raises Malformed."""
    if not isinstance(items, list):
        raise Malformed("the blocker list must be a JSON list of issue objects")
    numbers = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise Malformed(f"blocker entry #{i} is not an object")
        num = item.get("number")
        if isinstance(num, bool) or not isinstance(num, int):
            raise Malformed(f"blocker entry #{i} has no integer `number`")
        state = item.get("state")
        if not isinstance(state, str) or state.lower() not in ("open", "closed"):
            raise Malformed(f"blocker entry #{i} (#{num}) has state {state!r}; "
                            "expected open or closed")
        labels = item.get("labels")
        if not isinstance(labels, list) or not all(
                isinstance(lb, dict) and isinstance(lb.get("name"), str) for lb in labels):
            raise Malformed(f"blocker entry #{i} (#{num}) has no `labels` list of objects with a "
                            "`name`")
        if BLOCKER_LABEL not in {lb["name"].lower() for lb in labels}:
            raise Malformed(f"blocker entry #{i} (#{num}) does not carry {BLOCKER_LABEL}; the list "
                            "is not the blocker list")
        if "pull_request" in item:
            if not isinstance(item["pull_request"], dict):
                raise Malformed(f"blocker entry #{i} (#{num}) has a `pull_request` that is not an "
                                "object")
            continue  # a pull request, not an issue
        if state.lower() == "open":
            numbers.append(num)
    return sorted(set(numbers))


def decide(root, blockers_json=None, repo=None, run=subprocess.run):
    """Return (lines, verdict). Raises Malformed."""
    root = pathlib.Path(root)
    if not root.is_dir():
        raise Malformed(f"repo_root is not a directory: {root}")
    card = _load_json(root / SCORECARD, "scorecard")
    reasons, notes = check_scorecard(card, root, main_problem(root))
    reasons = check_definition(root) + reasons
    if blockers_json is not None:
        items = _load_json(pathlib.Path(blockers_json), "blockers list")
    else:
        items = fetch_blockers(root, repo, run=run)
    for num in open_blockers(items):
        reasons.append(f"open {BLOCKER_LABEL}: #{num}")
    verdict = "NO-GO" if reasons else "GO"
    return reasons + notes, verdict


def main(argv=None, run=subprocess.run):
    ap = argparse.ArgumentParser(prog="decide.py", description=__doc__.split("\n\n")[0],
                                 epilog="Exit: 0 GO, 1 NO-GO, 2 malformed input or usage "
                                        "(including --help).")
    ap.add_argument("repo_root")
    ap.add_argument("--blockers-json", metavar="PATH")
    ap.add_argument("--repo", metavar="OWNER/NAME")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        return 2  # --help and usage errors alike: never the GO code
    try:
        if args.repo is not None and not REPO_RE.fullmatch(args.repo):
            raise Malformed(f"--repo must be OWNER/NAME (each starting with a letter, digit or "
                            f"`_`; letters, digits, `_`, `.`, `-`; at most 100), got {args.repo!r}")
        lines, verdict = decide(args.repo_root, args.blockers_json, args.repo, run=run)
    except Malformed as exc:
        print(f"decide.py: MALFORMED: {exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, RecursionError) as exc:
        print(f"decide.py: MALFORMED: unreadable input: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(verdict)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
