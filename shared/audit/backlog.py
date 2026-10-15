"""Tenant-scoped, persisted WORM backlog health; no fabricated healthy defaults."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shared.audit.canonical_delivery import JOB_TYPE
from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
from shared.audit.durable_delivery import COLLECTION
from shared.audit.durable_delivery import JOB_TYPE as DURABLE_JOB_TYPE
from shared.audit.integrity import sha256_hex, verify_audit_event_integrity
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore

MAX_PENDING_SECONDS = 300


class AuditBacklogUnavailable(RuntimeError):
    pass


class AuditBacklogBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditBacklog:
    pending: int
    oldest_seconds: float

    @property
    def blocked(self):
        return self.oldest_seconds > MAX_PENDING_SECONDS


class CanonicalAuditBacklog:
    def __init__(self, engine):
        self.engine = engine
        self.writer = CanonicalPostgresAuditLog(engine)

    def inspect(self, tenant_id, *, now=None):
        try:
            with self.writer._tenant(tenant_id):
                rows = self.engine.query(
                    "SELECT a.audit_event_id,a.event_sha256,j.created_at,j.payload_json "
                    "FROM audit.audit_events a LEFT JOIN durable_jobs j ON j.job_type = ? "
                    "AND j.idempotency_key = 'canonical-audit:' || a.tenant_id::text || ':' || a.audit_event_id::text "
                    "WHERE a.tenant_id = ? AND a.worm_object_uri IS NULL",
                    (JOB_TYPE, tenant_id),
                )
            observed = now or datetime.now(UTC)
            if observed.tzinfo is None:
                raise ValueError("Audit backlog clock requires a timezone")
            ages = []
            for row in rows:
                payload = json.loads(row["payload_json"])
                if (
                    payload.get("tenant_id") != tenant_id
                    or payload.get("audit_event_id") != row["audit_event_id"]
                    or payload.get("event_sha256") != row["event_sha256"]
                    or not payload.get("sink_id")
                ):
                    raise ValueError("Pending canonical audit lacks a bound delivery intent")
                created = datetime.fromisoformat(str(row["created_at"]))
                if created.tzinfo is None:
                    raise ValueError("Audit backlog timestamp requires a timezone")
                age = (observed - created).total_seconds()
                if age < 0:
                    raise ValueError("Audit delivery clock is ahead of the health observation")
                ages.append(age)
            return AuditBacklog(len(rows), max(ages, default=0.0))
        except Exception as exc:
            raise AuditBacklogUnavailable("Canonical audit backlog cannot be verified") from exc

    def require_available(self, tenant_id):
        health = self.inspect(tenant_id)
        if health.blocked:
            raise AuditBacklogBlocked("Canonical WORM backlog exceeds five minutes")

    def all_tenants(self):
        rows = self.engine.query(
            "SELECT tenant_id::text AS tenant_id FROM core.tenants UNION "
            "SELECT DISTINCT payload_json::jsonb->>'tenant_id' AS tenant_id "
            "FROM durable_jobs WHERE job_type = ?",
            (JOB_TYPE,),
        )
        health = [self.inspect(row["tenant_id"]) for row in rows]
        return AuditBacklog(sum(row.pending for row in health), max((row.oldest_seconds for row in health), default=0.0))


class DurableAuditBacklog:
    """Cross-check committed facts, intents and jobs; success alone is not delivery."""

    def __init__(self, engine: Any, sink_id: str | None) -> None:
        self.engine, self.sink_id = engine, sink_id
        self.documents = SqliteDocumentStore(engine)
        self.queue = DurableJobQueue(engine)

    def _statement(self, name: str) -> str:
        # Select complete fixed statements; tenant and family remain parameters.
        if getattr(self.engine, "dialect", "") == "postgresql":
            catalog = {
                "tenant_marked": "SELECT event_id FROM durable_audit_events WHERE metadata_json ->> 'tenant_id' = ? AND metadata_json ->> 'audit_delivery_job_type' = ?",
                "tenant_jobs": "SELECT job_id FROM durable_jobs WHERE job_type = ? AND payload_json ->> 'tenant_id' = ?",
                "all_marked": "SELECT metadata_json FROM durable_audit_events WHERE metadata_json ->> 'audit_delivery_job_type' = ?",
            }
        else:
            catalog = {
                "tenant_marked": "SELECT event_id FROM durable_audit_events WHERE json_extract(metadata_json, '$.tenant_id') = ? AND json_extract(metadata_json, '$.audit_delivery_job_type') = ?",
                "tenant_jobs": "SELECT job_id FROM durable_jobs WHERE job_type = ? AND json_extract(payload_json, '$.tenant_id') = ?",
                "all_marked": "SELECT metadata_json FROM durable_audit_events WHERE json_extract(metadata_json, '$.audit_delivery_job_type') = ?",
            }
        return catalog[name]

    def inspect(self, tenant_id: str, *, now: datetime | None = None) -> AuditBacklog:
        try:
            if not tenant_id or not tenant_id.strip():
                raise ValueError("Audit backlog requires its tenant")
            if now is not None and now.tzinfo is None:
                raise ValueError("Audit backlog clock requires a timezone")
            with self.engine.transaction():
                return self._inspect(tenant_id, now)
        except Exception as exc:
            raise AuditBacklogUnavailable("Durable audit backlog cannot be verified") from exc

    def _inspect(self, tenant_id: str, observed: datetime | None) -> AuditBacklog:
        scoped = TenantScopedDocumentStore(self.documents, tenant_id)
        marked = self.engine.query(
            self._statement("tenant_marked"),
            (tenant_id, DURABLE_JOB_TYPE),
        )
        jobs = self.engine.query(
            self._statement("tenant_jobs"),
            (DURABLE_JOB_TYPE, tenant_id),
        )
        event_ids = {str(row["event_id"]) for row in marked}
        intents = scoped.list_all(COLLECTION)
        intent_ids = set()
        for intent in intents:
            event = intent.get("event")
            if event is None or not event.event_id or event.event_id in intent_ids:
                raise ValueError("Invalid or duplicated audit delivery intent")
            intent_ids.add(event.event_id)
        event_ids.update(intent_ids)
        job_ids = set()
        for row in jobs:
            job = self.queue.get(row["job_id"])
            event_id = job.payload.get("audit_event_id") if job else None
            if not isinstance(event_id, str) or not event_id or event_id in job_ids:
                raise ValueError("Invalid or duplicated audit delivery job")
            job_ids.add(event_id)
        event_ids.update(job_ids)
        ages = []
        for event_id in sorted(event_ids):
            row = self.engine.query_one(
                "SELECT * FROM durable_audit_events WHERE event_id = ?", (event_id,),
            )
            intent = scoped.get(COLLECTION, event_id)
            job = self.queue.get_by_idempotency_key("durable-audit:" + tenant_id + ":" + event_id)
            if row is None or intent is None or job is None or not self.sink_id:
                raise ValueError("Committed audit lacks its bound delivery intent/job/sink")
            event = DurableAuditLog._row_to_event(row)
            payload = job.payload
            if (
                not verify_audit_event_integrity(event)
                or event.metadata.get("tenant_id") != tenant_id
                or event.metadata.get("audit_delivery_job_type") not in (None, DURABLE_JOB_TYPE)
                or event.worm_sink_id != self.sink_id
                or intent.get("sink_id") != self.sink_id
                or intent.get("event").to_dict() != event.to_dict()
                or job.job_type != DURABLE_JOB_TYPE or job.correlation_id != event.correlation_id
                or payload.get("tenant_id") != tenant_id
                or payload.get("audit_event_id") != event_id
                or payload.get("event_hash") != event.event_hash
                or payload.get("sink_id") != self.sink_id
            ):
                raise ValueError("Audit delivery facts do not match the committed event")
            status = intent.get("status")
            if status == "delivered":
                receipt = intent.get("receipt", {})
                uri = str(receipt.get("object_uri") or "")
                if (
                    receipt.get("record_id") != event_id or receipt.get("sink_id") != self.sink_id
                    or receipt.get("record_type") != "audit-events"
                    or receipt.get("checksum") != sha256_hex(event.to_dict())
                    or not uri or uri.startswith("outbox:")
                ):
                    raise ValueError("Delivered audit lacks its verified external receipt")
                continue
            if status != "pending":
                raise ValueError("Unknown audit delivery status")
            created = job.created_at
            if created.tzinfo is None:
                raise ValueError("Audit delivery clock is invalid")
            ages.append(created)
        # A delivery may commit while this read-only observation is gathering
        # facts. Sample the live clock after collecting them; an explicit
        # acceptance clock retains its exact boundary/future-time semantics.
        clock = observed or datetime.now(UTC)
        if any(created > clock for created in ages):
            raise ValueError("Audit delivery clock is invalid")
        seconds = [(clock - created).total_seconds() for created in ages]
        return AuditBacklog(len(seconds), max(seconds, default=0.0))

    def require_available(self, tenant_id: str) -> None:
        if self.inspect(tenant_id).blocked:
            raise AuditBacklogBlocked("Durable WORM backlog exceeds five minutes")

    def all_tenants(self, *, now: datetime | None = None) -> AuditBacklog:
        try:
            marked = self.engine.query(
                self._statement("all_marked"),
                (DURABLE_JOB_TYPE,),
            )
            jobs = self.engine.query("SELECT payload_json FROM durable_jobs WHERE job_type = ?", (DURABLE_JOB_TYPE,))
            from shared.audit.platform_flag_delivery import SCOPE, inspect_platform_flag_backlog

            # Validate every global event/intent/job/sink/receipt before it is
            # separated from tenant facts. Pending globals remain in totals.
            platform = inspect_platform_flag_backlog(self.engine, self.sink_id, now=now)
            tenants = set()
            for row in marked + jobs:
                metadata = "metadata_json" in row.keys()
                payload = json.loads(row["metadata_json"] if metadata else row["payload_json"])
                if payload.get("scope") == SCOPE:
                    digests = platform.metadata_digests if metadata else platform.payload_digests
                    if sha256_hex(payload) not in digests:
                        raise ValueError("Global audit row was not verified by its platform owner")
                    continue
                tenant_id = payload.get("tenant_id")
                if not isinstance(tenant_id, str) or not tenant_id.strip():
                    raise ValueError("Audit delivery lacks its tenant")
                tenants.add(tenant_id)
            documents = self.engine.query(
                "SELECT collection,doc_id FROM durable_documents WHERE collection LIKE ?",
                (COLLECTION + ".tenant.%",),
            )
            for row in documents:
                intent = self.documents.get(row["collection"], row["doc_id"])
                event = intent.get("event") if intent else None
                tenant_id = event.metadata.get("tenant_id") if event else None
                if not isinstance(tenant_id, str) or not tenant_id.strip():
                    raise ValueError("Audit delivery document lacks its tenant")
                scoped = TenantScopedDocumentStore(self.documents, tenant_id)
                if row["collection"] != scoped._collection(COLLECTION) or scoped.get(COLLECTION, event.event_id) != intent:
                    raise ValueError("Audit delivery document differs from its tenant partition")
                tenants.add(tenant_id)
            if now is not None and now.tzinfo is None:
                raise ValueError("Audit backlog clock requires a timezone")
            health = [self.inspect(tenant_id, now=now) for tenant_id in sorted(tenants)]
            return AuditBacklog(platform.pending + sum(item.pending for item in health),
                                max(platform.oldest_seconds, max((item.oldest_seconds for item in health), default=0.0)))
        except Exception as exc:
            raise AuditBacklogUnavailable("Durable audit backlog tenants cannot be verified") from exc
