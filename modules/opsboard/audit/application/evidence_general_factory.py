"""Bind ordinary canonical exports to the actual app's durable tenant owners."""
from __future__ import annotations

from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralSiteScoreEvidenceAdmission,
)
from modules.opsboard.audit.application.evidence_general_retention import (
    GeneralSiteScoreRetainedExport,
)
from modules.opsboard.audit.application.sitescore_decision_chain import (
    SiteScoreRecordedDecisionChainReader,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreCanonicalEvidenceResolver,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.retained_delivery import RetainedEvidenceDelivery
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.factory import PersistenceBundle
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.repositories import DurableRealizedSiteStore


class GeneralSiteScoreExportFactory:
    def __init__(self, bundle: PersistenceBundle, *, audit_log, evidence_store, queue) -> None:
        self.bundle, self.audit_log, self.evidence_store, self.queue = bundle, audit_log, evidence_store, queue

    def __call__(self, tenant_id: str) -> GeneralSiteScoreRetainedExport:
        bundle = self.bundle
        if (not isinstance(bundle, PersistenceBundle) or not bundle.is_durable
                or not isinstance(tenant_id, str) or not tenant_id.strip()
                or self.audit_log is not bundle.audit_log or self.evidence_store is not bundle.evidence_store
                or self.queue is not bundle.job_queue):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:app_durable_owner")
        try:
            resolver = bundle.network_review_resolver()
            if resolver is None or resolver._repository.engine is not bundle.engine:
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:canonical_review_owner")
            # Read the existing tenant domain state through the original owner;
            # construction never persists a projection or creates an approval.
            review = resolver._factory(resolver._repository.load(tenant_id), tenant_id)
            workflow = review._decision_workflow
            source = SiteScoreCanonicalEvidenceResolver(decisions=review._decision_repository,
                reports=review._sitescore_repository, approvals=workflow._approval_store,
                audit_log=self.audit_log, review_owner=review)
            source._binding(tenant_id)
            delivery = RetainedEvidenceDelivery(engine=bundle.engine, tenant_id=tenant_id,
                queue=self.queue, evidence_store=self.evidence_store, audit_log=self.audit_log)
            receipts = TenantScopedCommandReceiptStore(self.queue, "sitescore-general-export",
                transaction_factory=bundle.engine.transaction)
            realized = getattr(bundle, "sitescore_realized_store", None)
            chain_owner = None
            if isinstance(realized, DurableRealizedSiteStore):
                if realized._store.engine is not bundle.engine:
                    raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:canonical_realization_owner_binding")
                if isinstance(realized._store, TenantScopedDocumentStore):
                    if realized._store.tenant_id != tenant_id:
                        raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:canonical_realization_owner_binding")
                    scoped = realized._store
                else:
                    scoped = TenantScopedDocumentStore(realized._store, tenant_id)
                # Read only the actual tenant namespace. A legacy unscoped
                # realization is not reclassified or copied into this tenant.
                chain_owner = DurableRealizedSiteStore(scoped)
            admission = GeneralSiteScoreEvidenceAdmission(source,
                SiteScoreRecordedDecisionChainReader(source, chain_owner))
            return GeneralSiteScoreRetainedExport(admission, delivery, receipts)
        except SiteScoreEvidenceUnavailable:
            raise
        except Exception as exc:
            raise SiteScoreEvidenceUnavailable(
                "CANONICAL_EVIDENCE_INPUT_REQUIRED:canonical_durable_owner_unavailable"
            ) from exc
