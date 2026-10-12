"""Tenant-scoped, persisted WORM backlog health; no fabricated healthy defaults."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from shared.audit.canonical_delivery import JOB_TYPE
from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog

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
