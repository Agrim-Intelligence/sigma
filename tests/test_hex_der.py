"""Hex-encoded and single-line encrypted private-key openings in `tools/leak_scan.py` (#451).

`key-body` recognised a private key's DER header only in BASE64. Four shapes got through, each planted
and run through the documented gesture `python3 tools/leak_scan.py` (#433's undetected-shape run): a
hex-encoded body, the hex first line of a PKCS1 key, and the lone first line of an encrypted PKCS8 and of
a PKCS12. This file plants each of them, plus a header-less armored PGP private-key first line, and the
public lookalikes that must stay clean.

Every plant is built at run time from a spaced public header (`bytes.fromhex` ignores the spaces, and the
source then holds no contiguous hex token the gate would read) plus seeded random bytes; nothing secret
is a literal here, and every row asserts that no planted value reaches the output.

All rows run in ONE scratch repository (one gesture, one run), as `test_key_body` does. A row is
`(group, id, content, expected)`: `expected` is the exact list of `<line>: <rule>` findings for that
path, `[]` meaning the file must be clean.

The plan's `## Tests` selector is the functions whose every row goes red on an assertion against the
pre-change gate: `test_hex`, `test_encrypted`, `test_pkcs12`, `test_pgp`, `test_encoded`. `test_public`
and `test_control_*` pass before the change as well; they pin the false-positive side."""
import base64
import codecs
import importlib.util
import random

import pytest

from test_leak_scan import ROOT, _git, _run, _scratch, _write


def _blob(head, total, seed):
    """Public DER header bytes (spaced hex) followed by seeded random bytes, `total` long."""
    raw = bytes.fromhex(head)
    rng = random.Random(seed)
    return raw + bytes(rng.randrange(256) for _ in range(total - len(raw)))


