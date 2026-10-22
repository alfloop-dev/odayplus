"""Receipt/search revision events committed with their native PostgreSQL owner.

Producers take their existing parent locks, then append an immutable snapshot
and reserve its job in the same transaction. Only post-commit consumers take
the match ledger fence. Whichever side commits last has an event that observes
the earlier side, including when an earlier event was already delivered.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from modules.listing.application.saved_search import SavedListingCriteria, receipt_matching_facts
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.listing_search_matches import (
    ListingSearchMatchConflict,
    ListingSearchMatchStore,
    evidence_digest,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobRequest, JobStatus, NonRetryableJobError

JOB_TYPE = "listing-search-receipt-match"
EVENTS = "listing.saved_search_revision_events"


class ListingSearchDelivery:
    def __init__(self, documents: TenantScopedDocumentStore, queue: DurableJobQueue):
        if (
            not isinstance(documents, TenantScopedDocumentStore)
            or getattr(queue, "_engine", None) is not documents.engine
            or getattr(documents.engine, "dialect", None) != "postgresql"
        ):
            raise ValueError("LISTING_SEARCH_SAME_POSTGRES_ENGINE_REQUIRED")
        self.documents, self.queue, self.engine = documents, queue, documents.engine

    def stage(self, *, kind: str, value: dict[str, Any]) -> None:
        """Append inside the owner's transaction; no match fence/parent reads."""
        if getattr(self.engine._local, "connection", None) is None:
            raise RuntimeError("LISTING_SEARCH_OWNER_TRANSACTION_REQUIRED")
        if kind == "search":
            criteria = SavedListingCriteria.from_query(value.get("query"))
            if criteria is None:
                return
            if (
                value.get("tenant_id") != self.documents.tenant_id
                or value.get("visibility") != "PRIVATE"
                or not value.get("owner_subject_id")
            ):
                raise ValueError("LISTING_SEARCH_PRIVATE_OWNER_REQUIRED")
            facts = {
                k: deepcopy(value.get(k))
                for k in (
                    "saved_view_id",
                    "version",
                    "tenant_id",
                    "owner_subject_id",
                    "visibility",
                    "name",
                    "created_at",
                    "resource",
                    "shared_role",
                    "deleted_at",
                )
            }
            facts["query"] = criteria.to_query()
            identity = facts["saved_view_id"]
        elif kind == "receipt":
            # The shared repository also carries legacy operator projections.
            # They are not canonical receipt evidence and cannot yield a hit.
            if "intake_id" not in value or not isinstance(value.get("scope"), dict):
                return
            facts = receipt_matching_facts(value)
            if facts["scope"].get("tenant_id") != self.documents.tenant_id:
                raise ValueError("LISTING_SEARCH_RECEIPT_TENANT_REQUIRED")
            identity = facts["intake_id"]
        else:
            raise ValueError("LISTING_SEARCH_EVENT_KIND_INVALID")
        version = facts.get("version")
        if not isinstance(identity, str) or not identity or type(version) is not int or version < 1:
            raise ValueError("LISTING_SEARCH_REVISION_REQUIRED")
        event_id = sha256(f"{kind}:{len(identity)}:{identity}:{version}".encode()).hexdigest()
        digest = evidence_digest(facts)
        row = self.documents.get(EVENTS, event_id)
        if row is None:
            row = {
                "id": event_id,
                "kind": kind,
                "tenant_id": self.documents.tenant_id,
                "facts": facts,
                "facts_sha256": digest,
                "job_id": str(uuid4()),
            }
            if not self.documents.put_if_absent(EVENTS, event_id, row, group_key=kind):
                row = self.documents.get(EVENTS, event_id)
        if (
            not isinstance(row, dict)
            or row.get("facts_sha256") != digest
            or evidence_digest(row.get("facts")) != digest
            or row.get("kind") != kind
            or row.get("tenant_id") != self.documents.tenant_id
        ):
            raise ListingSearchMatchConflict("LISTING_SEARCH_REVISION_FACTS_CHANGED")
        payload = {
            "tenant_id": self.documents.tenant_id,
            "event_id": event_id,
            "facts_sha256": digest,
        }
        job, _ = self.queue.enqueue(
            JobRequest(
                job_type=JOB_TYPE,
                payload=payload,
                job_id=row["job_id"],
                idempotency_key="listing-search-internal:" + row["job_id"],
            ),
            correlation_id="listing-search:" + event_id,
        )
        if job.job_id != row["job_id"] or job.job_type != JOB_TYPE or job.payload != payload:
            raise ListingSearchMatchConflict("LISTING_SEARCH_JOB_RESERVATION_CHANGED")

    def deliver(self, job: Any) -> None:
        """Consume only a server-reserved immutable event, never payload facts."""
        event_id = str(job.payload.get("event_id") or "")
        persisted = self.queue.get(job.job_id)
        row = self.documents.get(EVENTS, event_id)
        if (
            row is None
            or row.get("job_id") != job.job_id
            or persisted is None
            or persisted.job_type != JOB_TYPE
            or job.job_type != JOB_TYPE
            or persisted.payload != job.payload
            or persisted.idempotency_key != "listing-search-internal:" + job.job_id
            or row.get("tenant_id") != self.documents.tenant_id
            or job.payload.get("tenant_id") != self.documents.tenant_id
            or job.payload.get("facts_sha256") != row.get("facts_sha256")
            or evidence_digest(row.get("facts")) != row.get("facts_sha256")
        ):
            raise NonRetryableJobError("LISTING_SEARCH_UNBOUND_EVENT")
        with self.engine.transaction():
            # Use the shared worker claim protocol, and keep its row stable
            # until this short SQL-only ledger write commits. No producer ever
            # takes the ledger fence, so this introduces no parent lock cycle.
            self.engine.query_one(
                "SELECT job_id FROM durable_jobs WHERE job_id = ? FOR UPDATE", (job.job_id,)
            )
            self.engine.query_one(
                "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                ("listing-search-ledger:" + self.documents.tenant_id,),
            )
            latest = self.queue.get(job.job_id)
            if (
                latest is None
                or latest.status != JobStatus.RUNNING
                or latest.fence_token != job.fence_token
                or latest.version != job.version
                or latest.payload != job.payload
                or latest.lease_expires_at is None
                or latest.lease_expires_at <= datetime.now(UTC)
            ):
                raise NonRetryableJobError("LISTING_SEARCH_UNBOUND_EVENT")
            searches, receipts = [], []
            for kind, destination in (("search", searches), ("receipt", receipts)):
                for item in self.documents.list_by_group(EVENTS, kind):
                    if item.get("tenant_id") != self.documents.tenant_id or item.get(
                        "facts_sha256"
                    ) != evidence_digest(item.get("facts")):
                        raise NonRetryableJobError("LISTING_SEARCH_EVENT_INTEGRITY_INVALID")
                    destination.append(item["facts"])
            latest_searches = {}
            for search in searches:
                identity = search["saved_view_id"]
                if (
                    identity not in latest_searches
                    or search["version"] > latest_searches[identity]["version"]
                ):
                    latest_searches[identity] = search
            searches = [
                search for search in latest_searches.values() if not search.get("deleted_at")
            ]
            # Process this immutable version against committed opposite-side
            # versions. No source fetching, current-row rewriting or promotion.
            if row["kind"] == "search":
                facts = row["facts"]
                searches = (
                    []
                    if facts.get("deleted_at")
                    or latest_searches[facts["saved_view_id"]]["version"] != facts["version"]
                    else [facts]
                )
                changed_search_ids = {row["facts"]["saved_view_id"]}
                changed_receipt_ids = set()
            else:
                receipts = [row["facts"]]
                changed_search_ids = set()
                changed_receipt_ids = {row["facts"]["intake_id"]}
            from modules.listing.application.saved_search_admission import (
                listing_search_source_admitted,
            )

            receipts = [
                receipt
                for receipt in receipts
                if listing_search_source_admitted(self.engine, receipt, self.documents.tenant_id)
            ]
            ListingSearchMatchStore(self.documents).reconcile(
                searches=searches,
                receipts=receipts,
                changed_search_ids=changed_search_ids,
                changed_receipt_ids=changed_receipt_ids,
            )
