"""`key-body` (#433), the GUARDS half: false-positive guards, limit and ambiguity pins, the batch summary
and the timing tests. Each is green on a3c913c AND on the new gate, so none can be red-first; their red
proof is a mutation of the new gate (the controls in `test_key_body.py` and the plan's Step 5), so this
file is not a `## Tests` selector. One exception, added after code review 1: the `pin` row
`p256-pkcs8-line1` is red on a3c913c too; it lives here because the red-first file's bytes are frozen in
the red witness. Fixtures and helpers come from the red-first file."""
import base64
import random
import re

import pytest

from test_key_body import (
    _B31, _B64, _EC, _EC_PRIV, _ED_DER, _ED_RANDOM, _FLAT_RSA, _NOT_PRIVATE, _PRIV, _ROWS,
    _RSA2, _RSA12, _assert_flood_is_linear, _assert_whitespace_span_is_linear, _check, _der_text, _flood_repo,
    _gap_text, _kb, _lines, _mixed, _public_block, _row, _run_batch, _tolerance_body, _ws_span_repo)

_GUARDS = []


def _g(group, rid, content, expected, path=None):
    _row(group, rid, content, expected, path, rows=_GUARDS)


_g("token", "lone-64", "\n".join(_ED_RANDOM), [])
_g("token", "two-64", "\n".join(_RSA2), [])
_g("span", "limit-non-der-slice", "\n".join(["-----BEGIN CERTIFICATE-----"] + _RSA12 + ["-----END CERTIFICATE-----"]), [])
_g("span", "elided", "\n".join(["-----BEGIN CERTIFICATE-----"] + _B31 + ["...", "-----END CERTIFICATE-----"]), [])
for i, label in enumerate(["PRIVATE KEY", "PGP SECRET KEY BLOCK", "ENCRYPTED PRIVATE KEY"]):
    _g("span", f"private-label{i}", "\n".join([f"-----BEGIN {label}-----"] + _lines([64] * 3, seed=40 + i)
                                              + [f"-----END {label}-----"]), _kb(3))
for label in ("PUBLIC KEY", "RSA PUBLIC KEY", "CERTIFICATE"):
    _g("public", label.replace(" ", "-").lower(), _public_block(label), [])
_certs = []
for i in range(20):
    body = _lines([64] * 23 + [27], seed=100 + i)
    body[5] = re.sub(r"\d", "q", body[5])
    assert not _mixed(body[5])
    _certs.append("\n".join(["-----BEGIN CERTIFICATE-----"] + body + ["-----END CERTIFICATE-----"]))
_g("public", "ca-bundle", "\n".join(_certs), [], "certs/ca-bundle.crt")
_mime = base64.encodebytes((("a" + " " * 30) * 60).encode()).decode().splitlines()
assert sum(not _mixed(ln) for ln in _mime[:-1]) >= 3, "the fixture must be digit-less MIME text"
_sha = "".join(random.Random(1).choice(_B64) for _ in range(86))
_g("public", "mime-lockfile-datauri", "\n".join(
    ["Content-Transfer-Encoding: base64", ""] + _mime + [f'    "integrity": "sha512-{_sha}==",', f"integrity sha512-{_sha}==",
     "url(data:image/png;base64," + "".join(random.Random(2).choice(_B64) for _ in range(120)) + ")"]), [], "mail.eml")

_g("ambiguity", "hex-hashes", "\n".join("".join(random.Random(i).choice("0123456789abcdef") for _ in range(64))
                                        for i in range(6)), [])
_g("ambiguity", "base64-digests", "\n".join(_lines([44] * 4, seed=50)), _kb(2))
_cert = _der_text("certificate", _NOT_PRIVATE)
_C, _CE = "-----BEGIN CERTIFICATE-----", "-----END CERTIFICATE-----"
_g("ambiguity", "go-backtick-cert", "\n".join(["package x", "const ca = `" + _C] + _cert + [_CE + "`"]), _kb(4), "ambiguity/ca.go")
_g("ambiguity", "py-triple-quote-cert", "\n".join(['CA = """' + _C] + _cert + [_CE + '"""']), _kb(3), "ambiguity/ca.py")
_g("ambiguity", "py-triple-quote-own-lines", "\n".join(['CA = """', _C] + _cert + [_CE, '"""']), [], "ambiguity/ca2.py")
_g("ambiguity", "ssh2-continued-comment", "\n".join(["---- BEGIN SSH2 PUBLIC KEY ----", 'Comment: "4096-bit RSA, from\\',
                                                     'me at example"'] + _lines([70] * 3 + [24], seed=22)
                                                    + ["---- END SSH2 PUBLIC KEY ----"]), _kb(5))

