#!/usr/bin/env bash
# Sigma — audit evidence collector (read-only, reproducible).
#
# Measures the repo's CURRENT STATE and prints ONE structured JSON pack to
# stdout. Sibling of alignment-collect.sh, same non-negotiable contract, but a
# different question: alignment-collect walks COMMITS over a window and asks
# what changed; this walks the tracked FILE SET and asks what exists.
#
#   1. READ-ONLY & REPRODUCIBLE. Same tree + same RESOLVED window => byte-identical
#      output. Being precise, because the weaker claim is the true one: the churn
#      window is derived from "N days ago", so counts do shift as the calendar
#      moves past a commit. That is inherent to any churn window (alignment-collect.sh
#      has it too). The bounds are therefore EMITTED, so a reader sees exactly which
#      span produced the numbers instead of trusting a "deterministic" label that
#      quietly is not.
#   2. FAIL-OPEN. Missing dep, non-git tree, unreadable file -> valid MINIMAL JSON
#      with a degraded[] code, exit 0. An audit that cannot measure still runs;
#      one that crashed measures nothing.
#   3. SECRET SAFETY. stdout is LLM-facing and may be committed. Emits paths,
#      counts and ranks only — never file content, never a matched line. Marker
#      counting reads with `grep -c` and discards the text.
#   4. RENDERS NO VERDICT. Facts only; /sigma-audit judges. A collector that scored
#      would invite trusting the score over the code.
#
# Output is CAPPED at the top-N files by score, so the pack stays bounded no matter
# how large the repo is — that bound is the whole cost posture /sigma-audit rests on.
#
# Usage: audit-collect.sh [--churn-days N]   (N integer, default 180)
# jq-free, bash-3.2-safe, zero-dep.
case "${1:-}" in -h|--help) echo "usage: audit-collect.sh [--churn-days N]   (N integer, default 180)"; exit 0 ;; esac
set -u

MAX_FILES=40
CHURN_DAYS=180
PROJECT_DIR="$(pwd)"

# -- JSON helpers (no jq dependency) -------------------------------------------
# json_string is duplicated VERBATIM across five collectors; tests/test_json_string_escaping.py
# asserts the five bodies stay byte-identical, so a fix to one can never leave the others behind.
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
DEGRADED=""
add_degraded() { case ",$DEGRADED," in *",$1,"*) ;; *) DEGRADED="${DEGRADED:+$DEGRADED,}$1" ;; esac; }
degraded_json() {
  local out="" c IFS=,
  for c in $DEGRADED; do out="${out:+$out,}$(json_string "$c")"; done
  printf '[%s]' "$out"
}

# -- config: what counts as source -------------------------------------------
# Copied verbatim from alignment-collect.sh; a parity test in tests/test_audit_collect.py keeps the
# two in sync. If "what counts as source" drifts, two audits of one repo disagree about what they
# even looked at.
SOURCE_EXTS="js jsx ts tsx py go rb rs java kt swift c cc cpp h hpp cs php scala ex exs sh"

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


# -- output ---------------------------------------------------------------------
emit() { # files_json
  printf '{"schema":"audit-collect/v1","window":{"churn_days":%d,"since":%s,"until":%s},' \
    "$CHURN_DAYS" "$(json_string "$SINCE")" "$(json_string "$UNTIL")"
  printf '"degraded":%s,' "$(degraded_json)"
  printf '"totals":{"source_files":%d,"test_files":%d,"source_lines":%d},' \
    "$N_SOURCE" "$N_TEST" "$N_LINES"
  printf '"files":[%s]}\n' "$1"
}

# -- args -----------------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --churn-days) shift; case "${1:-}" in ''|*[!0-9]*) ;; *) CHURN_DAYS="$1" ;; esac ;;
  esac
  shift || true
done

SINCE=""; UNTIL=""; N_SOURCE=0; N_TEST=0; N_LINES=0

# -- fail-open gate -------------------------------------------------------------
if ! git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  add_degraded no_git
  emit ""
  exit 0
fi

