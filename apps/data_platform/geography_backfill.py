"""Point-in-time geography backfill from approved live providers.

ODP-PRODUCTION-GEOGRAPHY-BACKFILL-001. Geocodes every current canonical store
address through the governed live geocode provider, cross-checks the result
against the official administrative-boundary snapshot, persists an immutable
provider snapshot per call, and only then projects a point-in-time
``place_geography`` row with H3 resolution 8/9/10 and tenant lineage.

Contract:

- Every ``place_geography`` row written here originates from a live
  ``geocode.primary_api`` call whose verbatim payload is retained in
  ``geography_provider_snapshots`` under a content-addressed snapshot id.
- Replay is idempotent: an identical provider response maps to the same
  deterministic ``source_snapshot_id`` and inserts nothing new.
- Conflicting geography (a live observation that disagrees with the current
  canonical geography for a store) is quarantined in
  ``quarantined_records``; the canonical row is never overwritten.
- No fixture, mock, synthetic coordinate, or inferred ``opened_on`` is
  produced. The CLI refuses to run outside live provider mode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.external_data.geo.pipeline import (
    coordinates_in_market,
    normalize_address,
    stable_h3_index,
)

BACKFILL_NAMESPACE = uuid.UUID("6f9f7f9e-3f24-5c11-9c68-2f4f8b1de3a7")
H3_DERIVATION_VERSION = "stable_h3_index-v1"
SOURCE_KIND = "place_geography"
RUN_PREFIX = "place-geography-live"
H3_RESOLUTIONS = (8, 9, 10)
CANONICAL_H3_RESOLUTION = 9
# Same physical location tolerance for replayed provider observations
# (~1 meter). Larger drift is a conflicting observation, not a replay.
COORDINATE_TOLERANCE = 1e-5
# The official boundary dataset predates the 2014 Taoyuan special
# municipality upgrade; both names identify the same admin area.
ADMIN_CITY_ALIASES = {"桃園市": "桃園縣"}


class GeographyBackfillError(RuntimeError):
    """Raised when the backfill cannot proceed safely."""


def canonical_content_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_snapshot_id(store_id: str, content_sha256: str) -> uuid.UUID:
    return uuid.uuid5(BACKFILL_NAMESPACE, f"geocode:{store_id}:{content_sha256}")


def deterministic_run_id(partition_key: str) -> uuid.UUID:
    return uuid.uuid5(BACKFILL_NAMESPACE, f"{RUN_PREFIX}:{partition_key}")


def _norm_admin(value: str) -> str:
    normalized = (value or "").strip().replace("臺", "台")
    return ADMIN_CITY_ALIASES.get(normalized, normalized)


def _address_city_contradicts(address_city: str, provider_city: str) -> bool:
    """True when the source address names a different city than the provider.

    ``normalize_address`` extracts the city with any postal-code / country
    prefix still attached (``432台灣台中市``) and county-administered cities
    keep both levels (``屏東縣屏東市``), so the provider city is checked by
    containment. Either Taoyuan name matches either form.
    """
    address_norm = (address_city or "").replace("臺", "台")
    provider_norm = (provider_city or "").replace("臺", "台")
    if not address_norm or not provider_norm:
        return False
    accepted = {provider_norm}
    if provider_norm in {"桃園市", "桃園縣"}:
        accepted.update({"桃園市", "桃園縣"})
    return not any(name in address_norm for name in accepted)


@dataclass(frozen=True)
class AdminBoundaryIndex:
    """City/district membership index built from one official snapshot."""

    snapshot_id: str
    cities: frozenset[str]
    districts: frozenset[tuple[str, str]]

    @classmethod
    def from_records(
        cls, snapshot_id: str, records: Sequence[Mapping[str, Any]]
    ) -> AdminBoundaryIndex:
        cities: set[str] = set()
        districts: set[tuple[str, str]] = set()
        for record in records:
            level = str(record.get("admin_level") or "")
            name = _norm_admin(str(record.get("admin_name") or ""))
            if not name:
                continue
            if level == "city":
                cities.add(name)
            elif level == "district":
                parent = _norm_admin(str(record.get("parent_admin_code") or ""))
                districts.add((parent, name))
        if not cities:
            raise GeographyBackfillError(
                "official admin-boundary snapshot contains no city records"
            )
        return cls(
            snapshot_id=snapshot_id, cities=frozenset(cities), districts=frozenset(districts)
        )

    def match(self, city: str, district: str) -> bool:
        norm_city = _norm_admin(city)
        if norm_city not in self.cities:
            return False
        norm_district = _norm_admin(district)
        if norm_district and (norm_city, norm_district) not in self.districts:
            return False
        return True


@dataclass
class BackfillReport:
    run_id: str
    partition_key: str
    admin_snapshot_id: str = ""
    poi_snapshot_id: str = ""
    eligible_stores: int = 0
    processed: int = 0
    inserted: int = 0
    unchanged: int = 0
    skipped_no_address: int = 0
    quarantined: dict[str, int] = field(default_factory=dict)
    reconciled: bool = False
    partition_complete: bool = False

    @property
    def quarantined_total(self) -> int:
        return sum(self.quarantined.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "partition_key": self.partition_key,
            "admin_snapshot_id": self.admin_snapshot_id,
            "poi_snapshot_id": self.poi_snapshot_id,
            "eligible_stores": self.eligible_stores,
            "processed": self.processed,
            "inserted": self.inserted,
            "unchanged": self.unchanged,
            "skipped_no_address": self.skipped_no_address,
            "quarantined": dict(sorted(self.quarantined.items())),
            "quarantined_total": self.quarantined_total,
            "reconciled": self.reconciled,
            "partition_complete": self.partition_complete,
        }


# The engine's own DDL, executed statement-by-statement so it works through
# drivers that reject multi-statement commands (pg8000 via the Cloud SQL
# Python Connector). Keep in sync with sql/control_schema.sql.
_EVIDENCE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS {schema}.geography_provider_snapshots (
        source_snapshot_id UUID PRIMARY KEY,
        provider_id TEXT NOT NULL,
        provider_request_id TEXT,
        correlation_id TEXT NOT NULL,
        tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
        store_id UUID NOT NULL REFERENCES core.stores(store_id),
        source_id TEXT NOT NULL,
        request_address TEXT NOT NULL,
        response_payload JSONB NOT NULL,
        content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
        admin_snapshot_id TEXT,
        admin_match BOOLEAN NOT NULL,
        quality_flags TEXT[] NOT NULL DEFAULT '{{}}',
        observed_at TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        run_id UUID NOT NULL REFERENCES {schema}.ingestion_runs(run_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (jsonb_typeof(response_payload) = 'object')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_data_plane_geography_provider_snapshots_store
        ON {schema}.geography_provider_snapshots(tenant_id, store_id, observed_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS {schema}.geo_dataset_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        source_contract_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        record_count INTEGER NOT NULL CHECK (record_count >= 0),
        records JSONB NOT NULL,
        content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
        h3_resolution SMALLINT,
        h3_index_map JSONB NOT NULL DEFAULT '{{}}'::jsonb,
        observed_at TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        run_id UUID NOT NULL REFERENCES {schema}.ingestion_runs(run_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (jsonb_typeof(records) = 'array'),
        CHECK (jsonb_typeof(h3_index_map) = 'object')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_data_plane_geo_dataset_snapshots_provider
        ON {schema}.geo_dataset_snapshots(provider_id, observed_at DESC)
    """,
)


