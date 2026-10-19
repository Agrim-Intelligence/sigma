#!/usr/bin/env python3
"""chain_checkpoint.py -- is a checkpoint met? (#1904)

A checkpoint is an open issue carrying the reserved label `sdlc:checkpoint` (key `checkpoint_label` under
`discovery.github`). It stands for "that unit's code is on the integration branch", which no goal can do and
no person works on. It is never picked and never armed. `Blocked by` on it is an ordinary edge.

It is met in exactly two ways: a person closes the issue (the ordinary closed-blocker rule, nothing here), or
the unit it names has landed. The unit is the issue's own unit (label and body, as `features.read` reads it).
`status` answers the second with a LOCAL read only: the unit's branch from the registry's naming rule
(`feature/<unit>`), its tip from the remote-tracking ref (else the local branch), and git's own ancestry test
against `<remote>/<work.base>`. It never fetches and never calls the network, so it states what this checkout
last saw; a stale checkout reads `waiting`, never `met`.

Answers, a closed set:
  met      the unit's tip is contained in the integration branch.
  waiting  the unit is not contained there yet, or the checkpoint names no unit (only a person closes it).
  unknown  the unit is not in the registry, no integration branch is configured, or git could not answer.
           Every caller treats `unknown` as not met.

Known gap, stated rather than skipped: a unit landed by squash is not an ancestor, and a unit whose branch
was deleted after landing has no tip, so both read `waiting` or `unknown` until a person closes the
checkpoint. The pull-request half of `feature_landed.landed` needs the network and is not used here.
Also not changed: the loop's own pick gate still reads an OPEN blocker as open, so a `met` checkpoint whose
issue is still open does not by itself release what waits on it; the sheet and the stop reason say so and name
the one step (close the issue).
"""
import importlib.util
import pathlib

_HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_LABEL = "sdlc:checkpoint"
MET, WAITING, UNKNOWN = "met", "waiting", "unknown"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def label(config):
    gh = ((config or {}).get("discovery") or {}).get("github")
    v = gh.get("checkpoint_label") if isinstance(gh, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else DEFAULT_LABEL


def _row(unit, state, why):
    return {"unit": unit, "state": state, "why": why}


def status(sdlc_dir, config, unit, run=None):
    """-> {"unit", "state": met|waiting|unknown, "why"}. Local reads only; never raises. `run(argv, cwd)` ->
    (rc, out, err) is injectable; the default is `feature_landed.make_runner()`."""
    try:
        return _status(sdlc_dir, config, unit, run)
    except Exception as exc:                      # noqa: BLE001 - a status answers, it does not raise
        return _row(unit, UNKNOWN, "the check failed (%s)" % exc.__class__.__name__)


def _status(sdlc_dir, config, unit, run):
    if not unit:
        return _row(None, WAITING, "names no unit; only a person closing it meets it")
    registry, rebase = _load("feature_registry"), _load("feature_rebase")
    landed, features = _load("feature_landed"), _load("features")
    if registry.resolve_any_unit(sdlc_dir, unit) is None:
        return _row(unit, UNKNOWN, "the unit is not in the registry")
    base = rebase.base_of(config)
    if not base:
        return _row(unit, UNKNOWN, "no integration branch is configured (work.base)")
    remote, branch = rebase._remote(config), features.BRANCH_PREFIX + unit
    cwd = str(pathlib.Path(sdlc_dir).resolve().parent)
    run = run or landed.make_runner()
    tip = None
    for ref in ("refs/remotes/%s/%s" % (remote, branch), "refs/heads/%s" % branch):
        rc, out, _err = landed._git(run, cwd, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        if rc == 0 and landed._SHA_RE.match(out.strip()):
            tip = out.strip()
            break
    if tip is None:
        return _row(unit, UNKNOWN, "the unit branch is not available in this checkout")
    v = landed.landed(tip, "%s/%s" % (remote, base), branch, None, run, cwd, base_name=base)
    if v.verdict == landed.LANDED:
        return _row(unit, MET, "the unit tip is contained in %s/%s" % (remote, base))
    if v.base_sha:
        return _row(unit, WAITING, "the unit tip is not contained in %s/%s" % (remote, base))
    return _row(unit, UNKNOWN, "the integration branch %s/%s cannot be resolved here" % (remote, base))
