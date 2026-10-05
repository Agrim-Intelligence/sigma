#!/usr/bin/env python3
"""Canonical payload helpers for the receipt-publication rollout release manifest."""
import argparse
import hashlib
import json
import re
import datetime
import subprocess
import pathlib

#: `tag_object_sha` is DELIBERATELY not a field, and `payload()` rejects it as unknown.
#: Including it would be circular and therefore unsatisfiable: the tag's own
#: `Sigma-Receipt-Manifest-SHA256` trailer commits to the digest of these bytes, so the
#: manifest must exist BEFORE the tag object does -- see
#: `test_manifest_is_constructible_before_the_tag_object_exists`.  The residual (a trusted signer
#: recreating a same-name tag at the same commit with the same trailers) changes nothing a
#: verifier reads: `release_commit_sha`, `receipt_publication_rollout_at` and the manifest digest
#: are each pinned and compared in `verify_release_authority`, and the attack already presupposes
#: a trusted signer.  Pinning the tag object would need a second artifact outside this digest.
_FIELDS = ("schema_version", "kind", "canonical_repository_id", "release_tag", "release_commit_sha",
           "receipt_publication_rollout_at")


def payload(facts):
    if set(facts) - set(_FIELDS) or not set(_FIELDS[2:]) <= set(facts):
        raise ValueError("release manifest has missing or unknown fields")
    value = dict(facts, schema_version=1, kind="receipt-publication-rollout")
    if not all(isinstance(value[name], str) and value[name] for name in _FIELDS[2:]):
        raise ValueError("release manifest facts must be non-empty strings")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value["release_commit_sha"]):
        raise ValueError("invalid release commit SHA")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def payload_digest(data):
    return hashlib.sha256(data).hexdigest()


