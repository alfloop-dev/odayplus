"""External data connector for store opening date authority (ODP-STORE-OPENING-001)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from apps.data_platform.store_opening import (
    ApprovedStoreOpeningAuthority,
    MissingStoreOpeningAuthorityError,
    StoreOpeningBackfillEngine,
    StoreOpeningBackfillResult,
    StoreOpeningError,
    TenantIsolationError,
    UnauthoritativeStoreOpeningError,
    validate_store_opening_record,
)


class StoreOpeningAuthorityConnector:
    """Connector that ingests, validates, and backfills authoritative store opening dates."""

    connector_id = "store_opening_authority"
    target = "store"

    def __init__(self, db_conn: Any = None) -> None:
        self.db_conn = db_conn
        self.engine = StoreOpeningBackfillEngine(db_conn=db_conn)

    def validate_record(self, record: dict[str, Any]) -> ApprovedStoreOpeningAuthority:
        """Validate an individual store opening record.

        Fails closed if opened_on is missing, unapproved, or inferred from created_at.
        """
        return validate_store_opening_record(record)

    def backfill(
        self,
        snapshot_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        records: Sequence[dict[str, Any]],
        eligible_store_ids: Sequence[uuid.UUID | str] | None = None,
        dry_run: bool = False,
    ) -> StoreOpeningBackfillResult:
        """Run idempotent backfill of store opening dates into core.stores.opened_on."""
        return self.engine.run_backfill(
            snapshot_id=snapshot_id,
            tenant_id=tenant_id,
            records=records,
            eligible_store_ids=eligible_store_ids,
            dry_run=dry_run,
        )


__all__ = [
    "StoreOpeningAuthorityConnector",
    "ApprovedStoreOpeningAuthority",
    "StoreOpeningBackfillEngine",
    "StoreOpeningBackfillResult",
    "StoreOpeningError",
    "UnauthoritativeStoreOpeningError",
    "MissingStoreOpeningAuthorityError",
    "TenantIsolationError",
    "validate_store_opening_record",
]
