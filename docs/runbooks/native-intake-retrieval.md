# Native Intake retrieval

This owner consumes a proven `native-intake-source-policy` result at the current
`RETRIEVING` Intake version. The parent may originate from identity processing or
a proven bounded source-policy recheck. An historical result never grants a
later HTTP or storage operation. `AUTH_REQUIRED` and manual-entry approval do not
grant retrieval.

The runtime is unconfigured by default. The code and migration do not activate a
source, account, tenant, bucket, schedule, credential or deployment. Installation
and source/account approvals remain separate from this engineering scope.

## Configuration

The canonical Intake context must already be installed with its four contract
assets and the additive migrations. Revision `0028` follows this branch's
`0027`; its SQL35 asset adds only anonymous global controls. Other branches'
`0025`/`0026` are separate integration work. SQL35 skips a wholly absent optional
context and rejects a partial context or a partial quota installation.

| Environment variable | Required value |
| --- | --- |
| `ODP_NATIVE_INTAKE_RETRIEVAL_POLICY_JSON` | Closed object: `policy_id`, positive `version`, explicit canonical nonzero `tenant_ids`, `max_attempts` from 1 to 5, `deadline_seconds` from 1 to 120, and positive finite `backoff_seconds` with exactly `max_attempts - 1` entries. |
| `ODP_NATIVE_INTAKE_RETRIEVAL_STORAGE_JSON` | Closed object: positive `version`, `global_max_concurrency` from 1 to 100, and `storage_bindings` covering exactly the configured tenants. |
| Each storage binding | Exactly `tenant_id`, `bucket`, `kms_key_name`, `residency_mode`, `retention_class`. Native residency is `TW_ONLY`; the key parent/version must be in `asia-east1`. Provider region, region type and actual object key version are independently checked. The existing approved bucket-name allowlist is also required. |
| `ODP_NATIVE_INTAKE_RETRIEVAL_SIGNING_KEY` | Separate purpose-bound signing secret of at least 32 bytes; never copied to the child or job payload. |
| `ODP_NATIVE_INTAKE_SNAPSHOT_GCS_TOKEN` | Dedicated printable ASCII storage credential, at most 8192 bytes, read only at dispatch and supplied through a private file descriptor. There is no audit-token, ADC or general Google-token fallback. |

Only the admitted tenant's storage binding enters its job. Credentials and raw
content never enter SQL intents. Changing current configuration invalidates old
execution authority; an admission snapshot is not a future configuration grant.

The 120-second budget includes queue delay, all operations, redirects and
retries. The contract's long backoff sequence cannot fit all five attempts in
that budget. Numeric/SLO reconciliation remains OPEN; the implementation does
not reset the timeout or invent approval for more time.

## Admission and execution

The bounded partition command is
`python -m apps.worker.oday_worker.native_intake_retrieval --tenant-id UUID --limit 25`.
It prints only durable admission receipts. Each delivery is separately signed
and handled by the default worker's transaction-owned retrieval handler.

The owner resolves the immutable original submitter's current active SQL
account, current roles and explicit scope in the same SERIALIZABLE root. It
requires current `submit_url` authority, all native scope axes and permitted
listing-module scope. It does not impose human CONFIDENTIAL clearance, which
URL submission never required. The existing DATA_OWNER-only recheck command
keeps its separate module/clearance requirements.

Acceptance commits a real business RUNNING checkpoint before IO. The owner
renews physical and business leases atomically every 15 seconds for 45 seconds,
stops and joins its heartbeat before every business SQL root, and re-reads both
versions. The generic worker starts no competing heartbeat and infers no
retrieval success.

Every operation has a fresh committed SQL intent, current source/actor/input/
configuration checks and its own concurrency reservation. Each HTTP hop also
charges the global source's rolling 60-second rate ledger. These global ledgers
contain only independent reservation UUIDs, source IDs and database timestamps;
tenant workflow payloads reference them one way. Schema/RLS/reverse-FK changes
fail closed before a new reservation. Source/expiry indexes contain no tenant,
job, Intake or actor identifiers. A 12-second slot conservatively outlives the operation's 10-second
cap, including admission/renewal setup and bounded child cleanup.

The fixed stdlib child uses Python `-I -S`, minimal environment, closed file
descriptors, an owned process group and bounded JSON/base64 IPC. Its HTTP
transport validates every DNS answer, pins a public numeric address and retains
the original Host/TLS hostname. It uses no proxy, cookie, source authentication,
automatic redirect or compression. The owner may follow at most three
redirects with separate intent/quota checks under the same total deadline.
Content is limited to HTML/XHTML and 512000 bytes.

An upload journal commits the exact tenant/job/generation/business-fence/capture
key, hash, byte length, media and capture timestamp before create-only upload.
Unknown upload responses reconcile only that key, then pin an actual positive
object generation and independently hash its downloaded bytes. Late older
attempts are never adopted. Known orphan cleanup requires fresh owned SQL,
no committed snapshot reference, current permissions and exact generation.
Unknown SQL commits and lost fences do not authorize deletion. Unreaped children
retain their slot and unresolved job checkpoint rather than claiming closure.

## Committed outcomes

`PARSING` atomically commits the snapshot metadata, Intake state/version,
transition, canonical audit, both jobs, `intake.state_changed` and
`snapshot.created`. The snapshot association uses the existing transition's
`source_snapshot_id` and the snapshot's `intake_id`; there is no direct Intake
snapshot column. The installed natural key includes the Intake, so equal bytes
in different Intakes get separate owned objects, snapshots and creation events.

The snapshot event uses the real snapshot aggregate/version and the admitted
source-policy event as causation. Its closed payload excludes `object_uri` and
retains the catalog's `payload.object_uri` sensitive-field declaration. Terminal
replay verifies committed evidence without uploading or creating another event.

`FAILED` commits only a still-authorized negative outcome after nonretryable
failure or exhausted time/attempts. Protective settlement after total timeout
requires both actual accepted leases and fences to remain live; it grants no IO.
`QUARANTINED` covers current source denial, authentication walls and prohibited
retrieval. An authentication wall alone never rewrites source policy to
`SOURCE_BLOCKED`.

An already recorded authoritative job-cancellation request settles the jobs
without inventing an Intake `CANCELLED` transition. This phase adds no retrieval
cancellation request API or UI. Lost actor/input/configuration/fence authority cannot mutate the
Intake. Unresolved ownership and recovery/reopening remain explicit follow-up
work. Parser/matcher processing, actual-role UAT, staging/Ops, the task-review
gate and release are outside this retrieval implementation's completion claim.
