#!/usr/bin/env python3
"""Secret-safe launch exposure scanner (#333).

Usage: exposure_scan.py {history|tracked|refs} REPO [--json PATH]

`history` examines reachable text blobs, while `tracked` examines HEAD only.  It never prints a
matched value: reports identify the rule, location and a redacted preview.  Exit 0 means clean,
1 means findings (including stale allowlist entries), and 2 means bad input or git failure.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

MAX_BLOB_BYTES = 2 * 1024 * 1024
DEFAULT_ALLOWLIST = "docs/launch/exposure-allowlist.json"
PRIVATE_RULES = (
    ("absolute-home-path", re.compile(r"(?:/Users/[A-Za-z0-9._-]+/|/home/[A-Za-z0-9._-]+/|/private/tmp/claude-[A-Za-z0-9._-]+|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+\\)")),
    ("email-address", re.compile(r"(?i)\b(?!(?:[A-Z0-9._%+-]+@(?:example\.com|example\.invalid|users\.noreply\.github\.com)|noreply@anthropic\.com)\b)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("legacy-issue-reference", re.compile(r"#\d{4,}\b")),
)


def _load_scrub():
    path = pathlib.Path(__file__).resolve().parents[2] / "skills" / "agrim-loop" / "scripts" / "scrub.py"
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


def _allowlist(path):
    if not path or not pathlib.Path(path).exists():
        return []
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("invalid allowlist: %s" % exc)
    if not isinstance(data, list) or any(not isinstance(x, dict) or not x.get("path") or not x.get("rule") or not x.get("reason") for x in data):
        raise ValueError("allowlist entries require path, rule, and reason")
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
    return line, preview[:240]


def _matches(text, rules):
    for rule, rx in rules:
        for match in rx.finditer(text):
            line, preview = _safe_preview(text, match, rule, rules)
            yield rule, line, preview


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


def _commits_for_blob(repo, sha):
    return _run("git", "log", "--all", "--format=%H", "--find-object=" + sha, cwd=repo).splitlines()


def scan(repo, mode, allowlist=None, patterns=None):
    repo = pathlib.Path(repo).resolve()
    if not (repo / ".git").exists():
        raise ValueError("repository must be a working tree")
    allow = _allowlist(allowlist or repo / DEFAULT_ALLOWLIST)
    allowed, used = {(x["path"], x["rule"]): x for x in allow}, set()
    findings, skipped, counts = [], {"oversized": 0, "binary": 0}, {"legacy_issue_references": 0}
    rules = _patterns(patterns, repo)
    blobs = _history_blobs(repo) if mode == "history" else _tracked_blobs(repo)
    for sha, record in blobs:
        paths, commits = record["paths"], record["commits"]
        if _run("git", "cat-file", "-t", sha, cwd=repo).strip() != "blob":
            continue
        size = int(_run("git", "cat-file", "-s", sha, cwd=repo).strip())
        if size > MAX_BLOB_BYTES:
            skipped["oversized"] += 1; continue
        data = _run("git", "cat-file", "blob", sha, cwd=repo, text=False)
        if b"\0" in data:
            skipped["binary"] += 1; continue
        text = data.decode("utf-8", "replace")
        # A (rule, blob) is one finding even where Git has multiple paths to that content.  This
        # avoids multiplying a secret exposure while preserving every reachable path and commit.
        # The output identity is (rule, blob), never one finding per repeated token.  The only
        # public-facing legacy issue signal is its aggregate count, as promised by the goal.
        per_rule = {}
        for rule, line, preview in _matches(text, rules):
            if rule == "legacy-issue-reference":
                counts["legacy_issue_references"] += 1
                continue
            if rule not in per_rule:
                per_rule[rule] = [line, preview, 0]
            per_rule[rule][2] += 1
        for rule, (line, preview, hit_count) in per_rule.items():
            visible = []
            for path in sorted(paths):
                if (path, rule) in allowed:
                    used.add((path, rule))
                else:
                    visible.append(path)
            if visible:
                findings.append({"rule": rule, "path": visible[0], "paths": visible, "line": line,
                                 "blob": sha, "commits": commits, "matches": hit_count, "preview": preview})
    stale = [{"path": x["path"], "rule": x["rule"]} for x in allow if (x["path"], x["rule"]) not in used]
    return {"schema": "sigma.launch-exposure/v1", "mode": mode, "findings": findings,
            "stale_allowlist": stale, "skipped": skipped, "counts": counts}


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


def _write(report, path):
    """Write paired machine and human evidence without ever reconstructing a matched value."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Launch exposure scan", "", "Mode: `%s`" % report.get("mode", "refs"), ""]
    if "findings" in report:
        lines += ["| Rule | Path | Line | Blob |", "| --- | --- | ---: | --- |"]
        lines += ["| %s | `%s` | %s | `%s` |" % (x["rule"], x["path"], x["line"], x["blob"][:12])
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
    args = parser.parse_args(argv)
    try:
        report = scan_refs(args.repo) if args.verb == "refs" else scan(args.repo, args.verb, args.allowlist, args.patterns)
    except ValueError as exc:
        print("exposure-scan: " + str(exc), file=sys.stderr); return 2
    if args.json:
        evidence = args.json
    else:
        sha = _run("git", "rev-parse", "HEAD", cwd=args.repo).strip()[:12]
        stem = "exposure" if args.verb == "history" else args.verb
        evidence = pathlib.Path(args.repo) / "docs" / "launch" / "evidence" / (stem + "-" + sha + ".json")
    _write(report, evidence)
    if args.verb == "refs":
        print("refs: %d remote; %d local-only tags" % (len(report["remote"]), len(report["local_only_tags"])))
        return 0
    for item in report["findings"]:
        print("%s %s:%d %s %s" % (item["rule"], item["path"], item["line"], item["blob"][:12], item["preview"]))
    for item in report["stale_allowlist"]:
        print("stale-allowlist %s %s" % (item["rule"], item["path"]))
    print("skipped: oversized=%d binary=%d" % (report["skipped"]["oversized"], report["skipped"]["binary"]))
    return 1 if report["findings"] or report["stale_allowlist"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
