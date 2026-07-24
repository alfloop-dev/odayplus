from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from apps.data_platform.contracts import SourceKind
from apps.data_platform.serialization import aggregate_checksum
from apps.data_platform.store import PsycopgCanonicalStore, _PostgresLookup


def _schema() -> str:
    return (
        Path(__file__).parents[1]
        .joinpath("sql", "control_schema.sql")
        .read_text(encoding="utf-8")
    )


def test_legacy_ai_outputs_never_claim_registered_model_identity() -> None:
    schema = _schema()
    assert schema.count("legacy_external_model_output") >= 4
    assert "source_model_version IS NULL AND source_model_run_id IS NULL" in schema
    assert "model_reference" not in schema
    assert "feature_snapshot JSONB NOT NULL" in schema
    assert "segment_labels JSONB NOT NULL" in schema


def test_partial_geography_and_machine_event_evidence_are_durable() -> None:
    schema = _schema()
    assert "CREATE TABLE IF NOT EXISTS {{control_schema}}.place_geography" in schema
    assert "raw_address TEXT," in schema
    assert "CHECK ((latitude IS NULL) = (longitude IS NULL))" in schema
    assert (
        "CREATE TABLE IF NOT EXISTS "
        "{{control_schema}}.machine_status_event_evidence" in schema
    )
    assert "device_log_minimized_v1" in schema


def test_quarantine_and_lineage_are_run_and_snapshot_scoped() -> None:
    schema = _schema()
    assert "CREATE TABLE IF NOT EXISTS {{control_schema}}.quarantined_records" in schema
    assert "source_snapshot_id UUID PRIMARY KEY" in schema
    assert "reason_code TEXT NOT NULL" in schema
    assert "canonical_lineage" in schema
    assert "content_sha256 TEXT NOT NULL" in schema


def test_owner_mapping_schema_has_every_governed_live_enum() -> None:
    contract = json.loads(
        Path(__file__).parents[1]
        .joinpath("status_mapping.schema.json")
        .read_text(encoding="utf-8")
    )
    properties = contract["properties"]["mappings"]["properties"]
    assert set(properties) == {
        "transaction",
        "trade",
        "merchant_operation",
        "place_operation",
        "place_type",
        "device_connection",
    }


class _CaptureConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...]):
        assert sql.count("%s") == len(params)
        self.statements.append((sql, params))
        return self


class _Result:
    def __init__(
        self,
        *,
        one: tuple[object, ...] | None = None,
        many: list[tuple[object, ...]] | None = None,
    ) -> None:
        self._one = one
        self._many = many or []

    def fetchone(self) -> tuple[object, ...] | None:
        return self._one

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._many


class _ReconcileConnection:
    def __init__(self, *, raw_relation_exists: bool) -> None:
        self.raw_relation_exists = raw_relation_exists
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> _Result:
        self.statements.append((sql, params))
        if "to_regclass" in sql:
            relation = params[0] if self.raw_relation_exists else None
            return _Result(one=(relation,))
        return _Result()


class _LookupConnection:
    def __init__(self) -> None:
        self.execute_count = 0

    def execute(self, _sql: str, _params: tuple[object, ...]) -> _Result:
        self.execute_count += 1
        return _Result(
            one=(
                UUID("00000000-0000-4000-8000-000000000001"),
                UUID("00000000-0000-4000-8000-000000000002"),
                "fongniao_merchant-1",
            )
        )


class _AuthorityConnection:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self.statements: list[str] = []

    def execute(self, sql: str, _params: tuple[object, ...]) -> _Result:
        self.statements.append(sql)
        if "RETURNING source_kind, authority_rank" in sql:
            return _Result(one=("orders", 1) if self.accepted else None)
        if "SELECT source_kind, authority_rank" in sql:
            return _Result(one=("orders", 1))
        return _Result()


def _store() -> PsycopgCanonicalStore:
    store = object.__new__(PsycopgCanonicalStore)
    store._config = SimpleNamespace(
        control_schema="data_plane",
        raw_schema="fongniao_raw",
    )
    return store


def _envelope(envelope_factory, kind: SourceKind):
    return envelope_factory(
        kind,
        {
            "_id": f"{kind.value}-1",
            "createdAt": "2026-07-23T00:00:00Z",
        },
    )


