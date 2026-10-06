#!/usr/bin/env python3
"""Knowledge-graph integration (optional, opt-in). Sigma captures external research + internal
analysis (+ optionally the code) into a corpus and hands it to an external graph builder (default:
the `graphify` CLI). This helper is the deterministic side — read config, locate the corpus, compute
the build plan per scope. Zero-dep; the builder is a soft dependency. Disabled by default.

The /sigma-kg SKILL drives the builder for an INTERACTIVE build. `refresh()` (issue #1562) is the
non-interactive counterpart: the one code path that invokes the builder unattended, called from
`loop.py::_record()` when `knowledge_graph.auto_refresh` is true, so the "rebuilds at the end of
each Retrospective" the docs have always promised is a mechanism rather than a sentence."""
import sys, json, pathlib, hashlib, re, subprocess, shlex, shutil, time, math

_DEFAULTS = {"enabled": False, "scope": "full", "builder": "graphify", "auto_refresh": False}


def load_config(sdlc_dir):
    """The knowledge_graph config block, merged over safe defaults. Missing/garbage config -> defaults
    (disabled), so a project that never opted in is never touched."""
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    kg = {**_DEFAULTS, **(cfg.get("knowledge_graph") or {})}
    kg["enabled"] = kg.get("enabled") is True       # strict: only explicit boolean true opts in
    return kg


