# ODP-PGAP-AUDIT-001 Implementation Evidence

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude
Branch: task/ODP-PGAP-AUDIT-001

## Delivered Surface

- `shared/audit/` now stamps audit events and retained evidence records with
  deterministic SHA-256 hash-chain metadata: sequence, previous hash, record
  hash, key id, signature version, signature algorithm, and WORM sink id.
- `shared/audit/worm.py` provides the runtime WORM write path. Product runtime
  can set `ODP_AUDIT_WORM_SINK_URI=gs://...` to write stamped audit events and
  evidence bundles through the Cloud Storage JSON API with `ifGenerationMatch=0`;
  local/CI runs use an append-only file sink so the same path is exercised.
- `shared/infrastructure/persistence/audit_log.py` and
  `modules/opsboard/audit/evidence_store.py` provide durable SQLite-backed
  append-only stores. Product mutation methods reject direct delete/update, and
  duplicate event ids replay the existing record instead of overwriting it.
- Retention purge now tombstones expired records instead of deleting rows, so a
  purge of the oldest retained record preserves successor `previous_hash` links.
  The tombstone drops the bundle payload but retains retention metadata,
  original record hash, purge actor/reason/correlation, and a tombstone hash.
- Legal-hold and retention governance state now has its own tamper-evident hash
  and append-only governance log entries. Directly clearing `legal_hold` or
  rewriting `governance_log_json` fails verification.
- `apps/api/app/routes/audit.py` exposes retained evidence listing, fetch,
  legal-hold, expired listing, and retention purge endpoints. Governance
  endpoints derive authority from authenticated platform roles and then enforce
  separation of duties with `GovernedEvidenceOperation`.
- Sensitive evidence export requires export scope, purpose scope, expiration,
  masking, independent authorization, and identity-bound download evidence.
- `infra/terraform/audit/` defines the deployable WORM-capable sink: a versioned
  retention-policy Cloud Storage bucket, an append-only writer service account,
  and a separated retention-manager service account. The runtime account can
  impersonate only the append-only writer, not object deletion/update authority.

## Acceptance Mapping

| Acceptance | Evidence |
|---|---|
| Append-only external or WORM-capable sink | Audit logs and evidence stores write stamped records to the configured WORM sink before local persistence; GCS uses create-only uploads and Terraform provides objectCreator-only writer identity. |
| Verifiable hash chain or signature metadata | Audit events and evidence bundles carry sequence, previous hash, hash, key id, version, algorithm, and WORM sink id. Bundle, event, governance, and tombstone tamper tests are covered. |
| Governed retention purge, legal hold, export authorization | API legal-hold/purge endpoints require platform role mapping plus `GovernedEvidenceOperation`; export validation rejects self-authorization; purge rejects the original exporter/authorizer actor. |
| Sensitive export purpose, masking, expiration, identity boundary | Export service masks sensitive decision/audit payloads, requires purpose scope and expiration, and emits `download_evidence_id` tied to the identity boundary. |
| Restore and replay preserve hashes/order/actor/correlation/retention | Durable audit/evidence stores expose replay methods; integration tests replay snapshots into a fresh DB and compare hashes and metadata. |
| Tests prove rejection/protection/restore | Security and integration tests cover tamper rejection, direct deletion denial, sensitive export denial, legal-hold protection, retention purge, and replay. |

## Task Commits

- `1c56c1ef` - immutable evidence core anchor.
- `f49b9b9a` - governance/replay/WORM sink anchor.
- Pending remediation commit - runtime WORM writer, purge tombstones, governance
  integrity, and reviewer-requested regression tests.
