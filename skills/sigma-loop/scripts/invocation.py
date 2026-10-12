#!/usr/bin/env python3
"""Trusted, host-agnostic invocation adapter boundary (#612). OPT-IN: inert unless `.sdlc/config.json`
has `invocation.adapter`.

WHY. Some hosts refuse the loop's own approval step as self-approval, and a host-visible phase-end
relay cannot be proven from inside Sigma. Neither can be settled by environment markers
(`CLAUDECODE`, `CODEX_THREAD_ID`, `review.host`): the maker controls its own environment. So
authority is delegated to an ADAPTER the operator installed and pinned OUTSIDE the repository:

    git config --local sigma.invocation.adapter.<id>.path   /absolute/path/to/adapter
    git config --local sigma.invocation.adapter.<id>.sha256 <sha256 of that file>

Sigma runs exactly those bytes (copied to a private 0700 file after hashing, so a swap between hash
and exec cannot substitute other bytes), sends ONE versioned canonical-JSON challenge on stdin and
accepts only a strict canonical one-line receipt bound to the challenge digest and payload digest.

NOT CLAIMED. This is operator attestation, not isolation from the maker: a same-user process that can
write both the git config and the executable can pin its own adapter. A deterministic fixture proves
the PROTOCOL only; no real Claude/Codex/Cursor adapter ships here, so until an operator installs one
every host takes the attended/defer path. `accepted` from the relay means the adapter acknowledged the
exact bytes, not that any UI displayed them.

FAIL CLOSED. No `fcntl` (Windows), lock timeout, unsafe state path, missing/mismatched pin, unknown
adapter, malformed/replayed/expired receipt, adapter timeout or nonzero exit -> `Refused`; a `deferred`
receipt performs no autonomous side effect.

CEILINGS (measured by tests/test_invocation.py::test_state_cost): one stable lock file serialises
record reads/writes only (never held across the adapter call); at most `MAX_PRUNE` expired records are
removed per `authorize`, so growth is bounded by TTL x action rate. Worst-case added wall time per
phase end is `timeout_seconds` (default 30, max 120) -- only when the adapter is configured.
"""
import base64
import contextlib
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time

try:
    import fcntl
except ImportError:             # Windows: refuse rather than proceed without a lock
    fcntl = None

V = 1
#: Capability metadata, not a command registry: no built-in executable exists for any of them.
ADAPTERS = ("claude", "codex", "cursor")
KINDS = ("review-post", "phase-relay")
DEFAULT_TTL, TTL_RANGE = 300, (30, 600)
DEFAULT_TIMEOUT, TIMEOUT_RANGE = 30, (5, 120)
LOCK_WAIT_S = 5.0
MAX_PRUNE = 50
MAX_RESPONSE = 65536
MAX_REQUEST = 32768
_B64ID = re.compile(r"[A-Za-z0-9_-]{43}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_RECEIPT = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_RESPONSE_KEYS = tuple(sorted(("v", "action_id", "adapter_id", "challenge_sha256", "receipt_id",
                                      "status", "ack_sha256")))


class Refused(Exception):
    """A fail-closed refusal. The message is safe to print: it never carries adapter output."""


def _now():
    return time.time()


