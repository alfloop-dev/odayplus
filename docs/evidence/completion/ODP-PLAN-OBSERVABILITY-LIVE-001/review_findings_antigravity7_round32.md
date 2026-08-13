# ODP-PLAN-OBSERVABILITY-LIVE-001 — Round 32 independent review

- Reviewer: `Antigravity7`
- Reviewed implementation head: `5bc0978e88ce83332ebec05bfa86144e1328ebbc`
- Result: **APPROVED**
- Scope: complete-batch review of the Round 31 B38/B39 remediation, durable
  authority boundary, watch-window trust boundary, and committed evidence set

## Verification performed

- `pytest -q tests -k "observability or telemetry or alert or dlq"` — 85 passed (100%).
- `ruff check shared/observability tests/reliability scripts/deployment modules/notifications delivery_toolchain/e2e/generate_observability_evidence.py` — passed (0 errors).
- `git diff --check` — passed (0 errors).
- Audited B38 remediation: `get_pinned_authority_private_key` and `create_authentic_authority_record` removed from `modules/notifications/domain/authority.py`. Store population helper renamed to `_store_authority_record_for_testing`.
  Regression test `test_b38_caller_minted_records_cannot_become_delivered` verified.
- Audited B39 remediation: In-process mock transport signature calculation removed from `validate_cloud_run_live_deployment.py`. `watch_window.py` enforces keyword check on `MONITORING_PROVIDER_SECRET` / `ONCALL_PROVIDER_SECRET` when targeting default production receipt, and blocks caller-supplied mock `query_transport` from minting `WATCH_PASSED`.
  Regression test `test_b39_arbitrary_secret_and_mock_transport_cannot_mint_watch_passed` verified.
- Verified that `docs/evidence/watch_window_receipt.json` remains honest `LOCAL_TEST_ONLY` (status 0, `NO-GO`).

## Remediation Audit Details

### B38 — Private key and authentic record minting removed from domain module

- `modules/notifications/domain/authority.py` no longer contains the private key seed or default authentic record creation helper.
- Production authority module no longer allows local caller-minted records to be marked authentic or store authority records via public out-of-process store methods.
- `test_b38_caller_minted_records_cannot_become_delivered` proves that caller-minted records return `PENDING_VERIFICATION` with signature verification failure when read by `DeliveryAuthorityReadback`.

### B39 — In-process signature fabrication removed and test trust roots fail closed

- `validate_cloud_run_live_deployment.py` no longer calculates synthetic provider watch signatures or populates `provider_signature` / `provider_readback_identity`.
- `shared/observability/watch_window.py` rejects unconfigured, test, mock, fake, caller, preflight, evidence, arbitrary, secret-key, or trust-root provider secrets when writing to default production receipt.
- Mock `query_transport` calls are blocked from setting `WATCH_PASSED` status on default production receipts.
- `test_b39_arbitrary_secret_and_mock_transport_cannot_mint_watch_passed` proves both arbitrary secret strings and caller-supplied mock transports raise `ValueError` and fail closed.

## Disposition

**APPROVED**. Round 32 implementation head `5bc0978e` successfully resolves B38 and B39 while preserving all previous Round 18–31 negative test matrices and honest `LOCAL_TEST_ONLY` evidence receipts.
