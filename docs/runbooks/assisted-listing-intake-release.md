# Assisted Listing Intake v1 — Release Runbook

Task: ODP-INTAKE-RELEASE-001 · Design: ODP-SD-INTAKE-001 v0.2.1
Normative sources:
`docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_MIGRATION_ROLLOUT_RUNBOOK.md` (§4 phases, §5 rollback),
`docs/operations/ODAY_PLUS_ASSISTED_LISTING_INTAKE_RELIABILITY_PRIVACY_CONTRACT.md` (§4 restore order),
`docs/design/ODAY_PLUS_ASSISTED_LISTING_INTAKE_SYSTEM_DESIGN_RESPONSE.md` (§12 approvals).

This runbook is the operator flow for the governed release of assisted
listing intake v1: shadow processing, tenant/source write canary, UAT,
migration reconciliation, kill switch, rollback, restore, and the
fail-closed cutover gate.

## 1. Governance record (machine-readable)

| File | Role |
|---|---|
| `infra/assisted-listing-intake/feature_flags.yaml` | The 5 production flags. All `enabled: false` until every §12 approval is recorded. High-risk: enabling requires a dual approval through `shared/auth/feature_flags.py`. |
| `infra/assisted-listing-intake/release_authority.yaml` | §12 owner approval register. Humans record approvals here; automation never flips a row. An `approved` row without `approver` + `approved_at` + `evidence_ref` is treated as drift and fails the harness. |
| `infra/assisted-listing-intake/canary_plan.yaml` | Shadow acceptance metrics + 7-unit tenant/source write-canary ladder with entry gates. |
| `infra/assisted-listing-intake/rollback_triggers.yaml` | Kill-switch trigger register and the §5.2 mechanism order the rollback drill executes. |
| `infra/assisted-listing-intake/live_runtime_evidence.yaml` | Live runtime evidence register. Humans record the completed live targets (production shadow window, live staging E2E, live PITR/failover) and live canary unit results here; automation never flips `recorded`. `recorded: true` without the full attestation, a completed target without its own evidence, or a live claim while `recorded: false` is drift and fails the harness. |

## 2. The release harness

```bash
python3 scripts/release/assisted_listing_intake/run.py --phase all \
  --output-dir docs/evidence/completion/ODP-INTAKE-RELEASE-001 \
  --uat-report <playwright-json-report>
```

Phases run in this fixed order; each emits `<phase>.json` evidence:

1. **readiness** — proves at runtime that: every production flag is off and
   the governance engine rejects enable-without-dual-approval; every §12 row
   pending keeps its fail-closed effect active; memory/SQLite persistence
   and the in-memory object store are staging/CI surrogates that are never
   reported as production; `ODP_PERSISTENCE` unset silently falls back to
   memory (recorded as a deployment-gate hazard); GCS without credentials
   fails closed.
2. **migration** — staging backfill for two tenants through the real
   ODP-INTAKE-MIGRATION-001 harness, per-tenant shadow verification
   (counts + checksums, blocking findings must be 0), cross-tenant overlap
   check, then the scoped rollback proof on an isolated copy.
3. **shadow** — shadow processing canary at drill volume through the real
   intake state machine + snapshot service + durable audit/outbox. Measures
   every `shadow_acceptance` metric in the canary plan: tenant isolation,
   unknown/blocked source fail-closed, 0 ambiguous auto-merges, 0 automatic
   promotions, exact-duplicate agreement, field parity, snapshot checksum
   reconciliation, audit/outbox loss 0.
4. **killswitch** — fires a *detected* trigger (a real checksum mismatch
   surfaced by snapshot integrity verification → `TRG-CHECKSUM`) and
   executes the §5.2 mechanism steps in order: disable flags + refuse new
   work, evidence retained read-only, drain/park in-flight jobs with
   fence-token proof (stale fence rejected), event publication stopped with
   unpublished outbox rows retained, legacy-authoritative fact recorded,
   governance-only reversal paths recorded. Emits the full evidence packet
   (trigger, actor, flag versions, task counts, aggregate versions, outbox
   range, snapshot manifest, reconciliation results, tenant impact,
   release-authority state).
5. **restore** — reliability contract §4 restore order, steps 1–9, on
   isolated copies of the drill runtime and staging record: residency
   config, PITR-surrogate restore with table count/checksum validation,
   audit hash-chain verification, snapshot↔object-store reconciliation,
   identity redirect cycle check, listing revision/candidate uniqueness,
   job/idempotency/outbox reconciliation, projection rebuild from outbox,
   read-only validation then controlled write enablement. Runs **after**
   the kill-switch drill (§5.2 step 8 hands off to it).
6. **canary** — the 7-unit write-canary ladder in strict order. Units 1–2
   (internal tenant: assisted-entry-only, then one approved retrieval
   source) execute real write flows on the staging surrogate. Units 3–7
   (production) transition only on their **exact entry gates**:
   - while any required §12 approval is pending or live staging runtime
     evidence is unrecorded, they come out **BLOCKED** — a production unit
     executing today would itself be a release failure;
   - once a unit's gates genuinely pass it is marked **CLEARED for live
     execution** (`transition_allowed: true`) — the harness never executes
     a production unit on a surrogate;
   - a production unit counts as **passed** only via a human-recorded live
     result in `live_runtime_evidence.yaml → canary_units`; a recorded
     live failure halts the ladder and fails the drill.
   The ladder halts at the first blocked/awaiting unit. Promotion stays
   separately gated (`assisted_intake_v1_promotion`).
