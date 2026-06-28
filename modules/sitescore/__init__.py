"""SiteScore module public API.

Implements SiteScore v1: candidate-site scoring, versioned report storage,
and a batch scoring worker. The human approval closed loop (decision workflow
and realization hooks) lives in ``shared.workflow.sitescore``.
"""

from modules.sitescore.application.reporting import (
    SiteScoreReportService,
    run_sitescore_reports,
)
from modules.sitescore.domain.scoring import (
    SITESCORE_FEATURE_VERSION,
    SITESCORE_MODEL_VERSION,
    Interval,
    SiteScoreFeatureInput,
    SiteScoreRecommendation,
    SiteScoreReport,
    score_site,
    score_sites,
)
from modules.sitescore.infrastructure.repositories import InMemorySiteScoreRepository
from modules.sitescore.workers.scoring_worker import (
    SiteScoreBatchScoreResult,
    SiteScoreScoringWorker,
    run_sitescore_batch_score,
)

__all__ = [
    "SITESCORE_FEATURE_VERSION",
    "SITESCORE_MODEL_VERSION",
    "InMemorySiteScoreRepository",
    "Interval",
    "SiteScoreBatchScoreResult",
    "SiteScoreFeatureInput",
    "SiteScoreRecommendation",
    "SiteScoreReport",
    "SiteScoreReportService",
    "SiteScoreScoringWorker",
    "run_sitescore_batch_score",
    "run_sitescore_reports",
    "score_site",
    "score_sites",
]
