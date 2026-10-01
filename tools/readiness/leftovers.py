#!/usr/bin/env python3
"""Read-only inventory of files and local state created by Sigma."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

IGNORE_MARKER = "# Sigma runtime dirs (machine-written)"
AGENTS_MARKERS = ("<!-- sigma:codex:start -->", "<!-- sigma:codex:end -->")
# Keep this set source-locked by test_copied_markers_match_init_sources: these are exactly
# sdlc_init.py's `_CURSOR_RULES`, not every rule an adopter may have written themselves.
CURSOR_RULES = ("sdlc.mdc", "output-contract.mdc")

def _row(kind, path):
    return {"kind": kind, "path": str(path)}

def _has_ignore_block(path):
    try:
        return IGNORE_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False

def _pid_alive(path):
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True

def _git(repo, *args):
    try:
        return subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, check=False, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""

def _github(owner_repo):
    """Read repository-owned REST resources only when explicitly requested.

    The repository projects REST endpoint is already scoped to boards linked to this repository;
    Projects v2 has no equivalent REST listing, so this deliberately does not spend GraphQL quota.
    """
    try:
        def get(path):
            output = subprocess.run(["gh", "api", "--paginate", "--slurp", path], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
                                    timeout=15).stdout
            pages = json.loads(output or "[]")
            return [row for page in pages for row in page] if isinstance(pages, list) else []
        return get(f"repos/{owner_repo}/labels?per_page=100"), \
            get(f"repos/{owner_repo}/projects?per_page=100")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return [], []

def find_leftovers(repo, environ=None, github=None):
    """Return deterministic, read-only observations for one repository."""
    repo = Path(repo).resolve()
    rows = []
    if (repo / ".sdlc").exists():
        rows.append(_row(".sdlc", ".sdlc"))
    for rel in (".gitignore", ".git/info/exclude"):
        if _has_ignore_block(repo / rel):
            rows.append(_row("ignore-block", rel))
    agents = repo / "AGENTS.md"
    try:
        agent_text = agents.read_text(encoding="utf-8")
    except OSError:
        agent_text = ""
    # Either delimiter is residue: a torn/manual edit must not let the final check claim clean.
    if any(marker in agent_text for marker in AGENTS_MARKERS):
        rows.append(_row("agents-block", "AGENTS.md"))
    for name in CURSOR_RULES:
        path = repo / ".cursor" / "rules" / name
        if path.is_file():
            rows.append(_row("cursor-rule", path.relative_to(repo)))
    state = repo / ".sdlc" / "state"
    if state.is_dir():
        for pid_file in sorted(state.rglob("*.pid")):
            if _pid_alive(pid_file):
                rows.append(_row("running-pid", pid_file.relative_to(repo)))
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            path = Path(line[9:]).resolve()
            try:
                path.relative_to(repo / ".sdlc")
            except ValueError:
                continue
            rows.append(_row("worktree", path))
    for line in _git(repo, "branch", "--format=%(refname:short)").splitlines():
        if line.startswith(("sdlc/", "feature/")) or line == "sdlc-ledger":
            rows.append(_row("local-branch", line))
    for line in _git(repo, "branch", "-r", "--format=%(refname:short)").splitlines():
        branch = line.split("/", 1)[-1]
        if branch.startswith(("sdlc/", "feature/")) or branch == "sdlc-ledger":
            rows.append(_row("remote-branch", line))
    environment = os.environ if environ is None else environ
    homes = (Path(environment.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")),
             Path(environment.get("CODEX_HOME", Path.home() / ".codex")))
    for home in homes:
        if not home.is_dir():
            continue
        try:
            for path in home.rglob("sigma"):
                if path.is_dir() and path.name == "sigma":
                    rows.append(_row("installed-plugin", path))
        except OSError:
            continue
    if github:
        labels, boards = _github(github)
        for label in labels:
            if str(label.get("name", "")).startswith("sdlc:"):
                rows.append(_row("github-label", label["name"]))
        for board in boards:
            rows.append(_row("github-board", board.get("html_url", board.get("name", "unknown"))))
    return sorted(rows, key=lambda row: (row["kind"], row["path"]))

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--github", metavar="OWNER/REPO")
    args = parser.parse_args(argv)
    rows = find_leftovers(args.repo, github=args.github)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(f"{row['kind']}: {row['path']}")
    return 1 if rows else 0

if __name__ == "__main__":
    raise SystemExit(main())
