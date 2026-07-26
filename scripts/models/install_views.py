from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import (
    ModelTrainingConfigurationError,
    require_production_database_url,
)

MODEL_READY_SQL_PATH = Path(__file__).with_name("sql") / "model_ready_views.sql"
MODEL_READY_CONTRACT_VERSION = "2026-07-26.2"

PREREQUISITE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "core.transactions": (
        "transaction_id",
        "store_id",
        "event_time",
        "observation_time",
        "net_amount",
        "currency",
        "transaction_status",
        "ingested_at",
    ),
    "core.stores": (
        "store_id",
        "tenant_id",
        "store_format_code",
        "opened_on",
        "address_id",
    ),
    "core.address_locations": (
        "address_id",
        "latitude",
        "longitude",
        "geocode_confidence",
        "h3_res_9",
    ),
    "data_plane.canonical_lineage": (
        "source_snapshot_id",
        "run_id",
        "tenant_id",
        "canonical_table",
        "canonical_id",
        "projected_at",
    ),
    "data_plane.ingestion_runs": (
        "run_id",
        "source_kind",
        "partition_key",
        "status",
        "finished_at",
    ),
}

ACTIVE_VIEW_CONTRACTS: Mapping[str, str] = {
    "model_ready.forecast_training_view": "forecast-training-view-v2",
    "model_ready.candidate_site_view": "candidate-site-view-v2",
    "model_ready.heatzone_training_view": "heatzone-training-view-v2",
}

ELIGIBILITY_PREREQUISITE_SQL = """
SELECT
    count(*)::bigint AS total_store_rows,
    count(*) FILTER (WHERE store.opened_on IS NOT NULL)::bigint
        AS stores_with_opened_on,
    count(*) FILTER (WHERE store.opened_on IS NULL)::bigint
        AS stores_missing_opened_on,
    count(*) FILTER (WHERE store.address_id IS NULL)::bigint
        AS stores_missing_address,
    count(*) FILTER (
        WHERE address.address_id IS NOT NULL
          AND (address.latitude IS NULL OR address.longitude IS NULL)
    )::bigint AS stores_missing_coordinates,
    count(*) FILTER (
        WHERE address.address_id IS NOT NULL
          AND address.h3_res_9 IS NULL
    )::bigint AS stores_missing_h3_res_9,
    count(*) FILTER (WHERE store.store_format_code IS NULL)::bigint
        AS stores_missing_format,
    count(*) FILTER (
        WHERE store.opened_on IS NOT NULL
          AND store.store_format_code IS NOT NULL
          AND address.latitude IS NOT NULL
          AND address.longitude IS NOT NULL
          AND address.h3_res_9 IS NOT NULL
    )::bigint AS sitescore_anchor_prerequisite_rows,
    count(
        DISTINCT (store.tenant_id, address.h3_res_9)
    ) FILTER (
        WHERE store.opened_on IS NOT NULL
          AND address.latitude IS NOT NULL
          AND address.longitude IS NOT NULL
          AND address.h3_res_9 IS NOT NULL
    )::bigint AS heatzone_cell_prerequisite_rows,
    (
        SELECT count(*)::bigint
        FROM core.transactions AS txn
        WHERE txn.transaction_status = 'succeeded'
          AND txn.currency = 'TWD'
    ) AS successful_twd_transaction_rows
FROM core.stores AS store
LEFT JOIN core.address_locations AS address
    ON address.address_id = store.address_id
"""


class ModelReadyViewInstallError(RuntimeError):
    """Raised when model-ready views cannot be installed as a bound contract."""


class InstallationClient(Protocol):
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

    def transaction(self) -> AbstractContextManager[Any]: ...


