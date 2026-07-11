# Product Branch Baseline

Task: ODP-PV-001
Generated: 2026-06-28
Owner: Codex2
Reviewer: Codex

## Decision

Use `origin/dev` as the selected product-validation target branch.

`origin/dev` contains the ODP-PV-000 audit evidence and has the same product
subtree as `origin/main` and this task branch for the product-owned directories:

- `apps/`
- `modules/`
- `packages/`
- `shared/`
- `infra/`
- `tests/`

The task branch `task/ODP-PV-001` is currently checked out at `f454581`, which
matches `origin/main`. Its product directories match `origin/dev`; the only
known branch-level delta from `origin/main` to `origin/dev` is ODP-PV-000
evidence and task archive documentation.

## Clean Checkout Product Inventory

The selected product tree is not an orchestrator-only checkout. It contains:

| Area | Baseline contents |
|---|---|
| `apps/` | FastAPI API/BFF, Next.js OpsBoard web app, worker, scheduler, and CLI entrypoints |
| `modules/` | 13 product modules: integration, external_data, heatzone, listing, sitescore, forecastops, intervention, priceops, adlift, avm, netplan, learninghub, opsboard |
| `packages/` | TypeScript UI, design tokens, domain types, schemas, testkit, and openapi-client workspaces |
| `shared/` | Python auth, audit, jobs, workflow, domain, application, observability, and infrastructure primitives |
| `infra/` | Terraform, Docker, DB migrations, monitoring, optional Kubernetes, and Cloud Build assets |
| `tests/` | contract, data, integration, e2e, ops, performance, reliability, security, and smoke tests |

Observed file counts in the product directories:

| Directory | Files |
|---|---:|
| `apps/` | 126 |
| `modules/` | 303 |
| `packages/` | 59 |
| `shared/` | 51 |
| `infra/` | 24 |
| `tests/` | 93 |

## Product Subtree Hashes

These subtree hashes were identical for `HEAD`, `origin/main`, `origin/dev`,
and local `dev` at the time of this baseline:

| Directory | Tree hash |
|---|---|
| `apps/` | `b3e8df0942d060ed3f3422e35c04ac15517004e4` |
| `modules/` | `c589defac32b74bad2dd2b2bbaef0e181bd34d94` |
| `packages/` | `9a41eaadf10d4e4b3617a1cfb8397f23e65b5095` |
| `shared/` | `192d6d0d54b84d2cf7e07941a67042be968a6eb4` |
| `infra/` | `d4bd02afb3f2397ec53295b107f1b4a4f12a1ab3` |
| `tests/` | `1bbab2419867a15d99a5ba7e85068b9977f3c8a5` |

## Existing Task Branch Work

The remaining remote `origin/task/ODP-*` branches were checked against
`origin/dev` for product-directory changes:

```bash
git diff --name-only origin/dev...origin/task/<task> -- \
  apps modules packages shared infra tests
```

No remote `origin/task/ODP-*` branch produced product-directory output. For
this baseline, existing product task branch work is therefore considered merged
into the selected product tree or explicitly non-delta for the product
directories.

## Root Commands

Root Python setup and tests:

```bash
uv sync
make bootstrap
uv run pytest -m "not requires_live_env"
```

Root JavaScript install and typecheck:

```bash
npm ci
npm run typecheck --workspaces --if-present
```

The root `Makefile` also provides noninteractive aggregate commands:

```bash
make lint
make test
make smoke
make node-check
make ci
```

`make node-check` runs `npm ci`, workspace lint, typecheck, and tests when
`package-lock.json` is present.

## Verification

Commands run for this baseline:

```bash
AI_NAME=Codex2 python3 scripts/ai_status.py start ODP-PV-001 \
  "Creating product branch baseline evidence from ODP-PV-000 branch truth"
uv run pytest -m "not requires_live_env"
make bootstrap && uv run pytest -m "not requires_live_env"
npm ci && npm run typecheck --workspaces --if-present
git diff --name-only origin/dev...origin/task/<task> -- \
  apps modules packages shared infra tests
```

Observed results:

- Initial `uv run pytest -m "not requires_live_env"`: 664 passed, 10
  deselected, 5 failed. The failures were caused by the missing ignored local
  `.orchestrator/config.json` and resulting default target-branch assumptions.
- After `make bootstrap`: `uv run pytest -m "not requires_live_env"` passed
  with 669 passed, 10 deselected, and 6 warnings.
- `npm ci && npm run typecheck --workspaces --if-present` passed. `npm ci`
  reported 7 audit findings: 1 moderate and 6 high.
- Remote ODP task-branch product delta scan produced no product-directory
  output.

## Non-Claims

This baseline does not claim production readiness. It only establishes that
the selected branch has a coherent, runnable product monorepo checkout.

Do not treat this evidence as proof of:

- production persistence or durable audit retention
- live external provider ingestion
- production map/geocoder readiness
- approved model registry or solver runtime operations
- dependency/security remediation
- formal UAT or release signoff

Those gaps remain governed by the ODP-PV-000 current-state audit and the
follow-on product-validation lanes.
