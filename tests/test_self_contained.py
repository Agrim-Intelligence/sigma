import json, pathlib, subprocess, sys, tempfile, shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Directories the leakage scan does not walk. ONLY virtualenv names.
#:
#: NOT `build`/`dist`/`*.egg-info`: this set is matched against every path COMPONENT, so adding
#: them would silently stop scanning skills/<x>/build/ — and skills/ is shipped twice over, by
#: install.sh and by marketplace.json's `source: "./"`. That is the same any-depth name heuristic
#: .gitignore rejects for /build/ and /dist/, and that the private side's licence guard's docstring records
#: as the first hole it had to close. Build residue is untracked and rare; a missed leak in shipped
#: surface is neither.
#:
#: WHY THE VENV EXCLUSION IS NEEDED, stated correctly: any in-repo `.venv` that can actually RUN
#: this suite already trips the guard, because pytest pulls in pygments, whose
#: lexers/_cocoa_builtins.py contains "Temporal". duckdb (via a sub-package's `pip install -e`)
#: contains it too, at value/constant/__init__.py. So this predates any sub-package becoming
#: pip-installable — it
#: is a property of having a virtualenv in the tree at all, not of #95.
#:
#: This constant is module-level so `test_leak_under_a_build_dir_is_still_caught` reads THE SAME
#: object the scan does. An earlier version of that test kept a private copy and therefore pinned
#: nothing: re-adding build/dist to the scan left it green.
SKIP_DIRS = frozenset({"tests", "__pycache__", ".pytest_cache", ".venv", "venv"})



def test_example_has_valid_sdlc():
    ex = ROOT / "examples" / "hello-sdlc"
    cfg = json.loads((ex / ".sdlc" / "config.json").read_text())   # valid JSON
    assert "budget" in cfg
    goals = list((ex / ".sdlc" / "goals").glob("[0-9]*.md"))
    assert goals, "example needs at least one numbered goal"


