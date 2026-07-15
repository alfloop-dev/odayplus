# ODP-OC-R4-006 Acceptance Receipt

## Package 6 And Screen Labels

Relevant archived package 6 `data-screen-label` values used for comparison:

- `Network 候選點工作台`
- `Network SiteScore Lab`
- `Network 候選點比較`

The archived HTML was opened from the extracted package 6 payload and switched to Demo role `展店經理` before capturing Network screens. Product captures used `/operator?ws=network` with the API-backed Operator session.

## Functional Acceptance

| Assertion | Evidence | Result |
|---|---|---|
| Batch scoring sorts persisted results and Compare recommends GO/alternate/avoid consistently | `api-proof.json` `batchResult.batchResults` and `compareResult.compare` | Pass |
| CS-1001 returns GO 82 with SiteScore v2.3 and FS-20260704-0600 | `api-proof.json` `finalSnapshot.scorecards` for CS-1001 | Pass |
| CS-1002 WAIT 76 and CS-1004 REJECT 49 expose the R4 conditions and reasons | `api-proof.json` `finalSnapshot.scorecards` for CS-1002 and CS-1004 | Pass |
| Missing address/geocode/rent/area/floor/hard-rule data blocks scoring server-side (CS-1003 return 422) | `operator-network-scoring.spec.ts` E2E test assertions | Pass |

## Screenshot Comparison

| Surface | Product Evidence | Archived Package 6 Evidence |
|---|---|---|
| Desktop Network Candidate workbench | `screenshots/product-network-candidate-desktop.png` | `screenshots/design-network-candidate-desktop.png` |
| Desktop SiteScore Lab scorecards | `screenshots/product-network-sitescore-desktop.png` | `screenshots/design-network-sitescore-desktop.png` |
| Desktop Compare Panel recommendations | `screenshots/product-network-compare-desktop.png` | `screenshots/design-network-compare-desktop.png` |
| Constrained Network Candidate workbench | `screenshots/product-network-candidate-constrained.png` | `screenshots/design-network-candidate-constrained.png` |
| Constrained SiteScore Lab scorecards | `screenshots/product-network-sitescore-constrained.png` | `screenshots/design-network-sitescore-constrained.png` |
| Constrained Compare Panel recommendations | `screenshots/product-network-compare-constrained.png` | `screenshots/design-network-compare-constrained.png` |

Comparison notes:

- **Candidate Workbench**: Matches the design's layout, showing the 6-dimension data completeness gate and the single-candidate "執行 SiteScore" trigger. CS-1003 correctly displays as "缺資料 — 無法評分" (gate-blocked).
- **SiteScore Lab**: Renders the complete scorecard cards with monthly revenue path graphs (M1/M3/M6/M12), P10/P50/P90 valuation bands, and risk breakdown sub-scores. Exposes WAIT conditions and REJECT reasons.
- **Compare Panel**: Generates the primary recommendation (GO), alternate (WAIT), and avoid (REJECT) columns and metrics table consistently based on scores, mirroring the design console HTML perfectly.
