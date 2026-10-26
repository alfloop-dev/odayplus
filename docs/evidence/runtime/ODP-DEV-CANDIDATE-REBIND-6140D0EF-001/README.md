# ODP-DEV-CANDIDATE-REBIND-6140D0EF-001 — exact dev candidate evidence

Candidate `6140d0ef633cbf94522c171d9927103ab200257f` is the actual `origin/dev` head used for Runtime Release build `37006345960`. This evidence rebinds the staged release registry to the exact canonical manifest digest `sha256:d286dd009978a5b9aaa9ee9fc01d1c1be1d8b846855539be63b18504ad368108`.

## Verified inputs

- Exact-SHA CI: merge queue run [36953491190](https://github.com/alfloop-dev/odayplus/actions/runs/36953491190) and push run [36954325829](https://github.com/alfloop-dev/odayplus/actions/runs/36954325829) both succeeded. The classifier marked the change as development tooling; orchestrator checks passed and product child lanes were skipped.
- Runtime Release build: [37006345960](https://github.com/alfloop-dev/odayplus/actions/runs/37006345960) succeeded for `6140d0ef633cbf94522c171d9927103ab200257f`. Secret scan, Python SAST, production npm audit, SBOM, E2E backup/restore/rollback, WIF, first-release absence readback, image build/signing/attestation all passed.
- The production npm audit recorded zero findings. Previously disclosed dev-only findings remain unresolved and are not represented as cleared. External authoritative H01 LGPL evidence remains missing and is not represented as passed.
- Build-time readback reported all five Cloud Run services/jobs absent. Dispatch must re-read them immediately before deployment.
- Build is build-only. No lease, migration execution, service/job deployment, or live mutation occurred.

## Human/Ops authorization

`HUMANOPS-DEV-MIGRATION-20261002T130206Z` binds exactly this candidate, manifest digest, build run, and one dev deployment. It permits at most one fresh release request, one short-lived Supervisor lease, and one existing Runtime Release deploy phase in dev, including ordinary dev schema migrations. It excludes DB reset/stamp/delete, external data sources, staging, and production. Authorization was transcribed from the user's instruction `處理完成` after the exact-tuple scope and deviations were presented; no external identity signature is claimed. The record expires at `2026-10-02T19:02:06Z`.

## Admission and next control

The pre-rebind admission attempt against merged `origin/dev` correctly failed closed because the canonical registry still named the preceding SHA/digest and the intervening history included non-evidence orchestrator changes. The registry and canonical manifest in this branch are rebound to `6140d0ef633cbf94522c171d9927103ab200257f` / `sha256:d286dd009978a5b9aaa9ee9fc01d1c1be1d8b846855539be63b18504ad368108`. No lease or dispatch may occur until this PR is reviewed and merged, actual `origin/dev` registry admission returns zero errors, the signer secret is available non-interactively, and the task is moved to `in_progress` with a fresh nonce.

Staging and production gates remain blocked.
