"""Read-only retained WORM backlog; no skipped tenant, orphan or pending ACK."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from modules.opsboard.audit.evidence_store import DurableEvidenceBundleStore
from shared.audit.backlog import AuditBacklog, AuditBacklogUnavailable
from shared.audit.persistence import verify_retained_evidence_chain
from shared.audit.retained_delivery import (
    COLLECTION,
    JOB_TYPE,
    committed_retained_facts,
    verified_retained_receipt,
)
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore

# Exact server-implemented retained bundle versions. A caller cannot add a
# schema to this registry; an unknown explicit version is unavailable.
SUPPORTED_RETAINED_SCHEMAS = (
    "sitescore.canonical-sections.v1",
    "sitescore.general-decision-audit-retained.v1",
)


class RetainedEvidenceBacklog:
    """Observe committed records, audit marks, intents AND jobs as a union.

    Construction performs no DDL or external write. The supplied sink ID is
    the server's configured sink, not a job/body hint. A missing/corrupt owner
    cannot be hidden by deleting one side of the transaction or claiming that
    a worker-success status is an external acknowledgement.
    """

    def __init__(self, engine: Any, sink_id: str | None) -> None:
        self.engine, self.sink_id = engine, sink_id
        self.documents = SqliteDocumentStore(engine)
        self.queue = DurableJobQueue(engine)

    def _scan(self) -> dict[str, set[str]]:
        groups = defaultdict(set)

        def add(tenant_id, export_id):
            if (not isinstance(tenant_id, str) or not tenant_id.strip()
                    or not isinstance(export_id, str) or not export_id.strip()):
                raise ValueError("Retained delivery requires its actual tenant and export")
            groups[tenant_id].add(export_id)

        # The shared worker may predate this optional retained table. No
        # retained facts is a truthful empty lane; any orphan intent/job/audit
        # still enters the union and fails below. Do not create tables on read.
        columns = self.engine.query("PRAGMA table_info(durable_evidence_bundles)")
        rows = self.engine.query("SELECT * FROM durable_evidence_bundles ORDER BY seq") if columns else []
        records = [DurableEvidenceBundleStore._row_to_record(row) for row in rows]
        verify_retained_evidence_chain(records).raise_for_tamper()
        for record in records:
            schema = record.bundle.get("schema")
            if schema is not None:
                if schema not in SUPPORTED_RETAINED_SCHEMAS:
                    raise ValueError("Unsupported retained evidence bundle schema")
                add(record.bundle.get("identity_boundary", {}).get("tenant_id"), record.export_id)
        for row in self.engine.query("SELECT job_id FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,)):
            job = self.queue.get(row["job_id"])
            add(job.payload.get("tenant_id"), job.payload.get("export_id"))
        if getattr(self.engine, "dialect", "") == "postgresql":
            statement = "SELECT resource,metadata_json FROM durable_audit_events WHERE metadata_json ->> 'retained_delivery_job_type' = ?"
        else:
            statement = "SELECT resource,metadata_json FROM durable_audit_events WHERE json_extract(metadata_json, '$.retained_delivery_job_type') = ?"
        for row in self.engine.query(statement, (JOB_TYPE,)):
            metadata = json.loads(row["metadata_json"])
            resource = row["resource"]
            if not isinstance(resource, str) or not resource.startswith("audit-evidence/"):
                raise ValueError("Retained audit resource differs from its export")
            add(metadata.get("tenant_id"), resource.removeprefix("audit-evidence/"))
        for row in self.engine.query("SELECT collection,doc_id FROM durable_documents WHERE collection LIKE ?",
                                     (COLLECTION + ".tenant.%",)):
            intent = self.documents.get(row["collection"], row["doc_id"])
            if not isinstance(intent, dict):
                raise ValueError("Retained delivery intent is malformed")
            tenant_id, export_id = intent.get("tenant_id"), intent.get("export_id")
            add(tenant_id, export_id)
            scoped = TenantScopedDocumentStore(self.documents, tenant_id)
            if (row["collection"] != scoped._collection(COLLECTION) or row["doc_id"] != export_id
                    or scoped.get(COLLECTION, export_id) != intent):
                raise ValueError("Retained delivery intent differs from its exact tenant partition")
        return dict(groups)

    def _inspect(self, tenant_id: str, export_ids: set[str], now: datetime | None) -> AuditBacklog:
        if (not isinstance(tenant_id, str) or not tenant_id.strip()
                or (export_ids and (not isinstance(self.sink_id, str) or not self.sink_id.strip()))):
            raise ValueError("Retained backlog owner is unavailable")
        pending = []
        for export_id in sorted(export_ids):
            record, intent, job = committed_retained_facts(self.engine, tenant_id, self.sink_id, export_id)
            if intent.get("status") == "delivered":
                verified_retained_receipt(self.sink_id, intent.get("receipt"), record, intent)
            elif intent.get("status") == "pending" and intent.get("receipt") is None:
                pending.append(job.created_at)
            else:
                raise ValueError("Unknown retained delivery state")
        clock = now or datetime.now(UTC)
        if (clock.utcoffset() is None
                or any(at.utcoffset() is None or at > clock for at in pending)):
            raise ValueError("Retained backlog clock is invalid")
        ages = [(clock - at).total_seconds() for at in pending]
        return AuditBacklog(len(ages), max(ages, default=0.0))

    def inspect(self, tenant_id: str, *, now: datetime | None = None) -> AuditBacklog:
        try:
            with self.engine.transaction():
                return self._inspect(tenant_id, self._scan().get(tenant_id, set()), now)
        except Exception as exc:
            raise AuditBacklogUnavailable("Retained WORM backlog cannot be verified") from exc

    def tenants(self) -> set[str]:
        try:
            with self.engine.transaction():
                groups = self._scan()
                # Validate before contributing any partition to global metrics.
                for tenant_id, export_ids in groups.items():
                    self._inspect(tenant_id, export_ids, None)
                return set(groups)
        except Exception as exc:
            raise AuditBacklogUnavailable("Retained WORM backlog partitions cannot be verified") from exc
