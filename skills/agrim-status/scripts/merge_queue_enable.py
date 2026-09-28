"""Opt-in, admin-consented merge-queue ENABLE tool (#977, split (c) of #408; the mutating
counterpart to #976's read-only `merge_queue.py` advisor). Zero-dep.

WHY THIS EXISTS. #976 (`merge_queue.py`, already merged) detects the shape a GitHub merge queue would
fix and surfaces a read-only RECOMMENDATION via `/agrim-status` -- it never mutates anything and never
requests `administration` scope, by design. This module is the layer ON TOP: when a repo ADMIN
explicitly consents, it can actually enable the native GitHub merge queue on their behalf by (1)
enabling repo auto-merge if it's off, then (2) creating a `merge_queue` rule via the Rulesets API.

THIS IS A HIGH-RISK, IRREVERSIBLE-CLASS ACTION. Creating/mutating a ruleset and flipping repo settings
is a real branch-protection/governance change. Every safeguard in this module exists because of that,
not as decoration:

  - There is NO code path from `plan()` to a mutating `gh api` call -- `plan()` only ever calls
    `get_repo`/`list_rulesets`, both plain GETs, under every input. It takes no consent flag at all
    because there is nothing for a flag to gate.
  - `apply()` -- the only function that can mutate -- is never reachable from the CLI (`main()`)
    without the EXACT, full string `--yes-enable-merge-queue` present in argv (see `CONSENT_FLAG`).
    This is a manual `in argv` membership check, deliberately NOT built on `argparse`'s default
    flag parsing: `argparse` accepts unambiguous PREFIXES of long flags by default
    (`allow_abbrev=True`), so a naive argparse-based gate would let a bare `--yes` silently satisfy
    `--yes-enable-merge-queue` -- exactly the kind of near-miss this gate exists to catch. A test
    pins this (`test_apply_refuses_with_near_miss_flag_and_makes_zero_calls`).
  - Never triggered by the ordinary Sigma loop cycle. This module is invoked directly, by a human
    (via an agent relaying `plan`'s output and getting an explicit "yes" in chat first) -- see the
    "opt-in enable" paragraph in `skills/agrim-status/SKILL.md`.
  - `apply()`'s internal sequence is ordered to minimize the partial-mutation window and fail closed
    as early as possible: read current state -> READ-ONLY capability preflight (catches a
    missing-scope/plan-gated 403 for zero mutation cost, before anything is touched) -> enable
    auto-merge only if needed -> create the ruleset -> re-read and verify. The one step whose failure
    CAN leave governance partially changed (ruleset creation failing after auto-merge was just
    flipped on) is detected and reported explicitly via `result["partial"]` -- never silently left
    half-done. See the module's `apply()` docstring for the exact accounting.
  - Every mutating call's error is classified (`_classify_error`) into `plan_gate` (a 403 whose body
    says to upgrade -- GitHub merge queues need a paid plan, Team/Enterprise, for a private repo) vs.
    `permission` (a 403 for a token/user missing admin rights or the `administration` scope) vs.
    `other`, each with its own actionable remediation text (`ApiError.describe()`) -- never a bare
    "failed".

VERIFIED PAYLOAD SHAPE (research pass, this goal, cross-checked against the current docs.github.com
REST reference for "Create/Update a repository ruleset" as of this writing): the `merge_queue` rule
type takes exactly seven required `parameters` fields (`check_response_timeout_minutes`,
`grouping_strategy`, `max_entries_to_build`, `max_entries_to_merge`, `merge_method`,
`min_entries_to_merge`, `min_entries_to_merge_wait_minutes`) -- see `_ruleset_payload`. A Feb-2024
GitHub changelog post once said this rule type "cannot be configured via an API"; that gap has since
closed and the current docs list `merge_queue` as a valid `rules[].type` value with this exact
schema. NOT independently verified against a real live `POST` in this SDLC cycle (a future
live-fidelity check needs a disposable repository) -- every
test in `tests/test_merge_queue_enable.py` mocks `run`, and this codebase's own standing rule for this
cycle forbids a real mutating call against the maintainers' own repository."""
import json
import subprocess

