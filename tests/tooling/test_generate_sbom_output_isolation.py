"""Regression tests for generate_sbom output isolation (ODP-SBOM-CANDIDATE-OUTPUT-ISOLATION-001)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GENERATE_SBOM_SCRIPT = ROOT / "delivery_toolchain/security/generate_sbom.py"
HISTORICAL_EVIDENCE_DIR = ROOT / "docs/evidence/completion/ODP-OSS-LICENSE-GATE-002"
DEFAULT_EVIDENCE_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001"


def _dir_file_hashes(directory: Path) -> dict[str, str]:
    """Calculate SHA256 hashes for all files in a directory recursively."""
    if not directory.is_dir():
        return {}
    hashes: dict[str, str] = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            rel_name = str(p.relative_to(directory))
            content = p.read_bytes()
            hashes[rel_name] = hashlib.sha256(content).hexdigest()
    return hashes


def _run_sbom_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute generate_sbom.py CLI as a real subprocess."""
    cmd = [sys.executable, str(GENERATE_SBOM_SCRIPT)] + args
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def test_custom_output_creates_target_parent_and_writes_valid_sbom(tmp_path: Path) -> None:
    """Custom --output creates only the target parent directory and writes valid CycloneDX 1.5 JSON."""
    out_dir = tmp_path / "deeply" / "nested" / "output_dir"
    out_file = out_dir / "candidate_sbom.json"
    assert not out_dir.exists(), "Target parent directory should not exist before execution"

    res = _run_sbom_cli(["--output", str(out_file)])
    assert res.returncode == 0, f"generate_sbom failed:\n{res.stdout}\n{res.stderr}"
    assert out_file.is_file(), "Target SBOM file must be created"

    # Verify content validity
    data: dict[str, Any] = json.loads(out_file.read_text(encoding="utf-8"))
    assert data.get("bomFormat") == "CycloneDX"
    assert data.get("specVersion") == "1.5"
    assert isinstance(data.get("components"), list)
    assert len(data["components"]) > 0
    assert isinstance(data.get("dependencies"), list)
    assert len(data["dependencies"]) > 0

    # Verify stdout message does not crash or claim mirroring
    assert str(out_file) in res.stdout or out_file.name in res.stdout
    assert "Mirrored SBOM" not in res.stdout


def test_custom_output_does_not_modify_or_create_historical_evidence(tmp_path: Path) -> None:
    """Generating with --output must never touch, create or overwrite historical evidence receipts."""
    hist_before = _dir_file_hashes(HISTORICAL_EVIDENCE_DIR)
    default_before = _dir_file_hashes(DEFAULT_EVIDENCE_DIR)

    out_file = tmp_path / "sandbox" / "test_sbom.json"
    res = _run_sbom_cli(["--output", str(out_file)])
    assert res.returncode == 0, f"generate_sbom failed:\n{res.stdout}\n{res.stderr}"

    hist_after = _dir_file_hashes(HISTORICAL_EVIDENCE_DIR)
    default_after = _dir_file_hashes(DEFAULT_EVIDENCE_DIR)

    assert (
        hist_before == hist_after
    ), "Historical evidence directory ODP-OSS-LICENSE-GATE-002 was mutated"
    assert (
        default_before == default_after
    ), "Default evidence directory ODP-PGAP-SUPPLY-001 was mutated during custom output"


def test_output_outside_repo_and_display_formatting_does_not_raise(tmp_path: Path) -> None:
    """Absolute paths outside the workspace repo (/tmp) must format cleanly without ValueError."""
    abs_out = tmp_path / "outside_repo_sbom.json"
    res = _run_sbom_cli(["--output", str(abs_out)])
    assert (
        res.returncode == 0
    ), f"generate_sbom failed with outside-repo path:\n{res.stdout}\n{res.stderr}"
    assert f"SBOM successfully generated at {abs_out}" in res.stdout

    # Also test check mode on outside-repo path
    check_res = _run_sbom_cli(["--check", "--output", str(abs_out)])
    assert (
        check_res.returncode == 0
    ), f"generate_sbom --check failed on outside-repo path:\n{check_res.stdout}\n{check_res.stderr}"
    assert f"SBOM at {abs_out} is valid and up to date." in check_res.stdout