for tail, flagged in [(24, False), (27, False), (40, True), (41, True)]:
    _g("boundary", f"tail{tail}", "\n".join(_lines([64, 64, tail], seed=60 + tail)), _kb(2) if flagged else [])
for width, flagged in [(36, False), (39, False), (40, True), (41, True)]:
    _g("boundary", f"three-of-{width}", "\n".join(_lines([width] * 3, seed=70 + width)), _kb(2) if flagged else [])
for n_bad, n_lines, tail in [(2, 3, None), (2, 12, None), (1, 2, 36)]:
    _g("tolerance", f"bad{n_bad}-of-{n_lines}-tail{tail}", _tolerance_body(n_bad, n_lines, tail), [])

_g("anchor", "gap9", _gap_text(9), [])
_g("anchor", "non-armor-between", "\n".join([_PRIV, "this is not armor", _ED_RANDOM[0]]), [])
_g("anchor", "public-header", "\n".join(["-----BEGIN PUBLIC KEY-----", _ED_RANDOM[0]]), [])
_g("anchor", "lower-case-header", "\n".join(["-----begin priv" + "ate key-----", _ED_RANDOM[0]]), [])

for name in sorted(_NOT_PRIVATE):
    _g("der", f"public-{name}", _der_text(name, _NOT_PRIVATE)[0], [])
for wrap in (64, 70):
    _g("der", f"rewrap{wrap}", "\n".join(_FLAT_RSA[i:i + wrap] for i in range(0, 400, wrap)), _kb(2))
_g("der", "blank-every-2-no-der", "\n".join(_RSA12[0:2] + [""] + _RSA12[2:4] + [""] + _RSA12[4:6]), [])
for i, line in enumerate(["key: {b}", "KEY='{b}'", '{"k": "{b}"}', "x" + " " * 3 + "{b}", "#######-- {b}"]):
    _g("der-limit", f"not-line-start{i}", line.replace("{b}", _ED_DER[0]), [])

_hex40 = "".join(random.Random(3).choice("abcdef0123456789") for _ in range(40))
for what, line in (("slash", "/" * 80), ("sha1", _hex40), ("digitless", _lines([64], seed=2, mixed=False)[0])):
    assert not _mixed(line)
    _g("mixed", f"header-const-{what}", "\n".join([f'PRIVATE_KEY_HEADER = "{_PRIV}"', "", line]), [])

_flat_ec = "".join(_EC)
for w in (24, 32, 39):   # non-DER body narrower than 40: wholly clean, header and END included
    _g("narrow", f"ec{w}-comment", "\n".join([_EC_PRIV, "Comment: deploy"] + [_flat_ec[i:i + w] for i in range(0, len(_flat_ec), w)]), [])
    _g("narrow", f"pgp{w}-end", "\n".join(["-----BEGIN PGP PRIV" + "ATE KEY BLOCK-----", "Version: x", ""]
                                         + [_flat_ec[i:i + w] for i in range(0, len(_flat_ec), w)]
                                         + ["=Ab1c", "-----END PGP PRIV" + "ATE KEY BLOCK-----"]), [])
for wrap in (16, 20, 23):
    _g("narrow", f"der-led{wrap}", "\n".join(_FLAT_RSA[i:i + wrap] for i in range(0, 400, wrap)), [])

