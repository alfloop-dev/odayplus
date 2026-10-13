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

## Remaining release limits

The loopback browser uses the explicit POC/synthetic path. It does not prove
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

The behavior below is implemented and locally tested, but two lifecycle
semantics remain a release blocker against
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_STATE_CONTRACTS.md`:
the reviewed `SCORE_QUEUED` to `COMPLETED` precondition is worker job
accepted/committed (line 246), while this candidate waits for a successful
report commit; the reviewed `SCORE_FAILED` precondition is exhausted budget or
permanent validation failure (line 247), while this candidate also settles a
transient inference failure immediately. Schema/enum validation does not approve
either interpretation. Align the implementation with the reviewed lifecycle,
or obtain an explicit reviewed contract change, before accepting this workflow.

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
candidate changes, promotion completion, audit and delivery acknowledgement in
one SQL transaction. Report/prediction IDs are stable per job. Heartbeats update
both job stores and cannot renew an expired canonical lease.

A failed computation moves the actual job to `FAILED` and the promotion to
`SCORE_FAILED`, preserving the candidate. Manager retry checks current scope,
version, checkpoint, policy/parser admission and reason, renews the signed
generation, and reuses the job and candidate IDs. Retry-budget overrides are
refused because the current request contract has no incident/change reference.
Repeated expired attempts settle in dead letter at the recorded limit. An old
attempt whose input changed cannot commit; a fresh delivery claim settles that
stale checkpoint, after which authorized repair/replay can proceed. Cancellation
discards the result and settles a fresh claimed delivery without inference.

In this candidate, `SCORE_QUEUED` becomes `COMPLETED` only through successful
report commit; this timing is subject to the unresolved contract issue above.
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
