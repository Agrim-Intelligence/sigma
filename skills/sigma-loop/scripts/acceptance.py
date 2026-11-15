"""P1 acceptance record, shared by verify, publication, and retrospective review.

    python3 skills/sigma-loop/scripts/acceptance.py record .sdlc <goal> [--draft file]
    python3 skills/sigma-loop/scripts/acceptance.py set-verify-command .sdlc <goal> (--verify-command CMD | --clear)
    python3 skills/sigma-loop/scripts/acceptance.py check .sdlc <goal>
    python3 skills/sigma-loop/scripts/acceptance.py extend .sdlc <goal> --slice <ref> --reason <why> --criterion <text> [--criterion <text> ...]

No background work. Capture is explicit; only drafted GitHub criteria cause a comment.
`record` is create-only. Existing records win over later issue edits. The one exception is
`set-verify-command` (#2085): it rewrites the ONE `verify_command` metadata line of an existing record, only while
no verify evidence exists, because that evidence binds the record's exact bytes. Protocol bounds
keep review context short; these are document limits, not host resource quotas.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shlex
import sys
import tempfile

MAX_BYTES = 32768


def _load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def path(sdlc_dir, goal):
    raw = str(goal)
    stem = pathlib.Path(raw).stem if raw.endswith('.md') else raw
    reason = _load('state').unsafe_goal_reason(stem)
    if reason:
        raise ValueError('unsafe acceptance goal: ' + reason)
    return pathlib.Path(sdlc_dir) / 'acceptance' / (stem + '.md')


def _text(file):
    with pathlib.Path(file).open('rb') as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('acceptance record exceeds 32 KiB')
    return data.decode('utf-8')


MAX_CRITERIA = 7                    # the ORIGINAL block's upper bound (3 to 7); breaker.py reads this
MAX_ADDED = 10                      # D2: a goal that needs more than 10 added criteria is a new goal
_CANONICAL = r'##\s+Done when\s*'
_ALIAS = r'##\s+Acceptance criteria\s*'
_ADDED_HEADING = r'##\s+Added criteria\s*'
_ADDED_SUFFIX = re.compile(r'(.*?)\s+\(added:\s*([^;()]+?);\s*reason:\s*(.+)\)\s*', re.S)


def _section(text, heading):
    """The checklist statements of the first real level-two section matching ``heading``, or None when absent.
    Fenced examples do not count. Count and length bounds are the caller's."""
    found, fenced, lines, separated, footer = False, None, [], False, False
    for line in text.splitlines():
        fence = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            char = fence.group(1)[0]
            fenced = None if fenced == char else char if fenced is None else fenced
            continue
        if fenced:
            continue
        if re.match(r'^##\s+', line):
            if found:
                break
            found = bool(re.fullmatch(heading, line, re.I))
            continue
        if found and lines and not line.strip():
            separated = True
        if found and line.strip():
            item = re.fullmatch(r'\s*[-*]\s+(?:\[[ xX]\]\s+)?(.+?)\s*', line)
            if not item:
                if lines and separated:
                    footer = True  # allow issue metadata, but inspect the rest for lost criteria
                    continue
                raise ValueError('Done when must contain single-line checklist statements')
            if footer:
                raise ValueError('checklist item after footer paragraph; keep all acceptance criteria together')
            if re.fullmatch(r'\[[ xX]\]', item.group(1)):
                raise ValueError('acceptance statement is empty')
            lines.append(item.group(1))
            separated = False
    return lines if found else None


def criteria(text):
    """Read a real level-two Done when section; fenced examples do not count.

    TWO-PASS (#2222 D10): the canonical `## Done when` wins wherever it sits; `## Acceptance criteria` is an alias
    read only when the canonical heading is absent, so an alias appearing first never shadows it."""
    lines = _section(text, _CANONICAL)
    if lines is None:
        lines = _section(text, _ALIAS)
    if lines is None:
        return None
    if not 3 <= len(lines) <= MAX_CRITERIA or any(len(x) > 1024 for x in lines):
        raise ValueError('acceptance requires 3–7 statements of at most 1024 characters each')
    return lines


def criteria_hash(items):
    """sha256 of the normalised ORIGINAL criteria block: heading plus statements, checkbox state and surrounding
    whitespace ignored, so ticking a box never reads as a rewrite."""
    block = '## Done when\n' + ''.join('- ' + item.strip() + '\n' for item in items)
    return hashlib.sha256(block.encode('utf-8')).hexdigest()


