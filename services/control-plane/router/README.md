# Control-plane signal router contract

Contract version: `1.0.0`. The router validates the canonical
`services/research/schema.json` envelope and selects a deterministic destination
from `(domain, intent)`. It does not authorize business actions, publish to a
transport, own queue retry configuration, or mutate signal-store state.

Adapters must preserve `signal_id`, `tenant_id`, `idempotency_key`, and
`correlation_id` from `RoutingDecision`. Delivery is at least once. A retry uses
the same idempotency key; `retry_after_seconds` is a minimum delay. Invalid or
unsupported envelopes, missing routes, terminal downstream rejection, and
retry exhaustion go to a tenant-scoped dead-letter store. Expired signals are
dropped with an audit/metric event and must not be delivered.

## Monitoring handoff

The control-plane team owns router dashboards, alerts, and the five metrics in
`METRIC_CONTRACT`. Destination service owners named by `RouteTarget.owner` own
delivery health after acceptance. The messaging/platform team owns queue depth,
retry scheduling, and dead-letter availability. Security owns tenant-isolation
alerts and access to dead-letter payloads.

Metric labels are the exact low-cardinality tuples declared in
`METRIC_CONTRACT`; never label with tenant, signal, correlation, or idempotency
identifiers. Logs may carry those identifiers under existing retention and
access controls.

Initial alert handoff:

- page control-plane when failure ratio exceeds 5% for 10 minutes;
- page the destination owner when delivery failures exceed 5% for 10 minutes;
- page messaging/platform when oldest retry age exceeds 15 minutes or the
  dead-letter path is unavailable;
- ticket the producing team for repeated `invalid_signal`,
  `unsupported_version`, or `route_not_found` failures.

Every alert links the dashboard, runbook, destination owner, and a
tenant-authorized dead-letter replay procedure. Replay must revalidate the
envelope and retain the original idempotency key.
