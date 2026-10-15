"""Resource preflight contract; native gestures are distinct from patched source fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'tools' / 'parity' / 'resources.py'
GESTURE = ['--reserve-fraction', '0.25', '--record-bytes', '256', '--output-dir', '.']


def test_documented_gesture_yields_native_receipt_without_files(tmp_path):
    """Run the real documented argv on this OS; no fixture/native-source override is used."""
    result = subprocess.run([sys.executable, str(CLI), *GESTURE], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['schema'] == 'parity-resource-preflight/v1'
    assert receipt['status'] == 'ok'
    assert receipt['platform'] == sys.platform
    assert receipt['sources']
    assert int(receipt['limits']['output_bytes']) > 0
    assert list(tmp_path.iterdir()) == []
    assert not (CLI.parent / '__pycache__').exists()
