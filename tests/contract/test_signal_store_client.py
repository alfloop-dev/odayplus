"""Executable checks for the research-to-execution signal contract."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

CLIENT_PATH = Path(__file__).parents[2] / "services" / "signal-store" / "client.py"


def _contract() -> dict[str, object]:
    # The service directory intentionally is not a Python package. Loading the
    # contract by path proves it remains usable without inventing package wiring.
    return runpy.run_path(str(CLIENT_PATH))


def test_example_signal_envelope_is_json_round_trip_safe() -> None:
    contract = _contract()
    example = contract["EXAMPLE_SIGNAL_PAYLOAD"]

    encoded = json.dumps(example, allow_nan=False)

    assert json.loads(encoded) == example
    assert example["signal_version"] == "1.0.0"
    assert example["signal_type"].endswith(".v1")
    assert example["tenant_id"]
    assert example["idempotency_key"]


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
