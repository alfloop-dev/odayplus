# ODP-MOD-11 OpsBoard

## Purpose

OpsBoard is the operator console module for closing daily operational loops
across Store Ops, Growth, Network, and Governance. It converts alerts and
recommendations into auditable operator decisions, follow-up tasks, and
evidence records.

## Module Responsibilities

- Render a role-aware `/operator` React console with Today, Store Ops, Growth,
  Network, and Governance workspaces.
- Serve operator read models for Today queue, issues, approvals,
  notifications, tasks, and search.
- Persist workflow transitions for issue triage, assignment, actions, field
  reports, outcomes, escalation, and privacy-scoped evidence purpose.
- Persist approval decisions with a server-side reason gate for returned or
  rejected decisions.
- Record platform audit events for operator writes and maintain UI audit rows
  for governance traceability.
- Keep workflow writes idempotent by honoring `Idempotency-Key` and correlation
  metadata.

## API Contract

Base path: `/api/v1/operator`

| Endpoint | Verb | Purpose |
|---|---:|---|
| `/bootstrap` | GET | Full operator console state for initial React hydration |
| `/today` | GET | Today queue, KPI, decision rail, audit feed, notification, task view |
| `/issues` | GET | Store Ops issue list |
| `/approvals` | GET | Governance approval queue, decision log, audit rows |
| `/notifications` | GET | Notification read model |
| `/tasks` | GET | Follow-up task read model |
| `/search?q=` | GET | Search across queue, approval, and audit work |
| `/issues/{issue_id}/{action}` | POST | Store Ops transition write |
| `/approvals/{approval_id}/decision` | POST | Governance/Network approval decision write |
| `/evidence/{evidence_id}/purpose` | POST | Privacy-scoped evidence access purpose write |

## Authorization And Persistence

Operator reads require an authenticated role with `intervention:view`.
Workflow writes require `intervention:create`; approval writes require
`intervention:approve`. Denials are recorded by the shared authorization audit
policy.

Default local/test state is in memory. In durable product mode, the API injects
a `SqliteDocumentStore` into `OperatorStateStore`, which saves the operator
state under the `opsboard.operator` collection after each write.

## Frontend Integration

`OperatorConsole.tsx` reads `/bootstrap` on load, sends security and
correlation headers on writes, refreshes state after workflow changes, and
renders live notifications, approval counts, search results, and task follow-up
counts. Existing Store Ops, Network, Growth, and Governance workspaces keep
their established selectors for E2E continuity.
