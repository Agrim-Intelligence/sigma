#!/usr/bin/env python3
"""Run Sigma's opt-in knowledge-graph path without selecting an LLM backend.

Usage: ``python3 tools/kg_control.py``.  The control builds a three-document
fixture with a local executable, then uses the shipped ``kg.py`` and
``doctor.py`` CLIs.  It also proves the two safety boundaries: disabled graphs
do not spawn a builder, and a missing builder leaves a durable failure record.

The emitted JSON records corpus size and elapsed time.  It deliberately does
not estimate a live graphify extraction cost: selecting a backend is an
operator decision and this control never makes one.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
KG = ROOT / "skills" / "agrim-kg" / "scripts" / "kg.py"
DOCTOR = ROOT / "skills" / "agrim-doctor" / "scripts" / "doctor.py"
BUILDER = "sigma-kg-control-builder"


def _write_builder(directory):
    directory.mkdir(parents=True, exist_ok=True)
    builder = directory / BUILDER
    builder.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "if '--version' in sys.argv:\n"
        " print('sigma-kg-control-builder 1.0'); raise SystemExit(0)\n"
        "if len(sys.argv) < 2 or sys.argv[1] != 'extract':\n"
        " print('unsupported invocation', file=sys.stderr); raise SystemExit(2)\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
        f"graph = out / '{BUILDER}-out' / 'graph.json'\n"
        "graph.parent.mkdir(parents=True, exist_ok=True)\n"
        "graph.write_text(json.dumps({'source_location': 'analysis/seed.md'}))\n"
    )
    builder.chmod(0o755)


def _repo(base, *, enabled=True):
    root = base / ("enabled" if enabled else "disabled")
    sdlc = root / ".sdlc"
    (sdlc / "knowledge" / "analysis").mkdir(parents=True, exist_ok=True)
    (sdlc / "knowledge" / "analysis" / "seed.md").write_text("# Seed\ncontrol corpus\n")
    (sdlc / "config.json").write_text(json.dumps({"knowledge_graph": {
        "enabled": enabled, "scope": "research", "builder": BUILDER, "auto_refresh": True,
    }}))
    return root, sdlc


def _run(argv, *, env):
    return subprocess.run(argv, text=True, capture_output=True, env=env, timeout=60)


def run(workdir):
    workdir = pathlib.Path(workdir)
    bindir = workdir / "bin"; _write_builder(bindir)
    env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    started = time.monotonic()
    root, sdlc = _repo(workdir, enabled=True)
    refresh = _run([sys.executable, str(KG), "refresh", str(sdlc), str(root)], env=env)
    status = _run([sys.executable, str(KG), "status", str(sdlc)], env=env)
    doctor = _run([sys.executable, str(DOCTOR), "check", str(sdlc)], env=env)
    graph = root / f"{BUILDER}-out" / "graph.json"

    _, disabled_sdlc = _repo(workdir, enabled=False)
    disabled = _run([sys.executable, str(KG), "refresh", str(disabled_sdlc), str(workdir / "disabled")], env=env)

    missing_root = workdir / "missing"; missing_sdlc = missing_root / ".sdlc"
    (missing_sdlc / "knowledge" / "analysis").mkdir(parents=True, exist_ok=True)
    (missing_sdlc / "knowledge" / "analysis" / "seed.md").write_text("# Seed\n")
    (missing_sdlc / "config.json").write_text(json.dumps({"knowledge_graph": {
        "enabled": True, "scope": "research", "builder": "missing-kg-builder", "auto_refresh": True,
    }}))
    missing = _run([sys.executable, str(KG), "refresh", str(missing_sdlc), str(missing_root)], env=env)
    record = missing_sdlc / "state" / "kg-refresh.json"
    docs = list((sdlc / "knowledge").rglob("*.md"))
    checks = {
        "refresh": refresh.returncode == 0 and "graph refreshed" in refresh.stderr,
        "status": status.returncode == 0 and "graph: built" in status.stdout and graph.is_file(),
        "doctor": doctor.returncode == 0 and f"[OK ] {BUILDER} installed" in doctor.stdout,
        "disabled": disabled.returncode == 0 and "disabled" in disabled.stdout and not (workdir / "disabled" / f"{BUILDER}-out").exists(),
        "missing_builder": missing.returncode == 1 and record.is_file() and "could not run" in missing.stderr,
    }
    return {
        "schema": "sigma.kg-control/1", "ok": all(checks.values()), "checks": checks,
        "measurement": {"corpus_documents": len(docs), "corpus_bytes": sum(p.stat().st_size for p in docs),
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "live_backend_cost": "unavailable: no backend selected"},
    }


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=pathlib.Path)
    parser.add_argument("--json", type=pathlib.Path)
    args = parser.parse_args(argv[1:])
    if args.workdir:
        args.workdir.mkdir(parents=True, exist_ok=True)
        result = run(args.workdir)
    else:
        with tempfile.TemporaryDirectory(prefix="sigma-kg-control-") as temp:
            result = run(temp)
    text = json.dumps(result, sort_keys=True)
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
