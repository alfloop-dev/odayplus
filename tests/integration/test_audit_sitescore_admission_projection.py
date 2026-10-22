"""Real canonical SQLite source controls; no invented approved program records.

Policy admission remains unbound. These tests intentionally supply no synthetic
program/classification/retention approval to obtain a positive export result.
The actual decision/signature/audit producers are the existing resolver fixture.
"""
import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta

import pytest

from modules.opsboard.audit.application.evidence_admission import (
    AUDIT_FIELDS,
    DECISION_FIELDS,
    BoundNativeRecord,
    CanonicalRecordReference,
    EvidenceAdmissionOwners,
    EvidenceAdmissionReferences,
    EvidencePolicyScope,
    FieldAuthorityBinding,
    ProgramPurposeBinding,
    RetentionBinding,
    SafeSiteScoreSections,
    SiteScoreStage1Admission,
    SourceMetadataBinding,
    project_sitescore_sections,
    projection_sha256,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.persistence import EvidenceRetentionPolicy
from shared.auth import DataClassification, Scope
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from tests.integration.test_audit_sitescore_evidence_resolver import (
    case as case,
)
from tests.integration.test_audit_sitescore_evidence_resolver import (
    principal,
    resolver,
    rows,
    selected,
)


def current_source(case):
    parts, approvals, outcome = case
    return resolver(parts, approvals).resolve_source(principal(), selected(outcome))


def test_every_format_uses_same_exact_source_field_allowlist_without_writes(case):
    parts, _approvals, _outcome = case
    before = rows(parts[0])
    source = current_source(case)
    projected = project_sitescore_sections(source)
    documents = [projected.for_format(format) for format in ("json", "csv", "pdf")]
    assert documents[0] == documents[1] == documents[2]
    document = documents[0]
    assert document["export_admitted"] is False and projected.export_admitted is False
    assert document["source_digest"] == source.source_digest
    decision = document["sections"]["decision_log"][0]
    assert set(decision) == set(DECISION_FIELDS)
    assert decision["approval_id"] == source.approval_id
    assert decision["decision_actor"] == source.decision_actor
    assert decision["recorded_model_reference"] == source.recorded_model_reference
    assert decision["source_snapshot_ids"] == list(source.source_snapshot_ids)
    assert decision["rationale_sha256"] == source.rationale_sha256
    assert [row["event_id"] for row in document["sections"]["audit"]] == [event.event_id for event in source.events]
    assert all(set(row) == set(AUDIT_FIELDS) for row in document["sections"]["audit"])
    assert all(not ({"metadata", "reason", "report", "address", "authorized_by"} & set(row))
               for section in document["sections"].values() for row in section)
    original_projection_hash = projection_sha256(projected)
    # Returned renderer data is a detached copy, not mutable source authority.
    document["sections"]["decision_log"][0]["approval_id"] = "not-canonical"
    assert projected.for_format("json")["sections"]["decision_log"][0]["approval_id"] == source.approval_id
    assert projection_sha256(projected) == original_projection_hash
    assert rows(parts[0]) == before


def test_field_projection_can_only_reduce_the_shared_schema(case):
    source = current_source(case)
    projected = project_sitescore_sections(source, decision_fields=("decision_id", "report_sha256"),
                                          audit_fields=("event_id", "event_hash"))
    for format in ("json", "csv", "pdf"):
        sections = projected.for_format(format)["sections"]
        assert set(sections["decision_log"][0]) == {"decision_id", "report_sha256"}
        assert all(set(row) == {"event_id", "event_hash"} for row in sections["audit"])
    assert projected.export_admitted is False


@pytest.mark.parametrize("fields", [("reason",), ("address",), ("metadata",), (["event_id"],)])
def test_unapproved_or_malformed_fields_cannot_reach_any_renderer(case, fields):
    source = current_source(case)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="supported_section_field_authority"):
        project_sitescore_sections(source, audit_fields=fields)


@pytest.mark.parametrize("sections", [("outcome",), ("sla",), ("decision_log", "audit", "outcome")])
def test_unsupported_sections_are_not_silently_marked_complete(case, sections):
    source = replace(current_source(case), sections=sections)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="supported_section_field_authority"):
        project_sitescore_sections(source)


def test_canonical_decision_reference_cannot_substitute_for_missing_business_owners(case):
    parts, approvals, outcome = case
    before = rows(parts[0])
    # This is a real source reference, deliberately NOT an export policy.
    ref = CanonicalRecordReference("sitescore.decision", outcome.decision.decision_id,
                                   str(outcome.decision.version))
    refs = EvidenceAdmissionReferences(program=ref, purpose=ref, classification=ref, retention=ref)
    stage = SiteScoreStage1Admission(resolver(parts, approvals))
    with pytest.raises(SiteScoreEvidenceUnavailable, match="actual_program_classification_retention_source_owners"):
        stage.resolve(principal(), selected(outcome), refs)
    assert rows(parts[0]) == before


