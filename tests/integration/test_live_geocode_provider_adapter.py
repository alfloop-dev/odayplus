from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from modules.external_data.connectors import ExternalProviderConfigError, ExternalProviderMode
from modules.external_data.connectors.provider_registry import LIVE_MODE_ENV_VAR
from modules.external_data.geo import GeoPipeline
from modules.external_data.providers import (
    GeocodeProviderAuthError,
    GeocodeProviderRateLimitError,
    GeocodeProviderTimeoutError,
    PrimaryGeocodeProvider,
)

INGESTION_TIME = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)


class MockGeocodeClient:
    def __init__(self, *responses: Mapping[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def geocode(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _live_env(**overrides: str) -> dict[str, str]:
    env = {
        LIVE_MODE_ENV_VAR: "live",
        "ODP_GEOCODE_PROVIDER_API_KEY": "geocode-live-secret",
    }
    env.update(overrides)
    return env


def _success_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "request_id": "geo-live-req-001",
        "observed_at": "2026-06-28T11:59:00Z",
        "result": {
            "latitude": 25.026,
            "longitude": 121.543,
            "precision": "address",
            "confidence": 0.94,
            "city": "台北市",
            "district": "大安區",
            "provider_id": "geocode.primary_api",
        },
    }
    payload.update(overrides)
    return payload


def test_fixture_replay_is_default_and_preserves_geocode_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LIVE_MODE_ENV_VAR, raising=False)
    monkeypatch.delenv("ODP_GEOCODE_PROVIDER_API_KEY", raising=False)

    provider = PrimaryGeocodeProvider(correlation_id="corr-geocode-fixture")
    result = GeoPipeline(provider).geocode_record(
        {"address_raw": " 臺北市 信義區 信義路五段7號 3F "},
        as_of=INGESTION_TIME,
    )

    assert provider.mode is ExternalProviderMode.FIXTURE
    assert result.address.normalized_address == "台北市信義區信義路五段7號"
    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.97
    assert result.admin_match_flag is True
    assert result.quality_flags == ()
    assert result.h3_resolution_map[9] == result.address.h3_res_9
    assert result.provider_request_id == "geo-replay-req-001"
    assert result.provider_observed_at == datetime(2026, 6, 28, 11, 58, tzinfo=UTC)


def test_mocked_live_geocoder_success_uses_registry_metadata_and_redacts_secret() -> None:
    client = MockGeocodeClient(_success_payload())
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        retry_budget=2,
        correlation_id="corr-geocode-live",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_precision == "rooftop"
    assert result.address.geocode_confidence == 0.94
    assert result.provider_request_id == "geo-live-req-001"
    assert result.provider_observed_at == datetime(2026, 6, 28, 11, 59, tzinfo=UTC)
    assert client.calls[0]["provider"].source_contract_id == "geocode_result_snapshot"
    assert client.calls[0]["credential"].env_var == "ODP_GEOCODE_PROVIDER_API_KEY"
    assert client.calls[0]["retry_budget"] == 2
    assert "geocode-live-secret" not in repr(client.calls[0]["credential"])
    assert "geocode-live-secret" not in repr(result)


def test_missing_live_geocode_credential_fails_closed_without_secret_values() -> None:
    provider = PrimaryGeocodeProvider(
        client=MockGeocodeClient(_success_payload()),
        env={LIVE_MODE_ENV_VAR: "live"},
        correlation_id="corr-geocode-missing",
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    message = str(exc_info.value)
    assert "correlation_id=corr-geocode-missing" in message
    assert "ODP_GEOCODE_PROVIDER_API_KEY" in message
    assert exc_info.value.result.errors[0].code == "missing_credential"


def test_expired_live_geocode_credential_status_fails_closed_without_secret_values() -> None:
    provider = PrimaryGeocodeProvider(
        client=MockGeocodeClient(_success_payload()),
        env=_live_env(ODP_GEOCODE_PROVIDER_AUTH_STATUS="expired"),
        correlation_id="corr-geocode-expired",
    )

    with pytest.raises(ExternalProviderConfigError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    rendered = repr(exc_info.value.to_dict())
    assert exc_info.value.result.errors[0].code == "credential_expired"
    assert "ODP_GEOCODE_PROVIDER_AUTH_STATUS" in str(exc_info.value)
    assert "geocode-live-secret" not in rendered
    assert "geocode-live-secret" not in str(exc_info.value)


def test_provider_auth_error_from_live_geocoder_fails_closed_without_secret_values() -> None:
    client = MockGeocodeClient(
        GeocodeProviderAuthError(
            "live geocode provider authorization failed",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-auth",
            code="unauthorized",
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-auth",
    )

    with pytest.raises(GeocodeProviderAuthError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    assert "correlation_id=corr-geocode-auth" in str(exc_info.value)
    assert "unauthorized" in str(exc_info.value)
    assert "geocode-live-secret" not in str(exc_info.value)


def test_provider_timeout_from_live_geocoder_fails_closed_without_secret_values() -> None:
    client = MockGeocodeClient(
        GeocodeProviderTimeoutError(
            "live geocode provider request timed out",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-timeout",
            code="timeout",
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-timeout",
    )

    with pytest.raises(GeocodeProviderTimeoutError) as exc_info:
        GeoPipeline(provider).geocode_record({"address_raw": "台北市大安區復興南路二段100號"})

    assert "timeout" in str(exc_info.value)
    assert "geocode-live-secret" not in str(exc_info.value)


def test_rate_limit_retry_budget_retries_then_preserves_success_lineage() -> None:
    client = MockGeocodeClient(
        GeocodeProviderRateLimitError(
            "live geocode provider rate limit reached",
            provider_id="geocode.primary_api",
            correlation_id="corr-geocode-rate",
            code="rate_limited",
        ),
        _success_payload(request_id="geo-live-req-retry"),
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        retry_budget=1,
        correlation_id="corr-geocode-rate",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    import h3

    assert len(client.calls) == 2
    assert result.provider_request_id == "geo-live-req-retry"
    assert result.address.h3_res_9 and h3.is_valid_cell(result.address.h3_res_9)


def test_malformed_live_geocoder_response_sets_quality_flags_without_fabricating_h3() -> None:
    client = MockGeocodeClient(
        {
            "request_id": "geo-live-req-bad",
            "result": {
                "latitude": "not-a-number",
                "longitude": 121.543,
                "confidence": 0.9,
            },
        }
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-bad",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.provider_request_id == "geo-live-req-bad"
    assert result.address.h3_res_9 == ""
    assert "malformed_provider_response" in result.quality_flags
    assert "coordinates_out_of_market" in result.quality_flags
    assert "low_geocode_confidence" in result.quality_flags


def test_out_of_market_live_geocoder_response_preserves_provider_identity_and_flags() -> None:
    client = MockGeocodeClient(
        _success_payload(
            result={
                "latitude": 40.7128,
                "longitude": -74.006,
                "precision": "rooftop",
                "confidence": 0.91,
                "city": "台北市",
                "district": "大安區",
                "provider_id": "geocode.primary_api",
            }
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-oob",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    assert result.geocode_provider == "geocode.primary_api"
    assert result.address.geocode_confidence == 0.91
    assert result.h3_resolution_map == {}
    assert "coordinates_out_of_market" in result.quality_flags


def test_low_confidence_live_geocoder_response_preserves_admin_match_and_precision() -> None:
    client = MockGeocodeClient(
        _success_payload(
            result={
                "latitude": 25.026,
                "longitude": 121.543,
                "precision": "street",
                "confidence": 0.42,
                "city": "台北市",
                "district": "大安區",
                "provider_id": "geocode.primary_api",
            }
        )
    )
    provider = PrimaryGeocodeProvider(
        client=client,
        env=_live_env(),
        correlation_id="corr-geocode-low",
    )

    result = GeoPipeline(provider).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"},
        as_of=INGESTION_TIME,
    )

    import h3

    assert result.admin_match_flag is True
    assert result.address.geocode_precision == "street"
    assert result.address.geocode_confidence == 0.42
    assert result.address.h3_res_9 and h3.is_valid_cell(result.address.h3_res_9)
    assert "low_geocode_confidence" in result.quality_flags


class _RecordingTransport:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post_json(self, endpoint_url: str, **kwargs: Any) -> Any:
        self.calls.append({"endpoint_url": endpoint_url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _live_provider_with_transport(transport: Any, **env: str) -> PrimaryGeocodeProvider:
    from modules.external_data.providers.live import HttpGeocodeClient

    return PrimaryGeocodeProvider(
        client=HttpGeocodeClient("https://geocode.example/v1", transport=transport),
        env=_live_env(**env),
        correlation_id="corr-geocode-transport",
    )


def test_shared_candidate_conversion_matches_geocode_record_with_one_lookup() -> None:
    from modules.external_data.geo import normalize_address

    client = MockGeocodeClient(_success_payload())
    provider = PrimaryGeocodeProvider(client=client, env=_live_env(), correlation_id="corr-one")
    pipeline = GeoPipeline(provider)
    normalized = normalize_address("台北市大安區復興南路二段100號")

    candidate, payload = provider.lookup_with_payload(normalized)
    converted = pipeline.result_from_candidate(normalized, candidate)

    assert len(client.calls) == 1
    assert payload == _success_payload()
    assert converted.provider_request_id == "geo-live-req-001"
    assert converted.provider_observed_at == datetime(2026, 6, 28, 11, 59, tzinfo=UTC)
    assert converted.address.geocode_confidence == 0.94
    assert converted.quality_flags == ()
    replay = GeoPipeline(PrimaryGeocodeProvider(client=MockGeocodeClient(_success_payload()),
        env=_live_env(), correlation_id="corr-two")).geocode_record(
        {"address_raw": "台北市大安區復興南路二段100號"}, as_of=INGESTION_TIME)
    from dataclasses import replace

    # Address IDs are minted per AddressLocation; every converted fact matches.
    assert replace(replay, address=replace(replay.address, address_id=converted.address.address_id)) == converted


def test_shared_conversion_never_invents_provider_time_or_confidence() -> None:
    from modules.external_data.geo import GeocodeCandidate, normalize_address

    normalized = normalize_address("台北市大安區復興南路二段100號")
    candidate = GeocodeCandidate(25.026, 121.543, "unknown", 0.0, "geocode.primary_api")
    result = GeoPipeline().result_from_candidate(normalized, candidate, flags=("caller_flag",))

    assert result.provider_observed_at is None and result.provider_request_id == ""
    assert result.address.geocode_confidence == 0.0
    assert result.quality_flags == ("caller_flag", "low_geocode_confidence")


def test_http_client_keeps_serialization_and_never_puts_key_in_transport_headers() -> None:
    from modules.external_data.providers.live import GeocodeHttpResponse

    transport = _RecordingTransport(GeocodeHttpResponse(200, b'{"result":{"latitude":25.026,'
        b'"longitude":121.543,"precision":"address","confidence":0.94}}'))
    provider = _live_provider_with_transport(transport)
    from modules.external_data.geo import normalize_address

    candidate, payload = provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))

    assert candidate is not None and candidate.confidence == 0.94
    assert payload["result"]["latitude"] == 25.026
    call = transport.calls[0]
    assert call["endpoint_url"] == "https://geocode.example/v1"
    assert call["headers"] == {"Accept": "application/json", "Content-Type": "application/json",
                               "X-Correlation-Id": "corr-geocode-transport"}
    import json

    assert call["body"] == json.dumps({"address": "台北市大安區復興南路二段100號"}).encode("utf-8")
    assert call["credential"].value == "geocode-live-secret"
    assert "geocode-live-secret" not in repr(call["headers"]) + repr(call["credential"])


@pytest.mark.parametrize("status,error,code", [
    (401, GeocodeProviderAuthError, "unauthorized"),
    (403, GeocodeProviderAuthError, "unauthorized"),
    (429, GeocodeProviderRateLimitError, "rate_limited"),
    (302, None, "http_error"),
    (503, None, "http_error"),
])
def test_http_client_maps_transport_status_in_the_original_client(status: int, error: Any, code: str) -> None:
    from modules.external_data.geo import normalize_address
    from modules.external_data.providers import GeocodeProviderError
    from modules.external_data.providers.live import GeocodeHttpResponse

    provider = _live_provider_with_transport(_RecordingTransport(GeocodeHttpResponse(status, b"")))
    with pytest.raises(error or GeocodeProviderError) as raised:
        provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))
    assert raised.value.code == code
    if code == "http_error":
        assert raised.value.status_code == status


def test_http_client_maps_transport_timeout_to_provider_timeout() -> None:
    from modules.external_data.geo import normalize_address

    provider = _live_provider_with_transport(_RecordingTransport(TimeoutError("slow")))
    with pytest.raises(GeocodeProviderTimeoutError):
        provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))


class _NativeRunner:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.calls: list[dict[str, Any]] = []

    def run(self, request: dict, **kwargs: Any) -> dict:
        self.calls.append({"request": request, **kwargs})
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _native_transport(runner: _NativeRunner) -> Any:
    import time as _time

    from modules.external_data.application.native_intake_process import (
        NativeGeocodeProcessTransport,
    )
    from modules.external_data.providers.live import GeocodeHttpResponse

    return NativeGeocodeProcessTransport(deadline=_time.monotonic() + 5, guard=lambda: None,
        runner=runner, clock=lambda: INGESTION_TIME, response_type=GeocodeHttpResponse)


def test_native_geocode_transport_uses_private_credential_channel_and_journals_one_exchange() -> None:
    import base64
    import json

    from modules.external_data.geo import normalize_address

    body = json.dumps(_success_payload()).encode()
    runner = _NativeRunner({"status": 200, "media_type": "application/json",
                            "body_base64": base64.b64encode(body).decode()})
    transport = _native_transport(runner)
    provider = _live_provider_with_transport(transport)

    candidate, payload = provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))

    assert candidate is not None and candidate.provider_request_id == "geo-live-req-001"
    assert payload == _success_payload()
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["credential"] == "geocode-live-secret"
    assert set(call["request"]) == {"operation", "url", "correlation_id", "body_base64"}
    assert call["request"]["operation"] == "geocode_post"
    assert "geocode-live-secret" not in json.dumps(call["request"])
    exchange = transport.exchange
    assert exchange.response_body == body and exchange.status == 200
    assert exchange.fetched_at == INGESTION_TIME
    assert "geocode-live-secret" not in repr(exchange)
    with pytest.raises(Exception, match="NATIVE_GEOCODE_SECOND_EXCHANGE_REJECTED"):
        provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))
    assert len(runner.calls) == 1


@pytest.mark.parametrize("result", [
    {"status": 200, "media_type": "text/html", "body_base64": "e30="},
    {"status": 200, "media_type": "application/json", "body_base64": ""},
    {"status": 302, "media_type": None, "body_base64": "e30="},
    {"status": 200, "media_type": "application/json", "body_base64": "e30=", "extra": 1},
    {"status": 99, "media_type": None, "body_base64": ""},
])
def test_native_geocode_transport_untrusted_child_reply_is_fail_closed(result: dict) -> None:
    from modules.external_data.application.native_intake_process import NativeIORejected
    from modules.external_data.geo import normalize_address

    provider = _live_provider_with_transport(_native_transport(_NativeRunner(result)))
    with pytest.raises(NativeIORejected, match="NATIVE_IO_PROTOCOL_REJECTED"):
        provider.lookup_with_payload(normalize_address("台北市大安區復興南路二段100號"))


def test_native_geocode_transport_maps_reaped_deadline_but_never_hides_unreaped() -> None:
    from modules.external_data.application.native_intake_process import (
        NativeIORejected,
        NativeIOUnreaped,
    )
    from modules.external_data.geo import normalize_address

    address = normalize_address("台北市大安區復興南路二段100號")
    timed_out = _live_provider_with_transport(_native_transport(
        _NativeRunner(NativeIORejected("NATIVE_IO_DEADLINE_EXPIRED"))))
    with pytest.raises(GeocodeProviderTimeoutError):
        timed_out.lookup_with_payload(address)
    unreaped = _live_provider_with_transport(_native_transport(
        _NativeRunner(NativeIOUnreaped("NATIVE_IO_CHILD_UNREAPED"))))
    with pytest.raises(NativeIOUnreaped):
        unreaped.lookup_with_payload(address)
    rejected = _live_provider_with_transport(_native_transport(
        _NativeRunner(NativeIORejected("NATIVE_HTTP_ADDRESS_REJECTED"))))
    with pytest.raises(NativeIORejected, match="NATIVE_HTTP_ADDRESS_REJECTED"):
        rejected.lookup_with_payload(address)


def _native_geo_owner(**env: str) -> Any:
    from types import SimpleNamespace

    from modules.listing.application.pipeline import NativeIntakeGeoOwner
    from shared.infrastructure.persistence.factory import native_intake_geo_provider

    graph = SimpleNamespace(engine=None, queue=None, geo_provider=native_intake_geo_provider())
    return NativeIntakeGeoOwner(graph, env={"ODP_EXTERNAL_PROVIDER_MODE": "live",
        "ODP_PRODUCTION_PROVIDER_IDS": "geocode.primary_api",
        "ODP_GEOCODE_PROVIDER_URL": "https://geocode.synthetic.invalid/v1",
        "ODP_GEOCODE_PROVIDER_API_KEY": "synthetic-not-a-credential", **env})


def _production_env() -> dict[str, str]:
    return {"ODP_DEPLOY_ENV": "production",
            "ODP_PRODUCTION_PROVIDER_IDS": "geocode.primary_api,poi.commercial_api,admin_boundary.official_dataset",
            "ODP_POI_PROVIDER_URL": "https://poi.synthetic.invalid", "ODP_POI_PROVIDER_API_KEY": "synthetic-not-a-credential",
            **{name: "https://admin.synthetic.invalid" if name.endswith("URL") else "synthetic-not-a-credential"
               for name in _admin_boundary_env()}}


def _admin_boundary_env() -> list[str]:
    from modules.external_data.connectors import provider_registry as registered

    entry = next(p for p in registered() if p.provider_id == "admin_boundary.official_dataset")
    return [*entry.required_env_vars, *([entry.endpoint_env_var] if entry.endpoint_env_var else [])]


def _change_geo_license(monkeypatch: pytest.MonkeyPatch, **changes: Any) -> None:
    from dataclasses import replace
    from importlib import import_module

    registry = import_module("modules.external_data.connectors.provider_registry")
    monkeypatch.setattr(registry, "PROVIDER_REGISTRY", tuple(
        replace(p, license=replace(p.license, **changes)) if p.provider_id == "geocode.primary_api" else p
        for p in registry.PROVIDER_REGISTRY))


def test_native_geo_admits_only_the_original_validated_live_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, date, datetime

    assert _native_geo_owner().admitted_provider() == ("https://geocode.synthetic.invalid/v1", None)
    assert _native_geo_owner(**_production_env()).admitted_provider()[1] is None
    _change_geo_license(monkeypatch, expires_on=date(2099, 12, 30))
    assert _native_geo_owner().admitted_provider()[1] == datetime(2099, 12, 31, tzinfo=UTC)


@pytest.mark.parametrize(("env", "code"), [
    ({"ODP_EXTERNAL_PROVIDER_MODE": "fixture"}, "NOT_LIVE"),
    ({"ODP_EXTERNAL_PROVIDER_MODE": "disabled"}, "NOT_LIVE"),
    ({"ODP_EXTERNAL_PROVIDER_MODE": "unrecognised"}, "NOT_LIVE"),
    ({"ODP_PRODUCTION_PROVIDER_IDS": "poi.commercial_api", "ODP_POI_PROVIDER_API_KEY": "synthetic-not-a-credential"},
     "NOT_SELECTED"),
    ({"ODP_PRODUCTION_PROVIDER_IDS": "geocode.primary_api,unknown.provider"}, "VALIDATION_REFUSED"),
    ({"ODP_PRODUCTION_PROVIDER_IDS": ""}, "VALIDATION_REFUSED"),
    ({"ODP_DEPLOY_ENV": "production"}, "VALIDATION_REFUSED"),
    ({"ODP_GEOCODE_PROVIDER_API_KEY": "placeholder"}, "VALIDATION_REFUSED"),
    *[({"ODP_GEOCODE_PROVIDER_AUTH_STATUS": status}, "VALIDATION_REFUSED")
      for status in ("expired", "unauthorized", "revoked", "invalid")],
    ({"ODP_GEOCODE_PROVIDER_URL": "http://geocode.synthetic.invalid/v1"}, "ENDPOINT_UNAVAILABLE"),
    ({"ODP_GEOCODE_PROVIDER_URL": ""}, "ENDPOINT_UNAVAILABLE"),
])
def test_native_geo_refuses_unvalidated_or_unselected_provider(env: dict[str, str], code: str) -> None:
    from modules.listing.application.native_intake_retrieval_policy import NativeIntakeGeoUnadmitted

    with pytest.raises(NativeIntakeGeoUnadmitted, match="NATIVE_GEO_PROVIDER_" + code):
        _native_geo_owner(**env).admitted_provider()


@pytest.mark.parametrize("change", ["expired", "not_for_production"])
def test_native_geo_keeps_the_original_license_refusals(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    from datetime import date

    from modules.listing.application.native_intake_retrieval_policy import NativeIntakeGeoUnadmitted

    _change_geo_license(monkeypatch, **({"expires_on": date(2026, 1, 1)} if change == "expired"
                                        else {"allowed_in_production": False}))
    with pytest.raises(NativeIntakeGeoUnadmitted, match="NATIVE_GEO_PROVIDER_VALIDATION_REFUSED"):
        _native_geo_owner(**_production_env()).admitted_provider()


def test_vendor_provider_label_stays_a_claim_beside_the_selected_provider() -> None:
    from modules.external_data.geo import normalize_address
    from modules.external_data.providers.live import _candidate_from_geocode_payload

    normalized = normalize_address("台北市信義區市府路1號")
    point = {"latitude": 25.03, "longitude": 121.56, "precision": "rooftop", "confidence": 0.9}
    claimed = _candidate_from_geocode_payload({"result": {**point, "provider_id": "vendor"}}, normalized, "geocode.primary_api")
    silent = _candidate_from_geocode_payload({"result": point}, normalized, "geocode.primary_api")
    assert (claimed.provider, claimed.provider_claim) == ("vendor", "vendor")
    assert (silent.provider, silent.provider_claim) == ("geocode.primary_api", None)
    assert silent.provider_observed_at is None
