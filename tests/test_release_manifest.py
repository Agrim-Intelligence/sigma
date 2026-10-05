import importlib.util
import pathlib

import pytest


S = pathlib.Path(__file__).resolve().parent.parent / "skills" / "sigma-loop" / "scripts" / "release_manifest.py"
spec = importlib.util.spec_from_file_location("release_manifest", S)
release_manifest = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(release_manifest)
except FileNotFoundError:
    pass


def test_rollout_manifest_is_canonical_and_has_no_self_digest():
    data = release_manifest.payload({"canonical_repository_id": "R_1", "release_tag": "v1.2.0",
        "release_commit_sha": "b" * 40,
        "receipt_publication_rollout_at": "2026-01-01T00:00:00Z"})
    assert data.endswith(b"\n") and b"manifest_sha256" not in data
    assert release_manifest.payload_digest(data) == release_manifest.payload_digest(data)


def test_manifest_is_constructible_before_the_tag_object_exists():
    data = release_manifest.payload({"canonical_repository_id": "R_1", "release_tag": "v1.2.0",
        "release_commit_sha": "b" * 40, "receipt_publication_rollout_at": "2026-01-01T00:00:00Z"})
    assert b"tag_object_sha" not in data


def test_acceptance_interval_is_only_the_finished_fixed_rollout_window():
    rollout = "2026-01-01T00:00:00Z"
    assert release_manifest.acceptance_eligible(rollout, "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z", "2026-01-15T00:00:01Z")
    assert not release_manifest.acceptance_eligible(rollout, "2026-01-01T00:00:00Z", "2026-01-15T00:00:00Z", "2026-01-14T23:59:59Z")


def test_release_authority_requires_the_exact_full_signer_and_matching_tag_trailers():
    data = release_manifest.payload({"canonical_repository_id": "R_1", "release_tag": "v1.2.0",
        "release_commit_sha": "b" * 40,
        "receipt_publication_rollout_at": "2026-01-01T00:00:00Z"})
    digest = release_manifest.payload_digest(data)

    def git(command):
        args = command[3:]
        if args[:2] == ["verify-tag", "--raw"]:
            return "[GNUPG:] VALIDSIG AABBCCDDEEFF00112233445566778899AABBCCDD 2026-01-01"
        if args[:1] == ["rev-parse"] and args[1].endswith("^{tag}"):
            return "a" * 40
        if args[:1] == ["rev-parse"]:
            return "b" * 40
        if args[:1] == ["for-each-ref"]:
            return ("Sigma-Receipt-Rollout-At: 2026-01-01T00:00:00Z\n"
                    "Sigma-Receipt-Manifest-SHA256: " + digest)
        raise AssertionError(command)

    assert release_manifest.verify_release_authority("repo", data,
        ["aabbccddeeff00112233445566778899aabbccdd"], git)["release_tag"] == "v1.2.0"
    with pytest.raises(ValueError, match="full trusted signer"):
        release_manifest.verify_release_authority("repo", data, ["AABBCCDD"], git)


def test_signed_tag_uses_an_absolute_private_ref_without_prefixing_refs_tags():
    tag_ref = "refs/sdlc/rollout-tags/v1.2.0"
    seen = []
    def git(command):
        args = command[3:]; seen.append(args)
        if args[:2] == ["verify-tag", "--raw"]: return "[GNUPG:] VALIDSIG AABBCCDDEEFF00112233445566778899AABBCCDD"
        if args[:1] == ["rev-parse"]: return "a" * 40
        if args[:1] == ["for-each-ref"]: return "Sigma-Receipt-Rollout-At: 2026-01-01T00:00:00Z\nSigma-Receipt-Manifest-SHA256: " + "a" * 64
        raise AssertionError(args)
    release_manifest.verify_signed_tag("repo", tag_ref, ["aabbccddeeff00112233445566778899aabbccdd"], git)
    assert [args for args in seen if args[:1] == ["for-each-ref"]][0][1] == tag_ref


def test_release_manifest_write_once_refuses_replacement(tmp_path):
    manifest = tmp_path / "releases" / "receipt-publication-v1.json"
    assert release_manifest.write_once(manifest, b"canonical\n") is True
    assert release_manifest.write_once(manifest, b"canonical\n") is False
    with pytest.raises(ValueError, match="immutable"):
        release_manifest.write_once(manifest, b"replacement\n")


def test_publish_to_ledger_branch_commits_and_pushes_only_the_canonical_path(tmp_path):
    repo = tmp_path; (repo / "releases").mkdir()
    facts = {"canonical_repository_id": "R_1", "release_tag": "v1.2.0",
             "release_commit_sha": "b" * 40, "receipt_publication_rollout_at": "2026-01-01T00:00:00Z"}
    digest = release_manifest.payload_digest(release_manifest.payload(facts)); calls = []; staged = []
    def git(command):
        calls.append(command); args = command[3:]
        if args[:2] == ["verify-tag", "--raw"]: return "[GNUPG:] VALIDSIG AABBCCDDEEFF00112233445566778899AABBCCDD"
        if args[:1] == ["rev-parse"]: return "a" * 40 if args[1].endswith("^{tag}") else "b" * 40
        if args[:1] == ["for-each-ref"]: return "Sigma-Receipt-Rollout-At: 2026-01-01T00:00:00Z\nSigma-Receipt-Manifest-SHA256: " + digest
        if args[:2] == ["status", "--porcelain"]: return "?? releases/receipt-publication-v1.json"
        if args[:3] == ["branch", "--show-current"]: return "sdlc-ledger"
        if args[:3] == ["diff", "--cached", "--name-only"]: return "releases/receipt-publication-v1.json" if staged else ""
        if args[:1] == ["add"]: staged.append(True); return ""
        return ""
    result = release_manifest.publish_to_ledger_branch(repo, "R_1", "v1.2.0", ["aabbccddeeff00112233445566778899aabbccdd"], git)
    assert result["published"] is True
    assert any(call[3:4] == ["commit"] for call in calls)
    assert any(call[3:4] == ["push"] for call in calls)
