from __future__ import annotations

import pytest

from modules.external_data.application import assisted_intake
from modules.external_data.security.assisted_listing_retrieval import RetrievalSecurityGate

SYNTHETIC_URL = "https://www.synthetic.example/detail-77120345.html"
LIVE_ENV = (
    ("ODP_PRODUCT_MODE", "production"),
    ("ODP_REQUIRE_LIVE_DATA", "true"),
    ("ODP_EXTERNAL_PROVIDER_MODE", "live"),
)


def _clear_runtime_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, _ in LIVE_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize(("env_name", "env_value"), LIVE_ENV)
def test_synthetic_source_and_corpus_fail_closed_in_live_modes(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    _clear_runtime_mode(monkeypatch)
    monkeypatch.setenv(env_name, env_value)

    policy = assisted_intake.resolve_source_policy(SYNTHETIC_URL)
    result = assisted_intake.retrieve(SYNTHETIC_URL, policy=policy)

    assert policy.source_id == "SRC-SYNTHETIC"
    assert policy.policy == "SOURCE_BLOCKED"
    assert policy.may_retrieve is False
    assert policy.quarantines is True
    assert policy.failure_code == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_CODE
    assert policy.next_action == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_NEXT_ACTION
    assert result.ok is False
    assert result.raw == {}
    assert result.failure is not None
    assert result.failure.code == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_CODE
    assert result.failure.next_action == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_NEXT_ACTION
    assert result.failure.retryable is False


@pytest.mark.parametrize("product_mode", ["poc", "test", "local"])
def test_synthetic_corpus_remains_available_outside_live_mode(
    monkeypatch: pytest.MonkeyPatch,
    product_mode: str,
) -> None:
    _clear_runtime_mode(monkeypatch)
    monkeypatch.setenv("ODP_PRODUCT_MODE", product_mode)

    policy = assisted_intake.resolve_source_policy(SYNTHETIC_URL)
    result = assisted_intake.retrieve(SYNTHETIC_URL, policy=policy)

    assert policy.policy == "APPROVED_RETRIEVAL"
    assert policy.may_retrieve is True
    assert result.ok is True
    assert result.snapshot_id == "SNAP-SYNTHETIC-77120345"


@pytest.mark.parametrize(("env_name", "env_value"), LIVE_ENV)
def test_fixture_replay_gate_fails_before_fetch_in_live_modes(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    _clear_runtime_mode(monkeypatch)
    monkeypatch.setenv(env_name, env_value)
    calls: list[str] = []

    def unexpected_fetch(url: str, **_kwargs: object) -> object:
        calls.append(url)
        raise AssertionError("live fixture replay must fail before fetching")

    gate = RetrievalSecurityGate(fetcher=unexpected_fetch)
    result = gate.fetch(
        SYNTHETIC_URL,
        policy="APPROVED_RETRIEVAL",
        retrieval_method="fixture_replay",
    )

    assert result.failure is not None
    assert result.failure.code == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_CODE
    assert result.failure.next_action == assisted_intake.SYNTHETIC_FIXTURE_BLOCKED_NEXT_ACTION
    assert result.failure.retryable is False
    assert calls == []


def test_default_server_http_fetcher_never_falls_back_to_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_runtime_mode(monkeypatch)

    def fail_if_corpus_is_used(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("server_http must not call fixture retrieve()")

    monkeypatch.setattr(assisted_intake, "retrieve", fail_if_corpus_is_used)
    gate = RetrievalSecurityGate(resolver=lambda _host: ("93.184.216.34",))

    result = gate.fetch(SYNTHETIC_URL, policy="APPROVED_RETRIEVAL")

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-ADAPTER-MISSING"
    assert "approved live adapter" in result.failure.next_action


@pytest.mark.parametrize(
    "url",
    [
        "https://www.591.com.tw/rent-detail-12345.html",
        "https://www.rakuya.com.tw/sell?id=RK-123",
    ],
)
def test_assisted_entry_only_sources_are_unchanged_in_production(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    _clear_runtime_mode(monkeypatch)
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")

    policy = assisted_intake.resolve_source_policy(url)

    assert policy.policy == "ASSISTED_ENTRY_ONLY"
    assert policy.may_retrieve is False
    assert policy.quarantines is False
