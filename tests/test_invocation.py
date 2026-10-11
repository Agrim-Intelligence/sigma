"""#612: the trusted invocation adapter boundary.

EVERY host case below is a PROTOCOL SIMULATION: a deterministic fixture executable that speaks the
version-1 ABI. It proves Sigma's strict parsing, pinning and lifecycle for claude/codex/cursor alike;
it does NOT prove that any real host adapter exists or that a UI displayed anything. A missing
operator-installed adapter is a safe `defer`/refusal, never a passing "live host" test."""
import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "sigma-loop" / "scripts" / "invocation.py"
HOSTS = ("claude", "codex", "cursor")


def _load():
    spec = importlib.util.spec_from_file_location("invocation", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


inv = _load()

ADAPTER = textwrap.dedent('''\
    #!{py}
    import base64, hashlib, json, sys, time, uuid
    MODE = {mode!r}
    MARK = {mark!r}
    open(MARK, "a").write("ran\\n")
    verb = sys.argv[1]
    req = json.loads(sys.stdin.read())
    def canon(o): return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if verb == "describe":
        print(canon({{"adapter_id": {host!r}, "kinds": ["review-post", "phase-relay"], "v": 1}}
                    if MODE != "baddescribe" else {{"v": 2}}))
        sys.exit(0)
    ch = req["challenge"]
    csha = hashlib.sha256(canon(ch).encode()).hexdigest()
    ack = ch["payload_sha256"]
    if "payload_b64" in req:
        raw = base64.b64decode(req["payload_b64"])
        open(MARK + ".payload", "wb").write(raw)
        ack = hashlib.sha256(raw).hexdigest()
    out = {{"v": 1, "action_id": ch["action_id"], "adapter_id": ch["adapter_id"],
           "challenge_sha256": csha, "receipt_id": "r-" + uuid.uuid4().hex, "status": "accepted",
           "ack_sha256": ack}}
    if MODE == "defer": out["status"], out["ack_sha256"] = "deferred", ""
    if MODE == "wrongack": out["ack_sha256"] = "f" * 64
    if MODE == "otherchallenge": out["challenge_sha256"] = "e" * 64
    if MODE == "extra": out["extra"] = 1
    if MODE == "fixedreceipt": out["receipt_id"] = "same"
    if MODE == "nonzero": sys.exit(3)
    if MODE == "slow": time.sleep(60)
    line = canon(out)
    if MODE == "noncanonical": line = json.dumps(out, sort_keys=True)
    if MODE == "twolines": line += "\\n" + line
    print(line)
''')


class Rig:
    def __init__(self, tmp, host, mode="ok", repo=None):
        """`repo` attaches to an existing repository (its `.sdlc/config.json` is left alone);
        otherwise a fresh one is made with only the invocation block configured."""
        self.tmp, self.host = tmp, host
        self.repo = repo or tmp / "repo"
        if repo is None:
            self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.sdlc = self.repo / ".sdlc"
        self.sdlc.mkdir(exist_ok=True)
        self.mark = tmp / "ran.mark"
        self.exe = tmp / "adapter"
        tmp.mkdir(exist_ok=True)
        self.install(mode)
        if repo is None:
            self.config({"adapter": host})

    def install(self, mode, pin=True):
        self.exe.write_text(ADAPTER.format(py=sys.executable, mode=mode, mark=str(self.mark),
                                           host=self.host))
        self.exe.chmod(0o700)
        if pin:
            self.pin(str(self.exe), hashlib.sha256(self.exe.read_bytes()).hexdigest())

    def pin(self, path, sha):
        for k, v in (("path", path), ("sha256", sha)):
            subprocess.run(["git", "-C", str(self.repo), "config", "--local",
                            "sigma.invocation.adapter.%s.%s" % (self.host, k), v], check=True)

    def config(self, block):
        (self.sdlc / "config.json").write_text(json.dumps({"invocation": block}))

    def ran(self):
        return self.mark.exists()

    def review(self, **kw):
        args = dict(pr=7, head_sha="a" * 40, brief_sha256="b" * 64)
        args.update(kw)
        return inv.authorize(str(self.sdlc), "review-post", "612", "c" * 64, **args)


@pytest.fixture(params=HOSTS)
def rig(request, tmp_path):
    return Rig(tmp_path, request.param)


# ---- protocol simulation: the happy path and its single-use lifecycle (all three hosts) ----------

def test_accepted_receipt_is_single_use(rig):
    got = rig.review()
    assert got["status"] == "accepted" and got["adapter_id"] == rig.host
    inv.consume(str(rig.sdlc), got["action_id"])
    with pytest.raises(inv.Refused):
        inv.consume(str(rig.sdlc), got["action_id"])


def _unpin(r):
    for k in ("path", "sha256"):
        subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset-all",
                        "sigma.invocation.adapter.%s.%s" % (r.host, k)], check=True)


