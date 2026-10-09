"""GitHub-GraphQL capability check + REST-backed issue/PR helper (#801, slice 1). Stdlib only.

HONESTY FIRST. This is slice 1 of #801: DETECTION AND REPORTING ONLY. Nothing in the product calls
the ops below yet (no caller is migrated; `sources.py`/`work.py` keep their direct `gh issue|pr`
calls), the cache/probe path is not exercised by any shipped caller (`doctor` always passes
`probe=False`), and nothing here is evidence that /sigma-loop works in a Claude Code cloud session --
that is unmeasured. The 403 wording the probe recognises comes from issue #801's text, not from a
captured live session. See docs/cloud-sessions.md.

RUN CONTRACT (one convention, applies to every op and the probe): `run` is the `sources` convention --
a callable taking `args: list[str]` WITHOUT a leading "gh", returning stdout as `str`, and RAISING on
failure (as `sources._run_gh` raises RuntimeError carrying `.hint`). `run=None` selects `_default_run`,
a thin subprocess `gh` caller that raises the same way; tests always inject a fake.

CAPABILITY CHECK, `graphql_available(env, cache_dir, run, now, probe)`. Precedence, first hit wins:
  1. `SIGMA_GH_GRAPHQL` = off/0/false (unavailable) or on/1/true (available): the operator's polite
     lever. Any other value is ignored (noted in `reason`), never an error.
  2. `CLAUDE_CODE_REMOTE` in {true, 1}: unavailable, free, no I/O.
  3. Cache `<cache_dir>/gh-capability.json` -- read ONLY when `probe=True`; valid while the stored
     `env_key` matches and age < `CACHE_TTL_SECONDS` (900), so a stale "unavailable" self-heals.
     Corrupt/unreadable = miss. Holds no secrets (env_key is the CLAUDE_CODE_REMOTE value only).
  4. One probe, ONLY when `probe=True` and `run` is supplied (`probe=True` with no `run` raises
     ValueError; the module never shells out implicitly): ONE `api graphql` call, no retry, no loop,
     no background process. Third-shape text -> unavailable. Any OTHER failure -> available with
     reason "probe inconclusive" (fail-open: a network blip must not disable features). Written to the
     cache atomically (temp file + os.replace); any OSError is swallowed and noted in `reason`.
  5. Default: available ("no cloud signal").
Cost: O(1). Rate pools: REST `core` 5000/h vs `graphql` 5000 points/h (REST ~1 point/call, measured in
#1829).

REST OPS raise `GhApiError` (wraps the underlying failure: `str()` is the original text, `.hint` is
copied from the original when present) on transport failure or unparseable output; NO retries; every
call passes `--method` explicitly; label names are URL-quoted. Only `view_issue` comments and
`list_issues` paginate, both bounded by an explicit `cap` with a page loop (never `--paginate`).

KNOWN BYPASS (R5): `create_issue` is a plain REST create. It does NOT carry the feature-label refusal
that lives in `GitHubSource._run`. Migration slice 3 MUST preserve that refusal before any caller
moves onto it. `merge_pr` has no auto-merge (REST has none; documented, not emulated).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import time
import urllib.parse

_HERE = pathlib.Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gh_session = _load("gh_session")

CACHE_TTL_SECONDS = 900
CACHE_FILE = "gh-capability.json"
OVERRIDE_ENV = "SIGMA_GH_GRAPHQL"
_OFF = ("off", "0", "false")
_ON = ("on", "1", "true")
_PROBE_ARGS = ["api", "graphql", "-f", "query={viewer{login}}"]


class GhApiError(Exception):
    """A REST op failed. `str(self)` is the wrapped failure's text; `.hint` is its `.hint` or None."""

    def __init__(self, text, hint=None):
        super().__init__(text)
        self.hint = hint


def _default_run(args):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        hint = proc.stderr.strip() or "is `gh` installed and authenticated? run `gh auth status`"
        exc = RuntimeError("gh " + " ".join(args) + " failed: " + hint)
        exc.hint = hint
        raise exc
    return proc.stdout


# ---------------------------------------------------------------- capability check

def _result(available, reason, source):
    return {"available": available, "reason": reason, "source": source}


def _read_cache(path, env_key, now):
    try:
        data = json.loads(path.read_text())
        if data.get("env_key") == env_key and 0 <= now - float(data["at"]) < CACHE_TTL_SECONDS:
            return data
    except Exception:                                 # noqa: BLE001 - corrupt/missing cache = miss
        pass
    return None


