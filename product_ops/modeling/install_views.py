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
MODEL_READY_CONTRACT_VERSION = "2026-07-26.3"

REQUIRED_PREREQUISITE_COLUMNS: Mapping[str, tuple[str, ...]] = {
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
    "data_plane.place_geography": (
        "source_snapshot_id",
        "tenant_id",
        "store_id",
        "latitude",
        "longitude",
        "geocode_confidence",
        "h3_res_9",
        "run_id",
        "valid_from",
        "observed_at",
    ),
    "data_plane.transaction_authority": (
        "transaction_id",
        "source_kind",
        "source_snapshot_id",
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
        "processed_count",
        "valid_loaded",
        "quarantined_count",
        "reconciled",
        "partition_complete",
        "finished_at",
    ),
}

ACTIVE_VIEW_CONTRACTS: Mapping[str, str] = {
    "model_ready.forecast_training_view": "forecast-training-view-v2",
    "model_ready.candidate_site_view": "candidate-site-view-v2",
    "model_ready.heatzone_training_view": "heatzone-training-view-v2",
}
INSTALL_VIEW_RELATIONS = (
    "model_ready.forecast_training_view",
    "model_ready.valuation_view",
    "model_ready.listing_property_valuation_view",
    "model_ready.candidate_site_view",
    "model_ready.heatzone_training_view",
    "model_ready.avm_liquidity_training_view",
)

ELIGIBILITY_PREREQUISITE_SQL = """
SELECT
    count(*)::bigint AS total_store_rows,
    count(*) FILTER (WHERE store.opened_on IS NOT NULL)::bigint
        AS stores_with_opened_on,
    count(*) FILTER (WHERE store.opened_on IS NULL)::bigint
        AS stores_missing_opened_on,
    count(*) FILTER (WHERE geography.source_snapshot_id IS NULL)::bigint
        AS stores_missing_address,
    count(*) FILTER (
        WHERE geography.source_snapshot_id IS NOT NULL
          AND (geography.latitude IS NULL OR geography.longitude IS NULL)
    )::bigint AS stores_missing_coordinates,
    count(*) FILTER (
        WHERE geography.source_snapshot_id IS NOT NULL
          AND geography.h3_res_9 IS NULL
    )::bigint AS stores_missing_h3_res_9,
    count(*) FILTER (WHERE store.store_format_code IS NULL)::bigint
        AS stores_missing_format,
    count(*) FILTER (
        WHERE store.opened_on IS NOT NULL
          AND store.store_format_code IS NOT NULL
          AND geography.latitude IS NOT NULL
          AND geography.longitude IS NOT NULL
          AND geography.h3_res_9 IS NOT NULL
    )::bigint AS sitescore_anchor_prerequisite_rows,
    count(
        DISTINCT (store.tenant_id, geography.h3_res_9)
    ) FILTER (
        WHERE store.opened_on IS NOT NULL
          AND geography.latitude IS NOT NULL
          AND geography.longitude IS NOT NULL
          AND geography.h3_res_9 IS NOT NULL
    )::bigint AS heatzone_cell_prerequisite_rows,
    (
        SELECT count(*)::bigint
        FROM core.transactions AS txn
        WHERE txn.transaction_status = 'succeeded'
          AND txn.currency = 'TWD'
    ) AS successful_twd_transaction_rows
FROM core.stores AS store
LEFT JOIN LATERAL (
    SELECT place.*
    FROM data_plane.place_geography AS place
    WHERE place.tenant_id = store.tenant_id
      AND place.store_id = store.store_id
    ORDER BY place.valid_from DESC, place.observed_at DESC
    LIMIT 1
) AS geography ON TRUE
"""
OPTIONAL_PREREQUISITE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "external_data.real_estate_transactions": (
        "transaction_id",
        "source_id",
        "authority_partition",
        "source_record_id",
        "source_variant_id",
        "municipality",
        "district",
        "transaction_target",
        "transaction_date",
        "land_area_sqm",
        "building_area_sqm",
        "room_count",
        "hall_count",
        "bathroom_count",
        "building_type",
        "main_use",
        "main_material",
        "completion_date",
        "completion_year",
        "completion_month",
        "parking_area_sqm",
        "has_elevator",
        "total_price_twd",
        "last_seen_run_id",
    ),
    "external_data.real_estate_ingestion_runs": (
        "run_id",
        "source_id",
        "dataset_id",
        "license_id",
        "schema_sha256",
        "source_snapshot_id",
        "fetched_at",
        "status",
    ),
}

