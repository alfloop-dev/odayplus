from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from apps.data_platform.config import DataPlaneConfig
from apps.data_platform.contracts import (
    ProjectionBatchResult,
    QuarantineReason,
    ReconciliationResult,
    SourceEnvelope,
    SourceKind,
)
from apps.data_platform.identifiers import (
    brand_id_for_merchant,
    machine_id_for_device,
    store_id_for_place,
    tenant_id_for_merchant,
)
from apps.data_platform.mapping import (
    MappingLookup,
    MachineIdentity,
    MerchantIdentity,
    MissingMappingError,
    SourceContractError,
    StoreIdentity,
    project_merchant,
    project_device,
    project_daily_statistic,
    project_domain_input,
    project_forecast_input,
    project_learning_import,
    project_machine_status_event,
    project_place,
    project_transaction,
)
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.status_mapping import optional_status_contract


class CanonicalStore(Protocol):
    def install(self) -> None: ...

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
    ) -> None: ...

    def apply_batch(
        self,
        source_kind: SourceKind,
        envelopes: Sequence[SourceEnvelope],
        *,
        partition_key: str,
    ) -> ProjectionBatchResult: ...

    def get_checkpoint(self, source_kind: SourceKind, partition_key: str) -> str | None: ...

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None: ...

    def reconcile(
        self,
        run_id: str,
        source_kind: SourceKind,
        source_count: int,
        source_checksum: str,
        valid_checksum: str,
    ) -> ReconciliationResult: ...

    def complete_run(
        self,
        run_id: str,
        *,
        final_cursor: str | None,
        processed_count: int,
        reconciliation: ReconciliationResult,
        finished_at: datetime,
    ) -> None: ...

    def fail_run(
        self,
        run_id: str,
        *,
        source_kind: SourceKind,
        partition_key: str,
        source_snapshot_ids: Sequence[str],
        error: BaseException,
        retryable: bool,
    ) -> None: ...


