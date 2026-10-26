# Dev smoke principal isolation — read-only diagnosis

Task: `ODP-DEV-SMOKE-PRINCIPAL-ISOLATION-001` · Owner: Pi · Reviewer: Codex  
Recorded: 2026-10-09 · Scope: documentation only · Verification: none (task declaration)

## Outcome

**Human/Ops approval is required before runtime remediation.** The configured
smoke subject is the business-enabled account `ajoe734`, not a separate
least-privilege administrator. No eligible alternative identity is established
by the available records. Keep the finite `dev-admin` role policy unchanged;
keep all existing account roles, scope and status unchanged.

This completes the diagnosis, not principal isolation execution or deployment
acceptance. Independent Codex review is required. No account, password, session,
IAM, secret, configuration, source, model, workflow or gate was changed by this
worker. No live login, credentials access, deny probe, grant or UI run occurred.

## 1. Exact standing binding (non-secret)

Repository inspection used fetched `origin/dev`, commit
`2fe3ef933794d0bb293449b74dffe9602d53f767`, not worktree configuration:

- `.github/workflows/deploy-dev.yml`, deploy job / `Deploy Cloud Run by immutable
  digest`: exports `ODP_DEV_ADMIN_USERNAME` from environment variable
  `vars.ODP_DEV_ADMIN_USERNAME`, `ODP_DEV_ADMIN_PASSWORD` from the corresponding
  secret, optional `ODP_DEV_ADMIN_INITIAL_PASSWORD` from its secret, and
  `ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE` from its variable.
- `product_ops/deployment/deploy_cloud_run_waji.sh:58–63`: only when the primary
  username/password is empty does each independently fall back to
  `ODP_DEV_BOOTSTRAP_ADMIN_USERNAME` / `ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD`.
  It exports the resulting primary pair for the gate. These are fallback
  inputs, not an independently selected second smoke journey. A username-only
  rebinding is unsafe: the corresponding password must belong to that subject.
- `delivery_toolchain/e2e/check_live_e2e_gate.py` reads that primary pair and
  signs in through Web `/login`; the cookie/BFF establishes the API principal.
  A role/persona header cannot replace this persisted identity.
- Read-only GitHub variable queries at approximately **22:45 UTC** returned
  `ODP_DEV_ADMIN_USERNAME=ajoe734`,
  `ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE=cs-lead` (both exit 0).
  `gh variable get ODP_DEV_BOOTSTRAP_ADMIN_USERNAME --env dev` returned
  `variable ... was not found` (exit 1). This does not establish absence of
  repository/organization fallbacks or reveal any password binding contents.
- The preserved failed-run log independently records the effective inputs
  `ajoe734` / `cs-lead` for run `37960951819`; passwords are masked. Secret
  values were neither fetched nor copied into this document.

The failed candidate's gate/workflow files have no diff against the inspected
`origin/dev` versions. The supplied worker-seeded `ai-status.json` is not live
task truth; canonical `ai-status.sh show` confirmed Pi/Codex, `in_progress`.
The supplied handoff is preserved task context, not tracked `origin/dev` code.

## 2. Identity facts and account preservation

These are **existing historical receipts**, not new live assertions. The
17:06 UTC self/principal receipt binds:

| Fact | Recorded value |
|---|---|
| Username | `ajoe734` |
| Account / subject UUID | `17e9cb99-db46-4a07-8e61-6bf9b22cf5d2` |
| Tenant UUID | `e34f2117-de4b-478c-82fd-13c4ef428d42` |
| Identity source / status | `identity.accounts` / `active` |
| Complete roles | `auditor`, `operations_manager`, `platform_admin` |
| Clearance | `CONFIDENTIAL` |
| Scope arrays | `brand_ids`, `region_ids`, `store_ids`, `assigned_area_ids`, `heat_zone_ids`, `modules`: all `[]` |

