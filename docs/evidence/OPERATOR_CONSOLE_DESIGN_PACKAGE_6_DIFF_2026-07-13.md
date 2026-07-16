# Operator Console Design Package 6 Difference Receipt

Date: 2026-07-13
Decision: Package `(6)` is the canonical latest design delivery.
Design delta from package `(5)`: **0**.

## Source Resolution

The supplied path was URL-encoded. After percent-decoding it resolves to the
existing local file:

`/home/lupin/oday-plus/Oday Plus 營運管理後台 (6).zip`

The exact package and extracted payload are archived at:

`docs_archive/00_source_zips/operator_console/r4-20260707-package-6/`

`LATEST.json` under the parent archive directory is now the stable lookup point
for all later design audits.

## Integrity And Package Difference

| Check | Package (5) | Package (6) | Result |
| --- | --- | --- | --- |
| ZIP size | 153,747 bytes | 153,747 bytes | Same |
| ZIP SHA-256 | `ac42396833024b1831dfc80af52f2b9b07ae9ce70a92c61d1ea1cacb52e7c7e5` | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` | Different package metadata |
| Entry timestamp | 2026-07-07 01:43 | 2026-07-13 14:26 | Changed |
| File count | 5 | 5 | Same |
| Added / removed files | 0 / 0 | 0 / 0 | No delta |
| Changed extracted files | 0 | 0 | Byte-identical payload |
| Demo state | `oday-plus-r4-20260707` | `oday-plus-r4-20260707` | Same R4 design |

### Extracted File Proof

| File | SHA-256 in (5) and (6) | Result |
| --- | --- | --- |
| `.thumbnail` | `f852cc833bb49ae2e189f17f5ef52082b6bf316d8c155c2a2a0f07da6dfd6d26` | Identical |
| `Oday Plus Operator Console R4 Design Summary.dc.html` | `a75818d8be19285d332b002032902393dcff51d21f6a7dd5a52a82b91e4b35e2` | Identical |
| `Oday Plus Operator Console.dc.html` | `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48` | Identical |
| `oday-map.js` | `95d92ba75a28ff24d025242bf0edf11fb6474ee6212344dd4e5e2b934f114b6d` | Identical |
| `support.js` | `e0650b109ec8f78ccc370fa27762b0c485cee4f208156a671f346e8544fc2214` | Identical |

## Screen-By-Screen Delta

Every `data-screen-label` in package `(6)` also exists byte-for-byte in `(5)`.
No screen, panel, dialog, field, label, mock state, or interaction code changed.

| Design surface | Package (6) versus (5) | Existing implementation task impact |
| --- | --- | --- |
| Top Navigation | Identical | Keep `R4-002` |
| Today 今日工作 | Identical | Keep `R4-002` |
| Role Switch Menu | Identical | Keep `R4-002` |
| Notifications | Identical | Keep `R4-002` |
| Store Ops 全店四燈摘要 | Identical | Keep `R4-003` |
| Store Ops 門市營運 | Identical | Keep `R4-003` |
| Dialog Triage | Identical | Keep `R4-003` |
| Dialog Assign | Identical | Keep `R4-003` |
| Dialog Create Action | Identical | Keep `R4-003` |
| Drawer Field Report | Identical | Keep `R4-003` |
| Dialog Outcome Review | Identical | Keep `R4-003` |
| Dialog Escalate | Identical | Keep `R4-003` |
| Dialog Reply Review | Identical | Keep `R4-003` |
| Dialog Transfer | Identical | Keep `R4-003` |
| Dialog Camera Purpose | Identical | Keep `R4-003`, `R4-010` |
| Growth 營收成長 | Identical | Keep `R4-004` |
| Growth 建立入口 | Identical | Keep `R4-004` |
| Growth 會員分群 | Identical | Keep `R4-004` |
| Growth PriceOps | Identical | Keep `R4-004` |
| Dialog Growth Draft Builder | Identical | Keep `R4-004` |
| Dialog Growth Outcome | Identical | Keep `R4-004` |
| Network 展店與店網 | Identical | Keep `R4-005` |
| Network Expansion Flow Stepper | Identical | Keep `R4-005` |
| Network 找區域 | Identical | Keep `R4-005` |
| Network 物件雷達 | Identical | Keep `R4-005` |
| Network 候選點工作台 | Identical | Keep `R4-006` |
| Network SiteScore Lab | Identical | Keep `R4-006` |
| Network 候選點比較 | Identical | Keep `R4-006` |
| Network 選址審核 | Identical | Keep `R4-007` |
| Dialog Review Decision | Identical | Keep `R4-007` |
| Network 低效重配 | Identical | Keep `R4-008` |
| Govern 治理稽核 | Identical | Keep `R4-009` |

## Current Product Difference

Package `(6)` does not close any implementation gap by itself. The current
`/operator` audit remains unchanged functionally:

- Main still mounts the older v3 prototype through an iframe.
- Store Ops is missing the R4 all-store four-light summary and quick filters.
- Growth is missing the R4 three-entry area and complete five-step builder.
- Network is missing the expansion stepper, candidate data gate, and complete
  review-decision synchronization.
- Governance has surfaces that are not reachable and remains non-durable.
- Operator read/write API, reload persistence, server RBAC, and immutable audit
  evidence are still required.

The existing `ODP-OC-R4-001` through `ODP-OC-R4-012` scopes therefore remain
valid. `ODP-OC-R4-013` refreshes provenance, and `ODP-OC-R4-014` publishes this
exact ZIP, extracted payload, diff, audit, and complete task pack to `dev` so
independent Fleet worktrees can inspect the design bytes rather than relying on
the prose receipt alone.