PREREQUISITE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    **REQUIRED_PREREQUISITE_COLUMNS,
    **OPTIONAL_PREREQUISITE_COLUMNS,
}


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
    optional_missing_relations: tuple[str, ...]
    optional_missing_columns: Mapping[str, tuple[str, ...]]

    @property
    def ready(self) -> bool:
        return not self.missing_relations and not self.missing_columns

    @property
    def official_outcomes_ready(self) -> bool:
        return (
            not self.optional_missing_relations
            and not self.optional_missing_columns
        )

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
            "optional_missing_relations": list(self.optional_missing_relations),
            "optional_missing_columns": {
                relation: list(columns)
                for relation, columns in sorted(
                    self.optional_missing_columns.items()
                )
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
                "listing_property_avm": {
                    "trainable": self.official_outcomes_ready,
                    "reason": (
                        None
                        if self.official_outcomes_ready
                        else "OFFICIAL_REAL_ESTATE_OUTCOME_RELATION_MISSING"
                    ),
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
                    "reason": "OFFICIAL_SALE_OUTCOME_HAS_NO_MARKETING_INTERVAL",
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
        required_missing_relations, required_missing_columns = (
            self._inspect_prerequisites(REQUIRED_PREREQUISITE_COLUMNS)
        )
        optional_missing_relations, optional_missing_columns = (
            self._inspect_prerequisites(OPTIONAL_PREREQUISITE_COLUMNS)
        )
        prerequisite_counts: dict[str, int] = {}
        if not required_missing_relations and not required_missing_columns:
            row = self.client.query_one(ELIGIBILITY_PREREQUISITE_SQL)
            if row:
                prerequisite_counts = {
                    str(name): int(value or 0) for name, value in row.items()
                }
        return ModelReadyViewPreflight(
            missing_relations=required_missing_relations,
            missing_columns=required_missing_columns,
            prerequisite_counts=prerequisite_counts,
            optional_missing_relations=optional_missing_relations,
            optional_missing_columns=optional_missing_columns,
        )

    def _inspect_prerequisites(
        self,
        requirements: Mapping[str, tuple[str, ...]],
    ) -> tuple[tuple[str, ...], Mapping[str, tuple[str, ...]]]:
        missing_relations: list[str] = []
        missing_columns: dict[str, tuple[str, ...]] = {}
        for relation, required_columns in requirements.items():
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
            missing = tuple(column for column in required_columns if column not in available)
            if missing:
                missing_columns[relation] = missing
        return tuple(missing_relations), missing_columns

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
        contracts_by_relation: dict[str, dict[str, Any]] = {}
        with self.client.transaction():
            self.client.execute(
                "SELECT pg_advisory_xact_lock(hashtext('oday-plus:model-ready-views:2026-07-26.3'))"
            )
            self.client.execute("SET LOCAL lock_timeout = '10s'")
            self.client.execute("SET LOCAL statement_timeout = '5min'")
            self.client.execute(sql)
            self.client.execute(
                "UPDATE model_ready.view_contracts "
                "SET installer_sha256 = ?, installed_at = CURRENT_TIMESTAMP, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE relation_name IN (?, ?, ?, ?, ?, ?)",
                (digest, *INSTALL_VIEW_RELATIONS),
            )
            contracts = self.client.query(
                "SELECT relation_name, view_name, view_version, contract_state, "
                "training_enabled, blocked_reason, installer_sha256 "
                "FROM model_ready.view_contracts "
                "WHERE relation_name IN (?, ?, ?, ?, ?, ?) "
                "ORDER BY relation_name",
                INSTALL_VIEW_RELATIONS,
            )
            installed_contracts = {
                str(contract["relation_name"]): dict(contract) for contract in contracts
            }
            contracts_by_relation = {
                relation_name: installed_contracts[relation_name]
                for relation_name in ACTIVE_VIEW_CONTRACTS
                if relation_name in installed_contracts
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
            forecast = installed_contracts.get(
                "model_ready.forecast_training_view"
            )
            relation = self.client.query_one(
                "SELECT to_regclass(?) AS relation",
                ("model_ready.forecast_training_view",),
            )
            dealroom_avm = installed_contracts.get(
                "model_ready.valuation_view"
            )
            listing_property_avm = installed_contracts.get(
                "model_ready.listing_property_valuation_view"
            )
            listing_property_relation = self.client.query_one(
                "SELECT to_regclass(?) AS relation",
                ("model_ready.listing_property_valuation_view",),
            )
            liquidity = installed_contracts.get(
                "model_ready.avm_liquidity_training_view"
            )
            if (
                not forecast
                or forecast.get("view_version") != "forecast-training-view-v2"
                or forecast.get("contract_state") != "ACTIVE"
                or forecast.get("training_enabled") is not True
                or forecast.get("installer_sha256") != digest
                or not relation
                or relation.get("relation") is None
            ):
                raise ModelReadyViewInstallError(
                    "installed forecast view did not satisfy the registered contract"
                )
            if (
                not dealroom_avm
                or dealroom_avm.get("view_version") != "valuation-view-v1"
                or dealroom_avm.get("contract_state") != "BLOCKED"
                or dealroom_avm.get("training_enabled") is not False
                or dealroom_avm.get("blocked_reason")
                != "MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING"
                or dealroom_avm.get("installer_sha256") != digest
            ):
                raise ModelReadyViewInstallError(
                    "DealRoom AVM contract did not remain fail-closed"
                )
            if preflight.official_outcomes_ready:
                if (
                    not listing_property_avm
                    or listing_property_avm.get("view_version")
                    != "listing-property-valuation-view-v1"
                    or listing_property_avm.get("contract_state") != "ACTIVE"
                    or listing_property_avm.get("training_enabled") is not True
                    or listing_property_avm.get("installer_sha256") != digest
                    or not listing_property_relation
                    or listing_property_relation.get("relation") is None
                ):
                    raise ModelReadyViewInstallError(
                        "listing-property valuation view did not satisfy the "
                        "registered contract"
                    )
            elif (
                not listing_property_avm
                or listing_property_avm.get("contract_state") != "BLOCKED"
                or listing_property_avm.get("training_enabled") is not False
                or listing_property_avm.get("blocked_reason")
                != "OFFICIAL_REAL_ESTATE_OUTCOME_RELATION_MISSING"
                or listing_property_avm.get("installer_sha256") != digest
                or (
                    listing_property_relation
                    and listing_property_relation.get("relation") is not None
                )
            ):
                raise ModelReadyViewInstallError(
                    "missing optional official outcomes did not remain fail-closed"
                )
            if (
                not liquidity
                or liquidity.get("contract_state") != "BLOCKED"
                or liquidity.get("training_enabled") is not False
                or liquidity.get("blocked_reason")
                != "OFFICIAL_SALE_OUTCOME_HAS_NO_MARKETING_INTERVAL"
                or liquidity.get("installer_sha256") != digest
            ):
                raise ModelReadyViewInstallError(
                    "AVM liquidity contract did not remain fail-closed"
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
            "forecast": dict(forecast),
            "dealroom_avm": dict(dealroom_avm),
            "listing_property_avm": dict(listing_property_avm),
            "avm_liquidity": dict(liquidity),
            "optional_outcome_models_trainable": {
                "avm": False,
                "listing_property_avm": preflight.official_outcomes_ready,
                "sitescore": False,
                "heatzone": False,
                "avm-liquidity": False,
            },
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
        "CREATE OR REPLACE VIEW model_ready.listing_property_valuation_view",
        "FROM external_data.real_estate_transactions AS outcome",
        "external_data.real_estate_ingestion_runs AS ingestion",
        "'listing-property-valuation-view-v1'::text AS view_version",
        "total_price_twd::double precision AS realized_transaction_price",
        "'MATURE_REALIZED_TRANSACTION_OUTCOME_RELATION_MISSING'",
        "'OFFICIAL_SALE_OUTCOME_HAS_NO_MARKETING_INTERVAL'",
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
            "model-ready SQL contains prohibited row-generation constructs: " + ", ".join(found)
        )
    if "create or replace view model_ready.valuation_view" in lowered:
        raise ModelReadyViewInstallError(
            "official sale outcomes must not replace the DealRoom AVM relation"
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
            database_url = require_production_database_url(os.getenv("ODAY_DATABASE_URL", ""))
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
