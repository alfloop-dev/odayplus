"""General Decision/Audit export admission under existing shared policy.

This lane implements Audit Evidence UI Spec section 9, not the section 10
subsidy eligibility matrix. A program label and export reason are selections,
not approved business records. Only the existing SiteScore resource contract's
fixed reference fields are supported; raw reports, source bytes and arbitrary
metadata are excluded. This internal stage has no HTTP mount or delivery ACK.
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.opsboard.audit.application.evidence_general_document import (
    CHAIN_GENERAL_SCHEMA,
    CHAIN_LIMITS,
    GENERAL_SCHEMA,
    LIMITS,
    validate_general_document,
)
from modules.opsboard.audit.application.evidence_projection import (
    AUDIT_FIELDS,
    DECISION_FIELDS,
    EvidencePolicyScope,
    SafeSiteScoreSections,
    project_sitescore_sections,
)
from modules.opsboard.audit.application.sitescore_decision_chain import (
    RecordedSiteScoreDecisionChain,
    SiteScoreRecordedDecisionChainReader,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    ResolvedSiteScoreEvidence,
    SiteScoreCanonicalEvidenceResolver,
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from modules.opsboard.audit.domain.evidence import AUDIT_EVIDENCE_POLICY_VERSION
from shared.audit.integrity import sha256_hex
from shared.audit.persistence import EvidenceRetentionPolicy, resolve_retention_policy
from shared.audit.policy import mask_email, mask_text
from shared.auth import DataClassification, Principal
from shared.auth.abac import ResourceDescriptor

# This is the existing server resource policy used by SiteScore's real read
# guard, not a new classification asserted by an export request or registry row.
RESOURCE_CLASSIFICATION = ResourceDescriptor(type="sitescore").data_classification
SUPPORTED_CLASSIFICATION = DataClassification.CONFIDENTIAL


def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)


@dataclass(frozen=True)
class GeneralDecisionEvidenceSelection:
    selector: SiteScoreEvidenceSelector
    reason: str
    scope: EvidencePolicyScope
    program_label: str | None = None
    restricted_confirmation: bool = False

    def __post_init__(self) -> None:
        if (not isinstance(self.selector, SiteScoreEvidenceSelector)
                or not isinstance(self.reason, str) or not self.reason.strip()
                or not isinstance(self.scope, EvidencePolicyScope)
                or type(self.restricted_confirmation) is not bool
                or (self.program_label is not None and (
                    not isinstance(self.program_label, str) or not self.program_label.strip()))):
            raise ValueError("Actual source selection, reason and typed scope required")
        object.__setattr__(self, "reason", self.reason.strip())
        if self.program_label is not None:
            object.__setattr__(self, "program_label", self.program_label.strip())


def _masked_actor(value: str) -> str:
    # Ordinary exports do not imply unmask permission. Keep exact actors in
    # canonical internal records/audit, and explicitly mask public actor fields.
    return mask_email(value) if "@" in value else mask_text(value)


def _masked_sections(source: ResolvedSiteScoreEvidence) -> SafeSiteScoreSections:
    original = project_sitescore_sections(source)
    sections = []
    for name, rows in original.section_rows:
        sections.append((name, tuple(tuple(
            (key, _masked_actor(value) if key in {"actor", "decision_actor"} else value)
            for key, value in row) for row in rows)))
    result = SafeSiteScoreSections(source.source_digest, tuple(sections))
    result.for_format("json")
    return result


@dataclass(frozen=True)
class ResolvedGeneralDecisionEvidence:
    source: ResolvedSiteScoreEvidence
    selection: GeneralDecisionEvidenceSelection
    safe_sections: SafeSiteScoreSections
    classification: DataClassification
    retention_policy: EvidenceRetentionPolicy
    policy_fingerprint: str
    recorded_chain: RecordedSiteScoreDecisionChain | None = None

    def document(self) -> dict[str, object]:
        if self.selection.scope.format != "json":
            raise _missing("unimplemented_artifact_format")
        if (self.classification != SUPPORTED_CLASSIFICATION
                or self.policy_fingerprint != general_policy_fingerprint(self.classification, include_recorded_chain=self.recorded_chain is not None)):
            raise _missing("general_reference_field_policy_changed")
        projection = self.safe_sections.for_format("json")
        document = {"schema": CHAIN_GENERAL_SCHEMA if self.recorded_chain else GENERAL_SCHEMA,
                "source_digest": projection["source_digest"], "classification": self.classification.name,
                "masking_profile": "masked", "actor_fields_masked": True, "sections": projection["sections"],
                "limits": CHAIN_LIMITS if self.recorded_chain else LIMITS}
        if self.recorded_chain is not None:
            document["recorded_chain"] = self.recorded_chain.document()
        return validate_general_document(document)


def general_policy_fingerprint(classification: DataClassification, *, include_recorded_chain: bool = False) -> str:
    if classification != SUPPORTED_CLASSIFICATION:
        raise _missing("unsupported_reference_classification")
    retention = resolve_retention_policy(classification.name.lower(), sensitive=False)
    policy = {"policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
                       "resource_type": "sitescore", "classification": classification.name,
                       "classification_owner": "shared.auth.abac.ResourceDescriptor",
                       "decision_fields": DECISION_FIELDS, "audit_fields": AUDIT_FIELDS,
                       "masking_profile": "masked", "actor_fields_masked": True,
                       "retention_class": retention.retention_class,
                       "retention_days": retention.retention_days}
    if include_recorded_chain:
        policy["recorded_chain_schema"] = "sitescore.recorded-decision-chain.v1"
    return sha256_hex(policy)


class GeneralSiteScoreEvidenceAdmission:
    """A concrete current-policy adapter, without fabricated approval records.

    Higher classifications/raw data need actual corresponding owner admission;
    second confirmation alone never supplies sensitive authority. They are
    unsupported here rather than silently downgraded to this resource contract.
    """

    def __init__(self, source_owner: SiteScoreCanonicalEvidenceResolver,
                 recorded_chain_owner: SiteScoreRecordedDecisionChainReader | None = None) -> None:
        if not isinstance(source_owner, SiteScoreCanonicalEvidenceResolver):
            raise _missing("canonical_source_owner")
        if recorded_chain_owner is not None and (not isinstance(recorded_chain_owner, SiteScoreRecordedDecisionChainReader)
                or recorded_chain_owner.source_owner is not source_owner):
            raise _missing("same_canonical_recorded_chain_owner")
        self.source_owner, self.recorded_chain_owner = source_owner, recorded_chain_owner

    def resolve(self, principal: Principal, selection: GeneralDecisionEvidenceSelection
                ) -> ResolvedGeneralDecisionEvidence:
        if not isinstance(selection, GeneralDecisionEvidenceSelection):
            raise ValueError("Typed general source selection required; body claims are not authority")
        if selection.scope.format != "json":
            raise _missing("unimplemented_artifact_format")
        if selection.scope.destination != "authenticated-response":
            raise _missing("unimplemented_artifact_destination")
        classification = RESOURCE_CLASSIFICATION
        if classification != SUPPORTED_CLASSIFICATION:
            # Never fall through shared retention's unknown string fallback.
            raise _missing("unsupported_reference_classification")
        if (not isinstance(principal, Principal)
                or not principal.scope.permits_classification(classification)):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED")
        source = self.source_owner.resolve_source(principal, selection.selector)
        moments = [row.occurred_at for row in source.events]
        if (not moments or min(moments) < selection.scope.period_start
                or max(moments) > selection.scope.period_end):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_PERIOD_DENIED")
        # Resolve the shipped shared catalog, not caller days/retention labels.
        retention = resolve_retention_policy(classification.name.lower(), sensitive=False)
        chain = (self.recorded_chain_owner.read(principal, selection.selector)
            if self.recorded_chain_owner is not None and "decision_log" in selection.selector.sections else None)
        result = ResolvedGeneralDecisionEvidence(source, selection, _masked_sections(source),
            classification, retention, general_policy_fingerprint(classification, include_recorded_chain=chain is not None), chain)
        result.document()
        if self.source_owner.resolve_source(principal, selection.selector) != source:
            raise _missing("canonical_source_changed")
        return result
