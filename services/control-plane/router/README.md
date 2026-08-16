# Control-plane routing contract

Contract Version: `1.0.0`
Artifact Path: `services/control-plane/router/contract.py`
Upstream Schema: `services/research/schema.json` (signal envelope `1.x.y`)

## Overview

The control-plane router is the single decision point between research signal
producers and execution destinations. It validates the canonical signal
envelope and selects a deterministic destination from `(domain, intent)`.

Routing is a pure decision in the sense that `route()` reads only the envelope,
the injected routing table, and the injected clock, and produces no side
effects. It is deterministic **for a fixed evaluation time**, not across time:
steps 3 and 4 of the resolution order compare `effective_at` and `expires_at`
against `now`, so one unchanged envelope can fail `not_effective`, later return
a `RoutingDecision`, and later still fail `expired`. Adapters must therefore
re-evaluate an envelope on every redelivery instead of caching or replaying an
earlier outcome, and tests that need a stable result must inject a fixed clock
through `SignalRouter(now=...)`.

### In scope

- validating the envelope against `services/research/schema.json`;
- rejecting unsupported envelope major versions;
- applying `effective_at` / `expires_at` time windows;
- selecting the destination and its owning team;
- naming the stable failure codes, dispositions, and retry semantics that
  adapters translate without guessing;
- declaring the router metric contract.

### Not in scope

The router does not authorize business actions, publish to a transport, own
queue or retry configuration, or mutate signal-store state. Those remain
adapter and platform concerns.

## Routing decision

`SignalRouter.route()` returns a frozen `RoutingDecision`:

| Field | Type | Description |
| --- | --- | --- |
| `contract_version` | `string` | Router contract version that produced the decision (`1.0.0`). |
| `signal_id` | `string` | Envelope `signal_id`, carried through unchanged. |
| `tenant_id` | `string` | Envelope `tenant_id`, used for tenant-scoped delivery and dead-letter isolation. |
| `destination` | `string` | Logical destination name selected from the routing table. |
| `destination_owner` | `string` | Team accountable for delivery health after acceptance. |
| `idempotency_key` | `string` | Envelope `idempotency_key`, stable across every retry of the same signal. |
| `correlation_id` | `string` | `trace.correlation_id`, propagated for cross-service tracing. |

Adapters must preserve `signal_id`, `tenant_id`, `idempotency_key`, and
`correlation_id` verbatim. Rewriting any of them breaks deduplication or
tenant isolation downstream.

## Routing table

`DEFAULT_ROUTES` maps `(domain, intent)` to a destination and its owner. The
mapping is exhaustive: an unlisted pair is a `route_not_found` failure, never a
default or best-effort destination.

| Domain | Intent | Destination | Destination owner |
| --- | --- | --- | --- |
| `sitescore` | `decision_recommended` | `site-review` | `network-platform` |
| `forecast` | `decision_recommended` | `forecast-review` | `planning-platform` |
| `intervention` | `execution_requested` | `intervention-execution` | `operations-platform` |
| `pricing` | `execution_requested` | `pricing-execution` | `pricing-platform` |
| `adlift` | `decision_recommended` | `campaign-review` | `growth-platform` |
| `valuation` | `decision_recommended` | `valuation-review` | `network-platform` |
| `netplan` | `decision_recommended` | `network-review` | `network-platform` |
| `model_release` | `model_release_requested` | `model-release-review` | `ml-platform` |
| `model_release` | `rollback_requested` | `model-rollback` | `ml-platform` |

Callers may inject an alternative table through `SignalRouter(routes=...)` for
tests or staged rollout. Every injected target must declare a non-empty
destination name and a non-empty owner: `SignalRouter.__init__` validates the
table up front and raises `ValueError` for any entry that does not, so an
unowned destination fails at construction rather than at delivery time.

## Resolution order

The router evaluates a signal in a fixed order and stops at the first failure,
so the reported code is always the earliest reason the signal could not route:

1. **Schema validation** — the envelope must satisfy the canonical schema.
   Errors are ordered by path and the first is reported.
2. **Major version** — `signal_version` must be in the supported major
   (`1.x.y`).
3. **Effectiveness** — a future `effective_at` defers the signal.
4. **Expiry** — an `expires_at` at or before now drops the signal.
5. **Destination lookup** — `(domain, intent)` must exist in the routing table.

## Failure contract

Every failure raises `SignalRouteError` carrying a `RouterFailure` with a
stable code, a disposition, and a retryability flag. Adapters branch on the
disposition, not on the message text.

