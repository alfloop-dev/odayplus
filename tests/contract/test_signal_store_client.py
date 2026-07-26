"""Executable checks for the research-to-execution signal contract."""

from __future__ import annotations

import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

CLIENT_PATH = Path(__file__).parents[2] / "services" / "signal-store" / "client.py"


def _contract() -> dict[str, object]:
    # The service directory intentionally is not a Python package. Loading the
    # contract by path proves it remains usable without inventing package wiring.
    return runpy.run_path(str(CLIENT_PATH))


def _validator(contract: dict[str, object]) -> Draft202012Validator:
    schema_path = contract["SIGNAL_SCHEMA_PATH"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$id"] == contract["SIGNAL_SCHEMA_ID"]
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_example_signal_envelope_is_json_round_trip_safe() -> None:
    contract = _contract()
    example = contract["EXAMPLE_SIGNAL_PAYLOAD"]

    encoded = json.dumps(example, allow_nan=False)

    assert json.loads(encoded) == example
    assert example["signal_version"] == "1.0.0"
    assert example["signal_type"].endswith(".v1")
    assert example["tenant_id"]
    assert example["idempotency_key"]


def test_producer_and_worker_share_canonical_versioned_schema() -> None:
    contract = _contract()
    producer_validator = _validator(contract)
    worker_validator = _validator(contract)

    producer_validator.validate(contract["EXAMPLE_SIGNAL_PAYLOAD"])
    worker_validator.validate(
        json.loads(json.dumps(contract["EXAMPLE_SIGNAL_PAYLOAD"], allow_nan=False))
    )

    assert contract["SIGNAL_SCHEMA_VERSION"] == "1.0.0"


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        (lambda envelope: envelope.pop("tenant_id"), []),
        (lambda envelope: envelope.update(signal_version="2.0.0"), ["signal_version"]),
        (lambda envelope: envelope["subject"].pop("entity_id"), ["subject"]),
        (lambda envelope: envelope.update(produced_at="2026-06-26T10:00:00"), ["produced_at"]),
        (lambda envelope: envelope.update(unversioned_field=True), []),
    ],
)
def test_schema_rejects_invalid_or_unsupported_envelopes(mutation, expected_path) -> None:
    contract = _contract()
    invalid = deepcopy(contract["EXAMPLE_SIGNAL_PAYLOAD"])
    mutation(invalid)

    with pytest.raises(ValidationError) as exc_info:
        _validator(contract).validate(invalid)

    assert list(exc_info.value.absolute_path)[: len(expected_path)] == expected_path


def test_v1_payload_allows_additive_fields_for_backward_compatibility() -> None:
    contract = _contract()
    additive = deepcopy(contract["EXAMPLE_SIGNAL_PAYLOAD"])
    additive["payload"]["future_optional_field"] = {"introduced_in": "1.1.0"}

    _validator(contract).validate(additive)


def test_protocol_is_runtime_checkable() -> None:
    contract = _contract()
    signal_store_client = contract["SignalStoreClient"]

    class CompleteClient:
        def put_signal(self, envelope: object) -> object: ...

        def get_signal(self, signal_id: str, *, tenant_id: str) -> object: ...

        def list_signals(self, query: object) -> object: ...

        def lease_pending(self, **kwargs: object) -> object: ...

        def mark_consumed(self, signal_id: str, **kwargs: object) -> object: ...

        def reject_signal(self, signal_id: str, **kwargs: object) -> object: ...

    assert isinstance(CompleteClient(), signal_store_client)
    assert not isinstance(object(), signal_store_client)


def test_consumer_assumptions_cover_delivery_and_safety_boundaries() -> None:
    assumptions = " ".join(_contract()["CONSUMER_ASSUMPTIONS"])

    for boundary in (
        "tenant-scoped",
        "unsupported signal_version",
        "effective_at",
        "expires_at",
        "at least once",
        "active lease owner",
        "idempotency_key",
        "Unknown additive payload fields",
    ):
        assert boundary in assumptions
