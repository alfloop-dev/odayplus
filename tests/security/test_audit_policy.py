"""Audit policy + PII masking tests (ODP-SD-09 §11, §7; ODP-SA-04 §9)."""

from __future__ import annotations

from shared.audit.policy import (
    AuditOutcome,
    build_security_event,
    is_high_risk,
    mask_email,
    mask_phone,
    mask_text,
    requires_audit,
)
from shared.auth import (
    AccessRequest,
    Action,
    DataClassification,
    Decision,
    Environment,
    Principal,
    ResourceDescriptor,
)


def test_high_risk_actions_flagged() -> None:
    assert is_high_risk(Action.APPROVE)
    assert is_high_risk(Action.EXECUTE)
    assert not is_high_risk(Action.VIEW)


def test_requires_audit_for_mutations_and_exports() -> None:
    assert requires_audit(Action.CREATE)
    assert requires_audit(Action.EXPORT)
    assert requires_audit(Action.APPROVE)


def test_view_of_public_data_not_audited() -> None:
    assert not requires_audit(Action.VIEW, DataClassification.INTERNAL)


def test_view_of_restricted_data_audited() -> None:
    # SD-09 §11.2: reading Restricted data is audited
    assert requires_audit(Action.VIEW, DataClassification.RESTRICTED)


def test_mask_phone_keeps_last_three() -> None:
    assert mask_phone("0912345678") == "*******678"
    assert mask_phone("12") == "**"


def test_mask_email_masks_local_part() -> None:
    assert mask_email("alice@example.com") == "a****@example.com"
    assert mask_email("notanemail") == "**********"


def test_mask_text_generic() -> None:
    assert mask_text("secret") == "******"
    assert mask_text("secret", visible=2) == "****et"


def test_build_security_event_captures_auth_context() -> None:
    # ODP-AC-AUTH-005: actor, time, IP, resource, reason recorded.
    request = AccessRequest(
        principal=Principal(subject_id="user-1"),
        action=Action.APPROVE,
        resource=ResourceDescriptor(
            type="priceops",
            resource_id="plan-9",
            tenant_id="tenant-a",
            data_classification=DataClassification.RESTRICTED,
        ),
        environment=Environment(source_ip="10.1.2.3"),
    )
    decision = Decision.deny("role does not permit", policy_id="rbac")
    event = build_security_event(request, decision)

    assert event.event_type == "security.authorization"
    assert event.outcome == AuditOutcome.DENY
    assert event.actor == "user-1"
    assert event.resource == "priceops/plan-9"
    assert event.metadata["source_ip"] == "10.1.2.3"
    assert event.metadata["reason"] == "role does not permit"
    assert event.metadata["policy_id"] == "rbac"
    assert event.metadata["data_classification"] == "RESTRICTED"
    # canonical record fields still populated
    assert event.event_id
    assert event.occurred_at
    data = event.to_dict()
    assert data["outcome"] == "deny"