# Window bounds come from git, not the clock, and are emitted so the span is visible.
UNTIL="$(git -C "$PROJECT_DIR" log -1 --format=%cI 2>/dev/null)" || UNTIL=""
SINCE="$(git -C "$PROJECT_DIR" log -1 --format=%cI --before="${CHURN_DAYS} days ago" 2>/dev/null)" || SINCE=""
# A repo younger than the window has NO commit before the cutoff, so the lookup above returns "" —
# and every young repo hits it. The effective window really does start at the first commit, so report
# that rather than an empty bound: the span exists to be read, and a blank one silently defeats it.
if [ -z "$SINCE" ]; then
  SINCE="$(git -C "$PROJECT_DIR" log --reverse --format=%cI 2>/dev/null | head -n 1)" || SINCE=""
fi

# -- classify the tracked file set ----------------------------------------------
# Fed by heredoc, NOT a pipe: a piped `while` runs in a subshell in bash, and every counter
# incremented below would be discarded at the loop's end.
TEST_STEMS=""
SRC_LIST=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  case "$(classify_path "$f")" in
    test)   N_TEST=$((N_TEST + 1)); TEST_STEMS="${TEST_STEMS}${f##*/}
" ;;
    source) N_SOURCE=$((N_SOURCE + 1)); SRC_LIST="${SRC_LIST}${f}
" ;;
  esac
done <<EOF
$(git -C "$PROJECT_DIR" ls-files 2>/dev/null)
EOF

[ "$N_SOURCE" -eq 0 ] && add_degraded no_recognized_source

# -- per-file facts --------------------------------------------------------------
ROWS=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  [ -f "$PROJECT_DIR/$f" ] || continue
  lines=$(wc -l < "$PROJECT_DIR/$f" 2>/dev/null | tr -d ' ')
  case "$lines" in ''|*[!0-9]*) lines=0 ;; esac
  N_LINES=$((N_LINES + lines))
  churn=$(git -C "$PROJECT_DIR" rev-list --count --since="${CHURN_DAYS} days ago" HEAD -- "$f" 2>/dev/null)
  case "$churn" in ''|*[!0-9]*) churn=0 ;; esac
  last=$(git -C "$PROJECT_DIR" log -1 --format=%cI -- "$f" 2>/dev/null) || last=""
  # `grep -c` ALWAYS prints a count and exits 1 on zero matches, so `|| echo 0` would append a
  # SECOND line and emit `markers: 0\n0` — invalid JSON. Swallow the status instead.
  markers=$(grep -cE 'ponytail:|TODO|FIXME|HACK|XXX' "$PROJECT_DIR/$f" 2>/dev/null) || true
  case "$markers" in ''|*[!0-9]*) markers=0 ;; esac
  stem="${f##*/}"; stem="${stem%%.*}"
  # Match CONVENTIONAL test names, not a bare substring: a substring check makes a short stem
  # ("m", "e") pair with any test file containing that letter, silently reporting untested code as
  # tested — the one direction where being wrong is dangerous, because nobody re-checks a file the
  # pack says is covered.
  has_test=false
  for cand in "test_${stem}." "${stem}_test." "${stem}.test." "${stem}.spec." "${stem}Test." "test${stem}."; do
    case "$TEST_STEMS" in *"$cand"*) has_test=true; break ;; esac
  done
  score=$((lines * churn))
  ROWS="${ROWS}${score} $(printf '{"path":%s,"lines":%d,"churn":%d,"score":%d,"has_test":%s,"markers":%d,"last_commit":%s}' \
      "$(json_string "$f")" "$lines" "$churn" "$score" "$has_test" "$markers" "$(json_string "$last")")
"
done <<EOF
$SRC_LIST
EOF

# Rank by score desc, then by the row text asc — the second key is what makes ties reproducible.
FILES_JSON="$(printf '%s' "$ROWS" | grep -v '^$' \
  | sort -t' ' -k1,1nr -k2 | head -n "$MAX_FILES" | cut -d' ' -f2- | paste -sd, -)"
emit "$FILES_JSON"
