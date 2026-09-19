from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from dagster import (
    AssetExecutionContext,
    AssetSelection,
    DailyPartitionsDefinition,
    DefaultSensorStatus,
    Definitions,
    RunRequest,
    ScheduleDefinition,
    SensorEvaluationContext,
    asset,
    define_asset_job,
    sensor,
)

from apps.data_platform.cdc import (
    CdcDrainResult,
    MongoChangeStreamFactory,
    PsycopgCdcStore,
    ScopedCdcAdapter,
    ScopedCdcProjector,
    cdc_policy,
)
from apps.data_platform.contracts import BackfillWindow, SourceKind
from apps.data_platform.pipeline import DataPlaneRunner
from apps.data_platform.selection import read_limit_for
from apps.data_platform.source import MongoSource
from apps.data_platform.store import PsycopgCanonicalStore
from apps.data_platform.config import DataPlaneConfig

daily_partitions = DailyPartitionsDefinition(start_date="2022-03-23", timezone="UTC")


def _window(partition_key: str) -> BackfillWindow:
    day = date.fromisoformat(partition_key)
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return BackfillWindow(start, start + timedelta(days=1), partition_key)


def _run(context: AssetExecutionContext, source_kind: SourceKind) -> dict[str, Any]:
    runner = DataPlaneRunner.from_env()
    configured = DataPlaneConfig.from_env().max_records_per_run
    summary = runner.run_partition(
        source_kind,
        _window(context.partition_key),
        limit=read_limit_for(source_kind, configured),
    )
    context.add_output_metadata(summary.as_dict())
    return summary.as_dict()


@asset(partitions_def=daily_partitions, group_name="fongniao_dimensions")
def merchant_dimension(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.MERCHANT)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_dimensions",
    deps=[merchant_dimension],
)
def place_dimension(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.PLACE)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_dimensions",
    deps=[place_dimension],
)
def device_dimension(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.DEVICE)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_operations",
    deps=[device_dimension],
)
def device_daily_statistics_fact(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.DEVICE_DAILY_STATISTICS)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_operations",
    deps=[place_dimension],
)
def authoritative_orders(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.ORDERS)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_transactions",
    deps=[place_dimension],
)
def mapped_transactions(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.TRANSACTION)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_transactions",
    deps=[place_dimension],
)
def bounded_trade(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.TRADE)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_operations",
    deps=[device_dimension],
)
def bounded_device_log(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.DEVICE_LOG)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_forecast",
    deps=[place_dimension],
)
def ai_revenue_forecast_inputs(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.AI_REVENUE_STATS)


@asset(partitions_def=daily_partitions, group_name="fongniao_commercial")
def campaign_inputs(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.CAMPAIGN)


@asset(partitions_def=daily_partitions, group_name="fongniao_commercial")
def product_inputs(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.PRODUCT)


@asset(partitions_def=daily_partitions, group_name="fongniao_commercial")
def products_inputs(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.PRODUCTS)


@asset(partitions_def=daily_partitions, group_name="fongniao_commercial")
def promotion_inputs(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.PROMOTIONS)


@asset(
    partitions_def=daily_partitions,
    group_name="fongniao_learning",
    deps=[merchant_dimension],
)
def consumer_segment_import_lineage(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.AI_CONSUMER_KMEANS_V1)


@asset(partitions_def=daily_partitions, group_name="fongniao_privacy")
def minimized_member_quarantine(context: AssetExecutionContext) -> dict[str, Any]:
    return _run(context, SourceKind.MEMBER)


dimension_job = define_asset_job(
    "fongniao_dimensions_daily",
    selection=AssetSelection.assets(
        merchant_dimension, place_dimension, device_dimension
    ),
)
operations_job = define_asset_job(
    "fongniao_operations_daily",
    selection=AssetSelection.assets(device_daily_statistics_fact),
)
authoritative_transaction_job = define_asset_job(
    "fongniao_authoritative_transactions_daily",
    selection=AssetSelection.assets(authoritative_orders),
)
forecast_job = define_asset_job(
    "fongniao_forecast_inputs_daily",
    selection=AssetSelection.assets(ai_revenue_forecast_inputs),
)
commercial_job = define_asset_job(
    "fongniao_commercial_daily",
    selection=AssetSelection.assets(
        campaign_inputs, product_inputs, products_inputs, promotion_inputs
    ),
)
learning_job = define_asset_job(
    "fongniao_learning_daily",
    selection=AssetSelection.assets(consumer_segment_import_lineage),
)
transaction_job = define_asset_job(
    "fongniao_mapped_transactions_manual",
    selection=AssetSelection.assets(mapped_transactions),
)
trade_job = define_asset_job(
    "fongniao_bounded_trade_manual",
    selection=AssetSelection.assets(bounded_trade),
)
member_job = define_asset_job(
    "fongniao_member_minimized_manual",
    selection=AssetSelection.assets(minimized_member_quarantine),
)
device_log_job = define_asset_job(
    "fongniao_bounded_device_log_manual",
    selection=AssetSelection.assets(bounded_device_log),
)