# name -> (public header bytes, total length). The remainder of each blob is random.
_PRIVATE = {
    "pkcs1": ("30 82 04 a4 02 01 00 02 82 01 01 00", 1192),
    "pkcs8": ("30 82 04 bd 02 01 00 30 0d 06 09 2a 86 48 86 f7 0d 01 01 01 05 00", 1217),
    "pkcs8-ed25519": ("30 2e 02 01 00 30 05 06 03 2b 65 70 04 22 04 20", 48),
    "sec1-p256": ("30 77 02 01 01 04 20", 121),
    "sec1-p384": ("30 82 01 04 02 01 01 04 30 00", 192),
    "dsa": ("30 82 02 5c 02 01 00 02 81 81 00", 604),
    "pkcs8-v2": ("30 82 01 34 02 01 01 30 0d 06 09 2a 86 48 86 f7 0d 01 01 01 05 00", 316),
}
_ENCRYPTED = {   # EncryptedPrivateKeyInfo: SEQUENCE { SEQUENCE { OID <PBE scheme> ...
    "pbes2": ("30 82 06 4c 30 4e 06 09 2a 86 48 86 f7 0d 01 05 0d 30 41 30 29 06 09 2a 86 48 86 f7 0d 01 "
              "05 0c 30 1c 04 08", 1616),
    "pbes2-short-len": ("30 81 f1 30 4e 06 09 2a 86 48 86 f7 0d 01 05 0d 30 41 30 29 06 09 2a 86 48 86 "
                        "f7 0d 01 05 0c", 244),
    "pbes2-inner-81": ("30 82 06 4c 30 81 8e 06 09 2a 86 48 86 f7 0d 01 05 0d 30 41 30 29 06 09 2a 86 48 "
                       "86 f7 0d 01 05 0c 30 1c 04 08", 1616),
    "pbe-sha1-rc2": ("30 82 04 2b 30 1b 06 09 2a 86 48 86 f7 0d 01 05 0a 30 0e 04 08", 1071),
    "pbe-3des": ("30 82 04 2b 30 1b 06 0a 2a 86 48 86 f7 0d 01 0c 01 03 30 0d 04 08", 1071),
    "pbe-md5-des": ("30 82 02 3c 30 1b 06 09 2a 86 48 86 f7 0d 01 05 03 30 0e 04 08", 576),
}
_PKCS12 = {      # PFX: SEQUENCE { INTEGER 3, SEQUENCE { OID data ...
    "der": ("30 82 09 b1 02 01 03 30 82 09 77 06 09 2a 86 48 86 f7 0d 01 07 01 a0 82 09 68 04 82 09 64",
            2481),
    "short-authsafe": ("30 82 09 b1 02 01 03 30 81 f0 06 09 2a 86 48 86 f7 0d 01 07 01 a0 81 e5 04 81 e2",
                       2481),
    "ber-indefinite": ("30 80 02 01 03 30 80 06 09 2a 86 48 86 f7 0d 01 07 01 a0 80 24 80 04 82 09 64",
                       2481),
}
_PGP_PRIVATE = {  # a secret-key (tag 5) or secret-subkey (tag 7) packet, version 4 or 6
    "rsa-old": ("95 01 d8 04 65 3f 2a 11 01 08 00", 480),
    "rsa-old-1len": ("94 e6 04 65 3f 2a 11 01 08 00", 232),
    "rsa-subkey-old": ("9d 01 fe 04 65 3f 2a 11 01 08 00", 510),
    "ed25519-new": ("c5 5b 04 65 3f 2a 11 16 09 2b 06 01 04 01 da 47 0f 01 01 07", 93),
    "ed25519-v6-new": ("c5 5d 06 65 3f 2a 11 1b 00 00 00 20", 95),
    "ecdh-subkey-new": ("c7 70 04 65 3f 2a 11 12 0a 2b 81 04 00 23", 114),
}
_PUBLIC = {       # public or non-key structures that must NEVER be read as a private key
    "spki-rsa": ("30 82 01 22 30 0d 06 09 2a 86 48 86 f7 0d 01 01 01 05 00 03 82 01 0f 00", 294),
    "spki-ec": ("30 59 30 13 06 07 2a 86 48 ce 3d 02 01 06 08 2a 86 48 ce 3d 03 01 07 03 42 00", 91),
    "certificate": ("30 82 03 55 30 82 02 3d a0 03 02 01 02 02 14", 861),
    "csr": ("30 82 02 da 30 82 01 c2 02 01 00 30 45", 734),
    "crl": ("30 82 01 5a 30 81 c3 02 01 01 30 0d 06 09 2a 86 48 86 f7 0d 01 01 0b 05 00", 350),
    "pkcs7-data": ("30 82 03 09 06 09 2a 86 48 86 f7 0d 01 07 01 a0 82 02 fa", 777),
    "pkcs7-signed": ("30 82 03 09 06 09 2a 86 48 86 f7 0d 01 07 02 a0 82 02 fa", 777),
    "pkcs12-signed": ("30 82 09 b1 02 01 03 30 82 09 77 06 09 2a 86 48 86 f7 0d 01 07 02 a0 82 09 68",
                      2481),
    "pbkdf2-only": ("30 82 01 22 30 0d 06 09 2a 86 48 86 f7 0d 01 05 0c 30 08 04 04 00 00 00 00", 294),
    "ec-params": ("30 81 f7 02 01 01 30 2c 06 07 2a 86 48 ce 3d 01 01 02 21 00 ff ff ff ff 00 00 00 01",
                  250),
    "int3-then-int": ("30 82 09 b1 02 01 03 02 82 09 77 06 09 2a 86 48 86 f7 0d 01 07 01", 2481),
    "pgp-public-old": ("99 01 a2 04 65 3f 2a 11 01 08 00", 420),
    "pgp-public-new": ("c6 33 04 65 3f 2a 11 16 09 2b 06 01 04 01 da 47 0f 01 01 07", 53),
    "pgp-signature": ("89 01 33 04 00 01 08 00 06 05 02 65", 308),
    # a digest that opened `9e d0 57 9d 35 02 c9 ...` read as a PGP v2 key before the material checks (one sha256
    # in a lock file of the 97-file corpus): old-format subkey tag, 4-octet length, version 2, algo 18
    "digest-as-pgp-v2": ("9e d0 57 9d 35 02 c9 4b 4b 37 32 ac 12 03 75 cd a9 6f 92 31 14 52 28 47", 32),
    "pgp-secret-mpi-not-whole-bytes": ("95 01 d8 04 65 3f 2a 11 01 08 01", 480),
    "pgp-secret-v3-not-read": ("95 01 d8 03 65 3f 2a 11 00 00 01 08 00", 480),
    "pgp-secret-bad-mpi": ("95 01 d8 04 65 3f 2a 11 01 00 10", 480),
    "pgp-secret-rsa-mpi-too-big": ("95 01 d8 04 65 3f 2a 11 01 ff ff", 480),
    "pgp-secret-big-length": ("95 ff d8 04 65 3f 2a 11 01 08 00", 480),
    "pgp-secret-ec-bad-oid": ("c5 5b 04 65 3f 2a 11 16 09 11 06 01 04 01 da 47 0f 01 01 07", 93),
    "pgp-secret-v6-material-len": ("c5 5d 06 65 3f 2a 11 1b 12 34 00 20", 95),
    "pgp-secret-v4-x25519": ("c5 5b 04 65 3f 2a 11 19 00 00 00 20", 93),
    "pgp-secret-bad-algo": ("95 01 d8 04 65 3f 2a 11 7e 08 00", 480),
    "x509-v3-int": ("30 82 03 01 30 82 02 a0 a0 03 02 01 02 02 09", 773),
}


