# ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001 — bounded recovery of the Forecast model/history handoff

Baseline: `origin/dev` `1b14b276447a7778f1dad1045ced1a5bd6abe01e`. Read-only; no cloud write,
no training, no alias, no gate change. Machine-readable result: `recovery_evidence.json`,
checked by `verify_recovery_evidence.py` / `test_recovery_evidence.py`.

## Result

**Nothing to recover: no real Forecast model was ever produced.**

| Original task | Branch head | Merged | Terminal evidence |
|---|---|---|---|
| ODP-PRODUCTION-MODEL-REGISTRY-001 | `950b852c` (2026-07-28) | no, no PR (sidecar #657 only) | legacy `alfaloop-data-project` inventory: 1 303 eligible rows over 4 days (2026-06-19..22); training `2dzlg` exited 2, `release_mutation_completed=false` |
| ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001 | `c1af59a9` (2026-07-29) | no, no PR (sidecar #654 only) | backfill stopped on interactive reauth (`invalid_rapt`); §12 After state never populated, no activation receipt or dataset hash |

Current dev facts are reused from the saved receipt (copied verbatim as
`mlflow-model-readback-20261002.json`): `forecast_revenue_interval` 404 in dev MLflow, dev
artifact bucket empty, legacy bucket access unknown, current DB row count unknown. These were
not re-queried, and the 4-day legacy count is not used as the current count.

`product_ops/modeling/release.py` exposes only `inventory`, `train`, `promote`; there is no
import-existing-artifact entry, and none is needed because no approved artifact exists.
No one-time restore plan is proposed.

## Four readiness models — only what was actually read

- `forecast_revenue_interval`: registry absent (current readback); artifact missing; approval missing; history unknown.
- `dealroom_avm`, `sitescore_propensity`, `heatzone_priority`: **not read currently**. Their 2026-07/08
  handbacks report 0 eligible labels and no approval; that is not extended to a current registry claim.

The 2026-07-25 risk acceptance deferred bindings for that deploy; it is not a current GO.

## Handback

Missing inputs and owners are listed in `missing_inputs`. The gap returns to
ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001 (authoritative history in the current dev project, legacy
custody decision — Human/Ops) and ODP-PRODUCTION-MODEL-REGISTRY-001 (fresh governed train, independent
approval, rollback target). Neither task is on the current board or archive; re-registering them is a
coordinator decision.

Status separation: code delivered = evidence + verifier only; real data available = no; model ready = no;
deployed / live validated / signed off = no.