def test_absent_record_selectors_and_caller_claim_mapping_never_grant_admission(case):
    parts, approvals, outcome = case
    stage = SiteScoreStage1Admission(resolver(parts, approvals))
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="actual_program_purpose_classification_retention_references"):
        stage.resolve(principal(), selected(outcome), EvidenceAdmissionReferences())
    with pytest.raises(ValueError, match="body claims are not authority"):
        stage.resolve(principal(), selected(outcome), {
            "approved": True, "authorized_by": principal().subject_id,
            "data_classification": "internal", "retention_days": 1,
        })
    assert rows(parts[0]) == before


def test_wrong_port_objects_fail_as_missing_owners_not_a_record_approval(case):
    parts, approvals, outcome = case
    ref = CanonicalRecordReference("sitescore.decision", outcome.decision.decision_id,
                                   str(outcome.decision.version))
    refs = EvidenceAdmissionReferences(program=ref, purpose=ref, classification=ref, retention=ref)
    owners = EvidenceAdmissionOwners(program_purpose=object(), classification_fields=object(),
                                      retention=object(), source_metadata=object())
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="actual_program_classification_retention_source_owners"):
        SiteScoreStage1Admission(resolver(parts, approvals), owners).resolve(principal(), selected(outcome), refs)
    assert rows(parts[0]) == before


@pytest.mark.parametrize("tenant_or_store", ["foreign-tenant", "ungranted-store"])
def test_current_source_abac_still_precedes_every_business_record_read(case, tenant_or_store):
    parts, approvals, outcome = case
    before = rows(parts[0])
    actor = principal(scope=(Scope(tenant_id="not-the-source-tenant") if tenant_or_store == "foreign-tenant"
                             else Scope(tenant_id=principal().tenant_id, store_ids=frozenset({"not-the-source-store"}))))
    with pytest.raises((SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable)):
        SiteScoreStage1Admission(resolver(parts, approvals)).resolve(
            actor, selected(outcome), EvidenceAdmissionReferences())
    assert rows(parts[0]) == before


def test_untyped_source_and_unsupported_format_are_not_renderer_inputs(case):
    with pytest.raises(SiteScoreEvidenceUnavailable, match="canonical_source"):
        project_sitescore_sections({"approved": True, "decision_id": "caller"})
    projection = project_sitescore_sections(current_source(case))
    with pytest.raises(ValueError, match="Unsupported evidence format"):
        projection.for_format("docx")


