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
    ("mutation", "expected_path", "expected_validator"),
    [
        (lambda envelope: envelope.pop("tenant_id"), [], "required"),
        (
            lambda envelope: envelope.update(signal_version="2.0.0"),
            ["signal_version"],
            "pattern",
        ),
        (lambda envelope: envelope["subject"].pop("entity_id"), ["subject"], "required"),
        (
                lambda envelope: envelope.update(produced_at="2026-06-26T10:00:00"),
                ["produced_at"],
                "format",
        ),
        (
            lambda envelope: envelope.update(unversioned_field=True),
            [],
            "additionalProperties",
        ),
    ],
)
def test_schema_rejects_invalid_or_unsupported_envelopes(
    mutation, expected_path, expected_validator
) -> None:
    contract = _contract()
    invalid = deepcopy(contract["EXAMPLE_SIGNAL_PAYLOAD"])
    mutation(invalid)

    with pytest.raises(ValidationError) as exc_info:
        _validator(contract).validate(invalid)

    assert list(exc_info.value.absolute_path)[: len(expected_path)] == expected_path
    assert exc_info.value.validator == expected_validator


def test_v1_payload_allows_additive_fields_for_backward_compatibility() -> None:
    contract = _contract()
    additive = deepcopy(contract["EXAMPLE_SIGNAL_PAYLOAD"])
    additive["signal_version"] = "1.1.0"
    additive["payload"]["future_optional_field"] = {"introduced_in": "1.1.0"}
    additive["payload"]["decision"]["decision_channel"] = "automated"
    additive["payload"]["evidence"]["drift_score"] = 0.02

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


def test_signal_schema_documents_all_payload_and_envelope_keys() -> None:
    contract = _contract()
    schema_path = contract["SIGNAL_SCHEMA_PATH"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema.get("description"), "Top-level schema must have a description"

    # All top-level properties must have descriptions
    for prop_name, prop_def in schema["properties"].items():
        assert prop_def.get("description"), f"Property '{prop_name}' must have a description"

    # All payload properties must have descriptions
    payload_props = schema["$defs"]["payload"]["properties"]
    for payload_key, payload_def in payload_props.items():
        assert payload_def.get("description"), f"Payload key '{payload_key}' must have a description"

    # All nested object properties in $defs must have descriptions
    for def_name, def_body in schema["$defs"].items():
        assert def_body.get("description"), f"Definition '{def_name}' must have a description"
        if "properties" in def_body:
            for sub_key, sub_def in def_body["properties"].items():
                assert sub_def.get("description"), (
                    f"Property '{sub_key}' in definition '{def_name}' must have a description"
                )


def test_worker_contracts_reference_same_canonical_schema() -> None:
    contract = _contract()
    canonical_schema_path = contract["SIGNAL_SCHEMA_PATH"].resolve()

    # LEAN execution runtime consumer
    lean_consumer_path = Path(__file__).parents[2] / "services" / "execution" / "lean-runtime" / "consumer.py"
    lean_contract = runpy.run_path(str(lean_consumer_path))
    assert lean_contract["SIGNAL_SCHEMA_PATH"].resolve() == canonical_schema_path

    # Control-plane router
    router_path = Path(__file__).parents[2] / "services" / "control-plane" / "router" / "contract.py"
    router_contract = runpy.run_path(str(router_path))
    assert router_contract["SIGNAL_SCHEMA_PATH"].resolve() == canonical_schema_path

