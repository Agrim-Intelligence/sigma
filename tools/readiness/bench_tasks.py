#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check, seal and verify the benchmark task set (`evals/bench/tasks/`).

USAGE:
  bench_tasks.py check  [--tasks DIR] [--hidden-root DIR] [--frozen]
  bench_tasks.py verify [--tasks DIR] [--hidden-root DIR] [--only ID ...] [--external --scratch DIR] [--json OUT]
  bench_tasks.py seal ID [--tasks DIR] [--hidden-root DIR]     (hash a hidden bundle into task.json)
  bench_tasks.py build-manifest [--tasks DIR]                  (derive manifest.json from the task.json files)
  bench_tasks.py materialize ID [ID ...] [--record-digest] [--cache DIR]   (external: fetch the starting tree, network)
  bench_tasks.py lock --scratch DIR [--tasks DIR]               (resolve the one scoring environment; network)
  bench_tasks.py hidden-from-pr ID --scratch DIR [--tasks DIR] [--hidden-root DIR]   (external: network)
  bench_tasks.py manifest-sha [--tasks DIR]

EXIT: 0 = ok; 1 = findings (nothing is hidden by a warning); 2 = bad arguments or an unreadable input.

What a task is, in the harness's own terms (`evals/bench/bench.py`, `_load_tasks`): a manifest row with
`id`, `prompt`, `source` (a directory beside the manifest, copied as the arm's working tree),
`visible_command` (argv) and `hidden_bundle` (a directory name under the operator's hidden root). Each
task directory also holds a `task.json` carrying those fields plus the ones the pre-registration needs
(`kind`, `trap`, `status`, `hidden_sha256`, `hidden_files`); `manifest.json` is derived
from the `task.json` files and `check` refuses a manifest that has drifted from them.

Hidden bundle (outside the repository, under `~/.sigma-ops/bench/hidden/<hidden_bundle>/`): the
harness reads `verify.json` (argv, run with the bundle copied to `.sigma-hidden/` inside the scored tree);
this tool writes the same runner into every bundle, `run_hidden.py`, which copies `files/` over the tree
and runs pytest on `hidden.json`'s `run` list. `reference/` holds the reference fix (internal tasks only;
external tasks fetch theirs from `fix_sha`). The bundle digest is sha256 over sorted `<path> <sha256>`
lines; `task.json` carries every per-file hash so CI, which has no hidden root, can still see that a hidden
file was copied into the repository.

External tasks commit only `task.json` and `fetch.json` (their `repo/` is git-ignored; `materialize` fetches the
base commit's tree into it, with no history, so the committed manifest can then be handed to the harness). `lock`, `hidden-from-pr`, `materialize` and `verify --external` use the
network (git fetch, pip); none calls a model, and nothing here runs an arm. `verify --external` builds the one scoring environment from
`environment.lock` in a throwaway virtualenv under `--scratch` (dependencies only: the project under test is never installed) and fetches
each task's base and fix trees.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import site
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = ROOT / "evals" / "bench" / "tasks"
DEFAULT_HIDDEN = Path.home() / ".sigma-ops" / "bench" / "hidden"
MANIFEST_SCHEMA = "sigma.benchmark-tasks/v1"
KINDS = {"bug-fix", "feature", "refactor", "docs", "trap-spec-contradiction", "trap-plan-defect"}
MIN_TASKS, MAX_TASKS, TRAP_COUNT = 12, 18, 3
CLOSING_LINE = "Make the change in this repository."
MIN_LEAK_BYTES = 16  # an empty or one-line file (an empty __init__.py) is common to many trees and says nothing
SCORING_TIMEOUT = 600  # the harness default for --scoring-timeout-seconds; a task must score inside it
HIDDEN_RUNNER = '''\
"""Copy the hidden files over the scored tree, then run the hidden tests (written by bench_tasks.py)."""
import json
import os
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
spec = json.load(open(os.path.join(here, "hidden.json"), encoding="utf-8"))
for rel in spec["files"]:
    dst = os.path.join(os.getcwd(), rel)
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copyfile(os.path.join(here, "files", rel), dst)
sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          *spec.get("pytest_args", []), *spec["run"]]))
'''
TASK_FIELDS = ("id", "kind", "origin", "trap", "status", "prompt", "source", "visible_command",
               "visible_expected", "hidden_bundle", "hidden_sha256", "hidden_files", "tree_sha256")
ROW_FIELDS = ("id", "prompt", "source", "visible_command", "hidden_bundle", "kind", "origin", "trap",
              "status", "authorship", "hidden_sha256", "tree_sha256")
AUTHORSHIPS = ("independent-agent", "outside-human")


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"bench_tasks: unreadable {path}: {exc}")


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_digest(files):
    """The bundle digest from its per-file hashes: sha256 of sorted '<path> <sha256>' lines."""
    lines = "".join(f"{rel} {files[rel]}\n" for rel in sorted(files))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def tree_digest(directory):
    """Digest of a directory tree: sha256 of sorted '<path> <sha256>' lines over its regular files
    (interpreter and pytest caches excluded)."""
    files = {rel: digest for rel, digest in bundle_files(directory).items()
             if "__pycache__" not in rel.split("/") and ".pytest_cache" not in rel.split("/")}
    return bundle_digest(files)


def bundle_files(bundle):
    """Map every file under a hidden bundle (relative posix path) to its sha256."""
    bundle = Path(bundle)
    return {p.relative_to(bundle).as_posix(): file_sha256(p)
            for p in sorted(bundle.rglob("*")) if p.is_file() and not p.is_symlink()}


def load_tasks(tasks_dir):
    tasks_dir = Path(tasks_dir)
    return {p.parent.name: _load_json(p) for p in sorted(tasks_dir.glob("*/task.json"))}


def derived_manifest(tasks_dir, previous):
    """The manifest the task.json files imply, keeping the hand-written top-level facts."""
    rows = []
    for task_id, task in load_tasks(tasks_dir).items():
        rows.append({key: task[key] for key in ROW_FIELDS if key in task})
    out = {key: previous[key] for key in ("schema", "frozen", "model", "environment", "balance", "trap_authorship") if key in previous}
    out["tasks"] = rows
    return out


def _bench_module():
    spec = importlib.util.spec_from_file_location("bench_harness_for_tasks", ROOT / "evals" / "bench" / "bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def harness_accepts(tasks_dir):
    """Run the harness's own manifest loader over a copy where external trees are stood in by empty dirs."""
    tasks_dir = Path(tasks_dir)
    with tempfile.TemporaryDirectory(prefix="bench-tasks-load-") as scratch:
        copy = Path(scratch) / "tasks"
        copy.mkdir()
        shutil.copyfile(tasks_dir / "manifest.json", copy / "manifest.json")
        for row in _load_json(tasks_dir / "manifest.json").get("tasks", []):
            source = row.get("source")
            if isinstance(source, str) and source and not Path(source).is_absolute() and ".." not in Path(source).parts:
                (copy / source).mkdir(parents=True, exist_ok=True)
        try:
            _bench_module()._load_tasks(copy / "manifest.json")
        except Exception as exc:  # BenchmarkRefusal and anything else the loader raises
            return f"the harness refuses the manifest: {exc}"
    return ""


def harness_validates(tasks_dir, hidden_root):
    """Run the harness's own `_validate_inputs` over the ready tasks and a real hidden root (stand-in trees)."""
    tasks_dir = Path(tasks_dir)
    manifest = _load_json(tasks_dir / "manifest.json")
    ready = [row for row in manifest.get("tasks", []) if row.get("status") == "ready"]
    with tempfile.TemporaryDirectory(prefix="bench-tasks-validate-") as scratch:
        copy = Path(scratch) / "tasks"
        copy.mkdir()
        _write_manifest(copy / "manifest.json", dict(manifest, tasks=ready))
        for row in ready:
            (copy / row["source"]).mkdir(parents=True, exist_ok=True)
        bench = _bench_module()
        try:
            loaded = bench._load_tasks(copy / "manifest.json")
            bench._validate_inputs(loaded, 1.0, Path(hidden_root), Path(scratch) / "out" / "results.json", False, {},
                                   Path(scratch) / "scratch")
        except Exception as exc:
            return f"the harness refuses these tasks with this hidden root: {exc}"
    return ""


def _write_manifest(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check(tasks_dir=TASKS_DIR, hidden_root=None, require_frozen=False):
    """Return a list of findings (empty means the task set is internally consistent)."""
    tasks_dir = Path(tasks_dir)
    problems = []
    manifest_path = tasks_dir / "manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path.name}: missing"]
    manifest = _load_json(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"manifest schema must be {MANIFEST_SCHEMA}")
    if not isinstance(manifest.get("frozen"), bool):
        problems.append("manifest must say `\"frozen\": true` or `false` explicitly")
    tasks = load_tasks(tasks_dir)
    rows = manifest.get("tasks") or []
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        problems.append("manifest task ids are not unique")
    if sorted(ids) != sorted(tasks):
        problems.append(f"manifest ids differ from task directories: {sorted(set(ids) ^ set(tasks))}")
    if derived_manifest(tasks_dir, manifest) != manifest:
        problems.append("manifest.json has drifted from the task.json files (run build-manifest)")
    refusal = harness_accepts(tasks_dir)
    if refusal:
        problems.append(refusal)
    if not MIN_TASKS <= len(tasks) <= MAX_TASKS:
        problems.append(f"{len(tasks)} tasks: the pre-registration's operating characteristics cover "
                        f"{MIN_TASKS} to {MAX_TASKS}; anything else is an owner decision")
    traps = [t for t in tasks.values() if t.get("trap")]
    if len(traps) != TRAP_COUNT:
        problems.append(f"{len(traps)} trap tasks; the pre-registration has {TRAP_COUNT}")
    model = manifest.get("model") or {}
    for key in ("id", "training_cutoff", "cutoff_rule_date", "cutoff_source"):
        if not model.get(key):
            problems.append(f"manifest model.{key} is not recorded")
    if any(t.get("origin") == "external" for t in tasks.values()):
        env = manifest.get("environment") or {}
        lock = tasks_dir / str(env.get("lock", ENV_LOCK))
        if not lock.is_file():
            problems.append(f"manifest environment: {ENV_LOCK} is missing (run lock)")
        elif env.get("lock_sha256") != file_sha256(lock):
            problems.append(f"manifest environment: lock_sha256 is not the hash of {lock.name} (re-run lock)")
        elif "pytest==" not in lock.read_text(encoding="utf-8"):
            problems.append(f"manifest environment: {lock.name} does not pin pytest")
        if not env.get("python"):
            problems.append("manifest environment: the interpreter the lock was resolved under is not recorded")
    per_repo = {}
    for task_id, task in tasks.items():
        fetch_file = tasks_dir / task_id / "fetch.json"
        if task.get("origin") == "external" and fetch_file.is_file():
            per_repo.setdefault(_load_json(fetch_file).get("repo_url"), []).append(task_id)
    for repo_url, owners in per_repo.items():
        if len(owners) > 3:
            problems.append(f"{len(owners)} tasks from {repo_url}: at most 3 per repository")
    all_hashes = {}
    for task_id, task in tasks.items():
        problems += _check_task(tasks_dir, task_id, task, model)
        for rel, digest in (task.get("hidden_files") or {}).items():
            all_hashes.setdefault(digest, f"{task_id}:{rel}")
    scan_root = tasks_dir.parent if tasks_dir.parent.name == "bench" else tasks_dir
    problems += _leaks(scan_root, all_hashes)
    if hidden_root:
        problems += _check_hidden_on_disk(Path(hidden_root), tasks, tasks_dir)
        refusal = harness_validates(tasks_dir, hidden_root)
        if refusal:
            problems.append(refusal)
    if any(t.get("authorship") == "independent-agent" for t in tasks.values()) and not manifest.get("trap_authorship"):
        problems.append("manifest: trap_authorship must state that the traps are agent-authored, not outside-human")
    awaiting = [i for i, t in tasks.items() if t.get("status") == "awaiting-author"]
    if manifest.get("frozen") is True:
        if awaiting:
            problems.append(f"frozen manifest still has tasks awaiting an author: {awaiting}")
        if require_frozen and not hidden_root:
            problems.append("a frozen manifest is checked against the real hidden bundles: pass --hidden-root")
    elif require_frozen:
        problems.append("--frozen: manifest says frozen is false")
    return problems


def _check_task(tasks_dir, task_id, task, model):
    where = f"{task_id}/task.json"
    problems = []
    for key in TASK_FIELDS:
        if key not in task:
            problems.append(f"{where}: missing `{key}`")
    if problems:
        return problems
    if task["id"] != task_id:
        problems.append(f"{where}: id {task['id']!r} differs from its directory")
    if task["kind"] not in KINDS:
        problems.append(f"{where}: kind {task['kind']!r} not in {sorted(KINDS)}")
    if task["origin"] not in ("external", "internal"):
        problems.append(f"{where}: origin must be external or internal")
    if task["trap"] != task["kind"].startswith("trap-"):
        problems.append(f"{where}: `trap` must be true exactly for trap kinds")
    if task["status"] not in ("ready", "awaiting-author"):
        problems.append(f"{where}: status must be ready or awaiting-author")
    if task["status"] == "awaiting-author" and not task["trap"]:
        problems.append(f"{where}: only a trap can await an author")
    if task["source"] != f"{task_id}/repo":
        problems.append(f"{where}: source must be {task_id}/repo")
    if task["visible_expected"] not in ("pass", "fail"):
        problems.append(f"{where}: visible_expected must be pass or fail")
    elif task["visible_expected"] == "fail" and task["origin"] == "external":
        problems.append(f"{where}: an external task's visible tests are the repository's existing tests, which pass "
                        "at the base commit")
    if task["hidden_bundle"] != task_id:
        problems.append(f"{where}: hidden_bundle must equal the id")
    if task["status"] == "ready" and not task["prompt"].rstrip().endswith(CLOSING_LINE):
        problems.append(f"{where}: a ready task's prompt must end with the line `{CLOSING_LINE}`")
    if task["trap"] and task["status"] == "ready" and task.get("authorship") not in AUTHORSHIPS:
        problems.append(f"{where}: a ready trap must declare authorship, one of {list(AUTHORSHIPS)}")
    if task["status"] == "ready" and not task["tree_sha256"]:
        problems.append(f"{where}: tree_sha256 is not recorded (run seal)")
    if task["status"] == "ready":
        files = task["hidden_files"]
        if not files or bundle_digest(files) != task["hidden_sha256"]:
            problems.append(f"{where}: hidden_sha256 is not the digest of hidden_files")
    else:
        if task["hidden_files"] or task["hidden_sha256"]:
            problems.append(f"{where}: a task awaiting its author holds no hidden hashes yet")
        if not task["prompt"].startswith("AWAITING-AUTHOR"):
            problems.append(f"{where}: a task awaiting its author must say so in its prompt")
    directory = tasks_dir / task_id
    if task["origin"] == "external":
        fetch = directory / "fetch.json"
        if not fetch.is_file():
            problems.append(f"{task_id}: external task needs fetch.json")
        else:
            problems += _check_fetch(task_id, _load_json(fetch), model, task["prompt"])
        fetch_digest = _load_json(fetch).get("tree_sha256") if fetch.is_file() else None
        if task["status"] == "ready" and task["tree_sha256"] != fetch_digest:
            problems.append(f"{where}: tree_sha256 differs from the base-tree digest in fetch.json")
        tracked = _tracked(directory / "repo")
        if tracked:
            problems.append(f"{task_id}: an external tree must not be committed ({tracked[0]} ...)")
    else:
        repo = directory / "repo"
        if not repo.is_dir() or not any(repo.rglob("*")):
            problems.append(f"{task_id}: internal task needs a non-empty repo/")
        for path in repo.rglob("*"):
            if path.is_symlink() or ".git" in path.relative_to(repo).parts:
                problems.append(f"{task_id}: repo/ must hold no links and no .git ({path.name})")
        if (task["status"] == "ready" and repo.is_dir() and task["tree_sha256"]
                and tree_digest(repo) != task["tree_sha256"]):
            problems.append(f"{where}: the starting tree differs from tree_sha256 (edited after sealing?)")
    return problems


def _check_fetch(task_id, fetch, model, prompt=""):
    problems = []
    for key in ("repo_url", "base_sha", "fix_sha", "pr_number", "issue_number", "pr_created_at", "license",
                "stars", "stars_read_on", "nontest_lines_changed", "issue_title", "issue_body_sha256",
                "issue_fetched_at", "issue_updated_at", "install", "pytest_args", "tests_dir", "tree_sha256", "hidden_test_files"):
        if key not in fetch:
            problems.append(f"{task_id}/fetch.json: missing `{key}`")
    if problems:
        return problems
    for key in ("base_sha", "fix_sha"):
        value = fetch[key]
        if not (isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)):
            problems.append(f"{task_id}/fetch.json: {key} must be a full lowercase sha")
    if fetch["license"] not in ("MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"):
        problems.append(f"{task_id}/fetch.json: license {fetch['license']!r} is not MIT, BSD or Apache-2.0")
    if not isinstance(fetch.get("nontest_lines_changed"), int) or fetch["nontest_lines_changed"] > 300:
        problems.append(f"{task_id}/fetch.json: the PR must change 300 or fewer lines outside tests")
    if fetch["stars"] < 50:
        problems.append(f"{task_id}/fetch.json: fewer than 50 stars")
    rule = model.get("cutoff_rule_date")
    if rule and fetch["pr_created_at"][:10] < rule:
        problems.append(f"{task_id}/fetch.json: PR created {fetch['pr_created_at'][:10]}, before {rule}")
    tail = "\n\nMake the change in this repository."
    head = fetch["issue_title"] + "\n\n"
    body = prompt[len(head):-len(tail)] if prompt.startswith(head) and prompt.endswith(tail) else None
    if body is None or hashlib.sha256(body.encode("utf-8")).hexdigest() != fetch["issue_body_sha256"]:
        problems.append(f"{task_id}: the prompt is not the issue title and body as recorded (title, body sha256) "
                        "plus the one closing line")
    if not fetch["hidden_test_files"]:
        problems.append(f"{task_id}/fetch.json: the PR changed no test file")
    for needle, what in ((f"pull/{fetch['pr_number']}", "a link to the fixing PR"), (fetch["fix_sha"][:7], "the fix commit")):
        if needle in prompt:
            problems.append(f"{task_id}: the prompt contains {what} ({needle})")
    return problems


def _tracked(path):
    """Files git tracks under `path` (empty when `path` is not in a git work tree)."""
    base = Path(path)
    while not base.exists():
        base = base.parent
    try:
        done = subprocess.run(["git", "-C", str(base), "ls-files", "--", str(Path(path).resolve())],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in done.stdout.splitlines() if line] if done.returncode == 0 else []


def _leaks(tasks_dir, hidden_hashes):
    """A file under the task directories whose bytes equal any hidden file is a leak, named here."""
    problems = []
    for path in sorted(Path(tasks_dir).rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_file() and not path.is_symlink() and path.stat().st_size >= MIN_LEAK_BYTES:
            owner = hidden_hashes.get(file_sha256(path))
            if owner:
                problems.append(f"hidden file {owner} was copied into the repository at "
                                f"{path.relative_to(tasks_dir).as_posix()}")
    return problems


def _test_names(root, pattern):
    return {name for path in Path(root).rglob(pattern) if path.is_file()
            for name in re.findall(r"^\s*(?:async\s+)?def (test_\w+)", path.read_text(encoding="utf-8", errors="replace"), re.M)}


def _check_hidden_on_disk(hidden_root, tasks, tasks_dir=None):
    problems = []
    if _inside(hidden_root, ROOT):
        problems.append("the hidden root is inside the repository")
    for task_id, task in tasks.items():
        bundle = hidden_root / task["hidden_bundle"]
        if task.get("status") != "ready":
            if bundle.exists():
                problems.append(f"{task_id}: a bundle exists for a task awaiting its author; the harness would run "
                                "the placeholder")
            continue
        if not bundle.is_dir():
            problems.append(f"{task_id}: hidden bundle missing under the hidden root")
            continue
        if bundle_files(bundle) != task["hidden_files"]:
            problems.append(f"{task_id}: hidden bundle on disk differs from the hashes in task.json")
        if task.get("trap"):
            if not (bundle / "obvious").is_dir():
                problems.append(f"{task_id}: a trap's hidden bundle must hold obvious/ (the implementation that falls "
                                "into the trap, which the hidden tests must fail)")
            for name in ("expected_catch.txt", "author.json"):
                if not (bundle / name).is_file() or not (bundle / name).read_text(encoding="utf-8").strip():
                    problems.append(f"{task_id}: a trap's hidden bundle must hold a non-empty {name}")
            try:
                said = json.loads((bundle / "author.json").read_text(encoding="utf-8")).get("authorship")
            except (OSError, ValueError, AttributeError):
                said = None
            if said != task.get("authorship"):
                problems.append(f"{task_id}: author.json authorship {said!r} differs from task.json "
                                f"{task.get('authorship')!r}")
            if not task.get("author"):
                problems.append(f"{task_id}: a trap must name its author (as the author consents to be credited)")
        if tasks_dir and task.get("origin") == "internal":
            clash = _test_names(bundle / "files", "*.py") & _test_names(Path(tasks_dir) / task_id / "repo", "*.py")
            if clash:
                problems.append(f"{task_id}: hidden test names appear in the starting repository: {sorted(clash)}")
            named = sorted(name for name in _test_names(bundle / "files", "*.py") if name in task.get("prompt", ""))
            if named:
                problems.append(f"{task_id}: hidden test names appear in the prompt: {named}")
    return problems


def _inside(child, parent):
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


SECRET_MARKERS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
SECRET_PREFIXES = ("GH_", "GITHUB_", "ANTHROPIC", "AWS_", "OPENAI", "GOOGLE_", "AZURE_", "NPM_", "SSH_")


def clean_env(profile_dir):
    """The environment third-party code runs in: nothing secret-shaped, a fresh HOME and TMPDIR.

    Install scripts and the tests of external projects are code Sigma did not write; this is a scrub, not a
    sandbox (a process can still read any file the user can).
    """
    kept = {key: value for key, value in os.environ.items()
            if not any(marker in key.upper() for marker in SECRET_MARKERS)
            and not key.upper().startswith(SECRET_PREFIXES)}
    kept["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # No bytecode is written into the trees under test: a stale .pyc (same size, same whole-second mtime as a file that
    # was just replaced, which a fast run can hit) would make an overlaid hidden test run its old compiled version.
    kept["PYTHONDONTWRITEBYTECODE"] = "1"
    # The fresh HOME would hide an interpreter's user-site packages (pytest itself, on some machines), so the user base
    # stays pointed at the real one. A virtualenv, which verification uses, ignores the user site altogether.
    kept.setdefault("PYTHONUSERBASE", site.getuserbase())
    return _bench_module().arms_common.isolated_env(kept, Path(profile_dir))


def _argv(command, python):
    return [python if command[0] in ("python", "python3") else command[0], *command[1:]]


def _run(argv, cwd, timeout, env=None):
    """Run argv in its own process group; on timeout kill the whole group (a hung test must not outlive us)."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None, "timeout"
    return proc.returncode, out[-1500:]


def _hidden_run(tree, bundle, task, python, timeout, env=None):
    """The tool's own scoring run (output kept for the evidence record): copy the tree, add the bundle at
    .sigma-hidden, run verify.json. The pass or fail that counts comes from the harness's own functions."""
    with tempfile.TemporaryDirectory(prefix="bench-score-") as scratch:
        scored = Path(scratch) / "tree"
        shutil.copytree(tree, scored, symlinks=True)
        shutil.copytree(bundle, scored / ".sigma-hidden", symlinks=True)
        argv = _argv(_load_json(bundle / "verify.json")["command"], python)
        return _run(argv, scored, timeout, env)


def summary_line(tail):
    """pytest's final count line from an output tail, or a plain statement that there was none."""
    lines = [line.strip(" =") for line in str(tail).splitlines() if line.strip()]
    hits = [line for line in lines if re.search(r"\b\d+ (passed|failed|error|errors|skipped|deselected)\b", line)]
    return hits[-1][:160] if hits else "no pytest count line"


def _visible_run(tree, task, python, timeout, env=None):
    return _run(_argv(task["visible_command"], python), tree, timeout, env)


class _Scoring:
    """Score through the harness's own `_command_passed` and `_hidden_passed`, behind a pass-through launcher."""

    def __init__(self, scratch, python):
        self.bench = _bench_module()
        self.scratch = Path(scratch)
        self.launcher = self.scratch / "pass-through-launcher"
        self.launcher.write_text('#!/bin/sh\n[ "$1" = "--" ] || exit 64\nshift\nexec "$@"\n', encoding="utf-8")
        self.launcher.chmod(0o700)
        self.env = clean_env(self.scratch / "scoring-profile")
        # `python3` and `python` resolve to the one interpreter under test, whatever its directory holds; a wrapper
        # (not a symlink) keeps a virtualenv's own configuration in force.
        shim = self.scratch / "interpreter-shim"
        shim.mkdir()
        for name in ("python3", "python"):
            (shim / name).write_text(f'#!/bin/sh\nexec "{python}" "$@"\n', encoding="utf-8")
            (shim / name).chmod(0o755)
        self.env["PATH"] = str(shim) + os.pathsep + str(Path(python).parent) + os.pathsep + self.env.get("PATH", "")
        self.count = 0

    def visible(self, tree, task):
        return self.bench._command_passed(task["visible_command"], tree, self.env, str(self.launcher),
                                          SCORING_TIMEOUT)

    def hidden(self, tree, hidden_root, task):
        self.count += 1
        row = self.bench.Task(task["id"], task["prompt"], Path(tree), tuple(task["visible_command"]),
                              task["hidden_bundle"])
        return self.bench._hidden_passed(Path(hidden_root), row, Path(tree), self.scratch / f"eval-{self.count}",
                                         self.env, str(self.launcher), SCORING_TIMEOUT)


def _outcome(passed):
    return "pass" if passed else "fail"


def _score_pair(result, key, scoring, tree, hidden_root, bundle, task, python):
    """One hidden scoring of one tree by both paths; they must agree, and the time is recorded."""
    harness = scoring.hidden(tree, hidden_root, task)
    started = time.monotonic()
    code, tail = _hidden_run(tree, bundle, task, python, SCORING_TIMEOUT, scoring.env)
    result[f"hidden_on_{key}_exit"] = code
    result[f"hidden_on_{key}"] = _outcome(harness)
    result[f"hidden_on_{key}_seconds"] = round(time.monotonic() - started, 2)
    result[f"hidden_on_{key}_summary"] = f"exit {code}; " + summary_line(tail)
    if harness != (code == 0):
        result["inconsistent"] = True
    return tail


def _finish(result, reference_visible=True, visible_expected="pass"):
    # pytest exit 1 is "tests ran and some failed"; 2, 4 and 5 (collection error, usage, nothing collected) would also
    # read as a failing hidden run while proving nothing about the task, so only 1 counts as failing for the right reason.
    right_reason = result.get("hidden_on_start_exit") == 1
    if not right_reason and result["hidden_on_start"] == "fail":
        result["reason"] = "hidden tests failed on the starting tree without a pytest test failure (exit not 1)"
    visible_ok = (result["visible_on_start"] == "pass" if visible_expected == "pass"
                  else result["visible_on_start"] == "fail" and result.get("visible_on_start_exit") == 1)
    if not visible_ok:
        result.setdefault("reason", f"visible tests on the starting tree are not as recorded ({visible_expected}"
                                    + ("" if visible_expected == "pass" else ", failing by test failures") + ")")
    ok = (visible_ok and result["hidden_on_start"] == "fail" and right_reason
          and result["hidden_on_reference"] == "pass" and reference_visible
          and not result.get("inconsistent"))
    return dict(result, status="verified" if ok else "FAILED")


def verify_internal(tasks_dir, hidden_root, task_id, task, python=sys.executable):
    """Visible passes, hidden fails on the starting tree, hidden passes on the reference fix; scored the
    way the harness scores (its own functions behind a pass-through launcher)."""
    bundle = Path(hidden_root) / task["hidden_bundle"]
    result = {"task": task_id, "origin": "internal", "kind": task["kind"], "python": _version_text(python)}
    if task["status"] != "ready":
        return dict(result, status="skipped", reason="awaiting-author")
    start = Path(tasks_dir) / task_id / "repo"
    with tempfile.TemporaryDirectory(prefix="bench-verify-") as scratch:
        scoring = _Scoring(scratch, python)
        work_start = Path(scratch) / "start-tree"
        shutil.copytree(start, work_start, symlinks=True)
        started = time.monotonic()
        result["visible_on_start_exit"] = _visible_run(work_start, task, python, SCORING_TIMEOUT, scoring.env)[0]
        result["visible_on_start"] = _outcome(scoring.visible(work_start, task))
        result["visible_on_start_seconds"] = round(time.monotonic() - started, 2)
        _score_pair(result, "start", scoring, start, hidden_root, bundle, task, python)
        fixed = Path(scratch) / "reference-tree"
        shutil.copytree(start, fixed, symlinks=True)
        if (bundle / "reference").is_dir():
            shutil.copytree(bundle / "reference", fixed, symlinks=True, dirs_exist_ok=True)
        result["visible_on_reference"] = _outcome(scoring.visible(fixed, task))
        if (bundle / "obvious").is_dir():
            trap_tree = Path(scratch) / "obvious-tree"
            shutil.copytree(start, trap_tree, symlinks=True)
            shutil.copytree(bundle / "obvious", trap_tree, symlinks=True, dirs_exist_ok=True)
            result["hidden_on_obvious"] = _outcome(scoring.hidden(trap_tree, hidden_root, task))
            if result["hidden_on_obvious"] != "fail":
                result["inconsistent"] = True
                result["reason"] = "the hidden tests pass on the obvious (trap) implementation"
        tail = _score_pair(result, "reference", scoring, fixed, hidden_root, bundle, task, python)
        result["reference_tail"] = "" if result["hidden_on_reference"] == "pass" else tail
    return _finish(result, result["visible_on_reference"] == "pass", task["visible_expected"])


def _git(args, cwd, timeout=600):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if done.returncode:
        raise RuntimeError(f"git {' '.join(args[:2])}: {done.stderr.strip()[-300:]}")
    return done.stdout


def export_tree(repo_url, sha, dest):
    """Fetch one commit and write its tree (no .git, no history) into dest."""
    with tempfile.TemporaryDirectory(prefix="bench-fetch-") as scratch:
        _git(["init", "-q"], scratch)
        _git(["fetch", "-q", "--depth", "1", repo_url, sha], scratch)
        archive = Path(scratch) / "tree.tar"
        _git(["archive", "--format=tar", "-o", str(archive), "FETCH_HEAD"], scratch)
        Path(dest).mkdir(parents=True, exist_ok=True)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(dest)], check=True)


def materialize(fetch, dest):
    """The starting tree of an external task: the PR's base commit, no history, nothing from the fix."""
    export_tree(fetch["repo_url"], fetch["base_sha"], dest)


def materialize_task(tasks_dir, task_id, record_digest=False, cache=None):
    """Fetch an external task's starting tree into its (git-ignored) repo/ so the harness can copy it.

    The tree's digest must equal `tree_sha256` in fetch.json (a changed or rewritten remote is refused and the
    fetched tree removed); `record_digest` writes the digest when none is recorded yet."""
    directory = Path(tasks_dir) / task_id
    task = _load_json(directory / "task.json")
    if task.get("origin") != "external":
        raise SystemExit(f"bench_tasks: {task_id} is not an external task")
    if (directory / "repo").exists():
        raise SystemExit(f"bench_tasks: {task_id}/repo already exists; remove it to fetch again")
    fetch_path = directory / "fetch.json"
    fetch = _load_json(fetch_path)
    cached = Path(cache) / task_id if cache else None
    if cached and cached.is_dir():
        shutil.copytree(cached, directory / "repo", symlinks=True)
    else:
        materialize(fetch, directory / "repo")
        if cached:
            shutil.copytree(directory / "repo", cached, symlinks=True)
    digest = tree_digest(directory / "repo")
    if fetch.get("tree_sha256") not in (None, digest):
        shutil.rmtree(directory / "repo")
        raise SystemExit(f"bench_tasks: {task_id}: the fetched tree is not the one recorded in fetch.json")
    if record_digest and "tree_sha256" not in fetch:
        fetch["tree_sha256"] = digest
        fetch_path.write_text(json.dumps(fetch, indent=2) + "\n", encoding="utf-8")
    return directory / "repo"


def _version_text(python):
    done = subprocess.run([python, "-c", "import platform,sys;print(platform.python_implementation(),"
                           "platform.python_version(),sys.platform,platform.machine())"],
                          capture_output=True, text=True)
    return done.stdout.strip()


ENV_LOCK = "environment.lock"


def union_requirements(tasks_dir):
    """Requirements of the one environment every task is scored in: pytest plus every external task's list."""
    wanted = ["pytest"]
    for path in sorted(Path(tasks_dir).glob("*/fetch.json")):
        for item in _load_json(path).get("install", []):
            if item not in wanted:
                wanted.append(item)
    return wanted


def build_environment(scratch, requirements=None, lock=None):
    """One virtualenv for the whole benchmark (the harness resolves every task's argv on one PATH).

    Built from binary wheels only, from the scrubbed environment: an sdist would run its setup code here.
    Dependencies only: no task's own project is ever installed into it. Returns the interpreter path.
    """
    env_dir = Path(scratch) / "bench-env"
    env = clean_env(Path(scratch) / "bench-env-profile")
    subprocess.run([sys.executable, "-m", "venv", str(env_dir)], check=True, capture_output=True, env=env)
    python = str(env_dir / "bin" / "python")
    args = ["-r", str(lock)] if lock else list(requirements)
    subprocess.run([python, "-m", "pip", "install", "-q", "--only-binary=:all:", *args], check=True,
                   capture_output=True, timeout=900, env=env)
    return python


def write_lock(tasks_dir, scratch):
    """Resolve the union of requirements once, commit the resolved versions, and bind them into the manifest."""
    tasks_dir = Path(tasks_dir)
    python = build_environment(scratch, union_requirements(tasks_dir))
    env = clean_env(Path(scratch) / "bench-env-profile")
    freeze = subprocess.run([python, "-m", "pip", "freeze"], capture_output=True, text=True, env=env, check=True)
    lines = sorted(line for line in freeze.stdout.splitlines() if line.strip())
    (tasks_dir / ENV_LOCK).write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest_path = tasks_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    manifest["environment"] = {
        "python": _version_text(python), "lock": ENV_LOCK, "lock_sha256": file_sha256(tasks_dir / ENV_LOCK),
        "build": "python3 -m venv ENV && ENV/bin/pip install --only-binary=:all: -r " + ENV_LOCK +
                 ", then put ENV/bin first on the PATH that resolves python3 for visible and hidden commands"}
    _write_manifest(manifest_path, manifest)
    return python


def verify_external(tasks_dir, hidden_root, task_id, task, scratch, python):
    fetch = _load_json(Path(tasks_dir) / task_id / "fetch.json")
    bundle = Path(hidden_root) / task["hidden_bundle"]
    result = {"task": task_id, "origin": "external", "kind": task["kind"]}
    start = Path(scratch) / f"{task_id}-start"
    fixed = Path(scratch) / f"{task_id}-fix"
    materialize(fetch, start)
    result["start_tree_sha256"] = tree_digest(start)
    export_tree(fetch["repo_url"], fetch["fix_sha"], fixed)
    result["python"] = _version_text(python)
    scoring_dir = Path(scratch) / f"{task_id}-scoring"
    scoring_dir.mkdir()
    scoring = _Scoring(scoring_dir, python)
    started = time.monotonic()
    result["visible_on_start"] = _outcome(scoring.visible(start, task))
    result["visible_on_start_seconds"] = round(time.monotonic() - started, 2)
    # Measured, not required: a pull request may legitimately correct an old test, and then the visible suite is
    # red on the reference fix (disclosed in docs/bench/task-sourcing.md); a retry-until-visible-passes arm sees that.
    result["visible_on_reference"] = _outcome(scoring.visible(fixed, task))
    _score_pair(result, "start", scoring, start, hidden_root, bundle, task, python)
    tail = _score_pair(result, "reference", scoring, fixed, hidden_root, bundle, task, python)
    result["reference_tail"] = "" if result["hidden_on_reference"] == "pass" else tail
    if fetch.get("tree_sha256") not in (None, result["start_tree_sha256"]):
        result["inconsistent"] = True
        result["reason"] = "the fetched base tree differs from the tree_sha256 recorded in fetch.json"
    return _finish(result, True, "pass")


def manifest_sha(tasks_dir, rev=None):
    """sha256 of manifest.json: the working-tree bytes, or the committed blob at `rev` (what the freeze records)."""
    path = Path(tasks_dir) / "manifest.json"
    if rev is None:
        return file_sha256(path)
    blob = subprocess.run(["git", "-C", str(path.parent), "show", f"{rev}:./{path.name}"], capture_output=True)
    if blob.returncode:
        raise SystemExit(f"bench_tasks: cannot read the manifest at {rev}: {blob.stderr.decode()[-200:]}")
    return hashlib.sha256(blob.stdout).hexdigest()


def seal(tasks_dir, hidden_root, task_id):
    """Write the hidden bundle's per-file hashes and digest into the task's task.json."""
    path = Path(tasks_dir) / task_id / "task.json"
    task = _load_json(path)
    bundle = Path(hidden_root) / task["hidden_bundle"]
    if not bundle.is_dir():
        raise SystemExit(f"bench_tasks: no hidden bundle at {task['hidden_bundle']} under the hidden root")
    files = bundle_files(bundle)
    task["hidden_files"] = files
    task["hidden_sha256"] = bundle_digest(files)
    if task.get("origin") == "external":
        task["tree_sha256"] = _load_json(Path(tasks_dir) / task_id / "fetch.json").get("tree_sha256", "")
    elif (Path(tasks_dir) / task_id / "repo").is_dir():
        task["tree_sha256"] = tree_digest(Path(tasks_dir) / task_id / "repo")
    path.write_text(json.dumps(task, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return task["hidden_sha256"]


def build_manifest(tasks_dir):
    path = Path(tasks_dir) / "manifest.json"
    previous = _load_json(path) if path.is_file() else {"schema": MANIFEST_SCHEMA, "frozen": False}
    path.write_text(json.dumps(derived_manifest(tasks_dir, previous), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def hidden_from_pr(tasks_dir, hidden_root, task_id, scratch):
    """Build an external task's hidden bundle from the PR's own test files at fix_sha, then seal it."""
    fetch = _load_json(Path(tasks_dir) / task_id / "fetch.json")
    fixed = Path(scratch) / f"{task_id}-fix"
    export_tree(fetch["repo_url"], fetch["fix_sha"], fixed)
    bundle = Path(hidden_root) / task_id
    bundle.mkdir(parents=True, exist_ok=True)
    for rel in fetch["hidden_test_files"]:
        target = bundle / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixed / rel, target)
    (bundle / "run_hidden.py").write_text(HIDDEN_RUNNER, encoding="utf-8")
    (bundle / "hidden.json").write_text(json.dumps(
        {"files": fetch["hidden_test_files"], "run": fetch["hidden_test_files"],
         "pytest_args": fetch.get("pytest_args", [])}, indent=2) + "\n", encoding="utf-8")
    (bundle / "verify.json").write_text(json.dumps(
        {"command": ["python3", ".sigma-hidden/run_hidden.py"]}, indent=2) + "\n", encoding="utf-8")
    return seal(tasks_dir, hidden_root, task_id)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "verify", "seal", "build-manifest", "hidden-from-pr", "manifest-sha", "materialize", "lock"):
        p = sub.add_parser(name)
        p.add_argument("--tasks", default=str(TASKS_DIR))
        if name in ("check", "verify", "seal", "hidden-from-pr"):
            p.add_argument("--hidden-root", default=None)
        if name in ("seal", "hidden-from-pr"):
            p.add_argument("task_id")
        if name == "materialize":
            p.add_argument("task_ids", nargs="+")
            p.add_argument("--record-digest", action="store_true")
            p.add_argument("--cache", default=None, help="operator-side tree cache directory (outside the repository)")
        if name == "manifest-sha":
            p.add_argument("--rev", default=None, help="hash the manifest as committed at this git revision")
        if name == "check":
            p.add_argument("--frozen", action="store_true")
        if name in ("verify", "hidden-from-pr", "lock"):
            p.add_argument("--scratch", default=None)
        if name == "verify":
            p.add_argument("--only", nargs="*", default=None)
            p.add_argument("--external", action="store_true")
            p.add_argument("--json", default=None)
    return parser


def main(argv=None):
    if os.name != "posix":
        raise SystemExit("bench_tasks: POSIX only (process groups, the harness's own launcher contract)")
    args = _parser().parse_args(argv)
    tasks_dir = Path(args.tasks)
    hidden = Path(getattr(args, "hidden_root", None) or DEFAULT_HIDDEN)
    if args.cmd == "manifest-sha":
        print(manifest_sha(tasks_dir, args.rev))
        return 0
    if args.cmd == "build-manifest":
        build_manifest(tasks_dir)
        return 0
    if args.cmd == "lock":
        if not args.scratch:
            raise SystemExit("bench_tasks: --scratch DIR is required")
        write_lock(tasks_dir, args.scratch)
        return 0
    if args.cmd == "materialize":
        for task_id in args.task_ids:
            print(materialize_task(tasks_dir, task_id, args.record_digest, args.cache))
        return 0
    if args.cmd == "seal":
        print(seal(tasks_dir, hidden, args.task_id))
        return 0
    if args.cmd == "hidden-from-pr":
        if not args.scratch:
            raise SystemExit("bench_tasks: --scratch DIR is required")
        print(hidden_from_pr(tasks_dir, hidden, args.task_id, args.scratch))
        return 0
    if args.cmd == "check":
        problems = check(tasks_dir, getattr(args, "hidden_root", None), args.frozen)
        for line in problems:
            print(f"FINDING {line}")
        print(f"bench_tasks check: {len(problems)} finding(s)")
        return 1 if problems else 0
    return _verify_command(args, tasks_dir, hidden)


def _verify_command(args, tasks_dir, hidden):
    problems = check(tasks_dir, hidden)
    tasks = load_tasks(tasks_dir)
    results = []
    python = sys.executable
    if args.external:
        if not args.scratch:
            raise SystemExit("bench_tasks: --external needs --scratch DIR")
        lock = Path(tasks_dir) / ENV_LOCK
        if not lock.is_file():
            raise SystemExit(f"bench_tasks: {ENV_LOCK} is missing; run `lock --scratch DIR` first")
        python = build_environment(args.scratch, lock=lock)
    for task_id, task in tasks.items():
        if args.only and task_id not in args.only:
            continue
        if task["origin"] == "external":
            if not args.external:
                results.append({"task": task_id, "origin": "external", "status": "skipped",
                                "reason": "pass --external --scratch DIR (network)"})
                continue
            results.append(verify_external(tasks_dir, hidden, task_id, task, args.scratch, python))
        else:
            results.append(verify_internal(tasks_dir, hidden, task_id, task, python=python))
    for row in results:
        print(json.dumps(row, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps({"check": problems, "results": results}, indent=2) + "\n",
                                   encoding="utf-8")
    failed = [r["task"] for r in results if r["status"] == "FAILED"]
    print(f"bench_tasks verify: {sum(r['status'] == 'verified' for r in results)} verified, "
          f"{sum(r['status'] == 'skipped' for r in results)} skipped, {len(failed)} failed, "
          f"{len(problems)} check finding(s)")
    for line in problems:
        print(f"FINDING {line}")
    return 1 if (failed or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
