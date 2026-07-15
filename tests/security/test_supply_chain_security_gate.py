"""Supply-chain security gates validation tests for ODP-PGAP-SUPPLY-001."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_postcss_advisory_resolved() -> None:
    lockfile_path = ROOT / "package-lock.json"
    assert lockfile_path.exists()

    data = json.loads(lockfile_path.read_text(encoding="utf-8"))
    postcss_info = data.get("packages", {}).get("node_modules/postcss", {})
    assert postcss_info, "postcss should be installed as a dependency"

    version = postcss_info.get("version", "0.0.0")
    major, minor, patch = map(int, version.split("."))
    # PostCSS advisory is fixed in >= 8.5.10 or >= 8.4.38 depending on the backport.
    # We upgraded to 8.5.19, so let's check it's secure.
    assert (major == 8 and minor == 5 and patch >= 10) or (major == 8 and minor == 4 and patch >= 38) or (major > 8), f"PostCSS version {version} is vulnerable"


def test_npm_audit_passes() -> None:
    res = subprocess.run(["npm", "audit", "--audit-level=high"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"npm audit failed with output:\n{res.stdout}\n{res.stderr}"


def test_pip_audit_passes() -> None:
    res = subprocess.run(["uv", "run", "--with", "pip-audit", "pip-audit", "--local"], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"pip-audit failed with output:\n{res.stdout}\n{res.stderr}"


def test_secrets_scan_passes() -> None:
    res = subprocess.run([str(ROOT / "scripts/security/secret_scan.py")], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"Secret scanning failed with output:\n{res.stdout}"


def test_sast_scan_passes() -> None:
    res = subprocess.run([str(ROOT / "scripts/security/sast_scan.py")], cwd=ROOT, capture_output=True, text=True)
    assert res.returncode == 0, f"SAST scan failed with output:\n{res.stdout}"


def test_sbom_and_provenance_present_and_valid() -> None:
    sbom_path = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json"
    assert sbom_path.exists(), "SBOM JSON file must be generated"

    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data.get("bomFormat") == "CycloneDX"
    assert data.get("specVersion") == "1.5"
    assert len(data.get("components", [])) > 0

    # Verify metadata properties (provenance)
    metadata = data.get("metadata", {})
    properties = {p["name"]: p["value"] for p in metadata.get("properties", [])}
    assert "git-sha" in properties
    assert "signed-provenance" in properties
    assert properties["signed-provenance"].startswith("sha256:")


def test_sign_images_script_executable() -> None:
    script_path = ROOT / "scripts/security/sign_images.sh"
    assert script_path.exists()
    assert (script_path.stat().st_mode & 0o111) != 0, "sign_images.sh must be executable"
