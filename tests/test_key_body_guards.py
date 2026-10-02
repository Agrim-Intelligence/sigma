"""`key-body` (#433), the GUARDS half: false-positive guards, limit and ambiguity pins, the batch summary
and the timing tests. Each is green on a3c913c AND on the new gate, so none can be red-first; their red
proof is a mutation of the new gate (the controls in `test_key_body.py` and the plan's Step 5), so this
file is not a `## Tests` selector. One exception, added after code review 1: the `pin` row
`p256-pkcs8-line1` is red on a3c913c too; it lives here because the red-first file's bytes are frozen in
the red witness. Rows added after post-PR review 1 (`prefix`, `diffa`, `padding`) are red on the PR head
0c25573 or on a3c913c, as their comment says, and carry their own control (`test_control_prefix_and_diff_armor`).
Rows added after post-PR review 2 (`prefix-run`, `ws-limit`, and four `ambiguity` rows) pin measured behaviour:
the flagged `prefix-run` rows and the three `anchor-digest` rows are red on a3c913c, the rest green there.
Fixtures and helpers come from the red-first file."""
import base64
import random
import re

import pytest

from test_key_body import (
    _B31, _B64, _EC, _EC_PRIV, _ED_DER, _ED_RANDOM, _FLAT_RSA, _NOT_PRIVATE, _PRIV, _ROWS,
    _RSA2, _RSA12, _assert_flood_is_linear, _assert_whitespace_span_is_linear, _check, _der_text, _flood_repo,
    _gap_text, _kb, _lines, _mixed, _mutate, _public_block, _row, _run_batch, _tolerance_body, _ws_span_repo)
from test_leak_scan import _git, _run, _scratch

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

# Post-PR review 1 (#433): `+` and `/` are base64 characters, so a key line led by a diff's `+` or a `//` was
# swallowed whole by the candidate alternative and its DER start was never read. `prefix` rows are RED on the
# PR head 0c25573 and on a3c913c, except that the `// ` rows (a space ends the run) are red on a3c913c only;
# `diffa` rows (a unified diff of shape (a)) are red on both. Each goes clean under its seam below.
_P224 = {"ec-p224-sec1": ("3068020101041c", 106)}    # public SEC1 header bytes of a P-224 key, 64+64+16 chars
_PREFIX_KEYS = {"ed25519": _der_text("ed25519-pkcs8"), "x25519": _der_text("x25519-pkcs8"),
                "ed448": _der_text("ed448-pkcs8"), "x448": _der_text("x448-pkcs8"),
                "p224": _der_text("ec-p224-sec1", _P224)}
for kind, body in _PREFIX_KEYS.items():
    for pname, pre in (("plus", "+"), ("slash", "/"), ("dslash", "//"), ("dslash-sp", "// ")):
        _g("prefix", f"{kind}-{pname}", "\n".join(pre + ln for ln in body), _kb(2))
_DIFF = ["@@ -0,0 +1,3 @@", "+" + _PRIV]
_g("diffa", "der-first-line", "\n".join(_DIFF + ["+Comment: deploy", "+" + _ED_DER[0]]), _kb(5))
_g("diffa", "anchor-armor", "\n".join(_DIFF + ["+Comment: deploy", "+" + _ED_RANDOM[0]]), _kb(5))
_g("diffa", "anchor-blank", "\n".join(_DIFF + ["+", "+" + _ED_RANDOM[0]]), _kb(5))
# A short or empty token once the run is stripped: clean, and no traceback (the batch would exit 1 with it).
_g("prefix-limit", "slash40", "/" * 40, [])
_g("prefix-limit", "dslash-10M", "//" + "M" * 10, [])
_g("prefix-limit", "slash30-short-M", "/" * 30 + "MIIB" + "x" * 7, [])
# Line lengths count the base64 PAYLOAD, never the `=` padding (post-PR review 1, finding 2): a 28-character
# line that ends in `=` is a 27-character tail, and a 40-character line that ends in `=` is below the 40 floor.
_pad3 = _lines([64, 64, 28], seed=130)
_g("padding", "tail27-eq", "\n".join(_pad3[:2] + [_pad3[2][:27] + "="]), [])
_g("padding", "tail28-eq", "\n".join(_pad3[:2] + [_pad3[2] + "="]), _kb(2))
_g("padding", "three-39-eq", "\n".join(ln + "=" for ln in _lines([39] * 3, seed=131)), [])
_g("padding", "three-40-eq", "\n".join(ln + "=" for ln in _lines([40] * 3, seed=131)), _kb(2))

