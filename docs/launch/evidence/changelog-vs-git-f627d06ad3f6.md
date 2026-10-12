# CHANGELOG against git history

Dimension D8. Measured commit: `f627d06ad3f6fe391936a0e0bb1e6061f2af6aa4`, 2026-10-12, local run, no network.
`tests/test_release_consistency.py` checks headings, ISO dates and the doctor's fallback repository; it does not
read git. This record does.

## Method (commands)

```
git tag --list 'v*'
git rev-list -n1 <tag>                       # the commit a tag names
git show -s --format=%cs <commit>            # its committer date
git show <commit>:.claude-plugin/plugin.json # the version the manifest held there
git log --reverse --format='%h %cs' -S'"version": "<X>"' -- .claude-plugin/plugin.json   # first commit that set a version
git cat-file -t <token>                      # does a hex token in CHANGELOG.md name a commit
```

For each of the 12 release sections: the heading date was compared with the date of the commit that tagged the version
(six versions have a tag) or, for the other six, with the first commit that set that version in the plugin manifest.

## Result

| Check | Result |
|-------|--------|
| Release sections | 12, versions strictly descending, dates non-increasing (newest first) |
| Sections with a tag | 6 of 12 (patch releases 3, 4, 6, 7, 10 and 11 have none) |
| Tagged: heading date equals tag commit date | 6 of 6 equal |
| Tagged: manifest version at the tag equals the heading version | 6 of 6 equal |
| Untagged: heading date equals date of first commit setting the version | 5 of 6 equal; one differs by a calendar day (below) |
| Hex tokens in CHANGELOG.md that name a commit | 10 of 10 resolve (`git cat-file -t` prints `commit`) |

The one difference: the third section is headed 2026-10-08. The commit that first set that version in the manifest has
committer date 2026-10-09T02:01:36+05:30, which is 2026-10-08T20:31 UTC. The heading is the UTC date and `%cs`
prints the committer's local date; the same instant, so this is a timezone reading, not a false date. Every
other section is a same-day match under either reading.

The first release section's manifest version was set on 2026-09-29, eight days before its heading date and tag (2026-10-07).
That is consistent with a first public release cut from an already-versioned tree, and the changelog does not claim
otherwise.

## What this does not show

- It checks the dates and the commit tokens the changelog states. It does not check each bullet's claim against the
  diff of its release; bullets that cite pull-request or issue numbers were not resolved against GitHub (no network).
- Six of the twelve versions have no git tag in this repository, so for them the date is tied to a manifest commit, not
  to a tag. Whether the public repository will tag them is an owner release action (`docs/release.md`).
- The shipped changelog of the public export is a separate file with a pending first-release date; the release test
  covers its heading form. It was not compared with git here.
- Defects found: 0 false dates, 0 unresolved commit tokens, 0 version mismatches; one timezone note and the six
  untagged versions, recorded above. Nothing was changed in `CHANGELOG.md`.
