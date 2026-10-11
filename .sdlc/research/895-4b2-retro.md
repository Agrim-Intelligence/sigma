# #895 slice 4b-2 retro

Intent: make a cloud-session `work.merge()` reach the REST PUT. Shipped: merge_rights permissions, protection()
(classic + rulesets), post_review comment over REST; review-found fail-open (unresolved threads in cloud) closed by a
REST line-comment check that refuses on any comment. Grade: achieved for the code path, UNMEASURED live in a real cloud
session (token `permissions`, rules read, comment POST, PUT through the proxy).

Product debt left: `_unresolved_threads` is still GraphQL fail-open where GraphQL is available (unchanged);
`reviewDecision` unreadable in cloud (approval mode parks); `--auto` arm; merge_design / close_design / verify_merge
still GraphQL (next slice 4b-3); a cloud refusal on ANY line comment (resolved or not) is conservative.
Lesson: a slice that unblocks a path must audit what the old blocker was silently protecting (the review found that the
GraphQL block was the only thing keeping the fail-open thread check unreachable).
