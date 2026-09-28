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
import re
import shutil
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


def tool_available(which=None):
    """True iff the mutation tool is actually runnable. Checked rather than assumed -- the whole
    `absent` path exists because on most machines this is False."""
    return (which or shutil.which)(_TOOL) is not None


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


def _parse_results(output):
    """(killed, survived) from mutmut's own summary. Tolerant of both its emoji and plain forms."""
    killed = survived = 0
    for pat, add in ((r"(\d+)\s*(?:üéâ|killed)", "k"), (r"(\d+)\s*(?:üôÅ|survived)", "s")):
        m = re.search(pat, output or "", re.I)
        if m:
            if add == "k":
                killed = int(m.group(1))
            else:
                survived = int(m.group(1))
    return killed, survived


def run(root, targets, min_kill_rate=DEFAULT_MIN_KILL_RATE, timeout=DEFAULT_TIMEOUT,
        run_cmd=None, which=None):
    """-> {"verdict", "kill_rate", "killed", "survived", "reason", "ms"}.

    THREE STATES, AND `absent` IS NOT A PASS:
      verified    -- measured, kill_rate >= min_kill_rate
      unverified  -- measured, kill_rate < min_kill_rate
      absent      -- NOTHING WAS MEASURED: no tool, a crash, a timeout, or nothing to mutate.

    `kill_rate` is emitted as a NUMBER whenever it was measured, per #1935's "the measured rate is
    emitted as a number, not a boolean" -- and is None, never 0.0, when it was not. A fabricated
    zero would read as "every mutant survived", which is the opposite of "we did not look".
    """
    started = time.perf_counter()

    def out(verdict, reason, killed=0, survived=0, rate=None):
        return {"verdict": verdict, "kill_rate": rate, "killed": killed, "survived": survived,
                "reason": reason, "ms": int((time.perf_counter() - started) * 1000)}

    if not targets:
        return out(ABSENT, "no changed lines to mutate")
    if not tool_available(which):
        return out(ABSENT, "%s is not installed -- `pip install %s` to enable this gate"
                            % (_TOOL, _TOOL))
    argv = [_TOOL, "run", "--paths-to-mutate", ",".join(sorted(targets)), "--use-coverage"]
    try:
        proc = (run_cmd or _default_run)(root, argv, timeout)
    except subprocess.TimeoutExpired:
        return out(ABSENT, "%s timed out after %ss -- not measured, deliberately neither a pass "
                            "nor a merge freeze" % (_TOOL, timeout))
    except Exception as exc:                # noqa: BLE001 - a tool outage is `absent`, never a pass
        return out(ABSENT, "%s could not run (%s) -- not measured" % (_TOOL, exc))
    code, text = proc
    killed, survived = _parse_results(text)
    total = killed + survived
    if total == 0:
        return out(ABSENT, "%s produced no mutants (exit %s) -- not measured" % (_TOOL, code))
    rate = round(killed / total, 4)
    if rate >= min_kill_rate:
        return out(VERIFIED, "kill rate %.2f >= %.2f" % (rate, min_kill_rate),
                    killed, survived, rate)
    return out(UNVERIFIED, "kill rate %.2f < %.2f -- %d mutant(s) survived"
                            % (rate, min_kill_rate, survived), killed, survived, rate)


def _default_run(root, argv, timeout):
    p = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=timeout)
    return (p.returncode, (p.stdout or "") + (p.stderr or ""))


def waive(sdlc_dir, goal, mutant, reason, witness_mod):
    """Mark one surviving mutant `equivalent`, with a MANDATORY reason.

    Recorded as a witness rather than a config entry so the waiver is QUERYABLE -- who waived what
    and why -- which is what turns it into provenance instead of a silent exemption. A waiver
    without a reason is refused outright: the reason IS the artefact."""
    if not reason or not str(reason).strip():
        return None
    return witness_mod.record(sdlc_dir, goal, str(mutant), "other", None,
                               detail="equivalent-mutant waiver: %s" % reason)
