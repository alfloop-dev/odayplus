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
