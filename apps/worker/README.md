# Worker App

Event consumers and asynchronous workers. Workers should call module
application services and infrastructure adapters, then emit audit/job status.

The worker image's existing Cloud Run Job entrypoint also supports an audit-only
publisher, using the same image and persistence bundle:

```bash
python product_ops/deployment/cloud_run_job_entrypoint.py audit-publisher \
  --run-for-seconds 60 --max-jobs 100
```

Its atomic claim selects only `canonical-audit-delivery`,
`store-ops-audit-delivery` and `durable-audit-delivery`. Other queued or expired work remains untouched. It
keeps polling during an empty queue until the observation window expires or the
job bound is reached, and exports metrics during idle polls. Existing worker
retry, heartbeat, fence and default-registry receipt checks still apply.

This command must run in separately reserved capacity to protect audit delivery
from busy general workers. Starting it in the general worker slot does not
reserve capacity. Window and batch bounds are operational settings, not proof
of an approved throughput/SLO. Provisioning, schedule overlap, credentials,
least-privilege grants, live load/recovery and release admission follow
`docs/runbooks/canonical-audit-delivery.md`.


Native parser admission can be reserved explicitly for one tenant and source:

```bash
python -m apps.worker.oday_worker.native_intake_parser --tenant-id UUID --source-id SOURCE --limit 25 --include-progress
```

The command selects the single release bound to that source in the trusted
`ODP_NATIVE_INTAKE_PARSER_DEPLOYMENTS_JSON` startup configuration. It never
selects the newest catalog release. Archives must already exist locally; the
artifact URI is an identity, not a download instruction. Missing, malformed,
or unmapped startup configuration fails before admission.

Candidate scans follow the exact current human-assisted transition and its
manual JSON snapshot, with tenant/source/release-bound continuation cursors.
Each candidate passes the existing complete historical and current authority
checks in its own bounded SQL transaction. Each printed admission receipt is
already durable; a later failure preserves earlier receipts. Progress reports
include bounded conflicts and continuation, without exception messages or input
values. A later scan skips an existing parser run, including terminal runs;
this command does not authorize post-terminal reprocessing or create human
parser requests/withdrawals. It enables no deployment schedule or source.

The default worker handles admitted `native-intake-parser` jobs through the
canonical parser owner. That owner controls both job rows, paired leases,
operation journals and result settlement; a handler return alone cannot settle
a delivery. The current parser supports the fixed approved manual JSON format;
provider choice and executable paths cannot come from a job or request.
