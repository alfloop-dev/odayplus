# Subsidy Evidence Matrix

ODP-R6-002 defines the audit evidence bundle used by OpsBoard reviewers when a
program, model release, or intervention decision needs subsidy-ready evidence.

| Requirement ID | Required evidence | Export source |
| --- | --- | --- |
| ELIGIBILITY | Applicant, store, model, or intervention eligibility is documented. | Decision card `subsidy_requirements`, eligibility evidence refs, audit event ids |
| DECISION | Human or policy decision rationale is captured with actor and timestamp. | Decision card owner, decided_at, rationale, policy refs |
| EFFECT | Outcome, effect, or model validation evidence is attached. | Intervention outcome labels, model validation runs, release cards |
| CONTROL | Approval, separation of duties, conflict, rollback, or audit control is present. | Approval cards, conflict controls, rollback criteria, audit events |
| TRACE | Source audit events and artifact references can trace the decision end to end. | Export audit event, card audit_event_ids, bundle checksum |

Every exported bundle contains:

- `decision_cards`: reviewer-facing cards with owner, rationale, evidence refs,
  policy refs, input snapshot, model refs, lifecycle refs, metrics, readiness,
  and a per-card SHA-256 `card_hash`.
- `audit_events`: canonical platform audit events filtered by correlation id and
  export period.
- `subsidy_matrix`: one row per requirement with `READY` or `MISSING` status.
- `bundle_checksum`: SHA-256 checksum over the canonical bundle payload.

## QA-07 Module Mapping

| QA-07 ID | Module | Audit evidence source | Bundle fields |
| --- | --- | --- | --- |
| AUD-MOD-001 | SiteScore | Score report, GO/WAIT/REJECT decision, approval audit | `module`, `input_snapshot_id`, `model_refs`, `policy_refs`, `lifecycle_refs.approval` |
| AUD-MOD-002 | ForecastOps | Forecast alert, model validation, intervention trigger | `model_refs`, `data_snapshot_id`, `feature_version`, `lifecycle_refs.prediction` |
| AUD-MOD-003 | DealRoomAVM | Valuation card, finance/legal approval | `decision_type`, `owner`, `rationale`, `audit_event_ids`, `card_hash` |
| AUD-MOD-004 | NetPlan | Scenario comparison, solver log, management approval | `evidence_refs`, `controls`, `lifecycle_refs.recommendation`, `lifecycle_refs.approval` |
| AUD-MOD-005 | OpsBoard | Decision log export and monthly audit report | `audit_events`, `subsidy_matrix`, `bundle_checksum`, export audit event |
| AUD-MOD-006 | Integration / Data | Data lineage, mapping report, data quality report | `input_snapshot_id`, `data_snapshot_id`, `evidence_refs` |
| AUD-MOD-007 | Learning Hub | Model registry, model card, release approval | `model_refs`, `policy_refs`, `feature_version`, `lifecycle_refs.execution` |

Decision evidence must keep prediction, recommendation, approval, execution,
and outcome references separate in `lifecycle_refs`; a single screenshot or
summary is not sufficient for subsidy evidence readiness.

Sensitive or restricted exports must provide `export_scope` and are themselves
recorded as `audit.evidence_export.v1` audit events.

The export is intentionally evidence-only. It records whether subsidy evidence
is ready; it does not approve subsidy payment or override the source decision.
