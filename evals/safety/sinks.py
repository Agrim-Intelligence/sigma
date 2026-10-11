"""Sink probes for evals/safety/check.py (#1050, slice 2 of #1048, story #806, AC-3 and AC-4).

Every TABLE entry is planted, built at run time, through each sink that must keep it out of its
output: the action log, the ledger, the comment path (`comment_watch.tick` into a ledger note), the
review-block reason that becomes a public PR comment, and the real-repo commit refusal. The sinks are
the four the story names plus the review reason (D-1); the mirror and the research hook are not
probed (D-8), so the claim is "four named plus one", not "every sink".

Each plant is made twice, at the start of the text and straddling the sink's smallest cap (D-5), and
the sink's own output is read back. A leak is the raw needle, its first eight or its last ten
characters present; a missing `[REDACTED` placeholder is a finding too. The commit probe plants the
value on a staged line of a scratch git repository and runs the real `work.commit`: the outcome must
be the entry's pinned one (`refused`, `accepted`, or `refused-by:<rule>`), the refusal text must not
echo the value, and HEAD must move only when accepted.

Limits, stated. `work.post_review` is not run (it needs generation-bound review evidence, which
tests/test_work.py covers); its probe calls `work.scrub` and pins that `post_review` applies it to the
reason. The probes find a leak in a sink; they cannot say a sink is missing from the list.

Scratch only: one temporary directory and one scratch repository, removed on exit; nothing under the
real `.sdlc`. Cost is O(entries x sinks); the git repository is created once.
"""
import importlib.util
import inspect
import json
import pathlib
import subprocess
import tempfile


class SinkRefusal(Exception):
    """A sink module could not be loaded or probed: the caller turns it into exit 2."""


def _load(scripts, name):
    path = scripts / (name + ".py")
    try:
        spec = importlib.util.spec_from_file_location("_sigma_sink_" + name, str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as exc:
        raise SinkRefusal("%s.py failed to load (%s)" % (name, type(exc).__name__))
    return mod


def _sdlc(root, config):
    d = pathlib.Path(root) / ".sdlc"
    (d / "state").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(config))
    return d


def _flat(text):
    return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


class Sinks:
    """Loads the sink modules from one scripts directory and probes them with a text each."""

    CAPS = {"actionlog": 500, "ledger": 200, "comment": 160}

    def __init__(self, scripts, tmp):
        self.tmp, self.n = pathlib.Path(tmp), 0
        self.scripts = pathlib.Path(scripts)
        self.actionlog = _load(self.scripts, "actionlog")
        self.ledger = _load(self.scripts, "ledger")
        self.comment_watch = _load(self.scripts, "comment_watch")
        self.work = _load(self.scripts, "work")
        self.repo = None

    def _fresh(self, config):
        self.n += 1
        return _sdlc(self.tmp / ("s%d" % self.n), config)

    def probe_actionlog(self, text):
        d = self._fresh({"action_log": {"enabled": True}})
        self.actionlog.append(str(d), "99", "note", "agent", text=_flat(text))
        return "".join(p.read_text() for p in (d / "state" / "log").glob("*"))

    def probe_ledger(self, text):
        d = self._fresh({"ledger": {"enabled": True, "actor": "amy"}})
        self.ledger.append(str(d), json.loads((d / "config.json").read_text()), "note", "99", why=text)
        return json.dumps(self.ledger.read_all(str(d)))

    def probe_comment(self, text):
        cfg = {"ledger": {"enabled": True, "actor": "amy"}, "comment_watch": {"enabled": True},
               "discovery": {"source": "github"}}
        d = self._fresh(cfg)
        self.ledger.entries_dir(d).mkdir(parents=True, exist_ok=True)
        self.ledger.entry_file(d, "amy").write_text(json.dumps(
            {"id": "amy:1", "ts": self.ledger._stamp(), "actor": "amy", "kind": "claimed", "goal": "50"}) + "\n")
        comment = {"id": "IC_1", "author": "mallory", "body": text, "created_at": "2026-01-01T00:00:00Z",
                   "association": ""}
        # the real tick runs; only the network read is replaced
        self.comment_watch.sources.fetch_comments = lambda config, goal, run=None, limit=0: [comment]
        self.comment_watch.tick(str(d), config=cfg)
        return json.dumps(self.ledger.read_all(str(d)))

    def probe_review_reason(self, text):
        src = inspect.getsource(self.work.post_review)
        if "reason = scrub(" not in src:
            return "post_review no longer scrubs the reason before the public comment"
        return self.work.scrub(text)

    def commit(self, text):
        """-> (result, head_moved): the real `work.commit` over one staged line in the scratch repo."""
        if self.repo is None:
            self.repo = self._fresh({"work": {"enabled": True}}).parent
            self._git("init", "-q")
            for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
                self._git("config", k, v)
            (self.repo / ".gitignore").write_text(".sdlc/\n")
            (self.repo / "a.py").write_text("x = 1\n")
            self._git("add", "--", ".gitignore", "a.py")
            self._git("commit", "-q", "-m", "base")
            state = _load(self.scripts, "state")
            state.start_run(str(self.repo / ".sdlc"))
            self.work._save(str(self.repo / ".sdlc"), "0001-x.md", {
                "worktree": str(self.repo), "branch": "sdlc/0001-x", "base": "main", "remote": "origin", "pr": ""})
        before = self._git("rev-parse", "HEAD")
        (self.repo / "s.py").write_text(text + "\n")
        out = self.work.commit(str(self.repo / ".sdlc"), {"work": {"enabled": True}}, "0001-x.md",
                               message="feat: plant")
        return out, self._git("rev-parse", "HEAD") != before

    def _git(self, *args):
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return done.stdout.strip()


