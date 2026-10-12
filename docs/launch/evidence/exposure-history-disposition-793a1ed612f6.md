# Per-finding disposition of the exposure history scan at 793a1ed612f6

Dimension D10. Measured commit: `793a1ed612f616bfa23be3220a71f0ba78372dca` (origin/main when run, 2026-10-13).
Run locally on macOS, Python 3, from a clean worktree. This is NOT GitHub CI. The private patterns file stayed outside
the repository (supplied with `--patterns`); the raw scan JSON and the per-finding working files stayed in a scratch
directory outside the repository. This page records counts, rules, short descriptors and commit ids, never a matched value.
Machine-readable counts: `exposure-history-disposition-793a1ed612f6.json`.
It closes the item `exposure-rescan-f627d06ad3f6.md` left open ("NOT done: a per-finding disposition").

## Result

`exposure_scan.py history .` exited 1 (findings present; history mode reads no allowlist) with **495 findings** holding
**1,202 individual matches**, 0 oversized and 0 binary blobs skipped, and 0 findings from the private patterns file.
A finding is one (rule, blob); a match is one hit of the rule inside it. Every finding and every match is accounted for below.

| Class | Findings | Matches |
|-------|---------:|--------:|
| fixture / test data (synthetic value inside a `tests/` file) | 314 | 858 |
| detector prose, detector literal or detector false positive | 142 | 264 |
| placeholder (documented dummy value in a plan, research or evidence record) | 16 | 19 |
| redaction marker (value is itself a scrubbed-output literal) | 6 | 12 |
| owner-published role or maintainer contact (not in the five classes; see below) | 8 | 20 |
| **REAL** (personal data: the owner's local account name inside an absolute home path) | **9** | **29** |
| Total | 495 | 1,202 |

**A REAL finding exists.** All 9 are rule `absolute-home-path` in `.sdlc/` plan, research and design records. No REAL
finding is a secret, a credential or a private-pattern hit: the 115 findings under the secret-shaped rules (aws-key,
gh-token, private-key, jwt, sk-key, slack-token, url-password, credential-assignment) are all synthetic. The REAL ones
are listed by commit, path and rule only:

| Commit | Path | Rule | Matches | Reachable from origin/main |
|--------|------|------|--------:|----------------------------|
| `8969d65602ea` | `.sdlc/design/542.md` | absolute-home-path | 1 | no (a local stash entry) |
| `8969d65602ea` | `.sdlc/plans/346.md` | absolute-home-path | 2 | no (stash, one local WIP branch) |
| `8969d65602ea` | `.sdlc/research/822.md` | absolute-home-path | 1 | no (an `sdlc/822` branch, pushed to origin) |
| `8969d65602ea` | `.sdlc/research/895-4b2-controls.md` | absolute-home-path | 11 | no (a local stash entry) |
| `8e1e6a067428` | `.sdlc/plans/964.md` | absolute-home-path | 2 | no (an `sdlc/964` branch, pushed to origin) |
| `3a71d4f372ad` | `.sdlc/research/881-controls.md` | absolute-home-path | 1 | no (an `sdlc/881` branch, on origin only) |
| `ee140e1b676d` | `.sdlc/design/729.md` | absolute-home-path | 5 | no (local `sdlc/729` branch) |
| `b0d449ab56c6` | `.sdlc/design/729.md` | absolute-home-path | 4 | no (local `sdlc/729` branch) |
| `44ee86ec9f67` | `.sdlc/plans/258.md` | absolute-home-path | 2 | **yes** (in 43 commits of main history) |

What "REAL" means here, and does not: the matched text is the owner's local operating-system account name (and, in two
matches, a local temporary-directory name carrying a numeric user id) inside a path a model wrote into a goal record. It is
personal data and a private reference by this review's rule, so it is not classed as a fixture. It is not a credential, and
this scan cannot say whether anything else in those records is sensitive. Only one of the nine, `.sdlc/plans/258.md`, is in the
history of origin/main; the other eight sit on unmerged goal branches, a local stash and a WIP branch, three of them also
pushed to origin as goal branches. The earlier record (`exposure-disposition-8aee0c74c526.md`) routed historical
absolute-home-path findings to launch blocker #400; this page gives that blocker its exact list. Rewriting history, or
deleting the pushed goal branches and the stash entries, is an owner decision and was not done.

## By rule and class (findings)

