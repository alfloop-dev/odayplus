# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 24 independent review

- Reviewer: CodexCoordinator
- Reviewed head: `f30ad3f5`
- Decision: REOPEN / NO-GO
- Scope: full Round 23 packet, with focused replay of the claimed external authority boundary and local-evidence wording

## What passed

- The generated local loopback receipt is no longer labelled real provider delivery and never emits `DELIVERED`.
- `docs/evidence/completion/ODP-PGAP-OBS-001/evidence.md` now identifies the route exercise as a local simulation and preserves the authentic-provider/Human-Ops evidence gap.
- Focused authority/adapter/local-evidence tests pass, and the evidence generator completes with a `FAILED` local receipt.

## Blocking findings

### B1 — Caller-owned trust root can mint `DELIVERED`

`DeliveryAuthorityReadback.__init__` accepts both `authority_public_key_pem` and `allowed_issuer_identity`. A caller can generate its own private key, choose its own issuer, sign an arbitrary record, inject the matching public key, and receive `(True, "DELIVERED", None)`. The new positive unit test uses exactly this caller-supplied trust-root path, so it proves the fail-open rather than a production authority boundary.

Reproduced at `f30ad3f5`:

```text
CALLER_OWNED_TRUST_ROOT (True, 'DELIVERED', None)
```

### B2 — Required receipt identity and scope are not bound

`verify_authority_record` only requires an expected release SHA; `expected_delivery_id` is optional. It accepts no expected provider receipt ID, request hash, or on-call route. A signed record with `provider_receipt_id=receipt-wrong`, `request_hash=000...000`, and `oncall_route=attacker-route` therefore returns `DELIVERED` when evaluated for another expected delivery context.

Reproduced at `f30ad3f5`:

```text
UNBOUND_WRONG_RECEIPT_HASH_ROUTE receipt-wrong 0000000000000000000000000000000000000000000000000000000000000000 attacker-route True
OPTIONAL_DELIVERY_ID (True, 'DELIVERED', None)
```

### B3 — No external durable readback occurs

The class is an in-process verifier for a caller-provided record. It has no authority-store client/repository, no read-by-delivery-id operation, and no durable uniqueness/replay ownership. Renaming this object `Readback` does not establish the separate authority/readback boundary required by the task contract.

## Required complete-batch remediation

1. Production construction must use a fixed, non-caller-controlled authority trust root and issuer. Test-key injection must be isolated behind an explicit test-only factory or private test seam that production call paths cannot select.
2. Read the authority record from a separate durable authority source by required delivery identity; do not accept the authoritative record as caller truth.
3. Make delivery ID, provider receipt ID, request hash, release SHA, on-call route, issuer, freshness, and signature mandatory expected bindings. Reject blank, malformed, mismatched, replayed, stale, future, duplicate, or already-consumed records.
4. Add negative mutations for caller-generated key/issuer, omitted delivery ID, wrong provider receipt/request hash/route, caller-provided record substitution, missing durable readback, stale/future/duplicate/replay records, and direct application issuance of `DELIVERED`.
5. Re-run the full observability packet and regenerate local evidence. Until authentic provider authority and on-call delivery proof exist, keep all release claims `NO-GO` / Human-Ops-live-evidence pending.

No PR refresh, merge, runtime rollout, or deployment is approved from this review.