#: The one string that gates `apply()`. Must appear verbatim in argv -- see the module docstring for
#: why this is a manual membership check, not argparse-based flag parsing.
CONSENT_FLAG = "--yes-enable-merge-queue"

_MERGE_METHODS = ("MERGE", "SQUASH", "REBASE")

#: Shipped defaults for the merge_queue rule's `parameters`, named so a future tune-up has one place
#: to look. `merge_method` defaults to SQUASH to match this repo's own `.sdlc/config.json` /
#: `work.py`'s `merge_method: "squash"` convention -- overridable via `--merge-method`.
_CHECK_RESPONSE_TIMEOUT_MINUTES = 60
_GROUPING_STRATEGY = "ALLGREEN"
_MAX_ENTRIES_TO_BUILD = 5
_MAX_ENTRIES_TO_MERGE = 5
_MIN_ENTRIES_TO_MERGE = 1
_MIN_ENTRIES_TO_MERGE_WAIT_MINUTES = 5

_REMEDIATION = {
    "plan_gate": ("this repo's GitHub plan does not support this feature -- upgrade to a supported "
                  "plan (e.g. GitHub Team or Enterprise for a private repo) to enable a merge queue"),
    "permission": ("the token/user is missing the `administration` scope or admin rights on this "
                   "repo -- re-run as a repo admin with `administration:write`"),
    "other": "an unexpected API error -- re-run `plan` to inspect current state before retrying",
}


def _classify_error(text):
    """Classifies a `gh api` failure's stderr/stdout text into one of `_REMEDIATION`'s keys.
    Deliberately simple and conservative: "upgrade" anywhere in the text means the plan-gate message
    GitHub sends for a feature this repo's plan doesn't support; "403"/"resource not accessible"
    means a permission/scope problem; anything else is `other` rather than guessed at -- an
    unclassified error still fails closed, it just gets the more generic (still actionable) message."""
    t = (text or "").lower()
    if "upgrade" in t:
        return "plan_gate"
    if "403" in t or "resource not accessible" in t:
        return "permission"
    return "other"


class ApiError(Exception):
    """Raised by every `gh api` helper below on a non-zero exit, an unparseable response, or an
    unexpected response shape. Carries enough to build both a machine-usable `stage`/`classification`
    and a human-actionable message -- callers never have to re-derive either."""

    def __init__(self, stage, path, returncode, detail, payload=None):
        self.stage = stage
        self.path = path
        self.returncode = returncode
        self.detail = detail or ""
        self.classification = _classify_error(self.detail)
        self.payload = payload
        super().__init__(self.describe())

    def describe(self):
        remediation = _REMEDIATION[self.classification]
        return (f"[{self.stage}] {self.path} failed (exit {self.returncode}, {self.classification}): "
                f"{self.detail.strip()} -- {remediation}")


def _real_run(args, input_json=None):
    """The only function in this module that actually shells out. Returns (returncode, stdout,
    stderr) -- unlike #976's `merge_queue.py::_api` (which collapses every failure to a bare `None`,
    correct for a fail-open ADVISORY signal), this mutating tool needs the real exit code and stderr
    text to classify plan-gate vs. missing-scope vs. other and report it, so a caller gets an
    actionable message instead of a bare "failed". `input_json`, when given, is serialized and piped
    to stdin -- the standard `gh api ... --input -` idiom for a nested JSON body too complex for
    repeated `-f key=value` flags (the ruleset POST's `rules`/`conditions` arrays)."""
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            input=(json.dumps(input_json) if input_json is not None else None),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as exc:                                       # pragma: no cover -- exercised via mocks only
        return 1, "", str(exc)


def _normalize_merge_method(merge_method):
    """Accepts the `merge_method` value case-insensitively (this repo's own config spells it lower-
    case, `"squash"`; the GitHub API enum is upper-case) so a caller passing the repo's own convention
    never sends a malformed enum value to the API. `None` defaults to `SQUASH`. Anything that isn't
    one of the three real API values raises `ValueError` -- validated by `apply()` BEFORE any `gh`
    call is made, so a bad `--merge-method` flag can never cause a partial mutation."""
    m = (merge_method or "SQUASH").strip().upper()
    if m not in _MERGE_METHODS:
        raise ValueError(f"unsupported merge_method {merge_method!r} -- must be one of {_MERGE_METHODS}")
    return m


