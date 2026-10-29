#!/bin/bash
# Setup script of the `sigma-cloud` Claude Code cloud environment: this repository's own, for running
# Sigma's goals with `/sigma-goal <N> --land`, one goal per cloud session. Sigma never runs it for you;
# tools/cloud/README.md has the operator steps and what a run still needs by hand.
#
# It runs as root before each session starts, without GitHub credentials. No `set -e`: a non-zero
# exit fails the whole session, so every step is non-fatal, and a step that fails prints a
# `sigma-cloud: WARNING` line instead. Sessions start from the saved result of the last run, so what
# this installs stays at the version it had then; saving a changed script makes new sessions run it
# again, which also updates Sigma.
#
# Load it with the setup-script dialog's "Choose file" button, from an up-to-date checkout of main.
# Never paste it from a rendered markdown view: a `$...$` span renders as math and loses its `$`
# signs, which once turned another environment's copy into a syntax error that failed every session.

# The cloud clones this repository into the session user's home directory. It is found rather than
# named, so a different user name on the image costs nothing; with none found, section 4 warns.
REPO=$(for d in /home/*/sigma; do [ -e "$d/.git" ] && { echo "$d"; break; }; done)
SIGMA_MIN=1.0.21   # the first Sigma that reads the environment's verify approval
# The suite minus the six files CI's nightly tier skips (`SLOW` in .github/workflows/ci.yml), on the
# worker count the resource guard grants for `-n auto`. The operator's approval value is the hash of
# exactly this string: tests/test_cloud_setup_script.py keeps it, CI's list and tools/cloud/README.md
# in step, and changing it means the operator sets a new value.
VERIFY="python3 -m pytest tests/ --ignore=tests/test_readiness_drills.py --ignore=tests/test_readiness_growth_audit.py --ignore=tests/test_b6_463.py --ignore=tests/test_mutation_sample.py --ignore=tests/test_onboarding_control.py --ignore=tests/test_shared_sdlc_paths.py -rs -q -n auto -p no:cacheprovider"
# loop.py stops a verify after 60 minutes unless told otherwise. CI's nightly runs this same command on a
# 3-core macOS runner: about 60 minutes on 2026-10-17, and stopped at its 120-minute limit at about 98% on
# every run since. The guard grants 3 workers on a 4-vCPU box, so expect about 2 hours per verify here
# (not yet measured in the container), and each review fix-up cycle adds another.
VERIFY_TIMEOUT_MINUTES=240

# --- 1. Newer git ------------------------------------------------------------
# The image ships git 2.43, which this suite has never been run on; 2.46 is the oldest it is known
# green on. Not a measured requirement: an upgrade keeps the container on a git the suite has passed.
# Needs ppa.launchpadcontent.net and keyserver.ubuntu.com under Allowed domains.
# The key is the PPA's current signing key as Launchpad lists it (RSA-4096, 2024), pinned by its full
# fingerprint and checked after the fetch. The check wants the pinned key and no other: apt trusts
# every key in a signed-by keyring.
GIT_FLOOR=2.46
GIT_PPA_KEY=F911AB184317630C59970973E363C90F8F1B6217
GIT_PPA_KEYRING=/etc/apt/keyrings/git-core-ppa.gpg
GIT_PPA_LIST=/etc/apt/sources.list.d/git-core-ppa.list
git_new_enough() {
  local v
  v=$(git --version 2>/dev/null | awk '{print $3}')
  [ -n "$v" ] && [ "$(printf '%s\n' "$GIT_FLOOR" "$v" | sort -V | head -1)" = "$GIT_FLOOR" ]
}

if git_new_enough; then
  echo "sigma-cloud: git $(git --version | awk '{print $3}') is new enough"
else
  install -d -m 0755 /etc/apt/keyrings
  if curl -fsSL --max-time 60 \
      "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${GIT_PPA_KEY}" \
      | gpg --batch --yes --dearmor -o "$GIT_PPA_KEYRING" 2>/dev/null \
    && [ "$(gpg --batch --show-keys --with-colons "$GIT_PPA_KEYRING" 2>/dev/null \
      | awk -F: '$1 == "fpr" && prev == "pub" {print $10} {prev = $1}')" = "$GIT_PPA_KEY" ]; then
    echo "deb [signed-by=$GIT_PPA_KEYRING] https://ppa.launchpadcontent.net/git-core/ppa/ubuntu noble main" \
      > "$GIT_PPA_LIST"
    # Judge the PPA by its own index, not the full update's status: another source failing must
    # not cost the newer git. A PPA apt got no index from is dropped so later apt runs skip it.
    timeout -k 10 120 apt-get update -qq || true
    if apt-cache policy git 2>/dev/null | grep -q 'ppa.launchpadcontent.net/git-core/ppa'; then
      # A killed install can leave dpkg interrupted, which later apt runs refuse: finish what dpkg
      # started, then install git once more.
      DEBIAN_FRONTEND=noninteractive timeout -k 10 120 apt-get install -y -qq git || {
        DEBIAN_FRONTEND=noninteractive timeout -k 10 60 dpkg --configure -a
        DEBIAN_FRONTEND=noninteractive timeout -k 10 120 apt-get install -y -qq git
      } || true
    else
      rm -f "$GIT_PPA_LIST"
      echo "sigma-cloud: WARNING - apt could not read the git-core PPA; source removed (is ppa.launchpadcontent.net allowed?)."
    fi
  else
    # A list saved by an earlier run would point at the keyring removed here.
    rm -f "$GIT_PPA_KEYRING" "$GIT_PPA_LIST"
    echo "sigma-cloud: WARNING - could not fetch the git-core PPA key, or it is not ${GIT_PPA_KEY} alone (is keyserver.ubuntu.com allowed?)."
  fi
  if git_new_enough; then
    echo "sigma-cloud: git upgraded to $(git --version | awk '{print $3}')"
  else
    echo "sigma-cloud: WARNING - git is still $(git --version 2>/dev/null | awk '{print $3}'), older than $GIT_FLOOR."
  fi
fi

# --- 2. pytest in every python3 ----------------------------------------------
# Sigma ships stdlib-only, so the suite needs a python3 with pytest, pytest-xdist (`-n auto`) and
# PyYAML (tests/test_skill_frontmatter_yaml.py). The verify command and Sigma's planned-test checks
# both run the first python3 on PATH (#2079), so every interpreter on PATH gets them. Interpreters are
# told apart by sys.prefix, not by resolving symlinks: a virtualenv's python3 resolves to its base
# interpreter, but packages for it belong in the virtualenv.
TEST_DEPS="pytest pytest-xdist pyyaml"
seen="|"
for PY in $(which -a python3 2>/dev/null); do
  prefix=$("$PY" -c 'import sys; assert sys.version_info >= (3, 10); print(sys.prefix)' 2>/dev/null)
  if [ -z "$prefix" ]; then
    echo "sigma-cloud: WARNING - $PY did not run as Python 3.10 or newer; skipped."
    continue
  fi
  case "$seen" in *"|$prefix|"*) continue ;; esac
  seen="$seen$prefix|"
  "$PY" -c 'import pytest, xdist, yaml' 2>/dev/null \
    || timeout -k 10 300 uv pip install -q --system --break-system-packages --python "$PY" $TEST_DEPS >/dev/null 2>&1 \
    || timeout -k 10 300 "$PY" -m pip install -q --break-system-packages $TEST_DEPS >/dev/null 2>&1
  if "$PY" -c 'import pytest, xdist, yaml' 2>/dev/null; then
    echo "sigma-cloud: $PY has pytest $("$PY" -c 'import pytest; print(pytest.__version__)')"
  else
    echo "sigma-cloud: WARNING - could not install $TEST_DEPS for $PY; verify and the planned-test checks will fail."
  fi
done

# --- 3. Sigma Loop plugin ----------------------------------------------------
# A cloud session does not install the plugins a repository declares, so the VM gets it here.
# `install` keeps the version already on disk (the last run's), so update it too.
timeout -k 10 300 claude plugin marketplace add https://github.com/Agrim-Intelligence/sigma || true
timeout -k 10 300 claude plugin install sigmaloop@sigmaloop || true
timeout -k 10 300 claude plugin update sigmaloop@sigmaloop --scope user || true
SIGMA_VERSION=$(timeout -k 10 60 claude plugin list --json 2>/dev/null | python3 -c '
import json, re, sys
versions = [p["version"] for p in json.load(sys.stdin) if p.get("id") == "sigmaloop@sigmaloop"]
print(min(versions, key=lambda v: [int(n) for n in re.findall(r"\d+", v)], default=""))' 2>/dev/null)
if [ -z "$SIGMA_VERSION" ]; then
  echo "sigma-cloud: WARNING - sigmaloop install did not register."
elif [ "$(printf '%s\n' "$SIGMA_MIN" "$SIGMA_VERSION" | sort -V | head -1)" != "$SIGMA_MIN" ]; then
  echo "sigma-cloud: WARNING - sigmaloop $SIGMA_VERSION is older than $SIGMA_MIN; it cannot read the environment's verify approval, so every goal stops at verify."
else
  echo "sigma-cloud: sigmaloop $SIGMA_VERSION installed"
fi

# --- 4. Loop config ------------------------------------------------------------
# .sdlc/config.json is git-ignored here (`.sdlc/*`), so a fresh clone has no config at all. It is built
# from the installed Sigma's own template plus this repository's choices, and rewritten on every run:
# the script owns the cloud config. `--land` needs auto_merge protected or always. Here a run still
# ends parked, awaiting merge: the `basic` ruleset lets only its bypass actors push to any branch, so
# GitHub reports every PR blocked, and Sigma never merges a blocked PR. A maintainer merges it (owner
# decision on #2090; #2092 would let the run merge through the account's bypass).
# A git-local sigma.allowRepositoryShellCommands left in the saved environment would let every shell
# command the config names run unattended; the environment's approval covers VERIFY alone.
[ -z "$REPO" ] || git -C "$REPO" config --local --unset-all sigma.allowRepositoryShellCommands 2>/dev/null || true
if [ -n "$REPO" ] && git -C "$REPO" config --local --get-all sigma.allowRepositoryShellCommands >/dev/null 2>&1; then
  echo "sigma-cloud: WARNING - could not remove the checkout's git-local shell-command grant; every command the config names would run unattended."
fi
TEMPLATE=$(find "$HOME/.claude/plugins/cache" -path '*sigma-init/templates/config.json.tmpl' 2>/dev/null | sort -V | tail -1)
if [ -z "$REPO" ]; then
  echo "sigma-cloud: WARNING - found no checkout of this repository in a home directory; .sdlc/config.json not written."
elif [ -z "$TEMPLATE" ]; then
  echo "sigma-cloud: WARNING - no Sigma config template found (did section 3 install Sigma?); .sdlc/config.json not written."
elif TEMPLATE="$TEMPLATE" SDLC="$REPO/.sdlc" VERIFY="$VERIFY" VERIFY_TIMEOUT_MINUTES="$VERIFY_TIMEOUT_MINUTES" \
    python3 - <<'EOF'
# >>> sigma-cloud config
import json
import os
import pathlib

template = pathlib.Path(os.environ["TEMPLATE"])
sdlc = pathlib.Path(os.environ["SDLC"])
config = json.loads(template.read_text())
project_template = template.parent / "project.md.tmpl"
project_text = (project_template.read_text().replace("{{PROJECT_NAME}}", "sigma")
                if project_template.exists() else None)

discovery = config["discovery"]
discovery["source"] = "github"
github = discovery.setdefault("github", {})
github["repo"] = "Agrim-Intelligence/sigma"
github["assignee"] = "@me"
github.setdefault("project", {})["enabled"] = False  # the board is GraphQL, which the cloud proxy refuses
config.setdefault("ledger", {})["enabled"] = False
config["model_selection"] = "off"
config["verify"].update(command=os.environ["VERIFY"], enforce=True,
                        timeout_minutes=int(os.environ["VERIFY_TIMEOUT_MINUTES"]))
config["work"].update(enabled=True, auto_merge="protected", require_review="approval")

sdlc.mkdir(parents=True, exist_ok=True)
tmp = sdlc / "config.json.tmp"
tmp.write_text(json.dumps(config, indent=1) + "\n")
tmp.replace(sdlc / "config.json")
project = sdlc / "project.md"
if project_text is not None and not project.exists():
    project.write_text(project_text)
# <<< sigma-cloud config
EOF
then
  echo "sigma-cloud: .sdlc/config.json written from $TEMPLATE"
else
  echo "sigma-cloud: WARNING - could not write .sdlc/config.json; the loop has no config."
fi

exit 0