def _wrap(s, n):
    return [s[i:i + n] for i in range(0, len(s), n)]


def _hex_lines(table, name, width=60, upper=False, seed=1):
    head, total = table[name]
    s = _blob(head, total, seed).hex()
    return _wrap(s.upper() if upper else s, width)


def _b64_lines(table, name, width=64, seed=1):
    head, total = table[name]
    return _wrap(base64.b64encode(_blob(head, total, seed)).decode(), width)


_ROWS = []
_VALUES = []


def _row(group, rid, content, expected, encoding=None, red=False):
    """`content` is text (written UTF-8, or `encoding`) or bytes; the planted values are remembered so
    the run can assert none reaches the output."""
    path = f"{group}/{rid}.md"
    _ROWS.append((group, rid, path, content, expected, encoding))   # `red`: documentation only
    return path


def _plant(*lines):
    return "\n".join(lines) + "\n"


def _remember(lines):
    _VALUES.extend(ln for ln in lines if len(ln) >= 40)
    return lines


# ---------------------------------------------------------------- hex: the first line of a private key
for _name in _PRIVATE:
    _h = _remember(_hex_lines(_PRIVATE, _name))
    _row("hex", f"{_name}-flat", _plant("line one", "".join(_h)), ["2: key-body"])
    _row("hex", f"{_name}-body6", _plant(*_h[:6]), ["1: key-body"])
    _row("hex", f"{_name}-upper", _plant(*(ln.upper() for ln in _h[:6])), ["1: key-body"])
_pk8 = _remember(_hex_lines(_PRIVATE, "pkcs8"))
_pk1 = _remember(_hex_lines(_PRIVATE, "pkcs1", width=64))
_row("hex", "pkcs1-width-96", _plant(*_remember(_hex_lines(_PRIVATE, "pkcs1", width=96))[:6]),
     ["1: key-body"])
_row("hex", "quoted-lines", _plant(*(f'"{ln}",' for ln in _pk1[:6])), ["1: key-body"])
_row("hex", "comment-prefixed", _plant(*(f"# {ln}" for ln in _pk1[:6])), ["1: key-body"])
_row("hex", "indented", _plant(*(f"    {ln}" for ln in _pk1[:6])), ["1: key-body"])
_row("hex", "json-string", '{"blob": "' + "".join(_pk1) + '"}\n', ["1: key-body"])
_row("hex", "json-escaped-newline", '{"blob": "abc\\n' + "".join(_pk1) + '"}\n', ["1: key-body"])
_row("hex", "shell-assignment", "export BLOB=" + "".join(_pk8) + "\n", ["1: key-body"])
_row("hex", "0x-prefix", "BLOB = 0x" + "".join(_pk8) + "\n", ["1: key-body"])
_row("hex", "after-text", "payload follows " + "".join(_pk1) + " end\n", ["1: key-body"])
_row("hex", "mixed-case", "".join(c.upper() if i % 3 else c for i, c in enumerate("".join(_pk1[:2]))) + "\n",
     ["1: key-body"])