def _utc(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamps must be UTC Z strings")
    return datetime.datetime.fromisoformat(value[:-1] + "+00:00")


def acceptance_eligible(rollout_at, since, until, now):
    """Only the exact, complete 14-day post-rollout cohort can carry an acceptance verdict."""
    rollout, start, end, current = map(_utc, (rollout_at, since, until, now))
    return start == rollout and end == rollout + datetime.timedelta(days=14) and current >= end


def verify_payload(data, facts):
    """Reject byte-level tampering and any payload that cannot be reconstructed canonically."""
    return data == payload(facts) and payload_digest(data) == hashlib.sha256(data).hexdigest()


def parse_payload(data):
    """Return a release payload only when its *stored bytes* are canonical.

    A parsed JSON object is not sufficient evidence: accepting reformatted JSON would let a
    trailer authenticate one byte sequence while the scanner consumes another one.
    """
    try:
        facts = json.loads(data)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("release manifest is not JSON") from exc
    if not isinstance(facts, dict) or payload(facts) != data:
        raise ValueError("release manifest is not canonical")
    return facts


def _git(repo, args, run=None):
    """Run one non-interactive git command, with a small injectable seam for tests."""
    command = ["git", "-C", str(repo), *args]
    if run is not None:
        result = run(command)
        if isinstance(result, tuple):
            code, output = result
            if code:
                raise ValueError("git %s failed: %s" % (" ".join(args), output))
            return str(output).strip()
        return str(result).strip()
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode:
        raise ValueError("git %s failed: %s" % (" ".join(args), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip() + ("\n" + proc.stderr.strip() if proc.stderr.strip() else "")


def _trailer(contents, name):
    values = []
    prefix = name + ":"
    for line in contents.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            if value:
                values.append(value)
    if len(values) != 1:
        raise ValueError("release tag must contain exactly one %s trailer" % name)
    return values[0]


def verify_signed_tag(repo, tag, trusted_signers, run=None):
    """Verify an annotated release tag and return its immutable tag/commit facts.

    Git may report a valid signature from a signing subkey.  The `VALIDSIG` fingerprint is the
    full primary identity proof we pin, so abbreviated fingerprints and a successful command
    without a matching status line are both refusals.
    """
    status = _git(repo, ["verify-tag", "--raw", tag], run)
    allowed = {str(item).strip().lower() for item in trusted_signers
               if re.fullmatch(r"[0-9A-Fa-f]{40}", str(item).strip())}
    if not allowed:
        raise ValueError("receipt authority has no full trusted signer fingerprint")
    match = re.search(r"^\[GNUPG:\]\s+VALIDSIG\s+([0-9A-Fa-f]{40})\b", status, re.MULTILINE)
    if not match or match.group(1).lower() not in allowed:
        raise ValueError("release tag signer is not trusted")
    tag_object_sha = _git(repo, ["rev-parse", tag + "^{tag}"], run).splitlines()[0]
    release_commit_sha = _git(repo, ["rev-parse", tag + "^{commit}"], run).splitlines()[0]
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", tag_object_sha):
        raise ValueError("release tag object is not a SHA")
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", release_commit_sha):
        raise ValueError("release tag commit is not a SHA")
    # A fetched authority tag lives under a private absolute ref.  Prefixing it again would
    # silently read a different, local `refs/tags/refs/...` name.
    reference = tag if isinstance(tag, str) and tag.startswith("refs/") else "refs/tags/" + str(tag)
    contents = _git(repo, ["for-each-ref", reference, "--format=%(contents)"], run)
    return {"tag_object_sha": tag_object_sha, "release_commit_sha": release_commit_sha,
            "rollout_at": _trailer(contents, "Sigma-Receipt-Rollout-At"),
            "manifest_digest": _trailer(contents, "Sigma-Receipt-Manifest-SHA256")}


def verify_release_authority(repo, data, trusted_signers, run=None, tag_ref=None):
    """Tie canonical release bytes to the signed annotated tag and its trailers."""
    facts = parse_payload(data)
    tag = verify_signed_tag(repo, tag_ref or facts["release_tag"], trusted_signers, run)
    if tag["release_commit_sha"] != facts["release_commit_sha"]:
        raise ValueError("release manifest does not match signed tag commit")
    if tag["rollout_at"] != facts["receipt_publication_rollout_at"]:
        raise ValueError("release tag rollout trailer does not match manifest")
    digest = payload_digest(data)
    if tag["manifest_digest"].lower() != digest:
        raise ValueError("release tag digest trailer does not match manifest")
    return facts


def write_once(path, data):
    """Publish the exact canonical manifest once; a retry may only adopt identical bytes."""
    path = pathlib.Path(path)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("release manifest immutable bytes differ")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def publish_rollout_manifest(repo, path, canonical_repository_id, tag, trusted_signers, run=None):
    """Build and create-once publish a manifest from facts authenticated by an annotated tag."""
    tag_facts = verify_signed_tag(repo, tag, trusted_signers, run)
    facts = {"canonical_repository_id": canonical_repository_id, "release_tag": tag,
             "release_commit_sha": tag_facts["release_commit_sha"],
             "receipt_publication_rollout_at": tag_facts["rollout_at"]}
    data = payload(facts)
    if tag_facts["manifest_digest"].lower() != payload_digest(data):
        raise ValueError("release tag digest trailer does not match generated manifest")
    write_once(path, data)
    return facts


def _safe_manifest_path(repo, path):
    root = pathlib.Path(repo).resolve()
    target = pathlib.Path(path).resolve()
    if target != root / "releases" / "receipt-publication-v1.json":
        raise ValueError("manifest must be releases/receipt-publication-v1.json in the ledger repository")
    return target


def publish_to_ledger_branch(repo, canonical_repository_id, tag, trusted_signers, run=None):
    """Write the canonical artifact once, then commit and non-force-push only that artifact."""
    path = _safe_manifest_path(repo, pathlib.Path(repo) / "releases" / "receipt-publication-v1.json")
    branch = _git(repo, ["branch", "--show-current"], run)
    if branch != "sdlc-ledger":
        raise ValueError("manifest publish requires the checked-out sdlc-ledger branch")
    if _git(repo, ["diff", "--cached", "--name-only"], run):
        raise ValueError("manifest publish refuses an existing staged change")
    facts = publish_rollout_manifest(repo, path, canonical_repository_id, tag, trusted_signers, run)
    # An already-published identical artifact is an idempotent adoption.  Do not manufacture a
    # second commit or stage somebody else's changes while retrying it.
    status = _git(repo, ["status", "--porcelain", "--", "releases/receipt-publication-v1.json"], run)
    if not status:
        return {"facts": facts, "published": False}
    _git(repo, ["add", "--", "releases/receipt-publication-v1.json"], run)
    staged = _git(repo, ["diff", "--cached", "--name-only"], run)
    if staged != "releases/receipt-publication-v1.json":
        raise ValueError("manifest publish staged an unsafe path")
    _git(repo, ["commit", "--only", "-m", "ledger: publish receipt rollout manifest", "--",
                "releases/receipt-publication-v1.json"], run)
    _git(repo, ["push", "origin", "HEAD:refs/heads/sdlc-ledger"], run)
    return {"facts": facts, "published": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create or verify a receipt rollout release manifest")
    commands = parser.add_subparsers(dest="command")
    verify = commands.add_parser("verify", help="verify canonical manifest bytes against its signed release tag")
    verify.add_argument("--repo", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--trusted-signer", action="append", required=True)
    publish = commands.add_parser("publish", help="verify tag, write once, then commit and push the canonical ledger manifest")
    publish.add_argument("--repo", required=True)
    publish.add_argument("--canonical-repository-id", required=True)
    publish.add_argument("--tag", required=True)
    publish.add_argument("--trusted-signer", action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        try:
            data = pathlib.Path(args.manifest).read_bytes()
            print(json.dumps(verify_release_authority(args.repo, data, args.trusted_signer), sort_keys=True))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    elif args.command == "publish":
        try:
            print(json.dumps(publish_to_ledger_branch(args.repo, args.canonical_repository_id,
                args.tag, args.trusted_signer), sort_keys=True))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))


if __name__ == "__main__":
    main()
