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
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

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


# ================================================================ #277: the README is TRUE and executable
#
# Each check below is a pure function over text (or over the shipped code it derives from), so its
# control plants the defect into a copy of the text and runs the very same function.

_ALL_SHIPPED_TEXT = (".md", ".mdc", ".tmpl", ".txt", ".sh")


def _plugin_ids(root=ROOT):
    mp = json.loads((root / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    return mp["name"], f"{mp['plugins'][0]['name']}@{mp['name']}"


def missing_prose_skills(text, root=ROOT):
    """Every `/agrim-*` the README names ANYWHERE -- prose, headings, tables, code -- ships."""
    return sorted(n for n in set(_SKILL.findall(text))
                  if not (root / "skills" / n / "SKILL.md").is_file())


#: `claude plugin` / `codex plugin` verbs, read from the CLIs' own help (read-only):
#: claude 2.1.284 `claude plugin --help` / `claude plugin marketplace --help`; codex-cli
#: 0.154.0-alpha.6.2 `codex plugin --help` / `codex plugin marketplace --help`. `None` = a leaf verb.
PLUGIN_GRAMMAR = {
    "claude": {"marketplace": {"add", "list", "remove", "rm", "update"},
               **dict.fromkeys(("install", "i", "update", "uninstall", "remove", "enable", "disable",
                                "list", "validate", "details", "eval", "init", "new", "prune",
                                "autoremove", "tag"))},
    "codex": {"marketplace": {"add", "list", "upgrade", "remove"},
              **dict.fromkeys(("add", "list", "remove"))},
}
_PLUGIN_CMD = re.compile(r"(?m)(?<![\w/-])(claude|codex) plugins? ([^`\n#]*)")


def plugin_cli_errors(text, root=ROOT):
    """Every `claude plugin ...` / `codex plugin ...` command in a code span or fence: a verb the CLI
    has, and any `<plugin>@<marketplace>` id / marketplace name the one `.claude-plugin/` declares."""
    market, plugin_id = _plugin_ids(root)
    bad = []
    for span in _code(text):
        for host, rest in _PLUGIN_CMD.findall(span):
            toks = rest.split()
            if toks[:1] in (["..."], ["\u2026"]):                # prose ellipsis, not a command
                continue
            grammar = PLUGIN_GRAMMAR[host]
            if not toks or toks[0] not in grammar:
                bad.append(f"{host} plugin {rest.strip()}: unknown verb")
                continue
            args = toks[1:]
            if grammar[toks[0]] is not None:                     # marketplace <sub> ...
                if not args or args[0] not in grammar[toks[0]]:
                    bad.append(f"{host} plugin {rest.strip()}: unknown marketplace verb")
                    continue
                if args[0] in ("update", "remove", "rm", "upgrade") and args[1:2] and \
                        not args[1].startswith(("-", "<")) and args[1] != market:
                    bad.append(f"{host} plugin {rest.strip()}: marketplace is `{market}`")
                continue
            for a in args:
                if "@" in a and not a.startswith("<") and a != plugin_id:
                    bad.append(f"{host} plugin {rest.strip()}: the id is `{plugin_id}`")
    return sorted(set(bad))


def _subsection(text, heading_start):
    m = re.search(r"(?m)^### " + re.escape(heading_start) + r".*$", text)
    assert m, heading_start
    rest = text[m.end():]
    end = re.search(r"(?m)^##+ ", rest)
    return rest[:end.start()] if end else rest


INIT_SUBSECTIONS = ("What `/agrim-init` will ask you", "If `/agrim-init` says you lack access")


def uncopyable_gestures(text):
    """A `python3 <script>` gesture the user runs against THEIR repository must be copyable from its
    root: the script is `<installed-sigma>/skills/...` (or hooks/), never plugin-relative and never
    another placeholder. (`tools/` and `evals/` gestures are Sigma-development commands, run from a
    Sigma checkout.)"""
    bad = []
    for span in _code(text):
        for line in span.splitlines():
            for m in re.finditer(r"python3?\s+(\S+\.py)", line):
                script = m.group(1)
                if re.match(r"(?:<[\w-]+>/)?(?:skills|hooks)/", script) and \
                        not script.startswith("<installed-sigma>/"):
                    bad.append(script)
    return sorted(set(bad))


def test_every_skill_named_in_prose_ships():
    assert missing_prose_skills(README.read_text(encoding="utf-8")) == []


def test_control_a_skill_planted_in_plain_prose_is_caught():
    text = README.read_text(encoding="utf-8") + "\nThen run /agrim-nonexistent to finish.\n"
    assert missing_prose_skills(text) == ["agrim-nonexistent"]
    assert missing_skills(text) == []                  # the old, code-only extractor is blind to it


def test_every_plugin_cli_command_parses_and_names_the_shipped_id():
    for doc in (README, ROOT / "docs/agent-rules-detail.md", ROOT / "docs/onboarding-control.md"):
        assert plugin_cli_errors(doc.read_text(encoding="utf-8")) == [], doc


@pytest.mark.parametrize("plant", ["`claude plugin instal sigma@sigma`",
                                   "`codex plugin install sigma@sigma`",
                                   "`claude plugin install sigma@other`",
                                   "`codex plugin marketplace refresh`",
                                   "`claude plugin marketplace update elsewhere`"])
def test_control_a_bad_plugin_command_is_caught(plant):
    text = README.read_text(encoding="utf-8") + "\n" + plant + "\n"
    assert len(plugin_cli_errors(text)) == 1, plugin_cli_errors(text)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_the_plugin_grammar_matches_the_cli_where_it_is_installed(host):
    """Read-only: `<cli> plugin --help` must list every top-level verb the README uses. Skipped (never
    green-by-default) where the CLI is absent, as on CI runners."""
    binary = shutil.which(host)
    if not binary:
        pytest.skip(f"no `{host}` CLI on PATH")
    used = {t for host_, rest in _PLUGIN_CMD.findall("\n".join(_code(README.read_text(encoding="utf-8"))))
            if host_ == host for t in rest.split()[:1]}
    out = subprocess.run([binary, "plugin", "--help"], capture_output=True, text=True, timeout=60)
    help_text = out.stdout + out.stderr
    for verb in used:
        assert re.search(r"(?m)^\s+" + re.escape(verb) + r"\b", help_text), (verb, help_text[:400])


def test_installed_sigma_is_defined_once_and_every_user_gesture_uses_it():
    text = README.read_text(encoding="utf-8")
    assert text.count("`<installed-sigma>` is") == 1
    assert "<sigma>/" not in text
    assert uncopyable_gestures(text) == []
    for heading in INIT_SUBSECTIONS:
        assert "from the root of your repository" in _subsection(text, heading), heading


def test_control_a_plugin_relative_gesture_is_caught():
    text = README.read_text(encoding="utf-8") + (
        "\n```\npython3 skills/agrim-init/scripts/preflight.py check . --sdlc .sdlc\n```\n"
        "and `python3 <sigma>/skills/agrim-loop/scripts/loop.py next .sdlc`\n")
    assert uncopyable_gestures(text) == ["<sigma>/skills/agrim-loop/scripts/loop.py",
                                         "skills/agrim-init/scripts/preflight.py"]


# ---------------------------------------------------------------- the access table = preflight's output

def _preflight_mod():
    spec = importlib.util.spec_from_file_location(
        "preflight_277", ROOT / "skills/agrim-init/scripts/preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def access_rows(text):
    """`| Init reports | Run |` rows under "If /agrim-init says you lack access" -> {label: command}."""
    rows = {}
    for line in _subsection(text, INIT_SUBSECTIONS[1]).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] not in ("Init reports", "---"):
            code = re.findall(r"`([^`]+)`", cells[1])
            rows[cells[0]] = " then ".join(code) if code else cells[1]
    return rows


def preflight_fixes():
    """What preflight itself prints for the three gh rows, derived by driving `preflight()` with a
    fake runner over EVERY work / owner-type / board combination (the scope list is the variable
    part: the README shows it as a placeholder, and states the rule)."""
    pf = _preflight_mod()
    fine = ("✓ Logged in to github.com account c (GH_TOKEN)\n- Active account: true\n"
            "- Token: github_pat_11AB****\n- Token scopes: none\n")
    classic = "✓ Logged in to github.com account c\n- Token: ghp_****\n- Token scopes: 'repo'\n"

    def run(cfg, auth, auth_rc=0, owner="User"):
        answers = [
            (("git", "rev-parse", "--is-inside-work-tree"), (0, "true\n")),
            (("git", "rev-parse", "--verify", "-q", "HEAD"), (0, "abc\n")),
            (("git", "rev-parse", "--abbrev-ref", "HEAD"), (0, "main\n")),
            (("git", "rev-parse", "--verify", "-q", "refs/heads/main"), (0, "abc\n")),
            (("git", "remote", "get-url"), (0, "https://github.com/acme/app.git\n")),
            (("git", "remote"), (0, "origin\n")),
            (("git", "ls-remote"), (0, "abc\trefs/heads/main\n")),
            (("gh", "auth", "status"), (auth_rc, auth)),
            (("gh", "api"), (0, owner + "\n")),
        ]

        def runner(argv, cwd=None, timeout=None):
            for prefix, result in answers:
                if tuple(argv[:len(prefix)]) == prefix:
                    return result
            return 1, ""
        checks = pf.preflight("/r", cfg, runner=runner, which=lambda n: "/usr/bin/" + n)
        return {c["id"]: c for c in checks}

    fine_grained = set()
    need_by_combo = {}
    for work_on in (True, False):
        for owner in ("User", "Organization"):
            for board in (True, False):
                cfg = {"work": {"enabled": work_on},
                       "discovery": {"source": "github", "github": {"project": {"enabled": board}}}}
                c = run(cfg, fine, owner=owner)["scopes"]
                need = c["need"]
                need_by_combo[(work_on, owner, board)] = need
                fine_grained.add(" ".join(c["commands"]).replace(",".join(need), "<required scopes>"))
    login = run({"work": {"enabled": True}}, "You are not logged into any GitHub hosts.", auth_rc=1)
    refresh = run({"work": {"enabled": True}}, classic)["scopes"]
    return {"gh not logged in": login["gh-auth"]["commands"],
            "token missing scopes": [refresh["commands"][0].replace(
                ",".join(refresh["missing"]), "<missing scopes>")],
            "a fine-grained token (scopes cannot be read)": sorted(fine_grained),
            "_need": need_by_combo}


def access_table_drift(text):
    rows, fixes = access_rows(text), preflight_fixes()
    bad = [f"{label}: README `{rows.get(label)}`, preflight prints {want}"
           for label, want in fixes.items() if not label.startswith("_") and [rows.get(label)] != want]
    # the scope rule the README states must be the one preflight applies
    need = fixes["_need"]
    rule = {"repo": all("repo" in n for n in need.values()),
            "workflow": all(("workflow" in n) == w for (w, _o, _b), n in need.items()),
            "read:org": all(("read:org" in n) == (o == "Organization") for (_w, o, _b), n in need.items()),
            "project": all(("project" in n) == b for (_w, _o, b), n in need.items())}
    access = _subsection(text, INIT_SUBSECTIONS[0])
    stated = re.search(r"scopes \(([^)]*)\)", access)
    stated = " ".join(stated.group(1).split()) if stated else ""
    for scope, phrase in (("repo", "`repo`"), ("workflow", "`workflow` when work is on"),
                          ("read:org", "`read:org` when the owner is an organization"),
                          ("project", "`project` when a board is on")):
        if not rule[scope]:
            bad.append(f"preflight no longer applies the {scope} rule the test knows")
        elif phrase not in stated:
            bad.append(f"the Access bullet does not state `{phrase}` (it says: {stated!r})")
    return bad


def test_the_access_table_prints_what_preflight_prints():
    assert access_table_drift(README.read_text(encoding="utf-8")) == []


def test_control_the_old_fine_grained_row_is_caught():
    text = README.read_text(encoding="utf-8")
    row = [l for l in text.splitlines() if l.startswith("| a fine-grained token")][0]
    old = text.replace(row, "| a fine-grained token (scopes cannot be read) | "
                            "`gh auth login -h github.com -s repo,workflow` |")
    assert any("fine-grained" in b for b in access_table_drift(old))


# ---------------------------------------------------------------- done means merged, everywhere

_DONE_CLAIM = re.compile(r"records?\s+`?done`?")
_NO_MERGE_PATH = re.compile(r"(?i)\bfork\b|read-only|read access|never merges|leaves? the (?:reviewed )?PR")
_AFTER_MERGE = re.compile(r"(?i)once (?:the|its|upstream|the upstream) (?:PR )?merge|once .{0,20}merged|"
                          r"after the merge|only once|refused")


def done_contradictions(text):
    """A sentence that puts a no-merge path (fork, read-only, `off`/leaves the PR) and "records done"
    together, without the after-merge qualifier, contradicts #232 (done means merged)."""
    bad = []
    for line in text.splitlines():
        for sentence in re.split(r"(?<=[.;])\s+", line):
            if _DONE_CLAIM.search(sentence) and _NO_MERGE_PATH.search(sentence) and \
                    not _AFTER_MERGE.search(sentence):
                bad.append(sentence.strip()[:160])
    return bad


def _done_surfaces():
    return [README, ROOT / "skills/agrim-init/templates/config.json.tmpl"] + \
        sorted((ROOT / "docs").glob("*.md"))


def test_no_row_says_a_no_merge_path_records_done():
    hits = {str(p.relative_to(ROOT)): done_contradictions(p.read_text(encoding="utf-8"))
            for p in _done_surfaces()}
    assert {k: v for k, v in hits.items() if v} == {}


def test_control_the_old_auto_merge_row_is_caught():
    old = ('| `work.auto_merge` | `"off"` | `"always"` merges any clean+safe PR. A fork or read-only '
           'repo never merges — it opens the PR and records `done` |')
    assert len(done_contradictions(old)) == 1
    ok = "the merge-reconcile pass records `done` and closes the issue once the PR merges."
    assert done_contradictions(ok) == []


# ---------------------------------------------------------------- version strings in shipped prose

_VERSION = re.compile(r"(?:(?<=pre-)|(?<![\w.@^~=<>/+-]))v?(\d+)\.(\d+)(?:\.(\d+|x))?(?![\w-]|\.\d)")
#: A lower version is still a previous product's release when a release phrase introduces it.
_RELEASE_PHRASE = re.compile(r"(?i)(?:\bsince|\bpre-|\bbefore|\bas of|\buntil|\bshipped in|\bon|"
                             r"\bin|\bfrom|\bafter)\s*v?$")
_TWO_PART_PHRASE = re.compile(r"(?i)(?:\bsince|\bpre-|\bbefore|\bas of|\buntil|\bshipped in)\s*v?$")
#: A version introduced by one of these names another program, never a Sigma release.
_OTHER_PROGRAM = re.compile(r"(?i)(?:python|darwin|macos|codex-cli|bun|node|gh|git)\s+v?$")
#: (path, token): why that exact version string is legitimate there. The reason is required.
VERSION_ALLOW = {
    ("README.md", "1.4.x"): "the previous name's release line, named as such where an upgrade needs it",
    ("docs/upgrading.md", "1.4.x"): "the previous name's release line, named as such (upgrade guide)",
    ("docs/agent-rules-detail.md", "2.1.284"): "a Claude Code CLI version, measured",
    ("contract/README.md", "1.2.0"): "the event contract's own semver (contract/VERSION)",
    ("contract/README.md", "1.1.0"): "the event contract's own semver history",
}


def version_findings(path_texts, plugin=None):
    """[(path, line, token)] for every release-shaped version string in shipped prose that is above
    the shipped plugin version, or below it where a release phrase introduces it, unless another
    program's name introduces it or VERSION_ALLOW carries a reason for it."""
    plugin = plugin or _plugin_version()
    out = []
    for rel, body in path_texts:
        for n, line in enumerate(body.splitlines(), 1):
            for m in _VERSION.finditer(line):
                token = m.group(0).lstrip("v")
                before = line[max(0, m.start() - 16):m.start()]
                three = m.group(3) is not None
                if _OTHER_PROGRAM.search(before) or (rel, token) in VERSION_ALLOW:
                    continue
                if not three:
                    if _TWO_PART_PHRASE.search(before):
                        out.append((rel, n, token))
                    continue
                ver = (int(m.group(1)), int(m.group(2)),
                       10 ** 6 if m.group(3) == "x" else int(m.group(3)))
                if ver > plugin or (ver < plugin and _RELEASE_PHRASE.search(before)):
                    out.append((rel, n, token))
    return out


def _shipped_prose():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    for rel in tracked:
        if rel.startswith((".sdlc/", "tests/")) or rel == "CHANGELOG.md" \
                or not rel.endswith(_ALL_SHIPPED_TEXT):
            continue
        try:
            yield rel, (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue


def test_no_shipped_prose_names_a_release_the_plugin_has_not_shipped():
    """Scope: tracked `.md .mdc .tmpl .txt .sh` outside tests/, .sdlc/ and CHANGELOG.md (history).
    `.py` docstrings are not scanned: a code comment's history is not a claim a user acts on."""
    assert version_findings(_shipped_prose()) == []


def test_every_version_allowance_carries_a_reason_and_is_still_needed():
    texts = dict(_shipped_prose())
    for (rel, token), why in VERSION_ALLOW.items():
        assert why.strip(), (rel, token)
        assert token in texts.get(rel, ""), f"stale allowance {rel} {token}: remove it"


@pytest.mark.parametrize("plant,token", [("Since 1.0.9 a notebook counts", "1.0.9"),
                                         ("restores the pre-0.6 gate", "0.6"),
                                         ("Measured on 1.4.1: with", "1.4.1"),
                                         ("**Status:** shipped in 1.4.5.", "1.4.5"),
                                         ("a stale `sdlc:goal` (1.3.8).", "1.3.8"),
                                         ("On 1.4.x, run step 3", "1.4.x"),
                                         ("Before 1.3.7 both wrote", "1.3.7"),
                                         ("since 0.9.2 it reads", "0.9.2")])
def test_control_a_planted_release_version_is_caught(plant, token):
    assert version_findings([("docs/x.md", "text\n" + plant + "\n")]) == [("docs/x.md", 2, token)]


def test_control_other_programs_and_the_shipped_version_are_not_findings():
    ok = ("Python 3.12.13 on Darwin 25.6; codex-cli 0.154.0-alpha.6.2; older than 1.0.0; "
          "on Python 3.10 and 3.12")
    assert version_findings([("docs/x.md", ok)]) == []


# ---------------------------------------------------------------- the worked example's sample output

EXAMPLE = ROOT / "examples" / "hello-sdlc"


def _example_status_lines(text):
    fences = [b for b in re.findall(r"```[^\n]*\n(.*?)```", text, re.S) if b.startswith("backlog:")]
    assert len(fences) == 1, fences
    return fences[0].strip()


def test_the_example_status_sample_is_what_status_py_prints(tmp_path):
    """examples/hello-sdlc/README.md's `/agrim-status` sample is the real line: a fresh copy, its one
    goal recorded `done` through `loop.py record`, then `status.py` -- the script the skill runs."""
    repo = tmp_path / "hello"
    shutil.copytree(EXAMPLE, repo)
    env = dict(os.environ, HOME=str(tmp_path / "home"), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull)
    (tmp_path / "home").mkdir()
    status = [sys.executable, str(ROOT / "skills/agrim-status/scripts/status.py"), ".sdlc"]
    before = subprocess.run(status, cwd=repo, env=env, capture_output=True, text=True)
    rec = subprocess.run([sys.executable, str(ROOT / "skills/agrim-loop/scripts/loop.py"), "record",
                          ".sdlc", ".sdlc/goals/0001-add-exclaim.md", "done"],
                         cwd=repo, env=env, capture_output=True, text=True)
    assert rec.returncode == 0, rec.stdout + rec.stderr
    after = subprocess.run(status, cwd=repo, env=env, capture_output=True, text=True)
    text = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert after.stdout.strip() == _example_status_lines(text)
    assert "1 pending" in before.stdout and "iteration 0" in before.stdout, before.stdout
    assert ".gitignore" not in text or "git-ignores" in text     # the old manual-ignore tip is gone


def test_control_a_stale_example_sample_is_caught():
    stale = "```\nbacklog: 0 pending, 0 in-progress, 1 done, 0 parked | iteration 1 | review-queue: empty\n```\n"
    live = _example_status_lines((EXAMPLE / "README.md").read_text(encoding="utf-8"))
    assert _example_status_lines(stale) != live
