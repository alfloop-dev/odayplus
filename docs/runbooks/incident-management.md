# Incident Management Runbook

Source baseline: `ODP-OPS-05_INCIDENT_BACKUP_AND_RECOVERY_MANUAL` §2–§7, §13, §14,
`ODP-SD-10_EXCEPTION_HANDLING_AND_RELIABILITY_DESIGN` §11.
Owner: SRE / Security Owner / Project Manager.

## 1. Severity

| Severity | Impact | Example | Notify |
|---|---|---|---|
| P0 | Security, data integrity, or full-platform outage | permission bypass, audit loss, prod DB corruption | all owners immediately |
| P1 | Core business flow unusable | SiteScore / ForecastOps down | domain owner |
| P2 | Important feature partially down | listing import fail, AVM report delay | relevant owner |
| P3 | Non-core / deferrable | report format error | issue tracking |
| P4 | Improvement | usability | backlog |

## 2. Roles

Incident Commander, Tech Lead, Data Lead, ML Lead, Security Lead, Communications
Lead, Scribe, Business Owner, Support Lead. **P0/P1 must name an Incident
Commander.**

## 3. Lifecycle

```
Detected → Triage → Declare → Mitigate → Recover → Validate → Communicate → Close → Postmortem → Preventive actions
```

### Declare template

```yaml
incident_id:
severity:
title:
detected_at:
declared_at:
declared_by:
incident_commander:
affected_services:
affected_users:
affected_data:
initial_hypothesis:
communication_channel:
```

## 4. Response SLA

| Severity | Initial response | Status update | Recovery target |
|---|---|---|---|
| P0 | 15 min | every 30 min | per DR / RTO |
| P1 | 30 min | every 60 min | same-day or workaround |
| P2 | 4 h | daily | scheduled fix |
| P3 | 1 business day | per issue | backlog |
| P4 | scheduled | per issue | backlog |

## 5. Communication

Use the internal update, external/business update, and close-message templates
from ODP-OPS-05 §7. Always state data and decision-record status in user-facing
messages.

## 6. Service-interruption compensation evidence

If the SLA includes compensation, retain: `incident_id, start_time, end_time,
unavailable_minutes, affected_services, system_logs, monitoring_evidence,
calculation, compensation_level, approved_by`.

## 7. Postmortem

P0/P1 require a postmortem; P2 as needed.

```yaml
incident_id:
title:
severity:
date:
duration:
impact:
timeline:
root_cause:
contributing_factors:
what_went_well:
what_went_wrong:
detection_gap:
response_gap:
data_impact:
model_impact:
decision_impact:
customer_impact:
corrective_actions:
preventive_actions:
owners:
due_dates:
```

Principles: blameless, system-focused, every action item has an owner and a due
date and is tracked to completion. Retain postmortem evidence when subsidy audit
applies.

## Acceptance

- Incident levels, roles, and lifecycle are explicit.
- P0/P1 require an Incident Commander and a postmortem.
- Compensation evidence and postmortem outputs are producible.