Do not reinterpret empty axes as a newly approved scope enlargement. Preserve
these values exactly; do not remove `auditor` or `operations_manager`, add
`operator_viewer`, reset credentials, disable the account, or revoke sessions to
make the smoke pass.

Recorded audit chronology:

- Bootstrap: 2026-10-04 09:35:32.458286 UTC, event
  `76f289bd-e806-4013-8c23-4cca26d94afd`, `platform_admin` only.
- Auditor addition: 2026-10-09 04:04:53.793555 UTC, event
  `e2fb36e4-1130-45d9-94d3-f221ce284ef1`; same full scope and active status.
  The 14:43 preflight still reported `auditor` + `platform_admin`.
- Distinct operations-manager addition: **15:30:31.431353 UTC**, event
  `277a8444-3784-43c9-90c2-8f0846f0eb2e`, correlation
  `corr-odp-grant-ops-31eddda2-6a66-4f68-8dfb-22e5e8889b5a`.
  Before: `auditor` + `platform_admin`; after: all three roles above.
  `scope_before == scope_after`, both statuses `active`, `sessions_revoked=0`.
  Its reason records an owner's request, but that text alone is **not**
  independent authorization proof. The handoff coordinator disclaims this POST.

The official own audit route is
`GET /api/v1/operator/users/audit-trail?subject_id=<account UUID>`, not
`/users/<UUID>/audits`. The 17:06 receipt contains three events through that
route. Its existing logout receipt is 200, followed by session 401; this worker
did not repeat either request.

## 3. Correct failure classification and inventory limit

