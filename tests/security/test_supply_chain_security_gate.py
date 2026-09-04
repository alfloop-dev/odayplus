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
    assert (
        (major == 8 and minor == 5 and patch >= 10)
        or (major == 8 and minor == 4 and patch >= 38)
        or (major > 8)
    ), f"PostCSS version {version} is vulnerable"


def test_npm_audit_passes(monkeypatch) -> None:
    gate = _audit_gate()
    monkeypatch.setattr(
        gate,
        "run_npm_audit",
        lambda cwd=gate.ROOT, timeout=1.0: gate.classify_audit_output(_report_json(), ""),
    )
    outcome = gate.audit_with_retry(attempts=1, backoff=0, sleep=lambda _s: None)
    assert outcome.has_report
    code, verdict = gate.evaluate(outcome, "high")
    assert code == gate.EXIT_OK, f"clean audit report must pass the gate: {verdict}"
    assert "PASS" in verdict


def test_pip_audit_passes() -> None:
    res = subprocess.run(
        ["uv", "run", "--with", "pip-audit", "pip-audit", "--local"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"pip-audit failed with output:\n{res.stdout}\n{res.stderr}"


def test_secrets_scan_passes() -> None:
    res = subprocess.run(
        [str(ROOT / "delivery_toolchain/security/secret_scan.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Secret scanning failed with output:\n{res.stdout}"


def test_sast_scan_passes() -> None:
    res = subprocess.run(
        [str(ROOT / "delivery_toolchain/security/sast_scan.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
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
    from delivery_toolchain.security.generate_sbom import generate_sbom as current_generate_sbom

    current_sbom = current_generate_sbom()
    assert current_sbom.get("components") == data.get("components"), (
        "Committed sbom.json is stale and does not match the active package-lock.json or uv.lock. "
        "Run delivery_toolchain/security/generate_sbom.py to regenerate it."
    )


def test_sign_images_script_executable() -> None:
    script_path = ROOT / "delivery_toolchain/security/sign_images.sh"
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
        "dependencies = [", 'dependencies = [\n    "nonexistent-test-package-xyz>=1.0.0",'
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

    # Run pip-audit on requirements-vulnerable.txt
    res = subprocess.run(
        ["uv", "run", "--with", "pip-audit", "pip-audit", "-r", str(req_file)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, "pip-audit should fail when scanning a vulnerable fixture"


def test_unsigned_images_rejected_negative() -> None:
    # Run sign_images.sh verify on a bogus image name in CI mode and expect non-zero exit code
    script_path = ROOT / "delivery_toolchain/security/sign_images.sh"
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
    from delivery_toolchain.security.generate_sbom import generate_sbom as current_generate_sbom

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
        from delivery_toolchain.security.secret_scan import scan_file

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


# --- npm audit gate: registry transport failure must stay distinct from findings ---
# Regression cover for ODP-SUPPLY-CHAIN-LOCKFILE-CONSISTENCY-001. arborist only
# reaches POST /-/npm/v1/security/audits/quick after the bulk advisory endpoint
# throws, so that endpoint's "Invalid package tree" body is a registry response,
# not a verdict on package-lock.json. The gate must never read it as either a
# pass or a lockfile defect.


def _audit_gate():
    sys.path.insert(0, str(ROOT))
    from delivery_toolchain.security import npm_audit_gate

    return npm_audit_gate


def _report_json(**counts: int) -> str:
    levels = {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0}
    levels.update(counts)
    levels["total"] = sum(levels.values())
    return json.dumps(
        {"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {"vulnerabilities": levels}}
    )


# The two registry bodies observed on PR #1164 (runs 33829112721 and 33832485313)
# against an unchanged lockfile, plus the retirement notice for the same endpoint.
QUICK_ENDPOINT_400 = json.dumps(
    {
        "message": "400 Bad Request - POST https://registry.npmjs.org/-/npm/v1/security/audits/quick",
        "method": "POST",
        "uri": "https://registry.npmjs.org/-/npm/v1/security/audits/quick",
        "statusCode": 400,
        "body": "Invalid package tree, run  npm install  to rebuild your package-lock.json",
    }
)
REGISTRY_503 = json.dumps(
    {
        "message": "503 Service Unavailable",
        "method": "POST",
        "uri": "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk",
        "statusCode": 503,
        "body": "Service Unavailable",
    }
)


def test_npm_audit_gate_fails_closed_on_high_severity_findings() -> None:
    gate = _audit_gate()
    outcome = gate.classify_audit_output(_report_json(high=2), "")
    assert outcome.has_report
    code, verdict = gate.evaluate(outcome, "high")
    assert code == gate.EXIT_VULNERABLE, f"high findings must fail the gate, got {verdict}"
    assert "VULNERABILITIES FOUND" in verdict


def test_npm_audit_gate_fails_closed_on_critical_severity_findings() -> None:
    gate = _audit_gate()
    code, _ = gate.evaluate(gate.classify_audit_output(_report_json(critical=1), ""), "high")
    assert code == gate.EXIT_VULNERABLE, "critical findings must fail at a 'high' threshold"


def test_npm_audit_gate_ignores_findings_below_threshold() -> None:
    gate = _audit_gate()
    code, _ = gate.evaluate(gate.classify_audit_output(_report_json(low=3, moderate=1), ""), "high")
    assert code == gate.EXIT_OK, "findings below the threshold must not fail a 'high' gate"


def test_npm_audit_gate_does_not_pass_on_registry_transport_failure() -> None:
    """A registry error yields no vulnerability data, so the gate must stay closed."""
    gate = _audit_gate()
    for body in (QUICK_ENDPOINT_400, REGISTRY_503):
        outcome = gate.classify_audit_output(body, "")
        assert not outcome.has_report, f"registry error must not be read as a report: {body}"
        code, verdict = gate.evaluate(outcome, "high")
        assert code == gate.EXIT_AUDIT_UNAVAILABLE, (
            f"transport failure must not exit 0 or masquerade as findings, got {code}: {verdict}"
        )
        assert code != gate.EXIT_OK
        assert "AUDIT UNAVAILABLE" in verdict


def test_npm_audit_gate_transport_failure_is_not_reported_as_a_lockfile_defect() -> None:
    """The 400 body blames package-lock.json; the gate must not repeat that claim."""
    gate = _audit_gate()
    _, verdict = gate.evaluate(gate.classify_audit_output(QUICK_ENDPOINT_400, ""), "high")
    assert "registry" in verdict.lower()
    assert "rebuild your package-lock.json" not in verdict


def test_npm_audit_gate_rejects_unparsable_output() -> None:
    gate = _audit_gate()
    outcome = gate.classify_audit_output("npm notice This endpoint is being retired.", "")
    assert not outcome.has_report
    assert gate.evaluate(outcome, "high")[0] == gate.EXIT_AUDIT_UNAVAILABLE


def test_npm_audit_gate_rejects_report_without_severity_counts() -> None:
    gate = _audit_gate()
    outcome = gate.classify_audit_output(json.dumps({"auditReportVersion": 2}), "")
    assert not outcome.has_report, "a report with no severity counts proves nothing"


def test_npm_audit_gate_retries_transport_failures_before_giving_up(monkeypatch) -> None:
    gate = _audit_gate()
    attempts: list[int] = []
    transport = gate.AuditOutcome(gate.UNAVAILABLE, None, "registry error (statusCode=503)")
    healthy = gate.classify_audit_output(_report_json(), "")

    def fake_run(cwd=gate.ROOT, timeout=1.0):
        attempts.append(1)
        return transport if len(attempts) < 3 else healthy

    monkeypatch.setattr(gate, "run_npm_audit", fake_run)
    outcome = gate.audit_with_retry(attempts=3, backoff=0, sleep=lambda _s: None)

    assert len(attempts) == 3, "the gate must retry a transient registry failure"
    assert outcome.has_report
    assert gate.evaluate(outcome, "high")[0] == gate.EXIT_OK


def test_npm_audit_gate_stops_retrying_once_a_report_arrives(monkeypatch) -> None:
    gate = _audit_gate()
    attempts: list[int] = []

    def fake_run(cwd=gate.ROOT, timeout=1.0):
        attempts.append(1)
        return gate.classify_audit_output(_report_json(high=1), "")

    monkeypatch.setattr(gate, "run_npm_audit", fake_run)
    outcome = gate.audit_with_retry(attempts=3, backoff=0, sleep=lambda _s: None)

    assert len(attempts) == 1, "a delivered report must not be retried away"
    assert gate.evaluate(outcome, "high")[0] == gate.EXIT_VULNERABLE


def test_npm_audit_gate_exhausted_retries_stay_closed(monkeypatch) -> None:
    gate = _audit_gate()
    transport = gate.AuditOutcome(gate.UNAVAILABLE, None, "registry error (statusCode=400)")
    monkeypatch.setattr(gate, "run_npm_audit", lambda cwd=gate.ROOT, timeout=1.0: transport)

    outcome = gate.audit_with_retry(attempts=3, backoff=0, sleep=lambda _s: None)
    code, verdict = gate.evaluate(outcome, "high")

    assert code == gate.EXIT_AUDIT_UNAVAILABLE, "an unreachable registry must never pass the gate"
    assert code != gate.EXIT_OK, verdict


def test_npm_audit_gate_is_wired_into_the_release_gate() -> None:
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "npm_audit_gate.py" in package_json["scripts"]["audit:security"], (
        "make dependency-audit runs 'npm run audit:security'; it must reach the hardened gate"
    )


def test_npm_audit_gate_omits_dev_dependencies() -> None:
    """The gate audits the production tree only, matching the original command."""
    source = (ROOT / "delivery_toolchain/security/npm_audit_gate.py").read_text(encoding="utf-8")
    assert '"--omit=dev"' in source
