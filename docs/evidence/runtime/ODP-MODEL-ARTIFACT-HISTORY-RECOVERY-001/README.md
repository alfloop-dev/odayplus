# ODP-MODEL-ARTIFACT-HISTORY-RECOVERY-001 — bounded recovery of the Forecast model/history handoff

Baseline: `origin/dev` `1b14b276447a7778f1dad1045ced1a5bd6abe01e`. Read-only; no cloud write,
no training, no alias, no gate change. Machine-readable result: `recovery_evidence.json`,
checked by `verify_recovery_evidence.py` / `test_recovery_evidence.py`. Every cited Git blob is
saved verbatim under `sources/` and bound by sha256 (and compared with the Git object when the
commit is present locally); registry, history and board claims are parsed from the saved receipts.

## Result

**Within the observed scope, no approved Forecast artifact was located; this task restored nothing.**
That is a scoped finding, not proof of absence:

- Observed: dev MLflow `forecast_revenue_interval` 404 and empty dev artifact bucket (saved receipt,
  2026-10-02T19:17Z / 19:20Z); the located legacy training run `2dzlg` failed closed before
  registration; the two original task branches carry no approval file.
- Not observed: legacy bucket `gs://alfaloop-data-project-oday-plus-model-artifacts/`
  (`access_unknown`), legacy MLflow `oday-mlflow-7sxbjoeozq-de` (not queried), current dev DB counts
  (unknown), approvals held outside the repo/receipts, and the current state of the other three models.
  Whether a legacy version or approval exists there remains open.

| Original task | Branch head | Merged | Terminal evidence (saved under `sources/`) |
|---|---|---|---|
| ODP-PRODUCTION-MODEL-REGISTRY-001 | `950b852c` (2026-07-28) | no, no PR (sidecar #657 only) | legacy inventory `pmb8m`: 1 303 eligible rows, 2026-06-19..22; training `2dzlg` exit 2, `release_mutation_completed=false` |
| ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001 | `c1af59a9` (2026-07-29) | no, no PR (sidecar #654 only) | backfill stopped on reauth (`invalid_rapt`); §12 After state is a placeholder on that head |

The failed run bounds only that run. The 4-day legacy count is not used as the current count.

`product_ops/modeling/release.py` exposes only `inventory`, `train`, `promote`; there is no
import-existing-artifact subcommand. No one-time restore plan is proposed because nothing approved
was located; the legacy scope must first be read under explicit authorization.

## Four readiness models — only what was actually read

- `forecast_revenue_interval`: registry absent in dev (receipt-bound); artifact_missing and
  approval_missing scoped to the observed scope with the unobserved legacy scope listed; history unknown.
- `dealroom_avm`, `sitescore_propensity`, `heatzone_priority`: registry, artifact and approval
  **not read currently**. Their 2026-07/08 handbacks (facts bound to saved copies) report 0 eligible
  labels; that is not extended to a current claim.

The 2026-07-25 risk acceptance deferred bindings for that deploy; it is not a current GO.

## Board state and handback

- Historical observation (saved receipt, 2026-10-02T19:20:19Z): neither original task id was on the
  board or archive.
- Current (board readback `board-readback-20261003.json`, 2026-10-03T00:06:50Z): both re-registered —
  BACKFILL at 23:57:22Z and REGISTRY at 23:57:30Z, `todo`, `non_dispatchable`, owner Claude2 /
  reviewer Codex, pending real inputs and operation admission.

Missing inputs and owners are in `missing_inputs`, handed back to those two holders (history,
legacy custody decision, authorized legacy registry/bucket readback, fresh governed train, promotion
approval) and to the three outcome backfill tasks (current readback of the other models).

Status separation: code delivered = evidence + verifier only; historical state bytes recovered = no;
cloud authority = none; real data available = no; model ready = no; deployed / live validated /
signed off = no.
