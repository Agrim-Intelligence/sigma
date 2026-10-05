#!/usr/bin/env bash
# Sigma — risk-skill tripwire detector (read-only, deterministic).
#
# Inspects the CURRENT change (working tree + staged + untracked) and prints ONE
# JSON object naming which conditional-risk categories the change touches:
#   migration -> sigma-migration-check · contract -> sigma-contract-check
#   sensitive -> sigma-security-review
# It detects the TRIGGER only; it does not run the risk skills and cannot verify
# they ran. Consumed by the loop's Research (blast-radius) and Review phases,
# whose SKILL prose surfaces the matching /sdlc-<risk>-check when a category matches.
#
# Three principles:
#   1. READ-ONLY & DETERMINISTIC. Never mutates the repo tree it scans. (The one
#      exception is sourcing the optional `.sdlc/risk-detect.conf` for glob
#      overrides — trusted repo-local config, like direnv, inside the harness dir.)
#   2. FAIL-OPEN. Missing dep / non-git tree -> valid empty JSON, exit 0.
#   3. SECRET SAFETY. The content scan reads diff bodies to classify lines. For
#      any hit it emits ONLY {category,file,line,pattern_id} — never the matched
#      substring or the diff line. The diff text is scanned in-stream and
#      discarded. stdout is consumed by the loop and may surface to the engineer.
#
# Output: {"schema":"risk-detect/v1","matched":[<cat>...],"hits":[{category,file,line,pattern_id}...]}
# Usage:  risk-detect.sh        (no args; scans the current change)
# jq-free, bash-3.2-safe, zero-dep.

case "${1:-}" in -h|--help) echo "usage: risk-detect.sh   (no args; scans the current change)"; exit 0 ;; esac
set -uo pipefail
# Pin the locale ONCE, here, in the unforked main shell, and never assign a locale variable again —
# not even per command (`LC_ALL=C cmd`). sigma#244: bash calls setlocale() on every locale-variable
# assignment, including a temporary one (set, then restore). A libintl-linked bash (Homebrew's, on
# macOS) reaches CoreFoundation from setlocale(), and CoreFoundation is not fork-safe: in a forked,
# not-exec'd subshell (process/command substitution, pipeline element) it intermittently SIGSEGVs.
# The per-file `LC_ALL=C grep` in the content-scan subshell did exactly that under load (26 crash
# reports, one stack), and FAIL-OPEN turned the dead subshell into "nothing detected". C is also
# the deterministic choice for everything below (byte-wise awk/grep/sort/glob). Guarded by
# tests/test_risk_detect.py::test_locale_is_pinned_once_and_never_changed_in_a_forked_shell.
export LC_ALL=C

SCHEMA="risk-detect/v1"

json_string() {
  local s="$1"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"; s="${s//$'\t'/\\t}"
  s="${s//$'\b'/\\b}"; s="${s//$'\f'/\\f}"
  # C0 control-byte fallback (\u00XX): a static ANSI-C-quoted replace per byte, NOT a runtime loop —
  # each $'\NNN' is resolved at bash PARSE time, so this never forks a subshell (F28 follow-up: the
  # original fix built these via `$(printf ...)` per byte per call, up to 52 forks/call, ~700x slower
  # on real collector runs; a fixed ~30-byte value now costs the same either way).
  s="${s//$'\001'/\\u0001}"; s="${s//$'\002'/\\u0002}"; s="${s//$'\003'/\\u0003}"
  s="${s//$'\004'/\\u0004}"; s="${s//$'\005'/\\u0005}"; s="${s//$'\006'/\\u0006}"
  s="${s//$'\007'/\\u0007}"; s="${s//$'\013'/\\u000b}"; s="${s//$'\016'/\\u000e}"
  s="${s//$'\017'/\\u000f}"; s="${s//$'\020'/\\u0010}"; s="${s//$'\021'/\\u0011}"
  s="${s//$'\022'/\\u0012}"; s="${s//$'\023'/\\u0013}"; s="${s//$'\024'/\\u0014}"
  s="${s//$'\025'/\\u0015}"; s="${s//$'\026'/\\u0016}"; s="${s//$'\027'/\\u0017}"
  s="${s//$'\030'/\\u0018}"; s="${s//$'\031'/\\u0019}"; s="${s//$'\032'/\\u001a}"
  s="${s//$'\033'/\\u001b}"; s="${s//$'\034'/\\u001c}"; s="${s//$'\035'/\\u001d}"
  s="${s//$'\036'/\\u001e}"; s="${s//$'\037'/\\u001f}"
  printf '"%s"' "$s"
}

NL=$'\n'

# -- matched-category accumulator (dedup, sorted on emit) ----------------------
CATS=""
add_cat() { case " $CATS " in *" $1 "*) return ;; esac; CATS="${CATS:+$CATS }$1"; }
cats_json() {
  local first=1 c
  printf '['
  for c in $(printf '%s\n' $CATS | sort); do   # LC_ALL=C is pinned at the top
    [ "$first" -eq 1 ] && first=0 || printf ','
    json_string "$c"
  done
  printf ']'
}

