"""EARS / TAGGED / VAGUE sentence patterns (#659; library only; no main).

This is the ONE place these patterns live in the repo. They are copied verbatim from
`prd_lint.py:22-26`; `prd_lint.py` lives outside the repository (`~/.sigma-ops/prd/`), so it is NOT
imported here and this module is the only thing the repo can import. #535 repoints `acceptance.py`
at this module rather than writing a second one. Callers load it by path
(`goal_design._load_loop_script`); it has no top-level side effects.
"""
import re

COMP = r"THE \S.*? SHALL( NOT)? \S"
EARS = re.compile(r"^((WHEN|WHILE|IF|WHERE) [^,]+(, (WHEN|WHILE|IF) [^,]+)*, (THEN )?)?" + COMP)
TAGGED = re.compile(r"^(MEASURED|DOC|PROCESS): \S")
VAGUE = re.compile(r"^(add|support|handle|improve|update|fix|make|ensure|clean up|refactor)\b", re.I)


def is_ears(s):
    """True when `s` OPENS with the EARS clause pattern (TAGGED and VAGUE do not qualify).

    `EARS.match`, as in `prd_lint.py` verbatim -- not `fullmatch`: it checks the leading form
    (`[WHEN|IF|WHILE|WHERE ..., ]THE <x> SHALL [NOT] <y>`), not that `s` is one sentence or that
    nothing odd follows the first `SHALL <word>`."""
    return bool(EARS.match(s))


def classify(s):
    """`ears`, `tagged`, `vague` or `other`."""
    if EARS.match(s):
        return "ears"
    if TAGGED.match(s):
        return "tagged"
    if VAGUE.match(s):
        return "vague"
    return "other"