def _plantings(value, needle, cap):
    """The texts planted per sink, each with the characters of the needle that must not survive.

    `start`: the value first. `straddle`: the value placed so a cut at `cap` falls inside the needle
    (all but its last six characters before the cut, fewer for a needle longer than the cap), which
    is what a cut-before-scrub leaves a fragment of. Fragments checked: the needle, its last ten
    characters, and the eight just before the cut, so a half-redacted value is seen."""
    flat = _flat(needle)
    keep = min(len(flat) - 6, cap - 1)
    window = flat[keep - 8:keep]
    pre = value[:value.index(needle)] if needle in value else ""
    frags = (flat, flat[-10:], window)
    return (("start", value, frags),
            ("straddle", "x" * max(cap - len(pre) - keep - 1, 1) + " " + value, frags))


def _leaks(out, frags):
    return [w for w, frag in zip(("value", "tail", "fragment"), frags) if frag and frag in out]


def probe_all(scripts, table, live, generate, find):
    """-> findings for every (entry, sink, planting); `find(kind, name, digest, detail)` formats one."""
    by_key = {(n, h): rx for n, h, rx in live}
    out = []
    with tempfile.TemporaryDirectory(prefix="sigma-safety-") as tmp:
        sinks = Sinks(scripts, tmp)
        for row in table:
            name, digest, _gen, expect, span = row
            value = generate(row)
            rx = by_key.get((name, digest))
            m = rx.search(value) if rx is not None else None
            # a shape missing from the live list is still planted: the whole value stands in for the needle
            needle = m.group(span) if m else value
            for sink, probe in (("actionlog", sinks.probe_actionlog), ("ledger", sinks.probe_ledger),
                                ("comment", sinks.probe_comment), ("review-reason", sinks.probe_review_reason)):
                for where, text, frags in _plantings(value, needle, sinks.CAPS.get(sink, 160)):
                    got = probe(text)
                    bad = _leaks(got, frags)
                    if bad:
                        out.append(find("sink-leak", name, digest, "%s %s: %s present" % (sink, where, "+".join(bad))))
                    elif where == "start" and "[REDACTED" not in got:   # past a cap the placeholder may be cut off
                        out.append(find("sink-no-placeholder", name, digest, "%s %s: no typed placeholder" % (sink, where)))
            refusal, moved = sinks.commit(value)
            refused = str(refusal).startswith("REFUSED")
            rules = {expect} if expect == "refused" else {expect[11:]} if expect.startswith("refused-by:") else set()
            if expect == "accepted":
                if refused or not moved:
                    out.append(find("commit-sink", name, digest, "expected accepted, got a refusal or no commit"))
            else:
                if not refused or moved or not any("%s at s.py:" % r in refusal for r in (rules if expect != "refused" else {name})):
                    out.append(find("commit-sink", name, digest, "expected %s: refused=%s moved=%s" % (expect, refused, moved)))
                if _flat(needle) in refusal or _flat(needle)[-10:] in refusal:
                    out.append(find("commit-echo", name, digest, "refusal text echoes the value"))
    return out
