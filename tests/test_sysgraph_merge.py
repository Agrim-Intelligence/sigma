"""Tests for skills/sigma-kg/scripts/sysgraph_merge.py (#843, stage 1 of the system-graph merge).

Every test builds its trees in tmp_path; nothing here writes into the checkout. The module is
loaded by path (scripts are not a package) and driven through ``main(argv)`` so that os.replace
and the directory listing can be injected at the exact seam.
"""
import ast
import importlib.util
import itertools
import json
import os
import pathlib
import py_compile
import subprocess
import sys

import pytest

MOD = (pathlib.Path(__file__).resolve().parent.parent
       / "skills" / "sigma-kg" / "scripts" / "sysgraph_merge.py")

T1 = "2026-01-01T00:00:00Z"
T2 = "2026-02-01T00:00:00Z"
T3 = "2026-03-01T00:00:00Z"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
EMPTY_UNRESOLVED = '{\n  "unresolved": []\n}\n'


def _mod():
    spec = importlib.util.spec_from_file_location("sysgraph_merge", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def frag(repo="r", ct=T1, commit=SHA_A, nodes=None, edges=None, version="1.0", **extra):
    d = {"sysgraph_version": version, "repo": repo, "commit_time": ct, "commit": commit,
         "nodes": [{"id": repo + ":n"}] if nodes is None else nodes,
         "edges": [] if edges is None else edges}
    d.update(extra)
    return d


def put(d, name, obj):
    p = pathlib.Path(d) / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def raw(d, name, data):
    p = pathlib.Path(d) / name
    p.write_bytes(data)
    return p


@pytest.fixture
def tree(tmp_path):
    f = tmp_path / "frags"
    f.mkdir()
    return f, tmp_path / "out"


def run(argv, capsys):
    mod = _mod()
    rc = mod.main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def merge(f, o, capsys, *extra):
    return run(["merge", str(f), "--out", str(o)] + list(extra), capsys)


def load_out(o):
    return json.loads((pathlib.Path(o) / "merged.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- step 1: CLI shell

@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_exits_0_and_creates_nothing(tmp_path, flag):
    before = sorted(os.listdir(tmp_path))
    r = subprocess.run([sys.executable, str(MOD), flag], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    assert "usage:" in r.stdout
    assert "--out" in r.stdout and "--max-bytes" in r.stdout
    assert sorted(os.listdir(tmp_path)) == before


def test_unknown_flag_exits_2_and_writes_nothing(tree, capsys):
    f, o = tree
    rc, out, err = merge(f, o, capsys, "--stale-after-days", "3")
    assert rc == 2 and not o.exists()
    assert "--stale-after-days" in err


def test_missing_fragments_dir_exits_2(tmp_path, capsys):
    rc, out, err = run(["merge", str(tmp_path / "nope"), "--out", str(tmp_path / "o")], capsys)
    assert rc == 2 and not (tmp_path / "o").exists()


def test_fragments_path_that_is_a_file_exits_2(tmp_path, capsys):
    p = tmp_path / "a.json"
    p.write_text("{}")
    rc, out, err = run(["merge", str(p), "--out", str(tmp_path / "o")], capsys)
    assert rc == 2


def test_missing_out_or_verb_exits_2(tree, capsys):
    f, o = tree
    assert run(["merge", str(f)], capsys)[0] == 2
    assert run([], capsys)[0] == 2
    assert run(["frobnicate", str(f), "--out", str(o)], capsys)[0] == 2


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "1.5"])
def test_bad_max_bytes_exits_2(tree, capsys, bad):
    f, o = tree
    put(f, "a.json", frag())
    assert merge(f, o, capsys, "--max-bytes", bad)[0] == 2
    assert not o.exists()


# ---------------------------------------------------------------- step 2: selection (AC-1)

def test_greatest_commit_time_wins_per_repo(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("r", T2, SHA_A, nodes=[{"id": "n", "v": "mid"}]))
    put(f, "b.json", frag("r", T3, SHA_A, nodes=[{"id": "n", "v": "new"}]))
    put(f, "c.json", frag("r", T1, SHA_C, nodes=[{"id": "n", "v": "old"}]))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    m = load_out(o)
    assert m["merged_from"] == [{"repo": "r", "commit": SHA_A, "commit_time": T3, "file": "b.json"}]
    assert m["nodes"] == [{"id": "n", "v": "new"}]
    assert "2 superseded" in out


def test_time_tie_goes_to_greater_sha(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("r", T2, SHA_C))
    put(f, "b.json", frag("r", T2, SHA_B))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["merged_from"][0]["file"] == "a.json"
    # and the other way round, so the winner is not simply the first file
    put(f, "a.json", frag("r", T2, SHA_A))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["merged_from"][0]["file"] == "b.json"


def test_exact_tie_goes_to_smaller_file_name_and_is_reported(tree, capsys):
    f, o = tree
    put(f, "z.json", frag("r", T2, SHA_B, nodes=[{"id": "n", "v": "z"}]))
    put(f, "m.json", frag("r", T2, SHA_B, nodes=[{"id": "n", "v": "m"}]))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert load_out(o)["nodes"] == [{"id": "n", "v": "m"}]
    assert "tie: r: m.json, z.json" in out and "chose m.json" in out


def _permutation_set(d):
    # creation order deliberately differs from name order; the exact tie (c,d) differs in payload
    put(d, "d.json", frag("r", T2, SHA_B, nodes=[{"id": "n", "v": "d"}]))
    put(d, "b.json", frag("r", T2, SHA_A, nodes=[{"id": "n", "v": "b"}]))
    put(d, "c.json", frag("r", T2, SHA_B, nodes=[{"id": "n", "v": "c"}]))
    put(d, "a.json", frag("r", T1, SHA_C, nodes=[{"id": "n", "v": "a"}]))
    raw(d, "junk1.json", b"{")
    raw(d, "junk2.json", b"[")
    return ["d.json", "b.json", "c.json", "a.json", "junk1.json", "junk2.json"]


def test_every_listing_order_gives_identical_bytes_and_choice(tmp_path, capsys, monkeypatch):
    f = tmp_path / "frags"
    f.mkdir()
    names = _permutation_set(f)
    mod = _mod()
    results, reports = set(), set()
    for i, perm in enumerate(itertools.permutations(names)):
        monkeypatch.setattr(mod, "_list_dir", lambda p, perm=perm: list(perm))
        o = tmp_path / ("out%d" % i)
        assert mod.main(["merge", str(f), "--out", str(o)]) == 0
        reports.add(capsys.readouterr().out.rsplit("wrote:", 1)[0])
        results.add((o / "merged.json").read_bytes())
        merged = json.loads((o / "merged.json").read_text())
        assert [e["file"] for e in merged["merged_from"]] == ["c.json"]
    assert len(results) == 1 and len(reports) == 1


def test_real_directory_order_does_not_matter(tmp_path, capsys):
    f1 = tmp_path / "f1"
    f1.mkdir()
    _permutation_set(f1)
    f2 = tmp_path / "f2"
    f2.mkdir()
    for name in sorted(os.listdir(f1), reverse=True):
        (f2 / name).write_bytes((f1 / name).read_bytes())
    assert run(["merge", str(f1), "--out", str(tmp_path / "o1")], capsys)[0] == 0
    assert run(["merge", str(f2), "--out", str(tmp_path / "o2")], capsys)[0] == 0
    assert (tmp_path / "o1" / "merged.json").read_bytes() == (tmp_path / "o2" / "merged.json").read_bytes()


def test_non_json_names_and_subdirectories_are_not_fragments(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("r"))
    (f / "notes.txt").write_text("not a fragment")
    (f / "sub").mkdir()
    put(f / "sub", "x.json", frag("deep"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert [e["repo"] for e in load_out(o)["merged_from"]] == ["r"]
    assert "1 fragment file(s) read" in out


# ---------------------------------------------------------------- step 3: version + corrupt (AC-2)

def test_unknown_major_is_refused_and_named(tree, capsys):
    f, o = tree
    put(f, "good.json", frag("g"))
    put(f, "v2.json", frag("r", version="2.0"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert 'refused version: v2.json: sysgraph_version "2.0" unsupported major 2 (supported: 1)' in out
    assert "1 refused version" in out
    assert [e["repo"] for e in load_out(o)["merged_from"]] == ["g"]


@pytest.mark.parametrize("ver", ["1", "01.0", "1.00", "1.0.0", "", "1.0\n", "1. 0", " 1.0", "v1.0",
                                 "1234.0", "-1.0", "١.٠", None, 1.0, 1, ["1", "0"], {"a": 1}, True])
def test_malformed_versions_are_refused(tree, capsys, ver):
    f, o = tree
    put(f, "good.json", frag("g"))
    put(f, "bad.json", frag("r", version=ver))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert "refused version: bad.json:" in out and "1 refused version" in out
    assert "0 skipped corrupt" in out
    assert [e["repo"] for e in load_out(o)["merged_from"]] == ["g"]


def test_missing_version_is_refused(tree, capsys):
    f, o = tree
    put(f, "good.json", frag("g"))
    d = frag("r")
    del d["sysgraph_version"]
    put(f, "bad.json", d)
    rc, out, err = merge(f, o, capsys)
    assert "refused version: bad.json:" in out


@pytest.mark.parametrize("ver", ["1.0", "1.7", "1.999", "1.0"])
def test_supported_minor_versions_are_accepted(tree, capsys, ver):
    f, o = tree
    put(f, "a.json", frag("r", version=ver))
    assert merge(f, o, capsys)[0] == 0


def test_major_zero_is_unsupported(tree, capsys):
    f, o = tree
    put(f, "good.json", frag("g"))
    put(f, "z.json", frag("r", version="0.9"))
    rc, out, err = merge(f, o, capsys)
    assert "unsupported major 0" in out


_NAN = b'{"sysgraph_version":"1.0","repo":"r","commit_time":"%s","commit":"%s","nodes":[],"edges":[],"x":%s}'


def _corrupt_cases():
    ok = frag("r")

    def mut(**kw):
        d = dict(ok)
        d.update(kw)
        return json.dumps(d).encode()

    def drop(k):
        d = dict(ok)
        del d[k]
        return json.dumps(d).encode()

    def nanx(tok):
        return _NAN % (T1.encode(), SHA_A.encode(), tok)

    return [
        ("nonutf8", b"\xff\xfe\x00garbage"),
        ("notjson", b"{not json"),
        ("empty", b""),
        ("list", b"[1, 2]"),
        ("scalar", b"42"),
        ("norepo", drop("repo")),
        ("repoint", mut(repo=5)),
        ("repoempty", mut(repo="")),
        ("nocommittime", drop("commit_time")),
        ("badcommittime", mut(commit_time="yesterday")),
        ("impossibledate", mut(commit_time="2026-13-45T00:00:00Z")),
        ("committimeint", mut(commit_time=20260101)),
        ("nocommit", drop("commit")),
        ("shortcommit", mut(commit="abc")),
        ("uppercommit", mut(commit="A" * 40)),
        ("nodesstr", mut(nodes="x")),
        ("nodesmissing", drop("nodes")),
        ("edgesdict", mut(edges={})),
        ("edgesmissing", drop("edges")),
        ("nodeentrystr", mut(nodes=["x"])),
        ("nodenoid", mut(nodes=[{"v": 1}])),
        ("nodeidint", mut(nodes=[{"id": 1}])),
        ("nodeidempty", mut(nodes=[{"id": ""}])),
        ("edgeentrystr", mut(edges=["x"])),
        ("edgenotype", mut(edges=[{"from": "a", "to": "b"}])),
        ("edgefromint", mut(edges=[{"type": "t", "from": 1, "to": "b"}])),
        ("edgetonull", mut(edges=[{"type": "t", "from": "a", "to": None}])),
        ("nan", nanx(b"NaN")),
        ("infinity", nanx(b"Infinity")),
        ("neginfinity", nanx(b"-Infinity")),
        ("overflowfloat", nanx(b"1e999")),
        ("deepnest", b"[" * 200000 + b"]" * 200000),
        ("surrogate", json.dumps(dict(ok, nodes=[{"id": "\ud800"}])).encode()),
    ]


@pytest.mark.parametrize("name,data", _corrupt_cases(), ids=[c[0] for c in _corrupt_cases()])
def test_each_corrupt_kind_is_skipped_and_named(tree, capsys, name, data):
    f, o = tree
    put(f, "good.json", frag("g"))
    raw(f, "bad.json", data)
    rc, out, err = merge(f, o, capsys, "--max-bytes", "1000000")
    assert rc == 0, (out, err)
    assert "skipped corrupt: bad.json:" in out
    assert "1 skipped corrupt" in out and "0 refused version" in out
    assert [e["repo"] for e in load_out(o)["merged_from"]] == ["g"]


def test_oversized_file_is_corrupt_and_never_opened(tree, capsys, monkeypatch):
    f, o = tree
    put(f, "good.json", frag("g"))
    big = put(f, "big.json", frag("r", nodes=[{"id": "x" * 500}]))
    mod = _mod()
    opened = []
    real_open = os.open

    def spy(path, *a, **k):
        opened.append(os.path.basename(str(path)))
        return real_open(path, *a, **k)

    monkeypatch.setattr(mod.os, "open", spy)
    limit = os.path.getsize(f / "good.json") + 5
    assert big.stat().st_size > limit
    rc = mod.main(["merge", str(f), "--out", str(o), "--max-bytes", str(limit)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped corrupt: big.json:" in out and "size limit" in out
    assert "big.json" not in opened and "good.json" in opened


def test_file_exactly_at_the_limit_is_read(tree, capsys):
    f, o = tree
    p = put(f, "a.json", frag("r"))
    assert merge(f, o, capsys, "--max-bytes", str(p.stat().st_size))[0] == 0
    assert merge(f, o, capsys, "--max-bytes", str(p.stat().st_size - 1))[0] == 1


def test_default_limit_is_derived_from_the_machine():
    mod = _mod()
    assert mod.default_max_bytes() == os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 32


def test_no_sysconf_and_no_flag_refuses_but_flag_still_works(tree, capsys, monkeypatch):
    f, o = tree
    put(f, "a.json", frag("r"))
    mod = _mod()

    def boom(name):
        raise ValueError(name)

    monkeypatch.setattr(mod.os, "sysconf", boom)
    assert mod.main(["merge", str(f), "--out", str(o)]) == 2
    assert "--max-bytes" in capsys.readouterr().err and not o.exists()
    assert mod.main(["merge", str(f), "--out", str(o), "--max-bytes", "100000"]) == 0


def test_fifo_fragment_is_skipped_without_blocking(tmp_path):
    f = tmp_path / "frags"
    f.mkdir()
    put(f, "good.json", frag("g"))
    os.mkfifo(str(f / "pipe.json"))
    r = subprocess.run([sys.executable, str(MOD), "merge", str(f), "--out", str(tmp_path / "o")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "skipped corrupt: pipe.json:" in r.stdout and "not a regular file" in r.stdout


def test_symlink_fragment_is_skipped(tree, capsys, tmp_path):
    f, o = tree
    put(f, "good.json", frag("g"))
    target = put(tmp_path, "elsewhere.txt", frag("evil"))
    os.symlink(str(target), str(f / "link.json"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert "skipped corrupt: link.json:" in out
    assert [e["repo"] for e in load_out(o)["merged_from"]] == ["g"]


def test_directory_named_like_a_fragment_is_skipped(tree, capsys):
    f, o = tree
    put(f, "good.json", frag("g"))
    (f / "dir.json").mkdir()
    rc, out, err = merge(f, o, capsys)
    assert rc == 0 and "skipped corrupt: dir.json:" in out


def test_unattributable_corrupt_file_cannot_suppress_an_older_valid_one(tree, capsys):
    f, o = tree
    put(f, "old.json", frag("r", T1, SHA_A))
    raw(f, "newer.json", b"{truncated")
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    assert [e["file"] for e in load_out(o)["merged_from"]] == ["old.json"]
    assert "1 skipped corrupt" in out


@pytest.mark.parametrize("bad", [
    lambda: frag("r", T3, SHA_A, nodes="oops"),
    lambda: frag("r", T3, SHA_A, version="2.0"),
    lambda: frag("r", "not-a-time", SHA_A),
    lambda: frag("r", T3, "zz"),
    lambda: frag("r", T3, SHA_A, edges=[{"type": 1}]),
], ids=["nodes", "version", "committime", "commit", "edgeentry"])
def test_attributable_bad_newest_does_not_fall_back_to_older(tree, capsys, bad):
    f, o = tree
    put(f, "old.json", frag("r", T1, SHA_A))
    put(f, "new.json", bad())
    put(f, "other.json", frag("s"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    m = load_out(o)
    assert [e["repo"] for e in m["merged_from"]] == ["s"]
    assert "excluded repository: r:" in out and "new.json" in out
    assert all(n["id"] != "r:n" for n in m["nodes"])


def test_nothing_contributed_exits_1_and_writes_nothing(tree, capsys):
    f, o = tree
    raw(f, "a.json", b"garbage")
    put(f, "b.json", frag("r", version="9.9"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 1 and not o.exists()
    assert "0 repositories contributed" in out


def test_empty_fragments_dir_exits_1(tree, capsys):
    f, o = tree
    rc, out, err = merge(f, o, capsys)
    assert rc == 1 and not o.exists()


def test_one_attributable_bad_only_repo_exits_1(tree, capsys):
    f, o = tree
    put(f, "old.json", frag("r", T1))
    put(f, "new.json", frag("r", T2, nodes="x"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 1 and not o.exists()


def test_mixed_id_types_never_raise(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a", nodes=[{"id": 7}, {"id": "x"}]))
    put(f, "b.json", frag("b", nodes=[{"id": "x"}, {"id": None}], edges=[{"type": 1, "from": "a", "to": "b"}]))
    put(f, "c.json", frag("c", nodes=[{"id": "ok"}]))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0 and "2 skipped corrupt" in out
    assert [n["id"] for n in load_out(o)["nodes"]] == ["ok"]


def test_report_escapes_control_characters(tree, capsys):
    f, o = tree
    put(f, "good.json", frag("g"))
    raw(f, "ev\nil\x1b[31m.json", b"{nope")
    put(f, "v.json", frag("bad\nrepo\x07", version="9\n.0"))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    for line in out.splitlines():
        assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in line)
    assert "\x1b" not in out and "ev?il?[31m.json" in out


# ---------------------------------------------------------------- step 4: determinism + union (AC-3)

def test_two_runs_are_byte_identical_including_the_report(tmp_path, capsys):
    f = tmp_path / "frags"
    f.mkdir()
    _permutation_set(f)
    raw(f, "junk.json", b"{")
    r1 = run(["merge", str(f), "--out", str(tmp_path / "o")], capsys)
    first = {n: (tmp_path / "o" / n).read_bytes() for n in ("merged.json", "unresolved.json")}
    r2 = run(["merge", str(f), "--out", str(tmp_path / "o")], capsys)
    second = {n: (tmp_path / "o" / n).read_bytes() for n in ("merged.json", "unresolved.json")}
    assert first == second and r1 == r2


def test_output_shape_and_format(tree, capsys):
    f, o = tree
    put(f, "b.json", frag("b", nodes=[{"id": "b2"}, {"id": "b1"}], edges=[]))
    put(f, "a.json", frag("a", ct=T2, commit=SHA_B, nodes=[{"id": "a1", "kind": "svc"}]))
    rc, out, err = merge(f, o, capsys)
    assert rc == 0
    data = (o / "merged.json").read_bytes().decode("utf-8")
    m = json.loads(data)
    assert data == json.dumps(m, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert list(m) and set(m) == {"sysgraph_version", "merged_from", "nodes", "edges"}
    assert m["sysgraph_version"] == "1.0"
    assert m["merged_from"] == [
        {"repo": "a", "commit": SHA_B, "commit_time": T2, "file": "a.json"},
        {"repo": "b", "commit": SHA_A, "commit_time": T1, "file": "b.json"}]
    assert [n["id"] for n in m["nodes"]] == ["a1", "b1", "b2"]
    assert (o / "unresolved.json").read_text() == EMPTY_UNRESOLVED
    assert sorted(os.listdir(o)) == ["merged.json", "unresolved.json"]
    assert out.splitlines()[0] == ("merge: 2 fragment file(s) read; 2 repositories contributed; "
                                   "0 superseded; 0 skipped corrupt; 0 refused version")
    assert out.splitlines()[-1] == "wrote: %s/merged.json, %s/unresolved.json" % (o, o)


def test_single_repository_wording(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    out = merge(f, o, capsys)[1]
    assert "1 repository contributed" in out


def test_node_conflict_across_repos_keeps_greater_commit_time(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a", T1, SHA_C, nodes=[{"id": "shared", "from": "a"}]))
    put(f, "b.json", frag("b", T2, SHA_A, nodes=[{"id": "shared", "from": "b"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "b"}]


def test_node_conflict_time_tie_goes_to_greater_sha_then_smaller_file(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a", T2, SHA_A, nodes=[{"id": "shared", "from": "a"}]))
    put(f, "b.json", frag("b", T2, SHA_B, nodes=[{"id": "shared", "from": "b"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "b"}]
    put(f, "b.json", frag("b", T2, SHA_A, nodes=[{"id": "shared", "from": "b"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "a"}]


def test_node_conflict_winner_independent_of_repo_order(tree, capsys):
    """The higher (commit_time, commit) repo sorts FIRST; each _beats clause on its own."""
    f, o = tree
    # time clause: a is newer than b, and a also has the smaller sha
    put(f, "a.json", frag("a", T2, SHA_A, nodes=[{"id": "shared", "from": "a"}]))
    put(f, "b.json", frag("b", T1, SHA_C, nodes=[{"id": "shared", "from": "b"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "a"}]
    # sha clause: equal time, a has the greater sha
    put(f, "b.json", frag("b", T2, SHA_A, nodes=[{"id": "shared", "from": "b"}]))
    put(f, "a.json", frag("a", T2, SHA_B, nodes=[{"id": "shared", "from": "a"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "a"}]
    # equal key: the smaller file name wins, here the later repo
    put(f, "a.json", frag("b", T2, SHA_A, nodes=[{"id": "shared", "from": "b"}]))
    put(f, "b.json", frag("a", T2, SHA_A, nodes=[{"id": "shared", "from": "a"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "b"}]
    put(f, "a.json", frag("a", T2, SHA_A, nodes=[{"id": "shared", "from": "a"}]))
    put(f, "b.json", frag("b", T2, SHA_A, nodes=[{"id": "shared", "from": "b"}]))
    assert merge(f, o, capsys)[0] == 0
    assert load_out(o)["nodes"] == [{"id": "shared", "from": "a"}]


@pytest.mark.parametrize("v", ["1.0", "1.1", "1.0\n", "01.0", "1.00", "1.", "1", "2.0", "999.999",
                               "1.0000", "1.0 ", "", None, 1.0, ["1.0"], "\u0661.\u0660"])
def test_version_check_agrees_with_sysgraph_check_version(v):
    spec = importlib.util.spec_from_file_location("sysgraph_xcheck", MOD.parent / "sysgraph.py")
    sg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sg)
    try:
        sg.check_version({"sysgraph_version": v})
        theirs = True
    except sg.VersionError:
        theirs = False
    assert (_mod()._version_problem(v) is None) == theirs


def test_edges_are_kept_and_sorted_by_type_from_to(tree, capsys):
    f, o = tree
    e = lambda t, a, b, **k: dict(type=t, to=b, **dict(k, **{"from": a}))
    put(f, "a.json", frag("a", edges=[e("z", "a", "b"), e("a", "b", "a"), e("a", "a", "z")]))
    put(f, "b.json", frag("b", edges=[e("a", "a", "b"), e("a", "a", "b", w=1)]))
    assert merge(f, o, capsys)[0] == 0
    got = [(x["type"], x["from"], x["to"]) for x in load_out(o)["edges"]]
    assert got == sorted(got) and len(got) == 5


def test_unknown_top_level_keys_are_ignored_and_not_copied(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a", mystery={"secret": 1}, version_notes="x"))
    assert merge(f, o, capsys)[0] == 0
    text = (o / "merged.json").read_text()
    assert "mystery" not in text and "secret" not in text and "version_notes" not in text


def test_non_ascii_is_written_unescaped(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a", nodes=[{"id": "café"}]))
    assert merge(f, o, capsys)[0] == 0
    assert "café" in (o / "merged.json").read_bytes().decode("utf-8")


def test_merged_json_is_strict_json(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    assert merge(f, o, capsys)[0] == 0

    def no_const(c):
        raise AssertionError(c)

    json.loads((o / "merged.json").read_text(), parse_constant=no_const)


def test_files_are_mode_0644(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    assert merge(f, o, capsys)[0] == 0
    for n in ("merged.json", "unresolved.json"):
        assert (o / n).stat().st_mode & 0o777 == 0o644


# ---------------------------------------------------------------- step 5: atomic failure (AC-3)

def _seed_old(o):
    o.mkdir()
    (o / "merged.json").write_bytes(b"OLD-MERGED\n")
    (o / "unresolved.json").write_bytes(b"OLD-UNRESOLVED\n")


def _fail_nth_replace(mod, monkeypatch, n):
    real = os.replace
    calls = []

    def replace(src, dst):
        calls.append(os.path.basename(dst))
        if len(calls) == n:
            raise OSError("injected replace failure")
        return real(src, dst)

    monkeypatch.setattr(mod.os, "replace", replace)
    return calls


def test_failure_on_first_replace_leaves_both_old_and_no_tmp(tree, capsys, monkeypatch):
    f, o = tree
    put(f, "a.json", frag("a"))
    _seed_old(o)
    mod = _mod()
    calls = _fail_nth_replace(mod, monkeypatch, 1)
    rc = mod.main(["merge", str(f), "--out", str(o)])
    assert rc == 2 and "injected" in capsys.readouterr().err
    assert calls == ["unresolved.json"]
    assert (o / "merged.json").read_bytes() == b"OLD-MERGED\n"
    assert (o / "unresolved.json").read_bytes() == b"OLD-UNRESOLVED\n"
    assert sorted(os.listdir(o)) == ["merged.json", "unresolved.json"]


def test_failure_on_second_replace_leaves_new_unresolved_old_merged_no_tmp(tree, capsys, monkeypatch):
    f, o = tree
    put(f, "a.json", frag("a"))
    _seed_old(o)
    mod = _mod()
    calls = _fail_nth_replace(mod, monkeypatch, 2)
    rc = mod.main(["merge", str(f), "--out", str(o)])
    assert rc == 2
    assert calls == ["unresolved.json", "merged.json"]
    assert (o / "unresolved.json").read_bytes() == EMPTY_UNRESOLVED.encode()
    assert (o / "merged.json").read_bytes() == b"OLD-MERGED\n"
    assert sorted(os.listdir(o)) == ["merged.json", "unresolved.json"]


@pytest.mark.parametrize("n", [1, 2])
def test_failure_into_an_empty_out_leaves_no_tmp(tree, capsys, monkeypatch, n):
    f, o = tree
    put(f, "a.json", frag("a"))
    mod = _mod()
    _fail_nth_replace(mod, monkeypatch, n)
    assert mod.main(["merge", str(f), "--out", str(o)]) == 2
    left = sorted(os.listdir(o))
    assert left == ([] if n == 1 else ["unresolved.json"])


def test_data_error_only_input_writes_nothing_and_keeps_old_outputs(tree, capsys):
    f, o = tree
    raw(f, "a.json", b"garbage")
    _seed_old(o)
    rc, out, err = merge(f, o, capsys)
    assert rc == 1
    assert (o / "merged.json").read_bytes() == b"OLD-MERGED\n"
    assert (o / "unresolved.json").read_bytes() == b"OLD-UNRESOLVED\n"
    assert sorted(os.listdir(o)) == ["merged.json", "unresolved.json"]


def test_lone_surrogate_never_leaves_partial_output(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    raw(f, "b.json", json.dumps(frag("b", nodes=[{"id": "\ud83d"}])).encode())
    rc, out, err = merge(f, o, capsys)
    assert rc == 0 and "skipped corrupt: b.json:" in out
    assert (o / "merged.json").read_bytes().decode("utf-8")


# ---------------------------------------------------------------- step 6: containment (AC-4)

def test_out_equal_to_fragments_dir_is_refused(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    before = sorted(os.listdir(f))
    rc, out, err = merge(f, f, capsys)
    assert rc == 2 and sorted(os.listdir(f)) == before


def test_out_inside_fragments_dir_is_refused(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    rc, out, err = merge(f, f / "sub" / "deeper", capsys)
    assert rc == 2 and not (f / "sub").exists()


def test_out_spelled_through_a_symlink_to_fragments_is_refused(tmp_path, capsys):
    f = tmp_path / "frags"
    f.mkdir()
    put(f, "a.json", frag("a"))
    alias = tmp_path / "alias"
    os.symlink(str(f), str(alias))
    rc, out, err = run(["merge", str(f), "--out", str(alias / "x")], capsys)
    assert rc == 2 and not (f / "x").exists()


def test_fragments_spelled_through_a_symlink_still_contains_out(tmp_path, capsys):
    f = tmp_path / "frags"
    f.mkdir()
    put(f, "a.json", frag("a"))
    alias = tmp_path / "alias"
    os.symlink(str(f), str(alias))
    rc, out, err = run(["merge", str(alias), "--out", str(f / "x")], capsys)
    assert rc == 2 and not (f / "x").exists()


def test_sibling_directory_with_common_name_prefix_is_allowed(tmp_path, capsys):
    f = tmp_path / "frags"
    f.mkdir()
    put(f, "a.json", frag("a"))
    rc, out, err = run(["merge", str(f), "--out", str(tmp_path / "frags-out")], capsys)
    assert rc == 0


def test_out_that_is_a_symlink_is_refused(tree, capsys, tmp_path):
    f, o = tree
    put(f, "a.json", frag("a"))
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(str(real), str(o))
    rc, out, err = merge(f, o, capsys)
    assert rc == 2 and os.listdir(real) == []


@pytest.mark.parametrize("victim", ["merged.json", "unresolved.json"])
def test_preexisting_symlink_target_is_refused_and_untouched(tree, capsys, tmp_path, victim):
    f, o = tree
    put(f, "a.json", frag("a"))
    o.mkdir()
    decoy = tmp_path / "decoy.txt"
    decoy.write_bytes(b"DECOY")
    os.symlink(str(decoy), str(o / victim))
    rc, out, err = merge(f, o, capsys)
    assert rc == 2 and decoy.read_bytes() == b"DECOY"
    assert sorted(os.listdir(o)) == [victim]


def test_target_that_is_a_directory_is_refused(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    (o / "merged.json").mkdir(parents=True)
    assert merge(f, o, capsys)[0] == 2


def _snapshot(root):
    snap = {}
    for dirpath, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(dirpath, n)
            snap[os.path.relpath(p, root)] = os.lstat(p).st_mtime_ns if os.path.isfile(p) else None
    return snap


def test_relative_out_writes_only_the_fixed_names_and_nothing_elsewhere(tmp_path, capsys, monkeypatch):
    f = tmp_path / "work" / "frags"
    f.mkdir(parents=True)
    put(f, "a.json", frag("a"))
    (tmp_path / "bystander").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    before = _snapshot(tmp_path)
    rc, out, err = run(["merge", "frags", "--out", "out/nested"], capsys)
    assert rc == 0
    after = _snapshot(tmp_path)
    added = sorted(set(after) - set(before))
    assert added == ["work/out", "work/out/nested", "work/out/nested/merged.json",
                     "work/out/nested/unresolved.json"]
    assert {k: v for k, v in before.items()} == {k: after[k] for k in before}


def test_no_tmp_left_behind_on_success(tree, capsys):
    f, o = tree
    put(f, "a.json", frag("a"))
    merge(f, o, capsys)
    assert not [n for n in os.listdir(o) if n.endswith(".tmp")]


# ---- static guards

def _src():
    return MOD.read_text(encoding="utf-8")


def test_module_imports_no_network_or_process_modules():
    tree_ = ast.parse(_src())
    banned = {"subprocess", "socket", "urllib", "http", "requests", "ssl", "asyncio", "multiprocessing"}
    seen = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            seen.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            seen.add((node.module or "").split(".")[0])
    assert not (seen & banned), seen & banned


def test_module_compiles_and_parses_as_python_3_9(tmp_path):
    py_compile.compile(str(MOD), cfile=str(tmp_path / "x.pyc"), doraise=True)
    ast.parse(_src(), feature_version=(3, 9))


def test_no_pipe_union_in_annotations():
    tree_ = ast.parse(_src())
    anns = []
    for node in ast.walk(tree_):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            anns += [a.annotation for a in node.args.args + node.args.kwonlyargs if a.annotation]
            if node.returns:
                anns.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            anns.append(node.annotation)
    for a in anns:
        for sub in ast.walk(a):
            assert not (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)), ast.dump(a)


def test_module_has_a_main_guard_and_no_gh_calls():
    s = _src()
    assert 'if __name__ == "__main__"' in s
    assert "gh issue" not in s and "gh pr" not in s
