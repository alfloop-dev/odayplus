# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 31 independent review

- Reviewer: `Codex`
- Reviewed implementation head: `38ad83cc03a6ca0b381f01d0bcfd217eacab4b72`
- Result: **CHANGES_REQUESTED / NO-GO**
- Scope: complete-batch review of the Round 30 B36/B37 remediation, durable
  authority boundary, watch-window trust boundary, and committed evidence

## Verification performed

- `pytest -q tests -k "observability or telemetry or alert or dlq"` — 79 passed.
- `ruff check shared/observability tests/reliability scripts/deployment modules/notifications scripts/e2e/generate_observability_evidence.py` — passed.
- `git diff --check` and `git diff --check 1d78bd76..38ad83cc` — passed.
- Independent caller-minted authority mutation — returned
  `(True, 'DELIVERED', None)`.
- Independent caller-owned watch transport/secret mutation — returned
  `WATCH_PASSED`, `readback_verified=True`.

Round 31 correctly makes the committed `docs/evidence/watch_window_receipt.json`
internally honest: it is now `LOCAL_TEST_ONLY`, status code 0, with provider
readback and telemetry explicitly unverified, and its note retains `NO-GO`.
It also stops swallowing pinned-key verification failures and rejects
noncanonical or duplicate intent filenames. Those fixes are retained, but the
full packet still fails the authority and live-provider boundaries below.

## Blocking findings

### B38 — repository code exposes the pinned private key and caller-minted records become DELIVERED

`modules/notifications/domain/authority.py:227-257` adds the public helpers
`get_pinned_authority_private_key` and `create_authentic_authority_record` to the
production domain module. The private key is deterministically derived from the
repository-visible string `urn:pantheon:oncall-authority-v1:key-seed`, and the
pinned public key was changed to match it. The record helper uses that private
key by default and labels its locally generated result authentic.

A local caller can therefore choose every delivery binding, persist the record
through the public `store_authority_record_out_of_process` method, and make the
production readback return `DELIVERED` without an external provider or authority
operation. Independent mutation at the reviewed head:

```text
AUTHORITY_CALLER_MINT (True, 'DELIVERED', None)
```

The journal transition remains caller-owned as well. `_write_journal_intent`
constructs `store_identity` and `transition: CONSUMED` locally, while
`verify_journal_intent_record` verifies only the copied authority-record
signature payload. That signature does not bind the store/source/tenant
identity, transition, transition timestamp, or an independent authority
consume result. Round 30 requirement 1 was therefore not implemented; instead,
the production trust secret was moved into the repository so tests could mint
records that pass the pinned-key check.

Required remediation:

1. Remove all production/repository access to the pinned private key and any
   default local authentic-record minting helper. Test keys and constructors
   must live only in isolated test support and must not match production trust.
2. Ingest only records obtained through a separately configured, durable,
   read-only authority/provider boundary. Do not expose a production app write
   method that can populate the same authority store it trusts for delivery.
3. Define and verify a separate authority transition record/signature binding
   the exact delivery/provider/request/release/route, issuer/key ID,
   store/source/tenant identity, `CONSUMED` transition, and transition timestamp.
4. Add a regression that derives or imports every repository-visible test
   helper/secret and proves no caller-selected record or transition can produce
   `DELIVERED` or durable consumed state.

### B39 — arbitrary caller secrets and caller-owned transports still mint live watch success

`shared/observability/watch_window.py:586-597` rejects only two literal known
test strings. Any other caller-controlled environment value is accepted as the
supposed external provider trust root. The signing algorithm is public and uses
that same caller-supplied value, so a caller that constructs the query response
can also construct its accepted signature and readback identity.

Independent mutation supplied a locally constructed two-series response, a
caller-owned transport, and the unlisted local secret
`arbitrary-caller-controlled-secret-not-blocklisted`:

```text
WATCH_CALLER_MINT WATCH_PASSED True
['custom.googleapis.com/api_error_count',
 'custom.googleapis.com/api_latency_ms']
```

`scripts/deployment/validate_cloud_run_live_deployment.py:396-483` still does
exactly this in the runtime validator. Round 31 only renames its fallback from
`test-provider-secret-key` to
`live-authentic-provider-secret-for-preflight`; the same in-process mock builds
the time series and computes the matching proof. Lines 617-631 then record the
result as `WATCH_PASSED` without an explicit test-only mode or temporary receipt
path. This violates Round 30 requirement 3 and can overwrite the default durable
evidence artifact with caller-owned loopback success.

Required remediation:

1. Separate structural/unit preflight from live validation. Mock transports,
   local secrets, and synthetic series may return only `LOCAL_TEST_ONLY`; they
   must never set the live watch gate or write the production evidence path.
2. Load the live trust identity from a non-caller-controlled deployment secret
   or use provider-native authenticated readback whose provenance is verified
   independently. A denylist of known test strings is not a trust boundary.
3. Remove the production validator's local response/signature fabrication.
   Live success must come from the configured provider and exact deployed
   release, project, window, and complete API/worker/event-DLQ/model/solver/
   business signal inventory.
4. Add arbitrary-secret, caller-owned-transport, renamed-secret, two-series-only,
   and default-receipt-overwrite mutations. Every case must remain
   `LOCAL_TEST_ONLY`/`PENDING`/`NO-GO`.

## Disposition

Reopen the task to `in_progress`. Preserve the honest `LOCAL_TEST_ONLY` receipt,
canonical intent filenames, duplicate rejection, and all Round 26–30 negative
matrices while correcting B38 and B39 together. Authentic provider, on-call,
watch-window, and Human/Ops evidence remains pending; no PR, merge, rollout, or
deployment is authorized from this head.
