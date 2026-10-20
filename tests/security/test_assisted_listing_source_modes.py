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


@pytest.mark.parametrize(
    ("source_id", "stored_source", "provider_id", "stored_provider_id", "same_url", "same_content", "outcome"),
    [
        pytest.param("SRC-SYNTHETIC", "SRC-591", "synthetic-2024", "s591-2024", False, False,
                     "POSSIBLE_MATCH", id="no-synthetic-provider-alias"),
        pytest.param("SRC-SYNTHETIC", "SRC-591", "2024", "2024", False, False,
                     "POSSIBLE_MATCH", id="cross-source-equal-key"),
        pytest.param("SRC-SYNTHETIC", "SRC-591", "incoming", "stored", True, True,
                     "POSSIBLE_MATCH", id="cross-source-equal-url-and-content"),
        pytest.param("", "", "2024", "2024", False, False,
                     "POSSIBLE_MATCH", id="missing-source-is-not-an-identity"),
        pytest.param("SRC-OTHER", "SRC-591", "2024", "2024", False, False,
                     "POSSIBLE_MATCH", id="ordinary-cross-source-control"),
        pytest.param("SRC-591", "SRC-591", "2024", "2024", False, False,
                     "REVISION", id="same-source-provider-revision"),
        pytest.param("SRC-591", "SRC-591", "incoming", "stored", True, False,
                     "REVISION", id="same-source-url-revision"),
        pytest.param("SRC-591", "SRC-591", "2024", "2024", False, True,
                     "EXACT_DUPLICATE", id="same-source-unchanged-content"),
    ],
)
def test_exact_identity_requires_a_nonempty_equal_source(
    source_id, stored_source, provider_id, stored_provider_id, same_url, same_content, outcome,
) -> None:
    url = "https://identity-contract.example/listings/incoming"
    values = {"providerListingId": provider_id, "address": "台北市信義區松仁路 96 號",
              "rent": 55000, "areaPing": 18, "floor": "1F", "listingType": "店面"}
    stored = {"id": "L-SOURCE-BOUNDARY", "sourceId": stored_source,
              "sourceListingId": stored_provider_id,
              "canonicalUrl": url if same_url else "https://identity-contract.example/listings/stored",
              "contentFingerprint": "same-content" if same_content else "previous-content",
              "address": values["address"], "rentPerMonth": 58000,
              "areaPing": 18, "floor": "1F", "listingType": "店面"}

    result = assisted_intake.match_listing(
        values=values, canonical_url=url, source_id=source_id,
        fingerprint="same-content", listings=[stored],
    )

    assert result.outcome == outcome
    assert result.target_listing_id == stored["id"]
    if outcome == "POSSIBLE_MATCH":
        assert result.intake_stage == "NEEDS_REVIEW"


@pytest.mark.parametrize("identity", ["url", "provider", "split"])
@pytest.mark.parametrize("same_content", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_multiple_source_identity_matches_require_review_with_every_candidate(identity, same_content, reverse):
    url = "https://identity-contract.example/listings/incoming"
    values = {"providerListingId": "incoming", "address": "台北市測試路123號",
              "rent": 55000, "areaPing": 18, "floor": "1F", "listingType": "店面"}
    common = {"sourceId": "SRC-591", "contentFingerprint": "current" if same_content else "previous",
              "address": values["address"], "rentPerMonth": values["rent"],
              "areaPing": 18, "floor": "1F", "listingType": "店面"}
    listings = [
        {**common, "id": "L-A", "sourceListingId": "incoming" if identity != "url" else "other-a",
         "canonicalUrl": url if identity != "provider" else url + "-a"},
        {**common, "id": "L-B", "sourceListingId": "incoming" if identity != "url" else "other-b",
         "canonicalUrl": url if identity == "url" else url + "-b"},
    ]
    if identity == "split":
        listings[0]["sourceListingId"] = "other-a"
    if reverse:
        listings.reverse()
    result = assisted_intake.match_listing(values=values, canonical_url=url, source_id="SRC-591",
        fingerprint="current", listings=listings)
    assert result.outcome == "POSSIBLE_MATCH"
    assert result.intake_stage == "NEEDS_REVIEW"
    assert result.target_listing_id == "L-A"
    assert result.candidate_listing_ids == ("L-A", "L-B")
    assert all(signal.key == "sourceIdentityAmbiguous" and not signal.agrees for signal in result.signals)
    assert result.signals and all(candidate in result.signals[0].detail for candidate in result.candidate_listing_ids)
