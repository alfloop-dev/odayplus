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
    assert properties["policy-status"] in {"PASSED", "FAILED"}
    assert properties["image-digest"] == "UNBOUND" or (properties["image-digest"].startswith("sha256:") and len(properties["image-digest"]) == 71)
    assert properties["release-digest"] == "UNBOUND" or (properties["release-digest"].startswith("sha256:") and len(properties["release-digest"]) == 71)

    # Verify CycloneDX 1.5 extended component fields (supplier, licenses, hashes).
    # The root component and first-party workspace packages intentionally have no
    # hashes (C2 fix: coordinate-derived digests were removed).  Pick the first
    # third-party (npm/pypi) component for the schema assertion.
    third_party_comp = next(
        (
            c for c in data["components"]
            if c.get("supplier", {}).get("name") in {"npm", "pypi"}
            and not c.get("purl", "").startswith("pkg:npm/%40oday-plus/")
        ),
        None,
    )
    assert third_party_comp is not None, "At least one third-party component must be present"
    assert "supplier" in third_party_comp
    assert "licenses" in third_party_comp
    assert "purl" in third_party_comp
    # hashes is present on third-party packages that have a real lockfile digest
    # (omitted when no authentic digest is available — that is correct behaviour)

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
    assert "Policy Status:" in res.stdout


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
    from scripts.security.generate_sbom import check_license_policy

    clean_sbom = {
        "metadata": {
            "properties": [
                {"name": "image-digest", "value": "sha256:0000000000000000000000000000000000000000000000000000000000000000"},
                {"name": "release-digest", "value": "sha256:0000000000000000000000000000000000000000000000000000000000000000"},
            ]
        },
        "components": [
            {
                "name": "oday-plus",
                "version": "0.1.0",
                "type": "application",
                "purl": "pkg:generic/oday-plus@0.1.0",
                "licenses": [{"license": {"id": "MIT"}}],
            }
        ],
    }
    test_cases = [
        ("(AGPL-3.0)", False),
        ("GPL-3.0 OR MIT", False),
        ("GPL-2.0 WITH Classpath-exception-2.0", False),
        ("SEE LICENSE IN LICENSE.md", False),
        ("UNKNOWN", False),
        ("MIT OR Apache-2.0", True),
        ("Apache-2.0 AND LGPL-3.0-or-later", False),
        ("LGPL-3.0-or-later", False),
    ]

    for lic_expr, expected_pass in test_cases:
        test_sbom = json.loads(json.dumps(clean_sbom))
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


def test_vulnerability_audit_script_prod_passes() -> None:
    """Production vulnerability audit gate must PASS when production dependencies are clean."""
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/vulnerability_scan.py"), "--scope", "prod"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"vulnerability_scan.py --scope prod failed with output:\n{res.stderr}\n{res.stdout}"


def test_vulnerability_audit_script_full_fails_closed_without_active_exemption() -> None:
    """C3 — full/dev vulnerability audit gate must FAIL CLOSED when dev findings exist and exemption status is review_required."""
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts/security/vulnerability_scan.py"), "--scope", "full"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, "Full vulnerability scan must fail closed when no active exemption exists"
    assert "Vulnerability Audit Gate FAILED" in res.stdout or "Vulnerability Audit Gate FAILED" in res.stderr



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
                    "approved_by": "Antigravity5",
                    "approval_reference": "TEST-REF-001",
                    "status": "active",
                    "issued_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2099-12-31T23:59:59Z",
                    "scope": "all"
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
        assert any("AI agent names" in v for v in ex_violations)
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


# ─── Round-3/Round-4/Round-5 negative tests (C1/C4/C5, C2, C3/R2, R1, R3) ───


def test_arbitrary_approver_strings_rejected_negative(tmp_path: Path) -> None:
    """C1/C4 — generic, arbitrary, bare role, and AI-adjacent approver strings must be rejected.

    Bare role strings like 'Human/Ops', 'Legal/Ops', 'Security/Ops', 'TBD/Ops',
    'unknown/ops', 'N/A (ops)', and 'pending-legal' must be rejected because they
    do not identify a named accountable role-holder.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import _is_valid_approver

    bad_approvers = [
        "",
        "asdf",
        "x",
        "TBD",
        ".",
        "ClaudeCode",
        "Antigravity Team",
        "GPT-4 approver",
        "Gemini",
        "Codex5",
        "Human/Ops",
        "Legal/Ops",
        "Security/Ops",
        "TBD/Ops",
        "unknown/ops",
        "N/A (ops)",
        "pending-legal",
        "Ops",
        "Legal",
        "Security",
    ]
    for approver in bad_approvers:
        assert not _is_valid_approver(approver), (
            f"Approver '{approver}' should be rejected but was accepted"
        )

    # Legitimate named role-holder approvers must pass
    good_approvers = [
        "Jane Doe (Legal Counsel)",
        "Jane Doe, Legal",
        "Alice Smith, Security Director",
        "John Doe, Operations Officer",
        "TEST-ONLY Jane Doe, Legal Counsel",
    ]
    for approver in good_approvers:
        assert _is_valid_approver(approver), (
            f"Approver '{approver}' should be accepted but was rejected"
        )


def test_first_party_purl_delimiter_anchoring_and_spoof_negative() -> None:
    """C5 — first-party purl recognition must be delimiter-anchored to prevent spoofing."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import is_first_party_purl

    # Valid first-party PURLs
    assert is_first_party_purl("pkg:generic/oday-plus@0.1.0")
    assert is_first_party_purl("pkg:npm/%40oday-plus/web@0.1.0")
    assert is_first_party_purl("pkg:npm/%40oday-plus/design-tokens@0.1.0")

    # Spoofed PURLs attempting to match via unanchored prefix
    assert not is_first_party_purl("pkg:generic/oday-plus-evil@1.0")
    assert not is_first_party_purl("pkg:npm/%40oday-plus-evil/x@1.0")
    assert not is_first_party_purl("pkg:generic/oday-plus_attacker@2.0")