def _stamp(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- configuration


def settings(sdlc_dir):
    """-> None when the adapter is not configured (the default: zero behaviour change), else
    {adapter, ttl, timeout}. A configured-but-invalid block raises Refused: misconfiguration must not
    silently turn the gate off."""
    cfg, unreadable = None, False
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        unreadable = True
    block = cfg.get("invocation") if isinstance(cfg, dict) else None
    if unreadable or block is None or (isinstance(block, dict) and not block.get("adapter")):
        # The repository's config.json is agent-writable, so it can never switch the gate OFF where
        # the operator pinned an adapter: a pin with the config off (or unreadable) refuses.
        if _any_pin(_repo(sdlc_dir)):
            raise Refused("an operator adapter pin exists but invocation.adapter is not set (or "
                          "config.json is unreadable); set it, or remove the pin deliberately")
        return None
    if not isinstance(block, dict) or block.get("adapter") not in ADAPTERS:
        raise Refused("invocation.adapter must be one of %s" % ", ".join(ADAPTERS))
    out = {"adapter": block["adapter"]}
    for key, default, (lo, hi), name in (("ttl_seconds", DEFAULT_TTL, TTL_RANGE, "ttl"),
                                         ("timeout_seconds", DEFAULT_TIMEOUT, TIMEOUT_RANGE, "timeout")):
        val = block.get(key, default)
        if isinstance(val, bool) or not isinstance(val, int) or not lo <= val <= hi:
            raise Refused("invocation.%s must be an integer %d-%d" % (key, lo, hi))
        out[name] = val
    return out


def _git_config_file(repo):
    """The repository's local config FILE (where `git config --local` writes), found without running
    git: walk up for `.git`; a `gitdir:` file (linked worktree) is followed and its `commondir`
    honoured. -> Path, or None when there is no repository above `repo`."""
    p = pathlib.Path(repo)
    for q in (p, *p.parents):
        dot = q / ".git"
        if dot.is_dir():
            # Mirror git's discovery: only a directory that LOOKS like a repository counts; a bogus
            # empty `.git` is skipped (git keeps walking up and finds the parent's config).
            if not ((dot / "HEAD").is_file() and (dot / "objects").is_dir()):
                continue
            gitdir = dot
        elif dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return dot                       # exists but unreadable: caller treats as unknown
            if not text.startswith("gitdir:"):
                return dot
            gitdir = (q / text[len("gitdir:"):].strip()).resolve()
        else:
            continue
        common = gitdir / "commondir"
        if common.is_file():
            try:
                gitdir = (gitdir / common.read_text(encoding="utf-8").strip()).resolve()
            except OSError:
                return common
        return gitdir / "config"
    return None


def _any_pin(repo):
    """True when the local git config could hold an operator pin. NO subprocess: one file read, so the
    unconfigured default costs a stat and can never be broken by a git failure. A pin written by
    `git config --local sigma.invocation.adapter.<id>.path|sha256` lands in that file as a section
    named `sigma "invocation.adapter.<id>"`, so the text `invocation.adapter` is the marker (a
    superset: a stray mention only makes the gate stricter, never looser). A file that exists but
    cannot be read is UNKNOWN and fails closed; garbage text without the marker is simply no pin."""
    cfg = _git_config_file(repo)
    if cfg is None:
        return False
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace").lower().replace("\\", "")
        # git unescapes backslashes in quoted subsections (`"invocation\\.adapter"` is the same
        # subsection), so every backslash is dropped first; then ANY line mentioning sigma followed
        # by invocation..adapter counts (a superset of git's headers: stricter, never looser).
        return re.search(r"sigma[^\n]*invocation[^\n]*adapter", text) is not None
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _repo(sdlc_dir):
    return str(pathlib.Path(sdlc_dir).resolve().parent.resolve())


# --------------------------------------------------------------------------- trust root


def _git_local(repo, key):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": os.environ.get("HOME", "")}
    try:
        proc = subprocess.run(["git", "-C", repo, "config", "--local", "--get", key],
                              capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def load_adapter(repo, adapter_id):
    """-> the verified bytes of the operator-pinned adapter, or raise Refused BEFORE anything runs."""
    if adapter_id not in ADAPTERS:
        raise Refused("unknown adapter %r" % (adapter_id,))
    if fcntl is None or not hasattr(os, "getuid"):
        raise Refused("invocation requires a POSIX platform; not supported here")
    path = _git_local(repo, "sigma.invocation.adapter.%s.path" % adapter_id)
    pin = _git_local(repo, "sigma.invocation.adapter.%s.sha256" % adapter_id)
    if not path or not pin:
        raise Refused("no operator pin for adapter %r (git config --local "
                      "sigma.invocation.adapter.%s.path and .sha256)" % (adapter_id, adapter_id))
    if not os.path.isabs(path) or not _HEX64.fullmatch(pin):
        raise Refused("adapter pin for %r is malformed (absolute path and lowercase sha256 required)"
                      % adapter_id)
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise Refused("adapter %r is not a regular, non-symlink file" % adapter_id)
        if before.st_uid != os.getuid() or before.st_mode & 0o022:
            raise Refused("adapter %r must be owned by you and not group/world writable" % adapter_id)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            after = os.fstat(fd)
            if (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev):
                raise Refused("adapter %r changed while being opened" % adapter_id)
            data = b""
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                data += chunk
        finally:
            os.close(fd)
    except OSError as exc:
        raise Refused("adapter %r cannot be read (%s)" % (adapter_id, exc.__class__.__name__))
    if not hmac.compare_digest(sha256_hex(data), pin):
        raise Refused("adapter %r does not match its pinned sha256" % adapter_id)
    return data


def _kill_group(proc):
    """SIGKILL the process group of the adapter WE started (start_new_session: pgid == its pid)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.wait(timeout=5)


def _bounded_io(proc, request, timeout):
    """Write the request, read AT MOST MAX_RESPONSE+1 bytes of stdout (never an unbounded buffer),
    then wait for exit; any overrun kills the adapter's group. -> the bytes read."""
    if len(request) > MAX_REQUEST:
        _kill_group(proc)
        raise Refused("invocation request too large")
    deadline = time.monotonic() + timeout
    try:
        proc.stdin.write(request)
        proc.stdin.close()
    except OSError:
        pass                                   # the adapter exited early; its stdout/exit decide
    box = []
    reader = threading.Thread(target=lambda: box.append(proc.stdout.read(MAX_RESPONSE + 1)), daemon=True)
    reader.start()
    reader.join(max(0.0, deadline - time.monotonic()))
    if reader.is_alive():
        _kill_group(proc)
        raise Refused("adapter timed out after %ds" % timeout)
    out = box[0] if box else b""
    if len(out) > MAX_RESPONSE:
        _kill_group(proc)
        raise Refused("adapter response too large")
    try:
        proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise Refused("adapter timed out after %ds" % timeout)
    if proc.returncode != 0:
        raise Refused("adapter exited nonzero")
    return out


def run_adapter(sdlc_dir, data, verb, request, timeout):
    """Run the verified bytes (a private 0700 copy), one JSON request on stdin, one line back."""
    scratch = _dir(sdlc_dir)
    tmp = tempfile.mkdtemp(prefix="run-", dir=str(scratch))
    try:
        os.chmod(tmp, 0o700)
        exe = os.path.join(tmp, "adapter")
        fd = os.open(exe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        env = {"PATH": "/usr/bin:/bin", "INVOCATION_ABI_V": str(V)}
        for key in ("LANG", "LC_ALL"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        try:
            proc = subprocess.Popen([exe, verb, "--stdio"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                    start_new_session=True, cwd=tmp)
        except OSError as exc:
            raise Refused("adapter could not be executed (%s)" % exc.__class__.__name__)
        return _bounded_io(proc, request, timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- strict ABI


def _rid():
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()


def build_challenge(sdlc_dir, kind, adapter_id, goal, payload_sha256, ttl, *, phase=None, pr=None,
                    head_sha=None, brief_sha256=None):
    if kind not in KINDS or adapter_id not in ADAPTERS or not _HEX64.fullmatch(payload_sha256 or ""):
        raise Refused("malformed challenge request")
    if kind == "review-post":
        if not (isinstance(pr, int) and not isinstance(pr, bool) and pr > 0
                and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", head_sha or "")
                and _HEX64.fullmatch(brief_sha256 or "")):
            raise Refused("review-post needs pr, head_sha and brief_sha256")
        phase = "review"
    else:
        if pr is not None or head_sha is not None or brief_sha256 is not None or not phase:
            raise Refused("phase-relay takes a phase and no PR bindings")
    now = _now()
    return {"v": V, "kind": kind, "action_id": _rid(), "adapter_id": adapter_id,
            "repo_realpath": _repo(sdlc_dir), "goal": str(goal), "phase": phase, "pr": pr,
            "head_sha": head_sha, "brief_sha256": brief_sha256, "payload_sha256": payload_sha256,
            "issued_at": _stamp(now), "expires_at": _stamp(now + ttl), "nonce": _rid()}


def parse_response(raw, challenge):
    """Strict: canonical one line, exactly the seven keys, bound to THIS challenge. -> dict."""
    try:
        line = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("adapter response is not UTF-8")
    if not line.endswith("\n") or "\n" in line[:-1]:
        raise Refused("adapter response must be exactly one line")
    line = line[:-1]
    try:
        obj = json.loads(line)
    except ValueError:
        raise Refused("adapter response is not JSON")
    if not isinstance(obj, dict) or tuple(sorted(obj)) != _RESPONSE_KEYS or canonical(obj) != line:
        raise Refused("adapter response is not the canonical seven-field receipt")
    csha = sha256_hex(canonical(challenge).encode())
    if (type(obj["v"]) is not int or obj["v"] != V or obj["action_id"] != challenge["action_id"]
            or obj["adapter_id"] != challenge["adapter_id"] or obj["challenge_sha256"] != csha):
        raise Refused("receipt is not bound to this challenge")
    if not isinstance(obj["receipt_id"], str) or not _RECEIPT.fullmatch(obj["receipt_id"]):
        raise Refused("receipt_id is malformed")
    if obj["status"] == "accepted":
        if obj["ack_sha256"] != challenge["payload_sha256"]:
            raise Refused("acknowledgement is for different bytes than were sent")
    elif obj["status"] == "deferred":
        if obj["ack_sha256"] != "":
            raise Refused("a deferred receipt must not acknowledge anything")
    else:
        raise Refused("receipt status must be accepted or deferred")
    return obj


# --------------------------------------------------------------------------- durable state


def _dir(sdlc_dir):
    import importlib.util
    spec = importlib.util.spec_from_file_location("state", pathlib.Path(__file__).resolve().parent / "state.py")
    st = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(st)
    try:
        st.refuse_symlinks(sdlc_dir, "state/invocations", create_parents=True)
        path = pathlib.Path(sdlc_dir) / "state" / "invocations"
        path.mkdir(parents=True, exist_ok=True)
        st.refuse_symlinks(sdlc_dir, "state/invocations")
    except OSError as exc:
        raise Refused("unsafe invocation state path (%s)" % exc)
    return path


@contextlib.contextmanager
def _locked(sdlc_dir):
    """Stable, non-replaced lock file; bounded wait; any failure refuses (never an unlocked write)."""
    if fcntl is None:
        raise Refused("invocation requires POSIX file locks; not supported on this platform")
    d = _dir(sdlc_dir)
    lock = d / ".lock"
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise Refused("cannot open invocation lock (%s)" % exc.__class__.__name__)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("invocation lock is not a regular file")
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Refused("invocation lock busy")
                time.sleep(0.01)
        try:
            yield d
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _path(d, action_id):
    if not _B64ID.fullmatch(action_id or ""):
        raise Refused("malformed action id")
    return d / (action_id + ".json")


def _read(d, action_id):
    try:
        fd = os.open(_path(d, action_id), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Refused("cannot read action record (%s)" % exc.__class__.__name__)
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError:
            raise Refused("action record is corrupt")


def _write(d, rec):
    final = _path(d, rec["challenge"]["action_id"])
    tmp = d / (".tmp-" + _rid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)


def _prune(d):
    removed = 0
    cutoff = _now() - 10 * TTL_RANGE[1]
    for p in sorted(list(d.glob("*.json")) + list(d.glob(".tmp-*")) + list(d.glob("run-*"))):
        if removed >= MAX_PRUNE:
            break
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
                removed += 1
        except OSError:
            continue


# --------------------------------------------------------------------------- public API


def authorize(sdlc_dir, kind, goal, payload_sha256, *, request_extra=None, phase=None, pr=None,
              head_sha=None, brief_sha256=None):
    """One challenge, one adapter call. -> {"status": "accepted"|"deferred", "action_id", "adapter_id",
    "receipt_id"}; raises Refused. Returns {"status": "off"} when the adapter is not configured."""
    cfg = settings(sdlc_dir)
    if cfg is None:
        return {"status": "off"}
    repo = _repo(sdlc_dir)
    data = load_adapter(repo, cfg["adapter"])
    challenge = build_challenge(sdlc_dir, kind, cfg["adapter"], goal, payload_sha256, cfg["ttl"],
                                phase=phase, pr=pr, head_sha=head_sha, brief_sha256=brief_sha256)
    with _locked(sdlc_dir) as d:
        _prune(d)
        _write(d, {"challenge": challenge, "status": "issued"})
    request = {"challenge": challenge}
    if request_extra:
        request.update(request_extra)
    raw = run_adapter(sdlc_dir, data, "verify", canonical(request).encode(), cfg["timeout"])
    try:
        resp = parse_response(raw, challenge)
    except Refused:
        with _locked(sdlc_dir) as d:
            _write(d, {"challenge": challenge, "status": "refused"})
        raise
    with _locked(sdlc_dir) as d:
        rec = _read(d, challenge["action_id"])
        if not rec or rec.get("status") != "issued":
            raise Refused("action record changed during the adapter call")
        if _now() > _epoch(challenge["expires_at"]):
            _write(d, {"challenge": challenge, "status": "expired"})
            raise Refused("receipt expired before it could be recorded")
        horizon = _now() - TTL_RANGE[1]         # only records that can still be live: bounds the scan
        for other in d.glob("*.json"):          # a receipt id may be used once
            if other.name != _path(d, challenge["action_id"]).name and other.stat().st_mtime >= horizon:
                try:
                    o = _read(d, other.stem)
                    same = (o and o.get("receipt_id") == resp["receipt_id"]
                            and o["challenge"]["adapter_id"] == resp["adapter_id"])
                except (Refused, KeyError, TypeError):
                    continue                    # a foreign or corrupt file is skipped, never fatal
                if same:
                    raise Refused("duplicate receipt id")
        status = "verified" if resp["status"] == "accepted" else "deferred"
        _write(d, {"challenge": challenge, "status": status, "receipt_id": resp["receipt_id"]})
    return {"status": resp["status"], "action_id": challenge["action_id"],
            "adapter_id": challenge["adapter_id"], "receipt_id": resp["receipt_id"]}


def consume(sdlc_dir, action_id):
    """verified -> consumed, once. Raises Refused if absent, unverified, expired or already used."""
    with _locked(sdlc_dir) as d:
        rec = _read(d, action_id)
        if not rec or rec.get("status") != "verified":
            raise Refused("no verified, unused receipt for this action")
        if _now() > _epoch(rec["challenge"]["expires_at"]):
            _write(d, {**rec, "status": "expired"})
            raise Refused("receipt expired")
        _write(d, {**rec, "status": "consumed"})


def fresh_hosts(sdlc_dir):
    """Adapter ids with an unexpired verified/consumed receipt (a reply bound to a Sigma challenge;
    the record itself is a same-user file, so this is not a security boundary): the only thing that
    may name a host-specific path. Never raises; empty means 'no verified host'."""
    out = set()
    try:
        with _locked(sdlc_dir) as d:
            for p in d.glob("*.json"):
                rec = _read(d, p.stem)
                if (rec and rec.get("status") in ("verified", "consumed")
                        and _now() <= _epoch(rec["challenge"]["expires_at"])):
                    out.add(rec["challenge"]["adapter_id"])
    except (Refused, OSError, KeyError, ValueError):
        return set()
    return out


def diagnose(sdlc_dir, adapter_id):
    """-> 'installed-and-conforming' | 'defer' | 'refused'. `defer` = no pin / not configured."""
    if adapter_id not in ADAPTERS:
        return "refused"
    repo = _repo(sdlc_dir)
    if not _git_local(repo, "sigma.invocation.adapter.%s.path" % adapter_id):
        return "defer"
    try:
        cfg = settings(sdlc_dir) or {"timeout": DEFAULT_TIMEOUT, "ttl": DEFAULT_TTL}
        data = load_adapter(repo, adapter_id)
        raw = run_adapter(sdlc_dir, data, "describe", b'{"v":1}', cfg["timeout"])
        want = canonical({"adapter_id": adapter_id, "kinds": list(KINDS), "v": V}) + "\n"
        if raw.decode("utf-8", "replace") != want:
            return "refused"
        return "installed-and-conforming"
    except Refused:
        return "refused"


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print("usage: invocation.py diagnose <sdlc_dir> <claude|codex|cursor>\n"
              "  checks the operator-pinned adapter; prints installed-and-conforming | defer | refused")
        return 0
    if len(argv) == 3 and argv[0] == "diagnose":
        result = diagnose(argv[1], argv[2])
        print(result)
        return 0 if result == "installed-and-conforming" else 1
    print("usage: invocation.py diagnose <sdlc_dir> <claude|codex|cursor>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
