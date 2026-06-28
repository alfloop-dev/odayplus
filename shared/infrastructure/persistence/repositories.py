"""Durable, SQLite-backed implementations of the product module repositories.

Each class mirrors the public surface of its ``InMemory*`` counterpart exactly
(same method names, same return values, same versioning semantics) so it is a
drop-in replacement behind the existing repository interfaces — domain and
application tests stay compatible. State lives in ``durable_documents`` via
:class:`SqliteDocumentStore`, so writes survive a process restart.
"""

from __future__ import annotations

from uuid import uuid4

from modules.adlift.domain.incrementality import IncrementalityReport
from modules.avm.domain import DataRoom, NormalizedMargin, ValuationCase, ValuationReport
from modules.forecastops.domain.forecasting import (
    Alert,
    ForecastOutput,
    ForecastSeries,
    InterventionHandoff,
)
from modules.intervention.domain.lifecycle import Intervention, LabelRecord
from modules.sitescore.domain.scoring import SiteScoreReport
from shared.infrastructure.persistence.document_store import SqliteDocumentStore


class DurableSiteScoreRepository:
    """Durable mirror of ``InMemorySiteScoreRepository``."""

    _C = "sitescore.reports"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_report(self, report: SiteScoreReport) -> SiteScoreReport:
        version = self._store.count_in_group(self._C, report.candidate_site_id) + 1
        versioned = report.with_version(
            report_version=version,
            report_id=f"sitescore-report-{uuid4()}",
        )
        self._store.append_version(
            self._C,
            versioned.report_id,
            versioned,
            group_key=versioned.candidate_site_id,
        )
        return versioned

    def latest(self, candidate_site_id: str) -> SiteScoreReport | None:
        return self._store.latest_in_group(self._C, candidate_site_id)

    def history(self, candidate_site_id: str) -> list[SiteScoreReport]:
        return self._store.list_by_group(self._C, candidate_site_id)

    def get_report(self, report_id: str) -> SiteScoreReport | None:
        return self._store.get(self._C, report_id)

    def list_latest(self) -> list[SiteScoreReport]:
        return self._store.latest_per_group(self._C)


class DurableAVMRepository:
    """Durable mirror of ``InMemoryAVMRepository``."""

    _CASES = "avm.cases"
    _MARGINS = "avm.margins"
    _REPORTS = "avm.reports"
    _DATAROOMS = "avm.datarooms"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_case(self, case: ValuationCase) -> ValuationCase:
        self._store.put(self._CASES, case.case_id, case)
        return case

    def get_case(self, case_id: str) -> ValuationCase | None:
        return self._store.get(self._CASES, case_id)

    def list_cases(self) -> list[ValuationCase]:
        return self._store.list_all(self._CASES)

    def save_margin(self, margin: NormalizedMargin) -> NormalizedMargin:
        self._store.put(self._MARGINS, margin.case_id, margin)
        return margin

    def get_margin(self, case_id: str) -> NormalizedMargin | None:
        return self._store.get(self._MARGINS, case_id)

    def save_report(self, report: ValuationReport) -> ValuationReport:
        version = self._store.count_in_group(self._REPORTS, report.case_id) + 1
        versioned = report.with_version(
            valuation_version=version,
            report_id=f"avm-report-{uuid4()}",
        )
        self._store.append_version(
            self._REPORTS,
            versioned.report_id,
            versioned,
            group_key=versioned.case_id,
        )
        return versioned

    def replace_latest_report(self, report: ValuationReport) -> ValuationReport:
        if self._store.count_in_group(self._REPORTS, report.case_id) == 0:
            self._store.put(
                self._REPORTS, report.report_id, report, group_key=report.case_id, seq=1
            )
        else:
            self._store.replace_latest_in_group(
                self._REPORTS, report, group_key=report.case_id
            )
        return report

    def latest_report(self, case_id: str) -> ValuationReport | None:
        return self._store.latest_in_group(self._REPORTS, case_id)

    def save_dataroom(self, dataroom: DataRoom) -> DataRoom:
        self._store.put(self._DATAROOMS, dataroom.case_id, dataroom)
        return dataroom

    def get_dataroom(self, case_id: str) -> DataRoom | None:
        return self._store.get(self._DATAROOMS, case_id)