# Review pins (#433 code review 1): seams no other row could see, each seen red against its named mutation
# of the new gate (`no_len81`, `b_no_pad`, `label_case`). `p256-pkcs8-line1` is RED on a3c913c (its rule
# reads no DER line); the other six are green there.
_P256_PKCS8 = {"ec-p256-pkcs8": ("308187020100301306072a8648ce3d020106082a8648ce3d030107046d306b0201010420", 138)}
_g("pin", "p256-pkcs8-line1", _der_text("ec-p256-pkcs8", _P256_PKCS8)[0], _kb(2))   # the 0x81 length branch
_pad = _lines([64, 64, 42], seed=120)
_g("pin", "padded-last-line", "\n".join(_pad[:2] + [_pad[2] + "=="]), _kb(2))     # `={0,2}` in the candidate
for rid, label in (("label-lower", "rsa priv" + "ate key"), ("label-mixed", "Rsa Priv" + "ate Key")):
    _g("pin", rid, "\n".join([f"-----BEGIN {label}-----"] + _RSA12 + [f"-----END {label}-----"]), _kb(3))
# The allow marker on a header-less body: it makes its line non-base64 and so SPLITS the block; a short body
# split below the thresholds goes clean, a longer one is still flagged on what remains. A side effect, pinned.
_MARK = "  # leak-scan" + ": allow key-body measured side effect of the block rule"
_g("pin", "marker-3-lines", "\n".join([_B31[0] + _MARK] + _B31[1:]), [])
_g("pin", "marker-p256-shape", "\n".join(_EC[:2] + [_EC[2] + _MARK]), [])
_g("pin", "marker-6-lines", "\n".join([_RSA12[0] + _MARK] + _RSA12[1:6]), _kb(3))

_ALL = _ROWS + _GUARDS


@pytest.fixture(scope="module")
def guard_batch(tmp_path_factory):
    """ONE gesture over the union of both files' rows: the summary checks every plant at once."""
    return _run_batch(tmp_path_factory, _ALL)


def _ids(group):
    return [pytest.param(path, exp, id=rid) for g, rid, path, _c, exp in _GUARDS if g == group]


def test_the_batch_run_exits_1_reports_only_the_planted_paths_and_never_a_value(guard_batch):
    proc, found = guard_batch
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert set(found) <= {p for _g, _i, p, _c, _e in _ALL}, sorted(set(found))
    assert len({p for _g, _i, p, _c, _e in _ALL}) == len(_ALL), "two rows share a path"
    for _g_, _i, _p, content, _e in _ALL:
        text = content.decode() if isinstance(content, bytes) else content
        for ln in re.split(r"\r\n|\r|\n", text):
            if len(ln) >= 24 and re.fullmatch(r"[\sA-Za-z0-9+/=_-]+", ln):
                assert ln.strip() not in proc.stdout + proc.stderr


@pytest.mark.parametrize("path,expected", _ids("token"))
def test_the_limit_a_lone_line_or_two_random_lines_are_a_token_not_a_key(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("span"))
def test_the_public_span_exemption_guards(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("public"))
def test_real_public_blocks_bundles_mime_and_lockfiles_are_green(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("ambiguity"))
def test_the_documented_ambiguities(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("boundary"))
def test_the_28_and_40_floors_guards(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("tolerance"))
def test_two_digitless_lines_or_a_digitless_line_before_a_tail_are_clean(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("anchor"))
def test_what_breaks_the_header_anchor(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("der"))
def test_public_der_is_not_read_as_private(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("der-limit"))
def test_the_limit_a_key_that_is_not_at_the_start_of_its_line_is_not_seen(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("mixed"))
def test_the_anchor_needs_every_line_mixed(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("narrow"))
def test_the_limit_bodies_narrower_than_the_floors_are_not_seen(guard_batch, path, expected):
    _check(guard_batch, path, expected)


def test_a_begin_flood_is_linear_not_quadratic(tmp_path):
    _assert_flood_is_linear(_flood_repo(tmp_path))


def test_a_whitespace_line_inside_a_public_span_is_linear(tmp_path):
    _assert_whitespace_span_is_linear(_ws_span_repo(tmp_path))


@pytest.mark.parametrize("path,expected", _ids("pin"))
def test_review_pins(guard_batch, path, expected):
    _check(guard_batch, path, expected)