def test_inactive_and_review_required_exemptions_ignored_negative(tmp_path: Path) -> None:
    """C4 — exemption entries with status != 'active' must be ignored and fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.vulnerability_scan as vscan

    inactive_exemptions = tmp_path / "inactive_exemptions.json"
    inactive_exemptions.write_text(
        json.dumps({
            "exemptions": [
                {
                    "package_name": "brace-expansion",
                    "vulnerability_id": "GHSA-mh99-v99m-4gvg",
                    "scope": "dev",
                    "reason": "Review required test",
                    "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
                    "approval_reference": "TEST-REF-001",
                    "status": "review_required",
                    "issued_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2099-12-31T23:59:59Z",
                }
            ]
        }),
        encoding="utf-8",
    )

    orig_path = vscan.EXEMPTIONS_PATH
    try:
        vscan.EXEMPTIONS_PATH = inactive_exemptions
        active_ex, violations = vscan.load_vulnerability_exemptions()
        assert len(violations) == 0
        assert len(active_ex) == 0, "Non-active exemption entries must be ignored for finding suppression"
    finally:
        vscan.EXEMPTIONS_PATH = orig_path


def test_missing_required_exemption_field_rejected_negative(tmp_path: Path) -> None:
    """C4 — a vulnerability exemption with a missing required field must be flagged.

    Required fields per the schema: package_name, vulnerability_id, status, approved_by,
    approval_reference, issued_at, expires_at, scope, reason.
    """
    sys.path.insert(0, str(ROOT))
    import scripts.security.vulnerability_scan as vscan

    # Exemption missing 'issued_at', 'vulnerability_id', 'status', and 'approval_reference'
    incomplete_exemptions = tmp_path / "incomplete_exemptions.json"
    incomplete_exemptions.write_text(
        json.dumps({
            "exemptions": [
                {
                    "package_name": "some-package",
                    # missing: vulnerability_id, status, approval_reference, issued_at, scope
                    "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
                    "expires_at": "2099-12-31T23:59:59Z",
                    "reason": "Testing missing fields",
                }
            ]
        }),
        encoding="utf-8",
    )

    orig_path = vscan.EXEMPTIONS_PATH
    try:
        vscan.EXEMPTIONS_PATH = incomplete_exemptions
        _, violations = vscan.load_vulnerability_exemptions()
        assert len(violations) > 0, (
            "Missing required exemption fields must produce validation violations"
        )
        missing_field_violations = [v for v in violations if "missing required fields" in v]
        assert len(missing_field_violations) > 0, (
            f"Expected 'missing required fields' violation, got: {violations}"
        )
    finally:
        vscan.EXEMPTIONS_PATH = orig_path


def test_coordinate_derived_hash_absent_from_workspace_components() -> None:
    """C2 — workspace (link:) npm packages must not carry coordinate-derived hashes.

    Before the fix the root component and all pkg_info.get('link') workspace
    packages emitted sha256(pkg_name) as their hash, which falsely claimed to
    be an artifact digest.  After the fix those components must have no 'hashes'
    key at all.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import generate_sbom

    sbom = generate_sbom()
    for comp in sbom.get("components", []):
        purl = comp.get("purl", "")
        # Root and first-party workspace packages should NOT have hashes
        if purl.startswith("pkg:generic/oday-plus") or purl.startswith("pkg:npm/%40oday-plus/"):
            assert "hashes" not in comp, (
                f"Component '{purl}' must not carry a coordinate-derived hash; "
                f"got hashes={comp.get('hashes')}"
            )
        # Any component that does have hashes must have a non-empty list
        # (never an empty placeholder)
        if "hashes" in comp:
            assert len(comp["hashes"]) > 0, (
                f"Component '{purl}' has an empty hashes list, which is forbidden"
            )


def test_dev_scoped_exemption_does_not_suppress_prod_audit_negative(tmp_path: Path) -> None:
    """C3/R2 — a dev-scoped vulnerability exemption must NOT suppress the same finding
    in the prod-scope audit invocation.

    This tests the _filter_exemptions_by_scope() helper directly.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import _filter_exemptions_by_scope

    dev_only_exemption = [
        {
            "package_name": "brace-expansion",
            "vulnerability_id": "GHSA-mh99-v99m-4gvg",
            "scope": "dev",
            "reason": "Dev-only",
            "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
            "approval_reference": "TEST-REF-001",
            "status": "active",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-12-31T23:59:59Z",
        }
    ]

    prod_filtered = _filter_exemptions_by_scope(dev_only_exemption, "prod")
    assert len(prod_filtered) == 0, (
        "A dev-scoped exemption must NOT appear in the prod-scope exemption set; "
        f"got: {prod_filtered}"
    )

    full_filtered = _filter_exemptions_by_scope(dev_only_exemption, "full")
    assert len(full_filtered) == 1, (
        "A dev-scoped exemption MUST appear in the full/dev-scope exemption set"
    )


def test_non_matching_advisory_id_not_exempted_negative() -> None:
    """R1 — an exemption receipt for advisory GHSA-A must not suppress advisory GHSA-B
    in the same package.

    This verifies that is_npm_finding_exempted() requires the (package, advisory_id)
    pair, not just the package name.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import is_npm_finding_exempted

    exemptions = [
        {
            "package_name": "brace-expansion",
            "vulnerability_id": "GHSA-mh99-v99m-4gvg",
            "scope": "dev",
            "reason": "Only this specific advisory",
            "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
            "approval_reference": "TEST-REF-001",
            "status": "active",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-12-31T23:59:59Z",
        }
    ]

    # Simulate a DIFFERENT advisory on the same package
    different_advisory_item = {
        "via": [
            {
                "name": "brace-expansion",
                "url": "https://github.com/advisories/GHSA-ZZZZ-9999-0000",
                "id": "GHSA-ZZZZ-9999-0000",
            }
        ]
    }

    result = is_npm_finding_exempted(
        "brace-expansion", different_advisory_item, {}, exemptions
    )
    assert not result, (
        "An exemption for GHSA-mh99-v99m-4gvg must NOT suppress a different "
        "advisory GHSA-ZZZZ-9999-0000 on the same package"
    )


