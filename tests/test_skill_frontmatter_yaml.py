"""Each string value in `skills/*/SKILL.md` frontmatter must read, in YAML, exactly as it is written.

A host decides when to use a skill from its `description:`, and reads that frontmatter as YAML. In an
unquoted value ` #` starts a comment and `: ` is a scanner error. Measured 2026-09-14 on the installed
1.4.12 plugin: Claude Code listed agrim-slack's description as 63 of its 422 characters, cut at
"(Epic #2335)" before any of its "Use when" triggers, and cut agrim-wizard, agrim-goal-design and
agrim-goal-review at their own ` #`; PyYAML rejected agrim-brainstorm's and agrim-review's frontmatter.

PyYAML is the oracle on purpose: the property is "what a YAML parser reads", and a hand-written scalar
check would be a second implementation of the rule it polices. Like any test that needs a third-party
package, this skips where its package is not importable, because `tests/` has to run with nothing installed but
pytest. CI's `test` job runs it in a last step of its own, after installing PyYAML
(this repository's CI-workflow test pins that).
"""
import json
import os
import pathlib

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_skill_frontmatter_value_reads_exactly_as_written():
    skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    assert skills, "found no skills/*/SKILL.md to check"
    problems = []
    for path in skills:
        name = path.parent.name
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines or lines[0] != "---" or "---" not in lines[1:]:
            problems.append(f"{name}: no `---` frontmatter fence")
            continue
        front = lines[1:lines.index("---", 1)]
        try:
            parsed = yaml.safe_load("\n".join(front))
        except yaml.YAMLError as e:
            problems.append(f"{name}: frontmatter is not valid YAML: {e}")
            continue
        if not isinstance(parsed, dict) or not isinstance(parsed.get("description"), str):
            problems.append(f"{name}: YAML finds no description string")
            continue
        for line in front:
            key, sep, raw = line.partition(":")
            key = key.strip().strip("\"'")
            value = parsed.get(key)
            if not sep or line[:1].isspace() or not isinstance(value, str):
                continue
            raw = raw.strip()
            # ponytail: the only quoted form accepted is json.dumps's; YAML's others (single
            # quotes, \u escapes, block scalars) are refused, not re-implemented. Add one if needed.
            quoted = json.dumps(value, ensure_ascii=False)
            if raw not in (value, quoted):
                is_quoted = raw.startswith('"')
                expected = quoted if is_quoted else value
                label = "json.dumps of YAML's value has" if is_quoted else "YAML has"
                at = len(os.path.commonprefix([raw, expected]))
                problems.append(f"{name}: `{key}:` holds {len(raw)} chars but YAML reads "
                                f"{len(value)}; from char {at} the line has {raw[at:at + 30]!r}, "
                                f"{label} {expected[at:at + 30]!r}")
    assert not problems, (
        "SKILL.md frontmatter that YAML does not read exactly as written. Keep each value on its own "
        "line: plain when it contains neither ' #' nor ': ', otherwise double-quoted exactly as "
        "json.dumps(value, ensure_ascii=False) writes it.\n" + "\n".join(problems))