def _ruleset_payload(branch, merge_method="SQUASH"):
    """The exact request body for `POST/PUT .../rulesets` with a single `merge_queue` rule targeting
    `branch`. See the module docstring for the verified schema and its provenance."""
    return {
        "name": f"sigma-merge-queue-{branch}",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": [
            {
                "type": "merge_queue",
                "parameters": {
                    "check_response_timeout_minutes": _CHECK_RESPONSE_TIMEOUT_MINUTES,
                    "grouping_strategy": _GROUPING_STRATEGY,
                    "max_entries_to_build": _MAX_ENTRIES_TO_BUILD,
                    "max_entries_to_merge": _MAX_ENTRIES_TO_MERGE,
                    "merge_method": _normalize_merge_method(merge_method),
                    "min_entries_to_merge": _MIN_ENTRIES_TO_MERGE,
                    "min_entries_to_merge_wait_minutes": _MIN_ENTRIES_TO_MERGE_WAIT_MINUTES,
                },
            }
        ],
    }


def _rules_of(ruleset):
    rules = ruleset.get("rules") if isinstance(ruleset, dict) else None
    return rules if isinstance(rules, list) else []


# --- gh api helpers (each is a single call; each raises ApiError, never returns a partial/garbage
# value silently) -----------------------------------------------------------------------------------

def get_repo(repo, run):
    """GET repos/{repo} -- read-only."""
    path = f"repos/{repo}"
    rc, out, err = run(["gh", "api", path])
    if rc != 0:
        raise ApiError("read_repo", path, rc, err or out)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise ApiError("read_repo", path, rc, "unparseable JSON response")
    if not isinstance(data, dict):
        raise ApiError("read_repo", path, rc, "unexpected response shape (not an object)")
    return data


def list_rulesets(repo, run):
    """GET repos/{repo}/rulesets -- read-only. Doubles as `apply()`'s capability preflight: the
    cheapest read that would also fail on a missing-`administration`-scope or plan-gated token,
    before any mutating call is attempted."""
    path = f"repos/{repo}/rulesets"
    rc, out, err = run(["gh", "api", path])
    if rc != 0:
        raise ApiError("read_rulesets", path, rc, err or out)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise ApiError("read_rulesets", path, rc, "unparseable JSON response")
    if not isinstance(data, list):
        raise ApiError("read_rulesets", path, rc, "unexpected response shape (not a list)")
    return data


def get_ruleset(repo, ruleset_id, run):
    """GET repos/{repo}/rulesets/{id} -- read-only. Used by `apply()`'s post-create verify step."""
    path = f"repos/{repo}/rulesets/{ruleset_id}"
    rc, out, err = run(["gh", "api", path])
    if rc != 0:
        raise ApiError("verify_ruleset", path, rc, err or out)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise ApiError("verify_ruleset", path, rc, "unparseable JSON response")
    if not isinstance(data, dict):
        raise ApiError("verify_ruleset", path, rc, "unexpected response shape (not an object)")
    return data


def patch_auto_merge(repo, run):
    """PATCH repos/{repo} -f allow_auto_merge=true -- the ONE mutating call in the auto-merge step,
    the exact form the goal names: a normal repo-settings write most admin tokens already carry."""
    path = f"repos/{repo}"
    rc, out, err = run(["gh", "api", "-X", "PATCH", path, "-f", "allow_auto_merge=true"])
    if rc != 0:
        raise ApiError("enable_auto_merge", path, rc, err or out)
    return True


def create_merge_queue_ruleset(repo, branch, run, merge_method="SQUASH"):
    """POST repos/{repo}/rulesets with the `merge_queue` payload, via `--input -` (stdin JSON) --
    the ruleset body has nested arrays (`rules`, `conditions.ref_name.include`) too complex for
    repeated `-f key=value` flags."""
    path = f"repos/{repo}/rulesets"
    payload = _ruleset_payload(branch, merge_method=merge_method)
    rc, out, err = run(["gh", "api", path, "-X", "POST", "--input", "-"], input_json=payload)
    if rc != 0:
        raise ApiError("create_ruleset", path, rc, err or out, payload=payload)
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        raise ApiError("create_ruleset", path, rc, "unparseable JSON response", payload=payload)
    if not isinstance(data, dict):
        raise ApiError("create_ruleset", path, rc, "unexpected response shape (not an object)", payload=payload)
    return data


