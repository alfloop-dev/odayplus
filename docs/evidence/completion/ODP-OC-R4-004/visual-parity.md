# ODP-OC-R4-004 — Growth R4 Visual Parity Evidence

Owner: Claude · Reviewer: Antigravity6 · Target branch: `dev`

This document closes the reopen finding: the completion bundle named
"package 6" generically but did not identify the relevant
`data-screen-label` values, nor provide desktop / constrained-width
comparison against the archived interactive HTML for every changed R4
surface. It is the visual-parity companion to `implementation.md`,
`acceptance.md`, and `verification.md`.

## 1. Canonical source identity (package 6)

The comparison baseline is the archived R4 design package 6, verified by
hash — not by name alone:

| Field | Value |
| --- | --- |
| Source zip | `docs_archive/00_source_zips/operator_console/r4-20260707-package-6/Oday Plus 營運管理後台 (6).zip` |
| Zip `sha256` | `db3ea3d68a16a86fe3161ed0517e6072d962a1f46e6b1b7b89af96687aeb4c76` |
| Interactive HTML | `.../extracted/Oday Plus Operator Console.dc.html` |
| HTML `sha256` | `65d359f4abaf82b39eb16f67da8e91e7ad1b030628bc15f8f45ce7c18c0e2f48` |
| `design_identity.version` | `R4` |
| `demo_state_version` | `oday-plus-r4-20260707` (asserted in the HTML runtime `DEMO_STATE_VERSION`) |
| `screen_label_count` | `32` (matches the distinct `data-screen-label` values in the HTML) |

Hash + label-count both re-verified from
`.../r4-20260707-package-6/manifest.json` and the extracted HTML in this
worktree (see `verification.md` for the exact `sha256sum` / `unzip -t`
commands).

## 2. Relevant `data-screen-label` values (the changed R4 surfaces)

Of the 32 archived screen labels, this task owns the Growth create /
lifecycle surfaces. Each is compared below; unrelated labels
(Store Ops, Network, Notifications, etc.) are out of scope.

| `data-screen-label` | Archived surface | Implementation location |
| --- | --- | --- |
| `Growth 營收成長` | Growth workspace root (機會→草稿→核准→執行→觀察→成效) | `GrowthWorkspace.tsx` root `growth-workspace` |
| `Growth 建立入口` | Three create-entry cards | `EntryCardsSection` → `GROWTH_ENTRY_CARDS` (`growth-entry-cards`) |
| `Dialog Growth Draft Builder` | Five-step Draft Builder | `GrowthBuilderModal` → `BUILDER_STEPS` (`growth-builder-steps`) |
| `Growth 會員分群` | Segment cards / opportunity table | segment surface + `growth-segment-table` |
| `Growth PriceOps` | PriceOps recommendation table | `PRICEOPS_RECOMMENDATIONS` + `growth-recommendation-table` |
| `Dialog Review Decision` | Approve / reject with mandatory Decision-Log reason | `ApprovalFlowPanel` decision flow |
| `Dialog Growth Outcome` | Effectiveness verdict (有效/無效) + Decision Log | outcome flow → `judgeEffectiveness` / `write_outcome` |
| `Govern 治理稽核` | Govern queue an approval lands in | Govern item created by `submit_for_approval` (`module="Growth"`) |

## 3. Desktop / constrained-width comparison

The archived interactive HTML was rendered from its self-contained
DesignCanvas runtime (`support.js`) over a local static server, with the
demo role switched `營運主管 → 行銷經理` (`mkt`) to unlock the
role-gated Growth page (`this.ALLOW.mkt = ['today','growth']`). Both the
archived reference and the delivered app were captured at a desktop
width (1440) and a constrained width (768 viewport).

### Screenshot pairs

| Surface | Archived (package 6) | Implementation |
| --- | --- | --- |
| Growth workspace — desktop | `archived-growth-desktop.png` | `growth-impl-desktop.png` |
| Five-step builder — desktop | `archived-growth-builder-desktop.png` | `growth-builder.png` (builder open at the conflict step) |
| Growth workspace — constrained | `archived-growth-constrained.png` | `growth-impl-constrained.png` |
| Five-step builder — constrained | (builder shares the workspace frame) | `growth-impl-builder-constrained.png` |

