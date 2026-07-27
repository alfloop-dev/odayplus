# ODP-RUNTIME-PROVIDER-SELECTION-001 completion evidence

## Delivered behavior

Cloud Run live-deployment preflight now derives required production
configuration from the governed `ODP_PRODUCTION_PROVIDER_IDS` selection:

- selected providers must supply their production endpoint and governed
  authentication/secret configuration;
- unselected `listing.partner_feed` receives no invented credentials and
  remains fail-closed;
- the live E2E selection continues to require the POI, geocode, and
  administrative-boundary snapshot providers.

The change is limited to provider selection, live-provider configuration,
deployment preflight, and their focused operational tests. It does not weaken
fixture/mock rejection or enable an unselected provider.

## Verification and review

Reviewer Codex6 approved exact head
`1479a415f00b05c1c2112d165ca849e0b65669fa` against `origin/dev`
`a0952273b82a5e9d6f6e789183c74de16616aa1c`.

Final owner verification:

```text
uv run pytest -q tests/ops/test_cloud_run_live_deployment.py \
  tests/integration/test_external_provider_connectivity.py
result: 40 passed

bash -n scripts/deploy_cloud_run_waji.sh
result: passed

git diff --check origin/dev...HEAD
result: passed
```

PR #418 passed the orchestrator, product, product-E2E, and task-review gates
and merged into `dev` as
`bcb1a9d1d1e2792d159c4b2c754c286a98726ea0` on 2026-07-27.
