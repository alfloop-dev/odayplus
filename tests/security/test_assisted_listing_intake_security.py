from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from modules.external_data.application import assisted_intake
from modules.external_data.application.assisted_intake import RetrievalResult
from modules.external_data.security import (
    FetchResponse,
    RetrievalLimits,
    RetrievalSecurityGate,
    redact_sensitive_snapshot,
)

WRITER_HEADERS = {
    "x-subject-id": "operator-expansion-manager",
    "x-roles": "expansion_user",
    "x-operator-role": "expansion-manager",
    "x-tenant-id": "tenant-a",
}

READ_ONLY_REVIEWER_HEADERS = {
    "x-subject-id": "operator-site-reviewer",
    "x-roles": "site_reviewer",
    "x-operator-role": "site-reviewer",
    "x-tenant-id": "tenant-a",
}


def _write_headers(key: str) -> dict[str, str]:
    return {
        **WRITER_HEADERS,
        "Idempotency-Key": f"idem-{key}",
        "X-Correlation-Id": f"corr-{key}",
    }


class CountingFetcher:
    def __init__(self, responses: Sequence[FetchResponse] | None = None) -> None:
        self.calls: list[tuple[str, float, int]] = []
        self._responses = list(responses or [])

    def __call__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> FetchResponse:
        self.calls.append((url, timeout_seconds, max_response_bytes))
        if self._responses:
            return self._responses.pop(0)
        return FetchResponse(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html>listing</html>",
        )


def _public_resolver(_host: str) -> tuple[str, ...]:
    return ("93.184.216.34",)


@pytest.mark.parametrize(
    "policy",
    ["POLICY_UNKNOWN", "SOURCE_BLOCKED", "ASSISTED_ENTRY_ONLY", "AUTH_REQUIRED"],
)
def test_non_retrievable_source_policy_never_fetches(policy: str) -> None:
    fetcher = CountingFetcher()
    gate = RetrievalSecurityGate(resolver=_public_resolver, fetcher=fetcher)

    result = gate.fetch("https://approved.example/listing/1", policy=policy)

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-POLICY-BLOCKED"
    assert fetcher.calls == []


def test_unsupported_retrieval_method_and_scheme_fail_before_fetch() -> None:
    fetcher = CountingFetcher()
    gate = RetrievalSecurityGate(resolver=_public_resolver, fetcher=fetcher)

    bad_method = gate.fetch(
        "https://approved.example/listing/1",
        policy="APPROVED_RETRIEVAL",
        retrieval_method="browser_cookie_replay",
    )
    bad_scheme = gate.fetch("file:///etc/passwd", policy="APPROVED_RETRIEVAL")

    assert bad_method.failure is not None
    assert bad_method.failure.code == "ODP-INTAKE-RETRIEVAL-METHOD-BLOCKED"
    assert bad_scheme.failure is not None
    assert bad_scheme.failure.code == "ODP-INTAKE-RETRIEVAL-UNSUPPORTED-SCHEME"
    assert fetcher.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/listing",
        "http://10.0.0.5/listing",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/listing",
        "http://[fe80::1]/listing",
        "http://224.0.0.1/listing",
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_blocked_network_targets_are_rejected_before_connection(url: str) -> None:
    fetcher = CountingFetcher()
    gate = RetrievalSecurityGate(resolver=_public_resolver, fetcher=fetcher)

    result = gate.fetch(url, policy="APPROVED_RETRIEVAL")

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-NETWORK-BLOCKED"
    assert fetcher.calls == []


def test_dns_resolution_blocks_private_targets_before_fetch() -> None:
    fetcher = CountingFetcher()
    gate = RetrievalSecurityGate(resolver=lambda _host: ("10.1.2.3",), fetcher=fetcher)

    result = gate.fetch("https://approved.example/detail", policy="APPROVED_RETRIEVAL")

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-NETWORK-BLOCKED"
    assert fetcher.calls == []


def test_redirect_hop_is_revalidated_before_second_fetch() -> None:
    fetcher = CountingFetcher(
        [
            FetchResponse(
                status_code=302,
                headers={"Location": "http://169.254.169.254/latest/meta-data"},
                body=b"",
            )
        ]
    )
    gate = RetrievalSecurityGate(resolver=_public_resolver, fetcher=fetcher)

    result = gate.fetch("https://approved.example/detail", policy="APPROVED_RETRIEVAL")

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-NETWORK-BLOCKED"
    assert len(fetcher.calls) == 1


def test_dns_rebinding_on_redirect_is_blocked_before_refetch() -> None:
    resolutions = iter([("93.184.216.34",), ("10.0.0.8",)])
    fetcher = CountingFetcher(
        [
            FetchResponse(
                status_code=302,
                headers={"Location": "https://approved.example/final"},
                body=b"",
            )
        ]
    )
    gate = RetrievalSecurityGate(resolver=lambda _host: next(resolutions), fetcher=fetcher)

    result = gate.fetch("https://approved.example/detail", policy="APPROVED_RETRIEVAL")

    assert result.failure is not None
    assert result.failure.code == "ODP-INTAKE-RETRIEVAL-NETWORK-BLOCKED"
    assert len(fetcher.calls) == 1


