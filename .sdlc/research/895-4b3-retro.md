# #895 slice 4b-3 retro

Landed: merge_design and close_design over REST; reconcile by a read of the PR's `state`; ratchet 45 -> 43.
Left on #895: verify_merge.py (3 sites, own design), the `--auto` arm, reviewDecision, R5 sibling list, and every
live measurement (all REST bodies and statuses here are from GitHub docs or fakes).
Lessons: (1) a negative assertion keyed on the old `gh pr merge` argv became vacuous the moment the call moved; the
test helpers now match the REST shape, and a positive test pins that shape. (2) The write-surface scanner resolves
variables by name file-wide, so deleting one `args` assignment changed rows in unrelated functions; rename, don't
trust a row count. (3) The old "recheck found nothing, so it landed" inference was the dangerous part, not the
retry; the replacement requires a positive MERGED/CLOSED.
Process note: research, plan and implementation ran inline in one agent; only the diff review is independent.
