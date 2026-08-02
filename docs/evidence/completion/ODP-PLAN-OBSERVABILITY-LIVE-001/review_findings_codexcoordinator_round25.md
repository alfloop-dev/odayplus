# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 25 independent review

- Reviewer: CodexCoordinator
- Reviewed owner head: `247629ba`
- Decision: REOPEN / NO-GO
- Scope: complete Round 24 remediation, not only the newly added unit assertions

## Blocking findings

### B1 — The claimed durable readback defaults to an in-memory test store and has no production wiring

`DeliveryAuthorityReadback()` constructs `InMemoryDeliveryAuthorityStore` by default. That store is process-local, is lost on restart, and is not an out-of-process authority source. Repository search finds no non-test construction or call of `read_by_delivery_id`; therefore no production/provider path can perform the claimed durable readback.

Independent receipt:

```text
DEFAULT_STORE InMemoryDeliveryAuthorityStore
```

An abstract interface plus a test store is useful scaffolding, but it does not satisfy the deliverable or evidence claim that the separate durable authority/readback boundary is implemented.

### B2 — The caller-controlled test bypass remains selectable from production code

`DeliveryAuthorityReadback._create_for_testing` is shipped on the production class and accepts caller-owned key, issuer, and store. A leading underscore is naming convention, not an execution boundary. `InMemoryDeliveryAuthorityStore` is also exported from both `modules.notifications.domain` and the top-level `modules.notifications` package. Any ordinary in-process caller can select the same bypass that produces `DELIVERED` in the positive test.

```text
TEST_FACTORY_PUBLICLY_CALLABLE True
```

Test trust-root injection must live outside the production module/call graph, or be guarded by a build/runtime boundary that production cannot select.

### B3 — Replay consumption is not atomic

`read_by_delivery_id` performs `is_consumed`, signature/binding verification, and `mark_consumed` as separate calls. Concurrent readers can both observe false before either marks the record and both return `DELIVERED`. A durable implementation needs an atomic read-and-consume/compare-and-set transaction owned by the authority store, with a concurrency mutation proving exactly one success.

### B4 — Canonical identity formats remain under-validated

The expected request hash accepts any nonzero string of length at least 32 rather than exactly 64 hexadecimal SHA-256 characters. Release SHA checks length but not hexadecimal form. The durable boundary must validate canonical formats before comparison and signature acceptance.

## Required complete-batch remediation

1. Add a real durable, restart-safe authority read implementation and production wiring; do not default production readback to an in-memory store.
2. Make the authority store read-only to application code. External authority ingestion/writing must be outside the application caller boundary.
3. Remove production-selectable custom trust-root/test factory paths and stop exporting the in-memory test implementation from production package APIs.
4. Replace split replay checks with one atomic consume-if-valid operation and add concurrent/restart replay mutations.
5. Enforce exact canonical formats for request SHA-256, release commit SHA, timestamp/UTC, identifiers, route, and signature encoding.
6. Prove missing durable configuration fails closed; prove no application adapter or caller can write an authority record or issue `DELIVERED`.
7. Re-run the full packet and preserve explicit authentic-provider/Human-Ops evidence pending and release `NO-GO`.

No PR refresh, merge, runtime rollout, or deployment is approved.