def test_ai_and_domain_upsert_bindings_match_control_schema(
    envelope_factory,
) -> None:
    store = _store()
    connection = _CaptureConnection()
    forecast_envelope = _envelope(envelope_factory, SourceKind.AI_REVENUE_STATS)
    store._upsert_forecast_input(
        connection,
        forecast_envelope,
        SimpleNamespace(
            source_id="forecast-1",
            tenant_id=UUID("00000000-0000-4000-8000-000000000001"),
            store_id=UUID("00000000-0000-4000-8000-000000000002"),
            forecast_date=forecast_envelope.observed_at,
            predicted_value=Decimal("10.25"),
            observed_at=forecast_envelope.observed_at,
        ),
    )
    learning_envelope = _envelope(
        envelope_factory, SourceKind.AI_CONSUMER_KMEANS_V1
    )
    store._upsert_learning_import(
        connection,
        learning_envelope,
        SimpleNamespace(
            source_id="learning-1",
            tenant_id=UUID("00000000-0000-4000-8000-000000000001"),
            run_date=learning_envelope.observed_at,
            source_account_ref_hash="a" * 64,
            feature_snapshot={"avgTicket": Decimal("1.2345")},
            segment_id="segment-1",
            segment_labels=("one", "two"),
            observed_at=learning_envelope.observed_at,
        ),
    )
    domain_envelope = _envelope(envelope_factory, SourceKind.CAMPAIGN)
    store._upsert_domain_input(
        connection,
        domain_envelope,
        SimpleNamespace(
            source_id="campaign-1",
            input_kind="campaign",
            tenant_id=UUID("00000000-0000-4000-8000-000000000001"),
            store_id=None,
            effective_at=domain_envelope.observed_at,
            input_payload={"offerName": "Offer"},
        ),
    )
    assert len(connection.statements) == 3


def test_place_upsert_casts_nullable_geography_parameters() -> None:
    connection = _CaptureConnection()
    PsycopgCanonicalStore._upsert_place(
        connection,
        SimpleNamespace(
            address_id=UUID("00000000-0000-4000-8000-000000000010"),
            raw_address="台北市測試路 1 號",
            latitude=None,
            longitude=None,
            store_id=UUID("00000000-0000-4000-8000-000000000011"),
            tenant_id=UUID("00000000-0000-4000-8000-000000000001"),
            brand_id=UUID("00000000-0000-4000-8000-000000000002"),
            source_id="place-1",
            store_name="測試門市",
            store_status="open",
            store_format_code="source_type_1",
            effective_from="2026-07-23T00:00:00Z",
        ),
    )
    geography_sql = connection.statements[0][0]
    assert geography_sql.count("::double precision") == 6
    assert "ST_MakePoint" in geography_sql


def test_empty_partition_reconciles_when_dlt_did_not_create_a_raw_table() -> None:
    store = _store()
    connection = _ReconcileConnection(raw_relation_exists=False)
    store._connect = lambda: connection

    result = store.reconcile(
        "00000000-0000-4000-8000-000000000099",
        SourceKind.DEVICE_DAILY_STATISTICS,
        0,
        aggregate_checksum([]),
        aggregate_checksum([]),
    )

    assert result.reconciled
    assert result.raw_count == 0
    assert all(
        "raw_device_daily_statistics" not in sql or "to_regclass" in sql
        for sql, _ in connection.statements
    )


def test_non_empty_partition_requires_a_durable_raw_table() -> None:
    store = _store()
    connection = _ReconcileConnection(raw_relation_exists=False)
    store._connect = lambda: connection

    try:
        store.reconcile(
            "00000000-0000-4000-8000-000000000099",
            SourceKind.ORDERS,
            1,
            "source",
            "valid",
        )
    except RuntimeError as exc:
        assert "fongniao_raw.raw_orders" in str(exc)
    else:
        raise AssertionError("A missing raw table must fail a non-empty run")


def test_place_lookup_is_cached_within_a_projection_batch() -> None:
    connection = _LookupConnection()
    lookup = _PostgresLookup(connection)

    first = lookup.require_place("place-1")
    second = lookup.require_place("place-1")

    assert first is second
    assert connection.execute_count == 1


def test_transaction_authority_check_and_upsert_share_one_round_trip(
    envelope_factory,
) -> None:
    store = _store()
    connection = _AuthorityConnection(accepted=True)
    envelope = _envelope(envelope_factory, SourceKind.ORDERS)
    projection = SimpleNamespace(
        transaction_id=UUID("00000000-0000-4000-8000-000000000020"),
        source_id="order-1",
        store_id=UUID("00000000-0000-4000-8000-000000000011"),
        event_time=envelope.observed_at,
        observation_time=envelope.observed_at,
        payment_time=envelope.observed_at,
        gross_amount=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        net_amount=Decimal("100.00"),
        currency="TWD",
        payment_method="card",
        transaction_status="succeeded",
        ingested_at=envelope.observed_at,
    )

    store._upsert_transaction(connection, envelope, projection)

    assert len(connection.statements) == 2
    assert "RETURNING source_kind, authority_rank" in connection.statements[0]
    assert "INSERT INTO core.transactions" in connection.statements[1]
