from __future__ import annotations

import pytest

from modules.dealroom.application.outcome_audit import (
    generate_dealroom_outcome_audit_receipt,
    verify_audit_receipt,
)
from modules.dealroom.domain.confidential_access import (
    ConfidentialAccessAttempt,
    ConfidentialAccessAuditor,
    ConfidentialAccessDecision,
    ConfidentialLeakError,
    ConfidentialLevel,
    assert_no_confidential_leak,
    create_identity_proof,
)
from shared.auth.rbac import Action, Role


def _make_valid_context(actor_id: str, role: Role | str, tenant_id: str = "tenant-avm-001") -> dict:
    return {
        "authenticated": True,
        "verified_identity": True,
        "identity_proof_sha256": create_identity_proof(actor_id, role, tenant_id),
        "tenant_id": tenant_id,
        "data_room_access": True,
        "tenant_matched": True,
        "clearance": "HIGH",
    }


def test_confidential_access_permitted_for_finance_legal() -> None:
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context=_make_valid_context("usr-fin-001", Role.FINANCE_LEGAL),
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.PERMIT
    assert "usr-fin-001" in receipt["actor_id"]
    assert receipt["decision"] == "PERMIT"


def test_confidential_access_denied_for_unauthorized_roles() -> None:
    for forbidden_role in (Role.REGIONAL_SUPERVISOR, Role.FRANCHISEE, Role.MARKETING_MANAGER):
        attempt = ConfidentialAccessAttempt(
            actor_id="usr-supervisor-999",
            role=forbidden_role,
            resource="dealroom",
            action=Action.VIEW,
            context=_make_valid_context("usr-supervisor-999", forbidden_role),
        )
        decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
            attempt, ConfidentialLevel.HIGH
        )
        assert decision == ConfidentialAccessDecision.DENY
        assert "forbidden" in reason.lower() or "denied" in reason.lower()


def test_audit_receipt_generation_redacts_confidential_values_and_calculates_sha256() -> None:
    attempts = [
        ("usr-fin-001", Role.FINANCE_LEGAL, "dealroom", Action.VIEW, _make_valid_context("usr-fin-001", Role.FINANCE_LEGAL)),
        ("usr-sup-002", Role.REGIONAL_SUPERVISOR, "dealroom", Action.VIEW, _make_valid_context("usr-sup-002", Role.REGIONAL_SUPERVISOR)),
    ]
    raw_prices = (15800000.0, 22000000.0)
    receipt = generate_dealroom_outcome_audit_receipt(attempts, forbidden_raw_prices=raw_prices, dataset_snapshot_hash="a" * 64)

    assert receipt["kind"] == "avm-confidential-access-audit-receipt"
    assert receipt["permitted_count"] == 1
    assert receipt["denied_count"] == 1
    assert len(receipt["sha256"]) == 64
    assert verify_audit_receipt(receipt, expected_snapshot_hash="a" * 64) is True


def test_assert_no_confidential_leak_raises_on_raw_price_leak() -> None:
    leaking_receipt = {
        "kind": "audit",
        "unmasked_price": 12500000.0,
    }
    with pytest.raises(ConfidentialLeakError, match="Confidential price field pattern"):
        assert_no_confidential_leak(leaking_receipt)

    leaking_text = {"note": "raw price was 15800000.0"}
    with pytest.raises(ConfidentialLeakError, match="Confidential raw value"):
        assert_no_confidential_leak(leaking_text, forbidden_raw_values=(15800000.0,))


# --- B11 ABAC Context Mutation Tests ---

def test_b11_unauthenticated_principal_denied() -> None:
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context={"authenticated": False},
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "not authenticated" in reason


def test_b11_missing_data_room_access_authority_denied() -> None:
    ctx = _make_valid_context("usr-fin-001", Role.FINANCE_LEGAL)
    ctx["data_room_access"] = False
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context=ctx,
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "data_room_access" in reason


def test_b11_mismatched_tenant_denied() -> None:
    ctx = _make_valid_context("usr-fin-001", Role.FINANCE_LEGAL)
    ctx["tenant_matched"] = False
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context=ctx,
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "Tenant authority mismatch" in reason


def test_b11_insufficient_clearance_level_denied() -> None:
    ctx = _make_valid_context("usr-fin-001", Role.FINANCE_LEGAL)
    ctx["clearance"] = "PUBLIC"
    attempt = ConfidentialAccessAttempt(
        actor_id="usr-fin-001",
        role=Role.FINANCE_LEGAL,
        resource="dealroom",
        action=Action.VIEW,
        context=ctx,
    )
    decision, reason, receipt = ConfidentialAccessAuditor.evaluate_access(
        attempt, ConfidentialLevel.HIGH
    )
    assert decision == ConfidentialAccessDecision.DENY
    assert "Insufficient clearance level" in reason
