#!/usr/bin/env python3
"""Secret-safe launch exposure scanner (#333).

Usage: exposure_scan.py {history|tracked|refs} REPO [--json PATH] [--propose PATH]

`history` examines reachable text blobs, while `tracked` examines HEAD only.  It never prints a
matched value: reports identify the rule, location and a redacted preview.  Exit 0 means clean,
1 means findings (including stale allowlist entries), and 2 means bad input or git failure.

Allowlist (`docs/launch/exposure-allowlist.json`, tracked mode only): an entry names a path and a
rule and is scoped to content, never to the path alone: `blob` (one exact Git blob) or `lines`
(sha256 of each reviewed matched line, duplicates counted).  A `lines` entry survives an edit
elsewhere in the file, and goes stale (the scan exits 1) when a reviewed line changes or is
removed; a match it does not list, or one more copy of a listed line, is a finding.

Re-triage, run by whoever changes a file that carries an entry or adds a finding, with the
private-pattern file supplied from outside the repository: run
`exposure_scan.py tracked . --json <scratch> --propose <scratch-draft>`, read every row the scan
names, and only for a fixture, detector prose or placeholder copy its entry into the allowlist
with a one-line reason.  A real secret or private reference is never allowlisted: remove it.
The history mode never applies the allowlist.  The default allowlist is read from HEAD, never from
the working tree; the report records its source, blob and how many findings it covered (counts,
never values).

Limits of "exit 0 means clean" that the allowlist neither causes nor fixes: blobs over 2 MiB and
blobs holding a NUL byte (binary, UTF-16) are counted as skipped, not scanned; a key block with no
END line matches nothing; `legacy-issue-reference` is count-only; `private-pattern-N` names are the
pattern's line number (and are refused as allowlist rules: a private reference is removed, never
allowed), so re-run the re-triage after the private-pattern file changes.  A CRLF/LF conversion of a
file holding a key block makes its entry stale (fails closed), and a match spanning many lines (a
key pattern fixture) goes stale on an edit of any of them: noisy, never blind.  A hash of a
short guessable line can be confirmed by guessing: never allowlist a line holding a real secret.
"""
import argparse
import collections
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