| Rule | Fixture | Detector prose | Placeholder | Redaction marker | Owner contact | REAL | Total |
|------|--------:|---------------:|------------:|-----------------:|--------------:|-----:|------:|
| email-address | 184 | 38 | 4 | 0 | 8 | 0 | 234 |
| auth | 4 | 87 | 0 | 0 | 0 | 0 | 91 |
| absolute-home-path | 34 | 0 | 12 | 0 | 0 | 9 | 55 |
| credential-assignment | 30 | 9 | 0 | 0 | 0 | 0 | 39 |
| aws-key | 28 | 8 | 0 | 0 | 0 | 0 | 36 |
| private-key | 11 | 0 | 0 | 6 | 0 | 0 | 17 |
| gh-token | 15 | 0 | 0 | 0 | 0 | 0 | 15 |
| url-password | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| jwt | 3 | 0 | 0 | 0 | 0 | 0 | 3 |
| sk-key | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| slack-token | 1 | 0 | 0 | 0 | 0 | 0 | 1 |
| Total | 314 | 142 | 16 | 6 | 8 | 9 | 495 |

By location prefix: `tests/` 340 (314 fixture, 18 detector prose, 6 redaction marker, 2 owner contact), `skills/` 101
(94 detector prose, 7 placeholder), `tools/` 20 (13 detector prose, 7 placeholder), `.sdlc/` 23 (9 REAL, 12 detector prose,
2 placeholder), docs and community files 11 (5 detector prose, 6 owner contact). Reachability: 422 findings have a
commit in origin/main (1 REAL), 73 are only on other refs (8 REAL). No finding had its paths in two location prefixes.

## What each class is, per rule

- **email-address.** The matched text is almost always the public git user of an SSH remote URL (hosting-service, self-hosted
  and `.invalid` hosts) in remote-parsing tests (fixture) or in tool docstrings (detector prose); credentials-in-URL test
  strings with a dummy password; `.test` and `.example` addresses; and decorator lines (`@pytest...`, `@contextlib...`)
  that the rule reads as an address (detector false positive). The 8 owner-contact findings are the role and maintainer
  addresses on the owner's own domain in the code-owners file, the code of conduct, the security policy, the launch
  definition and one test of that definition, at two commits (an earlier spelling of the role address and the current
  one). `exposure-rescan-f627d06ad3f6.md` records the owner's decision of 2026-10-12 to publish them. They are not a secret
  and not one of the five classes you named, so they are counted apart rather than relabelled a placeholder.
- **auth.** 87 of 91 are the rule's Digest-scheme alternative matching an ordinary English noun phrase in code and prose
  (a change-detection vocabulary in the work and migrate scripts, a transcript, plans); detector false positive. The other 4
  are test strings for the Basic and Bearer schemes: a Basic value that decodes to a dummy `user:password` pair and Bearer
  values of the form `abc123...` (fixture).
- **absolute-home-path.** 34 fixtures in tests (home directories of invented one-word users such as a
  person's first name, "u", "you" or "fixture", on Linux, macOS and Windows layouts), 12 placeholders in records (an
  ellipsis in the user position, or "someone"), and the 9 REAL above.
- **aws-key.** 5 distinct values, all documented or obviously synthetic (the AWS documentation example key, `...EXAMPLE`
  variants, an `ABCDEFGHIJKLMNOP` run, a `...SECRETVALUE12345` and a `...LEAK0000...` string); in tests (fixture) and in the
  scrubber's own allow-listed fixture constants (detector literal).
- **gh-token.** 4 distinct values: alphabet runs, `SECRETSECRET...`, `REALSECRETTOKENLEAK01`; the name is bait for a scrubber
  test, the value is synthetic. All in `tests/`.
- **jwt.** 2 distinct values: the public jwt.io sample header and payload with a truncated signature, and a `{"sub":"1"}` token.
- **sk-key, slack-token, url-password.** one sk-style string spelling out "live secret value", two
  Slack-style strings whose body reads "fake token" and "commands token", and clone URLs with a dummy password in
  the user-info part (a two-word invented name and a "secret" misspelt with a digit).
- **private-key.** 17 findings. The key bodies in the fixtures are one or two lines of 60 to 95 characters built from a
  repeated 66 to 71 character line with at most 39 distinct characters. A real PKCS#8 key is far longer than two such lines,
  so these cannot be a whole key; they start with the common header prefix, which is why the scrubber test uses them. 11 are
  fixture, 6 are matches whose text already contains a scrubbed-output literal (redaction marker).
