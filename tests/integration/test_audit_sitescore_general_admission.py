"""Owned canonical SQLite source controls, not production/export acceptance.

No program, purpose, retention approval or source licence record is invented.
The existing canonical fixture records an engineering SiteScore review and
immutable events; it does not certify real model/source readiness or human UAT.
"""
from dataclasses import replace

import pytest

from modules.opsboard.audit.application.evidence_general_admission import (
    GENERAL_SCHEMA,
    GeneralDecisionEvidenceSelection,
    GeneralSiteScoreEvidenceAdmission,
)
from modules.opsboard.audit.application.evidence_projection import EvidencePolicyScope
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.persistence import resolve_retention_policy
from shared.audit.policy import mask_text
from shared.auth import DataClassification, Role
from tests.integration.test_audit_sitescore_evidence_resolver import case as case
from tests.integration.test_audit_sitescore_evidence_resolver import (
    principal,
    resolver,
    rows,
    selected,
)


def setup(case):
    parts, approvals, outcome = case
    owner = resolver(parts, approvals)
    actor = principal()
    source = owner.resolve_source(actor, selected(outcome))
    scope = EvidencePolicyScope("json", "authenticated-response", source.events[0].occurred_at,
                                source.events[-1].occurred_at)
    request = GeneralDecisionEvidenceSelection(selected(outcome), "Inspect the recorded decision", scope)
    return GeneralSiteScoreEvidenceAdmission(owner), actor, request


def test_general_reference_export_uses_existing_policy_without_new_approval_records(case):
    service, actor, request = setup(case)
    before = rows(case[0][0])
    result = service.resolve(actor, request)
    document = result.document()
    assert document["schema"] == GENERAL_SCHEMA
    assert document["classification"] == "CONFIDENTIAL"
    assert document["masking_profile"] == "masked" and document["actor_fields_masked"] is True
    assert result.retention_policy == resolve_retention_policy("confidential")
    assert result.selection.reason == "Inspect the recorded decision"
    assert result.selection.program_label is None
    assert document["limits"] == {"content": "recorded_decision_and_audit_references_only",
        "source_content_included": False, "model_readiness_claimed": False,
        "subsidy_eligibility_claimed": False}
    assert document["sections"]["decision_log"][0]["decision_actor"] == mask_text(result.source.decision_actor)
    assert document["sections"]["decision_log"][0]["report_sha256"] == result.source.report_sha256
    assert [row["event_id"] for row in document["sections"]["audit"]] == [row.event_id for row in result.source.events]
    assert all(row["actor"] == mask_text(event.actor)
               for row, event in zip(document["sections"]["audit"], result.source.events, strict=True))
    assert not hasattr(service, "program_owner") and not hasattr(service, "retention_approval_owner")
    assert rows(case[0][0]) == before


def test_program_label_is_selection_not_subsidy_approval_or_document_authority(case):
    service, actor, request = setup(case)
    before = rows(case[0][0])
    ordinary = service.resolve(actor, request)
    chosen = service.resolve(actor, replace(request, program_label="User-selected claim label"))
    assert chosen.document() == ordinary.document()
    assert chosen.selection.program_label == "User-selected claim label"
    assert chosen.document()["limits"]["subsidy_eligibility_claimed"] is False
    assert rows(case[0][0]) == before


@pytest.mark.parametrize("reason", ["", "   ", None, 13])
def test_real_export_reason_is_required_without_fabricating_purpose_approval(case, reason):
    _service, _actor, request = setup(case)
    before = rows(case[0][0])
    with pytest.raises(ValueError):
        replace(request, reason=reason)
    assert rows(case[0][0]) == before


@pytest.mark.parametrize("denial", ["clearance", "operator_module", "candidate", "tenant", "export_role"])
def test_current_field_object_tenant_and_export_authority_are_rechecked(case, denial):
    service, actor, request = setup(case)
    if denial == "clearance":
        actor = replace(actor, scope=replace(actor.scope, clearance=DataClassification.INTERNAL))
    elif denial == "operator_module":
        actor = replace(actor, scope=replace(actor.scope, modules=frozenset({"sitescore"})))
    elif denial == "candidate":
        actor = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"not-this-candidate"})))
    elif denial == "tenant":
        actor = replace(actor, scope=replace(actor.scope, tenant_id="foreign-tenant"))
    else:
        actor = replace(actor, roles=frozenset({Role.SITE_REVIEWER}))
    before = rows(case[0][0])
    with pytest.raises((SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable)):
        service.resolve(actor, request)
    assert rows(case[0][0]) == before


@pytest.mark.parametrize("format", ["csv", "pdf"])
def test_general_lane_rejects_unimplemented_bytes_formats(case, format):
    service, actor, request = setup(case)
    before = rows(case[0][0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="unimplemented_artifact_format"):
        service.resolve(actor, replace(request, scope=replace(request.scope, format=format)))
    assert rows(case[0][0]) == before


def test_period_scope_cannot_exclude_recorded_events_and_claim_complete(case):
    service, actor, request = setup(case)
    before = rows(case[0][0])
    with pytest.raises(SiteScoreEvidenceDenied, match="PERIOD_DENIED"):
        service.resolve(actor, replace(request, scope=replace(request.scope, period_start=request.scope.period_end)))
    assert rows(case[0][0]) == before


def test_general_lane_does_not_claim_an_unimplemented_external_destination(case):
    service, actor, request = setup(case)
    before = rows(case[0][0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="unimplemented_artifact_destination"):
        service.resolve(actor, replace(request, scope=replace(request.scope, destination="external-email")))
    assert rows(case[0][0]) == before
