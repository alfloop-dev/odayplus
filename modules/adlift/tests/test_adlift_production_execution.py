"""Production AdLift OSS execution contract tests."""

from __future__ import annotations

from datetime import date

import pytest

import modules.adlift.domain.incrementality as incrementality
from modules.adlift import (
    AdCampaign,
    AdLiftProductionExecutionError,
    AdLiftService,
    InMemoryAdLiftRepository,
    StoreDayMetric,
)


def _campaign(
    *,
    controls: tuple[str, ...] = ("control",),
    with_lineage: bool = True,
) -> AdCampaign:
    observations: list[StoreDayMetric] = []
    for store, pre, post in (
        ("treatment", 100.0, 140.0),
        ("control", 100.0, 110.0),
    ):
        for day in range(1, 5):
            observations.append(
                StoreDayMetric(
                    store_id=store,
                    business_date=date(2026, 7, day),
                    revenue=pre,
                    gross_margin=pre * 0.5,
                    source_snapshot_ids=(f"{store}-pre-{day}",) if with_lineage else (),
                )
            )
        for day in range(5, 9):
            observations.append(
                StoreDayMetric(
                    store_id=store,
                    business_date=date(2026, 7, day),
                    revenue=post,
                    gross_margin=post * 0.5,
                    source_snapshot_ids=(f"{store}-post-{day}",) if with_lineage else (),
                )
            )
    return AdCampaign(
        campaign_id="campaign-live",
        name="Live campaign",
        treatment_store_ids=("treatment",),
        candidate_control_store_ids=controls,
        pre_period_start=date(2026, 7, 1),
        pre_period_end=date(2026, 7, 4),
        campaign_period_start=date(2026, 7, 5),
        campaign_period_end=date(2026, 7, 8),
        ad_spend=100.0,
        observations=tuple(observations),
    )


def test_production_adlift_executes_statsmodels_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    calls = {"statsmodels": 0}
    original = incrementality._fit_statsmodels_matched_did

    def spy(*args, **kwargs):
        calls["statsmodels"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(incrementality, "_fit_statsmodels_matched_did", spy)
    service = AdLiftService()

    report = service.evaluate([_campaign()]).reports[0]

    assert calls["statsmodels"] == 2
    assert report.estimator_metadata["library"] == "statsmodels"
    assert report.estimator_metadata["estimator"] == "WLS"
    assert report.estimator_metadata["execution_mode"] == "production_oss"
    assert report.estimator_metadata["library_version"]
    assert report.estimator_metadata["model_version"] == report.model_version
    assert report.source_snapshot_ids


@pytest.mark.parametrize(
    "campaign",
    [
        _campaign(controls=()),
        _campaign(with_lineage=False),
    ],
)
def test_production_adlift_rejects_non_did_fallback(
    monkeypatch: pytest.MonkeyPatch,
    campaign: AdCampaign,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    repository = InMemoryAdLiftRepository()
    service = AdLiftService(repository=repository)

    with pytest.raises(AdLiftProductionExecutionError):
        service.evaluate([campaign])

    assert repository.latest_reports() == []


def test_production_adlift_statsmodels_failure_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setattr(
        incrementality,
        "_fit_statsmodels_matched_did",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("statsmodels runtime unavailable")
        ),
    )
    repository = InMemoryAdLiftRepository()
    service = AdLiftService(repository=repository)

    with pytest.raises(
        AdLiftProductionExecutionError,
        match="statsmodels DiD execution failed",
    ):
        service.evaluate([_campaign()])

    assert repository.latest_reports() == []
