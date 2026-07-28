"""Activate authoritative ForecastOps daily history in the canonical PostgreSQL 16 target.

This module never creates rows. It only propagates rows that already exist in the
approved legacy PostgreSQL source, which itself is populated exclusively by the
governed Mongo-to-PostgreSQL data plane (`apps/data_platform`). There is no
fixture, synthetic, seed, spine, or fallback path: every relation is copied by
selecting persisted source rows, and every insert is conflict-safe so replay is
idempotent.

Modes
-----
``inventory``
    Read-only. Reports per-relation counts, transaction date coverage, and
    forecast eligibility on both source and target. Aggregates only.
``activate``
    Dependency-ordered, conflict-safe copy of the transaction-attributable
    relations from source to target under a target advisory lock.
``verify``
    Read-only. Reports the canonical horizon windows the target
    ``model_ready.forecast_training_view`` can produce.

Transport contract
------------------
Both DSNs must be remote PostgreSQL URLs. A local transport is accepted only
with an explicit approved Cloud SQL proxy/connector attestation, mirroring the
contract already enforced by ``apps.data_platform.config``.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

import psycopg

SOURCE_URL_ENV = "ODP_LEGACY_DATABASE_URL"
TARGET_URL_ENV = "ODAY_DATABASE_URL"
PROXY_ENV = "ODP_ACTIVATION_CLOUD_SQL_PROXY"
SOURCE_INSTANCE_ENV = "ODP_ACTIVATION_SOURCE_INSTANCE"
TARGET_INSTANCE_ENV = "ODP_ACTIVATION_TARGET_INSTANCE"
CONNECTOR_EVIDENCE_ENV = "ODP_ACTIVATION_CONNECTOR_EVIDENCE"

APPROVED_CONNECTOR_EVIDENCE = frozenset(
    {"cloud-sql-auth-proxy-sidecar", "cloud-sql-python-connector"}
)
_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})
_PLACEHOLDER_TOKENS = ("<", ">", "change-me", "changeme", "example", "placeholder", "your-")

ACTIVATION_LOCK_KEY = 8_243_517_190_244_601_337
COPY_RELATION_TIMEOUT_MS = 900_000

# Reported by default because the acceptance bar is stated in these terms. The
# trainer's own horizons live in ``modules.forecastops.model_contract`` and can
# be measured with ``--horizons``.
DEFAULT_HORIZON_DAYS: tuple[int, ...] = (7, 14, 28)

CANONICAL_TRANSACTION_TABLE = "core.transactions"


class ForecastHistoryActivationError(RuntimeError):
    """Raised when the activation contract cannot be satisfied safely."""


@dataclass(frozen=True)
class Relation:
    """One conflict-safe copy step.

    ``source_predicate`` restricts the copy to transaction-attributable rows so
    the target never receives lineage that the forecast contract cannot explain.
    """

    schema: str
    table: str
    source_predicate: str | None = None

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"


# Dependency ordered: parents before children, so every foreign key resolves.
ACTIVATION_RELATIONS: tuple[Relation, ...] = (
    Relation("core", "tenants"),
    Relation("core", "brands"),
    Relation("core", "address_locations"),
    Relation("core", "stores"),
    Relation("core", "machines"),
    Relation("core", "transactions"),
    Relation(
        "data_plane",
        "ingestion_runs",
        source_predicate=(
            "run_id IN (SELECT run_id FROM data_plane.canonical_lineage "
            "WHERE canonical_table = 'core.transactions')"
        ),
    ),
    Relation(
        "data_plane",
        "canonical_lineage",
        source_predicate="canonical_table = 'core.transactions'",
    ),
)


def _reject_placeholder(value: str, field: str) -> None:
    lowered = value.lower()
    if any(token in lowered for token in _PLACEHOLDER_TOKENS):
        raise ForecastHistoryActivationError(f"{field} contains a placeholder value")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ForecastHistoryActivationError(f"{name} is required")
    return value


def require_activation_dsn(value: str, *, field: str, instance_env: str, env: Mapping[str, str]) -> str:
    """Validate one activation DSN and return it unchanged.

    Remote PostgreSQL is always allowed. Local transport requires the approved
    Cloud SQL proxy attestation and a matching instance connection name.
    """

    normalized = value.strip()
    if not normalized:
        raise ForecastHistoryActivationError(f"{field} is required")
    _reject_placeholder(normalized, field)
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"postgres", "postgresql"}:
        raise ForecastHistoryActivationError(f"{field} must be a PostgreSQL URL")
    if not parsed.path or parsed.path == "/":
        raise ForecastHistoryActivationError(f"{field} must name a database")

    socket_host = parse_qs(parsed.query).get("host", [""])[0]
    local_transport = (parsed.hostname or "").lower() in _LOCAL_HOSTS or socket_host.startswith(
        "/cloudsql/"
    )
    if not local_transport:
        return normalized

    if env.get(PROXY_ENV, "").strip().lower() not in {"1", "true", "yes"}:
        raise ForecastHistoryActivationError(
            f"{field} uses a local transport, which requires {PROXY_ENV}=true"
        )
    evidence = env.get(CONNECTOR_EVIDENCE_ENV, "").strip()
    if evidence not in APPROVED_CONNECTOR_EVIDENCE:
        raise ForecastHistoryActivationError(
            f"{CONNECTOR_EVIDENCE_ENV} must be one of "
            f"{', '.join(sorted(APPROVED_CONNECTOR_EVIDENCE))}"
        )
    instance = _required(env, instance_env)
    if instance.count(":") != 2:
        raise ForecastHistoryActivationError(f"{instance_env} must be project:region:instance")
    if socket_host.startswith("/cloudsql/") and instance not in socket_host:
        raise ForecastHistoryActivationError(
            f"{field} Cloud SQL socket does not match {instance_env}"
        )
    return normalized


@dataclass(frozen=True)
class ActivationConfig:
    source_dsn: str
    target_dsn: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ActivationConfig:
        values = dict(os.environ if env is None else env)
        return cls(
            source_dsn=require_activation_dsn(
                _required(values, SOURCE_URL_ENV),
                field=SOURCE_URL_ENV,
                instance_env=SOURCE_INSTANCE_ENV,
                env=values,
            ),
            target_dsn=require_activation_dsn(
                _required(values, TARGET_URL_ENV),
                field=TARGET_URL_ENV,
                instance_env=TARGET_INSTANCE_ENV,
                env=values,
            ),
        )


def _column_map(connection: psycopg.Connection, relation: Relation) -> dict[str, tuple[bool, bool]]:
    """Return ``{column: (nullable, has_default)}`` in ordinal order."""

    rows = connection.execute(
        """
        SELECT column_name, is_nullable = 'YES', column_default IS NOT NULL
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (relation.schema, relation.table),
    ).fetchall()
    return {str(name): (bool(nullable), bool(has_default)) for name, nullable, has_default in rows}


