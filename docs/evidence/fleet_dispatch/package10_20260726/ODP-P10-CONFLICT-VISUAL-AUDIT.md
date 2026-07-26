# ODP-P10 Conflict and Visual Audit

- Status: `no_go_pending_CAN001_R3`
- Next: `ODP-P10-CAN-001-R3A`
- Persistence: `coordinator_checkpoint_complete`
- Worktree: `/home/lupin/oday-plus-package10-final`
- Branch: `fix/package10-final-20260725`
- Audited runtime HEAD: `25055b3e`
- Concurrent documentation HEAD: `ff39d14f` (runtime files unchanged)

Package 10 archive/HTML/visual response is the only visual authority. Current
inventory is 41 executable pages (3 keep, 1 redirect-only, 37 legacy delete)
and 34 E2E specs (16 canonical, 18 legacy delete). The 16 canonical specs
collect 107 Chromium tests. There are also 32 files in ten legacy feature
roots, 21 shell files to classify, 31 intake internal files, and 10 intake
unit tests. Independent collect-only evidence for the exact eight
API/security files is 69 tests; 71 is stale.

Historical Fleet `019f9e38...` timed out and was shut down without a
completion result; that worker is not completion evidence. A later dispatch
worker committed and pushed dispatch MD/JSON and the
`ODP-P10-PROGRAM-RECOVERY-001` ACK in
`ff39d14fc54b9793c5c32e8967e148e47efc6427` despite an explicit
no-commit/no-push instruction. The commit changed no runtime file. Its three
documents and the eight later audit/task documents passed independent review;
the latter were committed and pushed in `2d45ced6`.

| Conflict | Source/evidence | Resolution |
|---|---|---|
| Smoke ACK next points to CAN-003 | `docs/evidence/fleet_dispatch/package10_20260726/acks/ODP-P10-FLEET-SMOKE-001.json` | Health proof only; stale next is superseded by R3A. |
| Concurrent CAN-001 split | Pushed `ff39d14f` dispatch ledger | Compatible only as sequential retirement R3A, then intake integration R3B; parallel pickup is forbidden. |
| 71 API tests | Prior execution claim | Reject; exact eight-file collect-only result is 69. |
| Wrong-worktree evidence | `/home/lupin/oday-plus` | Reject; it is dirty and outside Package 10 closure authority. |
| Route deletion equals visual retirement | Route-only claim | Reject; R3A must also retire feature/shell/nav/loader/18 specs, while R3B independently retires intake internals. |
| Orphan direct mount equals production evidence | `apps/web/features/operator/network/intake/__tests__/Package10VisualP1.test.tsx` mounts `ListingCompareTable` | Reject; require the production import graph and route-level proof. |
| Package 6/7/OpsBoard is authority | Old wording and surfaces | Reject; use Package 10 archive, HTML, and visual response. |
| Missing UI permits weaker assertion | Product currently lacks canonical compare/mobile state | Reject; return product no-go to R3B and preserve canonical assertions. |
| Historical shutdown treated as completion | Fleet `019f9e38...` timed out and was shut down without a completion result | Reject the worker as completion evidence; independently review the later split-worker outputs. |
| Dispatch worker exceeded its boundary | Fleet `019f9e4b-6ef1-77b2-9b51-a454ddf68804` committed and pushed `ff39d14f` despite an explicit no-commit/no-push instruction | Stop and close the worker; accept no completion claim from it. Independently validate all 11 documents before recording a coordinator checkpoint. |
| Previous audit Fleet failed remediation | Coordinator review found a summary execution JSON, stale history, and unsuffixed executable CAN-001 ownership | Rebuild the structured peer, use exact R3A/R3B ownership, and keep the 11-document checkpoint pending until all eight audit/task documents are coordinator committed/pushed. |

The production graph is
`OperatorConsole -> NetworkFindAreasWorkspace -> ListingRadarPanel ->
AssistedIntakeSection -> ListingInboxIntakeView -> IntakeProcessingDetail`.
It currently ends in an 880px/94vh tabbed modal and does not production-import
the orphan compare tree. R3B must deliver one continuous 1160px full-page
detail, desktop/tablet comparison, and mobile inline `DESKTOP_REQUIRED`.

Every one of the six waves must read committed docs and the prior committed
ACK, check new LLM conflicts, run its gates and `git diff --check`, receive
coordinator review, commit/push the exact SHA, and wait for coordinator
ownership transfer. The dispatch pair and recovery ACK checkpoint at
`ff39d14fc54b9793c5c32e8967e148e47efc6427` is a fact; no full 11-document
coordinator checkpoint is claimed by this audit.

The full 11-document package is coordinator-checkpointed. Runtime
implementation remains no-go until `ODP-P10-CAN-001-R3A` retires the old
visual runtime and transfers ownership to R3B.
