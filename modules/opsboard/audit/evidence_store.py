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
from dataclasses import replace
from typing import Any

from modules.opsboard.audit.domain.evidence import (
    AuditEvidenceBundle,
    EvidenceExportRequest,
)
from shared.audit.persistence import (
    EvidenceGovernanceError,
    EvidenceImmutabilityError,
    EvidenceRetentionPolicy,
    GovernedEvidenceOperation,
    RetainedEvidence,
    attach_retained_evidence_integrity,
    require_legal_hold_authority,
    require_retention_purge_authority,
    resolve_retention_policy,
    verify_retained_evidence,
    verify_retained_evidence_chain,
)
from shared.audit.integrity import (
    CHAIN_GENESIS_HASH,
    AuditChainVerification,
)
from shared.infrastructure.persistence.engine import SqliteEngine

_EVIDENCE_INTEGRITY_COLUMNS: dict[str, str] = {
    "sequence": "INTEGER",
    "previous_hash": "TEXT",
    "record_hash": "TEXT",
    "signature_key_id": "TEXT",
    "signature_version": "TEXT",
    "signature_alg": "TEXT",
    "worm_sink_id": "TEXT",
    "governance_log_json": "TEXT NOT NULL DEFAULT '[]'",
}


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
        self._ensure_integrity_columns()

    def save(self, record: RetainedEvidence) -> RetainedEvidence:
        existing = self._engine.query_one(
            "SELECT export_id FROM durable_evidence_bundles WHERE export_id = ?",
            (record.export_id,),
        )
        if existing is not None:
            raise EvidenceImmutabilityError(
                f"retained evidence is append-only; overwrite denied for {record.export_id}"
            )
        last = self._engine.query_one(
            "SELECT sequence, record_hash FROM durable_evidence_bundles "
            "WHERE sequence IS NOT NULL ORDER BY sequence DESC LIMIT 1"
        )
        sequence = int(last["sequence"]) + 1 if last is not None else 1
        previous_hash = (
            str(last["record_hash"]) if last is not None else CHAIN_GENESIS_HASH
        )
        stamped = attach_retained_evidence_integrity(
            record,
            sequence=sequence,
            previous_hash=previous_hash,
        )
        self._engine.execute(
            "INSERT INTO durable_evidence_bundles("
            "  export_id, program_id, purpose, requested_by, audit_event_id, "
            "  bundle_checksum, data_classification, sensitive, export_scope, "
            "  retention_class, retain_until, legal_hold, generated_at, "
            "  period_start, period_end, correlation_id, bundle_json, created_at, "
            "  sequence, previous_hash, record_hash, signature_key_id, "
            "  signature_version, signature_alg, worm_sink_id, governance_log_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stamped.export_id,
                stamped.program_id,
                stamped.purpose,
                stamped.requested_by,
                stamped.audit_event_id,
                stamped.bundle_checksum,
                stamped.data_classification,
                1 if stamped.sensitive else 0,
                stamped.export_scope,
                stamped.retention_class,
                stamped.retain_until.isoformat(),
                1 if stamped.legal_hold else 0,
                stamped.generated_at.isoformat(),
                stamped.period_start.isoformat(),
                stamped.period_end.isoformat(),
                stamped.correlation_id,
                json.dumps(stamped.bundle),
                stamped.generated_at.isoformat(),
                stamped.sequence,
                stamped.previous_hash,
                stamped.record_hash,
                stamped.signature_key_id,
                stamped.signature_version,
                stamped.signature_alg,
                stamped.worm_sink_id,
                json.dumps(list(stamped.governance_log)),
            ),
        )
        return stamped

    def get(self, export_id: str) -> RetainedEvidence | None:
        row = self._engine.query_one(
            "SELECT * FROM durable_evidence_bundles WHERE export_id = ?",
            (export_id,),
        )
        if row is None:
            return None
        record = self._row_to_record(row)
        verify_retained_evidence(record)
        return record

    def list_for_program(self, program_id: str) -> list[RetainedEvidence]:
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles WHERE program_id = ? ORDER BY seq",
            (program_id,),
        )
        records = [self._row_to_record(row) for row in rows]
        for record in records:
            verify_retained_evidence(record)
        return records

    def list_all(self) -> list[RetainedEvidence]:
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles ORDER BY seq"
        )
        records = [self._row_to_record(row) for row in rows]
        verify_retained_evidence_chain(records).raise_for_tamper()
        return records

    def list_expired(self, as_of: datetime) -> list[RetainedEvidence]:
        # Past-retention and not on legal hold. Filtering in Python keeps the
        # is_expired rule single-sourced on the record.
        rows = self._engine.query(
            "SELECT * FROM durable_evidence_bundles "
            "WHERE retain_until <= ? AND legal_hold = 0 ORDER BY seq",
            (as_of.isoformat(),),
        )
        return [self._row_to_record(row) for row in rows]

    def purge_expired(
        self,
        as_of: datetime,
        *,
        context: GovernedEvidenceOperation | None = None,
    ) -> list[str]:
        require_retention_purge_authority(context)
        expired = [record.export_id for record in self.list_expired(as_of)]
        for export_id in expired:
            self._engine.execute(
                "DELETE FROM durable_evidence_bundles WHERE export_id = ?",
                (export_id,),
            )
        return expired

    def apply_legal_hold(
        self, export_id: str, *, context: GovernedEvidenceOperation
    ) -> RetainedEvidence:
        record = self.get(export_id)
        if record is None:
            raise KeyError(export_id)
        require_legal_hold_authority(context, record)
        governance_log = (
            *record.governance_log,
            {"operation": "legal_hold", **context.to_dict()},
        )
        self._engine.execute(
            "UPDATE durable_evidence_bundles "
            "SET legal_hold = 1, governance_log_json = ? WHERE export_id = ?",
            (json.dumps(list(governance_log)), export_id),
        )
        return replace(record, legal_hold=True, governance_log=governance_log)

    def delete(self, export_id: str) -> None:
        raise EvidenceImmutabilityError(
            f"retained evidence is append-only; delete denied for {export_id}"
        )

    def verify_integrity(self) -> AuditChainVerification:
        return verify_retained_evidence_chain(self.list_all())

    def _ensure_integrity_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._engine.query("PRAGMA table_info(durable_evidence_bundles)")
        }
        for column, column_type in _EVIDENCE_INTEGRITY_COLUMNS.items():
            if column not in existing:
                self._engine.execute(
                    f"ALTER TABLE durable_evidence_bundles ADD COLUMN {column} {column_type}"
                )

    @staticmethod
    def _row_to_record(row) -> RetainedEvidence:
        values = _row_values(row)
        governance_log_raw = values.get("governance_log_json") or "[]"
        try:
            governance_log = tuple(json.loads(governance_log_raw))
        except json.JSONDecodeError as exc:
            raise EvidenceGovernanceError("invalid governance log JSON") from exc
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
            sequence=values.get("sequence"),
            previous_hash=values.get("previous_hash"),
            record_hash=values.get("record_hash"),
            signature_key_id=values.get("signature_key_id")
            or RetainedEvidence.__dataclass_fields__["signature_key_id"].default,
            signature_version=values.get("signature_version")
            or RetainedEvidence.__dataclass_fields__["signature_version"].default,
            signature_alg=values.get("signature_alg")
            or RetainedEvidence.__dataclass_fields__["signature_alg"].default,
            worm_sink_id=values.get("worm_sink_id")
            or RetainedEvidence.__dataclass_fields__["worm_sink_id"].default,
            governance_log=governance_log,
        )


def _row_values(row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


__all__ = ["DurableEvidenceBundleStore", "retained_evidence_from_bundle"]