# ---------------------------------------------------------------- encrypted PKCS8: a lone first line
for _name in _ENCRYPTED:
    _b = _remember(_b64_lines(_ENCRYPTED, _name))
    _h = _remember(_hex_lines(_ENCRYPTED, _name))
    _row("encrypted", f"{_name}-b64-first-line", _plant("line one", _b[0]), ["2: key-body"])
    _row("encrypted", f"{_name}-b64-flat", _plant("".join(_b)), ["1: key-body"])
    _row("encrypted", f"{_name}-hex-first-line", _plant("line one", _h[0]), ["2: key-body"])
    _row("encrypted", f"{_name}-hex-body6", _plant(*_h[:6]), ["1: key-body"])
_e = _remember(_b64_lines(_ENCRYPTED, "pbes2"))
_row("encrypted", "pbes2-b64-quoted", _plant(f'  "{_e[0]}",'), ["1: key-body"])
_row("encrypted", "pbes2-b64-json", '{"blob": "' + "".join(_e) + '"}\n', ["1: key-body"])
_row("encrypted", "pbes2-b64-url-alphabet",
     _plant(*_remember(_wrap(base64.urlsafe_b64encode(
         _blob(_ENCRYPTED["pbes2"][0], 1616, 5)).decode(), 64))[:1]), ["1: key-body"])

# ---------------------------------------------------------------- PKCS12: a lone first line
for _name in _PKCS12:
    _b = _remember(_b64_lines(_PKCS12, _name))
    _h = _remember(_hex_lines(_PKCS12, _name))
    _row("pkcs12", f"{_name}-b64-first-line", _plant("line one", _b[0]), ["2: key-body"])
    _row("pkcs12", f"{_name}-hex-first-line", _plant("line one", _h[0]), ["2: key-body"])
    _row("pkcs12", f"{_name}-b64-76", _plant(*_remember(_b64_lines(_PKCS12, _name, width=76))[:1]),
         ["1: key-body"])
_row("pkcs12", "b64-json", '{"blob": "' + "".join(_remember(_b64_lines(_PKCS12, "der"))) + '"}\n',
     ["1: key-body"])

# ---------------------------------------------------------------- PGP private key: a header-less first line
for _name in _PGP_PRIVATE:
    _b = _remember(_b64_lines(_PGP_PRIVATE, _name))
    _row("pgp", f"{_name}-b64-first-line", _plant("line one", _b[0]), ["2: key-body"])
    _row("pgp", f"{_name}-b64-quoted", _plant(f'"{_b[0]}",'), ["1: key-body"])
    _row("pgp", f"{_name}-hex-first-line",
         _plant("line one", _remember(_hex_lines(_PGP_PRIVATE, _name))[0]), ["2: key-body"])
_sub = _remember(_b64_lines(_PGP_PRIVATE, "rsa-subkey-old"))
assert _sub[0][0] == "n", "the subkey packet must open with `n`, the letter of a JSON `\\n` escape"
for _rid, _esc in (("n", "\\n"), ("rn", "\\r\\n"), ("t", "\\t"), ("double", "\\\\n")):
    _row("pgp", f"subkey-json-esc-{_rid}", '{"blob": "x' + _esc + _sub[0] + '"}\n', ["1: key-body"])
_row("pgp", "rsa-old-json", '{"blob": "' + "".join(_remember(_b64_lines(_PGP_PRIVATE, "rsa-old"))) + '"}\n',
     ["1: key-body"])

# ---------------------------------------------------------------- public lookalikes: clean
for _name in _PUBLIC:
    _b = _b64_lines(_PUBLIC, _name)
    _h = _hex_lines(_PUBLIC, _name)
    _row("public", f"{_name}-b64", _plant("line one", _b[0], "line three"), [])
    _row("public", f"{_name}-hex", _plant("line one", _h[0], "line three"), [])
    _row("public", f"{_name}-hex-flat", _plant("blob = " + "".join(_h)), [])


def _digests(n, size, seed):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(size)).hex() for _ in range(n)]


_row("public", "sha256-list", _plant(*(f"{d}  file{i}.tar" for i, d in enumerate(_digests(30, 32, 3)))), [])
_row("public", "sha256-bare-lines", _plant(*_digests(30, 32, 4)), [])
_row("public", "sha512-bare-lines", _plant(*_digests(12, 64, 5)), [])
_row("public", "md5-and-sha1", _plant(*_digests(10, 16, 6), *_digests(10, 20, 7)), [])
# every digest in this row opens with the byte that begins a DER SEQUENCE, then a plausible length
_row("public", "digests-opening-30", _plant(*("30" + d[2:] for d in _digests(20, 32, 8))), [])
_row("public", "digests-opening-3082", _plant(*("3082" + d[4:] for d in _digests(20, 32, 9))), [])
_row("public", "digests-opening-95", _plant(*("95" + d[2:] for d in _digests(20, 32, 10))), [])
_row("public", "uuid-and-short-hex", _plant("id 30820201-0000-4000-8000-000000000000", "color #308204", "x = 0x3082"),
     [])
