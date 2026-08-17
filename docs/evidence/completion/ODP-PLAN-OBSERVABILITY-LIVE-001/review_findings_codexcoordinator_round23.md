# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 23 Review

- Reviewed exact pushed head: `c49e372dee3ea9d06bf4fb947feb3e7d989fdbf6`
- Prior owner head: `3330e3d9a57e87b3f70b5c87aabb19b1bd557dbb`
- Disposition: `CHANGES_REQUESTED`
- Release claim: `NO-GO`

## Verified remediation

The application adapter no longer calls the in-process verifier helper and no
longer assigns its unused result. Independent Round 18–21 mutations now produce
only `TEST_ONLY` or `PENDING_VERIFICATION`; none produces `DELIVERED`.

Independent verification on the exact head:

- Round 18 mutation: `TEST_ONLY`
- Round 19 mutation: `TEST_ONLY`
- Round 20 mutation: `PENDING_VERIFICATION`
- Round 21 mutation: `PENDING_VERIFICATION`
- focused observability/telemetry/alert/DLQ tests: `66 passed`
- Ruff over every changed Python path and the full declared reliability scope:
  passed
- `git diff --check`: passed

## B1 — local loopback evidence still claims real successful delivery

`delivery_toolchain/e2e/generate_observability_evidence.py` starts a caller-owned loopback
HTTP server and has that server return `"status": "delivered"`. The generated
evidence then labels the result as all of the following:

- `Real Delivery`
- `Real Alert Delivery & Tested Routing`
- `successfully delivered`
- `real HTTP response-derived receipt`

The same template also contains a note saying the run is a local test-only
simulation. Those statements conflict. The current archived evidence makes the
problem concrete: it claims successful real delivery while the embedded adapter
receipt is actually `FAILED` because no provider trust root exists. A loopback
2xx or caller-authored response is not provider delivery evidence even when it
uses a real TCP socket.

Required batch correction:

1. Relabel every local/loopback heading, narrative, console message, and artifact
   as `LOCAL_TEST_ONLY`; remove all real/successfully-delivered claims.
2. Make the generator assert that local output is only `FAILED`, `TEST_ONLY`, or
   `PENDING_VERIFICATION`, and fail if any local composition yields `DELIVERED`.
3. Do not emit a fake provider `status=delivered`, provider signature, or readback
   field as if it came from an external authority.
4. Regenerate the local evidence so its prose and embedded receipt agree.
5. Add a mutation/contract test that rejects a local artifact containing a real
   delivery claim or a locally issued `DELIVERED` status.

## B2 — no separate authority owns the durable DELIVERED transition

The adapter boundary is now safe, but the repository still contains the old
in-process verifier implementation and mutable trust-root constants as unused
code. More importantly, no separately deployed provider/verifier authority or
durable read model now owns the transition from `PENDING_VERIFICATION` to
`DELIVERED`. Removing the unsafe application transition is necessary, but it
does not satisfy the packet's authentic route-delivery/readback deliverable.

Required batch correction:

1. Remove the unused in-process verifier/trust-root dead path from the
   application adapter module, or move the integration contract to an explicit
   separately owned authority/readback boundary that cannot be invoked as a
   local delivery issuer.
2. Keep the application receipt pending/test-only; any read model exposing
   `DELIVERED` must consume an authenticated durable authority record bound to
   delivery ID, provider receipt ID, request hash, release SHA, route, timestamp,
   and issuer identity.
3. Add negative tests for missing/forged/stale/mismatched authority records and
   for direct application attempts to issue `DELIVERED`.
4. If authentic provider/deployment evidence is unavailable, record an explicit
   Human/Ops/live-evidence handoff and keep this task `in_progress` and the
   release `NO-GO`; do not hand off the full task as complete.

No PR, merge, deployment, live-delivery claim, or approval is authorized from
this review.