class PlaceGeographyBackfill:
    """Idempotent live-provider geography backfill against PostgreSQL 16."""

    def __init__(
        self,
        connection: Any,
        *,
        geocode_provider: Any,
        admin_boundary_provider: Any,
        poi_provider: Any | None = None,
        control_schema: str = "data_plane",
        rate_limit_per_second: float = 5.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        progress: Callable[[str], None] | None = None,
        limit: int | None = None,
    ) -> None:
        if not control_schema.replace("_", "").isalnum():
            raise GeographyBackfillError("control schema must be a plain SQL identifier")
        self._conn = connection
        self._geocode = geocode_provider
        self._admin = admin_boundary_provider
        self._poi = poi_provider
        self._schema = control_schema
        self._min_interval = 1.0 / rate_limit_per_second if rate_limit_per_second > 0 else 0.0
        self._clock = clock
        self._progress = progress or (lambda message: None)
        self._limit = limit
        self._last_call = 0.0

    # -- SQL helpers ---------------------------------------------------------

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        cursor = self._conn.cursor()
        cursor.execute(sql, tuple(params))
        if cursor.description is None:
            return []
        return [tuple(row) for row in cursor.fetchall()]

    def _commit(self) -> None:
        self._conn.commit()

    # -- run lifecycle -------------------------------------------------------

    def ensure_evidence_tables(self) -> None:
        for statement in _EVIDENCE_DDL:
            self._execute(statement.format(schema=self._schema))
        self._commit()

    def run(self, *, as_of: datetime | None = None) -> BackfillReport:
        started_at = self._clock()
        as_of = as_of or started_at
        partition_key = as_of.date().isoformat()
        run_id = str(deterministic_run_id(partition_key))
        report = BackfillReport(run_id=run_id, partition_key=partition_key)

        self.ensure_evidence_tables()
        self._begin_run(run_id, partition_key, started_at)
        baseline = self._run_row_counts(run_id)
        try:
            admin_index = self._capture_admin_snapshot(run_id, report)
            stores = self._load_stores()
            report.eligible_stores = len(stores)
            self._progress(f"run {run_id}: {len(stores)} current stores with canonical addresses")
            for position, store in enumerate(stores, start=1):
                self._backfill_store(run_id, partition_key, store, admin_index, report)
                if position % 100 == 0:
                    self._progress(
                        f"{position}/{len(stores)} processed "
                        f"(+{report.inserted} inserted, ={report.unchanged} unchanged, "
                        f"q={report.quarantined_total})"
                    )
                if self._limit is not None and position >= self._limit:
                    break
            if self._poi is not None:
                self._capture_poi_snapshot(run_id, report)
            self._finalize_run(run_id, report, baseline)
        except Exception as exc:
            self._fail_run(run_id, exc)
            raise
        return report

    def _begin_run(self, run_id: str, partition_key: str, started_at: datetime) -> None:
        self._execute(
            f"""
            INSERT INTO {self._schema}.ingestion_runs (
                run_id, source_database, source_kind, partition_key, status, started_at
            ) VALUES (%s::uuid, 'fongniao_prod', %s, %s, 'RUNNING', %s::timestamptz)
            ON CONFLICT (run_id) DO UPDATE SET
                status = 'RUNNING',
                reconciled = FALSE,
                partition_complete = FALSE,
                error_type = NULL,
                error_message = NULL
            """,  # nosec B608 -- schema identifier validated in __init__.
            (run_id, SOURCE_KIND, partition_key, started_at.isoformat()),
        )
        self._commit()

    def _run_row_counts(self, run_id: str) -> tuple[int, int]:
        (canonical,) = self._execute(
            f"SELECT count(*) FROM {self._schema}.place_geography WHERE run_id = %s::uuid",  # nosec B608
            (run_id,),
        )[0]
        (quarantined,) = self._execute(
            f"SELECT count(*) FROM {self._schema}.quarantined_records "  # nosec B608
            "WHERE run_id = %s::uuid AND resolved_at IS NULL",
            (run_id,),
        )[0]
        return int(canonical), int(quarantined)

    def _finalize_run(self, run_id: str, report: BackfillReport, baseline: tuple[int, int]) -> None:
        canonical_after, quarantined_after = self._run_row_counts(run_id)
        canonical_delta = canonical_after - baseline[0]
        quarantined_delta = quarantined_after - baseline[1]
        # A same-day replay reuses the deterministic run id, so reconcile the
        # delta this execution produced. Replayed quarantines hit
        # ON CONFLICT DO NOTHING, hence <= for the quarantine delta.
        report.reconciled = (
            canonical_delta == report.inserted
            and 0 <= quarantined_delta <= report.quarantined_total
        )
        report.partition_complete = (
            report.processed + report.skipped_no_address >= report.eligible_stores
            if self._limit is None
            else False
        )
        self._execute(
            f"""
            UPDATE {self._schema}.ingestion_runs SET
                status = 'SUCCEEDED',
                processed_count = %s,
                valid_loaded = %s,
                quarantined_count = %s,
                reconciled = %s,
                partition_complete = %s,
                final_cursor = %s,
                canonical_checksum = %s,
                finished_at = %s::timestamptz
            WHERE run_id = %s::uuid
            """,  # nosec B608
            (
                report.processed,
                report.inserted,
                report.quarantined_total,
                report.reconciled,
                report.partition_complete,
                json.dumps(report.to_dict(), sort_keys=True),
                canonical_content_sha256(report.to_dict()),
                self._clock().isoformat(),
                run_id,
            ),
        )
        self._commit()

    def _fail_run(self, run_id: str, exc: Exception) -> None:
        try:
            self._conn.rollback()
            self._execute(
                f"""
                UPDATE {self._schema}.ingestion_runs SET
                    status = 'FAILED', error_type = %s, error_message = %s,
                    finished_at = %s::timestamptz
                WHERE run_id = %s::uuid
                """,  # nosec B608
                (type(exc).__name__, str(exc)[:2000], self._clock().isoformat(), run_id),
            )
            self._commit()
        except Exception:  # pragma: no cover - failure-path best effort
            pass

    # -- provider snapshots --------------------------------------------------

    def _capture_admin_snapshot(self, run_id: str, report: BackfillReport) -> AdminBoundaryIndex:
        result = self._admin.fetch_and_ingest()
        raw = result.raw_snapshot
        records = [dict(record) for record in raw.records]
        self._persist_dataset_snapshot(run_id, raw, records, h3_index_map=None)
        report.admin_snapshot_id = raw.snapshot_id
        self._commit()
        return AdminBoundaryIndex.from_records(raw.snapshot_id, records)

    def _capture_poi_snapshot(self, run_id: str, report: BackfillReport) -> None:
        result = self._poi.fetch_and_ingest()
        raw = result.raw_snapshot
        records = [dict(record) for record in raw.records]
        h3_index_map: dict[str, str] = {}
        for record in records:
            try:
                latitude = float(record.get("latitude"))
                longitude = float(record.get("longitude"))
            except (TypeError, ValueError):
                continue
            if coordinates_in_market(latitude, longitude):
                key = str(record.get("source_poi_id") or record.get("poi_name") or "")
                if key:
                    h3_index_map[key] = stable_h3_index(
                        latitude, longitude, CANONICAL_H3_RESOLUTION
                    )
        self._persist_dataset_snapshot(run_id, raw, records, h3_index_map=h3_index_map)
        report.poi_snapshot_id = raw.snapshot_id
        self._commit()

    def _persist_dataset_snapshot(
        self,
        run_id: str,
        raw: Any,
        records: list[dict[str, Any]],
        *,
        h3_index_map: dict[str, str] | None,
    ) -> None:
        existing = self._execute(
            f"SELECT content_sha256 FROM {self._schema}.geo_dataset_snapshots "  # nosec B608
            "WHERE snapshot_id = %s",
            (raw.snapshot_id,),
        )
        if existing:
            if existing[0][0] != raw.checksum_sha256:
                raise GeographyBackfillError(
                    f"dataset snapshot {raw.snapshot_id} already recorded with a "
                    "different checksum; immutable snapshot conflict"
                )
            return
        self._execute(
            f"""
            INSERT INTO {self._schema}.geo_dataset_snapshots (
                snapshot_id, provider_id, source_contract_id, correlation_id,
                record_count, records, content_sha256, h3_resolution, h3_index_map,
                observed_at, fetched_at, run_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb,
                %s::timestamptz, %s::timestamptz, %s::uuid
            )
            ON CONFLICT (snapshot_id) DO NOTHING
            """,  # nosec B608
            (
                raw.snapshot_id,
                raw.provider_id,
                raw.source_contract_id,
                raw.correlation_id,
                len(records),
                json.dumps(records, ensure_ascii=False, default=str),
                raw.checksum_sha256,
                CANONICAL_H3_RESOLUTION if h3_index_map is not None else None,
                json.dumps(h3_index_map or {}, ensure_ascii=False),
                raw.observed_at.isoformat(),
                raw.fetched_at.isoformat(),
                run_id,
            ),
        )

    # -- store loop ----------------------------------------------------------

    def _load_stores(self) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT store.store_id, store.tenant_id, store.source_store_id,
                   address.raw_address
            FROM core.stores AS store
            LEFT JOIN core.address_locations AS address
                ON address.address_id = store.address_id
            WHERE store.is_current
            ORDER BY store.store_id
            """  # nosec B608 -- static SQL over canonical relations.
        )
        return [
            {
                "store_id": str(row[0]),
                "tenant_id": str(row[1]),
                "source_id": str(row[2]),
                "raw_address": (row[3] or "").strip(),
            }
            for row in rows
        ]

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    def _backfill_store(
        self,
        run_id: str,
        partition_key: str,
        store: dict[str, Any],
        admin_index: AdminBoundaryIndex,
        report: BackfillReport,
    ) -> None:
        if not store["raw_address"]:
            report.skipped_no_address += 1
            return
        report.processed += 1
        normalized = normalize_address(store["raw_address"])
        self._throttle()
        candidate, payload = self._geocode.lookup_with_payload(normalized)
        fetched_at = self._clock()
        payload_map = dict(payload) if isinstance(payload, Mapping) else {"raw": str(payload)}
        content_sha = canonical_content_sha256(payload_map)
        snapshot_id = str(deterministic_snapshot_id(store["store_id"], content_sha))
        observed_at = (
            candidate.provider_observed_at if candidate is not None else None
        ) or fetched_at

        flags: list[str] = []
        reason: tuple[str, str] | None = None
        admin_match = False
        if (
            candidate is None
            or candidate.provider == "unresolved"
            or (candidate.latitude == 0.0 and candidate.longitude == 0.0)
        ):
            reason = ("GEOCODE_UNRESOLVED", "live provider returned no usable candidate")
            flags.append("missing_geocode")
        elif not coordinates_in_market(candidate.latitude, candidate.longitude):
            reason = (
                "COORDINATES_OUT_OF_MARKET",
                f"({candidate.latitude}, {candidate.longitude}) outside market bounds",
            )
            flags.append("coordinates_out_of_market")
        else:
            admin_match = admin_index.match(candidate.admin_city, candidate.admin_district)
            if not admin_match:
                reason = (
                    "ADMIN_BOUNDARY_MISMATCH",
                    f"provider admin ({candidate.admin_city}/{candidate.admin_district}) "
                    f"not in official snapshot {admin_index.snapshot_id}",
                )
                flags.append("admin_mismatch")
            elif _address_city_contradicts(normalized.city, candidate.admin_city):
                reason = (
                    "GEOCODE_ADMIN_MISMATCH",
                    f"address city {normalized.city} contradicts provider city "
                    f"{candidate.admin_city}",
                )
                flags.append("address_admin_mismatch")
            if candidate.confidence < 0.7:
                flags.append("low_geocode_confidence")

        self._persist_provider_snapshot(
            run_id,
            snapshot_id=snapshot_id,
            store=store,
            payload=payload_map,
            content_sha=content_sha,
            candidate=candidate,
            admin_index=admin_index,
            admin_match=admin_match,
            flags=flags,
            observed_at=observed_at,
            fetched_at=fetched_at,
        )

        if reason is None:
            reason = self._project_place_geography(
                run_id,
                snapshot_id=snapshot_id,
                store=store,
                normalized_address=normalized.normalized_address,
                candidate=candidate,
                content_sha=content_sha,
                observed_at=observed_at,
                report=report,
            )
        if reason is not None:
            self._quarantine(
                run_id,
                partition_key,
                snapshot_id=snapshot_id,
                store=store,
                content_sha=content_sha,
                reason_code=reason[0],
                reason_detail=reason[1],
            )
            report.quarantined[reason[0]] = report.quarantined.get(reason[0], 0) + 1
        self._commit()

    def _persist_provider_snapshot(
        self,
        run_id: str,
        *,
        snapshot_id: str,
        store: dict[str, Any],
        payload: dict[str, Any],
        content_sha: str,
        candidate: Any,
        admin_index: AdminBoundaryIndex,
        admin_match: bool,
        flags: list[str],
        observed_at: datetime,
        fetched_at: datetime,
    ) -> None:
        flags_literal = "{" + ",".join(sorted(set(flags))) + "}"
        self._execute(
            f"""
            INSERT INTO {self._schema}.geography_provider_snapshots (
                source_snapshot_id, provider_id, provider_request_id, correlation_id,
                tenant_id, store_id, source_id, request_address, response_payload,
                content_sha256, admin_snapshot_id, admin_match, quality_flags,
                observed_at, fetched_at, run_id
            ) VALUES (
                %s::uuid, %s, %s, %s, %s::uuid, %s::uuid, %s, %s, %s::jsonb,
                %s, %s, %s, %s::text[], %s::timestamptz, %s::timestamptz, %s::uuid
            )
            ON CONFLICT (source_snapshot_id) DO NOTHING
            """,  # nosec B608
            (
                snapshot_id,
                getattr(candidate, "provider", None) or "geocode.primary_api",
                getattr(candidate, "provider_request_id", "") or None,
                getattr(self._geocode, "correlation_id", "") or "unknown",
                store["tenant_id"],
                store["store_id"],
                store["source_id"],
                store["raw_address"],
                json.dumps(payload, ensure_ascii=False, default=str),
                content_sha,
                admin_index.snapshot_id,
                admin_match,
                flags_literal,
                observed_at.isoformat(),
                fetched_at.isoformat(),
                run_id,
            ),
        )

    def _project_place_geography(
        self,
        run_id: str,
        *,
        snapshot_id: str,
        store: dict[str, Any],
        normalized_address: str,
        candidate: Any,
        content_sha: str,
        observed_at: datetime,
        report: BackfillReport,
    ) -> tuple[str, str] | None:
        current = self._execute(
            f"""
            SELECT source_snapshot_id, latitude, longitude, h3_res_9
            FROM {self._schema}.place_geography
            WHERE tenant_id = %s::uuid AND store_id = %s::uuid
            ORDER BY valid_from DESC, observed_at DESC
            LIMIT 1
            """,  # nosec B608
            (store["tenant_id"], store["store_id"]),
        )
        h3_map = {
            resolution: stable_h3_index(candidate.latitude, candidate.longitude, resolution)
            for resolution in H3_RESOLUTIONS
        }
        if current:
            _existing_id, latitude, longitude, h3_res_9 = current[0]
            same_cell = h3_res_9 == h3_map[CANONICAL_H3_RESOLUTION]
            same_point = (
                latitude is not None
                and longitude is not None
                and abs(float(latitude) - candidate.latitude) <= COORDINATE_TOLERANCE
                and abs(float(longitude) - candidate.longitude) <= COORDINATE_TOLERANCE
            )
            if same_cell and same_point:
                report.unchanged += 1
                return None
            return (
                "GEOGRAPHY_CONFLICT",
                f"live observation h3={h3_map[CANONICAL_H3_RESOLUTION]} "
                f"({candidate.latitude}, {candidate.longitude}) conflicts with current "
                f"canonical geography h3={h3_res_9} ({latitude}, {longitude})",
            )
        self._execute(
            f"""
            INSERT INTO {self._schema}.place_geography (
                source_snapshot_id, source_id, tenant_id, store_id,
                raw_address, normalized_address, city, district,
                latitude, longitude, geocode_confidence,
                h3_res_8, h3_res_9, h3_res_10, h3_derivation_version,
                run_id, valid_from, observed_at
            ) VALUES (
                %s::uuid, %s, %s::uuid, %s::uuid, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s::uuid, %s::timestamptz, %s::timestamptz
            )
            ON CONFLICT (source_snapshot_id) DO NOTHING
            """,  # nosec B608
            (
                snapshot_id,
                store["source_id"],
                store["tenant_id"],
                store["store_id"],
                store["raw_address"],
                normalized_address,
                _norm_admin(candidate.admin_city) or None,
                _norm_admin(candidate.admin_district) or None,
                round(candidate.latitude, 7),
                round(candidate.longitude, 7),
                round(min(max(candidate.confidence, 0.0), 1.0), 4),
                h3_map[8],
                h3_map[9],
                h3_map[10],
                H3_DERIVATION_VERSION,
                run_id,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        self._execute(
            f"""
            INSERT INTO {self._schema}.canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id
            ) VALUES (%s::uuid, %s, %s, %s, %s::uuid, %s::uuid, %s, %s::uuid)
            ON CONFLICT (source_snapshot_id, canonical_table, canonical_id) DO NOTHING
            """,  # nosec B608
            (
                snapshot_id,
                SOURCE_KIND,
                store["source_id"],
                content_sha,
                run_id,
                store["tenant_id"],
                f"{self._schema}.place_geography",
                store["store_id"],
            ),
        )
        report.inserted += 1
        return None

    def _quarantine(
        self,
        run_id: str,
        partition_key: str,
        *,
        snapshot_id: str,
        store: dict[str, Any],
        content_sha: str,
        reason_code: str,
        reason_detail: str,
    ) -> None:
        self._execute(
            f"""
            INSERT INTO {self._schema}.quarantined_records (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, partition_key, reason_code, reason_detail, retryable
            ) VALUES (%s::uuid, %s, %s, %s, %s::uuid, %s, %s, %s, FALSE)
            ON CONFLICT (source_snapshot_id) DO NOTHING
            """,  # nosec B608
            (
                snapshot_id,
                SOURCE_KIND,
                store["source_id"],
                content_sha,
                run_id,
                partition_key,
                reason_code,
                reason_detail[:1000],
            ),
        )


# -- CLI ---------------------------------------------------------------------


def _require_live_mode(env: Mapping[str, str]) -> None:
    mode = env.get("ODP_EXTERNAL_PROVIDER_MODE", "").strip().lower()
    if mode != "live":
        raise GeographyBackfillError(
            "geography backfill only runs against approved live providers; "
            "set ODP_EXTERNAL_PROVIDER_MODE=live (fixture/mock modes are refused)"
        )


def _validate_dsn(dsn: str) -> dict[str, Any]:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(dsn)
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise GeographyBackfillError("ODAY_POSTGRES_DSN must be a PostgreSQL DSN")
    query = parse_qs(parsed.query)
    socket_host = (query.get("host") or [""])[0]
    if not socket_host.startswith("/cloudsql/") and parsed.hostname in {
        None,
        "",
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise GeographyBackfillError(
            "ODAY_POSTGRES_DSN must target the approved managed instance, not localhost"
        )
    return {
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/"),
        "host": parsed.hostname,
        "port": parsed.port,
        "cloud_sql_instance": socket_host.replace("/cloudsql/", "") if socket_host else None,
    }


def _open_connection(env: Mapping[str, str]) -> Any:
    dsn = env.get("ODAY_POSTGRES_DSN", "").strip()
    if not dsn:
        raise GeographyBackfillError("ODAY_POSTGRES_DSN is required")
    info = _validate_dsn(dsn)
    if info["cloud_sql_instance"] and env.get("ODP_GEO_CLOUD_SQL_CONNECTOR", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        # cloud-sql-python-connector is an approved data-plane transport
        # (see apps/data_platform/README.md).
        from google.cloud.sql.connector import Connector

        credentials = None
        token = env.get("ODP_GEO_GCP_ACCESS_TOKEN", "").strip()
        if token:
            from google.oauth2.credentials import Credentials

            credentials = Credentials(token=token)
        connector = Connector(credentials=credentials)
        return connector.connect(
            info["cloud_sql_instance"],
            "pg8000",
            user=info["user"],
            password=info["password"],
            db=info["database"],
        )
    import psycopg

    return psycopg.connect(dsn, autocommit=False)


class _QmarkQueryClient:
    """Adapts a DBAPI connection to the scripts.models QueryClient protocol."""

    def __init__(self, connection: Any) -> None:
        self._conn = connection

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(sql.replace("?", "%s"), tuple(params))
        columns = [column[0] for column in cursor.description or ()]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


def _build_engine(args: argparse.Namespace, env: Mapping[str, str]) -> PlaceGeographyBackfill:
    from modules.external_data.providers.live import (
        AdminBoundaryDatasetProvider,
        PoiCommercialApiProvider,
        PrimaryGeocodeProvider,
    )

    _require_live_mode(env)
    connection = _open_connection(env)
    geocode = PrimaryGeocodeProvider(env=dict(env), mode="live", retry_budget=2)
    admin = AdminBoundaryDatasetProvider(env=dict(env), mode="live")
    poi = None if args.skip_poi else PoiCommercialApiProvider(env=dict(env), mode="live")
    return PlaceGeographyBackfill(
        connection,
        geocode_provider=geocode,
        admin_boundary_provider=admin,
        poi_provider=poi,
        rate_limit_per_second=float(env.get("ODP_GEO_BACKFILL_RATE_LIMIT_PER_SECOND", "5")),
        progress=lambda message: print(message, file=sys.stderr, flush=True),
        limit=args.limit,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill point-in-time place geography from approved live providers",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="execute the live geography backfill")
    run.add_argument("--as-of", help="partition date (YYYY-MM-DD); defaults to today UTC")
    run.add_argument("--limit", type=int, default=None, help="process at most N stores")
    run.add_argument("--skip-poi", action="store_true", help="skip the POI dataset snapshot")
    inventory = sub.add_parser(
        "inventory", help="print the model-ready inventory for a model over the same DSN"
    )
    inventory.add_argument("--model", default="heatzone")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = dict(os.environ)
    try:
        if args.command == "inventory":
            from scripts.models.contracts import MODEL_SPECS
            from scripts.models.storage import PostgresModelReadySource

            connection = _open_connection(env)
            source = PostgresModelReadySource(_QmarkQueryClient(connection))
            result = source.inventory(MODEL_SPECS[args.model])
            print(json.dumps(result.to_dict(), sort_keys=True, default=str))
            return 0
        engine = _build_engine(args, env)
        as_of = datetime.fromisoformat(args.as_of).replace(tzinfo=UTC) if args.as_of else None
        report = engine.run(as_of=as_of)
        print(json.dumps(report.to_dict(), sort_keys=True))
        return 0 if report.reconciled else 2
    except GeographyBackfillError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AdminBoundaryIndex",
    "BackfillReport",
    "GeographyBackfillError",
    "PlaceGeographyBackfill",
    "canonical_content_sha256",
    "deterministic_run_id",
    "deterministic_snapshot_id",
    "main",
]
