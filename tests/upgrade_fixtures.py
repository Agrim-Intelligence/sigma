"""Upgrade-compat fixtures (#2002, FR-31/35/97): a synthetic 1.0.x project, built with the real CLIs.

Helper module, not a test file. Everything here is generic (no private names). The goals are built by
the same gestures the docs give (`phase_report.py start|end`, `work.py record-plan-review`), reusing
the helpers of tests/test_phase_record_gate.py; nothing is hand-written except the old-shaped log."""
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import test_phase_record_gate as g  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMPL = ROOT / "skills" / "sigma-init" / "templates" / "config.json.tmpl"
GOAL, STEM = g.GOAL, "0001-x"
SDLC = ".sdlc"

#: keys a 1.0.x repo adopted before these shipped -> dropped from the synthetic config, so "missing
#: new key" is real. (`action_log` is read as OFF when absent today; FR-96 reads it ON for lean later.)
DROPPED = ("action_log", "_action_log")
LEDGER_ON = {"ledger": {"enabled": True, "actor": "dana"}, "journal": {"enabled": True}}
LEDGER_OFF = {"ledger": {"enabled": False, "actor": "dana"}}


def omega_shaped_config():
    """The shipped template, parsed, + unknown generic keys, - keys that arrived later. Never a copy of a real repo."""
    cfg = json.loads(TMPL.read_text())
    for k in DROPPED:
        cfg.pop(k, None)
    for k in ("phase_record", "_phase_record"):
        cfg.get("gates", {}).pop(k, None)
    cfg["x_custom_key"] = {"owner": "dana", "n": 3}
    cfg["team_notes"] = "free text kept by the adopter"
    cfg.setdefault("gates", {})["x_custom_gate"] = {"enabled": False}
    return cfg


def gates_on(cfg, ledger=False):
    """In-memory overlay: the FR-97 composite (verify.enforce, gates.plan_review, gates.phase_record,
    action_log all on) + the ledger on or off. Pure; never written."""
    c = copy.deepcopy(cfg)
    c.setdefault("verify", {}).update({"command": "true", "enforce": True})
    gates = c.setdefault("gates", {})
    gates["plan_review"] = {"enabled": True}
    gates["phase_record"] = {"enabled": True}
    c["action_log"] = {"enabled": True}
    c.update(copy.deepcopy(LEDGER_ON if ledger else LEDGER_OFF))
    return c


def write_config(p, cfg):
    (p / SDLC / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")


def _build(tmp_path, upto, ledger, final_cfg):
    p = g._project(tmp_path)
    write_config(p, gates_on(final_cfg, ledger))        # the writer's config while the history is made
    g.phase(p, "research", "a1")
    g.phase(p, "plan", "a2")
    g.phase(p, "plan_review", "rev1")
    g.plan_review(p)
    if upto == "mid_implement":
        assert g.run(p, "phase_report.py", "start", SDLC, GOAL, "implement", "--model", "sonnet").returncode == 0
    write_config(p, final_cfg)                           # ...then the project reads as the 1.0.x repo
    return p


def plan_review_goal(tmp_path, ledger=False, final_cfg=None):
    """A goal at plan-review: research and plan done, an approving verdict bound to the plan's sha256."""
    return _build(tmp_path, "plan_review", ledger, final_cfg or omega_shaped_config())


def mid_implement_goal(tmp_path, ledger=False, final_cfg=None):
    """A goal mid-implement: the above plus an implement `start` with no `end`."""
    return _build(tmp_path, "mid_implement", ledger, final_cfg or omega_shaped_config())


def old_action_log(p):
    """A 1.0.x-shaped log: only {ts, kind, actor, thread, phase[, state]} -- no field added since."""
    rows = [{"ts": f"2026-01-0{i}T00:00:00.000Z", "kind": "phase", "actor": "loop", "thread": "main",
             "phase": ph, "state": st} for i, (ph, st) in enumerate(
                 [("research", "start"), ("research", "end")], 1)]
    f = p / SDLC / "state" / "log" / f"{STEM}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return f


def tree(p):
    """Every path under .sdlc (relative, sorted) -- for 'no file written' assertions."""
    base = p / SDLC
    return sorted(str(x.relative_to(base)) for x in base.rglob("*"))
