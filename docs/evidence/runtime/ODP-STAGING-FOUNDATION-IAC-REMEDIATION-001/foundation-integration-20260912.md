# Foundation integration and recovery preparation — 2026-09-12

Task: `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`; existing PR #1046.

The foundation moves firewall resources into `modules/runtime_foundation`, but the Runtime Release sources-off verifier still inspected only the root `network.tf`. It reported three missing firewall rules even though the rules exist in the instantiated module. The verifier now checks the actual module, binds all six module Terraform inputs alongside the caller, and rejects detached/conditional module calls, additional unbound inputs, root firewall definitions and NAT. The default-deny rule checks remain enforced.

This change also merges `dev` at `a04010cde22a0337fdda05a6fa5146f70cc699f4`. It does not rewrite the byte-exact Runtime Release manifest for candidate `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` or its registry. That artifact has the old contract digest and proof-source list. Required CI therefore remains blocked by the historical manifest mismatch until a genuine successor release candidate and its evidence are reconciled through the existing Runtime Release work. Updating those values by hand would misrepresent the build artifact.

## Actual validation

- Ruff passes for the verifier and new regressions.
- The initial focused selection passed 60 tests; its exact arguments and source hashes are in `foundation-egress-focused-20260912.json`.
- Final full manifest and Terraform Python suite is recorded in `foundation-full-checks-v2-20260912.json`, including JUnit counts. Four tests fail solely because the committed historical manifest has a different proof-source list and contract digest. These are unresolved failures, not approvals or passes.
- The registry checker reports the same two historical-manifest integrity errors. It no longer reports missing firewall rules; NO-GO remains enforced.
- Recovery storage `terraform validate` and eight mock plans passed before integration; see `recovery-storage-preparation-20260912.json`.

## Live preparation, not deployment

The user reauthenticated `admin@dev.cctech-support.com`. On 2026-09-12, project bucket inventory and the existing network, subnet, KMS metadata, deployer identity and protected state bucket were read successfully. The five previously missing staging GitHub variables match those fresh readbacks. The inventory contains no dedicated recovery bucket. The separate recovery state prefix had no objects and Terraform successfully initialized the existing GCS backend there.

`recovery-live-plan.json` is an actual Terraform plan using that backend, not a mock: **2 to add, 0 to change, 0 to destroy**. It proposes only `oday-staging-recovery-odayplus-runtime-20260825` and its additive deployer objectUser IAM. No binary plan was saved, no apply ran, and no recovery binding was set.

IAM tests show missing bucket create/manage and SQL/KMS-policy inspection permissions. An attempted `roles/cloudsql.viewer` grant was rejected by automatic approval review before execution because it requires explicit IAM authorization. A bounded temporary custom-role proposal awaits the user's approval at `support/handoffs/odp-authorized-completion-20260912/GCP-PERMISSION-PLAN.md` in the operational workspace. Missing KMS IAM readback is not evidence that the key's service-agent grant is absent.

The remaining live acceptance is unchanged: validate the real foundation SQL identity, create/verify dedicated recovery storage, and obtain API/Web Direct VPC ALL_TRAFFIC receipts from the unique Runtime Release path. Retained-plan cleanup is separately deferred to 2026-09-26T09:24:24Z and is not a foundation blocker.
