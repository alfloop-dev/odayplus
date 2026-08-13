# Acceptance Packet & Dependency Map: ODP-CAP-XLSX-IMPORT-001

- **Sidecar Task ID**: `ODP-CAP-XLSX-IMPORT-001-SIDECAR-ACCEPTANCE`
- **Parent Task ID**: `ODP-CAP-XLSX-IMPORT-001` — Implement governed XLSX import
- **Helper Kind**: `acceptance_packet`
- **Sidecar Owner / Reviewer**: `Codex5` / `Claude3`
- **Parent Owner / Reviewer**: `Claude3` / `Antigravity`
- **Packet Date**: 2026-08-08
- **Boundary**: support artifact only; this packet does not modify runtime, tests,
  generated OpenAPI artifacts, registries, governance policy, or L1 canonical truth.

## 1. Purpose and baseline pin

This packet turns the parent's five acceptance criteria and the independent
round-1 review findings into a concrete closeout checklist, dependency map, and
evidence plan. It is guidance for the parent owner and reviewer; it is not an
approval of the parent implementation.

| Baseline field | Pin at packet creation |
|---|---|
| Canonical parent state | `in_progress`; re-opened after review |
| Reviewed implementation head | `f0309f299e36de2c688cc6c7c6362d6329a90b68` |
| Parent local composed head | `91d6d9d739a24f83aa786a1e9c0fa0519fd79f62` (`f0309f29` merged with `origin/dev`) |
| Parent remote head | `origin/task/ODP-CAP-XLSX-IMPORT-001` = `f0309f29` |
| Parent PR | None found at packet creation |
| Review evidence | `ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW.md`, round 1 |

The parent local merge contains no XLSX-specific fix commit after `f0309f29`.
Therefore the findings below remain the acceptance starting point. Before using
this packet for approval, replace the reviewed-head pin with the candidate head
and rerun every required check against that exact SHA.

## 2. Acceptance outcome required

The parent is acceptable only when all five task criteria are met together,
domain validation is not bypassed, tenant boundaries hold end to end, and the
repository's blocking contract gate remains green.

At the reviewed head, the exit decision is **not ready for approval**:

| Gate | Required outcome | Reviewed-head gap |
|---|---|---|
| G1 Safe parsing | Untrusted workbook content is bounded, never evaluated, and fails closed | External-link detection can fail open; DTD/entity expansion, sparse cells, and wrong-sheet selection are not safely handled |
| G2 Read-only preview | Preview creates no intake/domain writes | Persistence is read-only, but the process-global preview store is unbounded and not a production authority |
| G3 Governed commit | Only rows accepted by the full domain pipeline are durably persisted | Commit creates UUIDs but persists no intake; commit validation is weaker than preview validation |
| G4 Idempotency | Same scoped request returns the same receipt without duplicate writes or cross-scope replay | Process-global key is not tenant-scoped and leaks the first tenant's receipt |
| G5 Error export | Authorized owner can download masked errors; other scopes learn nothing | Batch lookup is not tenant-bound; unknown IDs return 200; CSV formula injection remains possible |
| G6 Repository gates | Focused tests, OpenAPI drift gate, and lint pass | Checked-in OpenAPI is stale and `ruff` reports errors |

## 3. Dependency map

```mermaid
flowchart TD
    Client[Operator / API client]
    Auth[Tenant and actor principal]
    Replay[Route replay and request fingerprint]
    Routes[apps/api/app/routes/listings.py<br/>XLSX preview / commit / error export]
    Parser[Safe XLSX parser<br/>ZIP, XML, workbook relationships, sparse cells]
    Mapping[Header mapping and row normalization]
    Domain[assisted_intake domain validation<br/>URL and source-policy resolution]
    Batch[Batch authority<br/>tenant ownership, TTL, row snapshot]
    Idem[Durable idempotency authority<br/>scope + request hash + receipt]
    Intake[Intake repository / unit of work]
    Audit[Audit log]
    Export[Masked JSON / CSV / XLSX exporter]
    Readback[GET /intakes/{id}]
    OpenAPI[Checked-in OpenAPI and generated client]
    CI[Contract, unit, security, lint gates]

    Client --> Auth --> Routes
    Replay --> Routes
    Routes --> Parser --> Mapping --> Domain
    Domain --> Batch
    Batch --> Idem --> Intake --> Readback
    Intake --> Audit
    Batch --> Export
    Routes --> OpenAPI --> CI
    Parser --> CI
    Domain --> CI
    Intake --> CI
    Export --> CI
```

### 3.1 Dependency and ownership matrix