@dataclass(frozen=True)
class ModelReadyViewPreflight:
    missing_relations: tuple[str, ...]
    missing_columns: Mapping[str, tuple[str, ...]]
    prerequisite_counts: Mapping[str, int]

    @property
    def ready(self) -> bool:
        return not self.missing_relations and not self.missing_columns

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": MODEL_READY_CONTRACT_VERSION,
            "sql_path": str(MODEL_READY_SQL_PATH),
            "ready": self.ready,
            "missing_relations": list(self.missing_relations),
            "missing_columns": {
                relation: list(columns)
                for relation, columns in sorted(self.missing_columns.items())
            },
            "forecast_source": "core.transactions",
            "eligible_row_prerequisite_evidence": {
                "counts": dict(sorted(self.prerequisite_counts.items())),
                "note": (
                    "These are schema/input prerequisites, not training-eligible "
                    "row counts; trainer minimum-row checks remain authoritative."
                ),
            },
            "optional_outcome_models": {
                "avm": {
                    "trainable": False,
                    "reason": "MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING",
                },
                "sitescore": {
                    "contract_installable": self.ready,
                    "minimum_training_rows": 200,
                    "anchor_prerequisite_rows": self.prerequisite_counts.get(
                        "sitescore_anchor_prerequisite_rows", 0
                    ),
                },
                "heatzone": {
                    "contract_installable": self.ready,
                    "minimum_training_rows": 200,
                    "cell_prerequisite_rows": self.prerequisite_counts.get(
                        "heatzone_cell_prerequisite_rows", 0
                    ),
                },
                "avm-liquidity": {
                    "trainable": False,
                    "reason": "MATURE_LIQUIDITY_EVENT_RELATION_MISSING",
                },
            },
        }


class ModelReadyViewInstaller:
    def __init__(
        self,
        client: InstallationClient,
        *,
        sql_path: Path = MODEL_READY_SQL_PATH,
    ) -> None:
        self.client = client
        self.sql_path = sql_path

    def preflight(self) -> ModelReadyViewPreflight:
        missing_relations: list[str] = []
        missing_columns: dict[str, tuple[str, ...]] = {}
        for relation, required_columns in PREREQUISITE_COLUMNS.items():
            row = self.client.query_one(
                "SELECT to_regclass(?) AS relation",
                (relation,),
            )
            if not row or row.get("relation") is None:
                missing_relations.append(relation)
                continue
            schema, table = relation.split(".", 1)
            column_rows = self.client.query(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ? "
                "ORDER BY ordinal_position",
                (schema, table),
            )
            available = {str(item["column_name"]) for item in column_rows}
            missing = tuple(
                column for column in required_columns if column not in available
            )
            if missing:
                missing_columns[relation] = missing
        prerequisite_counts: dict[str, int] = {}
        if not missing_relations and not missing_columns:
            row = self.client.query_one(ELIGIBILITY_PREREQUISITE_SQL)
            if row:
                prerequisite_counts = {
                    str(name): int(value or 0)
                    for name, value in row.items()
                }
        return ModelReadyViewPreflight(
            missing_relations=tuple(missing_relations),
            missing_columns=missing_columns,
            prerequisite_counts=prerequisite_counts,
        )

    def install(self) -> dict[str, Any]:
        preflight = self.preflight()
        if not preflight.ready:
            raise ModelReadyViewInstallError(
                "model-ready view prerequisites are incomplete: "
                + json.dumps(preflight.to_dict(), sort_keys=True)
            )
        sql_bytes = self.sql_path.read_bytes()
        sql = sql_bytes.decode("utf-8")
        digest = hashlib.sha256(sql_bytes).hexdigest()
        _validate_sql_contract(sql)
        with self.client.transaction():
            self.client.execute(
                "SELECT pg_advisory_xact_lock(hashtext("
                "'oday-plus:model-ready-views:2026-07-26.2'))"
            )
            self.client.execute("SET LOCAL lock_timeout = '10s'")
            self.client.execute("SET LOCAL statement_timeout = '5min'")
            self.client.execute(sql)
            self.client.execute(
                "UPDATE model_ready.view_contracts "
                "SET installer_sha256 = ?, installed_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP",
                (digest,),
            )
        contracts = self.client.query(
            "SELECT relation_name, view_name, view_version, contract_state, "
            "training_enabled, blocked_reason, installer_sha256 "
            "FROM model_ready.view_contracts "
            "WHERE relation_name IN (?, ?, ?) "
            "ORDER BY relation_name",
            tuple(ACTIVE_VIEW_CONTRACTS),
        )
        contracts_by_relation = {
            str(contract["relation_name"]): dict(contract)
            for contract in contracts
        }
        verification_errors: list[str] = []
        for relation_name, expected_version in ACTIVE_VIEW_CONTRACTS.items():
            contract = contracts_by_relation.get(relation_name)
            relation = self.client.query_one(
                "SELECT to_regclass(?) AS relation",
                (relation_name,),
            )
            if not contract:
                verification_errors.append(f"{relation_name}: contract missing")
                continue
            if contract.get("view_version") != expected_version:
                verification_errors.append(f"{relation_name}: version mismatch")
            if contract.get("contract_state") != "ACTIVE":
                verification_errors.append(f"{relation_name}: not ACTIVE")
            if contract.get("training_enabled") is not True:
                verification_errors.append(f"{relation_name}: training disabled")
            if contract.get("installer_sha256") != digest:
                verification_errors.append(f"{relation_name}: digest mismatch")
            if not relation or relation.get("relation") is None:
                verification_errors.append(f"{relation_name}: view missing")
        if verification_errors:
            raise ModelReadyViewInstallError(
                "installed views did not satisfy registered ACTIVE contracts: "
                + "; ".join(verification_errors)
            )
        return {
            "status": "installed",
            "contract_version": MODEL_READY_CONTRACT_VERSION,
            "sql_sha256": digest,
            "active_contracts": contracts_by_relation,
            "eligible_row_prerequisite_evidence": dict(
                sorted(preflight.prerequisite_counts.items())
            ),
            "minimum_data_checks": "trainer",
        }


