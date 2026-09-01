"""The build-once artifact handoff the deploy phase is dispatched with.

Two properties matter here and nothing else can supply them:

* the handoff is *complete* -- an image reference without a signature or an SBOM
  reference is not a weaker manifest, it is one that must not be written; and
* the handoff is *reproducible* -- a lease is issued against `manifest_digest`,
  so re-running the build phase for the same SHA has to produce the same digest
  or the lease silently stops verifying.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from delivery_toolchain.release.build_release_handoff import (
    HANDOFF_COMPONENTS,
    HandoffError,
    build_handoff,
    main,
    resolve_created_at,
)
from delivery_toolchain.release.release_manifest import (
    build_release_manifest,
    compute_data_contract_digest,
    compute_manifest_digest,
    validate_manifest,
    validate_release_admission,
)

ROOT = Path(__file__).resolve().parents[2]
SHA = "b" * 40
CREATED_AT = "2026-08-26T12:00:00+00:00"

REPO = "asia-east1-docker.pkg.dev/odayplus/oday-plus-dev"


def ref(name: str, fill: str) -> str:
    return f"{REPO}/{name}@sha256:{fill * 64}"


def components(**overrides: str) -> dict[str, str]:
    built = {
        "api": ref("api", "1"),
        "web": ref("web", "2"),
        "worker": ref("worker", "3"),
        "scheduler": ref("scheduler", "4"),
    }
    built.update(overrides)
    return built


def valid_snapshot() -> dict:
    return {
        "id": "snap-handoff-001",
        "uri": "gs://odayplus-snapshots/masked/snap-handoff-001.tar.gz",
        "content_sha256": "sha256:" + "7" * 64,
        "data_contract_digest": compute_data_contract_digest(root=ROOT),
        "masked": True,
    }


def valid_rollback_summary(candidate_sha: str) -> dict:
    older_sha = "9" * 40 if candidate_sha != "9" * 40 else "8" * 40
    return {
        "release_id": "odp-older-001",
        "candidate_sha": older_sha,
        "manifest_digest": "sha256:" + "8" * 64,
        "components": {
            "api": {"image": ref("api", "a")},
            "web": {"image": ref("web", "b")},
        },
        "data_snapshot": {
            "id": "snap-older-001",
            "uri": "gs://odayplus-snapshots/masked/snap-older-001.tar.gz",
            "content_sha256": "sha256:" + "c" * 64,
            "data_contract_digest": compute_data_contract_digest(root=ROOT),
            "masked": True,
        },
    }


def valid_rollback(current_sha: str = SHA, release_id: str = "odp-prev-001") -> dict:
    prev_sha = "0" * 40 if current_sha != "0" * 40 else "9" * 40
    return build_release_manifest(
        release_id=release_id,
        candidate_sha=prev_sha,
        components={
            "api": {"image": ref("api", "a")},
            "web": {"image": ref("web", "b")},
            "worker": {"image": ref("worker", "c")},
            "scheduler": {"image": ref("scheduler", "d")},
        },
        sbom_refs=[ref("api", "5")],
        signature_refs=[ref("api", "6")],
        created_at="2026-08-25T12:00:00+00:00",
        created_by_workflow=(
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@"
            + prev_sha
        ),
        data_snapshot={
            "id": "snap-prev-001",
            "uri": "gs://odayplus-snapshots/masked/snap-prev-001.tar.gz",
            "content_sha256": "sha256:" + "c" * 64,
            "data_contract_digest": compute_data_contract_digest(root=ROOT),
            "masked": True,
        },
        rollback_release=valid_rollback_summary(prev_sha),
        release_status="ready",
        root=ROOT,
    )


def handoff(**overrides):
    release_sha = overrides.get("release_sha", SHA)
    kwargs = {
        "release_sha": release_sha,
        "components": components(),
        "sbom_refs": [ref("api", "5")],
        "signature_refs": [ref("api", "6")],
        "data_snapshot": valid_snapshot(),
        "rollback_release": valid_rollback(release_sha),
        "created_at": CREATED_AT,
        "created_by_workflow": "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@" + release_sha,
    }
    kwargs.update(overrides)
    return build_handoff(**kwargs)


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------


def test_a_complete_build_produces_an_admissible_manifest() -> None:
    images, manifest = handoff()

    assert images == components()
    assert validate_manifest(manifest, expected_candidate_sha=SHA) == []
    assert validate_release_admission(manifest) == []
    assert manifest["release_status"] == "ready"
    assert manifest["manifest_digest"] == compute_manifest_digest(manifest)


def test_the_manifest_records_that_migration_shares_the_worker_image() -> None:
    """Plan section 5.1: a shared image has to be stated, not inferred."""

    _, manifest = handoff()
    assert manifest["components"]["migration"]["image"] == components()["worker"]
    assert manifest["components"]["migration"]["shares_image_with"] == "worker"


@pytest.mark.parametrize("missing", HANDOFF_COMPONENTS)
def test_an_incomplete_image_set_refuses_to_write_a_handoff(missing: str) -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(components=components(**{missing: ""}))
    assert any(f"缺少 {missing}" in error for error in excinfo.value.errors)


def test_a_mutable_tag_is_not_an_artifact_identity() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(components=components(api=f"{REPO}/api:release-{SHA}"))
    assert any("immutable @sha256 reference" in error for error in excinfo.value.errors)


def test_a_build_with_no_signature_reference_is_not_a_release() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(signature_refs=[])
    assert any("缺少 Cosign 簽章參照" in error for error in excinfo.value.errors)


def test_a_build_with_no_sbom_reference_is_not_a_release() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(sbom_refs=[])
    assert any("缺少 SBOM 參照" in error for error in excinfo.value.errors)


def test_supply_chain_references_must_be_fetchable_digests() -> None:
    """A path or a tag is a claim; a digest is something an auditor can pull."""

    with pytest.raises(HandoffError) as excinfo:
        handoff(sbom_refs=["docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json"])
    assert any("SBOM參照必須是 immutable" in error for error in excinfo.value.errors)


def test_a_branch_name_is_not_an_exact_release_sha() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(release_sha="dev")
    assert any("40 字元小寫 git SHA" in error for error in excinfo.value.errors)


# --------------------------------------------------------------------------
# Reproducibility: an issued lease must survive a re-run of the build phase
# --------------------------------------------------------------------------


def test_rebuilding_the_same_release_reproduces_the_same_manifest_digest() -> None:
    first = handoff()[1]
    second = handoff()[1]
    assert first == second
    assert first["manifest_digest"] == second["manifest_digest"]


def test_the_manifest_identity_carries_no_run_scoped_value() -> None:
    """A run id or wall-clock timestamp would change the digest on every re-run."""

    _, manifest = handoff()
    assert manifest["created_at"] == CREATED_AT
    assert "/actions/runs/" not in manifest["created_by_workflow"]
    assert manifest["created_by_workflow"].endswith(f"@{SHA}")
    assert manifest["release_id"] == f"odp-{SHA[:12]}"


def test_created_at_defaults_to_the_commit_time_not_the_current_time() -> None:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    resolved = resolve_created_at(head, root=ROOT)
    assert resolve_created_at(head, root=ROOT) == resolved

    _, manifest = handoff(release_sha=head, created_at=None, created_by_workflow=None)
    assert manifest["created_at"] == resolved
    assert manifest["candidate_sha"] == head


def test_an_unknown_release_sha_cannot_be_dated() -> None:
    with pytest.raises(HandoffError) as excinfo:
        resolve_created_at("c" * 40, root=ROOT)
    assert any("無法讀取" in error for error in excinfo.value.errors)


def _snapshot_and_rollback_args(tmp_path: Path) -> list[str]:
    snap = valid_snapshot()
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "FIXTURE_PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")
    return [
        "--data-snapshot-id",
        snap["id"],
        "--data-snapshot-uri",
        snap["uri"],
        "--data-snapshot-sha256",
        snap["content_sha256"],
        "--rollback-manifest",
        str(prev_manifest_path),
    ]


def _cli(tmp_path: Path, *extra: str) -> tuple[int, Path, Path]:
    images_path = tmp_path / "runtime-release-images.json"
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    argv = [
        "--release-sha",
        SHA,
        "--created-at",
        CREATED_AT,
        "--images-output",
        str(images_path),
        "--manifest-output",
        str(manifest_path),
        *extra,
    ]
    return main(argv), images_path, manifest_path


def _component_args() -> list[str]:
    args: list[str] = []
    for name, value in components().items():
        args += ["--component", f"{name}={value}"]
    return args


def test_the_cli_writes_both_halves_of_the_handoff(tmp_path: Path) -> None:
    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        *_snapshot_and_rollback_args(tmp_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0

    assert json.loads(images_path.read_text(encoding="utf-8")) == components()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert validate_release_admission(manifest) == []
    assert manifest["data_snapshot"]["masked"] is True
    assert manifest["rollback_release"]["candidate_sha"] != SHA


def test_the_cli_reports_the_manifest_digest_to_the_workflow(tmp_path: Path) -> None:
    github_output = tmp_path / "github-output"
    github_output.touch()
    code, _, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        *_snapshot_and_rollback_args(tmp_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
        "--github-output",
        str(github_output),
    )
    assert code == 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    emitted = dict(
        line.split("=", 1)
        for line in github_output.read_text(encoding="utf-8").splitlines()
        if line
    )
    assert emitted["manifest_digest"] == manifest["manifest_digest"]
    assert emitted["release_id"] == manifest["release_id"]


def test_the_cli_writes_nothing_when_the_build_was_incomplete(tmp_path: Path) -> None:
    """A partial handoff on disk would be indistinguishable from a real one."""

    code, images_path, manifest_path = _cli(
        tmp_path,
        "--component",
        f"api={components()['api']}",
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 1
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_missing_data_snapshot_refuses_to_write_a_handoff() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(data_snapshot=None)
    assert any("缺少 masked data snapshot" in err for err in excinfo.value.errors)


def test_missing_rollback_release_refuses_to_write_a_handoff() -> None:
    with pytest.raises(HandoffError) as excinfo:
        handoff(rollback_release=None)
    assert any("缺少 rollback release" in err for err in excinfo.value.errors)


def test_direct_rollback_summary_is_not_accepted_as_a_previous_manifest() -> None:
    """The direct API must not provide a summary-shaped validation bypass."""

    with pytest.raises(HandoffError) as excinfo:
        handoff(rollback_release=valid_rollback_summary("0" * 40))

    assert any("rollback manifest 無效" in err for err in excinfo.value.errors)
    assert any("missing required field: schema_version" in err for err in excinfo.value.errors)


def test_previous_manifest_with_current_release_id_fails_closed() -> None:
    current_release_id = f"odp-{SHA[:12]}"

    with pytest.raises(HandoffError) as excinfo:
        handoff(
            rollback_release=valid_rollback(release_id=current_release_id),
        )

    assert any("rollback release_id" in err for err in excinfo.value.errors)


def test_rollback_manifest_cli_option(tmp_path: Path) -> None:
    # Build a previous release manifest
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rollback_release"]["candidate_sha"] == "0" * 40
    assert manifest["rollback_release"]["manifest_digest"] == prev_manifest["manifest_digest"]
    assert validate_release_admission(manifest) == []


def test_rollback_release_file_option(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, _, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-release-file",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rollback_release"]["manifest_digest"] == prev_manifest["manifest_digest"]


def test_rollback_manifest_legacy_v1_fails_closed_no_synthetic_snapshot(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40, schema_version=1)
    prev_manifest.pop("data_snapshot", None)
    prev_manifest.pop("rollback_release", None)
    prev_manifest["manifest_digest"] = compute_manifest_digest(prev_manifest)
    v1_path = tmp_path / "V1_RELEASE_MANIFEST.json"
    v1_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(v1_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_rollback_manifest_forged_digest_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest["manifest_digest"] = "sha256:" + "f" * 64
    forged_path = tmp_path / "FORGED_PREV_MANIFEST.json"
    forged_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(forged_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_rollback_manifest_tampered_fields_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest["components"]["api"]["image"] = ref("api", "9")
    tampered_path = tmp_path / "TAMPERED_PREV_MANIFEST.json"
    tampered_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(tampered_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_rollback_manifest_same_candidate_sha_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha=SHA)
    same_sha_path = tmp_path / "SAME_SHA_MANIFEST.json"
    same_sha_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-current-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-current-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(same_sha_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_option(tmp_path: Path) -> None:
    snap = valid_snapshot()
    snap_file = tmp_path / "approved_snapshot.json"
    snap_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["data_snapshot"]["id"] == snap["id"]
    assert manifest["data_snapshot"]["uri"] == snap["uri"]
    assert manifest["data_snapshot"]["content_sha256"] == snap["content_sha256"]
    assert manifest["data_snapshot"]["masked"] is True
    assert validate_release_admission(manifest) == []


def test_data_snapshot_file_missing_masked_fails_closed(tmp_path: Path) -> None:
    snap = valid_snapshot()
    snap.pop("masked")
    snap_file = tmp_path / "missing_masked_snapshot.json"
    snap_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_unmasked_fails_closed(tmp_path: Path) -> None:
    snap = valid_snapshot()
    snap["masked"] = False
    snap_file = tmp_path / "unmasked_snapshot.json"
    snap_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_missing_data_contract_digest_fails_closed(tmp_path: Path) -> None:
    snap = valid_snapshot()
    snap.pop("data_contract_digest")
    snap_file = tmp_path / "missing_digest_snapshot.json"
    snap_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_mismatched_data_contract_digest_fails_closed(tmp_path: Path) -> None:
    snap = valid_snapshot()
    snap["data_contract_digest"] = "sha256:" + "0" * 64
    snap_file = tmp_path / "mismatched_digest_snapshot.json"
    snap_file.write_text(json.dumps(snap, indent=2), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_non_dict_fails_closed(tmp_path: Path) -> None:
    snap_file = tmp_path / "list_snapshot.json"
    snap_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_invalid_json_fails_closed(tmp_path: Path) -> None:
    snap_file = tmp_path / "invalid_json_snapshot.json"
    snap_file.write_text("{not valid json", encoding="utf-8")

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_file_nonexistent_fails_closed(tmp_path: Path) -> None:
    snap_file = tmp_path / "nonexistent_snapshot.json"

    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-file",
        str(snap_file),
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_content_sha_alias_and_hex_normalization(tmp_path: Path) -> None:
    raw_hex = "e" * 64
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-hex-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-hex-001.tar.gz",
        "--data-snapshot-content-sha",
        raw_hex,
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["data_snapshot"]["content_sha256"] == f"sha256:{raw_hex}"


def test_unmasked_data_snapshot_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-unmasked-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/unmasked/snap-unmasked-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--data-snapshot-unmasked",
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_data_snapshot_contract_digest_mismatch_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_path = tmp_path / "PREV_RELEASE_MANIFEST.json"
    prev_manifest_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-mismatch-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-mismatch-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--data-snapshot-contract-digest",
        "sha256:" + "0" * 64,
        "--rollback-manifest",
        str(prev_manifest_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()


def test_rollback_manifest_inline_json_string(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest_json = json.dumps(prev_manifest)

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-inline-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-inline-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        prev_manifest_json,
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["rollback_release"]["candidate_sha"] == "0" * 40
    assert manifest["rollback_release"]["manifest_digest"] == prev_manifest["manifest_digest"]
    assert validate_release_admission(manifest) == []


def test_rollback_manifest_missing_component_fails_closed(tmp_path: Path) -> None:
    _, prev_manifest = handoff(release_sha="0" * 40)
    prev_manifest["components"].pop("web", None)
    prev_manifest["manifest_digest"] = compute_manifest_digest(prev_manifest)
    broken_path = tmp_path / "NO_WEB_MANIFEST.json"
    broken_path.write_text(json.dumps(prev_manifest, indent=2), encoding="utf-8")

    code, images_path, manifest_path = _cli(
        tmp_path,
        *_component_args(),
        "--data-snapshot-id",
        "snap-broken-001",
        "--data-snapshot-uri",
        "gs://odayplus-snapshots/masked/snap-broken-001.tar.gz",
        "--data-snapshot-sha256",
        "sha256:" + "f" * 64,
        "--rollback-manifest",
        str(broken_path),
        "--sbom-ref",
        ref("api", "5"),
        "--signature-ref",
        ref("api", "6"),
    )
    assert code != 0
    assert not images_path.exists()
    assert not manifest_path.exists()

