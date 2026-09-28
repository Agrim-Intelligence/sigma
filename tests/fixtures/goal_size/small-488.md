`ledger.files_for()` uses a case-sensitive glob (`d.glob(f"{safe}-*.jsonl")`) to find an actor's own
ledger files for `sync.py`'s `publish()`/`bootstrap()`. On a case-INSENSITIVE filesystem (default
macOS APFS, Windows NTFS — not Linux ext4), two differently-cased `ledger.actor` configurations for
what the filesystem treats as the same login (`"acme-user"` vs `"Acme-User"`) write to what the OS
considers the SAME file — the second writer's claim/entries silently merge into the first writer's
already-existing file, under whichever casing was created first. `files_for()`'s glob, matching
against the OS-reported (case-preserved) directory-entry name, then fails to find that actor's own
file under the *other* casing — even though the content genuinely exists, merged in.

**Where:** `skills/agrim-loop/scripts/ledger.py::files_for()` (glob pattern) and `_safe_name()` (no
case normalization — line ~417-421, "keep = [c for c in str(who) if c.isalnum() or c in \"-_.\"]",
never lowercased).

**Confirmed NOT to affect the read path**: `read_all()`/`open_claims()`/`comment_watch.tick()` all
glob every `*.jsonl` unconditionally and attribute by the entry's own in-content `actor` field, never
the filename — so a claim/note from a case-varied actor is read correctly regardless. **Confirmed to
affect only** `sync.py`'s `publish()`/`bootstrap()` path (via `files_for()`), which needs to name
specific files to stage onto the shared `sdlc-ledger` ops branch.

**Found during:** the post-1.0.4 real-ticket validation pass for `comment_watch` (#385), while
deliberately testing case-insensitive self-suppression against a real GitHub login — an incidental,
adjacent discovery, not a defect in `comment_watch.py` itself.

**Reproduced empirically** (not just reasoned about): seeded ledger entries under `actor="acme-user"`
and `actor="Acme-User"` on a case-insensitive filesystem — both landed in the same physical file
(confirmed same inode), `read_all()` parsed both correctly, but `files_for(dir, "Acme-User")`
returned `[]` despite that actor's entry genuinely existing on disk.

**Practical severity: low.** Requires (a) `ledger.actor` hand-configured with a casing that doesn't
match the operator's real `gh api user` canonical login, AND (b) a case-insensitive filesystem
(default on macOS/Windows, not Linux). Two genuinely different real GitHub accounts can never
collide this way, since GitHub itself enforces case-insensitive username uniqueness — this is purely
a same-person, inconsistent-local-config scenario.

**Suggested fix:** normalize casing once, consistently, at the boundary — either lowercase in
`_safe_name()` (changes the on-disk filename format, needs a compat note for existing ledgers) or
resolve `ledger.actor` to its real canonical `gh api user` login once at config-load time rather than
trusting whatever casing was hand-typed into `config.json`. Needs a design decision (which layer
owns canonicalization), not just a quick patch — this is exactly the "safe by convention, one level
further out" pattern this repo's own review history has flagged more than once.

