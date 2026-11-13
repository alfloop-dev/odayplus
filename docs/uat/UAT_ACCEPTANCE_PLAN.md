---
doc_id: ODP-R7-003-UAT-ACCEPTANCE-PLAN
title: ODay Plus UAT Acceptance Plan
version: 0.1.0
status: draft
owner: QA Lead / Product Owner
source_documents:
  - ODP-QA-06_UAT_AND_FORMAL_ACCEPTANCE_CHECKLIST.md
  - ODP-QA-03_END_TO_END_TEST_SCENARIOS.md
---

# ODay Plus UAT Acceptance Plan

## Purpose

This plan converts the QA-06 role checklist into a release-ready UAT packet.
UAT validates that each role can complete the business decision loop, understand
system recommendations, approve or reject high-risk actions, trace outcomes, and
export subsidy-ready evidence.

## Entry Criteria

| Area | Required state |
|---|---|
| Environment | Staging environment deployed with build version recorded (QA-06 §3). Integration environments may support engineering preparation but do not substitute for this full formal UAT. |
| Data | Deterministic UAT snapshot with at least 3 HeatZones, 20 Listings, 10 Candidate Sites, 5 SiteScore Reports, 10 Stores, 3 Red Alerts, 3 Interventions, 2 Price Plans, 2 Ad Campaigns, 2 AVM Reports, 2 NetPlan Scenarios, 3 Model Versions, and 20 Decision Logs (QA-06 §3) |
| Accounts | qa_admin, executive_user, expansion_user, site_reviewer, ops_manager, field_supervisor, marketing_user, pricing_user, finance_user, legal_user, data_scientist, mlops_user, franchisee_user, audit_user, readonly_user, no_permission_user |
| Evidence | Screenshot/video capture path, audit export path, and manifest owner assigned |
| Defects | Known issues reviewed; P0/P1 open defects block UAT start unless accepted by Product Owner and QA Lead |

## Role Scripts

QA-06 §4 defines the task and pass criteria for each of the 55 IDs below.
The existing plan owners (QA Lead / Product Owner) complete the step-by-step
execution and session arrangements in the release UAT packet.

| Role | Script IDs | Required sign-off evidence |
|---|---|---|
| Expansion user | UAT-EXP-001..005 | HeatZone screenshot, Listing import result, Candidate Site audit refs |
| SiteScore reviewer | UAT-SITE-001..005 | SiteScore report, GO/WAIT/REJECT decision log, export audit |
| Operations manager | UAT-OPS-001..005 | Four-light overview, root cause evidence, alert assignment, realization view |
| Field supervisor | UAT-FIELD-001..005 | Task scope view, intervention update, attachment, observation maturity |
| Marketing user | UAT-AD-001..005 | Campaign setup, control match, lift report, continue/stop decision |
| Pricing user | UAT-PRICE-001..005 | Demand simulation, hard constraint result, approval, rollback drill |
| Finance/legal | UAT-AVM-001..005 | Valuation card, Data Room checklist, finance approval, masked export |
| Executive user | UAT-NET-001..005 | Scenario setup, solver result, alternatives, NetPlan approval |
| AI/data user | UAT-ML-001..005 | Data Quality Center, Feature Registry, Model Card, Shadow/Canary, Rollback |
| Franchisee | UAT-FRAN-001..005 | Self-store isolation, recommendation view, feedback submission |
| Audit user | UAT-AUDIT-001..005 | Decision search, decision detail, evidence export, permission change trace |

### SiteScore Review

See QA-06 §4.2 (`UAT-SITE-001..005`) for the original review tasks and pass criteria.

### Franchisee

See QA-06 §4.10 (`UAT-FRAN-001..005`) for the original franchisee tasks and pass criteria.

## Exit Criteria

| Gate | Pass condition |
|---|---|
| Functional UAT | All 55 role-script IDs listed in Role Scripts executed and completed, each with a traceable result (QA-06 §8). A not-applicable mark does not count as completed for full formal UAT. |
| Defects | Zero P0/P1 UAT defects; accepted-with-conditions items have owner and due date |
| High-risk decisions | SiteScore GO, PriceOps approval, AdLift spend, AVM reserve price, NetPlan EXIT/MOVE, model release, data quality override, permission change, and sensitive export have audit evidence |
| Sign-off | Product Owner, QA Lead, Security Owner, SRE Owner, and Business Owner sign-off captured |
| Evidence traceability | Each sign-off row links to evidence_id, build_version, data_snapshot, model_version where applicable, and audit export reference |

## Sign-off Record

Use `docs/uat/UAT_SIGNOFF_TEMPLATE.yaml` for each session. Store completed
records with the release evidence package and list them in the manifest.
