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


def consumer(
    handler=lambda envelope: f"lean:{envelope['signal_id']}",
    now=datetime(2026, 6, 26, 4, tzinfo=UTC),
):
    return runtime.LeanSignalConsumer(
        handler=handler,
        receipts=runtime.InMemoryReceiptStore(),
        now=lambda: now,
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


def test_unknown_receipt_claim_state_fails_closed_before_handler() -> None:
    class InvalidClaimStore(runtime.InMemoryReceiptStore):
        def claim(self, **_kwargs):
            return None

    calls = 0

    def handle(_envelope):
        nonlocal calls
        calls += 1
        return "execution:42"

    message = Message(signal())
    instance = runtime.LeanSignalConsumer(
        handler=handle,
        receipts=InvalidClaimStore(),
        now=lambda: datetime(2026, 6, 26, 4, tzinfo=UTC),
    )

    assert instance.consume(message) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert message.rejection == (True, "invalid_receipt_claim_state")
    assert message.acked is False
    assert calls == 0


def test_broker_ack_failure_is_not_reported_as_receipt_completion_failure() -> None:
    class FailingAckMessage(Message):
        def ack(self) -> None:
            raise OSError("broker unavailable")

    message = FailingAckMessage(signal())

    assert consumer().consume(message) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert message.rejection == (True, "broker_ack_failure: OSError")


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


def test_expired_signal_is_rejected_without_retry() -> None:
    message = Message(signal())

    outcome = consumer(now=datetime(2026, 6, 28, 4, tzinfo=UTC)).consume(message)

    assert outcome == runtime.ConsumptionOutcome.REJECTED
    assert message.rejection == (False, "signal_expired")
    assert message.acked is False


def test_additive_payload_fields_stay_consumable_within_major_one() -> None:
    # Schema evolution policy: 1.x consumers must accept additive payload keys.
    value = signal()
    value["signal_version"] = "1.1.0"
    value["payload"]["future_optional_field"] = {"introduced_in": "1.1.0"}
    value["payload"]["evidence"]["drift_score"] = 0.02
    message = Message(value)

    assert consumer().consume(message) == runtime.ConsumptionOutcome.CONSUMED
    assert message.acked is True


def test_undecodable_body_is_rejected_without_retry() -> None:
    message = Message(None)
    message.body = b"{not json"

    assert consumer().consume(message) == runtime.ConsumptionOutcome.REJECTED
    assert message.rejection and message.rejection[0] is False


def test_handler_failure_releases_claim_so_a_later_retry_can_execute() -> None:
    attempts = 0

    def flaky(_envelope):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("execution venue unavailable")
        return "execution:42"

    instance = consumer(flaky)
    failed = Message(signal())
    retried = Message(signal())

    assert instance.consume(failed) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    # The released claim must not permanently poison the idempotency key.
    assert instance.consume(retried) == runtime.ConsumptionOutcome.CONSUMED
    assert attempts == 2
    assert retried.acked is True


def test_idempotency_key_reused_by_another_signal_never_executes() -> None:
    calls = 0

    def handle(_envelope):
        nonlocal calls
        calls += 1
        return "execution:42"

    instance = consumer(handle)
    assert instance.consume(Message(signal())) == runtime.ConsumptionOutcome.CONSUMED

    colliding = signal()
    colliding["signal_id"] = "sig_01J2SITE000000000000000002"
    message = Message(colliding)

    assert instance.consume(message) == runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    assert message.rejection == (True, "receipt_claim_failure: ValueError")
    assert message.acked is False
    assert calls == 1


def test_empty_handler_result_reference_is_not_recorded_as_consumed() -> None:
    message = Message(signal())

    assert consumer(lambda _envelope: "").consume(message) == (
        runtime.ConsumptionOutcome.RETRYABLE_FAILURE
    )
    assert message.acked is False


BROKER_ENV = {
    "LEAN_SIGNAL_BROKER_SERVERS": "broker-a:9093, broker-b:9093",
    "LEAN_SIGNAL_TOPIC": "research.signals.v1",
    "LEAN_SIGNAL_CONSUMER_GROUP": "lean-runtime",
}


def test_broker_configuration_is_validated_at_composition_boundary() -> None:
    config = runtime.BrokerConfig.from_mapping(BROKER_ENV)
    assert config.bootstrap_servers == ("broker-a:9093", "broker-b:9093")
    assert config.security_protocol == "SASL_SSL"
    assert config.dead_letter_topic is None

    with pytest.raises(ValueError, match="required"):
        runtime.BrokerConfig.from_mapping({})


@pytest.mark.parametrize(
    "setting",
    ["LEAN_SIGNAL_BROKER_SERVERS", "LEAN_SIGNAL_TOPIC", "LEAN_SIGNAL_CONSUMER_GROUP"],
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_required_broker_setting_fails_startup(setting, blank) -> None:
    with pytest.raises(ValueError, match="required"):
        runtime.BrokerConfig.from_mapping({**BROKER_ENV, setting: blank})


@pytest.mark.parametrize("servers", [" , ", ",", " ,, "])
def test_endpoint_list_of_only_separators_fails_startup(servers) -> None:
    with pytest.raises(ValueError, match="required"):
        runtime.BrokerConfig.from_mapping(
            {**BROKER_ENV, "LEAN_SIGNAL_BROKER_SERVERS": servers}
        )


def test_blank_endpoints_are_dropped_and_survivors_kept() -> None:
    config = runtime.BrokerConfig.from_mapping(
        {**BROKER_ENV, "LEAN_SIGNAL_BROKER_SERVERS": " broker-a:9093 , ,broker-b:9093"}
    )
    assert config.bootstrap_servers == ("broker-a:9093", "broker-b:9093")


@pytest.mark.parametrize("protocol", ["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"])
def test_supported_security_protocols_are_accepted(protocol) -> None:
    config = runtime.BrokerConfig.from_mapping(
        {**BROKER_ENV, "LEAN_SIGNAL_SECURITY_PROTOCOL": protocol}
    )
    assert config.security_protocol == protocol


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_security_protocol_falls_back_to_the_strict_default(blank) -> None:
    # Deployment templates render an unset optional variable as an empty string;
    # that must mean "default", not "unsupported protocol: ".
    config = runtime.BrokerConfig.from_mapping(
        {**BROKER_ENV, "LEAN_SIGNAL_SECURITY_PROTOCOL": blank}
    )
    assert config.security_protocol == "SASL_SSL"


def test_unsupported_security_protocol_fails_startup() -> None:
    with pytest.raises(ValueError, match="unsupported LEAN_SIGNAL_SECURITY_PROTOCOL"):
        runtime.BrokerConfig.from_mapping(
            {**BROKER_ENV, "LEAN_SIGNAL_SECURITY_PROTOCOL": "TELNET"}
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_dead_letter_topic_is_unset_not_a_whitespace_topic(blank) -> None:
    config = runtime.BrokerConfig.from_mapping(
        {**BROKER_ENV, "LEAN_SIGNAL_DEAD_LETTER_TOPIC": blank}
    )
    assert config.dead_letter_topic is None


def test_dead_letter_topic_equal_to_input_topic_fails_startup() -> None:
    # Otherwise every permanent rejection is republished to the input topic and
    # redelivered forever.
    with pytest.raises(ValueError, match="must differ from LEAN_SIGNAL_TOPIC"):
        runtime.BrokerConfig.from_mapping(
            {**BROKER_ENV, "LEAN_SIGNAL_DEAD_LETTER_TOPIC": BROKER_ENV["LEAN_SIGNAL_TOPIC"]}
        )


def test_distinct_dead_letter_topic_is_stripped_and_retained() -> None:
    config = runtime.BrokerConfig.from_mapping(
        {**BROKER_ENV, "LEAN_SIGNAL_DEAD_LETTER_TOPIC": " research.signals.dlq "}
    )
    assert config.dead_letter_topic == "research.signals.dlq"