- **credential-assignment.** Constant names ending in a token word assigned a label string (feature, tier and backlog label
  names; detector prose in `skills/` and `.sdlc/`), a shell variable reference, a path-fragment constant, and dummy
  assignments in tests (a secret-named variable holding the documentation example key or an alphabet-and-digits run).

## Method

1. Checked memory first (`memory_pressure`: 48% free), ran nothing else heavy, and ran the history scan alone, once:
   `python3 tools/readiness/exposure_scan.py history . --patterns <owner patterns file> --json <scratch>/hist.json`.
   It read 625 reachable commits (the repository's object store holds every local and remote-tracking branch, backup refs
   and a stash entry; origin/main alone is 257 commits). Wall time about 100 seconds.
2. A scratch script re-read every flagged blob with `git cat-file --batch` (the blob at the commit that carries it), re-ran the
   finding's own rule to find every match (1,202, against 495 findings: the scan keeps only the first line per finding), and
   extracted for each match the matched value and the line before and after, redacted by all the scanner's rules plus the
   private patterns.
3. Reviewed, by reading them: every distinct matched value for the email, home-path, auth and each secret-shaped rule
   (about 75 distinct values in all, listed above in descriptor form); every distinct normalised line shape for the
   secret-shaped rules and for auth (digits collapsed); and the file, commit and line of every match outside `tests/`. The
   private-key bodies were checked for length and distinct-character count. The `Bearer`/`Basic` values and the
   token-looking values were checked against the placeholder words and known documentation samples.
4. Classified in a script from the reviewed value and the path prefix (rules in the section above), took the worst class per
   finding (REAL, then owner contact, fixture, placeholder, detector prose, redaction marker), and counted per finding and per match.
5. Control: the classifier is not decoration where it matters. It flagged the nine REAL findings from the value, so it can fail;
   it did not flag any secret-shaped finding because none carries a real-looking value. Its secret-rule decision for a `tests/`
   file is path-based after the manual value review, so a new real-looking value placed in `tests/` would not turn it red by
   itself (see limits).

### Individually read versus grouped, and why grouping is sound

Not every one of the 1,171 context lines was read one at a time. The 314 fixture findings in tests are grouped by identical
matched value and identical normalised line shape (for example 291 matches of one SSH-user value across remote-parsing
tests). That grouping is sound for this scan because the hit is the matched substring: the same substring on a line of the
same shape carries the same exposure, and any second sensitive string on the same line would be a hit of another rule and
is redacted in, and shown by, the preview. What the grouping could hide is a different value in an otherwise identical
line, which is why the grouping key includes the matched value and the secret-shaped rules were read by shape as well as value.
Every finding outside `tests/` (155 of 495) and every one of the 115 secret-shaped findings was read by its value, and the
nine REAL ones by file, line and commit.

## What this scan cannot see

- Blobs over 2 MiB and blobs with a NUL byte are skipped; this run skipped 0 of each, so none were skipped silently here,
  but any later one would be. UTF-16 text counts as binary.
- Wrapped or split secrets: a secret broken across lines, or assembled in code from parts (several fixtures here concatenate
  a prefix and a suffix on purpose), matches no rule or only part of one. A key block with no END line matches nothing.
- Values no rule recognises: a short guessable password, a token of an unlisted provider, a name or address in free text,
  an internal hostname. The private patterns file covers only what the owner put in it, and 0 of its patterns hit.
- Anything outside Git blobs: commit messages, tags, issue and PR text, the GitHub mirror, release assets, Actions logs.
- Refs: the scan reads what this clone holds. A branch deleted from the remote but kept here, or one never fetched, is not
  the same set as what a stranger can clone; and the stash entry that carries four of the REAL findings never leaves this machine unless pushed.
- Rule 'legacy-issue-reference' is count-only in this scan (99,569 hits this run); it is not a finding class.
- The class decision for a secret-shaped match in `tests/` rests on a person having read the value. This page does not make
  that read repeatable by a script; the next owner of this check re-reads any new distinct value.
- Comparison with the earlier record: `exposure-rescan-f627d06ad3f6.md` reported 488 findings on "250 commits"; this run
  reports 495 on 625 commits. The ref set of the earlier run is not recorded, so the two numbers are not reconciled here.

## Commands

```
python3 tools/readiness/exposure_scan.py history . --patterns <owner patterns file> --json <scratch>/hist.json
git cat-file --batch   # per-blob re-read, driven from a scratch script outside the repository
git for-each-ref --contains <commit>
```
