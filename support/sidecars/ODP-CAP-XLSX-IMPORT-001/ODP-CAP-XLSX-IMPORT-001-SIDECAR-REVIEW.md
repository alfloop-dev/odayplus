# Sidecar Review Packet: ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW

- **Task ID**: `ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW`
- **Parent Task**: `ODP-CAP-XLSX-IMPORT-001` — *Implement governed xlsx import* (owner `Antigravity`, reviewer `Antigravity2`, status `review`)
- **Helper Kind**: `review_packet`
- **Owner**: `Claude2`
- **Reviewer**: `Antigravity`
- **Packet Revision**: **round 1 (2026-08-08)**
- **Target Artifact**: `support/sidecars/ODP-CAP-XLSX-IMPORT-001/ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW.md`

### Parent Pin (read this before trusting any number below)

| Field | Round 1 pin |
|---|---|
| Parent head | **`f0309f29`** — *ODP-CAP-XLSX-IMPORT-001: implement governed xlsx import* (committed by `Antigravity6`, `2026-08-08T10:39:01Z`) |
| Parent status | `review` (`ai-status.json`, `last_update` `2026-08-08T10:39:29Z`) |
| Parent branch | `task/ODP-CAP-XLSX-IMPORT-001` — **local only**, not on `origin` |
| Parent PR | **none** — `gh pr list --search XLSX --state all` → `[]` |
| Merge-base with `origin/dev` | `8585f2b2` |
| Landed on `dev`? | **No** — `git merge-base --is-ancestor f0309f29 origin/dev` → exit 1 |
| Recorded `base_sha` | `956170de` (an ancestor of both `f0309f29` and `dev`, but **not** the actual merge-base; see §6 Q4) |
| Deliverable surface | 6 files, **1413 insertions / 0 deletions** (§2) |
| `dev` at time of review | `2cd107d5` |

> [!CAUTION]
> **The parent is red on `dev`'s own test suite, and the failure is a regression
> this commit introduces.** `tests/contract/test_openapi_artifact_and_client.py::test_artifact_is_checked_in_and_matches_the_live_app`
> **passes at the merge-base `8585f2b2` (17 passed) and fails at `f0309f29`.** The
> three new routes were never exported into `packages/openapi-client/openapi.json`.
> CI's blocking `Check API contract drift` step (`make api-contract`,
> `.github/workflows/ci.yml` L114–118) exits **1** at the parent head. See §5 B6 —
> this alone stops the parent's PR from merging, and it is a one-command fix.

