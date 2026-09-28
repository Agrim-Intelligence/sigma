#!/usr/bin/env bash
# Sigma — discovery scan (read-only, deterministic).
#
# Greps tracked source for mechanical tech-debt signals and emits candidate
# backlog items as JSON. It writes NOTHING — `pipeline.py discover` turns
# candidates into `proposed` goal files (which the loop never runs until a human
# promotes them to `pending`). Two v1 signals, aggregated per file:
# TODO/FIXME/HACK/XXX markers (tech-debt) and skipped/xfail tests (test-gap).
#
# Principles (same as the other read-only collectors, e.g. risk-detect.sh):
#   READ-ONLY & DETERMINISTIC · FAIL-OPEN (no git → empty, exit 0) ·
#   SECRET SAFETY — a candidate carries the marker LOCATION (file:line) + a count,
#   NEVER the comment/marker text (a TODO may contain a secret).
#
# Output: {"schema":"discovery-scan/v1","candidates":[{title,category,source,priority,evidence[]}...]}
# jq-free, bash-3.2-safe, zero-dep.

case "${1:-}" in -h|--help) echo "usage: discovery-scan.sh   (no args; scans \$CLAUDE_PROJECT_DIR or the cwd)"; exit 0 ;; esac
set -uo pipefail
SCHEMA="discovery-scan/v1"

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
append() { local n="$1" cur="${!1}"; if [ -z "$cur" ]; then printf -v "$n" '%s' "$2"; else printf -v "$n" '%s,%s' "$cur" "$2"; fi; }

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
emit_empty() { printf '{"schema":%s,"candidates":[]}\n' "$(json_string "$SCHEMA")"; }

command -v git >/dev/null 2>&1 || { emit_empty; exit 0; }
git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { emit_empty; exit 0; }

# Search tracked source only; exclude the harness, docs, and common vendor dirs.
EXCL=(-- . ':(exclude).sdlc/**' ':(exclude)docs/**' ':(exclude)node_modules/**' ':(exclude)vendor/**' ':(exclude)dist/**' ':(exclude)build/**')

# NOTE: git grep's default engine is POSIX ERE, which has NO `\b` word boundary —
# we bracket markers with line-edges / non-letters instead so they match portably
# without -P (PCRE, which isn't guaranteed to be compiled in).
TODO_RE='(^|[^A-Za-z])(TODO|FIXME|HACK|XXX)([^A-Za-z]|$)'
SKIP_RE='(\.skip\(|it\.skip|describe\.skip|test\.skip|xit\(|xdescribe\(|@pytest\.mark\.skip|@unittest\.skip|xfail|t\.Skip\(|@Ignore|@Disabled)'

CANDS=""

# v1 LIMITATION (parity with the other collectors): git grep -l C-quotes paths
# containing `"`/`\`/control bytes, so a marker in an exotically named file is
# silently DROPPED (fail-safe under-report — never a crash or leak).
scan_category() { # regex category title_fmt priority
  local re="$1" cat="$2" fmt="$3" prio="$4"
  local file row rest ln ev cnt evcount title
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    ev=""; cnt=0; evcount=0
    # Per-file: collect line numbers ONLY (strip the known "file:" prefix, take
    # the leading digits; the match text after is never read into output).
    while IFS= read -r row; do
      rest="${row#"$file":}"; ln="${rest%%:*}"
      case "$ln" in (''|*[!0-9]*) continue ;; esac
      cnt=$((cnt + 1))
      if [ "$evcount" -lt 10 ]; then append ev "$(json_string "$file:$ln")"; evcount=$((evcount + 1)); fi
    done < <(git -C "$PROJECT_DIR" grep -nI -E "$re" -- "$file" 2>/dev/null)
    [ "$cnt" -gt 0 ] || continue
    title="$(printf "$fmt" "$cnt" "$file")"
    append CANDS "$(printf '{"title":%s,"category":%s,"source":"discovery","priority":%s,"count":%s,"evidence":[%s]}' \
      "$(json_string "$title")" "$(json_string "$cat")" "$(json_string "$prio")" "$cnt" "$ev")"
  done < <(git -C "$PROJECT_DIR" grep -lI -E "$re" "${EXCL[@]}" 2>/dev/null | LC_ALL=C sort)
}

scan_category "$TODO_RE" tech-debt "Resolve %s TODO/FIXME marker(s) in %s" low
scan_category "$SKIP_RE" test-gap  "Re-enable %s skipped test(s) in %s"    med

printf '{"schema":%s,"candidates":[%s]}\n' "$(json_string "$SCHEMA")" "$CANDS"
exit 0
