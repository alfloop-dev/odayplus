"""Read-only canonical source choices and the exact masked export preview.

A preview is not retention, an external delivery ACK, a new approval or full
QA03 coverage. No Governance UI row or caller actor is a source owner.
"""
from __future__ import annotations

from modules.opsboard.audit.application.evidence_general_admission import (
    SUPPORTED_CLASSIFICATION,
    GeneralDecisionEvidenceSelection,
)
from modules.opsboard.audit.application.evidence_general_retention import (
    GeneralSiteScoreRetainedExport,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.integrity import sha256_hex
from shared.auth import Action, Principal, rbac_allows
from shared.workflow.sitescore import SiteScoreDecision


class GeneralSiteScoreEvidencePreview:
    def __init__(self, service: GeneralSiteScoreRetainedExport) -> None:
        if not isinstance(service, GeneralSiteScoreRetainedExport):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:general_owner")
        self.service = service

    def _authorize(self, principal: Principal) -> None:
        self.service._binding(principal)
        if (not isinstance(principal, Principal) or not principal.authenticated
                or not rbac_allows(principal, "audit", Action.EXPORT)
                or not rbac_allows(principal, "operator_console", Action.VIEW)
                or not principal.scope.permits_module("operator_console")
                or not rbac_allows(principal, "sitescore", Action.VIEW)
                or not principal.scope.permits_classification(SUPPORTED_CLASSIFICATION)):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED")

    def sources(self, principal: Principal) -> list[dict]:
        self._authorize(principal)
        source_owner = self.service.admission.source_owner
        choices = []
        with self.service.engine.transaction():
            decisions = source_owner.decisions.list_decisions()
            if not isinstance(decisions, list) or any(not isinstance(row, SiteScoreDecision) for row in decisions):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:canonical_decisions")
            for decision in decisions:
                if decision.tenant_id != principal.tenant_id:
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_OWNER_BINDING_REQUIRED")
                # Pending workflow rows are not exported as a completed decision.
                if decision.network_action is None or not decision.history:
                    continue
                if type(decision.version) is not int or decision.version < 1:
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_FACTS_INVALID")
                try:
                    source = source_owner.resolve_source(principal,
                        SiteScoreEvidenceSelector(decision.decision_id, decision.version))
                except SiteScoreEvidenceDenied:
                    # Current object/area grants determine visible choices.
                    continue
                choices.append({"decision_id": source.decision_id, "decision_version": source.decision_version,
                    "candidate_id": source.candidate_id, "decided_at": source.decided_at.isoformat(),
                    "period_start": min(event.occurred_at for event in source.events).isoformat(),
                    "period_end": max(event.occurred_at for event in source.events).isoformat(),
                    "source_digest": source.source_digest})
        return choices

    def preview(self, principal: Principal, selection: GeneralDecisionEvidenceSelection) -> dict:
        self._authorize(principal)
        result = self.service.admission.resolve(principal, selection)
        document = result.document()
        return {"document": document, "artifact_sha256": sha256_hex(document),
            "decision_id": result.source.decision_id, "decision_version": result.source.decision_version,
            "reason": result.selection.reason, "classification": result.classification.name,
            "retention_class": result.retention_policy.retention_class,
            "retention_days": result.retention_policy.retention_days,
            "formats": ["json", "csv", "pdf"], "retained": False, "external_acknowledged": False}
