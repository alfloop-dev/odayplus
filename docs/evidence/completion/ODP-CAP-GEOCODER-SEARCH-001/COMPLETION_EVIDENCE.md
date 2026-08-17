# ODP-CAP-GEOCODER-SEARCH-001 — Completion Evidence

- Task: Implement governed frontend geocoder search
- Owner: Claude3 · Reviewer: Claude · Target branch: `dev`
- Baseline: `origin/dev` @ `8585f2b2`
- Capability: `UX-SCR-EXP-001` "前端 geocoder 地址搜尋未接", listed in
  `docs/design/ODAY_PLUS_UNOWNED_CAPABILITY_SCOPE_DECISION_2026-08-03.md` §5
- Scope note: delivered **without waiting for the production geocoder
  endpoint**, per the task brief. The unconfigured endpoint is handled as an
  explicit, honest error state rather than by falling back to fixtures.

## Deliverables

| Artifact | Purpose |
|---|---|
| `apps/web/features/operator/network/geocoder/geocoderTypes.ts` | UI projection of `geocode_result_snapshot`: candidate shape, quality-flag vocabulary, review requirement, audit event |
| `apps/web/features/operator/network/geocoder/geocoderPolicy.ts` | The governance core — thresholds, market bounds, admin-match rule, precision tiers, query/selection gates, risk copy |
| `apps/web/features/operator/network/geocoder/geocoderClient.ts` | The only lookup path; endpoint resolution, error vocabulary, and the defensive parser that decides which provider rows are admissible |
| `apps/web/features/operator/network/geocoder/geocoderAudit.ts` | Audit-event builders for selection, low-confidence override, and recorded rejection |
| `apps/web/features/operator/network/geocoder/geocoderPermissions.ts` | Role gating: search = `expansion-manager` + `pm-audit`; select = `expansion-manager` only |
| `apps/web/features/operator/network/geocoder/GeocoderSearchPanel.tsx` | The operator surface — search, candidate list, explicit-review gate, error states, audited rejection |
| `apps/web/features/operator/network/geocoder/geocoder.module.css` | Responsive stylesheet (desktop row layout, stacked below 760px) |
| `apps/web/features/operator/NetworkFindAreasWorkspace.tsx` | Production mounting on the Find Areas tab, plus the accepted/rejected geocode receipt |
| `apps/web/features/operator/networkFindAreas.module.css` | Receipt styling in the tray column |
| `.../geocoder/__tests__/geocoderPolicy.test.ts` | 30 tests pinning every threshold and gate |
| `.../geocoder/__tests__/geocoderClient.test.ts` | 18 tests, over half of them on the no-fabrication invariant |
| `.../geocoder/__tests__/GeocoderSearchPanel.test.tsx` | 16 tests over the four acceptance behaviours |
| `.../network/__tests__/NetworkFindAreasGeocoder.test.tsx` | 4 tests proving the capability is actually mounted and role-gated |

## Acceptance criteria

### 1. Address search and candidate selection work

`GeocoderSearchPanel` takes a query, validates it locally (`validateQuery`
rejects blank and under-4-character input before spending a provider call),
issues one POST through `geocoderClient.searchAddress`, and renders each
returned candidate with its coordinate, precision, confidence, provider and
administrative levels. Selecting a candidate opens a confirm block; confirming
emits the candidate, its assessment, and the audit event to the parent.

The parser accepts **both** wire shapes: the provider gateway's current
single-result `{ result, request_id, observed_at }`
(`services/provider-gateway/app.py` `POST /geocode`) and the multi-candidate
`{ results: [...] }` the production endpoint will return, so the surface does
not need reworking when that endpoint lands.

Mounted at `NetworkFindAreasWorkspace → FindAreasPanel → tray column`, covered
by `NetworkFindAreasGeocoder.test.tsx`. It sits in the tray rather than
`.mapPanel` because that panel is a fixed-height grid area with
`overflow: hidden` on this screen, so content stacked above the deck.gl canvas
is clipped.

### 2. Low-confidence candidates require explicit review

The thresholds are **not invented here** — they mirror
`modules/external_data/geo/pipeline.py` verbatim so the console and the
ingestion pipeline cannot disagree about what "low confidence" means:

| Rule | Console | Pipeline source |
|---|---|---|
| `confidence < 0.7` → `low_geocode_confidence` | `LOW_CONFIDENCE_THRESHOLD` | `GeoPipeline.geocode_record` L180 |
| Taiwan bounding box → `coordinates_out_of_market` | `coordinatesInMarket` | `coordinates_in_market`, `TAIWAN_*_RANGE` |
| Both sides state a differing admin level → `admin_mismatch` | `adminMatches` | `GeoPipeline._admin_matches` |
| NFKC + 臺→台 + floor-suffix strip | `normalizeAddress` | `normalize_address` |

