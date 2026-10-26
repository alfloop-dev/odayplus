"""Canonical producer/detail engineering controls, never signed human QA03 UAT."""
import json
from dataclasses import replace

import pytest

from modules.opsboard.audit.application.sitescore_decision_chain import (
    RecordedSiteScoreDecisionChain,
    SiteScoreRecordedDecisionChainReader,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from modules.sitescore.domain.scoring import SiteScoreRecommendation
from shared.audit.integrity import sha256_hex
from shared.auth import DataClassification
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableRealizedSiteStore
from shared.workflow.sitescore import CandidateSiteRealizationHook, DecisionAction
from tests.integration.test_audit_sitescore_evidence_resolver import (
    case as case,
)
from tests.integration.test_audit_sitescore_evidence_resolver import (
    principal,
    resolver,
    rows,
    selected,
)
from tests.unit.test_network_canonical_review_projection import report
from tests.unit.test_network_review_canonical_action_metadata import (
    ACTOR,
    TENANT,
    components,
    install_real_related_producers,
)


@pytest.fixture
def approved_chain(tmp_path, request):
    parts = components(tmp_path / "owned-chain-engineering.sqlite")
    engine, scores, decisions, _audit, workflow, service = parts
    try:
        approvals, _intake, _candidate = install_real_related_producers(engine, decisions, workflow, service)
        realized = DurableRealizedSiteStore(TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT))
        workflow.register_hook(CandidateSiteRealizationHook(realized))
        partial = getattr(request, "param", None) == "partial"
        value = replace(report(), payback_period=None, payback_p50_months=None,
            buildout_capex=None, recommendation=SiteScoreRecommendation.WAIT) if partial else report()
        frozen = scores.save_report(value)
        decision = workflow.open_decision(frozen, created_by="engineering-proposer", tenant_id=TENANT,
            correlation_id="engineering-chain-create", verified_creator=True)
        workflow.submit_for_review(decision.decision_id, submitted_by="engineering-proposer",
            verified_submitter=True, submission_summary="Engineering canonical producer, not human acceptance",
            correlation_id="engineering-chain-submit")
        outcome = workflow.decide(decision.decision_id, action=DecisionAction.APPROVE, actor=ACTOR,
            reason="Recorded engineering approval, not a human UAT signoff", network_action="WAIT" if partial else "GO",
            conditions="Financial evidence is still required" if partial else None,
            actor_role_id="siteReviewer", correlation_id="engineering-chain-decide")
        yield parts, approvals, outcome, realized, frozen
    finally:
        engine.close()


def test_true_frozen_prediction_human_signature_and_realization_joins_are_partial_without_outcome(approved_chain):
    parts, approvals, outcome, realized, frozen = approved_chain
    before = rows(parts[0])
    reader = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals), realized)
    result = reader.read(principal(), selected(outcome))
    value = result.document()
    assert value["prediction"]["intervals"]["m3"] == frozen.m3.to_dict()
    assert value["prediction"]["report_sha256"] == sha256_hex(frozen.to_dict())
    assert value["prediction"]["source_snapshot_ids"] == list(frozen.source_snapshot_ids)
    assert value["prediction"]["model_reference"] == frozen.model_version
    assert value["recommendation"]["recorded_recommendation"] == frozen.recommendation.value
    assert value["human_decision"]["approval_id"] == outcome.decision.approval_id
    assert value["human_decision"]["approval_id"] != outcome.decision.decision_id
    assert value["human_decision"]["actor"] != ACTOR
    assert value["human_decision"]["reason_sha256"] == sha256_hex(outcome.decision.history[-1].reason)
    assert value["human_decision"]["reason_masked"] != outcome.decision.history[-1].reason
    execution = value["execution"]
    assert execution["status"] == "recorded"
    assert execution["record"]["decision_id"] == outcome.decision.decision_id
    assert execution["record"]["baseline_trajectory"] == frozen.baseline_trajectory()
    assert execution["record"]["kind"] == "canonical_candidate_realization"
    assert value["outcome"]["status"] == "unavailable" and value["outcome"]["record"] is None
    assert value["coverage"] == {"state": "degraded", "complete": False, "missing": ["outcome"]}
    assert value["limits"]["model_readiness_claimed"] is False
    assert rows(parts[0]) == before
    parts[1].save_report(replace(report(), model_version="recorded-later-model"))
    after_saved = rows(parts[0])
    assert reader.read(principal(), selected(outcome)) == result
    assert rows(parts[0]) == after_saved and after_saved != before


