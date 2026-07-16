# ODP-OC-R4-005 Implementation Receipt

Task: ODP-OC-R4-005
Owner: Codex
Reviewer: Antigravity
Status: package-6 parity fix after audit reopen

## Delivered Implementation

The original functional implementation was merged to `dev` through PR #280 at
merge commit `dbf390f0`. This task branch adds the package-6 parity fix required
by the reopen finding.

- Added the Network R4 six-step expansion stepper from Find Areas through Review.
- Added API-backed Listing Radar snapshot and write actions.
- Implemented `L-2024 -> CS-1001` conversion with idempotent replay.
- Implemented `L-2029 -> L-2025` duplicate merge while retaining source evidence.
- Implemented `L-2030` hard-rule archive with a required reason.
- Preserved the real HeatZoneMap path and verified zone/lens synchronization.
- Removed the outer OpsBoard shell for `/operator` routes so Operator screens do
  not render the extra sidebar/global header.
- Removed nested `WorkspaceChrome` only for the Network workspace and replaced it
  with package-6-style Network heading, stats, tabs, and stepper.
- Reworked Find Areas into a package-6-style left lens, center map/recommendation,
  and right detail workbench while keeping the real HeatZoneMap canvas.
- Reworked Listing Radar into a package-6-style compliance/source/inbox/detail
  layout while preserving existing API-backed actions and test ids.
- Added product `data-screen-label` coverage for `Network 展店與店網`,
  `Network Expansion Flow Stepper`, `Network 找區域`, and `Network 物件雷達`.

## Owned Layer

- `apps/web/features/operator/NetworkFindAreasWorkspace.tsx`
- `apps/web/features/operator/OperatorConsole.tsx`
- `apps/web/src/app/OpsBoardFrame.tsx`
- `apps/web/features/operator/operator.module.css`
- `apps/web/features/operator/networkFindAreas.module.css`
- `apps/web/features/operator/network/NetworkShell.tsx`
- `apps/web/features/operator/network/ExpansionStepper.tsx`
- `apps/web/features/operator/network/ListingRadarPanel.tsx`
- `apps/api/app/routes/operator_modules/network_listings.py`
- `modules/opsboard/application/network_listings.py`
- `tests/e2e/operator-network-listings.spec.ts`
- task-scoped evidence in this directory

## Not Changing

- API write semantics for conversion, merge, archive, and snapshot reads.
- HeatZoneMap implementation internals or standalone `/w/expansion/heatzone`
  behavior.
- Growth, Governance, SiteScore scoring, Review decision, Rebalance, persistence,
  staging, or deployment layers.

## Evidence Files

- `api-proof.json` proves the conversion, replay, merge, archive, and final
  snapshot states.
- `map-pixel-proof.json` proves the product HeatZoneMap canvas is nonblank before
  and after selected-zone synchronization in desktop and constrained viewports.
- `network-stepper.png` is the expected task-packet screenshot artifact for the
  product Network stepper surface.
- `screenshot-manifest.json` records package 6 provenance, relevant design
  labels, shell assertions, URLs, and screenshot inventory.
- `screenshots/` contains product/design desktop and constrained captures for
  Find Areas / Expansion Stepper and Listing Radar.
