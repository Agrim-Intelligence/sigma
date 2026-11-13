#!/usr/bin/env python3
"""#2160: a project's recorded, time-limited known-flake exception, read by the goal run itself.

The setting is `verify.known_flakes`, a list a PERSON writes (nothing here ever adds an entry). Each entry:
`test` (a test id or a pattern where `*` and `?` are the only wildcards), `tracking` (the issue that tracks the
fix), `approved_by`, `record` (an http(s) URL of the approval), `expires` (YYYY-MM-DD) and `bases` (a nonempty list
of base-branch patterns, same wildcards). The module is pure standard library and has no side effects of its own;
the one process it can start is the solo re-run, and only through a callable its caller hands in.

What it decides, for a FAILED full verify: if EVERY failing or erroring test matches an unexpired entry covering
the goal's base, and EVERY one of them passes when re-run alone on the same head, the verify is
`passed-with-known-flakes`. Anything else stays red. It never skips, quarantines, xfails or loosens a test.

FAILS CLOSED. Each of these returns "not accepted" with a reason, never an exception: the setting is malformed;
no failing id could be read from the output; the failure counts do not agree with the ids read; the run stopped
early; a failing id matches no entry; an entry is expired or does not cover the base; a solo re-run fails, errors
or times out; a setup or collection error that no entry names.

Limits (stated, not hidden): ids are read from pytest's short summary (`FAILED <id>` / `ERROR <id>` lines and its
final count line), and the solo re-run is `<python> -m pytest <id>`; a project whose verify command is not pytest
reads as "no failing id could be read" and stays red. Entry expiry and the base are judged here from the clock and
the work record; they are not tamper-proof (local evidence).
"""
import datetime
import re

STATE = "passed-with-known-flakes"
ENTRY_KEYS = ("test", "tracking", "approved_by", "record", "expires", "bases")
_FAIL_LINE = re.compile(r"^(FAILED|ERROR) (\S+)(?: - .*)?$")
_COUNT = re.compile(r"(\d+) (failed|errors?)\b")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_EARLY_STOP = re.compile(r"(?i)stopping after \d+ failure|^!+ .*interrupted|keyboardinterrupt|^!+ .*stopped")


def read(config):
    """-> (entries, error). Absent setting -> ([], None). Any malformation -> ([], reason): the WHOLE list is
    refused, because a half-read waiver list is not one a person approved."""
    try:
        verify = (config or {}).get("verify")
        if verify is None:
            return [], None
        if not isinstance(verify, dict):
            return [], "verify must be an object"
        raw = verify.get("known_flakes")
        if raw is None:
            return [], None
        if not isinstance(raw, list):
            return [], "verify.known_flakes must be a list"
        entries = []
        for i, item in enumerate(raw):
            where = "verify.known_flakes[%d]" % i
            if not isinstance(item, dict):
                return [], where + " must be an object"
            extra = sorted(set(item) - set(ENTRY_KEYS))
            if extra:
                return [], "%s has unknown key(s): %s" % (where, ", ".join(map(str, extra)))
            for key in ("test", "tracking", "approved_by", "record", "expires"):
                value = item.get(key)
                if not isinstance(value, str) or not value.strip():
                    return [], "%s.%s must be a nonempty string" % (where, key)
            if re.search(r"\s", item["test"]) or item["test"].startswith("-"):
                return [], where + ".test must be one test id, with no whitespace and not starting with `-`"
            if not re.match(r"https?://\S+$", item["record"]):
                return [], where + ".record must be an http(s) URL of the approval"
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["expires"]):
                    raise ValueError
                expires = datetime.date.fromisoformat(item["expires"])
            except ValueError:
                return [], where + ".expires must be a date, YYYY-MM-DD"
            bases = item.get("bases")
            if (not isinstance(bases, list) or not bases
                    or any(not isinstance(b, str) or not b.strip() for b in bases)):
                return [], where + ".bases must be a nonempty list of branch patterns"
            entries.append({"test": item["test"], "tracking": item["tracking"].strip(),
                            "approved_by": item["approved_by"].strip(), "record": item["record"],
                            "expires": item["expires"], "bases": list(bases), "_expires": expires})
        return entries, None
    except Exception as exc:                                   # noqa: BLE001 - unreadable means red
        return [], "verify.known_flakes could not be read: %s" % exc


def integration_branch(config):
    """`work.base` (the integration branch) with a leading `<work.remote>/` removed, as `work.settings` reads it;
    '' when unset."""
    block = (config or {}).get("work") if isinstance(config, dict) else None
    block = block if isinstance(block, dict) else {}
    base, remote = block.get("base"), block.get("remote")
    base = base.strip() if isinstance(base, str) else ""
    remote = remote if isinstance(remote, str) and remote else "origin"      # `work.settings`' default remote
    if base.startswith(remote + "/") and len(base) > len(remote) + 1:
        base = base[len(remote) + 1:]
    return base


