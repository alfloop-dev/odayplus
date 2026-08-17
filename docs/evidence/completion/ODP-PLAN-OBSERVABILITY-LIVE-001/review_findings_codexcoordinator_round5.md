# ODP-PLAN-OBSERVABILITY-LIVE-001 — CodexCoordinator Round 5 Review

- Implementation head reviewed: `d8f2a386`
- Decision: `CHANGES_REQUESTED`
- Review scope: independent watch-signal sufficiency and durable proof integrity

## Blocking findings

1. The watch allowlist is too broad to prove service health. Two positive
   `api_request_count` points are sufficient for `WATCH_PASSED`; no error-rate,
   latency, or health signal is required. Request volume alone does not prove
   that a deployment was healthy during the observation window.

2. The verifier does not bind the stored provider response to the receipt
   envelope. It checks only that provider metric types are allowlisted. It does
   not compare the stored provider project, release SHA, point timestamps, or
   point values with the receipt fields used to calculate the canonical hash.
   Those provider fields can be changed without invalidating the receipt.

3. The claim that the canonical digest covers the full proof is therefore not
   true. The digest covers copied point arrays and derived results, while the
   original provider response remains outside the digest and is not
   independently reconciled.

## Reproduced fail-closed gaps

At exact head `d8f2a386`:

```text
REQUEST_COUNT_ONLY_PASS WATCH_PASSED
observed_metric_types = [custom.googleapis.com/api_request_count]
error_count = 0
health_check_pass = True
window_coverage_seconds = 840.0

TAMPERED_PROVIDER_PROOF_VERIFY_PASS WATCH_PASSED
```

The second case changed the stored provider project, release SHA, point
timestamp, and point value while leaving the copied receipt fields unchanged.

## Round 6 exit criteria

- Define the minimum required watch signal set and aggregation contract.
  A passing watch must include independent error/failure and latency or health
  evidence, not merely one arbitrary allowlisted metric.
- Bind explicit thresholds and units to each required signal.
- Canonicalize and hash the complete provider response, or recompute all
  canonical point/type/project/SHA fields from the provider response and
  compare them exactly during verification.
- Reject missing, additional, mismatched, stale, reordered, or altered provider
  proof according to an explicitly documented canonicalization rule.
- Add both reproduced cases as mutation tests.