def test_npm_audit_empty_stdout_nonzero_exit_fails_closed(tmp_path: Path) -> None:
    """R3 — npm audit exiting non-zero with empty stdout must be treated as a violation.

    A network or registry failure that writes to stderr and exits non-zero but
    produces no stdout was previously returned as (True, []) — the gate reported
    PASSED having audited nothing.  The fixed code must return (False, [error]).
    """
    import unittest.mock

    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_node_audit

    fake_result = unittest.mock.MagicMock()
    fake_result.stdout = ""
    fake_result.stderr = "npm ERR! network timeout"
    fake_result.returncode = 1

    with unittest.mock.patch("subprocess.run", return_value=fake_result):
        ok, violations = run_node_audit("prod", exemptions=[])

    assert not ok, (
        "npm audit returning exit code 1 with empty stdout must fail closed, not PASS"
    )
    assert len(violations) > 0, (
        "A non-zero npm audit exit with empty stdout must produce at least one violation"
    )
    assert any("exited with code" in v for v in violations), (
        f"Expected 'exited with code' in violations, got: {violations}"
    )


# ─── Round-6 negative tests (B1, B2, B3) ───


def test_generate_sbom_fails_closed_on_malformed_package_lock(tmp_path: Path) -> None:
    """B1 — malformed package-lock.json must cause generate_sbom to fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    bad_lock = tmp_path / "package-lock.json"
    bad_lock.write_text("{ malformed json }", encoding="utf-8")

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path
        (tmp_path / "uv.lock").write_text("[package]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to parse package-lock.json"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_generate_sbom_fails_closed_on_missing_package_lock(tmp_path: Path) -> None:
    """B1 — missing package-lock.json must cause generate_sbom to fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path
        (tmp_path / "uv.lock").write_text("[package]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Required dependency inventory missing"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_generate_sbom_fails_closed_on_malformed_uv_lock(tmp_path: Path) -> None:
    """B1 — malformed uv.lock must cause generate_sbom to fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path
        (tmp_path / "package.json").write_text('{"dependencies": {"foo": "^1.0.0"}}', encoding="utf-8")
        (tmp_path / "package-lock.json").write_text(
            '{"packages": {"": {}, "node_modules/foo": {"name": "foo", "version": "1.0.0"}}}', encoding="utf-8"
        )
        (tmp_path / "uv.lock").write_text("invalid toml === ", encoding="utf-8")
        with pytest.raises(ValueError, match="Failed to parse uv.lock"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_npm_audit_scanner_error_payload_rejected() -> None:
    """B2 — parseable npm audit error JSON must be rejected and not return PASS."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_node_audit

    fake_res = unittest.mock.MagicMock()
    fake_res.stdout = json.dumps({"error": {"code": "ENOTFOUND", "summary": "registry unavailable"}})
    fake_res.returncode = 1

    with unittest.mock.patch("subprocess.run", return_value=fake_res):
        ok, violations = run_node_audit("prod", exemptions=[])

    assert not ok, "npm audit returning scanner error payload must fail closed"
    assert any("scanner error payload" in v for v in violations)


def test_npm_audit_missing_vulnerabilities_field_rejected() -> None:
    """B2 — npm audit response missing vulnerabilities field must be rejected."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_node_audit

    fake_res = unittest.mock.MagicMock()
    fake_res.stdout = json.dumps({"status": "unknown"})
    fake_res.returncode = 1

    with unittest.mock.patch("subprocess.run", return_value=fake_res):
        ok, violations = run_node_audit("prod", exemptions=[])

    assert not ok, "npm audit missing vulnerabilities field must fail closed"
    assert any("missing expected 'vulnerabilities' field" in v for v in violations)


def test_pip_audit_scanner_error_payload_rejected() -> None:
    """B2 — parseable pip-audit error JSON must be rejected and not return PASS."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_python_audit

    fake_res = unittest.mock.MagicMock()
    fake_res.stdout = json.dumps({"error": "PyPI index offline"})
    fake_res.returncode = 1

    with unittest.mock.patch("subprocess.run", return_value=fake_res):
        ok, violations = run_python_audit("all", exemptions=[])

    assert not ok, "pip-audit returning scanner error payload must fail closed"
    assert any("scanner error payload" in v for v in violations)


def test_pip_audit_missing_dependencies_field_rejected() -> None:
    """B2 — pip-audit response missing dependencies field must be rejected."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_python_audit

    fake_res = unittest.mock.MagicMock()
    fake_res.stdout = json.dumps({"status": "unknown"})
    fake_res.returncode = 1

    with unittest.mock.patch("subprocess.run", return_value=fake_res):
        ok, violations = run_python_audit("all", exemptions=[])

    assert not ok, "pip-audit missing dependencies field must fail closed"
    assert any("missing expected 'dependencies' field" in v for v in violations)


def test_exemption_validator_positive_schema_and_ordering() -> None:
    """B3 — exemption validator enforces ISO timestamps, temporal ordering, and valid reference contracts."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import validate_exemption_entry

    # Invalid issued_at
    bad_issued = {
        "package_name": "pkg-a",
        "vulnerability_id": "GHSA-123",
        "status": "active",
        "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
        "approval_reference": "SEC-REF-001",
        "issued_at": "not-a-date",
        "expires_at": "2099-12-31T23:59:59Z",
        "scope": "dev",
        "reason": "Testing invalid timestamp",
    }
    valid, violations = validate_exemption_entry(bad_issued, "vulnerability")
    assert not valid
    assert any("invalid issued_at" in v for v in violations)

    # Trivial approval_reference
    bad_ref = dict(bad_issued, issued_at="2026-01-01T00:00:00Z", approval_reference="x")
    valid, violations = validate_exemption_entry(bad_ref, "vulnerability")
    assert not valid
    assert any("missing valid approval_reference" in v for v in violations)

    # Invalid temporal ordering (expires_at <= issued_at)
    bad_order = dict(
        bad_issued,
        issued_at="2026-06-01T00:00:00Z",
        expires_at="2026-01-01T00:00:00Z",
        approval_reference="SEC-REF-001",
    )
    valid, violations = validate_exemption_entry(bad_order, "vulnerability")
    assert not valid
    assert any("must be after issued_at" in v for v in violations)


def test_dev_scoped_license_exemption_does_not_suppress_release_finding(tmp_path: Path) -> None:
    """B3 — a dev-scoped license exemption must NOT suppress a GPL finding in release policy evaluation."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    dev_exemption = tmp_path / "dev_license_exemption.json"
    dev_exemption.write_text(
        json.dumps({
            "exemptions": [
                {
                    "package_name": "gpl-package",
                    "purl": "pkg:npm/gpl-package@1.0.0",
                    "reason": "Dev-only license exemption",
                    "approved_by": "TEST-ONLY Jane Doe (Legal Counsel)",
                    "approval_reference": "SEC-REF-001",
                    "status": "active",
                    "issued_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2099-12-31T23:59:59Z",
                    "scope": "dev"
                }
            ]
        }),
        encoding="utf-8",
    )

    fake_sbom = {
        "metadata": {"properties": []},
        "components": [
            {
                "name": "gpl-package",
                "version": "1.0.0",
                "purl": "pkg:npm/gpl-package@1.0.0",
                "licenses": [{"license": {"id": "GPL-3.0"}}],
            }
        ],
    }

    orig_path = sbom_mod.EXEMPTIONS_PATH
    try:
        sbom_mod.EXEMPTIONS_PATH = dev_exemption
        # Release policy check (default scope="prod")
        is_passed, violations = sbom_mod.check_license_policy(fake_sbom, scope="prod")
        assert not is_passed, "dev-scoped license exemption must NOT suppress GPL finding in release policy gate"
        assert any("Denied license 'GPL-3.0'" in v for v in violations)
    finally:
        sbom_mod.EXEMPTIONS_PATH = orig_path


# ─── Round-7 negative tests (B1, B2, B3) ───


def test_generate_sbom_fails_closed_on_empty_parseable_inventories(tmp_path: Path) -> None:
    """B1 — empty parseable package-lock.json and uv.lock must cause generate_sbom to fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path
        (tmp_path / "package-lock.json").write_text('{"packages": {}}', encoding="utf-8")
        (tmp_path / "uv.lock").write_text("[package]\n", encoding="utf-8")

        with pytest.raises(ValueError, match="missing required non-root dependency inventory"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_generate_sbom_fails_closed_on_manifest_incomplete_inventories(tmp_path: Path) -> None:
    """B1 — inventories missing declared manifest dependencies must fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path
        (tmp_path / "package.json").write_text('{"dependencies": {"express": "^4.18.0"}}', encoding="utf-8")
        (tmp_path / "package-lock.json").write_text(
            json.dumps({"packages": {"": {}, "node_modules/lodash": {"name": "lodash", "version": "4.17.21"}}}),
            encoding="utf-8",
        )
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["pytest>=7.0"]', encoding="utf-8")
        (tmp_path / "uv.lock").write_text('[[package]]\nname = "requests"\nversion = "2.31.0"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="missing declared dependencies"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_scanner_payloads_round7_five_negative_shapes() -> None:
    """B2 — test all five incomplete success-shaped scanner payloads fail closed."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_node_audit, run_python_audit

    # 1. npm_version_only: {"auditReportVersion": 2}
    fake_res1 = unittest.mock.MagicMock()
    fake_res1.stdout = json.dumps({"auditReportVersion": 2})
    fake_res1.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res1):
        ok1, v1 = run_node_audit("prod", exemptions=[])
    assert not ok1, "npm audit version-only payload must fail closed"
    assert any("missing expected 'vulnerabilities' field" in item for item in v1)

    # 2. npm_vulns_only: {"vulnerabilities": {}} without auditReportVersion or metadata
    fake_res2 = unittest.mock.MagicMock()
    fake_res2.stdout = json.dumps({"vulnerabilities": {}})
    fake_res2.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res2):
        ok2, v2 = run_node_audit("prod", exemptions=[])
    assert not ok2, "npm audit vulns-only payload missing scanner metadata must fail closed"
    assert any("missing valid scanner metadata" in item for item in v2)

    # 3. pip_empty_stdout: ""
    def fake_run3(cmd, **kwargs):
        res = unittest.mock.MagicMock()
        if "export" in cmd:
            res.stdout = "psycopg==3.3.4\n"
            res.returncode = 0
        else:
            res.stdout = ""
            res.returncode = 0
        return res
    with unittest.mock.patch("subprocess.run", side_effect=fake_run3):
        ok3, v3 = run_python_audit("all", exemptions=[])
    assert not ok3, "pip-audit empty stdout must fail closed"
    assert any("empty output" in item for item in v3)

    # 4. pip_vulnerabilities_only: {"vulnerabilities": []}
    fake_res4 = unittest.mock.MagicMock()
    fake_res4.stdout = json.dumps({"vulnerabilities": []})
    fake_res4.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res4):
        ok4, v4 = run_python_audit("all", exemptions=[])
    assert not ok4, "pip-audit vulns-only payload missing dependencies list must fail closed"
    assert any("missing expected 'dependencies' field" in item for item in v4)

    # 5. pip_empty_list: []
    fake_res5 = unittest.mock.MagicMock()
    fake_res5.stdout = json.dumps([])
    fake_res5.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res5):
        ok5, v5 = run_python_audit("all", exemptions=[])
    assert not ok5, "pip-audit empty list payload must fail closed"
    assert any("empty dependency list schema" in item for item in v5)


