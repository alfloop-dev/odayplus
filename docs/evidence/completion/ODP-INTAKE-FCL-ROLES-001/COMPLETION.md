# ODP-INTAKE-FCL-ROLES-001 Completion Evidence

## Scope

- Baseline: `c900e906f96cb3750274c24e1a8f2922999f9048`
- Branch: `task/ODP-INTAKE-FCL-ROLES-001`
- Owner: Codex
- Disposition: implementation complete, awaiting umbrella integration

## Implemented

1. The operator role switcher exposes all six required Assisted Listing Intake
   modes:
   - Expansion staff: own/assigned proposer
   - Expansion manager: managed-scope independent reviewer
   - Data steward: source/data correction scope
   - Governance reviewer: governance read-only
   - Privacy officer: purpose-bound restricted evidence
   - Permission-limited user: masked read-only
2. Required local intake roles remain reachable when the shell response still
   returns an older role list.
3. Role switches preserve the active pathname, intake selection, dialog,
   section, query string, and hash whenever the target role retains Network
   access. Each switch reloads bootstrap data with the new authoritative
   operator and API-role headers.
4. The signed-in subject remains stable across role changes. The role does not
   become the actor identity, so proposer/reviewer separation remains
   enforceable.
5. The permission evaluator distinguishes own/assigned, source scope,
   purpose-bound restricted evidence, masked data, governance read-only, and
   no-access cases. Denials carry stable reason codes.
6. Unknown role-to-header mappings fail closed with no API roles instead of
   inheriting `operations_manager`.

## Role And Header Matrix

| Operator role | Intake mode | API roles | Write mode |
|---|---|---|---|
| `expansion-staff` | `own-assigned` | `expansion_user` | Own/assigned correction and proposals |
| `expansion-manager` | `managed-scope` | `expansion_user,site_reviewer` | Managed actions and independent review |
| `data-steward` | `source-data` | `data_owner,expansion_user` | Source-scoped correction and identity work |
| `governance-reviewer` | `governance-read-only` | `auditor` | Read-only |
| `privacy-officer` | `purpose-bound` | `finance_legal,auditor` | Purpose-bound restricted evidence |
| `permission-limited` | `masked-read-only` | `auditor` | Masked read-only |

## Verification

| Command | Result |
|---|---|
| `npm run test --workspace=@oday-plus/web -- --run features/operator/network/intake/__tests__/intakeRoleMatrix.test.ts features/operator/network/intake/__tests__/IdentityDecisionPanel.test.tsx features/operator/network/intake/__tests__/PromotionReviewPanel.test.tsx` | PASS: 3 files, 52 tests |
| `npm run typecheck --workspace=@oday-plus/web` | PASS |
| `npx playwright test tests/e2e/operator-assisted-listing-intake-roles.spec.ts --project=chromium` | PASS: 3 tests |
| `npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts --project=chromium --grep "canonical 4"` | PASS: independent promotion reviewer and durable receipts |
| `uv run pytest tests/contract/test_assisted_listing_operations.py::test_url_intake_and_concurrency_lifecycle -q` | PASS: identity self-review denied, independent review accepted |
| `uv run pytest tests/integration/test_assisted_listing_promotion.py::test_promotion_saga_segregation_of_duties -q` | PASS |
| `git diff --check` | PASS before commit |

The role E2E verifies the actual operator UI, six role selections, visible role
mode labels, authoritative bootstrap headers, stable actor subject, deep-link
preservation, write/read-only variants, staff own-record filtering,
permission-limited field masking, and the backend `403` denial envelope.

## Integration Dependencies

- Baseline `c900e906` does not yet contain the canonical
  `/w/expansion/listings/intake/:intakeId` page. The role-switch proof therefore
  uses the currently reachable durable intake URL state under
  `/operator?ws=network&selected=...&dialog=detail`. The role switch preserves
  arbitrary pathname/query/hash state and is ready to compose with
  `ODP-INTAKE-FCL-DETAIL-001`.
- `IdentityDecisionPanel` has complete self-review behavior and tests but is
  not mounted by the baseline production composition. Its production mounting
  belongs to `ODP-INTAKE-FCL-IDENTITY-001`; the role policy exported here
  separates proposer and reviewer permissions for that integration.
- The legacy operator submit endpoint wraps an RBAC denial with backend code
  `forbidden`; the role layer exposes the more specific UI policy codes such as
  `ROLE_DENIED`, `OWNERSHIP_REQUIRED`, `SOURCE_SCOPE_DENIED`,
  `PURPOSE_REQUIRED`, and `SELF_REVIEW_DENIED`. Runtime normalization remains
  owned by `ODP-INTAKE-FCL-RUNTIME-001`.