_row("public", "base64-prose", _plant("the quick brown fox jumps over the lazy dog " * 3), [])

# ---------------------------------------------------------------- the floors: below them a head is not readable
_row("floor", "pbes2-b64-width-24", _plant(*_remember(_b64_lines(_ENCRYPTED, "pbes2", width=24))[:1]),
     ["1: key-body"], red=True)
_row("floor", "pbes2-b64-width-23", _plant(*_wrap(base64.b64encode(_blob(*_ENCRYPTED["pbes2"], 1)).decode(), 23)), [])
_row("floor", "pbes2-hex-width-36", _plant(*_remember(_hex_lines(_ENCRYPTED, "pbes2", width=36))[:1]),
     ["1: key-body"], red=True)
_row("floor", "pbes2-hex-width-35", _plant(*_hex_lines(_ENCRYPTED, "pbes2", width=35)), [])
_row("floor", "pkcs12-b64-width-32", _plant(*_remember(_b64_lines(_PKCS12, "der", width=32))[:1]),
     ["1: key-body"], red=True)
_row("floor", "pkcs12-b64-width-28-oid-cut", _plant(*_b64_lines(_PKCS12, "der", width=28)), [])
_row("floor", "pkcs12-hex-width-44", _plant(*_remember(_hex_lines(_PKCS12, "der", width=44))[:1]),
     ["1: key-body"], red=True)
_row("floor", "pkcs12-hex-width-40-oid-cut", _plant(*_hex_lines(_PKCS12, "der", width=40)), [])
_row("floor", "pkcs1-hex-width-36", _plant(*_remember(_hex_lines(_PRIVATE, "pkcs1", width=36))[:1]),
     ["1: key-body"], red=True)
_row("floor", "pkcs1-hex-width-34", _plant(*_hex_lines(_PRIVATE, "pkcs1", width=34)), [])

# ---------------------------------------------------------------- text encodings (composition with #450)
_t = "line one\n" + "".join(_pk1[:1]) + "\n"
_row("encoded", "hex-utf16-le-bom", codecs.BOM_UTF16_LE + _t.encode("utf-16-le"), ["2: key-body"])
_row("encoded", "hex-utf16-be-bom", codecs.BOM_UTF16_BE + _t.encode("utf-16-be"), ["2: key-body"])
_row("encoded", "hex-utf32-le-bom", codecs.BOM_UTF32_LE + _t.encode("utf-32-le"), ["2: key-body"])
_row("encoded", "hex-utf32-be-bom", codecs.BOM_UTF32_BE + _t.encode("utf-32-be"), ["2: key-body"])
_t = "line one\n" + _remember(_b64_lines(_ENCRYPTED, "pbes2"))[0] + "\n"
_row("encoded", "pbes2-utf16-le-bom", codecs.BOM_UTF16_LE + _t.encode("utf-16-le"), ["2: key-body"])
_row("encoded", "pbes2-utf32-le-bom", codecs.BOM_UTF32_LE + _t.encode("utf-32-le"), ["2: key-body"])
_t = "line one\n" + _remember(_b64_lines(_PGP_PRIVATE, "rsa-old"))[0] + "\n"
_row("encoded", "pgp-utf32-be-bom", codecs.BOM_UTF32_BE + _t.encode("utf-32-be"), ["2: key-body"])


@pytest.fixture(scope="module")
def batch(tmp_path_factory):
    repo = _scratch(tmp_path_factory.mktemp("hex"))
    for _group, _rid, path, content, _expected, encoding in _ROWS:
        if isinstance(content, str) and encoding:
            content = content.encode(encoding)
        _write(repo, path, content)
    proc = _run(repo)
    out = proc.stdout + proc.stderr
    for value in _VALUES:
        assert value not in out, "a planted value was printed"
    found = {}
    for ln in proc.stdout.splitlines():
        if ln.startswith("leak_scan:"):
            continue
        path, line, rule = ln.split(":", 2)
        found.setdefault(path, []).append(f"{line}:{rule}")
    return found