`FAILURE_CONTRACT` in `contract.py` is the single source of truth for the two
middle columns below — every `RouterFailure` is built from it, and the doc
conformance tests assert this table matches it exactly, code for code.

| Code | Disposition | Retryable | Remediation owner |
| --- | --- | --- | --- |
| `invalid_signal` | `dead_letter` | no | Producing team — envelope does not satisfy the canonical schema. |
| `unsupported_version` | `dead_letter` | no | Producing team — envelope major version is outside `1.x.y`. |
| `not_effective` | `retry` | yes | Router — redeliver after `retry_after_seconds`. |
| `expired` | `drop` | no | Producing team — signal outlived `expires_at`; audit only. |
| `route_not_found` | `dead_letter` | no | Control-plane — add the route, or producing team if the pair is wrong. |
| `downstream_unavailable` | `retry` | yes | Destination owner — transient delivery failure. |
| `delivery_rejected` | `dead_letter` | no | Destination owner — terminal rejection by the destination. |
| `retry_exhausted` | `dead_letter` | no | Destination owner and messaging/platform — retry budget spent. |

`delivery_failure()` translates adapter-observed delivery outcomes into these
same semantics: a retryable downstream failure below `max_attempts` yields
`downstream_unavailable`, exhausting the budget yields `retry_exhausted`, and a
non-retryable downstream response yields `delivery_rejected`.

## Delivery semantics

- Delivery is **at least once**. Destinations must be idempotent on
  `idempotency_key`.
- A retry reuses the **same** idempotency key. Generating a fresh key turns a
  retry into a duplicate execution.
- `retry_after_seconds` is a **minimum** delay, not a schedule. Adapters may
  back off longer; they must not deliver sooner.
- Invalid or unsupported envelopes, missing routes, terminal downstream
  rejection, and retry exhaustion go to a **tenant-scoped** dead-letter store.
- Expired signals are **dropped** with an audit and metric event, never
  delivered and never dead-lettered for replay.

## Monitoring handoff

### Metric contract

Emit exactly these metrics with exactly these labels:

| Metric | Labels | Purpose |
| --- | --- | --- |
| `control_plane_router_decisions_total` | `destination`, `domain`, `intent` | Successful routing decisions; the denominator for routing health. |
| `control_plane_router_failures_total` | `code`, `disposition` | Routing failures split by contract code and disposition. |
| `control_plane_router_delivery_attempts_total` | `destination`, `outcome` | Adapter delivery attempts, including retries. |
| `control_plane_router_route_duration_seconds` | `destination` | Routing decision latency. |
| `control_plane_router_oldest_retry_age_seconds` | `destination` | Age of the oldest pending retry; the retry-backlog signal. |

Labels are the exact low-cardinality tuples declared in `METRIC_CONTRACT`.
Never label with tenant, signal, correlation, or idempotency identifiers — they
are unbounded and will degrade the metrics backend. Logs may carry those
identifiers under existing retention and access controls.

### Ownership

| Surface | Owner |
| --- | --- |
| Router dashboards, alerts, and the five contract metrics | Control-plane team |
| Delivery health after destination acceptance | Team named by `RouteTarget.owner` |
| Queue depth, retry scheduling, dead-letter availability | Messaging/platform team |
| Tenant-isolation alerts and dead-letter payload access | Security team |

### Initial alert routing

| Condition | Page |
| --- | --- |
| Router failure ratio above 5% for 10 minutes | Control-plane |
| Delivery failures for one destination above 5% for 10 minutes | That destination's owner |
| Oldest retry age above 15 minutes, or dead-letter path unavailable | Messaging/platform |
| Repeated `invalid_signal`, `unsupported_version`, or `route_not_found` | Ticket the producing team (no page) |

Every alert links the dashboard, the runbook, the destination owner, and a
tenant-authorized dead-letter replay procedure. Replay must revalidate the
envelope against the canonical schema and retain the original idempotency key;
a replay that mints a new key is a duplicate execution, not a recovery.

## Versioning and compatibility

- `ROUTER_CONTRACT_VERSION` is reported on every decision so consumers can pin
  behaviour.
- Additive routes and additive optional fields are minor changes. Consumers
  must tolerate destinations they do not yet know.
- Removing a route, renaming a destination, or changing a code's disposition or
  retryability is breaking and requires a major bump plus destination-owner
  sign-off.
- The router tracks the signal envelope major version through
  `SUPPORTED_SIGNAL_MAJOR`. A schema `2.0.0` requires an explicit router
  opt-in, not an implicit upgrade.
