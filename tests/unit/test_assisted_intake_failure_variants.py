from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.worker.assisted_listing_intake.worker import (
    AssistedIntakeStageError,
    source_observation_is_stale,
    stage_failure,
)
from modules.external_data.application.assisted_intake import (
    RETRIEVAL_CORPUS,
    PermanentParserFailure,
    RetrievalResult,
    parse_snapshot,
)
from modules.external_data.security.assisted_listing_retrieval import (
    FetchResponse,
    RetrievalSecurityGate,
)


def test_supported_retrieval_failure_variants_keep_distinct_codes() -> None:
    assert (
        RETRIEVAL_CORPUS[
            "https://www.synthetic.example/detail-50000002.html"
        ].failure.code
        == "AUTH_WALL_ENCOUNTERED"
    )
    assert (
        RETRIEVAL_CORPUS[
            "https://www.synthetic.example/detail-50000003.html"
        ].failure.code
        == "BOT_CHALLENGE_ENCOUNTERED"
    )


def test_unsupported_parser_payload_is_a_permanent_failure() -> None:
    with pytest.raises(PermanentParserFailure):
        parse_snapshot(
            RetrievalResult(
                snapshot_id="snapshot-invalid",
                captured_at="2026-07-23T00:00:00Z",
                raw={"_parser_failure": "permanent"},
            )
        )


def test_stage_failure_preserves_permanent_parser_contract() -> None:
    failure = stage_failure(
        "PARSING",
        AssistedIntakeStageError(
            code="PARSER_PERMANENT_FAILURE",
            summary="Unsupported source representation.",
            next_action="Use assisted entry.",
            retryable=False,
        ),
    )

    assert failure == {
        "code": "PARSER_PERMANENT_FAILURE",
        "summary": "Unsupported source representation.",
        "nextAction": "Use assisted entry.",
        "retryable": False,
    }


def test_source_observation_staleness_uses_configured_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_INTAKE_STALE_SNAPSHOT_SECONDS", "3600")
    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)

    assert source_observation_is_stale("2026-07-23T10:00:00Z", now=now)
    assert not source_observation_is_stale("2026-07-23T11:30:00Z", now=now)


def test_retrieval_gate_preserves_safe_source_observed_time() -> None:
    gate = RetrievalSecurityGate(
        resolver=lambda _host: ("93.184.216.34",),
        fetcher=lambda _url, *, timeout_seconds, max_response_bytes: FetchResponse(
            status_code=200,
            headers={
                "Content-Type": "text/html",
                "X-Source-Observed-At": "2025-01-15T02:45:00Z",
            },
            body=b"{}",
        ),
    )

    result = gate.fetch(
        "https://www.synthetic.example/detail-50000005.html",
        policy="APPROVED_RETRIEVAL",
    )

    assert result.ok
    assert result.source_observed_at == "2025-01-15T02:45:00Z"
