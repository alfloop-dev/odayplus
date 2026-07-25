# Operator Console: R7 rebuild decision + retirement plan

- doc_id: ODP-OC-REACT-RETIREMENT-001
- date: 2026-07-25
- decision_by: owner (bjoe734)
- status: DECISION recorded; physical retire+rebuild is fleet task ODP-OC-R7-FE-001

## Decision

The operator console is to be **rebuilt from the canonical R7 / Package 10 design**
(API-connected) rather than carried forward as the current R5/Package-7 React.

## Honest status of the current React (no spin)

The current React operator console (`apps/web/features/operator/**`) is **NOT broken
garbage**. The CI label checker `scripts/e2e/check_product_grade_ci_gates.py` verifies it
implements all **37 Package-7 (R5) screen labels**. What it is: an R5 implementation
(canonical is R7/40 + VDC-001..005) whose **deployed instance never showed live data
because web→API auth (401) was never wired**, and which the owner has chosen to replace
with a clean R7 rebuild.

## Why it was NOT physically deleted in this change (important)

A naive `rm -rf apps/web/features/operator` **breaks the web build**: the operator feature
is a **load-bearing dependency of other features** —
- `apps/web/features/shell/shellClient.ts` imports `../operator/network/operatorNetworkClient`
- `apps/web/features/expansion/ExpansionWorkspace.tsx` imports `../operator/network/intake/AssistedIntakeSection`
  and the `OperatorRoleId` type from `../operator/navigation`

So retirement is **not a wholesale delete** — it requires the R7 rebuild to either preserve
the shared modules (network client, assisted-intake section, role types) or refactor `shell`
and `expansion` off them. That work belongs to the fleet task, done in a build-green way,
not a hasty deletion that ships a red PR.

## Hard rules for the rebuild (ODP-OC-R7-FE-001)

- Build the new console from the R7/Package 10 extracted design
  (`docs_archive/00_source_zips/operator_console/r7-20260720-package-10/extracted`) + binding
  review `ODAY_PLUS_ASSISTED_LISTING_INTAKE_UI_VISUAL_DESIGN_RESPONSE_RESPONSE_REVIEW_003.md`.
- Handle the shell/expansion dependency explicitly; keep the web build green.
- Do not treat the deployed empty gate as "design was wrong": it was a no-data/401 state.
- Reuse the backend `/api/v1/operator/*` routes + durable persistence.
