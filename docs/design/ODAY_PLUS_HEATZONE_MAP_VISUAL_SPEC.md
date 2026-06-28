---
doc_id: ODP-UXD-003-HEATZONE-MAP-VISUAL-SPEC
title: "ODay Plus HeatZone Map Visual Spec"
version: 0.1.0
status: draft
document_class: ux-blueprint
project: ODay Plus
language: zh-TW
updated_at: 2026-06-28
owner: "Product Design / Frontend"
approvers: "Product Lead / Frontend Lead"
content_format: markdown
source_documents:
  - docs/design/ODAY_PLUS_EXPANSION_WORKFLOW_BLUEPRINT.md
  - docs/design/ODAY_PLUS_COMPONENT_CONTRACTS.md
  - docs/design/ODAY_PLUS_VISUAL_DESIGN_SYSTEM.md
related_documents:
  - modules/heatzone/domain/scoring.py
  - apps/api/oday_api/routes/heatzone.py
---

# ODay Plus HeatZone Map Visual Spec

## 1. Purpose & Boundary

本文件定義 `/w/expansion/heatzone` 的地圖視覺、圖層、互動、tooltip、drawer 與資料契約。HeatZone 地圖是決策工具，不是裝飾性 dashboard；主要任務是幫展店審查者找出「值得看 Listing、值得送 SiteScore」的格網。

後端既有契約：

- `GET /heatzones/map` 回傳 FeatureCollection，feature properties 來自 `HeatZoneScoreResult.to_map_feature()`。
- `POST /heatzones/score-jobs` 建立 score job，回傳 scores、map_features、job_id、audit_event_id、correlation_id。
- `GET /heatzones/snapshots/{snapshot_id}` 讀 snapshot。
- `GET /heatzones/{h3_index}` 讀單一 heat zone detail。

## 2. Data Contract

Map feature 必讀欄位：

| Property | UI use |
|---|---|
| `heat_zone_id` | feature id、drawer deep link |
| `h3_index` | H3 cell label、detail route key |
| `score` | choropleth strength and ranking |
| `priority_rank` | side panel rank |
| `unmet_demand_score` | score breakdown |
| `format_fit_score` | ODay G2 fit breakdown |
| `cannibalization_risk` | risk overlay / warning |
| `rent_feasibility` | affordability breakdown |
| `listing_availability` | listing layer badge |
| `confidence` | opacity / warning / disabled action |
| `status` | HeatZone state chip |
| `last_scored_at` | data freshness |
| `model_version` | model badge |
| `feature_version` | feature view badge |
| `admin_city`, `admin_district` | geographic grouping |
| `warnings` | tooltip and drawer warning list |

Geometry can be absent in early API responses. If `geometry` is null, frontend must render the row/ranked-list alternative and show a map inline warning: `地圖 geometry 尚未可用；列表仍可用於審查。`

## 3. Visual Layers

| Layer | Default | Purpose | Interaction |
|---|---|---|---|
| Base map | on | Neutral geography, roads, district labels | pan/zoom only |
| H3 HeatZone | on | score and state by cell | hover tooltip, click drawer |
| Listing points | off initially | active listings / candidate availability | click listing preview |
| Candidate sites | on when available | converted candidate sites | click candidate drawer |
| Existing stores | on | cannibalization context | hover store summary |
| Competitors / POI | off initially | evidence context | cluster at low zoom |
| Low confidence mask | on | confidence below threshold | disabled direct SiteScore |

Layer toggles live in the map toolbar and must persist in URL query as `layers=h3,candidates,stores`.

## 4. Color & Encoding

Use semantic map tokens from the design system; do not introduce one-off colors.

### 4.1 Score Encoding

- Choropleth by `score`, bucketed into 5 bins: `0-20`, `20-40`, `40-60`, `60-80`, `80-100`.
- Higher score uses stronger heat tone, but never as the only signal. Each cell tooltip and side panel shows numeric score and rank.
- Selected cell has focus outline token and does not rely on fill color alone.

### 4.2 State Encoding

| `status` | Label | Visual rule |
|---|---|---|
| `UNTOUCHED` | 未開發 | neutral border + score fill |
| `PARTIALLY_ABSORBED` | 部分吸收 | blue/info chip |
| `SATURATED` | 飽和 | gray/red risk pattern, deprioritized unless evidence says otherwise |
| `UNDER_REALIZED` | 未實現 | orange warning chip |
| `STILL_EXPANDABLE` | 可擴張 | green success chip |
| `SUPPRESSED_LOW_CONFIDENCE` | 低信心暫停 | diagonal pattern + disabled direct action |

### 4.3 Confidence Encoding

