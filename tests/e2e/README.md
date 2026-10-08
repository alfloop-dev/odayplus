# E2E Tests

End-to-end business flow tests.

`test_acceptance_coverage.py` is the formal QA-03 acceptance registry. It maps
each E2E scenario ID to its deterministic dataset, automation reference,
business role, route or surface, and required audit evidence.

`test_live_business_journeys.py` guards the six live business journeys
(operations, growth, expansion, governance, franchise, intake) run by
`delivery_toolchain/e2e/live_business_journeys.py`. Its `BusinessWeb` double is
an offline fixture and never live acceptance evidence; see
`docs/design/ODP_BUSINESS_LIVE_JOURNEYS.md`.
