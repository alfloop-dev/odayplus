"""Supply-chain security gates validation tests for ODP-PGAP-SUPPLY-001."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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
    assert "sbom-content-digest" in properties
    assert properties["sbom-content-digest"].startswith("sha256:")

    # Fail closed check: verify committed sbom matches current lockfiles (B5)
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom as current_generate_sbom

    current_sbom = current_generate_sbom()
    assert current_sbom.get("components") == data.get("components"), (
        "Committed sbom.json is stale and does not match the active package-lock.json or uv.lock. "
        "Run scripts/security/generate_sbom.py to regenerate it."
    )


def test_sign_images_script_executable() -> None:
    script_path = ROOT / "scripts/security/sign_images.sh"
    assert script_path.exists()
    assert (script_path.stat().st_mode & 0o111) != 0, "sign_images.sh must be executable"


# --- Negative tests verifying that the supply-chain security gates fail closed (B7) ---

def test_stale_lockfiles_rejected_negative(tmp_path: Path) -> None:
    # Copy pyproject.toml and uv.lock to a temporary directory
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(ROOT / "uv.lock", tmp_path / "uv.lock")

    # Modify pyproject.toml in the tmp dir to add a dependency
    pyproject_path = tmp_path / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    modified_content = content.replace(
        'dependencies = [',
        'dependencies = [\n    "nonexistent-test-package-xyz>=1.0.0",'
    )
    pyproject_path.write_text(modified_content, encoding="utf-8")

    # Run uv lock --check in the tmp directory; it should fail
    res = subprocess.run(["uv", "lock", "--check"], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode != 0, "uv lock --check should have failed for a stale lockfile"


def test_generated_client_drift_rejected_negative() -> None:
    index_path = ROOT / "packages/openapi-client/src/index.ts"
    if index_path.exists():
        original_content = index_path.read_text(encoding="utf-8")
        try:
            # Append a syntax / type error
            index_path.write_text(original_content + "\nconst drift_test_const: number = 'breaking_type_drift';\n", encoding="utf-8")
            res = subprocess.run(["npm", "run", "typecheck", "--workspace=@oday-plus/openapi-client"], cwd=ROOT, capture_output=True, text=True)
            assert res.returncode != 0, "Typecheck should fail when openapi client has type drift/errors"
        finally:
            index_path.write_text(original_content, encoding="utf-8")


def test_vulnerable_fixtures_rejected_negative(tmp_path: Path) -> None:
    # Create a requirements file with a known vulnerable library version
    req_file = tmp_path / "requirements-vulnerable.txt"
    req_file.write_text("urllib3==1.26.15\n", encoding="utf-8")

    # Run pip-audit on requirements-vulnerable.txt
    res = subprocess.run(
        ["uv", "run", "--with", "pip-audit", "pip-audit", "-r", str(req_file)],
        cwd=tmp_path, capture_output=True, text=True
    )
    assert res.returncode != 0, "pip-audit should fail when scanning a vulnerable fixture"


def test_unsigned_images_rejected_negative() -> None:
    # Run sign_images.sh verify on a bogus image name in CI mode and expect non-zero exit code
    script_path = ROOT / "scripts/security/sign_images.sh"
    res = subprocess.run(
        ["env", "CI=true", str(script_path), "verify", "ghcr.io/totally/nonexistent-image@sha256:0000000000000000000000000000000000000000000000000000000000000000"],
        cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode != 0, "Verification of unsigned/nonexistent image should fail with non-zero exit code"


def test_invalid_provenance_rejected_negative(tmp_path: Path) -> None:
    # Modify a component in a copy of sbom.json
    sbom_src = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json"
    data = json.loads(sbom_src.read_text(encoding="utf-8"))

    # Modify version of first component
    if data.get("components"):
        data["components"][0]["version"] = "9.9.9"

    # Verify that comparing it to current_generate_sbom fails
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom as current_generate_sbom

    current_sbom = current_generate_sbom()
    assert current_sbom.get("components") != data.get("components"), "Drift check must fail when components list is tampered with"


def test_leaked_test_secrets_rejected_negative() -> None:
    # Create temporary files inside the workspace to avoid pytest tmp path containing the word "test"
    test_dir = ROOT / "tests" / "security" / "tmp_test_secrets"
    test_dir.mkdir(parents=True, exist_ok=True)

    non_test_dir = ROOT / "apps" / "api" / "tmp_secrets"
    non_test_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Case A: Leaked AWS Key without pragma
        secret_file_a = test_dir / "test_secret_leak_no_pragma.py"
        secret_file_a.write_text('AWS_KEY = "AKIA1234567890ABCDEF"\n', encoding="utf-8")  # pragma: allowlist-secret

        sys.path.insert(0, str(ROOT))
        from scripts.security.secret_scan import scan_file

        violations_a = scan_file(secret_file_a)
        assert len(violations_a) > 0, "Should detect AWS key leak without pragma"

        # Case B: Leaked AWS Key with old bypass '# approved'
        secret_file_b = test_dir / "test_secret_leak_old_bypass.py"
        secret_file_b.write_text('AWS_KEY = "AKIA1234567890ABCDEF"  # approved\n', encoding="utf-8")  # pragma: allowlist-secret
        violations_b = scan_file(secret_file_b)
        assert len(violations_b) > 0, "Should detect AWS key leak even with legacy '# approved' bypass"

        # Case C: Leaked AWS Key with pragma
        secret_file_c = test_dir / "test_secret_leak_with_pragma.py"
        secret_file_c.write_text('AWS_KEY = "AKIA1234567890ABCDEF"  # pragma: allowlist-secret\n', encoding="utf-8")  # pragma: allowlist-secret
        violations_c = scan_file(secret_file_c)
        assert len(violations_c) == 0, "Should bypass AWS key leak if pragma allowlist is present in test path"

        # Case D: Leaked AWS Key with pragma in a NON-test path
        secret_file_d = non_test_dir / "prod_file.py"
        secret_file_d.write_text('AWS_KEY = "AKIA1234567890ABCDEF"  # pragma: allowlist-secret\n', encoding="utf-8")  # pragma: allowlist-secret
        violations_d = scan_file(secret_file_d)
        assert len(violations_d) > 0, "Should reject secrets even with pragma if not in test/fixture/mock path"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        shutil.rmtree(non_test_dir, ignore_errors=True)
