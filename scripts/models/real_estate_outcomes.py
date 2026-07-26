from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from modules.external_data.providers.taiwan_real_estate import (
    GOVERNMENT_OPEN_DATA_LICENSE_V1,
    MAX_DOWNLOAD_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_ROWS,
    PARSER_VERSION,
    SOURCES,
    OfficialRealEstateDownloader,
    OfficialRealEstateSourceError,
    OfficialRealEstateTransaction,
    ParsedOfficialRealEstateBatch,
    parse_official_real_estate,
)
from scripts.models.contracts import (
    ModelTrainingConfigurationError,
    require_production_database_url,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "infra"
    / "db"
    / "migrations"
    / "000009_official_real_estate_outcomes.sql"
)


class OutcomeDatabase(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]: ...

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None: ...

    def transaction(self) -> Any: ...


class OfficialRealEstateOutcomeStore:
    def __init__(
        self,
        client: OutcomeDatabase,
        *,
        migration_path: Path = MIGRATION_PATH,
    ) -> None:
        self.client = client
        self.migration_path = migration_path

    def install_schema(self) -> None:
        sql = self.migration_path.read_text(encoding="utf-8")
        _validate_migration(sql)
        self.client.execute(sql)

    def upsert(self, batch: ParsedOfficialRealEstateBatch) -> Mapping[str, Any]:
        source = batch.artifact.source
        with self.client.transaction():
            self.install_schema()
            existing_run = self.client.query_one(
                "SELECT status, parsed_row_count, inserted_row_count, "
                "updated_row_count FROM external_data.real_estate_ingestion_runs "
                "WHERE run_id = ?",
                (batch.run_id,),
            )
            if existing_run and existing_run.get("status") == "SUCCEEDED":
                return {
                    "status": "already_applied",
                    "run_id": str(batch.run_id),
                    "source_id": source.source_id,
                    "source_snapshot_id": batch.source_snapshot_id,
                    "content_sha256": batch.artifact.content_sha256,
                    "schema_sha256": batch.schema_sha256,
                    "parsed_row_count": int(existing_run["parsed_row_count"]),
                    "inserted_row_count": int(existing_run["inserted_row_count"]),
                    "updated_row_count": int(existing_run["updated_row_count"]),
                }
            self.client.execute(
                "INSERT INTO external_data.real_estate_ingestion_runs ("
                "run_id, source_id, dataset_id, source_url, metadata_url, publisher, "
                "license_id, parser_version, content_type, content_sha256, "
                "schema_sha256, source_snapshot_id, content_length_bytes, etag, "
                "source_published_at, fetched_at, status, parsed_row_count"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?) "
                "ON CONFLICT (run_id) DO UPDATE SET "
                "status = 'RUNNING', error_code = NULL, error_message = NULL, "
                "completed_at = NULL, updated_at = CURRENT_TIMESTAMP",
                (
                    batch.run_id,
                    source.source_id,
                    source.dataset_id,
                    source.source_url,
                    source.metadata_url,
                    source.publisher,
                    source.license_id,
                    PARSER_VERSION,
                    batch.artifact.content_type,
                    batch.artifact.content_sha256,
                    batch.schema_sha256,
                    batch.source_snapshot_id,
                    len(batch.artifact.content),
                    batch.artifact.etag,
                    batch.artifact.source_published_at,
                    batch.artifact.fetched_at,
                    len(batch.records),
                ),
            )
            existing_records = {
                (
                    str(row["authority_partition"]),
                    str(row["source_record_id"]),
                    str(row["source_variant_id"]),
                ): str(row["raw_record_sha256"])
                for row in self.client.query(
                    "SELECT authority_partition, source_record_id, "
                    "source_variant_id, raw_record_sha256 "
                    "FROM external_data.real_estate_transactions "
                    "WHERE source_id = ?",
                    (source.source_id,),
                )
            }
            inserted_count = 0
            updated_count = 0
            for record in batch.records:
                prior_digest = existing_records.get(
                    (
                        record.authority_partition,
                        record.source_record_id,
                        record.source_variant_id,
                    )
                )
                if prior_digest is None:
                    inserted_count += 1
                elif prior_digest != record.raw_record_sha256:
                    updated_count += 1
                self._upsert_record(batch, record)
            self.client.execute(
                "UPDATE external_data.real_estate_ingestion_runs SET "
                "status = 'SUCCEEDED', inserted_row_count = ?, updated_row_count = ?, "
                "completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                "WHERE run_id = ?",
                (inserted_count, updated_count, batch.run_id),
            )
        return {
            "status": "succeeded",
            "run_id": str(batch.run_id),
            "source_id": source.source_id,
            "dataset_id": source.dataset_id,
            "license_id": source.license_id,
            "source_snapshot_id": batch.source_snapshot_id,
            "content_sha256": batch.artifact.content_sha256,
            "schema_sha256": batch.schema_sha256,
            "parsed_row_count": len(batch.records),
            "inserted_row_count": inserted_count,
            "updated_row_count": updated_count,
        }

    def inventory(self) -> Mapping[str, Any]:
        self.install_schema()
        source_rows = self.client.query(
            "SELECT source_id, count(*) AS transaction_count, "
            "min(transaction_date) AS transaction_min, "
            "max(transaction_date) AS transaction_max, "
            "max(last_observed_at) AS last_observed_at "
            "FROM external_data.real_estate_transactions "
            "GROUP BY source_id ORDER BY source_id"
        )
        run_rows = self.client.query(
            "SELECT source_id, status, content_sha256, schema_sha256, "
            "parsed_row_count, completed_at "
            "FROM external_data.real_estate_ingestion_runs "
            "ORDER BY fetched_at DESC LIMIT 20"
        )
        return {
            "status": "ok",
            "parser_version": PARSER_VERSION,
            "sources": [_jsonable(row) for row in source_rows],
            "recent_runs": [_jsonable(row) for row in run_rows],
        }

    def _upsert_record(
        self,
        batch: ParsedOfficialRealEstateBatch,
        record: OfficialRealEstateTransaction,
    ) -> None:
        self.client.execute(
            "INSERT INTO external_data.real_estate_transactions ("
            "transaction_id, source_id, authority_partition, source_record_id, "
            "source_variant_id, municipality, district, transaction_target, address, "
            "transaction_date, transaction_count_summary, land_area_sqm, "
            "urban_land_use, non_urban_land_use, non_urban_land_designation, "
            "transferred_floor, total_floors, building_type, main_use, "
            "main_material, completion_date, completion_year, completion_month, "
            "building_area_sqm, room_count, hall_count, bathroom_count, "
            "has_partition, has_management, total_price_twd, unit_price_twd_sqm, "
            "parking_type, parking_area_sqm, parking_price_twd, remarks, "
            "main_building_area_sqm, auxiliary_building_area_sqm, "
            "balcony_area_sqm, has_elevator, transfer_number, first_seen_run_id, "
            "last_seen_run_id, "
            "first_observed_at, last_observed_at, raw_record_sha256"
            ") VALUES ("
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
            ") ON CONFLICT ("
            "source_id, authority_partition, source_record_id, source_variant_id"
            ") "
            "DO UPDATE SET "
            "municipality = EXCLUDED.municipality, district = EXCLUDED.district, "
            "transaction_target = EXCLUDED.transaction_target, address = EXCLUDED.address, "
            "transaction_date = EXCLUDED.transaction_date, "
            "transaction_count_summary = EXCLUDED.transaction_count_summary, "
            "land_area_sqm = EXCLUDED.land_area_sqm, "
            "urban_land_use = EXCLUDED.urban_land_use, "
            "non_urban_land_use = EXCLUDED.non_urban_land_use, "
            "non_urban_land_designation = EXCLUDED.non_urban_land_designation, "
            "transferred_floor = EXCLUDED.transferred_floor, "
            "total_floors = EXCLUDED.total_floors, "
            "building_type = EXCLUDED.building_type, main_use = EXCLUDED.main_use, "
            "main_material = EXCLUDED.main_material, "
            "completion_date = EXCLUDED.completion_date, "
            "completion_year = EXCLUDED.completion_year, "
            "completion_month = EXCLUDED.completion_month, "
            "building_area_sqm = EXCLUDED.building_area_sqm, "
            "room_count = EXCLUDED.room_count, hall_count = EXCLUDED.hall_count, "
            "bathroom_count = EXCLUDED.bathroom_count, "
            "has_partition = EXCLUDED.has_partition, "
            "has_management = EXCLUDED.has_management, "
            "total_price_twd = EXCLUDED.total_price_twd, "
            "unit_price_twd_sqm = EXCLUDED.unit_price_twd_sqm, "
            "parking_type = EXCLUDED.parking_type, "
            "parking_area_sqm = EXCLUDED.parking_area_sqm, "
            "parking_price_twd = EXCLUDED.parking_price_twd, remarks = EXCLUDED.remarks, "
            "main_building_area_sqm = EXCLUDED.main_building_area_sqm, "
            "auxiliary_building_area_sqm = EXCLUDED.auxiliary_building_area_sqm, "
            "balcony_area_sqm = EXCLUDED.balcony_area_sqm, "
            "has_elevator = EXCLUDED.has_elevator, "
            "transfer_number = EXCLUDED.transfer_number, "
            "last_seen_run_id = EXCLUDED.last_seen_run_id, "
            "last_observed_at = EXCLUDED.last_observed_at, "
            "raw_record_sha256 = EXCLUDED.raw_record_sha256, "
            "updated_at = CURRENT_TIMESTAMP",
            (
                record.transaction_id,
                record.source_id,
                record.authority_partition,
                record.source_record_id,
                record.source_variant_id,
                record.municipality,
                record.district,
                record.transaction_target,
                record.address,
                record.transaction_date,
                record.transaction_count_summary,
                record.land_area_sqm,
                record.urban_land_use,
                record.non_urban_land_use,
                record.non_urban_land_designation,
                record.transferred_floor,
                record.total_floors,
                record.building_type,
                record.main_use,
                record.main_material,
                record.completion_date,
                record.completion_year,
                record.completion_month,
                record.building_area_sqm,
                record.room_count,
                record.hall_count,
                record.bathroom_count,
                record.has_partition,
                record.has_management,
                record.total_price_twd,
                record.unit_price_twd_sqm,
                record.parking_type,
                record.parking_area_sqm,
                record.parking_price_twd,
                record.remarks,
                record.main_building_area_sqm,
                record.auxiliary_building_area_sqm,
                record.balcony_area_sqm,
                record.has_elevator,
                record.transfer_number,
                batch.run_id,
                batch.run_id,
                batch.artifact.fetched_at,
                batch.artifact.fetched_at,
                record.raw_record_sha256,
            ),
        )
        self.client.execute(
            "INSERT INTO external_data.real_estate_transaction_observations ("
            "run_id, transaction_id, source_id, authority_partition, "
            "source_record_id, source_variant_id, source_file, source_row_number, "
            "raw_record_sha256, raw_fields, observed_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSONB), ?) "
            "ON CONFLICT (run_id, transaction_id) DO NOTHING",
            (
                batch.run_id,
                record.transaction_id,
                record.source_id,
                record.authority_partition,
                record.source_record_id,
                record.source_variant_id,
                record.source_file,
                record.source_row_number,
                record.raw_record_sha256,
                json.dumps(
                    record.raw_fields,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                batch.artifact.fetched_at,
            ),
        )


def _validate_migration(sql: str) -> None:
    required = (
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_ingestion_runs",
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_transactions",
        "CREATE TABLE IF NOT EXISTS external_data.real_estate_transaction_observations",
        "license_id = 'government-open-data-license-v1'",
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        "raw_fields JSONB NOT NULL",
    )
    missing = [fragment for fragment in required if fragment not in sql]
    if missing:
        raise OfficialRealEstateSourceError(
            "official outcome migration is incomplete: " + ", ".join(missing)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Bounded ingestion of official Taiwan government real-estate sale outcomes")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sources", help="print the fixed approved-source registry")
    subparsers.add_parser("inventory", help="inspect landed official outcome rows")
    backfill = subparsers.add_parser(
        "backfill",
        help="download, verify, parse and atomically upsert one official snapshot",
    )
    backfill.add_argument("--source", required=True, choices=tuple(sorted(SOURCES)))
    backfill.add_argument("--expected-dataset-id", required=True)
    backfill.add_argument(
        "--expected-license-id",
        required=True,
        choices=(GOVERNMENT_OPEN_DATA_LICENSE_V1,),
    )
    backfill.add_argument("--expected-sha256")
    backfill.add_argument(
        "--max-download-bytes",
        type=int,
        default=MAX_DOWNLOAD_BYTES,
    )
    backfill.add_argument(
        "--max-expanded-bytes",
        type=int,
        default=MAX_EXPANDED_BYTES,
    )
    backfill.add_argument("--max-rows", type=int, default=MAX_ROWS)
    backfill.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client: OutcomeDatabase | None = None,
    downloader: OfficialRealEstateDownloader | None = None,
) -> int:
    args = _parser().parse_args(argv)
    owned_client = None
    try:
        if args.command == "sources":
            print(
                json.dumps(
                    {
                        "parser_version": PARSER_VERSION,
                        "sources": {
                            key: {
                                "source_id": source.source_id,
                                "dataset_id": source.dataset_id,
                                "source_url": source.source_url,
                                "metadata_url": source.metadata_url,
                                "publisher": source.publisher,
                                "license_id": source.license_id,
                                "media_type": source.media_type,
                            }
                            for key, source in sorted(SOURCES.items())
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        if client is None:
            database_url = require_production_database_url(os.getenv("ODAY_DATABASE_URL", ""))
            from shared.infrastructure.persistence.postgresql import PostgresEngine

            owned_client = PostgresEngine(
                database_url,
                bootstrap=False,
                validate_schema=False,
            )
            client = owned_client
        store = OfficialRealEstateOutcomeStore(client)
        if args.command == "inventory":
            print(json.dumps(store.inventory(), default=str, sort_keys=True))
            return 0
        source = SOURCES[args.source]
        source.validate_binding(
            dataset_id=args.expected_dataset_id,
            license_id=args.expected_license_id,
        )
        artifact = (downloader or OfficialRealEstateDownloader()).download(
            source,
            max_bytes=args.max_download_bytes,
            timeout_seconds=args.timeout_seconds,
            expected_sha256=args.expected_sha256,
        )
        batch = parse_official_real_estate(
            artifact,
            max_rows=args.max_rows,
            max_expanded_bytes=args.max_expanded_bytes,
        )
        print(
            json.dumps(
                store.upsert(batch),
                ensure_ascii=False,
                default=str,
                sort_keys=True,
            )
        )
        return 0
    except (
        ModelTrainingConfigurationError,
        OfficialRealEstateSourceError,
        OSError,
        ValueError,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    finally:
        if owned_client is not None:
            owned_client.close()


def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items()}


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MIGRATION_PATH",
    "OfficialRealEstateOutcomeStore",
    "main",
]
