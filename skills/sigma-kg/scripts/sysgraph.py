#!/usr/bin/env python3
"""Validate a system graph fragment against Sigma's sysgraph vocabulary (#663).

usage: sysgraph.py validate <file> [--max-bytes N]
       sysgraph.py version
       sysgraph.py -h | --help

`validate` reads one JSON fragment and prints one finding per line (at most 100, then a count of
the rest). Exit 0 valid, 1 findings, 2 usage error or an unreadable, non-UTF-8, non-JSON or
oversized file, or a missing schema file. It is read-only, makes no network or model call, and is
structural: it does not check that a cited file or line exists. The vocabulary lives in
`skills/sigma-kg/schema/sysgraph.v1.json` (Sigma's own format, not JSON Schema); this module reads
every list from there. Stdlib only.
"""
import json
import os
import re
import sys
from pathlib import Path

#: Majors this reader has rules for. A schema file `sysgraph.v<N>.json` must exist for each.
SUPPORTED_MAJORS = (1,)
#: Tests monkeypatch this to point the loader at another directory.
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
MAX_PRINTED = 100

USAGE = ("usage: sysgraph.py validate <file> [--max-bytes N]\n"
         "       sysgraph.py version\n"
         "       sysgraph.py -h | --help\n")

_VERSION_RE = re.compile(r"^[0-9]{1,3}\.[0-9]{1,3}$")
_TIME_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SCHEME_RE = re.compile(r"^https?://")


class InputError(Exception):
    """The input or the schema cannot be read (exit 2)."""


class VersionError(Exception):
    """The fragment's declared version cannot be accepted (exit 1)."""


def _echo(value, limit=80):
    """At most `limit` characters of a value, so a finding never carries a whole secret."""
    try:
        text = value if isinstance(value, str) else json.dumps(value)
    except (TypeError, ValueError):
        text = repr(value)
    text = re.sub(r"[\x00-\x1f\x7f]", "?", str(text))
    return text if len(text) <= limit else text[:limit] + "..."


