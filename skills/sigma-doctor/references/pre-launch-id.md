# An install under the pre-launch plugin id

When the plugin was renamed to Sigma Loop its update key
changed, so an install recorded under the old id keeps working and silently stops updating. A separate row
names it and prints, in order, the exact commands for this host: uninstall (per recorded scope), remove the
old marketplace (only when nothing else installed uses it), add the marketplace it came from, install
`sigmaloop@sigmaloop`. It fires only for the old plugin name recorded from the pre-launch repository, or for the exact old id when the
recorded source cannot be read (a fork added by a local path or a non-GitHub git URL, with the old names, cannot be told apart and fires; a fork recorded from a GitHub repository does not). It never runs, removes or writes anything; `.sdlc/` data is kept. See
`docs/upgrading.md`, "From the pre-launch name".
