"""Load and verify the release-bound PG16 model-ready inventory receipt."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shared.integrity import compute_content_sha256

RECEIPT_SCHEMA_VERSION = 1
RECEIPT_KIND = "pg16-model-ready-inventory-receipt"
RECEIPT_PATH = Path(__file__).with_name("model_ready_inventory_receipt.json")

_ISO_UTC = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
_CAPABILITY_COUNT_FIELDS = ("observed_count", "eligible_count")
_CAPABILITY_TEXT_FIELDS = ("relation", "view_version")


class ModelReadyReceiptError(RuntimeError):
    """Raised when the model-ready inventory receipt cannot be trusted."""


def compute_receipt_sha256(body: dict[str, Any]) -> str:
    """Return the canonical content hash, excluding the integrity envelope."""
    return compute_content_sha256(body)


def load_model_ready_receipt(path: Path = RECEIPT_PATH) -> dict[str, Any]:
    """Load a structurally complete, non-synthetic, integrity-checked receipt."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelReadyReceiptError(
            f"model-ready inventory receipt is missing at {path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelReadyReceiptError(
            f"model-ready inventory receipt at {path} is unreadable: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ModelReadyReceiptError(
            "model-ready inventory receipt must be a JSON object"
        )
    if payload.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ModelReadyReceiptError(
            f"unsupported receipt schema_version={payload.get('schema_version')!r}"
        )
    if payload.get("kind") != RECEIPT_KIND:
        raise ModelReadyReceiptError(f"unexpected receipt kind={payload.get('kind')!r}")

    inventory_version = payload.get("inventory_version")
    if not isinstance(inventory_version, str) or not inventory_version.strip():
        raise ModelReadyReceiptError("receipt inventory_version is missing")
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str) or not _ISO_UTC.fullmatch(observed_at):
        raise ModelReadyReceiptError(
            f"receipt observed_at must be an ISO-8601 UTC timestamp, got {observed_at!r}"
        )
    if payload.get("auto_seeded") is not False:
        raise ModelReadyReceiptError(
            "receipt auto_seeded must be exactly false; synthetic seeding is forbidden"
        )

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        raise ModelReadyReceiptError("receipt capabilities must be a non-empty object")
    for service, capability in capabilities.items():
        if not isinstance(capability, dict):
            raise ModelReadyReceiptError(
                f"receipt capability {service!r} must be an object"
            )
        for count_field in _CAPABILITY_COUNT_FIELDS:
            value = capability.get(count_field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ModelReadyReceiptError(
                    f"receipt capability {service!r} field {count_field} "
                    f"must be a non-negative integer, got {value!r}"
                )
        for text_field in _CAPABILITY_TEXT_FIELDS:
            value = capability.get(text_field)
            if not isinstance(value, str) or not value.strip():
                raise ModelReadyReceiptError(
                    f"receipt capability {service!r} field {text_field} is missing"
                )

    integrity = payload.get("integrity")
    declared = (
        integrity.get("content_sha256") if isinstance(integrity, dict) else None
    )
    actual = compute_receipt_sha256(payload)
    if declared != actual:
        raise ModelReadyReceiptError(
            "receipt integrity hash mismatch: the receipt was edited after generation "
            f"(declared={declared!r})"
        )
    return payload


__all__ = [
    "RECEIPT_KIND",
    "RECEIPT_PATH",
    "RECEIPT_SCHEMA_VERSION",
    "ModelReadyReceiptError",
    "compute_receipt_sha256",
    "load_model_ready_receipt",
]
