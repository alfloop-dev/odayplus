# ODP-FLOW-001 — Closeout Record

Task: Complete Integration and External Data flow
Owner: Claude2 · Reviewer of record: Antigravity2
Status transition: `review_approved` → `done` (2026-07-12)

## Delivery

The reviewed closed-loop deliverable is durable in `dev`:

- Scheduled and manual external ingestion persist canonical outputs
  (`ExternalIngestionService` composes the existing `ExternalFetchScheduler`
  window/watermark/freshness logic with canonical mapping + persistence).
- DQ / quarantine / lineage / freshness are queryable via the persisted
  ingestion-run store.
- API and UI read persisted run state (router injection wired through
  `apps/api`).
- Idempotent retry rejection and audit paths pass end-to-end.

Primary implementation and verification evidence:

- `docs/evidence/completion/ODP-FLOW-001/implementation.md`
- `docs/evidence/completion/ODP-FLOW-001/verification.md`
- `docs/evidence/PRODUCT_FLOW_IMPLEMENTATION_MATRIX_2026-07-12.md`
  (Integration / External Data row marked done, FLOW-001 detail section)

## Merge trail

- PR #241 (`task/ODP-FLOW-001` → `dev`) merged 2026-07-12T15:01:23Z.
- The delivered code, the FLOW-001 evidence, and the import-cycle fix are
  all ancestors of `dev`.

## Import-cycle fix (regression guard)

Adding `ingestion_service`/`ingestion_store` to
`modules/external_data/application/__init__.py` created an eager import cycle
(external_data → shared persistence → heatzone.workers → external_data.geo).
The symbols are now re-exported lazily via a PEP 562 `__getattr__`; every real
caller imports from the submodules, so the public API is unchanged. Guard
check: `python3 -c "import modules.heatzone.workers"` must not error.

## Verification at closeout (dev tip)

- `import modules.heatzone.workers` — OK (previously order-dependent failure)
- `from modules.external_data.application import ExternalIngestionService` — OK
- `import apps.api.server` — OK
- `pytest tests/integration -k "external or ingestion or flow or cross_flow"
  -p no:warnings` — 68 passed
- `ruff check modules/external_data modules/integration` — clean