# -- hits accumulator (bash 3.2-safe indirect append) -------------------------
HITS=""
append() {
  local n="$1" cur="${!1}"
  if [ -z "$cur" ]; then printf -v "$n" '%s' "$2"; else printf -v "$n" '%s,%s' "$cur" "$2"; fi
}

emit_empty() { printf '{"schema":%s,"matched":[],"hits":[]}\n' "$(json_string "$SCHEMA")"; }

# -- fail-open gates -----------------------------------------------------------
command -v git >/dev/null 2>&1 || { emit_empty; exit 0; }
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { emit_empty; exit 0; }

# -- config: category path globs (mainstream defaults; conf overrides) ---------
# Substring globs (no leading-slash requirement) so they match at any depth:
# e.g. *migration* matches both migrations/001.sql and db/migrate/2_x.rb.
MIGRATION_GLOBS='*migration* *migrate* *alembic* *.sql *schema.prisma *liquibase* *flyway*'
CONTRACT_GLOBS='*openapi* *swagger* *.proto *.graphql *api/* *routes/* *controllers/* *.d.ts *.thrift'
# `*/.env*` is the depth companion the two dotenv patterns needed to honour the at-any-depth rule
# above: match_globs matches the WHOLE path, so `.env*` alone anchored at the repo root and `*.env`
# alone required a path ENDING in ".env" — a nested backend/.env.local was the one silent gap in
# these otherwise depth-free lists. One companion covers every depth (a case glob's `*` crosses
# `/`). Normalizing match_globs to a basename instead would silently break CONTRACT_GLOBS'
# slash-embedded patterns (*api/*, *routes/*, *controllers/*), so the fix is the pattern, not the
# matcher. A project overriding SENSITIVE_GLOBS in .sdlc/risk-detect.conf replaces this whole line
# and does not inherit the companion — the same all-or-nothing override this config has always had.
SENSITIVE_GLOBS='*auth* *login* *session* *permission* *rbac* *payment* *billing* .env* *.env */.env* *secret* *credential*'
# Optional glob overrides. Sourcing executes trusted repo-local bash — it lives in the harness-owned,
# scan-excluded .sdlc/ dir (same trust as a .envrc), not the source tree this collector reports on.
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.sdlc/risk-detect.conf" ] && . "$PROJECT_DIR/.sdlc/risk-detect.conf" 2>/dev/null

# Exclude the SDLC machinery and docs from the scan — the tripwire watches the
# engineer's source, not the harness state (.sdlc/*) or documentation (docs/*).
EXCL=(-- . ':(exclude).sdlc/**' ':(exclude)docs/**')

hit_obj() { # category file line pattern_id  -> JSON object (location only)
  printf '{"category":%s,"file":%s,"line":%d,"pattern_id":%s}' \
    "$(json_string "$1")" "$(json_string "$2")" "$3" "$(json_string "$4")"
}

