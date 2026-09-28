# SPDX-License-Identifier: MIT
"""The setup wizard's two actions that actually change something (issue #1560) -- kept apart
from setup_wizard.py's pure classifier so the classifier's own tests never shell out or touch a
real filesystem beyond a tmp_path fixture."""
import pathlib
import subprocess


def _load_sdlc_init():
    import importlib.util
    path = pathlib.Path(__file__).resolve().parent / "sdlc_init.py"
    spec = importlib.util.spec_from_file_location("sdlc_init", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run_scaffold(repo_root):
    """Reuses sdlc_init.scaffold() verbatim -- the exact function `/agrim-init` itself calls, so
    the wizard's bootstrap and the manual command produce byte-identical results. Never raises:
    a scaffold failure is reported, not thrown, so the wizard conversation can offer retry/skip
    rather than crashing the hook that triggered it."""
    try:
        created, skipped = _load_sdlc_init().scaffold(repo_root)
        return {"ok": True, "detail": f"created {len(created)} file(s), "
                                       f"{len(skipped)} already present"}
    except Exception as exc:                # noqa: BLE001 - report, never crash the wizard
        return {"ok": False, "detail": str(exc)}


def _real_run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def run_graphify_install(run=None):
    """`python3 -m pip install graphifyy` with consent already given by the caller -- this function
    performs the install, it does not ask. PEP 668 (externally-managed environments:
    Debian/Ubuntu/Homebrew system pythons) is the one failure mode specifically named, because it is
    common enough on real developer machines to deserve a real answer instead of a raw pip traceback
    -- the same reasoning this repo's own verify.command docstring already documents for duckdb's
    install story (.sdlc/config.json's own verify block). Never raises: exceptions from the run
    callable are caught and reported.

    `python3 -m pip`, NOT a bare `pip`. A bare `pip` is whichever one PATH happens to resolve
    first, which on a machine with a pyenv/conda/venv layout is routinely NOT the interpreter that
    will later try to `import` what it installed -- the install "succeeds" and the feature stays
    silently dark, which is precisely the class of failure this wizard exists to prevent. The
    module form installs into the interpreter that is running, by construction. doctor.py's own
    `graphify installed` check is what verifies the result either way."""
    run = run or _real_run
    try:
        result = run(["python3", "-m", "pip", "install", "graphifyy"])
    except Exception as exc:                # noqa: BLE001 - report, never crash the wizard
        return {"ok": False, "detail": f"could not run pip: {exc}"}
    if result.returncode == 0:
        return {"ok": True, "detail": "graphifyy installed"}
    stderr = result.stderr or ""
    if "externally-managed-environment" in stderr:
        return {"ok": False, "detail": (
            "this Python won't accept a plain pip install (an 'externally-managed' system "
            "Python -- common on Debian/Ubuntu/Homebrew). Either add --break-system-packages "
            "to the pip command, or install inside a virtual environment instead."
        )}
    return {"ok": False, "detail": stderr.strip() or "pip install failed with no output"}