class DurableForecastOpsRepository:
    """Durable mirror of ``InMemoryForecastOpsRepository``."""

    _SERIES = "forecastops.series"
    _FORECASTS = "forecastops.forecasts"
    _ALERTS = "forecastops.alerts"
    _HANDOFFS = "forecastops.handoffs"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_series(self, series: ForecastSeries) -> ForecastSeries:
        self._store.put(self._SERIES, series.store_id, series)
        return series

    def list_series(self) -> list[ForecastSeries]:
        return self._store.list_all(self._SERIES)

    def get_series(self, store_id: str) -> ForecastSeries | None:
        return self._store.get(self._SERIES, store_id)

    def save_forecast(self, forecast: ForecastOutput) -> ForecastOutput:
        version = self._store.count_in_group(self._FORECASTS, forecast.store_id) + 1
        versioned = forecast.with_version(
            forecast_version=version,
            forecast_output_id=f"forecast-output-{uuid4()}",
        )
        self._store.append_version(
            self._FORECASTS,
            versioned.forecast_output_id,
            versioned,
            group_key=versioned.store_id,
        )
        return versioned

    def latest_forecasts(self) -> list[ForecastOutput]:
        return self._store.latest_per_group(self._FORECASTS)

    def history(self, store_id: str) -> list[ForecastOutput]:
        return self._store.list_by_group(self._FORECASTS, store_id)

    def save_alert(self, alert: Alert) -> Alert:
        self._store.put(self._ALERTS, alert.alert_id, alert)
        return alert

    def list_alerts(self) -> list[Alert]:
        return self._store.list_all(self._ALERTS)

    def save_handoff(self, handoff: InterventionHandoff) -> InterventionHandoff:
        self._store.put(self._HANDOFFS, handoff.handoff_id, handoff)
        return handoff

    def list_handoffs(self) -> list[InterventionHandoff]:
        return self._store.list_all(self._HANDOFFS)


class DurableAdLiftRepository:
    """Durable mirror of ``InMemoryAdLiftRepository``."""

    _C = "adlift.reports"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save_report(self, report: IncrementalityReport) -> IncrementalityReport:
        version = self._store.count_in_group(self._C, report.campaign_id) + 1
        versioned = report.with_version(
            report_version=version,
            report_id=f"adlift-report-{uuid4()}",
        )
        self._store.append_version(
            self._C,
            versioned.report_id,
            versioned,
            group_key=versioned.campaign_id,
        )
        return versioned

    def latest_reports(self) -> list[IncrementalityReport]:
        return self._store.latest_per_group(self._C)

    def latest_for_campaign(self, campaign_id: str) -> IncrementalityReport | None:
        return self._store.latest_in_group(self._C, campaign_id)

    def history(self, campaign_id: str) -> list[IncrementalityReport]:
        return self._store.list_by_group(self._C, campaign_id)


class DurableInterventionRepository:
    """Durable mirror of ``InMemoryInterventionRepository``."""

    _C = "intervention.interventions"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def save(self, intervention: Intervention) -> Intervention:
        self._store.put(
            self._C,
            intervention.intervention_id,
            intervention,
            group_key=intervention.store_id,
        )
        return intervention

    def get(self, intervention_id: str) -> Intervention | None:
        return self._store.get(self._C, intervention_id)

    def list_all(self) -> list[Intervention]:
        return self._store.list_all(self._C)

    def list_by_store(self, store_id: str) -> list[Intervention]:
        return self._store.list_by_group(self._C, store_id)


class DurableLabelRegistry:
    """Durable mirror of ``InMemoryLabelRegistry`` (intervention label hook)."""

    _C = "intervention.labels"

    def __init__(self, store: SqliteDocumentStore) -> None:
        self._store = store

    def __call__(self, label: LabelRecord) -> None:
        self._store.put(
            self._C, label.intervention_id, label, group_key=label.store_id
        )

    def get(self, intervention_id: str) -> LabelRecord | None:
        return self._store.get(self._C, intervention_id)

    def list_labels(self) -> list[LabelRecord]:
        return self._store.list_all(self._C)

    def intervened_windows(self, store_id: str) -> list[LabelRecord]:
        return [
            label
            for label in self._store.list_by_group(self._C, store_id)
            if label.exclude_from_baseline
        ]


__all__ = [
    "DurableAVMRepository",
    "DurableAdLiftRepository",
    "DurableForecastOpsRepository",
    "DurableInterventionRepository",
    "DurableLabelRegistry",
    "DurableSiteScoreRepository",
]
