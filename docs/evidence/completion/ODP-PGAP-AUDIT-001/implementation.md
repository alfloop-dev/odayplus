# ODP-PGAP-AUDIT-001 Implementation Evidence

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude
Branch: task/ODP-PGAP-AUDIT-001

## Delivered Surface

- `shared/audit/` now stamps audit events and retained evidence records with
  deterministic SHA-256 hash-chain metadata: sequence, previous hash, record
  hash, key id, signature version, signature algorithm, and WORM sink id.
- `shared/infrastructure/persistence/audit_log.py` and
  `modules/opsboard/audit/evidence_store.py` provide durable SQLite-backed
  append-only stores. Product mutation methods reject direct delete/update, and
  duplicate event ids replay the existing record instead of overwriting it.
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
| Append-only external or WORM-capable sink | Runtime stores reject product delete/update; Terraform module creates a retention-policy bucket with separated writer and retention identities. |
| Verifiable hash chain or signature metadata | Audit events and evidence bundles carry sequence, previous hash, hash, key id, version, algorithm, and WORM sink id. Tamper tests cover both event and bundle mutation. |
| Governed retention purge, legal hold, export authorization | API legal-hold/purge endpoints require platform role mapping plus `GovernedEvidenceOperation`; export validation rejects self-authorization. |
| Sensitive export purpose, masking, expiration, identity boundary | Export service masks sensitive decision/audit payloads, requires purpose scope and expiration, and emits `download_evidence_id` tied to the identity boundary. |
| Restore and replay preserve hashes/order/actor/correlation/retention | Durable audit/evidence stores expose replay methods; integration tests replay snapshots into a fresh DB and compare hashes and metadata. |
| Tests prove rejection/protection/restore | Security and integration tests cover tamper rejection, direct deletion denial, sensitive export denial, legal-hold protection, retention purge, and replay. |

## Task Commits

- `1c56c1ef` - immutable evidence core anchor.
- `f49b9b9a` - governance/replay/WORM sink anchor.
