# Rebuilt dev admission for a31

Candidate `a31e02ae391811a4c323ec4d834b70e200953366`, Runtime Release build `36333397898`, manifest `sha256:499110d08fc91eef448ba9e3697005b0978669946ca0065e065cb18871ca83b2`. User decision `HUMANOPS-DEV-MIGRATION-20260927T225545Z` was recorded at `2026-09-27T22:55:45Z` and expires `2026-09-28T04:55:45Z`. The exact scope is in `a31-user-authorization.json`.

Gate0/1 evidence is described in their receipts: a31 docs-scope CI skipped product jobs; inherited355 full product CI is supported by11 identical product/build Git objects. Gate4 is a user-approved dev-only deviation. Registry is GO for dev only, subject to independent review and merge. Staging/production remain blocked.

The manifest is the actual hosted build artifact byte-for-byte. Twelve Artifact Registry references resolved at recording time. Build36333397898 succeeded and skipped all deploy jobs because no lease was supplied. No deployment claim is made.

The original355 authorization and prepared request remain historical candidate-inapplicable evidence. `gate-4-receipt-355a94b5.md` preserves the prior355 receipt. The new a31 receipt is `a31-gate-4-receipt.md`.

## Historical355 records

# ODP-DEV-MIGRATION-ADMISSION-001

The current interactive user replied **確認** to both the exact candidate-scoped dev risk/deployment request and the backup/synchronization request for the supervisor's two local release input files. This change records that decision and changes only dev gate4/release admission from NO-GO to GO for candidate355a94b5. Other gates, the manifest bytes, security policies and product code are unchanged.

- `user-authorization.json`: exact scope, reply, provenance, candidate/build/manifest and six-hour expiry.
- `gate-4-receipt.md`: actual measurements and explicit deviations; no failing automated audit is presented as successful.
- `release-lease-request.json`: one newly generated nonce, to be recorded on the existing parent only after this admission is independently reviewed/merged, required dependencies are done, local input files equal the reviewed records and the parent is reopened by its real owner.
- `initial-input-sync-receipt.json`: authorized backup and sync from old419e6bf4 to reviewed355a94b5 NO-GO; it did not deploy or issue a lease.

Prepared/transcribed by interactive Codex. The assigned owner must independently inspect and adopt this evidence before Codex2 review. The historic unapproved draft in PR1374 is not rewritten or falsely treated as signed; this is the subsequent affirmative decision.

After merge, back up the two current local inputs and synchronize the reviewed GO registry plus unchanged manifest for this exact candidate. The existing parent ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 must use a fresh supervisor-issued signed lease and existing Runtime Release deploy. Never manually invoke an unsigned deployment or reuse the consumed419e6bf4 nonce. Stop if the authorization expires or candidate/artifact/risk scope changes.

Completion of this admission task means the reviewed admission delivery is merged. Actual dev deployment remains the parent rollout's responsibility, requiring real migration, Cloud Run revision/URL, smoke, sources-off, egress and IAM receipts. No external sources or staging/production changes are authorized.
