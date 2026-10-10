# Web immutable manifest binding — source/offline evidence

Task: ODP-WEB-IMMUTABLE-MANIFEST-BINDING-001 · Owner: Pi · Reviewer: Codex2

## Scope and provenance

Initial baseline and last authenticated deployed source:
`31785c571e062b9bef4512ec3d848391c47621d3`.
On 2026-10-10 at 14:22 UTC, before independent approval, the owner synchronized
this task with actual `origin/dev`
`d99de4a05ef58340247470ab213089b8706543c7`: #1448 had normally merged at
14:00:26 UTC and archived at 14:04:24 UTC. This is meaningful reviewed-source
composition, not a cosmetic release commit.
The task-scoped handoff records Runtime Release 38044574206 succeeding under
`dev-admin`, but authenticated Web manifest metadata was empty. This task
repairs that source omission; it does not claim a new runtime readback.

The normal shell already exports `ODP_RELEASE_MANIFEST_DIGEST` from
`MANIFEST_DIGEST` and serializes it into the API environment. The Web serializer
now forwards that same export in both `full` and `dev-admin`. SHA, profile,
transport audience, secret bindings, BFF and gates are unchanged. No API digest
is substituted for Web identity. Missing metadata remains empty, not invented;
the unchanged strict identity guard refuses it. Admission/manifest validation
remains upstream; this serializer is not a new admission authority.

## Workspace and composition boundary

Canonical allocation: `pi-20261010T134420Z-90e2b821`, isolated
`task/ODP-WEB-IMMUTABLE-MANIFEST-BINDING-001`, created from exact baseline above.
All edits are confined to this allocated worktree; no shared or #1448 checkout
is edited. At preparation, PR #1448 was OPEN, independently approved and frozen
at `0191f99282db7de486a43e7ca18221188f67a810`. Its deploy-script changes concern
profile/foreground setup and the final gate, not this Web serializer. This PR
neither imports nor moves that approved head or its account/bundle/journal logic.
The canonical task note records the composition boundary before edits.
Those statements describe the initial preparation. The current unapproved
branch now inherits the already merged #1448 source through `origin/dev`;
its frozen approved head was not edited or reopened. Original 78244003 and
4a7b929e receipts remain historical, not final-head verification.

Before a normal release is frozen or given exact-source approval, verify this
integrated account-source plus Web-binding tree and required CI, then obtain
fresh manifest/admission for its exact SHA/image/digest/profile tuple. The strict
source observer requires the entire admitted candidate tree to equal the
reviewed head. Neither old SourceApproval nor the prior successful release
authorizes a new composition. UI #1451's independent P2 repair is owner-scheduled
after the consented foreground staging; it is not an invitation transport
prerequisite. Its later ordinary release must start after staging to consume
the actual GitHub bundle, without any same-job secret-refresh assumption.
No deployment, live env patch, secret/token read, IAM, account, credential, role,
business, source, model or provisioning effect was performed here.

## Offline verification

Declared before implementation through the canonical status writer:

```text
uv run --frozen pytest tests/contract/test_web_release_manifest_binding.py tests/ops/test_conditional_oidc_deployment.py tests/ops/test_cloud_run_live_deployment.py tests/release/test_release_profile.py -q
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
git diff --check origin/dev...HEAD
uv run --frozen ruff check tests/contract/test_web_release_manifest_binding.py
uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py
```

The last two preflight checks were added to the declaration after the initial
anchor. The generated boundary inventory adds only this new test's row.

The new contract executes the actual shell export and both actual inline
stdlib serializers with isolated synthetic metadata; it never runs the deploy
script or any cloud command. It proves exact SHA/profile/nonempty digest for
both profiles and that inherited metadata cannot override the normal export.
Missing, empty, malformed or different digests remain truthful and are refused
by the existing `release_identity_failures` guard. Independently missing or
mismatched API/Web SHA, profile and digest are refused without substitution.
The offline readback projection is not an authenticated HTTP/BFF runtime test.

At source anchor `4a7b929e5f26012c4e2a25cf08d3e3826fd052d7`, the focused
and existing deploy/profile suites completed with **exit 0** in 88.003 seconds;
shell syntax and committed diff checks also exited 0. Pytest used double-quiet
output with no numeric summary; skips and one existing Starlette deprecation
warning are retained in receipt `cfca056fdead20b0`, not treated as live proof.
The first attempt exited 127 because worker PATH omitted the installed user
`uv`; that failed receipt is preserved. Recovery prepended
`/home/lupin/.local/bin` and used `UV_PROJECT_ENVIRONMENT=$PANTHEON_STATUS_ROOT/.venv`
with `UV_NO_SYNC=1`, so no dependency environment was mutated.

Ruff passed. Boundary preflight initially refused the stale generated inventory;
`uv run --frozen python delivery_toolchain/governance/check_code_boundaries.py
--write-inventory` regenerated its single new test row and passed (1212 files).
These are verification repairs, not policy exemptions.

The final evidence/inventory commit is measured again at its **exact HEAD**
before `task_finalize.sh`: `task_verification.py run` writes actual terminal
exit-code/SHA/command receipts under `.orchestrator/evidence/verification-*`.
Those ignored machine receipts and the canonical task note bind the submitted
head without a circular self-SHA edit to this document. Required remote CI and
independent Codex2 approval remain mandatory; source tests alone are not live
immutable-manifest acceptance or full-product/model/F11 acceptance.
