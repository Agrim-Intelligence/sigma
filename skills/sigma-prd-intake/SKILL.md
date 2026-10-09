---
name: sigma-prd-intake
description: Turn a PRD file into cited Dossiers plus one umbrella ticket, any shape accepted. Use for a PRD or /sigma-prd-intake.
allowed-tools: Bash(python3 *)
---

## Codex path resolution

Codex has no `CLAUDE_SKILL_DIR`. In each command, replace `${CLAUDE_SKILL_DIR}` with the
absolute directory of this installed `SKILL.md` (shown in the skill catalog); never run an
empty path. Claude keeps its provided value.

# sigma-prd-intake

The PRD door into `docs/dossier-pipeline.md` (Stage 0): a PRD file of ANY shape -- no headings,
no EARS, no ids -- becomes one Dossier per business outcome plus one umbrella ticket. The
scope-to-goals compile is not the route for a PRD. Engine: `skills/sigma-prd-intake/scripts/intake.py`
(deterministic: it validates, hashes, maps and files; it never reads the PRD with an LLM).

> **No code reading.** The PRD is the only input. Do not read source files or grep the repository.
> `allowed-tools` declares intent and spends prompts; it is not a sandbox (#1957): `Bash(python3 *)`
> can read anything, so this paragraph carries the rule on every host. `intake.py` additionally
> refuses a repository source file as `--prd` and the flags `--repo-read`, `--read-code`, `--grep`.
> That constrains the adapter, not you.

## The flow

1. **Read the PRD** (a file path). Group it into at most 12 business outcomes; each becomes one Dossier.
2. **Draft the answers file** for each outcome, over the fixed Dossier bank
   (`python3 "${CLAUDE_SKILL_DIR}/../sigma-dossier/scripts/dossier.py" bank`). Every answer needs a
   verbatim quote of at least 20 characters from the PRD in `cites` (per-answer, keyed by answer id). Never invent. Where the PRD
   is silent, vague or contradicts itself, set `status` to `silent`, `vague` or `contradiction`
   (a contradiction cites both quotes as a list): intake records the visible placeholder in the bank
   slot and a paired `open_<id>` question for design to carry into Doubts.
   ```json
   {"outcomes": [{"name": "Shared notes", "section": "Requirements",
     "answers": {"title": "...", "problem": "...", "constraints": "10 MB or 25 MB?"},
     "status": {"constraints": "contradiction"},
     "cites": {"title": "<verbatim>", "problem": "<verbatim>", "constraints": ["<q1>", "<q2>"]}}]}
   ```
3. **Plan** (validates every quote, writes `.sdlc/intake/<sha12>.json`, prints the mapping):
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/intake.py" plan --prd docs/prd.md --answers /tmp/answers.json
   ```
   Show the human the printed `PRD section -> Dossier title -> answer ids` table. The quote check
   proves a quote EXISTS in the PRD, not that it SUPPORTS the answer: the human reviews the mapping.
4. **Dry run, then file**:
   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/intake.py" file --plan .sdlc/intake/<sha12>.json --dry-run
   python3 "${CLAUDE_SKILL_DIR}/scripts/intake.py" file --plan .sdlc/intake/<sha12>.json
   ```
   Each Dossier carries a `### Source` block (PRD path, sha256, section, per-answer cites). The
   umbrella is labelled `epic` (minted if absent), never `sdlc:goal`, and lists every Dossier.
   Dossiers are filed before the umbrella, so none points back to it.

## The refused gesture (documented, and tested as written)

Asked to treat repository code as a PRD, intake refuses with exit 2 and `REFUSED [repo-source]`:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/intake.py plan --prd skills/sigma-loop/scripts/loop.py --answers a.json
```

The allow-list covers every path under every root that applies, tracked or not: the repo of the current
directory, of `--sdlc-dir`, of the PRD file itself, and the plugin install directory of this script. Outside a git
repo the current directory is the root. Every `--prd`, anywhere, must also be a `.md` or `.txt` file
(`prd-not-text-doc`), so a credentials file cannot be quoted into an issue body.

## Recovery

`file` records each filed Dossier in `.sdlc/intake/<plan-hash>.filed.json` and a re-run files only
the rest and the umbrella. Known limit: the ticket is created before
`.filed.json` is written (for the umbrella too), so a crash in that gap makes the resume file a duplicate.
The lever: `intake.py file --plan P --status` lists the recorded numbers; a human closes the duplicate.

## Not built (deferred, stated)

- stdin text and an issue number as the PRD (a stdin PRD has no stable path; an issue PRD needs a read through sources).
- The fast lane for already-clean PRDs.
- Design (`sigma-goal-design`) runs on the output unchanged; `sdlc:designed` is written only by goal-review on CONFIRM, never here.