Two UI-side refinements are namespaced as such: `coarse_precision`
(district/centroid/manual/approximate locate a neighbourhood, not a unit) and
`unknown_precision`.

**The policy fails closed.** Any candidate carrying at least one flag becomes
`explicit_review_required`, and confirming it needs BOTH an acknowledgement and
a written reason of at least 10 characters — the acknowledgement alone records
no rationale for an audit reader. An absent or non-finite confidence is treated
as below-threshold rather than coerced to a passing value, and an unrecognised
precision tier routes to review rather than being assumed precise.

### 3. Errors never fabricate coordinates

This is the invariant the client is built around, enforced at four points:

1. **No fixture fallback.** An absent or `mock://` `NEXT_PUBLIC_ODP_GEOCODER_URL`
   returns a structured `ODP-GEOCODER-UNCONFIGURED` error and the panel never
   calls out. The copy states explicitly that no simulated coordinate and no
   administrative centroid is being substituted.
2. **Coordinates are a hard admission gate.** A provider row without two finite
   numeric coordinates is dropped, never repaired with a default or a centroid.
   Dropped rows are counted into `rejectedRowCount` and disclosed on screen, so
   a partial answer never reads as a complete one.
3. **Every failure path returns `{ ok: false }`.** HTTP status, malformed body,
   network failure and client timeout each map to a distinct error code. A body
   that will not parse is a failure, not an empty result — "no candidates" and
   "we could not read the answer" must not look alike.
4. **A failed retry clears the previous result.** A stale candidate list under a
   fresh error banner is exactly how a fabricated coordinate gets used, so the
   panel drops the prior result on any error
   (`GeocoderSearchPanel.test.tsx` — "clears a previous result when a retry
   fails").

Errors render the server's own copy (never overwritten with invented text)
alongside the error code, correlation ID and timestamp, matching the
`OperatorApiError` envelope in `../operatorNetworkClient`.

### 4. Selection and override actions are audited

`buildSelectionAuditEvent` records actor role, timestamp, correlation ID, the
raw address as typed, the full selected coordinate/precision/confidence/provider,
the quality flags, the review requirement, the acknowledgement, and the reason.
`action` is `low_confidence_override` whenever a flagged candidate was accepted
anyway, so a governance query can count overrides without re-deriving the policy.

The risk summary is captured **verbatim as displayed** rather than rebuilt at
submit time: an audit reader needs what the operator was actually shown.

`buildRejectionAuditEvent` covers the third terminal action — an operator who
finds nothing usable records that, with a mandatory reason. "The geocoder
offered nothing usable" is itself governance evidence: it explains why a
downstream record has no coordinate, and must not be indistinguishable from a
search that never happened.

### 5. Responsive UI and focused tests

`geocoder.module.css` is fluid to a phone: below 760px the search control goes
full width, the candidate row stacks, coordinates wrap instead of overflowing,
and confirm actions become full-width targets. State is never encoded by colour
alone — every candidate spells out "需人工覆核" or "可直接採用" in text next to
the colour treatment, matching the intake surface's accessibility rule.

## Verification

```
cd apps/web
npx vitest run                      # 39 files, 333 tests passed
npx tsc --noEmit                    # clean
npx next lint --dir features/operator/network/geocoder   # no warnings or errors
```

The 68 tests added by this task (30 policy + 18 client + 16 panel + 4 wiring)
all pass. Pre-existing `@next/next/no-assign-module-variable` lint errors in
`features/operator/governance/governanceEnvelope.ts` are untouched by this task.

## Deliberate boundaries

- **No backend change.** `modules/external_data/geo/pipeline.py`, the provider
  gateway's `POST /geocode`, and `packages/schemas` are unmodified. This lane
  owns the frontend projection only.
- **The accepted coordinate is not persisted.** It is held as an on-screen
  receipt with its audit fields, because the production geocoder endpoint — and
  the write path that would accompany it — is not yet wired. The alternative
  was to silently drop the operator's decision; showing what would be persisted
  is the honest interim. Persistence lands with that endpoint.
- **Thresholds are mirrored, not owned.** If `geo/pipeline.py` moves its 0.7
  cutoff or its market bounds, `geocoderPolicy.ts` must move with it; the
  constants carry comments naming their source for exactly this reason.
