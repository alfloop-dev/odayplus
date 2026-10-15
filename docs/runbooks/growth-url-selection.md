# Growth URL selection and unavailable records

Task: `ODP-SYS-ACCEPT-261011`. The archived R7 handoff requires cold open,
reload, back/forward and shareable links. This fixes record selection within
the existing Growth tabs and URL vocabulary; independent full-screen review
and whole-system acceptance remain open.

`item` is an explicit record identity. If it is absent from the currently
scoped API snapshot, the detail panel shows unavailable and exposes no other
record's approval, transition or outcome controls. The URL remains intact so
the caller can retry or select an available row. A subsequently missing record
unmounts the old detail; returning data restores only that requested ID.

`segment` is an explicit data scope. A missing segment produces an empty
action/recommendation selection instead of widening to all records. A valid
empty segment does not select an action from another segment. Navigation
preserves the requested segment and unrelated query fields until the caller
explicitly selects another scope. A `draft` recommendation must exist in the
current segment's recommendations; a missing/differently scoped target cannot
open an unrelated builder.

When `item` is absent, default selection uses the first action matching the
existing `gkind`/`gstatus` filters. An explicitly requested available item stays
the requested item even if the card list filters hide it; the detail remains
bound to that identity. Empty API lists yield a null selection/gate instead of
throwing or selecting an unrelated global record. The existing kind/status
display classification is shared between the view model and card list without
introducing new business statuses.

Tests cover missing/empty/cross-segment targets, disappearing/returning rows,
default filtering and preserved URL fields. Supplemental browser checks use
explicit synthetic data at 1440/1024/390, verify direct/reload/back/forward,
no business POSTs and unavailable-panel accessibility. They remain outside
the fixed business-case inventory and do not admit live accounts or sources.

## Additional discovered release gap

`RecommendationSection` in `GrowthWorkspace.tsx` still computes PriceOps scenario
bands from price multipliers and `handleWriteback` creates a random decision ID
with a fixed actor entirely in browser state. This is not backend simulation,
idempotent writeback or audit. The existing canonical API is
`POST /priceops/plans/{plan_id}/decision-writeback`, backed by
`PriceOpsService.writeback_decision`; canonical recommendation projection
already supplies `planId` and `itemId`. The frontend recommendation type does
not yet retain these bindings. This remains a mandatory follow-up, including
actual simulation/provenance, verified identity/authority, matching receipts,
reason, retry and source/record scope. The URL fix does not claim to complete it.

## Supply-chain check correction

The PDF dependency change updated the lock and notice but omitted the committed
SBOM. Parent CI's security lane therefore failed two freshness tests. Regenerate
`docs/evidence/sbom.json` using the repository generator; do not edit tests,
remove the freshness gate, modify legal exemptions or change human receipts.
The generated difference adds only runtime ReportLab and dev pypdf components
and refreshes its genuine generation metadata/content digest. Historical
failed CI output stays in acceptance evidence.

The full parent backend CI also exposed seven `.gitattributes` classification
failures and seven outdated Governance admission checks. The Git attributes
file is now explicitly listed under the existing kept `repository_metadata`
configuration rule; no catch-all, provider surface, freeze inventory or approval
status is changed. The live journey runner matches the actual decision HTTP
correlation, never supplies the old risk/budget reason, and blocks a scope with
no authored reason before mutations. Offline HTTP proofs now use the real
permission dependency with explicit synthetic subject/tenant/role instead of
an unguarded router. Wrong audit correlation remains a failing negative probe.
These updates do not execute a live journey or admit production credentials.
