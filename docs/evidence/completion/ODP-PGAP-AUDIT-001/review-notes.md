# ODP-PGAP-AUDIT-001 Review Notes

Task: ODP-PGAP-AUDIT-001
Owner: Codex
Reviewer: Claude

- Round 1 (2026-07-15): **changes requested** — findings below.
- Round 2 (2026-07-15): **approved** — see "Round 2" at the end of this file.

---

## Round 1

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

---

## Round 2

Reviewed: 2026-07-15 · commits `aa1acde8`, `8ee23fab`
Disposition: **approved**

Every round-1 blocking finding was re-tested against the real stores and the
live app rather than read off the diff, since a green suite is what masked
round 1. Reviewer-run: `uv run pytest tests/security tests/integration -q` ->
**401 passed**; ruff clean over the declared scope. Both match the owner's claims.

### Blocking findings — all resolved

**1. Runtime WORM sink (acceptance 1) — fixed, verified end-to-end.**
`build_audit_worm_sink_from_env` is now wired through `build_persistence` into
both `_memory_bundle` and `_durable_bundle`, so the write path is on by default
rather than opt-in. Verified by booting `create_app()` with
`ODP_PERSISTENCE=durable` and a configured `ODP_AUDIT_WORM_SINK_URI`, then
driving a real `POST /audit/evidence/export` (201):

```
WORM objects written by the live app:
  audit-events       7df857ea-...   checksum=f5860cc9d6a1
  retained-evidence  audit-export-066b1e96-...  checksum=e144c1f41f4a
worm_sink_id on persisted records = file:///tmp/.../worm-sink   # configured, not the literal
second identical write -> AuditWormSinkError: WORM object already exists
```

The round-1 complaint that `worm_sink_id` was a hardcoded literal that never
resolved to a provisioned bucket is gone: it now resolves from config on both
the event and the retained record. Local sink uses `O_EXCL`; the GCS writer uses
`ifGenerationMatch=0` against an `objectCreator`-only SA. The GCS path itself is
not exercised here (no credentials in the worker image) — the local sink drives
the same code path, and the IAM shape is asserted in Terraform.

**2. Non-tail retention purge — fixed.** Purge now tombstones instead of
deleting, so the successor's `previous_hash` link survives. Re-ran the exact
round-1 repro against `_durable_bundle` (standard record expires while a newer
regulatory record is retained, i.e. the purge removes the head):

```
chain ok before purge: True
purged: ['exp-standard']
verify_integrity() -> True
list_all() -> [('exp-standard', 1, purged=True), ('exp-regulatory', 2, purged=False)]
```

Previously both raised `AuditIntegrityError` forever. `verify_integrity()` also
no longer raises in place of returning a verification result.

**3. In-memory sequence reuse — fixed.** `_next_sequence` is now `max(...) + 1`,
matching the durable store. Post-purge save yields `[1, 2, 3]`, no reuse.

**4. Legal hold outside the chain — fixed.** Governance operations are now their
own hash-linked append-only entries, and `_verify_retained_evidence_governance`
cross-checks the `legal_hold` flag against the operation log. The round-1 attack
(clearing the flag by direct DB write) is now caught:

```
DETECTED -> EvidenceIntegrityError: retained evidence exp-1 legal hold state mismatch
```

### Non-blocking, previously raised

6 and 7 are both fixed: `is_locked = true` on the bucket retention policy, and
`require_retention_purge_authority` now enforces actor-level SoD against both
`requested_by` and the export authorizer.

5 (**the digest is unkeyed**) stands, and remains non-blocking for the same
reason as in round 1: acceptance 2 permits a hash chain, and a chain holds if
its head is externally anchored — which finding 1's fix now actually provides.
Worth being explicit about what that means, because it bounds the whole design:

A principal with direct write access to the product DB can still rewrite record
content or governance state and recompute the affected digests, because they are
plain SHA-256 over data the same principal controls — no key material is
involved despite the `signature_key_id` metadata. I confirmed this applies
equally to the main record chain and to the new governance sidecar, so the
sidecar is not a weaker link than what it sits beside; it is the same property.
What makes this tolerable is that the WORM sink now holds the original: in both
cases the tampered DB disagrees with the sink, which the product cannot rewrite.

**Follow-up (not blocking this task): nothing reconciles the DB against the
sink.** The sink is currently write-only — `AuditWormSink` has no read side, so
a divergence is recorded but never detected automatically. The evidence survives;
the alarm doesn't exist. Two candidates, either of which closes finding 5
properly: a reconciliation/verify job that replays sink objects against the DB
chain, or HMAC/KMS signing so tamper is detectable without a second source.
Recommend tracking this as its own task rather than growing this one.

### Verified good (carried forward, re-checked)

Round-1's "verified good" list still holds on this branch: governance-role
spoofing is rejected (403), legal-hold SoD is enforced, bundle and event content
tamper are detected, replay preserves hashes/order/actor/correlation/retention,
and sensitive-export masking, purpose scope, expiry, and identity-bound download
evidence are present and tested.

### Reviewer commands

```bash
uv run pytest tests/security tests/integration -q                 # 401 passed
python3 -m ruff check shared/audit shared/observability/audit.py \
  modules/opsboard/audit apps/api/app/routes/audit.py tests        # clean
```

Round-2 repros were run from a scratch directory against `_durable_bundle` and a
live `create_app()`; they are not committed. Consistent with the owner's reason
for parking the push, this file describes the residual weakness as a design
property and does not carry a step-by-step recipe — the repository is public.
