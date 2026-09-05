"""The manifest CLI must never bless a manifest it has not actually cleared.

`release_manifest.py` is quoted as the first verifier command in the release
evidence README, so whatever it prints is what an auditor records.  A blocked
candidate that produced a success verdict here would be exactly the fake green
light the release gates exist to remove, which is why these tests lock the
exit code and the absence of any success wording rather than the prose.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from delivery_toolchain.release.release_manifest import (
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


def ready_manifest(tmp_path: Path) -> Path:
    """Write a synthetic admissible manifest for the positive-path assertion."""

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["schema_version"] = 2
    manifest["release_status"] = "ready"
    manifest.pop("blockers", None)
    # Synthetic references keep the positive path independent of whichever
    # artifact the current candidate happens to have published.
    manifest["components"] = {
        "api": {"image": "registry.example.invalid/odayplus/api@sha256:" + "a" * 64}
    }
    manifest["sbom_refs"] = ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64]
    manifest["signature_refs"] = ["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64]
    manifest["data_snapshot"] = {
        "id": "snap-ready-001",
        "uri": "gs://odayplus-snapshots/masked/snap-ready-001.tar.gz",
        "object_generation": 123,
        "content_sha256": "sha256:" + "d" * 64,
        "data_contract_digest": manifest["data_contract_digest"],
        "masked": True,
    }
    manifest["rollback_release"] = {
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
            "data_contract_digest": manifest["data_contract_digest"],
            "masked": True,
        },
    }
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

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["release_status"] = "blocked"
    manifest["components"] = {}
    manifest["sbom_refs"] = []
    manifest["signature_refs"] = []
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
