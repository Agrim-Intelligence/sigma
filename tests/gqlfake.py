"""Shared fake for `sources._swap_labels`' GraphQL transport (#1391 step 2, extended #1392).

Lifecycle transitions do not issue `gh issue edit --add-label/--remove-label`; they issue ONE
`gh api graphql` document with two aliased root mutations. Several test modules drive a REAL
`GitHubSource` through an injectable runner, so each of them needs to answer those three request
shapes. This is that answer, in ONE place -- it models GITHUB'S BEHAVIOUR, not the transport, so a
swap is translated into the equivalent add/remove records and every pre-existing assertion about
WHICH labels a transition writes stays valid.

Imported as a plain sibling module (`import gqlfake`) -- pytest puts each test file's own directory
on `sys.path`, the same way these tests already reach `skills/` by explicit path.
"""
import json, re

_GQL_LABEL_IDS = {}          # name -> synthetic node id, stable within a run
#: Label names this fake should answer as ABSENT from the repo — a test opts a name in to exercise
#: the unknown-label path. Empty by default: every name resolves.
_UNKNOWN_LABELS = set()

_GQL_LAST_ISSUE = {"n": "0"}  # the mutation carries only a node id, so remember the
                              # number from the `issue(number:)` lookup that precedes it


def swap(args, labels=None, calls=None, repo_args=("--repo", "o/r")):
    """Handle sources._swap_labels' three GraphQL shapes. Returns a canned response string, or None
    if `args` is not a graphql call (so the caller falls through to its own handling).

    `labels`: a mutable set the mutation is applied to (models the real label set).
    `calls`: if given, one synthetic `issue edit ... --add-label/--remove-label` entry is appended
    per label changed, so call-log assertions written before this change keep working."""
    if len(args) >= 2 and args[0] == "repo" and args[1] == "view":
        # `_owner_name` falls back to this when discovery.github.repo is unset (the shipped default,
        # and what most of these fixtures use).
        return json.dumps({"owner": {"login": "o"}, "name": "r"})
    if not (len(args) >= 2 and args[0] == "api" and args[1] == "graphql"):
        return None
    doc = next((a[len("query="):] for a in args if str(a).startswith("query=")), "")
    if "label(name:" in doc:
        # #1393 review bug_001: `_label_node_ids` fetches BY NAME now (one aliased field per label)
        # instead of an unpaginated `labels(first: 100)` page -- on a repo with >100 labels the
        # sdlc:* ones could fall outside that window, and every lifecycle transition then silently
        # stopped writing. This fake answers the same shape: a null field for a label the repo does
        # not have, which is exactly the "unknown label" signal `_swap_labels` raises on.
        fields = {}
        for alias, name in re.findall(r'(a\d+): label\(name: "((?:[^"\\]|\\.)*)"\)', doc):
            name = name.replace('\\"', '"').replace("\\\\", "\\")
            if name in _UNKNOWN_LABELS:
                fields[alias] = None
                continue
            _GQL_LABEL_IDS.setdefault(name, "L_%d" % (abs(hash(name)) % 10**8))
            fields[alias] = {"id": _GQL_LABEL_IDS[name], "name": name}
        return json.dumps({"data": {"repository": fields}})
    if "labels(first" in doc:
        names = sorted(labels) if labels is not None else []
        for n in ("sdlc:goal", "sdlc:in-progress", "sdlc:parked", "sdlc:blocked",
                  "sdlc:needs-confirmation", "sdlc:blocking", "sdlc:dependency"):
            if n not in names:
                names.append(n)
        for n in names:
            _GQL_LABEL_IDS.setdefault(n, "L_%d" % (abs(hash(n)) % 10**8))
        return json.dumps({"data": {"repository": {"labels": {"nodes": [
            {"id": _GQL_LABEL_IDS[n], "name": n} for n in names]}}}})
    if "issue(number" in doc:
        m = re.search(r"issue\(number: (\d+)\)", doc)
        if m:
            _GQL_LAST_ISSUE["n"] = m.group(1)
        return json.dumps({"data": {"repository": {"issue": {"id": "I_node"}}}})
    if doc.startswith("mutation"):
        num = _GQL_LAST_ISSUE["n"]
        by_id = {v: k for k, v in _GQL_LABEL_IDS.items()}
        for alias, verb in (("a: addLabelsToLabelable", "--add-label"),
                            ("r: removeLabelsFromLabelable", "--remove-label")):
            if alias not in doc:
                continue
            seg = doc[doc.index(alias):]
            ids = re.findall(r'"(L_\d+)"', seg[:seg.index("}")] if "}" in seg else seg)
            for lid in ids:
                name = by_id.get(lid)
                if name is None:
                    continue
                if verb == "--add-label":
                    if labels is not None:
                        labels.add(name)
                else:
                    if labels is not None:
                        labels.discard(name)
                if calls is not None:
                    calls.append(["issue", "edit", num or "0", *repo_args, verb, name])
        return json.dumps({"data": {"a": {"clientMutationId": None}}})
    return "{}"


def labels_in(args):
    """The label NAMES a `_swap_labels` mutation is about, decoded from its node ids — or `[]` when
    `args` is not a mutation call.

    Exists so a test can express "make THIS label's write fail" in the vocabulary it cares about
    (`sdlc:blocking`) instead of in transport details. Before the swap, a fixture could simply match
    `--add-label sdlc:blocking` in the argv; the mutation carries opaque node ids instead, and a
    test forced to match those would be asserting on the fake rather than on behaviour.
    """
    if not (len(args) >= 2 and args[0] == "api" and args[1] == "graphql"):
        return []
    doc = next((a[len("query="):] for a in args if str(a).startswith("query=")), "")
    if not doc.startswith("mutation"):
        return []
    by_id = {v: k for k, v in _GQL_LABEL_IDS.items()}
    return [by_id[i] for i in re.findall(r'"(L_\d+)"', doc) if i in by_id]
