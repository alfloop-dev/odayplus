# ODP-INTAKE-LOAD-001 runtime evidence

This packet records measured execution of the assisted-listing intake durable
adapter. It does not replace staging Cloud SQL, Cloud Tasks, GCS, or Pub/Sub
evidence and does not enable any production flag. The quantitative values in
the reliability contract remain proposed because its named owner approvals are
still pending.

## Reproduce

```bash
uv run pytest tests/performance/assisted_listing_intake tests/reliability/assisted_listing_intake -q
python3 -m ruff check tests/performance/assisted_listing_intake tests/reliability/assisted_listing_intake scripts/load/assisted_listing_intake scripts/chaos/assisted_listing_intake
python3 scripts/load/assisted_listing_intake/run.py --volume 1000 --concurrency 100
python3 scripts/chaos/assisted_listing_intake/run.py
```

`load-report.json` is generated from real concurrent calls to the durable
enqueue, lease, fenced-status-update, parser-workload, and queue-age paths.
Separate submitter and worker barriers prove both observed peak counts instead
of treating worker labels as concurrency. It evaluates the 1,000-row batch maximum,
100-worker local adapter envelope, daily-volume projection, availability,
latency percentiles, queue age, parse completion, and sample-derived
error-budget consumption.

No human review SLA is claimed. This repository has neither production review
timestamps nor the business-calendar service needed to calculate that SLI, so
`human_review_completion_sla` is recorded in `not_executed_targets` and keeps
`production_ready=false`.

`chaos-report.json` records the local product paths for provider-timeout job
failure, duplicate delivery, expired worker lease/fence rejection, durable
database reopen, object-store checksum/generation rejection, queue backlog,
retry-budget exhaustion, FAILED state, and authorized replay. RPO and RTO are
calculated from that measured local drill timeline. Managed-provider, Cloud
SQL, GCS, Cloud Tasks/Pub/Sub, and restore drills remain explicitly listed in
`missed_production_targets`.

## Target disposition

| Gate | Result | Qualification |
|---|---|---|
| Local durable capacity and SLO | Pass | The canonical run observed 100 concurrent submitters and workers; exact p95/p99, capacity, and 0.2% receipt over-target fraction are in `load-report.json`. |
| Human review completion SLA | Not executed | Production review timestamps and business-calendar evaluation are still required. |
| Local recovery | Pass | Exact events, retry transitions, and timings are in `chaos-report.json`. |
| Managed provider latency | Not executed | Local durable timeout failure is covered; provider/network injection is still required. |
| Cloud SQL regional failover | Not executed | Local durable close/reopen is covered; staging regional failover evidence is still required. |
| GCS consistency/reconciliation | Not executed | Product adapter checksum/generation rejection is covered; real bucket reconciliation is still required. |
| Cloud Tasks/Pub/Sub backlog and DLQ | Not executed | Durable queue backlog/replay is covered; managed-service evidence is still required. |
| Owner approval of proposed SLO/RPO/RTO | Pending | Contract section 12 remains fail-closed. |

These qualifications are deliberately retained as missed production-readiness
targets; this task does not weaken or reinterpret them as passed.