def _validate_sql_contract(sql: str) -> None:
    required_fragments = (
        "CREATE OR REPLACE VIEW model_ready.forecast_training_view",
        "CREATE OR REPLACE VIEW model_ready.candidate_site_view",
        "CREATE OR REPLACE VIEW model_ready.heatzone_training_view",
        "FROM core.transactions AS txn",
        "data_plane.canonical_lineage",
        "'forecast-training-view-v2'::text AS view_version",
        "AS feature_snapshot_time",
        "AS prediction_origin_time",
        "AS label_maturity_time",
        "'candidate-site-view-v2'::text AS view_version",
        "'heatzone-training-view-v2'::text AS view_version",
        "AS is_training_eligible",
    )
    missing = tuple(fragment for fragment in required_fragments if fragment not in sql)
    if missing:
        raise ModelReadyViewInstallError(
            "model-ready SQL is missing contract fragments: " + ", ".join(missing)
        )
    lowered = sql.lower()
    prohibited = ("generate_series(", "random(", "setseed(", "create table as")
    found = tuple(fragment for fragment in prohibited if fragment in lowered)
    if found:
        raise ModelReadyViewInstallError(
            "model-ready SQL contains prohibited row-generation constructs: "
            + ", ".join(found)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or inspect production model-ready PostgreSQL views",
    )
    parser.add_argument(
        "command",
        choices=("inventory", "install"),
        help="inventory is read-only; install applies the versioned SQL transaction",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client: InstallationClient | None = None,
) -> int:
    args = _parser().parse_args(argv)
    owned_client = None
    try:
        if client is None:
            database_url = require_production_database_url(
                os.getenv("ODAY_DATABASE_URL", "")
            )
            from shared.infrastructure.persistence.postgresql import PostgresEngine

            owned_client = PostgresEngine(
                database_url,
                bootstrap=False,
                validate_schema=False,
            )
            client = owned_client
        installer = ModelReadyViewInstaller(client)
        if args.command == "inventory":
            result = installer.preflight().to_dict()
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ready"] else 2
        print(json.dumps(installer.install(), sort_keys=True))
        return 0
    except (
        ModelReadyViewInstallError,
        ModelTrainingConfigurationError,
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
                sort_keys=True,
            )
        )
        return 2
    finally:
        if owned_client is not None:
            owned_client.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "MODEL_READY_CONTRACT_VERSION",
    "MODEL_READY_SQL_PATH",
    "ModelReadyViewInstallError",
    "ModelReadyViewInstaller",
    "ModelReadyViewPreflight",
    "PREREQUISITE_COLUMNS",
    "main",
]