def resolve_copy_columns(
    target_columns: Mapping[str, tuple[bool, bool]],
    source_columns: Mapping[str, tuple[bool, bool]],
    *,
    relation: str,
) -> tuple[str, ...]:
    """Choose the columns to copy, failing closed on an unsatisfiable target column.

    A target column that is NOT NULL and has no default must exist in the source;
    otherwise the copy would have to invent a value, which this module never does.
    """

    if not target_columns:
        raise ForecastHistoryActivationError(f"target relation {relation} does not exist")
    if not source_columns:
        raise ForecastHistoryActivationError(f"source relation {relation} does not exist")
    missing_required = [
        name
        for name, (nullable, has_default) in target_columns.items()
        if name not in source_columns and not nullable and not has_default
    ]
    if missing_required:
        raise ForecastHistoryActivationError(
            f"{relation} target requires columns absent from the source: "
            f"{', '.join(sorted(missing_required))}"
        )
    selected = tuple(name for name in target_columns if name in source_columns)
    if not selected:
        raise ForecastHistoryActivationError(f"{relation} has no shared columns to copy")
    return selected


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _count(connection: psycopg.Connection, relation: str, predicate: str | None = None) -> int:
    where = f" WHERE {predicate}" if predicate else ""
    row = connection.execute(f"SELECT count(*) FROM {relation}{where}").fetchone()
    return int(row[0]) if row else 0