def added(text):
    """The `## Added criteria` lines as ``[{text, slice, reason}]``. A line without the
    `(added: <slice>; reason: <why>)` suffix yields ``slice`` and ``reason`` of None so review can see it."""
    out = []
    for raw in _section(text, _ADDED_HEADING) or []:
        match = _ADDED_SUFFIX.fullmatch(raw)
        if match and match.group(1).strip() and match.group(3).strip():
            out.append({'text': match.group(1).strip(), 'slice': match.group(2).strip(),
                        'reason': match.group(3).strip()})
        else:
            out.append({'text': raw, 'slice': None, 'reason': None})
    return out


def read(sdlc_dir, goal):
    file = path(sdlc_dir, goal)
    return parse(_text(file), file.stem)


def parse(text, goal_stem):
    """Validate local or committed bytes through the same bounded record contract."""
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('acceptance record exceeds 32 KiB')
    match = re.fullmatch(r'---\n(.*?)\n---\n(.*)', text, re.S)
    if not match:
        raise ValueError('acceptance metadata is missing')
    try:
        metadata = dict((key, json.loads(value)) for key, value in
                        (line.split(': ', 1) for line in match.group(1).splitlines()))
    except (ValueError, TypeError) as exc:
        raise ValueError('acceptance metadata is malformed') from exc
    if metadata.get('kind') != 'acceptance' or metadata.get('goal') != goal_stem:
        raise ValueError('acceptance kind/goal does not match this goal')
    command = metadata.get('verify_command')
    if command is not None and (not isinstance(command, str) or not command.strip() or '\0' in command):
        raise ValueError('acceptance verify_command must be a nonempty string or null')
    drafted = metadata.get('drafted')
    if drafted is not None and not isinstance(drafted, bool):
        raise ValueError('acceptance drafted must be true or false')
    items = criteria(match.group(2))
    if items is None:
        raise ValueError('acceptance Done when is missing')
    extra = added(match.group(2))
    if len(extra) > MAX_ADDED:
        raise ValueError('acceptance has more than %d added criteria (at most %d); a goal that needs more is a new goal'
                         % (MAX_ADDED, MAX_ADDED))
    if any(len(x['text']) + len(x['slice'] or '') + len(x['reason'] or '') > 1024 for x in extra):
        raise ValueError('an added criterion exceeds 1024 characters')
    baseline = metadata.get('criteria_sha256')
    if baseline is not None and not (isinstance(baseline, str) and re.fullmatch(r'[0-9a-f]{64}', baseline)):
        raise ValueError('acceptance criteria_sha256 must be a sha256 hex string or absent')
    return {'text': text, 'criteria': items, 'verify_command': command, 'drafted': drafted,
            'added': extra, 'criteria_sha256': baseline}


def digest(sdlc_dir, goal):
    try:
        return hashlib.sha256(read(sdlc_dir, goal)['text'].encode('utf-8')).hexdigest()
    except FileNotFoundError:
        return None


def refusal(sdlc_dir, goal):
    try:
        read(sdlc_dir, goal)
    except (OSError, ValueError) as exc:
        return ('acceptance record missing or invalid; run `acceptance.py record .sdlc <goal>` '
                'during P1 GOAL before code: %s (nothing pushed)' % exc)
    return None


def extend_marker(text, slice_ref, reason):
    """The issue-comment marker `extend` posts and `pr` looks for (D12): the sha256 of `text|slice|reason`."""
    digest_ = hashlib.sha256('|'.join((text, slice_ref, reason)).encode('utf-8')).hexdigest()
    return '<!-- sigma:acceptance-extend %s -->' % digest_


def _record_text(sdlc_dir, goal, text=None):
    """``(text_or_None, reason)``: ``text`` when given (the bytes about to be staged), else the root record's."""
    if text is not None:
        return text, 'given'
    try:
        return _text(path(sdlc_dir, goal)), 'root record'
    except FileNotFoundError:
        return None, 'record absent'
    except (OSError, ValueError) as exc:
        return None, 'record unreadable: %s' % exc


def baseline_refs(sdlc_dir, goal):
    """The git refs whose history can hold the goal's first-added record, remote before local, then the base the
    file may already have landed on. Read from the goal's own work record (`state/work/<goal>.json`), never from
    config. ``[]`` when there is no work record: the baseline is then unavailable and `prefix_check` says so."""
    try:
        stem = path(sdlc_dir, goal).stem
        work = json.loads((pathlib.Path(sdlc_dir) / 'state' / 'work' / (stem + '.json')).read_text(encoding='utf-8'))
        remote, branch, base = work.get('remote'), work.get('branch'), work.get('base')
        refs = []
        for name in ((remote + '/' + branch) if remote and branch else None, branch,
                     (remote + '/' + base) if remote and base else None):
            if isinstance(name, str) and name and name not in refs:
                refs.append(name)
        return refs
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def reasonless_added(sdlc_dir, goal, text=None):
    """Texts of the added criteria that carry no `(added: <slice>; reason: <why>)` suffix. ``[]`` when the record
    is absent or unparseable (other clauses own that). Reads no config and no environment."""
    current, _ = _record_text(sdlc_dir, goal, text)
    if current is None:
        return []
    try:
        return [x['text'] for x in parse(current, path(sdlc_dir, goal).stem)['added'] if x['reason'] is None]
    except ValueError:
        return []


