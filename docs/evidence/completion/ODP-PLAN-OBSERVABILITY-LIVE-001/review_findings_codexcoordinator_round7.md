# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 7 Review

- Implementation head reviewed:
  `fa23a1c981bd93af0c604fcafbdb53ac2e340302`
- Remote task head verified:
  `fa23a1c981bd93af0c604fcafbdb53ac2e340302`
- Decision: `CHANGES_REQUESTED`
- Review mode: complete execution-packet re-audit after reopen
- Release/deployment decision: `NO-GO`; no deployment is authorized by this review

## Verified improvements

The Round-6 remediation correctly:

- requires independent per-signal and per-category watch-window coverage;
- rejects negative and non-finite values in the watch-window verifier;
- recomputes provider proof and binds it into the canonical watch receipt;
- merges current `origin/dev`;
- passes the independent focused suite at the exact reviewed head:
  `pytest -q tests -k "observability or telemetry or alert or dlq"`.

Those improvements close the prior Round-6 examples. They do not satisfy the
full execution packet because production signal wiring, live route proof,
current durable evidence, and exporter-wide domain/integrity checks remain
open.

## Blocking findings

### B1 — The required signal inventory is catalogued but not wired end-to-end

`PLATFORM_METRICS` declares API, event/queue, data, model, solver-adjacent, and
business KPI definitions, but repository-wide production-call-site inspection
finds actual observations for only a limited subset. The worker records
`job_duration_seconds` and `job_failure_count`
(`apps/worker/oday_worker/main.py:151-188`), assisted-listing records DLQ and
external connector failures, and the audit pipeline records audit metrics.
There are no production observation calls for most data/model/business KPI
definitions.

The only production exporter endpoint constructs an exporter over the API
process's in-memory `default_registry()`
(`apps/api/oday_api/main.py:631-645`). Worker/scheduler metrics are stored in
their own process memory, and no worker/scheduler exporter or shared production
metrics backend was found. Therefore the API exporter cannot export the
worker-process measurements that the acceptance packet requires.

Declaring definitions, dashboard panels, and tests is not equivalent to wiring
signals. Provide an explicit inventory mapping every required signal to its
production writer, shared/export path, provider metric identity, and test.
Missing signals must remain `UNAVAILABLE`/`NO-GO`; they cannot be inferred from
catalog presence.

### B2 — The claimed real on-call delivery is a mock/local success

`test_alert_routing_and_real_notification_delivery()` injects
`mock_transport`, which unconditionally returns HTTP 200
(`tests/reliability/test_runtime_observability.py:525-540`). The adapter treats
any 2xx response as `DELIVERED` and creates its own `delivery_id`
(`modules/notifications/infrastructure/adapters.py:90-136`); it does not require
an authenticated provider-issued receipt, immutable request/response binding,
or provider readback.

The archived evidence itself states that it uses FastAPI TestClient and a local
loopback server and is test-only
(`docs/evidence/completion/ODP-PGAP-OBS-001/evidence.md:26-31`). That evidence
is useful for adapter behavior, but it cannot satisfy the packet's configured
real-route delivery criterion.

Require a redacted, provider-issued delivery receipt/readback tied to the exact
release, configured route identity, alert ID, request hash, delivery time, and
status. Mock/loopback 2xx responses must remain explicitly `TEST_ONLY`, never
`DELIVERED` evidence for the live gate. Do not store or print alert details or
provider responses without an explicit redaction/allowlist policy.

### B3 — The committed watch receipt is stale and invalid under the current verifier

`docs/evidence/watch_window_receipt.json` is bound to release
`10c620969a90627e4a67053a4708658f99faa07f`, not the reviewed head. It also lacks
the current required top-level `gcp_project`, verified readback flag,
time-series proof, per-signal timestamps/values, provider proof hash, and
canonical receipt hash.

Independent verification against its own recorded release fails immediately:

```text
ValueError: Watch-window receipt missing valid non-empty gcp_project.
```

A passing unit test that creates a temporary current-schema receipt does not
replace the durable exact-release artifact required by acceptance. Generate
and archive a current provider-backed receipt only after the required
production signals are genuinely available. Until then, keep the watch status
`UNVERIFIED`/`NO-GO`.

### B4 — Registry/exporter domain checks and export receipt integrity remain fail-open

`MetricsRegistry.increment()`, `set()`, and `observe()` accept negative and
non-finite values (`shared/observability/metrics.py:117-137`). The production
exporter converts values to `float` without enforcing metric-specific domains
or units (`shared/observability/metrics.py:563-595`), and readback only proves
that a value is numeric (`shared/observability/metrics.py:757-778`).

Independent mutation at the exact reviewed head exported a counter value of
`-5.0` and returned `readback_status=SUCCESS`. Exporting `-5.0` and `+5.0` with
otherwise identical series produced the same receipt ID:

```text
negative_export_status=SUCCESS
negative_export_value=-5.0
negative_receipt_id=gcp-cm-readback-aaaaaaaaaaaa-e0a4eeb8b3538932
positive_receipt_id=gcp-cm-readback-aaaaaaaaaaaa-e0a4eeb8b3538932
receipt_collision_across_value_mutation=True
```

The collision occurs because the receipt digest binds only project, release,
series/point counts, and metric types
(`shared/observability/metrics.py:801-805`), not canonical values, timestamps,
units, labels, or provider response identity.

Enforce finite metric-specific domains and units at registry mutation,
pre-export, and readback. Canonically bind all exported/read-back series
identities, labels, values, units, timestamps, query window, provider resource
identity, and provider-issued receipt/proof into the export receipt. Add
negative, NaN, infinity, wrong-unit, value-tamper, timestamp-tamper, and
label-tamper tests.

### B5 — The owner handoff cites a nonexistent exact head

The live task handoff states implementation head
`fa23a1c990263f35fe4d6537bf49547d06e22d99`, but that object does not exist in
the repository. The local and remote task branch both resolve to
`fa23a1c981bd93af0c604fcafbdb53ac2e340302`.

The next handoff must name an exact pushed object verified by
`git rev-parse HEAD` and `git ls-remote`, and must include the complete
acceptance matrix and verification results for that object.

## Required complete-batch remediation

Before the next handoff, close and re-audit all of the following together:

1. production writers and export/readback paths for every required API,
   worker, event/DLQ, data/model, solver, business KPI, and audit signal;
2. dashboards, alert policies, route, SLO owner, and runbook identifiers bound
   to an exact release;
3. authenticated provider-issued and redacted on-call delivery evidence;
4. a current exact-release watch receipt with independent per-signal/category
   coverage;
5. metric-specific domain/unit validation at mutation, export, and readback;
6. canonical receipt integrity over values, timestamps, units, labels,
   provider identity, and response proof;
7. defensive tests covering absent signal writers, cross-process export,
   mock-route rejection, stale receipt rejection, all domain mutations, and
   receipt tampering;
8. exact pushed-head verification, focused tests, Ruff, and
   `git diff --check`.

Do not hand off, open/refresh a PR, or deploy after fixing only one of these
examples. Local and mocked tests may prove mechanics, but authentic provider,
route, watch-window, and deployment evidence must remain pending until it
exists.

## Decision

`CHANGES_REQUESTED`. Exact head
`fa23a1c981bd93af0c604fcafbdb53ac2e340302` is not approved. The prior
watch-verifier corrections are valid, but the complete live observability
execution packet remains open and the release remains `NO-GO`.
