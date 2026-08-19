from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from models.shared_ml.model_ready_receipt import (
    ModelReadyReceiptError,
    compute_receipt_sha256,
    load_model_ready_receipt,
)
from product_ops.modeling.model_ready_receipt import build_receipt, write_receipt
from product_ops.modeling.storage import ModelReadyInventory


class _InventorySource:
    def inventory(self, spec):
        labeled = 1303 if spec.key == "forecastops" else 0
        return ModelReadyInventory(
            model_key=spec.key,
            relation=spec.relation,
            contract_registry_exists=True,
            contract_version=spec.expected_view_version,
            contract_trainable=True,
            blocked_reason=None,
            relation_exists=True,
            available_columns=spec.required_columns,
            missing_columns=(),
            eligible_row_count=labeled,
            labeled_row_count=labeled,
            temporal_min=None,
            temporal_max=None,
        )


def test_checked_in_receipt_is_complete_and_integrity_checked() -> None:
    receipt = load_model_ready_receipt()
    assert receipt["auto_seeded"] is False
    assert set(receipt["capabilities"]) == {
        "forecastops",
        "avm",
        "sitescore",
        "heatzone",
    }
    assert receipt["capabilities"]["avm"]["observed_count"] == 0


def test_generator_observes_inventory_and_emits_reproducible_hash() -> None:
    receipt = build_receipt(
        _InventorySource(),
        inventory_version="pg16-test-v1",
        observed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    assert receipt["capabilities"]["forecastops"]["eligible_count"] == 1303
    assert receipt["capabilities"]["sitescore"]["observed_count"] == 0
    assert receipt["integrity"]["content_sha256"] == compute_receipt_sha256(receipt)


def test_tampered_or_auto_seeded_receipt_fails_closed(tmp_path) -> None:
    receipt = build_receipt(
        _InventorySource(),
        inventory_version="pg16-test-v1",
        observed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    receipt["capabilities"]["avm"]["observed_count"] = 1
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ModelReadyReceiptError, match="integrity hash mismatch"):
        load_model_ready_receipt(path)

    receipt["integrity"]["content_sha256"] = compute_receipt_sha256(receipt)
    receipt["auto_seeded"] = True
    receipt["integrity"]["content_sha256"] = compute_receipt_sha256(receipt)
    path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ModelReadyReceiptError, match="auto_seeded"):
        load_model_ready_receipt(path)


def test_write_receipt_will_not_replace_immutable_evidence(tmp_path) -> None:
    receipt = build_receipt(
        _InventorySource(),
        inventory_version="pg16-test-v1",
        observed_at=datetime(2026, 7, 25, tzinfo=UTC),
    )
    path = tmp_path / "receipt.json"
    write_receipt(receipt, path)
    write_receipt(receipt, path)
    changed = dict(receipt, inventory_version="pg16-test-v2")
    with pytest.raises(FileExistsError):
        write_receipt(changed, path)
