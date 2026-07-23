# ODP-INTAKE-RELEASE-001 — Completion Evidence

Task: Execute tenant and source canary, UAT, restore, rollback, and governed cutover
Design ref: ODP-SD-INTAKE-001 v0.2.1 (approved_design_sha e644bd0e)
Owner: Claude · Reviewer: Codex2 · Date: 2026-07-23

## What was delivered

1. **Release governance configs** — `infra/assisted-listing-intake/`
   (`feature_flags.yaml`, `release_authority.yaml`, `canary_plan.yaml`,
   `rollback_triggers.yaml`, `live_runtime_evidence.yaml`, `README.md`). All
   production flags are **disabled**, §12 owner approvals are recorded as
   **pending**, and live runtime targets are recorded as **pending**; the harness
   fails closed on incomplete, contradictory, or unaudited claims.
2. **Release drill harness** — `scripts/release/assisted_listing_intake/`
   (`config.py` fail-closed config loader, `gates.py` §12/flag/production
   gates, `drills.py` runtime drills, `run.py` phase orchestrator). Covered by
   `tests/ops/test_assisted_listing_intake_release.py` (34 tests), including
   blocked, malformed, fully approved/live, full canary ladder, and live-failure
   transition cases. Cutover also rejects incomplete, stale, or
   error-budget-exhausted production canary evidence.
3. **Release runbook** — `docs/runbooks/assisted-listing-intake-release.md`.
4. **Runtime drill evidence** — the JSON files in this directory, emitted by an
   actual harness execution (not hand-written).

## Drill execution (all phases, required order)

Command:

```
python3 scripts/release/assisted_listing_intake/run.py --phase all \
  --output-dir docs/evidence/completion/ODP-INTAKE-RELEASE-001 \
  --uat-report docs/evidence/completion/ODP-INTAKE-RELEASE-001/uat-playwright-report.json
```

| Phase | Evidence | Result |
| --- | --- | --- |
| readiness | `readiness.json` | PASS — flags off, surrogates rejected, §12 pending ⇒ cutover blocked |
| migration | `migration.json` | PASS — staging backfill → reconciliation → scoped rollback proof |
| shadow | `shadow.json` | PASS — shadow canary metrics within thresholds |
| killswitch | `killswitch.json` | PASS — rollback trigger + §5.2 mechanism order |
| restore | `restore.json` | PASS — reliability contract §4 restore order 1–8 after rollback; injected CHECKSUM_MISMATCH detected |
| canary | `canary.json` | PASS — write-canary units 1–2 executed; units 3+ are **BLOCKED** until their exact approval/live-evidence gates pass |
| uat | `uat.json` + `uat-playwright-report.json` | PASS — 8/8 intake product cases (7 expected + 1 flaky-recovered, unexpected=0) |
| cutover | `cutover.json` | PASS — cutover **BLOCKED** (fail-closed), no failed drills, no enabled prod flags |

Summary: `release-drill-report.json` — `passed: true`, `cutover_blocked: true`,
`production_ready: false`.

## Verification commands (exact, all green)

```
CI=1 npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts --reporter=json
  → ingested evidence: 7 passed + 1 flaky (passed on retry), unexpected=0
CI=1 npx playwright test tests/e2e/operator-assisted-listing-intake.spec.ts
  → latest verification: 8 passed
  → CI=1 is required: the score-failure fault control in
    apps/api/app/routes/listings.py is gated on CI=1 and 403s otherwise.
uv run pytest tests/ops/test_assisted_listing_intake_release.py -q   → 34 passed
python3 scripts/e2e/check_product_release_gate.py                    → PASS
python3 scripts/e2e/check_product_grade_ci_gates.py --require-go --report → PASS (37 labels, GO)
git diff --check origin/dev...HEAD                                   → clean
```

## Not executed (release-gated, honest scope)

- **Live staging / production canary units 3+**: no live staging environment is
  provisioned (Human/Ops gate — see ODP-OC-R5-003 blocker: GCP project, WIF,
  staging vars/secrets absent). The governed transition input is
  `infra/assisted-listing-intake/live_runtime_evidence.yaml`. The canary ladder
  therefore executes only the non-production units and records units 3+ as
  BLOCKED. Once approvals and validated live evidence are recorded, the same
  ladder clears each production unit in order and accepts results only from
  human-recorded evidence; it never substitutes a surrogate execution.
- **Production flag enablement**: every flag in
  `infra/assisted-listing-intake/feature_flags.yaml` remains disabled until all
  §12 owner approvals plus live runtime evidence exist. `gates.py` fails the
  readiness and cutover phases if any flag flips early or an approval row is
  marked approved without evidence.

No legacy, SQLite-as-prod, memory, fixture, or silent-fallback path is
presented as production-ready: drills that use scratch stores label them as
drill scratch state inside their own evidence JSON, and
`release-drill-report.json` pins `production_ready: false`.
