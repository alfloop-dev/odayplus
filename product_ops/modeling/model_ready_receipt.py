"""Generate a release-bound receipt from the persisted model-ready inventory."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models.model_ready.contracts import MODEL_SPECS, require_production_database_url
from models.model_ready.storage import ModelReadySource, PostgresModelReadySource
from models.shared_ml.model_ready_receipt import (
    RECEIPT_KIND,
    RECEIPT_PATH,
    RECEIPT_SCHEMA_VERSION,
    compute_receipt_sha256,
)

PRODUCTION_CAPABILITIES = ("forecastops", "avm", "sitescore", "heatzone")


def build_receipt(
    source: ModelReadySource,
    *,
    inventory_version: str,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Observe all production capabilities and construct an integrity envelope."""
    if not inventory_version.strip():
        raise ValueError("inventory_version is required")
    observation = observed_at or datetime.now(UTC)
    if observation.tzinfo is None or observation.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    observation = observation.astimezone(UTC)

    capabilities: dict[str, dict[str, Any]] = {}
    for service in PRODUCTION_CAPABILITIES:
        spec = MODEL_SPECS[service]
        inventory = source.inventory(spec)
        if not inventory.contract_registry_exists:
            raise RuntimeError(
                f"{service}: persisted model_ready.view_contracts is unavailable"
            )
        if not inventory.contract_version:
            raise RuntimeError(f"{service}: persisted view version is unavailable")
        capabilities[service] = {
            "relation": inventory.relation,
            "view_version": inventory.contract_version,
            "observed_count": inventory.labeled_row_count,
            "eligible_count": inventory.eligible_row_count,
        }

    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": RECEIPT_KIND,
        "inventory_version": inventory_version.strip(),
        "observed_at": observation.isoformat().replace("+00:00", "Z"),
        "auto_seeded": False,
        "capabilities": capabilities,
    }
    receipt["integrity"] = {"content_sha256": compute_receipt_sha256(receipt)}
    return receipt


def write_receipt(receipt: dict[str, Any], path: Path) -> None:
    """Write canonical JSON without silently replacing an unlike receipt."""
    encoded = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != encoded:
        raise FileExistsError(
            f"{path} already contains a different immutable inventory receipt"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate",))
    parser.add_argument("--inventory-version", required=True)
    parser.add_argument("--output", type=Path, default=RECEIPT_PATH)
    args = parser.parse_args(argv)

    database_url = os.getenv("ODAY_DATABASE_URL", "").strip()
    require_production_database_url(database_url)
    source = PostgresModelReadySource.from_database_url(database_url)
    write_receipt(
        build_receipt(source, inventory_version=args.inventory_version),
        args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