> [!CAUTION]
> **Two findings are cross-tenant data leaks reproducible through the HTTP API**,
> not theoretical: **B3** (a second tenant reusing an `Idempotency-Key` value
> receives the *first* tenant's commit receipt) and **B4** (any authenticated
> actor can download any other tenant's row-error report by `batch_id`). Both are
> caused by process-global dicts in `xlsx_import.py` that carry no tenant binding,
> while the surrounding route code *does* scope correctly. §5 has the transcripts.

> [!WARNING]
> **`commit_xlsx_import()` does not write anything.** Its "commit" loop allocates
> `uuid.uuid4()` values into a list and returns them as `intake_ids`; there is no
> repository, session, insert, or persist call anywhere in the function (**B1**).
> The acceptance criterion *"commit writes validated rows only"* is therefore
> satisfied vacuously, and `GET /intakes/{id}` returns **404** for every id the
> commit endpoint hands back. The completion evidence's claim that commit
> *"Idempotently commits validated rows into intake state"* is not accurate at
> this head.

---

## Core Notice & Scope Boundary

> [!NOTE]
> This sidecar task is support-only. It creates exactly one file, under
> `support/sidecars/ODP-CAP-XLSX-IMPORT-001/`, and modifies **no** L1 canonical
> document, contract truth, runtime, registry, or governance implementation.
> Nothing under `modules/`, `apps/`, `infra/`, `scripts/`, or `tests/` is touched
> by this branch. All verification ran read-only inside throwaway detached
> worktrees at `f0309f29` and `8585f2b2`; both were removed after the run, and
> the probe scripts were deleted with them. Nothing in this branch executes.

---

## 1. Capability Being Delivered

The parent implements governed XLSX intake as a third submission path alongside
the existing URL and CSV paths in the assisted-listing intake API. Its five
acceptance criteria, from `ai-status.json`:

| # | Acceptance criterion (verbatim) |
|---|---|
| AC-1 | `malformed formula and external-link inputs fail safely` |
| AC-2 | `preview performs no writes` |
| AC-3 | `commit writes validated rows only` |
| AC-4 | `duplicate commit is idempotent` |
| AC-5 | `row errors are downloadable with sensitive masking` |

The task summary adds a hard constraint that is not in the AC list but is
governing: **「不得繞過 domain validation」** — domain validation must not be
bypassed. `xlsx_import.py`'s own module docstring restates it as rule 6:
*"Domain validation (URL, address, scope, ranges) is strictly enforced and never
bypassed."* §5 B2 is a direct violation of that rule.

A notable and defensible design call: the parser is **zero-dependency**, built on
stdlib `zipfile` + `xml.etree.ElementTree` rather than adding `openpyxl`. That
keeps the dependency surface flat and avoids a large parser in the request path.
The cost is that OpenXML's sparse-cell and sheet-ordering semantics have to be
reimplemented by hand — and §5 B5 and N4 are exactly where that hand-rolling is
incomplete.

---

## 2. Delivered Surface @ `f0309f29`

```
 apps/api/app/routes/listings.py                                      +163
 docs/evidence/completion/ODP-CAP-XLSX-IMPORT-001/completion_summary.md +45
 modules/external_data/application/__init__.py                         +30
 modules/external_data/application/xlsx_import.py                     +791
 tests/contract/test_xlsx_import_api.py                               +134
 tests/unit/listing/test_xlsx_import.py                               +250
                                                                ────────────
                                                                 1413  -0    across 6 files
```

Pure addition — zero deletions, no generated state mirrors swept in. `Cross-Dir: yes`
is correctly declared on the commit (it spans `apps/`, `docs/`, `modules/`, `tests/`).

### Layer map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│      Governed XLSX import — delivered surface @ f0309f29                      │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  apps/api/app/routes/listings.py                                             │
│   POST /intake-batches/xlsx/preview        L1750  previewXlsxBatch            │
│   POST /intake-batches/xlsx/commit         L1790  commitXlsxBatch  (202)      │
│   GET  /intake-batches/xlsx/errors/{id}/export L1840 exportXlsxBatchErrors    │
│        │            │                   │                                    │
│        │  authorize_intake_action("submit_csv" / "view")   ← reused, correct  │
│        │  replay(key, body, tenant_id, actor_id, op, make) ← tenant-scoped ✓  │
│        ▼            ▼                   ▼                                    │
│  modules/external_data/application/xlsx_import.py                            │
│                                                                              │
│   SafeXlsxParser  L163 ── zipfile + ElementTree                              │
│     _check_zip_limits     L202  entries ≤500, declared size ≤100 MB   ← N2    │
│     _check_external_links L210  warns only; except: pass               ← N9   │
│     _find_first_sheet     L244  sorted(filenames)[0]                   ← N4   │
│     _parse_sheet_rows     L252  positional cells, ignores @r           ← B5   │
│     _parse_cell_value     L300  formulas flagged, never evaluated       ✓     │
│                                                                              │
│   map_and_validate_rows L385 ── address / rent / area / floor / URL+policy    │
│   preview_xlsx_import   L549 ──► _PREVIEW_STORE   (module global) ← N7, B4    │
│   commit_xlsx_import    L585 ──► _IDEMPOTENCY_STORE (module global) ← B3      │
│                                └─ re-validates address + rent ONLY    ← B2    │
│                                └─ writes NOTHING                      ← B1    │
│   export_xlsx_import_errors L674 ── json / csv / xlsx + masking       ← N6    │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Delivered components

**1. `SafeXlsxParser`** — `xlsx_import.py` L163–340. Stdlib-only OpenXML reader.
Caps at `MAX_XLSX_BYTES` 20 MB, `MAX_UNCOMPRESSED_BYTES` 100 MB, `MAX_ZIP_ENTRIES`
500. Formula cells (`<f>`) are detected and their text scanned for
`HYPERLINK/CMD/EXEC/DDE/[/]`; the formula is **never evaluated** — only the cached
`<v>` is read. External links are detected two ways: an `externalLinks` path in the
namelist, and `.rels` entries with `TargetMode="External"` or an absolute
`http/https/ftp/file` target.

**2. `mask_sensitive_value()`** — L347–378. Three regex passes: secrets
(`password|secret|token|apikey|bearer` + delimiter), emails (`u***r@domain`),
phones (TW mobile `09xx` and landline forms → `0912****78`).

**3. `map_and_validate_rows()`** — L385–537. Maps 20 header aliases (zh + en) to
7 target fields, then applies five domain rules: required `address_raw`,
non-negative numeric `rent_amount`, non-negative numeric `area_ping`, floor
normalisation, and `validate_url()` + `resolve_source_policy()` with quarantine
rejection. Reuses `assisted_intake`'s existing normalisers rather than
reimplementing them — the right call.

**4. `preview_xlsx_import()`** — L549–582. Parse → map → validate → cache into
`_PREVIEW_STORE`, returns first 20 valid rows as `preview_rows`.

**5. `commit_xlsx_import()`** — L585–671. Idempotency-cache check, a *reduced*
re-validation pass, uuid allocation, audit event. See B1/B2/B3.

**6. `export_xlsx_import_errors()`** — L674–791. Masks every error field, then
emits JSON, CSV (UTF-8 BOM), or a hand-built minimal XLSX with a shared-strings
table.

**7. Three API routes** — `listings.py` L1744–1875, with Pydantic request/response
models at L362–391.

**8. Tests** — 5 unit + 3 contract = **8**, matching the `Verified:` trailer.

---

## 3. Acceptance Verification Matrix

| Ref | Criterion | Verdict @ `f0309f29` | Basis |
|---|---|---|---|
| **AC-1** | malformed formula and external-link inputs fail safely | **Met for execution; not for containment** — formulas are provably never evaluated (P9), corrupt ZIP/XML fails closed. But external links and formulas produce a *warning only* and the row still flows to commit (§6 Q1), the XXE claim in the docstring is unimplemented (N1), and `_check_external_links` swallows exceptions (N9). |
| **AC-2** | preview performs no writes | **Met for persistence; qualified** — no DB/storage write occurs. Preview does mutate process-global `_PREVIEW_STORE` without bound (N7). The test named for this criterion asserts nothing about writes (N11). |
| **AC-3** | commit writes validated rows only | **NOT MET** — commit writes *nothing at all* (**B1**), and its re-validation is strictly weaker than preview's, accepting rows preview rejects (**B2**). |
| **AC-4** | duplicate commit is idempotent | **Met within one process, but unsafely** — replay works; the cache is process-local (lost on restart, not shared across replicas) and **not tenant-scoped**, which is **B3**. |
| **AC-5** | row errors are downloadable with sensitive masking | **Met for masking; NOT MET for access control** — the three masking patterns work. Retrieval has no ownership check (**B4**), unknown ids return an empty 200 instead of 404 (N8), and the CSV export reintroduces formula injection (N6). |
| **AC-6** | *(implicit)* domain validation is never bypassed — task summary + module rule 6 | **NOT MET** — **B2**. |
| **AC-7** | *(implicit)* repo test suite and CI gates stay green | **NOT MET** — **B6**: contract test regresses green→red, `make api-contract` exits 1. |
| **AC-8** | *(implicit)* clean lint across the delivered surface | **NOT MET** — 9 `ruff` errors (N10). |

---

## 4. Verification Suite Commands

The parent branch is local-only and unpushed, so reproduce via a detached
worktree. Run from the repo root.

```bash
# 0. Materialise both pins (throwaway; removed at the end)
git worktree add --detach /tmp/odp-xlsx-pin-f0309f29 f0309f29
git worktree add --detach /tmp/odp-xlsx-base-8585f2b2 8585f2b2

# 1. Parent's own declared test surface  (expect: 8 passed)
cd /tmp/odp-xlsx-pin-f0309f29
/home/lupin/.local/bin/uv run pytest -q \
  tests/unit/listing/test_xlsx_import.py tests/contract/test_xlsx_import_api.py

# 2. The regression the parent's Verified: trailer did not cover
/home/lupin/.local/bin/uv run pytest -q tests/contract/test_openapi_artifact_and_client.py
#    -> FAILS at f0309f29
cd /tmp/odp-xlsx-base-8585f2b2
/home/lupin/.local/bin/uv run pytest -q tests/contract/test_openapi_artifact_and_client.py
#    -> 17 passed at the merge-base.  Regression confirmed.

# 3. The blocking CI gate (.github/workflows/ci.yml "Check API contract drift")
cd /tmp/odp-xlsx-pin-f0309f29
/home/lupin/.local/bin/uv run python scripts/openapi/check_drift.py; echo "exit=$?"

# 4. Lint
/home/lupin/.local/bin/uv run ruff check --output-format=concise \
  modules/external_data/application/xlsx_import.py apps/api/app/routes/listings.py \
  tests/unit/listing/test_xlsx_import.py tests/contract/test_xlsx_import_api.py

# 5. Deliverable shape / landing state
git diff --numstat 8585f2b2 f0309f29
git merge-base --is-ancestor f0309f29 origin/dev; echo "ancestor-of-dev: $?"
gh pr list --search "XLSX" --state all --json number

# 6. Cleanup
cd - && git worktree remove /tmp/odp-xlsx-pin-f0309f29 \
     && git worktree remove /tmp/odp-xlsx-base-8585f2b2
```

### Recorded results (owner run, round 1, 2026-08-08, `dev` = `2cd107d5`)

| # | Command | Result |
|---|---|---|
| 1 | `pytest` on the parent's two test files | **8 passed** — matches the `Verified:` trailer exactly |
| 2a | `pytest tests/contract/test_openapi_artifact_and_client.py` @ `f0309f29` | **FAILED** `test_artifact_is_checked_in_and_matches_the_live_app` |
| 2b | same file @ `8585f2b2` (merge-base) | **17 passed** — so 2a is a regression, not pre-existing |
| 3 | `scripts/openapi/check_drift.py` | **`API contract gate: FAIL`**, real exit code **1**. `[1/3] artifact freshness` ERROR; `[2/3]` OK; `[3/3]` 0 breaking |
| 4 | `ruff check` | **9 errors** (2× B904, 3× I001, 3× F401, 1× B007) |
| 5 | `git diff --numstat 8585f2b2 f0309f29` | 6 files, **1413 insertions, 0 deletions** |
| — | `git merge-base --is-ancestor f0309f29 origin/dev` | exit **1** — parent has not landed |
| — | `git branch -r --list '*XLSX*'` | empty — parent branch is not on `origin` |
| — | `gh pr list --search XLSX --state all` | `[]` — no PR exists |

> [!NOTE]
> Reviewer reproduction notes: `pytest` and `ruff` are not on `PATH` in a task
> worktree — invoke through `uv` exactly as written. The first `uv run` in a
> fresh worktree provisions a `.venv` (234 packages, ~2 s). `scripts/openapi/check_drift.py`
> emits MLflow bootstrap noise on stderr; ignore it and read the final gate line.
> Note that piping `check_drift.py` into `tail` masks its exit status — redirect
> to a file if you need the real code.

### Probe results (§5 evidence)

Every finding below was reproduced with read-only scripts exercising only public
APIs (plus `_PREVIEW_STORE` / `_IDEMPOTENCY_STORE`, which the parent's own route
code also imports directly). Probes were deleted after the run and are
reconstructible from the code blocks in §5.

| Probe | Claim | Observed |
|---|---|---|
| P1 | Sparse row (Excel omits blank `<c>`) shifts values left | `坪數`=25 landed in `rent_amount`; `租金` never populated |
| P2 | Reported `row_index` ignores the `r` attribute | spreadsheet row 7 reported as `row_index=2` |
| P3 | Commit accepts rows preview rejects | preview `valid=0, ['INVALID_RANGE','INVALID_URL']` vs commit `accepted=1, rejected=0` |
| P4 | Commit performs no persistence | no `repository`/`session`/`save`/`insert`/`persist` token in the function body; `intake_id = str(uuid.uuid4())` |
| P5 | Module idempotency cache is not tenant-scoped | tenant-b receipt == tenant-a's `batch_id` **and** `intake_ids` |
| P6 | Text `-50000` is "sanitised" into `+50000` | parsed as `50000`, sole error is `FORMULA_WARNING`, row **valid** with `rent_amount=50000.0` |
| P7 | CSV export preserves a leading `=` | data line ends `,=cmd\|'/c calc'!A1` |
| P8 | `_PREVIEW_STORE` never evicts | 0 → 50 entries after 50 previews; no TTL, cap, or eviction |
| P9 | Formulas are not evaluated | `HYPERLINK("http://evil.com",…)` → cached value `"45000"` returned, flag set |
| P10 | Error export is readable across tenants | tenant-B `GET` on tenant-A's `batch_id` → **HTTP 200** + tenant-A's row errors |
| P11 | Unknown `batch_id` → 200, not the declared 404 | body = BOM + header row only |
| P12 | Commit `intake_ids` do not resolve | `GET /intakes/{id}` → **404** |
| P13 | Cross-tenant leak reproduces end-to-end over HTTP | tenant-B `POST /commit` returned `batch_id="tenant-a-real-batch"` |
| P14 | DTD entity expansion is not blocked | 606-byte upload → 100 000-char cell, ×165, no exception |
| P15 | Wrong worksheet imported | `workbook.xml` first sheet = `sheet3.xml`; parser read `sheet1.xml` |
| P16 | Zip guard passes a ×1017 amplification | 60.4 KB upload declares 60.0 MB, under the 100 MB cap, fully materialised |

---

## 5. Findings for the Parent Reviewer

Severity: **B** = should block approval, **N** = non-blocking, record and schedule,
**Q** = scope question needing an owner decision, not a defect.

### B1 (B) — `commit_xlsx_import()` persists nothing; AC-3 is satisfied vacuously

`xlsx_import.py` L636–642 is the entire "commit writing" step:

```python
    # Perform commit writing
    committed_intake_ids = []
    ts = datetime.now(UTC).isoformat()

    for item in valid_to_commit:
        intake_id = str(uuid.uuid4())
        committed_intake_ids.append(intake_id)
```

The loop variable `item` is never read — `ruff` flags it as `B007` (N10), which
is the linter independently observing that the row data goes nowhere. There is no
repository parameter, no session, and no `save`/`insert`/`persist` call anywhere in
the function (P4). The only durable side effect is the optional audit event at
L657–669.

End-to-end consequence (P12): the commit endpoint returns `202` with
`intake_ids`, and every one of them 404s.

```bash
POST /intake-batches/xlsx/commit  ->  202
     {"accepted_count": 1, "intake_ids": ["999ee31a-7229-40e2-aa65-9c04927c69b0"], ...}
GET  /intakes/999ee31a-7229-40e2-aa65-9c04927c69b0  ->  404
```

So AC-3 *"commit writes validated rows only"* is true only in the sense that the
empty set contains no invalid rows. The sibling CSV batch path in the same file
does persist; this one does not. The completion evidence states commit
*"Idempotently commits validated rows into intake state"* — that is not accurate
at this head, and it is the single largest gap between the evidence document and
the code.

**Suggested remedy (parent owner's call):** wire the commit to the same intake
persistence the CSV batch route uses, and add a test that reads a committed row
back through `GET /intakes/{id}` rather than asserting only on counts.

### B2 (B) — Commit re-validation is strictly weaker than preview; domain validation *is* bypassed

Preview runs the full `map_and_validate_rows()` (5 rules). Commit does **not** call
it. `commit_xlsx_import` L620–634 re-checks only two things:

```python
    for r in rows:
        addr = str(r.get("address_raw") or "").strip()
        if not addr:
            rejected_count += 1
            continue
        rent = r.get("rent_amount")
        if rent is not None:
            try:
                if float(rent) < 0:
                    rejected_count += 1
                    continue
            except (ValueError, TypeError):
                rejected_count += 1
                continue
        valid_to_commit.append(r)
```

Not re-checked: `area_ping` range and numeric type, `original_url` validity, and —
most importantly — `resolve_source_policy()` **quarantine**. And `rows` on the
commit endpoint is arbitrary client JSON (`XlsxCommitRequest.rows: list[dict[str, Any]]`);
it is not read back from the preview batch, so nothing forces a commit payload to
have ever passed preview.

Reproduce (P3 / P13) — the same row, judged twice:

```python
row = {"address_raw": "台北市信義區1號", "rent_amount": 30000,
       "area_ping": -999, "original_url": "not-a-valid-url-at-all"}

map_and_validate_rows([dict(row, _row_index=2)])
# -> valid=0, errors=['INVALID_RANGE', 'INVALID_URL']

commit_xlsx_import(rows=[row])
# -> accepted_count=1, rejected_count=0
```

Over HTTP the same payload returns `202 {"accepted_count": 1, "rejected_count": 0}`.

This directly contradicts the task summary's 「不得繞過 domain validation」 and the
module's own rule 6. A quarantined source URL — the mechanism that exists to keep
disallowed sources out of intake — is not consulted on the write path at all.

**Suggested remedy:** have commit call `map_and_validate_rows()` and commit only
its `valid_rows`, or require a `batch_id` that resolves to a stored preview and
commit the server-side validated rows rather than client-supplied ones. The
second is stronger and also fixes the "client can post anything" shape.

### B3 (B) — Module-global idempotency cache is not tenant-scoped: cross-tenant receipt leak

`_IDEMPOTENCY_STORE` (L546) is keyed on the **raw** `idempotency_key` string with
no tenant, actor, or payload component:

```python
    if idempotency_key and idempotency_key in _IDEMPOTENCY_STORE:
        cached = _IDEMPOTENCY_STORE[idempotency_key]
        return XlsxCommitReceipt(batch_id=cached.batch_id, ..., intake_ids=cached.intake_ids, replayed=True)
```

The route layer's shared `replay()` helper *is* scoped correctly —
`listings.py` L1203–1205 builds `f"{tenant_id}:{actor_id}:{operation_id}:{scope}:{key}"`.
That is precisely why the leak is reachable: because `replay()` treats tenant B's
request as new, it calls `make()`, which calls `commit_xlsx_import()`, which hits
the **unscoped** module cache and returns tenant A's receipt.

Reproduce (P13), two tenants, two actors, same `Idempotency-Key` value:

```bash
POST /intake-batches/xlsx/commit   # tenant A, Idempotency-Key: PROBE-B5-SHARED-KEY-01234567
     body: {"batch_id": "tenant-a-real-batch", "rows":[{"address_raw":"TENANT-A-CONFIDENTIAL-ADDRESS"}], ...}
  -> 202  batch_id="tenant-a-real-batch"  intake_ids=["565ce5ce-00de-41ac-8f85-0a2a79a0f6a2"]

POST /intake-batches/xlsx/commit   # tenant B, SAME Idempotency-Key
     body: {"batch_id": "tenant-b-own-batch",  "rows":[{"address_raw":"TENANT-B-ADDRESS"}], ...}
  -> 202  batch_id="tenant-a-real-batch"  intake_ids=["565ce5ce-00de-41ac-8f85-0a2a79a0f6a2"]
          Idempotency-Replayed: false
```

Tenant B receives tenant A's `batch_id` and `intake_ids`, and the
`Idempotency-Replayed: false` header asserts this is a *fresh* commit — so nothing
signals to the caller that the response belongs to someone else. Tenant B's own
rows are silently dropped. `Idempotency-Key` is client-chosen and only constrained
to `^[A-Za-z0-9._:-]+$`, 16–128 chars, so collision does not require guessing
anything secret; a shared client library with a fixed key prefix would collide by
construction.

**Suggested remedy:** delete `_IDEMPOTENCY_STORE` entirely and rely on the route's
`replay()`, which already provides tenant+actor+operation-scoped idempotency with
a payload fingerprint. If the module must stay independently idempotent, key it on
`(tenant_id, actor_id, idempotency_key)` and compare a payload digest, returning
`409` on key reuse with a different body.

### B4 (B) — Error export has no ownership check: any tenant can read any batch's row errors

`listings.py` L1863–1864, in `export_xlsx_batch_errors` (L1840):

```python
            preview_res = _PREVIEW_STORE.get(batch_id)
            row_errors = preview_res.row_errors if preview_res else []
```

`authorize_intake_action` is called just above — but with
`resource={"scope": {"tenant_id": tenant_id}}`, i.e. it authorises the caller
against the caller's *own* tenant. The `batch_id` is then looked up in a
process-global dict with no check that the batch belongs to that tenant. The
route also reaches directly into the application module's `_`-prefixed private
globals, which is how the boundary got lost.

Reproduce (P10):

```bash
# tenant A previews a file containing a row error
POST /intake-batches/xlsx/preview   (x-tenant-id: …0001)
  -> batch_id = "xlsx-batch-3c79c980-4f4d-4c4a-a76e-7c5baeed4d34"

# tenant B, different subject id, requests that batch's errors
GET /intake-batches/xlsx/errors/xlsx-batch-3c79c980-…/export?format=json   (x-tenant-id: …0002)
  -> 200
     [{"row_index": 2, "field": "address_raw", "code": "REQUIRED_FIELD_MISSING",
       "message": "address_raw is required and cannot be empty", "value": null}]
```

The exported payload carries the offending cell values from the other tenant's
upload. PII masking is applied, but masking is not an authorisation control: it
covers phones, emails and secret-keyword patterns only — addresses, URLs, rents and
error context pass through in the clear. Batch ids are UUID4 and not enumerable,
but they are returned to clients, appear in URLs, and are likely to reach logs and
support tickets, so this is unguessable-by-default rather than access-controlled.

**Suggested remedy:** store `tenant_id` (and ideally `actor_id`) alongside each
preview result and return `404` when the requested batch does not belong to the
caller. Expose a narrow accessor from `xlsx_import.py` instead of importing
`_PREVIEW_STORE` into the route.

### B5 (B) — Sparse cells shift values into the wrong columns: silent data corruption

`_parse_sheet_rows` L262–265 walks `<c>` elements **positionally** and never reads
the `r` attribute that carries each cell's true column reference:

```python
            for cell_elem in row_elem.findall("{*}c"):
                val, has_formula = self._parse_cell_value(cell_elem, shared_strings)
                row_cells.append((val, has_formula))
```

Column assignment is then `header_row[col_idx]` by list position (L277). Excel and
every other OpenXML writer **omit `<c>` elements for empty cells**, so any
spreadsheet with a blank cell mid-row silently shifts every subsequent value one
column left.

Reproduce (P1) — headers `地址 | 租金 | 坪數`, data row with a blank rent, so `B2` is
omitted and only `A2` and `C2` are present:

```xml
<row r="2"><c r="A2" t="inlineStr"><is><t>台北市信義區1號</t></is></c>
           <c r="C2"><v>25</v></c></row>
```

```
parsed: {'_row_index': 2, '地址': '台北市信義區1號', '租金': 25}
mapped: {'address_raw': '台北市信義區1號', 'rent_amount': 25.0, ...}
```

The 坪數 (area) value **25** was imported as **rent_amount = 25.0**, and no warning
or error is produced. The row validates cleanly and would be committed. Because a
blank cell anywhere in a real-world spreadsheet triggers this, and because rent
and area are both plain positive numbers that pass every range check, the
corruption is undetectable downstream.

The parent's own test fixture `_create_mock_xlsx` always emits a `<c>` for every
column of every row, which is why the 8 passing tests never exercise this path.

**Suggested remedy:** parse the `r` attribute (e.g. `A2` → column index 0) and
place values by decoded column, defaulting absent cells to empty. One regression
test built from a fixture with an omitted middle cell would pin it.

### B6 (B) — Contract gate regresses green → red; the parent's PR cannot merge as-is

The three new operations (`previewXlsxBatch`, `commitXlsxBatch`,
`exportXlsxBatchErrors`) were never exported into
`packages/openapi-client/openapi.json` — `grep` for either the operation ids or
`xlsx/preview` across `*.json`/`*.yaml`/`*.ts` returns nothing.

```
$ uv run python scripts/openapi/check_drift.py; echo "exit=$?"
[1/3] OpenAPI artifact freshness
ERROR: packages/openapi-client/openapi.json is stale — the API changed but the artifact was not regenerated.
Run: python3 scripts/openapi/export_openapi.py
[2/3] Generated client freshness
OK: packages/openapi-client/src/generated/types.ts matches the artifact.
[3/3] Breaking-change diff against origin/dev
      OK: 0 additive, 0 approved breaking, 0 unapproved breaking.

API contract gate: FAIL
exit=1
```

This is enforced twice, and both are blocking:

| Enforcement point | @ `8585f2b2` (merge-base) | @ `f0309f29` (parent head) |
|---|---|---|
| `tests/contract/test_openapi_artifact_and_client.py::test_artifact_is_checked_in_and_matches_the_live_app` | **17 passed** | **FAILED** |
| `.github/workflows/ci.yml` L114–118 `Check API contract drift` → `make api-contract` | pass | **exit 1** |

The `Verified:` trailer on `f0309f29` reads
`python3 -m pytest tests/unit/listing/test_xlsx_import.py tests/contract/test_xlsx_import_api.py`
— only the two new files. Those 8 tests do pass (reproduced). The regression is in
a *pre-existing* repo test that the change breaks, which a task-scoped test
selection cannot surface.

**Suggested remedy:** run `python3 scripts/openapi/export_openapi.py`, regenerate
the client, and commit both artifacts; then re-run `make api-contract` and at
minimum `pytest tests/contract` before opening the PR. Cheap to fix, but it is a
hard merge blocker today.

### N1 (N) — The documented XXE protection is not implemented

`SafeXlsxParser`'s docstring L171 claims protection against *"XML Entity Expansion
(XXE)"*. The implementation uses `xml.etree.ElementTree.fromstring` directly, with
no `defusedxml`, no DTD rejection, and no entity limits. Python's stdlib
ElementTree expands internal DTD entities and is documented as vulnerable to
"billion laughs" / quadratic blowup.

Reproduce (P14) — five nested entities inside `xl/worksheets/sheet1.xml`:

```
upload size: 606 bytes
expanded cell length: 100000 chars   (amplification x165)
parse completed without raising
```

Each additional nesting level multiplies by 10, so the ceiling is memory, not the
file-size cap — the 20 MB `MAX_XLSX_BYTES` and 100 MB uncompressed guards are both
computed *before* expansion and neither constrains it. Filed **N** rather than
**B** because reaching it requires an authenticated actor holding `submit_csv`,
and because the practical impact (memory pressure) overlaps N2; the docstring
claim being false is what makes it worth recording — a future reader will trust it.

**Suggested remedy:** parse with `defusedxml.ElementTree` (`forbid_dtd=True`), or
reject any part whose prolog contains a `<!DOCTYPE` declaration before handing it
to ElementTree. Then either implement the claim or delete it from the docstring.

### N2 (N) — Zip-bomb guard measures declared metadata and still permits ×1000 amplification

`_check_zip_limits` L206 sums `info.file_size` from the ZIP **central directory** —
a field written by whoever built the archive, not a measurement of what
decompression will produce. Two separate weaknesses:

1. The value is attacker-controlled. A crafted central directory that understates
   `file_size` passes the check, and `zf.read()` at L235/L253 still materialises
   the real payload with no cap.
2. Even when the declared value is honest, the guard permits the full 100 MB. The
   parser reads whole parts with `zf.read()`, so a single small upload can force a
   ~100 MB allocation, and there is no per-process or concurrency budget.

Reproduce (P16), fully honest archive:

```
upload: 60.4 KB compressed | declared uncompressed: 60.0 MB (passes the 100 MB cap)
amplification ratio: x1017
parse completed
```

Combined with the API layer accepting the file as an unbounded base64 string in
the JSON body (decoded in full before any size check), a handful of concurrent
requests is enough to pressure a container.

**Suggested remedy:** stream each part through `zf.open()` with a running
decompressed-byte counter and abort past the limit, rather than trusting
`file_size`; lower the per-part cap to what a legitimate sheet needs; and bound
the request body at the API layer.

### N3 (N) — Reported `row_index` is a sequence position, not the spreadsheet row

`_parse_sheet_rows` L274 derives the index from enumeration order:

```python
        for row_idx, raw_row in enumerate(raw_grid[1:], start=2):
```

XLSX rows carry an explicit `r` attribute and may be non-contiguous (deleted or
skipped rows). Reproduce (P2): a sheet whose only data row is `r="7"` reports
`row_index = 2`. Every error in the downloadable report then points the operator
at the wrong line of their own file — which is the primary purpose of AC-5's
export. Same root cause as B5 (positional parsing), and a fix for B5 should carry
this with it.

### N4 (N) — The imported worksheet is chosen by filename sort, not workbook sheet order

`_find_first_sheet` L244–250 sorts `xl/worksheets/sheet*.xml` lexicographically and
takes `[0]`. OpenXML sheet *order* lives in `xl/workbook.xml`'s `<sheets>` element
and its `r:id` relationships; part filenames carry no ordering guarantee and do not
change when sheets are reordered, renamed, or deleted.

Reproduce (P15) — a workbook whose first sheet is `Listings` → `worksheets/sheet3.xml`,
with a leftover `Scratch` → `worksheets/sheet1.xml`:

```
workbook.xml first <sheet> -> worksheets/sheet3.xml ('Listings')
parser actually read:        [{'_row_index': 2, '地址': 'SCRATCH-SHEET-ROW'}]
```

The wrong sheet is imported silently. Lexicographic sort also puts `sheet10.xml`
before `sheet2.xml`. **Suggested remedy:** resolve the first `<sheet>` in
`workbook.xml` through `xl/_rels/workbook.xml.rels`.

### N5 (N) — Formula-prefix "sanitisation" converts negative numbers into positive ones

`FORMULA_PREFIXES = ("=", "@", "+", "-")` (L69) includes `-`, and both formula
branches strip with `lstrip("=@+-")` (L286, L291). A rent stored as *text*
`-50000` — routine when a sheet has text-formatted columns — is treated as an
injection prefix and rewritten.

Reproduce (P6):

```
raw cell text:        '-50000'
parsed as:            50000
validation errors:    [('rent_amount', 'FORMULA_WARNING')]
valid row rent_amount: 50000.0     <- row is VALID
```

The `INVALID_RANGE` rule that exists to reject negative rent never fires, because
the negative sign was removed before validation. A `FORMULA_WARNING` is recorded,
but it is an advisory entry and does not set `has_error`, so the row commits. Note
also that `lstrip` strips *all* leading occurrences, so `--=+x` → `x`.

**Suggested remedy:** neutralise injection by prefixing on *output* (see N6)
rather than mutating the imported value, and drop `-`/`+` from the input-side
prefix set — or at minimum parse numerics before sanitising.

### N6 (N) — The masked CSV export re-introduces the formula injection the parser guards against

`export_xlsx_import_errors` applies `mask_sensitive_value()` to every field, then
writes values straight into the CSV (L700–707). Masking targets phones, emails and
secret keywords; it does nothing about leading formula characters.

Reproduce (P7):

```
error value: =cmd|'/c calc'!A1
CSV data line: 2,address_raw,REQUIRED_FIELD_MISSING,address_raw is required,=cmd|'/c calc'!A1
```

The export is served with `Content-Disposition: attachment`, so the intended
consumer opens it in a spreadsheet — where a leading `=` is a live formula. The
module spends considerable effort refusing to execute formulas on the way *in* and
then emits one on the way *out*. Values whose leading prefix was stripped on input
(N5) are still reachable here via `field`/`message` and via any error value not
routed through the formula branch.

**Suggested remedy:** prefix any exported cell beginning with `= @ + -` (or a
tab/CR followed by one) with a single quote, in all three export formats.

### N7 (N) — `_PREVIEW_STORE` grows without bound

L545 declares a module-global dict; `preview_xlsx_import` L581 inserts into it and
nothing ever removes an entry — no TTL, no size cap, no eviction. Each entry
retains the preview's full `valid_rows`, i.e. the entire parsed content of the
upload (up to a 20 MB source file).

Reproduce (P8): `_PREVIEW_STORE` goes 0 → 50 entries after 50 previews and stays
there. An authenticated actor can grow process memory monotonically with repeated
previews. This is also why AC-2 ("preview performs no writes") deserves the
qualification in §3: no *persistent* write occurs, but preview is not free of
state either.

### N8 (N) — Unknown `batch_id` returns an empty 200; the declared 404 is unreachable

The route declares `responses=api_error_responses(400, 404)` but never raises 404:
`_PREVIEW_STORE.get(batch_id)` returning `None` falls through to `row_errors = []`
and a well-formed empty report.

Reproduce (P11):

```
GET /intake-batches/xlsx/errors/xlsx-batch-does-not-exist/export?format=csv
-> 200, body: '﻿Row Index,Field,Error Code,Error Message,Masked Value\r\n'
```

Two consequences. The published contract advertises a 404 that cannot occur.
More operationally: because `_PREVIEW_STORE` is per-process and in-memory, a
restart, a deploy, or simply a second replica handling the GET makes **every**
export return this empty report — and an operator reading it concludes their
import had no errors. A silent empty result is the worst available failure mode
for this endpoint.

**Suggested remedy:** return 404 when the batch is unknown (which also resolves
the contract mismatch), and move preview results to a shared store with an
explicit TTL — that fixes N7 and B4's ownership binding at the same time.

### N9 (N) — External-link detection fails open

`_check_external_links` L229–230 wraps each `.rels` parse in a bare
`except Exception: pass`. A malformed or hostile relationships part therefore
skips detection *silently* — the one input most likely to be deliberately
malformed is the one that disables the check. The surrounding `parse()` already
converts `ET.ParseError` into a clean `MALFORMED_XLSX_FILE` failure, so failing
closed here costs nothing.

### N10 (N) — `ruff` reports 9 errors across the delivered surface

```
apps/api/app/routes/listings.py:1775:17          B904  raise ... from err
apps/api/app/routes/listings.py:1781:17          B904  raise ... from err
modules/external_data/application/xlsx_import.py:15:1    I001  unsorted imports
modules/external_data/application/xlsx_import.py:23:36   F401  `dataclasses.field` unused
modules/external_data/application/xlsx_import.py:640:9   B007  loop variable `item` unused
tests/contract/test_xlsx_import_api.py:3:1       I001  unsorted imports
tests/contract/test_xlsx_import_api.py:4:8       F401  `pytest` unused
tests/unit/listing/test_xlsx_import.py:3:1       I001  unsorted imports
tests/unit/listing/test_xlsx_import.py:4:8       F401  `json` unused
```

Six are auto-fixable. `B007` at L640 is the one worth reading as more than style —
it is the linter pointing at B1. Separately, `export_xlsx_import_errors` L783
contains `ET.canonicalize(s) if False else s.replace(...)` — a dead conditional
that reads as an abandoned experiment and should be deleted.

### N11 (N) — Two acceptance tests do not test the property they are named for

- `test_preview_performs_no_writes` (unit L143) makes no assertion about writes at
  all. Its six assertions cover `batch_id` prefix, row counts, and parsed values —
  every one of which would still pass if preview wrote to the database.
- `test_commit_writes_validated_rows_only` (unit L162) asserts
  `accepted_count == 1`, `rejected_count == 2`, `len(intake_ids) == 1`, and the
  audit record. Given B1, these counts are satisfied by a function that writes
  nothing; the test cannot distinguish "wrote one row" from "wrote nothing and
  returned 1".

Both are cited as the evidence for AC-2 and AC-3 in the completion summary's
verification matrix. The tests are not wrong about what they assert — they are
just not evidence for the criteria they are mapped to. **Suggested remedy:** assert
against a spy/instrumented repository (no calls for preview; exactly the validated
rows for commit), and read at least one committed row back through the API.

### Positives worth recording

- The zero-dependency stdlib parser is a defensible architectural call: it keeps
  `openpyxl` out of the request path and off the dependency surface.
- **Formulas are genuinely never evaluated.** P9 confirms the cached `<v>` is read
  and the `<f>` text only inspected. The core security promise of AC-1 holds.
- The routes reuse the existing `authorize_intake_action` and `replay()` helpers
  rather than inventing parallel auth/idempotency — and `replay()`'s composite key
  is correctly tenant+actor+operation scoped. The failures in B3 come from the
  *module* globals, not from the route wiring.
- `map_and_validate_rows` reuses `normalize_address`, `normalize_floor`,
  `validate_url`, and `resolve_source_policy` from `assisted_intake` instead of
  reimplementing domain rules. The preview path's validation is genuinely good;
  B2 is that commit does not use it.
- The masking helper does what it claims for its three target patterns, including
  the CJK-context cases in the tests, and it is applied to `field`, `message`, and
  `value` alike.
- The commit emits a structured audit event carrying `correlation_id` and scope.
- The diff is 100% deliverable code — 1413 insertions, 0 deletions, no generated
  state mirrors swept in.
- The 8 tests claimed by the `Verified:` trailer reproduce exactly, and the
  completion summary's per-criterion test mapping is accurate about *which* test
  is intended to cover *which* criterion (N11 is about what those tests assert,
  not about the mapping being wrong).

---

## 6. Scope Questions for the Parent Owner

These need an owner decision; they are not defects this packet can call.

**Q1 — Does "fail safely" (AC-1) mean warn-and-continue, or reject?**
Formulas and external links currently set `has_formula_or_external_link = True`,
append a warning, and let the row proceed to commit. Nothing rejects such a file.
That is a coherent reading of "fail safely" (nothing executes), but a governed
import might be expected to refuse a workbook carrying external links outright, or
require an explicit override. The API surfaces
`has_formula_or_external_link_warnings` to the client but takes no action on it.

**Q2 — `scope` is threaded everywhere and enforced nowhere.**
`preview_xlsx_import(..., scope=...)` accepts the parameter and never reads it.
`commit_xlsx_import(..., scope=...)` only echoes it into audit metadata. The
module docstring's rule 6 lists "scope" among the strictly-enforced domain
validations. Either scope should constrain something (tenant binding on stored
batches would also resolve B4), or it should be documented as audit-only.

**Q3 — Reusing the `submit_csv` permission for XLSX.**
Both write routes authorise `"submit_csv"`; export authorises `"view"`.
`intake_authorization.py` L82 maps `submit_csv` to `Action.CREATE` and L200 groups
it with `submit_url`, so file-based intake is the established category and the
reuse is reasonable. Flagging only so the owner can confirm no separate XLSX
permission is expected by the governance model.

**Q4 — Lane state: the parent is in `review` with nothing to review through ReviewBus.**
`task/ODP-CAP-XLSX-IMPORT-001` is local-only, has never been pushed to `origin`,
and has no PR. Reviewer `Antigravity2` therefore has no PR to act on. Separately,
`ai-status.json` records `base_sha: 956170de` while the branch's actual merge-base
with `dev` is `8585f2b2` — `956170de` is an ancestor of both, so it is stale rather
than wrong, but a reviewer diffing against the recorded `base_sha` will see extra
`dev` commits mixed into the range. Use `8585f2b2` (or
`git merge-base origin/dev f0309f29`). Both are lane observations for the parent
owner, outside this sidecar's authority to fix.

---

## 7. Recommendation

| Finding | Severity | One-line disposition |
|---|---|---|
| **B6** | B | Regenerate the OpenAPI artifact — one command, unblocks CI. Do this first. |
| **B1** | B | Commit persists nothing; AC-3 is not delivered. |
| **B2** | B | Commit bypasses domain validation, contradicting the task's own hard constraint. |
| **B3** | B | Cross-tenant receipt leak via the unscoped module idempotency cache. |
| **B4** | B | Cross-tenant read of row errors; no ownership binding on `batch_id`. |
| **B5** | B | Sparse cells corrupt column alignment on ordinary real-world spreadsheets. |
| N1–N11 | N | Record and schedule; N5/N6 and N3/N4 pair naturally with the B5 fix. |
| Q1–Q4 | Q | Owner decision. |

**This packet's recommendation to the parent reviewer (`Antigravity2`): do not
approve `ODP-CAP-XLSX-IMPORT-001` at `f0309f29`.** B6 makes it unmergeable
regardless. B1 and B2 mean the headline capability — a governed commit that writes
validated rows — is not delivered as described. B3 and B4 are cross-tenant leaks
reproducible over HTTP. B5 corrupts data silently on inputs the importer exists to
accept.

The foundation is sound and worth keeping: the parsing, masking, validation, and
route-wiring layers are competently built, and the preview path is close to
correct. The gap is concentrated in the commit path and in two process-global dicts
that should not be holding multi-tenant state.

---

## 8. Handoff Note

This sidecar review packet is **round 1** and ready for review.

- **Owner**: `Claude2`
- **Assigned Reviewer**: `Antigravity` (who is also the parent task's owner)
- **Sidecar scope compliance**: this branch adds exactly one file,
  `support/sidecars/ODP-CAP-XLSX-IMPORT-001/ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW.md`.
  No canonical truth, contract, runtime, registry, or governance file is touched.
  `git diff --stat origin/dev...HEAD` shows that single path.
- **Reviewer diff shortcut**: `git diff origin/dev...HEAD` — one added file.
- **Reviewing the evidence rather than the prose**: every §5 finding is
  reproducible from the code blocks in that section against a detached worktree at
  `f0309f29` (§4 step 0). §4 also gives the base-pin comparison that proves B6 is a
  regression and not pre-existing. The probe scripts were deleted after running;
  nothing in this branch executes.
- **Cheapest path for the parent owner**: B6 is a single command
  (`python3 scripts/openapi/export_openapi.py` + regenerate the client) and clears
  the merge blocker. B3 is a deletion (drop `_IDEMPOTENCY_STORE`, keep the route's
  `replay()`). B2 is a substitution (call `map_and_validate_rows` in commit). B1,
  B4 and B5 need real work.
- **Next Action**: hand off `ODP-CAP-XLSX-IMPORT-001-SIDECAR-REVIEW` to reviewer
  `Antigravity`. On approval, the parent owner decides whether to absorb these
  findings into `ODP-CAP-XLSX-IMPORT-001` or route them to follow-up tasks.
