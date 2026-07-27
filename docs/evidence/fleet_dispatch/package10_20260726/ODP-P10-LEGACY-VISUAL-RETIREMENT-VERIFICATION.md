# Package 10 Legacy Visual Retirement Verification

- Verification date: `2026-07-26`
- Canonical worktree: `/home/lupin/oday-plus-package10-final`
- Canonical branch: `fix/package10-final-20260725`
- Verified HEAD: `435c79e3a99839541aa3710d58049010e3ba7ab7`
- Result: `retirement_verified_at_head`
- Release status: `NO-GO`

This is a coordinator current-state verification, not a replacement for the
R3A or R3B owner ACKs. It proves that the retired executable visual
implementations are absent from the current canonical branch. It does not
claim that Package 10 is release-ready.

## Verified Evidence

| Requirement | Current-head evidence | Result |
|---|---|---|
| Only canonical executable pages remain | `find apps/web/src/app -type f -name 'page.tsx'` returns only `/operator`, `/intake/[intakeId]`, and `/franchisee` | Pass |
| Every path retired by R3A and R3B remains absent | The two committed ACK inventories contain 117 unique `deleted_paths`; checking every path at HEAD returns no survivor | Pass |
| Retired shell CSS is gone | `odp-shell`, `odp-skip-link`, `odp-header`, `odp-env-badge`, `odp-iconbtn`, `odp-sidebar`, `odp-navlink`, and `odp-main` have zero active matches | Pass |
| Reusable generic control CSS was not over-deleted | `.odp-select` remains in `packages/ui/src/styles/shell.css` | Pass |
| Old product identity is absent from active code | `OpsBoard` and `R0 導覽骨架` have zero matches under `apps/web` and `packages/ui` | Pass |
| Alternate intake detail visuals are gone | `AssistedIntakeQueuePanel`, `IdentityDecisionPanel`, `IntakeAssignmentSlaDialog`, and `IntakeDetailDialog` have zero source or test matches | Pass |
| Canonical detail owns comparison and evidence | `IntakeProcessingDetail.tsx` directly imports and renders `ListingCompareTable` and `MatchEvidencePanel` | Pass |
| Unit evidence mounts the production detail | `Package10VisualP1.test.tsx` imports and renders `IntakeProcessingDetail`; it does not mount a retired alternative | Pass |
| Remaining dialogs are commands, not an alternate page | `IntakeDialogShell` is used only by add URL, field correction, transfer, identity decision, and SLA pause command dialogs | Pass |

Authoritative retirement inventories:

- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3A.json`
- `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-CAN-001-R3B.json`

## Release Boundary

Visual retirement is verified, but the program remains `NO-GO`:

1. PR #396 (`ODP-P10-CAN-001-R3C`) and PR #397
   (`ODP-P10-CAN-001-R3D`) still require real Antigravity4 review.
2. R3A must consume the accepted R3C/R3D SHAs, restore the required
   `originalUrl` retry coverage, and replace stale selectors/assertions without
   weakening Package 10 behavior.
3. R3B must run the declared 16-spec/107-test Chromium gate unchanged.
4. CAN-004 must rerun this retirement verification against the exact release
   SHA and reconcile the accepted SiteScore, live E2E, WIF, and auth/runtime
   SHAs before any deployment claim.

Any resurrection of a retired path, selector, alternate detail component, or
legacy E2E spec is a release blocker. Compatibility markup is not an accepted
remediation.