def prefix_check(sdlc_dir, goal, refs=None, text=None, root=None):
    """``(refusal_or_None, notes)``: the append-only guard (#2222 D1, D6, D9). No override argument, no flag, no
    config key and no environment variable is read anywhere in this path.

    Clauses, each independently refusing:
      H  the record's stored `criteria_sha256` equals the hash of its current original block;
      B  the first-added committed blob's criteria are an exact ORDERED prefix of the current combined list
         (original then added), when both carry a hash the hashes are equal, a hash the baseline carried is
         still present, and `drafted` equals the baseline's;
      P  the record parses: original block 3 to 7 items, added items only in `## Added criteria`;
      R  no added criterion lacks its slice and reason;
      D  when a first-added baseline exists, a missing or unparseable current record refuses.
    A baseline that cannot be read skips B (and D) and says so in ``notes``: the guard refuses on git being
    unreadable never, only on a clear mismatch from H, P or R."""
    notes = []
    stem = path(sdlc_dir, goal).stem
    review = _load('review_context')
    base_root = pathlib.Path(root) if root else pathlib.Path(sdlc_dir).resolve().parent
    rel = pathlib.PurePosixPath(pathlib.Path(sdlc_dir).resolve().name) / 'acceptance' / (stem + '.md')
    refs = baseline_refs(sdlc_dir, goal) if refs is None else refs
    blob, why = review.first_added_blob(base_root, refs, rel.as_posix()) if refs else (None, 'no goal branch recorded')
    baseline = None
    if blob is not None:
        try:
            baseline = parse(blob.decode('utf-8'), stem)
            notes.append('baseline: %s' % why)
        except (ValueError, UnicodeDecodeError) as exc:
            notes.append('baseline: unavailable (first committed record unparseable: %s)' % exc)
    else:
        notes.append('baseline: unavailable (%s)' % why)
    current_text, how = _record_text(sdlc_dir, goal, text)
    current = None
    if current_text is not None:
        try:
            current = parse(current_text, stem)
        except ValueError as exc:
            how = 'unparseable: %s' % exc
    if current is None:
        if baseline is not None:                                                                  # clause D
            return ('the acceptance record is missing or unparseable (%s) but a committed baseline exists; '
                    'criteria are append-only and cannot be deleted or re-recorded' % how, notes)
        return None, notes
    if current['criteria_sha256'] is not None and current['criteria_sha256'] != criteria_hash(current['criteria']):
        return ('the Done when block no longer matches its recorded criteria_sha256; criteria are append-only: '
                'add with `acceptance.py extend`, never edit', notes)                                # clause H
    if baseline is not None:                                                                         # clause B
        old = baseline['criteria'] + [x['text'] for x in baseline['added']]
        new = current['criteria'] + [x['text'] for x in current['added']]
        if new[:len(old)] != old:
            return ('the committed criteria are not an ordered prefix of this record; criteria are append-only '
                    '(no edit, removal, weakening or reorder)', notes)
        if baseline['criteria_sha256'] and current['criteria_sha256'] and \
                baseline['criteria_sha256'] != current['criteria_sha256']:
            return 'criteria_sha256 differs from the first committed record; it is fixed at first record', notes
        if baseline['criteria_sha256'] and not current['criteria_sha256']:
            return ('criteria_sha256 was removed after the first committed record; it is fixed at first record',
                    notes)
        if baseline['drafted'] != current['drafted']:
            return 'drafted differs from the first committed record; it is fixed at first record', notes
    lacking = [x['text'] for x in current['added'] if x['reason'] is None]                           # clause R
    if lacking:
        return ('added criterion without slice and reason: %s; add with `acceptance.py extend --slice <ref> '
                '--reason <why>`' % '; '.join(lacking), notes)
    return None, notes


def _norm_statement(item):
    """Comparison form of one criterion: whitespace collapsed, trailing `(added: ...; reason: ...)` removed.
    Checkbox state is already gone (`_section` drops it)."""
    match = _ADDED_SUFFIX.fullmatch(item)
    return ' '.join((match.group(1) if match else item).split())


