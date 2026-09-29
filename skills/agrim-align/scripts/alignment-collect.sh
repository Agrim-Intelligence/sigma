#!/usr/bin/env bash
# Sigma — alignment-review evidence collector (read-only, deterministic).
#
# Gathers FACTS from git history + SDLC artifacts over an N-day window and
# prints ONE structured JSON "evidence pack" to stdout. It renders NO scores
# and NO verdicts — only facts and flags. The agrim-align skill judges the pack.
#
# Three principles, non-negotiable (same as the other read-only collectors):
#   1. READ-ONLY & DETERMINISTIC. Only reads the repo. Same state + same window
#      => byte-identical output. Never mutates, commits, or opens a PR.
#   2. FAIL-OPEN. Any missing dep, non-git tree, or unparseable input -> a valid
#      MINIMAL JSON object with a machine-readable degraded[] code, exit 0.
#      Non-zero exit is reserved ONLY for "could not emit even minimal JSON".
#   3. SECRET SAFETY. The hard-stop scan reads diff bodies to find candidate
#      secrets/SQL/auth/contract patterns. For any hit it emits ONLY
#      {commit,file,line,pattern_id} — never the matched substring, the diff
#      line, a captured group, or a redacted sample. The diff text is scanned
#      in-stream and discarded. stdout is LLM-facing and may be committed.
#
# Usage: alignment-collect.sh [--since-days N]   (N integer, default 1)
# jq-free, bash-3.2-safe, zero-dep.

case "${1:-}" in -h|--help) echo "usage: alignment-collect.sh [--since-days N]   (N integer, default 1)"; exit 0 ;; esac
set -uo pipefail
# Pin the locale ONCE, in the main shell. A per-command `LC_ALL=C cmd` inside a pipeline or
# $(...)/<(...) runs in a forked, not-exec'd bash, whose temporary-env setlocale() (Homebrew bash
# links libintl -> CoreFoundation, not fork-safe) intermittently SIGSEGVs under load; fail-open
# then reads the dead subshell as "nothing found". Byte-order sort is unchanged (already LC_ALL=C).
export LC_ALL=C

SCHEMA="alignment-collect/v1"

# -- JSON helpers (no jq dependency) -------------------------------------------
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

# -- degraded[] accumulator ----------------------------------------------------
DEGRADED=""   # space-separated unique codes
add_degraded() {
  case " $DEGRADED " in *" $1 "*) return ;; esac
  DEGRADED="${DEGRADED:+$DEGRADED }$1"
}
degraded_json() {
  local first=1 c
  printf '['
  for c in $(printf '%s\n' $DEGRADED | sort); do
    [ "$first" -eq 1 ] && first=0 || printf ','
    json_string "$c"
  done
  printf ']'
}

# -- minimal fail-open pack (valid JSON, empty window/commits, empty d1..d7) ----
emit_minimal() {
  printf '{"schema":%s,' "$(json_string "$SCHEMA")"
  printf '"window":{"since_days":%d,"oldest":{"sha":"","date":""},"newest":{"sha":"","date":""},"commit_count":0},' "$SINCE_DAYS"
  printf '"degraded":%s,' "$(degraded_json)"
  printf '"commits":[],'
  printf '"dimensions":{'
  printf '"d1":{"commits_with_source":0,"commits_with_fresh_plan":0,"plan_existed_pct":0,"per_commit":[],"files_changed_outside_any_plan":[],"files_outside_plan_confidence":"low"},'
  printf '"d2":{"tests_touched_with_source_pct":0,"test_command_known":false},'
  printf '"d3":{"per_commit":[],"churn_hotspots":[]},'
  printf '"d4":{"net_lines_added_window":0,"new_files_added":0,"per_commit":[]},'
  printf '"d5":{"reviews_dir_present":false,"commits_with_review_pct":0},'
  printf '"d6":{"hits":[]},'
  printf '"d7":{"decisions_added":[],"repeated_revert_or_fixup_count":0}'
  printf '}}\n'
}

# -- parse args ----------------------------------------------------------------
SINCE_DAYS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --since-days)
      shift
      case "${1:-}" in (''|*[!0-9]*) : ;; (*) SINCE_DAYS="$1" ;; esac
      ;;
    --since-days=*)
      v="${1#--since-days=}"
      case "$v" in (''|*[!0-9]*) : ;; (*) SINCE_DAYS="$v" ;; esac
      ;;
  esac
  shift || true
