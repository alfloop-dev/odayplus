from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_CONTRACTS = REPO_ROOT / "packages/domain-types/src/frontend-contracts.ts"
INDEX = REPO_ROOT / "packages/domain-types/src/index.ts"
COMPONENT_CONTRACTS = REPO_ROOT / "docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md"

DOMAIN_COMPONENTS = [
    "HeatZoneScoreCard",
    "CandidateSiteCard",
    "SiteScoreReportSummary",
    "ForecastBandChart",
    "FourLightBadge",
    "RootCauseEvidenceCard",
    "InterventionTimeline",
    "PricingPlanComparison",
    "AdLiftReportCard",
    "ValuationRangeChart",
    "NetPlanScenarioCard",
    "ModelReleaseCard",
    "DecisionAuditTimeline",
]

REQUIRED_CONTRACT_TYPES = [
    "CandidateSiteCardContract",
    "SiteScoreReportSummaryContract",
    "ForecastBandChartContract",
    "FourLightBadgeContract",
    "RootCauseEvidenceCardContract",
    "InterventionTimelineContract",
    "PricingPlanComparisonContract",
    "AdLiftReportCardContract",
    "ValuationRangeChartContract",
    "NetPlanScenarioCardContract",
    "ModelReleaseCardContract",
    "DecisionAuditTimelineContract",
]

REQUIRED_EVIDENCE_FIELDS = [
    "Interval",
    "Confidence",
    "DataQuality",
    "DecisionStatus",
    "JobStatus",
    "ModelStatus",
    "FieldPermission",
    "featureSnapshotTime",
    "modelVersion",
    "policyVersion",
    "audit",
    "dataQuality",
]


def test_frontend_domain_contracts_are_exported() -> None:
    assert FRONTEND_CONTRACTS.exists()
    assert 'export * from "./frontend-contracts.ts";' in INDEX.read_text(encoding="utf-8")


def test_all_documented_domain_components_have_type_coverage() -> None:
    source = FRONTEND_CONTRACTS.read_text(encoding="utf-8")
    docs = COMPONENT_CONTRACTS.read_text(encoding="utf-8")

    for component in DOMAIN_COMPONENTS:
        assert component in docs, f"{component} missing from design component contracts"
        assert component in source, f"{component} missing from frontend domain type coverage"

    for contract_type in REQUIRED_CONTRACT_TYPES:
        assert f"export type {contract_type}" in source


def test_contracts_preserve_evidence_first_decision_metadata() -> None:
    source = FRONTEND_CONTRACTS.read_text(encoding="utf-8")

    for field in REQUIRED_EVIDENCE_FIELDS:
        assert field in source

    forbidden_markers = ["TODO", "TBD", "any;"]
    for marker in forbidden_markers:
        assert marker not in source
