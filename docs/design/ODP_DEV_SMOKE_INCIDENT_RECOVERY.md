# Quarantined dev-smoke identity: recovery decision and design

Task: **ODP-DEV-SMOKE-INCIDENT-RECOVERY-PLAN-001** · Owner: Pi · Independent reviewer: Codex2

**DOCS ONLY. Proposed design, not an approved recovery or an execution runbook.**
No credentials, browser/cloud/secret/database/account calls, scripts, endpoints or
framework changes are authorized by this task. Independent plan review is still
required. The safe present decision is **hold credential effects**: the deployed
source has no supported same-account lost-password recovery API, and its bundle
consumer cannot admit a new recovery lineage without a separately reviewed change.
This does not stop invitation #1447, ordinary UI releases using unchanged original
credentials, or independent data/model lanes.

## 1. Incident truth and evidence boundary

The task-scoped [898 handoff](../../support/handoffs/dev-live-898630fe-20261010/README.md)
and [d50 handoff](../../support/handoffs/dev-live-d50cd331-20261010/README.md)
report actual authenticated observations, not simulated creation. The
[evidence packet](../evidence/runtime/ODP-DEV-SMOKE-INCIDENT-RECOVERY-PLAN-001/README.md)
retains the facts and source-analysis boundary when these supervisor-materialized
handoffs are not present in a normal checkout. This worker has not recollected
live evidence; the underlying collectors' receipts are named by the handoff but
not supplied here. Historical observations are not fresh authorization.

| Item | Retained factual state |
| --- | --- |
| Creation consent | `HUMAN-ODP-DEV-SMOKE-20261010-001` (AUTH001), valid for the original bounded creation; not a missing-consent incident and not permission for automatic reset |
| Target | `odp-dev-smoke`, **13faae19-21c6-4663-8e89-b93ea7f1107d**, active, roles exactly `[platform_admin]` |
| Tenant | `e34f2117-de4b-478c-82fd-13c4ef428d42` |
| Preserved original | `17e9cb99-db46-4a07-8e61-6bf9b22cf5d2` (`ajoe734`), actual roles `auditor + platform_admin`; selected status/clearance/scope/identity unchanged, original password still authenticated |
| Invitation | `1a012edb-842b-4846-8699-6ebd4e350218`; issuer is original UUID, accepting actor is target UUID |
| Durable issue / accept | `0b85d791-129a-4dab-8c51-0978c4111a12` / `a9c756a7-cdf6-48f3-9bd9-7c9437958939`; acceptance committed `2026-10-10T16:44:42.681277Z` |
| Interrupted execution | `89dad153-138f-4541-8bd2-e972d59cce08`; plan `sha256:d466580ea9c4b941b92e302669db5777ebf746c6b06e8efb9c812fbc69d581c9` |
| Durable quarantine | Once at 16:52, event `019e39ed-27f9-4a33-bc99-9bd0e561e576`; execution `recovery-required`, **binding NULL** |
| Secret observation | GitHub dev bundle metadata absent at 16:47; no binding intent, acknowledged PUT, decrypted binding or later fresh-job new-bundle consumer proof |
| Uncertainty | Parent killed memory-only child at 600 seconds; no child completion receipt; interrupted-child session cleanup **UNKNOWN** |

Quarantine describes the execution, not account disablement or password rollback.
The lost random password is not recoverable by extending a timeout, retrying the
old child, reading a password hash, invitation replay, memory/log scraping or
password guessing. The parent timeout is the proven interruption boundary;
repeated history reads are a latency concern, not an established Cloud Run,
proxy, signing or per-phase root cause. Preparation failures and failed/limited
collectors remain failures; do not relabel them or reset owner/churn history.
Archived #1448/#1452/#1451 and their frozen sources are untouched.

Latest *provided* runtime observation: Runtime Release **38075833887**, SHA
`d50cd331a53b7aba3a6f2f8fe8919423620f0754`, manifest
`sha256:ef0226f5e81cb8b3567e2cd9175af3806dedeb2799b0d9d7df20c0da69df7af8`,
`dev-admin`, sources disabled, model readiness `not_claimed`. Actual Web and API
own matching identity readbacks and UI checks passed using original credentials.
That success does **not** prove new-bundle consumption, full-product or F11 acceptance.

## 2. Exact deployed source inventory (read only)

All source citations below pin **d50cd331a53b7aba3a6f2f8fe8919423620f0754**
(the observed deployed source and `origin/dev` at analysis). No inference is made
from unapproved invitation work. Source matches deployment by the supplied
handoff; Git inspection alone is not a fresh serving-revision observation.

### Official HTTP surface