def issue_divergence(text, goal_stem, issue_body):
    """``(state, detail)`` for the issue-vs-record check (D4): ``state`` is `skip`, `ok` or `diverge`.

    Runs whenever the issue body has a valid criteria section (canonical or alias), whatever the record's
    `drafted` flag or `criteria_sha256` presence say: both live in the record's own bytes, so neither may switch the
    witness off (#2222 review B2; a drafted record legitimately arises only when the issue has no valid section).
    Otherwise `skip` with the reason. Compares text only, alias-normalised, checkbox-blind. It reads no config and
    no environment."""
    try:
        record_ = parse(text, goal_stem)
    except ValueError as exc:
        return 'skip', 'record unparseable: %s' % exc
    try:
        issue = criteria(issue_body or '')
    except ValueError:
        issue = None
    if issue is None:
        return 'skip', 'no criteria section in issue'
    if [_norm_statement(x) for x in issue] != [_norm_statement(x) for x in record_['criteria']]:
        return 'diverge', ('the issue\'s Done when no longer matches the recorded original criteria; the record '
                           'wins and is never rewritten: a person decides whether the goal changes')
    return 'ok', ''


def extend_comment_refusal(sdlc_dir, goal, source, text=None):
    """Refusal string or None (D12): every ADDED criterion needs an issue comment carrying its extend marker
    for that exact text, slice and reason. Unreadable comment history refuses (fail closed; the next tick retries).
    The caller exempts local-file goals. Reads no config and no environment. An actor who can post issue comments
    can fabricate the comment: this is an audit trail, not proof of authorship."""
    current, _ = _record_text(sdlc_dir, goal, text)
    if current is None:
        return None
    try:
        rows = [x for x in parse(current, path(sdlc_dir, goal).stem)['added'] if x['reason'] is not None]
    except ValueError:
        return None
    if not rows:
        return None
    try:
        comments = source.fetch_comments_strict(goal)['comments']
        if not isinstance(comments, list):
            raise ValueError('comment history is not a list')
        bodies = [c.get('body') or '' for c in comments if isinstance(c, dict)]
    except Exception as exc:  # noqa: BLE001 - any doubt refuses; a retry costs nothing
        return 'could not read the issue comments to confirm the added criteria (%s)' % exc
    missing = [x['text'] for x in rows
               if not any(extend_marker(x['text'], x['slice'], x['reason']) in body for body in bodies)]
    if missing:
        return ('added criterion without its extend comment on the issue: %s; add criteria only with '
                '`acceptance.py extend`, which posts it' % '; '.join(missing))
    return None


def prefix_refusal(sdlc_dir, goal, refs=None, text=None, root=None):
    """The refusal string of `prefix_check`, or None."""
    return prefix_check(sdlc_dir, goal, refs=refs, text=text, root=root)[0]


def _guard(sdlc_dir, goal):
    """Raise ValueError when the append-only guard refuses; used by record (existing file) and set-verify-command."""
    why = prefix_refusal(sdlc_dir, goal)
    if why:
        raise ValueError(why)


_FLAG_REMEDY = "drop `--verify-command` and record again"
_FILE_REMEDY = "edit the goal file %s: delete its `verify_command:` line, then record again"
_REPLACE_REMEDY = ("pass the repository's own verify.command instead, or run `acceptance.py set-verify-command` "
                   "with `--clear`")


def trust_conflict(sdlc_dir, config, verify_command, remedy=None):
    """Record time (#2085): the refusal text when the environment approval covers the repository's `verify.command`
    alone and ``verify_command`` is outside it, else None. REFUSAL-ONLY and fail-open: it never grants, and any
    doubt returns None, because `loop.py verify` stays the authority (and names the same reason there).
    Nothing spawns unless the variable is set to a well-formed value and the goal adds a command."""
    try:
        if verify_command is None:
            return None
        state = _load('state')
        return _load('shell_policy').goal_command_conflict(
            pathlib.Path(sdlc_dir).resolve().parent,
            state.combined_verify_commands(config, verify_command),
            state.repository_verify_commands(config), remedy=remedy)
    except Exception:  # noqa: BLE001 - any doubt accepts; verify time decides
        return None