def _previous_day_partition(context: Any) -> RunRequest:
    scheduled = context.scheduled_execution_time or datetime.now(UTC)
    partition_key = (scheduled.astimezone(UTC) - timedelta(days=1)).date().isoformat()
    return RunRequest(partition_key=partition_key)


dimension_schedule = ScheduleDefinition(
    name="fongniao_dimensions_daily_schedule",
    job=dimension_job,
    cron_schedule="0 1 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)
operations_schedule = ScheduleDefinition(
    name="fongniao_operations_daily_schedule",
    job=operations_job,
    cron_schedule="0 2 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)
authoritative_transaction_schedule = ScheduleDefinition(
    name="fongniao_authoritative_transactions_daily_schedule",
    job=authoritative_transaction_job,
    cron_schedule="30 2 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)
forecast_schedule = ScheduleDefinition(
    name="fongniao_forecast_inputs_daily_schedule",
    job=forecast_job,
    cron_schedule="0 3 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)
commercial_schedule = ScheduleDefinition(
    name="fongniao_commercial_daily_schedule",
    job=commercial_job,
    cron_schedule="0 4 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)
learning_schedule = ScheduleDefinition(
    name="fongniao_learning_daily_schedule",
    job=learning_job,
    cron_schedule="0 5 * * *",
    execution_timezone="UTC",
    execution_fn=_previous_day_partition,
)


def _changed(source_kinds: tuple[SourceKind, ...], since: datetime) -> bool:
    config = DataPlaneConfig.from_env()
    source = MongoSource(config)
    return any(source.has_changes_since(kind, since) for kind in source_kinds)


def _sensor_tick(
    context: SensorEvaluationContext,
    source_kinds: tuple[SourceKind, ...],
    prefix: str,
) -> RunRequest | None:
    now = datetime.now(UTC)
    since = (
        datetime.fromisoformat(context.cursor)
        if context.cursor
        else now - timedelta(days=1)
    )
    if not _changed(source_kinds, since):
        context.update_cursor(now.isoformat())
        return None
    partition_key = (now - timedelta(days=1)).date().isoformat()
    context.update_cursor(now.isoformat())
    return RunRequest(
        run_key=f"{prefix}:{partition_key}:{now.strftime('%Y%m%d%H')}",
        partition_key=partition_key,
    )


@sensor(
    job=dimension_job,
    minimum_interval_seconds=900,
    default_status=DefaultSensorStatus.RUNNING,
)
def dimension_change_sensor(context: SensorEvaluationContext) -> RunRequest | None:
    return _sensor_tick(
        context,
        (SourceKind.MERCHANT, SourceKind.PLACE, SourceKind.DEVICE),
        "dimensions",
    )


@sensor(
    job=operations_job,
    minimum_interval_seconds=900,
    default_status=DefaultSensorStatus.RUNNING,
)
def operations_change_sensor(context: SensorEvaluationContext) -> RunRequest | None:
    return _sensor_tick(
        context,
        (SourceKind.DEVICE_DAILY_STATISTICS,),
        "operations",
    )


@sensor(
    job=authoritative_transaction_job,
    minimum_interval_seconds=900,
    default_status=DefaultSensorStatus.RUNNING,
)
def authoritative_transaction_change_sensor(
    context: SensorEvaluationContext,
) -> RunRequest | None:
    return _sensor_tick(
        context,
        (SourceKind.ORDERS,),
        "authoritative-transactions",
    )



# --- Scoped CDC (H07 decision 5: Dagster-resident sensor, no message broker) ---
#
# The sensor *is* the consumer: each tick drains a bounded slice of the change
# stream and writes it straight to `data_plane.cdc_staging_events`, then replays
# the staged rows into the canonical tables. Nothing is enqueued anywhere else,
# which is the whole point of the ruling — no Kafka, Redpanda, Pub/Sub or
# RabbitMQ is introduced.
#
# Two things about the shape are deliberate. The tick interval is well under the
# sub-10s target so queuing delay is not what spends the budget, and the drain
# is bounded per tick so one busy partition cannot monopolise the sensor daemon.
#
# The recovery branch is what keeps hard constraint (a) true in the wiring and
# not just in the adapter: when a resume token falls outside the oplog window,
# the tick does not invent a new baseline, it requests a run of the *existing*
# batch job for the affected partitions. CDC is layered on the snapshot path;
# the snapshot path is what catches it when it falls.

#: Change events one tick may drain per partition.
CDC_TICK_LIMIT = 500
#: Tick interval, chosen against the sub-10s end-to-end target from H07.
CDC_TICK_SECONDS = 5

#: Batch job each scoped kind falls back to when its resume token expires.
_CDC_RECOVERY_JOBS = {
    SourceKind.ORDERS: "fongniao_authoritative_transactions_daily",
    SourceKind.DEVICE_LOG: "fongniao_bounded_device_log_manual",
}


def _cdc_drain(source_kind: SourceKind, partition_id: str) -> dict[str, Any]:
    """Run one resident drain-and-replay tick for a scoped collection.

    Built per tick rather than held open across ticks: a sensor daemon can be
    restarted between evaluations, so a connection cached on the module would
    outlive the process that validated it. Correctness does not depend on the
    stream staying open either — the durable resume token is what makes a tick
    boundary invisible to the data.
    """
    config = DataPlaneConfig.from_env()
    landing = PsycopgCdcStore(config)
    canonical = PsycopgCanonicalStore(config)
    adapter = ScopedCdcAdapter(
        store=landing,
        stream_factory=MongoChangeStreamFactory(MongoSource(config).database),
    )
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    landing.open_tick(run_id, source_kind, partition_id, started_at=started_at)
    error: BaseException | None = None
    try:
        result = adapter.drain(
            source_kind, partition_id, limit=CDC_TICK_LIMIT, run_id=run_id
        )
    except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
        error = exc
        raise
    finally:
        if error is not None:
            landing.close_tick(
                run_id,
                CdcDrainResult(
                    source_kind=source_kind, partition_id=partition_id, run_id=run_id
                ),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                error=error,
            )
    if result.plans:
        projector = ScopedCdcProjector(canonical_store=canonical, landing_store=landing)
        projection = projector.apply(source_kind, partition_id, result.plans)
    else:
        projection = None
    landing.close_tick(
        run_id, result, started_at=started_at, finished_at=datetime.now(UTC)
    )
    payload = result.as_dict()
    payload["projection"] = None if projection is None else projection.as_dict()
    return payload


def _cdc_sensor_tick(
    context: SensorEvaluationContext, source_kind: SourceKind
) -> RunRequest | None:
    """Drain one partition and, only on expiry, ask the batch path to recover.

    The cursor lives in PostgreSQL, not in the Dagster sensor cursor, because
    it has to survive the sensor daemon being rebuilt and has to be readable by
    the batch recovery path. The Dagster cursor carries the partition being
    served, which is scheduling state and nothing more.
    """
    policy = cdc_policy(source_kind)
    partition_id = context.cursor or policy.collection
    payload = _cdc_drain(source_kind, partition_id)
    context.update_cursor(partition_id)
    recovery = payload.get("recovery_plan")
    if not recovery:
        return None
    partitions = recovery.get("partition_keys") or []
    if not partitions:
        return None
    # Re-read the oldest uncovered day first; later ticks walk forward, so a
    # multi-day gap does not turn into one unbounded backfill run.
    return RunRequest(
        run_key=f"cdc-recovery:{source_kind.value}:{partitions[0]}",
        partition_key=partitions[0],
    )


@sensor(
    job=authoritative_transaction_job,
    minimum_interval_seconds=CDC_TICK_SECONDS,
    default_status=DefaultSensorStatus.STOPPED,
)
def scoped_cdc_orders_sensor(context: SensorEvaluationContext) -> RunRequest | None:
    return _cdc_sensor_tick(context, SourceKind.ORDERS)


@sensor(
    job=device_log_job,
    minimum_interval_seconds=CDC_TICK_SECONDS,
    default_status=DefaultSensorStatus.STOPPED,
)
def scoped_cdc_device_log_sensor(context: SensorEvaluationContext) -> RunRequest | None:
    return _cdc_sensor_tick(context, SourceKind.DEVICE_LOG)


#: Sensors that open a change stream. They default to STOPPED, unlike the batch
#: sensors above, because starting one reads `fongniao_prod` through the
#: `odp_cdc_reader` credential: enabling it is an operator action against a live
#: cluster, not something a deployment of this code should perform by itself.
SCOPED_CDC_SENSORS = (scoped_cdc_orders_sensor, scoped_cdc_device_log_sensor)


defs = Definitions(
    assets=[
        merchant_dimension,
        place_dimension,
        device_dimension,
        device_daily_statistics_fact,
        authoritative_orders,
        mapped_transactions,
        bounded_trade,
        bounded_device_log,
        ai_revenue_forecast_inputs,
        campaign_inputs,
        product_inputs,
        products_inputs,
        promotion_inputs,
        consumer_segment_import_lineage,
        minimized_member_quarantine,
    ],
    jobs=[
        dimension_job,
        operations_job,
        authoritative_transaction_job,
        forecast_job,
        commercial_job,
        learning_job,
        transaction_job,
        trade_job,
        member_job,
        device_log_job,
    ],
    schedules=[
        dimension_schedule,
        operations_schedule,
        authoritative_transaction_schedule,
        forecast_schedule,
        commercial_schedule,
        learning_schedule,
    ],
    sensors=[
        dimension_change_sensor,
        operations_change_sensor,
        authoritative_transaction_change_sensor,
        *SCOPED_CDC_SENSORS,
    ],
)
