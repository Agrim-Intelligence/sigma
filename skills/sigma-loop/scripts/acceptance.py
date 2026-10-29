"""P1 acceptance record, shared by verify, publication, and retrospective review.

    python3 skills/sigma-loop/scripts/acceptance.py record .sdlc <goal> [--draft file]
    python3 skills/sigma-loop/scripts/acceptance.py set-verify-command .sdlc <goal> (--verify-command CMD | --clear)

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


def criteria(text):
    """Read a real level-two Done when section; fenced examples do not count."""
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
            found = bool(re.fullmatch(r'##\s+Done when\s*', line, re.I))
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
    if not found:
        return None
    if not 3 <= len(lines) <= 7 or any(len(x) > 1024 for x in lines):
        raise ValueError('acceptance requires 3–7 statements of at most 1024 characters each')
    return lines


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
    return {'text': text, 'criteria': items, 'verify_command': command, 'drafted': drafted}


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
    metadata = {'kind': 'acceptance', 'goal': file.stem, 'verify_command': verify_command, 'drafted': drafted}
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('verb', choices=['record', 'set-verify-command'])
    parser.add_argument('sdlc_dir')
    parser.add_argument('goal')
    parser.add_argument('--draft')
    parser.add_argument('--verify-command')
    parser.add_argument('--clear', action='store_true')
    args = parser.parse_args(argv)
    if args.verb == 'record' and args.clear:
        parser.error('--clear is only valid for set-verify-command')
    if args.verb == 'set-verify-command':
        if args.draft is not None:
            parser.error('--draft is only valid for record')
        if args.clear == (args.verify_command is not None):
            parser.error('set-verify-command needs exactly one of --verify-command or --clear')
    try:
        if args.verb == 'record':
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
