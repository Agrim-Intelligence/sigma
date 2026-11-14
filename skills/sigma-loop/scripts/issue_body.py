"""Agent-ready goal bodies: ONE place that renders and checks the sections a goal needs (#2222).

    render(context, criteria, verify_command=None)  -> body text with a `## Done when` section
    check(body, required)                           -> the required sections the body lacks
    criteria_from_served(lines)                     -> checklist statements from `Requirements served` lines

A goal body that leaves acceptance to `acceptance.py record` later has no criteria to record; every writer of
an issue body (handoff, compile_plan, goal_design slices) renders through here so the body parses with
`acceptance.criteria` by construction. No network, no config and no environment is read here.
"""
import importlib.util
import pathlib
import re


def _load(name):
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: Section name -> the heading patterns that count as having it. `Done when` also accepts the alias that
#: `acceptance.criteria` reads.
_SECTIONS = {
    'done when': (r'##\s+Done when\s*', r'##\s+Acceptance criteria\s*'),
    'verification command': (r'##\s+Verification command\s*',),
}


def render(context, criteria, verify_command=None):
    """The body: ``context`` (text), then `## Done when` with ``criteria`` as unchecked checklist items, then an
    optional `## Verification command`. Refuses zero criteria, and refuses any list `acceptance.criteria` would
    refuse (3 to 7 statements of at most 1024 characters), so a rendered body is always recordable."""
    items = [str(c).strip() for c in (criteria or []) if str(c).strip()]
    if not items:
        raise ValueError('a goal body needs at least one acceptance criterion; none were given')
    block = '## Done when\n' + ''.join('- [ ] %s\n' % item for item in items)
    parsed = _load('acceptance').criteria(block)          # raises ValueError on 0-2 or 8+ items, or an empty one
    if parsed is None or len(parsed) != len(items):
        raise ValueError('acceptance criteria could not be rendered as a checklist')
    out = (context.rstrip() + '\n\n' if context and context.strip() else '') + block
    if verify_command:
        if '\n' in verify_command or '\0' in verify_command:
            raise ValueError('verification command must be a single line')
        out += '\n## Verification command\n`%s`\n' % verify_command.strip()
    return out


def check(body, required):
    """Names (as given in ``required``) of the sections ``body`` lacks. `Done when` counts only when
    `acceptance.criteria` reads a valid list from it (an invalid list is missing, not present)."""
    missing = []
    for name in required:
        key = str(name).strip().lower()
        if key == 'done when':
            try:
                present = _load('acceptance').criteria(body or '') is not None
            except ValueError:
                present = False
        else:
            patterns = _SECTIONS.get(key) or (r'##\s+' + re.escape(str(name).strip()) + r'\s*',)
            present = any(re.search(r'(?im)^' + p + r'$', body or '') for p in patterns)
        if not present:
            missing.append(name)
    return missing


def criteria_from_served(lines):
    """Checklist statements from `Requirements served` lines: a leading bullet, checkbox and `R<n>:`-style label
    are dropped, blanks and duplicates removed, order kept. A line that is only a reference (`#12`) yields nothing."""
    out = []
    for raw in lines or []:
        text = re.sub(r'^\s*(?:[-*]\s+)?(?:\[[ xX]\]\s+)?', '', str(raw)).strip()
        text = re.sub(r'^\*\*[A-Za-z]+-?\d+[.:]?\*\*\s*', '', text)          # `**FR-1.**`
        text = re.sub(r'^(?:R-?\d+|REQ-?\d+|\d+[.)])\s*[:.\-)]?\s*', '', text, flags=re.I).strip()
        if len(text) > 1 and not re.fullmatch(r'#?\d+', text) and text not in out:
            out.append(text)
    return out


# ---- goal_readiness (#2222): off | warn | require ------------------------------------------------------------

MODES = ('off', 'warn', 'require')


def readiness_mode(config):
    """`goal_readiness.mode`: off | warn | require, default warn (D5: a default of require would park every existing
    goal on upgrade). An unrecognised value raises ValueError; it is never silently defaulted."""
    block = (config or {}).get('goal_readiness')
    if block is None:
        return 'warn'
    if not isinstance(block, dict):
        raise ValueError('goal_readiness must be an object with a `mode`')
    mode = block.get('mode', 'warn')
    if mode not in MODES:
        raise ValueError('goal_readiness.mode must be one of %s, got %r' % (', '.join(MODES), mode))
    return mode


def readiness_required(config):
    """The sections a goal body must carry: `goal_readiness.required_sections` when set, else `Done when`, plus
    `Verification command` only when the repository has a configured `verify.command` (nothing to name otherwise)."""
    block = (config or {}).get('goal_readiness')
    custom = block.get('required_sections') if isinstance(block, dict) else None
    if custom is not None:
        if not isinstance(custom, list) or not all(isinstance(x, str) and x.strip() for x in custom):
            raise ValueError('goal_readiness.required_sections must be a list of section names')
        return [x.strip() for x in custom]
    required = ['Done when']
    command = ((config or {}).get('verify') or {}).get('command')
    if isinstance(command, str) and command.strip():
        required.append('Verification command')
    return required


def readiness_missing(body, config):
    """Required sections ``body`` lacks under ``config``. Raises ValueError for an invalid `goal_readiness` block."""
    return check(body, readiness_required(config))
