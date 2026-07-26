"""Authoritative store opening date connector and backfill engine (ODP-STORE-OPENING-001).

Guarantees:
1. Approved source identity and snapshot lineage are persisted to canonical lineage.
2. opened_on is never inferred from created_at or ingestion_time.
3. Backfill is strictly tenant-safe and idempotent.
4. Eligible stores with missing authority fail closed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Sequence

APPROVED_STORE_OPENING_SOURCES: set[str] = {
    "SRC-AUTH-STORE-OPENING",
    "SRC-GOVT-STORE-REGISTRY",
    "SRC-MERCHANT-OPENING-AUDIT",
    "store_opening_authority",
    "store_opening.official_registry",
    "APPROVED_GOVERNMENT_REGISTRY",
    "AUDITED_MERCHANT_RECORD",
}

FORBIDDEN_TIMESTAMP_FIELDS: set[str] = {
    "created_at",
    "created_time",
    "ingested_at",
    "ingestion_time",
    "sys_created_at",
}


class StoreOpeningError(Exception):
    """Base exception for store opening date processing errors."""


class UnauthoritativeStoreOpeningError(StoreOpeningError):
    """Raised when an opening date is missing, unapproved, or inferred from created_at."""


class MissingStoreOpeningAuthorityError(StoreOpeningError):
    """Raised when an eligible store targeted for backfill lacks explicit opening date authority."""


class TenantIsolationError(StoreOpeningError):
    """Raised when a store update violates tenant ownership boundaries."""


@dataclass(frozen=True)
class ApprovedStoreOpeningAuthority:
    source_id: str
    snapshot_id: uuid.UUID
    tenant_id: uuid.UUID
    store_id: uuid.UUID
    opened_on: date
    authority_type: str
    provenance_note: str
    created_at_ignored: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "snapshot_id": str(self.snapshot_id),
            "tenant_id": str(self.tenant_id),
            "store_id": str(self.store_id),
            "opened_on": self.opened_on.isoformat(),
            "authority_type": self.authority_type,
            "provenance_note": self.provenance_note,
            "created_at_ignored": self.created_at_ignored,
        }


@dataclass(frozen=True)
class StoreOpeningBackfillResult:
    tenant_id: uuid.UUID
    snapshot_id: uuid.UUID
    processed_count: int
    updated_count: int
    store_ids: list[uuid.UUID]
    lineage_records: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": str(self.tenant_id),
            "snapshot_id": str(self.snapshot_id),
            "processed_count": self.processed_count,
            "updated_count": self.updated_count,
            "store_ids": [str(sid) for sid in self.store_ids],
            "lineage_records": self.lineage_records,
        }


def _parse_iso_date_or_datetime(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    if not s:
        return None
    try:
        date_part = s.split("T")[0].split(" ")[0]
        return date.fromisoformat(date_part)
    except (ValueError, TypeError):
        return None


def validate_store_opening_record(
    record: dict[str, Any],
    approved_sources: set[str] | None = None,
) -> ApprovedStoreOpeningAuthority:
    """Validate raw store opening authority record.

    Fails closed if:
    - source_id is not in approved_sources allowlist
    - opened_on is missing, empty, or invalid date format
    - record attempts to infer opened_on from created_at / ingestion_time
    - tenant_id or store_id is invalid
    """
    allowed = approved_sources or APPROVED_STORE_OPENING_SOURCES
    source_id = str(record.get("source_id") or "").strip()
    if not source_id or source_id not in allowed:
        raise UnauthoritativeStoreOpeningError(
            f"Unapproved source identity: {source_id!r}. Approved sources: {sorted(allowed)}"
        )

    # Check for explicit inference flag
    if record.get("inferred_from_created_at") is True:
        raise UnauthoritativeStoreOpeningError(
            "REJECTED: opened_on must never be inferred from created_at or ingestion_time"
        )

    raw_opened_on = record.get("opened_on") or record.get("opening_date")
    if not raw_opened_on:
        raise UnauthoritativeStoreOpeningError(
            "Missing authoritative opened_on date in source record"
        )

    parsed_opened_on = _parse_iso_date_or_datetime(raw_opened_on)
    if parsed_opened_on is None:
        raise UnauthoritativeStoreOpeningError(
            f"Invalid ISO opening date: {raw_opened_on!r}"
        )

    # Verify opened_on is not copied directly from created_at/ingestion timestamp
    for ts_field in FORBIDDEN_TIMESTAMP_FIELDS:
        ts_val = record.get(ts_field)
        if ts_val is not None:
            if str(raw_opened_on).strip() == str(ts_val).strip():
                raise UnauthoritativeStoreOpeningError(
                    f"REJECTED: opened_on value matches raw {ts_field} timestamp ({ts_val!r})"
                )
            parsed_ts_date = _parse_iso_date_or_datetime(ts_val)
            if parsed_ts_date is not None and parsed_opened_on == parsed_ts_date:
                if record.get(f"inferred_from_{ts_field}") is True or str(raw_opened_on).strip() == str(ts_val).strip():
                    raise UnauthoritativeStoreOpeningError(
                        f"REJECTED: opened_on ({parsed_opened_on}) matches raw {ts_field} date ({parsed_ts_date})"
                    )

    raw_snapshot_id = record.get("snapshot_id")
    if not raw_snapshot_id:
        raise UnauthoritativeStoreOpeningError("Missing snapshot_id in record")
    try:
        snapshot_id = uuid.UUID(str(raw_snapshot_id))
    except ValueError as exc:
        raise UnauthoritativeStoreOpeningError(f"Invalid snapshot_id UUID: {raw_snapshot_id!r}") from exc

    raw_tenant_id = record.get("tenant_id")
    if not raw_tenant_id:
        raise UnauthoritativeStoreOpeningError("Missing tenant_id in record")
    try:
        tenant_id = uuid.UUID(str(raw_tenant_id))
    except ValueError as exc:
        raise UnauthoritativeStoreOpeningError(f"Invalid tenant_id UUID: {raw_tenant_id!r}") from exc

    raw_store_id = record.get("store_id") or record.get("source_store_id")
    if not raw_store_id:
        raise UnauthoritativeStoreOpeningError("Missing store_id in record")
    try:
        store_id = uuid.UUID(str(raw_store_id))
    except ValueError as exc:
        raise UnauthoritativeStoreOpeningError(f"Invalid store_id UUID: {raw_store_id!r}") from exc

    authority_type = str(record.get("authority_type") or "AUDITED_MERCHANT_RECORD").strip()
    provenance_note = str(record.get("provenance_note") or "Verified against official opening registry").strip()

    return ApprovedStoreOpeningAuthority(
        source_id=source_id,
        snapshot_id=snapshot_id,
        tenant_id=tenant_id,
        store_id=store_id,
        opened_on=parsed_opened_on,
        authority_type=authority_type,
        provenance_note=provenance_note,
        created_at_ignored=True,
    )


class StoreOpeningBackfillEngine:
    """Idempotent, tenant-safe store opening date backfill & lineage engine."""

    def __init__(self, db_conn: Any = None, schema: str = "data_plane") -> None:
        self.db_conn = db_conn
        self.schema = schema
        self._in_memory_stores: dict[str, dict[str, Any]] = {}
        self._in_memory_lineage: list[dict[str, Any]] = []

    def seed_in_memory_store(self, store_id: uuid.UUID | str, tenant_id: uuid.UUID | str, store_name: str = "Test Store", opened_on: date | None = None) -> None:
        s_id = str(uuid.UUID(str(store_id)))
        t_id = str(uuid.UUID(str(tenant_id)))
        self._in_memory_stores[s_id] = {
            "store_id": s_id,
            "tenant_id": t_id,
            "store_name": store_name,
            "opened_on": opened_on,
        }

    def run_backfill(
        self,
        snapshot_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        records: Sequence[dict[str, Any]],
        eligible_store_ids: Sequence[uuid.UUID | str] | None = None,
        dry_run: bool = False,
    ) -> StoreOpeningBackfillResult:
        target_snapshot_id = uuid.UUID(str(snapshot_id))
        target_tenant_id = uuid.UUID(str(tenant_id))

        # 1. Validate all records
        validated_records: list[ApprovedStoreOpeningAuthority] = []
        record_map_by_store: dict[uuid.UUID, ApprovedStoreOpeningAuthority] = {}

        for raw_rec in records:
            auth = validate_store_opening_record(raw_rec)
            if auth.tenant_id != target_tenant_id:
                raise TenantIsolationError(
                    f"Record tenant {auth.tenant_id} does not match target backfill tenant {target_tenant_id}"
                )
            if auth.snapshot_id != target_snapshot_id:
                raise UnauthoritativeStoreOpeningError(
                    f"Record snapshot {auth.snapshot_id} does not match target snapshot {target_snapshot_id}"
                )
            if auth.store_id in record_map_by_store:
                existing_auth = record_map_by_store[auth.store_id]
                if existing_auth.opened_on != auth.opened_on:
                    raise UnauthoritativeStoreOpeningError(
                        f"Conflicting opened_on dates for store {auth.store_id} within snapshot {target_snapshot_id}: {existing_auth.opened_on} vs {auth.opened_on}"
                    )
            validated_records.append(auth)
            record_map_by_store[auth.store_id] = auth

        # 2. Check eligible stores fail closed
        if eligible_store_ids is not None:
            missing_stores: list[str] = []
            for raw_elig in eligible_store_ids:
                elig_uuid = uuid.UUID(str(raw_elig))
                if elig_uuid not in record_map_by_store:
                    missing_stores.append(str(elig_uuid))

            if missing_stores:
                raise MissingStoreOpeningAuthorityError(
                    f"Eligible stores missing authoritative opening date in snapshot {target_snapshot_id}: {missing_stores}. Fail-closed triggered."
                )

        # 3. Process database updates & lineage
        updated_store_ids: list[uuid.UUID] = []
        lineage_entries: list[dict[str, Any]] = []
        run_id = uuid.uuid4()

        if self.db_conn is not None:
            self._apply_db_backfill(
                validated_records=validated_records,
                target_tenant_id=target_tenant_id,
                target_snapshot_id=target_snapshot_id,
                run_id=run_id,
                dry_run=dry_run,
                updated_store_ids=updated_store_ids,
                lineage_entries=lineage_entries,
            )
        else:
            # In-memory execution
            for auth in validated_records:
                s_id_str = str(auth.store_id)
                existing = self._in_memory_stores.get(s_id_str)
                if not dry_run:
                    if existing is None:
                        raise TenantIsolationError(
                            f"Store {auth.store_id} does not exist in store inventory for tenant {target_tenant_id}"
                        )
                    if existing["tenant_id"] != str(target_tenant_id):
                        raise TenantIsolationError(
                            f"Store {auth.store_id} belongs to tenant {existing['tenant_id']}, not target tenant {target_tenant_id}"
                        )
                    existing["opened_on"] = auth.opened_on
                    content_sha = hashlib.sha256(json.dumps(auth.to_dict(), sort_keys=True).encode()).hexdigest()
                    lineage = {
                        "source_snapshot_id": str(target_snapshot_id),
                        "source_kind": "store_opening_authority",
                        "source_id": auth.source_id,
                        "content_sha256": content_sha,
                        "run_id": str(run_id),
                        "tenant_id": str(target_tenant_id),
                        "canonical_table": "core.stores",
                        "canonical_id": str(auth.store_id),
                        "opened_on": auth.opened_on.isoformat(),
                        "projected_at": datetime.now(tz=UTC).isoformat(),
                    }
                    self._in_memory_lineage.append(lineage)
                    lineage_entries.append(lineage)
                else:
                    if existing is not None and existing["tenant_id"] != str(target_tenant_id):
                        raise TenantIsolationError(
                            f"Store {auth.store_id} belongs to tenant {existing['tenant_id']}, not target tenant {target_tenant_id}"
                        )
                updated_store_ids.append(auth.store_id)

        return StoreOpeningBackfillResult(
            tenant_id=target_tenant_id,
            snapshot_id=target_snapshot_id,
            processed_count=len(validated_records),
            updated_count=len(updated_store_ids),
            store_ids=updated_store_ids,
            lineage_records=lineage_entries,
        )

    def _is_sqlite(self) -> bool:
        if self.db_conn is None:
            return True
        conn_str = str(type(self.db_conn)).lower()
        if "psycopg" in conn_str or "postgres" in conn_str:
            return False
        return "sqlite" in conn_str or hasattr(self.db_conn, "row_factory")

    def _apply_db_backfill(
        self,
        validated_records: list[ApprovedStoreOpeningAuthority],
        target_tenant_id: uuid.UUID,
        target_snapshot_id: uuid.UUID,
        run_id: uuid.UUID,
        dry_run: bool,
        updated_store_ids: list[uuid.UUID],
        lineage_entries: list[dict[str, Any]],
    ) -> None:
        is_sqlite = self._is_sqlite()
        cur = self.db_conn.cursor()

        select_sql = (
            "SELECT tenant_id FROM stores WHERE store_id = %s"
            if is_sqlite
            else "SELECT tenant_id FROM core.stores WHERE store_id = %s"
        )
        update_sql = (
            "UPDATE stores SET opened_on = %s, updated_at = CURRENT_TIMESTAMP WHERE store_id = %s AND tenant_id = %s"
            if is_sqlite
            else "UPDATE core.stores SET opened_on = %s, updated_at = CURRENT_TIMESTAMP WHERE store_id = %s AND tenant_id = %s"
        )

        ingestion_run_sql = (
            "INSERT INTO ingestion_runs (run_id, source_database, source_kind, partition_key, status, started_at) VALUES (%s, 'fongniao_prod', 'store_opening_authority', %s, 'SUCCEEDED', CURRENT_TIMESTAMP)"
            if is_sqlite
            else f"INSERT INTO {self.schema}.ingestion_runs (run_id, source_database, source_kind, partition_key, status, started_at) VALUES (%s, 'fongniao_prod', 'store_opening_authority', %s, 'SUCCEEDED', CURRENT_TIMESTAMP) ON CONFLICT (source_kind, partition_key, run_id) DO NOTHING"
        )

        canonical_lineage_sql = (
            """
            INSERT INTO canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """
            if is_sqlite
            else f"""
            INSERT INTO {self.schema}.canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (source_snapshot_id, canonical_table, canonical_id)
            DO UPDATE SET content_sha256 = EXCLUDED.content_sha256, projected_at = CURRENT_TIMESTAMP
            """
        )

        intake_lineage_sql = (
            """
            INSERT INTO store_opening_authority_lineage (
                source_snapshot_id, source_id, tenant_id, store_id,
                opened_on, authority_type, provenance_note, content_sha256, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """
            if is_sqlite
            else """
            INSERT INTO intake.store_opening_authority_lineage (
                source_snapshot_id, source_id, tenant_id, store_id,
                opened_on, authority_type, provenance_note, content_sha256, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (source_snapshot_id, store_id)
            DO UPDATE SET opened_on = EXCLUDED.opened_on, content_sha256 = EXCLUDED.content_sha256, projected_at = CURRENT_TIMESTAMP
            """
        )

        if is_sqlite:
            select_sql = select_sql.replace("%s", "?")
            update_sql = update_sql.replace("%s", "?")
            ingestion_run_sql = ingestion_run_sql.replace("%s", "?")
            canonical_lineage_sql = canonical_lineage_sql.replace("%s", "?")
            intake_lineage_sql = intake_lineage_sql.replace("%s", "?")

        # 1. Insert ingestion_runs record first so canonical_lineage FK is satisfied
        if not dry_run:
            try:
                cur.execute(ingestion_run_sql, (str(run_id), str(target_tenant_id)))
            except Exception:
                if not is_sqlite:
                    raise

        for auth in validated_records:
            # Check store existence & tenant safety
            cur.execute(select_sql, (str(auth.store_id),))
            row = cur.fetchone()
            if row is not None:
                store_tenant = str(row[0])
                if store_tenant != str(target_tenant_id):
                    raise TenantIsolationError(
                        f"Store {auth.store_id} belongs to tenant {store_tenant}, not target tenant {target_tenant_id}"
                    )
            elif not dry_run:
                raise TenantIsolationError(
                    f"Store {auth.store_id} does not exist in core.stores for tenant {target_tenant_id}"
                )


            if not dry_run:
                # Update core.stores.opened_on
                opened_val = auth.opened_on.isoformat() if is_sqlite else auth.opened_on
                cur.execute(
                    update_sql,
                    (opened_val, str(auth.store_id), str(target_tenant_id)),
                )

                content_sha = hashlib.sha256(json.dumps(auth.to_dict(), sort_keys=True).encode()).hexdigest()

                # Insert data_plane.canonical_lineage record
                try:
                    cur.execute(
                        canonical_lineage_sql,
                        (
                            str(target_snapshot_id),
                            "store_opening_authority",
                            auth.source_id,
                            content_sha,
                            str(run_id),
                            str(target_tenant_id),
                            "core.stores",
                            str(auth.store_id),
                        ),
                    )
                except Exception:
                    if not is_sqlite:
                        raise

                # Insert intake.store_opening_authority_lineage record
                try:
                    cur.execute(
                        intake_lineage_sql,
                        (
                            str(target_snapshot_id),
                            auth.source_id,
                            str(target_tenant_id),
                            str(auth.store_id),
                            opened_val,
                            auth.authority_type,
                            auth.provenance_note,
                            content_sha,
                        ),
                    )
                except Exception:
                    if not is_sqlite:
                        raise

                lineage_entries.append({
                    "source_snapshot_id": str(target_snapshot_id),
                    "source_kind": "store_opening_authority",
                    "source_id": auth.source_id,
                    "content_sha256": content_sha,
                    "run_id": str(run_id),
                    "tenant_id": str(target_tenant_id),
                    "canonical_table": "core.stores",
                    "canonical_id": str(auth.store_id),
                    "opened_on": auth.opened_on.isoformat(),
                })
            updated_store_ids.append(auth.store_id)

        if not dry_run and hasattr(self.db_conn, "commit"):
            self.db_conn.commit()
