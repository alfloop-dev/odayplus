# ODP-PLAN-OSS-LICENSE-GATE-001 — Coordinator Review Round 8

- Reviewer: `CodexCoordinator`
- Owner: `Antigravity5`
- Exact implementation head reviewed: `204c7478350403fc9c40e74e362bca0883f87aae`
- Review date: `2026-07-31T06:50:00Z`
- Verdict: `CHANGES_REQUESTED`

## Scope

This review inspected the exact pushed head, policy and exemption registries,
receipt validation, license/SBOM evaluation, vulnerability audit behavior, and
the current integration lineage. No owner implementation code was changed.

## Blocking findings

### B1 — Active exemptions still trust a repository-local self-attestation

`validate_exemption_entry()` checks an active exemption by opening
`docs/security/receipts/<approval_reference>.json` and accepting it when the
file repeats `status = active` and the same reference
(`scripts/security/exemption_validator.py:299-317`). It does not authenticate
the issuer or bind the receipt to the exemption's named principal, role,
package/advisory, scope, policy version, release, timestamps, artifact hashes,
or signature.

`resolve_approval_reference()` exists but has no callers. Even if it were
called, its current repository search and optional field comparisons would not
establish that the record came from the authorized Human/Ops decision source.

Use a fixed authoritative receipt verifier and schema for
`ODP-PLAN-OSS-LEGAL-POLICY-001`. It must validate the named principal ID and
role, approval source/reference, exact decision and scope, issue/expiry/review
times, policy/SBOM/audit/NOTICE hashes, and signature or independently
verifiable immutable receipt hash. A repository-local JSON file must not be
able to establish its own authority.

### B2 — The unapproved legal proposal is treated as an active allowlist

`docs/security/license_policy.json` and
`LICENSE_AND_SUPPLY_CHAIN_POLICY.md:27-37` state that LGPL variants are allowed
outright. The authoritative Human/Ops task
`ODP-PLAN-OSS-LEGAL-POLICY-001` is still `todo`, and there is no valid policy
approval receipt.

The technical gate may implement proposal mechanics, but it may not convert a
proposal into an approved legal decision. Mark the policy as proposed/unapproved
and keep release evaluation fail closed until the authoritative legal receipt
selects or revises the allow/deny/review policy and LGPL obligations. Bind that
receipt's exact policy version and hash during every release check.

### B3 — The clean-worktree production audit is not reproducible

Running the focused task test file from this exact clean worktree produced one
failure:

`test_vulnerability_audit_script_prod_passes`

The scanner invoked `pip-audit --local`; in the clean worktree environment it
returned an empty dependency inventory, so the gate correctly failed closed.
This shows the current command audits whichever environment happens to invoke
it rather than an authoritative locked production dependency set.

Run the audit against the frozen project/lockfile environment used for the
release, record the command/tool/version/input hashes, and make the test
reproducible in a clean CI checkout. Do not weaken the empty-inventory
fail-closed behavior.

### B4 — The task branch is behind current `origin/dev`

The reviewed branch merge base is
`acfb0f71a591e674e96e6129bd644eaa6a201d13`, while current `origin/dev` is
`9e5c9f29670844ac4ecdec407c84255e0a33bce3`. The branch therefore lacks later
planning/archive, release-gate, shell, human-gate, and HeatZone integration
changes.

Merge current `origin/dev`, resolve workflow/policy conflicts, rerun the full
technical verification from the reconciled head, and hand off that exact head
for another independent review.

## Required defensive tests

Add tests proving:

1. a repository-local receipt cannot establish its own authority;
2. an active receipt with missing or mismatched principal ID/role, decision,
   scope, policy version/hash, artifact hashes, issue/expiry/review time, or
   signature is rejected;
3. an unapproved policy proposal keeps the release gate closed;
4. the audit scans the frozen release dependency set in a clean checkout and
   rejects an empty or mismatched inventory.

## Decision

`CHANGES_REQUESTED`. Exact head `204c7478` is not approved. Automated checks
cannot substitute for the missing Human/Ops legal decision, and the current
receipt path does not prove authoritative approval.
