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
CANDIDATE_SHA = "ace4265b5190c00c72846b637fc04850bacec77e"

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


def ready_manifest(tmp_path: Path) -> Path:
    """Write a synthetic admissible manifest for the positive-path assertion."""

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["release_status"] = "ready"
    manifest.pop("blockers", None)
    manifest["sbom_refs"] = ["oci://registry.example.invalid/odayplus/sbom@sha256:" + "b" * 64]
    manifest["signature_refs"] = ["oci://registry.example.invalid/odayplus/api@sha256:" + "c" * 64]
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    assert validate_manifest(manifest) == []

    path = tmp_path / "ready-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_blocked_manifest_exits_non_zero_without_success_wording() -> None:
    result = run_cli("--manifest", str(MANIFEST_PATH), "--expected-sha", CANDIDATE_SHA)

    assert result.returncode != 0
    found = SUCCESS_WORDING.search(result.stdout)
    assert found is None, f"blocked manifest must not print {found.group(0)!r}"
    assert "BLOCKED:" in result.stdout


def test_blocked_manifest_reports_each_recorded_blocker() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    result = run_cli("--manifest", str(MANIFEST_PATH))

    assert result.returncode != 0
    assert "release_status='ready'" in result.stdout
    for blocker in manifest["blockers"]:
        assert blocker["id"] in result.stdout


def test_structure_only_never_reports_a_deployable_verdict() -> None:
    result = run_cli("--manifest", str(MANIFEST_PATH), "--structure-only")

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