def test_retrieval_limits_emit_terminal_and_retryable_failures() -> None:
    timeout_fetcher = CountingFetcher()

    def timeout(*_args: Any, **_kwargs: Any) -> FetchResponse:
        timeout_fetcher.calls.append(("timeout", 0, 0))
        raise TimeoutError

    timeout_gate = RetrievalSecurityGate(resolver=_public_resolver, fetcher=timeout)
    timeout_result = timeout_gate.fetch(
        "https://approved.example/detail",
        policy="APPROVED_RETRIEVAL",
    )

    content_gate = RetrievalSecurityGate(
        resolver=_public_resolver,
        fetcher=CountingFetcher(
            [FetchResponse(status_code=200, headers={"Content-Type": "image/png"}, body=b"png")]
        ),
    )
    content_result = content_gate.fetch(
        "https://approved.example/detail",
        policy="APPROVED_RETRIEVAL",
    )

    size_gate = RetrievalSecurityGate(
        resolver=_public_resolver,
        fetcher=CountingFetcher(
            [
                FetchResponse(
                    status_code=200,
                    headers={"Content-Type": "text/html"},
                    body=b"x" * 11,
                )
            ]
        ),
        limits=RetrievalLimits(max_response_bytes=10),
    )
    size_result = size_gate.fetch(
        "https://approved.example/detail",
        policy="APPROVED_RETRIEVAL",
    )

    assert timeout_result.failure is not None
    assert timeout_result.failure.code == "ODP-INTAKE-RETRIEVAL-TIMEOUT"
    assert timeout_result.failure.retryable is True
    assert content_result.failure is not None
    assert content_result.failure.code == "ODP-INTAKE-RETRIEVAL-CONTENT-TYPE"
    assert content_result.failure.retryable is False
    assert size_result.failure is not None
    assert size_result.failure.code == "ODP-INTAKE-RETRIEVAL-RESPONSE-TOO-LARGE"
    assert size_result.failure.retryable is False


def test_api_rejects_credentials_tokens_and_private_endpoint_submission_fields() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={
            "url": "https://www.synthetic.example/detail-77120345.html",
            "heatZoneId": "HZ-01",
            "cookie": "sessionid=prod-secret",
            "bearerToken": "Bearer abcdefghijklmnop",
            "privateApiEndpoint": "https://internal.example/api/listings",
        },
    )

    assert response.status_code == 422
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "cookie" in serialized
    assert "bearerToken" in serialized
    assert "privateApiEndpoint" in serialized
    assert "prod-secret" not in serialized


def test_embedded_credentials_and_token_query_urls_are_rejected() -> None:
    client = TestClient(create_app())

    userinfo = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://user:pass@www.synthetic.example/detail-77120345.html"},
    )
    token_query = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.synthetic.example/detail-77120345.html?access_token=public"},
    )

    assert userinfo.status_code == 400
    assert token_query.status_code in {400, 422}


def test_blocked_source_submit_and_retry_never_call_retrieve(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app())

    def fail_retrieve(*_args: Any, **_kwargs: Any) -> RetrievalResult:
        raise AssertionError("retrieve must not be called for blocked policy")

    monkeypatch.setattr(assisted_intake, "retrieve", fail_retrieve)

    assisted = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.591.com.tw/rent-detail-12345.html", "heatZoneId": "HZ-01"},
    )
    unknown = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.unknown-domain.com/rent/123", "heatZoneId": "HZ-01"},
    )
    retry = client.post(
        f"/api/v1/operator/network-listings/intake/{assisted.json()['id']}/retry",
        headers=WRITER_HEADERS,
        json={"actorRoleId": "expansionManager"},
    )

    assert assisted.status_code == 200
    assert assisted.json()["stage"] == "AWAITING_ASSISTED_ENTRY"
    assert unknown.status_code == 200
    assert unknown.json()["stage"] == "QUARANTINED"
    assert retry.status_code == 200
    assert retry.json()["stage"] == "AWAITING_ASSISTED_ENTRY"


