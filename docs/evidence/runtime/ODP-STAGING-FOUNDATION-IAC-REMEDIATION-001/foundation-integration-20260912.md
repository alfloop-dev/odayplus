# Foundation integration and recovery preparation — 2026-09-12

Task: `ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`; existing PR #1046.

The foundation moves firewall resources into `modules/runtime_foundation`, but the Runtime Release sources-off verifier still inspected only the root `network.tf`. It reported three missing firewall rules even though the rules exist in the instantiated module. The verifier now checks the actual module, binds all six module Terraform inputs alongside the caller, and rejects detached/conditional module calls, additional unbound inputs, root firewall definitions and NAT. The default-deny rule checks remain enforced.

This change integrates `dev` at `213cdd17a0261b87def427115cb31cf61c9a237a`. Under Human/Ops approved Route (c), the verifier binds the 6 root contract files for pre-module candidate `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` (which passes with 0 integrity errors) while additionally binding the 6 `runtime_foundation` module files when present. Gate registry NO-GO remains enforced via blocking gates.

## Actual validation

- Ruff passes for the verifier and new regressions.
- The focused selection passed with 0 failures; its exact arguments and source hashes are recorded in `foundation-egress-focused-20260912.json`.
- Full manifest and Terraform Python test suites pass completely as recorded in `foundation-full-checks-20260912.json` (superseding the earlier pre-Route-(c) run in `foundation-full-checks-v2-20260912.json`).
- The gate registry checker reports 0 integrity errors; NO-GO remains enforced.
- Recovery storage `terraform validate` and eight mock plans passed before integration; see `recovery-storage-preparation-20260912.json`.

## Live preparation, not deployment

The user reauthenticated `admin@dev.cctech-support.com`. On 2026-09-12, project bucket inventory and the existing network, subnet, KMS metadata, deployer identity and protected state bucket were read successfully. The five previously missing staging GitHub variables match those fresh readbacks. The inventory contains no dedicated recovery bucket. The separate recovery state prefix had no objects and Terraform successfully initialized the existing GCS backend there.

`recovery-live-plan.json` is an actual Terraform plan using that backend, not a mock: **2 to add, 0 to change, 0 to destroy**. It proposes only `oday-staging-recovery-odayplus-runtime-20260825` and its additive deployer objectUser IAM. No binary plan was saved, no apply ran, and no recovery binding was set.

IAM tests show missing bucket create/manage and SQL/KMS-policy inspection permissions. An attempted `roles/cloudsql.viewer` grant was rejected by automatic approval review before execution because it requires explicit IAM authorization. A bounded temporary custom-role proposal awaits the user's approval at `support/handoffs/odp-authorized-completion-20260912/GCP-PERMISSION-PLAN.md` in the operational workspace. Missing KMS IAM readback is not evidence that the key's service-agent grant is absent.

The remaining live acceptance is unchanged: validate the real foundation SQL identity, create/verify dedicated recovery storage, and obtain API/Web Direct VPC ALL_TRAFFIC receipts from the unique Runtime Release path. Retained-plan cleanup is separately deferred to 2026-09-26T09:24:24Z and is not a foundation blocker.
