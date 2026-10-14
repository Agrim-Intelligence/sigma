#!/usr/bin/env python3
"""Resolve WHICH mechanism gives this host an author-blind reviewer (issue #1983).

WHY THIS EXISTS. `sigma-review` opened by ASSERTING "you did not write this code" -- true when
/sigma-loop dispatches the gate, false whenever the authoring session runs /sigma-review itself.
An unenforceable assertion in prose is exactly what that skill's own evidence rule bans.

WHERE THE BOUNDARY IS. `breaker.py`'s `no_channel()` already states the half this module supplies:
"that the ORCHESTRATOR spawns the breaker, never the maker -- is a spawn topology this function
cannot observe from a string ... and must be enforced at the dispatch site." This IS that site. It
does not re-implement breaker's leak checks; it calls them (see `check`).

HOST-AGNOSTIC BY BRANCHING, NOT BY LOWEST COMMON DENOMINATOR. Each host reaches the SAME property --
a model context that never saw the author's reasoning reads the brief -- by its own best route. No
branch is a downgrade. `inline` is the only degradation, and it is per-MACHINE, never per-host: a
Claude machine with no marker degrades exactly as a Cursor machine without `cursor-agent` does.

AUTO-DETECTION uses observed session markers only. Claude's markers retain precedence;
`CODEX_SESSION_ID`/`CODEX_THREAD_ID` identify Codex on a real Codex host. Cursor still needs
`review.host` because no marker has been verified there. A missing CLI is named, never guessed.

Fail-open: every failure resolves `inline` WITH A STATED REASON and exit 0 -- a broken resolver must
not stop a review. `check` is the opposite and fails CLOSED, because a leak guard that passes on
error is decoration. Zero deps.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time

#: What the SKILL prose branches on.
MECHANISMS = ("subagent", "process", "command", "inline")

#: Legal `review.host` values. An unknown value is a loud `inline`, never a guess.
HOSTS = ("auto", "claude", "cursor", "codex", "command", "inline")

#: Per-host invocation, and whether that branch has been RUN end to end BY THIS PROJECT -- NOT
#: whether this machine can run it (that is what the PATH check answers, separately). The two are
#: different questions and conflating them would let an unproven branch look proven merely because
#: its binary is installed. `verified` remains False for Codex until an entire review gate, including
#: its brief and verdict, has been exercised on the actual host.
_HOST_COMMANDS = {
    "cursor": (["cursor-agent", "-p", "--output-format", "text"], False),
    "codex": (["codex", "exec", "--sandbox", "read-only", "--ephemeral", "-"], False),
}


def _load(name):
    """Import a sibling script by path -- the kit's standard zero-install module loader. Legal here
    only because `breaker.py` lives in this same skill; a DIFFERENT skill must shell out to the CLI
    instead (north-star Architecture Rule 3)."""
    spec = importlib.util.spec_from_file_location(
        name, pathlib.Path(__file__).resolve().parent / (name + ".py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _config(sdlc_dir):
    try:
        return json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except Exception:               # noqa: BLE001 - fail-open by design; see module docstring
        return {}


def _out(mechanism, host, reason, command=None, verified=True, timeout=900):
    got = {"mechanism": mechanism, "host": host, "verified": verified, "reason": reason,
           "timeout_seconds": timeout}
    if command is not None:
        got["command"] = command
    return got


shell_policy = _load("shell_policy")      # #707


def resolve(sdlc_dir, env=None, which=None):
    """-> the dict the SKILL prose branches on. Never raises; never returns an unknown mechanism."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    cfg = _config(sdlc_dir)
    review = cfg.get("review") if isinstance(cfg, dict) else None
    # EVERY read below is coerced. `_config` guarded only the read+parse, so a config that WAS valid
    # JSON but the wrong shape still crashed the gate -- and a list-valued `command` is not
    # contrived, since this function's own `process` output carries `command` as a list.
    review = review if isinstance(review, dict) else {}
    host = str(review.get("host") or "auto").strip()
    codex_here = (env.get("SIGMA_HOST") == "codex" or
                  (env.get("SIGMA_HOST") != "claude" and
                   (env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")) and
                   not (env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"))))
    overrides = review.get("host_overrides")
    if codex_here and isinstance(overrides, dict) and "codex" in overrides:
        host = str(overrides["codex"]).strip()
    command = str(review.get("command") or "").strip()
    try:
        timeout = int(review.get("timeout_seconds") or 900)
    except (TypeError, ValueError):
        timeout = 900

    if review.get("independent") is False:
        # The loop already honours this flag; without it here, a direct /sigma-review told the agent
        # to dispatch while doctor reported "a fresh reviewer is not spawned" for the same config.
        return _out("inline", "other", "review.independent is false (operator opted out)",
                    timeout=timeout)
    if host not in HOSTS:
        return _out("inline", "other", "unknown review.host %r; expected one of %s"
                    % (host, ", ".join(HOSTS)), timeout=timeout)
    if host == "inline":
        return _out("inline", "other", "review.host is inline (operator's explicit choice)",
                    timeout=timeout)
    if host == "command" or (host == "auto" and command):
        if not command:
            return _out("inline", "other", "review.host is command but review.command is empty",
                        timeout=timeout)
        # Unverified for the same reason codex is: no review has been run through an operator
        # command end to end by this project. `verified` is about THIS PROJECT's evidence.
        return _out("command", "other", "review.command configured", command=command,
                    verified=False, timeout=timeout)
    if host in _HOST_COMMANDS:
        argv, verified = _HOST_COMMANDS[host]
        if not which(argv[0]):
            return _out("inline", host,
                        "review.host is %s but %s is not on PATH" % (host, argv[0]),
                        timeout=timeout)
        return _out("process", host, "%s resolved on PATH" % argv[0],
                    command=list(argv), verified=verified, timeout=timeout)
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"):
        return _out("subagent", "claude", "host dispatches its own subagents", timeout=timeout)
    if host == "auto" and (env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")):
        argv, verified = _HOST_COMMANDS["codex"]
        if not which(argv[0]):
            return _out("inline", "codex", "Codex session detected but codex is not on PATH",
                        timeout=timeout)
        return _out("process", "codex", "Codex session marker and codex CLI resolved",
                    command=list(argv), verified=verified, timeout=timeout)
    if host == "claude":
        # Symmetric with the PATH check above: an explicit host that cannot actually be reached
        # degrades loudly. Handing back `subagent` where none can be spawned produces an inline
        # self-review stamped "dispatched subagent", which is the failure this module prevents.
        return _out("inline", "claude",
                    "review.host is claude but no CLAUDECODE / CLAUDE_CODE_SESSION_ID marker is set",
                    timeout=timeout)
    return _out("inline", "other",
                "no host marker in the environment and no review.command configured",
                timeout=timeout)


#: breaker's finding for implementation content, which does NOT apply to a reviewer. See `check`.
_IMPLEMENTATION_FINDING = "implementation content reached the brief"


def check(brief_path, scratch_paths=()):
    """-> (ok, findings). The dispatch-site half of breaker.py's guarantee, for REVIEWS.

    `no_channel`'s own docstring names this as the requirement it cannot check from a string, because
    a string cannot reveal who spawned whom. So the check lives here, at the site that actually
    spawns the reviewer -- and it CALLS breaker rather than cloning its patterns, so a tightening
    of the shared-scratch rule is inherited rather than missed.

    BUT A REVIEWER IS NOT A BREAKER, AND ONE OF BREAKER'S TWO FINDINGS MUST NOT BE APPLIED HERE.
    A breaker must never see the implementation -- that is its whole isolation property. A code
    reviewer MUST see the diff: it is the artifact under review. `_LEAK` also matches any line
    starting with `-` (breaker.brief() switched its own bullets to `*` for exactly this reason) and
    any prose containing `def `/`class `/`import `/`return `, so a markdown review brief trips it on
    formatting alone. Measured, not assumed: the real #1983 brief was flagged on its first run.
    Carrying that through would be the false positive breaker's own comment warns about -- "exactly
    the sort of false positive that gets a real guard switched off".

    What DOES bind a reviewer is the shared-scratch rule: a brief naming a scratch file the maker can
    write is a maker->reviewer channel whatever the artifact is. The other half of the reviewer's
    isolation -- that the maker's reasoning never arrives -- is structural in `review_context.py`,
    which has no field to carry it, and is not something a string check could confirm anyway.

    Fails CLOSED, unlike `resolve`: a brief that could not even be read has not been checked, and a
    leak guard that reports ok when it checked nothing is decoration."""
    try:
        text = pathlib.Path(brief_path).read_text(encoding="utf-8")
    except OSError as exc:
        return (False, ["brief unreadable, so nothing was checked: %s" % exc])
    return _check_brief_text(text, scratch_paths)


def _check_brief_text(text, scratch_paths):
    """Check the bytes that will actually be dispatched, without a second file read."""
    _, findings = _load("breaker").no_channel(text, scratch_paths)
    findings = [f for f in findings if f != _IMPLEMENTATION_FINDING]
    return (not findings, findings)


def _stop_review_process(proc):
    """Kill the dedicated review process group, then reap its leader on every failure path."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        proc.kill()
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        # A process that deliberately detached from our group may still hold a pipe. Close our
        # descriptors rather than letting cleanup itself wait forever; the direct child is reaped.
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        proc.kill()
        proc.wait(timeout=2)


#: A review verdict is a line that is NOTHING but the verdict. Prose such as "Verdict: approve would be
#: premature" ahead of a final `VERDICT: block` must not count (the checker could flip its own block).
_VERDICT_LINE = re.compile(
    r"(?im)^[\s*_`]*verdict[\s*_`]*:[\s*_`]*(approve|approved|block|blocked|unblock|unblocked)[\s*_`.]*$")
_PLAN_SHA_LINE = re.compile(r"(?m)^Plan sha256: ([0-9a-f]{64})\s*$")

#: Phases whose approving verdict needs a run receipt (the names `phase_gate.record_verdict` uses).
RECEIPT_PHASES = ("plan_review", "review")


def parse_verdicts(output):
    """-> the set of normalised standalone verdicts (approve|block|unblock) found in `output`."""
    return {"approve" if m.startswith("approv") else "unblock" if m.startswith("unblock") else "block"
            for m in (x.group(1).lower() for x in _VERDICT_LINE.finditer(output))}


def lone_verdict(output):
    """The ONE verdict `output` prints, or None for none or for conflicting ones. Never picks among
    several: a reviewer that prints both `approve` and `block` has not approved."""
    found = parse_verdicts(output)
    return next(iter(found)) if len(found) == 1 else None


#: Phase -> the `# Independent review brief - <name>` header a brief for it carries.
_BRIEF_NAME = {"plan_review": "plan-review", "review": "code-review"}
_BRIEF_HEADER = re.compile(r"(?m)\A# Independent review brief - (\S+)")
#: Ceilings for the code-review key. Past either, the checkout cannot be bound cheaply, so the run
#: FAILS (`content_unbound`, naming which) rather than approving something unbound.
_MAX_UNTRACKED = 5000
_KEY_SECONDS = 120.0


def _content_key(sdlc_dir, goal):
    """-> (key, cause). `key` is the sha256 of the goal's checkout as a code reviewer sees it -- HEAD
    (or "unborn"), the content of every tracked path that differs from it, STREAMED into the hash (never held in memory), and every
    untracked non-ignored path: a file's bytes, a symlink's target string, a nested repo's HEAD --
    `.sdlc` excluded (the loop's own state, including this receipt, must not invalidate itself).
    `key` is None, with `cause` naming why in words a person can act on, when it cannot be computed;
    both callers FAIL CLOSED on that. Not covered: ignored files, anything under `.sdlc`."""
    try:                       # the work record, read directly: importing work.py per call costs ~16 MB
        p = pathlib.Path(str(goal))
        rec = json.loads((pathlib.Path(sdlc_dir) / "state" / "work" /
                          ("%s.json" % (p.stem if p.suffix == ".md" else str(goal)))).read_text()) or {}
        rec = rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):               # no record means the project root, below
        rec = {}
    cwd = str(rec.get("worktree") or pathlib.Path(sdlc_dir).resolve().parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    spec = ["--", ".", ":(exclude).sdlc"]
    deadline = time.monotonic() + _KEY_SECONDS

    def git(*args):
        got = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=max(1.0, _KEY_SECONDS), env=env)
        return got.returncode, got.stdout

    def hash_path(digest, name):
        """Fold one path's CONTENT into the digest, streaming: a file's bytes (plus its exec bit), a
        symlink's target string, a nested repository's HEAD and dirty state, or `gone` if absent."""
        path = os.path.join(cwd, os.fsdecode(name))
        digest.update(b"\0" + name + b"\0")
        try:
            if os.path.islink(path):
                digest.update(b"L" + os.fsencode(os.readlink(path)))
            elif os.path.isdir(path):           # a nested repository / submodule
                code, nested = git("-C", path, "rev-parse", "--verify", "-q", "HEAD")
                digest.update(b"R" + (nested.strip() if code == 0 else b"unborn"))
                digest.update(hashlib.sha256(git("-C", path, "status", "--porcelain=v1", "-z")[1]).digest())
            elif os.path.exists(path):
                digest.update(b"X" if os.stat(path).st_mode & 0o111 else b"F")
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        if time.monotonic() > deadline:
                            raise TimeoutError
                        digest.update(chunk)
            else:
                digest.update(b"gone")
        except TimeoutError:
            raise
        except OSError as exc:
            raise OSError("an untracked or changed path cannot be read (%r: %s)" % (os.fsdecode(name), exc.strerror))
    try:
        if git("rev-parse", "--is-inside-work-tree")[0]:
            return None, "this directory is not a git checkout"
        code, head = git("rev-parse", "--verify", "-q", "HEAD")
        digest = hashlib.sha256((head.strip() if code == 0 else b"unborn") + b"\0")
        # Tracked changes: git names them (cheap, no blob held in memory -- `git diff --binary` of a
        # rewritten 100 MB file held ~800 MB in git itself), and Python streams each file's bytes.
        if code == 0:
            code, listing = git("diff-index", "-z", "--name-only", "--no-renames", "HEAD", *spec)
        else:
            code, listing = git("ls-files", "-z", *spec)
        if code:
            return None, "git could not list this checkout's tracked changes"
        changed = sorted(n for n in listing.split(b"\0") if n)
        code, listing = git("ls-files", "--others", "--exclude-standard", "-z", *spec)
        if code:
            return None, "git could not list this checkout's untracked files"
        names = sorted(n for n in listing.split(b"\0") if n)
        if len(names) > _MAX_UNTRACKED:
            return None, ("there are %d untracked files (limit %d): commit, remove or git-ignore build "
                          "output" % (len(names), _MAX_UNTRACKED))
        digest.update(b"T")
        for name in changed:
            hash_path(digest, name)
        digest.update(b"U")
        for name in names:
            hash_path(digest, name)
    except TimeoutError:
        return None, "diff too large or slow to hash within %ds: commit or stash the large change, or split it" % _KEY_SECONDS
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc) if isinstance(exc, OSError) and exc.args else "git could not be run here (%s)" % type(exc).__name__
    return digest.hexdigest(), None


def _receipt_path(sdlc_dir, goal, phase):
    if phase not in RECEIPT_PHASES:
        raise ValueError("phase must be one of %s" % ", ".join(RECEIPT_PHASES))
    p = pathlib.Path(str(goal))
    goal_stem = p.stem if p.suffix == ".md" else str(goal)
    bad = _load("state").unsafe_goal_reason(goal_stem)
    if bad:
        raise ValueError("unsafe goal %r for the reviewer run receipt: %s" % (goal, bad))
    return pathlib.Path(sdlc_dir) / "state" / "reviewer-runs" / ("%s.%s.json" % (goal_stem, phase))


def _write_receipt(sdlc_dir, goal, phase, **fields):
    """ONE file per goal+phase, atomically replaced: the LATEST run's outcome is the only one that
    counts, so a later success clears a failure and a later failure overwrites a success. Bounded by
    construction (no log to prune); `work.py finish` removes a goal's receipts with its record."""
    path = _receipt_path(sdlc_dir, goal, phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields.update(at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), goal=str(goal), phase=phase)
    fd, tmp = tempfile.mkstemp(prefix=".run-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(fields, handle, sort_keys=True); handle.write("\n"); handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def clear_receipts(sdlc_dir, goal):
    for phase in RECEIPT_PHASES:
        try:
            _receipt_path(sdlc_dir, goal, phase).unlink(missing_ok=True)
        except (ValueError, OSError):
            pass


def _config_state(sdlc_dir):
    """-> (cfg_dict_or_None, unreadable). A missing config is the defaults; a damaged one is not."""
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        return None, True
    return (cfg, False) if isinstance(cfg, dict) else (None, True)


def _independent_on(cfg):
    review = cfg.get("review") if isinstance(cfg, dict) else None
    return not (isinstance(review, dict) and review.get("independent") is False)


def configured_reviewer_unavailable(cfg, route):
    """True when `route` is inline although the operator CONFIGURED a reviewer (any `review.host` other
    than `auto`/`inline`, or `review.command` under `auto`) -- the host's binary is missing, the command
    empty, the host unknown, no session marker. Decided from CONFIG, not from a flag `resolve` could
    forget to set. `host: inline` (the operator's explicit choice) and a machine with nothing configured
    are not this."""
    if route.get("mechanism") in ("process", "command", "subagent"):
        return False
    review = cfg.get("review") if isinstance(cfg, dict) else None
    review = review if isinstance(review, dict) else {}
    host = str(review.get("host") or "auto").strip()
    env = os.environ
    codex_here = (env.get("SIGMA_HOST") == "codex" or
                  (env.get("SIGMA_HOST") != "claude" and
                   (env.get("CODEX_SESSION_ID") or env.get("CODEX_THREAD_ID")) and
                   not (env.get("CLAUDECODE") or env.get("CLAUDE_CODE_SESSION_ID"))))
    overrides = review.get("host_overrides")
    if codex_here and isinstance(overrides, dict) and "codex" in overrides:   # as resolve() reads it
        host = str(overrides["codex"]).strip()
    return host != "inline" and (host != "auto" or bool(str(review.get("command") or "").strip()))


def _read_receipt(sdlc_dir, goal, phase):
    """-> ("missing", None) | ("unreadable", why) | ("ok", dict) -- `ok` meaning a readable object,
    whatever its outcome."""
    try:
        got = json.loads(_receipt_path(sdlc_dir, goal, phase).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError) as exc:
        return "unreadable", str(exc)
    return ("ok", got) if isinstance(got, dict) else ("unreadable", "not a JSON object")


#: Failure kinds that are NOT the reviewer failing: the checkout could not be bound to the review, or
#: moved under it. The lever is to fix that and re-run, not to park.
_CHECKOUT_KINDS = ("content_unbound", "content_changed_during_run")


def failed_receipt(sdlc_dir, goal, phase):
    """-> (kind, detail) of a recorded run that did not finish OK, else None. Ignored when the operator
    opted out (`review.independent: false`). An unreadable receipt counts as failed: fail closed."""
    cfg, unreadable = _config_state(sdlc_dir)
    if not unreadable and not _independent_on(cfg):
        return None
    try:
        state, got = _read_receipt(sdlc_dir, goal, phase)
    except ValueError:
        return None
    if state == "unreadable":
        return "unreadable", got
    if state == "ok" and got.get("outcome") != "ok":
        return got.get("failure") or "unknown", got.get("detail") or ""
    return None


def inline_review_refusal(sdlc_dir, goal, route):
    """For the PR-review generation path's inline branch: refuse an inline route the operator configured away from, or one after
    a failed code-review run for this goal, whatever the live route says. None otherwise."""
    failed = failed_receipt(sdlc_dir, goal, "review")
    if failed:
        return ("the independent reviewer FAILED for %s (%s): an inline self-review does not replace it. "
                "Park the goal (`--qkind reviewer_down`), fix the reviewer and re-run it" % (goal, failed[0]))
    cfg, unreadable = _config_state(sdlc_dir)
    if unreadable:
        return "%s/config.json is unreadable: no inline approval until it parses" % sdlc_dir
    if _independent_on(cfg) and configured_reviewer_unavailable(cfg, route):
        return ("the configured reviewer is not installed or not usable here (%s): install or log in to it, "
                "or set review.host to `inline` explicitly" % route.get("reason"))
    return None


def _failed_message(sdlc_dir, goal, phase, kind, detail, gesture):
    if kind in _CHECKOUT_KINDS:
        return ("the independent review could not be bound to this checkout for %s %s (%s%s): this is not "
                "the reviewer failing, so do not park. Fix that, then re-run `%s`"
                % (goal, phase, kind, ": " + detail if detail else "", gesture))
    return ("the independent reviewer FAILED for %s %s (%s): no approving verdict can be recorded, "
            "and an inline self-review does not replace it, whatever route resolves now. Park the goal "
            "(`loop.py record %s %s parked \"independent reviewer failed: %s\" --qkind reviewer_down`); "
            "to resume, fix the reviewer (login, command, timeout) and re-run `%s`"
            % (goal, phase, kind, sdlc_dir, goal, kind, gesture))


def independent_run_refusal(sdlc_dir, goal, phase, plan_sha256=""):
    """None, or why an APPROVING `phase` verdict may not be recorded for `goal` (AGENTS.md: a failed
    independent reviewer is never an approval).

    Order matters. (1) A damaged config refuses: `resolve()` fails open to `inline` and a verdict gate
    must not inherit that. (2) With `review.independent` on, a recorded run that did not finish OK
    refuses WHATEVER route resolves now -- the reviewer binary may have vanished since, turning the
    route inline, and that must not launder the failure. (3) An inline answer caused by something the
    operator configured being unavailable refuses, loudly (decided from config, not a flag); an inline answer the operator
    chose, or that nothing was configured for, stays allowed. (4) On a process/command route the
    approval must match a receipt the runner wrote -- never the agent restating a verdict -- bound to
    the brief's phase, the plan bytes (plan-review) or the checkout (code review). Proves the process
    ran and approved these bytes; a maker can still call the CLI by hand or write the file."""
    cfg, unreadable = _config_state(sdlc_dir)
    if unreadable:
        return ("%s/config.json is unreadable, so whether review.independent is on cannot be told: no "
                "approving verdict is recorded until it parses" % sdlc_dir)
    if not _independent_on(cfg):
        return None
    gesture = ("reviewer.py run %s <brief> --scratch <dir> --goal %s --phase %s" % (sdlc_dir, goal, phase))
    try:
        state, receipt = _read_receipt(sdlc_dir, goal, phase)
    except ValueError as exc:
        return "no approval for an unusable goal or phase (%s)" % exc
    if state == "unreadable":
        return "the reviewer run receipt for %s %s is unreadable (%s): re-run `%s`" % (goal, phase, receipt, gesture)
    if state == "ok" and receipt.get("outcome") != "ok":
        return _failed_message(sdlc_dir, goal, phase, receipt.get("failure") or "unknown",
                               receipt.get("detail") or "", gesture)
    route = resolve(sdlc_dir)
    mechanism = route.get("mechanism")
    if mechanism not in ("process", "command"):
        if configured_reviewer_unavailable(cfg, route):
            return ("the configured reviewer is not installed or not usable here (%s), so no approving "
                    "verdict is recorded: install or log in to it, or set review.host to `inline` "
                    "explicitly if self-review is what you want" % route.get("reason"))
        return None
    if state == "missing":
        return ("the resolved reviewer route is `%s` but no run of it is on record for %s %s, so no "
                "approving verdict can be recorded (a self-review is not independent and is not counted): "
                "run `%s` and record what it printed" % (mechanism, goal, phase, gesture))
    if receipt.get("verdict") != "approve":
        return ("the independent reviewer's last run for %s %s did not print a lone `VERDICT: approve` "
                "(got %r): an approval cannot be recorded; address its findings and re-run `%s`"
                % (goal, phase, receipt.get("verdict"), gesture))
    if receipt.get("brief_phase") != _BRIEF_NAME.get(phase):
        return ("the reviewer's last run for %s %s was given a %r brief, not a %s one: re-run `%s`"
                % (goal, phase, receipt.get("brief_phase"), _BRIEF_NAME.get(phase), gesture))
    if phase == "review":
        key = receipt.get("content_key")
        if not key or key != _content_key(sdlc_dir, goal)[0]:
            return ("the checkout changed since the independent reviewer last ran for %s (or could not be "
                    "bound to it): re-run `%s` and record that verdict" % (goal, gesture))
    if phase == "plan_review" and (not plan_sha256 or receipt.get("plan_sha256") != plan_sha256):
        return ("the independent reviewer approved a different plan than sha256 %s…: re-run `%s` on "
                "the current plan" % (plan_sha256[:12], gesture))
    return None


def run_review(sdlc_dir, brief_path, scratch_paths, goal=None, phase=None):
    """Execute a resolved process/command review with a real timeout and fresh process group.

    The operator-owned `review.command` retains its shell semantics. `process` commands remain
    argv lists. Unsupported platforms refuse before spawning: killing only a shell while its
    children keep consuming quota would be a silent half-guarantee.

    With `goal` and `phase` it leaves the run receipt. The receipt is written PESSIMISTICALLY (failed)
    only once the reviewer is about to LAUNCH, so a run refused beforehand (leak guard, shell policy,
    a bad timeout) -- a maker's typo, not a reviewer failure -- never overwrites an earlier good one,
    while a launched run that is killed or fails leaves a failure. The reviewer keeps the repo cwd: it
    must read the repository.
    """
    if os.name != "posix" or not hasattr(os, "killpg"):
        print("reviewer run: process-tree timeout is unsupported on this platform", file=sys.stderr)
        return 2
    if not scratch_paths:
        print("reviewer run: at least one --scratch path is required for the leak check",
              file=sys.stderr)
        return 2
    route = resolve(sdlc_dir)
    mechanism = route["mechanism"]
    receipt = bool(goal and phase)
    if (goal or phase) and not receipt:
        print("reviewer run: --goal and --phase go together", file=sys.stderr)
        return 2
    if mechanism not in ("process", "command"):
        print(f"reviewer run: resolved {mechanism}; use that route instead ({route['reason']})",
              file=sys.stderr)
        return 2
    if receipt:
        try:
            _receipt_path(sdlc_dir, goal, phase)
        except ValueError as exc:
            print(f"reviewer run: cannot record the run receipt ({exc})", file=sys.stderr)
            return 2
    try:
        brief = pathlib.Path(brief_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"LEAK: brief unreadable, so nothing was checked: {exc}", file=sys.stderr)
        return 1
    ok, findings = _check_brief_text(brief, scratch_paths)
    if not ok:
        for finding in findings:
            print(f"LEAK: {finding}", file=sys.stderr)
        return 1
    if mechanism == "command" and not shell_policy.repository_shell_commands_allowed(
            pathlib.Path(sdlc_dir).resolve().parent):
        # #707 (TM-04): `review.command` is a repository-supplied shell string, and its stdout is
        # accepted as the reviewer's verdict. Same gate as `verify.command`; no verdict is produced.
        print("reviewer run: " + shell_policy.refusal_message(), file=sys.stderr)
        return 2
    timeout = route["timeout_seconds"]
    if not isinstance(timeout, int) or timeout <= 0:
        print("reviewer run: review.timeout_seconds must be positive", file=sys.stderr)
        return 2
    command = route["command"]

    def failed(kind, code, message, detail=""):
        print(message, file=sys.stderr)
        if receipt:
            _write_receipt(sdlc_dir, goal, phase, outcome="failed", failure=kind, detail=detail)
        return code
    key = None
    if receipt and phase == "review":
        key, cause = _content_key(sdlc_dir, goal)
        if key is None:
            return failed("content_unbound", 1, "reviewer run: this checkout cannot be bound to the review: "
                          "%s. Fix that and re-run (nothing was sent to the reviewer)" % cause, cause)
    if receipt:
        _write_receipt(sdlc_dir, goal, phase, outcome="failed", failure="not_completed")
    try:
        proc = subprocess.Popen(command, shell=mechanism == "command", stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except OSError as exc:
        return failed("start_failed", 1,
                      f"reviewer run: could not start reviewer process ({type(exc).__name__})")
    try:
        output, _errors = proc.communicate(input=brief, timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_review_process(proc)
        return failed("timeout", 124,
                      f"reviewer run: timed out after {timeout}s; reviewer process group stopped")
    except BaseException:
        _stop_review_process(proc)
        raise
    if proc.returncode != 0:
        return failed("nonzero_exit", 1,
                      f"reviewer run: reviewer exited with exit {proc.returncode}; no verdict accepted")
    if not output.strip():
        return failed("empty_output", 1, "reviewer run: reviewer returned empty output; no verdict accepted")
    if key is not None:
        after, cause = _content_key(sdlc_dir, goal)
        if after != key:
            return failed("content_changed_during_run", 1,
                          "reviewer run: the checkout changed while the reviewer ran (%s); no verdict accepted. "
                          "If the reviewer writes caches into the checkout, a re-run usually passes once those "
                          "files exist (or git-ignore them)" % (cause or "files differ"), cause or "")
    if receipt:
        named = _BRIEF_HEADER.match(brief)
        plan = _PLAN_SHA_LINE.search(brief)
        _write_receipt(sdlc_dir, goal, phase, outcome="ok", verdict=lone_verdict(output),
                       brief_sha256=hashlib.sha256(brief.encode("utf-8")).hexdigest(),
                       output_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
                       plan_sha256=plan.group(1) if plan else None,
                       brief_phase=named.group(1) if named else None, content_key=key)
    sys.stdout.write(output)
    return 0


USAGE = ("usage: reviewer.py resolve <sdlc_dir> | check <brief_path> [--scratch <path>]... | "
         "run <sdlc_dir> <brief_path> --scratch <path> [--scratch <path>]... "
         "[--goal <goal> --phase plan_review|review]")


def main(argv):
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) >= 2 and argv[0] == "resolve":
        print(json.dumps(resolve(argv[1])))
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        scratch = [argv[i + 1] for i, a in enumerate(argv) if a == "--scratch" and i + 1 < len(argv)]
        ok, findings = check(argv[1], scratch)
        for f in findings:
            print("LEAK: %s" % f, file=sys.stderr)
        return 0 if ok else 1
    if len(argv) >= 3 and argv[0] == "run":
        options = argv[3:]
        if len(options) < 2 or len(options) % 2 or any(
                options[i] not in ("--scratch", "--goal", "--phase") for i in range(0, len(options), 2)):
            print(USAGE, file=sys.stderr)
            return 2
        pairs = [(options[i], options[i + 1]) for i in range(0, len(options), 2)]
        one = lambda flag: next((v for f, v in reversed(pairs) if f == flag), None)
        return run_review(argv[1], argv[2], [v for f, v in pairs if f == "--scratch"],
                          goal=one("--goal"), phase=one("--phase"))
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    # The script-time floor: this run's wall time, recorded to the session resolved at exit.
    raise SystemExit(_load("timing_store").timed_main(main, sys.argv[1:], "reviewer"))
