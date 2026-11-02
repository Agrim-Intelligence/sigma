"""Recognise "the host's commit signer is down" and say so once, in plain words (#2135).

THE PROBLEM. In a Claude Code cloud container the commit-signing helper can stop answering. Every
`git commit` then fails, and the failure reached the operator as a raw git error (`work.py commit`) or,
worse, as a "publish deferred ... will retry" line the watcher repeats forever, or as nothing at all
(`sync.py init` swallowed it). Nobody learned that the actual cause was one thing that no retry fixes.

WHAT THIS MODULE IS. The one place that owns the words (`MESSAGE`) and the one classifier
(`unavailable`), so `work.py` and `sync.py` cannot drift. Stdlib only, no I/O of its own: a caller
passes git's failure text and, when it needs the signing setting, a callable that reads it.

WHAT IT DELIBERATELY DOES NOT DO. It never retries a commit unsigned and never offers to turn signing
off: the message says Sigma's commit and publish steps will not do that for you. The unattended rebase
engine (`unattended_git.py` and the upkeep resolution step that uses it) has its own deliberate, pinned
unsigned path and is out of scope, which is why the wording speaks only for the commit and publish steps.

HOW A FAILURE IS RECOGNISED, and why this is three clauses. The signer's own stderr is not stable: the
gpg format prints `error: gpg failed to sign the data:`, the ssh format prints whatever the helper
printed, a missing helper adds `fatal: cannot exec ...`. The one stable line is git's
`fatal: failed to write commit object`. That line is also what git prints when the OBJECT STORE cannot be
written (a full disk, a read-only repository), where "your signer is down" would be a lie. So an outage is
all of: signing is in effect, the fatal line is present, and no object-store marker is present on any
line. The signing setting is read only on the failure path (a callable), so a successful commit pays
nothing. A residual false positive (an object-store failure worded outside the listed markers, with
signing on) is accepted and bounded by that list.
"""
import re

MESSAGE = ("Commit signing is unavailable on this machine, so nothing can be committed or pushed. "
           "Start a fresh session (in a Claude Code cloud container the signer comes back when the "
           "container restarts). Sigma's commit and publish steps will not turn signing off for you.")

#: The first clause of MESSAGE: the substring every consumer keys on (the park classifier, the receipt
#: pass-through), so a consumer never re-types the wording.
NEEDLE = "Commit signing is unavailable on this machine"

#: What `work.py commit` returns it under, and what the CLI maps to exit 4.
PARK_PREFIX = "PARK: " + NEEDLE

#: Appended to what `work.py` prints, because the loop's instruction files sit at their context-budget
#: ceiling (`evals/phase_context_budget.json`) and cannot carry this lever: the verb says what to do.
PARK_TAIL = "Do not retry: record this goal as parked with this message."

_FATAL = "failed to write commit object"

# Object-store write failures: the same fatal line, a different cause. Matched PER LINE, ignoring case.
_OBJECT_STORE_MARKERS = ("no space left", "unable to create temporary file", "insufficient permission")
_PERMISSION_DENIED = "permission denied"
_OBJECT_STORE_WORD = re.compile(r"\bobjects?\b")

_LABELLED = re.compile(r"^(error|fatal|hint|warning):\s*(.*)$", re.IGNORECASE)
# Used only when the caller could not say what argv the runner put in front of git's stderr.
_AFTER_ARGV = re.compile(r"^.*?: ((?:error|fatal):.*)$", re.IGNORECASE)

DETAIL_CAP = 200


class SigningUnavailable(RuntimeError):
    """Carries the finished plain text. Each script loads its own copy of this module, so a consumer
    must recognise the failure by `NEEDLE in str(exc)`, never by class identity across modules."""


def _lines(detail):
    return str(detail).splitlines()


def _is_object_store_line(line):
    low = line.lower()
    if any(marker in low for marker in _OBJECT_STORE_MARKERS):
        return True
    return _PERMISSION_DENIED in low and bool(_OBJECT_STORE_WORD.search(low))


def unavailable(detail, signing_on):
    """True only when signing is in effect (`signing_on`, read by the CALLER), git's fatal line is in
    `detail`, and no line of it names an object-store write failure."""
    if not signing_on:
        return False
    lines = _lines(detail)
    if not any(_FATAL in line.lower() for line in lines):
        return False
    return not any(_is_object_store_line(line) for line in lines)


def signing_on(read, forced=False):
    """Is commit signing in effect? `forced` means `-S` is in the argv. Otherwise `read()` returns the
    output of `git config --type=bool commit.gpgsign`; an exception from it (git exits 1 for an unset
    key) means off."""
    if forced:
        return True
    try:
        return str(read()).strip().lower() == "true"
    except Exception:                            # noqa: BLE001 - an unreadable setting is "not on"
        return False


def _forces_signing(argv):
    """True when the argv carries a signing flag as a flag: `-S`, `-S<keyid>`, `--gpg-sign[=<keyid>]`.
    The value after `-m` and anything after `--` is data, not a flag."""
    skip = False
    for arg in argv:
        arg = str(arg)
        if skip:
            skip = False
            continue
        if arg == "--":
            return False
        if arg == "-m":
            skip = True
            continue
        if arg.startswith("-S") or arg == "--gpg-sign" or arg.startswith("--gpg-sign="):
            return True
    return False


def _strip_argv(detail, prefix):
    """The runners raise `<argv>: <stderr>`; drop the argv so it is not shown as the signer's words."""
    text = str(detail)
    if prefix and text.startswith(prefix + ": "):
        return text[len(prefix) + 2:]
    first, newline, rest = text.partition("\n")
    match = _AFTER_ARGV.match(first)
    return (match.group(1) + newline + rest) if match else text


def _detail_line(detail, prefix=""):
    """The last line of git's stderr that says something, without its `error:`/`fatal:` label, clipped.
    Skipped: blank lines, a label with nothing after it, git's `hint:`/`warning:` advice, and the fatal
    line itself. "" when nothing is left."""
    chosen = ""
    for raw in _lines(_strip_argv(detail, prefix)):
        line = raw.strip()
        labelled = _LABELLED.match(line)
        if labelled:
            if labelled.group(1).lower() in ("hint", "warning"):
                continue                        # git's advice, not the signer's words
            line = labelled.group(2).strip()
        if not line or _FATAL in line.lower():
            continue
        chosen = line
    flat = " ".join(chosen.split())
    return flat if len(flat) <= DETAIL_CAP else flat[:DETAIL_CAP - 3] + "..."


def plain(detail, prefix=""):
    """MESSAGE plus, when git said anything usable, one short detail line, all on a single line so it
    can be recorded as a park reason. `prefix` is the argv text the runner put before git's stderr."""
    line = _detail_line(detail, prefix)
    return MESSAGE + (f" (signer said: {line})" if line else "")


def classify(detail, argv, read, prefix=""):
    """The ONE read-config-and-classify step both `work.py` and `sync.py` use. Returns the plain text
    when `detail` (a failed `git commit` run with `argv`) is a signing outage, else None. The config is
    read only for a text that already looks like the outage, so a hook rejection costs no extra git.

    The verdict is read off git's stderr ALONE: the runner's `<argv>: ` prefix carries the commit MESSAGE,
    and a message that says `failed to write commit object` (or `No space left`) must not decide it."""
    stderr = _strip_argv(detail, prefix)
    if not unavailable(stderr, True):
        return None
    if not unavailable(stderr, signing_on(read, forced=_forces_signing(argv))):
        return None
    return plain(detail, prefix)
