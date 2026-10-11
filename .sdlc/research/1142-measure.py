#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""#1142 measurements: what the history check and the re-root cost, so the CHANGELOG's numbers can be
re-run rather than trusted. Research script, run by hand; it builds every repository it touches in a
fresh temporary directory and removes it.

    python3 .sdlc/research/1142-measure.py history [COMMITS]     # default 32500
    python3 .sdlc/research/1142-measure.py reroot [FILES ...]    # default 1000 10000

`history` times `git merge-base` (related, 5 commits apart; and unrelated) and the root walk
`_shares_history` falls back to, on a branch of COMMITS commits, best of 5. `reroot` times
`sync.bootstrap` re-rooting an orphan onto a remote branch of FILES entries files (re-root, TEAM.md
render and push included). Results depend on the machine and its load; say both when quoting them.
"""
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "skills" / "sigma-loop" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(cwd, *args, stdin=None):
    return subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, text=True,
                          check=True).stdout.strip()


def _best(cwd, args, runs=5):
    best, code = None, None
    for _ in range(runs):
        start = time.perf_counter()
        code = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True).returncode
        took = time.perf_counter() - start
        best = took if best is None else min(best, took)
    return best * 1000, code


def history(commits):
    scratch = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = scratch / "r"
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        stream = []
        for i in range(1, commits + 1):
            stream.append(f"commit refs/heads/sdlc-ledger\nmark :{i}\ncommitter t <t@e> {1700000000 + i} +0000\n"
                          f"data 2\nc\n" + (f"from :{i - 1}\n" if i > 1 else "")
                          + f"M 644 inline entries/a.jsonl\ndata {len(str(i)) + 1}\n{i}\n\n")
        # the remote-tracking ref, 5 commits behind, written by fast-import rather than update-ref
        stream.append(f"reset refs/remotes/origin/sdlc-ledger\nfrom :{commits - 5}\n\n")
        stream.append("commit refs/heads/orphan\ncommitter o <o@e> 1700000000 +0000\ndata 2\no\n\n")
        _git(repo, "fast-import", "--quiet", stdin="".join(stream))
        tracking = "refs/remotes/origin/sdlc-ledger"
        print(f"history: {commits} commits, git {_git(repo, 'version').split()[-1]}")
        print("  merge-base, related (5 apart): %.1f ms (exit %d)" % _best(repo, ["merge-base", "sdlc-ledger", tracking]))
        print("  root walk, rev-list --max-parents=0: %.1f ms (exit %d)"
              % _best(repo, ["rev-list", "--max-parents=0", "sdlc-ledger"]))
        print("  merge-base, unrelated: %.1f ms (exit %d)" % _best(repo, ["merge-base", "orphan", tracking], runs=1))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def reroot(files):
    sync, ledger = _load("sync"), _load("ledger")
    scratch = pathlib.Path(tempfile.mkdtemp())
    try:
        origin = scratch / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        stream = ["commit refs/heads/main\ncommitter t <t@e> 1700000000 +0000\ndata 4\nbase\n"
                  "M 644 inline README\ndata 2\nx\n\n",
                  "commit refs/heads/sdlc-ledger\ncommitter a <a@e> 1700000001 +0000\ndata 7\nledger\n"]
        for i in range(files):
            body = json.dumps({"id": f"u{i}:1", "ts": "2026-10-01T00:00:00Z", "actor": f"u{i}",
                               "kind": "note", "goal": "g"}) + "\n"
            stream.append(f"M 644 inline entries/u{i}-0000000{i % 10}.1.jsonl\ndata {len(body.encode())}\n{body}")
        stream.append("M 644 inline README.md\ndata 2\nr\n\n")
        _git(origin, "fast-import", "--quiet", stdin="".join(stream))
        clone = scratch / "b"
        subprocess.run(["git", "clone", "-q", "--single-branch", "--branch", "main", str(origin), str(clone)],
                       check=True)
        for key, value in (("user.email", "b@e"), ("user.name", "Bo"), ("commit.gpgsign", "false")):
            _git(clone, "config", key, value)
        base = clone / ".sdlc"
        (base / "state").mkdir(parents=True)
        config = {"ledger": {"enabled": True, "actor": "bo"}}
        (base / "config.json").write_text(json.dumps(config), encoding="utf-8")
        empty = _git(clone, "hash-object", "-t", "tree", "/dev/null")
        _git(clone, "branch", "sdlc-ledger", _git(clone, "commit-tree", empty, "-m", "orphan"))
        worktree = sync.worktree(base)
        _git(clone, "worktree", "add", "-q", str(worktree), "sdlc-ledger")
        ledger.append(base, config, "note", "g.md", why="on the orphan")
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-qm", "ledger: bo")
        start = time.perf_counter()
        out = sync.bootstrap(base, config)
        took = time.perf_counter() - start
        assert "re-rooted" in out and out.endswith("published"), out
        print(f"reroot: {files} entries files on the remote branch: bootstrap (re-root + publish) {took:.2f} s")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    verb, rest = (sys.argv[1] if len(sys.argv) > 1 else ""), [int(a) for a in sys.argv[2:]]
    if verb == "history":
        history(*(rest or [32500]))
    elif verb == "reroot":
        for n in rest or [1000, 10000]:
            reroot(n)
    else:
        print(__doc__)
        sys.exit(2)