def corpus_dir(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "knowledge"


def _gaps_file(sdlc_dir):
    return corpus_dir(sdlc_dir) / "gaps.md"


def gap_list(sdlc_dir):
    """Open knowledge gaps - logged query-misses, in log order. [] if none."""
    f = _gaps_file(sdlc_dir)
    if not f.exists():
        return []
    return [ln[2:].strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.startswith("- ") and not ln.startswith("- [x]")]      # open only; `- [x]` = resolved


def gap_log(sdlc_dir, question):
    """Record a query-miss so the graph tracks what it does NOT know - the backlog the loop can later
    fill. Exact (whitespace-normalized) duplicates are skipped so the gap log can't bloat into the
    very noise it exists to surface. Returns True if newly logged, False if empty or already known.
    ponytail: exact-match dedup; a re-missed resolved gap re-opens. Semantic dedup only if noisy."""
    question = " ".join(question.split())
    if not question or question in gap_list(sdlc_dir):
        return False
    f = _gaps_file(sdlc_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    head = "" if f.exists() else "# Knowledge gaps - logged query-misses (what the graph doesn't know yet)\n\n"
    with f.open("a", encoding="utf-8") as fh:
        fh.write(head + f"- {question}\n")
    return True


def gap_resolve(sdlc_dir, question):
    """Close an open gap (the loop filled it): mark its line `- [x] ...` so gap_list drops it. The
    resolved line stays as history (archive-not-delete). Returns True if a matching open gap was
    found; a later re-miss of the same question re-opens it via gap_log."""
    question = " ".join(question.split())
    f = _gaps_file(sdlc_dir)
    if not question or not f.exists():
        return False
    out, hit = [], False
    for ln in f.read_text(encoding="utf-8").splitlines():
        if not hit and ln.startswith("- ") and not ln.startswith("- [x]") and ln[2:].strip() == question:
            out.append(f"- [x] {question}"); hit = True
        else:
            out.append(ln)
    if hit:
        f.write_text("\n".join(out) + "\n", encoding="utf-8")
    return hit


def _count_md(d):
    return sum(1 for _ in d.rglob("*.md")) if d.exists() else 0


_MAINTAIN_THRESHOLD = 200       # research+analysis files before maintenance is flagged due
                                # ponytail: arbitrary; tune if it fires too early / too late


def _cited_paths(text):
    """Repo paths cited in backticks (must contain '/' + a file extension). ponytail: backtick
    heuristic only - URLs (with '://') are excluded by the charclass; bare filenames are skipped."""
    return set(re.findall(r"`([\w.\-/]+/[\w.\-]+\.\w+)`", text))


#: Web-capture retention (#2702). `research/web/` is written once per WebSearch/WebFetch by the
#: `research_capture` hook and nothing ever pruned it (2,814 files in 40 days on one machine). The
#: pass below archives — never deletes — by AGE (a policy default, configurable) and by a BYTE bound
#: DERIVED from the disk the corpus sits on: `disk.total * web_retention_disk_share`. A ratio, not a
#: size, so it is 49 MB on a 494 GB disk and 10 MB on a 100 GB laptop; `_occupancy` sums
#: `st_blocks * 512` (what the files actually take, 6.3x their content here) so the bound and its
#: measurement agree. Opt-in: `web_retention_enabled: true`, or the operator typing `kg.py retain`.
_WEB_RETENTION_DAYS = 90
_WEB_RETENTION_DISK_SHARE = 0.0001


def _occupancy(st):
    """Bytes a file takes on disk (block-rounded), falling back to its length where the OS has no
    `st_blocks` — the honest denominator for a bound sold as 'how much of this machine'."""
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks else st.st_size


def _web_settings(kg):
    """(days, share) from the config block, or raise ValueError naming the offending key. A bad
    share must REFUSE, not archive everything: `share <= 0` would make the bound 0."""
    days = kg.get("web_retention_days", _WEB_RETENTION_DAYS)
    share = kg.get("web_retention_disk_share", _WEB_RETENTION_DISK_SHARE)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or days < 0:
        raise ValueError(f"web_retention_days must be a number >= 0 (0 = age bound off), got {days!r}")
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        # (NaN and +-inf both fail the range test above, so no isfinite clause is needed here.)
        raise ValueError(f"web_retention_disk_share must be a number in (0, 1], got {share!r}")
    return days, share


def web_bound(sdlc_dir):
    """The derived byte bound for the live web set: a share of the TOTAL size of the volume holding
    the corpus (nearest existing ancestor when the web dir does not exist yet)."""
    _, share = _web_settings(load_config(sdlc_dir))
    p = corpus_dir(sdlc_dir) / "research" / "web"
    while not p.exists():
        p = p.parent
    return int(shutil.disk_usage(p).total * share)


def retain_web(sdlc_dir, force=False, now=None):
    """Archive old / over-bound web captures into `knowledge/archive/research/web/` (same names, same
    gitignored tree). Oldest-first single pass: a file goes when it is older than the age bound OR
    the running occupancy is still over the byte bound; the walk stops at the first file that is
    neither, so a second pass moves nothing. Each move is one same-volume `rename` — atomic and
    mtime-preserving (doctor's staleness rule is unaffected) — so a crash mid-pass loses nothing and
    the next pass simply continues. `force` is the hand-run `kg.py retain` verb: typing it IS the
    opt-in, so the `web_retention_enabled` gate is skipped (never the `enabled` gate).

    Returns a dict and NEVER raises: `ran` says whether the pass executed; `ok` says whether anything
    went wrong. A destination that already exists is `skipped` (never overwritten); a source another
    worker's `_record` already moved is `raced` (two loops on one `.sdlc` finishing together), not a
    failure; any other OSError stops the pass with `ok=False` and its text."""
    res = {"ok": True, "ran": False, "archived": 0, "skipped": 0, "raced": 0, "remaining": 0,
           "bound_bytes": None, "live_bytes": 0, "detail": ""}
    kg = load_config(sdlc_dir)
    if not kg.get("enabled"):
        res["detail"] = "knowledge graph disabled"; return res
    if not force and kg.get("web_retention_enabled") is not True:
        res["detail"] = "web_retention_enabled is not true (opt-in) — run `kg.py retain` by hand"
        return res
    try:
        days, _ = _web_settings(kg)
        bound = web_bound(sdlc_dir)
    except ValueError as exc:
        res.update(ok=False, detail=str(exc)); return res
    web = corpus_dir(sdlc_dir) / "research" / "web"
    res["bound_bytes"] = bound
    if not web.exists():
        res.update(ran=True, detail="no web captures yet"); return res
    files = []
    for p in web.glob("*.md"):
        try:
            st = p.stat()
        except FileNotFoundError:               # raced away between glob and stat
            res["raced"] += 1; continue
        files.append((st.st_mtime, p.name, _occupancy(st), p))
    files.sort()
    total = sum(f[2] for f in files)
    res["live_bytes"] = total
    cutoff = (now if now is not None else time.time()) - days * 86400 if days > 0 else None
    archive = corpus_dir(sdlc_dir) / "archive" / "research" / "web"
    res["ran"] = True
    gone = 0                                    # rename-phase races only; stat-phase ones never entered `files`
    for mtime, name, occ, p in files:
        if not ((cutoff is not None and mtime < cutoff) or total > bound):
            break
        dest = archive / name
        if dest.exists():
            res["skipped"] += 1; continue      # never overwrite; the live copy stays and is reported
        try:
            archive.mkdir(parents=True, exist_ok=True)
            p.rename(dest)
        except FileNotFoundError:
            res["raced"] += 1; gone += 1
        except OSError as exc:
            res.update(ok=False, detail=f"archiving {name} failed: {exc}"); break
        else:
            res["archived"] += 1
        total -= occ
    res["remaining"] = len(files) - res["archived"] - gone
    res["live_bytes"] = total
    if res["ok"]:
        res["detail"] = f"archived {res['archived']} web captures -> {archive}"
    return res


def _retention_line(res):
    """One console line for a retention result; the caller picks the stream."""
    if not res["ok"]:
        return f"knowledge-graph retention: FAILED — {res['detail']}"
    if not res["ran"]:
        return f"knowledge-graph retention: off — {res['detail']}"
    extra = "".join(f", {res[k]} {k}" for k in ("skipped", "raced") if res[k])
    return (f"knowledge-graph retention: archived {res['archived']} web capture(s){extra}, "
            f"{res['remaining']} remain ({res['live_bytes'] // 1024} KiB of a "
            f"{res['bound_bytes'] // 1024} KiB bound derived from this disk)")


def _tracked_notes(analysis):
    """Paths (relative to `analysis`) git tracks there, or an empty set: no `.git` (file or dir), a
    failing git, or a timeout all mean 'nothing is tracked' -> `mv`, never `rm`. ONE call per
    `maintain` report, whatever the number of proposals (measured 5.5 ms per fork on a 400-note dir)."""
    if not (analysis / ".git").exists():
        return set()
    try:
        r = subprocess.run(["git", "-C", str(analysis), "ls-files", "-z"], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {t for t in r.stdout.split("\0") if t} if r.returncode == 0 else set()


def _archive_cmds(analysis, knowledge, notes, reason, tracked=frozenset()):
    """The exact, quoted shell command that archives `notes` (paths relative to the corpus) — and
    nothing runs it here. Two shapes: a note git tracks in a `.git`-bearing analysis dir (#2698's
    shared ops-branch worktree, where `.git` is a FILE) is `git rm` + one commit — the commit IS the
    archive, history keeps the note and every synced machine shrinks; anything else (plain files, or a
    note `kg.py note` wrote since the last publish and git has never seen) is `mv -n` into
    `knowledge/archive/analysis/`, mirroring its relative path. `tracked` comes from
    `_tracked_notes`, computed once by the caller. The commit is pathspec-limited to the notes it
    removes, so anything else staged in that worktree is left alone. Empty `notes` -> ""."""
    q = shlex.quote
    git_rm, mv = [], []
    for rel in notes:
        in_analysis = str(pathlib.PurePosixPath(rel).relative_to("analysis"))
        if in_analysis in tracked:
            git_rm.append(f"git -C {q(str(analysis))} rm -q -- {q(in_analysis)}")
        else:
            dest = knowledge / "archive" / rel
            mv.append(f"mkdir -p {q(str(dest.parent))} && mv -n {q(str(knowledge / rel))} {q(str(dest))}")
    if git_rm:
        git_rm.append(f"git -C {q(str(analysis))} commit -q -m {q('archive: ' + reason)} -- "
                      + " ".join(q(str(pathlib.PurePosixPath(rel).relative_to("analysis")))
                                 for rel in notes if str(pathlib.PurePosixPath(rel).relative_to("analysis")) in tracked))
    return " && ".join(git_rm + mv)


def _stats(d):
    """stat() of every capture in `d`, skipping one a concurrent retention pass renamed mid-glob."""
    out = []
    for p in (d.glob("*.md") if d.exists() else ()):
        try:
            out.append(p.stat())
        except FileNotFoundError:
            continue
    return out


def maintain_report(sdlc_dir, repo_root="."):
    """Report-only maintenance audit of the knowledge corpus - proposes, never archives/deletes:
    - stale: analysis notes citing a repo path that no longer exists
    - dups:  groups of analysis notes with byte-identical content
    - apply: one runnable archive command per proposal (#2702) — printed, never executed
    - web:   the live web-capture set against its derived byte bound, and what is archived already
    - counts / over_threshold: corpus-size signal
    ponytail: exact-content dedup + backtick-path citations; richer signals only if this proves noisy."""
    corpus = corpus_dir(sdlc_dir)
    analysis = corpus / "analysis"
    root = pathlib.Path(repo_root)
    notes = sorted(analysis.rglob("*.md")) if analysis.exists() else []
    stale, by_hash = [], {}
    for n in notes:
        text = n.read_text(encoding="utf-8")
        dead = sorted(p for p in _cited_paths(text) if not (root / p).exists())
        if dead:
            stale.append({"note": str(n.relative_to(corpus)), "missing": dead})
        by_hash.setdefault(hashlib.sha256(text.encode("utf-8")).hexdigest(), []).append(
            str(n.relative_to(corpus)))
    dups = sorted(sorted(v) for v in by_hash.values() if len(v) > 1)
    tracked = _tracked_notes(analysis) if notes else set()
    proposed = set()                            # a note in two proposals gets ONE command (the first)
    apply = []
    for s in stale:
        apply.append({"reason": f"stale {s['note']} (cites missing {', '.join(s['missing'])})",
                      "cmd": _archive_cmds(analysis, corpus, [s["note"]], f"stale {s['note']}", tracked)})
        proposed.add(s["note"])
    for grp in dups:
        members = [m for m in grp[1:] if m not in proposed]
        apply.append({"reason": f"duplicate of {grp[0]}: {', '.join(grp[1:])}",
                      "cmd": _archive_cmds(analysis, corpus, members, f"duplicates of {grp[0]}", tracked)})
        proposed.update(members)
    kg = load_config(sdlc_dir)
    web_dir, arch_dir = corpus / "research" / "web", corpus / "archive" / "research" / "web"
    live, archived = _stats(web_dir), _stats(arch_dir)
    try:
        bound = web_bound(sdlc_dir) if kg.get("enabled") else None
    except ValueError:
        bound = None
    web = {"count": len(live), "bytes": sum(_occupancy(s) for s in live), "bound_bytes": bound,
           "over": bound is not None and sum(_occupancy(s) for s in live) > bound,
           "retention": kg.get("web_retention_enabled") is True,
           "archived_count": len(archived), "archived_bytes": sum(_occupancy(s) for s in archived)}
    counts = {"research": _count_md(corpus / "research"), "analysis": len(notes),
              "gaps": len(gap_list(sdlc_dir))}
    return {"stale": stale, "dups": dups, "apply": apply, "web": web, "counts": counts,
            "over_threshold": counts["research"] + counts["analysis"] > _MAINTAIN_THRESHOLD}


def _discovery_source(sdlc_dir):
    """The configured backlog source ('github' or 'local-goals'), same one-line read
    `sources.py`'s `get_source()` factory already does, kept inline (not imported) so `kg.py` stays
    zero-dep. Tolerates missing/garbage config the same way `load_config` does; defaults to
    'local-goals', matching `get_source()`'s own default."""
    try:
        cfg = json.loads((pathlib.Path(sdlc_dir) / "config.json").read_text())
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return ((cfg.get("discovery") or {}).get("source")) or "local-goals"


def note_id(sdlc_dir, ref):
    """Derive the KG analysis-note filename stem for a goal reference (issue #1050) — the
    going-forward id scheme, matching the 2026-08-15 historical backfill's `issue-<N>` files
    exactly in github mode, and extending it to local-goals mode.

    - github mode: `ref` is (or contains) an issue number -> `issue-<N>`.
    - local-goals mode: `ref` may be a full goal path (`.sdlc/goals/0004-foo.md`), a bare stem
      (`0004-foo`), or a bare numeric id (`7`) — take the leading digit run of the basename, the
      SAME id convention `sources.py`'s `LocalSource._resolve_ref`/`create_dependency` already use
      as the one, single source of truth for what a local goal's id means (`p.name.split("-", 1)[0]`
      all-digits). Zero-padded to 4 digits either way, so a bare id and its file-derived form always
      produce the identical note filename. Falls back to `goal-<ref>` verbatim (never raises) when no
      digits are found at all — an edge case not reachable via the wired `/sigma-loop`/`/sigma-goal`
      call (which always passes a real goal path), kept safe for any future direct caller."""
    ref = str(ref).strip()
    if _discovery_source(sdlc_dir) == "github":
        digits = re.sub(r"\D", "", ref)
        return f"issue-{digits}" if digits else f"issue-{ref}"
    stem = pathlib.Path(ref).name
    stem = stem[:-3] if stem.endswith(".md") else stem
    lead = stem.split("-", 1)[0]
    return f"goal-{int(lead):04d}" if lead.isdigit() else f"goal-{stem}"


def write_note(sdlc_dir, note_id_str, content):
    """Write one compact analysis note to the KG corpus — the going-forward half of the historical
    backfill (issue #1050). Gated on `knowledge_graph.enabled`, the identical strict-true check
    `build_plan` already uses, so a project that never opted in sees zero behavior change. Returns
    `False` (no-op; fail-open) when disabled, `True` when written. Overwrites any existing note at
    the same id on purpose — a goal can only complete once, but a re-run (e.g. a re-opened issue)
    should refresh rather than duplicate."""
    if not load_config(sdlc_dir).get("enabled"):
        return False
    analysis = corpus_dir(sdlc_dir) / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / f"{note_id_str}.md").write_text(content.rstrip("\n") + "\n", encoding="utf-8")
    return True


def build_plan(sdlc_dir, repo_root="."):
    """The build plan for the configured scope, or None if KG is disabled.
    scope 'full' includes the code tree; 'research' (anything else) is corpus-only."""
    kg = load_config(sdlc_dir)
    if not kg.get("enabled"):
        return None
    include_code = kg.get("scope", "full") == "full"
    return {
        "scope": kg.get("scope", "full"),
        "builder": kg.get("builder", "graphify"),
        "corpus": str(corpus_dir(sdlc_dir)),
        "include_code": include_code,
        "code_root": str(pathlib.Path(repo_root).resolve()) if include_code else None,
        "auto_refresh": bool(kg.get("auto_refresh")),
    }


#: Wall-clock cap on one unattended builder run, in seconds. Overridable per project with
#: `knowledge_graph.refresh_timeout_seconds` because the honest bound is a property of the CORPUS,
#: not of this machine: the first (cold) build extracts every document, while every later one
#: re-bills only what changed (graphify keeps a per-file content cache), so a repo adopting the
#: graph with a large existing corpus needs a bigger number exactly once. A cap has to exist at all
#: because this runs inside `_record()` — an unbounded build would stall a completed goal forever.
_REFRESH_TIMEOUT = 900


def _run_builder(argv, timeout=None):
    """The real subprocess call, isolated so `refresh()`'s tests can inject a fake and assert on the
    argv that WOULD have run. Returns the same dict shape a fake must return."""
    if argv[0] != "graphify" and not _shell_opt_in(pathlib.Path(argv[-1])):
        # #707 (TM-04): a repo-configured builder is a repository-supplied executable. Gated HERE, at
        # the one real spawn, so every caller (loop.py record, a direct `kg.py refresh`) is covered.
        # A DUPLICATE of shell_policy's Git-local read, never an import (a skill must not import a
        # sibling's Python).
        return {"returncode": 126, "stdout": "", "stderr": (
            "REFUSED: repository-configured knowledge_graph.builder requires explicit operator trust; "
            "run `git -C <trusted-project> config --local sigma.allowRepositoryShellCommands true` "
            "after inspecting the project")}
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {"returncode": r.returncode, "stdout": r.stdout or "", "stderr": r.stderr or ""}


def _shell_opt_in(root):
    """The operator's Git-local opt-in (`sigma.allowRepositoryShellCommands`); any failure is no."""
    try:
        r = subprocess.run(["git", "-C", str(root), "config", "--local", "--type=bool",
                            "sigma.allowRepositoryShellCommands"],
                           capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip().lower() == "true"


def refresh(sdlc_dir, repo_root=".", runner=None):
    """Rebuild the graph via the configured builder — but ONLY when `knowledge_graph.auto_refresh`
    is true. Issue #1562: three docs promised this ran at the end of every Retrospective and nothing
    anywhere called a builder, so a 500-document corpus sat un-graphed with no error to notice.

    Returns `{"ok", "ran", "detail"}` and NEVER raises. `ran` is the load-bearing field: it reports
    whether the builder was actually invoked, which is the only thing a caller (or a negative
    control) can check that an implementation ignoring the gate could not also fake. `ok` is about
    whether anything went WRONG — a correctly gated no-op is `ok=True, ran=False`.

    Fail-open, exactly like `note()` above: disabled, gated off, `scope: full`, no corpus, a missing
    builder or a builder that fails all degrade to a reported result. `_record()` calls this after a
    goal is already recorded, so an exception escaping here would cost bookkeeping that already
    landed.

    TWO DELIBERATE NON-DECISIONS, both of which belong to the builder and not to Sigma:

    * **No `--backend` is passed.** Choosing one would mean reimplementing graphify's own
      `detect_backend()` priority, which encodes a security finding (ollama must never silently
      shadow a paid key). Duplicating it here is the hardened-sibling-divergence bug class
      `doctor.py::_load_loop_script` already names. It also means Sigma never picks a backend
      that bills someone, and never spawns a nested agent CLI from inside a goal slot. When no
      backend is configured the builder says so, naming the env var to set, and that text is
      surfaced verbatim rather than flattened into "refresh failed".
    * **`--out` IS passed, and points at the repo root** (the parent of `.sdlc`, the same directory
      `status()` derives `graph_built` from). Without it the builder writes `<corpus>/graphify-out/`
      — so a fully successful build lands where `status()`, `/sigma-context`'s gate and `graphify
      query`'s default path all structurally cannot see it. That is the second half of #1562, and
      this repo still carries the evidence: an empty `.sdlc/knowledge/graphify-out/` from a hand-run
      on 2026-08-24, with `status` reporting `graph: not built` ever since.

    The `extract ... --out` shape is graphify's CLI. A project pointing `builder` at something else
    must present the same two verbs; `kg.py` already assumes that builder shape in `status()`, which
    hardcodes `<builder>-out/graph.json`.
    """
    res = _refresh(sdlc_dir, repo_root, runner)
    if res["ran"] or not res["ok"]:
        _write_refresh_record(sdlc_dir, res)
    return res


def _refresh(sdlc_dir, repo_root, runner):
    plan = build_plan(sdlc_dir, repo_root)
    if plan is None:
        return {"ok": True, "ran": False, "detail": "knowledge graph disabled — nothing to refresh"}
    if not plan["auto_refresh"]:
        return {"ok": True, "ran": False,
                "detail": "auto_refresh is off — run /sigma-kg by hand to refresh the graph"}
    if plan["include_code"]:
        # Named, not silently half-done: building only the corpus would ship a graph missing the
        # code `scope: full` explicitly asked for, and nothing downstream could tell.
        return {"ok": False, "ran": False, "detail": (
            "scope: full auto-refresh is not implemented — it needs a separate code extract plus a "
            "merge-graphs pass. Run /sigma-kg by hand, or set scope: research")}
    corpus = pathlib.Path(plan["corpus"])
    if not corpus.exists() or not any(corpus.rglob("*.md")):
        return {"ok": True, "ran": False,
                "detail": f"corpus {corpus} is empty — nothing to refresh yet"}
    out_root = pathlib.Path(sdlc_dir).resolve().parent
    timeout = load_config(sdlc_dir).get("refresh_timeout_seconds", _REFRESH_TIMEOUT)
    argv = [plan["builder"], "extract", str(corpus), "--out", str(out_root)]
    try:
        r = (runner or _run_builder)(argv, timeout=timeout)
    except Exception as exc:                # noqa: BLE001 — a KG hiccup must never break a retro
        return {"ok": False, "ran": False,
                "detail": f"could not run the '{plan['builder']}' builder: {exc}"}
    if r["returncode"] == 0:
        return {"ok": True, "ran": True, "detail": f"graph refreshed into {out_root}/"
                                                   f"{plan['builder']}-out"}
    msg = (r["stderr"] or r["stdout"] or f"exit {r['returncode']}").strip()
    return {"ok": False, "ran": True, "detail": msg[:500]}


#: #2704: the last real refresh outcome, so the builder's own words outlive the goal that produced
#: them. `_record` forwards `refresh`'s stderr once into a long stream; on the maintainer's machine
#: that was the ONLY place "No LLM backend configured" ever appeared, and `graphify-out/` sat empty
#: for three weeks. Written only when the builder ran or `refresh` refused for a real reason -- a
#: correctly gated no-op (graph off, auto_refresh off, empty corpus) writes nothing, so a repo that
#: never opted in never grows a state file. One writer (refresh); `warn()` and doctor read it.
def _refresh_record_path(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "kg-refresh.json"


def _write_refresh_record(sdlc_dir, res):
    try:
        p = _refresh_record_path(sdlc_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": time.time(), **res}), encoding="utf-8")
    except Exception:                       # noqa: BLE001 -- fail-open: the goal is already recorded
        pass


def last_refresh(sdlc_dir):
    """The last recorded refresh outcome (`at`, `ok`, `ran`, `detail`), or None if none was ever
    recorded on this machine. Garbage reads as None."""
    try:
        rec = json.loads(_refresh_record_path(sdlc_dir).read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) and "at" in rec else None
    except Exception:                       # noqa: BLE001
        return None


def corpus_size(sdlc_dir):
    """(documents, bytes) over every `.md` under the corpus -- the same glob `refresh()` hands the
    builder and doctor's staleness check reads, so "the corpus" means one thing everywhere. The
    first-build guide asks for a MEASURED size, and this is the measurement."""
    docs = list(corpus_dir(sdlc_dir).rglob("*.md")) if corpus_dir(sdlc_dir).exists() else []
    return len(docs), sum(p.stat().st_size for p in docs)


def _human(size):
    """Bytes for a human: `<1 KB`, `N KB`, or `N.N MB` -- never a rounded-to-zero `0 KB`."""
    if size < 1024:
        return "<1 KB"
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def _age(seconds):
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


#: `warn()` prints at most once per this many seconds, per repo, across every caller (the session
#: hook and `loop.py next` share the stamp). The issue asked for "at most once a day".
_WARN_INTERVAL = 86400


def _warn_stamp(sdlc_dir):
    return pathlib.Path(sdlc_dir) / "state" / "kg-warned.stamp"


def warn(sdlc_dir):
    """#2704. One line, or "". Fires when `auto_refresh` is on and the graph is ABSENT (never
    built) or STALE (a corpus document is newer than it) -- and then at most once a day.

    Names what can be named without guessing: the builder missing from PATH (`shutil.which`), else
    the last recorded refresh's own text (the builder's words, verbatim), else the fact that no
    refresh has ever been attempted here. Sigma never picks an LLM backend, so it never says
    "set GEMINI_API_KEY" on its own authority -- if the builder said it, it is quoted. States the
    measured corpus size and says plainly that the first build's cost was not measured.

    Silent (and writes nothing) when the graph is disabled, auto_refresh is off, the corpus is
    empty (nothing to build from -- `refresh()` says the same), or the graph is fresh -- the
    issue's negative controls, and the shipped default. `auto_refresh` must be the boolean `true`
    here, as doctor's row requires; `build_plan` is looser (`bool(...)`), so a stringy "yes" refreshes
    but is never warned about -- tightening that is a change to `refresh`'s gate, not to this one. The staleness rule is the same
    one doctor.py's "knowledge graph auto-refresh is working" row restates; keep them in lockstep.
    Never raises."""
    try:
        kg = load_config(sdlc_dir)
        if not kg.get("enabled") or kg.get("auto_refresh") is not True:
            return ""
        builder = kg.get("builder", "graphify")
        base = pathlib.Path(sdlc_dir)
        graph = base.resolve().parent / f"{builder}-out" / "graph.json"
        corpus = corpus_dir(sdlc_dir)
        docs = list(corpus.rglob("*.md")) if corpus.exists() else []
        if not docs:
            return ""                       # nothing to build from yet -- refresh() says the same
        if graph.exists():
            g_mtime = graph.stat().st_mtime
            newer = [p for p in docs if p.stat().st_mtime > g_mtime]
            if not newer:
                return ""
            newest = max(newer, key=lambda p: p.stat().st_mtime)
            state = (f"the graph at {builder}-out/graph.json is stale: {len(newer)} corpus "
                     f"document(s) are newer than it (newest: {newest.relative_to(corpus)}; graph "
                     f"built {_age(time.time() - g_mtime)} ago)")
        else:
            state = f"the graph has never been built ({builder}-out/graph.json is absent)"
        stamp = _warn_stamp(sdlc_dir)
        try:
            if 0 <= time.time() - stamp.stat().st_mtime < _WARN_INTERVAL:
                return ""
        except OSError:
            pass
        rec = last_refresh(sdlc_dir)
        if shutil.which(builder) is None:
            why = (f"the '{builder}' builder is not on PATH"
                   + (" (pip install graphifyy)" if builder == "graphify" else ""))
        elif rec is None:
            why = ("auto-refresh has never attempted a build on this machine -- no goal has "
                   "completed a Retrospective here since auto_refresh was turned on")
        elif not rec.get("ok"):
            detail = " ".join(str(rec.get("detail", "")).split())[:300]
            why = f"last auto-refresh attempt {_age(time.time() - rec['at'])} ago FAILED: {detail}"
        elif graph.exists() and newest.stat().st_mtime > rec["at"]:
            # A built graph, an ok record, and documents written since: the ordinary state between
            # a successful build and the next Retrospective (a `gap log` alone makes it so). True
            # and unalarming -- never "build it by hand", there is nothing pending but the next retro.
            why = (f"last auto-refresh attempt {_age(time.time() - rec['at'])} ago reported ok; "
                   f"the documents above were written since, and the next Retrospective rebuilds it")
        elif graph.exists():
            # Review round 2, N1: the ok record is NEWER than every stale document, so that build ran
            # over them and left graph.json untouched -- a builder exiting 0 without writing, or
            # writing somewhere other than --out. The next retro will not fix that; say so.
            why = (f"last auto-refresh attempt {_age(time.time() - rec['at'])} ago reported ok, yet "
                   f"{builder}-out/graph.json was not updated by it")
        else:
            why = (f"last auto-refresh attempt {_age(time.time() - rec['at'])} ago reported ok, yet "
                   f"no graph is where status() looks")
        n, size = corpus_size(sdlc_dir)
        text = f"knowledge graph: auto_refresh is on but {state}. Why: {why}. Corpus: {n} documents, {_human(size)}. "
        if graph.exists():
            text += "Run /sigma-kg to refresh it by hand; /sigma-doctor carries the standing row."
        else:
            text += ("First-build cost: not measured on this machine. Run /sigma-kg to build it by hand "
                     "and read what the builder reports; /sigma-doctor carries the standing row.")
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        except OSError:
            pass                            # a stamp that cannot be written costs a repeat, not a crash
        return text
    except Exception:                       # noqa: BLE001 -- a warning helper must never be the outage
        return ""


def status(sdlc_dir):
    kg = load_config(sdlc_dir)
    corpus = corpus_dir(sdlc_dir)
    docs, size = corpus_size(sdlc_dir)
    return {
        **kg,
        "corpus": str(corpus),
        "research_files": _count_md(corpus / "research"),
        "analysis_files": _count_md(corpus / "analysis"),
        "corpus_docs": docs,
        "corpus_bytes": size,
        # the builder writes <builder>-out/ at the repo root (parent of .sdlc); graphify -> graphify-out
        "graph_built": (pathlib.Path(sdlc_dir).resolve().parent
                        / f"{kg.get('builder', 'graphify')}-out" / "graph.json").exists(),
    }


USAGE = ("usage: kg.py status <sdlc_dir> | plan <sdlc_dir> [repo_root] | "
         "refresh <sdlc_dir> [repo_root] | retain <sdlc_dir> | warn <sdlc_dir> | maintain <sdlc_dir> [repo_root] | "
         'gap log "<q>" [sdlc_dir] | gap list [sdlc_dir] | gap resolve "<q>" [sdlc_dir] | '
         "<content on stdin> | note <sdlc_dir> <ref> | note-check <sdlc_dir> <ref>")


def main(argv):
    if argv[1:] in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    sdlc = argv[2] if len(argv) > 2 else ".sdlc"
    if len(argv) >= 2 and argv[1] == "status":
        s = status(sdlc)
        if not s["enabled"]:
            print("knowledge-graph: disabled (set knowledge_graph.enabled=true in .sdlc/config.json)")
            return 0
        print(f"knowledge-graph: ENABLED | scope={s['scope']} builder={s['builder']} "
              f"auto_refresh={s['auto_refresh']} | corpus={s['corpus']} "
              f"(research {s['research_files']}, analysis {s['analysis_files']}; "
              f"{s['corpus_docs']} documents, {_human(s['corpus_bytes'])}) | "
              f"graph: {'built' if s['graph_built'] else 'not built'}")
        return 0
    if len(argv) >= 2 and argv[1] == "plan":
        plan = build_plan(sdlc, argv[3] if len(argv) > 3 else ".")
        print("knowledge-graph: disabled — nothing to build." if plan is None
              else json.dumps(plan, indent=2))
        return 0
    if len(argv) >= 2 and argv[1] == "refresh":
        # Exit code separates a GATED NO-OP from a real failure: 0 for both "did nothing, correctly"
        # and "built it", 1 only when something actually went wrong. `loop.py::_record` reports on
        # the strength of that distinction, so a repo with the graph off stays silent.
        # #2702: web-capture retention rides this same record-time hop, FIRST, so the builder below
        # consumes the trimmed live set — and independently of `auto_refresh`, which gates only the
        # build. Silent unless it moved, skipped or raced something, or refused (same routing rule).
        ret = retain_web(sdlc)
        if not ret["ok"] or ret["archived"] or ret["skipped"] or ret["raced"]:
            print(_retention_line(ret), file=sys.stderr)
        res = refresh(sdlc, argv[3] if len(argv) > 3 else ".")
        # STREAM ROUTING IS LOAD-BEARING, not cosmetic. A correctly gated no-op goes to stdout: a
        # human running this by hand still learns why nothing happened, while `loop.py::_record`
        # (which forwards only stderr) stays silent on every goal in every repo that never turned
        # the graph on. Anything else — a real build, or any failure — goes to stderr, so it reaches
        # the console where a human will see it. That is the LIVENESS half of #1562: a refresh that
        # died must be distinguishable from one with nothing to do.
        quiet_no_op = res["ok"] and not res["ran"]
        print(f"knowledge-graph refresh: {'ok' if res['ok'] else 'FAILED'} — {res['detail']}",
              file=sys.stdout if quiet_no_op else sys.stderr)
        return 0 if res["ok"] and ret["ok"] else 1
    if len(argv) >= 2 and argv[1] == "retain":
        # The operator's hand-run: typing the verb is the opt-in, so `web_retention_enabled` is not
        # required (the graph itself still must be enabled). Real action or failure -> stderr.
        ret = retain_web(sdlc, force=True)
        acted = not ret["ok"] or ret["archived"] or ret["skipped"] or ret["raced"]
        print(_retention_line(ret), file=sys.stderr if acted else sys.stdout)
        return 0 if ret["ok"] else 1
    if len(argv) >= 2 and argv[1] == "warn":
        # #2704: stdout, exit 0 either way -- "" means nothing is due (fresh, off, or warned within
        # the day). A caller wraps a non-empty line where its human will see it: the SessionStart
        # hook as additionalContext, `loop.py next` on stderr. A non-zero exit here would mean the
        # helper itself broke, which it is written not to do.
        text = warn(sdlc)
        if text:
            print(text)
        return 0
    if len(argv) >= 2 and argv[1] == "maintain":
        sdlc = argv[2] if len(argv) > 2 else ".sdlc"
        rep = maintain_report(sdlc, argv[3] if len(argv) > 3 else ".")
        c = rep["counts"]
        print(f"knowledge-graph maintain (report-only): research {c['research']}, analysis "
              f"{c['analysis']}, gaps {c['gaps']}" + (" | OVER THRESHOLD" if rep["over_threshold"] else ""))
        cmds = iter(rep["apply"])
        for s in rep["stale"]:
            print(f"  stale: {s['note']} -> missing {', '.join(s['missing'])}")
            print(f"    apply: {next(cmds)['cmd'] or '(already covered by a proposal above)'}")
        for grp in rep["dups"]:
            print("  duplicate: " + " == ".join(grp))
            print(f"    apply: {next(cmds)['cmd'] or '(already covered by a proposal above)'}")
        if not rep["stale"] and not rep["dups"]:
            print("  clean: no stale or duplicate notes.")
        w = rep["web"]
        kib = lambda b: f"{b // 1024} KiB"                          # noqa: E731
        bound = f"bound {kib(w['bound_bytes'])} (derived from this disk)" if w["bound_bytes"] is not None \
            else "bound n/a (graph disabled or limits malformed)"
        print(f"  web captures: {w['count']} live ({kib(w['bytes'])}), {bound}"
              f"{' | OVER BOUND' if w['over'] else ''}; archived so far: {w['archived_count']} "
              f"({kib(w['archived_bytes'])}) under knowledge/archive/research/web "
              f"(still in a cold build's input; delete it yourself if you want it out of the graph)")
        if w["retention"]:
            print("    retention: automatic (web_retention_enabled: true) at every recorded goal")
        elif w["over"]:
            print(f"    apply: python3 {shlex.quote(str(pathlib.Path(__file__).resolve()))} retain "
                  f"{shlex.quote(sdlc)}   # or set knowledge_graph.web_retention_enabled: true to automate")
        print("  (report-only: nothing archived/deleted; review + run the apply lines yourself, from "
              "this same directory - archive, don't delete.)")
        return 0
    if len(argv) >= 3 and argv[1] == "gap":
        sub = argv[2]
        if sub == "log" and len(argv) >= 4:
            gdir = argv[4] if len(argv) > 4 else ".sdlc"
            print(f"kg gap: logged - {argv[3]}" if gap_log(gdir, argv[3])
                  else f"kg gap: already known (skipped) - {argv[3]}")
            return 0
        if sub == "list":
            gaps = gap_list(argv[3] if len(argv) > 3 else ".sdlc")
            print("\n".join(f"- {g}" for g in gaps) if gaps else "kg gap: no gaps logged.")
            return 0
        if sub == "resolve" and len(argv) >= 4:
            gdir = argv[4] if len(argv) > 4 else ".sdlc"
            print(f"kg gap: resolved - {argv[3]}" if gap_resolve(gdir, argv[3])
                  else f"kg gap: no open gap matching - {argv[3]}")
            return 0
        print('usage: kg.py gap log "<q>" [sdlc_dir] | gap list [sdlc_dir] | gap resolve "<q>" [sdlc_dir]',
              file=sys.stderr)
        return 2
    if len(argv) >= 4 and argv[1] == "note":
        gdir, ref = argv[2], argv[3]
        content = sys.stdin.read()
        if not content.strip():
            print("usage: <content on stdin> | kg.py note <sdlc_dir> <ref>", file=sys.stderr)
            return 2
        nid = note_id(gdir, ref)
        if write_note(gdir, nid, content):
            print(f"kg note: wrote {gdir}/knowledge/analysis/{nid}.md")
        else:
            print("kg note: knowledge graph disabled, skipped (no-op)")
        return 0
    if len(argv) >= 4 and argv[1] == "note-check":
        # #2701: the read-only sibling of `note` — does the note `note` would write for this ref
        # exist? Same `enabled` gate as `write_note`, same filename via `note_id`, same corpus path.
        # Stream routing is the gate for `loop.py::_record`, exactly as with `refresh`: disabled and
        # present go to stdout, so a record forwarding only stderr stays silent; MISSING goes to
        # stderr, one line, naming the path. rc 1 on missing is a finding for a shell caller to
        # branch on, not a fault — `_record` ignores it and never refuses the record over it.
        gdir, ref = argv[2], argv[3]
        if not load_config(gdir).get("enabled"):
            print("kg note-check: knowledge graph disabled, skipped (no-op)")
            return 0
        path = corpus_dir(gdir) / "analysis" / f"{note_id(gdir, ref)}.md"
        if path.exists():
            print(f"kg note-check: present {path}")
            return 0
        print(f"knowledge note missing for {ref}: expected {path} — sigma-retro step 4 writes it "
              "(kg.py note)", file=sys.stderr)
        return 1
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
