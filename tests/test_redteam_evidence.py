"""The D10 red-team evidence (redteam-7997e29e4270) is well formed and carries no host path or token."""
import json
import re
from pathlib import Path

EV = Path(__file__).resolve().parent.parent / "docs/launch/evidence"
HOST = re.compile(r"/(?:Users|home|private|tmp|var)/\w|~/|\.sigma-ops|[A-Z]:\\\\|gh[pous]_[A-Za-z0-9]{20}")


def test_redteam_evidence_is_well_formed_and_clean():
    j = EV / "redteam-7997e29e4270.json"
    d = json.loads(j.read_text())
    assert d["schema"] == "redteam/v1" and d["commit"] == "7997e29e4270"
    assert d["scoring"].startswith("no dimension is scored")
    assert all(f["label"] in ("launch:blocker", "launch:next") for f in d["findings"])
    assert d["owner_run_injection_drill"]["status"].startswith("NOT RUN")
    for p in (j, EV / "redteam-7997e29e4270.md"):
        assert not HOST.search(p.read_text()), p.name