7. **uat** — role-based operator UAT. Run the exact product gate:

   ```bash
   PLAYWRIGHT_JSON_OUTPUT_NAME=/tmp/uat-report.json \
     npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts --reporter=json
   python3 scripts/release/assisted_listing_intake/run.py --phase uat \
     --uat-report /tmp/uat-report.json --output-dir docs/evidence/completion/ODP-INTAKE-RELEASE-001
   ```

   Without a report the phase fails closed.
8. **cutover** — governed cutover gate. Recomputes the §12 register, flag
   manifest, and live-evidence register. Two valid passing states:
   - **BLOCKED** (today's expected state): any §12 row pending or live
     runtime evidence unrecorded, with every production flag off and every
     drill green;
   - **AUTHORIZED** (`cutover_authorized: true`): every §12 row approved
     **and** live staging runtime evidence recorded **and** every drill
     green (runbook §4 rule).
   Any prematurely enabled flag, drifted approval/evidence row, or failed
   drill fails this phase in either state.

Exit codes: `0` all executed phases passed (including cutover *correctly
blocked*), `1` a phase failed, `2` governance-config drift (fail closed).

Note: `killswitch`/`restore` share drill databases — run them in one
invocation (`--phase all`) or pass a persistent `--work-dir`.

## 3. What the drills do NOT claim

The drills execute the production adapters (durable queue/outbox/audit,
state machine, snapshot service, migration harness) against the SQLite
staging surrogate and in-memory GCS surrogate. Every report carries
`environment: staging-surrogate`, `production_ready: false`, and
`not_executed_targets` marking what still needs the live environment:

- production shadow window (≥ 7 days or 10k rows),
- live Cloud SQL PITR restore + regional failover drills,
- live staging E2E with GCS/Cloud Tasks/Pub/Sub,
- production canary units 3–7.

No legacy, SQLite, memory, fixture, or silent-fallback path is presented
as production-ready; the readiness phase proves each rejection at runtime.

## 4. Recording a §12 approval (human release authority only)

1. Owner reviews the drill evidence under
   `docs/evidence/completion/ODP-INTAKE-RELEASE-001/`.
2. Edit `infra/assisted-listing-intake/release_authority.yaml`: set the
   row's `status: approved` and fill `approver`, `approved_at` (UTC ISO),
   `evidence_ref` (link to the exact evidence file/commit).
3. Commit via a task PR. Automation must never flip a row; the harness
   treats an approved row without those fields as drift and blocks.
4. Re-run `--phase cutover`. Cutover unblocks only when **all** rows are
   approved **and** live staging runtime evidence is recorded.

### Recording live staging runtime evidence (human release authority only)

The governed input for live evidence is
`infra/assisted-listing-intake/live_runtime_evidence.yaml` — there is no
CLI override. To record it:

1. Complete the live targets in the register (production shadow window,
   live staging E2E over GCS/Cloud Tasks/Pub/Sub, live Cloud SQL PITR +
   regional failover) and collect their evidence artifacts.
2. Set each target `status: completed` with `completed_at` (UTC ISO) and a
   per-target `evidence_ref`, then set `recorded: true` with
   `recorded_by`, `recorded_at`, `evidence_ref`, and the
   `error_budget_intact` attestation.
3. As live canary units 3–7 complete, append their results under
   `canary_units` (`unit`, `passed`, `completed_at`, `evidence_ref`).
   The harness accepts a production unit as passed **only** from this
   record — never from a surrogate execution.
4. Commit via a task PR and re-run `--phase canary` / `--phase cutover`.

The register is schema-validated fail-closed at load: `recorded: true`
missing any attestation field or required target, a completed target or
canary unit without its own evidence, or a live claim while
`recorded: false` blocks the harness as drift (exit code 2).

## 5. Enabling a production flag (after full §12 approval only)

Flags are high-risk: `FeatureFlagRegistry.enable` requires ≥ 2 recorded
approvers, and the manifest loader rejects `enabled: true` without them.
Follow the canary ladder order — `shadow` → `write` (per tenant/source
unit) → `events` → `promotion` — never enable out of order, and re-run the
harness after each unit.

## 6. Kill switch / rollback (production incident)

Triggers: see `rollback_triggers.yaml` (§5.1). On any trigger:

1. Disable the tenant/source flags (dual-approval disable is NOT required
   to disarm — disabling is always allowed) and stop new Cloud Tasks.
2. Follow the §5.2 mechanism order exactly as the kill-switch drill does;
   the drill evidence (`killswitch.json`) is the executable reference.
3. If restore is required, follow the §4 restore order (`restore.json` is
   the executable reference).
4. Capture the evidence packet fields listed in
   `rollback_triggers.yaml → evidence_packet.required_fields`.

## 7. Verification commands (release gate)

```bash
python3 -m pytest tests/ops/test_assisted_listing_intake_release.py -q
npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts
python3 scripts/e2e/check_product_release_gate.py
python3 scripts/e2e/check_product_grade_ci_gates.py --require-go --report
git diff --check origin/dev...HEAD
```
