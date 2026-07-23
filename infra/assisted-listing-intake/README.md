# Assisted Listing Intake v1 — Release Infrastructure Configuration

Task: ODP-INTAKE-RELEASE-001 · Design: ODP-SD-INTAKE-001 v0.2.1

Machine-readable release governance for the assisted listing intake rollout.
Consumed by `scripts/release/assisted_listing_intake/run.py` (the release
drill/gate harness) and by human release authority.

| File | Purpose |
|---|---|
| `feature_flags.yaml` | The five production flags (`assisted_intake_v1_read/shadow/write/events/promotion`). All `enabled: false` and high-risk (dual approval enforced by `shared/auth/feature_flags.py`). They stay off until every §12 approval is recorded. |
| `release_authority.yaml` | §12 owner approval register. All rows `pending` ⇒ every fail-closed effect active and governed cutover BLOCKED. Humans record approvals here; automation never flips a row. |
| `canary_plan.yaml` | Shadow acceptance metrics + write-canary tenant/source ladder (internal → low-volume prod → 5/25/50/100%) with entry gates per unit. |
| `rollback_triggers.yaml` | Kill-switch/rollback trigger register and the §5.2 mechanism order the rollback drill executes. |
| `live_runtime_evidence.yaml` | Live runtime evidence register (runbook §4). Humans record completed live targets and live canary unit results here via task PR; automation never flips `recorded`. Schema-validated fail-closed — a partial or smuggled record blocks the harness as drift. Cutover unblocks only when every §12 row is approved, the register is recorded, and units 3–7 each have a current passing result. |

Run the gates:

```bash
python3 scripts/release/assisted_listing_intake/run.py --phase all \
  --output-dir docs/evidence/completion/ODP-INTAKE-RELEASE-001
```

The harness fails closed: any pending approval, enabled production flag, or
unrecorded live runtime evidence blocks the cutover phase and exits nonzero
on drift. Cutover reaches `cutover_authorized: true` only when every §12
row is approved, `live_runtime_evidence.yaml` is validly recorded,
production canary units 3–7 all have current passing evidence, and every
drill is green.
See `docs/runbooks/assisted-listing-intake-release.md` for the operator flow.