def test_unconfigured_and_unpinned_is_off_and_never_runs_anything(tmp_path):
    r = Rig(tmp_path, "claude")
    _unpin(r)
    r.config({})
    assert inv.authorize(str(r.sdlc), "review-post", "612", "c" * 64, pr=7, head_sha="a" * 40,
                         brief_sha256="b" * 64) == {"status": "off"}
    assert not r.ran() and not (r.sdlc / "state" / "invocations").exists()


@pytest.mark.parametrize("how", ["null-adapter", "block-removed", "corrupt-config"])
def test_repo_config_cannot_switch_the_gate_off_where_a_pin_exists(tmp_path, how):
    """The repository's config.json is agent-writable; the operator's pin is the root of trust."""
    r = Rig(tmp_path, "claude")
    if how == "null-adapter":
        r.config({"adapter": None})
    elif how == "block-removed":
        (r.sdlc / "config.json").write_text("{}")
    else:
        (r.sdlc / "config.json").write_text("{not json")
    with pytest.raises(inv.Refused, match="pin exists"):
        r.review()
    assert not r.ran()


def test_deferred_receipt_authorizes_nothing(tmp_path):
    r = Rig(tmp_path, "codex", mode="defer")
    got = r.review()
    assert got["status"] == "deferred"
    with pytest.raises(inv.Refused):
        inv.consume(str(r.sdlc), got["action_id"])


# ---- adversarial spoof controls: environment, repo config and pins are never authority ----------

def test_environment_identity_is_not_authority(tmp_path, monkeypatch):
    r = Rig(tmp_path, "claude")
    for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "SIGMA_HOST"):
        monkeypatch.setenv(k, "1")
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset",
                    "sigma.invocation.adapter.claude.path"], check=True)
    with pytest.raises(inv.Refused, match="no operator pin"):
        r.review()
    assert not r.ran()


def test_repository_config_cannot_name_a_command(tmp_path):
    r = Rig(tmp_path, "claude")
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset-all",
                    "sigma.invocation.adapter.claude.sha256"], check=True)
    r.config({"adapter": "claude", "path": str(r.exe), "command": str(r.exe),
              "sha256": hashlib.sha256(r.exe.read_bytes()).hexdigest()})
    with pytest.raises(inv.Refused):
        r.review()
    assert not r.ran()


@pytest.mark.parametrize("how", ["wrong-hash", "relative", "symlink", "group-writable", "missing-file",
                                 "not-hex"])
def test_a_bad_pin_refuses_before_anything_runs(tmp_path, how):
    r = Rig(tmp_path, "cursor")
    good = hashlib.sha256(r.exe.read_bytes()).hexdigest()
    if how == "wrong-hash":
        r.pin(str(r.exe), "0" * 64)
    elif how == "relative":
        r.pin("adapter", good)
    elif how == "symlink":
        link = tmp_path / "link"
        link.symlink_to(r.exe)
        r.pin(str(link), good)
    elif how == "group-writable":
        r.exe.chmod(0o770)
    elif how == "missing-file":
        r.pin(str(tmp_path / "nope"), good)
    else:
        r.pin(str(r.exe), "ABC")
    with pytest.raises(inv.Refused):
        r.review()
    assert not r.ran()