class _PostgresLookup(MappingLookup):
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._merchants: dict[str, MerchantIdentity] = {}
        self._places: dict[str, StoreIdentity] = {}
        self._devices: dict[str, MachineIdentity] = {}

    def require_merchant(self, source_merchant_id: str) -> MerchantIdentity:
        cached = self._merchants.get(source_merchant_id)
        if cached is not None:
            return cached
        tenant_id = tenant_id_for_merchant(source_merchant_id)
        brand_id = brand_id_for_merchant(source_merchant_id)
        row = self._connection.execute(
            """
            SELECT t.tenant_id, b.brand_id
            FROM core.tenants AS t
            JOIN core.brands AS b
              ON b.tenant_id = t.tenant_id
            WHERE t.tenant_id = %s AND b.brand_id = %s
            """,
            (tenant_id, brand_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_MERCHANT_MAPPING,
                f"Missing merchant mapping for source merchant {source_merchant_id}"
            )
        identity = MerchantIdentity(
            source_merchant_id,
            UUID(str(row[0])),
            UUID(str(row[1])),
        )
        self._merchants[source_merchant_id] = identity
        return identity

    def require_place(self, source_place_id: str) -> StoreIdentity:
        cached = self._places.get(source_place_id)
        if cached is not None:
            return cached
        store_id = store_id_for_place(source_place_id)
        row = self._connection.execute(
            """
            SELECT s.tenant_id, s.brand_id, b.brand_code
            FROM core.stores AS s
            JOIN core.brands AS b
              ON b.brand_id = s.brand_id AND b.tenant_id = s.tenant_id
            WHERE s.store_id = %s AND s.source_store_id = %s
            """,
            (store_id, source_place_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_PLACE_MAPPING,
                f"Missing place mapping for source place {source_place_id}"
            )
        brand_code = str(row[2])
        prefix = "fongniao_"
        if not brand_code.startswith(prefix):
            raise MissingMappingError(
                QuarantineReason.TENANT_OWNERSHIP_MISMATCH,
                f"Place {source_place_id} is not owned by a fongniao merchant tenant"
            )
        identity = StoreIdentity(
            source_place_id=source_place_id,
            source_merchant_id=brand_code.removeprefix(prefix),
            tenant_id=UUID(str(row[0])),
            brand_id=UUID(str(row[1])),
            store_id=store_id,
        )
        self._places[source_place_id] = identity
        return identity

    def require_device(self, source_device_id: str) -> MachineIdentity:
        cached = self._devices.get(source_device_id)
        if cached is not None:
            return cached
        machine_id = machine_id_for_device(source_device_id)
        row = self._connection.execute(
            """
            SELECT s.tenant_id, m.store_id
            FROM core.machines AS m
            JOIN core.stores AS s ON s.store_id = m.store_id
            WHERE m.machine_id = %s AND m.source_machine_id = %s
            """,
            (machine_id, source_device_id),
        ).fetchone()
        if row is None:
            raise MissingMappingError(
                QuarantineReason.MISSING_DEVICE_MAPPING,
                f"Missing device mapping for source device {source_device_id}",
            )
        identity = MachineIdentity(
            source_device_id=source_device_id,
            tenant_id=UUID(str(row[0])),
            store_id=UUID(str(row[1])),
            machine_id=machine_id,
        )
        self._devices[source_device_id] = identity
        return identity


class PsycopgCanonicalStore:
    """Transactional canonical writer and lineage/checkpoint authority."""

    def __init__(
        self,
        config: DataPlaneConfig,
        *,
        connection_factory: Any | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._connect = connection_factory or self._default_connection_factory()
        self._status_contract = optional_status_contract(config.status_mapping_path)

    def _default_connection_factory(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for canonical persistence") from exc
        return lambda: psycopg.connect(self._config.postgres_dsn)

    @property
    def _schema(self) -> str:
        return self._config.control_schema

    def install(self) -> None:
        path = Path(__file__).with_name("sql") / "control_schema.sql"
        sql = path.read_text(encoding="utf-8").replace(
            "{{control_schema}}", self._schema
        )
        with self._connect() as connection:
            connection.execute(sql)

    def begin_run(
        self,
        run_id: str,
        source_kind: SourceKind,
        partition_key: str,
        resumed_from: str | None,
        started_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.ingestion_runs (
                    run_id, source_database, source_kind, partition_key, status,
                    resumed_from, started_at
                ) VALUES (%s, 'fongniao_prod', %s, %s, 'RUNNING', %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status = 'RUNNING',
                    error_type = NULL,
                    error_message = NULL
                """,
                (run_id, source_kind.value, partition_key, resumed_from, started_at),
            )

    def apply_batch(
        self,
        source_kind: SourceKind,
        envelopes: Sequence[SourceEnvelope],
        *,
        partition_key: str,
    ) -> ProjectionBatchResult:
        if not envelopes:
            return ProjectionBatchResult((), {})
        valid: list[str] = []
        reason_counts: dict[str, int] = {}
        with self._connect() as connection:
            lookup = _PostgresLookup(connection)
            for envelope in envelopes:
                try:
                    with connection.transaction():
                        self._project_one(connection, lookup, source_kind, envelope)
                        self._resolve_quarantine(connection, envelope)
                    valid.append(
                        f"{envelope.source_snapshot_id}:{envelope.content_sha256}"
                    )
                except SourceContractError as exc:
                    reason = exc.reason_code.value
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                    with connection.transaction():
                        self._quarantine(
                            connection,
                            envelope,
                            partition_key=partition_key,
                            reason_code=reason,
                            reason_detail=str(exc),
                        )
        return ProjectionBatchResult(tuple(valid), reason_counts)

    def _project_one(
        self,
        connection: Any,
        lookup: _PostgresLookup,
        source_kind: SourceKind,
        envelope: SourceEnvelope,
    ) -> None:
        if source_kind is SourceKind.MERCHANT:
            projection = project_merchant(envelope, self._status_contract)
            self._upsert_merchant(connection, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.tenants",
                projection.tenant_id,
            )
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.brands",
                projection.brand_id,
            )
        elif source_kind is SourceKind.PLACE:
            projection = project_place(
                envelope, lookup, self._status_contract
            )
            self._upsert_place(connection, projection)
            self._upsert_place_geography(connection, envelope, projection)
            if projection.address_id is not None:
                self._lineage(
                    connection,
                    envelope,
                    projection.tenant_id,
                    "core.address_locations",
                    projection.address_id,
                )
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.stores",
                projection.store_id,
            )
        elif source_kind is SourceKind.DEVICE:
            projection = project_device(envelope, lookup)
            self._upsert_device(connection, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.machines",
                projection.machine_id,
            )
        elif source_kind in {
            SourceKind.ORDERS,
            SourceKind.TRANSACTION,
            SourceKind.TRADE,
        }:
            projection = project_transaction(
                envelope, lookup, self._status_contract
            )
            self._upsert_transaction(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.transactions",
                projection.transaction_id,
            )
        elif source_kind is SourceKind.DEVICE_DAILY_STATISTICS:
            projection = project_daily_statistic(envelope, lookup)
            self._upsert_daily_statistic(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.store_daily_facts",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.AI_REVENUE_STATS:
            projection = project_forecast_input(envelope, lookup)
            self._upsert_forecast_input(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.forecast_inputs",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.AI_CONSUMER_KMEANS_V1:
            projection = project_learning_import(envelope, lookup)
            self._upsert_learning_import(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.learning_import_lineage",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.DEVICE_LOG:
            projection = project_machine_status_event(
                envelope, lookup, self._status_contract
            )
            self._upsert_machine_status_event(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                "core.machine_status_events",
                projection.status_event_id,
            )
        elif source_kind in {
            SourceKind.CAMPAIGN,
            SourceKind.PRODUCT,
            SourceKind.PRODUCTS,
            SourceKind.PROMOTIONS,
        }:
            projection = project_domain_input(envelope, lookup)
            self._upsert_domain_input(connection, envelope, projection)
            self._lineage(
                connection,
                envelope,
                projection.tenant_id,
                f"{self._schema}.domain_inputs",
                UUID(envelope.source_snapshot_id),
            )
        elif source_kind is SourceKind.MEMBER:
            raise SourceContractError(
                QuarantineReason.SENSITIVE_MEMBER_EXCLUDED,
                "Member records are raw-minimized and excluded from canonical projection",
            )
        else:  # pragma: no cover - exhaustive StrEnum guard
            raise ValueError(f"Unsupported source kind: {source_kind}")

    def _quarantine(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        *,
        partition_key: str,
        reason_code: str,
        reason_detail: str,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.quarantined_records (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, partition_key, reason_code, reason_detail, retryable
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                source_kind = EXCLUDED.source_kind,
                source_id = EXCLUDED.source_id,
                content_sha256 = EXCLUDED.content_sha256,
                partition_key = EXCLUDED.partition_key,
                reason_code = EXCLUDED.reason_code,
                reason_detail = EXCLUDED.reason_detail,
                quarantined_at = CURRENT_TIMESTAMP,
                resolved_at = NULL
            """,
            (
                envelope.source_snapshot_id,
                envelope.source_kind.value,
                envelope.source_id,
                envelope.content_sha256,
                envelope.run_id,
                partition_key,
                reason_code,
                reason_detail,
            ),
        )

    def _resolve_quarantine(
        self, connection: Any, envelope: SourceEnvelope
    ) -> None:
        connection.execute(
            f"""
            UPDATE {self._schema}.quarantined_records
            SET resolved_at = CURRENT_TIMESTAMP
            WHERE source_snapshot_id = %s AND resolved_at IS NULL
            """,
            (envelope.source_snapshot_id,),
        )

    @staticmethod
    def _upsert_merchant(connection: Any, projection: Any) -> None:
        connection.execute(
            """
            INSERT INTO core.tenants (
                tenant_id, tenant_name, status, created_at, updated_at
            ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO UPDATE SET
                tenant_name = EXCLUDED.tenant_name,
                status = EXCLUDED.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (projection.tenant_id, projection.tenant_name, projection.tenant_status),
        )
        connection.execute(
            """
            INSERT INTO core.brands (
                brand_id, tenant_id, brand_code, brand_name, brand_type,
                brand_capture_group, status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, 'owned', 'fongniao_prod',
                %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (brand_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                brand_code = EXCLUDED.brand_code,
                brand_name = EXCLUDED.brand_name,
                brand_type = 'owned',
                brand_capture_group = 'fongniao_prod',
                status = EXCLUDED.status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.brand_id,
                projection.tenant_id,
                projection.brand_code,
                projection.brand_name,
                projection.brand_status,
            ),
        )

    @staticmethod
    def _upsert_place(connection: Any, projection: Any) -> None:
        if projection.address_id is not None:
            connection.execute(
                """
                INSERT INTO core.address_locations (
                    address_id, raw_address, normalized_address,
                    latitude, longitude, geom, geocode_precision,
                    geocode_confidence, manual_override_flag, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    CASE
                        WHEN %s::double precision IS NULL
                          OR %s::double precision IS NULL THEN NULL
                        ELSE ST_SetSRID(
                            ST_MakePoint(
                                %s::double precision,
                                %s::double precision
                            ),
                            4326
                        )
                    END,
                    'source',
                    CASE
                        WHEN %s::double precision IS NULL
                          OR %s::double precision IS NULL THEN NULL
                        ELSE 1.00
                    END,
                    FALSE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                ON CONFLICT (address_id) DO UPDATE SET
                    raw_address = EXCLUDED.raw_address,
                    normalized_address = EXCLUDED.normalized_address,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    geom = EXCLUDED.geom,
                    geocode_precision = 'source',
                    geocode_confidence = EXCLUDED.geocode_confidence,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    projection.address_id,
                    projection.raw_address,
                    projection.raw_address,
                    projection.latitude,
                    projection.longitude,
                    projection.longitude,
                    projection.latitude,
                    projection.longitude,
                    projection.latitude,
                    projection.longitude,
                    projection.latitude,
                ),
            )
        connection.execute(
            """
            INSERT INTO core.stores (
                store_id, tenant_id, brand_id, source_store_id, store_name,
                store_status, ownership_type, store_format_code, address_id,
                effective_from, effective_to, is_current, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, 'owned', %s, %s, %s,
                '9999-12-31 23:59:59+00', TRUE,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (store_id) DO UPDATE SET
                tenant_id = EXCLUDED.tenant_id,
                brand_id = EXCLUDED.brand_id,
                source_store_id = EXCLUDED.source_store_id,
                store_name = EXCLUDED.store_name,
                store_status = EXCLUDED.store_status,
                ownership_type = 'owned',
                store_format_code = EXCLUDED.store_format_code,
                address_id = EXCLUDED.address_id,
                is_current = TRUE,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.store_id,
                projection.tenant_id,
                projection.brand_id,
                projection.source_id,
                projection.store_name,
                projection.store_status,
                projection.store_format_code,
                projection.address_id,
                projection.effective_from,
            ),
        )

    def _upsert_place_geography(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.place_geography (
                source_snapshot_id, source_id, tenant_id, store_id,
                raw_address, latitude, longitude, run_id, observed_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                raw_address = EXCLUDED.raw_address,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                run_id = EXCLUDED.run_id,
                observed_at = EXCLUDED.observed_at
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.raw_address,
                projection.latitude,
                projection.longitude,
                envelope.run_id,
                envelope.observed_at,
            ),
        )

    def _upsert_transaction(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        projection: Any,
    ) -> None:
        authority_rank = {
            SourceKind.ORDERS: 1,
            SourceKind.TRANSACTION: 2,
            SourceKind.TRADE: 3,
        }[envelope.source_kind]
        authority = connection.execute(
            f"""
            INSERT INTO {self._schema}.transaction_authority (
                transaction_id, source_kind, authority_rank, source_snapshot_id
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (transaction_id) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                authority_rank = EXCLUDED.authority_rank,
                source_snapshot_id = EXCLUDED.source_snapshot_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE EXCLUDED.authority_rank <=
                  {self._schema}.transaction_authority.authority_rank
            RETURNING source_kind, authority_rank
            """,
            (
                projection.transaction_id,
                envelope.source_kind.value,
                authority_rank,
                envelope.source_snapshot_id,
            ),
        ).fetchone()
        if authority is None:
            authority = connection.execute(
                f"""
                SELECT source_kind, authority_rank
                FROM {self._schema}.transaction_authority
                WHERE transaction_id = %s
                """,
                (projection.transaction_id,),
            ).fetchone()
            raise SourceContractError(
                QuarantineReason.SOURCE_SUPERSEDED,
                (
                    f"{envelope.source_kind.value} transaction is superseded by "
                    f"authoritative {authority[0] if authority else 'unknown'}"
                ),
            )
        connection.execute(
            """
            INSERT INTO core.transactions (
                transaction_id, source_transaction_id, store_id, machine_id,
                member_id, event_time, observation_time, payment_time,
                gross_amount, discount_amount, net_amount, currency,
                payment_method, transaction_status, refund_of_transaction_id,
                price_schedule_id, promotion_id, source_system, ingested_at
            ) VALUES (
                %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, NULL, NULL, NULL, 'fongniao_prod', %s
            )
            ON CONFLICT (transaction_id) DO UPDATE SET
                source_transaction_id = EXCLUDED.source_transaction_id,
                store_id = EXCLUDED.store_id,
                event_time = EXCLUDED.event_time,
                observation_time = EXCLUDED.observation_time,
                payment_time = EXCLUDED.payment_time,
                gross_amount = EXCLUDED.gross_amount,
                discount_amount = EXCLUDED.discount_amount,
                net_amount = EXCLUDED.net_amount,
                currency = EXCLUDED.currency,
                payment_method = EXCLUDED.payment_method,
                transaction_status = EXCLUDED.transaction_status,
                source_system = 'fongniao_prod',
                ingested_at = EXCLUDED.ingested_at
            """,
            (
                projection.transaction_id,
                projection.source_id,
                projection.store_id,
                projection.event_time,
                projection.observation_time,
                projection.payment_time,
                projection.gross_amount,
                projection.discount_amount,
                projection.net_amount,
                projection.currency,
                projection.payment_method,
                projection.transaction_status,
                projection.ingested_at,
            ),
        )
    @staticmethod
    def _upsert_device(connection: Any, projection: Any) -> None:
        connection.execute(
            """
            INSERT INTO core.machines (
                machine_id, store_id, source_machine_id, machine_serial_no,
                equipment_brand_id, machine_family, machine_type, capacity_kg,
                capacity_band, installed_on, removed_on, machine_status,
                effective_from, effective_to, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, 'other', %s, NULL, 'medium',
                NULL, NULL, %s, %s, '9999-12-31 23:59:59+00',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            ON CONFLICT (machine_id) DO UPDATE SET
                store_id = EXCLUDED.store_id,
                source_machine_id = EXCLUDED.source_machine_id,
                machine_serial_no = EXCLUDED.machine_serial_no,
                equipment_brand_id = EXCLUDED.equipment_brand_id,
                machine_type = EXCLUDED.machine_type,
                machine_status = EXCLUDED.machine_status,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                projection.machine_id,
                projection.store_id,
                projection.source_id,
                projection.serial_number,
                projection.product_id,
                projection.machine_type,
                projection.machine_status,
                projection.effective_from,
            ),
        )

    def _upsert_daily_statistic(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.store_daily_facts (
                source_snapshot_id, source_id, tenant_id, store_id, machine_id,
                period_start, period_end, gross_amount, transaction_count,
                gateway, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                gross_amount = EXCLUDED.gross_amount,
                transaction_count = EXCLUDED.transaction_count,
                gateway = EXCLUDED.gateway
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.machine_id,
                projection.period_start,
                projection.period_end,
                projection.gross_amount,
                projection.transaction_count,
                projection.gateway,
                envelope.run_id,
            ),
        )

    def _upsert_forecast_input(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.forecast_inputs (
                source_snapshot_id, source_id, tenant_id, store_id,
                forecast_date, predicted_value, output_class,
                source_model_version, source_model_run_id,
                source_freshness_at, observed_at, run_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                'legacy_external_model_output', NULL, NULL, %s, %s, %s
            )
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                predicted_value = EXCLUDED.predicted_value,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.forecast_date,
                projection.predicted_value,
                envelope.source_updated_at,
                projection.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_domain_input(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.domain_inputs (
                source_snapshot_id, source_id, input_kind, tenant_id,
                store_id, effective_at, input_payload, source_freshness_at,
                observed_at, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                tenant_id = EXCLUDED.tenant_id,
                store_id = EXCLUDED.store_id,
                effective_at = EXCLUDED.effective_at,
                input_payload = EXCLUDED.input_payload,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.input_kind,
                projection.tenant_id,
                projection.store_id,
                projection.effective_at,
                json.dumps(projection.input_payload, sort_keys=True),
                envelope.source_updated_at,
                envelope.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_learning_import(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.learning_import_lineage (
                source_snapshot_id, source_id, tenant_id, run_date,
                source_account_ref_hash, feature_snapshot, segment_id,
                segment_labels, output_class, source_model_version,
                source_model_run_id, source_freshness_at, observed_at, run_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb,
                'legacy_external_model_output', NULL, NULL, %s, %s, %s
            )
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                feature_snapshot = EXCLUDED.feature_snapshot,
                segment_id = EXCLUDED.segment_id,
                segment_labels = EXCLUDED.segment_labels,
                source_freshness_at = EXCLUDED.source_freshness_at,
                observed_at = EXCLUDED.observed_at
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.run_date,
                projection.source_account_ref_hash,
                json.dumps(
                    {
                        key: str(value)
                        for key, value in projection.feature_snapshot.items()
                    },
                    sort_keys=True,
                ),
                projection.segment_id,
                json.dumps(list(projection.segment_labels)),
                envelope.source_updated_at,
                projection.observed_at,
                envelope.run_id,
            ),
        )

    def _upsert_machine_status_event(
        self, connection: Any, envelope: SourceEnvelope, projection: Any
    ) -> None:
        connection.execute(
            """
            INSERT INTO core.machine_status_events (
                status_event_id, store_id, machine_id, event_time,
                status_type, severity, error_code, resolved_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (status_event_id) DO UPDATE SET
                store_id = EXCLUDED.store_id,
                machine_id = EXCLUDED.machine_id,
                event_time = EXCLUDED.event_time,
                status_type = EXCLUDED.status_type,
                severity = EXCLUDED.severity,
                error_code = EXCLUDED.error_code
            """,
            (
                projection.status_event_id,
                projection.store_id,
                projection.machine_id,
                projection.event_time,
                projection.status_type,
                projection.severity,
                projection.error_code,
            ),
        )
        connection.execute(
            f"""
            INSERT INTO {self._schema}.machine_status_event_evidence (
                source_snapshot_id, source_id, tenant_id, store_id,
                machine_id, status_event_id, content_sha256,
                observation_time, source_freshness_at, run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id) DO UPDATE SET
                content_sha256 = EXCLUDED.content_sha256,
                observation_time = EXCLUDED.observation_time,
                source_freshness_at = EXCLUDED.source_freshness_at,
                run_id = EXCLUDED.run_id
            """,
            (
                envelope.source_snapshot_id,
                projection.source_id,
                projection.tenant_id,
                projection.store_id,
                projection.machine_id,
                projection.status_event_id,
                envelope.content_sha256,
                projection.observation_time,
                envelope.source_updated_at,
                envelope.run_id,
            ),
        )

    def _lineage(
        self,
        connection: Any,
        envelope: SourceEnvelope,
        tenant_id: UUID,
        canonical_table: str,
        canonical_id: UUID,
    ) -> None:
        connection.execute(
            f"""
            INSERT INTO {self._schema}.canonical_lineage (
                source_snapshot_id, source_kind, source_id, content_sha256,
                run_id, tenant_id, canonical_table, canonical_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_snapshot_id, canonical_table, canonical_id)
            DO UPDATE SET
                run_id = EXCLUDED.run_id,
                tenant_id = EXCLUDED.tenant_id,
                content_sha256 = EXCLUDED.content_sha256,
                projected_at = CURRENT_TIMESTAMP
            """,
            (
                envelope.source_snapshot_id,
                envelope.source_kind.value,
                envelope.source_id,
                envelope.content_sha256,
                envelope.run_id,
                tenant_id,
                canonical_table,
                canonical_id,
            ),
        )

    def get_checkpoint(self, source_kind: SourceKind, partition_key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT source_cursor
                FROM {self._schema}.checkpoints
                WHERE source_kind = %s AND partition_key = %s
                """,
                (source_kind.value, partition_key),
            ).fetchone()
        return None if row is None else str(row[0])

    def record_checkpoint(
        self,
        source_kind: SourceKind,
        partition_key: str,
        envelope: SourceEnvelope,
        processed_count: int,
    ) -> None:
        source_cursor = str(envelope.source_document.get("_id") or envelope.source_id)
        with self._connect() as connection:
            connection.execute(
                f"""
                INSERT INTO {self._schema}.checkpoints (
                    source_kind, partition_key, source_cursor, source_updated_at,
                    run_id, processed_count
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_kind, partition_key) DO UPDATE SET
                    source_cursor = EXCLUDED.source_cursor,
                    source_updated_at = EXCLUDED.source_updated_at,
                    run_id = EXCLUDED.run_id,
                    processed_count = EXCLUDED.processed_count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    source_kind.value,
                    partition_key,
                    source_cursor,
                    envelope.source_updated_at,
                    envelope.run_id,
                    processed_count,
                ),
            )

    def reconcile(
        self,
        run_id: str,
        source_kind: SourceKind,
        source_count: int,
        source_checksum: str,
        valid_checksum: str,
    ) -> ReconciliationResult:
        raw_table = f"raw_{source_kind.value}"
        with self._connect() as connection:
            raw_relation = f"{self._config.raw_schema}.{raw_table}"
            raw_relation_exists = connection.execute(
                "SELECT to_regclass(%s)",
                (raw_relation,),
            ).fetchone()
            if raw_relation_exists is None or raw_relation_exists[0] is None:
                if source_count:
                    raise RuntimeError(
                        f"Raw landing table {raw_relation} is missing for a non-empty run"
                    )
                raw_rows = []
            else:
                raw_rows = connection.execute(
                    f"""
                    SELECT source_snapshot_id::text, content_sha256
                    FROM {raw_relation}
                    WHERE run_id = %s
                    """,
                    (run_id,),
                ).fetchall()
            canonical_rows = connection.execute(
                f"""
                SELECT DISTINCT source_snapshot_id::text, content_sha256
                FROM {self._schema}.canonical_lineage
                WHERE run_id = %s AND source_kind = %s
                """,
                (run_id, source_kind.value),
            ).fetchall()
            quarantine_rows = connection.execute(
                f"""
                SELECT reason_code, COUNT(*)
                FROM {self._schema}.quarantined_records
                WHERE run_id = %s AND source_kind = %s AND resolved_at IS NULL
                GROUP BY reason_code
                """,
                (run_id, source_kind.value),
            ).fetchall()
        raw_checksum = aggregate_checksum([f"{row[0]}:{row[1]}" for row in raw_rows])
        canonical_checksum = aggregate_checksum(
            [f"{row[0]}:{row[1]}" for row in canonical_rows]
        )
        return ReconciliationResult(
            source_total=source_count,
            valid_loaded=len(canonical_rows),
            quarantined_count=sum(int(row[1]) for row in quarantine_rows),
            raw_count=len(raw_rows),
            canonical_count=len(canonical_rows),
            source_checksum=source_checksum,
            raw_checksum=raw_checksum,
            valid_checksum=valid_checksum,
            canonical_checksum=canonical_checksum,
            quarantine_reason_counts={
                str(row[0]): int(row[1]) for row in quarantine_rows
            },
        )

    def complete_run(
        self,
        run_id: str,
        *,
        final_cursor: str | None,
        processed_count: int,
        reconciliation: ReconciliationResult,
        finished_at: datetime,
    ) -> None:
        status = "SUCCEEDED" if reconciliation.reconciled else "RECONCILIATION_FAILED"
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE {self._schema}.ingestion_runs
                SET status = %s,
                    final_cursor = %s,
                    processed_count = %s,
                    valid_loaded = %s,
                    quarantined_count = %s,
                    source_checksum = %s,
                    raw_checksum = %s,
                    canonical_checksum = %s,
                    finished_at = %s
                WHERE run_id = %s
                """,
                (
                    status,
                    final_cursor,
                    processed_count,
                    reconciliation.valid_loaded,
                    reconciliation.quarantined_count,
                    reconciliation.source_checksum,
                    reconciliation.raw_checksum,
                    reconciliation.canonical_checksum,
                    finished_at,
                    run_id,
                ),
            )

    def fail_run(
        self,
        run_id: str,
        *,
        source_kind: SourceKind,
        partition_key: str,
        source_snapshot_ids: Sequence[str],
        error: BaseException,
        retryable: bool,
    ) -> None:
        with self._connect() as connection:
            with connection.transaction():
                connection.execute(
                    f"""
                    UPDATE {self._schema}.ingestion_runs
                    SET status = 'FAILED',
                        error_type = %s,
                        error_message = %s,
                        finished_at = %s
                    WHERE run_id = %s
                    """,
                    (type(error).__name__, str(error), datetime.now(UTC), run_id),
                )
                connection.execute(
                    f"""
                    INSERT INTO {self._schema}.projection_failures (
                        failure_id, run_id, source_kind, partition_key,
                        source_snapshot_ids, error_type, error_message, retryable
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        uuid4(),
                        run_id,
                        source_kind.value,
                        partition_key,
                        [UUID(value) for value in source_snapshot_ids],
                        type(error).__name__,
                        str(error),
                        retryable,
                    ),
                )