def test_example_loop_mechanics_run():
    """The example's loop runs end-to-end with a stub run_goal (mechanics, not the agent).
    Runs on a COPY so the committed example state isn't mutated."""
    ex = ROOT / "examples" / "hello-sdlc"
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(ex / ".sdlc", pathlib.Path(d) / ".sdlc")
        code = (
            "import importlib.util;"
            f"spec=importlib.util.spec_from_file_location('loop', r'{ROOT}/skills/agrim-loop/scripts/loop.py');"
            "lp=importlib.util.module_from_spec(spec);spec.loader.exec_module(lp);"
            f"r=lp.run_loop(r'{d}/.sdlc', lambda g:('done',''));"
            "print(r);assert r['done']==1 and r['stopped']=='backlog-empty'"   # consumes the goal, not vacuous
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_no_onshot_specifics_in_shipped_files():
    banned = ("media-orch", "OnShot", "Temporal", "RunPod", "/services/", "onshot")
    # include .tmpl: templates materialize into every user's repo, so they're shipped surface too
    scan_suffixes = (".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".tmpl")
    # Exclude tests/: test files legitimately NAME the banned words as leakage guards
    # (test_hook.py, test_packaging_slice4.py) — that's not host-project coupling in shipped logic.
    #
    # TRACKED files, not a filesystem walk (same principle as _tracked_files() / #581 below): only
    # the index answers "what ships". #1434 and #1461 each fought this the other way -- carving out
    # .sdlc/ and .claude/ by name once each was caught scanning gitignored local state -- but a
    # carve-out only covers the path it was filed for. Untracked clutter anywhere else (any stray
    # file sitting in the working tree, e.g. a one-off export dropped in the repo root) still trips
    # a raw rglob; it can never appear in `git ls-files`, so scanning that instead closes the whole
    # class instead of the next instance. See test_onshot_guard_ignores_untracked_files below.
    files = _tracked_files()
    if files is None:
        pytest.skip("not a git checkout (or git unavailable) — nothing to enumerate")
    offenders = []
    for f in files:
        rel = pathlib.PurePosixPath(f)
        if rel.suffix not in scan_suffixes:
            continue
        if SKIP_DIRS & set(rel.parts):
            continue
        text = (ROOT / f).read_text(errors="ignore")
        offenders += [f"{f}: {b}" for b in banned if b in text]
    assert not offenders, "host-project leakage in shipped files:\n" + "\n".join(offenders)


def test_onshot_guard_ignores_untracked_files(tmp_path, monkeypatch):
    """Pins the #1434/#1461-class fix above by calling the REAL guard, not a reimplementation:
    an untracked file sitting in the working tree must not fail it, but a tracked one still must.
    Monkeypatches the module's ROOT (which the guard and _tracked_files() both read) to point at a
    synthetic repo instead of writing into this repo's own root -- a version of this test that only
    re-checked `git ls-files` output in isolation would stay green even if the guard above were
    reverted to ROOT.rglob(); calling the guard itself is what makes this an actual regression pin."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    tracked = tmp_path / "leak.md"
    tracked.write_text("see OnShot here\n", encoding="utf-8")
    subprocess.run(["git", "add", "leak.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)

    untracked = tmp_path / "project.json"
    untracked.write_text('{"title": "OnShot"}\n', encoding="utf-8")

    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    with pytest.raises(AssertionError) as exc_info:
        test_no_onshot_specifics_in_shipped_files()
    message = str(exc_info.value)
    assert "leak.md" in message, "a tracked leak must still be caught"
    assert "project.json" not in message, "untracked clutter must not be flagged"


def test_leak_under_a_build_dir_is_still_caught(tmp_path):
    """The exclusion above must never grow to a name that can appear inside shipped surface.

    `skills/` ships via install.sh AND via marketplace.json's `source: "./"`, so a directory named
    `build` or `dist` under it is shipped surface, not residue. An earlier version of this change
    skipped both at any depth and hid a planted leak; this pins the fix.

    It reads the module-level SKIP_DIRS — the SAME object the scan uses — so adding a name to the
    exclusion fails here. An earlier version of this test kept a private copy of the set and
    therefore pinned nothing at all: re-adding build/dist to the scan left it green. Only the
    matching rule is re-implemented, against a fixture tree, because the real scan is hard-wired
    to ROOT.
    """
    banned = ("media-orch", "OnShot", "Temporal", "RunPod", "/services/", "onshot")
    leak = tmp_path / "skills" / "agrim-loop" / "build" / "leak.md"
    leak.parent.mkdir(parents=True)
    leak.write_text("see OnShot media-orch\n", encoding="utf-8")

    offenders = []
    for p in tmp_path.rglob("*"):
        if not p.is_file() or p.suffix not in (".py", ".md", ".json"):
            continue
        if SKIP_DIRS & set(p.relative_to(tmp_path).parts):
            continue
        text = p.read_text(errors="ignore")
        offenders += [b for b in banned if b in text]
    assert offenders, "a leak under skills/<x>/build/ must still be caught — that is shipped surface"


# --------------------------------------------------------------------------- #581: stray-file hygiene
#
# `marketplace.json` ships `source: "./"` and `plugin.json` carries no file list, so EVERY tracked
# file at ANY path is shipped surface. #530 removed a zero-byte `err` that had been shipping since
# `aab822c`; nothing stopped the next one. This is the guard.
#
# The rule is `size == 0 AND basename not in ZERO_BYTE_MARKERS`, and it is deliberately NOT
# conjoined with "extensionless" or "root-level", the two narrowing ideas the issue floated:
#   - extensionless would miss the equally common shell-redirect stray that DOES have one
#     (`out.txt`, `nohup.out`), and buys no precision — a survey of all 620 tracked files at the
#     time this landed found SIX extensionless files (2x .gitignore, 2x LICENSE, .dockerignore,
#     VERSION) and ZERO zero-byte ones, so zero-byte alone already has no false positives;
#   - root-level would miss a stray under `skills/`, which ships identically.
# A zero-byte file carries no content by definition, so it is only ever meaningful as a MARKER —
# and every legitimate marker has a well-known name. Hence a name allowlist rather than a path one.
#
# TRACKED files, not `rglob`: this asks "what ships", and only the index answers that. Walking the
# working tree would fail the suite on a developer's own untracked scratch file, which is noise, not
# a shipping defect.
#
#: Zero-byte basenames that are MEANINGFUL, not strays. None of these exist in the tree today (the
#: survey found no zero-byte tracked file at all) — the list is forward-looking, so that adding a
#: legitimate empty marker later is a normal commit instead of a CI failure someone has to decode.
ZERO_BYTE_MARKERS = frozenset({"__init__.py", "py.typed", ".gitkeep", ".keep", ".nojekyll"})


def _is_stray_zero_byte(path):
    """THE rule, shared by the real scan and the planted-stray pin below — the same
    single-object discipline SKIP_DIRS follows above, so weakening it cannot leave the pin green."""
    return path.is_file() and path.stat().st_size == 0 and path.name not in ZERO_BYTE_MARKERS


def _tracked_files():
    """Repo-relative paths git is actually tracking, or None when that can't be determined."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return [f for f in out.stdout.split("\0") if f] if out.returncode == 0 else None


def test_no_stray_zero_byte_file_is_tracked():
    """#581: a zero-byte tracked file is shipped surface that carries nothing — the exact shape of
    the `err` stray #530 removed. Any new one has to be a named marker or an explicit allowlist
    entry, which is a decision someone makes rather than a file nobody notices.

    Tracked PATHS, working-tree SIZES: the index answers "what ships", `stat` answers "how big is it
    here". They can disagree — a tracked file truncated locally fails here before it ships, which is
    the useful direction for a guard to lean."""
    files = _tracked_files()
    if files is None:
        pytest.skip("not a git checkout (or git unavailable) — nothing to enumerate")
    offenders = sorted(f for f in files if _is_stray_zero_byte(ROOT / f))
    assert not offenders, (
        "zero-byte tracked file(s) would ship to every plugin user:\n  "
        + "\n  ".join(offenders)
        + "\n\nDelete it, or add its basename to ZERO_BYTE_MARKERS if it is a real marker.")


def test_a_planted_stray_is_caught_and_a_real_marker_is_not(tmp_path):
    """Both directions of the rule, against a fixture tree — the same shape (and the same reason)
    as `test_leak_under_a_build_dir_is_still_caught` above: the real scan is hard-wired to the
    repo's own index, so the detection itself is pinned here instead.

    Because the real tree contains no zero-byte file at all, this fixture is the ONLY thing holding
    the rule's shape — so it plants one stray per REJECTED conjunct, and each is load-bearing:
      - `err` sits under `skills/`, not at the root, so a `root-level` narrowing fails here;
      - `out.log` carries an extension, so an `extensionless` narrowing fails here. Without it that
        narrowing passes untouched, because `err` happens to satisfy it too.
    Reads `_is_stray_zero_byte` rather than restating the rule, so neither narrowing can be made
    without this test going red."""
    stray = tmp_path / "skills" / "agrim-loop" / "err"          # the #530 shape, one level down
    stray.parent.mkdir(parents=True)
    stray.touch()
    redirect = tmp_path / "skills" / "agrim-loop" / "out.log"    # the same accident, with a suffix
    redirect.touch()
    marker = tmp_path / "skills" / "agrim-loop" / "__init__.py"  # legitimately empty
    marker.touch()
    real = tmp_path / "skills" / "agrim-loop" / "loop.py"
    real.write_text("x = 1\n", encoding="utf-8")

    assert _is_stray_zero_byte(stray), "the planted zero-byte stray was not caught"
    assert _is_stray_zero_byte(redirect), "an extension does not make a zero-byte stray legitimate"
    assert not _is_stray_zero_byte(marker), "an allowlisted empty marker must not be flagged"
    assert not _is_stray_zero_byte(real), "a file with content must not be flagged"
