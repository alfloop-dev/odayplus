# Performance Tests

Load, latency, and capacity tests.

`test_acceptance_budgets.py` defines the QA-05 release budgets for API P95,
frontend render, batch/job runtime, solver turnaround, RPO/RTO, restore drills,
and observability fields. Production readiness evidence must use these budgets
as blocking gate targets.