| Dependency | Property the parent must preserve | Acceptance evidence |
|---|---|---|
| Request principal / authorization | Tenant and actor context is mandatory for preview, commit, and export; no caller may address another tenant's batch | Two-tenant API test for every batch operation |
| Route replay boundary | Idempotency scope includes tenant, actor or authorized principal, operation, key, and request fingerprint | Same-scope replay, changed-payload conflict, and cross-tenant collision tests |
| XLSX ZIP/XML parser | Enforce upload, entry, decompressed-byte, and amplification limits while reading; prohibit entity expansion and external fetches | Malformed ZIP/XML, DTD/entity, external relationship, and compressed-amplification fixtures |
| Workbook metadata | Resolve the first visible worksheet through `workbook.xml` relationships and honor cell/row references | Non-`sheet1.xml` first sheet, sparse cell, and non-contiguous row fixtures |
| Mapping + domain validation | Preview and commit use one authoritative validation result or the exact same full validator | Differential test showing every preview rejection is also rejected at commit |
| Batch authority | Batch belongs to a tenant/principal, expires, is bounded, and has defined replica/restart semantics | Ownership, expiration, capacity, and restart/replica tests or an explicit durable-store contract |
| Intake persistence | Commit writes accepted normalized rows through the existing intake repository/unit-of-work boundary | Repository spy plus API readback of every returned `intake_id` |
| Idempotency authority | A committed result survives the supported process/replica lifecycle without duplicate writes | Retry after restart/second replica and write-count assertion |
| Error exporter | Mask sensitive values in all formats and neutralize spreadsheet formula prefixes | Golden JSON/CSV/XLSX tests using secrets, email, phone, and `= + - @` payloads |
| Audit authority | Commit success/failure and security-relevant denials are attributable to tenant, actor, batch, and receipt | Audit assertions with no unmasked sensitive payloads |
| OpenAPI/client artifact | All three routes and their schemas are checked in and reproducible | `delivery_toolchain/openapi/check_drift.py` exits 0 |

## 4. Parent acceptance checklist

### AC-1 — Malformed formula and external-link inputs fail safely

- [ ] Corrupt ZIPs, invalid XML, missing workbook parts, and unsupported workbook
      structures return a controlled 4xx response without a traceback or partial
      batch/commit state.
- [ ] Formula cells are never evaluated, invoked, dereferenced, or copied into an
      executable export form. The parent documents whether such a row is rejected
      or quarantined; a warning alone must not allow dangerous content to commit.
- [ ] External-link parts and external relationship targets fail closed. A parser
      exception cannot silently disable the check.
- [ ] DTD/entity expansion and external entities are rejected before materializing
      expanded cell content.
- [ ] Limits apply to bytes actually read/decompressed, not only attacker-controlled
      ZIP metadata; the implementation stops reading as soon as a limit is crossed.
- [ ] Workbook relationship order, cell `r` references, and row `r` references are
      honored so sparse or reordered worksheets cannot silently remap data.
- [ ] Numeric signs survive normalization (`-50000` must not become `50000`).

Required fixtures: corrupt archive, malformed XML, formula with a cached value,
external `.rels`, DTD/entity expansion, high-compression entry, sparse row,
out-of-order worksheet filename, and negative numeric text.

### AC-2 — Preview performs no writes

- [ ] Preview invokes no intake repository `save`/`insert`, transaction commit,
      outbox publish, or equivalent domain write.
- [ ] A repository/unit-of-work spy proves a zero write count, including when some
      rows are valid and when parsing fails mid-file.
- [ ] Preview results are isolated by tenant/principal and cannot be enumerated or
      read using only another tenant's `batch_id`.
- [ ] Any preview cache has an explicit TTL and capacity/eviction rule. Its behavior
      across supported workers and restarts is defined; process-global unbounded
      dictionaries are not treated as durable authority.
- [ ] Repeated preview requests do not mutate previously returned batch snapshots.

### AC-3 — Commit writes validated rows only

- [ ] Commit consumes an immutable preview snapshot or reruns the complete same
      mapping and domain-validation pipeline. It must not use a reduced validator.
- [ ] Address, rent, area, floor, URL, source-policy, quarantine, and every other
      preview domain rule are enforced at commit.
- [ ] Every accepted row is durably written through the intake repository/unit of
      work; generated IDs without a write do not satisfy this criterion.
- [ ] Every rejected row produces a stable row-indexed error and produces no intake
      write. Spreadsheet row numbers—not array positions—are reported.
- [ ] Every returned `intake_id` is readable through the supported intake read path
      by the same authorized tenant after commit.
- [ ] Partial-failure/transaction semantics are explicit and tested so a failure
      cannot produce an ambiguous receipt or orphaned subset.
- [ ] Commit emits attributable, non-sensitive audit evidence.

Mandatory differential assertion: for the same row set, the commit-accepted set
must be a subset of the full preview-valid set, and persisted rows must equal the
commit-accepted set exactly.

### AC-4 — Duplicate commit is idempotent

- [ ] Idempotency identity is at least tenant + authorized principal + operation +
      idempotency key; the request content/batch identity is fingerprinted.
- [ ] Same scope, key, and payload returns the same receipt and performs zero extra
      intake writes.
- [ ] Same scope and key with a different payload/batch returns a conflict rather
      than replaying an unrelated receipt.
- [ ] Different tenants using the same key never share `batch_id`, counts,
      `intake_ids`, or audit records.
