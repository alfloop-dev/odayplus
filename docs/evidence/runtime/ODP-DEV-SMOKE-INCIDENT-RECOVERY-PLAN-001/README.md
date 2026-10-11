# Docs-only incident recovery review packet

Task: **ODP-DEV-SMOKE-INCIDENT-RECOVERY-PLAN-001** · Pi → Codex2

Artifact: [recovery design](../../../design/ODP_DEV_SMOKE_INCIDENT_RECOVERY.md).
This packet is source-analysis evidence, **not live execution, authorization,
independent approval, credential binding or full acceptance evidence**.

## Supplied incident observations (not recollected by this worker)

Task-scoped supervisor handoffs:
- `support/handoffs/dev-live-898630fe-20261010/README.md`
  (SHA-256 `3ad5bb152198dc3f5ebeeb7874c6a880023c6abb2dc0a3bcd40e693e55f5227b`).
- `support/handoffs/dev-live-d50cd331-20261010/README.md`
  (SHA-256 `2ce98fc5a308e9fd689a133903706f6920301c283700698f28c533a7196887b9`).

These context files are materialized by the supervisor, not tracked in the
analyzed `origin/dev`. Their runtime assertions are attributed here, not claimed
as independent fresh observations. The 898 handoff names
`authenticated-preflight.json`, `reconciliation-after-attempt.json`,
`quarantine-receipt.json`, `UNCERTAINTY-RECONCILIATION.json`, provenance readbacks
and original runtime validation. Those underlying collector bytes were not
provided in this worker's packet; obtain the retained non-secret receipts from
the foreground coordinator before any future execution. Hashes prove context
integrity only, not consent, mailbox custody or deployment authority.

Retained handoff facts sufficient to avoid a false rollback/reset narrative:
- AUTH001 authorized creation occurred **once**. Same-tenant `odp-dev-smoke`,
  UUID `13faae19-21c6-4663-8e89-b93ea7f1107d`, is **active**, `[platform_admin]`.
- Tenant `e34f2117-de4b-478c-82fd-13c4ef428d42`; original UUID
  `17e9cb99-db46-4a07-8e61-6bf9b22cf5d2`, roles `auditor + platform_admin`,
  selected identity/status/clearance/scope unchanged; original password authenticates.
- Invitation `1a012edb-842b-4846-8699-6ebd4e350218`, issue
  `0b85d791-129a-4dab-8c51-0978c4111a12`, accept
  `a9c756a7-cdf6-48f3-9bd9-7c9437958939`; original issuer and new accepting actor.
  Acceptance committed `2026-10-10T16:44:42.681277Z`.
- Execution `89dad153-138f-4541-8bd2-e972d59cce08`, plan
  `sha256:d466580ea9c4b941b92e302669db5777ebf746c6b06e8efb9c812fbc69d581c9`.
  Quarantined once at 16:52, audit `019e39ed-27f9-4a33-bc99-9bd0e561e576`:
  `recovery-required`, **binding NULL**. GitHub dev bundle metadata absent at
  16:47; no durable intent, acknowledged PUT or decrypted/fresh-job consumer proof.
- Parent 600-second timeout killed child; generated password was memory-only and
  lost; no completion receipt. Interrupted-session cleanup **UNKNOWN**. Later
  collectors' own logout 200/session 401 does not resolve the child's sessions.
- Later release 38075833887 at d50, manifest
  `sha256:ef0226f5e81cb8b3567e2cd9175af3806dedeb2799b0d9d7df20c0da69df7af8`,
  dev-admin, passed using original credentials; no new-bundle or F11 claim.

## Source receipt

`git fetch origin dev` completed successfully. At source inventory,
`git rev-parse origin/dev` returned
`d50cd331a53b7aba3a6f2f8fe8919423620f0754`. All design links use that immutable
commit, not branch-relative URLs. `git show origin/dev:<path>` supplied additional
router/service source for inspection. The two requested cache files matched the
canonical Git bytes by SHA-256:

| File at d50 | Git bytes and source-doc cache SHA-256 |
| --- | --- |
| `delivery_toolchain/release/provision_dev_smoke.py` | `e96335ad51da9a34ed06a86fd030f5bc41c8ecf8091ffeb3bc7e97341f966461` |
| `shared/identity/dev_smoke_journal.py` | `98fd75a737fd2a647a4158b33ed5fdb249c84622a5328cf49989895266b2475e` |

Read-only inventory covered live route mounting, user permission wiring, users
and journal router, invitation acceptance API/BFF/service, self-password change,
internal identity store/Argon2 service, identity administration, strict bundle
reader/ACK and gate. Bounded `git grep` over `apps shared modules` for
reset-password/password-reset/recovery-capability/forgot-password spellings
found no recovery route (only the journal's prohibition comment). That negative
search alone is not the conclusion: the inspected actual routes/services prove
self-change needs the lost password, invitations create new identities and the
quarantined root cannot bind. Source line citations are in design §2.

## Verification and review boundary

Task declares **Verification: none**. No test/build suites, browser, cloud,
secret, database or account operations were run by this worker. Git source reads
and docs-only diff inspection are not runtime verification. Mandatory commit/PR
wrapper policy preflights are delivery checks, not incident acceptance. Independent
Codex2 review must be pinned to the submitted exact PR head; no approval is
asserted in this packet. The remote PR/head and atomic review submission are
recorded by `task_finalize.sh` via the live canonical status root, not a mutable
self-approval field here.

Docs inspection checks (repeat on the committed submission head):
- `git diff --check origin/dev...HEAD`: whitespace/error check only.
- `git diff --name-status origin/dev...HEAD`: exactly the two task artifact
  Markdown additions; no runtime source/endpoint/workflow/lockfile or frozen
  evidence changes.
- `git status --short`: clean after private-index commits, before publication.
- Requested source-doc cache SHA-256 matches canonical Git bytes (table above).

Anchor `e2d5d4e4c963` preserved factual inventory across the two files before the
conditional protocol was added. The final submitted head includes design §§4–7;
exact-head diff/remote proof belongs to the PR delivery receipt, not this anchor.
No temporary probe/script was added to the repo; source snapshots/message files
stay in orchestrator scratch. No global work summary/activity history was scanned.

## Acceptance mapping for independent review

| Acceptance | Location / disposition |
| --- | --- |
| Actual active account, quarantine, NULL binding and no false rollback/consent gap | Facts above and design §1; attributed supplied observations, raw collector availability limitation explicit |
| Official deployed API or concrete gap | Design §2 immutable source links; same-account reset missing; old root/reader/ACK cannot recover |
| Narrow new-target decision, original preserved | §4 ungranted request; target-session revocation explicit, original/other sessions excluded |
| No rewriting/replay/admission weakening | §5 distinct recovery root/capability/rotation/intent/ACK, new strict versioned consumer, full source/current admission/custody requirements |
| Finite budget and memory custody under timeout | §6 absolute 2400-second proposal, receipts before effects, unknown/quarantine matrix; parent-loss gap retained |
| Docs only, exact-head diff and independent Codex2 review | This packet, §7 and canonical remote PR submission; review pending, no runtime authority |

Special review concern: the deployed CLI uses a secret environment input; the
proposal bans plaintext-env/argv custody, including its future fresh consumer.
A reviewed memory-only consumer adapter is another pre-effect prerequisite, not
an already available feature. No v1 decoder bypass or same-job refresh is proposed.
Source implementation/tests/deployment/owner execution authorization all remain
separate future gates. Independent #1447/UI/data lanes are not suspended.
