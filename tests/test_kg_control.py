"""The public KG launch-control gesture is deterministic and proves its safety gates."""
import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "kg_control.py"


def test_public_knowledge_graph_control_builds_and_exercises_opt_in_boundaries(tmp_path):
    result = subprocess.run([sys.executable, str(TOOL), "--workdir", str(tmp_path)],
                            text=True, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout)
    assert report["schema"] == "sigma.kg-control/1"
    assert report["checks"] == {"refresh": True, "status": True, "doctor": True,
                                "disabled": True, "missing_builder": True}
    assert report["measurement"]["corpus_documents"] == 1
    assert report["measurement"]["live_backend_cost"] == "unavailable: no backend selected"
