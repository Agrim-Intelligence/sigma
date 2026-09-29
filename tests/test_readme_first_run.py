"""The README's first-run path names only things that exist (#231).

A new user copies the README literally. So every `/agrim-*` skill it names must be a shipped skill,
every repo script path it names must be a tracked file, every `/agrim-init --flag` must be a flag
the scaffolder accepts, and the version floor it states must be the one doctor enforces -- which
must not sit above the version the plugin actually ships.

Each extractor is a pure function over README text, so the control (planting a bad name into a
copy of the text) runs the same code the real check does.
"""
import importlib.util
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

_SPAN = re.compile(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", re.S)
_SKILL = re.compile(r"(?<![\w/.-])/(agrim-[a-z0-9-]+)")
_REPO_PY = re.compile(r"(?:^|[\s\"'(/])((?:skills|hooks|tools|evals)/[\w./-]+?\.py)\b")
_BARE_PY = re.compile(r"(?<![\w/.-])([A-Za-z_][\w-]*\.py)\b")
_INIT_FLAG = re.compile(r"/agrim-init((?:\s+--[a-z][\w-]*)+)")


def _code(text):
    """Every inline code span and fenced block body -- the text a reader copies."""
    return [a or b for a, b in _SPAN.findall(text)]


def missing_skills(text, root=ROOT):
    names = {m for span in _code(text) for m in _SKILL.findall(span)}
    return sorted(n for n in names if not (root / "skills" / n / "SKILL.md").is_file())


def missing_scripts(text, root=ROOT):
    shipped = {p.name for p in root.rglob("*.py")
               if not {".git", ".sdlc", "node_modules"} & set(p.relative_to(root).parts)}
    bad = set()
    for span in _code(text):
        for path in _REPO_PY.findall(span):
            if not (root / path).is_file():
                bad.add(path)
        for name in _BARE_PY.findall(span):
            if name not in shipped:
                bad.add(name)
    return sorted(bad)


def unknown_init_flags(text, root=ROOT):
    usage = (root / "skills/agrim-init/scripts/sdlc_init.py").read_text(encoding="utf-8")
    known = set(re.findall(r"\[(--[a-z]+)\]", re.search(r'USAGE = "([^"]+)"', usage).group(1)))
    # #236: `/agrim-init` runs init_flow.py, the one entry point; its USAGE lists its own flags.
    flow = (root / "skills/agrim-init/scripts/init_flow.py").read_text(encoding="utf-8")
    known |= set(re.findall(r"(?m)^\s+(--[a-z][\w-]*)", re.search(r'USAGE = """(.+?)"""', flow, re.S).group(1)))
    known |= set(re.findall(r"(?<=\s)(--[a-z][\w-]*)", re.search(r'USAGE = """(.+?)"""', flow, re.S).group(1)))
    used = {f for span in _code(text) for group in _INIT_FLAG.findall(span)
            for f in group.split()}
    return sorted(used - known)


def _doctor():
    path = ROOT / "skills/agrim-doctor/scripts/doctor.py"
    spec = importlib.util.spec_from_file_location("doctor_231", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plugin_version():
    raw = json.loads((ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    return tuple(int(x) for x in raw.split("."))


# ------------------------------------------------------------------------------------ the checks

def test_every_skill_the_readme_names_ships():
    assert missing_skills(README.read_text(encoding="utf-8")) == []


def test_every_script_the_readme_names_ships():
    assert missing_scripts(README.read_text(encoding="utf-8")) == []


def test_every_agrim_init_flag_the_readme_names_is_accepted():
    assert unknown_init_flags(README.read_text(encoding="utf-8")) == []


def test_the_floor_does_not_sit_above_the_shipped_version_and_the_readme_states_it():
    floor = _doctor()._agents_floor()
    assert floor is not None
    assert floor <= _plugin_version(), (floor, _plugin_version())
    text = README.read_text(encoding="utf-8")
    assert f"older than {'.'.join(map(str, floor))}" in text
    detail = (ROOT / "docs/agent-rules-detail.md").read_text(encoding="utf-8")
    assert f"older than {'.'.join(map(str, floor))}" in detail


def test_no_shipped_file_names_the_previous_products_label_release():
    """The control the issue names: `grep -n "1\\.3\\.6"` over shipped files returns nothing."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    hits = []
    for rel in tracked:
        if rel.startswith(".sdlc/"):
            continue
        try:
            body = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        if re.search(r"(?<![\d.])1\.3\.6(?![\d])", body):
            hits.append(rel)
    assert hits == []


def test_quickstart_covers_every_host_with_one_placeholder():
    text = README.read_text(encoding="utf-8")
    quick = text.split("## Quickstart", 1)[1].split("\n## ", 1)[0]
    for host in ("Claude Code", "Codex", "Cursor"):
        assert f"### {host}" in quick, host
    assert "claude plugin install sigma@sigma" in quick
    assert "codex plugin add sigma@sigma" in quick
    assert "init_flow.py . --cursor" in quick      # #236: the one entry point, on every host
    # the placeholder is defined exactly once, and nothing else stands in for the URL
    assert quick.count("`<SIGMA_REPO>` is") == 1
    assert "<git-url-or-local-path>" not in text


# ---------------------------------------------------------------------------------- the controls

def test_control_a_planted_skill_name_is_caught():
    text = README.read_text(encoding="utf-8") + "\nRun `/agrim-nonexistent` next.\n"
    assert missing_skills(text) == ["agrim-nonexistent"]


def test_control_a_planted_script_path_is_caught():
    text = (README.read_text(encoding="utf-8")
            + "\n```\npython3 skills/agrim-loop/scripts/bogus_script.py .sdlc\n```\n"
            + "and `nothere_helper.py`.\n")
    assert missing_scripts(text) == ["nothere_helper.py", "skills/agrim-loop/scripts/bogus_script.py"]


def test_control_a_planted_init_flag_is_caught():
    # (#236: `--board` became a real flag of the one entry point; the plant is a flag nothing has)
    text = README.read_text(encoding="utf-8") + "\n`/agrim-init --boardroom`\n"
    assert unknown_init_flags(text) == ["--boardroom"]
