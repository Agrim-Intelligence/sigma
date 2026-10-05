"""Mutation-testing gate scoped to the diff (issue #1935) -- kill rate, not coverage.

WHY KILL RATE. Line coverage says a line RAN. It does not say anything was CHECKED. Given
`def is_valid_age(age): return 0 <= age <= 120` and the test `assert is_valid_age(25) == True`,
coverage is 100% -- and `return 0 <= age <= 1200`, `return 0 < age <= 120` and `return True` all
still pass it. Coverage is the metric an agent can already game; kill rate is the one it cannot.

THE TOOL IS NOT INSTALLED HERE, AND THIS GATE DOES NOT INSTALL IT. Measured 2026-09-01: mutmut,
cosmic_ray and mutpy are all absent, and mutmut is not on PATH. `coverage` 7.10.7 and `pytest-cov`
7.1.0 ARE present, which is what the double-scoping needs. .sdlc/config.json's own `_command` note
already refuses a pip install on the verify path, and every reason it gives applies here unchanged:
build isolation fetches setuptools/wheel over the network on every invocation, so a network blip
would park every goal, and a bare pip install errors on any PEP 668 externally-managed interpreter
without --break-system-packages or a venv. Neither belongs in a must-never-flake, per-goal gate.

SO A MISSING TOOL RESOLVES TO `absent`, WHICH IS THE STATE THIS ISSUE ALREADY SPECIFIES. #1935 asks
for `mutation: not measured` to be "neither fail-open (silent pass, the exact ABSENT != PASS bug this
product exists to surface) nor fail-closed (tool outage = org-wide merge freeze)". A missing tool, a
crash and a timeout are the same fact -- nothing was measured -- and all three produce `absent`.
That is the designed behaviour exercised on day one, not a workaround for the tool being missing.
On this machine, today, the honest verdict is `absent` and not a kill-rate number.

DOUBLE-SCOPED, OR IT GETS SWITCHED OFF. Mutants x full-suite-run is hours per PR on a repo this
size, and a two-hour gate is disabled within a week -- leaving a decoration rather than a check. Both
dimensions are scoped: mutate only lines the diff CHANGED, and run only the tests that COVER those
lines. `paths_to_mutate` and `--use-coverage` are how mutmut is told each half.

EQUIVALENT MUTANTS ARE WAIVED WITH A REASON, NEVER SILENTLY. Some survivors do not change behaviour
(`x < 10` -> `x <= 9` on integers). A hard "kill 80% or no merge" rule eventually blocks a correct
change, and the rational response is to lower the threshold or switch the gate off. A waiver is
recorded as a witness (#1934) carrying its reason, so it becomes provenance -- who waived what, and
why -- rather than a line in a config file nobody reads.

EVERY KILLED MUTANT IS A WITNESS. Killing a mutant IS an existing test failing for the right reason,
which is exactly the evidence a behaviour-preserving refactor can never produce through red-first
TDD. That is the shared vocabulary #1934 and this module were designed against together; see
docs/superpowers/specs/2026-09-01-test-trust-shared-vocabulary-1934-1935.md.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import time

#: #1933/#1934's vocabulary, reused. `absent` IS this issue's "not measured".
VERIFIED, UNVERIFIED, ABSENT = "verified", "unverified", "absent"

#: Default minimum kill rate. Deliberately a DEFAULT and not a constant: #1935 requires the
#: threshold be configurable and the measured rate emitted "as a number, not a boolean".
DEFAULT_MIN_KILL_RATE = 0.8

#: Seconds before the run is abandoned as `absent`. A gate that hangs is a gate someone removes.
DEFAULT_TIMEOUT = 900

_TOOL = "mutmut"


def tool_available(which=None, tool=None):
    """True iff the mutation tool is actually runnable. Checked rather than assumed -- the whole
    `absent` path exists because on most machines this is False."""
    return (which or shutil.which)(tool or _TOOL) is not None


def changed_lines(diff_text):
    """{path: {line numbers ADDED or MODIFIED}} from a unified diff -- the first scope dimension.

    Reads the hunk headers' NEW-file side (`@@ -a,b +c,d @@`) and walks the body, counting only `+`
    and context lines against the new file's numbering. Deletions do not advance it, because a line
    that no longer exists cannot be mutated."""
    out, path, new_line = {}, None, None
    for line in (diff_text or "").splitlines():
        if line.startswith("diff --git "):
            path, new_line = line.split(" b/", 1)[-1], None
            continue
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if m:
            new_line = int(m.group(1))
            continue
        if path is None or new_line is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(path, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            pass                                # removed: the new file never had this line
        elif line.startswith(" "):
            new_line += 1
    return out


#: mutmut prints one progress line per mutant, redrawn in place with carriage returns, and the LAST
#: redraw is the tally. In its default output each count FOLLOWS an emoji; with --simple-output it
#: follows a plain word. Measured on mutmut 2.5.1 (tests/fixtures/mutmut/). The emoji are written as
#: \N escapes so this source stays plain ASCII and cannot be mis-encoded again.
_LABELS = {
    "killed": r"(?:\N{PARTY POPPER}|KILLED)",
    "timeout": r"(?:\N{ALARM CLOCK}|TIMEOUT)",
    "suspicious": r"(?:\N{THINKING FACE}|SUSPICIOUS)",
    "survived": r"(?:\N{SLIGHTLY FROWNING FACE}|SURVIVED)",
    "skipped": r"(?:\N{SPEAKER WITH CANCELLATION STROKE}|SKIPPED)",
}


def _parse_counts(output):
    """{killed, timeout, suspicious, survived, skipped} from the LAST tally mutmut printed.

    A tally missing killed OR survived is not a tally: {} (which `run` turns into `absent`), never a
    rate computed from half of it. The legacy lowercase `N killed, M survived` wording is accepted ONLY
    when neither mutmut label appears at all, so a truncated real tally cannot fall through to it: its
    `4/4  KILLED 3` would otherwise read as 4 killed (the bug this module had)."""
    text = output or ""
    counts = {}
    for name, label in _LABELS.items():
        found = re.findall(label + r"\s+(\d+)", text)
        if found:
            counts[name] = int(found[-1])
    if counts:
        return counts if "killed" in counts and "survived" in counts else {}
    legacy = {}
    for name in ("killed", "survived"):
        m = re.search(r"(\d+) " + name + r"\b", text)
        if m:
            legacy[name] = int(m.group(1))
    return legacy if len(legacy) == 2 else {}


def _parse_results(output):
    """(killed, survived) from mutmut's own tally. A tally the parser cannot read is (0, 0), which
    `run` turns into `absent`, never into a rate."""
    c = _parse_counts(output)
    return c.get("killed", 0), c.get("survived", 0)


def parse_survivor_ids(results_text):
    """Mutant ids under the `Survived` heading of `mutmut results` (ranges like `1-4, 7` expanded)."""
    ids, in_survived = [], False
    for line in (results_text or "").splitlines():
        head = re.match(r"^(Timed out|Suspicious|Survived|Skipped)\b", line)
        if head:
            in_survived = head.group(1) == "Survived"
            continue
        if in_survived and re.fullmatch(r"[\d\s,\-]+", line) and line.strip():
            for part in line.split(","):
                lo, _, hi = part.strip().partition("-")
                if lo.isdigit():
                    ids.extend(range(int(lo), int(hi or lo) + 1))
    return ids


def run(root, targets, min_kill_rate=DEFAULT_MIN_KILL_RATE, timeout=DEFAULT_TIMEOUT,
        run_cmd=None, which=None, tool=None, runner=None, use_coverage=True):
    """-> {"verdict", "kill_rate", "killed", "survived", "reason", "ms", "counts", "code"}.

    `code` names why nothing was measured (no-targets, tool-missing, timeout, crash, no-mutants) and
    is None when a rate was; callers branch on it, never on the wording of `reason`.

    THREE STATES, AND `absent` IS NOT A PASS:
      verified    -- measured, kill_rate >= min_kill_rate
      unverified  -- measured, kill_rate < min_kill_rate
      absent      -- NOTHING WAS MEASURED: no tool, a crash, a timeout, or nothing to mutate.

    `kill_rate` is emitted as a NUMBER whenever it was measured, per #1935's "the measured rate is
    emitted as a number, not a boolean" -- and is None, never 0.0, when it was not. A fabricated
    zero would read as "every mutant survived", which is the opposite of "we did not look".

    `kill_rate` is killed / (killed + survived). Timed-out, suspicious and skipped mutants are
    reported in `counts` and are NOT in the denominator, so a module with many of them is read with
    `counts` beside the rate.

    `tool` is the mutmut executable (a throwaway virtualenv's, never one on the verify path),
    `runner` the shell command mutmut runs per mutant (scoped test files, never the full suite).
    """
    started = time.perf_counter()

    def out(verdict, reason, killed=0, survived=0, rate=None, counts=None, code=None):
        return {"verdict": verdict, "kill_rate": rate, "killed": killed, "survived": survived,
                "reason": reason, "ms": int((time.perf_counter() - started) * 1000),
                "counts": counts or {}, "code": code}

    if not targets:
        return out(ABSENT, "no changed lines to mutate", code="no-targets")
    exe = tool or _TOOL
    if not tool_available(which, exe):
        return out(ABSENT, "%s is not installed -- `pip install %s` to enable this gate"
                            % (_TOOL, _TOOL), code="tool-missing")
    argv = [exe, "run", "--paths-to-mutate", ",".join(sorted(targets)), "--simple-output"]
    if use_coverage:
        argv.append("--use-coverage")
    if runner:
        argv += ["--runner", runner]
    try:
        proc = (run_cmd or _default_run)(root, argv, timeout)
    except subprocess.TimeoutExpired:
        return out(ABSENT, "%s timed out after %ss -- not measured, deliberately neither a pass "
                            "nor a merge freeze" % (_TOOL, timeout), code="timeout")
    except Exception as exc:                # noqa: BLE001 - a tool outage is `absent`, never a pass
        return out(ABSENT, "%s could not run (%s) -- not measured" % (_TOOL, exc), code="crash")
    code, text = proc
    counts = _parse_counts(text)
    killed, survived = counts.get("killed", 0), counts.get("survived", 0)
    total = killed + survived
    if total == 0:
        tail = " | ".join(l.strip() for l in text.strip().splitlines()[-2:])[:200]
        return out(ABSENT, "%s produced no mutants (exit %s: %s) -- not measured"
                            % (_TOOL, code, tail), counts=counts, code="no-mutants")
    rate = round(killed / total, 4)
    if rate >= min_kill_rate:
        return out(VERIFIED, "kill rate %.2f >= %.2f" % (rate, min_kill_rate),
                    killed, survived, rate, counts)
    return out(UNVERIFIED, "kill rate %.2f < %.2f -- %d mutant(s) survived"
                            % (rate, min_kill_rate, survived), killed, survived, rate, counts)


def _default_run(root, argv, timeout):
    """Run in its own process group and kill the WHOLE group on overrun: mutmut applies a mutant to
    the file in place and spawns the test runner through a shell, so killing only the direct child
    would leave a mutated source file and an orphaned test run behind."""
    p = subprocess.Popen(argv, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        text, _ = p.communicate(timeout=timeout)
    except BaseException:                   # a timeout, Ctrl-C or SIGTERM: never leave the group running
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (OSError, AttributeError):   # no process groups on this platform: kill what we can
            p.kill()
        p.communicate()
        raise
    return (p.returncode, text or "")


def waive(sdlc_dir, goal, mutant, reason, witness_mod):
    """Mark one surviving mutant `equivalent`, with a MANDATORY reason.

    Recorded as a witness rather than a config entry so the waiver is QUERYABLE -- who waived what
    and why -- which is what turns it into provenance instead of a silent exemption. A waiver
    without a reason is refused outright: the reason IS the artefact."""
    if not reason or not str(reason).strip():
        return None
    return witness_mod.record(sdlc_dir, goal, str(mutant), "other", None,
                               detail="equivalent-mutant waiver: %s" % reason)