class EngineeringReadPorts:
    """Explicit owned-SQLite port contract fixture, NOT approved policy records.

    Production has none of these adapters wired. Test-only records are marked
    engineeringFixture and cannot become production program/retention authority.
    This exercises composition of a trusted server-port snapshot, not admission.
    """

    collection = "engineering-only.evidence-admission-port-snapshots"

    def __init__(self, engine, source, actor):
        self.engine = engine
        self.documents = SqliteDocumentStore(engine)
        self.retain_until = datetime.now(UTC) + timedelta(days=1)
        self.policy_scope = EvidencePolicyScope("json", "engineering-only-internal-fixture",
                                               source.events[0].occurred_at, source.events[-1].occurred_at)
        self.source = source
        self.actor = actor
        self.sensitive = False
        self.refs = {
            name: CanonicalRecordReference(self.collection, name, str(source.decision_version))
            for name in ("program", "purpose", "classification", "retention", "sensitive")
        }
        for name in (*self.refs, *source.source_snapshot_ids):
            self.documents.put(self.collection, name, {
                "engineeringFixture": True, "record_id": name,
                "tenant_id": source.tenant_id, "subject_id": actor.subject_id,
                "source_digest": source.source_digest,
                "purpose_reference": asdict(self.refs["purpose"]),
                "classification_reference": asdict(self.refs["classification"]),
                "policy_scope": self.scope_document(self.policy_scope),
                "retain_until": self.retain_until.isoformat(),
            })

    @staticmethod
    def scope_document(scope):
        return {**asdict(scope), "period_start": scope.period_start.isoformat(), "period_end": scope.period_end.isoformat()}

    def bound(self, context, reference):
        native = self.documents.get(self.collection, reference.record_id)
        if (native is None or native["tenant_id"] != context.principal.tenant_id
                or native["subject_id"] != context.principal.subject_id
                or native["source_digest"] != context.source.source_digest
                or native["purpose_reference"] != asdict(context.references.purpose)
                or native["classification_reference"] != asdict(context.references.classification)
                or (context.policy_scope is not None and native["policy_scope"] != self.scope_document(context.policy_scope))):
            return None
        checksum = hashlib.sha256(json.dumps(native, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return BoundNativeRecord(reference, checksum, native["tenant_id"], native["subject_id"],
                                 native["source_digest"], native)

    def read_authorized(self, context, reference, other=None):
        if reference == self.refs["program"]:
            return ProgramPurposeBinding(self.bound(context, reference), self.bound(context, other),
                                          ("decision_log", "audit"))
        if reference == self.refs["classification"]:
            return FieldAuthorityBinding(self.bound(context, reference), DataClassification.CONFIDENTIAL,
                                          ("decision_id", "approval_id"), ("event_id", "event_hash"), self.sensitive)
        if reference == self.refs["retention"]:
            # Engineering TTL only; deliberately not an existing business policy.
            record = self.bound(context, reference)
            if record is None:
                return None
            return RetentionBinding(record, EvidenceRetentionPolicy("engineering-fixture-only", 1),
                                     datetime.fromisoformat(record.native_record["retain_until"]))
        return None

    def read_snapshot(self, context, snapshot_id):
        reference = CanonicalRecordReference(self.collection, snapshot_id, str(self.source.decision_version))
        record = self.bound(context, reference)
        if record is None:
            return None
        return SourceMetadataBinding(record, snapshot_id, record.content_sha256)


def test_trusted_engineering_port_snapshots_compose_only_internal_projection_not_export(case):
    parts, approvals, outcome = case
    actor = principal()
    source = current_source(case)
    ports = EngineeringReadPorts(parts[0], source, actor)
    references = EvidenceAdmissionReferences(**{name: ports.refs[name] for name in ports.refs})
    owners = EvidenceAdmissionOwners(ports, ports, ports, ports)
    before = rows(parts[0])
    result = SiteScoreStage1Admission(resolver(parts, approvals), owners).resolve(actor, selected(outcome), references)
    assert result.export_admitted is False and result.source.export_admitted is False
    assert result.safe_sections.export_admitted is False
    assert result.source.source_digest == source.source_digest
    assert tuple(snapshot.snapshot_id for snapshot in result.snapshots) == source.source_snapshot_ids
    assert result.program_purpose.program.native_record["engineeringFixture"] is True
    for format in ("json", "csv", "pdf"):
        sections = result.safe_sections.for_format(format)["sections"]
        assert set(sections["decision_log"][0]) == {"decision_id", "approval_id"}
        assert all(set(row) == {"event_id", "event_hash"} for row in sections["audit"])
    assert rows(parts[0]) == before
    # Nothing in a successful engineering port contract binds production ports.
    with pytest.raises(SiteScoreEvidenceUnavailable, match="actual_program_classification_retention_source_owners"):
        SiteScoreStage1Admission(resolver(parts, approvals)).resolve(actor, selected(outcome), references)


@pytest.mark.parametrize("missing", ["program", "purpose", "classification", "retention", "source"])
def test_absent_engineering_owner_records_do_not_become_authorization(case, missing):
    parts, approvals, outcome = case
    source = current_source(case)
    ports = EngineeringReadPorts(parts[0], source, principal())
    references = EvidenceAdmissionReferences(**{name: ports.refs[name] for name in ports.refs})
    key = source.source_snapshot_ids[0] if missing == "source" else missing
    # Remove only the explicit engineering record; canonical sources stay true.
    ports.documents.put(ports.collection, key, None)
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="CANONICAL_EVIDENCE_INPUT_REQUIRED"):
        SiteScoreStage1Admission(resolver(parts, approvals), EvidenceAdmissionOwners(ports, ports, ports, ports)).resolve(
            principal(), selected(outcome), references)
    assert rows(parts[0]) == before


@pytest.mark.parametrize("format", ["json", "csv", "pdf"])
def test_directly_constructed_projection_cannot_smuggle_unapproved_fields(case, format):
    source = current_source(case)
    forged = SafeSiteScoreSections(source.source_digest, (
        ("decision_log", ((("decision_id", source.decision_id), ("raw_report", {"contact": "not-exportable"})),)),
    ))
    with pytest.raises(SiteScoreEvidenceUnavailable, match="canonical_projection_shape"):
        forged.for_format(format)


@pytest.mark.parametrize("missing", ["port", "reference", "record"])
def test_sensitive_engineering_snapshot_never_substitutes_for_independent_authority(case, missing):
    parts, approvals, outcome = case
    ports = EngineeringReadPorts(parts[0], current_source(case), principal())
    ports.sensitive = True
    refs = {name: ports.refs[name] for name in ports.refs}
    if missing == "reference":
        refs["sensitive"] = None
    references = EvidenceAdmissionReferences(**refs)
    owners = EvidenceAdmissionOwners(ports, ports, ports, ports, None if missing == "port" else ports)
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="CANONICAL_EVIDENCE_INPUT_REQUIRED"):
        SiteScoreStage1Admission(resolver(parts, approvals), owners).resolve(principal(), selected(outcome), references)
    assert rows(parts[0]) == before


