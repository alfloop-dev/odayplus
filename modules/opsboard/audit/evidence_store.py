"""Durable audit-evidence store for OpsBoard exports (ODP-PV-011).

This binds the generic retention contract in :mod:`shared.audit.persistence` to
OpsBoard's :class:`~modules.opsboard.audit.domain.evidence.AuditEvidenceBundle`:

* :func:`retained_evidence_from_bundle` projects a freshly built bundle into a
  hash-stamped, retention-scoped :class:`RetainedEvidence` record.
* :class:`DurableEvidenceBundleStore` persists those records columnar (on their
  queryable dimensions) plus a JSON blob of the full bundle, over the
  ODP-PV-009 :class:`SqliteEngine`, so an export survives a process restart.

The durable store mirrors :class:`shared.audit.persistence.InMemoryEvidenceBundleStore`
method-for-method, so the two are interchangeable behind the
:class:`~shared.audit.persistence.EvidenceBundleStore` protocol.
"""

from __future__ import annotations

import json
from datetime import datetime

from modules.opsboard.audit.domain.evidence import (
    AuditEvidenceBundle,
    EvidenceExportRequest,
)
from shared.audit.persistence import (
    EvidenceRetentionPolicy,
    RetainedEvidence,
    resolve_retention_policy,
)
from shared.infrastructure.persistence.engine import SqliteEngine


def retained_evidence_from_bundle(
    bundle: AuditEvidenceBundle,
    request: EvidenceExportRequest,
    *,
    retention_policy: EvidenceRetentionPolicy | None = None,
    correlation_id: str | None = None,
    legal_hold: bool = False,
) -> RetainedEvidence:
    """Project an exported bundle into a durable, retention-scoped record.

    The privacy scope (classification, sensitive flag, export scope) is taken
    from the originating request; retention defaults to the policy resolved from
    that privacy scope unless an explicit policy is supplied.
    """

    policy = retention_policy or resolve_retention_policy(
        request.data_classification, sensitive=request.sensitive
    )
    return RetainedEvidence(
        export_id=bundle.export_id,
        program_id=bundle.program_id,
        purpose=bundle.purpose,
        requested_by=bundle.requested_by,
        audit_event_id=bundle.audit_event_id,
        bundle_checksum=bundle.bundle_checksum,
        data_classification=request.data_classification,
        sensitive=request.sensitive,
        export_scope=request.export_scope,
        retention_class=policy.retention_class,
        retain_until=policy.retain_until(bundle.generated_at),
        generated_at=bundle.generated_at,
        period_start=bundle.period_start,
        period_end=bundle.period_end,
        correlation_id=correlation_id or request.correlation_ids[0],
        bundle=bundle.to_dict(),
        legal_hold=legal_hold,
    )


class DurableEvidenceBundleStore:
    """Durable mirror of ``InMemoryEvidenceBundleStore`` over SQLite."""

    def __init__(self, engine: SqliteEngine) -> None:
        self._engine = engine

    def save(self, record: RetainedEvidence) -> RetainedEvidence:
        self._engine.execute(
            "INSERT INTO durable_evidence_bundles("
            "  export_id, program_id, purpose, requested_by, audit_event_id, "
            "  bundle_checksum, data_classification, sensitive, export_scope, "
            "  retention_class, retain_until, legal_hold, generated_at, "
            "  period_start, period_end, correlation_id, bundle_json, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(export_id) DO UPDATE SET "
            "  bundle_checksum = excluded.bundle_checksum, "
            "  retention_class = excluded.retention_class, "
            "  retain_until = excluded.retain_until, "
            "  legal_hold = excluded.legal_hold, "
            "  bundle_json = excluded.bundle_json",
            (
                record.export_id,
                record.program_id,
                record.purpose,
                record.requested_by,
                record.audit_event_id,
                record.bundle_checksum,
                record.data_classification,
                1 if record.sensitive else 0,
                record.export_scope,
                record.retention_class,
                record.retain_until.isoformat(),
                1 if record.legal_hold else 0,
                record.generated_at.isoformat(),
                record.period_start.isoformat(),
                record.period_end.isoformat(),
                record.correlation_id,
                json.dumps(record.bundle),
                record.generated_at.isoformat(),
            ),
        )
        return record

    def get(self, export_id: str) -> RetainedEvidence | None:
        row = self._engine.query_one(
            "SELECT * FROM durable_evidence_bundles WHERE export_id = ?",
            (export_id,),
        )
        return None if row is None else self._row_to_record(row)

    def list_for_program(self, program_id: str) -> list[RetainedEvidence]:
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles WHERE program_id = ? ORDER BY seq",
            (program_id,),
        )
        return [self._row_to_record(row) for row in rows]

    def list_all(self) -> list[RetainedEvidence]:
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles ORDER BY seq"
        )
        return [self._row_to_record(row) for row in rows]

    def list_expired(self, as_of: datetime) -> list[RetainedEvidence]:
        # Past-retention and not on legal hold. Filtering in Python keeps the
        # is_expired rule single-sourced on the record.
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles "
            "WHERE retain_until <= ? AND legal_hold = 0 ORDER BY seq",
            (as_of.isoformat(),),
        )
        return [self._row_to_record(row) for row in rows]

    def purge_expired(self, as_of: datetime) -> list[str]:
        expired = [record.export_id for record in self.list_expired(as_of)]
        for export_id in expired:
            self._engine.execute(
                "DELETE FROM durable_evidence_bundles WHERE export_id = ?",
                (export_id,),
            )
        return expired

    @staticmethod
    def _row_to_record(row) -> RetainedEvidence:
        return RetainedEvidence(
            export_id=row["export_id"],
            program_id=row["program_id"],
            purpose=row["purpose"],
            requested_by=row["requested_by"],
            audit_event_id=row["audit_event_id"],
            bundle_checksum=row["bundle_checksum"],
            data_classification=row["data_classification"],
            sensitive=bool(row["sensitive"]),
            export_scope=row["export_scope"],
            retention_class=row["retention_class"],
            retain_until=datetime.fromisoformat(row["retain_until"]),
            generated_at=datetime.fromisoformat(row["generated_at"]),
            period_start=datetime.fromisoformat(row["period_start"]),
            period_end=datetime.fromisoformat(row["period_end"]),
            correlation_id=row["correlation_id"],
            bundle=json.loads(row["bundle_json"]),
            legal_hold=bool(row["legal_hold"]),
        )


__all__ = ["DurableEvidenceBundleStore", "retained_evidence_from_bundle"]