MAX_BLOB_BYTES = 2 * 1024 * 1024
DEFAULT_ALLOWLIST = "docs/launch/exposure-allowlist.json"
_OBJECT_ID = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")
_LINE_HASH = re.compile(r"^[0-9a-f]{64}$")
DRAFT_REASON = "TRIAGE REQUIRED: replace with the reviewed reason"
PRIVATE_RULES = (
    ("absolute-home-path", re.compile(r"(?:/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|/private/tmp/claude-[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\)")),
    ("email-address", re.compile(r"(?i)\b(?!(?:[A-Z0-9._%+-]+@(?:example\.com|example\.invalid|users\.noreply\.github\.com)|noreply@anthropic\.com)\b)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("legacy-issue-reference", re.compile(r"#\d{4,}\b")),
)


def _load_scrub():
    path = pathlib.Path(__file__).resolve().parents[2] / "skills" / "sigma-loop" / "scripts" / "scrub.py"
    spec = importlib.util.spec_from_file_location("sigma_exposure_scrub", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*args, cwd=None, text=True):
    proc = subprocess.run(args, cwd=cwd, text=text, capture_output=True)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or "git command failed")
    return proc.stdout if text else proc.stdout


def _patterns(path=None, repo=None):
    scrub = _load_scrub()
    seen, rules = set(), []
    for name, rx in tuple(scrub.SHAPE_RULES) + tuple(scrub.COMMIT_SHAPE_RULES):
        if name not in seen:
            seen.add(name); rules.append((name, rx))
    rules.extend(PRIVATE_RULES)
    pattern_path = path or os.environ.get("SIGMA_LEAK_PATTERNS")
    if pattern_path:
        try:
            source = pathlib.Path(pattern_path).resolve()
            if repo and (source == pathlib.Path(repo).resolve() or pathlib.Path(repo).resolve() in source.parents):
                raise ValueError("private patterns file must live outside the scanned repository")
            for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                raw = raw.strip()
                if raw and not raw.startswith("#"):
                    rx = re.compile(raw, re.I)
                    if rx.match(""):
                        raise ValueError("private pattern %d matches empty text" % number)
                    rules.append(("private-pattern-%d" % number, rx))
        except (OSError, re.error) as exc:
            raise ValueError("cannot load private patterns: %s" % exc)
    return rules


def _allowlist(path, text=None):
    """Load the allowlist.  An entry is scoped to content, never to a bare path and rule.

    ``blob``: the exact reviewed Git blob (any edit of the file makes it stale).
    ``lines``: sha256 of every reviewed matched line (the whole line, or every line a multi-line
    match spans), one hash per match, duplicates counted.  It survives an edit elsewhere in the
    file and goes stale, and the scan fails, when a reviewed line changes or when a match appears
    that the list does not cover.
    """
    if text is None and (not path or not pathlib.Path(path).exists()):
        return []
    try:
        data = json.loads(text if text is not None else pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("invalid allowlist: %s" % exc)

    def scoped(x):
        has_blob, has_lines = "blob" in x, "lines" in x
        if has_blob == has_lines:
            return False
        if has_blob:
            return isinstance(x["blob"], str) and bool(_OBJECT_ID.fullmatch(x["blob"]))
        return (isinstance(x["lines"], list) and bool(x["lines"])
                and all(isinstance(h, str) and _LINE_HASH.fullmatch(h) for h in x["lines"]))
    if not isinstance(data, list) or any(
            not isinstance(x, dict) or not x.get("path") or not x.get("rule") or not x.get("reason")
            or not scoped(x) for x in data):
        raise ValueError("allowlist entries require path, rule, reason and exactly one of "
                         "blob (a Git object id) or lines (sha256 of each reviewed matched line)")
    if any(str(x["reason"]).startswith("TRIAGE REQUIRED") for x in data):
        raise ValueError("allowlist entry still carries a draft reason; triage it first")
    if any(str(x["rule"]).startswith("private-pattern") for x in data):
        raise ValueError("a private-pattern finding is never allowlisted: remove the reference")
    keys = [(x["path"], x["rule"]) for x in data]
    if len(keys) != len(set(keys)):
        raise ValueError("allowlist has more than one entry for the same path and rule")
    return data


def _safe_preview(text, match, rule, rules):
    line = text.count("\n", 0, match.start()) + 1
    start = text.rfind("\n", 0, match.start()) + 1
    end = text.find("\n", match.end())
    raw = text[start:len(text) if end < 0 else end]
    # Redact every rule on this line, not only the finding being rendered: one diagnostic must
    # never reveal a neighbouring credential or private reference.
    preview = raw
    for name, rx in rules:
        preview = rx.sub("[REDACTED:%s]" % name, preview)
    # Allowlist identity: every full line the match spans, so a multi-line match (a key block)
    # is covered in whole.  Hashed, never stored or printed as text.
    digest = hashlib.sha256(raw.strip().encode("utf-8", "replace")).hexdigest()
    return line, preview[:240], digest


def _matches(text, rules):
    for rule, rx in rules:
        for match in rx.finditer(text):
            line, preview, digest = _safe_preview(text, match, rule, rules)
            yield rule, line, preview, digest


def _history_blobs(repo):
    """Return every reachable blob with every path and commit that contains it.

    ``rev-list --objects`` identifies the reachable object universe, but deliberately
    de-duplicates an object and can therefore retain only its first path.  Walk every
    reachable commit tree as well so an identical blob at renamed or copied paths is
    reported completely.
    """
    object_ids = {row.split(" ", 1)[0] for row in
                  _run("git", "rev-list", "--objects", "--all", cwd=repo).splitlines()}
    result = {}
    for commit in _run("git", "rev-list", "--all", cwd=repo).splitlines():
        for entry in _run("git", "ls-tree", "-r", "-z", commit, cwd=repo).split("\0"):
            if not entry:
                continue
            metadata, _, path = entry.partition("\t")
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != "blob" or fields[2] not in object_ids:
                continue
            sha = fields[2]
            record = result.setdefault(sha, {"paths": set(), "commits": []})
            record["paths"].add(path)
            if commit not in record["commits"]:
                record["commits"].append(commit)
    return result.items()


def _tracked_blobs(repo):
    paths = _run("git", "ls-files", "-z", cwd=repo).split("\0")
    result = {}
    for path in filter(None, paths):
        sha = _run("git", "rev-parse", "HEAD:" + path, cwd=repo).strip()
        result.setdefault(sha, {"paths": set(), "commits": []})["paths"].add(path)
    head = _run("git", "rev-parse", "HEAD", cwd=repo).strip()
    for record in result.values():
        record["commits"] = [head]
    return result.items()


def _batch_metadata(repo, shas):
    """Read Git object metadata in one process instead of one process per blob."""
    process = subprocess.run(["git", "cat-file", "--batch-check"], cwd=repo,
                             input="".join(sha + "\n" for sha in shas).encode(), capture_output=True)
    if process.returncode:
        raise ValueError(process.stderr.decode("utf-8", "replace").strip() or "git cat-file metadata failed")
    rows = process.stdout.splitlines()
    if len(rows) != len(shas):
        raise ValueError("cannot inspect reachable Git object")
    meta = {}
    for sha, row in zip(shas, rows):
        fields = row.decode("ascii", "replace").split()
        if len(fields) != 3 or fields[0] != sha:
            raise ValueError("cannot inspect reachable Git object")
        meta[sha] = (fields[1], int(fields[2]))
    return meta


def _batch_contents(repo, shas):
    """Read selected text blobs in one batch, after their size limit was checked."""
    process = subprocess.run(["git", "cat-file", "--batch"], cwd=repo,
                             input="".join(sha + "\n" for sha in shas).encode(), capture_output=True)
    if process.returncode:
        raise ValueError(process.stderr.decode("utf-8", "replace").strip() or "git cat-file batch failed")
    output = process.stdout
    offset = 0
    contents = {}
    for sha in shas:
        end = output.find(b"\n", offset)
        fields = output[offset:end].decode("ascii", "replace").split()
        offset = end + 1
        if len(fields) != 3 or fields[0] != sha or fields[1] != "blob":
            raise ValueError("cannot read reachable Git blob")
        size = int(fields[2])
        data = output[offset:offset + size]
        offset += size + 1
        contents[sha] = data
    return contents


def scan(repo, mode, allowlist=None, patterns=None, propose=None, allowed=None):
    repo = pathlib.Path(repo).resolve()
    if not (repo / ".git").exists():
        raise ValueError("repository must be a working tree")
    if mode == "tracked" and _run("git", "status", "--porcelain", "--untracked-files=no", cwd=repo).strip():
        # The scan reads committed blobs but the allowlist from the working tree: a verdict on a
        # dirty tree would describe a tree nobody committed.
        raise ValueError("tracked files differ from HEAD; commit them first (the scan reads HEAD)")
    # The allowlist is for the tracked tree only.  History is an audit of every reachable blob:
    # a reviewed line at HEAD says nothing about what an older blob of that path held.
    # The default allowlist is read from HEAD, like the blobs: an untracked or edited copy in the
    # working tree cannot make a verdict.  An explicit --allowlist is for hermetic controls and is
    # recorded in the report as such.
    allow, source, allow_blob = [], "none", None
    if mode == "tracked" and allowlist:
        allow, source = _allowlist(allowlist), "explicit"
    elif mode == "tracked":
        try:
            allow_blob = _run("git", "rev-parse", "HEAD:" + DEFAULT_ALLOWLIST, cwd=repo).strip()
        except ValueError:
            allow_blob = None                      # not in HEAD: no allowlist
        if allow_blob:
            allow, source = _allowlist(None, _run("git", "show", allow_blob, cwd=repo)), "HEAD"
    allow_by_key = {(x["path"], x["rule"]): x for x in allow}
    used, covered_count = set(), 0
    findings, skipped, counts = [], {"oversized": 0, "binary": 0}, {"legacy_issue_references": 0}
    rules = _patterns(patterns, repo)
    blobs = _history_blobs(repo) if mode == "history" else _tracked_blobs(repo)
    blob_records = dict(blobs)
    metadata = _batch_metadata(repo, list(blob_records))
    readable = [sha for sha, (kind, size) in metadata.items()
                if kind == "blob" and size <= MAX_BLOB_BYTES]
    contents = _batch_contents(repo, readable)
    for sha, record in blob_records.items():
        paths, commits = record["paths"], record["commits"]
        kind, size = metadata[sha]
        if kind != "blob":
            continue
        if size > MAX_BLOB_BYTES:
            skipped["oversized"] += 1; continue
        data = contents[sha]
        if b"\0" in data:
            skipped["binary"] += 1; continue
        text = data.decode("utf-8", "replace")
        # A (rule, blob) is one finding even where Git has multiple paths to that content.  This
        # avoids multiplying a secret exposure while preserving every reachable path and commit.
        # The output identity is (rule, blob), never one finding per repeated token.  The only
        # public-facing legacy issue signal is its aggregate count, as promised by the goal.
        per_rule = {}
        for rule, line, preview, digest in _matches(text, rules):
            if rule == "legacy-issue-reference":
                counts["legacy_issue_references"] += 1
                continue
            if rule not in per_rule:
                per_rule[rule] = [line, preview, 0, collections.Counter()]
            per_rule[rule][2] += 1
            per_rule[rule][3][digest] += 1
        for rule, (line, preview, hit_count, digests) in per_rule.items():
            visible = []
            for path in sorted(paths):
                entry = allow_by_key.get((path, rule))
                if entry is None:
                    visible.append(path); continue
                index = allow.index(entry)
                if "blob" in entry:
                    covered = entry["blob"].lower() == sha.lower()
                    if covered:
                        used.add(index)
                else:
                    reviewed = collections.Counter(entry["lines"])
                    # Covered only when every match is a reviewed line, counted: a new finding,
                    # or one more copy of a reviewed line, is not covered.  The entry counts as
                    # live only when it equals the observed matches, so an edited or removed
                    # reviewed line makes it stale and the scan fails until it is re-triaged.
                    covered = not (digests - reviewed)
                    if covered and digests == reviewed:
                        used.add(index)
                    elif covered and propose is not None:
                        # live matches are a strict part of the entry: draft its replacement
                        propose[(path, rule)] = propose.get((path, rule), collections.Counter()) + digests
                if not covered:
                    visible.append(path)
                else:
                    covered_count += 1
                    if allowed is not None:
                        allowed.append((rule, path, line, sha, preview))
            if visible:
                findings.append({"rule": rule, "path": visible[0], "paths": visible, "line": line,
                                 "blob": sha, "commits": commits, "matches": hit_count, "preview": preview})
                if propose is not None:
                    for path in visible:
                        propose[(path, rule)] = propose.get((path, rule), collections.Counter()) + digests
    stale = [{"path": x["path"], "rule": x["rule"]}
             for index, x in enumerate(allow) if index not in used]
    # Counts and the allowlist blob only: what the allowlist covered is auditable, never a value.
    return {"schema": "sigma.launch-exposure/v1", "mode": mode, "findings": findings,
            "stale_allowlist": stale, "skipped": skipped, "counts": counts,
            "allowlist": {"source": source, "blob": allow_blob, "entries": len(allow),
                          "covered_findings": covered_count}}


def scan_refs(repo):
    repo = pathlib.Path(repo).resolve()
    remote = []
    try:
        rows = _run("git", "ls-remote", "--heads", "--tags", "origin", cwd=repo).splitlines()
    except ValueError as exc:
        raise ValueError("cannot list origin refs: %s" % exc)
    remote_tags = set()
    for row in rows:
        sha, ref = row.split("\t", 1)
        if ref.endswith("^{}"):
            continue
        kind, name = ("branch", ref.removeprefix("refs/heads/")) if ref.startswith("refs/heads/") else ("tag", ref.removeprefix("refs/tags/"))
        remote.append({"name": name, "kind": kind, "sha": sha})
        if kind == "tag": remote_tags.add(name)
    local_tags = set(filter(None, _run("git", "tag", "--list", cwd=repo).splitlines()))
    return {"schema": "sigma.launch-exposure-refs/v1", "remote": sorted(remote, key=lambda x: (x["kind"], x["name"])),
            "local_only_tags": sorted(local_tags - remote_tags),
            "owner_checklist": ["enable secret scanning and push protection on the public repository", "delete or keep backup/public-snapshot-* tag", "decide the fate of remote branches"]}


def _display_path(path):
    """Keep public evidence free of paths absent from the shipped snapshot."""
    if path.startswith(("tests/", "tools/")):
        return "[historical non-shipped path]"
    return path


def _write(report, path):
    """Write paired machine and human evidence without ever reconstructing a matched value."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Launch exposure scan", "", "Mode: `%s`" % report.get("mode", "refs"), ""]
    if "findings" in report:
        lines += ["| Rule | Path | Line | Blob |", "| --- | --- | ---: | --- |"]
        lines += ["| %s | `%s` | %s | `%s` |" % (x["rule"], _display_path(x["path"]), x["line"], x["blob"][:12])
                  for x in report["findings"]]
        lines += ["", "Skipped: oversized=%d, binary=%d." %
                  (report["skipped"]["oversized"], report["skipped"]["binary"])]
        lines += ["Legacy issue references: %d (count only)." % report["counts"]["legacy_issue_references"]]
    else:
        lines += ["Remote refs: %d; local-only tags: %d." %
                  (len(report["remote"]), len(report["local_only_tags"]))]
        lines += ["", "## Owner checklist", *["- " + x for x in report["owner_checklist"]]]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("verb", choices=("history", "tracked", "refs")); parser.add_argument("repo")
    parser.add_argument("--json"); parser.add_argument("--allowlist"); parser.add_argument("--patterns")
    parser.add_argument("--list-allowed", action="store_true", help="tracked only: also print every "
                        "finding an allowlist entry covers (rule, path, line, blob, redacted preview), "
                        "so a reviewer can read what an allowlist diff hides")
    parser.add_argument("--propose", help="tracked only: write draft allowlist entries (hashes, no "
                        "values) for the uncovered findings; each needs a human-written reason")
    args = parser.parse_args(argv)
    try:
        if args.propose and args.verb != "tracked":
            raise ValueError("--propose is only for the tracked mode")
        if args.propose:
            # Draft hashes describe uncovered findings, which may be real: never inside the repo
            # (committable), and never over the evidence.
            target = pathlib.Path(args.propose).resolve()
            root = pathlib.Path(args.repo).resolve()
            evidence_paths = ({pathlib.Path(args.json).resolve(), pathlib.Path(args.json).with_suffix(".md").resolve()}
                              if args.json else set())
            if target == root or root in target.parents or target in evidence_paths:
                raise ValueError("--propose must name a file outside the repository and not the evidence")
        if args.list_allowed and args.verb != "tracked":
            raise ValueError("--list-allowed is only for the tracked mode")
        drafts = {} if args.propose else None
        allowed = [] if args.list_allowed else None
        report = scan_refs(args.repo) if args.verb == "refs" else scan(args.repo, args.verb, args.allowlist, args.patterns, drafts, allowed)
    except ValueError as exc:
        print("exposure-scan: " + str(exc), file=sys.stderr); return 2
    if args.json:
        evidence = args.json
    else:
        sha = _run("git", "rev-parse", "HEAD", cwd=args.repo).strip()[:12]
        stem = "exposure" if args.verb == "history" else args.verb
        evidence = pathlib.Path(args.repo) / "docs" / "launch" / "evidence" / (stem + "-" + sha + ".json")
    _write(report, evidence)
    if args.propose:
        pathlib.Path(args.propose).write_text(json.dumps(
            [{"path": path, "rule": rule, "reason": DRAFT_REASON, "lines": sorted(digests.elements())}
             for (path, rule), digests in sorted(drafts.items())], indent=2) + "\n", encoding="utf-8")
    if args.verb == "refs":
        print("refs: %d remote; %d local-only tags" % (len(report["remote"]), len(report["local_only_tags"])))
        return 0
    for item in report["findings"]:
        print("%s %s:%d %s %s" % (item["rule"], item["path"], item["line"], item["blob"][:12], item["preview"]))
    for rule, path, line, sha, preview in sorted(allowed or []):
        print("allowed %s %s:%d %s %s" % (rule, path, line, sha[:12], preview))
    for item in report["stale_allowlist"]:
        print("stale-allowlist %s %s" % (item["rule"], item["path"]))
    print("skipped: oversized=%d binary=%d" % (report["skipped"]["oversized"], report["skipped"]["binary"]))
    return 1 if report["findings"] or report["stale_allowlist"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
