# ODP-PGAP-AUDIT-001 Review Notes

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude
Reviewed: 2026-07-15
Disposition: **changes requested** (reopened to owner)

The delivered shape is close and the hash-chain core is well factored. The
reviewer-run verification matches the owner's claims: `397 passed` for
`uv run pytest tests/security tests/integration`, and ruff clean over the
declared scope. The findings below are all cases the green suite does not
cover; each was reproduced against the real store, not reasoned about.

## Blocking findings

### 1. No WORM sink exists at runtime (acceptance 1 not met)

`infra/terraform/audit/` provisions the bucket, but no code writes to it.
`grep -rniE "storage\.googleapis|google-cloud-storage|gcs" --include=*.py shared/ modules/ apps/`
returns nothing. `worm_sink_id` is the hardcoded literal
`DEFAULT_AUDIT_WORM_SINK_ID = "odp-local-worm-audit-sink"`; it is never read
from config and never resolves to the provisioned bucket. Evidence lands only
in product-owned SQLite.

Acceptance 1 requires a sink "that product credentials cannot mutate or
delete". The store classes reject `delete()`/overwrite at the writer contract,
but that is convention inside the process, not a credential boundary — the same
engine mutates rows freely (see finding 3, which does exactly that). The
Terraform module is a deployable definition, so mapping it to acceptance 1 in
`implementation.md` substitutes a static artifact for runtime evidence, which
the brief rules out.

Either ship the write path to the WORM sink and prove it at runtime, or anchor
the chain head somewhere the product cannot rewrite. Until then the chain is
tamper-*evident* only against an attacker who cannot recompute it — and since
the digest is unkeyed (finding 5), anyone with DB write can.

### 2. A legitimate retention purge permanently breaks the chain

Confirmed on **both** stores. `verify_retained_evidence_chain` walks
`previous_hash` links, so removing any record that is not the tail leaves the
successor pointing at a deleted hash. `list_all()` then raises forever, and
`DurableEvidenceBundleStore.verify_integrity()` raises instead of returning
`ok=False`, so the verification path itself dies.

This is the *normal* case, not a corner: retention classes differ (standard
365d vs regulatory 2555d), so an older standard record expires while a newer
regulatory record is still retained — the purge removes the head.

Reproduced against `_durable_bundle` (the production path):

```
chain ok before purge: True
purged: ['exp-standard']                     # authorized records_manager sweep
verify_integrity() RAISES: AuditIntegrityError - previous hash does not match
list_all()        RAISES: AuditIntegrityError - previous hash does not match
```

The tests miss this because `test_api_governance_blocks_spoofing_and_purges_only_non_held`
saves `program-held` first and purges `program-purge` second — i.e. only ever
the tail record. A purge of the *first* record is what production will do.

Purge and an unbroken hash chain are in direct tension; resolving it usually
means tombstoning (retain the hash link, drop the payload) rather than deleting
the row.

### 3. `InMemoryEvidenceBundleStore` reuses sequence numbers after purge

`save()` derives `sequence=len(self._records) + 1`, so after a purge shrinks the
dict the next save re-issues a live sequence:

```
sequences after new save: [('exp-regulatory', 2), ('exp-new', 2)]
```

`DurableEvidenceBundleStore.save()` gets this right (`MAX(sequence) + 1`). The
two stores are documented as interchangeable behind the Protocol, so the
in-memory one should match.

### 4. `legal_hold` is outside the hash chain, so holds are not tamper-evident

`retained_evidence_integrity_payload` deliberately excludes `legal_hold` and
`governance_log`. The docstring explains why (a hold must not invalidate the
bundle hash), but the consequence is that hold state has no integrity
protection at all. Clearing a hold by direct DB write is undetected, and the
record then purges — defeating acceptance 6's "legal-hold protection":

```
legal_hold applied: True   chain ok: True
--- after direct DB write clearing the hold ---
legal_hold now: False
chain STILL verifies ok: True        <-- tamper undetected
purged despite the hold: ['exp-1']
```

The bundle-tamper test passes because bundle content *is* hashed; the governance
sidecar is the unprotected surface. Chaining governance operations as their own
append-only hash-linked entries would cover both this and finding 2.

## Non-blocking

5. **The "signature" is an unkeyed digest.** `signature_key_id`
   (`odp-audit-hash-chain-key-v1`) implies a key, but `sha256_hex` is a plain
   digest — no HMAC, no key material. Acceptance 2 permits "hash chain *or*
   signature", so a chain is in scope; the key-id metadata is misleading and a
   plain chain only holds if its head is externally anchored (finding 1).

6. **Terraform retention policy is not locked.** `google_storage_bucket.worm`
   sets `retention_policy` without `is_locked = true`, so it can be lifted;
   `retention_manager` holds `roles/storage.objectAdmin` (delete). The writer SA
   is correctly `objectCreator`-only.

7. **Purge has no actor-level SoD.** `require_retention_purge_authority` checks
   role and reason but not actor, unlike `require_legal_hold_authority`. Since
   `COMPLIANCE_OFFICER` grants audit `EXPORT` + `UPDATE` + `DELETE`, one
   principal can export evidence, hold it, and purge it.

## Verified good

- Body-supplied `role` cannot escalate: `_assert_governance_role` intersects the
  requested governance role against the authenticated principal's platform
  roles, and the spoofing test covers it (403).
- Legal-hold SoD (exporter cannot hold their own export) is enforced and tested.
- Bundle and audit-event content tamper are both detected via the durable path.
- Replay preserves event hashes, order, actor, correlation id, and retention
  metadata into a fresh DB.
- Sensitive-export masking (`reviewer_email` -> `a****@example.com`), purpose
  scope, expiry, and identity-bound download evidence are present and tested.

## Reviewer commands

```bash
uv run pytest tests/security tests/integration -q            # 397 passed
python3 -m ruff check shared/audit shared/observability/audit.py \
  modules/opsboard/audit apps/api/app/routes/audit.py tests   # clean
```

Findings 2, 3, and 4 were reproduced with throwaway scripts against
`InMemoryEvidenceBundleStore` and `_durable_bundle`; the transcripts are quoted
inline above.
