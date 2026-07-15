# Operator Console R5 Fleet Execution Tasks

Date: 2026-07-15
Status: ready for Fleet dispatch
Machine queue: `docs/design/OPERATOR_CONSOLE_R5_FLEET_EXECUTION_TASKS_2026-07-15.json`

## Objective

Adopt canonical package 7 / R5, implement the assisted listing intake workflow,
and prevent the existing package 6 / 32-label gate from being used as current
release proof.

## Canonical Source

- Pointer: `docs_archive/00_source_zips/operator_console/LATEST.json`
- Archive: `docs_archive/00_source_zips/operator_console/r5-20260715-package-7/`
- ZIP SHA-256: `fa1a980d1d0c3fe2102e11ac009a57a1fe25bdb5539f9bd03378c2a628a9b552`
- Interactive HTML SHA-256: `1e1bcfa329842216422b1d3ae2a44e7014dc8005cc156e2dcc978a6e4a5c3a2d`
- Demo state: `oday-plus-r5-20260714`
- Screen labels: 37, with five additions and no removals
- Requirements: `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`
- Diff receipt: `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_7_DIFF_2026-07-15.md`

Every owner and reviewer must open the extracted interactive HTML. A prose-only
review or a package 6 hash is not R5 acceptance evidence.

## Impact Decision

- Historical R4 tasks `001..010` remain completed against package 6. They are
  inputs, not current R5 parity proof.
- R4 Network task `005` is materially superseded for URL intake by R5-001.
- Historical `ODP-EXT-002` remains the ingestion contract. Its R5 addendum at
  `docs/evidence/fleet_dispatch/ODP-EXT-002-R5-ADDENDUM.md` must compose with
  R5-001; neither layer may independently duplicate the other.
- R4-011 may continue fixing its known CI defects, but its 32-label package 6
  gate cannot authorize release. R5-002 owns current validation.
- R4-012 must not release R4 after package 7 receipt; R5-003 owns cutover.

## Task Index

| Task | Fleet | Deliverable |
|---|---|---|
| `ODP-OC-R5-000` | design-source-delivery | Publish R5 archive, manifest, diff, task pack, and Fleet source references |
| `ODP-OC-R5-001` | network-assisted-intake | API-backed durable URL intake, parsing review, matching, decisions, and audit UI |
| `ODP-OC-R5-004` | product-functional-e2e | Real API-backed browser flow verification of all 11 stages, 5 match outcomes, 5 policy states, reload persistence, corrections, audit, error, and responsive behavior |
| `ODP-OC-R5-005` | security-governance | Enforce fail-closed assisted-listing retrieval controls for SSRF, redirects, DNS, private-networks, metadata, credential privacy, RBAC, and audit |
| `ODP-OC-R5-002` | validation | Mandatory 37-label product E2E, visual, a11y, policy, persistence, and regression gate |
| `ODP-OC-R5-003` | release | Staging proof, rollback, and dev-to-main cutover for R5 |

## R5-001 Required Surfaces

1. `Network URL 收件佇列`
2. `Dialog 從網址新增物件`
3. `Dialog 收件處理詳情`
4. `Dialog 欄位修正`
5. `Dialog 收件決策確認`

Required outcomes are `NEW`, `EXACT_DUPLICATE`, `REVISION`,
`POSSIBLE_MATCH`, and `QUARANTINED`. Required policy states are
`APPROVED_RETRIEVAL`, `ASSISTED_ENTRY_ONLY`, `AUTH_REQUIRED`,
`SOURCE_BLOCKED`, and `POLICY_UNKNOWN`.

No search-result crawler, scheduled discovery, private browser API, credential
collection UI, automatic ambiguous merge, or automatic Candidate promotion is
allowed.

## R5-004 Functional E2E Requirements

Verify the entire URL-assisted listing intake lifecycle derived from package 7 / R5 requirements via real API-backed browser flows. Tests must cover:

1. **11 Ingestion Stages**: Verify the progression and rendering of the following stages: `SUBMITTED`, `CHECKING_IDENTITY`, `CHECKING_SOURCE_POLICY`, `AWAITING_ASSISTED_ENTRY`, `RETRIEVING`, `PARSING`, `MATCHING`, `NEEDS_REVIEW`, `READY`, `QUARANTINED`, and `FAILED`.
2. **5 Match Outcomes**: Assert UI and backend transitions for `NEW`, `EXACT_DUPLICATE`, `REVISION`, `POSSIBLE_MATCH`, and `QUARANTINED` states with deterministic fixtures.
3. **5 Source Policy States**: Prove that access controls resolve to `APPROVED_RETRIEVAL`, `ASSISTED_ENTRY_ONLY`, `AUTH_REQUIRED`, `SOURCE_BLOCKED`, or `POLICY_UNKNOWN`, ensuring proper fetch or fallback behaviors.
4. **Persistence & Corrections**: Ensure submitted decisions, manual corrections, and correction reasons survive page reloads and browser context restarts.
5. **Audit Trails**: Verify that every human action (create, revise, duplicate, quarantine, promote, etc.) records actor role, timestamps, snapshot IDs, parser versions, and correlation IDs.
6. **Error & Responsive Proof**: Validate responsive layouts across desktop, tablet, and mobile, enforcing that ambiguous side-by-side comparison routes to desktop-required warnings.

## R5-005 Retrieval Security Requirements

Verify the security sandbox and governance rules surrounding page retrieval derived from package 7 / R5 requirements:

1. **SSRF & Redirect Controls**: Block Server-Side Request Forgery by ensuring loopback, private networks, multicast, and link-local targets are rejected before connection.
2. **DNS Re-validation**: Re-evaluate DNS resolution at every redirect hop to prevent DNS-rebinding attacks from bypassing access controls.
3. **Private-Network Access**: Enforce blocking of all local/internal IP ranges (RFC 1918, IPv6 local).
4. **Metadata Protection**: Prevent access to cloud provider instance metadata endpoints (e.g., `169.254.169.254`).
5. **Credential Privacy**: Never accept, transmit, or persist raw external provider credentials, cookies, tokens, or private API endpoints via the intake UI.
6. **RBAC Rules**: Ensure only authorized roles (Expansion managers/Data stewards) can modify matching states, make merge decisions, or promote to Candidate Sites.
7. **Fail-Closed Retrieval**: Enforce default-deny (fail-closed) behavior for any `POLICY_UNKNOWN` or policy-failed retrieval request without retrieving target content.

## Wave Plan

| Wave | Tasks | Rule |
|---|---|---|
| 0 | `R5-000` | Archive and source pointer must be durable before implementation review |
| 1 | `R5-001` | Compose existing Operator Network and ODP-EXT-002 contracts |
| 2 | `R5-004`, `R5-005` | Enforce functional E2E and security gates before final visual verification |
| 3 | `R5-002` | Runs after functional/security verification and absorbs unresolved R4-011 gate defects |
| 4 | `R5-003` | Runs only after the R5 gate is green |

## Release Stop Conditions

- `LATEST.json` does not resolve to package 7.
- Any current task or gate claims package 6 / 32 labels is the latest design.
- Any of the five R5 labels is missing from implementation or E2E evidence.
- URL intake exists only in fixture/session state or does not survive reload.
- Policy-unknown sources are fetched instead of failing closed.
- `POSSIBLE_MATCH` is merged without a reasoned human decision.
- Decision/audit evidence lacks source snapshot, parser version, actor, reason,
  before/after values, or correlation ID.
- Existing 32 R4 labels regress under package 7 validation.
- Staging, rollback, or mandatory product E2E proof is missing.

## Mandatory Validation

```bash
unzip -t 'docs_archive/00_source_zips/operator_console/r5-20260715-package-7/Oday Plus 營運管理後台 (7).zip'
test "$(sha256sum 'docs_archive/00_source_zips/operator_console/r5-20260715-package-7/Oday Plus 營運管理後台 (7).zip' | cut -d ' ' -f 1)" = fa1a980d1d0c3fe2102e11ac009a57a1fe25bdb5539f9bd03378c2a628a9b552
npm run typecheck --workspace=@oday-plus/web
npm run build --workspace=@oday-plus/web
uv run pytest tests/contract tests/integration tests/security
npx playwright test tests/e2e/e2e-operator-console.spec.ts
```

R5-002 may split browser specs by domain, but it may not reduce label, role,
responsive, persistence, audit, map, or accessibility coverage.