def record(sdlc_dir, goal, config=None, source=None, draft=None, verify_command=None):
    file = path(sdlc_dir, goal)
    if file.exists():
        existing = read(sdlc_dir, goal)  # never overwrite intent, even on a retry after the issue changes
        _guard(sdlc_dir, goal)
        if verify_command is not None and existing['verify_command'] != verify_command:
            print('acceptance.py: existing record kept, its verify_command is unchanged; change it with '
                  '`acceptance.py set-verify-command` (only before verify evidence exists)', file=sys.stderr)
        return file
    config = config if config is not None else _load('state').load_config(sdlc_dir)
    source = source or _load('sources').get_source(sdlc_dir, config)
    issue = source.fetch_title_body(goal)
    body = issue.get('body', '')
    if not body.strip():
        raise ValueError('goal body unavailable; restore source access before recording acceptance')
    try:
        items = criteria(body)
    except ValueError as exc:
        if draft is None:           # #1811: an invalid body list is the thing to fix, or a draft replaces it
            raise ValueError('%s; edit the issue body, or pass --draft with 3–7 criteria' % exc)
        items = None
    drafted = items is None
    if drafted:
        if draft is None:
            raise ValueError('no Done when section; draft 3–7 criteria in a file and pass --draft')
        items = criteria(_text(draft))
        if items is None:
            raise ValueError('draft requires a Done when section')
    from_flag = verify_command is not None
    if verify_command is None and str(goal).endswith('.md'):
        verify_command = _load('frontmatter').get(_text(goal), 'verify_command')
        if verify_command and not verify_command.strip().strip('\'"').strip():
            verify_command = None
    # #2085, AC-1: nothing is built, posted or written for a command the environment approval cannot cover. The
    # remedy depends on where the command came from. For a goal file's own `verify_command:` line the file edit is
    # the fix (the flag was never passed), which is why AC-3's "clear first" holds for the record-held case only.
    conflict = trust_conflict(sdlc_dir, config, verify_command,
                              _FLAG_REMEDY if from_flag else _FILE_REMEDY % goal)
    if conflict:
        raise ValueError(conflict)
    metadata = {'kind': 'acceptance', 'goal': file.stem, 'verify_command': verify_command, 'drafted': drafted,
                'criteria_sha256': criteria_hash(items)}
    text = ('---\n' + '\n'.join(k + ': ' + json.dumps(v, ensure_ascii=False) for k, v in metadata.items())
            + '\n---\n## Done when\n' + ''.join('- [ ] ' + item + '\n' for item in items))
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('acceptance record exceeds 32 KiB')
    if verify_command is not None and (not isinstance(verify_command, str) or not verify_command.strip() or '\0' in verify_command):
        raise ValueError('acceptance verify_command must be a nonempty string or null')
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    if drafted and not str(goal).endswith('.md'):
        comment = '<!-- sigma:acceptance ' + sha + ' -->\n' + text
        comments = source.fetch_comments_strict(goal)['comments']
        if not isinstance(comments, list):
            raise ValueError('could not read acceptance comment history')
        if not any(c.get('body') == comment for c in comments if isinstance(c, dict)):
            source.note(goal, comment)  # REST re-POST on 5xx/transport only; an ambiguous ack may duplicate
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.acceptance-', dir=str(file.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, file)  # atomic create-only, refuse unsupported filesystems
        except FileExistsError:
            if read(sdlc_dir, goal)['text'] != text:
                raise ValueError('concurrent acceptance record differs; retain original intent')
    finally:
        os.unlink(temporary)
    _load('ledger').safe_append(sdlc_dir, 'acceptance', goal, config=config,
                               ref='acceptance/' + file.name, acceptance_sha256=sha)
    return file


def _with_verify_command(text, command):
    """``text`` with its one `verify_command` header line replaced (appended to the header if absent), cutting the
    header where `parse()` does: the first `\\n---\\n`. Everything else is carried over byte for byte. The value is
    serialised as `record` does, `json.dumps(..., ensure_ascii=False)`."""
    match = re.fullmatch(r'---\n(.*?)\n---\n(.*)', text, re.S)
    if not match:
        raise ValueError('acceptance metadata is missing')
    lines = match.group(1).split('\n')
    line = 'verify_command: ' + json.dumps(command, ensure_ascii=False)
    for index in range(len(lines) - 1, -1, -1):             # parse() keeps the LAST of a repeated key
        if lines[index].startswith('verify_command: '):
            lines[index] = line
            break
    else:
        lines.append(line)
    return '---\n' + '\n'.join(lines) + '\n---\n' + match.group(2)


