# System graph fragments

A system graph fragment is one JSON file that says what a repository's services, endpoints, topics and
owners are, and how they connect. Every edge carries evidence: where the claim came from, and how sure
the extractor was. This page is the contract; `skills/sigma-kg/scripts/sysgraph.py` enforces it. It does
not build, merge or query a graph (later goals do), and it makes no network or model call.

The vocabulary lives in `skills/sigma-kg/schema/sysgraph.v1.json`. That file is Sigma's own
vocabulary-and-rules document, read by hand-written code. It is not JSON Schema (not Draft-07); no
external validator reads it. The script reads every list below from that file, and a test holds this page to it.

## Shape

```
{ "sysgraph_version": "1.0", "repo": "<id>",
  "nodes": [ { "id": "Service:billing", "type": "Service", "name": "billing" } ],
  "edges": [ { "type": "calls", "from": "Service:billing", "to": "Endpoint:users:POST /v1/auth/token",
               "evidence": [ { "source": "static", "file": "src/billing/client.py", "line": 42,
                               "commit": "<40 lowercase hex>", "extracted_at": "2026-10-17T09:30:00Z",
                               "confidence": "resolved" } ] } ] }
```

Required top-level keys: `sysgraph_version`, `repo` (a non-empty string), `nodes` and `edges` (lists).
Other top-level keys are ignored. Nodes carry no evidence; each needs `id`, `type` and `name`.

## Node types

`Service`, `Repo`, `Team`, `Endpoint`, `Topic`, `Schema`, `Datastore`, `Function`, `Feature`, `BusinessRule`, `ADR`.

A node id is `Type:` followed by 1 to 500 characters with no control characters; `Type` is one of the
node types and must equal the node's `type`. Ids are unique within a fragment. There is no endpoint type
matrix: `calls Datastore -> Team` is not refused.

## Edge types

`exposes`, `calls`, `publishes`, `consumes`, `implemented_by`, `reads`, `writes`, `realised_by`, `enforced_in`, `governs`, `owned_by`.

## Evidence

Each edge needs an `evidence` list of 1 to 8 entries. A missing, null, empty or non-list value is a finding
naming the edge. Fields:

- `source`: one of `contract`, `static`, `trace`, `human`, `model`.
- `file`: repository-relative path (no leading `/`, no drive letter, no `..` segment, at most 1,024 characters).
- `line`: integer of at least 1.
- `commit`: 40 lowercase hex characters.
- `url`: `http` or `https`, at most 2,048 characters, no userinfo and no query string or fragment.
- `version`: the version of the document the `url` describes.
- `extracted_at`: UTC, `YYYY-MM-DDTHH:MM:SSZ`.
- `confidence`: one of `exact`, `resolved`, `inferred`.

Every entry requires `source`, `extracted_at`, `confidence` and an anchor: a `commit`, or both `url` and
`version`. A `url` alone or a `version` alone is a finding. `contract` and `static` entries also require
`file` and `line`. A `model` entry must have `confidence` `inferred` and is never premise-eligible:
`premise_eligible(edge)` in `sysgraph.py` is true only when at least one entry comes from another source.
Consumers must call it; nothing here can force them.

## Versions

`sysgraph_version` is the string `MAJOR.MINOR`. A reader refuses a major it has no rules for, naming the
version it saw and the majors it supports, and stops after that one finding. A malformed version (`1`,
`01.0`, empty, a number, a missing key) is refused the same way. A higher minor of a known major is accepted
with a warning on stderr and unknown fields are ignored; a minor may add only optional fields. Node or edge
types added after 1.0 are refused by a 1.0 reader even under a newer minor: the warning is advisory.

## Validate

```
python3 skills/sigma-kg/scripts/sysgraph.py validate <file>
```

Exit 0: valid (prints `valid: N nodes, M edges, K dangling endpoints`). Exit 1: findings, one per line,
at most 100 printed then `… and R more`, and a final `findings: N` with the true total. Exit 2: usage
error, or a file that is missing, a directory, not UTF-8, not JSON, too deeply nested, or over the size
limit, or a missing schema file. `--max-bytes N` overrides the size limit in either direction. `version`
prints the supported majors. The command is read-only and stdlib only; it runs on the system python3.

## What validation does not claim

Validation is structural, not truthful. It does not check that a cited file or line exists, nor that it
says what the edge claims. A dangling edge (an endpoint that is not among the fragment's own nodes) is
structurally valid but unresolved; the count is printed, and merging resolves it later. Timestamps are not
compared to the clock. Findings echo field names and at most 80 characters of an offending value.

Ceilings: the file is read whole and parsed in memory, so the size limit is physical memory divided by 32,
a heuristic that the measurements below check. A larger fragment is refused with its size; there is no
streaming mode. Output is bounded at 100 lines whatever the input. The end-to-end run from an installed
plugin in a real host is unmeasured.

## Measurements

Measured on a Mac16,8 with 24 GiB of memory (25,769,803,776 bytes), 2026-10-17, system python3 3.9.6,
`/usr/bin/time -l python3 skills/sigma-kg/scripts/sysgraph.py validate <file>`, three runs per size, on
generated valid fragments (a quarter as many nodes as edges, one or two evidence entries per edge):

| edges | file size | wall time (3 runs) | peak resident memory | memory / file size |
|---|---|---|---|---|
| 10,000 | 3.4 MB | 0.15 to 0.18 s | 37 MB | 10.8 (interpreter baseline included) |
| 100,000 | 34.6 MB | 1.22 to 1.27 s | 258 MB | 7.5 |
| 1,000,000 | 349 MB | 15.2 to 16.6 s | 1.89 to 1.96 GB | 5.6 |

Time grows linearly with edges. Measured memory is at most 7.5 times the file size once the interpreter
baseline is amortised, well under the factor of 32 behind the size limit; on this machine the limit is
805,306,368 bytes, so the 1,000,000-edge file is accepted, and a file at the limit would be expected to
peak near 4.5 to 6 GB (an extrapolation, not a measurement). Nothing else was measured: other machines,
other Python versions, hostile or deeply nested input beyond the refusal test, and an agent invoking the
gesture from an installed plugin in a real host.
