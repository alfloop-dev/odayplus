# Production Model Registry and Artifact Evidence (ODP-PV-013)

Phase: PV Product-Grade E2E Validation · Owner: Claude2 · Reviewer: Codex

## Goal

Connect the Learning Hub model lifecycle to a **durable artifact + model
registry** with auditable evidence, instead of demo (in-memory, session-only)
registry state. After this task a registered model version points at a real,
content-hashed artifact, and the full registry state — versions, validation
metrics, aliases, shadow/canary, promotion, rollback, and release decisions —
survives a process restart and is reproducible into an audit manifest.

## Approach

This builds directly on the durable persistence seam from **ODP-PV-009**
(`shared/infrastructure/persistence`, SQLite + WAL, restart-survivable, no
runtime DB server). Two layers were added:

1. **Content-addressed artifact store** (`models/shared_ml/artifact_store.py`).
   `put_artifact` hashes the bytes (SHA-256) and returns an `ArtifactRecord`
   whose `uri` is derived from the digest (`odp-artifact://sha256/<hex>`). A
   `ModelVersion.artifact_uri` set to that value is bound to exact bytes, so it
   cannot silently drift from the artifact it claims to describe. `verify`
   re-hashes the stored bytes to prove the artifact was not tampered with.

2. **Durable Learning Hub registry repository + artifact store**
   (`shared/infrastructure/persistence/repositories.py`). `DurableLearningHubRepository`
   and `DurableArtifactStore` mirror the exact public surface of their
   in-memory counterparts over `SqliteDocumentStore`, so `LearningHubService`
   and the MLflow adapter run on durable storage with **no application
   changes**. A new `LearningHubRepository` protocol formalizes the seam the
   service depends on.

Registry state is reduced into an auditable, JSON-serializable manifest by
`build_model_registry_evidence(...)`: per version it records stage, aliases,
metrics, the linked validation status, model-card completeness/approval/rollback
link, and the content digests of every bound artifact.

## What landed

| Layer | File |
| --- | --- |
| Content-addressed artifact store + registry evidence builder | `models/shared_ml/artifact_store.py` |
| Public exports | `models/shared_ml/__init__.py` |
| `LearningHubRepository` protocol (typed durable seam) | `modules/learninghub/infrastructure/repositories.py` |
| Service/adapter accept the protocol (durable drop-in) | `modules/learninghub/application/release.py`, `modules/learninghub/infrastructure/mlflow_adapter.py` |
| Durable Learning Hub repository + durable artifact store | `shared/infrastructure/persistence/repositories.py` |
| Persistence package exports | `shared/infrastructure/persistence/__init__.py` |
| Integration tests | `tests/integration/test_model_registry_artifacts.py` |

The durable repositories reuse the `durable_documents` schema owned by
`infra/db/migrations/000002_durable_e2e_persistence.sql` (ODP-PV-009); no new
DDL was required — model versions, cards, validation runs, alias pointers,
release decisions, artifact records, and artifact blobs are all stored as
collections in that generic aggregate table.

## Design notes

- **Aliases without delete.** The document store has no delete primitive, so an
  alias is a small overwritable pointer document; clearing an alias writes a
  `None` pointer. This reproduces the in-memory mapping semantics (the
  `ModelVersion.aliases` frozenset is kept in sync on every set/clear) and
  survives restart.
- **Content addressing / dedup.** Artifact blobs are keyed by digest, so
  re-registering identical bytes is idempotent and two distinct payloads can
  never collide on a URI.
- **Layering.** `models/shared_ml` never imports upward into
  `modules.learninghub`; the evidence builder reads registries through a
  locally-declared minimal protocol.

## Acceptance evidence

All three acceptance criteria are covered by
`tests/integration/test_model_registry_artifacts.py`:

1. **Model versions, validation metrics, aliases, shadow/canary, promotion,
   rollback persist** — `test_full_lifecycle_promote_rollback_survives_restart`
   drives shadow → full promote v1 → full promote v2 (retiring v1) →
   rollback to v1, with each phase re-read after a simulated restart (engine
   closed, durable repos rebuilt on the same file). Production/champion/
   previous_production/shadow aliases, stage transitions, version metrics, and
   release decisions all survive.
2. **Model cards include data snapshot, feature set, policy/version, owner,
   approval, rollback link** — `test_model_card_carries_required_links` asserts
   `is_complete`, `is_approved`, owner, dataset snapshot id, feature/label set
   ids, validation-run link, rollback conditions, and reviewer role.
3. **Product E2E can promote/rollback a deterministic model with audit
   traceability** — the lifecycle test runs the rollback through the
   `run_learninghub_release` worker entrypoint and, after a second restart,
   confirms the rollback decision, the persisted `DurableAuditLog` trail
   (release + rollback events, correlation-indexed), and an `audit_event_id` on
   every decision. `test_artifact_content_addressing_and_tamper_evidence` and
   `test_registry_evidence_manifest_is_audit_complete` prove tamper-evident
   digests and an audit-complete evidence manifest.

## Verification

```bash
uv run pytest tests/integration/test_model_registry_artifacts.py -p no:warnings -q   # 4 passed
uv run pytest tests/integration tests/contract -p no:warnings -q                     # 192 passed
uv run ruff check models/shared_ml shared/infrastructure/persistence \
  modules/learninghub tests/integration/test_model_registry_artifacts.py             # clean
```

(Pre-existing, unrelated failures in `.orchestrator/` and `scripts/` tests stem
from git-remote operations unavailable in the sandbox and are independent of
this task.)

## Notes / follow-ups

- The durable backend is the E2E/local durability path; production durability
  against the canonical Postgres schema is a separate, mechanical wiring step
  (the repository protocol + factory seam make that swap drop-in). Wiring the
  Learning Hub durable repositories into `build_persistence()` /
  `PersistenceBundle` for the running API is the natural next step when an HTTP
  registry surface is exposed.
- Artifact blobs are stored in SQLite for the E2E lane; a blob/object-store
  backend (GCS) can be substituted behind the same `ArtifactStore` protocol
  without touching the registry or evidence code.