| Official operation at this SHA | Authorization / semantics | Incident suitability |
| --- | --- | --- |
| `GET /api/v1/operator/users`, `/{subject_id}`, `/audit-trail` | Authenticated user-view permission, live identity service, verified same tenant | Future authorized reconciliation only; tenant audit must include issuance, not only a target-subject filter |
| `POST /api/v1/operator/users/invitations` | User-update permission, verified principal plus persisted active admin, non-must-change credential and durable live session; returns new 256-bit capability once | Creates an invitation, **not** recovery; existing target email refused |
| `POST /api/v1/operator/users/invitations/{invitation_id}/revoke` | Same persisted admin/tenant boundary | Accepted/revoked invitation refused; cannot undo acceptance or recover account |
| `POST /api/v1/auth/invitations/accept` through Web `POST /auth/invitations` | Hashed expiring single-use invitation capability; no caller tenant/role substitution; Web origin/CSRF, bounded body, service transport IAM | Atomically creates **new** UUID, Argon2 credential, pureadmin role/scope and acceptance audit; rejects consumed capability / existing account, no reset and no login |
| Web `POST /auth/password` | CSRF, durable authenticated **own** session, active own account and **current password verification** | Self-change only; no arbitrary target; lost password cannot satisfy it. Revokes that account's other sessions, so original-admin session would target the wrong account |
| `POST /api/v1/operator/users` / `/{subject_id}/status` | Live same-tenant administration; existing accounts only | Roles/scope/status, not passwords. Disable revokes sessions; no credential recovery. Not authorized as a shortcut |
| `GET` / `POST /api/v1/operator/users/dev-smoke-journal` | Dev + dev-admin; verified persisted original admin UUID and fixed tenant on every call | Inspect, reserve, quarantine and binding stages only. No reset/retry/resume/recovery-execution action |

Sources:
- [users router L168–500](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator_modules/users_roles.py#L168-L500),
  [live operator permission wiring L977–1002](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator.py#L977-L1002),
  [durable router mounting L2139–2178](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/oday_api/main.py#L2139-L2178).
- [acceptance API](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/identity_invitations.py),
  [bounded Web acceptance adapter](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/app/auth/invitations/route.ts),
  [invitation service L119–365](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/invitation_service.py#L119-L365).
- [self password change L92–281](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/app/auth/password/route.ts#L92-L281),
  [identity administration](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/modules/opsboard/application/identity_user_role_management.py).

### Internal facilities are not supported recovery APIs

[PostgresIdentityStore.changePassword L215–225](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/web/src/lib/auth/identityStore.ts#L215-L225)
is an internal SQL hash update, not an authenticated admin-reset endpoint. It
cannot be invoked directly to bypass self-change verification, and by itself
does not atomically couple capability consumption, audit and sessions. Its mock
implementation is not deployed recovery evidence. The
[CredentialService](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/credential_service.py)
provides Argon2id (64 MiB minimum, time cost 3, parallelism 1), not reset authority.
Invitation's SHA-256 token storage is a useful future pattern, not permission to
reuse an accepted invitation as a recovery capability. Bootstrap is not recovery.

### Reservation, binding and consumer blockers

- [ProvisioningJournal L202–334](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/shared/identity/dev_smoke_journal.py#L202-L334)
  reserves AUTH001 once across processes, appends quarantine once and offers no
  reset/resume. Another UUID does not free the authorization root.
- [Server journal L297–373](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/apps/api/app/routes/operator_modules/users_roles.py#L297-L373)
  requires `reserved` for binding operations; this incident is
  `recovery-required`. Existing NULL binding cannot be filled by the old writer.
- [WebInvitationExecutor L864–974](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L864-L974)
  refuses existing username/email, rechecks source/admission/custody and owns
  single-attempt invitation/acceptance. It does not recover an existing identity.
- [Bundle writer L1343–1400](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L1343-L1400)
  composes successful creation and durable intent before one encrypted PUT.
  [GitHub store L977–1079](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L977-L1079)
  requires authenticated pinned custodian, absent metadata, correct repo/key,
  no redirect/retry, and **201**; 204 means replacement, not acknowledged creation.
- [Strict decoder / ACK predicate L1094–1195](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/release/provision_dev_smoke.py#L1094-L1195)
  accepts schema 1 with AUTH001 only. ACK proof requires exactly one reserved
  root plus matching intent/ACK, **without quarantine**. This old root fails.
  A new authorization/schema is refused; relabelling a new bundle AUTH001 does
  not repair provenance. [Gate L2106–2138](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/e2e/check_live_e2e_gate.py#L2106-L2138)
  requires that predicate for bundle consumers; [L3347–3400](https://github.com/alfloop-dev/odayplus/blob/d50cd331a53b7aba3a6f2f8fe8919423620f0754/delivery_toolchain/e2e/check_live_e2e_gate.py#L3347-L3400)
  refuses malformed nonempty bundles without per-field/legacy fallback.

**Conclusion:** an authenticated same-tenant original admin can issue invitations
and inspect/quarantine the original journal, but at this SHA cannot recover the
**same 13faae19 identity** via a newly random, hash-only recovery capability with
atomic credential/audit completion. Two concrete gaps block execution:
(1) supported authenticated same-account recovery API and (2) distinct recovery
journal/bundle-consumer provenance integration. Neither longer timeout nor owner
consent alone supplies those missing capabilities.

## 3. Decision boundary

Keep the incident held pending independent review of a conditional protocol and
a separate narrow owner decision. No source or credential effects follow from
reviewing or merging these documents. Do not resume the original creation task,
rewrite old audit records, invent an ACK, or reinterpret broad “continue” as reset
permission. A future implementation must use a new task/PR, independent security
review, tests/CI and normal deployment admission, never edit frozen deliveries.