def _work_base_raw(config):
    block = (config or {}).get("work") if isinstance(config, dict) else None
    base = block.get("base") if isinstance(block, dict) else None
    return base.strip() if isinstance(base, str) else ""


def default_branch(root, remote="origin"):
    """The remote's default branch as the LOCAL clone knows it (`refs/remotes/<remote>/HEAD`), '' when it cannot be
    read. No network."""
    import subprocess
    try:
        done = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "refs/remotes/%s/HEAD" % remote],
                              capture_output=True, text=True, timeout=20)
        name = done.stdout.strip() if done.returncode == 0 else ""
    except Exception:                                          # noqa: BLE001 - unreadable adds nothing
        return ""
    return name[len(remote) + 1:] if name.startswith(remote + "/") else name


def integration_set(config, base, root=None):
    """The branches no wildcard may cover. `work.base` when it is set; when it is UNSET the goal's own recorded base
    counts as the integration branch (so only an entry naming it exactly covers it); and the remote's default
    branch, when the local clone knows it, always does."""
    found = {integration_branch(config) or base, _work_base_raw(config)}      # both `origin/main` and `main`
    if root:
        block = (config or {}).get("work") if isinstance(config, dict) else None
        remote = block.get("remote") if isinstance(block, dict) else None
        found.add(default_branch(root, remote if isinstance(remote, str) and remote else "origin"))
    return {b for b in found if isinstance(b, str) and b}


def _glob(pattern, text):
    return re.fullmatch("".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern),
                        text, re.S) is not None


def expired(entry, today):
    """Stops applying ON its `expires` date (today >= expires): the conservative reading of an expiry date."""
    return today >= entry["_expires"]


def covers_base(entry, base, integration=None):
    """True when a pattern in `bases` matches `base`. The integration branch (`work.base`) is covered only by a
    pattern that IS that branch's name, never by a wildcard."""
    if not isinstance(base, str) or not base:
        return False
    if integration is not None and not isinstance(integration, (set, frozenset, list, tuple)):
        integration = {integration} if integration else set()
    if integration and base in integration:
        return base in entry["bases"]
    return any(_glob(p, base) for p in entry["bases"])


def active(entries, base, today, integration=None):
    return [e for e in entries if not expired(e, today) and covers_base(e, base, integration)]


def parse_failures(output):
    """-> (ids, reason). `ids` is the ordered, de-duplicated list of ids on pytest's `FAILED`/`ERROR` summary lines;
    `reason` is None, or why the output cannot be trusted to list every failure (then the caller stays red)."""
    text = _ANSI.sub("", output or "")
    ids, lines = [], text.splitlines()
    for line in lines:
        found = _FAIL_LINE.match(line.strip())
        if found and found.group(2) not in ids:
            ids.append(found.group(2))
    if any(_EARLY_STOP.search(line.strip()) for line in lines):
        return ids, "the run stopped early, so the failures listed are not all of them"
    if not ids:
        return ids, "no failing test id could be read from the output"
    last = next((l.strip() for l in reversed(lines) if l.strip()), "")
    final = last.strip("= ").strip()
    counted = None
    if _COUNT.search(final) and re.search(r" in [\d.]+s\b", final):   # the very last line: `=== 1 failed in 1s ===` or `-q`'s bare form
        counted = sum(int(n) for n, _ in _COUNT.findall(final))
    if counted is None:
        return ids, "the output does not end with pytest's final failure count line, so other output may follow the failures"
    if counted != len(ids):
        return ids, "the output counts %d failure(s) but lists %d id(s)" % (counted, len(ids))
    return ids, None


def _summary(output):
    """The `FAILED`/`ERROR` lines and the final count line, for the PR body."""
    lines = [_ANSI.sub("", l).strip() for l in (output or "").splitlines()]
    kept = [l for l in lines if _FAIL_LINE.match(l)]
    final = [l for l in lines if l.startswith("=") and _COUNT.search(l)]
    return (kept + final[-1:])[:200]