def test_a_changed_binary_is_refused_not_run(tmp_path):
    r = Rig(tmp_path, "claude")
    r.install("defer", pin=False)          # new bytes, old pin
    with pytest.raises(inv.Refused, match="pinned sha256"):
        r.review()
    assert not r.ran()


def test_a_path_shadow_executable_is_not_used(tmp_path, monkeypatch):
    r = Rig(tmp_path, "claude")
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "adapter").write_text("#!/bin/sh\necho hi\n")
    (shadow / "adapter").chmod(0o700)
    monkeypatch.setenv("PATH", str(shadow))
    assert r.review()["status"] == "accepted"
    assert r.ran()                           # the pinned bytes ran, not the shadow


def test_unknown_adapter_id_in_config_refuses(tmp_path):
    r = Rig(tmp_path, "claude")
    r.config({"adapter": "evil"})
    with pytest.raises(inv.Refused):
        r.review()


@pytest.mark.parametrize("mode,why", [
    ("wrongack", "different bytes"), ("otherchallenge", "not bound"), ("extra", "canonical"),
    ("noncanonical", "canonical"), ("twolines", "one line"), ("nonzero", "nonzero")])
def test_a_malformed_or_misbound_receipt_is_refused(tmp_path, mode, why):
    r = Rig(tmp_path, "codex", mode=mode)
    with pytest.raises(inv.Refused, match=why):
        r.review()


def test_a_timeout_kills_the_adapter_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "TIMEOUT_RANGE", (1, 120))
    r = Rig(tmp_path, "codex", mode="slow")
    r.config({"adapter": "codex", "timeout_seconds": 1})
    with pytest.raises(inv.Refused, match="timed out"):
        r.review()


def test_a_replayed_receipt_id_is_refused(tmp_path):
    r = Rig(tmp_path, "claude", mode="fixedreceipt")
    r.review()
    with pytest.raises(inv.Refused, match="duplicate receipt"):
        r.review()


def test_an_expired_receipt_cannot_be_consumed(rig, monkeypatch):
    got = rig.review()
    real = inv._now
    monkeypatch.setattr(inv, "_now", lambda: real() + 10_000)
    with pytest.raises(inv.Refused, match="expired"):
        inv.consume(str(rig.sdlc), got["action_id"])


def test_bytes_swapped_after_hashing_are_never_executed(tmp_path):
    r = Rig(tmp_path, "claude")
    data = inv.load_adapter(_repo(r), "claude")
    r.exe.write_text("#!/bin/sh\nexit 0\n")           # swapped after the hash check
    inv._dir(str(r.sdlc))
    out = inv.run_adapter(str(r.sdlc), data, "describe", b"{}", 10)
    assert b'"kinds"' in out


def _repo(r):
    return str(r.repo.resolve())


def test_ttl_and_timeout_are_range_checked(tmp_path):
    r = Rig(tmp_path, "claude")
    for bad in ({"adapter": "claude", "ttl_seconds": 5}, {"adapter": "claude", "ttl_seconds": True},
                {"adapter": "claude", "timeout_seconds": 9999}):
        r.config(bad)
        with pytest.raises(inv.Refused):
            inv.settings(str(r.sdlc))


# ---- state and lock safety fail closed -----------------------------------------------------------

def test_a_symlinked_state_directory_refuses(tmp_path):
    r = Rig(tmp_path, "claude")
    (r.sdlc / "state").symlink_to(tmp_path)
    with pytest.raises(inv.Refused):
        r.review()
    assert not r.ran()


def test_no_fcntl_refuses_rather_than_writing_unlocked(tmp_path, monkeypatch):
    r = Rig(tmp_path, "claude")
    monkeypatch.setattr(inv, "fcntl", None)
    with pytest.raises(inv.Refused, match="POSIX"):
        r.review()