def _write_cache(path, entry):
    """Atomic write; returns None on success or a short failure note (never raises)."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(entry))
        os.replace(tmp, path)
        return None
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return "cache not written (%s)" % exc.__class__.__name__


def graphql_available(env=None, cache_dir=None, run=None, now=None, probe=False):
    """-> {"available": bool, "reason": str, "source": "override|env|cache|probe|default"}. See module doc."""
    env = os.environ if env is None else env
    note = ""
    override = str(env.get(OVERRIDE_ENV, "")).strip().lower()
    if override in _OFF:
        return _result(False, "%s=%s (operator override)" % (OVERRIDE_ENV, override), "override")
    if override in _ON:
        return _result(True, "%s=%s (operator override)" % (OVERRIDE_ENV, override), "override")
    if override:
        note = " (ignored unrecognised %s=%s)" % (OVERRIDE_ENV, override)
    remote = str(env.get("CLAUDE_CODE_REMOTE", "")).strip().lower()
    if remote in ("true", "1"):
        return _result(False, "CLAUDE_CODE_REMOTE set: Claude Code cloud session, GitHub GraphQL is "
                              "blocked by the cloud proxy (inferred from env, not probed)" + note, "env")
    if probe:
        if run is None:
            raise ValueError("graphql_available(probe=True) needs an injected run=; it never shells out implicitly")
        now = time.time() if now is None else now
        path = pathlib.Path(cache_dir) / CACHE_FILE if cache_dir else None
        if path is not None:
            hit = _read_cache(path, remote, now)
            if hit is not None:
                return _result(bool(hit["available"]), str(hit.get("reason", "")) + " (cached)", "cache")
        try:
            run(list(_PROBE_ARGS))
            available, reason = True, "graphql probe succeeded"
        except Exception as exc:                      # noqa: BLE001 - classify, never propagate
            if gh_session.graphql_unavailable(str(exc)):
                available, reason = False, "graphql probe: GitHub GraphQL not available from this session"
            else:
                return _result(True, "probe inconclusive (not cached): %s" % str(exc)[:120] + note, "probe")
        if path is not None:
            failed = _write_cache(path, {"env_key": remote, "available": available,
                                         "reason": reason, "at": now})
            if failed:
                reason += "; " + failed
        return _result(available, reason + note, "probe")
    return _result(True, "no cloud signal" + note, "default")


# ---------------------------------------------------------------- REST ops

def _endpoint(repo, tail):
    return "repos/%s/%s" % (repo or "{owner}/{repo}", tail)


def _call(run, args):
    try:
        return (run or _default_run)(args)
    except GhApiError:
        raise
    except Exception as exc:                          # noqa: BLE001 - wrap, keep the text + hint
        raise GhApiError(str(exc), getattr(exc, "hint", None)) from exc


def _json(run, args):
    raw = _call(run, args)
    try:
        return json.loads(raw or "null")
    except (ValueError, TypeError) as exc:
        raise GhApiError("unparseable gh api output for %s: %s" % (args[1], str(raw)[:120])) from exc


def _list_fetch():
    # Lazy on purpose: loading `sources` reconfigures stdout/stderr and pulls a dozen siblings.
    return _load("sources").fetch_issues_rest


def list_issues(run=None, repo=None, labels=(), cap=200, state="open", fetch=None):
    """Open issues via the injected `fetch` (default: sources.fetch_issues_rest, loaded lazily)."""
    fetch = fetch or _list_fetch()
    try:
        return fetch(run or _default_run, repo, list(labels), cap, state=state)
    except GhApiError:
        raise
    except Exception as exc:                          # noqa: BLE001
        raise GhApiError(str(exc), getattr(exc, "hint", None)) from exc


def view_issue(run, number, repo=None, cap=100):
    """GET the issue plus at most `cap` comments (page loop of 100; ceil(cap/100) requests max)."""
    issue = _json(run, ["api", _endpoint(repo, "issues/%d" % number), "--method", "GET"])
    comments, page = [], 1
    while len(comments) < cap:
        got = _json(run, ["api", _endpoint(repo, "issues/%d/comments" % number), "--method", "GET",
                          "-f", "per_page=100", "-f", "page=%d" % page]) or []
        comments.extend(got)
        if len(got) < 100:
            break
        page += 1
    issue["comments"] = comments[:cap]
    return issue


def comment_issue(run, number, body, repo=None):
    return _json(run, ["api", _endpoint(repo, "issues/%d/comments" % number), "--method", "POST",
                       "-f", "body=%s" % body])


def add_labels(run, number, labels, repo=None):
    args = ["api", _endpoint(repo, "issues/%d/labels" % number), "--method", "POST"]
    for name in labels:
        args += ["-f", "labels[]=%s" % name]
    return _json(run, args)


def remove_label(run, number, label, repo=None):
    """DELETE one label. A 404 (label not on the issue) is a no-op; anything else raises."""
    args = ["api", _endpoint(repo, "issues/%d/labels/%s" % (number, urllib.parse.quote(label, safe=""))),
            "--method", "DELETE"]
    try:
        return _json(run, args)
    except GhApiError as exc:
        if "404" in str(exc):
            return None
        raise


def create_issue(run, title, body, labels=(), repo=None):
    """Plain REST create. BYPASSES the feature-label refusal in GitHubSource._run (see module doc)."""
    args = ["api", _endpoint(repo, "issues"), "--method", "POST", "-f", "title=%s" % title,
            "-f", "body=%s" % body]
    for name in labels:
        args += ["-f", "labels[]=%s" % name]
    return _json(run, args)


def close_issue(run, number, repo=None, reason=None):
    args = ["api", _endpoint(repo, "issues/%d" % number), "--method", "PATCH", "-f", "state=closed"]
    if reason:
        args += ["-f", "state_reason=%s" % reason]
    return _json(run, args)


def create_pr(run, title, body, head, base, repo=None):
    return _json(run, ["api", _endpoint(repo, "pulls"), "--method", "POST", "-f", "title=%s" % title,
                       "-f", "body=%s" % body, "-f", "head=%s" % head, "-f", "base=%s" % base])


def view_pr(run, number, repo=None):
    return _json(run, ["api", _endpoint(repo, "pulls/%d" % number), "--method", "GET"])


def merge_pr(run, number, merge_method="squash", sha=None, repo=None):
    """PUT /pulls/{n}/merge. No auto-merge exists in REST; not emulated. `sha` guards against races."""
    args = ["api", _endpoint(repo, "pulls/%d/merge" % number), "--method", "PUT",
            "-f", "merge_method=%s" % merge_method]
    if sha:
        args += ["-f", "sha=%s" % sha]
    return _json(run, args)
