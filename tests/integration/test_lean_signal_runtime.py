from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RUNTIME_PATH = ROOT / "services" / "execution" / "lean-runtime" / "consumer.py"
CLIENT_PATH = ROOT / "services" / "signal-store" / "client.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load_module("lean_signal_consumer", RUNTIME_PATH)
contract = load_module("signal_store_contract_for_lean", CLIENT_PATH)


class Message:
    def __init__(self, envelope: object) -> None:
        self.body = json.dumps(envelope).encode()
        self.acked = False
        self.rejection: tuple[bool, str] | None = None

    def ack(self) -> None:
        self.acked = True

    def reject(self, *, retryable: bool, reason: str) -> None:
        self.rejection = (retryable, reason)


def consumer(handler=lambda envelope: f"lean:{envelope['signal_id']}"):
    return runtime.LeanSignalConsumer(
        handler=handler,
        receipts=runtime.InMemoryReceiptStore(),
        now=lambda: datetime(2026, 6, 26, 4, tzinfo=UTC),
    )


def signal() -> dict:
    value = deepcopy(contract.EXAMPLE_SIGNAL_PAYLOAD)
    value["effective_at"] = "2026-06-26T03:00:00Z"
    value["expires_at"] = "2026-06-27T03:00:00Z"
    return value


def test_consumes_valid_signal_and_acknowledges_after_handler() -> None:
    seen = []
    message = Message(signal())

    outcome = consumer(lambda envelope: seen.append(envelope["signal_id"]) or "job:1").consume(
        message
    )

    assert outcome == runtime.ConsumptionOutcome.CONSUMED
    assert seen == [signal()["signal_id"]]
    assert message.acked is True
    assert message.rejection is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(signal_version="2.0.0"),
        lambda value: value.pop("tenant_id"),
        lambda value: value.update(unknown_envelope_field=True),
    ],
)
def test_invalid_or_unsupported_signal_fails_closed(mutation) -> None:
    value = signal()
    mutation(value)
    message = Message(value)

    outcome = consumer().consume(message)

    assert outcome == runtime.ConsumptionOutcome.REJECTED
    assert message.acked is False
    assert message.rejection and message.rejection[0] is False
    assert message.rejection[1].startswith("invalid_signal:")


def test_replay_uses_durable_receipt_without_repeating_side_effect() -> None:
    calls = 0

    def handle(_envelope):
        nonlocal calls
        calls += 1
        return "execution:42"

    instance = consumer(handle)
    first = Message(signal())
    replay = Message(signal())

    assert instance.consume(first) == runtime.ConsumptionOutcome.CONSUMED
    assert instance.consume(replay) == runtime.ConsumptionOutcome.REPLAYED
    assert calls == 1
    assert first.acked and replay.acked


def test_receipt_completion_failure_cannot_repeat_execution_side_effect() -> None:
    class FailingCompletionStore(runtime.InMemoryReceiptStore):
        def complete(self, **_kwargs) -> None:
            raise OSError("receipt backend unavailable")

    calls = 0

    def handle(_envelope):
        nonlocal calls
        calls += 1
        return "execution:42"

    instance = runtime.LeanSignalConsumer(
        handler=handle,
        receipts=FailingCompletionStore(),
        now=lambda: datetime(2026, 6, 26, 4, tzinfo=UTC),
    )
    first = Message(signal())
    redelivery = Message(signal())

    assert instance.consume(first) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert first.rejection == (True, "receipt_completion_failure: OSError")
    assert instance.consume(redelivery) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert redelivery.rejection == (True, "execution_in_doubt")
    assert calls == 1
    assert not first.acked and not redelivery.acked


def test_time_window_and_handler_failures_have_explicit_retry_semantics() -> None:
    future = signal()
    future["effective_at"] = "2026-06-26T05:00:00Z"
    deferred = Message(future)
    assert consumer().consume(deferred) == runtime.ConsumptionOutcome.DEFERRED
    assert deferred.rejection == (True, "signal_not_yet_effective")

    failed = Message(signal())

    def fail(_envelope):
        raise RuntimeError("broker-visible detail must not leak")

    assert consumer(fail).consume(failed) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert failed.rejection == (True, "handler_failure: RuntimeError")


def test_broker_configuration_is_validated_at_composition_boundary() -> None:
    config = runtime.BrokerConfig.from_mapping(
        {
            "LEAN_SIGNAL_BROKER_SERVERS": "broker-a:9093, broker-b:9093",
            "LEAN_SIGNAL_TOPIC": "research.signals.v1",
            "LEAN_SIGNAL_CONSUMER_GROUP": "lean-runtime",
        }
    )
    assert config.bootstrap_servers == ("broker-a:9093", "broker-b:9093")
    assert config.security_protocol == "SASL_SSL"

    with pytest.raises(ValueError, match="required"):
        runtime.BrokerConfig.from_mapping({})