# -- name scan: glob-match the changed-path set (cheap, robust, primary) -------
match_globs() { # path globstring -> 0 if any glob matches
  local p="$1" g
  for g in $2; do case "$p" in $g) return 0 ;; esac; done
  return 1
}
SEEN_NAMES=""
seen_name() { # dedup best-effort (newline-delimited; rare newline-in-path tolerated)
  case "$NL$SEEN_NAMES$NL" in *"$NL$1$NL"*) return 0 ;; esac
  SEEN_NAMES="${SEEN_NAMES:+$SEEN_NAMES$NL}$1"; return 1
}
while IFS= read -r -d '' p; do
  [ -n "$p" ] || continue
  seen_name "$p" && continue
  match_globs "$p" "$MIGRATION_GLOBS" && { add_cat migration; append HITS "$(hit_obj migration "$p" 0 path)"; }
  match_globs "$p" "$CONTRACT_GLOBS"  && { add_cat contract;  append HITS "$(hit_obj contract  "$p" 0 path)"; }
  match_globs "$p" "$SENSITIVE_GLOBS" && { add_cat sensitive; append HITS "$(hit_obj sensitive "$p" 0 path)"; }
done < <( {
  git -C "$PROJECT_DIR" diff --name-only -z "${EXCL[@]}" 2>/dev/null
  git -C "$PROJECT_DIR" diff --cached --name-only -z "${EXCL[@]}" 2>/dev/null
  git -C "$PROJECT_DIR" ls-files --others --exclude-standard -z "${EXCL[@]}" 2>/dev/null
} )

# -- content scan: classify ADDED lines in-stream, emit location only ----------
# Feed a unified diff of working + staged changes, plus synthesized "all-added"
# diffs for untracked text files, into one awk that never prints a matched value.
# v1 LIMITATION: the `+++` header has no raw/-z form, so a hit on a file whose
# NAME contains `"`/`\`/control bytes carries git's C-quoted literal in `file`
# (valid JSON, findable location, not the raw path).
emit_combined_diff() {
  git -C "$PROJECT_DIR" diff --no-color -U0 "${EXCL[@]}" 2>/dev/null
  git -C "$PROJECT_DIR" diff --cached --no-color -U0 "${EXCL[@]}" 2>/dev/null
  while IFS= read -r -d '' f; do
    [ -f "$PROJECT_DIR/$f" ] || continue
    grep -Iq . -- "$PROJECT_DIR/$f" 2>/dev/null || continue   # skip binary (C locale, pinned at top)
    # Prefix a synthetic "diff --git" header so the awk's inhunk reset fires for this
    # block too — an untracked file gets exactly one hunk, all-added.
    printf 'diff --git a/%s b/%s\n+++ b/%s\n@@ -0,0 +1 @@\n' "$f" "$f" "$f"
    awk '{ print "+" $0 }' "$PROJECT_DIR/$f" 2>/dev/null
  done < <(git -C "$PROJECT_DIR" ls-files --others --exclude-standard -z "${EXCL[@]}" 2>/dev/null)
  # Completion marker (sigma#244). No diff line can be a bare "#..." (git headers never start with
  # "#", content lines carry a +/-/space prefix), so this can only come from here. If it never
  # reaches the reader, a process in this pipeline DIED, and that must not read as "nothing found".
  printf '# risk-detect:end\n'
}

CONTENT_SCAN_DONE=0
while IFS= read -r hline; do
  [ -n "$hline" ] || continue
  cat="${hline%%$'\t'*}"; obj="${hline#*$'\t'}"
  [ "$cat" = "__end__" ] && { CONTENT_SCAN_DONE=1; continue; }
  add_cat "$cat"; append HITS "$obj"