- [ ] The guarantee holds for the supported restart and multi-replica topology, or
      the API contract explicitly narrows the guarantee and the deployment enforces
      that boundary.
- [ ] Concurrent identical requests cannot both win the write race.

### AC-5 — Row errors are downloadable with sensitive masking

- [ ] The route loads a tenant/principal-owned batch; access by another tenant is
      denied without returning row data or revealing whether the batch exists.
- [ ] Unknown or expired batch IDs return the documented not-found response.
- [ ] JSON, CSV, and XLSX output consistently mask secrets, tokens, email addresses,
      phone numbers, and any other governed sensitive fields.
- [ ] CSV/XLSX cells beginning with `=`, `+`, `-`, or `@` are neutralized without
      corrupting legitimate negative numeric values.
- [ ] Download headers, media types, filenames, encodings, and empty-report behavior
      are covered by API tests.
- [ ] Export never reads or writes intake state and never returns another tenant's
      raw preview row.

### Cross-cutting gates

- [ ] The task summary constraint—domain validation must never be bypassed—is a
      blocking invariant, not an optional hardening item.
- [ ] The checked-in OpenAPI artifact and generated client reflect all XLSX routes.
- [ ] Focused unit/contract tests assert the named property rather than only response
      shape or counts.
- [ ] Lint and formatting checks pass for every changed Python file.
- [ ] Completion evidence names the exact candidate SHA and does not claim writes,
      durability, or isolation that the tests do not demonstrate.

## 5. Review-finding disposition map

This map prevents a fix from satisfying a checklist box while leaving the
original failure mode untested. Finding labels refer to the round-1 review packet.

| Finding | Acceptance owner | Required disposition before parent approval |
|---|---|---|
| B1 no persistence | AC-3 | Real repository write + readback + write-count evidence |
| B2 weaker commit validation | AC-3 / cross-cutting | One validation authority + differential rejection test |
| B3 cross-tenant idempotency leak | AC-4 | Scoped durable key + two-tenant collision test |
| B4 cross-tenant error export | AC-5 | Ownership check + two-tenant denial test |
| B5 sparse-cell corruption | AC-1 | Cell-reference-aware parser + sparse fixture |
| B6 OpenAPI drift | Cross-cutting | Regenerate artifact/client and pass drift check |
| N1 / N2 / N9 XML and ZIP fail-open risks | AC-1 | Fail-closed parser and read-time resource limits |
| N3 / N4 row/sheet addressing | AC-1 / AC-3 | Workbook relationship and row-reference fixtures |
| N5 sign corruption | AC-1 / AC-3 | Type-aware normalization test |
| N6 export formula injection | AC-5 | Format-safe escaping golden tests |
| N7 unbounded preview store | AC-2 | TTL/capacity plus supported topology semantics |
| N8 unknown batch returns 200 | AC-5 | Reachable not-found behavior test |
| N10 lint failures | Cross-cutting | Clean lint on changed surface |
| N11 property-free tests | All | Replace with state/write/isolation assertions |

## 6. Candidate-head verification protocol

Run from the parent task worktree after replacing `<candidate-sha>` with the exact
review head. Record command, exit code, and concise result in completion evidence.

```bash
test "$(git rev-parse HEAD)" = "<candidate-sha>"

uv run pytest -q \
  tests/unit/listing/test_xlsx_import.py \
  tests/contract/test_xlsx_import_api.py

uv run pytest -q tests/contract/test_openapi_artifact_and_client.py
uv run python delivery_toolchain/openapi/check_drift.py

uv run ruff check \
  modules/external_data/application/xlsx_import.py \
  apps/api/app/routes/listings.py \
  tests/unit/listing/test_xlsx_import.py \
  tests/contract/test_xlsx_import_api.py

git diff --check
```

The focused suite must include the adversarial fixtures and stateful assertions
listed in §4. Passing the original eight tests alone is insufficient because the
round-1 review showed that two named acceptance tests did not assert the property
in their names.

Reviewer spot checks should also confirm:

1. each returned `intake_id` resolves through the supported read path;
2. tenant B cannot replay or export tenant A's batch using colliding identifiers;
3. a second worker/restart behaves according to the documented idempotency and
   batch-authority contract;
4. `git merge-base --is-ancestor <candidate-sha> origin/dev` is evaluated only at
   final closeout, not used as a substitute for review evidence.

## 7. Handoff to parent owner

For `Claude3`:

- Use §4 as the parent implementation and evidence checklist.
- Keep the parent review pin current; do not reuse `f0309f29` results for a later
  fix head without rerunning the checks.
- Treat B1–B6 as blocking. The N-series items mapped to a stated acceptance or
  security property in §5 must either be fixed at the parent head or receive an
  explicit reviewer-approved scope disposition; this sidecar does not alter the
  canonical task scope.
- Hand the resulting candidate and evidence to the parent's assigned reviewer,
  `Antigravity`.

For this sidecar's reviewer, `Claude3`: review only this support packet's accuracy,
scope boundary, and usefulness. Absorption into the parent implementation remains
a parent-owner decision.
