# Operator Intake canonical workflow acceptance

The operator inbox and V1 Assignment/SLA commands must refer to the same durable
Intake UUID. Before this change, the operator production factory wrote
`operator.assisted_intakes` with an `IN-...` identifier, while the canonical
workflow used another collection and UUID. UI tests that supplied Assignment/SLA
fields did not prove the submission route could create those resources.

## Authoritative data and compatibility

The production operator factory now injects the canonical workflow store and
its existing tenant-bound policy into `OperatorIntakeWorkflowRepository`.
New submissions use the verified actor subject and actual submission time. The
canonical document is the business authority; Assignment and SLA facts are read
only from matching tenant/intake/resource bindings, with their own versions.
The existing operator aggregate may retain presentation caches, but a subsequent
request reloads the canonical repository before serving or mutating Intake.

Legacy `IN-...` records remain readable. Display names, capture timestamps and
old fixtures cannot establish a canonical subject, submission date or workflow
resource. Missing facts remain null. This change does not migrate historical
records, create assignments for them, or grant new identity/source policies.
Batch records can carry their actual creation/update time, but an old display
actor or submitter fallback does not establish a verified subject ID; that
submitted-by fact remains null.

The displayed update instant comes from recorded canonical Intake and matching
Assignment/SLA modification times. Observed time is recorded when retrieval
actually observes the source. Missing values do not become the current time,
and an SLA state label is never sorted as a due timestamp.

## Submission and command transaction

The bridge uses the existing canonical transaction owner and the same database
engine as the operator replay repository. New Intake, workflow bootstrap, audit,
outbox and actor-bound submission replay must commit or roll back together.
A tenant advisory lock serializes duplicate submissions across application
replicas. Repeating an accepted idempotency key returns its saved response
without creating a second Intake, Assignment or SLA.

Approved retrieval through this production bridge requires the shared durable
job queue. No external snapshot side effect should execute inside the HTTP
submission transaction. The worker resolves the tenant-bound canonical Intake
repository and advances its version when saving progress. Retry must refer to
the actual persisted job identity and retain the same workflow resource IDs.
Missing workflow policy/store or a mismatched queue does not silently select a
fixture repository.

Command authorization must run again against the current canonical resource
inside the mutation transaction, including an idempotent replay. Relevant
resource locks prevent ownership changes from racing this check. A prior list,
detail or job response is not write authority. Generic single-Intake job read
and retry routes must resolve the canonical record, validate the reverse job
binding and use the same actor-bound retry lifecycle as the operator endpoint.

Every duplicate lookup must authorize the existing resource before revealing
its ID or state, including a nonterminal conflict. Tenant/row authorization and
field masking precede Inbox filters and counts. A transfer directory response
is only a candidate list; command execution still revalidates actor, target,
policy, version and scope.

## Verification evidence required

| Proof | Required observation |
| --- | --- |
| Actual PostgreSQL HTTP submission | Production operator factory creates one canonical UUID with verified actor, real timestamp, Assignment/SLA IDs and versions. |
| Cross-route lifecycle | The same UUID is assignable/transferable through V1; restarted operator list/detail return the resulting resource IDs, owner and versions. |
| Replay and rollback | Replay creates no extra resources; policy, audit/outbox or replay persistence failure leaves no partial workflow or queued job. |
| Scope | Another tenant cannot read the record; same-tenant out-of-scope duplicates do not reveal its ID. |
| Compatibility | Legacy records do not gain fabricated workflows, actor IDs or dates. |
| Worker/retry | Tenant-bound repository, version advancement, original job identity, and failure behavior remain consistent with the canonical owner. |
| Browser | Compiled UI preserves filters, timestamps and navigation at 1440/1024/390 pixels using explicitly synthetic loopback data. |

Use the recorded commands/results in the candidate's evidence package. Test
names or mocked DTO fields alone do not satisfy these observations. Intermediate
failures, skipped tests and incomplete proof remain visible in that package.

The three native password-session PostgreSQL browser cases require the Web
production build from the same checkout. The existing `product-db` preparation
step builds it once before SQL tests; local runs use the same command:

