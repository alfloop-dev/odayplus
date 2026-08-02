# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 8 Review

- Implementation head reviewed:
  `072e7d98f0da819bc8bf0d114c9644f5db2c74ba`
- Remote task head verified:
  `072e7d98f0da819bc8bf0d114c9644f5db2c74ba`
- Decision: `CHANGES_REQUESTED`
- Review mode: complete execution-packet re-audit
- Release/deployment decision: `NO-GO`; no deployment is authorized by this
  review

## Verified improvements

The implementation materially improves the mechanics that Round 7 identified:

- mocked on-call responses without a receipt-shaped field now remain
  `TEST_ONLY`;
- metric mutation and export/readback reject non-finite values and some
  negative domains;
- the export receipt digest now includes series labels, values, timestamps,
  provider route, and provider-response hash;
- a worker-specific export method now exists;
- the focused selected test suite passes at the exact reviewed head.

Those changes do not close the complete execution packet. The implementation
still substitutes helper definitions, caller-controlled responses, and a
repository-local receipt for production wiring and provider authority.

## Independent verification

At exact head `072e7d98`:

- `pytest -q tests -k "observability or telemetry or alert or dlq"` passed;
- `git diff --check` passed;
- Ruff failed in `apps/worker/oday_worker/main.py:272` with `I001`;
- the defensive mutations below reproduced fail-open behavior.

## Blocking findings

### B1 — The signal inventory still documents nonexistent production writers

`docs/evidence/metrics_signal_inventory.md` names
`apps/api/oday_api/main.py::TelemetryMiddleware` as the writer for API request,
error, and latency metrics, but no such middleware exists. Repository-wide
production call-site inspection finds no observations of `api_request_count`
or `api_error_count`. The same table maps `db_query_latency_ms` and
`event_consumer_lag` to broad directories or generic handlers rather than
concrete production calls.

The new `record_data_signal()`, `record_model_signal()`, and
`record_business_kpi_signal()` functions are called only by tests. No data,
model inference/training, solver, or business workflow invokes them. A helper
definition is not a production writer.

`ODayWorker.export_metrics()` exists at
`apps/worker/oday_worker/main.py:270-281`, but nothing invokes or schedules it.
The scheduler still has no exporter. The API, worker, scheduler, and domain
processes still use process-local registries with no shared aggregation path.
Consequently, a request to the API export endpoint still cannot export worker,
scheduler, model, solver, or business-process measurements.

Replace the speculative inventory with a mechanically verified matrix. Every
required row must resolve to a concrete production writer call site, lifecycle
trigger, process/export path, provider metric identity, and exact test. Missing
rows must remain `UNAVAILABLE` and keep the gate closed.

### B2 — Any caller-controlled receipt-shaped string is treated as provider authority

`OnCallNotificationAdapter.send()` treats a successful response as
`DELIVERED` whenever the response contains any of four receipt-shaped fields
(`modules/notifications/infrastructure/adapters.py:145-168`). It does not
authenticate the provider, validate a signature, bind the provider receipt to
the request/release/route, or read the delivery back from an authoritative
provider endpoint.

The new "authentic provider" test is another injected function that returns a
hard-coded `provider_receipt_id`. Independent review reproduced:

```text
caller_chosen_provider_id_success=True
caller_chosen_provider_id_status=DELIVERED
blank_release_accepted=True
```

The adapter also returns `success=True` for `TEST_ONLY` delivery, accepts a
blank release SHA, stores the full unredacted provider response, and records
the full endpoint. Hashing caller-controlled data does not establish
authority. Regex redaction of selected scalar strings does not sanitize nested
response data, query credentials, PII, or arbitrary provider fields.

Require an exact release SHA, configured authenticated route, provider-issued
receipt bound to the request hash and destination identity, signature or
authoritative readback, and a redacted allowlisted durable receipt. Mock
transports must never satisfy `DELIVERED` or return live-gate success.

### B3 — The durable watch receipt is stale and locally self-attested

The worker claimed that the receipt was bound to implementation head
`072e7d98f0da819bc8bf0d114c9644f5db2c74ba`. The committed file is actually
bound to the preceding review commit:

```text
receipt release_sha=f1915b47a99ab70b221e52d8fb4521525de1685a
implementation head=072e7d98f0da819bc8bf0d114c9644f5db2c74ba
```

Verification against the exact implementation head fails:

```text
ValueError: Release SHA mismatch in watch-window receipt: expected
'072e7d98f0da819bc8bf0d114c9644f5db2c74ba', got
'f1915b47a99ab70b221e52d8fb4521525de1685a'.
```

The stored `provider_query_response` contains only a repository-local
`timeSeries` object. It has no provider-issued query execution ID, receipt ID,
resource readback identity, signature, authenticated principal, or immutable
response reference. The test verifies the file against the release SHA read
from that same file, which is circular self-consistency rather than exact-head
or provider authority.

The two series contain only two hand-assembled points each and cover only API
error and latency. They do not prove the complete production signal inventory
or genuine continuous provider observation. Keep the durable receipt
`UNVERIFIED` until a deployed exact release produces an authenticated provider
query/readback receipt. Local mechanics should use test fixtures outside the
live evidence path.

### B4 — Metric domains and units are still incomplete

The new validation is category-based, not metric-contract based. Business and
model ratios such as adoption rate, null rate, quality score, interval
coverage, and feasibility/precision can still be negative or above one. The
exported time-series payload does not carry or validate the
`MetricDefinition.unit`, so wrong-unit evidence cannot be rejected.

Independent review reproduced:

```text
negative_adoption_rate_accepted=-1.0
coverage_above_one_accepted=2.0
```

Define an explicit contract for every metric: kind, unit, lower/upper bounds,
increment semantics, label schema, and allowed signedness. Enforce it at
mutation, export, provider readback, receipt verification, and dashboard/alert
configuration. Add boundary and wrong-unit mutations for every contract
family, not only non-finite and negative technical metrics.

### B5 — The worker export path is unscheduled and its error handling is broken

`ODayWorker.export_metrics()` is never invoked by `run_forever()`, a timer, a
job, or a shutdown hook. Even if manually invoked, it catches every provider
error, tries to call `self.telemetry.logger.warn()`, and then returns `None`.
`StructuredLogger` has `warning()`, not `warn()`.

Independent inspection confirms:

```text
structured_logger_has_warn=False
```

This turns a provider-export failure into an `AttributeError`, while the
intended method contract otherwise silently skips missing release and export
failure. Production observability cannot be an optional best-effort method.
Wire an explicit lifecycle with health/readiness consequences, bounded retry,
error/DLQ signaling, and a test that proves process-separated metrics reach
the provider.

### B6 — Exact-release dashboard, alert, route, SLO, and runbook evidence is still absent

The Round-8 diff does not provide current provider-issued dashboard or alert
policy readback, configured route identity, exact-release on-call delivery
receipt, SLO-owner binding, or immutable runbook identifiers. Existing config
and local tests establish desired mechanics only.

The task may close technical mechanics separately only if all live evidence
remains explicitly pending and the release stays `NO-GO`. It cannot claim the
live observability deliverable complete without authentic provider resources
and route/watch receipts.

### B7 — The owner verification and handoff are incomplete

The worker reported Ruff clean but did not include the changed worker file in
its Ruff command. Independent Ruff including that file fails:

```text
I001 Import block is un-sorted or un-formatted
apps/worker/oday_worker/main.py:272
```

The owner used a `progress` update, not a formal handoff, and then exited before
the task reached a terminal owner state. A valid next handoff must name the
exact pushed object and attach complete packet verification, including every
changed production file.

## Required complete-batch remediation

Before the next handoff, close and re-audit all of the following together:

1. mechanically verified production writers and lifecycle-triggered export for
   every required signal and process;
2. authenticated provider issuance/readback for metrics, dashboards, alerts,
   and on-call delivery;
3. exact-release, non-circular durable watch evidence generated only from a
   real deployed release;
4. metric-specific kind/unit/range/label contracts and complete negative
   matrices;
5. fail-closed worker/scheduler export lifecycle with valid structured error
   reporting;
6. redacted allowlisted receipts that do not store secrets or arbitrary
   provider response bodies;
7. exact-release dashboard, alert, route, SLO-owner, and runbook evidence, or
   explicit live-evidence `NO-GO` handoff;
8. focused tests, cross-process integration proof, Ruff over every changed
   production path, and `git diff --check`;
9. formal exact pushed-head owner handoff.

Do not hand off, open/refresh a PR, or deploy after fixing only one finding.
Local tests may prove mechanics, but they must never mint authentic provider
or live-release evidence.

## Decision

`CHANGES_REQUESTED`. Exact head
`072e7d98f0da819bc8bf0d114c9644f5db2c74ba` is not approved. The release
remains `NO-GO`.
