"""Planned pytest red-before-green proof (#267), separate from legacy advisory witnesses.

One explicit collection and test run per verify. No pytest plugin dependency. Only assertion
failures attributed by pytest's per-node summary qualify. Whole test-file bytes bind red to green;
external fixtures/helpers are not part of that identity. Local evidence is not tamper-proof.
"""
import hashlib
import importlib.util
import json
import math
import pathlib
import re
import subprocess
import time


def _witness():
    spec = importlib.util.spec_from_file_location('witness', pathlib.Path(__file__).with_name('witness.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROVENANCE = 'planned-pytest-v1'
MAX_SELECTORS = 25  # protocol scope ceiling, matching flake_check.MAX_SCOPED_FILES


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def selectors(plan):
    """Canonical ## Tests entries are backticked pytest file/node selectors, one per bullet."""
    if plan is None:
        raise ValueError('plan has no ## Tests section')
    active, found, result = False, False, []
    for line in pathlib.Path(plan).read_text().splitlines():
        if line.strip() == '## Tests':
            if found:
                raise ValueError('duplicate ## Tests section')
            active = found = True
            continue
        if line.startswith('## '):
            active = False
        if not active or not line.strip():
            continue
        match = re.fullmatch(r'\s*- `([^`]+)`\s*', line)
        if not match:
            raise ValueError('## Tests requires one backticked pytest selector per bullet')
        selector = match[1]
        file = selector.split('::')[0]
        parts = pathlib.PurePosixPath(file).parts
        if (not file.endswith('.py') or not parts or file.startswith(('/', '-'))
                or any(p in ('.', '..') for p in parts) or '\\' in selector
                or any(c.isspace() for c in selector) or any(c in selector for c in ';|&')):
            raise ValueError('unsafe pytest selector: ' + selector)
        if selector not in result:
            result.append(selector)
    if not result or len(result) > MAX_SELECTORS:
        raise ValueError('## Tests requires 1–25 pytest selectors; use --no-tests for an explicit exception')
    return result


def _run(root, argv):
    return subprocess.run(argv, cwd=root, capture_output=True, text=True)


def _selected(node, scope):
    return any(node == s or node.startswith(s + '::') or node.startswith(s + '[') for s in scope)


def collect(root, scope, run=None):
    run = run or _run
    proc = run(root, ['python3', '-m', 'pytest', '--collect-only', '-q', '--color=no',
                      '--rootdir=' + str(root), *scope])
    nodes = [line.strip() for line in proc.stdout.splitlines() if '::' in line]
    if proc.returncode or not nodes or any(not _selected(n, scope) for n in nodes):
        raise ValueError('planned pytest collection failed or returned unexpected node ids')
    if any(not any(_selected(n, [s]) for n in nodes) for s in scope):
        raise ValueError('a planned selector collected no tests')
    return sorted(set(nodes))


def _hashes(root, nodes):
    root = pathlib.Path(root).resolve()
    result, cache = {}, {}
    for node in nodes:
        file = (root / node.split('::')[0]).resolve()
        if not file.is_relative_to(root):
            raise ValueError('planned test escapes the repository')
        if file not in cache:
            cache[file] = digest(file)
        result[node] = cache[file]
    return result


def observe(sdlc_dir, goal, root, plan, run=None):
    """Return green proof or an explicit absence; write only attributed, stable assertion reds."""
    run = run or _run
    try:
        plan_hash = digest(plan) if plan else None
        scope = selectors(plan)
        # Snapshot before collection too: imports may edit tests or the plan.
        before_files = _hashes(root, scope)
        nodes = collect(root, scope, run)
        hashes = _hashes(root, nodes)
        if before_files != _hashes(root, scope) or plan_hash != digest(plan):
            raise ValueError('test or plan changed during collection')
        started = time.time()
        proc = run(root, ['python3', '-m', 'pytest', '-q', '-rA', '--tb=short', '--color=no',
                          '--rootdir=' + str(root), *nodes])
        completed = time.time()
        if hashes != _hashes(root, nodes) or plan_hash != digest(plan):
            raise ValueError('test or plan changed during observation')
        passed, assertion = set(), set()
        # Captured test output can itself contain FAILED/PASSED diagnostics. Only pytest's
        # final summary is authoritative; absent summaries earn no proof.
        summaries = re.split(r"^=+ short test summary info =+\s*$", proc.stdout, flags=re.M)
        summary = summaries[-1] if len(summaries) > 1 else ""
        for line in summary.splitlines():
            if line.startswith('PASSED '):
                node = line[len('PASSED '):].strip()
                if node in hashes:
                    passed.add(node)
            elif line.startswith('FAILED '):
                node, sep, reason = line[len('FAILED '):].partition(' - ')
                if node in hashes and sep and re.match(r'(AssertionError\b|assert\b)', reason):
                    assertion.add(node)
        # Collection/interruption/internal/usage failures cannot create trustworthy red.
        if proc.returncode == 1:
            w = _witness()
            for node in sorted(assertion):
                w.record(sdlc_dir, goal, node, 'assertion',
                         w.source_hash(w.test_source(node, root=root)),
                         detail='planned pytest assertion', provenance=PROVENANCE,
                         file_hash=hashes[node], observed_at=completed)
        return {'provenance': PROVENANCE, 'plan_sha256': plan_hash, 'selectors': scope,
                'nodes': hashes, 'started_at': started, 'completed_at': completed,
                'passed': proc.returncode == 0 and passed == set(nodes),
                'exit': proc.returncode, 'error': '' if passed == set(nodes) else 'planned tests did not all pass'}
    except (OSError, ValueError, TypeError) as exc:
        return {'passed': False, 'error': str(exc)}


def _timestamp(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def refusal(sdlc_dir, goal, root, plan, proof):
    """Fail closed; stream one goal's journal, retaining at most one eligible red per node."""
    try:
        scope = selectors(plan)
        if not isinstance(proof, dict) or proof.get('provenance') != PROVENANCE or proof.get('passed') is not True:
            return 'no observed green for planned tests'
        if proof.get('plan_sha256') != digest(plan) or proof.get('selectors') != scope:
            return 'plan changed since observed green'
        nodes = proof.get('nodes')
        if not isinstance(nodes, dict) or not nodes or any(not _selected(n, scope) for n in nodes):
            return 'invalid planned test identities'
        if any(not any(_selected(n, [s]) for n in nodes) for s in scope) or nodes != _hashes(root, nodes):
            return 'planned test files changed since observed green'
        started, completed = proof.get('started_at'), proof.get('completed_at')
        if not _timestamp(started) or not _timestamp(completed) or completed < started:
            return 'invalid observed green ordering'
        missing = set(nodes)
        try:
            with _witness().path(sdlc_dir, goal).open() as journal:
                for line in journal:
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    node, at = row.get('test'), row.get('observed_at')
                    if (isinstance(node, str) and node in missing and row.get('provenance') == PROVENANCE
                            and row.get('kind') == 'assertion' and row.get('file_hash') == nodes[node]
                            and _timestamp(at) and at < started):
                        missing.remove(node)
        except FileNotFoundError:
            pass
        if missing:
            return 'no matching assertion red before green: ' + ', '.join(sorted(missing))
        return ''
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return 'cannot validate planned test proof: ' + str(exc)