@pytest.mark.parametrize("approved_chain", ["partial"], indirect=True)
def test_partial_financial_report_wait_decision_has_truthful_readonly_audit(approved_chain):
    parts, approvals, outcome, realized, frozen = approved_chain
    before = rows(parts[0])
    value = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals), realized).read(
        principal(), selected(outcome)).document()
    assert value["prediction"]["intervals"]["m12"] == frozen.m12.to_dict()
    assert value["prediction"]["payback_period"] is None
    assert value["prediction"]["payback_p50_months"] is None
    assert value["human_decision"]["approval_id"] == outcome.decision.approval_id
    assert value["execution"]["status"] == "not_applicable"
    assert rows(parts[0]) == before


def test_event_realized_count_without_real_owner_does_not_claim_execution(approved_chain):
    parts, approvals, outcome, _realized, _frozen = approved_chain
    before = rows(parts[0])
    document = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals)).read(principal(), selected(outcome)).document()
    assert document["execution"]["status"] == "unavailable"
    assert document["execution"]["record"] is None and "execution" in document["coverage"]["missing"]
    assert rows(parts[0]) == before


def test_rejected_decision_does_not_invent_an_execution_and_retains_actual_human_evidence(case):
    parts, approvals, outcome = case
    before = rows(parts[0])
    value = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals)).read(principal(), selected(outcome)).document()
    assert value["execution"]["status"] == "not_applicable" and value["execution"]["record"] is None
    assert value["human_decision"]["approval_status"] == "rejected"
    assert value["outcome"]["record"] is None and value["coverage"]["complete"] is False
    assert rows(parts[0]) == before


@pytest.mark.parametrize("change", ["scope", "clearance", "foreign_realization_owner", "wrong_round", "baseline"])
def test_current_scope_and_real_realization_binding_cannot_be_replaced_by_unrelated_facts(approved_chain, change):
    parts, approvals, outcome, realized, _frozen = approved_chain
    actor = principal()
    if change == "scope":
        actor = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"not-this-candidate"})))
    elif change == "clearance":
        actor = replace(actor, scope=replace(actor.scope, clearance=DataClassification.INTERNAL))
    elif change == "foreign_realization_owner":
        realized = DurableRealizedSiteStore(TenantScopedDocumentStore(SqliteDocumentStore(parts[0]), "other-engineering-tenant"))
    else:
        original = realized.get(outcome.decision.candidate_site_id)
        row = replace(original, decision_id="another-real-round") if change == "wrong_round" else replace(
            original, baseline_trajectory={**original.baseline_trajectory, "m1": original.baseline_trajectory["m1"] + 1})
        realized.put(row)
    before = rows(parts[0])
    reader = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals), realized)
    if change == "wrong_round":
        result = reader.read(actor, selected(outcome)).document()
        assert result["execution"]["status"] == "unavailable" and result["execution"]["record"] is None
    else:
        with pytest.raises((SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable)):
            reader.read(actor, selected(outcome))
    assert rows(parts[0]) == before


@pytest.mark.parametrize("extra", ["opaque_report", "raw_source", "approval_record"])
def test_allowlist_detail_cannot_acquire_native_opaque_fields_after_construction(approved_chain, extra):
    parts, approvals, outcome, realized, _frozen = approved_chain
    actual = SiteScoreRecordedDecisionChainReader(resolver(parts, approvals), realized).read(principal(), selected(outcome))
    value = actual.document()
    value["prediction"][extra] = {"engineering-private-value": "must not render"}
    changed = RecordedSiteScoreDecisionChain(actual.source_digest, json.dumps(value).encode())
    with pytest.raises(SiteScoreEvidenceUnavailable, match="recorded_chain_shape"):
        changed.document()