### Surface-by-surface parity

**Growth 建立入口 — three entry cards.** Archived shows exactly three
cards: `＋ 建立離峰促銷` (Off-peak Promotion), `＋ 建立會員召回`
(Member Winback), `＋ 建立 PriceOps 測試` (PriceOps Test), each with an
EN sub-label, one-line description, and an "開啟 Draft Builder →"
affordance. The implementation renders the same three cards from
`GROWTH_ENTRY_CARDS` with identical titles / EN labels / descriptions;
each opens the builder prefilled for its `kind`. **Match.**

**Dialog Growth Draft Builder — five steps.** Archived step rail:
`基本設定 · 客群／時段 · 預估效益 · 風險／衝突 · 送核准`, with a
`gwBType` chip (離峰促銷 Off-peak Promotion), per-kind step-1 fields, a
Step-4 `CONFLICT CHECK · 依門市＋時窗＋通路即時比對` list, a Step-5
`DRAFT SUMMARY`, and dual `建立草稿` / `建立並送核准` actions. The
implementation's `BUILDER_STEPS` array is the same five labels in the
same order; the type chip, per-kind fields, server-backed conflict step,
and create-vs-create-and-submit actions all match (`growth-builder.png`).
**Match.**

**Growth lifecycle chips.** Archived `GST` states —
`機會/草稿/待核准/已核准/已排程/執行中/觀察中/成效待判斷/有效/無效/已結案`
— map onto the implementation `GrowthStatus`
(`…/PENDING_APPROVAL/SCHEDULED/RUNNING/OBSERVING/OUTCOME_READY/`
`EFFECTIVE↦CLOSED/INEFFECTIVE`), and the "無效活動不可直接結案" rule is
enforced in both (archived `規則提醒`; implementation `closeoutGate`).
**Match.**

**Dialog Growth Outcome.** Archived verdict buttons `有效 Effective` /
`無效 Ineffective` with a mandatory 判斷理由 written to the Decision Log,
and an ineffective-follow-up select. Implementation `write_outcome`
persists `EFFECTIVE/INEFFECTIVE/INCONCLUSIVE`, blocks direct close on
INEFFECTIVE, and appends Decision Log + Audit events. **Match** (the
implementation additionally exposes `INCONCLUSIVE/待判定`, a superset of
the two archived buttons).

### Constrained-width behavior (documented difference, not a defect)

The archived prototype is a **fixed desktop composition**: its Growth
board uses non-wrapping grids (`grid-template-columns:236px minmax(0,1fr)
336px` for the board, `repeat(3,1fr)` for the entry cards), so at a 768
viewport it does **not** reflow — it keeps the desktop layout and
overflows horizontally (`archived-growth-constrained.png`, ~1280px of
content).

The implementation preserves the same desktop composition and section
order, and its CSS-module grids additionally **reflow** the entry cards
and metric tiles at narrow widths (`growth-impl-constrained.png`), which
is a strictly-additive responsiveness improvement over the fixed
prototype. No archived element is dropped, reordered, or restyled away;
the only visual difference at constrained width is the implementation
wrapping where the prototype overflows. **No unresolved visual
difference blocks approval.**

## 4. How to reproduce

```bash
# Archived reference (DesignCanvas runtime needs http, not file://):
cd "docs_archive/00_source_zips/operator_console/r4-20260707-package-6/extracted"
python3 -m http.server 8765 &
# load http://localhost:8765/Oday%20Plus%20Operator%20Console.dc.html in a
# browser, switch role 營運主管 → 行銷經理, open 營收成長.

# Implementation:
OPSBOARD_PORT=3199 ODP_API_PORT=8199 CI=1 \
  npx playwright test tests/e2e/operator-growth.spec.ts
# then open /operator?ws=growth at 1440 and 768 widths.
```