[Run 37960951819](https://github.com/alfloop-dev/odayplus/actions/runs/37960951819)
used candidate `7869551a58e8e13b6967dc4583d7a22ced77216b`. A read-only
`gh run view ... --json databaseId,headSha,status,conclusion,url` at 22:45 UTC
returned `status=completed`, `conclusion=failure` (exit 0).

The preserved log's decisive 17:00:23 UTC diagnostic is:

```text
admin:identity_user_list: status=200 users=1 selfListed=True
roles=['auditor', 'operations_manager', 'platform_admin']
identitySource=identity.accounts
```

This is a successfully served authoritative identity list with an **ineligible
role set**, not evidence of the generic heading's suggested OIDC/JWKS failure.
`_check_dev_admin_session` accepts active pure `platform_admin`, or
`_read_admin_roles` with exactly `platform_admin` plus `auditor` and/or
`operator_viewer`, whose effective permissions equal pinned finite grants.
`operations_manager` is deliberately excluded. A failed account can still be
labelled `pure-admin` by the report's default branch; that label is not a
passing pure-admin proof. No relaxation or persona substitution is justified.

The `users=1 selfListed=True` observation establishes **no alternate eligible
account in that returned tenant inventory at the failed gate**. The self and
audit receipts concern only this account; a role catalog is not an identity
inventory. These records cannot prove global absence, present-day inventory,
an alternative's password usability, or independent authorization. No
cross-tenant enumeration or new credentialed request was attempted.

The handoff records build/sign/attest/admission success, then live-gate failure.
The failed log records API/Web traffic restoration to
`36c94d7156011b41d71a8f6774558f35b1b45b83`; existing 17:02 and 17:06 authenticated
readbacks report that SHA for both services. They do not establish live state
at 22:45, nor rollback of every database effect. Admission to attempt deployment
is not deployment success. No `deployment_success=true` receipt is established
by this evidence; do not manufacture one.

## 4. Precise Human/Ops blocker and bounded next plan

**Blocker:** no independently authorized, active, tenant-bound, least-privilege
smoke identity separate from `ajoe734` has been proven. This task has no
permission to obtain live login credentials or change accounts/configuration.

Request the following explicit Human/Ops decision in a separate authorized lane:

1. Authorize an existing tenant administrator/Ops custodian to supply a fresh,
   sanitized tenant-local `GET /api/v1/operator/users` inventory and account/audit
   readback. Name the intended smoke account UUID, username, tenant, active
   status, complete scope and exact roles. Prefer an **existing pure
   `platform_admin`**; a read-enabled candidate must meet the unchanged §5.6
   finite-role/permission and audit requirements. Prove credential ownership
   and smoke-use consent without publishing credentials. Do not use a foreign
   tenant, service bearer or business persona as a shortcut.
2. If eligible, explicitly approve rebinding the **dev environment's**
   `ODP_DEV_ADMIN_USERNAME` and `ODP_DEV_ADMIN_PASSWORD` together to that
   identity through the approved secret/configuration custodian. Review whether
   the optional initial-password binding is applicable to that same subject;
   do not recycle `ajoe734`'s initial credential. Reconfirm that `cs-lead` is a
   persona the chosen account does not hold. Do not change release-profile,
   grant policy, tenant or IAM. Record only non-secret binding receipts.
3. If no eligible identity exists, Human/Ops must separately authorize and
   review a supported account/credential provisioning path, exact tenant/scope,
   pure-admin role, credential lifecycle and audit requirements **before**
   execution. No provisioning is approved here. Re-running
   `shared.identity.bootstrap` is not a second-account solution: it no-ops
   when any active account exists. `/operator/users` role-save also refuses
   manufacturing an account. A supported provisioning capability may need a
   separately scoped implementation; do not substitute direct SQL/reset or
   invent an invitation endpoint.
4. Preserve `ajoe734`'s complete three-role snapshot and all scope/status.
   Obtain independent review of the chosen principal/binding evidence, then
   seek a normally authorized exact-tuple release with unchanged signing,
   manifest/admission and live gate. Follow immutable artifact policy; no blind
   rebuild/retag/retry, lease waiver or fabricated success.
5. Only after a genuinely successful admitted release, re-review any proposed
   viewer grant and its scripts against the latest audited business account.
   The preserved grant/deny/UI scripts have stale two-role expectations and
   remain unexecuted. UI widths 390/1024/1440, StoreOps materialization,
   sources/models, full-profile admission, Package10/VDC and independent F11
   remain separate holds, not completed acceptance.

PRs #1435 and #1441 remain archived/immutable; this diagnosis does not reopen
or alter those deliveries. Approval of this document does not authorize any of
the operational changes proposed above.

## 5. Evidence provenance and review reproduction

Sources: supplied `support/handoffs/dev-smoke-principal-isolation-20261009/README.md`,
`origin/dev:docs/design/ODP_DEV_ADMIN_RELEASE_PROFILE.md` (§5.1/5.2/5.6), the
repository paths in §1, the run URL in §3, and preserved receipts under
`/tmp/odp-read-auth-integration-20261009/`. Scratch files are historical local
records, not committed live acceptance artifacts. The decisive non-secret
facts are reproduced above so review is not dependent on an accessible `/tmp`.

| Preserved file | SHA-256 |
|---|---|
| `deployment-37960951819-failed.log` | `7780cafab5e5b734fdac0911f4500e3cb76b931584e083c09e51c9d5dd9496ea` |
| `deployment-preflight-before-37960951819.json` | `3c60ef886d7a3379e80c65b938ea0e831d762011d7bc5f3aebb29604547c4cc9` |
| `deployment-preflight-rollback-roles3.json` | `e22aee79d316b92014f6e212ac7c96e9975fe038059793f31cdd95e18ef11bf5` |
| `deployment-preflight.json` | `8313b277047e68e63976cfdd0a8f0aabb011af96d2b6b6653ea12fd93c3dc688` |

Inspection only: `git fetch origin dev`, `git show origin/dev:<path>`,
`git diff <failed-candidate> origin/dev -- <gate/workflow paths>`, scoped reads
of the preserved receipts, `sha256sum`, the non-secret GitHub variable queries
and run-status query above. No tests/build/E2E were run (verification: none).
Review must assess the diagnosis and Human blocker, not count prepared scripts,
old preflight `verified=true`, or CI completion as successful principal isolation.