def _ids(group):
    return [r[1] for r in _ROWS if r[0] == group]


def _check(batch, group, rid):
    for g, i, path, _content, expected, _enc in _ROWS:
        if (g, i) == (group, rid):
            assert batch.get(path, []) == expected, f"{path}: {batch.get(path, [])} != {expected}"
            return
    raise AssertionError(rid)


@pytest.mark.parametrize("rid", _ids("hex"))
def test_hex(batch, rid):
    """The hex first line of a PKCS1 / PKCS8 / SEC1 / DSA key is read: bare, a 6-line body, a quote or
    comment prefix, JSON, shell, `0x`, after text, either case."""
    _check(batch, "hex", rid)


@pytest.mark.parametrize("rid", _ids("encrypted"))
def test_encrypted(batch, rid):
    """The lone first line of an encrypted PKCS8 (PBES2, PBES1 pkcs-5 and pkcs-12 schemes), base64 or hex."""
    _check(batch, "encrypted", rid)


@pytest.mark.parametrize("rid", _ids("pkcs12"))
def test_pkcs12(batch, rid):
    """The lone first line of a PFX: version 3 then the `data` content type, DER or BER indefinite."""
    _check(batch, "pkcs12", rid)


@pytest.mark.parametrize("rid", _ids("pgp"))
def test_pgp(batch, rid):
    """A header-less first line of an armored (or hex) PGP secret-key / secret-subkey packet."""
    _check(batch, "pgp", rid)


@pytest.mark.parametrize("rid", _ids("encoded"))
def test_encoded(batch, rid):
    """The same plants inside UTF-16 and UTF-32 text are still read (the decodings of #450 compose)."""
    _check(batch, "encoded", rid)


@pytest.mark.parametrize("rid", _ids("floor"))
def test_floor(batch, rid):
    """At the floor (24 base64 / 36 hex characters; the OID must be visible) a head is read; one step below it
    the line stays clean. Documented residue: it passes before the change for the clean half."""
    _check(batch, "floor", rid)


@pytest.mark.parametrize("rid", _ids("public"))
def test_public(batch, rid):
    """Public DER, PGP public/signature packets and digests stay clean. Passes before the change too."""
    _check(batch, "public", rid)


def test_every_row_ran(batch):
    assert sum(len(_ids(g)) for g in ("hex", "encrypted", "pkcs12", "pgp", "encoded", "public", "floor")) == len(_ROWS)


def _gate_module():
    spec = importlib.util.spec_from_file_location("leak_scan_head_reader", ROOT / "tools" / "leak_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_head_reader_never_raises():
    """`_private_head` reads attacker-shaped bytes: every prefix of every accepted shape, and random heads
    opening with each first byte it dispatches on, return a bool and never raise (an IndexError would take the
    whole gate down with a traceback instead of printing findings). The full shapes are read, so the walk is
    not vacuous. Guard: the function is new, so this is not part of the red selector."""
    gate = _gate_module()
    rng = random.Random(451)
    blobs = [_blob(h, n, 2) for table in (_PRIVATE, _ENCRYPTED, _PKCS12, _PGP_PRIVATE, _PUBLIC)
             for h, n in table.values()]
    for blob in blobs:
        for n in range(0, 40):
            assert gate._private_head(blob[:n]) in (True, False)
    for table in (_PRIVATE, _ENCRYPTED, _PKCS12, _PGP_PRIVATE):
        for h, n in table.values():
            assert gate._private_head(_blob(h, n, 3)[:24]) or table is _PKCS12 or len(bytes.fromhex(h)) > 24, h
    for first in (0x30, 0x94, 0x95, 0x96, 0x97, 0x9C, 0x9D, 0x9E, 0x9F, 0xC5, 0xC7, 0xFF, 0x80, 0x00):
        for _ in range(500):
            head = bytes([first]) + bytes(rng.randrange(256) for _ in range(rng.randrange(0, 30)))
            assert gate._private_head(head) in (True, False)
            tok = head.hex() + "0" * 40
            assert gate._hex_private(tok) in (True, False)
            assert gate._der_private(base64.b64encode(head + bytes(30)).decode()) in (True, False)
