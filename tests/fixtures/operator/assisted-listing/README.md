# Operator Assisted Listing E2E Fixtures

This directory contains the mock retrieval and policy fixtures used in `tests/e2e/operator-network-assisted-intake.spec.ts` to test the various ingestion outcomes of the Operator Console.

## Fixture URLs and Outcomes

- `https://www.synthetic.example/detail-77120345.html`: Clean new listing, routes to `READY / NEW`.
- `https://www.synthetic.example/detail-88520242.html`: Revision of `L-2024` with rent reduction, routes to `READY / REVISION`.
- `https://www.synthetic.example/detail-99310418.html`: Ambiguous listing with similar address to `L-2025`, routes to `NEEDS_REVIEW / POSSIBLE_MATCH`.
- `https://www.synthetic.example/detail-40028801.html`: Malformed page structure, routes to `AWAITING_ASSISTED_ENTRY`.
- `https://www.synthetic.example/detail-50000001.html`: Simulates upstream retrieval timeout, routes to `FAILED` (retryable).
- `https://www.591.com.tw/rent-detail-16244102.html`: Gated by `ASSISTED_ENTRY_ONLY` policy, remains on client entry without fetching.
- `https://listing-aggregator.example/item/7731`: Gated by `SOURCE_BLOCKED` policy, fails closed.
- `https://unknown-house.example.tw/item/7731`: Unknown source host, gated by `POLICY_UNKNOWN` policy and quarantined.

See [corpus.json](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-oc-r5-004/tests/fixtures/operator/assisted-listing/corpus.json) for the structured mock payloads returned by `assisted_intake.RETRIEVAL_CORPUS`.