def find_existing_merge_queue_rule(rulesets, branch):
    """A ruleset already enforcing `merge_queue` on `branch`, if any -- read-only helper for
    `plan()`'s "already done" reporting. Matches on target-branch ref + rule type only, not exact
    parameter equality: a differently-tuned existing `merge_queue` rule still counts as "already
    present" for planning purposes. `apply()` never overwrites an existing ruleset -- it only ever
    creates a new one, so this is advisory-only, same as #976's own advisory idiom."""
    ref = f"refs/heads/{branch}"
    for rs in rulesets or []:
        if not isinstance(rs, dict):
            continue
        conditions = rs.get("conditions")
        ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
        includes = ref_name.get("include") if isinstance(ref_name, dict) else None
        if not isinstance(includes, list) or ref not in includes:
            continue
        for rule in _rules_of(rs):
            if isinstance(rule, dict) and rule.get("type") == "merge_queue":
                return rs
    return None


def plan(repo, branch, run):
    """READ-ONLY. Under every input, this function only ever calls `get_repo`/`list_rulesets` -- both
    plain GETs -- and never anything with `-X`, `-f`, or `--input`. Reports exactly what `apply()`
    would do: whether auto-merge would change, whether a `merge_queue` rule already exists on
    `branch`, and a preview of the exact ruleset payload `apply()` would send. There is no flag or
    input that can turn this into a mutating call -- that boundary is enforced by this function simply
    never calling anything but the two GET helpers above, not by a runtime check."""
    result = {"repo": repo, "branch": branch}
    try:
        repo_data = get_repo(repo, run)
    except ApiError as e:
        result["ok"] = False
        result["stage"] = e.stage
        result["error"] = e.describe()
        return result
    auto_merge_on = bool(repo_data.get("allow_auto_merge"))
    result["auto_merge_currently_on"] = auto_merge_on

    try:
        rulesets = list_rulesets(repo, run)
    except ApiError as e:
        result["ok"] = False
        result["stage"] = e.stage
        result["error"] = e.describe()
        return result

    existing = find_existing_merge_queue_rule(rulesets, branch)
    result["ok"] = True
    result["auto_merge_would_change"] = not auto_merge_on
    result["existing_merge_queue_ruleset"] = existing
    result["ruleset_would_be_created"] = existing is None
    result["payload_preview"] = _ruleset_payload(branch)
    return result


