# ODP-P10-CAN-001-R3B Listing Radar Addendum

- Addendum ID: `ODP-P10-CAN-001-R3B-ADD-001`
- Parent task: `ODP-P10-CAN-001-R3B`
- Status: `ready_for_pickup`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Predecessor product commit: `ded04ac49504a1d948831eb5077625bef415ab50`
- Predecessor ACK commit: `2442108400a77ba5fa4adafa8a083461529a03dd`

## Finding

Coordinator review after R3A found active Package 7 baseline wording in
`apps/web/features/operator/network/ListingRadarPanel.tsx`. The file is in the
production intake graph, but the original R3B writable path list omitted it.

## Authorization

R3B may edit:

```text
apps/web/features/operator/network/ListingRadarPanel.tsx
```

The authorized change is limited to removing or replacing stale Package 7
visual-baseline wording so the production graph identifies Package 10 as its
authority. No Listing Radar behavior, API binding, state model, or layout may
change under this addendum unless the parent R3B composition requires a direct
entry-point update and the R3B ACK names that necessity and diff.

## Gate

After R3B:

- active app/package source in the production intake graph contains no
  `Package 6` or `Package 7` visual-baseline wording;
- `tests/e2e/operator-network-assisted-intake.spec.ts` remains unchanged in
  R3B even though its test titles still say Package 7; CAN-003-R3A owns that
  canonical spec wording and assertion alignment;
- all parent R3B gates still apply.

The R3B ACK must cite this committed addendum and record the original writable
path omission as an other-LLM/task-inventory conflict.