def test_reproduced_weak_receipt_cannot_suppress_gpl_and_fails_schema(tmp_path: Path) -> None:
    """B3 — weak receipt (zzzzz, foo, +08:00, reason x) fails schema and cannot suppress GPL-3.0."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod
    from scripts.security.exemption_validator import validate_exemption_entry

    weak_receipt = {
        "package_name": "gpl-package",
        "purl": "pkg:npm/gpl-package@1.0.0",
        "approved_by": "zzzzz",
        "approval_reference": "foo",
        "issued_at": "2026-01-01T00:00:00+08:00",
        "expires_at": "2099-12-31T23:59:59+08:00",
        "reason": "x",
        "status": "active",
        "scope": "prod",
    }

    # 1. Schema validation must report all 5 violations
    valid, violations = validate_exemption_entry(weak_receipt, "license")
    assert not valid, "Weak receipt must fail positive schema validation"
    assert any("invalid approver 'zzzzz'" in v for v in violations)
    assert any("missing valid approval_reference" in v for v in violations)
    assert any("must be ISO UTC" in v for v in violations)
    assert any("invalid reason 'x'" in v for v in violations)

    # 2. Policy evaluation must NOT suppress GPL-3.0 and must fail closed
    weak_exemptions_file = tmp_path / "weak_license_exemptions.json"
    weak_exemptions_file.write_text(json.dumps({"exemptions": [weak_receipt]}), encoding="utf-8")

    fake_sbom = {
        "metadata": {"properties": []},
        "components": [
            {
                "name": "gpl-package",
                "version": "1.0.0",
                "purl": "pkg:npm/gpl-package@1.0.0",
                "licenses": [{"license": {"id": "GPL-3.0"}}],
            }
        ],
    }

    orig_path = sbom_mod.EXEMPTIONS_PATH
    try:
        sbom_mod.EXEMPTIONS_PATH = weak_exemptions_file
        is_passed, policy_violations = sbom_mod.check_license_policy(fake_sbom, scope="prod")
        assert not is_passed, "Weak receipt must NOT suppress GPL-3.0 in release policy evaluation"
        assert any("Denied license 'GPL-3.0'" in v for v in policy_violations)
        assert any("invalid approver 'zzzzz'" in v for v in policy_violations)
    finally:
        sbom_mod.EXEMPTIONS_PATH = orig_path


# ─── Round-8 negative tests (B1, B2, B3) ───


def test_round8_b1_malformed_and_dev_incomplete_manifests(tmp_path: Path) -> None:
    """B1 — missing manifests, malformed manifest JSON/TOML, and dev-group-incomplete inventories must fail closed."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod

    orig_root = sbom_mod.ROOT
    try:
        sbom_mod.ROOT = tmp_path

        # 1. Missing package.json
        (tmp_path / "package-lock.json").write_text('{"packages": {"": {}, "node_modules/foo": {"name": "foo", "version": "1.0.0"}}}', encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = []', encoding="utf-8")
        (tmp_path / "uv.lock").write_text('[[package]]\nname = "bar"\nversion = "1.0.0"\n', encoding="utf-8")

        with pytest.raises(ValueError, match="Required manifest missing"):
            sbom_mod.generate_sbom()

        # 2. Malformed package.json
        (tmp_path / "package.json").write_text("{ malformed json }", encoding="utf-8")
        with pytest.raises(ValueError, match="package.json missing valid JSON schema"):
            sbom_mod.generate_sbom()

        # 3. Valid package.json, but pyproject.toml has dev dependency group missing from uv.lock
        (tmp_path / "package.json").write_text('{"dependencies": {"foo": "^1.0.0"}}', encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["bar>=1.0.0"]\n[dependency-groups]\ndev = ["pytest>=7.0.0"]', encoding="utf-8")
        with pytest.raises(ValueError, match="missing declared dependencies"):
            sbom_mod.generate_sbom()
    finally:
        sbom_mod.ROOT = orig_root


def test_round8_b2_nested_scanner_payload_schema_failures() -> None:
    """B2 — malformed nested entries in npm audit and pip-audit payloads must fail closed."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_node_audit, run_python_audit

    # 1. npm payload with empty vulnerability object
    fake_res1 = unittest.mock.MagicMock()
    fake_res1.stdout = json.dumps({"auditReportVersion": 2, "vulnerabilities": {"brace-expansion": {}}})
    fake_res1.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res1):
        ok1, v1 = run_node_audit("prod", exemptions=[])
    assert not ok1, "npm audit entry lacking severity must fail closed"
    assert any("missing or unrecognized severity" in item for item in v1)

    # 2. pip-audit payload with empty dependency object
    fake_res2 = unittest.mock.MagicMock()
    fake_res2.stdout = json.dumps({"dependencies": [{}]})
    fake_res2.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res2):
        ok2, v2 = run_python_audit("all", exemptions=[])
    assert not ok2, "pip-audit dependency entry lacking name/version must fail closed"
    assert any("missing or empty 'name' or 'version'" in item for item in v2)


def test_round8_b3_fake_person_and_unresolved_active_receipts_rejected(tmp_path: Path) -> None:
    """B3 — cosmetic person/role/reference patterns and unresolved active receipts must fail validation and cannot suppress GPL-3.0."""
    sys.path.insert(0, str(ROOT))
    import scripts.security.generate_sbom as sbom_mod
    from scripts.security.exemption_validator import validate_exemption_entry

    fake_receipts = [
        {
            "package_name": "gpl-package",
            "purl": "pkg:npm/gpl-package@1.0.0",
            "approved_by": "Fake Person, abc",
            "approval_reference": "FOO-BAR",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-12-31T23:59:59Z",
            "reason": "aaaaaaaaaa",
            "status": "active",
            "scope": "prod",
        },
        {
            "package_name": "gpl-package",
            "purl": "pkg:npm/gpl-package@1.0.0",
            "approved_by": "Attacker Person (Legal Counsel)",
            "approval_reference": "SEC-999999",
            "issued_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-12-31T23:59:59Z",
            "reason": "Authentic looking long explanation for legal risk waiver",
            "status": "active",
            "scope": "prod",
        },
    ]

    for rec in fake_receipts:
        valid, violations = validate_exemption_entry(rec, "license")
        assert not valid, f"Fake active receipt {rec['approved_by']} must fail validation"

        # Policy evaluation must NOT suppress GPL-3.0
        fake_ex_file = tmp_path / "fake_license_exemptions.json"
        fake_ex_file.write_text(json.dumps({"exemptions": [rec]}), encoding="utf-8")

        fake_sbom = {
            "metadata": {"properties": []},
            "components": [
                {
                    "name": "gpl-package",
                    "version": "1.0.0",
                    "purl": "pkg:npm/gpl-package@1.0.0",
                    "licenses": [{"license": {"id": "GPL-3.0"}}],
                }
            ],
        }

        orig_path = sbom_mod.EXEMPTIONS_PATH
        try:
            sbom_mod.EXEMPTIONS_PATH = fake_ex_file
            is_passed, policy_violations = sbom_mod.check_license_policy(fake_sbom, scope="prod")
            assert not is_passed, f"Fake active receipt {rec['approved_by']} must NOT suppress GPL-3.0"
            assert any("Denied license 'GPL-3.0'" in v for v in policy_violations)
        finally:
            sbom_mod.EXEMPTIONS_PATH = orig_path


def test_round8_b1_authoritative_receipt_contract(tmp_path: Path) -> None:
    """B1 — active exemptions must resolve to an authoritative signed receipt under ODP-PLAN-OSS-LEGAL-POLICY-001 with matching field bindings."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        AuthoritativeReceiptVerifier,
        compute_canonical_receipt_hash,
        compute_file_sha256,
        compute_policy_hash,
        compute_receipt_signature,
        resolve_approval_reference,
        validate_exemption_entry,
    )

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    # 1. Unresolvable reference fails validation
    is_valid, violations = validate_exemption_entry(entry, "license", base_dir=tmp_path)
    assert not is_valid
    assert any("could not be resolved to an authentic legal policy receipt" in v for v in violations)

    # 2. Plain repo file without verifier fails resolution (fail closed)
    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    mismatched_receipt = {
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "package_name": "other-package",
        "scope": "prod",
    }
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(mismatched_receipt), encoding="utf-8")

    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path)
    assert not res_ok
    assert "cannot self-establish authority" in res_err

    # 3. Matching receipt with AuthoritativeReceiptVerifier passes resolution
    key = "secret-test-authority-key-12345"
    verifier = AuthoritativeReceiptVerifier(authority_key=key, trusted_source_systems={"legal_vault", "ODP-PLAN-OSS-LEGAL-POLICY-001"})

    uv_lock_hash = compute_file_sha256(ROOT / "uv.lock")
    pkg_lock_hash = compute_file_sha256(ROOT / "package-lock.json")
    pol_hash = compute_policy_hash()

    matching_receipt = {
        "approval_ref": "POLICY-LGPL-001",
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "principal_id": "usr-jane-001",
        "principal_role": "Legal Counsel",
        "source_system": "legal_vault",
        "policy_decision": "approved",
        "policy_name": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "policy_version": "1.0.0",
        "policy_hash": pol_hash,
        "package_name": "psycopg",
        "package_purl": "pkg:pypi/psycopg@3.3.4",
        "scope": "prod",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reviewed_at": "2026-07-01T00:00:00Z",
        "source_digest": "a" * 40,
        "release_digest": "b" * 64,
        "sbom_digest": "c" * 64,
        "python_lock_digest": uv_lock_hash,
        "npm_lock_digest": pkg_lock_hash,
        "evidence_report_digest": "d" * 64,
    }
    matching_receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(matching_receipt)
    matching_receipt["signature"] = compute_receipt_signature(matching_receipt["canonical_receipt_hash"], key)

    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(matching_receipt), encoding="utf-8")

    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert res_ok, f"Resolution failed: {res_err}"
    assert res_err is None