def _sync_worktree_copy(sdlc_dir, stem, file, old_text, new_text, state):
    """Keep the goal worktree's copy of the record (`work.py commit` copies it there at P1, create-only) identical
    to the root's, or the stale copy lands on the base and the root's untracked one then refuses `git pull`. Only a
    copy that is byte-identical to the OLD root record is replaced; a differing or symlinked one is left alone and
    named, with the one command that fixes it. Never raises: the root write already happened.

    ``new_text == old_text`` is the rerun that changes nothing (the root already holds the new bytes, perhaps after a
    crash between the root write and this call). It runs the same comparison against the root record but NEVER
    writes: it cannot prove that a copy which differs from the root holds the old bytes, so it names the `cp` line
    for whoever owns the worktree, and says nothing when the copy is absent or already identical."""
    cp = None
    try:
        try:
            work = json.loads((pathlib.Path(sdlc_dir) / 'state' / 'work' / (stem + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        worktree = work.get('worktree') if isinstance(work, dict) else None
        if not isinstance(worktree, str) or not worktree:
            return
        target = pathlib.Path(worktree, file.resolve().relative_to(pathlib.Path(sdlc_dir).resolve().parent))
        try:
            os.lstat(target)
        except FileNotFoundError:
            return                                           # no copy yet: `work.py commit` will copy this one
        root_record = file.resolve()
        cp = shlex.join(['cp', str(root_record), str(target)])
        if os.path.islink(target) or os.path.islink(target.parent):
            print('acceptance.py: the goal worktree copy %s is a symlink and was left alone; the operator or the '
                  'loop slot owning that worktree replaces it with `%s`' % (target, cp), file=sys.stderr)
        elif target.read_bytes() == old_text.encode('utf-8'):
            if new_text != old_text:                             # a no-op rerun finds the copy already identical
                state.atomic_write_text(target, new_text)
        else:
            print('acceptance.py: the goal worktree copy %s differs from the root record and was left alone; the '
                  'operator or the loop slot owning that worktree decides, then runs `%s`' % (target, cp),
                  file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - best effort after the root write
        # `cp` is bound once both paths are known; a failure before that (no work record, an unresolvable path) has none
        print('acceptance.py: the goal worktree copy was not synced: %s%s' % (
            exc, '; the operator or the loop slot owning that worktree replaces it with `%s`' % cp if cp else ''),
            file=sys.stderr)


def _tracked_root_notice(sdlc_dir, file):
    """A root record that git TRACKS is now modified, and a dirty tracked file makes the next `work.py start` in the
    root checkout refuse. Rewriting is still right (refusing would wedge the goal), so say what clears it and who
    runs it. Never raises."""
    try:
        import subprocess
        root = pathlib.Path(sdlc_dir).resolve().parent
        rel = file.resolve().relative_to(root).as_posix()
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        done = subprocess.run(['git', '-C', str(root), 'ls-files', '--error-unmatch', '--', rel],
                              capture_output=True, text=True, timeout=10, check=False, env=env,
                              stdin=subprocess.DEVNULL)
        if done.returncode == 0:
            print('acceptance.py: %s is tracked in the root checkout and is now modified, which makes the next '
                  '`work.py start` there refuse. Once the goal\'s PR carries this change, the operator or the loop '
                  'slot that owns the root checkout runs `git -C %s checkout -- %s` (or commits the file on the '
                  'goal branch)' % (rel, shlex.quote(str(root)), shlex.quote(rel)), file=sys.stderr)
    except Exception:  # noqa: BLE001 - advice only
        pass


def _log_edit(sdlc_dir, stem, old, new, state):
    """One `note` row, hashes only (a 12-hex sha256 prefix, `none` for null; never the command), on a CHANGE only.
    `note` is not an INTERNAL kind, so it cannot move a goal's marker, closure or retention. The path is resolved
    first: `actionlog.append` given the relative `.sdlc` the docs print creates an empty `.sdlc/.sdlc/state/log`
    tree (a pre-existing defect, not fixed here). Fail-open: a log problem never changes the verb's exit code."""
    try:
        sdlc = pathlib.Path(sdlc_dir).resolve()
        if (state.load_config(sdlc).get('action_log') or {}).get('enabled') is not True:
            return
        def short(command):
            return 'none' if command is None else hashlib.sha256(command.encode('utf-8')).hexdigest()[:12]
        _load('actionlog').safe_append(
            sdlc, stem, 'note', actor='loop',
            text='verify_command %s old=%s new=%s' % ('cleared' if new is None else 'replaced', short(old), short(new)))
    except Exception:  # noqa: BLE001 - a trace is never worth a failed edit
        pass


def set_verify_command(sdlc_dir, goal, verify_command=None, clear=False):
    """Clear (``clear=True``) or replace the recorded goal's `verify_command` while no verify evidence exists (#2085,
    AC-2). Returns `cleared`, `replaced` or `unchanged`; raises ValueError/OSError to refuse, with nothing written.

    Only the one metadata line moves. Refused once `.sdlc/state/verify/<goal>.json` exists, for clear, replace and a
    no-op alike: the evidence binds the record's exact bytes. A no-op also compares the goal worktree's copy with the
    root record and names the `cp` line when they differ, without ever writing it. A replace asks the same
    environment-approval question `record` asks. A local goal whose frontmatter still declares a command refuses
    `--clear`, because the clear would not clear it."""
    state = _load('state')
    if clear == (verify_command is not None):
        raise ValueError('set-verify-command needs exactly one of --verify-command or --clear')
    file = path(sdlc_dir, goal)
    stem = file.stem
    state.refuse_symlinks(sdlc_dir, 'acceptance/%s.md' % stem)        # a path relative to the base: #708
    try:
        old_text = _text(file)
    except FileNotFoundError:
        raise ValueError('no acceptance record for goal %s; run `acceptance.py record %s %s` first' % (stem, sdlc_dir, goal))
    old = parse(old_text, stem)['verify_command']
    _guard(sdlc_dir, goal)
    evidence = state.evidence_path(sdlc_dir, goal)
    if evidence.exists():
        raise ValueError('verify evidence exists for goal %s (%s): it binds this record\'s exact bytes, so the record is '
                         'not rewritten after a verify. To start over, the operator removes that file, reruns this '
                         'command, then runs `loop.py verify`' % (stem, evidence))
    if clear:
        if state.goal_file_verify_command(goal):
            raise ValueError('the goal file\'s frontmatter still declares a verify_command, so clearing the record '
                             'would not clear it; delete that `verify_command:` line in %s first' % goal)
        new = None
    else:
        if not isinstance(verify_command, str) or not verify_command.strip() or '\0' in verify_command:
            raise ValueError('acceptance verify_command must be a nonempty string or null')
        new = verify_command
    if new == old:
        # Nothing to write, but a crash between the root write and the worktree sync leaves the copy stale while the
        # root already holds the new bytes, and this rerun is how the operator finds out. Compare only, never write.
        _sync_worktree_copy(sdlc_dir, stem, file, old_text, old_text, state)
        return 'unchanged'
    if new is not None:
        try:
            config = state.load_config(sdlc_dir)
        except Exception:  # noqa: BLE001 - no config means no repository command to conflict with
            config = {}
        conflict = trust_conflict(sdlc_dir, config, new, _REPLACE_REMEDY)
        if conflict:
            raise ValueError(conflict)
    new_text = _with_verify_command(old_text, new)
    parse(new_text, stem)                                    # the bound and the contract, before any byte is written
    state.atomic_write_text(file, new_text)
    _sync_worktree_copy(sdlc_dir, stem, file, old_text, new_text, state)
    _tracked_root_notice(sdlc_dir, file)
    _log_edit(sdlc_dir, stem, old, new, state)
    return 'cleared' if new is None else 'replaced'


def _extend_line(text, slice_ref, reason):
    return '- [ ] %s (added: %s; reason: %s)' % (text, slice_ref, reason)


def _clean_field(value, what, forbidden=''):
    value = (value or '').strip()
    if not value:
        raise ValueError('extend needs a non-blank %s' % what)
    if any(c in value for c in '\n\r\0' + forbidden):
        raise ValueError('extend %s must be one line%s' % (what, (' without ' + ' '.join(forbidden)) if forbidden else ''))
    return value


def extend(sdlc_dir, goal, slice_ref, reason, criteria_list, config=None, source=None):
    """The ONE verb that adds criteria (#2222 D2, D3, D12). Appends ``- [ ] <text> (added: <slice>; reason: <why>)``
    lines under `## Added criteria`; the original `## Done when` block stays byte-identical, and nothing here can
    edit, replace or remove a line. Refuses (nothing written, nothing posted) when the append-only guard already
    refuses, when slice or reason is blank, when the eleventh added criterion would be written, or when a line
    would exceed 1024 characters.

    ORDER: comment, then write. A GitHub goal gets one issue comment per criterion carrying `extend_marker`, so the
    addition is in the open and `work.py pr` can find it; a retry skips a criterion whose marker is already there
    (idempotent), and if a post fails the file is NOT written. A local-file goal has no issue and posts nothing.
    The write is temp-then-replace, so a crash leaves the whole old record or the whole new one, and the worktree
    copy is synced through `_sync_worktree_copy`. The record's bytes change, so verify evidence bound to the old
    bytes is stale by design: run `loop.py verify` again."""
    state = _load('state')
    slice_ref = _clean_field(slice_ref, '--slice', ';()')
    reason = _clean_field(reason, '--reason')
    texts = [_clean_field(c, '--criterion') for c in (criteria_list or [])]
    if not texts:
        raise ValueError('extend needs at least one --criterion')
    file = path(sdlc_dir, goal)
    stem = file.stem
    state.refuse_symlinks(sdlc_dir, 'acceptance/%s.md' % stem)
    try:
        old_text = _text(file)
    except FileNotFoundError:
        raise ValueError('no acceptance record for goal %s; run `acceptance.py record %s %s` first' % (stem, sdlc_dir, goal))
    old = parse(old_text, stem)
    _guard(sdlc_dir, goal)                                       # a tampered original is never extended over
    if len(old['added']) + len(texts) > MAX_ADDED:
        raise ValueError('a goal that needs more than %d added criteria is a new goal (this record has %d, '
                         'adding %d)' % (MAX_ADDED, len(old['added']), len(texts)))
    lines = [_extend_line(t, slice_ref, reason) for t in texts]
    if any(len(line) - len('- [ ] ') > 1024 for line in lines):
        raise ValueError('an added criterion exceeds 1024 characters including its suffix')
    body = old_text.rstrip('\n') + '\n'
    if old['added'] == [] and re.search(r'(?im)^##\s+Added criteria\s*$', old_text) is None:
        body += '\n## Added criteria\n'
    new_text = body + '\n'.join(lines) + '\n'
    again = parse(new_text, stem)                                # bounds and contract, before any byte is written
    if again['criteria'] != old['criteria'] or again['criteria_sha256'] != old['criteria_sha256'] \
            or again['added'][:len(old['added'])] != old['added'] \
            or [x['text'] for x in again['added'][len(old['added']):]] != texts:
        raise ValueError('the record layout does not allow a safe append (is `## Added criteria` the last section?)')
    config = config if config is not None else _safe_config(state, sdlc_dir)
    if source is None and not str(goal).endswith('.md') and \
            ((config.get('discovery') or {}).get('source')) == 'github':
        source = _load('sources').get_source(sdlc_dir, config)
    if source is not None and not str(goal).endswith('.md'):
        comments = source.fetch_comments_strict(goal)['comments']
        if not isinstance(comments, list):
            raise ValueError('could not read the issue comment history; nothing was written')
        bodies = [c.get('body') or '' for c in comments if isinstance(c, dict)]
        for text in texts:
            marker = extend_marker(text, slice_ref, reason)
            if not any(marker in body_ for body_ in bodies):
                posted = source.note(goal, '%s\nAcceptance criterion added (slice %s): %s\nReason: %s'
                                     % (marker, slice_ref, text, reason))
                if posted is False:              # a declined write (hard stops) is not a post: write nothing
                    raise ValueError('the issue declined the extend comment for %r; nothing was written' % text)
    state.atomic_write_text(file, new_text)
    _sync_worktree_copy(sdlc_dir, stem, file, old_text, new_text, state)
    _tracked_root_notice(sdlc_dir, file)
    return file


def _safe_config(state, sdlc_dir):
    try:
        return state.load_config(sdlc_dir)
    except Exception:  # noqa: BLE001 - no config means no GitHub source to comment on
        return {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('verb', choices=['record', 'set-verify-command', 'check', 'extend'])
    parser.add_argument('sdlc_dir')
    parser.add_argument('goal')
    parser.add_argument('--draft')
    parser.add_argument('--verify-command')
    parser.add_argument('--clear', action='store_true')
    parser.add_argument('--slice')
    parser.add_argument('--reason')
    parser.add_argument('--criterion', action='append')
    args = parser.parse_args(argv)
    if args.verb == 'record' and args.clear:
        parser.error('--clear is only valid for set-verify-command')
    if args.verb == 'set-verify-command':
        if args.draft is not None:
            parser.error('--draft is only valid for record')
        if args.clear == (args.verify_command is not None):
            parser.error('set-verify-command needs exactly one of --verify-command or --clear')
    try:
        if args.verb == 'extend':
            print('acceptance.py: extended %s' % extend(args.sdlc_dir, args.goal, args.slice, args.reason, args.criterion))
        elif args.verb == 'check':
            refusal_text, notes = prefix_check(args.sdlc_dir, args.goal)
            for note in notes:
                print('acceptance.py: ' + note)
            if refusal_text:
                raise ValueError(refusal_text)
            print('acceptance.py: criteria intact for %s' % path(args.sdlc_dir, args.goal).stem)
        elif args.verb == 'record':
            print(record(args.sdlc_dir, args.goal, draft=args.draft, verify_command=args.verify_command))
        else:
            result = set_verify_command(args.sdlc_dir, args.goal, args.verify_command, args.clear)
            print('acceptance.py: verify_command %s for %s' % (result, path(args.sdlc_dir, args.goal).stem))
    except (OSError, ValueError, RuntimeError) as exc:
        print('acceptance.py: REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
