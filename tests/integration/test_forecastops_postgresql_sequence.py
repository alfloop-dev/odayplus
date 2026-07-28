from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from modules.forecastops import ForecastInput, ForecastOpsService, StoreDayObservation
from shared.infrastructure.persistence import DurableForecastOpsRepository
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.postgresql import PostgresEngine

pytestmark = pytest.mark.skipif(
    not os.environ.get("INTAKE_TEST_DATABASE_URL"),
    reason="INTAKE_TEST_DATABASE_URL is not configured",
)


def _repository(database_url: str) -> tuple[PostgresEngine, DurableForecastOpsRepository]:
    engine = PostgresEngine(
        database_url,
        bootstrap=True,
        validate_schema=False,
        min_pool_size=1,
        max_pool_size=2,
    )
    return engine, DurableForecastOpsRepository(SqliteDocumentStore(engine))


def test_postgresql_multi_instance_forecast_sequence_is_atomic() -> None:
    database_url = os.environ["INTAKE_TEST_DATABASE_URL"]
    engine_a, repository_a = _repository(database_url)
    engine_b, repository_b = _repository(database_url)
    tenant_id = f"tenant-forecast-postgres-{uuid4()}"
    store_id = f"store-forecast-postgres-{uuid4()}"
    origin = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)
    start = origin.date() - timedelta(days=7)
    forecast_input = ForecastInput(
        tenant_id=tenant_id,
        store_id=store_id,
        observations=tuple(
            StoreDayObservation(
                store_id=store_id,
                business_date=start + timedelta(days=index),
                actual_revenue=100_000.0 + index * 1_000.0,
                source_snapshot_ids=(f"orders-postgres-{index}",),
            )
            for index in range(7)
        ),
        prediction_origin_time=origin,
    )
    barrier = Barrier(2)

    def forecast(repository: DurableForecastOpsRepository, run_id: str) -> int:
        barrier.wait()
        result = ForecastOpsService(repository=repository).forecast(
            [forecast_input],
            prediction_run_id=run_id,
            scored_at=origin,
        )
        return result.forecasts[0].forecast_version

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            versions = sorted(
                executor.map(
                    lambda args: forecast(*args),
                    (
                        (repository_a, "pred-run-postgres-a"),
                        (repository_b, "pred-run-postgres-b"),
                    ),
                )
            )

        assert versions == [1, 2]
        history = repository_a.history(tenant_id, store_id)
        assert [item.forecast_version for item in history] == [1, 2]
        assert len({item.forecast_output_id for item in history}) == 2
    finally:
        engine_a.execute(
            "DELETE FROM durable_documents WHERE collection LIKE ?",
            (f"%:{tenant_id}",),
        )
        engine_a.close()
        engine_b.close()
