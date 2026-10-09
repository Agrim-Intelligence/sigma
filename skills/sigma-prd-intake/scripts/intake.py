#!/usr/bin/env python3
"""intake.py (#822) -- the deterministic half of `sigma-prd-intake`, the PRD door into the Dossier
pipeline (`docs/dossier-pipeline.md` Stage 0).

The MODEL reads the PRD and drafts an answers file; this script never reads a PRD with an LLM. It
validates that every answer carries a quote that really is in the PRD, hashes the PRD, prints the
PRD-section -> Dossier mapping, and files one Dossier per business outcome through `dossier.file`,
then ONE umbrella ticket listing them. It never rejects a PRD for its format.

    python3 intake.py plan --prd <file> --answers <answers.json> [--dir .sdlc]
    python3 intake.py file --plan <.sdlc/intake/<sha12>.json> [--dry-run] [--status]

Limits, stated: the quote check proves a quote EXISTS in the PRD, not that it SUPPORTS the answer
(a human reviews the mapping); the repo guard constrains this adapter, not the model; stdin and
issue-number PRDs and the fast lane are not built. Cost: O(len(PRD) * cites) substring checks, one
`dossier.file` per outcome plus one umbrella; linear at 10x/100x, no lock, nothing grows unbounded
(at most MAX_OUTCOMES Dossiers per PRD, PRD capped at MAX_PRD_BYTES).
"""
import hashlib, importlib.util, json, os, pathlib, sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load(skill, name):
    """Cross-load a sibling skill's script (the same narrow, named exception `dossier.py` uses)."""
    path = _HERE.parent.parent / skill / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


dossier = _load("sigma-dossier", "dossier")
triage = _load("sigma-loop", "triage")

MAX_PRD_BYTES = 262144          #: a PRD past this is split by the human
MAX_OUTCOMES = 12               #: above this the umbrella is the wrong shape
MIN_QUOTE_CHARS = 20            #: a one-character "quote" would match anything
STATUSES = ("silent", "vague", "contradiction")
PLACEHOLDER = "Not stated in the PRD; see %s."
NEXT_STEP = "file and stop"
DECISION = dossier.DECISIONS[NEXT_STEP]
UMBRELLA_LABEL = "epic"
#: Never read as PRD input: code and config, anywhere in the repo; docs-type files elsewhere pass.
DOC_SUFFIXES = (".md", ".txt")
CODE_DIRS = ("skills", "hooks", "tools", "tests", "evals")
READ_FLAGS = ("--repo-read", "--read-code", "--grep")