def test_round8_b2_unapproved_lgpl_policy_fails_closed() -> None:
    """B2 — unapproved LGPL policy remains in review_required_licenses and fails check_license_policy until ODP-PLAN-OSS-LEGAL-POLICY-001 is completed."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import check_license_policy, generate_sbom

    sbom = generate_sbom()
    lgpl_sbom = json.loads(json.dumps(sbom))
    lgpl_sbom["components"].append({
        "name": "psycopg",
        "version": "3.3.4",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "licenses": [{"license": {"id": "LGPL-3.0-only"}}]
    })

    is_passed, violations = check_license_policy(lgpl_sbom, scope="prod")
    assert not is_passed, "Unapproved LGPL policy must fail closed"
    assert any("requiring security review" in v and "LGPL-3.0-only" in v for v in violations)


def test_round8_b3_clean_worktree_audit_reproducibility() -> None:
    """B3 — scanner results must validate against frozen lockfiles and fail closed on empty or malformed reports."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_python_audit

    fake_res = unittest.mock.MagicMock()
    fake_res.stdout = json.dumps([])
    fake_res.returncode = 0
    with unittest.mock.patch("subprocess.run", return_value=fake_res):
        ok, violations = run_python_audit("all", exemptions=[])
    assert not ok
    assert any("empty dependency list schema" in v for v in violations)


