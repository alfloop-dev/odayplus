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
STORE_OPENING_RUN_NAMESPACE = uuid.UUID("183ce176-5cf1-4c6f-9099-625b76fb6fab")
STORE_OPENING_LINEAGE_NAMESPACE = uuid.UUID("e82339e0-5266-4f80-b3a2-f9afc55a86dd")


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
    source_id = str(record.get("source_id") or "").strip()
    if not source_id:
        raise UnauthoritativeStoreOpeningError("Missing source identity")
    source_allowlist = (
        APPROVED_STORE_OPENING_SOURCES if approved_sources is None else approved_sources
    )
    if source_id not in source_allowlist:
        raise UnauthoritativeStoreOpeningError(
            f"Unapproved source identity: {source_id!r}. "
            f"Approved sources: {sorted(source_allowlist)}"
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
        if schema != "data_plane":
            raise ValueError("store-opening lineage is restricted to the data_plane schema")
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
        if eligible_store_ids is None:
            eligible_store_ids = self._eligible_store_ids(target_tenant_id)
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
        # A snapshot/tenant pair is one logical replayable run.  A deterministic
        # identifier prevents retries from manufacturing new ingestion history.
        run_id = uuid.uuid5(
            STORE_OPENING_RUN_NAMESPACE,
            f"{target_tenant_id}:{target_snapshot_id}",
        )

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
                if existing is None:
                    raise TenantIsolationError(
                        f"Store {auth.store_id} does not exist in store inventory "
                        f"for tenant {target_tenant_id}"
                    )
                if not dry_run:
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

    def _eligible_store_ids(self, tenant_id: uuid.UUID) -> list[uuid.UUID]:
        """Return the authoritative tenant inventory still missing opened_on."""
        if self.db_conn is None:
            return [
                uuid.UUID(store_id)
                for store_id, store in self._in_memory_stores.items()
                if store["tenant_id"] == str(tenant_id) and store["opened_on"] is None
            ]
        is_sqlite = self._is_sqlite()
        sql = (
            "SELECT store_id FROM stores WHERE tenant_id = ? AND opened_on IS NULL"
            if is_sqlite
            else "SELECT store_id FROM core.stores WHERE tenant_id = %s AND opened_on IS NULL"
        )
        cur = self.db_conn.cursor()
        cur.execute(sql, (str(tenant_id),))
        return [uuid.UUID(str(row[0])) for row in cur.fetchall()]

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
            "SELECT tenant_id, opened_on FROM stores WHERE store_id = %s"
            if is_sqlite
            else (
                "SELECT tenant_id, opened_on FROM core.stores "
                "WHERE store_id = %s FOR UPDATE"
            )
        )
        update_sql = (
            "UPDATE stores SET opened_on = %s, updated_at = CURRENT_TIMESTAMP WHERE store_id = %s AND tenant_id = %s"
            if is_sqlite
            else "UPDATE core.stores SET opened_on = %s, updated_at = CURRENT_TIMESTAMP WHERE store_id = %s AND tenant_id = %s"
        )

        ingestion_run_sql = (
            "INSERT INTO ingestion_runs (run_id, source_database, source_kind, partition_key, status, started_at) VALUES (%s, 'fongniao_prod', 'store_opening_authority', %s, 'SUCCEEDED', CURRENT_TIMESTAMP) ON CONFLICT (run_id) DO NOTHING"
            if is_sqlite
            else "INSERT INTO data_plane.ingestion_runs (run_id, source_database, source_kind, partition_key, status, started_at) VALUES (%s, 'fongniao_prod', 'store_opening_authority', %s, 'SUCCEEDED', CURRENT_TIMESTAMP) ON CONFLICT (source_kind, partition_key, run_id) DO NOTHING"
        )

        canonical_lineage_sql = (
            """
            INSERT INTO canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (source_snapshot_id, canonical_table, canonical_id)
            DO NOTHING
            """
            if is_sqlite
            else """
            INSERT INTO data_plane.canonical_lineage (
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
                lineage_id, source_snapshot_id, source_id, tenant_id, store_id,
                opened_on, authority_type, provenance_note, content_sha256, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (source_snapshot_id, store_id) DO NOTHING
            """
            if is_sqlite
            else """
            INSERT INTO intake.store_opening_authority_lineage (
                lineage_id, source_snapshot_id, source_id, tenant_id, store_id,
                opened_on, authority_type, provenance_note, content_sha256, projected_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (source_snapshot_id, store_id)
            DO NOTHING
            """
        )
        existing_authority_sql = (
            """
            SELECT source_id, tenant_id, opened_on, authority_type, content_sha256
            FROM store_opening_authority_lineage
            WHERE source_snapshot_id = %s AND store_id = %s
            """
            if is_sqlite
            else """
            SELECT source_id, tenant_id, opened_on, authority_type, content_sha256
            FROM intake.store_opening_authority_lineage
            WHERE source_snapshot_id = %s AND store_id = %s
            """
        )
        authority_snapshot_sql = """
            SELECT sr.retrieval_mode, sr.legal_approval_ref,
                   sr.license_approval_ref, sr.production_enabled,
                   sr.kill_switch
            FROM intake.source_snapshots ss
            JOIN intake.source_registry sr ON sr.source_id = ss.source_id
            WHERE ss.source_snapshot_id = %s
              AND ss.tenant_id = %s
              AND ss.source_id = %s
        """

        if is_sqlite:
            select_sql = select_sql.replace("%s", "?")
            update_sql = update_sql.replace("%s", "?")
            ingestion_run_sql = ingestion_run_sql.replace("%s", "?")
            canonical_lineage_sql = canonical_lineage_sql.replace("%s", "?")
            intake_lineage_sql = intake_lineage_sql.replace("%s", "?")
            existing_authority_sql = existing_authority_sql.replace("%s", "?")

        try:
            if not is_sqlite:
                for auth in validated_records:
                    cur.execute(
                        authority_snapshot_sql,
                        (
                            str(target_snapshot_id),
                            str(target_tenant_id),
                            auth.source_id,
                        ),
                    )
                    authority = cur.fetchone()
                    approved = (
                        authority is not None
                        and authority[0] == "APPROVED_RETRIEVAL"
                        and (authority[1] or authority[2])
                        and authority[3] is True
                        and authority[4] is False
                    )
                    if not approved:
                        raise UnauthoritativeStoreOpeningError(
                            f"Snapshot {target_snapshot_id} is not bound to an enabled, "
                            f"approved source registry identity {auth.source_id!r}"
                        )

            # Insert ingestion_runs first so canonical_lineage's FK is satisfied.
            if not dry_run:
                cur.execute(ingestion_run_sql, (str(run_id), str(target_tenant_id)))

            for auth in validated_records:
                # Check store existence & tenant safety even during dry-run.
                cur.execute(select_sql, (str(auth.store_id),))
                row = cur.fetchone()
                if row is None:
                    raise TenantIsolationError(
                        f"Store {auth.store_id} does not exist in core.stores for tenant {target_tenant_id}"
                    )
                store_tenant = str(row[0])
                if store_tenant != str(target_tenant_id):
                    raise TenantIsolationError(
                        f"Store {auth.store_id} belongs to tenant {store_tenant}, not target tenant {target_tenant_id}"
                    )
                existing_opened_on = (
                    _parse_iso_date_or_datetime(row[1]) if row[1] is not None else None
                )
                if (
                    existing_opened_on is not None
                    and existing_opened_on != auth.opened_on
                ):
                    raise UnauthoritativeStoreOpeningError(
                        f"Conflicting authoritative opened_on for store {auth.store_id}: "
                        f"persisted {existing_opened_on}, proposed {auth.opened_on}"
                    )

                opened_val = auth.opened_on.isoformat() if is_sqlite else auth.opened_on
                content_sha = hashlib.sha256(json.dumps(auth.to_dict(), sort_keys=True).encode()).hexdigest()
                cur.execute(
                    existing_authority_sql,
                    (str(target_snapshot_id), str(auth.store_id)),
                )
                existing_authority = cur.fetchone()
                if existing_authority is not None:
                    existing_fingerprint = (
                        str(existing_authority[0]),
                        str(existing_authority[1]),
                        str(existing_authority[2]),
                        str(existing_authority[3]),
                        str(existing_authority[4]),
                    )
                    proposed_fingerprint = (
                        auth.source_id,
                        str(target_tenant_id),
                        auth.opened_on.isoformat(),
                        auth.authority_type,
                        content_sha,
                    )
                    if existing_fingerprint != proposed_fingerprint:
                        raise UnauthoritativeStoreOpeningError(
                            f"Conflicting authority for store {auth.store_id} in immutable snapshot "
                            f"{target_snapshot_id}"
                        )

                if not dry_run:
                    cur.execute(
                        update_sql,
                        (opened_val, str(auth.store_id), str(target_tenant_id)),
                    )
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
                    lineage_id = uuid.uuid5(
                        STORE_OPENING_LINEAGE_NAMESPACE,
                        f"{target_snapshot_id}:{auth.store_id}",
                    )
                    cur.execute(
                        intake_lineage_sql,
                        (
                            str(lineage_id),
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
        except Exception:
            if hasattr(self.db_conn, "rollback"):
                self.db_conn.rollback()
            raise