class Refused(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


def _norm(text):
    return " ".join(str(text).split())


def _write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _find_root(start):
    for d in [pathlib.Path(start).resolve(), *pathlib.Path(start).resolve().parents]:
        if (d / ".git").exists():
            return d
    return None


#: The plugin/repo this script is installed in: a root of its own, even with no .git (CR1-1).
SELF_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent.parent


def _in_code_dir(rel):
    """Top directory is a code dir, compared casefolded (macOS/Windows resolve Skills/ to skills/)."""
    return bool(rel.parts) and rel.parts[0].casefold() in CODE_DIRS


def check_prd_path(arg, cwd, sdlc_dir):
    """AC-4: no repository source file may be the PRD. Roots are every repo the cwd, --dir,
    the PRD itself or this script's install lives in; the allow-list applies under ALL of them
    (stricter, never looser). The .md/.txt allow-list applies to every path, in a repo or not."""
    p = pathlib.Path(arg).resolve()
    if p.is_dir():
        raise Refused("prd-is-directory", "%s is a directory" % arg)
    roots = [r for r in (_find_root(cwd), _find_root(sdlc_dir), _find_root(p.parent),
                         SELF_ROOT.resolve()) if r]
    if not (_find_root(cwd) or _find_root(sdlc_dir)):
        roots.append(pathlib.Path(cwd).resolve())            # no repo at hand: cwd is the base
    for base in roots:
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        if p.suffix.lower() not in DOC_SUFFIXES or _in_code_dir(rel):
            raise Refused("repo-source", "%s is repository source, not a PRD (only .md/.txt outside "
                          "%s are accepted)" % (arg, "/".join(CODE_DIRS)))
    if p.suffix.lower() not in DOC_SUFFIXES:
        raise Refused("prd-not-text-doc", "%s is not a .md/.txt document" % arg)
    return p


def _quotes(value):
    return [value] if isinstance(value, str) else list(value or [])


def _check_quotes(key, quotes, prd_norm):
    if not quotes:
        raise Refused("uncited-answer", "answer %r has no verbatim PRD quote" % key)
    for q in quotes:
        qn = _norm(q)
        if len(qn) < MIN_QUOTE_CHARS:
            raise Refused("quote-too-short", "quote for %r is under %d characters" %
                          (key, MIN_QUOTE_CHARS))
        if qn not in prd_norm:
            raise Refused("quote-not-in-prd", "quote for %r is not in the PRD: %r" % (key, qn[:80]))
    return [_norm(q) for q in quotes]


def _build(outcome, prd_norm):
    name = _norm(outcome.get("name") or "")
    if not name:
        raise Refused("bad-outcome", "an outcome has no name")
    answers, status = outcome.get("answers") or {}, outcome.get("status") or {}
    cites = outcome.get("cites") or {}
    bank = {q["id"]: q["ask"] for q in dossier.QUESTIONS}
    if len([k for k in answers if k.startswith(dossier.FOLLOWUP_PREFIX)]) > dossier.MAX_FOLLOWUPS:
        raise Refused("too-many-followups", "outcome %r: more than %d %s* answers" %
                      (name, dossier.MAX_FOLLOWUPS, dossier.FOLLOWUP_PREFIX))
    for k in answers:
        if not (k in bank or k == "next_step" or k.startswith(
                (dossier.FOLLOWUP_PREFIX, dossier.OPEN_PREFIX))):
            raise Refused("unknown-answer-id", "outcome %r: answer id %r" % (name, k))
    out, qs, cites_out, cited = {}, {}, {}, 0
    ids = list(bank) + [k for k in answers if k not in bank and k != "next_step"]
    for key in ids:
        text = _norm(answers.get(key) or "")
        st = status.get(key)
        if st is not None and st not in STATUSES:
            raise Refused("bad-status", "outcome %r: status %r for %r" % (name, st, key))
        if key.startswith(dossier.FOLLOWUP_PREFIX) and st:
            raise Refused("bad-status", "%r is a follow-up; status applies to bank and open_ ids" % key)
        if not text and not st:
            if key in bank:
                st = "silent"
            else:
                continue
        if st is None and key.startswith(dossier.OPEN_PREFIX):
            st = "vague"                                     # an open_ is never a cited answer
        if st is None:                                       # a real, cited answer
            out[key] = text
            cites_out[key] = _check_quotes(key, _quotes(cites.get(key)), prd_norm)
            cited += 1
            continue
        quotes = []
        if st != "silent":
            quotes = _check_quotes(key, _quotes(cites.get(key)), prd_norm)
            if st == "contradiction" and len(quotes) < 2:
                raise Refused("contradiction-needs-two-quotes",
                              "outcome %r: %r needs both contradicting quotes" % (name, key))
        quoted = " / ".join('"%s"' % q for q in quotes)
        if key in bank:                                      # a gap in the bank: placeholder + open_
            oid = dossier.OPEN_PREFIX + key
            out[key] = PLACEHOLDER % oid
            out[oid] = text or "The PRD is %s on: %s" % (st, bank[key])
            qs[oid] = ("The PRD is silent on: %s" % bank[key] if st == "silent" else
                       "The PRD is %s on %s: %s -- what is intended?" % (st, key, quoted))
        else:                                                # a model-supplied open_ gap
            oid = key
            out[oid] = text or "The PRD is %s here." % st
            qs[oid] = ("The PRD is silent here: %s" % out[oid] if st == "silent" else
                       "The PRD is %s: %s -- what is intended?" % (st, quoted))
        if quotes:
            cites_out[oid] = quotes
    if "title" in out and out["title"].startswith("Not stated in the PRD"):
        out["title"] = name                                  # never the placeholder text
        if status.get("title") == "silent" or not answers.get("title"):
            out.pop("open_title", None)
            qs.pop("open_title", None)
    if cited == 0:
        raise Refused("no-cited-answers", "outcome %r has no real cited answer" % name)
    out["next_step"] = NEXT_STEP
    ordered = {k: out[k] for k in list(bank) + ["next_step"]}
    ordered.update({k: v for k, v in out.items() if k not in ordered})
    questions = [dict(q) for q in dossier.bank()] + [
        {"id": k, "ask": v} for k, v in qs.items()]
    return {"name": name, "section": _norm(outcome.get("section") or ""), "answers": ordered,
            "questions": questions, "cites": cites_out, "cited_count": cited}


def _args(tail, flags, switches=()):
    vals, i = {}, 0
    while i < len(tail):
        a = tail[i]
        if a in switches:
            vals[a] = True
        elif a in flags and i + 1 < len(tail):
            vals[a] = tail[i + 1]
            i += 1
        else:
            raise Refused("usage", "unexpected argument %r" % a)
        i += 1
    return vals


def cmd_plan(tail):
    a = _args(tail, ("--prd", "--answers", "--dir"))
    if "--prd" not in a or "--answers" not in a:
        raise Refused("usage", "plan needs --prd and --answers")
    sdlc = pathlib.Path(a.get("--dir", ".sdlc"))
    prd = check_prd_path(a["--prd"], os.getcwd(), sdlc)
    if not prd.is_file():
        raise Refused("prd-unreadable", "%s is not a readable file" % a["--prd"])
    if prd.stat().st_size > MAX_PRD_BYTES:
        raise Refused("prd-too-large", "PRD is over %d bytes; split it" % MAX_PRD_BYTES)
    raw = prd.read_bytes()
    prd_norm = _norm(raw.decode("utf-8", errors="replace"))
    if not prd_norm:
        raise Refused("prd-empty", "the PRD has no text")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(pathlib.Path(a["--answers"]).read_text(encoding="utf-8"))
        raw_outcomes = payload["outcomes"]
        assert isinstance(raw_outcomes, list)
    except Exception as exc:                                  # noqa: BLE001
        raise Refused("answers-unreadable", "%s: %s" % (a["--answers"], exc))
    if not raw_outcomes:
        raise Refused("no-outcomes", "the answers file has no outcomes")
    if len(raw_outcomes) > MAX_OUTCOMES:
        raise Refused("too-many-outcomes", "%d outcomes; at most %d per PRD" %
                      (len(raw_outcomes), MAX_OUTCOMES))
    outcomes = [_build(o, prd_norm) for o in raw_outcomes]
    names = [o["name"] for o in outcomes]
    if len(set(names)) != len(names):
        raise Refused("duplicate-outcome", "outcome names must be unique")
    try:
        shown = str(prd.relative_to(pathlib.Path.cwd().resolve()))
    except ValueError:
        shown = str(prd)
    plan_path = sdlc / "intake" / (sha[:12] + ".json")
    _write_json(plan_path, {"version": 1, "prd": {"path": shown, "sha256": sha},
                            "outcomes": outcomes})
    lines = ["PRD %s" % shown, "sha256 %s" % sha, "plan %s" % plan_path, "",
             "PRD section -> Dossier title -> answer ids"]
    for o in outcomes:
        lines.append("%s -> %s -> %s" % (o["section"] or "(whole PRD)", o["answers"]["title"],
                                         ", ".join(k for k in o["answers"] if k != "next_step")))
    print("\n".join(lines))
    return 0


def _plan_key(plan_path):
    return hashlib.sha256(pathlib.Path(plan_path).read_bytes()).hexdigest()[:12]


def _load_record(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"dossiers": {}, "umbrella": None}


def _umbrella_body(prd, filed):
    lines = ["## PRD umbrella", "", "- **source** — %s" % dossier._defang(prd["path"]),
             "- **sha256** — %s" % prd["sha256"], "", "### Dossiers", ""]
    lines += ["- #%s %s" % (num, dossier._defang(name)) for name, num in filed.items()]
    return "\n".join(lines)


def cmd_file(tail, run=None):
    a = _args(tail, ("--plan", "--dir"), ("--dry-run", "--status"))
    if "--plan" not in a:
        raise Refused("usage", "file needs --plan")
    plan_path = pathlib.Path(a["--plan"])
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        prd, outcomes = plan["prd"], plan["outcomes"]
    except Exception as exc:                                  # noqa: BLE001
        raise Refused("plan-unreadable", "%s: %s" % (plan_path, exc))
    sdlc = pathlib.Path(a.get("--dir") or plan_path.resolve().parent.parent)
    record_path = plan_path.parent / (_plan_key(plan_path) + ".filed.json")
    record = _load_record(record_path)
    filed = dict(record.get("dossiers") or {})
    if a.get("--status"):
        for name, num in filed.items():
            print("#%s %s" % (num, name))
        print("umbrella %s" % (("#%s" % record["umbrella"]) if record.get("umbrella") else "not filed"))
        return 0
    config = triage._config(str(sdlc))
    dry = bool(a.get("--dry-run"))

    def _src(o):
        s = {"path": prd["path"], "sha256": prd["sha256"], "cites": o.get("cites") or {}}
        if o.get("section"):
            s["section"] = o["section"]
        return s

    for o in outcomes:                                        # validate everything before any write
        res = dossier.file(str(sdlc), config, o["answers"], o["questions"], DECISION,
                           run=run, apply=False, prd_source=_src(o))
        if res["outcome"] == "failed":
            raise Refused("file-failed", "%s: %s" % (o["name"], res["detail"]))
    if dry:
        for o in outcomes:
            print("would file %s%s" % (o["name"], " (already filed)" if o["name"] in filed else ""))
        print("would file the umbrella (label %s)" % UMBRELLA_LABEL)
        return 0
    for o in outcomes:
        if o["name"] in filed:
            continue
        res = dossier.file(str(sdlc), config, o["answers"], o["questions"], DECISION,
                           run=run, apply=True, prd_source=_src(o))
        if res["outcome"] != "filed":
            raise Refused("file-failed", "%s: %s" % (o["name"], res["detail"]))
        filed[o["name"]] = res["number"]
        _write_json(record_path, {"dossiers": filed, "umbrella": record.get("umbrella")})
        print("filed #%s %s" % (res["number"], o["name"]))
    if not record.get("umbrella"):
        source = dossier._resolve_source(str(sdlc), config, run=run)
        title = "PRD intake: %s (%d Dossiers)" % (pathlib.Path(prd["path"]).name, len(outcomes))
        try:
            number = source.create_dependency(title, _umbrella_body(prd, filed), "@me",
                                              labels=[UMBRELLA_LABEL], goal_label=False)
        except Exception as exc:                              # noqa: BLE001
            raise Refused("umbrella-failed", str(exc))
        if number is None:
            raise Refused("umbrella-failed", "gh did not return an issue number")
        record["umbrella"] = str(number)
        _write_json(record_path, {"dossiers": filed, "umbrella": record["umbrella"]})
        print("filed umbrella #%s" % number)
    return 0


_USAGE = ("usage: intake.py plan --prd <file> --answers <answers.json> [--dir .sdlc]\n"
          "       intake.py file --plan <plan.json> [--dry-run] [--status]")


def main(argv, run=None):
    if argv[1:] in (["-h"], ["--help"]):
        print(_USAGE)
        return 0
    try:
        for flag in READ_FLAGS:
            if flag in argv:
                raise Refused("repo-read-flag", "%s is refused: intake never reads repository code"
                              % flag)
        if len(argv) < 2 or argv[1] not in ("plan", "file"):
            raise Refused("usage", _USAGE)
        return cmd_plan(argv[2:]) if argv[1] == "plan" else cmd_file(argv[2:], run=run)
    except Refused as r:
        print("intake.py: REFUSED [%s]: %s" % (r.code, r.detail), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_load("sigma-loop", "timing_store").timed_main(main, sys.argv, "intake"))