```sh
ODP_PRODUCT_MODE=production NEXT_PUBLIC_ODP_PRODUCT_MODE=production \
ODP_DATA_BINDING_MODE=live NEXT_PUBLIC_ODP_DATA_BINDING_MODE=live \
ODP_E2E_MODE=false NEXT_PUBLIC_ODP_E2E_MODE=false \
npm run build --workspace=@oday-plus/web
```

Rebuild after source or public build settings change. Per-case database URLs,
session secrets and identity signing material belong to the running fixture,
not this build step. The existing helper starts `next start`; its 90-second
server-readiness limit and the browser's original action/navigation limits
remain unchanged. This avoids counting development compilation after login as
browser navigation. The isolated synthetic records, real session and worker
checks, and existing service-identity requirement still apply; this is not
human UAT or production data admission.

## Remaining release limits

The ordinary loopback packet uses the explicit POC/synthetic path. It does not prove
production source retrieval, authenticated real-role browser actions, live
publisher delivery or formal business UAT. A fixture retrieval method is not an
admitted production adapter; the existing source/security policy gate must not
be bypassed to make a test appear successful.

Real capture/adapters, privacy/source policy authority, production credentials,
40-screen/six-role visual signoff, independent review, manual UAT, deployment
manifest and the nine release authority gates remain separate open criteria.
This runbook changes no approval record and authorizes no production rollout.

## Promotion continuation acceptance

An Operator submission that has been resolved to a listing must be usable by
the approved V1 promotion request and independent-review routes. The canonical
Intake remains authoritative even when an older operator record with the same
identifier is present. Projection must retain the canonical tenant, scope,
submitter, current assignment and version while making the recorded listing
match available to the promotion service. Saving the projection must not replace
the canonical document with a presentation-only shape.

Both promotion entry points must refer to the same durable decision. A request
does not itself create a Candidate. An independent, in-scope reviewer must
approve the current decision, and the resulting Candidate and emitted event
must retain the requested `target_format_code`.

Current linked-resource authorization must precede both a new mutation and a
cached replay inside the transaction. An orphan decision, scope revocation,
self-review, wrong tenant or stale version cannot be made valid by an earlier
successful read. Request, decision, candidate/listing writes, outbox and replay
must use a consistent transaction owner. Arbitrary persistence failures roll
back their partial writes. A failed actual worker attempt retains the candidate
and recorded recovery checkpoint; its committed failure receipt must remain
discoverable after restart without creating a second candidate on replay.
An exception while reserving or committing the job is a transaction failure;
it cannot manufacture a retained receipt for work that was never accepted.

Required isolated PostgreSQL evidence includes an actual Operator submission,
correction and resolution command, V1 request, second synthetic actor review,
fresh-process receipt/candidate readback, both-entry-point identity, replay,
concurrent mutation and rollback. A measured synthetic Listing/Address fixture
can establish local integration behavior; it cannot establish a live geocoder,
source admission, production scoring-model approval or real-role UAT.

Manual entry must retain missing measurements as unknown. Creating a listing
must not manufacture geocode confidence, coordinates, H3 cells, frontage,
scores, evidence IDs or a passed hard-rule result. A business HeatZone identifier
is not a geocoded H3 cell. An unmeasured listing may be saved for follow-up, but
must remain ineligible for promotion until the required evidence exists.

Operator revision preserves the existing Listing and AddressLocation aggregate,
including source identity, geocode provenance, tenant, revision, precision,
confidence inputs and recorded extension fields. A correction to rent, area or
floor does not authorize replacement of unrelated source or location evidence.

Promotion receipts refer to an actual canonical audit row committed with the
command. The request records pending review; the completed review records its
success, rejection or committed score failure. The production persistence bundle
supplies the outbox on the same engine. A missing outbox is an unavailable
dependency, not permission to omit domain events or recorded property identity.
Producer events use the reviewed complete payload registry, with later schema
overlays taking precedence over earlier catalog clauses.

## Candidate implementation: actual SiteScore delivery and result commit

