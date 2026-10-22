"""Match committed native receipt revisions with saved listing criteria."""

from shared.infrastructure.persistence.listing_search_delivery import ListingSearchDelivery
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.postgresql import PostgresDocumentStore
from shared.jobs.queue import NonRetryableJobError


def handle_listing_search_match(job, persistence):
    tenant = job.payload.get("tenant_id")
    if not isinstance(tenant, str) or not tenant.strip():
        raise NonRetryableJobError("LISTING_SEARCH_TENANT_REQUIRED")
    ListingSearchDelivery(
        TenantScopedDocumentStore(PostgresDocumentStore(persistence.engine), tenant),
        persistence.job_queue,
    ).deliver(job)
