# ODP-OC-R4-005 Acceptance Receipt

## Package 6 And Screen Labels

Relevant archived package 6 `data-screen-label` values used for comparison:

- `Network 展店與店網`
- `Network Expansion Flow Stepper`
- `Network 找區域`
- `Network 物件雷達`

The archived HTML was opened from the extracted package 6 payload and switched
to Demo role `展店經理` before capturing Network screens. Product captures used
`/operator?ws=network` with the API-backed Operator session.

## Functional Acceptance

| Assertion | Evidence | Result |
|---|---|---|
| `HZ-01 -> L-2024 -> CS-1001` completes through product UI and APIs | `api-proof.json`, Playwright focused suite | Pass |
| Converting `L-2024` creates `CS-1001` once and navigates to Candidate | `api-proof.json`, `operator-network-listings.spec.ts` | Pass |
| `L-2029` merges into `L-2025` without deleting source evidence | `api-proof.json` retained evidence array and target containment | Pass |
| `L-2030` archives with a reason and keeps hard-rule evidence | `api-proof.json` archive block | Pass |
| Real HeatZone map remains nonblank and syncs selected zone/lens | `map-pixel-proof.json` desktop/constrained `hasVisiblePixels: true`, `selectedZoneAfter: HZ-02` | Pass |
| Product Network no longer renders the extra OpsBoard shell/sidebar or nested Network workspace chrome | `screenshot-manifest.json` `productShellAssertions` all `0` | Pass |

## Screenshot Comparison

| Surface | Product Evidence | Archived Package 6 Evidence |
|---|---|---|
| Desktop Network / stepper / Find Areas | `screenshots/product-network-desktop.png` | `screenshots/design-network-desktop.png` |
| Desktop Listing Radar | `screenshots/product-listing-radar-desktop.png` | `screenshots/design-listing-radar-desktop.png` |
| Constrained Network / stepper / Find Areas | `screenshots/product-network-constrained.png` | `screenshots/design-network-constrained.png` |
| Constrained Listing Radar | `screenshots/product-listing-radar-constrained.png` | `screenshots/design-listing-radar-constrained.png` |

Comparison notes:

- The product now exposes the R4 six-step flow states, Network tabs, HZ/L/CS ids,
  selected zone chip, API-backed Listing Radar rows, source cards, detail rail,
  conversion, merge, archive, and map sync.
- Reopen fix: product Network now opts out of the outer OpsBoard shell and the
  nested workspace chrome, matching the package 6 top-level Operator shell.
- Find Areas now follows the package 6 workbench structure: left lens stack,
  center HeatZone map plus recommended areas, and right HeatZone detail rail.
- Listing Radar now follows the package 6 structure: compliance banner, source
  cards, source filter panel, listing inbox, and detail rail.
- In a 390px viewport, both archived design and product preserve a wide Network
  workbench and are captured with horizontal overflow rather than duplicate nav
  or overlapping chrome.

## Review Boundary

This evidence refresh resolves the audit gap for package 6 provenance, relevant
`data-screen-label` values, desktop/constrained screenshot comparison, and the
reopen finding that the prior package-6 screenshots still showed the outer
OpsBoard shell plus nested Network chrome.