def evaluate(output, entries, base, today, run_solo, integration=None):
    """Judge one failed verify. -> dict: `accepted` (bool), `reason` (None or why not), `ids`, `matched`
    ({id: entry test pattern}), `solo` ({id: {"status", "exit", "output"}}). `run_solo(id) -> (status, exit,
    output)` is how the caller re-runs one test alone on the same head; status must be "ok" and exit 0 to pass."""
    result = {"accepted": False, "reason": None, "ids": [], "matched": {}, "solo": {}, "base": base}
    ids, reason = parse_failures(output)
    result["ids"] = ids
    result["summary"] = _summary(output)
    if reason:
        result["reason"] = reason
        return result
    usable = active(entries, base, today, integration)
    if not usable:
        result["reason"] = "no unexpired known-flake entry covers base %r" % (base,)
        return result
    for tid in ids:
        hit = next((e for e in usable if _glob(e["test"], tid)), None)
        if hit is None:
            result["reason"] = "failing test %s matches no active known-flake entry" % tid
            return result
        result["matched"][tid] = hit["test"]
    for tid in ids:
        try:
            status, code, out = run_solo(tid)
        except Exception as exc:                                # noqa: BLE001 - a re-run that blew up is red
            status, code, out = "error", None, "re-run raised: %s" % exc
        result["solo"][tid] = {"status": status, "exit": code, "output": (out or "")[-4000:]}
        if status != "ok" or code != 0:
            result["reason"] = "solo re-run of %s did not pass (%s, exit %s)" % (tid, status, code)
            return result
        if not re.search(r"\b[1-9]\d* passed\b", out or "") or re.search(r"\b\d+ (skipped|xfailed|deselected)\b", out or ""):
            result["reason"] = "solo re-run of %s exited 0 but did not report the test as passed (skipped?)" % tid
            return result
    result["accepted"] = True
    return result


def record_for(entries, matched):
    """The entries behind a verdict, as plain JSON for the evidence and the PR body (no private keys)."""
    seen, out = set(), []
    for pattern in matched.values():
        if pattern in seen:
            continue
        seen.add(pattern)
        for e in entries:
            if e["test"] == pattern:
                out.append({k: e[k] for k in ENTRY_KEYS})
                break
    return out


def evidence_refusal(data, config, base, today=None, root=None):
    """None when `data` (a parsed evidence file) is VALID known-flake evidence for `base` right now, else the
    reason it is not. Re-reads the CURRENT setting: an entry that has since expired, been removed or stopped
    covering the base voids evidence written earlier."""
    today = today or datetime.date.today()
    integration = integration_set(config, base, root if root is not None else data.get("root"))
    if data.get("verify_state") != STATE:
        return "last verify FAILED (exit %s)" % data.get("exit")
    kf = data.get("known_flakes")
    if not isinstance(kf, dict) or kf.get("accepted") is not True or not kf.get("ids"):
        return "known-flake evidence is malformed"
    entries, error = read(config)
    if error:
        return "known flakes cannot be read (%s)" % error
    ev_base = kf.get("base")
    if not ev_base or (base is not None and base != ev_base):
        return "known-flake evidence was recorded for base %r, not %r" % (ev_base, base)
    if base is None:
        return "known-flake evidence needs the goal's base to be judged"
    usable = active(entries, base, today, integration)
    for tid, pattern in (kf.get("matched") or {}).items():
        if not any(e["test"] == pattern and _glob(e["test"], tid) for e in usable):
            return "the known-flake entry for %s has expired or no longer covers base %r" % (tid, base)
    if set((kf.get("matched") or {})) != set(kf["ids"]):
        return "known-flake evidence does not name a match for every failing test"
    for tid in kf["ids"]:
        solo = (kf.get("solo") or {}).get(tid) or {}
        if solo.get("status") != "ok" or solo.get("exit") != 0:
            return "known-flake evidence has no passing solo re-run for %s" % tid
    return None


def pr_section(data):
    """The PR-body section `work.py pr` writes for known-flake evidence: the summary, the failing ids and the
    solo re-run output. '' when `data` is not known-flake evidence."""
    kf = data.get("known_flakes") if isinstance(data, dict) else None
    if not isinstance(kf, dict) or data.get("verify_state") != STATE:
        return ""
    lines = ["## Verify passed with known flakes", "",
             "The full verify was red. Every failing test is on the project's recorded known-flake list, and "
             "each passed when re-run alone on this head. Base: `%s`." % kf.get("base"), "",
             "Verify summary:", "", "```", *[str(x)[:300] for x in (kf.get("summary") or [])], "```", "",
             "Failing tests and the entry that covers each:", ""]
    for tid in kf.get("ids") or []:
        entry = next((e for e in kf.get("entries") or [] if e.get("test") == (kf.get("matched") or {}).get(tid)), {})
        lines.append("- `%s` (entry `%s`, tracking %s, approved by %s, record %s, expires %s)"
                     % (tid, entry.get("test"), entry.get("tracking"), entry.get("approved_by"),
                        entry.get("record"), entry.get("expires")))
    lines += ["", "Solo re-run output:", ""]
    for tid in kf.get("ids") or []:
        solo = (kf.get("solo") or {}).get(tid) or {}
        lines += ["`%s`: %s, exit %s" % (tid, solo.get("status"), solo.get("exit")), "", "```",
                  (solo.get("output") or "").strip()[-1500:], "```", ""]
    return "\n" + "\n".join(lines).rstrip() + "\n"
