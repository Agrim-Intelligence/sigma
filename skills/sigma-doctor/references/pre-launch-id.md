# An install under the pre-launch plugin id

When the plugin was renamed to Sigma Loop its update key
changed, so an install recorded under the old id keeps working and silently stops updating. A separate row
names it and prints, in order, the exact commands for this host: uninstall (per recorded scope), remove the
old marketplace (only when nothing else installed uses it), add the marketplace it came from, install
`sigmaloop@sigmaloop`. It fires only for the exact old id, or for the old plugin name from the pre-launch
repository: a fork never fires. It never runs, removes or writes anything; `.sdlc/` data is kept. See
`docs/upgrading.md`, "From the pre-launch name".
