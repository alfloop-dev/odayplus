"""Immutable diagnostic evidence on the actual Forecast SQL owner."""
from __future__ import annotations

from dataclasses import dataclass

from modules.forecastops.domain.root_cause import (
    DiagnosticEvidenceConflict,
    DiagnosticEvidenceEnvelope,
    DiagnosticEvidenceUnavailable,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


@dataclass(frozen=True)
class _RetainedEvidence:
    evidence: DiagnosticEvidenceEnvelope
    source_sha256: str


@dataclass(frozen=True)
class DiagnosticEvidenceSaveReceipt:
    evidence: DiagnosticEvidenceEnvelope
    created: bool


class DurableDiagnosticEvidenceRepository:
    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    @property
    def engine(self):
        return self._store.engine

    def _collection(self, tenant_id: str) -> str:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise DiagnosticEvidenceUnavailable("Diagnostic tenant scope is required")
        if getattr(self._store, "tenant_id", tenant_id) != tenant_id:
            raise DiagnosticEvidenceUnavailable("Diagnostic repository tenant differs")
        return "forecastops.diagnostic_evidence:" + tenant_id

    def get(self, tenant_id: str, evidence_id: str) -> DiagnosticEvidenceEnvelope | None:
        retained = self._store.get(self._collection(tenant_id), evidence_id)
        if retained is None:
            return None
        if type(retained) is not _RetainedEvidence:
            raise DiagnosticEvidenceUnavailable("Retained diagnostic evidence is invalid")
        value = retained.evidence
        if type(value) is not DiagnosticEvidenceEnvelope:
            raise DiagnosticEvidenceUnavailable("Retained diagnostic evidence is invalid")
        try:
            value.__post_init__()
        except (ValueError, TypeError, OverflowError) as exc:
            raise DiagnosticEvidenceUnavailable("Retained diagnostic evidence is invalid") from exc
        if (value.tenant_id != tenant_id or value.evidence_id != evidence_id
                or value.source_sha256 != retained.source_sha256):
            raise DiagnosticEvidenceUnavailable("Retained diagnostic evidence owner differs")
        return value

    def save(self, value: DiagnosticEvidenceEnvelope) -> DiagnosticEvidenceEnvelope:
        """Preserve the original public return value for existing callers."""
        return self.save_with_created(value).evidence

    def save_with_created(self, value: DiagnosticEvidenceEnvelope) -> DiagnosticEvidenceSaveReceipt:
        """Internal persistence port, never an HTTP metrics admission boundary.

        The reader's capture method derives this object from retained owners in
        the same transaction; SQL put-if-absent preserves it across processes.
        """
        if type(value) is not DiagnosticEvidenceEnvelope:
            raise DiagnosticEvidenceUnavailable("Diagnostic evidence must be typed")
        value.__post_init__()
        collection = self._collection(value.tenant_id)
        with self.engine.transaction():
            created = self._store.put_if_absent(collection, value.evidence_id,
                                      _RetainedEvidence(value, value.source_sha256), seq=1)
            retained = self.get(value.tenant_id, value.evidence_id)
            if retained is None or retained.source_sha256 != value.source_sha256:
                raise DiagnosticEvidenceConflict("Diagnostic evidence identity already differs")
        return DiagnosticEvidenceSaveReceipt(evidence=retained, created=created)
