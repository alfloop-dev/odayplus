# Operator Console R7 / Package 10 Parity and Live Deployment Tasks

- doc_id: ODP-OC-R7-UPLIFT-001
- date: 2026-07-25
- status: implementation complete locally; remote approval blocked
- target_branch: `dev`
- baseline_ref: `origin/dev@a13a1075258be98222e5bddd0acd99636179a149`
- canonical_design: R7 / Package 10
- canonical_archive: `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/`

## Corrected Truth

The React console contains substantial R5 functionality and must not be
discarded. It is not, however, visually complete against Package 10.

The prior product-grade gate checks Package 7 and accepts nine hard-coded
screen labels as implementation proof. A screen-label inventory proves neither
layout parity nor production rollout. Package 10 requires runtime screenshots
and interaction checks against the canonical HTML at every target viewport.

The Cloud Run screenshot reported on 2026-07-25 has three independent causes:

1. `/operator` is blocked by the production bootstrap gate.
2. the R7 compact shell was only enabled for the Network workspace;
3. Deploy Dev has not delivered Package 10 to Cloud Run. The Package 10
   integration run and every later run through `a13a1075` failed before rollout.

## Binding Sources

Workers must read these together:

1. `docs_archive/00_source_zips/operator_console/LATEST.json`
2. Package 10 source and standalone HTML under the canonical archive
3. `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_REVIEW_003.md`
4. Package 10 desktop/tablet/mobile evidence under
   `docs/evidence/design_review/assisted_listing_intake_r7_package10/`
5. System Design, OpenAPI, authorization, privacy, reliability, and persistence
   contracts. These override mock behavior in the prototype.

## Dispatched Work

| Task | Status | Owner | Scope |
|---|---|---|---|
| `ODP-OC-R7-RUNTIME-001` | implementation complete, local integration | Codex + Helmholtz | Cloud Run audience, bounded upstream/bootstrap timeout, explicit 504 |
| `ODP-OC-R7-SHELL-001` | implementation complete, local integration | Codex | R7 compact shell for every workspace; retain static navigation during data gates |
| `ODP-OC-R7-ROUTE-001` | implementation complete, local integration | Turing (`019f9b84-b8c4-77e2-aab0-1a067fadb52e`) | URL-restorable Network tab and intake cold-open; isolate intake from unrelated Network gates |
| `ODP-OC-R7-TODAY-001` | implementation complete, local integration | Hume (`019f9b92-1bc2-7ee2-8dc0-7763332805e7`) | Package 10 Today composition using live envelope only |
| `ODP-OC-R7-INTAKE-VIS-001` | implementation complete, local integration | Hubble (`019f9b90-fd47-77a0-a67d-82164eb7f11f`) | State Matrix, durable detail label, side-by-side compare, promotion confirmation |
| `ODP-OC-R7-STORE-001` | implementation complete, local integration | Descartes (`019f9b9a-1ce7-7b72-9d1c-f861ba57c346`) | Store Ops page-by-page visual and interaction parity |
| `ODP-OC-R7-GROWTH-001` | implementation complete, local integration | Goodall (`019f9b9a-37e1-7ed3-882f-840ce86c7248`) | Growth page-by-page visual and interaction parity |
| `ODP-OC-R7-NETWORK-001` | implementation complete, local integration | Lovelace (`019f9b9e-4c9e-7e23-9ffa-1cfb9fc30297`) | Find Areas, Listing Radar, Candidate, SiteScore, Compare, Review, Rebalance parity |
| `ODP-OC-R7-GOVERN-001` | implementation complete, local integration | Kierkegaard (`019f9b9a-4f1b-7b51-b4f2-7e84c9f810b0`) | Governance approvals, decision evidence, and audit feed parity |
| `ODP-OC-R7-REMOTE-VQA-001` | blocked on deploy credentials | QA Fleet | Authenticated Cloud Run visual regression at 390, 1024, and 1440 px |
| `ODP-OC-R7-DEPLOY-001` | blocked external | Human/Ops + Codex2 | Configure WIF or deploy service credentials, deploy exact SHA, verify traffic and rollback |

Current local integration branch:
`fix/package10-final-20260725`.

## Route Contract

These are separate acceptance surfaces and all must be checked:

| Route | Required result |
|---|---|
| `/operator` | Package 10 shell and Today workspace |
| `/operator?ws=store` | Package 10 Store Ops |
| `/operator?ws=growth` | Package 10 Growth |
| `/operator?ws=network&tab=radar` | Package 10 Network Listing Radar and assisted intake |
| `/operator?ws=govern` | Package 10 Governance |
| `/w/expansion/listings` | production Assisted Listing Intake list entry |
| `/intake/:intakeId` | durable Assisted Listing Intake detail entry |

The product routes may reuse a shared surface, but they may not drift into
visually and behaviorally independent versions.

## Local Integration Verification

Verified on `fix/package10-final-20260725`:

- `/intake/IN-3001` now renders inside the R7 Operator Console Network Radar
  instead of the legacy Expansion/OpsBoard workspace.
- The durable route opens `Intake 收件處理詳情頁` from the real intake list/get
  API flow and survives list refresh races.
- `Intake 狀態矩陣` exposes all 12 processing stages, 5 source-policy states,
  5 match outcomes, and 15 error/conflict contracts.
- Chromium checks at 1440, 1024, and 390 px returned HTTP 200 with no document
  width overflow.
- Web verification: 35 test files, 276 tests passed; TypeScript passed.
- Package 10 and Cloud Run contract verification: 24 tests passed.
- Package 10 source gate: ZIP SHA, HTML SHA, and 40/40 canonical screen labels
  passed.

This is local integration evidence only. It does not close
`ODP-OC-R7-REMOTE-VQA-001` or authorize production rollout.

## Acceptance Gates

1. Do not mark a page complete from label presence, unit tests, or local API E2E
   alone.
2. Capture implementation and canonical screenshots at 390, 1024, and 1440 px.
   Record visible differences by region and close every P0/P1 item.
3. Assert route cold-open, reload, back, forward, and shareable-state behavior.
4. Production must never render fixture operational rows. Static shell
   navigation and role labels may render while data is loading or unavailable.
5. Every API wait is bounded and produces a correlation-aware error/retry
   surface.
6. Web and API `/platform/version` values must equal the deployed `dev` SHA;
   Cloud Run traffic must point 100% to that verified revision.
7. The authenticated remote visual run, not a local Playwright run, closes
   production visual approval.
8. A failed or skipped deploy is a failed release. Completion evidence must
   remain `implementation_complete`, not `deployed` or `visual_approved`.

## External Blocker

Deploy Dev run `30161769381` failed preflight with neither WIF nor service-account
credentials available, so build and rollout were skipped. Repository code cannot
provision those GitHub environment credentials. Human/Ops must configure the
`dev` environment before `ODP-OC-R7-DEPLOY-001` can execute.