@contextmanager
def _activation_lock(connection: psycopg.Connection) -> Iterator[None]:
    connection.execute("SELECT pg_advisory_lock(%s)", (ACTIVATION_LOCK_KEY,))
    try:
        yield
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (ACTIVATION_LOCK_KEY,))


def copy_relation(
    source: psycopg.Connection,
    target: psycopg.Connection,
    relation: Relation,
) -> dict[str, Any]:
    """Copy one relation source -> target with ``ON CONFLICT DO NOTHING``."""

    columns = resolve_copy_columns(
        _column_map(target, relation),
        _column_map(source, relation),
        relation=relation.qualified,
    )
    column_sql = ", ".join(_quote(name) for name in columns)
    where = f" WHERE {relation.source_predicate}" if relation.source_predicate else ""
    source_rows = _count(source, relation.qualified, relation.source_predicate)
    before = _count(target, relation.qualified)

    staging = f"activation_stage_{relation.schema}_{relation.table}"
    target.execute(f"DROP TABLE IF EXISTS pg_temp.{_quote(staging)}")
    target.execute(
        f"CREATE TEMP TABLE {_quote(staging)} "
        f"(LIKE {relation.qualified} INCLUDING DEFAULTS) ON COMMIT DROP"
    )

    read_sql = f"COPY (SELECT {column_sql} FROM {relation.qualified}{where}) TO STDOUT"
    write_sql = f"COPY {_quote(staging)} ({column_sql}) FROM STDIN"
    with source.cursor() as reader, target.cursor() as writer:
        with reader.copy(read_sql) as outbound, writer.copy(write_sql) as inbound:
            for block in outbound:
                inbound.write(block)

    inserted = target.execute(
        f"INSERT INTO {relation.qualified} ({column_sql}) "
        f"SELECT {column_sql} FROM {_quote(staging)} ON CONFLICT DO NOTHING"
    ).rowcount
    target.execute(f"DROP TABLE IF EXISTS pg_temp.{_quote(staging)}")
    after = _count(target, relation.qualified)
    return {
        "relation": relation.qualified,
        "copied_columns": len(columns),
        "source_rows_selected": source_rows,
        "target_rows_before": before,
        "target_rows_inserted": max(int(inserted), 0),
        "target_rows_after": after,
    }


def transaction_date_coverage(connection: psycopg.Connection) -> dict[str, Any]:
    """Aggregate-only daily coverage of successful TWD transactions."""

    rows = connection.execute(
        """
        SELECT (event_time AT TIME ZONE 'UTC')::date AS event_date,
               count(*)::bigint AS transaction_count,
               count(DISTINCT store_id)::bigint AS store_count
        FROM core.transactions
        WHERE transaction_status = 'succeeded' AND currency = 'TWD'
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    dates = [row[0] for row in rows]
    gaps: list[str] = []
    for previous, current in zip(dates, dates[1:], strict=False):
        if (current - previous).days > 1:
            gaps.append(f"{previous.isoformat()}..{current.isoformat()}")
    return {
        "distinct_dates": len(dates),
        "min_date": dates[0].isoformat() if dates else None,
        "max_date": dates[-1].isoformat() if dates else None,
        "calendar_gaps": gaps,
        "daily_partitions": [
            {
                "date": row[0].isoformat(),
                "transaction_count": int(row[1]),
                "store_count": int(row[2]),
            }
            for row in rows
        ],
    }


def _relation_exists(connection: psycopg.Connection, schema: str, name: str) -> bool:
    row = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
            UNION ALL
            SELECT 1 FROM information_schema.views
            WHERE table_schema = %s AND table_name = %s
        )
        """,
        (schema, name, schema, name),
    ).fetchone()
    return bool(row and row[0])