def test_check_mode_zero_writes(tmp_path: Path) -> None:
    """--check mode must be strictly read-only: no files created, modified, or touched."""
    target_file = tmp_path / "readonly_check_target.json"
    gen_res = _run_sbom_cli(["--output", str(target_file)])
    assert gen_res.returncode == 0

    target_hash_before = hashlib.sha256(target_file.read_bytes()).hexdigest()
    target_stat_before = target_file.stat()
    hist_before = _dir_file_hashes(HISTORICAL_EVIDENCE_DIR)
    default_before = _dir_file_hashes(DEFAULT_EVIDENCE_DIR)

    check_res = _run_sbom_cli(["--check", "--output", str(target_file)])
    assert check_res.returncode == 0, f"--check failed:\n{check_res.stdout}\n{check_res.stderr}"

    target_hash_after = hashlib.sha256(target_file.read_bytes()).hexdigest()
    target_stat_after = target_file.stat()
    hist_after = _dir_file_hashes(HISTORICAL_EVIDENCE_DIR)
    default_after = _dir_file_hashes(DEFAULT_EVIDENCE_DIR)

    assert target_hash_before == target_hash_after, "Target file modified by --check"
    assert (
        target_stat_before.st_mtime_ns == target_stat_after.st_mtime_ns
    ), "Target file touched by --check"
    assert hist_before == hist_after, "Historical evidence directory touched by --check"
    assert default_before == default_after, "Default evidence directory touched by --check"


def test_check_missing_file_fails_closed(tmp_path: Path) -> None:
    """--check fails closed (exit code 1) when the target SBOM file does not exist."""
    missing_file = tmp_path / "nonexistent_sbom.json"
    res = _run_sbom_cli(["--check", "--output", str(missing_file)])
    assert res.returncode == 1
    assert "SBOM file is missing at" in res.stderr
    assert str(missing_file) in res.stderr


def test_check_corrupted_json_fails_closed(tmp_path: Path) -> None:
    """--check fails closed (exit code 1) when the target SBOM file contains invalid JSON."""
    corrupted_file = tmp_path / "corrupt_sbom.json"
    corrupted_file.write_text('{"incomplete_json": [', encoding="utf-8")

    res = _run_sbom_cli(["--check", "--output", str(corrupted_file)])
    assert res.returncode == 1
    assert "Failed to read committed SBOM at" in res.stderr
    assert str(corrupted_file) in res.stderr


def test_check_stale_components_fails_closed(tmp_path: Path) -> None:
    """--check fails closed when components differ from the current environment."""
    stale_file = tmp_path / "stale_components_sbom.json"
    gen_res = _run_sbom_cli(["--output", str(stale_file)])
    assert gen_res.returncode == 0

    data = json.loads(stale_file.read_text(encoding="utf-8"))
    assert len(data.get("components", [])) > 0
    # Tamper with component version
    data["components"][0]["version"] = "99.99.99-tampered"
    stale_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    check_res = _run_sbom_cli(["--check", "--output", str(stale_file)])
    assert check_res.returncode == 1
    assert "is stale; run delivery_toolchain/security/generate_sbom.py to regenerate." in check_res.stderr


def test_check_stale_dependencies_fails_closed(tmp_path: Path) -> None:
    """--check fails closed when dependency graph differs."""
    stale_file = tmp_path / "stale_deps_sbom.json"
    gen_res = _run_sbom_cli(["--output", str(stale_file)])
    assert gen_res.returncode == 0

    data = json.loads(stale_file.read_text(encoding="utf-8"))
    # Tamper with dependencies graph
    data["dependencies"] = [{"ref": "pkg:tampered@1.0.0", "dependsOn": []}]
    stale_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    check_res = _run_sbom_cli(["--check", "--output", str(stale_file)])
    assert check_res.returncode == 1
    assert "is stale; run delivery_toolchain/security/generate_sbom.py to regenerate." in check_res.stderr


def test_check_stale_release_digests_fails_closed(tmp_path: Path) -> None:
    """--check fails closed when repository release digests metadata property is tampered with."""
    stale_file = tmp_path / "stale_release_digests_sbom.json"
    gen_res = _run_sbom_cli(["--output", str(stale_file)])
    assert gen_res.returncode == 0

    data = json.loads(stale_file.read_text(encoding="utf-8"))
    props = data.get("metadata", {}).get("properties", [])
    for p in props:
        if p.get("name") == "repository-release-digests":
            p["value"] = json.dumps(
                {"alfloop-dev/odayplus": "0000000000000000000000000000000000000000"}
            )
    stale_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    check_res = _run_sbom_cli(["--check", "--output", str(stale_file)])
    assert check_res.returncode == 1
    assert "is stale; run delivery_toolchain/security/generate_sbom.py to regenerate." in check_res.stderr
