"""Supply-chain security gates validation tests for ODP-PGAP-SUPPLY-001."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert (
        (major == 8 and minor == 5 and patch >= 10)
        or (major == 8 and minor == 4 and patch >= 38)
        or (major > 8)
    ), f"PostCSS version {version} is vulnerable"


def test_npm_audit_passes() -> None:
    res = subprocess.run(
        ["npm", "audit", "--omit=dev", "--audit-level=high"], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode == 0, f"npm audit failed with output:\n{res.stdout}\n{res.stderr}"


def test_pip_audit_passes() -> None:
    venv_bin = str(ROOT / ".venv/bin")
    home_dir = Path.home()
    uv_path = shutil.which("uv") or shutil.which("uv", path=f"{venv_bin}:{home_dir}/.local/bin:{home_dir}/.cargo/bin:/usr/local/bin")
    pip_audit_path = shutil.which("pip-audit") or shutil.which("pip-audit", path=venv_bin)
    if uv_path:
        cmd = [uv_path, "run", "--with", "pip-audit", "pip-audit", "--local"]
    elif pip_audit_path:
        cmd = [pip_audit_path, "--local"]
    else:
        pytest.fail("Neither 'uv' nor 'pip-audit' executable found in PATH; missing security scanner is an infrastructure failure")
    res = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"pip-audit failed with output:\n{res.stdout}\n{res.stderr}"


def test_secrets_scan_passes() -> None:
    res = subprocess.run(
        [str(ROOT / "scripts/security/secret_scan.py")], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode == 0, f"Secret scanning failed with output:\n{res.stdout}"


def test_sast_scan_passes() -> None:
    res = subprocess.run(
        [str(ROOT / "scripts/security/sast_scan.py")], cwd=ROOT, capture_output=True, text=True
    )
    assert res.returncode == 0, f"SAST scan failed with output:\n{res.stdout}"


def test_generate_sbom_cli_help() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/generate_sbom.py"), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"generate_sbom.py --help failed with output:\n{res.stderr}"
    assert "--image-digest" in res.stdout
    assert "--release-digest" in res.stdout
    assert "--check-policy" in res.stdout
    assert "--readback" in res.stdout


def test_sbom_and_provenance_present_and_valid() -> None:
    sbom_path = ROOT / "docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json"
    assert sbom_path.exists(), "SBOM JSON file must be generated"

    data = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert data.get("bomFormat") == "CycloneDX"
    assert data.get("specVersion") == "1.5"
    assert len(data.get("components", [])) > 0

    # Verify metadata properties (provenance and attestations)
    metadata = data.get("metadata", {})
    properties = {p["name"]: p["value"] for p in metadata.get("properties", [])}
    assert "git-sha" in properties
    assert "sbom-content-digest" in properties
    assert "image-digest" in properties
    assert "release-digest" in properties
    assert "policy-status" in properties
    assert properties["policy-status"] == "PASSED"
    assert properties["image-digest"] == "UNBOUND" or (properties["image-digest"].startswith("sha256:") and len(properties["image-digest"]) == 71)
    assert properties["release-digest"] == "UNBOUND" or (properties["release-digest"].startswith("sha256:") and len(properties["release-digest"]) == 71)

    # Verify CycloneDX 1.5 extended component fields (supplier, licenses, hashes)
    comp = data["components"][0]
    assert "supplier" in comp
    assert "licenses" in comp
    assert "hashes" in comp
    assert "purl" in comp

    # Verify dependency graph
    assert "dependencies" in data
    assert len(data["dependencies"]) > 0

    # Fail closed check: verify committed sbom matches current lockfiles (B5)
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom as current_generate_sbom

    current_sbom = current_generate_sbom()
    assert current_sbom.get("components") == data.get("components"), (
        "Committed sbom.json is stale and does not match the active package-lock.json or uv.lock. "
        "Run scripts/security/generate_sbom.py to regenerate it."
    )


def test_third_party_notices_present_and_valid() -> None:
    notices_path = ROOT / "THIRD_PARTY_NOTICES"
    assert notices_path.exists(), "THIRD_PARTY_NOTICES file must exist"
    content = notices_path.read_text(encoding="utf-8")
    assert "# THIRD PARTY NOTICES" in content
    assert "Total cataloged components:" in content


def test_sbom_readback_cli() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/generate_sbom.py"), "--readback"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"--readback failed with output:\n{res.stderr}"
    assert "CycloneDX SBOM Readback" in res.stdout
    assert "Image Digest:" in res.stdout
    assert "Release Digest:" in res.stdout
    assert "Policy Status: PASSED" in res.stdout


def test_license_policy_fail_closed_negative() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import check_license_policy, generate_sbom

    sbom = generate_sbom()
    # Inject a component with a denied GPL-3.0 license
    sbom["components"].append({
        "name": "vulnerable-forbidden-lib",
        "version": "1.0.0",
        "purl": "pkg:npm/vulnerable-forbidden-lib@1.0.0",
        "licenses": [{"license": {"id": "GPL-3.0"}}]
    })

    is_passed, violations = check_license_policy(sbom)
    assert not is_passed, "License policy gate must fail closed when a GPL-3.0 license is present without exemption"
    assert any("GPL-3.0" in v for v in violations)


def test_sign_images_script_executable() -> None:
    script_path = ROOT / "scripts/security/sign_images.sh"
    assert script_path.exists()
    assert (script_path.stat().st_mode & 0o111) != 0, "sign_images.sh must be executable"



# --- Negative tests verifying that the supply-chain security gates fail closed (B7) ---


def test_stale_lockfiles_rejected_negative(tmp_path: Path) -> None:
    venv_bin = str(ROOT / ".venv/bin")
    home_dir = Path.home()
    uv_path = shutil.which("uv") or shutil.which("uv", path=f"{venv_bin}:{home_dir}/.local/bin:{home_dir}/.cargo/bin:/usr/local/bin")
    if not uv_path:
        pytest.fail("'uv' executable not found in PATH; missing security scanner is an infrastructure failure")
    # Copy pyproject.toml and uv.lock to a temporary directory
    shutil.copy(ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    shutil.copy(ROOT / "uv.lock", tmp_path / "uv.lock")

    # Modify pyproject.toml in the tmp dir to add a dependency
    pyproject_path = tmp_path / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")
    modified_content = content.replace(
        "dependencies = [", 'dependencies = [\n    "nonexistent-test-package-xyz>=1.0.0",'
    )
    pyproject_path.write_text(modified_content, encoding="utf-8")

    # Run uv lock --check in the tmp directory; it should fail
    res = subprocess.run([uv_path, "lock", "--check"], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode != 0, "uv lock --check should have failed for a stale lockfile"


def test_generated_client_drift_rejected_negative() -> None:
    index_path = ROOT / "packages/openapi-client/src/index.ts"
    if index_path.exists():
        original_content = index_path.read_text(encoding="utf-8")
        try:
            # Append a syntax / type error
            index_path.write_text(
                original_content + "\nconst drift_test_const: number = 'breaking_type_drift';\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["npm", "run", "typecheck", "--workspace=@oday-plus/openapi-client"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            assert res.returncode != 0, (
                "Typecheck should fail when openapi client has type drift/errors"
            )
        finally:
            index_path.write_text(original_content, encoding="utf-8")


def test_vulnerable_fixtures_rejected_negative(tmp_path: Path) -> None:
    # Create a requirements file with a known vulnerable library version
    req_file = tmp_path / "requirements-vulnerable.txt"
    req_file.write_text("urllib3==1.26.15\n", encoding="utf-8")

    venv_bin = str(ROOT / ".venv/bin")
    home_dir = Path.home()
    uv_path = shutil.which("uv") or shutil.which("uv", path=f"{venv_bin}:{home_dir}/.local/bin:{home_dir}/.cargo/bin:/usr/local/bin")
    pip_audit_path = shutil.which("pip-audit") or shutil.which("pip-audit", path=venv_bin)
    if uv_path:
        cmd = [uv_path, "run", "--with", "pip-audit", "pip-audit", "-r", str(req_file)]
    elif pip_audit_path:
        cmd = [pip_audit_path, "-r", str(req_file)]
    else:
        pytest.fail("Neither 'uv' nor 'pip-audit' executable found in PATH; missing security scanner is an infrastructure failure")

    # Run pip-audit on requirements-vulnerable.txt
    res = subprocess.run(
        cmd,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, "pip-audit should fail when scanning a vulnerable fixture"


def test_unsigned_images_rejected_negative() -> None:
    # Run sign_images.sh verify on a bogus image name in CI mode and expect non-zero exit code
    script_path = ROOT / "scripts/security/sign_images.sh"
    res = subprocess.run(
        [
            "env",
            "CI=true",
            str(script_path),
            "verify",
            "ghcr.io/totally/nonexistent-image@sha256:0000000000000000000000000000000000000000000000000000000000000000",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, (
        "Verification of unsigned/nonexistent image should fail with non-zero exit code"
    )


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
    assert current_sbom.get("components") != data.get("components"), (
        "Drift check must fail when components list is tampered with"
    )


def test_leaked_test_secrets_rejected_negative() -> None:
    # Create temporary files inside the workspace to avoid pytest tmp path containing the word "test"
    test_dir = ROOT / "tests" / "security" / "tmp_test_secrets"
    test_dir.mkdir(parents=True, exist_ok=True)

    non_test_dir = ROOT / "apps" / "api" / "tmp_secrets"
    non_test_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Case A: Leaked AWS Key without pragma
        secret_file_a = test_dir / "test_secret_leak_no_pragma.py"
        secret_file_a.write_text(
            'AWS_KEY = "AKIA1234567890ABCDEF"\n',  # pragma: allowlist-secret
            encoding="utf-8",
        )

        sys.path.insert(0, str(ROOT))
        from scripts.security.secret_scan import scan_file

        violations_a = scan_file(secret_file_a)
        assert len(violations_a) > 0, "Should detect AWS key leak without pragma"

        # Case B: Leaked AWS Key with old bypass '# approved'
        secret_file_b = test_dir / "test_secret_leak_old_bypass.py"
        secret_file_b.write_text(
            'AWS_KEY = "AKIA1234567890ABCDEF"  # approved\n',  # pragma: allowlist-secret
            encoding="utf-8",
        )
        violations_b = scan_file(secret_file_b)
        assert len(violations_b) > 0, (
            "Should detect AWS key leak even with legacy '# approved' bypass"
        )

        # Case C: Leaked AWS Key with pragma
        secret_file_c = test_dir / "test_secret_leak_with_pragma.py"
        secret_file_c.write_text(
            'AWS_KEY = "AKIA1234567890ABCDEF"  # pragma: allowlist-secret\n', encoding="utf-8"
        )  # pragma: allowlist-secret
        violations_c = scan_file(secret_file_c)
        assert len(violations_c) == 0, (
            "Should bypass AWS key leak if pragma allowlist is present in test path"
        )

        # Case D: Leaked AWS Key with pragma in a NON-test path
        secret_file_d = non_test_dir / "prod_file.py"
        secret_file_d.write_text(
            'AWS_KEY = "AKIA1234567890ABCDEF"  # pragma: allowlist-secret\n', encoding="utf-8"
        )  # pragma: allowlist-secret
        violations_d = scan_file(secret_file_d)
        assert len(violations_d) > 0, (
            "Should reject secrets even with pragma if not in test/fixture/mock path"
        )
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        shutil.rmtree(non_test_dir, ignore_errors=True)


def test_composite_and_unclassifiable_licenses_fail_closed() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import check_license_policy, generate_sbom

    sbom = generate_sbom(
        image_digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
        release_digest="sha256:0000000000000000000000000000000000000000000000000000000000000000",
    )
    test_cases = [
        ("(AGPL-3.0)", False),
        ("GPL-3.0 OR MIT", False),
        ("GPL-2.0 WITH Classpath-exception-2.0", False),
        ("SEE LICENSE IN LICENSE.md", False),
        ("UNKNOWN", False),
        ("MIT OR Apache-2.0", True),
        ("Apache-2.0 AND LGPL-3.0-or-later", True),
    ]

    for lic_expr, expected_pass in test_cases:
        test_sbom = json.loads(json.dumps(sbom))
        test_sbom["components"].append({
            "name": f"test-package-{hash(lic_expr)}",
            "version": "1.0.0",
            "purl": f"pkg:npm/test-package-{hash(lic_expr)}@1.0.0",
            "licenses": [{"license": {"name": lic_expr}}]
        })
        is_passed, _ = check_license_policy(test_sbom, require_digests=True)
        assert is_passed == expected_pass, f"License expression '{lic_expr}' expected pass={expected_pass}, got {is_passed}"


def test_sbom_verify_cli_no_mutation() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/generate_sbom.py"), "--verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"--verify failed with output:\n{res.stderr}"
    assert "SBOM verification PASSED" in res.stdout


def test_unbound_digests_fail_closed() -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import check_license_policy, generate_sbom

    sbom_unbound = generate_sbom()  # No image/release digest passed
    is_passed, violations = check_license_policy(sbom_unbound, require_digests=True)
    assert not is_passed, "check_license_policy with require_digests=True must fail closed when image/release digest is missing"
    assert any("Image digest is missing" in v for v in violations)
    assert any("Release digest is missing" in v for v in violations)


def test_missing_policy_file_raises_error(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_policy_path = sbom_mod.POLICY_PATH
    try:
        sbom_mod.POLICY_PATH = tmp_path / "nonexistent_policy.json"
        with pytest.raises(FileNotFoundError):
            sbom_mod.load_license_policy()
    finally:
        sbom_mod.POLICY_PATH = orig_policy_path


def test_check_notices_cli(tmp_path: Path) -> None:
    # Must specify --output to a tmp path so pytest does not mutate committed docs/evidence/completion/ODP-PGAP-SUPPLY-001/sbom.json
    dummy_output = tmp_path / "sbom.json"
    res = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/security/generate_sbom.py"),
            "--output",
            str(dummy_output),
            "--check-notices",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"--check-notices failed with output:\n{res.stderr}"


def test_deploy_script_digest_fail_closed() -> None:
    script_path = ROOT / "scripts/deploy_cloud_run_waji.sh"
    assert script_path.exists()
    content = script_path.read_text(encoding="utf-8")
    assert "exit 1" in content
    assert "Failed to resolve valid sha256 image digest" in content
    assert "Failed to resolve valid sha256 release attestation digest" in content
    assert "cosign-signed" not in content, "Deploy script must not fall back to synthetic release digest"


def test_sbom_readback_digest_assertion(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom, readback_sbom

    sbom_file = tmp_path / "sbom.json"
    img_dig = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    rel_dig = "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    data = generate_sbom(image_digest=img_dig, release_digest=rel_dig)
    sbom_file.write_text(json.dumps(data), encoding="utf-8")

    # Correct expected digests: must return 0
    code = readback_sbom(sbom_file, expected_image_digest=img_dig, expected_release_digest=rel_dig)
    assert code == 0

    # Wrong expected image digest: must fail closed with exit code 1
    code_bad_img = readback_sbom(
        sbom_file,
        expected_image_digest="sha256:9999999999999999999999999999999999999999999999999999999999999999",
        expected_release_digest=rel_dig,
    )
    assert code_bad_img == 1

    # Wrong expected release digest: must fail closed with exit code 1
    code_bad_rel = readback_sbom(
        sbom_file,
        expected_image_digest=img_dig,
        expected_release_digest="sha256:9999999999999999999999999999999999999999999999999999999999999999",
    )
    assert code_bad_rel == 1


def test_dependency_graph_tampering_alters_sbom_digest(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import compute_sbom_digest, generate_sbom

    sbom_a = generate_sbom()
    digest_a = next(p["value"] for p in sbom_a["metadata"]["properties"] if p["name"] == "sbom-content-digest")

    # Modify dependency graph
    sbom_b = json.loads(json.dumps(sbom_a))
    if sbom_b.get("dependencies"):
        sbom_b["dependencies"].append({"ref": "pkg:generic/tampered@1.0.0", "dependsOn": []})

    # Invoke production verifier helper to compute tampered digest
    _, _, digest_b = compute_sbom_digest(sbom_b["components"], sbom_b["dependencies"])

    assert digest_a != digest_b, "Graph tampering must alter sbom-content-digest"


def test_vulnerability_audit_script_passes() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/vulnerability_scan.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"vulnerability_scan.py failed with output:\n{res.stderr}\n{res.stdout}"


def test_ai_approver_rejection_negative(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    # Create a temporary exemptions file with an AI agent approver
    bad_exemptions = tmp_path / "license_exemptions.json"
    bad_exemptions.write_text(
        json.dumps({
            "exemptions": [
                {
                    "package_name": "some-unapproved-package",
                    "purl": "pkg:npm/some-unapproved-package@1.0.0",
                    "reason": "AI approved test",
                    "approved_by": "Antigravity5"
                }
            ]
        }),
        encoding="utf-8",
    )

    orig_path = sbom_mod.EXEMPTIONS_PATH
    try:
        sbom_mod.EXEMPTIONS_PATH = bad_exemptions
        _, _, _, _, _, ex_violations = sbom_mod.load_license_policy()
        assert len(ex_violations) > 0
        assert any("AI agent names cannot serve as legal approvers" in v for v in ex_violations)
    finally:
        sbom_mod.EXEMPTIONS_PATH = orig_path


def test_verify_sbom_rejects_tampered_graph(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom, verify_sbom

    tampered_sbom_path = tmp_path / "tampered_sbom.json"
    sbom = generate_sbom()
    # Tamper dependency graph
    sbom["dependencies"].append({"ref": "pkg:generic/tampered-node@1.0.0", "dependsOn": []})
    tampered_sbom_path.write_text(json.dumps(sbom), encoding="utf-8")

    code = verify_sbom(tampered_sbom_path)
    assert code == 1, "verify_sbom must reject a tampered dependency graph"
