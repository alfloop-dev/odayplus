# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 30 independent review

- Reviewer: `Codex`
- Reviewed exact pushed head: `7e23469e77411a6c4d139beb210d5eee1d02c809`
- Result: **CHANGES_REQUESTED / NO-GO**
- Scope: complete-batch replay of the Round 29 durable authority remediation,
  committed evidence, and the required observability verification packet

## Verification performed

- `pytest -q tests -k "observability or telemetry or alert or dlq"` — 79 passed.
- `ruff check shared/observability tests/reliability scripts/deployment modules/notifications scripts/e2e/generate_observability_evidence.py` — passed.
- `git diff --check` and the pushed-head diff check — passed.
- The local alert evidence is now labelled as a local simulation and its receipt
  is `FAILED`, which is internally honest for that narrow artifact.

Green tests do not satisfy the production acceptance because two independent
fail-closed mutations below still cross claimed authority/evidence boundaries.

## Blocking findings

### B36 — a copied local record still fabricates durable consume authority

`verify_journal_intent_record` verifies that the intent copies the authority
record's fields, but lines 218–223 explicitly swallow failure against the pinned
Ed25519 public key. The copied `issuer_signature` only signs the authority record;
it does not sign `store_identity` or the `CONSUMED` transition. Both the store path
identity and every record field are locally readable, so a caller can manufacture
the complete journal object without any external authority operation.

Independent mutation at the exact reviewed head:

```text
BEFORE (False, 'PENDING_VERIFICATION', 'Cryptographic signature verification failed: ')
AFTER (False, 'PENDING_VERIFICATION', "Authority record for delivery ID 'copied-local-intent' has already been consumed (replay attempt rejected)")
PERSISTED_CONSUMED ['copied-local-intent']
```

The record was signed with a freshly generated, non-pinned key. Normal readback
correctly rejected it before the forged intent was added. After a local caller
copied the same record fields and signature into the canonical SHA-256 journal
filename, a fresh read accepted the intent and persisted `consumed`. The added B34
test only substitutes a different signature than the stored record, so it misses
the copy-exactly attack. The same implementation also accepts duplicate exact
intents under arbitrary `*.intent` names because filenames are not required to be
the canonical delivery-ID digest and duplicate identical records are not rejected.

This leaves Round 29 B34 unresolved. A local crash journal can assist recovery,
but it cannot itself authenticate that an external consume transition occurred.

### B37 — committed watch evidence is caller-authenticated TEST_ONLY data labelled WATCH_PASSED

`docs/evidence/watch_window_receipt.json` still claims `WATCH_PASSED`,
`readback_verified: true`, and provider receipt/signature/readback identities for
release `aecbdae29b545a56fc808d486e78d54d27b1cfff`, not the reviewed head. The
embedded response has only locally constructed API error and API latency series,
not the required API/worker/event-DLQ/model/solver/business inventory.

The supposed provider trust root is a repository-visible test string. The existing
test sets it directly, and the committed artifact validates as live success with it:

```text
MONITORING_PROVIDER_SECRET=evidence-provider-trust-root-secret \
  verify_watch_window_receipt(aecbdae29b545a56fc808d486e78d54d27b1cfff, ...)
WATCH_PASSED aecbdae29b545a56fc808d486e78d54d27b1cfff True
```

In addition, `validate_cloud_run_live_deployment.py` builds the monitoring response
inside `mock_provider_transport`, falls back to `test-provider-secret-key`, computes
the matching provider fields itself, and then treats the result as
`watch_window_ok`. This is caller-owned loopback proof, precisely what the task's
fail-closed acceptance rejects. It contradicts the Round 24–29 direction to keep
external provider/on-call/watch-window/Human evidence pending and release `NO-GO`.

## Required complete-batch remediation

1. Do not swallow pinned-key verification failure. Give the recovery transition an
   independently verifiable authority record/signature that binds the canonical
   delivery ID, provider receipt, request hash, release SHA, route, issuer/key ID,
   exact store/source/tenant identity, transition, and transition timestamp.
2. Prove that copying an authentic or unauthentic stored authority record into a
   caller-written intent cannot alter durable consumed state. Reject noncanonical
   intent filenames and all duplicate intents, including byte-identical duplicates.
3. Separate test trust roots/transports from production/live validation. A caller
   that supplies the query response and the signing secret must never mint provider
   readback success.
4. Regenerate the committed watch artifact as explicitly `LOCAL_TEST_ONLY` /
   `PENDING` / `NO-GO`, or replace it only with authentic provider readback for the
   exact reviewed release and the complete signal inventory. Do not retain a
   `WATCH_PASSED` live claim backed by repository-visible test material.
5. Re-run the entire positive and negative matrix, preserving all Round 26–29
   durability, concurrency, schema, path, release/project, coverage, route, and
   evidence gates together.
6. Keep provider/on-call/watch-window/Human-Ops evidence pending and do not open or
   refresh a PR, merge, roll out, or deploy until the full packet passes independent
   review.

No approval, PR, merge, runtime rollout, or deployment is authorized from this
head.