The worker follows the acceptance boundary in
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md` section 7.
Before external inference, its transaction validates the current source and
parent bindings, changes `SCORE_QUEUED` to `COMPLETED`, and records a promotion
audit, completion event and `worker_acceptance` receipt. That receipt binds the
tenant, job, intake, promotion, candidate, promotion version, acceptance fence,
timestamp and actual audit ID. A second connection must be able to read the
committed acceptance while inference is still blocked and no report exists.
The terminal promotion does not reopen when subsequent computation fails.

`workflow.jobs` is authoritative for `intake-sitescore`. The durable delivery
queue reserves the same UUID and contains only `job_id`, `tenant_id`, and a
signed `attempt_token`. API receipts read physical job state, reauthorize the
current parent, and expose only the approved receipt fields. Saving a promotion
must never construct a successful job projection. A new candidate has no score,
recommendation or model version until an admitted model result is committed.

API and worker require PostgreSQL and the same transaction engine for workflow,
listing/candidate, outbox and delivery repositories. Provision
`ODP_INTAKE_SCORE_ATTEMPT_SIGNING_KEY` through the deployment secret mechanism,
with at least 32 bytes of secret material. A missing key prevents enqueue or
execution; inspecting an existing receipt does not need the key. Rotation must
account for outstanding signed deliveries: this implementation has no key ring
and does not authorize automatic resigning of existing jobs.

Enqueue also requires `ODP_INTAKE_SCORE_RETRY_POLICY_JSON`, an explicit object
with `policy_id`, positive integer `version`, `max_attempts` between one and
five, and `backoff_seconds` containing exactly `max_attempts - 1` positive,
finite delays. Each delay must be at most 86,400 seconds. Missing or invalid
configuration returns unavailable and rolls back the enclosing review; there
is no production default. The canonical payload retains the policy snapshot.
The configured policy must still match before accepting or committing an
attempt. An authorized failed-job replay may refresh that snapshot but cannot
increase the recorded attempt budget. Configuration alone is not approval;
production owners must supply policy authority, applicable tenant/model scope,
version and evidence. Synthetic test policy values prove mechanism behavior
only. The schema's `timeout_at` retains the 30-second enqueue deadline; it is
not enforced as an inference execution limit.

The canonical intake must pin its recorded source policy and snapshot. The live
SQL source policy must remain enabled, unexpired and legally licensed; the
snapshot must match the actual tenant/intake/source/candidate lineage.
`MANUAL_ENTRY` requires recorded manual provenance. Retrieved/parser-derived
input additionally requires a successful parser run, a current production
release and exact input/output schema compatibility. Static source defaults,
fixture snapshots and a display name cannot provide these bindings. The guard
does not inspect a GCS object or grant source, storage or parser approval.

External inference runs outside locks. Before result persistence the worker
locks and rereads the parent, candidate, listing, address, authoritative job and
delivery; validates both leases, fence/version, cancellation, input fingerprint
and current source admission; then commits reports, prediction lineage,
candidate changes, job outcome, audit and delivery acknowledgement in
one SQL transaction. Report/prediction IDs are stable per job. Heartbeats update
both job stores and cannot renew an expired canonical or delivery lease.
Acceptance and result commits are separate transactions: failure to persist
the report cannot undo an earlier committed promotion acceptance.

Before acceptance, permanent validation failure or exhausted budget may move
the promotion to `SCORE_FAILED`, preserving the candidate as `SCORING_FAILED`.
After acceptance, a permanent failure updates the job and candidate while
preserving the entire `COMPLETED` promotion receipt. A retryable failure with
remaining budget atomically writes canonical `RETRYING`/`next_attempt_at`,
delivery `QUEUED`/`RETRYING`, and a `sitescore.retry_scheduled` audit. It does not
emit a terminal failure or a manual replay event. The public job receipt shows
`QUEUED` plus delivery state `RETRYING`. Automatic retry keeps the signed
generation, candidate, original prediction time and admission snapshot.

The PostgreSQL factory explicitly enables canonical score scheduling on its
queue. The bridge mirrors eligibility into delivery-only `available_at` in
the same transaction as the authoritative checkpoint. A global queue poll
must not query tenant-protected `workflow.jobs`: a non-bypass worker has no
tenant context before authenticating the selected signed reference. The queue
filters delivery availability before `LIMIT`; a future retry does not consume
an attempt or hide another eligible job. The bridge then checks the signature,
sets tenant context and rechecks canonical due time before execution. A stale
or changed delivery hint never authorizes an early canonical attempt. Claims,
due checks and leases use PostgreSQL `statement_timestamp()`. SQLite uses its
own scheduling column and does not query the canonical workflow schema.
Merely claiming a delivery is not acceptance. Delivery `attempts` count claims,
including recovery before worker acceptance; canonical `attempt` counts starts
at the business checkpoint. The two counters need not match. Canonical workers
use `claim_next` plus the bridge, not the legacy queue `lease`/`nack` API.

The runtime PostgreSQL bootstrap adds nullable `available_at` and its index;
the SQLite bootstrap has an additive migration. Apply and verify schema changes
under the approved migration identity before a restricted runtime starts. The
PostgreSQL DDL requires a table lock, and index creation needs a deployment
window assessment. This implementation does not grant runtime `BYPASSRLS`.
Generic old deliveries with NULL availability remain immediately eligible;
canonical intake score deliveries with NULL availability are parked. Do not
backfill every old score job with the current time: that could erase a retry
delay, bypass a missing policy, or invent worker acceptance. Inventory pending
jobs by authorized tenant, reconcile their canonical checkpoint, signature,
policy and acceptance proof, and use a separately reviewed fenced recovery
procedure. That existing-job recovery procedure remains a release requirement.

Manager retry checks current scope,
version, checkpoint, policy/parser admission and reason, renews the signed
generation, and reuses the job and candidate IDs. Retry-budget overrides are
refused because the current request contract has no incident/change reference.
The UI does not offer ordinary replay for an exhausted dead-letter receipt.
For a fresh score execution, the failed request also remains the candidate's
head: a new idempotency key or a larger configured budget cannot reopen it or
admit another execution for that candidate. The dead-letter event reports this
state; it does not authorize recovery or reset. Recovery of this existing job
still requires the separately reviewed fenced procedure described above.
Manual replay cannot skip a pending automatic retry delay.
Repeated expired attempts settle in dead letter at the recorded limit. An old
attempt whose input changed cannot commit; a fresh delivery claim settles that
stale checkpoint, after which authorized repair/replay can proceed. Cancellation
discards the result and settles a fresh claimed delivery without inference.
Cancellation does not emit `sitescore.failed` or invent a promotion transition.
The promotion and candidate remain at their recorded checkpoint. The contract
does not define reopening a promotion cancelled before acceptance; that product
policy remains unresolved and must not be represented as an implemented path.

A durable SQL queue is not proof of Cloud Tasks transport. The
contract's 30-second SiteScore enqueue budget is not an approved inference
timeout/backoff policy; inference-level hard limits and external transport
admission still require separate production validation.

Required local proof includes real PostgreSQL enqueue, worker success and
restart readback; signed-reference tampering; concurrent parent/registry/fence
changes; lease expiry and heartbeat; cancellation and budget exhaustion;
failing model admission; atomic storage rollback; and an actual failed-job
retry reaching a result with the original IDs. Synthetic computation proves
these boundaries only. Production model approval, live object integrity,
denial/replay audit delivery, physical property reconciliation, cross-listing
active-candidate uniqueness, full visual/UAT evidence and release authority
remain separate acceptance criteria.

## Explicit target format and recorded hard-rule evidence

The approved V1 request accepts an explicitly supplied `target_format_code`
string of 1–64 characters. There is no approved enumeration or tenant catalog
in this contract. The request UI starts empty and preserves the exact entered
code, including case and whitespace; it does not infer a format from area or
select a fixture value. Drafts and retry keys stay with the same Intake and
survive version refresh, but do not carry over to a different Intake.

The promotion receipt now adds an optional nullable `target_format_code` copied
from the persisted decision. Request, lookup, review and replay return that
same value. The independent reviewer sees it in the receipt and confirmation;
a historical missing value remains unavailable and cannot be filled from a
browser draft to enable approval. This additive response field is an
implementation enhancement; no new format catalog or membership policy is
introduced or assumed to be approved.

`hardRuleFailures` is nullable: missing/null means unmeasured, an explicitly
recorded empty array means no recorded failures, and a nonempty array means
failed rules. Both canonical promotion and live compatibility conversion need
a recorded passing verdict. Synthetic fixtures must seed that evidence
explicitly; geocoding or a missing field cannot imply a passing verdict.

Live legacy conversion cannot create a candidate without the canonical
promotion request and independent review. Compatibility resynchronization
requires an existing candidate and matching tenant/listing/candidate/target
approval with distinct proposer and reviewer. The writer must preserve the
original creator, property/observation lineage and measured point-in-time
features. No historical business record is migrated by this change.
