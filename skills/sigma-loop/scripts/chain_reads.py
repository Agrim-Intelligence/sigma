"""chain_reads.py -- a bounded read cache for ONE chain run cycle. Library only; no main.

A cycle reads the same issue many times: the plan's own closure, the closed-prerequisite check, the label drain, the
growth check each ask for it again (one 21-member chain's cycle read one issue 49 times, and the cycle cost about
1,100 GitHub calls). `CycleReads(run)` wraps the `gh` runner for the length of one `chain.run` call and answers a
repeat of a GET of ONE issue or ONE issue's comment page from memory.

What makes that safe:
  * only `api repos/<owner>/<repo>/issues/<N>` and `.../issues/<N>/comments` are kept, keyed by the whole argument
    list (a page, a field list or a different comment page is a different key); a list, a graphql read, a user
    lookup and anything else go straight through;
  * ANY call that can write (a method other than GET, a graphql mutation, a command that is not `api`) first drops
    everything kept, so a read after a write goes to GitHub;
  * it lives for one `chain.run` call, built after the cycle's out-of-process sweep and dropped before the pick, so a
    write by another process cannot be hidden by it;
  * it is bounded: MAX_ENTRIES entries and MAX_BYTES characters kept in all (a body is at most 65,536 characters).
    Past a bound a read is simply not kept; it is never refused;
  * a failed read raises as before and keeps nothing.

Not measured here: the saving on a real repository; the counting fake and the harness count calls.
"""
import re

MAX_ENTRIES = 400
MAX_BYTES = 16 * 1024 * 1024

_ISSUE = re.compile(r"^repos/[^/\s]+/[^/\s]+/issues/\d+(?:/comments)?$")


def _method(args):
    """The HTTP method of an `api` argument list (GET when none is given)."""
    for i, a in enumerate(args):
        if a in ("--method", "-X") and i + 1 < len(args):
            return str(args[i + 1]).upper()
    return "GET"


def _endpoint(args):
    return next((a for a in args[1:] if not str(a).startswith("-")), "")


def is_read(args):
    """True only for a call that cannot write: an `api` GET, or a graphql call with no mutation in it."""
    if not args or args[0] != "api":
        return False
    if _method(args) != "GET" and _endpoint(args) != "graphql":
        return False
    if _endpoint(args) == "graphql":
        return not any("mutation" in str(a).lower() for a in args) and "--input" not in args
    return True


def kept_key(args):
    """The key under which `args` is kept, or None when it is not one issue / one comment page."""
    if is_read(args) and _method(args) == "GET" and _ISSUE.match(_endpoint(args)):
        return tuple(str(a) for a in args)
    return None


class CycleReads:
    def __init__(self, base):
        self.base, self.store, self.size, self.hits = base, {}, 0, 0

    def __call__(self, args, *more, **kw):
        if more or kw:
            return self.base(args, *more, **kw)
        key = kept_key(args)
        if key is not None and key in self.store:
            self.hits += 1
            return self.store[key]
        if key is None and not is_read(args):
            self.store.clear()
            self.size = 0
        out = self.base(args)
        if key is not None and isinstance(out, str) and len(self.store) < MAX_ENTRIES and self.size + len(out) <= MAX_BYTES:
            self.store[key] = out
            self.size += len(out)
        return out
