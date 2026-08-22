# Market Intelligence BFF OpenAPI Specification

Contract: `odayplus.market-intelligence-api.v2`
Version: `2.0.0`
Part of Task: `ODP-API-001`

## Purpose
This package provides the canonical OpenAPI 3.0 specification and JSON schemas for the Market Intelligence BFF API (v2).

## Resources & Operations
- **Market Cells** (`GET /cells/{cell_id}`, `GET /cells`)
- **Site Market Context** (`GET /sites/{site_id}/context`, `POST /sites/context/batch`)
- **Candidate Compare** (`GET /compare`, `POST /compare`)
- **Evidence & Lineage** (`GET /evidence/{site_id}`, `GET /evidence/cells/{cell_id}`)
- **Coverage Surface** (`GET /coverage`)
- **Data Gaps** (`GET /data-gaps`, `GET /data-gaps/{gap_id}`)
- **Data Acquisition Plans** (`GET /acquisition-plans`, `POST /acquisition-plans`, `GET /acquisition-plans/{plan_id}`)
- **Health & Diagnostics** (`GET /health`, `GET /diagnostics`)

## Product Authorization & Tenant Invariants
- Enforces strict tenant isolation across all endpoints.
- Enforces RBAC permissions through ODayPlus product authorization.
- Explicit readiness & missingness: Missing domains are never rendered as zero.