def apply(repo, branch, run, merge_method="SQUASH"):
    """The ONLY function in this module that can mutate. Never call this directly from a CLI/agent
    context without a prior, explicit, in-chat admin "yes" -- `main()`'s consent-flag gate is the
    enforcement point for that; this function itself has no consent check because it is meant to be
    called only after the gate has already passed (and, in tests, deliberately without it, to test the
    apply logic in isolation from the CLI gate).

    Sequence, in order, chosen to minimize the partial-mutation window (see module docstring):
      1. `get_repo` -- read current `allow_auto_merge`.
      2. `list_rulesets` -- READ-ONLY capability preflight. Catches a missing-scope or plan-gated
         token for zero mutation cost, before step 3 touches anything. NOTE: this cannot fully
         guarantee step 4's POST will succeed (read vs. write permission can differ) -- it only
         catches the common case (no `administration` access at all).
      3. `patch_auto_merge`, skipped entirely if already on.
      4. `create_merge_queue_ruleset`.
      5. Re-`get_repo` + `get_ruleset` to verify the requested state actually landed.

    `result["partial"]` is `True` exactly when auto-merge WAS just flipped on (`auto_merge.changed`)
    and something after that did not fully succeed (ruleset creation failed, or verification failed
    or mismatched) -- the exact case the goal's done_when #5 requires never be silently left
    unreported. `result["ok"]` is `True` only when every step succeeded AND verification confirms it."""
    result = {"repo": repo, "branch": branch, "auto_merge": None, "ruleset": None,
              "verified": None, "partial": False, "ok": False}

    try:
        merge_method = _normalize_merge_method(merge_method)
    except ValueError as e:
        result["stage"] = "validate_input"
        result["error"] = str(e)
        return result

    try:
        repo_data = get_repo(repo, run)
    except ApiError as e:
        result["stage"] = e.stage
        result["error"] = e.describe()
        return result
    auto_merge_on = bool(repo_data.get("allow_auto_merge"))

    try:
        list_rulesets(repo, run)                                    # read-only preflight, see docstring
    except ApiError as e:
        result["stage"] = "preflight_rulesets_read"
        result["error"] = e.describe()
        result["auto_merge"] = {"changed": False, "value": auto_merge_on}
        return result                                                # zero mutations made

    if auto_merge_on:
        result["auto_merge"] = {"changed": False, "value": True}
    else:
        try:
            patch_auto_merge(repo, run)
        except ApiError as e:
            result["stage"] = e.stage
            result["error"] = e.describe()
            result["auto_merge"] = {"changed": False, "value": auto_merge_on}
            return result                                            # the PATCH itself failed -- no mutation
        result["auto_merge"] = {"changed": True, "value": True}

    try:
        created = create_merge_queue_ruleset(repo, branch, run, merge_method=merge_method)
    except ApiError as e:
        result["stage"] = e.stage
        result["error"] = e.describe()
        result["ruleset"] = None
        result["partial"] = bool(result["auto_merge"]["changed"])    # auto-merge on, ruleset NOT created
        return result
    result["ruleset"] = {"changed": True, "id": created.get("id")}

    try:
        verify_repo = get_repo(repo, run)
        verify_ruleset = get_ruleset(repo, created.get("id"), run)
    except ApiError as e:
        result["stage"] = e.stage
        result["error"] = e.describe()
        result["verified"] = False
        result["partial"] = True                                     # ruleset call succeeded, verify didn't
        return result

    verified = (
        bool(verify_repo.get("allow_auto_merge")) is True
        and any(isinstance(r, dict) and r.get("type") == "merge_queue" for r in _rules_of(verify_ruleset))
    )
    result["verified"] = verified
    result["ok"] = verified
    if not verified:
        result["partial"] = True
        result["error"] = ("post-apply verification did not confirm the expected state (auto-merge "
                            "and/or the merge_queue rule) -- re-run `plan` to inspect current state "
                            "before retrying")
    return result


def _flag_value(argv, flag, default=None):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _usage():
    return (
        "usage:\n"
        "  merge_queue_enable.py plan <owner/repo> [--branch main]\n"
        "  merge_queue_enable.py apply <owner/repo> " + CONSENT_FLAG + " [--branch main] "
        "[--merge-method SQUASH]\n"
        "\n"
        "`apply` is an ADMIN-scoped, irreversible-class action (it can enable repo auto-merge and "
        "create a branch-protection ruleset). It is REFUSED unless the exact flag " + CONSENT_FLAG +
        " is present -- never run it without a repo admin's explicit, in-chat 'yes' to `plan`'s output "
        "first."
    )


def main(argv, run=None):
    if argv[1:] in (["-h"], ["--help"]):
        print(_usage())
        return 0
    run = run or _real_run
    if len(argv) < 3:
        print(_usage())
        return 2

    cmd, repo = argv[1], argv[2]
    branch = _flag_value(argv, "--branch", "main")

    if cmd == "plan":
        result = plan(repo, branch, run)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    if cmd == "apply":
        if CONSENT_FLAG not in argv:
            print(
                "REFUSED: this is an ADMIN-scoped, irreversible-class action -- it can enable repo "
                "auto-merge (if off) and creates a `merge_queue` branch-protection ruleset. It "
                f"requires the exact, explicit consent flag `{CONSENT_FLAG}`; no abbreviation or "
                "near-miss flag is accepted. Run `plan` first, show a repo admin exactly what would "
                f"change, get an explicit 'yes' in chat, THEN re-run with `apply <repo> "
                f"{CONSENT_FLAG}`. No `gh` call has been made."
            )
            return 2
        merge_method = _flag_value(argv, "--merge-method", "SQUASH")
        result = apply(repo, branch, run, merge_method=merge_method)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    print(_usage())
    return 2


if __name__ == "__main__":                                          # pragma: no cover
    import sys
    sys.exit(main(sys.argv))
