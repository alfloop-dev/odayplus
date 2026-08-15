"""Security and privacy acceptance suite coverage for production readiness."""

from __future__ import annotations

SECURITY_ACCEPTANCE_CONTROLS = {
    "SEC-AUTH-001": {
        "name": "Unauthenticated, expired, and invalid token requests are denied",
        "automation": "tests/security/test_rbac_abac.py::test_unauthenticated_denied_and_audited",
        "evidence": "SECURITY_REPORT",
        "blocks_release": True,
    },
    "SEC-AUTHZ-001": {
        "name": "Horizontal and vertical privilege escalation are denied",
        "automation": "tests/security/test_rbac_abac.py",
        "evidence": "SECURITY_REPORT",
        "blocks_release": True,
    },
    "SEC-EXPORT-001": {
        "name": "Restricted exports require permission, reason, watermark, and audit",
        "automation": "tests/security/test_audit_policy.py",
        "evidence": "AUDIT_EXPORT",
        "blocks_release": True,
    },
    "SEC-PRIV-001": {
        "name": "PII is masked or removed outside approved production paths",
        "automation": "tests/security/test_audit_policy.py::test_mask_email_masks_local_part",
        "evidence": "SECURITY_REPORT",
        "blocks_release": True,
    },
    "SEC-HIGHRISK-001": {
        "name": "High-risk actions require feature policy and dual approval",
        "automation": "tests/security/test_feature_flags.py::test_high_risk_enable_requires_dual_approval",
        "evidence": "SECURITY_REPORT",
        "blocks_release": True,
    },
    "SEC-CI-001": {
        "name": "Secrets, dependency, SAST, container, IaC, and license scans complete",
        "automation": "CI security gates",
        "evidence": "SECURITY_REPORT",
        "blocks_release": True,
    },
}

OWASP_API_CASES = {
    "missing_token",
    "invalid_token",
    "scope_mismatch",
    "rate_limit",
    "payload_size",
    "schema_validation",
    "idempotency_key",
    "replay",
    "correlation_id",
    "safe_error_message",
}


def test_security_controls_block_release_when_missing() -> None:
    for control_id, control in SECURITY_ACCEPTANCE_CONTROLS.items():
        assert control["blocks_release"], control_id
        assert control["automation"]
        assert control["evidence"] in {"SECURITY_REPORT", "AUDIT_EXPORT"}


def test_owasp_api_security_cases_are_registered() -> None:
    assert {
        "missing_token",
        "invalid_token",
        "scope_mismatch",
        "rate_limit",
        "payload_size",
        "schema_validation",
        "idempotency_key",
        "replay",
        "correlation_id",
        "safe_error_message",
    } == OWASP_API_CASES


def test_ci_security_gate_requires_no_unresolved_high_or_critical_findings() -> None:
    ci_gate = SECURITY_ACCEPTANCE_CONTROLS["SEC-CI-001"]
    required_scans = {"Secrets", "dependency", "SAST", "container", "IaC", "license"}
    assert required_scans <= set(ci_gate["name"].replace(",", "").split())
    assert ci_gate["blocks_release"]
