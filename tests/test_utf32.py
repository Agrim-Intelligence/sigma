"""UTF-32 text in `tools/leak_scan.py` (#450). A UTF-32 little-endian file with its byte-order mark starts
`FF FE 00 00`, whose first two bytes are the UTF-16 little-endian mark, so the gate used to decode it as
UTF-16 and scan NUL-interleaved garbage: every plant in it passed CLEAN.

Driven through the documented gesture `python3 tools/leak_scan.py` in a scratch git repository (the
helpers of `test_leak_scan`), planting a PEM private key and, separately, a home path. Every plant is
built from fragments at run time (the gate scans tests/ too), and every test asserts that no planted value
reaches stdout or stderr.

The `## Tests` selector of the plan is the functions that go red against the pre-fix gate on an assertion:
`test_bom`, `test_tail2_le`, `test_badcp_le`, `test_badcp_be`. The rest are guards that pass before the fix as well (the
named-finding behaviour of the BOM-less variants, the odd-tail variants that already failed closed, the
file that is valid under two decodings, the clean control) and they pin the behaviour so a later change
cannot trade it away."""
import codecs

import pytest

from test_leak_scan import _PLANTS, _SECRET, _run, _scratch, _write

_PRIV_RULE = "private-key"
_KEY = _PLANTS["private-key"][1]
_HOME = _PLANTS["home-path"][1]
_PLANT = {"key": (_KEY, "private-key"), "home": (_HOME, "home-path")}
_VALUES = ["jdoesmith", "MIIEow", _SECRET]


def _text(plant):
    return "line one\n" + plant + "\n"


def _encode(kind, text):
    """The four UTF-32 spellings a text file can arrive in."""
    return {
        "le": text.encode("utf-32-le"),
        "be": text.encode("utf-32-be"),
        "lebom": codecs.BOM_UTF32_LE + text.encode("utf-32-le"),
        "bebom": codecs.BOM_UTF32_BE + text.encode("utf-32-be"),
    }[kind]


def _run_with(tmp_path, data):
    repo = _scratch(tmp_path)
    _write(repo, "notes.md", data)
    proc = _run(repo)
    for value in _VALUES:
        assert value not in proc.stdout + proc.stderr, "a planted value was printed"
    return proc


def _named(proc, rule=None, opaque=False):
    """The gate reported `notes.md` by name: a decoded `rule` and/or (`opaque`) `opaque-binary`. With
    neither, any named finding will do."""
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("notes.md:")]
    assert proc.returncode == 1 and lines, proc.stdout + proc.stderr
    if rule:
        assert any(ln.endswith(": " + rule) for ln in lines), proc.stdout
    if opaque:
        assert any(ln.endswith(": opaque-binary") for ln in lines), proc.stdout
    return lines


@pytest.mark.parametrize("which", ["le-key", "be-key", "le-home", "be-home"])
def test_bom(tmp_path, which):
    """The four BOM variants (the silent one is `le`) are DECODED: the rule is named, not `opaque`."""
    endian, name = which.split("-")
    plant, rule = _PLANT[name]
    proc = _run_with(tmp_path, _encode(endian + "bom", _text(plant)))
    lines = _named(proc, rule)
    assert not any(ln.endswith("opaque-binary") for ln in lines), proc.stdout


def test_tail2_le(tmp_path):
    """Two stray bytes after a complete UTF-32 LE file keep the file an even length, which is exactly
    what lets the UTF-16 reading succeed: the plant must still be found."""
    data = _encode("lebom", _text(_KEY)) + b"\x00\x00"
    _named(_run_with(tmp_path, data), _PRIV_RULE, opaque=True)


def test_badcp_le(tmp_path):
    """A group that is no code point (above U+10FFFF) must not stop the rest of the file being read."""
    data = codecs.BOM_UTF32_LE + b"\xff\xff\xff\xff" + _text(_KEY).encode("utf-32-le")
    _named(_run_with(tmp_path, data), _PRIV_RULE, opaque=True)


@pytest.mark.parametrize("endian", ["le", "be"])
def test_tail_odd(tmp_path, endian):
    """An odd stray tail: the plant is still read (BE) or the file is named, never CLEAN."""
    data = _encode(endian + "bom", _text(_KEY)) + b"\x00\x00\x00"
    _named(_run_with(tmp_path, data))


def test_badcp_be(tmp_path):
    data = codecs.BOM_UTF32_BE + b"\xff\xff\xff\xff" + _text(_KEY).encode("utf-32-be")
    _named(_run_with(tmp_path, data), _PRIV_RULE, opaque=True)


@pytest.mark.parametrize("endian", ["le", "be"])
def test_bom_only_and_empty_are_clean(tmp_path, endian):
    """A bare byte-order mark is a valid empty text; so is a clean text. Not a finding."""
    repo = _scratch(tmp_path)
    _write(repo, "empty.txt", _encode(endian + "bom", ""))
    _write(repo, "ok.txt", _encode(endian + "bom", "hello\n"))
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_valid_under_two_decodings_is_scanned_under_both(tmp_path):
    """`FF FE 00 00` then UTF-16 LE text is a legal UTF-32 file AND a UTF-16 file starting with U+0000.
    A UTF-32-only decoder would read the secret as noise; both readings must be scanned."""
    body = "token " + _SECRET + "\n"
    body += " " * (len(body) % 2)               # whole 4-byte groups: a complete UTF-32 file
    data = codecs.BOM_UTF32_LE + body.encode("utf-16-le")
    assert len(data) % 4 == 0
    _named(_run_with(tmp_path, data), "gh-token")


def test_dual_valid_be(tmp_path):
    """The same, big-endian: `00 00 FE FF` then UTF-16 BE text."""
    body = "token " + _SECRET + "\n"
    body += " " * (len(body) % 2)
    data = codecs.BOM_UTF32_BE + body.encode("utf-16-be")
    assert len(data) % 4 == 0
    _named(_run_with(tmp_path, data), "gh-token")


def test_utf32_bom_then_utf16_bom(tmp_path):
    """`FF FE 00 00 FF FE` ... is a UTF-32 mark followed by a UTF-16 text: the plant is still read."""
    data = codecs.BOM_UTF32_LE + codecs.BOM_UTF16_LE + _text(_KEY).encode("utf-16-le")
    _named(_run_with(tmp_path, data), _PRIV_RULE)


@pytest.mark.parametrize("endian", ["le", "be"])
def test_plant_past_the_first_8192_bytes(tmp_path, endian):
    """The mark decides the decoding, so a plant far past the NUL-heuristic window is still read."""
    text = "x" * 9000 + "\n" + _text(_KEY)
    _named(_run_with(tmp_path, _encode(endian + "bom", text)), _PRIV_RULE)


@pytest.mark.parametrize("which", ["le-key", "be-key", "le-home", "be-home"])
def test_bomless_is_named_never_clean(tmp_path, which):
    """No mark, no way to know the width: the file is a named finding (a decoded rule or
    `opaque-binary`), never CLEAN."""
    endian, name = which.split("-")
    _named(_run_with(tmp_path, _encode(endian, _text(_PLANT[name][0]))))


@pytest.mark.parametrize("endian", ["le", "be"])
def test_bomless_mixed_parity_is_named(tmp_path, endian):
    """A BOM-less UTF-32 file whose NULs sit on both byte parities (an ASCII plant next to a code point
    above the BMP) is not UTF-16 by parity, and must not read as CLEAN."""
    text = "\U0001f600 " + _text(_KEY)
    _named(_run_with(tmp_path, _encode(endian, text)))
