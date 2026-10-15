# Growth approval and lifecycle command authority

Date: 2026-10-12

## Required permissions and preserved boundaries

The foundation RBAC matrix (`shared/auth/rbac.py`, derived from ODP-SA-04 §6)
separates intervention CREATE, APPROVE and EXECUTE. Draft creation, submit and
outcome routes retain their existing CREATE dependency. Growth approval decisions
now require APPROVE and lifecycle transitions require EXECUTE in both the fixture
and durable router factories. Missing approval/execution guard wiring denies the
request; it cannot fall back to CREATE. Existing session, tenant and permission
checks still run before service replay.

No role grant was changed. In the current foundation matrix operations_manager
may create/approve interventions, while regional_supervisor may execute them.
Auditor is read-only. These foundation grants are not the complete production
record-scope, segregation or role-to-domain admission policy.

## Receipt and state invariants

Direct Growth decision and transition APIs use the verified principal subject,
tenant and accepted role rather than body actor claims. They use the existing
private command receipt map introduced for draft create/submit: actor/tenant/key,
complete command and operation, verified role, with changed intent returning 409.
The current action, or approval plus linked action, is loaded before replay.
Legacy raw-key receipts are refused; export/reload preserves the new bindings.

Generic execution cannot write PENDING_APPROVAL, APPROVED or DRAFT in place of
submit/decision commands. It cannot write CLOSED or INEFFECTIVE in place of the
human outcome command. Existing lifecycle and closeout eligibility checks remain;
a passing closeout gate does not replace a decision/audit-producing outcome write.
Approval resolution additionally requires its linked action still be pending,
so a pending approval cannot revive a cancelled action. Approved and executed
remain separate stages.

## UI ownership and bounded retry

Activity detail is keyed by action ID. Departing a selected record invalidates
its pending UI callback; its late response cannot overwrite the next record.
Inline submit retains its key, and lifecycle intent retains a key for the same
record/target. Changed transition target uses another key. A synchronous busy
ref guards duplicate clicks. The client accepts only matching record/status
acknowledgements, and the existing 20-second timeout returns uncertainty.
Errors keep the selected record visible, use an alert and do not assert rollback.
Approval and lifecycle help text meets the checked contrast threshold in its
scoped panel; original R7 geometry remains unchanged.

## Validation and limits

The packet binds the final candidate to component, full web, API/domain assembly,
actual local API browser and three-width supplemental browser evidence. Adversarial
cases include distinct RBAC roles, forged body actors, changed record/request/key,
other actors, unconfigured authority, legacy receipt refusal, tenant mismatch,
export/reload and attempts to bypass decisions with generic transitions.

The first new replay test mistakenly requested OBSERVING from an already observing
fixture; the service correctly returned 409. It now uses the admitted OUTCOME_READY
transition. First browser scans exposed 4.41:1 help-text contrast and were corrected.
The second browser run's unscoped alert selector also matched Next's route announcer;
it is now scoped to the transition panel. Failed outputs remain in the evidence.

This does not admit real-account testing, a deployed candidate, cross-reload retry,
multi-process exactly-once execution, actual campaign execution or canonical
business/audit/outbox atomicity. The live-domain regression uses SQLite assembly,
not actual PostgreSQL/canonical first-party command acceptance. Governance-mediated
actor/replay paths, required reason/segregation policy and all fine-grained scopes
remain distinct acceptance gaps. Private receipt maps are not canonical schemas
or approved retention policies. Independent 40-screen/role review, actual source
and model bindings, operational rehearsal and human release authority remain open.