# Post-PR review 2 (#433): the measured reach of a `+` / `/` run before a DER first line. The symbol runs take
# at most 6 (two runs of 1-3), whatever follows the key; a longer run is read only through the whole-line
# candidate, so the line, run included, must be 40+ base64 characters with nothing else on it. The flagged
# rows are red on a3c913c (it reads no DER line); the `limit-` rows are the documented limit, clean on both.
_P224_24 = _der_text("ec-p224-sec1", _P224, width=24)[0]
_g("prefix-run", "plus6-trailing-text", "+" * 6 + _ED_DER[0] + '",', _kb(2))
_g("prefix-run", "mixed6-trailing-text", "+/+/+/" + _ED_DER[0] + " \\", _kb(2))
_g("prefix-run", "limit-plus7-trailing-text", "+" * 7 + _ED_DER[0] + '",', [])
_g("prefix-run", "limit-slash10-trailing-text", "/" * 10 + _ED_DER[0] + " x", [])
_g("prefix-run", "plus7-whole-line", "+" * 7 + _ED_DER[0], _kb(2))
_g("prefix-run", "slash10-whole-line", "/" * 10 + _ED_DER[0], _kb(2))
_g("prefix-run", "limit-plus15-24-char-line", "+" * 15 + _P224_24, [])   # 39 characters
_g("prefix-run", "plus16-24-char-line", "+" * 16 + _P224_24, _kb(2))     # 40 characters
# Ambiguities found by post-PR review 2, pinned as measured. (2) The header anchor reads prose naming a
# private BEGIN header, so ONE mixed 40+ digest below it (blank, `+` or `Key: value` lines between) is a
# finding; a3c913c had no anchor. (3) The public-certificate span is not diff-aware: a `+`-prefixed
# certificate is a finding on both gates.
_DIGEST = _lines([43], seed=140)[0] + "="
_HDR_PROSE = "`-----BEGIN PRIV" + "ATE KEY-----`"
_g("ambiguity", "anchor-digest-changelog", f"- The gate reads the {_HDR_PROSE} header.\n\n{_DIGEST}", _kb(4))
_g("ambiguity", "anchor-digest-table", f"| {_HDR_PROSE} | a PKCS8 header |\n\n{_DIGEST}", _kb(4))
_g("ambiguity", "anchor-digest-diff", f"@@ -1,0 +1,3 @@\n+- Reads the {_HDR_PROSE} header.\n+\n+{_DIGEST}", _kb(5))
_g("ambiguity", "cert-added-by-a-diff", "\n".join(["--- /dev/null", "+++ b/ca.pem", "@@ -0,0 +1,12 @@"]
                                                 + ["+" + ln for ln in _public_block("CERTIFICATE").splitlines()]), _kb(6))
# (4) Indentation other than space / tab, and line separators other than LF / CR, hide a DER first line.
for name, ws in (("em-space", " "), ("ideographic-space", "　"), ("vt", "\v"), ("ff", "\f")):
    _g("ws-limit", f"indent-{name}", ws + _ED_DER[0], [])
for name, sep in (("u2028", " "), ("u2029", " "), ("nel", "\x85")):
    _g("ws-limit", f"separator-{name}", sep.join(["x", _ED_DER[0], "y"]), [])

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


@pytest.mark.parametrize("path,expected", _ids("prefix") + _ids("diffa"))
def test_a_key_line_behind_a_diff_plus_or_a_slash_run(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("prefix-limit") + _ids("padding"))
def test_short_stripped_tokens_and_padding_lengths(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("prefix-run"))
def test_the_measured_reach_of_a_plus_or_slash_run(guard_batch, path, expected):
    _check(guard_batch, path, expected)


@pytest.mark.parametrize("path,expected", _ids("ws-limit"))
def test_the_limit_other_whitespace_and_separators_hide_a_der_line(guard_batch, path, expected):
    _check(guard_batch, path, expected)


#: The post-PR-review-1 seams, each mutated in a scratch COPY of the gate: the `lstrip` becoming a no-op, and
#: the diff `+` dropped from the armor lines `_anchored` walks over.
_FIX1_SEAMS = {"lstrip": ('.lstrip("+/")', '.lstrip("")'),
               "diff-armor": ('_BLANK_OR_ARMOR = re.compile(r"\\+?[ \\t]*', '_BLANK_OR_ARMOR = re.compile(r"[ \\t]*')}


def test_control_prefix_and_diff_armor(tmp_path):
    """Run the control: every `prefix` row the PR head missed (`+`, `/`, `//`) goes clean when the `lstrip`
    is a no-op; the two `diffa` anchor rows go clean when the armor walk drops the diff `+`; the `diffa` DER
    row, caught by both, goes clean only with both mutated."""
    rows = [r for r in _GUARDS if r[0] in ("prefix", "diffa") and not r[1].endswith("dslash-sp")]
    want = {"lstrip": {p for g, _i, p, _c, _e in rows if g == "prefix"},
            "diff-armor": {p for _g_, i, p, _c, _e in rows if i.startswith("anchor-")},
            "both": {p for _g_, _i, p, _c, _e in rows}}
    for name, seams in (("lstrip", ["lstrip"]), ("diff-armor", ["diff-armor"]), ("both", ["lstrip", "diff-armor"])):
        repo = _scratch(tmp_path / name)
        for _g_, _i, path, content, _e in rows:
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text("line one\n" + content + "\n", encoding="utf-8")
        _git(repo, "add", "-A")
        before = {ln.split(":")[0] for ln in _run(repo).stdout.splitlines() if ln.endswith(": key-body")}
        assert before == {p for _g_, _i, p, _c, _e in rows}, sorted({p for _g_, _i, p, _c, _e in rows} - before)
        for s in seams:
            _mutate(repo, *_FIX1_SEAMS[s])
        after = {ln.split(":")[0] for ln in _run(repo).stdout.splitlines() if ln.endswith(": key-body")}
        assert not (after & want[name]), (name, sorted(after & want[name]))