def test_read_only_reviewer_can_read_but_cannot_write_intake_or_listing_actions() -> None:
    client = TestClient(create_app())
    writer_submit = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.synthetic.example/detail-88520242.html", "heatZoneId": "HZ-01"},
    )
    intake_id = writer_submit.json()["id"]

    read = client.get("/api/v1/operator/network-listings", headers=READ_ONLY_REVIEWER_HEADERS)
    submit = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=READ_ONLY_REVIEWER_HEADERS,
        json={"url": "https://www.synthetic.example/detail-77120345.html", "heatZoneId": "HZ-01"},
    )
    correct = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        headers=READ_ONLY_REVIEWER_HEADERS,
        json={
            "fields": {"address": "新北市板橋區府中路 99 號 1F"},
            "reason": "勘誤地址",
            "riskSummary": "修改地址會改變比對結果。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
    )
    quarantine = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/decide",
        headers=READ_ONLY_REVIEWER_HEADERS,
        json={
            "action": "quarantine",
            "reason": "治理覆核",
            "riskSummary": "隔離會停止此收件。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
    )
    merge = client.post(
        "/api/v1/operator/network-listings/listings/L-2029/merge",
        headers=READ_ONLY_REVIEWER_HEADERS,
        json={
            "targetListingId": "L-2025",
            "reason": "重複",
            "riskSummary": "合併會改變來源證據。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
    )
    promote = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/promote",
        headers=READ_ONLY_REVIEWER_HEADERS,
        json={
            "reason": "核准轉候選",
            "riskSummary": "會建立候選點。",
            "riskAcknowledged": True,
            "actorRoleId": "expansionManager",
        },
    )

    assert read.status_code == 200
    assert submit.status_code == 403
    assert correct.status_code == 403
    assert quarantine.status_code == 403
    assert merge.status_code == 403
    assert promote.status_code == 403


def test_identity_affecting_writes_require_idempotency_and_correlation() -> None:
    client = TestClient(create_app())
    submit = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.synthetic.example/detail-99310418.html", "heatZoneId": "HZ-02"},
    )
    intake_id = submit.json()["id"]
    body = {
        "fields": {"address": "新北市板橋區府中路 99 號 1F"},
        "reason": "勘誤地址",
        "riskSummary": "修改地址會改變比對結果。",
        "riskAcknowledged": True,
        "actorRoleId": "expansionManager",
    }

    missing_idempotency = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        headers={**WRITER_HEADERS, "X-Correlation-Id": "corr-missing-idem"},
        json=body,
    )
    missing_correlation = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        headers={**WRITER_HEADERS, "Idempotency-Key": "idem-missing-corr"},
        json=body,
    )

    assert missing_idempotency.status_code == 422
    assert "idempotency key is required" in missing_idempotency.json()["detail"]
    assert missing_correlation.status_code == 422
    assert "correlation id is required" in missing_correlation.json()["detail"]


def test_idempotent_high_impact_replay_does_not_append_audit() -> None:
    client = TestClient(create_app())
    submit = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": "https://www.synthetic.example/detail-99310418.html", "heatZoneId": "HZ-02"},
    )
    intake_id = submit.json()["id"]
    body = {
        "fields": {"address": "新北市板橋區府中路 99 號 1F"},
        "reason": "勘誤地址",
        "riskSummary": "修改地址會改變比對結果。",
        "riskAcknowledged": True,
        "actorRoleId": "expansionManager",
    }

    first = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        headers=_write_headers("correct-replay"),
        json=body,
    )
    replay = client.post(
        f"/api/v1/operator/network-listings/intake/{intake_id}/correct",
        headers=_write_headers("correct-replay"),
        json=body,
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["auditEvents"] == first.json()["auditEvents"]


def test_raw_snapshot_redacts_contact_and_token_material(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://www.synthetic.example/detail-70000001.html"
    monkeypatch.setitem(
        assisted_intake.RETRIEVAL_CORPUS,
        url,
        RetrievalResult(
            snapshot_id="SNAP-SYNTHETIC-70000001",
            captured_at="2026-07-15T03:00:00Z",
            raw={
                "source_listing_id": "synthetic-70000001",
                "title": "No PII title",
                "address_raw": "新北市新莊區興德路 88 號 1F",
                "rent_text": "NT$45,000 / 月",
                "rent_amount": 45000,
                "area_text": "16 坪",
                "area_ping": 16.0,
                "floor": "1F",
                "listing_type": "店面",
                "listing_status": "active",
                "confidence": 0.93,
                "contactPhone": "0912-345-678",
                "brokerEmail": "broker@example.com",
                "authorization": "Bearer secret-token-value",
            },
        ),
    )
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/operator/network-listings/intake/submit",
        headers=WRITER_HEADERS,
        json={"url": url, "heatZoneId": "HZ-01"},
    )

    assert response.status_code == 200
    raw_snapshot = response.json()["rawSnapshot"]
    serialized = json.dumps(raw_snapshot, ensure_ascii=False)
    assert "0912-345-678" not in serialized
    assert "broker@example.com" not in serialized
    assert "secret-token-value" not in serialized
    assert raw_snapshot["contactPhone"] == "[REDACTED]"
    assert raw_snapshot["brokerEmail"] == "[REDACTED]"
    assert raw_snapshot["authorization"] == "[REDACTED]"


def test_redaction_recurses_through_nested_snapshot_values() -> None:
    redacted = redact_sensitive_snapshot(
        {
            "notes": ["Call 0912-345-678", "email broker@example.com"],
            "nested": {"token": "sk-live-secret-token"},
        }
    )

    serialized = json.dumps(redacted, ensure_ascii=False)
    assert "0912-345-678" not in serialized
    assert "broker@example.com" not in serialized
    assert "sk-live-secret-token" not in serialized