def load_schema(major, schema_dir=None):
    if type(major) is not int or major not in SUPPORTED_MAJORS:
        raise ValueError("unsupported schema major: %s" % _echo(major))
    path = Path(schema_dir if schema_dir is not None else SCHEMA_DIR) / ("sysgraph.v%d.json" % major)
    try:
        with open(str(path), "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise InputError("cannot read schema file %s: %s" % (path, e))


def default_max_bytes():
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 32
    except (AttributeError, ValueError, OSError):
        raise InputError("cannot derive a size limit from this machine's memory; pass --max-bytes N")


def check_version(fragment):
    """Return the integer major of a usable declared version, or raise VersionError."""
    if not isinstance(fragment, dict):
        raise VersionError("top level must be a JSON object")
    if "sysgraph_version" not in fragment:
        raise VersionError('missing required key "sysgraph_version"')
    v = fragment["sysgraph_version"]
    if not isinstance(v, str) or len(v) > 7 or not _VERSION_RE.match(v):
        raise VersionError('sysgraph_version %s: must be a string "MAJOR.MINOR" (digits, no leading zero)'
                           % json.dumps(_echo(v)))
    parts = v.split(".")
    if any(len(p) > 1 and p[0] == "0" for p in parts):
        raise VersionError('sysgraph_version "%s": must be a string "MAJOR.MINOR" (digits, no leading zero)' % v)
    major = int(parts[0])
    if major not in SUPPORTED_MAJORS:
        raise VersionError('sysgraph_version "%s": unsupported major %d (this reader supports: %s)'
                           % (v, major, ", ".join(str(m) for m in SUPPORTED_MAJORS)))
    return major


def load_fragment(path, max_bytes=None):
    """Bounded read + parse; refuses an unsupported or malformed version (VersionError)."""
    if max_bytes is None:
        max_bytes = default_max_bytes()
    try:
        with open(str(path), "rb") as f:
            data = f.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise InputError("%s: file is %d bytes, over the limit of %d (raise it with --max-bytes N)"
                             % (path, os.path.getsize(str(path)), max_bytes))
        text = data.decode("utf-8")
        fragment = json.loads(text)
    except InputError:
        raise
    except UnicodeDecodeError:
        raise InputError("%s: not valid UTF-8" % path)
    except RecursionError:
        raise InputError("%s: JSON nested too deeply" % path)
    except ValueError as e:
        raise InputError("%s: not valid JSON (%s)" % (path, e))
    except OSError as e:
        raise InputError("%s: cannot read (%s)" % (path, e.strerror or e))
    check_version(fragment)
    return fragment


def version_warning(fragment, schema):
    fmin = int(fragment["sysgraph_version"].split(".")[1])
    smin = int(schema["sysgraph_version"].split(".")[1])
    if fmin > smin:
        return ('warning: sysgraph_version "%s" is newer than this reader\'s %s; unknown fields are ignored, '
                'but node or edge types added after %s are refused'
                % (fragment["sysgraph_version"], schema["sysgraph_version"], schema["sysgraph_version"]))
    return None


class _Findings(object):
    def __init__(self):
        self.total = 0
        self.kept = []

    def add(self, text):
        self.total += 1
        if len(self.kept) < MAX_PRINTED:
            self.kept.append(text)


def _id_problem(value, schema):
    rules = schema["id"]
    if not isinstance(value, str):
        return "must be a string"
    if len(value) > rules["max_rest"] + 40:
        return "too long"
    prefix, sep, rest = value.partition(":")
    if not sep or prefix not in schema["node_types"]:
        return "must look like Type:name with Type one of the node types"
    if not (rules["min_rest"] <= len(rest) <= rules["max_rest"]):
        return "name part must be %d to %d characters" % (rules["min_rest"], rules["max_rest"])
    if any(ord(c) < 32 or ord(c) == 127 for c in rest):
        return "contains a control character"
    return None


def _check_entry(entry, schema, where, out):
    ev = schema["evidence"]
    lim = ev["limits"]

    def bad(field, msg):
        out.add('%s: "%s": %s' % (where, field, msg))

    if not isinstance(entry, dict):
        out.add("%s: must be an object" % where)
        return
    for field in ev["required"]:
        if field not in entry:
            bad(field, "required")
    source = entry.get("source")
    rule = None
    if "source" in entry:
        if isinstance(source, str) and source in ev["sources"]:
            rule = ev["sources"][source]
        else:
            bad("source", "must be one of %s (got %s)" % (", ".join(ev["sources"]), json.dumps(_echo(source))))
    if "confidence" in entry:
        c = entry["confidence"]
        if not (isinstance(c, str) and c in ev["confidence"]):
            bad("confidence", "must be one of %s (got %s)" % (", ".join(ev["confidence"]), json.dumps(_echo(c))))
        elif rule is not None and "requires_confidence" in rule and c != rule["requires_confidence"]:
            bad("confidence", "%s evidence must be %s (got %s)" % (source, rule["requires_confidence"], c))
    if "extracted_at" in entry:
        t = entry["extracted_at"]
        if not (isinstance(t, str) and len(t) <= 20 and _TIME_RE.match(t)):
            bad("extracted_at", "must be YYYY-MM-DDTHH:MM:SSZ (got %s)" % json.dumps(_echo(t)))
    requires = rule["requires"] if rule is not None else []
    if "file" in entry or "file" in requires:
        f = entry.get("file")
        if "file" not in entry:
            bad("file", "required for %s evidence" % source)
        elif not isinstance(f, str) or not f or len(f) > lim["file"]:
            bad("file", "must be a non-empty string of at most %d characters" % lim["file"])
        elif f.startswith("/") or f.startswith("\\") or _DRIVE_RE.match(f) or ".." in f.replace("\\", "/").split("/"):
            bad("file", "must be repository-relative (no leading /, drive letter or .. segment)")
    if "line" in entry or "line" in requires:
        if "line" not in entry:
            bad("line", "required for %s evidence" % source)
        elif type(entry["line"]) is not int or entry["line"] < 1:
            bad("line", "must be an integer of at least 1 (got %s)" % _echo(entry["line"]))
    commit_ok = False
    if "commit" in entry:
        c = entry["commit"]
        if isinstance(c, str) and len(c) == 40 and _COMMIT_RE.match(c):
            commit_ok = True
        else:
            bad("commit", "must be 40 lowercase hex characters (got %s)" % json.dumps(_echo(c)))
    if "url" in entry:
        u = entry["url"]
        if not isinstance(u, str) or len(u) > lim["url"]:
            bad("url", "must be a string of at most %d characters" % lim["url"])
        elif not _SCHEME_RE.match(u):
            bad("url", "must be http or https")
        else:
            authority = u.split("://", 1)[1].split("/", 1)[0]
            if "@" in authority:
                bad("url", "userinfo (user:password@) is not allowed")
            elif "?" in u or "#" in u:
                bad("url", "query string or fragment is not allowed")
    if "version" in entry:
        v = entry["version"]
        if not isinstance(v, str) or not v or len(v) > lim["version"]:
            bad("version", "must be a non-empty string of at most %d characters" % lim["version"])
    url_ok = isinstance(entry.get("url"), str) and bool(entry.get("url"))
    ver_ok = isinstance(entry.get("version"), str) and bool(entry.get("version"))
    if not (commit_ok or (url_ok and ver_ok)) and "commit" not in entry:
        out.add("%s: anchor: needs commit, or url with version (url present: %s, version present: %s)"
                % (where, "url" in entry, "version" in entry))


def _validate(fragment, schema):
    out = _Findings()
    dangling = 0
    if not isinstance(fragment, dict):
        out.add("top level must be a JSON object")
        return out, 0
    for key in schema["header"]:
        if key not in fragment:
            out.add('missing required key "%s"' % key)
    if "repo" in fragment and not (isinstance(fragment["repo"], str) and fragment["repo"]):
        out.add('"repo": must be a non-empty string')
    nodes = fragment.get("nodes", [])
    edges = fragment.get("edges", [])
    if "nodes" in fragment and not isinstance(nodes, list):
        out.add('"nodes": must be a list')
        nodes = []
    if "edges" in fragment and not isinstance(edges, list):
        out.add('"edges": must be a list')
        edges = []
    ids = set()
    for i, node in enumerate(nodes):
        where = "nodes[%d]" % i
        if not isinstance(node, dict):
            out.add("%s: must be an object" % where)
            continue
        nid, ntype = node.get("id"), node.get("type")
        problem = _id_problem(nid, schema)
        if problem:
            out.add('%s: "id": %s (got %s)' % (where, problem, json.dumps(_echo(nid))))
        if not (isinstance(ntype, str) and ntype in schema["node_types"]):
            out.add('%s: "type": unknown node type %s' % (where, json.dumps(_echo(ntype))))
        elif problem is None and nid.partition(":")[0] != ntype:
            out.add('%s: "type": %s does not match id prefix %s' % (where, ntype, nid.partition(":")[0]))
        if not (isinstance(node.get("name"), str) and node["name"]):
            out.add('%s: "name": must be a non-empty string' % where)
        if problem is None:
            if nid in ids:
                out.add('%s: "id": duplicate node id %s' % (where, json.dumps(_echo(nid))))
            ids.add(nid)
    ev = schema["evidence"]
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            out.add("edges[%d]: must be an object" % i)
            continue
        etype, src, dst = edge.get("type"), edge.get("from"), edge.get("to")
        ident = "%s %s -> %s" % (_echo(etype), _echo(src), _echo(dst))
        where = "edges[%d] (%s)" % (i, ident)
        if not (isinstance(etype, str) and etype in schema["edge_types"]):
            out.add('%s: "type": unknown edge type %s' % (where, json.dumps(_echo(etype))))
        for label, end in (("from", src), ("to", dst)):
            problem = _id_problem(end, schema)
            if problem:
                out.add('%s: "%s": %s' % (where, label, problem))
            elif end not in ids:
                dangling += 1
        evidence = edge.get("evidence")
        if evidence is None or evidence == [] or evidence == {}:
            out.add("%s: no evidence" % where)
        elif not isinstance(evidence, list):
            out.add('%s: "evidence": must be a list of %d to %d entries' % (where, ev["min"], ev["max"]))
        elif len(evidence) > ev["max"]:
            out.add('%s: "evidence": at most %d entries (got %d)' % (where, ev["max"], len(evidence)))
        else:
            for j, entry in enumerate(evidence):
                _check_entry(entry, schema, "edges[%d].evidence[%d] (%s)" % (i, j, ident), out)
    return out, dangling


def validate(fragment, schema):
    """Findings (a list of strings, capped at 100) for a parsed fragment; [] means valid."""
    out, _ = _validate(fragment, schema)
    return list(out.kept)


_SCHEMA_CACHE = {}


def premise_eligible(edge, schema=None):
    """True only if at least one evidence entry comes from a premise-eligible (non-model) source."""
    if not isinstance(edge, dict) or not isinstance(edge.get("evidence"), list):
        return False
    if schema is None:
        if 1 not in _SCHEMA_CACHE:
            _SCHEMA_CACHE[1] = load_schema(1)
        schema = _SCHEMA_CACHE[1]
    sources = schema["evidence"]["sources"]
    for entry in edge["evidence"]:
        if isinstance(entry, dict):
            s = entry.get("source")
            if isinstance(s, str) and s in sources and sources[s]["premise_eligible"]:
                return True
    return False


def _cmd_validate(args):
    path, max_bytes, i = None, None, 0
    while i < len(args):
        a = args[i]
        if a == "--max-bytes":
            if i + 1 >= len(args) or not args[i + 1].isdigit() or int(args[i + 1]) < 1:
                sys.stderr.write("--max-bytes needs a positive integer\n" + USAGE)
                return 2
            max_bytes = int(args[i + 1])
            i += 2
        elif a.startswith("-") or path is not None:
            sys.stderr.write("unexpected argument: %s\n%s" % (_echo(a), USAGE))
            return 2
        else:
            path = a
            i += 1
    if path is None:
        sys.stderr.write(USAGE)
        return 2
    try:
        fragment = load_fragment(path, max_bytes)
        schema = load_schema(int(fragment["sysgraph_version"].split(".")[0]))
    except VersionError as e:
        print(str(e))
        print("findings: 1")
        return 1
    except InputError as e:
        sys.stderr.write("sysgraph: %s\n" % e)
        return 2
    warn = version_warning(fragment, schema)
    if warn:
        sys.stderr.write(warn + "\n")
    out, dangling = _validate(fragment, schema)
    if out.total == 0:
        print("valid: %d nodes, %d edges, %d dangling endpoints"
              % (len(fragment["nodes"]), len(fragment["edges"]), dangling))
        return 0
    for line in out.kept:
        print(line)
    if out.total > len(out.kept):
        print("… and %d more" % (out.total - len(out.kept)))
    print("findings: %d" % out.total)
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-h" in argv or "--help" in argv:
        sys.stdout.write(USAGE)
        return 0
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    if argv[0] == "validate":
        return _cmd_validate(argv[1:])
    if argv[0] == "version" and len(argv) == 1:
        try:
            schema = load_schema(SUPPORTED_MAJORS[-1])
        except InputError as e:
            sys.stderr.write("sysgraph: %s\n" % e)
            return 2
        print("supported majors: %s" % ", ".join(str(m) for m in SUPPORTED_MAJORS))
        print("schema version: %s" % schema["sysgraph_version"])
        return 0
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