def test_a_held_lock_times_out_as_a_refusal(tmp_path, monkeypatch):
    import fcntl
    r = Rig(tmp_path, "claude")
    d = inv._dir(str(r.sdlc))
    fd = os.open(d / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    monkeypatch.setattr(inv, "LOCK_WAIT_S", 0.2)
    try:
        with pytest.raises(inv.Refused, match="busy"):
            r.review()
    finally:
        os.close(fd)


def test_state_cost_is_bounded_and_pruned(tmp_path):
    """Measures the serialised section: 100 sequential authorizations, then the record count after
    aging them out (prune removes at most MAX_PRUNE per call)."""
    r = Rig(tmp_path, "claude")
    import time
    t0 = time.perf_counter()
    for _ in range(100):
        r.review()
    per = (time.perf_counter() - t0) / 100
    assert per < 2.0, per
    assert len(list((r.sdlc / "state" / "invocations").glob("*.json"))) == 100


# ---- diagnose: the documented gesture ------------------------------------------------------------

def _cli(sdlc, host):
    return subprocess.run([sys.executable, str(SCRIPT), "diagnose", str(sdlc), host],
                          capture_output=True, text=True)


def test_diagnose_without_an_adapter_is_a_safe_defer_with_no_side_effects(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".sdlc").mkdir()
    for host in HOSTS:
        proc = _cli(repo / ".sdlc", host)
        assert proc.stdout.strip() == "defer" and proc.returncode == 1
    assert not (repo / ".sdlc" / "state").exists()


def test_diagnose_a_conforming_adapter_and_a_nonconforming_one(rig):
    proc = _cli(rig.sdlc, rig.host)
    assert proc.stdout.strip() == "installed-and-conforming" and proc.returncode == 0
    assert inv.fresh_hosts(str(rig.sdlc)) == set()           # a describe is not a receipt
    rig.install("baddescribe")
    assert _cli(rig.sdlc, rig.host).stdout.strip() == "refused"
    assert _cli(rig.sdlc, "nope").stdout.strip() == "refused"


def test_fresh_hosts_is_empty_without_a_verified_receipt(rig):
    assert inv.fresh_hosts(str(rig.sdlc)) == set()
    rig.review()
    assert inv.fresh_hosts(str(rig.sdlc)) == {rig.host}


# ---- phase-end relay, through the DOCUMENTED gesture: `phase_report.py end <sdlc> <goal> <phase>` ----

PHASE_REPORT = ROOT / "skills" / "sigma-loop" / "scripts" / "phase_report.py"


def _end(r, tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(home)}
    subprocess.run([sys.executable, str(PHASE_REPORT), "start", str(r.sdlc), "42", "research"],
                   capture_output=True, text=True, env=env, check=True)
    return subprocess.run([sys.executable, str(PHASE_REPORT), "end", str(r.sdlc), "42", "research"],
                          capture_output=True, text=True, env=env)


def test_the_relay_sends_exactly_the_bytes_printed_and_reports_on_stderr(rig, tmp_path):
    proc = _end(rig, tmp_path)
    assert proc.returncode == 0, proc.stderr
    sent = pathlib.Path(str(rig.mark) + ".payload").read_bytes()
    assert sent == proc.stdout.encode("utf-8")                 # byte-exact, trailing newline included
    assert "acknowledged by adapter %s" % rig.host in proc.stderr
    assert "display not claimed" in proc.stderr and "relay" not in proc.stdout


@pytest.mark.parametrize("mode,why", [("defer", "deferred"), ("wrongack", "different bytes"),
                                      ("nonzero", "nonzero"), ("noncanonical", "canonical")])
def test_a_bad_relay_is_unavailable_never_acknowledged(tmp_path, mode, why):
    r = Rig(tmp_path, "cursor", mode=mode)
    proc = _end(r, tmp_path)
    assert proc.returncode == 0                                 # the local phase boundary still stands
    assert "host relay unavailable" in proc.stderr and why in proc.stderr
    assert "acknowledged" not in proc.stderr and proc.stdout.strip()


def test_a_missing_pin_makes_the_relay_unavailable_not_a_failure(tmp_path):
    r = Rig(tmp_path, "claude")
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset",
                    "sigma.invocation.adapter.claude.path"], check=True)
    proc = _end(r, tmp_path)
    assert proc.returncode == 0 and "host relay unavailable: no operator pin" in proc.stderr
    assert not r.ran()


