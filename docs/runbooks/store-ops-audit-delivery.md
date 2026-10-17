# Store Ops audit delivery and interrupted GCS writes

Candidate task trace: `ODP-SYS-ACCEPT-261011`.
Implementation: `modules/opsboard/application/store_ops_audit_delivery.py`,
`apps/worker/consumers/store_ops_audit.py`, `shared/audit/worm.py`.

This runbook describes the runtime delivery path. It does not authorize a
production release, an IAM change, or a retention-policy decision. The existing
release registers remain the authority for production admission.

## Transaction and delivery

The live Store Ops route writes the scoped business state, runtime audit event,
replay receipt, delivery intent and `store-ops-audit-delivery` job in one
PostgreSQL transaction. Queue persistence uses the same engine. A failed audit,
receipt or enqueue rolls the transaction back.

After commit, the request may attempt external delivery. The registered
`ODayWorker` handler also retries the committed intent, so recovery does not
require another HTTP request. Before delivery, the handler checks the tenant,
event identifier, configured sink, queued event integrity and committed audit
hash. An external failure keeps the business result committed and delivery
pending; it never manufactures a successful immutable receipt.

For GCS, every upload retains `ifGenerationMatch=0`. A `412` response triggers
an exact-object read with `alt=media`, followed by verification of record type,
record ID, sequence, checksum, payload hash and the original timestamp. Only
then is a receipt returned. Recovery never deletes, replaces or patches an
existing object. A normal first upload does not need the recovery credential.

## Credentials and release prerequisites

The writer continues to use `ODP_AUDIT_WORM_GCS_TOKEN` or
`GOOGLE_OAUTH_ACCESS_TOKEN`. Recovery uses the separate, explicit
`ODP_AUDIT_WORM_GCS_RECOVERY_TOKEN`, or the constructor's `recovery_token`.
It does not fall back to the writer credential. Both upload and read reject
redirects, so neither credential is forwarded to another endpoint.

Cloud Storage object reads require `storage.objects.get`. The existing
Terraform writer binding is `roles/storage.objectCreator`; this change does
not add read, overwrite or delete permission to that account. A human-approved
recovery principal and its exact approved bucket/prefix scope must be recorded
by the Security, Privacy and Platform/SRE owners before live recovery is
admitted. Do not reuse the retention manager's object-admin token.

The production review must also bind the exact code/artifact and deployment
target, approved residency, retention/hold policy, credential refresh and
rotation, worker availability, retry/dead-letter monitoring, and recovery
evidence. No static access-token example is evidence of production credential
refresh. The owner approvals and live evidence in the existing release
registers must remain pending until their actual requirements are satisfied.

Runtime `durable_audit_events` and its WORM delivery are distinct from the
canonical `audit.audit_events` retention-policy contract. These recovery
checks do not certify that the Store Ops canonical audit integration is complete.

## Verification before a live canary

Use an isolated synthetic tenant and a separately authorized test bucket.
The action manifest must bind the actor, tenant, record, mutation, bucket,
scope, candidate digest and execution window before any live business write.

| Scenario | Required evidence |
|---|---|
| First create | One immutable object; upload contains `ifGenerationMatch=0`; correct checksum and original receipt. |
| Response lost after create | Business and audit committed; delivery intent/job pending; exactly one object exists. |
| Worker restart/retry | Fresh PostgreSQL connection; worker consumes the durable job; retry receives 412; exact-object read verifies the original bytes; intent delivered and job succeeded. |
| Missing read credential | No GET with the writer credential; no receipt; intent pending and job retrying or dead-letter according to the queue budget. |
| Read denied or unavailable | Pending remains pending; committed business state and immutable object remain unchanged. |
| Wrong record/hash/type/sequence/timestamp | Recovery rejected; no overwrite and no delivered receipt. |
| Redirect | Rejected; no credential reaches the redirect destination. |
| Replay after recovery | Business mutation not repeated; no duplicate business audit or immutable object. |

The repeatable local checks are:

```bash
.venv/bin/python -m pytest tests/integration/test_gcs_worm_recovery.py -q
# INTAKE_TEST_DATABASE_URL must identify an isolated disposable PostgreSQL 16.
.venv/bin/python -m pytest tests/integration/test_store_ops_scoped_postgresql.py -q
```

The HTTP tests translate the production GCS endpoint only inside the test
fixture to a loopback protocol server. They use synthetic credentials and do
not connect to Google Cloud. The PostgreSQL tests exercise the actual route,
durable queue, default worker registry and connection restart against that
local protocol server. Neither suite proves live IAM, bucket retention locks,
residency, live credential rotation or production SLOs.

## Failure handling

Use the existing queue's retry/dead-letter controls. Correlate the job's event
ID with the committed delivery intent and runtime audit row. A missing
credential, 403, failed read, content mismatch or wrong configured target is a
failed recovery, even if an object is visible in a console.

Do not mark a pending intent delivered by editing its document, fabricate a
receipt, delete the object, remove the generation precondition, or reexecute
the business mutation. After the approved configuration or availability is
restored, retry the same committed event through the governed worker queue.
Production requeue/rollback actions still require the incident and release
authority specified by the existing runbooks.

## Partial health and workflow availability

Issue, evidence and audit availability is independent of optional health
measurements. A valid scoped issue remains readable and its typed workflow
remains usable when health slots are unavailable. Original tenant, store,
actor, source-record hash, transaction, receipt and compare-and-swap checks
still apply; malformed workflow data retains the existing unavailable error.

Production summary/issues responses use nullable values for the existing
three light enums and for `riskScore`. Each store's `healthAvailability`
contains `state`, `complete`, four `lights` slots and a `riskScore` slot. Slots
use `state`, `source`, optional `recordIds`, `reasonCode` and `message`.
Top-level `healthAvailability` carries `totalCount`, `coveredCount`,
`unavailableCount` and `warningCount`; warnings may overlap incomplete stores.
Each `fourLightSummary` dimension adds `unavailableCount`. Unknown values are
excluded from colored counts and green filters, while their stores and issues
remain present. An empty store list has zero counts and is complete.

Only alerts read from the existing ForecastOps owner during this authorized
request can supply a demand observation. Closed/removed alerts cannot keep a
retained issue's old color alive. Actual policy green is a single observed
dimension, not overall healthy status. Missing/read-failed optional health
ports yield null slots with generic availability reasons, without leaking
connection details or failing an otherwise valid issue command. Generic
issues, an empty queue, old colors, source-name strings, metadata flags and a
zero score do not establish equipment, cleaning, margin or risk measurements.
The current missing owners remain null; no source admission or model maturity
is inferred. Evidence confidence is null when the source did not measure it.

Snapshot normalization operates on a copy. Reads never rewrite the persisted
store master or history used by scoped command validation. PostgreSQL fixture
setup writes/observes legacy global poison through its raw document-store
handle; the public unbound live repository still refuses access. The SQLite
materializer tests use an explicit PostgreSQL-dialect unit double, and the CAS
engineering controls use ordinary SQLite. Neither is native PostgreSQL proof.

## Protocol references

- [Cloud Storage request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)
- [Objects: insert](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/insert)
- [Objects: get and required permissions](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get)
- [Cloud Storage IAM roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles)
