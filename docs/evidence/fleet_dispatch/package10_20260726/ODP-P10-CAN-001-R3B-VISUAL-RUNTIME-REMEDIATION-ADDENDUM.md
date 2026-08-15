---
doc_id: ODP-P10-CAN-001-R3B-ADD-006
title: Package 10 Intake Detail Visual Runtime Remediation Addendum
status: ready-for-supervisor-dispatch
owner: Claude
reviewer: Antigravity
updated_at: 2026-07-26
---

# ODP-P10-CAN-001-R3B ADD-006

## 1. Assignment

Remediate the coordinator-rejected R3B runtime without weakening the canonical
Package 10 design or E2E assertions. Claude owns implementation. Antigravity
performs an independent visual and contract review. Codex remains coordinator
and accepts or rejects the result.

Canonical authority remains:

- `docs_archive/00_source_zips/operator_console/r7-20260720-package-10/Oday Plus 營運管理後台 (10).zip`
- ZIP SHA-256:
  `d1583a00496f928b0765c1756c9671fedf615f12c84c00494d454e983645d7f8`
- Canonical HTML SHA-256:
  `cc4e6ae97462bc99b1c2353c792cb3bec40d51a6c5efcfde165e5f47105e661d`
- `docs/evidence/design_review/assisted_listing_intake_r7_package10/package10-tablet-detail.png`
- `docs/evidence/design_review/assisted_listing_intake_r7_package10/package10-mobile-detail.png`

Read the parent R3B task, ADD-001 through ADD-005, the current R3B ACK, and this
addendum before editing.

## 2. Coordinator Rejection Evidence

The coordinator ran the real FastAPI and Next.js runtime and captured:

- `runtime-r3b/detail-390.png`
- `runtime-r3b/detail-1024.png`
- `runtime-r3b/detail-1440.png`
- `runtime-r3b/direct-detail-1440.png`

Observed geometry:

| Route / viewport | Topbar height | Detail top | Document overflow |
|---|---:|---:|---|
| Operator, 390px | 278px | 1104.34px | none |
| Operator, 1024px | 109px | 696.42px | none |
| Operator, 1440px | 57px | 543.23px | none |
| Direct intake, 1440px | n/a | 494.44px | none |

The detail is therefore not the first canonical workspace surface beneath the
global console chrome. The Network title, KPI strip, expansion stepper,
Network tabs, compliance strip, and status message remain above it on both the
Operator path and the durable direct route.

The runtime screenshots also prove that the parsed-lineage and match-review
tables collapse into the first narrow column. Text wraps character by
character while most of the 1160px detail width is blank. The immediate cause
is native `th` and `td` elements receiving `.fieldCell { display: flex; }`,
which removes table-cell layout semantics.

The client additionally accepts resource version `0`:

```text
validResourceVersion(value) -> Number.isInteger(value) && value >= 0
```

The API contract accepts only `^W/"[1-9][0-9]*"$`; version zero must fail
closed before any assignment or SLA request.

The first full accessibility run also exposed a navigation hydration race:
`network-tab-1` was clicked, but the page remained on Find Areas and
`intake-add-button` never appeared. A focused rerun and a later full run
passed. This is recorded as a product stability defect, not discarded as an
environment failure.

## 3. Required Remediation

### 3.1 Canonical detail placement

When `selected=<intakeId>&dialog=detail` is active, and on
`/intake/<intakeId>`, render the intake detail immediately after the global
Operator topbar/status banner and its durable return/deep-link row.

Do not render these surfaces before the detail:

- Network heading and KPI strip.
- Expansion flow stepper.
- Network tab strip.
- Compliance strip.
- Network status/toast rows.

The canonical source cards and Listing Radar remain below the detail, as shown
in Package 10. Do not delete them and do not reintroduce a modal, drawer,
fixed-offset overlay, nested card, or retired detail tabs.

### 3.2 Table layout and responsive truth

- Native `table`, `thead`, `tbody`, `tr`, `th`, and `td` semantics must remain.
- Never apply `display:flex`, `display:grid`, or `display:block` directly to
  desktop/tablet table cells.
- Put any vertical label/chip composition inside a cell wrapper.
- At 1024px and 1440px, lineage and comparison columns must use the available
  detail width with readable wrapping and no large unexplained blank region.
- At 390px, parsed lineage remains readable without character-per-line text.
- At 390px, only the ambiguous `POSSIBLE_MATCH` comparison/decision surface is
  replaced by the inline `DESKTOP_REQUIRED` state. Preserved values and the
  durable link remain visible.

### 3.3 Resource versions

`validResourceVersion` must accept positive integers only. Add executable
tests proving `0`, negative values, fractions, strings, null, and undefined
all produce `RESOURCE_VERSION_UNAVAILABLE` and invoke none of claim, transfer,
pause, or resume endpoints.

### 3.4 Navigation stability

The Listing Radar tab must not lose a user click during initial preference,
bootstrap, or URL hydration. Fix the product state/URL synchronization rather
than adding retries, sleeps, or weakened assertions to the canonical E2E
spec.

## 4. Additional Authorized Paths

The parent R3B write set remains in force. This addendum additionally
authorizes only:

- `apps/web/features/operator/NetworkFindAreasWorkspace.tsx`
- `apps/web/features/operator/network/__tests__/NetworkFindAreasWorkspace.route-gate.test.tsx`

`tests/e2e/**`, `apps/api/**`, source-policy decisions, auth middleware, and
permission rules remain forbidden. The previously authorized single screen
label change in the a11y spec must not be expanded.

## 5. Required Gates

1. Focused intake and Network route tests pass.
2. Full web unit suite passes.
3. Web and root typecheck pass.
4. Root build passes.
5. The unchanged six-test accessibility spec passes twice consecutively.
6. Runtime screenshots are recaptured at 390px, 1024px, and 1440px plus the
   direct route.
7. Screenshots prove no Network workspace chrome precedes the detail, tables
   are readable, 390px shows the scoped `DESKTOP_REQUIRED` state, and document
   width does not overflow.
8. Static inspection proves no old intake detail tabs, dialogs, orphan
   components, or synthetic assignment/SLA authority returned.
9. `git diff --check` passes.

## 6. Return Contract

Update only the R3B ACK with:

- every rejection finding and its concrete remediation;
- exact test commands and counts;
- screenshot paths and measured geometry;
- other-LLM assignment/conflict inspection;
- implementation commit SHA and pushed ref;
- Claude owner result;
- Antigravity independent review decision;
- coordinator decision.

R3B remains `no-go`. CAN-002 must not start until the coordinator accepts a
committed and pushed R3B ACK and exact product SHA.
