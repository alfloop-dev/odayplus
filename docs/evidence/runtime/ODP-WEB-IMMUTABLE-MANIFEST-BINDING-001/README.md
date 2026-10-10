# Web immutable manifest binding — source/offline evidence

Task: ODP-WEB-IMMUTABLE-MANIFEST-BINDING-001 · Owner: Pi · Reviewer: Codex2

## Scope and provenance

Baseline `origin/dev` and deployed source:
`31785c571e062b9bef4512ec3d848391c47621d3`.
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

Before a normal release is frozen or given exact-source approval, Worker
Manager / merge queue must compose BOTH independently reviewed PRs into the
candidate tree. Verify that integrated tree and required CI, then obtain fresh
manifest/admission for its exact SHA/image/digest/profile tuple. Neither old
SourceApproval nor the prior successful release authorizes a new composition.
No deployment, live env patch, secret/token read, IAM, account, credential, role,
business, source, model or provisioning effect was performed here.

## Offline verification

Declared before implementation through the canonical status writer:

```text
uv run --frozen pytest tests/contract/test_web_release_manifest_binding.py tests/ops/test_conditional_oidc_deployment.py tests/ops/test_cloud_run_live_deployment.py tests/release/test_release_profile.py -q
bash -n product_ops/deployment/deploy_cloud_run_waji.sh
git diff --check origin/dev...HEAD
```

The new contract executes the actual shell export and both actual inline
stdlib serializers with isolated synthetic metadata; it never runs the deploy
script or any cloud command. It proves exact SHA/profile/nonempty digest for
both profiles and that inherited metadata cannot override the normal export.
Missing, empty, malformed or different digests remain truthful and are refused
by the existing `release_identity_failures` guard. Independently missing or
mismatched API/Web SHA, profile and digest are refused without substitution.
The offline readback projection is not an authenticated HTTP/BFF runtime test.

Verification is pending at the initial anchor. Exact-head terminal exit codes
and canonical verification receipts will be recorded before submission. Required
remote CI and independent Codex2 approval remain mandatory; source tests alone
are not live immutable-manifest acceptance or full-product/model/F11 acceptance.
