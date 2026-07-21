# Fleet Execution Addendum: ODP-EXT-002 / Operator Console R5

- Parent: ODP-PV-LIVE-SRC-001
- Status: required composition input for `ODP-OC-R5-001`
- Scope boundary: external_data_sources
- Owner lane: integration / source ingestion
- Reviewer lane: governance / product validation
- Execution branch: `task/ODP-OC-R5-001`
- Release authority: none; product release is gated by `ODP-OC-R5-002` and `ODP-OC-R5-003`

## Task Boundary

This addendum does not rewrite or close the historical PR #82
`ODP-EXT-002` dispatch. It narrows the current product implementation to the
human-assisted Package 7 workflow. `ODP-OC-R5-001` owns the integrated UI/API
delivery and composes the existing ingestion primitives.

- Allowed paths: `apps/web/features/listing/`, `modules/external_data/`, `modules/integration/`, `packages/schemas/source_contracts/external/`, `scripts/external_data_backfill.py`, listing-related E2E/integration tests, and task-specific files under `docs/evidence/completion/`.
- Out of scope: map UI implementation, remote staging deployment changes, unrelated product workflows, and any committed provider secrets or raw sensitive provider data.
- External dependencies: provider terms approval remains external operational state. A provider credential is required only when the approved retrieval method requires one.

## Objective

Build a human-assisted listing intake workflow. A user selects a listing on an
external site and submits its URL; the system determines whether it is new,
retrieves and parses the page only when that source and retrieval method are
approved, then persists raw evidence, canonical data, revisions, and quarantine
records. Scheduled crawling and automatic site discovery are not part of this
task.

## Canonical Design Source

- Latest pointer: `docs_archive/00_source_zips/operator_console/LATEST.json`
- R5 archive: `docs_archive/00_source_zips/operator_console/r5-20260715-package-7/`
- Interactive source: `docs_archive/00_source_zips/operator_console/r5-20260715-package-7/extracted/Oday Plus Operator Console.dc.html`
- Design requirements: `docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_DESIGN_REQUIREMENTS.md`
- Package diff: `docs/evidence/OPERATOR_CONSOLE_DESIGN_PACKAGE_7_DIFF_2026-07-15.md`
- Required labels: `Network URL 收件佇列`, `Dialog 從網址新增物件`,
  `Dialog 收件處理詳情`, `Dialog 欄位修正`, `Dialog 收件決策確認`.

The worker and reviewer must open the extracted interactive source. The prose
requirement alone is not visual acceptance evidence.

## Revised Workflow Decision (2026-07-14)

```text
Human finds listing
-> submit URL
-> normalize URL and identify source
-> exact duplicate check
-> approved-access policy gate
-> retrieve page or request assisted field entry
-> persist immutable raw snapshot
-> parse and normalize fields
-> content fingerprint / entity match
-> create new listing, append revision, or quarantine
-> human review and promote to candidate
```

### Newness and Revision Rules

- Exact duplicate: same provider plus canonical URL or stable provider listing ID.
- Probable same entity: normalized address plus area, floor, and listing type;
  rent/price changes alone create a revision, not a new entity.
- New listing: no exact identity and no entity match above the configured
  threshold. Ambiguous matches require human review and are never auto-merged.
- Every submission records submitter, submitted time, original URL, canonical
  URL, source, match result, parser version, and correlation ID.

### Access and Parsing Policy

- Do not crawl search/result pages, enumerate listing IDs, or schedule site-wide
  discovery.
- Server-side page retrieval is enabled only for a source/method with recorded
  approval. Robots/terms checks do not replace written authorization where the
  provider requires it.
- If retrieval is not approved, keep the URL as source evidence and use an
  assisted entry form or operator-provided HTML snapshot. Do not call private
  browser APIs or reuse session cookies.
- Store only fields required for expansion review. Redact contact and personal
  data unless explicitly approved and required.

## Current Proof Boundary

- Current proof: deterministic listing fixtures, source contracts, and a
  provider-feed adapter that remains available for future licensed feeds.
- The revised live claim requires a real human-submitted URL, policy-gate proof,
  persisted raw/canonical snapshots, identity/revision results, and human review
  evidence. It does not require unattended discovery or a bulk provider feed.

## Execution Commands

- `unzip -t 'docs_archive/00_source_zips/operator_console/r5-20260715-package-7/Oday Plus 營運管理後台 (7).zip'`
- `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-002`
- `uv run pytest tests/integration/test_external_source_connectors.py tests/e2e/test_external_source_product_e2e.py -q`

## Blocking Dependencies

- Provider terms approval and the allowed retrieval method are external operational state.
- Provider secrets must never be committed.

## Implementation Evidence Required

- human URL submission and source-policy gate
- canonical URL and provider listing ID extraction
- raw landing snapshot
- exact identity key and content fingerprint
- canonical transform
- entity revision history
- quarantine path

## Verification Evidence Required

- first submission contract test
- exact duplicate contract test
- changed-price revision contract test
- ambiguous entity match review test
- malformed payload contract test
- unapproved-source fail-closed test
- timeout contract test
- fixture-compatible replay

## Acceptance Criteria

- a user can submit one listing URL without enabling scheduled crawling
- the system classifies it as new, exact duplicate, revision, or needs review
- approved retrieval persists raw and canonical snapshots with lineage
- unapproved retrieval falls back to assisted entry without fetching the page
- bad records enter quarantine
- fixture replay remains CI default

## Handoff Artifacts

- assisted-intake E2E receipt
- source snapshot sample
- duplicate and revision decision evidence
- quarantine event evidence

## Repo-Side Evidence Added 2026-06-30

- `modules/external_data/providers/live.py` provides `ListingPartnerFeedProvider`, fixture replay, and an HTTP live adapter boundary with redacted credential objects.
- Raw snapshot, canonical snapshot, source snapshot id, idempotency keys, duplicate quarantine, malformed-row quarantine, and correlation id are covered through deterministic fixture and approved mock paths.
- Live fetch still requires external provider endpoint and credential values outside the repository; without them, validation/fetch fails closed.

## Verification Observed 2026-06-30

- `python3 -m pytest tests/integration/test_external_source_connectors.py` -> `6 passed in 0.14s`
- `python3 -m pytest tests/e2e/test_external_source_product_e2e.py` -> `5 passed in 2.21s`
- `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-002` -> `Fleet dispatch checks passed for ODP-EXT-002.`

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic fixture proof
- provider secrets must never be committed
- completion evidence must include the implementation, verification, acceptance,
  and handoff artifacts named above
- this ingestion task cannot claim product completion without R5-001 UI integration
  and the mandatory R5-002 product gate
