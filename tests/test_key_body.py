"""`key-body` (#433), the RED-FIRST half: every test here fails on a3c913c on an assertion and passes on
the new gate, so this whole file is the plan's `## Tests` selector (the #267 red-before-green gate). Node ids are kept short ON PURPOSE:
the gate credits a red only when pytest's `-rA` summary line keeps `- AssertionError`, and pytest trims
that line at the terminal width (80 columns when none is set), so every node id here is <= 53 characters. The
green-on-a3c913c guards (false-positive guards, limit and ambiguity pins, the batch summary, the timing
tests) live in `test_key_body_guards.py`, which imports the helpers below.

Driven through the documented gesture `python3 tools/leak_scan.py` in a scratch git repository (the
`_scratch` helper of `test_leak_scan`). Every fixture is built at run time from a seeded `random.Random`:
random base64 of the real line lengths, plus -- ONLY where a DER header is the subject -- the PUBLIC ASN.1
header bytes of the format (no key material). No whole key-shaped line appears in this file (the gate
scans tests/ too)."""
import base64
import concurrent.futures
import random
import re
import string
import subprocess
import sys

import pytest

from test_leak_scan import GESTURE, _git, _run, _scratch, _write

_B64 = string.ascii_letters + string.digits + "+/"
_PRIV = "-----BEGIN PRIV" + "ATE KEY-----"
_EC_PRIV = "-----BEGIN EC PRIV" + "ATE KEY-----"
_RSA_PRIV = "-----BEGIN RSA PRIV" + "ATE KEY-----"


def _mixed(s):
    return bool(re.search("[A-Z]", s) and re.search("[a-z]", s) and re.search(r"\d", s))


def _lines(widths, seed=433, mixed=True):
    """Random base64 lines of these widths; each has upper+lower+digit and none starts with `M` (so no
    DER header can be read from it -- only the line-count rules and the anchor can fire)."""
    rng = random.Random(seed)
    out = []
    for w in widths:
        while True:
            s = "".join(rng.choice(_B64) for _ in range(w))
            if s[0] != "M" and (_mixed(s) == mixed):
                out.append(s)
                break
    return out


#: name -> (the PUBLIC DER header bytes, total length in bytes). The rest is random.
_DER = {
    "ed25519-pkcs8": ("302e020100300506032b657004220420", 48),
    "x25519-pkcs8": ("302e020100300506032b656e04220420", 48),
    "ed448-pkcs8": ("3047020100300506032b6571043b0439", 73),
    "x448-pkcs8": ("3046020100300506032b6f6f043a0438", 72),
    "ec-p256-sec1": ("30770201010420", 121),
    "ec-p384-sec1": ("30820104020101043000", 192),
    "rsa-pkcs1": ("308204a40201000282010100", 1192),
    "dsa-pkcs1": ("3082025c020100028181", 604),
    "rsa-pkcs8": ("308204bd020100300d06" "092a864886f70d0101010500", 1217),   # split: a 36+ char hex run is read as a key (#451)
    "pkcs8-v2": ("3082013402010130" "0d06092a864886f70d0101010500", 316),
}
_NOT_PRIVATE = {   # first bytes of public / non-key DER: must NOT be read as a private key
    "spki-ec": ("3059301306072a8648ce3d020106082a8648ce3d030107034200", 91),
    "certificate": ("3082035530820", 861),
    "csr": ("308202da308201c2020100", 734),
    "dh-params": ("3082010802820101", 268),
    "pkcs7": ("3082030906092a864886f70d010702", 777),
    "pkcs12": ("308209b1020103308209770609", 2481),
    # explicit EC PARAMETERS (`openssl ecparam -param_enc explicit`): public, but `02 01 01 30` like PKCS8 v2
    "ec-params-p256-explicit": ("3081f7020101302c06072a8648ce3d0101022100ffffffff00000001", 250),
    "ec-params-p384-explicit": ("30820157020101303c06072a8648ce3d0101023100ffffffffffffff", 347),
    "set-0x31": ("31770201010420", 121),   # leading byte 0x31 (a SET) also begins with `M`; not a SEQUENCE
    # an ECDSA signature SEQUENCE{INTEGER r (32 bytes, opening 01 04), INTEGER s}: public. Its INTEGER is
    # longer than one byte, so only the INTEGER-length-1 check keeps (01, 04) from reading as SEC1.
    "ecdsa-sig-int32": ("304402200104", 70),
}


