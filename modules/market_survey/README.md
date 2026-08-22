# Market Survey Module (ODP-SURVEY-001)

Contract: `odayplus.survey-workflow.v2`
Release: `consumer-b.json` (ODayPlus Consumer)
Requires Contracts: `odayplus.market-data-facade.v2`, `emgi.field-survey.v1`
Provides Contracts: `odayplus.survey-workflow.v2`

## Architectural Invariants

1. **Evidence vs. Ground Truth**: Platform field-survey observations (`emgi.field-survey.v1`) ingested into odayplus are strictly evidentiary snapshots (`review_status: PENDING_REVIEW`, `promotion_status: NOT_PROMOTED`), not automatic ground truth.
2. **Reviewer Separation**: The submitter / field surveyor cannot approve or review their own survey submission (Segregation of Duties / Four-Eyes Principle).
3. **Correction & Resurvey**: Revisions link back to prior evidence via `replaces_survey_id` / `replaces_observation_id`, marking prior records as `is_superseded`.
4. **Expiry & SLA**: Uncompleted survey assignments past their `expires_at` deadline transition to `EXPIRED`.
5. **Governed Promotion**: Only `APPROVED`, non-superseded, non-retracted survey evidence can be promoted to canonical operational entities (such as Candidate Sites or Store Audits).