def forecast_horizon_windows(
    connection: psycopg.Connection,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
) -> dict[str, Any]:
    """Report canonical horizon windows the target training view can produce.

    A window of ``h`` days exists for a store when it holds ``h`` consecutive
    training-eligible calendar dates. No persisted row is created or changed:
    the only relations written are ``ON COMMIT DROP`` temporary ones, and
    callers roll the probing transaction back (see :func:`probe_target`).
    """

    if not _relation_exists(connection, "model_ready", "forecast_training_view"):
        return {"available": False, "reason": "model_ready.forecast_training_view is absent"}

    # The view re-derives 28-day causal features per row, so it is scanned once
    # into a temporary projection and every aggregate below reads that instead.
    connection.execute("DROP TABLE IF EXISTS pg_temp.forecast_eligibility_probe")
    connection.execute(
        """
        CREATE TEMP TABLE forecast_eligibility_probe ON COMMIT DROP AS
        SELECT tenant_id, store_id, date, is_training_eligible, exclusion_reason
        FROM model_ready.forecast_training_view
        """
    )
    connection.execute(
        "CREATE INDEX ON pg_temp.forecast_eligibility_probe (tenant_id, store_id, date)"
    )
    connection.execute("ANALYZE pg_temp.forecast_eligibility_probe")

    totals = connection.execute(
        """
        SELECT count(*)::bigint,
               count(*) FILTER (WHERE is_training_eligible)::bigint,
               count(DISTINCT tenant_id)::bigint,
               count(DISTINCT store_id) FILTER (WHERE is_training_eligible)::bigint,
               min(date) FILTER (WHERE is_training_eligible),
               max(date) FILTER (WHERE is_training_eligible)
        FROM pg_temp.forecast_eligibility_probe
        """
    ).fetchone()

    exclusions = connection.execute(
        """
        SELECT coalesce(exclusion_reason, 'ELIGIBLE'), count(*)::bigint
        FROM pg_temp.forecast_eligibility_probe
        GROUP BY 1 ORDER BY 2 DESC
        """
    ).fetchall()

    # Consecutive eligible dates per store, via the classic date - row_number
    # gaps-and-islands grouping, materialized once for every horizon question.
    connection.execute("DROP TABLE IF EXISTS pg_temp.forecast_eligible_streaks")
    connection.execute(
        """
        CREATE TEMP TABLE forecast_eligible_streaks ON COMMIT DROP AS
        WITH eligible AS (
            SELECT tenant_id, store_id, date,
                   date - (row_number() OVER (
                       PARTITION BY tenant_id, store_id ORDER BY date
                   ))::int AS island
            FROM pg_temp.forecast_eligibility_probe
            WHERE is_training_eligible
        )
        SELECT tenant_id, store_id, island,
               count(*)::bigint AS streak_days,
               min(date) AS streak_start,
               max(date) AS streak_end
        FROM eligible
        GROUP BY tenant_id, store_id, island
        """
    )

    runs = connection.execute(
        """
        SELECT max(streak_days)::bigint,
               min(streak_start) FILTER (
                   WHERE streak_days = (SELECT max(streak_days) FROM pg_temp.forecast_eligible_streaks)
               ),
               max(streak_end) FILTER (
                   WHERE streak_days = (SELECT max(streak_days) FROM pg_temp.forecast_eligible_streaks)
               ),
               count(DISTINCT store_id)::bigint
        FROM pg_temp.forecast_eligible_streaks
        """
    ).fetchone()

    window_counts: dict[str, Any] = {}
    for horizon in horizons:
        row = connection.execute(
            """
            SELECT
                coalesce(sum(greatest(streak_days - %s + 1, 0)), 0)::bigint,
                count(DISTINCT store_id) FILTER (WHERE streak_days >= %s)::bigint
            FROM pg_temp.forecast_eligible_streaks
            """,
            (horizon, horizon),
        ).fetchone()
        window_counts[f"h{horizon}"] = {
            "complete_windows": int(row[0]) if row else 0,
            "stores_with_complete_window": int(row[1]) if row else 0,
        }

    return {
        "available": True,
        "rows": int(totals[0]) if totals else 0,
        "training_eligible_rows": int(totals[1]) if totals else 0,
        "tenant_count": int(totals[2]) if totals else 0,
        "eligible_store_count": int(totals[3]) if totals else 0,
        "eligible_min_date": totals[4].isoformat() if totals and totals[4] else None,
        "eligible_max_date": totals[5].isoformat() if totals and totals[5] else None,
        "exclusion_reasons": {str(reason): int(count) for reason, count in exclusions},
        "longest_eligible_streak_days": int(runs[0]) if runs and runs[0] else 0,
        "longest_streak_start": runs[1].isoformat() if runs and runs[1] else None,
        "longest_streak_end": runs[2].isoformat() if runs and runs[2] else None,
        "stores_with_any_streak": int(runs[3]) if runs and runs[3] else 0,
        "horizon_windows": window_counts,
    }