done < <(
  emit_combined_diff | awk '
    /^# risk-detect:end$/ { print "__end__\t"; next }
    function jesc(s,  i,c,cr) {
      gsub(/\\/,"\\\\",s); gsub(/"/,"\\\"",s)
      cr = sprintf("%c",13); if (index(s,cr)) gsub(cr,"\\r",s)
      for (i=1;i<=31;i++) {
        if (i==13) continue
        c = sprintf("%c",i)
        if (index(s,c)) gsub(c,sprintf("\\u%04x",i),s)
      }
      return s
    }
    # A real "+++ " file header only appears OUTSIDE a hunk (before the first @@). Once inside a hunk, a
    # line rendered "+++ ..." is added CONTENT whose source began "++ " — treating it as a header would
    # capture the line (secret and all) into `file` and emit it. Track hunk state (parity with
    # alignment-collect.sh scan_hardstops, slice #89) so an in-hunk "+++ " line is scanned as content
    # and never becomes the `file` value.
    /^diff --git / { inhunk=0; file=""; next }
    !inhunk && /^--- / { next }
    !inhunk && /^\+\+\+ / { f=$0; sub(/^\+\+\+ [ab]\//,"",f); sub(/\t.*$/,"",f); file=f; next }
    /^@@ /     { inhunk=1; h=$0; sub(/^@@ -[0-9,]+ \+/,"",h); sub(/[, ].*$/,"",h); newln=h+0; next }
    inhunk && /^\+/ {
      line=$0; sub(/^\+/,"",line)
      if (line ~ /(ALTER[ \t]+TABLE|CREATE[ \t]+TABLE|DROP[ \t]+(TABLE|DATABASE|SCHEMA)|ADD[ \t]+COLUMN|DROP[ \t]+COLUMN|CREATE[ \t]+INDEX|RENAME[ \t]+(TABLE|COLUMN)|add_column|create_table|drop_column|createTable|dropTable|addColumn)/)
        emit("migration","ddl")
      # contract: route-decorator/handler content is a strong API signal at any
      # path. Exported-type DECLARATIONS are caught by the contract PATH globs
      # (api/routes/*.proto/*.graphql/*.d.ts) in the name scan — deliberately NOT
      # a blanket `export type|interface` content match, which would fire on every
      # internal export and bury the signal in noise.
      if (line ~ /(@(Get|Post|Put|Patch|Delete)\(|(app|router|api)\.(get|post|put|patch|delete)\()/)
        emit("contract","route")
      # Secret-shape patterns kept in lockstep with alignment-collect.sh scan_hardstops (F29
      # parity) — a parity test in tests/test_risk_detect.py enforces the two sets stay equal.
      # DATABASE_URL/REDIS_URL are BARE names: this group appends its own [ \t]*[:=], so the
      # literal "DATABASE_URL=" would demand a second separator and match nothing. Uppercase only
      # is deliberate — that is the conventional spelling for these two, and the awk ERE is
      # case-sensitive, so a lowercase variant would be a separate (noisier) decision.
      # (No apostrophes anywhere in this awk program: it is one single-quoted shell string.)
      if (line ~ /(AWS_SECRET_ACCESS_KEY|aws_secret_access_key|api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|access[_-]?token|password|DATABASE_URL|REDIS_URL)[ \t]*[:=]/)
        emit("sensitive","secret")
      else if (line ~ /(AKIA[0-9A-Z]{8}|ghp_[0-9A-Za-z]{8}|xox[baprs]-[0-9A-Za-z-]{8}|glpat-[0-9A-Za-z_-]{8}|AIza[0-9A-Za-z_-]{8}|-----BEGIN[ A-Z]*PRIVATE KEY-----|sk_live_[0-9A-Za-z]{8})/)
        emit("sensitive","token")
      else if (line ~ /(Authorization[ \t]*[:=]|[Bb]earer[ \t]+[A-Za-z0-9]|[ "_.]jwt|oauth)/)
        emit("sensitive","auth")
      else if (line ~ /(ssn|social_security|credit_card|card_number|passport_no)/)
        emit("sensitive","pii")
      newln++; next
    }
    inhunk && /^ / { newln++; next }
    function emit(cat,pid,  loc){
      loc=(newln>0?newln:0)
      printf "%s\t{\"category\":\"%s\",\"file\":\"%s\",\"line\":%d,\"pattern_id\":\"%s\"}\n",
             cat, cat, jesc(file), loc, jesc(pid)
    }
  '
)

# Still FAIL-OPEN (valid JSON, exit 0), but never SILENT: a dead content scan is named on stderr so
# a missing category can be told apart from an absent one. Location-free: no path, no diff text.
[ "$CONTENT_SCAN_DONE" -eq 1 ] || \
  echo "risk-detect: content scan incomplete (a scan process died before end-of-stream); matched/hits may be missing content-scan categories" >&2
printf '{"schema":%s,"matched":%s,"hits":[%s]}\n' "$(json_string "$SCHEMA")" "$(cats_json)" "$HITS"
exit 0
