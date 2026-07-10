from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from modules.sitescore.domain.scoring import SiteScoreReport
from shared.domain import Prediction, PredictionRun, SiteScoreRun


@dataclass
class InMemorySiteScoreRepository:
    """Stores SiteScore reports with per-candidate version history.

    Re-scoring a candidate site appends a new immutable report version rather
    than overwriting the previous one, so the decision audit trail can always
    resolve the exact report version a human approved.
    """

    _history: dict[str, list[SiteScoreReport]] = field(default_factory=dict)
    _by_report_id: dict[str, SiteScoreReport] = field(default_factory=dict)
    _prediction_runs: dict[str, PredictionRun] = field(default_factory=dict)
    _predictions: dict[str, list[Prediction]] = field(default_factory=dict)
    _sitescore_runs: dict[str, SiteScoreRun] = field(default_factory=dict)

    def save_report(self, report: SiteScoreReport) -> SiteScoreReport:
        versions = self._history.setdefault(report.candidate_site_id, [])
        versioned = report.with_version(
            report_version=len(versions) + 1,
            report_id=f"sitescore-report-{uuid4()}",
        )
        versions.append(versioned)
        self._by_report_id[versioned.report_id] = versioned
        return versioned

    def latest(self, candidate_site_id: str) -> SiteScoreReport | None:
        versions = self._history.get(candidate_site_id)
        if not versions:
            return None
        return versions[-1]

    def history(self, candidate_site_id: str) -> list[SiteScoreReport]:
        return list(self._history.get(candidate_site_id, []))

    def get_report(self, report_id: str) -> SiteScoreReport | None:
        return self._by_report_id.get(report_id)

    def list_latest(self) -> list[SiteScoreReport]:
        return [versions[-1] for versions in self._history.values() if versions]

    def save_prediction_run(self, run: PredictionRun) -> PredictionRun:
        self._prediction_runs[run.prediction_run_id] = run
        return run

    def get_prediction_run(self, prediction_run_id: str) -> PredictionRun | None:
        return self._prediction_runs.get(prediction_run_id)

    def save_prediction(self, prediction: Prediction) -> Prediction:
        self._predictions.setdefault(prediction.prediction_run_id, []).append(prediction)
        return prediction

    def get_predictions(self, prediction_run_id: str) -> list[Prediction]:
        return list(self._predictions.get(prediction_run_id, []))

    def save_sitescore_run(self, run: SiteScoreRun) -> SiteScoreRun:
        self._sitescore_runs[run.sitescore_run_id] = run
        return run

    def get_sitescore_run(self, sitescore_run_id: str) -> SiteScoreRun | None:
        return self._sitescore_runs.get(sitescore_run_id)


__all__ = ["InMemorySiteScoreRepository"]

