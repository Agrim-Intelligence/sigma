"""Planned pytest red-before-green proof (#267), separate from legacy advisory witnesses.

One explicit collection and test run per verify. No pytest plugin dependency. Only assertion
failures attributed by pytest's per-node summary qualify. Whole test-file bytes bind red to green;
external fixtures/helpers are not part of that identity. Local evidence is not tamper-proof.

#2084: a bullet `runner: path > suite > test` names a runner configured at `tests.runners.<runner>`. Green is exit 0;
red is a nonzero exit (and, when the runner sets `red_pattern`, a match in the output tail). Not measured against a
real vitest: the tests use a shell stub.
"""
import collections
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time


def _witness():
    spec = importlib.util.spec_from_file_location('witness', pathlib.Path(__file__).with_name('witness.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROVENANCE = 'planned-pytest-v1'
RUNNER_PROVENANCE = 'planned-runner-v1'            # #2084: red confirmed by the runner's `red_pattern`
RUNNER_EXIT_PROVENANCE = 'planned-runner-exit-v1'  # #2084: red from a nonzero exit alone (weaker, kind 'other')
#: Every (provenance, kind) pair that is a red; the kind rides with the provenance so a weak row cannot pass as a strong one.
RED_PAIRS = frozenset(((PROVENANCE, 'assertion'), (RUNNER_PROVENANCE, 'assertion'), (RUNNER_EXIT_PROVENANCE, 'other')))
RUNNER_OUTPUT_CHARS = 200000                       # the red pattern sees only this much of the output's tail
DEFAULT_RUNNER_TIMEOUT = 3600                      # a chosen bound, not a measured one; loop passes the config's
MAX_SELECTORS = 25  # protocol scope ceiling, matching flake_check.MAX_SCOPED_FILES
WIDE_COLUMNS = '4000'


_BOUNDED = _RUNNER_ID = None


def _bounded():
    global _BOUNDED
    if _BOUNDED is None:
        spec = importlib.util.spec_from_file_location('bounded_run', pathlib.Path(__file__).with_name('bounded_run.py'))
        _BOUNDED = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_BOUNDED)
    return _BOUNDED


Runner = collections.namedtuple('Runner', 'runner file name')


def _rejected(message, kind=None):
    """#2116: a plain ValueError (callers and tests print its type) carrying `kind` for the two cases `--land` can take an
    exception for: 'no-selectors' (nothing planned) and 'unconfigured-runner' (a runner bullet whose runner has no
    config). Any other rejection has no kind: a malformed section is a plan defect, not a missing runner."""
    error = ValueError(message)
    error.kind = kind
    return error


def parse_runner(node):
    """#2084: Runner(runner, file, name) for a runner node id `runner: file > suite > test`, else None. `file` is the
    text before the FIRST ` > `; `name` is everything after it (suite titles may hold `>`), empty for the whole file.
    The pattern lives in witness.RUNNER_ID, defined once so the witness and this module cannot disagree."""
    global _RUNNER_ID
    if _RUNNER_ID is None:
        _RUNNER_ID = _witness().RUNNER_ID
    match = _RUNNER_ID.match(node) if isinstance(node, str) else None
    if not match:
        return None
    file, _, name = match[2].partition(' > ')
    return Runner(match[1], file, name)


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


_NONE = re.compile(r'none: (\S.*)')
#: #1970: `.sdlc/features/` is the unit registry, which #1941 puts in every unit goal's own commit; it is Sigma's
#: membership record, not code, so a docs-only goal inside a unit is still docs-only.
_DOC_DIRS = ('docs/', '.sdlc/plans/', '.sdlc/research/', '.sdlc/acceptance/', '.sdlc/features/')
_DOC_SUFFIXES = ('.md', '.txt', '.png', '.svg')


def none_reason(plan):
    """#1942: the reason of a `## Tests` section that is exactly one `none: <reason>` line, else None."""
    if plan is None:
        return None
    body, active = [], False
    for line in pathlib.Path(plan).read_text().splitlines():
        if line.strip() == '## Tests':
            if active or body:
                return None
            active = True
            continue
        if line.startswith('## '):
            active = False
        if active and line.strip():
            body.append(line.strip())
    match = _NONE.fullmatch(body[0]) if len(body) == 1 else None
    return match[1].strip() if match else None


def docs_only(paths):
    """#1942: True when every changed path is documentation (never an empty list, never a `..` path)."""
    paths = [p.strip() for p in paths if p.strip()]
    return bool(paths) and all(
        '..' not in pathlib.PurePosixPath(p).parts
        and (p.startswith(_DOC_DIRS) or p.endswith(_DOC_SUFFIXES)) for p in paths)


_PLACEHOLDER = re.compile(r'\{\{|\}\}|\{(\w+)\}')
_SENTINEL = {'file': '\ue000', 'name': '\ue001'}
_FLAG_EQUALS = re.compile('--[A-Za-z0-9][\\w-]*=[\ue000\ue001]')
_REDIRECT = re.compile(r'[0-9&]*[<>]+&?')
#: Words after which the next word is itself run as a command or script text.
_WRAPPERS = frozenset(('eval', 'exec', 'command', 'builtin', 'env', 'nohup', 'time', 'xargs', 'sudo', 'nice', 'source', '.'))
_SHELLS = frozenset(('sh', 'bash', 'zsh', 'dash', 'ksh'))
#: A shell shipped with `-c <script>`: `-c` or any short-option cluster holding `c` (`-lc`, `-ec`).
_SHELL_C = re.compile(r'-[A-Za-z]*c[A-Za-z]*')
#: An interpreter's eval flag: its next word is program text. Only the common interpreters; a template author who
#: reaches another one with a placeholder owns that (documented in docs/configuration.md).
_INTERPRETERS = re.compile(r'(python[0-9.]*|node|nodejs|perl|ruby|php|bun|deno)')
_EVAL_FLAG = re.compile(r'-[A-Za-z]*[cepE][A-Za-z]*|--eval|--print|--command')
_CONTROL = frozenset((';', '&&', '||', '|', '&', '(', ')', '!'))


def _unsafe_runner_file(file):
    return (not file or file.startswith(('/', '-')) or any(p in ('', '.', '..') for p in file.split('/'))
            or '\\' in file or '\x00' in file or any(c.isspace() for c in file) or any(c in file for c in ';|&'))


def render_command(template, file, name, runner='<name>'):
    """#2084: the shell command a runner bullet runs. PURE: it spawns nothing and raises ValueError naming
    `tests.runners.<runner>`. An ALLOWLIST on the template, checked before any value is substituted (a quote-state
    machine is unsound against `$( "..." )`, `$'..'`, a backslash before a placeholder and heredocs):
    a template that uses {file}/{name} may hold none of ' " ` $ \\ or <<; every token holding a placeholder must BE
    the placeholder or a `--flag=` plus it; a placeholder may not be a command word, follow a wrapper (`eval`, `env`,
    ...), `sh -c` or a redirection. `{{`/`}}` are literal braces, undone on the TEMPLATE only. Then each value is
    `shlex.quote`d; one beginning with `-`, or holding a newline or NUL, is refused."""
    key = 'tests.runners.' + runner
    pieces, pos = [], 0                                     # ('lit', text) | ('ph', 'file'|'name')
    for found in _PLACEHOLDER.finditer(template):
        pieces.append(('lit', template[pos:found.start()]))
        if found[1] is None:
            pieces.append(('lit', found[0][0]))
        elif found[1] in _SENTINEL:
            pieces.append(('ph', found[1]))
        else:
            raise ValueError('%s: unknown placeholder {%s}; only {file} and {name} (write {{ and }} for literal braces)'
                             % (key, found[1]))
        pos = found.end()
    pieces.append(('lit', template[pos:]))
    if any(c in template for c in _SENTINEL.values()):
        raise ValueError('%s: the template holds a reserved character' % key)
    values = {'file': file, 'name': name}
    for label, value in values.items():
        if not isinstance(value, str) or '\n' in value or '\r' in value or '\x00' in value:
            raise ValueError('%s: {%s} may not contain a newline or NUL' % (key, label))
        if value.startswith('-'):
            raise ValueError("%s: {%s} may not start with '-' (it would read as an option)" % (key, label))
    if not file:
        raise ValueError('%s: {file} may not be empty' % key)
    if any(kind == 'ph' for kind, _ in pieces):
        literal = ''.join(text for kind, text in pieces if kind == 'lit')
        if any(c in literal for c in ('\'', '"', '`', '$', '\\', '\n', '\r')) or '<<' in literal:
            raise ValueError('%s: a template that uses {file}/{name} may not contain \' " ` $ \\ a newline or <<; write the '
                             'placeholder bare, Sigma quotes it (put quotes, $VAR and heredocs in a script the '
                             'template calls)' % key)
        skeleton = ''.join(text if kind == 'lit' else _SENTINEL[text] for kind, text in pieces)
        try:
            tokens = shlex.split(skeleton)
        except ValueError as exc:
            raise ValueError('%s: the template does not parse: %s' % (key, exc)) from None
        previous, command_word, at_command, plain_args = None, None, True, 0
        for token in tokens:
            if any(c in token for c in _SENTINEL.values()):
                if token not in _SENTINEL.values() and not _FLAG_EQUALS.fullmatch(token):
                    raise ValueError('%s: a placeholder must be a whole word or follow `--flag=`: %s'
                                     % (key, token.replace(_SENTINEL['file'], '{file}').replace(_SENTINEL['name'], '{name}')))
                shell = command_word in _SHELLS
                if (at_command or previous in _WRAPPERS
                        or (shell and (plain_args == 0 or (previous is not None and _SHELL_C.fullmatch(previous))))
                        or (_INTERPRETERS.fullmatch(command_word or '') and previous is not None
                            and _EVAL_FLAG.fullmatch(previous))
                        or (previous is not None and _REDIRECT.fullmatch(previous))):
                    raise ValueError('%s: a placeholder may not be a command word, follow a wrapper such as eval or '
                                     'an eval flag (sh -lc, python -c, node -e), be the first argument of a bare '
                                     'shell (sh {name}), or be a redirection target' % key)
            if token in _CONTROL:
                at_command, command_word, plain_args = True, None, 0
            elif at_command:
                command_word, at_command, plain_args = os.path.basename(token), False, 0
            elif not token.startswith('-'):
                plain_args += 1
            previous = token
    return ''.join(text if kind == 'lit' else shlex.quote(values[text]) for kind, text in pieces)


def has_runner_bullets(plan):
    """True when ``plan``'s `## Tests` holds a runner-named bullet. Total: an unreadable plan has none."""
    try:
        active = False
        for line in pathlib.Path(plan).read_text().splitlines():
            if line.strip() == '## Tests':
                active = True
            elif line.startswith('## '):
                active = False
            found = re.fullmatch(r'\s*- `([^`]+)`\s*', line) if active else None
            if found and parse_runner(found[1]):
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def selectors(plan, runners=None):
    """Canonical ## Tests entries are backticked pytest file/node selectors, one per bullet, or (#2084) a runner
    bullet `runner: path > suite > test` whose runner is configured in `runners` ({name: {command, red_pattern}})."""
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
        runner = parse_runner(selector)
        if runner:
            spec = (runners or {}).get(runner.runner)
            if spec is None:
                raise _rejected("unknown test runner '%s': configure tests.runners.%s in .sdlc/config.json"
                                % (runner.runner, runner.runner), 'unconfigured-runner')
            if _unsafe_runner_file(runner.file):
                raise ValueError('unsafe runner file: ' + runner.file)
            if '\x00' in runner.name or '`' in runner.name:
                raise ValueError('unsafe runner test name: ' + runner.name)
            render_command(spec['command'], runner.file, runner.name, runner.runner)     # a bad template fails at red
            if selector not in result:
                result.append(selector)
            continue
        file = selector.split('::')[0]
        parts = pathlib.PurePosixPath(file).parts
        if (not file.endswith('.py') or not parts or file.startswith(('/', '-'))
                or any(p in ('.', '..') for p in parts) or '\\' in selector
                or any(c.isspace() for c in selector) or any(c in selector for c in ';|&')):
            raise ValueError('unsafe pytest selector: ' + selector)
        if selector not in result:
            result.append(selector)
    if not result or len(result) > MAX_SELECTORS:
        message = '## Tests requires 1–25 pytest selectors; use --no-tests, or the line `none: <reason>` for a docs-only change'
        raise _rejected(message, 'no-selectors' if not result else None)
    return result


def tests_section_error(plan, runners=None):
    """#2116: '' when `plan`'s `## Tests` is one `work.py pr` can use (selectors, runner bullets, or the docs-only
    `none: <reason>` line), else the parser's own message. This is the parser `pr()` and `loop.py red` run, not a
    copy of it, so Plan and Plan-Review can never accept what the PR step rejects."""
    try:
        if none_reason(plan):
            return ''
        selectors(plan, runners)
        return ''
    except (OSError, ValueError) as exc:
        return str(exc)


def unrecordable_case(plan, runners, runner_trusted):
    """#2116: `(case, detail)` when no runner in this container can record red for `plan`, else None. Cases:
    'no-selectors' (nothing is planned), 'unconfigured-runner' (a runner bullet with no `tests.runners` entry) and
    'runner-untrusted' (runner bullets, but only the git-local grant runs a runner command and `runner_trusted` is
    False). A malformed section (a `none:` line is one: it is not a bullet) and a plain pytest plan are never a case:
    their red is recordable or their defect is the plan's."""
    try:
        if plan is None:
            return None
        selectors(plan, runners)
    except ValueError as exc:
        kind = getattr(exc, 'kind', None)
        return (kind, str(exc)) if kind else None
    except OSError:
        return None
    if has_runner_bullets(plan) and not runner_trusted:
        return ('runner-untrusted', 'the plan names a test runner and the active trust (the environment approval) does '
                'not cover runner commands; only the git-local grant does')
    return None


def planned_without_red(sdlc_dir, goal, plan):
    """#2116: the planned pytest selectors that have no red row in the goal's witness journal -- the gap Review may
    not start with. [] when every one has a row, and when nothing applies: no plan, or a section `selectors(plan)` (no
    runners granted) rejects -- `none:`, a runner bullet (its red depends on the trust, which `unrecordable_case`
    judges) or a malformed one (Plan-Review's). A selector is satisfied by any red row for a node it selects, so a file selector needs one node."""
    try:
        if plan is None:
            return []
        scope = selectors(plan)
    except (OSError, ValueError):
        return []
    reds = [row.get('test') for row in _witness().witnesses(sdlc_dir, goal)
            if (row.get('provenance'), row.get('kind')) in RED_PAIRS]
    return [s for s in scope if not any(isinstance(t, str) and _selected(t, [s]) for t in reds)]


def _run(root, argv):
    # Piped pytest sizes its -rA summary from COLUMNS (80 if unset) and drops the ' - reason' of a
    # longer line (#414); an inherited narrow value is overridden on purpose.
    return subprocess.run(argv, cwd=root, capture_output=True, text=True,
                          env={**os.environ, 'COLUMNS': WIDE_COLUMNS})


def _selected(node, scope):
    return any(node == s or node.startswith(s + '::') or node.startswith(s + '[') for s in scope)


def collect(root, scope, run=None, python='python3'):
    run = run or _run
    runner_nodes = [s for s in scope if parse_runner(s)]            # #2084: a runner node is its own id
    scope = [s for s in scope if not parse_runner(s)]
    if not scope:
        return sorted(set(runner_nodes))
    proc = run(root, [python, '-m', 'pytest', '--collect-only', '-q', '--color=no',
                      '--rootdir=' + str(root), *scope])
    nodes = [line.strip() for line in proc.stdout.splitlines() if '::' in line]
    if proc.returncode or not nodes or any(not _selected(n, scope) for n in nodes):
        raise ValueError('planned pytest collection failed or returned unexpected node ids')
    if any(not any(_selected(n, [s]) for n in nodes) for s in scope):
        raise ValueError('a planned selector collected no tests')
    return sorted(set(nodes) | set(runner_nodes))


def _hashes(root, nodes):
    root = pathlib.Path(root).resolve()
    result, cache = {}, {}
    for node in nodes:
        runner = parse_runner(node)
        file = (root / (runner.file if runner else node.split('::')[0])).resolve()
        if not file.is_relative_to(root):
            raise ValueError('planned test escapes the repository')
        if file not in cache:
            cache[file] = digest(file)
        result[node] = cache[file]
    return result


def _split_traceback_assertions(output, failed):
    """Return uniquely attributable assertion failures from pytest's report blocks.

    Pytest 7--9 prints bare ``FAILED <node>`` lines in its short summary, while
    the assertion type lives in the preceding traceback.  The summary still
    supplies the authoritative failing node; this only joins it to a report
    block when its file and unparameterized function name select exactly one
    such node.  Ambiguous parameterized reports fail closed.
    """
    reports = re.split(r"^=+ short test summary info =+\s*$", output, flags=re.M)[0]
    # Captured test stdout/stderr shares pytest's report stream.  It is
    # untrusted test-controlled text, so do not parse any split traceback
    # attribution from a run that contains it. Refusing a noisy real assertion
    # is safer than crediting a forged one.
    if re.search(r"^-+ Captured (?:stdout|stderr|log) .*-+$", reports, re.M):
        return set()
    assertions = set()
    for block in re.split(r"(?m)^_{3,}.*_{3,}\s*$", reports):
        if not re.search(r"^E\s+(?:AssertionError\b|assert\b)", block, re.M):
            continue
        locations = re.findall(r"^(?P<file>[^:\n]+\.py):\d+: in (?P<name>[^\s]+)",
                               block, re.M)
        for report_file, report_name in locations:
            report_file = report_file.lstrip("./")
            candidates = {
                node for node in failed
                if (node.split("::", 1)[0] == report_file
                    or report_file.endswith("/" + node.split("::", 1)[0]))
                and node.rsplit("::", 1)[-1].split("[", 1)[0] == report_name
            }
            if len(candidates) == 1:
                assertions.update(candidates)
    return assertions


def _exec_runner(command, cwd, seconds):
    """Default seam for a runner command: -> (status, exit, output). bounded_run's ring-buffered, process-group
    TERM-then-KILL run; where process groups do not exist it REFUSES (status 'refused'), never runs unbounded."""
    bounded = _bounded()
    done = bounded.run_group(command, cwd, min(seconds, bounded.MAX_SECONDS), shell=True,
                             env=bounded.unattended_env({**os.environ, 'COLUMNS': WIDE_COLUMNS}), merge=True,
                             tail_bytes=RUNNER_OUTPUT_CHARS)
    return done.outcome, done.code, (done.out or '') + (('\n' + done.detail) if done.detail else '')


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def runner_command(node, runners):
    """-> (command, red_pattern or None) for runner node `node`, rendered from the CURRENT config."""
    found = parse_runner(node)
    spec = (runners or {}).get(found.runner)
    if spec is None:
        raise ValueError("unknown test runner '%s': configure tests.runners.%s in .sdlc/config.json"
                         % (found.runner, found.runner))
    return render_command(spec['command'], found.file, found.name, found.runner), spec.get('red_pattern')


def _run_runners(root, nodes, runners, timeout, exec_runner):
    """Run each runner node under ONE shared deadline. -> {node: {...}} plus the `reds` to record. Nothing here
    writes. A nonzero exit is a candidate red unless it is 126/127 (not executable / not found), negative (a
    signal), or the run ended in a timeout/stop/refusal/error: a harness failure is not a red."""
    exec_runner = exec_runner or _exec_runner
    deadline = time.monotonic() + (timeout if timeout else DEFAULT_RUNNER_TIMEOUT)
    patterns = {}
    for node in nodes:
        pattern = runner_command(node, runners)[1]
        try:
            patterns[node] = re.compile(pattern) if pattern else None
        except re.error as exc:
            raise ValueError('tests.runners.%s.red_pattern is not a valid regular expression: %s'
                             % (parse_runner(node).runner, exc)) from None
    results = {}
    for node in nodes:
        command, pattern = runner_command(node, runners)
        entry = {'exit': None, 'red': None, 'status': 'not-run', 'command_sha256': _sha(command),
                 'pattern_sha256': _sha(pattern or '')}
        results[node] = entry
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue                                        # the shared deadline is spent: later runners do not start
        status, code, output = exec_runner(command, root, remaining)
        entry.update(status=status, exit=code if isinstance(code, int) and not isinstance(code, bool) else None)
        if status not in ('ok', 'failed') or entry['exit'] is None or entry['exit'] < 0 or entry['exit'] in (126, 127):
            continue
        if entry['exit'] > 0:
            tail = (output or '')[-RUNNER_OUTPUT_CHARS:]
            if patterns[node] is None:
                entry['red'] = 'exit'
            elif patterns[node].search(tail):
                entry['red'] = 'pattern'
    return results


def observe(sdlc_dir, goal, root, plan, run=None, runners=None, timeout=None, exec_runner=None, python='python3'):
    """Return green proof or an explicit absence; write only attributed, stable assertion reds.

    #2084: `runners` ({name: {command, red_pattern}}) lets the plan name runner bullets. Pytest nodes run exactly as
    before through `run`; runner commands go through the separate `exec_runner(command, cwd, seconds) -> (status,
    exit, output)` seam under ONE deadline (`timeout` seconds in all), inside the SAME started/completed pair and
    file-hash snapshot bracket. Callers grant `runners` only under the git-local trust."""
    run = run or _run
    try:
        plan_hash = digest(plan) if plan else None
        scope = selectors(plan, runners)
        # Snapshot before collection too: imports may edit tests or the plan.
        before_files = _hashes(root, scope)
        nodes = collect(root, scope, run, python)
        hashes = _hashes(root, nodes)
        if before_files != _hashes(root, scope) or plan_hash != digest(plan):
            raise ValueError('test or plan changed during collection')
        runner_nodes = [n for n in nodes if parse_runner(n)]
        pytest_nodes = [n for n in nodes if n not in runner_nodes]
        started = time.time()
        proc = run(root, [python, '-m', 'pytest', '-q', '-rA', '--tb=short', '--color=no',
                          '--rootdir=' + str(root), *pytest_nodes]) if pytest_nodes else None
        ran = _run_runners(root, runner_nodes, runners, timeout, exec_runner) if runner_nodes else {}
        completed = time.time()
        if hashes != _hashes(root, nodes) or plan_hash != digest(plan):
            raise ValueError('test or plan changed during observation')
        passed, assertion = set(), set()
        missing = []
        if proc is not None:
            # Captured test output can itself contain FAILED/PASSED diagnostics. Only pytest's
            # final summary is authoritative; absent summaries earn no proof.
            summaries = re.split(r"^=+ short test summary info =+\s*$", proc.stdout, flags=re.M)
            summary = summaries[-1] if len(summaries) > 1 else ""
            failed = set()
            # An expected failure (XFAIL/XPASS) ran and behaved as declared, so it is accounted for; a
            # whole-file selector over a file with one used to fail every verify. SKIPPED proves nothing.
            accounted = set()
            for line in summary.splitlines():
                if line.startswith(('PASSED ', 'XFAIL ', 'XPASS ')):
                    kind, _, rest = line.partition(' ')
                    node = rest.partition(' - ')[0].strip() if kind != 'PASSED' else rest.strip()
                    if node in hashes:
                        (passed if kind == 'PASSED' else accounted).add(node)
                elif line.startswith('FAILED '):
                    node, sep, reason = line[len('FAILED '):].partition(' - ')
                    if node in hashes:
                        failed.add(node)
                        if sep and re.match(r'(AssertionError\b|assert\b)', reason):
                            assertion.add(node)
            assertion.update(_split_traceback_assertions(proc.stdout, failed))
            # Collection/interruption/internal/usage failures cannot create trustworthy red.
            if proc.returncode == 1:
                w = _witness()
                for node in sorted(assertion):
                    w.record(sdlc_dir, goal, node, 'assertion',
                             w.source_hash(w.test_source(node, root=root)),
                             detail='planned pytest assertion', provenance=PROVENANCE,
                             file_hash=hashes[node], observed_at=completed)
            missing = sorted(set(pytest_nodes) - passed - accounted)
        exits = [proc.returncode] if proc is not None and proc.returncode else []
        errors = []
        if missing:
            errors.append('planned tests did not all pass: %d not PASSED/XFAIL (first: %s)' % (
                len(missing), ', '.join(missing[:3])))
        bad = []
        for node in runner_nodes:                           # in bullet order: the first nonzero is the aggregate exit
            entry = ran[node]
            if entry['exit'] == 0 and entry['status'] == 'ok':
                continue
            bad.append(node)
            exits.append(entry['exit'] if isinstance(entry['exit'], int) and entry['exit'] > 0 else 1)
            errors.append('runner test did not pass: %s (status %s, exit %s%s)' % (
                node, entry['status'], entry['exit'], '' if entry['red'] else '; earned no red'))
        w = _witness() if runner_nodes else None
        for node in runner_nodes:
            entry = ran[node]
            if entry['red']:
                strong = entry['red'] == 'pattern'
                w.record(sdlc_dir, goal, node, 'assertion' if strong else 'other',
                         w.source_hash(w.test_source(node, root=root)),
                         detail='bound:%s:%s' % (entry['command_sha256'], entry['pattern_sha256']),
                         provenance=RUNNER_PROVENANCE if strong else RUNNER_EXIT_PROVENANCE,
                         file_hash=hashes[node], observed_at=completed)
        result = {'provenance': PROVENANCE, 'plan_sha256': plan_hash, 'selectors': scope,
                  'nodes': hashes, 'started_at': started, 'completed_at': completed,
                  'passed': (proc is None or (proc.returncode == 0 and not missing)) and not bad,
                  'exit': exits[0] if exits else 0,
                  'error': '; '.join(errors)}
        if runner_nodes:
            result['runners'] = ran
        return result
    except (OSError, ValueError, TypeError) as exc:
        return {'passed': False, 'error': str(exc)}


def _timestamp(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _red_pairs(node):
    """The (provenance, kind) pairs that count as a red for `node`, checked as a PAIR so an exit-only row cannot pass
    as a pattern-confirmed one (#2084)."""
    if parse_runner(node):
        return {(RUNNER_PROVENANCE, 'assertion'), (RUNNER_EXIT_PROVENANCE, 'other')}
    return {(PROVENANCE, 'assertion')}


def refusal(sdlc_dir, goal, root, plan, proof, runners=None):
    """Fail closed; stream one goal's journal, retaining at most one eligible red per node.

    #2084: `runners` must be the CURRENT config's map. Each runner command is re-rendered from the current plan and
    config; the proof's `runners` hashes and the red row's `bound:` detail must both equal them, so editing a template
    or pattern after a red voids both."""
    try:
        scope = selectors(plan, runners)
        if not isinstance(proof, dict) or proof.get('provenance') != PROVENANCE or proof.get('passed') is not True:
            return 'no observed green for planned tests'
        if proof.get('plan_sha256') != digest(plan) or proof.get('selectors') != scope:
            return 'plan changed since observed green'
        nodes = proof.get('nodes')
        if not isinstance(nodes, dict) or not nodes or any(not _selected(n, scope) for n in nodes):
            return 'invalid planned test identities'
        if any(not any(_selected(n, [s]) for n in nodes) for s in scope) or nodes != _hashes(root, nodes):
            return 'planned test files changed since observed green'
        bound = {}
        for node in nodes:
            if parse_runner(node):
                command, pattern = runner_command(node, runners)
                bound[node] = (_sha(command), _sha(pattern or ''))
                seen = (proof.get('runners') or {}).get(node)
                if not isinstance(seen, dict) or (seen.get('command_sha256'), seen.get('pattern_sha256')) != bound[node]:
                    return 'runner command changed since observed: ' + node
        started, completed = proof.get('started_at'), proof.get('completed_at')
        if not _timestamp(started) or not _timestamp(completed) or completed < started:
            return 'invalid observed green ordering'
        missing, rebound = set(nodes), set()
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
                    if (isinstance(node, str) and node in missing
                            and (row.get('provenance'), row.get('kind')) in _red_pairs(node)
                            and row.get('file_hash') == nodes[node] and _timestamp(at) and at < started):
                        if node in bound and row.get('detail') != 'bound:%s:%s' % bound[node]:
                            rebound.add(node)           # a red for an older command or pattern
                        else:
                            missing.remove(node)
        except FileNotFoundError:
            pass
        if missing & rebound:
            return 'runner command changed since observed: ' + ', '.join(sorted(missing & rebound))
        if missing:
            return 'no matching assertion red before green: ' + ', '.join(sorted(missing))
        return ''
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return 'cannot validate planned test proof: ' + str(exc)


SEALED_PROVENANCE = 'sealed-pytest-v1'  # #847: red = ANY test-body failure on base content (see classify_sealed)


def classify_sealed(collect_rc, collect_out, run_rc, run_out, nodes):
    """Pure classifier for the sealed-pytest-v1 proof. -> {collection_errors, red, passed, other, error}.

    Unlike `observe`, ANY `FAILED` node counts as red once collection was clean and the run exited 1,
    including an ImportError raised inside the test body (D-1; that proves "symbol missing", not
    "assertion meaningful", and the exception class is returned per red so a reader can see it). Never
    red: a collection error (exit 2 or `ERROR <file>`), fixture `ERROR <node>`, skip, xfail, xpass,
    exit 3/4/5, a missing summary. Nodes are matched by collected id (never by splitting on ' - ').
    `red` maps node -> ('assertion'|'other', ExceptionClass-or-''). `run_rc=None` classifies the
    collect-only step alone. `witness.classify` is deliberately not used: it labels an in-body
    ImportError a collection error."""
    out = {'collection_errors': [], 'red': {}, 'passed': [], 'other': [], 'error': ''}
    files = []
    for line in (collect_out or '').splitlines():
        line = line.strip()
        if line.startswith('ERROR '):
            files.append(line[len('ERROR '):].partition(' - ')[0].replace('collecting ', '', 1).strip())
    if files or collect_rc == 2:
        out['collection_errors'] = files or ['<file not named by pytest>']
        return out
    if collect_rc == 5 or (collect_rc == 0 and not nodes):
        out['error'] = 'pytest collected no tests (exit %s)' % collect_rc
        return out
    if collect_rc != 0:
        out['error'] = 'pytest --collect-only exited %s' % collect_rc
        return out
    if run_rc is None:
        return out
    if run_rc not in (0, 1):
        out['error'] = 'pytest run exited %s (not 0 or 1)' % run_rc
        return out
    parts = re.split(r'^=+ short test summary info =+\s*$', run_out or '', flags=re.M)
    if len(parts) < 2:
        out['error'] = 'pytest run printed no short test summary'
        return out
    lines = parts[-1].splitlines()
    for node in nodes:
        failed = [ln for ln in lines if ln == 'FAILED ' + node or ln.startswith('FAILED ' + node + ' - ')]
        if failed:
            rest = failed[0][len('FAILED ' + node):]
            reason = rest[3:] if rest.startswith(' - ') else ''
            if re.match(r'(AssertionError\b|assert\b)', reason):
                out['red'][node] = ('assertion', 'AssertionError')
            else:
                match = re.match(r'[A-Za-z_][\w.]*', reason)
                out['red'][node] = ('other', match[0].rstrip('.') if match else '')
        elif 'PASSED ' + node in lines:
            out['passed'].append(node)
        else:
            out['other'].append(node)
    if run_rc == 0 and out['red']:
        out.update(red={}, error='pytest exited 0 but the summary lists FAILED nodes')
    return out


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv):
    """`red_green.py check-tests <sdlc_dir> <goal>` (#2116): the end-of-Plan check. Exit 0 and one line when the
    goal's plan `## Tests` parses; exit 2 and the parser's message on stderr when it does not or there is no plan."""
    if len(argv) != 3 or argv[0] != 'check-tests':
        print('usage: red_green.py check-tests <sdlc_dir> <goal>', file=sys.stderr)
        return 2
    sdlc_dir, goal = argv[1], argv[2]
    plan = _sibling('review_context').phase_doc_file(sdlc_dir, goal, 'plans')
    if plan is None:
        print('red_green.py: no plan for goal %s under %s/plans' % (goal, sdlc_dir), file=sys.stderr)
        return 2
    try:
        state = _sibling('state')
        runners = state.test_runners(state.load_config(sdlc_dir))
    except Exception as exc:                        # noqa: BLE001 - an unreadable config is stated, never a pass
        print('red_green.py: cannot read tests.runners (%s: %s)' % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    message = tests_section_error(plan, runners)
    if message:
        print('red_green.py: ' + message, file=sys.stderr)
        return 2
    print('red_green.py: ## Tests in %s is usable by `work.py pr`' % plan)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
