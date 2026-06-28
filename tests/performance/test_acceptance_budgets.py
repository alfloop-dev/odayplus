"""Performance, reliability, DR, and observability acceptance budgets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LatencyBudget:
    budget_id: str
    surface: str
    target_p95_seconds: float
    evidence_type: str
    required_concurrency: tuple[int, ...] = (10, 50, 100, 200)


API_BUDGETS: tuple[LatencyBudget, ...] = (
    LatencyBudget("PERF-API-QUERY", "general query APIs", 1.5, "PERFORMANCE_REPORT"),
    LatencyBudget("PERF-API-DETAIL", "detail query APIs", 2.0, "PERFORMANCE_REPORT"),
    LatencyBudget("PERF-API-JOB", "job creation APIs", 1.0, "JOB_LOG"),
    LatencyBudget("PERF-API-SITESCORE", "SiteScore report read", 3.0, "PERFORMANCE_REPORT"),
    LatencyBudget("PERF-API-LIST", "large list query", 3.0, "PERFORMANCE_REPORT"),
    LatencyBudget("PERF-API-AUDIT", "audit decision query", 3.0, "PERFORMANCE_REPORT"),
    LatencyBudget("PERF-API-EXPORT", "export job creation", 1.0, "JOB_LOG"),
)

FRONTEND_BUDGETS: tuple[LatencyBudget, ...] = (
    LatencyBudget("PERF-FE-SHELL", "app shell first usable", 3.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-ROUTE", "route transition", 1.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-LISTING", "listing first page render", 2.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-SITESCORE", "SiteScore report render", 2.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-STORE", "store detail render", 2.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-MAP", "map initial layer", 3.0, "TEST_REPORT"),
    LatencyBudget("PERF-FE-CHART", "chart interaction", 0.5, "TEST_REPORT"),
    LatencyBudget("PERF-FE-COMMAND", "command palette open", 0.3, "TEST_REPORT"),
)

JOB_BUDGETS = {
    "listing_import_1000_rows_minutes": 10,
    "sitescore_report_hours": 24,
    "avm_report_business_days": 3,
    "netplan_proposal_hours": 48,
}

DR_TARGETS = {
    "rpo_minutes": 60,
    "rto_minutes": 240,
    "required_restore_drills": {
        "cloud_sql",
        "bigquery",
        "model_artifacts",
        "audit_evidence",
        "iac_state",
    },
}

OBSERVABILITY_FIELDS = {
    "timestamp",
    "service",
    "environment",
    "severity",
    "correlation_id",
    "request_id",
    "entity_id",
    "error_code",
}


def test_api_performance_budgets_match_qa05_targets() -> None:
    assert {budget.target_p95_seconds for budget in API_BUDGETS} <= {1.0, 1.5, 2.0, 3.0}
    for budget in API_BUDGETS:
        assert budget.target_p95_seconds <= 3.0
        assert budget.required_concurrency == (10, 50, 100, 200)
        assert budget.evidence_type in {"PERFORMANCE_REPORT", "JOB_LOG"}


def test_frontend_performance_budgets_match_qa05_targets() -> None:
    budget_by_id = {budget.budget_id: budget.target_p95_seconds for budget in FRONTEND_BUDGETS}
    assert budget_by_id["PERF-FE-SHELL"] == 3.0
    assert budget_by_id["PERF-FE-CHART"] == 0.5
    assert budget_by_id["PERF-FE-COMMAND"] == 0.3
    assert max(budget_by_id.values()) <= 3.0


def test_batch_solver_and_dr_targets_are_release_gated() -> None:
    assert JOB_BUDGETS["listing_import_1000_rows_minutes"] <= 10
    assert JOB_BUDGETS["sitescore_report_hours"] <= 24
    assert JOB_BUDGETS["avm_report_business_days"] <= 3
    assert JOB_BUDGETS["netplan_proposal_hours"] <= 48
    assert DR_TARGETS["rpo_minutes"] <= 60
    assert DR_TARGETS["rto_minutes"] <= 240
    assert len(DR_TARGETS["required_restore_drills"]) == 5


def test_observability_fields_support_perf_security_and_audit_evidence() -> None:
    assert {"correlation_id", "request_id", "entity_id"} <= OBSERVABILITY_FIELDS
    assert {"timestamp", "service", "environment", "severity"} <= OBSERVABILITY_FIELDS
