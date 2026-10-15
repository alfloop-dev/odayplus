"""Inbox facts retain authoritative bindings and nulls instead of UI guesses."""

from copy import deepcopy

import pytest

from modules.listing.application.intake_authorization import mask_intake
from shared.auth import DataClassification, Principal, Role, Scope
from shared.infrastructure.persistence.operator_intake_workflow import project_operator_intake


def _facts():
    intake = {
        "intake_id": "intake-a", "scope": {"tenant_id": "tenant-a", "assigned_area_id": "area-a"},
        "submitted_by": "subject-a", "submitted_at": "2026-10-01T01:00:00Z",
        "updated_at": "2026-10-01T02:00:00Z", "last_observed_at": None,
        "assignment_id": "assignment-a", "sla_instance_id": "sla-a",
        "state": "SUBMITTED", "intake_method": "URL", "version": 3,
        "original_url": "https://example.invalid/private-original",
        "operator_presentation": {"capturedAt": "2026-10-01T09:00:00Z", "submitter": "Display Name",
                                  "ownerSubjectId": "forged", "dueAt": "forged"},
    }
    assignment = {"assignment_id": "assignment-a", "intake_id": "intake-a", "tenant_id": "tenant-a",
                  "version": 7, "status": "CLAIMED", "owner_subject_id": "subject-b",
                  "due_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-01T03:00:00Z"}
    sla = {"sla_instance_id": "sla-a", "assignment_id": "assignment-a", "intake_id": "intake-a",
           "tenant_id": "tenant-a", "version": 11, "state": "PAUSED", "due_at": "2026-10-02T04:00:00Z",
           "updated_at": "2026-10-01T04:00:00Z"}
    return intake, assignment, sla


def test_projection_reads_linked_resource_versions_and_true_timestamps():
    intake, assignment, sla = _facts()
    row = project_operator_intake(intake, {"assignment-a": assignment}, {"sla-a": sla})
    assert row["submittedBy"] == "subject-a" and row["submitter"] == "subject-a"
    assert row["ownerSubjectId"] == "subject-b" and row["assignmentVersion"] == 7
    assert row["slaVersion"] == 11 and row["dueAt"] == sla["due_at"]
    assert row["updatedAt"] == sla["updated_at"]
    assert row["lastObservedAt"] is None and row["submittedAt"] != row["capturedAt"]
    assert row["assignedAreaId"] == "area-a"
    assert "original_url" not in row and "operator_presentation" not in row


@pytest.mark.parametrize("resource,field", [
    ("assignment", "tenant_id"), ("assignment", "intake_id"), ("assignment", "assignment_id"),
    ("sla", "tenant_id"), ("sla", "intake_id"), ("sla", "assignment_id"), ("sla", "sla_instance_id"),
])
def test_projection_rejects_cross_resource_and_cross_tenant_links(resource, field):
    intake, assignment, sla = _facts()
    (assignment if resource == "assignment" else sla)[field] = "foreign"
    row = project_operator_intake(intake, {"assignment-a": assignment}, {"sla-a": sla})
    assert row["slaInstanceId"] is None and row["slaVersion"] is None
    if resource == "assignment":
        assert row["assignmentId"] is None and row["ownerSubjectId"] is None and row["dueAt"] is None


def test_missing_facts_do_not_fall_back_to_labels_capture_or_lexical_sla():
    intake, _, _ = _facts()
    for name in ("submitted_at", "submitted_by", "updated_at"):
        intake.pop(name)
    row = project_operator_intake(intake, {}, {})
    assert all(row[name] is None for name in (
        "submittedAt", "submittedBy", "updatedAt", "lastObservedAt", "dueAt", "ownerSubjectId",
    ))


def test_projection_masking_does_not_expose_canonical_raw_aliases_or_contact_values():
    intake, assignment, sla = _facts()
    intake["fields"] = [{"field_path": "contactPhone", "classification": "RESTRICTED",
                         "effective": "secret-phone", "parsed": "secret-phone"}]
    intake["operator_presentation"]["parsedFields"] = {"contactPhone": {
        "sourceValue": "secret-phone", "normalizedValue": "secret-phone", "correctedValue": None,
    }}
    principal = Principal(subject_id="subject-a", roles=frozenset({Role.SITE_REVIEWER}),
                          scope=Scope(tenant_id="tenant-a", clearance=DataClassification.INTERNAL))
    original = deepcopy(intake)
    masked = mask_intake(principal, project_operator_intake(intake, {"assignment-a": assignment}, {"sla-a": sla}))
    assert "secret-phone" not in str(masked)
    assert "private-original" not in str(masked)
    assert intake == original


def test_nonterminal_duplicate_checks_resource_authority_before_disclosing_identity():
    from modules.opsboard.application.network_listings import (
        InMemoryAssistedIntakeRepository,
        NetworkListingService,
    )

    repo = InMemoryAssistedIntakeRepository()
    repo.save_intake({"id": "hidden-intake-uuid", "tenantId": "tenant-a",
                      "canonicalUrl": "https://example.invalid/hidden", "stage": "SUBMITTED"})
    service = NetworkListingService(intake_repository=repo)
    authorized = []

    def deny(resource):
        authorized.append(resource["id"])
        raise PermissionError("SCOPE_DENIED")

    with pytest.raises(PermissionError, match="^SCOPE_DENIED$"):
        service.submit_intake(url="https://example.invalid/hidden", heat_zone_id=None,
                              actor_role_id="expansion_user", actor_name="verified-subject",
                              idempotency_key="different-submission-key", correlation_id="test-correlation",
                              authorize_existing=deny)
    assert authorized == ["hidden-intake-uuid"]