- `confidence >= 0.85`：normal opacity。
- `0.70 <= confidence < 0.85`：show warning chip。
- `< 0.70`：apply low-confidence pattern, action disabled, tooltip explains reasons from `warnings`。

## 5. Map Layout

Desktop `lg+`：

```text
Page Header
Filter Bar
Map Toolbar
Map Canvas                                  Ranked Panel
  Base + H3 + optional layers                 KPIs
  Tooltip / selection                         Top heat zones table
                                               Selected HeatZoneScoreCard
```

Required map toolbar controls:

- District filter shortcut.
- Score threshold slider.
- Confidence threshold slider.
- Layer menu.
- Map theme: `minimal|light|dark|print_safe`，default `minimal`。
- Fit to results.
- Export visible rows，需 audit + watermark when sensitive layers are included。

## 6. Hover Tooltip

Tooltip must be compact and keyboard reachable:

```text
Rank #12 · 大安區
Score 82 / 100 · STILL_EXPANDABLE
Unmet demand 0.91 · ODay G2 Fit 0.78
Cannibalization risk 0.22 · Rent feasibility 0.74
Confidence 0.88 · scored 2026-06-28
```

If warnings exist, show the first warning inline and expose full list in drawer.

## 7. Click / Selection Behavior

Clicking a cell:

1. Sets URL `selected=<heat_zone_id>&drawer=zone`.
2. Opens Right Drawer with `HeatZoneScoreCard`.
3. Keeps map viewport, filters, layers, selected rank in URL or restorable state.
4. Moves keyboard focus into drawer; closing returns focus to selected cell or row.

Clicking a listing or candidate point:

- Listing: opens listing preview drawer and preserves `heatZone=<id>` context.
- Candidate: opens candidate drawer with SiteScore readiness.

## 8. Ranked Panel

Ranked panel is not optional because it is the accessible/list alternative for the map.

Required columns:

| Column | Behavior |
|---|---|
| Rank | `priority_rank` |
| Area | admin city/district or h3 index |
| Score | numeric + bucket label |
| State | state chip with text |
| Confidence | numeric + warning |
| Listings | active listing availability |
| Action | open drawer / view listings |

Panel supports keyboard navigation. Selecting a row selects the matching cell.

## 9. Drawer Content

`HeatZoneScoreCard` drawer sections:

1. Summary: rank, score, state, district, h3 resolution.
2. Score breakdown: unmet demand, format fit, cannibalization risk, rent feasibility, listing availability.
3. Evidence: POI count, competitor count, active listing count, median rent, existing stores if available.
4. Confidence and warnings: reasons, data quality, source snapshots.
5. Version/Audit: model version, feature version, feature snapshot time, prediction origin time, last scored at.
6. Next actions: view listings, create research task, rerun score job, export evidence.

Direct `run SiteScore` is not shown on HeatZone unless a candidate site is selected. HeatZone points to Listing/Candidate workflow instead.

## 10. Loading / Empty / Error

| State | Required UI |
|---|---|
| Loading | map skeleton + ranked panel skeleton; chrome already visible |
| Empty | `EmptyState`: no HeatZone scores, actions `重新計算 HeatZone` and `檢查資料來源` |
| Error | inline error panel with code, correlation_id, retry, timestamp |
| Partial | map renders available features; failed layers show layer-level error |
| No geometry | ranked panel usable; map warning, no crash |

Score job progress must show `QUEUED|RUNNING|SUCCEEDED|FAILED|PARTIAL` and never fake percentages.

## 11. Permission & Sensitive Data

- Users without `expansion.heatzone.read` do not see the route entry; direct URL -> 403.
- `expansion.heatzone.score` required for rerun score job.
- Export requires explicit permission, reason, audit, and watermark.
- Rent, competitor detail, and precise listing attributes can be masked by field permission while still showing aggregate score.

## 12. Accessibility

- Every map feature has an equivalent row in ranked panel.
- Keyboard users can move through ranked rows, open drawer, close drawer, and activate next actions.
- State/risk uses text + icon/pattern + tooltip, not color alone.
- Respect reduced motion; map transitions should be short and disableable.
- Provide data table export / view for screen reader users.

## 13. Implementation Checklist

- [ ] `/heatzones/map` FeatureCollection is accepted even with null geometry.
- [ ] H3 score, state, confidence, selection, and low-confidence mask are visually distinct.
- [ ] Ranked panel is feature-complete and keyboard usable.
- [ ] Drawer shows score breakdown, evidence, warnings, versions, and next actions.
- [ ] Layer settings, filters, selected cell, and drawer state are URL-restorable.
- [ ] Loading/empty/error/partial/no-geometry states are visible and do not blank the page.
- [ ] Export and rerun score job go through permission + audit.
