# ODP-OC-R4-004 — Growth R4 Visual Parity Evidence

Owner: Claude · Reviewer: Antigravity6 · Target branch: `dev`

This document is the visual-parity companion to `implementation.md`,
`acceptance.md`, and `verification.md`.

## 0. Reopen resolution (2026-07-14)

The prior bundle (PR #282 / commit `aaf13c48`) was **reopened**: the
delivered Growth screen did not actually match canonical package 6. The
concrete findings, and how each is now fixed in the delivered app:

| Reopen finding | Root cause | Fix |
| --- | --- | --- |
| Growth had a different information architecture (flat Segment/PriceOps/Growth-Action stack) vs package 6's three-column lifecycle/filter/detail workbench | `GrowthWorkspace` rendered stacked `<section>` tables | Rebuilt to the package-6 IA: inline header → three entry cards → **tab bar (活動 / 會員分群 / PriceOps)** → default **活動** tab is a **three-column campaign workbench** (filter rail · action cards · sticky lifecycle detail). `GrowthWorkspace.tsx` + new `growth.module.css`. |
| Desktop wrapped Growth in an OpsBoard **sidebar + global header** that the design does not have | `OpsBoardFrame` (root layout) wrapped **every** route, including `/operator` | `OpsBoardFrame` now renders `/operator*` **full-bleed** (no `AppShell` sidebar/global header); the console keeps only its own "Top Navigation", which *is* in the design. Shared shell fix for all operator workspaces. |
| A **nested Operator header** (breadcrumb + `PageHeader`) not in the design | Growth rendered `@oday-plus/ui` `PageHeader` with an "Operator Console / 營收成長" breadcrumb | Removed; replaced with the design's inline title row (`營收成長` + subtitle + freshness). |
| Constrained width had outer header / control / text **overlap** | The two stacked headers (OpsBoard global header + console topbar) collided | Resolved by the chrome removal above; the workbench also collapses to a single column ≤1120px and entry cards to one column ≤820px (`growth.module.css` media queries). |

The `growth-prefix-*-fail.png` renders are retained as the *before* (fail)
state; `growth-impl-*.png` are the *after* (fixed) renders.

## 1. Canonical source identity (package 6)

The comparison baseline is the archived R4 design package 6, verified by
hash — not by name alone:

| Field | Value |
| --- | --- |
| Source zip | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip` |
| Zip `sha256` | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` |
| Interactive HTML | `.../extracted/Oday Plus Operator Console.dc.html` |
| `design_identity.version` | `R4` |
| `demo_state_version` | `oday-plus-r4-20260707` (asserted in the HTML runtime `DEMO_STATE_VERSION`) |
| `screen_label_count` | `32` (distinct `data-screen-label` values in the HTML) |

## 2. Relevant `data-screen-label` values (the changed R4 surfaces)

The canonical Growth screen (`data-screen-label="Growth 營收成長"`, HTML
line 641, container `max-width:1720px;margin:0 auto;padding:16px 20px 44px`)
nests these labels; each maps to a piece of the rebuilt implementation:

| `data-screen-label` | Archived surface | Implementation location |
| --- | --- | --- |
| `Growth 營收成長` | Growth screen root: inline title `營收成長` + subtitle `機會 → 草稿 → 核准 → 執行 → 觀察 → 成效…` | `GrowthWorkspace` root `growth-workspace` (`g.screen`, `max-width:1720px`) |
| `Growth 建立入口` | Three create-entry cards, `grid repeat(3,1fr)` | `EntryCardsSection` → `GROWTH_ENTRY_CARDS` (`growth-entry-cards`) |
| `Growth 會員分群` | Segment cards, `grid auto-fill minmax(232px,1fr)` | `SegmentSection` (`growth-tab-segments` → `growth-segment-table`) |
| `Growth PriceOps` | PriceOps pricing table | `RecommendationSection` (`growth-tab-priceops` → `growth-recommendation-table`) |
| campaign column (`gwTabCamp`, `grid 236px minmax(0,1fr) 336px`) | Filter rail · action cards · sticky lifecycle detail | `CampaignWorkbench` (`g.campaign`) — default `活動` tab |
| `Dialog Growth Draft Builder` | Five-step Draft Builder | `GrowthBuilderModal` → `BUILDER_STEPS` (`growth-builder-steps`) |
| `Dialog Review Decision` | Approve / reject with mandatory reason | `ApprovalFlowPanel` |
| `Dialog Growth Outcome` | Effectiveness verdict + Decision Log | `CloseoutPanel` → `judgeEffectiveness` / `write_outcome` |
| `Govern 治理稽核` | Govern queue the approval lands in | Govern item from `submit_for_approval` (`module="Growth"`) |

## 3. Desktop / constrained-width comparison

Both the archived interactive HTML (rendered from its DesignCanvas runtime
with the demo role switched `營運主管 → 行銷經理` to unlock the role-gated
Growth page) and the delivered app were captured at a desktop width (1440)
and a constrained width (768).

### Screenshot set

| Surface | Archived (package 6) | Implementation (fixed) |
| --- | --- | --- |
| Growth workspace — desktop | `archived-growth-desktop.png` | `growth-impl-desktop.png` |
| Growth workspace — constrained (768) | `archived-growth-constrained.png` | `growth-impl-constrained.png` |
| Five-step builder — desktop | `archived-growth-builder-desktop.png` | `growth-impl-builder-desktop.png` |
| 會員分群 (Segments) tab — desktop | (segment card grid in the archived Growth screen) | `growth-impl-segments.png` |
| PriceOps tab — desktop | (PriceOps table in the archived Growth screen) | `growth-impl-priceops.png` |
| Reopen *before* state (fail evidence) | — | `growth-prefix-desktop-fail.png`, `growth-prefix-constrained-fail.png` |

### Surface-by-surface parity (after the rebuild)

- **Shell.** Delivered `/operator` now shows only the console's own dark
  Top Navigation — no OpsBoard left sidebar, no second global header, no
  breadcrumb — matching the archived screen which sits directly under
  "Top Navigation". **Match** (was the primary reopen failure).
- **Growth 建立入口.** Three cards `＋ 建立離峰促銷` / `＋ 建立會員召回`
  / `＋ 建立 PriceOps 測試`, each with an EN sub-label, one-line
  description and an "開啟 Draft Builder →" affordance, in a
  `repeat(3,1fr)` grid — same as archived. **Match.**
- **Tab bar.** `活動 Campaign / 會員分群 Segments / PriceOps`, with 活動
  the default active tab (archived `gwTabCamp` default). **Match.**
- **活動 campaign workbench.** Three columns — left rail (`＋ 新增 Growth
  Action` + 類型 / 狀態 filter chips + rule reminder), a middle action-card
  list (id · type · status · next-step · title · segment/window · metric
  row), and a right sticky detail panel with the **8-step lifecycle
  stepper** (草稿→送審→核准→排程→執行→觀察→成效→結案), impact rows,
  目標 / 成效判斷 / Rollback, approval + closeout gate, and audit ids —
  matching archived layout `236px minmax(0,1fr) 336px`. **Match.**
- **會員分群.** Segment cards (`minmax(232px,1fr)` grid) with name, trend,
  count, revenue share, 建議打法, data-status and 建立活動草稿 — same as
  archived. **Match.**
- **PriceOps.** Pricing table (門市/時窗/現價/建議價/… + 建立草稿), with the
  HARD_CONSTRAINT_FAILED row's draft action disabled. **Match.**
- **Dialog Growth Draft Builder.** Five-step rail
  (`基本設定 · 客群／時段 · 預估效益 · 風險／衝突 · 送核准`) with a type
  chip, per-step fields, a Step-4 server conflict-check panel, and a Step-5
  review summary + dual `建立草稿` / `建立並送核准` actions. **Match.**
- **Growth lifecycle rule.** The "無效活動不可直接結案" rule (archived
  `規則提醒`) is enforced in `closeoutGate`; INEFFECTIVE cannot close
  directly. **Match.**
- **Constrained (768).** No header/control/text overlap; the campaign
  workbench collapses to a single column and the entry cards to one column.
  **Match** (was the constrained-overlap reopen failure).

No unresolved visual difference remains between the delivered Growth
surfaces and canonical package 6. The `growth-prefix-*-fail.png` renders
document the rejected prior state for the audit trail.