def _der_text(name, table=_DER, seed=7, width=64):
    head, total = table[name]
    rng = random.Random(seed)
    raw = bytes.fromhex(head if len(head) % 2 == 0 else head + "0") + bytes(
        rng.randrange(256) for _ in range(total - len(head) // 2))
    s = base64.b64encode(raw[:total]).decode()
    return [s[i:i + width] for i in range(0, len(s), width)]


def _ec_sec1_base64url(seed=8, width=64):
    """An EC P-256 SEC1 body (public header bytes + seeded random), base64url-encoded. Byte 9 is set to
    0xfb, so character 12 -- inside the 24 characters the DER recogniser decodes -- is `-`: a recogniser
    that drops `-`/`_` from its token class cannot see this line."""
    head, total = _DER["ec-p256-sec1"]
    rng = random.Random(seed)
    raw = bytearray(bytes.fromhex(head) + bytes(rng.randrange(256) for _ in range(total - len(head) // 2)))
    raw[9] = 0xFB
    s = base64.urlsafe_b64encode(bytes(raw)).decode()
    assert s[12] == "-" and s[0] == "M", "the fixture must carry base64url inside the decoded 24 characters"
    return [s[i:i + width] for i in range(0, len(s), width)]


# ---------------------------------------------------------------- the plants, ONE gesture per file
# Each row is (group, id, path, content, expected): content is str (written as `line one\n` + it + `\n`, as
# `_scratch` does, so a body starting the plant is line 2) or bytes (written raw); expected is the EXACT list
# of `<line>: <rule>` findings the gate must print for that path ([] = the file is clean). Every row in THIS
# file is red on a3c913c; the guards file adds the green-on-a3c913c rows and runs the union once more.
_EC = _lines([64, 64, 36])
_ED_RANDOM = _lines([64], seed=5)
_RSA2 = _lines([64, 64], seed=9)
_ED_DER = _der_text("ed25519-pkcs8")
_RSA12 = _lines([64] * 12, seed=11)
_FLAT_RSA = "".join(_der_text("rsa-pkcs1", width=64))
_B31 = _lines([64] * 3, seed=31)
_ROWS = []


def _row(group, rid, content, expected, path=None, rows=_ROWS):
    rows.append((group, rid, path or f"{group}/{rid}.txt", content, expected))


def _kb(*lines):
    return [f"{n}: key-body" for n in lines]


def _shapes(header, body):
    return {"a": ("\n".join([header, "Comment: deploy"] + body), 4),
            "b": (f"Paste the {header} block below.\n\n" + "\n".join(body), 4),
            "c": ("\n".join(body), 2)}


assert len(_ED_DER) == 1 and len(_ED_DER[0]) == 64
for s in "abc":
    _row("shapes", f"ec-p256-{s}", _shapes(_EC_PRIV, _EC)[s][0], _kb(_shapes(_EC_PRIV, _EC)[s][1]))
    _row("shapes", f"ed25519-{s}", _shapes(_PRIV, _ED_DER)[s][0], _kb(_shapes(_PRIV, _ED_DER)[s][1]))
for s in "ab":
    _row("shapes", f"anchor-only-{s}", _shapes(_PRIV, _ED_RANDOM)[s][0], _kb(4))
_row("shapes", "truncated-rsa-a", "\n".join([_RSA_PRIV, "Comment: deploy"] + _RSA2), _kb(4))
_row("shapes", "tail-more-text", "\n".join(_EC + ["", "the end of the paste", "more prose"]), _kb(2))

for i, h in enumerate(["PUBLIC KEY", "CERTIFICATE", "RSA PUBLIC KEY"]):
    _row("adjacent", f"hdr{i}", "\n".join([f"-----BEGIN {h}-----"] + _RSA12), _kb(3))
_row("adjacent", "glued", "\n".join(["-----BEGIN CERTIFICATE-----"] + _lines([64] * 3, seed=3) + _RSA12), _kb(3))

_row("span", "der-in-cert", "\n".join(["-----BEGIN CERTIFICATE-----"] + _der_text("rsa-pkcs1")
                                      + ["-----END CERTIFICATE-----"]), _kb(3))
_row("span", "prose-inside", "\n".join(["-----BEGIN CERTIFICATE-----"] + _B31
                                       + ["the rest is in the portal", "-----END CERTIFICATE-----"]), _kb(3))
_b32 = _lines([64] * 3, seed=32)
_row("span", "mismatched-end", "\n".join(["-----BEGIN CERTIFICATE-----"] + _b32 + ["-----END PUBLIC KEY-----"]), _kb(3))
_row("span", "missing-end", "\n".join(["-----BEGIN CERTIFICATE-----"] + _b32), _kb(3))

#: (label, armor, CRC/tail lines): real public blocks a3c913c flags; the guards file holds the ones it did not.
_PUBLIC = [("pgp-public", "PGP PUBLIC KEY BLOCK", ["Version: GnuPG v2", ""], ["=AbCd"]),
           ("pgp-signature", "PGP SIGNATURE", [""], ["=XyZ1"]), ("pgp-message", "PGP MESSAGE", [""], ["=Q9zA"]),
           ("csr", "CERTIFICATE REQUEST", [], []), ("crl", "X509 CRL", [], []), ("pkcs7", "PKCS7", [], []),
           ("dh", "DH PARAMETERS", [], []), ("x942-dh", "X9.42 DH PARAMETERS", [], [])]


def _public_block(label, armor=(), tail=()):
    body = _lines([64] * 9 + [28], seed=21)
    return "\n".join([f"-----BEGIN {label}-----"] + list(armor) + body + list(tail) + [f"-----END {label}-----"])


for rid, label, armor, tail in _PUBLIC:
    _row("public", rid, _public_block(label, armor, tail), [])
_row("public", "ssh2", "\n".join(["---- BEGIN SSH2 PUBLIC KEY ----", 'Comment: "256-bit ED25519"']
                                          + _lines([70] * 3 + [24], seed=22) + ["---- END SSH2 PUBLIC KEY ----"]), [])

for tail in (28, 39):
    _row("boundary", f"{tail}", "\n".join(_lines([64, 64, tail], seed=60 + tail)), _kb(2))


def _tolerance_body(n_bad, n_lines, tail):
    body = _lines([64] * n_lines + ([tail] if tail else []), seed=80 + n_lines)
    for i in range(n_bad):
        body[i] = re.sub(r"\d", "q", body[i])
    return "\n".join(body)


for n_bad, n_lines, tail in [(1, 3, None), (1, 12, None), (0, 2, 36)]:
    _row("tolerance", f"{n_bad}of{n_lines}" + (f"-tail{tail}" if tail else ""), _tolerance_body(n_bad, n_lines, tail), _kb(2))
for sep_name, sep in (("crlf", "\r\n"), ("lf", "\n")):
    for pad_name, pad in (("none", ""), ("spaces", "  "), ("tab", "\t")):
        _row("eol", f"{sep_name}-{pad_name}", ("line one" + sep + sep.join(pad + ln + pad for ln in _EC) + sep).encode(), _kb(2))
_row("eol", "lone-cr-body", ("one\rtwo\r" + "\r".join(_EC) + "\r").encode(), _kb(3))
_row("eol", "lone-cr-home-path", ("one\rtwo\rsee /Us" + "ers/jdoe" + "smith/projects/app\r").encode(), ["3: home-path"])


def _gap_text(gap):
    armor = ["Proc-Type: 4,ENCRYPTED", "DEK-Info: AES-128-CBC,00", "", "", "Comment: x", "", "Key: v", ""][:gap]
    armor += [""] * (gap - len(armor))
    return "\n".join([_PRIV] + armor + _ED_RANDOM)


for gap in (0, 1, 3, 8):
    _row("anchor", f"gap{gap}", _gap_text(gap), _kb(3 + gap))

for name in sorted(_DER):
    _row("der", name, _der_text(name)[0], _kb(2))
_row("der", "openssh", "b3BlbnNzaC1rZXktdjEA" + "".join(random.Random(3).choice(_B64) for _ in range(50)), _kb(2))
for wrap in [24, 28, 32, 36, 39]:
    _row("der", f"rewrap{wrap}", "\n".join(_FLAT_RSA[i:i + wrap] for i in range(0, 400, wrap)), _kb(2))
for i, (lead, trail) in enumerate([("# ", ""), ("> ", ""), ("// ", ""), (" * ", ""), ("; ", ""), ('"', '",'),
                                   ("'", "'"), ('["', '",'), ("            \"", '"'), ("- ", ""), ("    - \"", '"'),
                                   ("", "  # trailing text")]):
    _row("der", f"behind{i}", lead + _ED_DER[0] + trail, _kb(2))
_row("der", "base64url-ec-sec1", "\n".join(_ec_sec1_base64url()), _kb(2))
_c07 = _der_text("rsa-pkcs1")[:6]
_row("der", "blank-every-2", "\n".join(_c07[0:2] + [""] + _c07[2:4] + [""] + _c07[4:6]), _kb(2))
_row("der", "allow-marker", _ED_DER[0] + "  # leak-scan" + ": allow key-body test vector from the format spec\n"
     + _ED_DER[0], _kb(3))
_row("mixed", "ec-under-header", "\n".join([_EC_PRIV, ""] + _EC), _kb(4))

# #449: a DER-led private key may be carried inside a JSON string, a quoted shell value, or a
# YAML-like assignment. All use the same runtime-built public DER header; old line-start matching
# misses them through the documented gesture.
for rid, line in (("json", '{{"key":"{b}"}}'), ("shell", "KEY='{b}'"), ("yaml", "key: {b}")):
    _row("embedded", rid, line.format(b=_ED_DER[0]), _kb(2))

# A JSON carrier holds a newline as a LITERAL backslash + n/r/t, so the character before the token is the
# escape's letter, not a boundary. Runtime-built; nothing contiguous.
for rid, esc in (("json-esc-n", "\\n"), ("json-esc-r", "\\r"), ("json-esc-t", "\\t"), ("json-esc-rn", "\\r\\n")):
    _row("embedded", rid, '{{"k":"x{e}{b}"}}'.format(e=esc, b=_ED_DER[0]), _kb(2))
_row("der", "json-esc-n-public", '{{"k":"x{e}{b}"}}'.format(e="\\n", b=_der_text("spki-ec", _NOT_PRIVATE)[0]), [])


def _run_batch(tmp_path_factory, rows):
    """ONE scratch repo holding every row, ONE run of the documented gesture: (proc, {path: ["<line>: <rule>"]})."""
    repo = _scratch(tmp_path_factory.mktemp("batch"))
    for _g, _i, path, content, _e in rows:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text("line one\n" + content + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    proc = _run(repo)
    found = {}
    for ln in proc.stdout.splitlines():
        m = re.match(r"(.+?):(\d+): (\S+)", ln)
        if m:
            found.setdefault(m.group(1), []).append(f"{m.group(2)}: {m.group(3)}")
    return proc, found


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    return _run_batch(tmp_path_factory, _ROWS)


def _ids(group, rows=_ROWS):
    return [pytest.param(path, exp, id=rid) for g, rid, path, _c, exp in rows if g == group]


def _check(batch, path, expected):
    """A green row has NO finding of any rule; a red row has exactly `expected` among its findings of the
    rules `expected` names (another rule, e.g. scrub's `private-key` on a terminated block, may also fire)."""
    proc, found = batch
    rules = {e.split(": ")[1] for e in expected}
    got = sorted(f for f in found.get(path, []) if not expected or f.split(": ")[1] in rules)
    assert got == sorted(expected), f"{path}: {found.get(path)} != {expected}"


@pytest.mark.parametrize("path,expected", _ids("shapes"))
def test_shape(batch, path, expected):
    """Shapes (a) BEGIN+armor+body, no END; (b) prose naming the header + blank + body; (c) the body alone:
    EC P-256 (64, 64, 36), Ed25519 (one DER-led line), a non-DER line under an anchor, truncated RSA."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("adjacent"))
def test_adjacent(batch, path, expected):
    """A private-shaped body under an adjacent public header (no END) is red."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("span"))
def test_span(batch, path, expected):
    """Outside a matching BEGIN..END public span, or with a DER private start inside one, a body is red."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("public"))
def test_public(batch, path, expected):
    """Real public PGP / CSR / CRL / PKCS7 / DH / SSH2 blocks, which a3c913c flagged, are green."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("boundary"))
def test_tail(batch, path, expected):
    """Two full mixed lines and a 28-39 char tail are red."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("tolerance"))
def test_tolerance(batch, path, expected):
    """One digit-less line is tolerated in a 3+ line block; none in a 2-line + tail block."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("eol"))
def test_eol(batch, path, expected):
    """CRLF, lone CR and outer whitespace."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("anchor"))
def test_anchor(batch, path, expected):
    """The PRIVATE-header anchor reaches through 0..8 blank or `Key: value` lines."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("der"))
def test_der(batch, path, expected):
    """The DER first-line recogniser: every private format, OpenSSH, re-wraps, prefixes, base64url."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("mixed"))
def test_mixed(batch, path, expected):
    """The anchor flags an all-mixed body under a header and a blank line."""
    _check(batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("embedded"))
def test_embedded(batch, path, expected):
    """A DER private-key token inside common one-line carriers is red through the documented gesture."""
    _check(batch, path, expected)


# ---------------------------------------------------------------- mutation controls (run the control)
# On a3c913c each control fails on an ASSERTION: `_mutate`'s seam-count assert (the seam does not exist
# there) or, earlier, the control's own "the correct gate flags this" assert.
def _mutate(repo, old, new):
    gate = repo / "tools" / "leak_scan.py"
    src = gate.read_text(encoding="utf-8")
    assert src.count(old) == 1, f"mutation seam {old!r} must appear exactly once, else the control is vacuous"
    gate.write_text(src.replace(old, new), encoding="utf-8")


_SEAMS = {
    "anchor": ("_GAP = 8", "_GAP = -1"),
    "tail": ("{28,39}", "{99,99}"),
    "der": ("_DER_PRIVATE = {(0, 0x30), (0, 0x02), (1, 0x04), (1, 0x30)}", "_DER_PRIVATE = set()"),
    "span": ('return "PRIVATE" in label.upper() or "SECRET" in label.upper()', "return True"),
    "lone-cr": ('_LONE_CR = re.compile(r"\\r(?!\\n)")', '_LONE_CR = re.compile(r"(?!)")'),
    "tolerance": ("(n >= 3 and bad <= 1)", "(n >= 3 and bad <= 0)"),
    "pem-begin": ('_PEM_BEGIN = re.compile(r"BEGIN ([^\\r\\n-]{1,64}?) ?-{4,5}")',
                  '_PEM_BEGIN = re.compile(r"(?m)-{4,5} ?BEGIN (.*?) ?-{4,5}[ \\t]*\\r?$")'),
    "span-line": ('_SPAN_LINE = re.compile(r"[ \\t]*(?:(?:[A-Za-z0-9+/=]+|\\.{3}|\\u2026)[ \\t]*|[A-Za-z][A-Za-z0-9 -]*:[^\\r\\n]*)?\\r?$")',
                  '_SPAN_LINE = re.compile(r"[ \\t]*(?:[A-Za-z0-9+/=]+|\\.{3}|\\u2026|[A-Za-z][A-Za-z0-9 -]*:[^\\r\\n]*)?[ \\t]*\\r?$")'),
    "embedded": ('for m in _DER_TOKEN.finditer(text):', 'for m in ():')
}
_CA = "\n".join(["-----BEGIN CERTIFICATE-----"] + [re.sub(r"\d", "q", _lines([64] * 3 + [27], 90)[0])]
                + _lines([64] * 2 + [27], 91) + ["-----END CERTIFICATE-----"])


def _flip(tmp_path, text, seams, line, where="k.txt"):
    repo = _scratch(tmp_path, text, where)
    proc = _run(repo)
    before = (proc.returncode, f"{where}:{line}: key-body" in proc.stdout)
    for s in seams:
        _mutate(repo, *_SEAMS[s])
    return before, _run(repo)


@pytest.mark.parametrize("name,text,line,seams", [
    pytest.param("shape-a anchor-only", "\n".join([_PRIV, "Comment: deploy"] + _ED_RANDOM), 4, ["anchor"], id="anchor"),
    pytest.param("shape-c EC tail-only", "\n".join(_EC), 2, ["tail"], id="tail"),
    pytest.param("shape-c Ed25519 DER-only", "\n".join(_ED_DER), 2, ["der"], id="der"),
])
def test_control_disarm(tmp_path, name, text, line, seams):
    """Disarming each mechanism turns the shape only it catches clean."""
    before, after = _flip(tmp_path, text, seams, line)
    assert before == (1, True), name
    assert after.returncode == 0 and "key-body" not in after.stdout, name + after.stdout


def test_control_reopen_a(tmp_path):
    """Acceptance: a mutation that re-opens shape (a) -- anchor, tail rule and DER header all disarmed in
    the scratch COPY -- turns every (a) fixture clean while the 3+ line rule (never broken) still fires."""
    plants = {"ec": _shapes(_EC_PRIV, _EC)["a"], "ed25519": _shapes(_PRIV, _ED_DER)["a"],
              "rsa-trunc": ("\n".join([_RSA_PRIV, "Comment: deploy"] + _RSA2), 4)}
    for name, (text, line) in plants.items():
        before, after = _flip(tmp_path / name, text, ["anchor", "tail", "der"], line)
        assert before == (1, True), name
        assert after.returncode == 0 and "key-body" not in after.stdout, name
    control = "\n".join([_RSA_PRIV, "Comment: deploy"] + _RSA12[:6])
    repo = _scratch(tmp_path / "ctl", control, "k.txt")
    for s in ("anchor", "tail", "der"):
        _mutate(repo, *_SEAMS[s])
    assert "k.txt:4: key-body" in _run(repo).stdout


def test_control_lone_cr(tmp_path):
    """The lone-CR normalisation is load-bearing."""
    repo = _scratch(tmp_path)
    _write(repo, "k.txt", ("one\rtwo\r" + "\r".join(_EC) + "\r").encode())
    assert _run(repo).returncode == 1
    _mutate(repo, *_SEAMS["lone-cr"])
    assert _run(repo).returncode == 0


def test_control_span(tmp_path):
    """Disarming the span exemption turns a CA bundle red."""
    repo = _scratch(tmp_path, _CA, "certs/ca.crt")
    assert _run(repo).returncode == 0
    _mutate(repo, *_SEAMS["span"])
    proc = _run(repo)
    assert proc.returncode == 1 and "certs/ca.crt:3: key-body" in proc.stdout, proc.stdout


def test_control_tolerance(tmp_path):
    """The tolerance of one digit-less line is load-bearing."""
    body = _lines([64] * 5, seed=95)
    body[2] = re.sub(r"\d", "q", body[2])
    repo = _scratch(tmp_path, "\n".join(body), "k.txt")
    assert _run(repo).returncode == 1
    _mutate(repo, *_SEAMS["tolerance"])
    assert _run(repo).returncode == 0


def test_control_embedded_token(tmp_path):
    """The token iterator, not an accidentally broad line-start rule, catches JSON carriers."""
    repo = _scratch(tmp_path, '{{"key":"{}"}}'.format(_ED_DER[0]), "embedded.json")
    assert "embedded.json:2: key-body" in _run(repo).stdout
    _mutate(repo, *_SEAMS["embedded"])
    assert "key-body" not in _run(repo).stdout


# ---------------------------------------------------------------- linear time: ONE budget for test and control
#: The timeout of the timing tests (guards file) AND of their control here: the same gesture, the same number.
#: Correct code on a 256 KB plant measured about 0.2-0.3 s CPU (1 s wall at load average 37); the quadratic
#: forms need minutes.
_BUDGET = 10


def _within_budget(repo):
    return subprocess.run([sys.executable, *GESTURE], cwd=str(repo), capture_output=True, text=True,
                          timeout=_BUDGET)


def _flood_repo(tmp_path):
    """256 KB of `-----BEGIN ` on ONE line, then a one-line block (reaches `_anchored`) and a counted
    block (reaches `_public_spans`). A flood with no candidate line would pass vacuously."""
    flood = "-----BEGIN " * (256 * 1024 // 11)
    return _scratch(tmp_path, "\n".join([flood, _lines([64], seed=5)[0], ""] + _lines([64] * 3, seed=6)), "k.txt")


def _ws_span_repo(tmp_path):
    """A counted block (so `_public_spans` runs) and, under a public BEGIN line, 256 KB of spaces then `x!`."""
    text = "\n".join(["-----BEGIN CERTIFICATE-----", " " * (256 * 1024) + "x!", "-----END CERTIFICATE-----"]
                     + _lines([64] * 3, seed=8))
    return _scratch(tmp_path, text, "w.txt")


def _assert_flood_is_linear(repo):
    proc = _within_budget(repo)
    assert proc.returncode == 1 and "k.txt:5: key-body" in proc.stdout, proc.stdout


def _assert_whitespace_span_is_linear(repo):
    proc = _within_budget(repo)
    assert proc.returncode == 1 and "w.txt:5: key-body" in proc.stdout, proc.stdout


def test_control_quadratic(tmp_path):
    """Run the control: the SAME assertion functions, the SAME gesture and `_BUDGET` as the guards file's
    timing tests, on scratch copies whose seam holds the quadratic form. Both run at once (two threads),
    so the control costs one budget of wall time, not two."""
    flood, ws = _flood_repo(tmp_path / "flood"), _ws_span_repo(tmp_path / "ws")
    _mutate(flood, *_SEAMS["pem-begin"])
    _mutate(ws, *_SEAMS["span-line"])

    def outcome(check, repo):
        try:
            check(repo)
        except subprocess.TimeoutExpired:
            return "timed out"
        return "returned in budget"
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        got = list(pool.map(outcome, [_assert_flood_is_linear, _assert_whitespace_span_is_linear], [flood, ws]))
    assert got == ["timed out", "timed out"], got