def test_configured_port_unavailability_and_mismatched_source_reference_do_not_write(case, monkeypatch):
    parts, approvals, outcome = case
    ports = EngineeringReadPorts(parts[0], current_source(case), principal())
    references = EvidenceAdmissionReferences(**{name: ports.refs[name] for name in ports.refs})
    owners = EvidenceAdmissionOwners(ports, ports, ports, ports)
    before = rows(parts[0])
    original = ports.read_snapshot
    monkeypatch.setattr(ports, "read_snapshot", lambda context, snapshot_id:
                        replace(original(context, snapshot_id), snapshot_id=context.source.decision_id))
    with pytest.raises(SiteScoreEvidenceUnavailable, match="actual_source_metadata"):
        SiteScoreStage1Admission(resolver(parts, approvals), owners).resolve(principal(), selected(outcome), references)
    assert rows(parts[0]) == before
    def unavailable(_context, _snapshot_id):
        raise OSError("Explicit engineering owner unavailable")
    monkeypatch.setattr(ports, "read_snapshot", unavailable)
    with pytest.raises(SiteScoreEvidenceUnavailable, match="canonical_admission_owner_unavailable"):
        SiteScoreStage1Admission(resolver(parts, approvals), owners).resolve(principal(), selected(outcome), references)
    assert rows(parts[0]) == before


@pytest.mark.parametrize("mismatch", ["purpose", "classification"])
def test_same_source_and_subject_cannot_mix_different_selected_policy_records(case, mismatch):
    parts, approvals, outcome = case
    source = current_source(case)
    ports = EngineeringReadPorts(parts[0], source, principal())
    references = EvidenceAdmissionReferences(**ports.refs)
    wrong = replace(ports.refs[mismatch], record_id="other-" + mismatch)
    native = ports.documents.get(ports.collection, "retention")
    native[mismatch + "_reference"] = asdict(wrong)
    ports.documents.put(ports.collection, "retention", native)
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="CANONICAL_EVIDENCE_INPUT_REQUIRED"):
        SiteScoreStage1Admission(resolver(parts, approvals), EvidenceAdmissionOwners(ports, ports, ports, ports)).resolve(
            principal(), selected(outcome), references)
    assert rows(parts[0]) == before


def test_every_policy_owner_gets_exact_immutable_selected_references_and_scope(case):
    parts, approvals, outcome = case
    source = current_source(case)
    ports = EngineeringReadPorts(parts[0], source, principal())
    references = EvidenceAdmissionReferences(**ports.refs)
    policy_scope = EvidencePolicyScope("json", "engineering-only-internal-fixture", source.events[0].occurred_at,
                                       source.events[-1].occurred_at)
    contexts = []
    class RecordingPorts(EngineeringReadPorts):
        def read_authorized(self, context, *args):
            contexts.append(context)
            return super().read_authorized(context, *args)
        def read_snapshot(self, context, *args):
            contexts.append(context)
            return super().read_snapshot(context, *args)
    owner = RecordingPorts(parts[0], source, principal())
    result = SiteScoreStage1Admission(resolver(parts, approvals), EvidenceAdmissionOwners(owner, owner, owner, owner)).resolve(
        principal(), selected(outcome), references, policy_scope=policy_scope)
    assert contexts and all(context.references == references and context.policy_scope == policy_scope for context in contexts)
    assert result.references == references and result.policy_scope == policy_scope
    assert result.export_admitted is False


@pytest.mark.parametrize("mismatch", ["format", "destination", "period_start", "period_end"])
def test_engineering_scope_snapshot_cannot_be_reused_for_other_selected_scope(case, mismatch):
    parts, approvals, outcome = case
    source = current_source(case)
    ports = EngineeringReadPorts(parts[0], source, principal())
    changes = {"format": "csv", "destination": "other-engineering-destination",
               "period_start": ports.policy_scope.period_start - timedelta(seconds=1),
               "period_end": ports.policy_scope.period_end + timedelta(seconds=1)}
    scope = replace(ports.policy_scope, **{mismatch: changes[mismatch]})
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable):
        SiteScoreStage1Admission(resolver(parts, approvals), EvidenceAdmissionOwners(ports, ports, ports, ports)).resolve(
            principal(), selected(outcome), EvidenceAdmissionReferences(**ports.refs), policy_scope=scope)
    assert rows(parts[0]) == before