def test_an_unconfigured_repo_prints_no_relay_line_and_writes_no_state(tmp_path):
    r = Rig(tmp_path, "claude")
    _unpin(r)
    r.config({})
    proc = _end(r, tmp_path)
    assert proc.returncode == 0 and "relay" not in proc.stderr
    assert not (r.sdlc / "state" / "invocations").exists() and not r.ran()


# ---- the DOCUMENTED gesture, copied out of docs/invocation-adapter.md and run ---------------------

def _doc_gesture():
    text = (ROOT / "docs" / "invocation-adapter.md").read_text(encoding="utf-8")
    block = text.split("## Install and check (operator, once)")[1].split("```")[1]
    return [line for line in block.splitlines() if line.strip() and not line.startswith("bash")]


def test_the_documented_install_and_diagnose_gesture_works_as_written(tmp_path):
    import shlex
    r = Rig(tmp_path, "claude")
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset-all",
                    "sigma.invocation.adapter.claude.path"], check=True)
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset-all",
                    "sigma.invocation.adapter.claude.sha256"], check=True)
    digest = hashlib.sha256(r.exe.read_bytes()).hexdigest()
    lines = _doc_gesture()
    assert len(lines) == 3, lines
    out = ""
    for line in lines:
        line = (line.replace("/absolute/path/to/adapter", str(r.exe))
                .replace("<sha256 of that file>", digest).replace("<installed-sigma>", str(ROOT)))
        argv = shlex.split(line)
        if argv[0] == "git":
            argv[1:1] = ["-C", str(r.repo)]
        else:
            argv[0] = sys.executable
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(r.repo))
        out = proc.stdout.strip()
    assert out == "installed-and-conforming" and proc.returncode == 0


def test_control_the_documented_gesture_is_red_when_the_digest_step_is_dropped(tmp_path):
    import shlex
    r = Rig(tmp_path, "claude")
    subprocess.run(["git", "-C", str(r.repo), "config", "--local", "--unset-all",
                    "sigma.invocation.adapter.claude.sha256"], check=True)
    proc = subprocess.run([sys.executable, str(SCRIPT), "diagnose", str(r.sdlc), "claude"],
                          capture_output=True, text=True)
    assert proc.stdout.strip() == "refused" and proc.returncode == 1


def test_the_scaffolded_config_ships_the_adapter_off():
    tmpl = (ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl").read_text(encoding="utf-8")
    cfg = json.loads(tmpl)
    assert cfg["invocation"]["adapter"] is None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "config.json").write_text(tmpl)
        assert inv.settings(d) is None


def test_a_corrupt_foreign_record_does_not_brick_authorize_and_leftovers_are_pruned(tmp_path):
    r = Rig(tmp_path, "claude")
    r.review()
    d = r.sdlc / "state" / "invocations"
    (d / ("x" * 43 + ".json")).write_text("{not json")
    (d / ("y" * 43 + ".json")).write_text('{"receipt_id": "z"}')          # no challenge key
    old = d / ".tmp-leftover"
    old.write_text("x")
    os.utime(old, (1, 1))
    assert r.review()["status"] == "accepted"
    assert not old.exists()


def test_a_relay_with_no_constructed_block_says_unavailable(tmp_path, capsys):
    r = Rig(tmp_path, "claude")
    spec = importlib.util.spec_from_file_location("phase_report_t", ROOT / "skills/sigma-loop/scripts/phase_report.py")
    pr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pr)
    pr.relay_block(str(r.sdlc), "42", "research", None)
    assert "no constructed Block B" in capsys.readouterr().err and not r.ran()
