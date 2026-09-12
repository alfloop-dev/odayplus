"""The manifest CLI must never bless a manifest it has not actually cleared.

`release_manifest.py` is quoted as the first verifier command in the release
evidence README, so whatever it prints is what an auditor records.  A blocked
candidate that produced a success verdict here would be exactly the fake green
light the release gates exist to remove, which is why these tests lock the
exit code and the absence of any success wording rather than the prose.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from delivery_toolchain.release.release_manifest import (
    build_sources_off_attestation,
    compute_manifest_digest,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "delivery_toolchain/release/release_manifest.py"
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
# Derived from the committed manifest: the candidate is rebound every time a
# build publishes a new one, and a pinned literal would fail the suite for a
# rebind rather than for a CLI regression.
CANDIDATE_SHA = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["candidate_sha"]

# Word-boundary matched so that "INVALID:" does not read as a success verdict.
SUCCESS_WORDING = re.compile(r"\bPASS(ED)?\b|\bVALID\b|is valid")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


def write_manifest(tmp_path: Path, manifest: dict, name: str) -> Path:
    assert validate_manifest(manifest) == []
    path = tmp_path / name
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001: borrow the identity, never
# the posture.
#
# These fixtures deliberately start from the committed manifest so a candidate
# rebind does not require a test edit. What they must not inherit is the release
# *posture*: `sources_off_attestation` and `initial_release_recovery` each
# describe one specific release, and a fixture that then declares a rollback
# binding, or empties `components`, is describing a manifest that cannot exist --
# the validator rejects it for the collision rather than for the thing under
# test. Dropping the posture fields is what makes the fixture independent.
# Downgrading the fixture to `schema_version: 1` would make the same symptom go
# away by moving it to a version the v2 posture rules do not police, which hides
# the collision instead of removing it.
POSTURE_FIELDS = ("sources_off_attestation", "initial_release_recovery")


def manifest_identity() -> dict:
    """The committed manifest reduced to identity: no posture, no verdict."""

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    for field in POSTURE_FIELDS:
        manifest.pop(field, None)
    manifest.pop("blockers", None)
    return manifest


def rollback_binding(data_contract_digest: str) -> dict:
    """The previous approved release this fixture would roll back to."""

    return {
        "release_id": "odp-prev-001",
        "candidate_sha": "0" * 40,
        "manifest_digest": "sha256:" + "e" * 64,
        "components": {
            "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "f" * 64},
            "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "1" * 64},
        },
        "data_snapshot": {
            "id": "snap-prev-001",
            "uri": "gs://odayplus-snapshots/masked/snap-prev-001.tar.gz",
            "object_generation": 122,
            "content_sha256": "sha256:" + "2" * 64,
            "data_contract_digest": data_contract_digest,
            "masked": True,
        },
    }


def masked_snapshot(data_contract_digest: str) -> dict:
    return {
        "id": "snap-ready-001",
        "uri": "gs://odayplus-snapshots/masked/snap-ready-001.tar.gz",
        "object_generation": 123,
        "content_sha256": "sha256:" + "d" * 64,
        "data_contract_digest": data_contract_digest,
        "masked": True,
    }


def ready_manifest(tmp_path: Path) -> Path:
    """Write a synthetic admissible manifest for the positive-path assertion."""

    manifest = manifest_identity()
    manifest["release_status"] = "ready"
    # Synthetic references keep the positive path independent of whichever
    # artifact the current candidate happens to have published.
    manifest["components"] = {
        "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64}
    }
    manifest["sbom_refs"] = ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64]
    manifest["signature_refs"] = ["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64]
    manifest["data_snapshot"] = masked_snapshot(manifest["data_contract_digest"])
    manifest["rollback_release"] = rollback_binding(manifest["data_contract_digest"])
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return write_manifest(tmp_path, manifest, "ready-manifest.json")


BLOCKERS = [
    {
        "id": "TEST-BLOCKER-001",
        "severity": "P0",
        "reason": "Synthetic blocker; this candidate never produced an image.",
        "evidence_ref": "docs/evidence/gates/README.md",
    },
    {
        "id": "TEST-BLOCKER-002",
        "severity": "P0",
        "reason": "Synthetic blocker; no Cosign signature exists for this candidate.",
        "evidence_ref": "docs/evidence/gates/README.md",
    },
]


def blocked_manifest(tmp_path: Path) -> Path:
    """Write a synthetic blocked manifest for the fail-closed assertions.

    The committed manifest tracks the candidate of the day and flips between
    ``ready`` and ``blocked`` as builds land, so deriving the blocked subject
    here keeps these assertions about the CLI's refusal wording rather than
    about today's release state.
    """

    manifest = manifest_identity()
    manifest["release_status"] = "blocked"
    manifest["components"] = {}
    manifest["sbom_refs"] = []
    manifest["signature_refs"] = []
    # A blocked release still records what it would have rolled back to; the
    # bindings are what make it reviewable rather than merely refused.
    manifest["data_snapshot"] = masked_snapshot(manifest["data_contract_digest"])
    manifest["rollback_release"] = rollback_binding(manifest["data_contract_digest"])
    manifest["blockers"] = BLOCKERS
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return write_manifest(tmp_path, manifest, "blocked-manifest.json")


def test_blocked_manifest_exits_non_zero_without_success_wording(tmp_path: Path) -> None:
    path = blocked_manifest(tmp_path)

    result = run_cli("--manifest", str(path), "--expected-sha", CANDIDATE_SHA)

    assert result.returncode != 0
    found = SUCCESS_WORDING.search(result.stdout)
    assert found is None, f"blocked manifest must not print {found.group(0)!r}"
    assert "BLOCKED:" in result.stdout


def test_blocked_manifest_reports_each_recorded_blocker(tmp_path: Path) -> None:
    path = blocked_manifest(tmp_path)

    result = run_cli("--manifest", str(path))

    assert result.returncode != 0
    assert "release_status='ready'" in result.stdout
    for blocker in BLOCKERS:
        assert blocker["id"] in result.stdout


def test_structure_only_never_reports_a_deployable_verdict(tmp_path: Path) -> None:
    result = run_cli("--manifest", str(blocked_manifest(tmp_path)), "--structure-only")

    # Structural checking still succeeds on a blocked manifest -- that is the
    # whole point of keeping the blocked record hashable and reviewable.
    assert result.returncode == 0
    assert "STRUCTURE-OK:" in result.stdout
    assert "Release admission was NOT evaluated" in result.stdout
    assert "ADMISSIBLE:" not in result.stdout
    assert SUCCESS_WORDING.search(result.stdout) is None


def test_ready_manifest_reports_admissible(tmp_path: Path) -> None:
    path = ready_manifest(tmp_path)

    result = run_cli("--manifest", str(path), "--expected-sha", CANDIDATE_SHA)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ADMISSIBLE:" in result.stdout
    assert "BLOCKED:" not in result.stdout


def test_committed_manifest_verifies_against_its_own_candidate() -> None:
    """The committed manifest must survive the command an auditor actually runs.

    ``--structure-only`` is the read-only form: it re-derives the manifest digest
    from the file and binds it to the candidate SHA, without expressing any view
    on whether the release may be deployed.
    """

    result = run_cli(
        "--manifest", str(MANIFEST_PATH), "--expected-sha", CANDIDATE_SHA, "--structure-only"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STRUCTURE-OK:" in result.stdout


def test_missing_manifest_fails_closed(tmp_path: Path) -> None:
    result = run_cli("--manifest", str(tmp_path / "absent.json"))

    assert result.returncode != 0
    assert "INVALID:" in result.stdout
    assert SUCCESS_WORDING.search(result.stdout) is None


def test_candidate_sha_mismatch_fails_closed() -> None:
    result = run_cli("--manifest", str(MANIFEST_PATH), "--expected-sha", "0" * 40)

    assert result.returncode != 0
    assert "INVALID:" in result.stdout
    assert "candidate_sha" in result.stdout


def sources_off_manifest(tmp_path: Path) -> Path:
    """Write a synthetic admissible sources-off manifest."""
    manifest = manifest_identity()
    manifest["release_status"] = "ready"
    manifest["components"] = {
        "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64},
        "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "b" * 64},
        "worker": {"image": "registry.example.invalid/odayplus/worker@sha256:" + "c" * 64},
        "scheduler": {"image": "registry.example.invalid/odayplus/scheduler@sha256:" + "d" * 64},
    }
    manifest["sbom_refs"] = ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "e" * 64]
    manifest["signature_refs"] = ["oci://registry.example.invalid/odayplus/api@sha256:" + "f" * 64]
    manifest["sources_off_attestation"] = build_sources_off_attestation(
        candidate_sha=manifest["candidate_sha"],
        components=manifest["components"],
        source_policy_digest=manifest["source_policy_digest"],
        provider_mode="disabled",
        sources_inventory=[
            {"source_id": sid, "status": "disabled", "credentials_present": False, "public_egress": "denied"}
            for sid in [
                "store_master_snapshot", "machine_master_snapshot", "machine_cycle_event",
                "machine_status_event", "transaction_event", "price_schedule_snapshot",
                "maintenance_work_order_event", "customer_service_case_event", "poi_snapshot",
                "geocode_result_snapshot", "admin_boundary_snapshot", "listing_raw_snapshot",
                "competitor_store_snapshot", "demographics_snapshot", "weather_daily_snapshot",
                "store_opening_authority_snapshot",
            ]
        ],
    )
    manifest["rollback_release"] = {
        "release_id": "odp-prev-001",
        "candidate_sha": "0" * 40,
        "manifest_digest": "sha256:" + "1" * 64,
        "components": {
            "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "2" * 64},
            "web": {"image": "registry.example.invalid/odayplus/web@sha256:" + "3" * 64},
        },
        "sources_off_attestation": {
            "binding_digest": "sha256:" + "4" * 64,
        },
    }
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return write_manifest(tmp_path, manifest, "sources-off-manifest.json")


def test_sources_off_manifest_structure_only_without_pythonpath(tmp_path: Path) -> None:
    """Invoking CLI with env -u PYTHONPATH must successfully load contract verifiers."""
    path = sources_off_manifest(tmp_path)
    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}

    proc = subprocess.run(
        [sys.executable, str(CLI), "--manifest", str(path), "--structure-only"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=clean_env,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STRUCTURE-OK:" in proc.stdout
    assert "cannot load Terraform egress contract verifier" not in proc.stdout


def test_sources_off_manifest_corrupted_contract_fails_closed_without_pythonpath(tmp_path: Path) -> None:
    """Contract verifier errors must still fail closed when invoked without PYTHONPATH."""
    manifest = json.loads(sources_off_manifest(tmp_path).read_text(encoding="utf-8"))
    manifest["sources_off_attestation"]["egress_evidence"]["firewall_egress"] = "allow-all"
    manifest["sources_off_attestation"]["binding_digest"] = "sha256:" + "0" * 64
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    corrupted_path = tmp_path / "corrupted-sources-off.json"
    corrupted_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    clean_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, str(CLI), "--manifest", str(corrupted_path), "--structure-only"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=clean_env,
        check=False,
    )

    assert proc.returncode != 0
    assert "INVALID:" in proc.stdout
    assert "egress_evidence" in proc.stdout