done

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"

# -- fail-open gates -----------------------------------------------------------
if ! command -v git >/dev/null 2>&1; then
  add_degraded "no_git"; emit_minimal; exit 0
fi
if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  add_degraded "no_git"; emit_minimal; exit 0
fi

# -- config (defaults + optional override) -------------------------------------
# Hardcoded inline. .sdlc/alignment-collect.conf is an OPTIONAL bash override only.
PLAN_FRESHNESS_HOURS=24
SOURCE_EXTS="js jsx ts tsx py go rb rs java kt swift c cc cpp h hpp cs php scala ex exs sh"
# shellcheck disable=SC1091
[ -f "$PROJECT_DIR/.sdlc/alignment-collect.conf" ] && . "$PROJECT_DIR/.sdlc/alignment-collect.conf" 2>/dev/null

# Large-batch thresholds (inline constants).
LARGE_BATCH_LINES=400
LARGE_BATCH_FILES=15

# The loop's machine-accumulated knowledge (lessons/retros) is not "work in a
# direction" — exclude it from the commit walk, the way retrospectives were.
PATHSPEC_EXCLUDE=":(exclude).sdlc/knowledge/**"

# -- classify a path: source | test | docmeta | other -------------------------
# docs/ or the .sdlc/ layer = meta; else ext in SOURCE_EXTS = source; among
# source, *test*/*spec* = test; else other.
classify_path() {
  local f="$1" ext e
  case "$f" in docs/*|.sdlc/*|*/docs/*) printf 'docmeta'; return ;; esac
  ext="${f##*.}"
  for e in $SOURCE_EXTS; do
    if [ "$ext" = "$e" ]; then
      case "$f" in
        *test*|*spec*|*Test*|*Spec*|*_test.*|*.test.*|*.spec.*) printf 'test' ;;
        *) printf 'source' ;;
      esac
      return
    fi
  done
  printf 'other'
}

# -- freshness helper: is file mtime within PLAN_FRESHNESS_HOURS? --------------
file_fresh() {
  [ -f "$1" ] || return 1
  find "$1" -mmin "-$((PLAN_FRESHNESS_HOURS*60))" 2>/dev/null | grep -q .
}

# -- file mtime as epoch seconds (portable) ------------------------------------
# F21/#339-class bug (fixed identically in watch.sh, commit 05d2289): BSD `stat -f %m` is a
# format-flag call, but on GNU coreutils `-f`/`--file-system` means filesystem-status mode and
# takes no FORMAT arg — it doesn't fail cleanly, it prints its own "  File: ..." block to STDOUT
# while still exiting nonzero, and that stray text becomes this function's return value, landing
# in the caller's `diff_h=$(( ... ))` arithmetic (the d1 plan-freshness loop, below) where a
# bareword token inside it triggers bash's own arithmetic-expansion variable-dereference quirk —
# a hard `set -u` abort, violating this file's own FAIL-OPEN contract. Fixed the same way: stop
# asking the shell's `stat` to be portable, read the mtime with `os.stat()` (identical on every
# platform Python runs on) instead. Unlike `05d2289`'s inline `... || true)"` form, no trailing
# `|| true` is needed here: this script sets no `-e` (only `set -uo pipefail`, top of file), and
# the one caller (the `p_epoch="$(date_mtime_epoch ...)"` assignment, below) never reads this
# function's exit status — only its stdout, via the `[ -n "$p_epoch" ]` guard right after it — so
# there is nothing for a nonzero return to abort. (Citing code, not line numbers, on purpose: this
# exact function is why #423 itself was filed with already-stale line-number citations.)
#
# python3 is a NEW dependency for this file as of the fix above — no other dimension needs it. A
# missing interpreter must not render as a confident "no fresh plan anywhere" (this file's header
# requires a degraded[] code for a missing dep, not a silently-worse answer). Checked ONCE, here,
# not inside date_mtime_epoch() itself: that function runs inside a `$(...)` subshell at its call
# site, so an add_degraded() call from inside it would mutate only the subshell's own copy of
# DEGRADED and never reach the parent's — this has to run in the main shell to count.
command -v python3 >/dev/null 2>&1 || add_degraded "no_python3"
# BATCHED, one interpreter for the whole list (issue #1486). This used to take ONE path and was
# called from inside the per-commit loop, once per plan per commit -- a `bash -x` trace of a
# 23-commit window measured 3,689+ `python3 -c` spawns, ~107s of pure interpreter startup at 29ms
# each, to read mtimes that cannot change while this script runs. The python3 reasoning above is
# unchanged and still right; only the call site was wrong. Reads paths on stdin, writes
# `<epoch>\t<path>` for each, in input order.
#
# `os.path.isfile` mirrors the `[ -f "$1" ]` this replaced EXACTLY -- `find -name '*.md'` carries no
# -type f, so a directory named `*.md` would stat fine and must still be skipped. A path that is
# absent or unstattable is omitted, which the caller's `continue` treated identically.
mtimes_epoch_batch() {
  python3 -c '
import os, sys
for line in sys.stdin:
    p = line.rstrip("\n")
    if not p or not os.path.isfile(p):
        continue
    try:
        sys.stdout.write("%d\t%s\n" % (int(os.stat(p).st_mtime), p))
    except OSError:
        pass
' 2>/dev/null
}

# -- render a newline-delimited path list as a sorted JSON string array --------
# Delegates to json_string() per element rather than a fourth hand-rolled awk escaper: this was
# previously its own `gsub(/\\/,...); gsub(/"/,...)` pair (backslash + quote only, like json_string
# and jesc before F28/#354) with the identical C0-control-byte gap -- a literal tab/CR in a file path
# produced invalid JSON. awk can't call a shell function directly, so the loop moved out of the awk
# pipeline into a shell `while read` calling the bash json_string() (already fixed for the full C0
# range); the per-path `$(...)` here is bounded by file count in one commit's diff, not per-byte like
# F28's original ~700x-slower cut, so it doesn't reintroduce that regression. #437.
NL=$'\n'
TAB=$'\t'
json_file_array() {
  local _label="$1" list="$2" out="" first=1 p
  if [ -z "$list" ]; then printf '[]'; return; fi
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    if [ "$first" -eq 1 ]; then out="$(json_string "$p")"; first=0
    else out="${out},$(json_string "$p")"; fi
  done < <(printf '%s\n' "$list" | grep . | sort)
  printf '[%s]' "$out"
}

# -- render churn_hotspots (dimensions.d3) as a sorted JSON array of {file,changes} objects ----------
# #1142: this was its own hand-rolled `gsub(/\\/,...); gsub(/"/,...)` awk pair -- backslash + quote
# only, the same gap json_file_array had before #437 -- so a tab/CR byte in a file path produced
# invalid JSON. Fixed the same way #437 fixed json_file_array: delegate per-path escaping to
# json_string. The count/sort step stays in awk (unchanged from before); only the OUTPUT encoding
# changes, from a NUL-delimited stream (not tab or newline -- either could legitimately appear IN a
# real path, which is exactly the byte this fix is closing the gap for; NUL cannot, it's the filename
# terminator on every POSIX filesystem) that a shell loop then decodes, escaping each path via
# json_string on the way. Callers must only ever invoke this with a non-empty "$@" — expanding an
# EMPTY "${arr[@]}" under `set -u` is an unbound-variable error on bash < 4.4 (this file's own
# bash-3.2-safe contract, header comment), so the emptiness check stays at the call site, same as
# json_file_array's own "$list" check stays a plain string emptiness test rather than living in here.
json_hotspots_array() {
  local out="" first=1 c p
  while IFS= read -r -d '' c && IFS= read -r -d '' p; do
    if [ "$first" -eq 1 ]; then out="{\"file\":$(json_string "$p"),\"changes\":$c}"; first=0
    else out="${out},{\"file\":$(json_string "$p"),\"changes\":$c}"; fi
  done < <(printf '%s\n' "$@" | sort | uniq -c | sort -rn \
             | awk '{ match($0,/^ *[0-9]+ /); c=substr($0,1,RLENGTH)+0; p=substr($0,RLENGTH+1);
                      printf "%d%c%s%c", c, 0, p, 0 }')
  printf '[%s]' "$out"
}

# -- render files_changed_outside_any_plan (dimensions.d1) as a sorted, DEDUPED JSON string array ----
# #1142: same fix as json_hotspots_array, immediately above -- this was the other hand-rolled
# backslash+quote-only awk pair in this file with the identical gap. Kept as its own function rather
# than a call to json_file_array: this list needs `sort -u` (an outside-plan path can recur across
# many commits in the window and must appear once in the final array), which json_file_array's own
# callers -- always one commit's own already-unique files_changed list -- never needed.
json_outside_plan_array() {
  local list="$1" out="" first=1 p
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    if [ "$first" -eq 1 ]; then out="$(json_string "$p")"; first=0
    else out="${out},$(json_string "$p")"; fi
  done < <(printf '%s' "$list" | grep . | sort -u)
  printf '[%s]' "$out"
}

# -- window bounds -------------------------------------------------------------
SINCE_ARG="${SINCE_DAYS} days ago"

SHAS="$(git -C "$PROJECT_DIR" log --no-merges --reverse \
          --since="$SINCE_ARG" --format='%H' \
          -- . "$PATHSPEC_EXCLUDE" 2>/dev/null)" || SHAS=""

SHA_SORTED="$(printf '%s\n' $SHAS | grep . | sort)"
COMMIT_COUNT=0
[ -n "$SHA_SORTED" ] && COMMIT_COUNT="$(printf '%s\n' "$SHA_SORTED" | wc -l | tr -d ' ')"

OLDEST_SHA=""; OLDEST_DATE=""; NEWEST_SHA=""; NEWEST_DATE=""
if [ "$COMMIT_COUNT" -gt 0 ]; then
  read -r OLDEST_SHA OLDEST_DATE < <(git -C "$PROJECT_DIR" log --no-merges --reverse \
      --since="$SINCE_ARG" --format='%H %cI' -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | head -n1)
  read -r NEWEST_SHA NEWEST_DATE < <(git -C "$PROJECT_DIR" log --no-merges \
      --since="$SINCE_ARG" --format='%H %cI' -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | head -n1)
fi

# -- artifact inventories (paths relative to PROJECT_DIR) ----------------------
list_plan_paths() {
  [ -d "$PROJECT_DIR/.sdlc/plans" ] || return 0
  find "$PROJECT_DIR/.sdlc/plans" -maxdepth 1 -name '*.md' 2>/dev/null | sort
}
PLAN_PATHS="$(list_plan_paths)"
# Hoisted out of the commit loop (#1486): plan mtimes are invariant for this run.
PLAN_MTIMES=""
[ -n "$PLAN_PATHS" ] && PLAN_MTIMES="$(printf '%s\n' "$PLAN_PATHS" | mtimes_epoch_batch)"

# .sdlc/reviews/ (risk-review + counter-review artifacts) presence.
REVIEWS_DIR_PRESENT=false
[ -d "$PROJECT_DIR/.sdlc/reviews" ] && REVIEWS_DIR_PRESENT=true

# -- d2: does the project DOCUMENT how it verifies? ----------------------------
# Sigma's proving command lives in .sdlc/config.json -> verify.command. We
# detect PRESENCE of a non-empty command (jq-free heuristic grep); we NEVER run
# anything derived from it.
TEST_COMMAND_KNOWN=false
# Heuristic (jq-free): the config declares a "verify" block AND carries a non-empty "command". Requiring
# "verify" present avoids a false-positive on some unrelated "command" key. Advisory FACT, not a gate.
if [ -f "$PROJECT_DIR/.sdlc/config.json" ] \
   && grep -q '"verify"' "$PROJECT_DIR/.sdlc/config.json" 2>/dev/null \
   && grep -Eq '"command"[[:space:]]*:[[:space:]]*"[^"]+' "$PROJECT_DIR/.sdlc/config.json" 2>/dev/null; then
  TEST_COMMAND_KNOWN=true
fi
[ "$TEST_COMMAND_KNOWN" = true ] || add_degraded "no_test_command"

# -- walk commits, accumulate per-commit + per-dimension facts -----------------
COMMITS_JSON=""
D1_PER=""; D3_PER=""; D4_PER=""; D6_HITS=""

COMMITS_WITH_SOURCE=0
COMMITS_WITH_FRESH_PLAN=0
COMMITS_SOURCE_WITH_TESTS=0
COMMITS_WITH_REVIEW=0
NET_LINES_WINDOW=0
NEW_FILES_WINDOW=0
REVERT_FIXUP_COUNT=0
RECOGNIZED_SOURCE_SEEN=0

declare -a HOTSPOT_FILES=()
OUTSIDE_PLAN_FILES=""

append() { # comma-join "$2" onto the accumulator named by "$1".
  local n="$1" cur="${!1}"
  if [ -z "$cur" ]; then printf -v "$n" '%s' "$2"; else printf -v "$n" '%s,%s' "$cur" "$2"; fi
}

# Scan one commit's diff for hard-stop candidates, emit LOCATION-ONLY hits.
# The matched bytes never reach a variable that is printed — only
# commit/file/line/pattern_id are captured (secret-safe).
# v1 LIMITATION: the unified-diff `+++` header has no `-z`/raw form, so for a hit
# on a file whose NAME contains a `"`, `\`, or control byte, the `file` field
# carries git's C-quoted literal (still valid JSON, still a findable location).
scan_hardstops() {
  local sha="$1"
  git -C "$PROJECT_DIR" show -p --no-color --format='' "$sha" \
      -- . "$PATHSPEC_EXCLUDE" 2>/dev/null \
    | awk -v sha="$sha" '
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
      # A real "--- "/"+++ " file header only appears OUTSIDE a hunk (before the first @@). Once inside
      # a hunk, a line rendered "+++ ..." is added CONTENT whose source began "++ " — treating it as a
      # header would capture the line (secret and all) into `file` and emit it. Track hunk state, so an
      # in-hunk "+++ " line is scanned as content (location-only) and never becomes the `file` value.
      /^diff --git / { inhunk=0; file=""; next }
      !inhunk && /^--- / { next }
      !inhunk && /^\+\+\+ / { f=$0; sub(/^\+\+\+ [ab]\//,"",f); sub(/\t.*$/,"",f); file=f; next }
      /^@@ / { inhunk=1; h=$0; sub(/^@@ -[0-9,]+ \+/,"",h); sub(/[, ].*$/,"",h); newln=h+0; next }
      inhunk && /^\+/ {
        line=$0; sub(/^\+/,"",line)
        # Secret-shape patterns kept in lockstep with risk-detect.sh content-scan (F29 parity) —
        # a parity test in tests/test_risk_detect.py enforces the two sets stay equal.
        # DATABASE_URL/REDIS_URL are BARE names: this group appends its own [ \t]*[:=], so the
        # literal "DATABASE_URL=" would demand a second separator and match nothing. Uppercase only
        # is deliberate — that is the conventional spelling for these two, and the awk ERE is
        # case-sensitive, so a lowercase variant would be a separate (noisier) decision.
        # (No apostrophes anywhere in this awk program: it is one single-quoted shell string.)
        if (line ~ /(AWS_SECRET_ACCESS_KEY|aws_secret_access_key|api[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret|access[_-]?token|password|DATABASE_URL|REDIS_URL)[ \t]*[:=]/)
          emit("secret","secret_key")
        else if (line ~ /(AKIA[0-9A-Z]{8}|ghp_[0-9A-Za-z]{8}|xox[baprs]-[0-9A-Za-z-]{8}|glpat-[0-9A-Za-z_-]{8}|AIza[0-9A-Za-z_-]{8}|-----BEGIN[ A-Z]*PRIVATE KEY-----|sk_live_[0-9A-Za-z]{8})/)
          emit("secret","generic_token")
        if (file ~ /\.env($|\.)/) emit("env_file","env_file")
        if (line ~ /(DROP[ \t]+(TABLE|DATABASE|SCHEMA)|TRUNCATE[ \t]+TABLE|DELETE[ \t]+FROM)/)
          emit("destructive_sql","destructive_sql")
        if (file ~ /\/(auth|billing|payment|payments|permissions)\// || line ~ /(stripe|[Cc]harge)/)
          emit("auth_billing","auth_billing")
        if (line ~ /(export[ \t]+(default[ \t]+)?(type|interface|enum)|public[ \t]+(api|interface))/)
          emit("exported_contract","exported_contract")
        newln++
        next
      }
      inhunk && /^ / { newln++; next }
      function emit(cat,pid,  loc) {
        loc = (newln>0 ? newln : 0)
        printf "{\"commit\":\"%s\",\"file\":\"%s\",\"line\":%d,\"pattern_id\":\"%s\",\"category\":\"%s\"}\n",
               jesc(sha), jesc(file), loc, jesc(pid), jesc(cat)
      }
    '
}

while IFS= read -r sha; do
  [ -n "$sha" ] || continue

  date="$(git -C "$PROJECT_DIR" show -s --format='%cI' "$sha" 2>/dev/null)"
  subject="$(git -C "$PROJECT_DIR" show -s --format='%s' "$sha" 2>/dev/null)"

  ins_total=0; del_total=0; file_count=0
  src_files=""; test_files=""; doc_files=""; other_files=""
  has_source=0; has_test=0
  while IFS= read -r -d '' tok; do
    ins="${tok%%$'\t'*}"; rest="${tok#*$'\t'}"
    del="${rest%%$'\t'*}"; path="${rest#*$'\t'}"
    if [ -z "$path" ]; then
      IFS= read -r -d '' _oldpath || true
      IFS= read -r -d '' path || true
    fi
    [ -n "${path:-}" ] || continue
    case "$ins" in (*[!0-9]*) ins=0 ;; esac
    case "$del" in (*[!0-9]*) del=0 ;; esac
    ins_total=$((ins_total + ins)); del_total=$((del_total + del))
    file_count=$((file_count + 1))
    HOTSPOT_FILES+=("$path")
    cls="$(classify_path "$path")"
    case "$cls" in
      source) has_source=1; RECOGNIZED_SOURCE_SEEN=1; src_files="${src_files}${src_files:+$NL}$path" ;;
      test)   has_source=1; has_test=1; RECOGNIZED_SOURCE_SEEN=1; test_files="${test_files}${test_files:+$NL}$path" ;;
      docmeta) doc_files="${doc_files}${doc_files:+$NL}$path" ;;
      other)  other_files="${other_files}${other_files:+$NL}$path" ;;
    esac
  done < <(git -C "$PROJECT_DIR" show --no-color --numstat -z --format='' "$sha" \
              -- . "$PATHSPEC_EXCLUDE" 2>/dev/null)

  net=$((ins_total - del_total))
  NET_LINES_WINDOW=$((NET_LINES_WINDOW + net))

  added_here="$(git -C "$PROJECT_DIR" show --no-color --diff-filter=A --name-only -z --format='' "$sha" \
                  -- . "$PATHSPEC_EXCLUDE" 2>/dev/null | tr -cd '\0' | wc -c | tr -d ' ' || true)"
  added_here=${added_here:-0}
  NEW_FILES_WINDOW=$((NEW_FILES_WINDOW + added_here))

  # d1: plan freshness vs THIS commit (low-confidence mtime correlation).
  plan_present=false; plan_path=""
  if [ -n "$PLAN_PATHS" ] && [ -n "$date" ]; then
    commit_epoch="$(git -C "$PROJECT_DIR" show -s --format='%ct' "$sha" 2>/dev/null)"
    while IFS="$TAB" read -r p_epoch p; do
      [ -n "$p" ] || continue
      [ -n "$p_epoch" ] || continue
      diff_h=$(( (commit_epoch > p_epoch ? commit_epoch - p_epoch : p_epoch - commit_epoch) / 3600 ))
      if [ "$diff_h" -le "$PLAN_FRESHNESS_HOURS" ]; then
        plan_present=true
        plan_path="${p#"$PROJECT_DIR"/}"
        break
      fi
    done <<< "$PLAN_MTIMES"
  fi

  if [ "$has_source" -eq 1 ]; then
    COMMITS_WITH_SOURCE=$((COMMITS_WITH_SOURCE + 1))
    [ "$plan_present" = true ] && COMMITS_WITH_FRESH_PLAN=$((COMMITS_WITH_FRESH_PLAN + 1))
    [ "$has_test" -eq 1 ] && COMMITS_SOURCE_WITH_TESTS=$((COMMITS_SOURCE_WITH_TESTS + 1))

    while IFS= read -r sf; do
      [ -n "$sf" ] || continue
      found_in_plan=0
      if [ -n "$PLAN_PATHS" ]; then
        while IFS= read -r p; do
          [ -n "$p" ] || continue
          if grep -qF -- "$sf" "$p" 2>/dev/null; then found_in_plan=1; break; fi
        done <<< "$PLAN_PATHS"
      fi
      [ "$found_in_plan" -eq 0 ] && OUTSIDE_PLAN_FILES="${OUTSIDE_PLAN_FILES}${sf}
"
    done <<EOF
${src_files}
${test_files}
EOF
  fi

  # d5: commits_with_review — a review artifact dir exists (weak proxy).
  if [ "$REVIEWS_DIR_PRESENT" = true ]; then
    COMMITS_WITH_REVIEW=$((COMMITS_WITH_REVIEW + 1))
  fi

  # d7 weak signal: revert / fixup / amend subjects.
  case "$subject" in
    [Rr]evert*|*fixup!*|*squash!*|*amend*) REVERT_FIXUP_COUNT=$((REVERT_FIXUP_COUNT + 1)) ;;
  esac

  # d3 flags.
  large_batch=false
  { [ "$((ins_total + del_total))" -gt "$LARGE_BATCH_LINES" ] || [ "$file_count" -gt "$LARGE_BATCH_FILES" ]; } \
    && large_batch=true
  pure_fmt=false
  { [ "$((ins_total + del_total))" -gt 20 ] && [ "${net#-}" -le 2 ]; } && pure_fmt=true

  files_json="$(json_file_array source "$src_files")"
  tfiles_json="$(json_file_array test "$test_files")"
  dfiles_json="$(json_file_array doc "$doc_files")"
  ofiles_json="$(json_file_array other "$other_files")"

  commit_obj="$(printf '{"sha":%s,"date":%s,"subject":%s,"insertions":%d,"deletions":%d,"files_changed":%d,"source_files":%s,"test_files":%s,"doc_files":%s,"other_files":%s}' \
    "$(json_string "$sha")" "$(json_string "$date")" "$(json_string "$subject")" \
    "$ins_total" "$del_total" "$file_count" \
    "$files_json" "$tfiles_json" "$dfiles_json" "$ofiles_json")"
  append COMMITS_JSON "$commit_obj"

  d1_obj="$(printf '{"commit":%s,"plan_present":%s,"plan_path":%s}' \
    "$(json_string "$sha")" "$plan_present" "$(json_string "$plan_path")")"
  append D1_PER "$d1_obj"

  d3_obj="$(printf '{"commit":%s,"insertions":%d,"deletions":%d,"files_changed":%d,"large_batch_flag":%s,"pure_formatting_suspect":%s}' \
    "$(json_string "$sha")" "$ins_total" "$del_total" "$file_count" "$large_batch" "$pure_fmt")"
  append D3_PER "$d3_obj"

  d4_obj="$(printf '{"commit":%s,"net_lines_added":%d,"new_files_added":%d}' \
    "$(json_string "$sha")" "$net" "$added_here")"
  append D4_PER "$d4_obj"

  hits_for_commit="$(scan_hardstops "$sha")"
  if [ -n "$hits_for_commit" ]; then
    while IFS= read -r hit; do
      [ -n "$hit" ] || continue
      append D6_HITS "$hit"
    done <<< "$hits_for_commit"
  fi
done <<< "$SHA_SORTED"

if [ "$COMMIT_COUNT" -gt 0 ] && [ "$RECOGNIZED_SOURCE_SEEN" -eq 0 ]; then
  add_degraded "no_recognized_source"
fi

plan_existed_pct=0
tests_pct=0
review_pct=0
if [ "$COMMITS_WITH_SOURCE" -gt 0 ]; then
  plan_existed_pct=$(( COMMITS_WITH_FRESH_PLAN * 100 / COMMITS_WITH_SOURCE ))
  tests_pct=$(( COMMITS_SOURCE_WITH_TESTS * 100 / COMMITS_WITH_SOURCE ))
fi
if [ "$COMMIT_COUNT" -gt 0 ]; then
  review_pct=$(( COMMITS_WITH_REVIEW * 100 / COMMIT_COUNT ))
fi

HOTSPOTS_JSON="[]"
[ "${#HOTSPOT_FILES[@]}" -gt 0 ] && HOTSPOTS_JSON="$(json_hotspots_array "${HOTSPOT_FILES[@]}")"

OUTSIDE_JSON="$(json_outside_plan_array "$OUTSIDE_PLAN_FILES")"

# decisions changed in window — Sigma's decision registry (.sdlc/decisions.json).
# #1266: this was a FOURTH hand-rolled JSON-array escaper (after F28/#354's json_string/jesc, #437's
# json_file_array, #1142's json_hotspots_array/json_outside_plan_array) sharing the identical narrow
# gap -- a bare `gsub(/\\/,...); gsub(/"/,...)` awk pair with no C0-control-byte handling. Delegates
# to json_outside_plan_array() directly rather than a fifth escaper: this list is already sorted+
# deduped the same way OUTSIDE_JSON's is (both ultimately via `sort -u` ahead of the same `grep .`
# empty-line filter), and json_outside_plan_array does exactly that plus the escaping internally, so
# the local `grep . | sort -u` step is now redundant and dropped. json_outside_plan_array's
# own output is already `[...]`-wrapped (matching OUTSIDE_JSON's own contract, used the same way at
# d1 below), so DECISIONS_JSON now carries the brackets itself -- the "[]" default mirrors
# HOTSPOTS_JSON's own empty-case default a few lines up, and the d7 printf below no longer adds its
# own brackets around it.
DECISIONS_JSON="[]"
if [ "$COMMIT_COUNT" -gt 0 ]; then
  DECISIONS_JSON="$(json_outside_plan_array "$(
    git -C "$PROJECT_DIR" log --no-merges --since="$SINCE_ARG" --name-only -z --format='' \
        -- '.sdlc/decisions.json' 2>/dev/null | tr '\0' '\n'
  )")"
fi

# -- assemble output (stable key order) ----------------------------------------
{
printf '{"schema":%s,' "$(json_string "$SCHEMA")"

printf '"window":{"since_days":%d,' "$SINCE_DAYS"
printf '"oldest":{"sha":%s,"date":%s},' "$(json_string "$OLDEST_SHA")" "$(json_string "$OLDEST_DATE")"
printf '"newest":{"sha":%s,"date":%s},' "$(json_string "$NEWEST_SHA")" "$(json_string "$NEWEST_DATE")"
printf '"commit_count":%d},' "$COMMIT_COUNT"

printf '"degraded":%s,' "$(degraded_json)"

printf '"commits":[%s],' "$COMMITS_JSON"

printf '"dimensions":{'

printf '"d1":{"commits_with_source":%d,"commits_with_fresh_plan":%d,"plan_existed_pct":%d,"per_commit":[%s],"files_changed_outside_any_plan":%s,"files_outside_plan_confidence":"low"},' \
  "$COMMITS_WITH_SOURCE" "$COMMITS_WITH_FRESH_PLAN" "$plan_existed_pct" "$D1_PER" "$OUTSIDE_JSON"

printf '"d2":{"tests_touched_with_source_pct":%d,"test_command_known":%s},' \
  "$tests_pct" "$TEST_COMMAND_KNOWN"

printf '"d3":{"per_commit":[%s],"churn_hotspots":%s},' "$D3_PER" "$HOTSPOTS_JSON"

printf '"d4":{"net_lines_added_window":%d,"new_files_added":%d,"per_commit":[%s]},' \
  "$NET_LINES_WINDOW" "$NEW_FILES_WINDOW" "$D4_PER"

printf '"d5":{"reviews_dir_present":%s,"commits_with_review_pct":%d},' \
  "$REVIEWS_DIR_PRESENT" "$review_pct"

printf '"d6":{"hits":[%s]},' "$D6_HITS"

printf '"d7":{"decisions_added":%s,"repeated_revert_or_fixup_count":%d}' \
  "$DECISIONS_JSON" "$REVERT_FIXUP_COUNT"

printf '}}\n'
}

exit 0