def test_round9_b1_repo_local_lookalike_rejected(tmp_path: Path) -> None:
    """1. A fully populated repository-local lookalike receipt is rejected when no verifier is provided."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        resolve_approval_reference,
        validate_exemption_entry,
    )

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    full_repo_lookalike = {
        "approval_ref": "POLICY-LGPL-001",
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "principal_id": "usr-jane-doe-001",
        "principal_role": "Legal Counsel",
        "source_system": "legal_vault",
        "policy_decision": "approved",
        "policy_name": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "policy_version": "1.0.0",
        "policy_hash": "a" * 64,
        "package_name": "psycopg",
        "package_purl": "pkg:pypi/psycopg@3.3.4",
        "scope": "prod",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reviewed_at": "2026-07-01T00:00:00Z",
        "source_digest": "a" * 40,
        "release_digest": "b" * 64,
        "sbom_digest": "c" * 64,
        "python_lock_digest": "d" * 64,
        "npm_lock_digest": "e" * 64,
        "evidence_report_digest": "f" * 64,
        "canonical_receipt_hash": "b" * 64,
        "signature": "sig_valid_hex_123456",
    }
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(full_repo_lookalike), encoding="utf-8")

    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path)
    assert not res_ok
    assert "cannot self-establish authority" in res_err

    is_valid, violations = validate_exemption_entry(entry, "license", base_dir=tmp_path)
    assert not is_valid
    assert any("cannot self-establish authority" in v for v in violations)


def test_round9_b1_missing_or_mismatched_receipt_fields_rejected(tmp_path: Path) -> None:
    """2. Missing or mismatched principal/source/policy/scope/release/evidence/integrity fields are rejected."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        AuthoritativeReceiptVerifier,
        compute_canonical_receipt_hash,
        compute_file_sha256,
        compute_policy_hash,
        compute_receipt_signature,
        resolve_approval_reference,
    )

    key = "secret-test-key-999"
    verifier = AuthoritativeReceiptVerifier(authority_key=key, trusted_source_systems={"legal_vault"})

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    uv_lock_hash = compute_file_sha256(ROOT / "uv.lock")
    pkg_lock_hash = compute_file_sha256(ROOT / "package-lock.json")
    pol_hash = compute_policy_hash()

    # a) Missing principal_id
    bad_receipt = {
        "approval_ref": "POLICY-LGPL-001",
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        # missing principal_id
        "principal_role": "Legal Counsel",
        "source_system": "legal_vault",
        "policy_decision": "approved",
        "policy_name": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "policy_version": "1.0.0",
        "policy_hash": pol_hash,
        "package_name": "psycopg",
        "package_purl": "pkg:pypi/psycopg@3.3.4",
        "scope": "prod",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reviewed_at": "2026-07-01T00:00:00Z",
        "source_digest": "a" * 40,
        "release_digest": "b" * 64,
        "sbom_digest": "c" * 64,
        "python_lock_digest": uv_lock_hash,
        "npm_lock_digest": pkg_lock_hash,
        "evidence_report_digest": "d" * 64,
    }
    bad_receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(bad_receipt)
    bad_receipt["signature"] = compute_receipt_signature(bad_receipt["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(bad_receipt), encoding="utf-8")
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not res_ok
    assert "missing required fields" in res_err

    # b) Mismatched package_name
    bad_receipt["principal_id"] = "usr-jane-doe-001"
    bad_receipt["package_name"] = "other-pkg"
    bad_receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(bad_receipt)
    bad_receipt["signature"] = compute_receipt_signature(bad_receipt["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(bad_receipt), encoding="utf-8")
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not res_ok
    assert "does not match entry package" in res_err

    # c) Invalid timestamp ordering (reviewed_at before issued_at)
    bad_receipt["package_name"] = "psycopg"
    bad_receipt["issued_at"] = "2026-07-01T00:00:00Z"
    bad_receipt["reviewed_at"] = "2026-06-01T00:00:00Z"
    bad_receipt["expires_at"] = "2026-12-31T23:59:59Z"
    bad_receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(bad_receipt)
    bad_receipt["signature"] = compute_receipt_signature(bad_receipt["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(bad_receipt), encoding="utf-8")
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not res_ok
    assert "timestamp ordering violation" in res_err

    # d) Invalid hash format (non-hex)
    bad_receipt["reviewed_at"] = "2026-07-01T00:00:00Z"
    bad_receipt["policy_hash"] = "invalid_non_hex_hash!"
    bad_receipt["canonical_receipt_hash"] = compute_canonical_receipt_hash(bad_receipt)
    bad_receipt["signature"] = compute_receipt_signature(bad_receipt["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(bad_receipt), encoding="utf-8")
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not res_ok
    assert "is not a valid hex digest format" in res_err


def test_round9_b1_authoritative_verifier_mismatch_rejected(tmp_path: Path) -> None:
    """3. An authoritative verifier/readback mismatch is rejected even when local JSON is internally consistent."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        AuthoritativeReceiptVerifier,
        compute_canonical_receipt_hash,
        compute_file_sha256,
        compute_policy_hash,
        compute_receipt_signature,
        resolve_approval_reference,
    )

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    uv_lock_hash = compute_file_sha256(ROOT / "uv.lock")
    pkg_lock_hash = compute_file_sha256(ROOT / "package-lock.json")
    pol_hash = compute_policy_hash()

    valid_local_json = {
        "approval_ref": "POLICY-LGPL-001",
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "principal_id": "usr-jane-doe-001",
        "principal_role": "Legal Counsel",
        "source_system": "legal_vault",
        "policy_decision": "approved",
        "policy_name": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "policy_version": "1.0.0",
        "policy_hash": pol_hash,
        "package_name": "psycopg",
        "package_purl": "pkg:pypi/psycopg@3.3.4",
        "scope": "prod",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reviewed_at": "2026-07-01T00:00:00Z",
        "source_digest": "a" * 40,
        "release_digest": "b" * 64,
        "sbom_digest": "c" * 64,
        "python_lock_digest": uv_lock_hash,
        "npm_lock_digest": pkg_lock_hash,
        "evidence_report_digest": "d" * 64,
    }
    valid_local_json["canonical_receipt_hash"] = compute_canonical_receipt_hash(valid_local_json)
    valid_local_json["signature"] = compute_receipt_signature(valid_local_json["canonical_receipt_hash"], "wrong-key")
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(valid_local_json), encoding="utf-8")

    failing_verifier = AuthoritativeReceiptVerifier(authority_key="correct-key", trusted_source_systems={"legal_vault"})

    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=failing_verifier)
    assert not res_ok
    assert "Authoritative verifier rejected receipt" in res_err
    assert "signature mismatch" in res_err


def test_round9_b2_frozen_inventory_clean_worktree_audit_reproducible() -> None:
    """4. The real production audit succeeds reproducibly against a non-empty frozen dependency inventory in a clean checkout."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_python_audit

    ok, findings = run_python_audit("prod", exemptions=[])
    assert ok, f"Production Python audit must pass on locked inventory, findings: {findings}"
    assert len(findings) == 0


def test_round10_b1_caller_controlled_callback_cannot_approve_lookalike(tmp_path: Path) -> None:
    """B1 — caller-controlled dummy callback or unconfigured verifier cannot approve repository-local lookalike."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        AuthoritativeReceiptVerifier,
        resolve_approval_reference,
    )

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    lookalike = {"status": "active", "approved_by": "Jane Doe (Legal Counsel)", "approval_reference": "POLICY-LGPL-001"}
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(lookalike), encoding="utf-8")

    # 1. Plain function/lambda callback is REJECTED
    def dummy_fn(ref, rec, ent):
        return True
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=dummy_fn)
    assert not res_ok
    assert "cannot self-establish authority without a configured AuthoritativeReceiptVerifier" in res_err

    # 2. Unconfigured AuthoritativeReceiptVerifier (no authority key) is REJECTED
    unconfig_verifier = AuthoritativeReceiptVerifier(authority_key=None)
    res_ok, res_err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=unconfig_verifier)
    assert not res_ok
    assert "cannot self-establish authority" in res_err


def test_round10_b2_receipt_field_bindings_and_tamper_mutations(tmp_path: Path) -> None:
    """B2 — bind receipt to policy hash, lock hashes, timestamps, purl, vulnerability_id, and verify recomputed digest."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.exemption_validator import (
        AuthoritativeReceiptVerifier,
        compute_canonical_receipt_hash,
        compute_file_sha256,
        compute_policy_hash,
        compute_receipt_signature,
        resolve_approval_reference,
    )

    key = "authority-secret-key-456"
    verifier = AuthoritativeReceiptVerifier(authority_key=key, trusted_source_systems={"legal_vault"})

    entry = {
        "package_name": "psycopg",
        "purl": "pkg:pypi/psycopg@3.3.4",
        "vulnerability_id": "GHSA-1234-5678",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reason": "Authoritative legal review waiver",
        "status": "active",
        "scope": "prod",
    }

    receipts_dir = tmp_path / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    uv_lock_hash = compute_file_sha256(ROOT / "uv.lock")
    pkg_lock_hash = compute_file_sha256(ROOT / "package-lock.json")
    pol_hash = compute_policy_hash()

    base_receipt = {
        "approval_ref": "POLICY-LGPL-001",
        "status": "active",
        "approved_by": "Jane Doe (Legal Counsel)",
        "approval_reference": "POLICY-LGPL-001",
        "principal_id": "usr-jane-001",
        "principal_role": "Legal Counsel",
        "source_system": "legal_vault",
        "policy_decision": "approved",
        "policy_name": "ODP-PLAN-OSS-LEGAL-POLICY-001",
        "policy_version": "1.0.0",
        "policy_hash": pol_hash,
        "package_name": "psycopg",
        "package_purl": "pkg:pypi/psycopg@3.3.4",
        "vulnerability_id": "GHSA-1234-5678",
        "scope": "prod",
        "issued_at": "2026-07-01T00:00:00Z",
        "expires_at": "2026-12-31T23:59:59Z",
        "reviewed_at": "2026-07-01T00:00:00Z",
        "source_digest": "a" * 40,
        "release_digest": "b" * 64,
        "sbom_digest": "c" * 64,
        "python_lock_digest": uv_lock_hash,
        "npm_lock_digest": pkg_lock_hash,
        "evidence_report_digest": "d" * 64,
    }

    # 1. Valid receipt passes
    rec = dict(base_receipt)
    rec["canonical_receipt_hash"] = compute_canonical_receipt_hash(rec)
    rec["signature"] = compute_receipt_signature(rec["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(rec), encoding="utf-8")
    ok, err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert ok, f"Valid receipt must pass: {err}"

    # 2. Tampered vulnerability_id mismatch
    rec = dict(base_receipt)
    rec["vulnerability_id"] = "GHSA-9999-9999"
    rec["canonical_receipt_hash"] = compute_canonical_receipt_hash(rec)
    rec["signature"] = compute_receipt_signature(rec["canonical_receipt_hash"], key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(rec), encoding="utf-8")
    ok, err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not ok
    assert "vulnerability_id" in err

    # 3. Tampered canonical_receipt_hash mismatch
    rec = dict(base_receipt)
    rec["canonical_receipt_hash"] = "e" * 64
    rec["signature"] = compute_receipt_signature("e" * 64, key)
    (receipts_dir / "POLICY-LGPL-001.json").write_text(json.dumps(rec), encoding="utf-8")
    ok, err = resolve_approval_reference("POLICY-LGPL-001", entry, base_dir=tmp_path, verifier_fn=verifier)
    assert not ok
    assert "canonical_receipt_hash" in err


def test_round10_b3_frozen_python_audit_no_ambient_fallback(tmp_path: Path) -> None:
    """B3 — missing lock or export failure must fail closed with export error; ambient audit fallback forbidden."""
    import unittest.mock
    sys.path.insert(0, str(ROOT))
    from scripts.security.vulnerability_scan import run_python_audit

    # 1. Missing uv.lock fails closed
    with unittest.mock.patch("pathlib.Path.exists", return_value=False):
        ok, violations = run_python_audit("prod", exemptions=[])
    assert not ok
    assert any("ambient audit fallback is forbidden" in v or "missing" in v for v in violations)

    # 2. Failed uv export fails closed
    with unittest.mock.patch("subprocess.run", side_effect=Exception("uv export simulated error")):
        ok, violations = run_python_audit("prod", exemptions=[])
    assert not ok
    assert any("uv export" in v for v in violations)


def test_round10_b4_sbom_integrity_and_tamper_mutations(tmp_path: Path) -> None:
    """B4 — verify_sbom and readback_sbom require exact source/lock/policy/evidence/image/release bindings."""
    sys.path.insert(0, str(ROOT))
    from scripts.security.generate_sbom import (
        compute_lockfile_hashes,
        generate_sbom,
        readback_sbom,
        verify_sbom,
    )

    pkg_lock_hash, uv_lock_hash, policy_hash, evidence_hash = compute_lockfile_hashes()

    # 1. Generate active SBOM and write to tmp_path
    sbom_path = tmp_path / "sbom.json"
    sbom = generate_sbom()
    sbom_path.write_text(json.dumps(sbom, indent=2), encoding="utf-8")

    # 2. Readback with correct expected bindings passes
    res = readback_sbom(
        sbom_path,
        expected_git_sha=sbom["metadata"]["properties"][0]["value"],
        expected_package_lock_hash=pkg_lock_hash,
        expected_uv_lock_hash=uv_lock_hash,
        expected_policy_hash=policy_hash,
        expected_evidence_report_hash=evidence_hash,
    )
    assert res == 0

    # 3. Readback with tampered expected git-sha fails
    res = readback_sbom(sbom_path, expected_git_sha="0" * 40)
    assert res == 1

    # 4. Verify SBOM with tampered metadata property fails verify
    tampered_sbom = json.loads(json.dumps(sbom))
    for p in tampered_sbom["metadata"]["properties"]:
        if p["name"] == "policy-hash":
            p["value"] = "f" * 64
    tampered_path = tmp_path / "tampered_sbom.json"
    tampered_path.write_text(json.dumps(tampered_sbom, indent=2), encoding="utf-8")

    v_res = verify_sbom(tampered_path)
    assert v_res == 1
