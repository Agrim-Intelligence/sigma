# What is new in 1.0.35

This page lists every change in this release. The same entries are in the `1.0.35` section of the
[CHANGELOG](../CHANGELOG.md). Start with [Upgrading from 1.0.34](#upgrading-from-1034) if you only want to know what
changes for you.

One new opt-in setting, one safety fix that changes what a goal's pull request can contain, and the source of a public
docs site that is not published yet.

## Upgrading from 1.0.34

1. **Restart sessions, and stop a running watcher** so it picks up the new code: `touch .sdlc/state/watch.stop`, then
   remove the file and start the watcher again.
2. **`work.protection_unreadable: "allow"` (#2242).** Opt-in; the default `"hold"` is unchanged. With
   `auto_merge: "always"`, a merge proceeds when every branch-protection read is refused with HTTP 403 and the
   branch's own `protected` flag reads false. It never applies under `"protected"`; a timeout, 5xx, rate limit, 404 or
   malformed answer still holds, and so does a classic 404 paired with a rules 403. `/sigma-doctor` shows a row while it
   is on.
   - **What you do:** nothing, unless your repository is on a plan where protection reads return 403 and you want
     auto-merge to proceed.
   - **Not measured:** the read shape was measured only as a manual read of the four endpoints on a free-plan private
     repository. A real merge through the new path was not run.
3. **Absolute home-directory paths stay out of a goal's pull request (#2243).** `work.py commit` rewrites them in the
   plan, research and acceptance files to repo-relative paths or `[path]`; `work.py pr`, `work.py checkpoint` and the
   three pushes of `work.py rebase` refuse when the branch's committed artifacts still hold one; the plan comment on
   the issue is scrubbed first.
   - **What you do:** if a command now refuses naming such a file, fix the path in it and commit again.
   - **Not covered:** a raw `git commit` pushed outside Sigma, history already published, a path with a space (it ends
     at the space), and `.slices.json`. This stops new leaks only.
4. **Public docs site source (#2244).** A static `site/` directory and a Pages workflow. GitHub Pages is not enabled, so
   nothing is published, and the workflow run on `main` fails until the owner enables Pages. The site was not rendered
   in a real browser, and a restyle is planned.

## Not measured

The full test suite was not run for this release. A real merge through the `protection_unreadable` path was not run.
Nothing was run in Antigravity, and Cursor is unverified until a live test. The README install URLs are not claimed to
work: they name a repository slug that does not exist yet (issue #2248 is open).