def relation_inventory(connection: psycopg.Connection) -> dict[str, int | None]:
    counts: dict[str, int | None] = {}
    for relation in ACTIVATION_RELATIONS:
        if not _relation_exists(connection, relation.schema, relation.table):
            counts[relation.qualified] = None
            continue
        counts[relation.qualified] = _count(connection, relation.qualified)
    return counts


def parse_horizons(value: str) -> tuple[int, ...]:
    """Parse a comma-separated horizon-day list, failing closed on bad input."""

    horizons: list[int] = []
    for token in value.split(","):
        candidate = token.strip()
        if not candidate:
            continue
        try:
            days = int(candidate)
        except ValueError as error:
            raise ForecastHistoryActivationError(
                f"horizon {candidate!r} is not an integer number of days"
            ) from error
        if days <= 0:
            raise ForecastHistoryActivationError(f"horizon {days} must be a positive day count")
        horizons.append(days)
    if not horizons:
        raise ForecastHistoryActivationError("at least one horizon is required")
    return tuple(dict.fromkeys(horizons))


def probe_target(
    target: psycopg.Connection,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
) -> dict[str, Any]:
    """Measure the target without persisting anything.

    ``forecast_horizon_windows`` needs ``CREATE TEMP TABLE``, which PostgreSQL
    forbids in a read-only transaction, so the probe runs in a normal
    transaction that is always rolled back. Only ``pg_temp`` relations are
    written, and the rollback drops them.
    """

    try:
        target.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        return {
            "relations": relation_inventory(target),
            "transaction_coverage": transaction_date_coverage(target),
            "forecast_windows": forecast_horizon_windows(target, horizons=horizons),
        }
    finally:
        target.rollback()


def run_inventory(
    config: ActivationConfig,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
) -> dict[str, Any]:
    with (
        psycopg.connect(config.source_dsn) as source,
        psycopg.connect(config.target_dsn) as target,
    ):
        source.read_only = True
        return {
            "mode": "inventory",
            "source": {
                "relations": relation_inventory(source),
                "transaction_coverage": transaction_date_coverage(source),
            },
            "target": probe_target(target, horizons=horizons),
        }


def run_activation(
    config: ActivationConfig,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    with (
        psycopg.connect(config.source_dsn) as source,
        psycopg.connect(config.target_dsn) as target,
    ):
        source.read_only = True
        source.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        target.execute(f"SET statement_timeout = {COPY_RELATION_TIMEOUT_MS}")
        with _activation_lock(target):
            for relation in ACTIVATION_RELATIONS:
                receipts.append(copy_relation(source, target, relation))
                target.commit()
        probe = probe_target(target, horizons=horizons)
    return {
        "mode": "activate",
        "status": "SUCCEEDED",
        "relations": receipts,
        "target_transaction_coverage": probe["transaction_coverage"],
        "target_forecast_windows": probe["forecast_windows"],
    }


def run_verify(
    config: ActivationConfig,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZON_DAYS,
) -> dict[str, Any]:
    with psycopg.connect(config.target_dsn) as target:
        probe = probe_target(target, horizons=horizons)
    return {
        "mode": "verify",
        "target_relations": probe["relations"],
        "target_transaction_coverage": probe["transaction_coverage"],
        "target_forecast_windows": probe["forecast_windows"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("inventory", "activate", "verify"))
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path for the JSON receipt; stdout is always written.",
    )
    parser.add_argument(
        "--horizons",
        default=",".join(str(days) for days in DEFAULT_HORIZON_DAYS),
        help="Comma-separated horizon lengths in days to measure.",
    )
    args = parser.parse_args(argv)

    horizons = parse_horizons(args.horizons)
    config = ActivationConfig.from_env()
    if args.mode == "inventory":
        payload = run_inventory(config, horizons=horizons)
    elif args.mode == "activate":
        payload = run_activation(config, horizons=horizons)
    else:
        payload = run_verify(config, horizons=horizons)

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
