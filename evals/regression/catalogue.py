#!/usr/bin/env python3
"""Schema checker for the planted-regression catalogue (#1057, slice 2 of #808). Read-only.

    python3 evals/regression/catalogue.py check [--root DIR]

Reads `gates.json`, `catalogue.json` and `mutators.py` under DIR/evals/regression (default: this
repository) and prints one `catalogue.py: <kind>: <detail>` line per problem on stderr.
Exit 0 clean, 1 problems, 2 a file missing or unreadable.

Rejected: duplicate or malformed gates (bad status, empty argv, pending without a reason, live with
one), an unknown gate id, an empty `must_be_red_by`, a row whose only gate is pending, a
(regression, form) the mutation engine lacks or whose `mutation` text is not that form's anchor, and
a mutator form with no row. `pending` gates are legal to list, never sufficient alone (D-4 of the
design: the control, not this checker, decides how pending affects its exit code).
Cost: O(rows + gates), no state, no network, nothing written.
"""
import argparse
import importlib.util
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _load(root):
    d = root / "evals" / "regression"
    try:
        gates = json.loads((d / "gates.json").read_text(encoding="utf-8"))["gates"]
        rows = json.loads((d / "catalogue.json").read_text(encoding="utf-8"))["rows"]
        spec = importlib.util.spec_from_file_location("mutators_for_catalogue", d / "mutators.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # import only; apply() is never called
        anchors = {tuple(a.split(None, 2)[:2]): a.split(None, 2)[2] for a in mod.anchors()}
        if not all(isinstance(x, dict) for x in gates + rows):
            raise ValueError("gates and rows must be lists of objects")
        for r in rows:
            for k in ("must_be_red_by", "must_be_red_in_live"):
                if not all(isinstance(x, str) for x in r.get(k, [])) or not isinstance(r.get(k, []), list):
                    raise ValueError(f"{k} must be a list of strings")
        if not all(isinstance(x.get(k, ""), str) for x in gates + rows
                   for k in ("id", "regression", "form", "mutation", "status")):
            raise ValueError("id/regression/form/mutation/status must be strings")
    except Exception as e:  # mutators.py is an untrusted import: any failure is a named exit 2
        sys.stderr.write(f"catalogue.py: unreadable: {e}\n")
        return None
    return gates, rows, anchors


def problems(gates, rows, anchors):
    out, status = [], {}
    for g in gates:
        gid = g.get("id")
        if gid in status:
            out.append(f"duplicate-gate: {gid}")
        status[gid] = g.get("status")
        if g.get("status") not in ("live", "pending"):
            out.append(f"bad-status: {gid} has {g.get('status')!r}")
        if not g.get("argv"):
            out.append(f"empty-argv: {gid}")
        if g.get("status") == "pending" and not g.get("pending_reason"):
            out.append(f"pending-without-reason: {gid}")
        if g.get("status") == "live" and g.get("pending_reason"):
            out.append(f"live-with-pending-reason: {gid}")
    seen = set()
    for r in rows:
        rid, key = r.get("id"), (r.get("regression"), r.get("form"))
        if rid in seen:
            out.append(f"duplicate-row: {rid}")
        seen.add(rid)
        need = r.get("must_be_red_by") or []
        if not need:
            out.append(f"empty-must-be-red-by: {rid}")
        out += [f"unknown-gate: {rid} lists {g!r}" for g in need if g not in status]
        if need and not any(status.get(g) == "live" for g in need):
            out.append(f"only-pending: {rid} has no live gate")
        if key not in anchors:
            out.append(f"unknown-mutation: {rid} {key} is not in mutators.py")
        elif r.get("mutation") != anchors[key]:
            out.append(f"anchor-mismatch: {rid} says {r.get('mutation')!r}, engine has {anchors[key]!r}")
    rowed = {(r.get("regression"), r.get("form")) for r in rows}
    out += [f"uncatalogued-mutation: {k[0]} {k[1]} has no row" for k in anchors if k not in rowed]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="catalogue.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--root", default=str(HERE.parent.parent))
    args = ap.parse_args(argv)
    loaded = _load(pathlib.Path(args.root))
    if loaded is None:
        return 2
    found = problems(*loaded)
    for line in found:
        sys.stderr.write(f"catalogue.py: {line}\n")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
