# ODay Plus

ODay Plus is a monorepo for the full platform described by the ODP source
documents. The initial repository shape follows `ODP-SD-04` section 3 and the
R0 foundation scope in `ODP-OPS-01`.

## Repository Layout

```text
apps/
  api/          FastAPI core API and BFF entrypoint
  web/          OpsBoard web shell
  worker/       Event consumers and async workers
  scheduler/    Scheduled triggers and orchestration entrypoints
  cli/          Admin and migration utilities
modules/        Domain modules with domain/application/infrastructure/api/workers layers
shared/         Cross-cutting Python primitives, auth, audit, jobs, workflow, observability
packages/       TypeScript packages for generated clients, schemas, UI, and testkit
pipelines/      dbt, orchestration, and data quality assets
models/         Model code, validation, and model-card generation surfaces
solver/         Optimization models for pricing and network planning
infra/          Terraform, Docker, optional Kubernetes, and Cloud Build assets
tests/          Cross-module contract, integration, e2e, performance, and security tests
```

The domain modules currently scaffolded under `modules/` are:

- `integration`
- `external_data`
- `heatzone`
- `listing`
- `sitescore`
- `forecastops`
- `intervention`
- `priceops`
- `adlift`
- `avm`
- `netplan`
- `learninghub`
- `opsboard`

Each module keeps the same internal boundary:

```text
domain/
application/
infrastructure/
api/
workers/
tests/
README.md
```

Domain code should not import concrete infrastructure, cloud SDKs, HTTP
clients, or framework code. Cross-domain interaction should go through shared
DTOs, events, APIs, read-only model-ready views, or workflow orchestration.

## Local Setup

Python tooling is managed with `uv`:

```bash
uv sync
uv run pytest
```

Node workspaces are declared in the root `package.json`:

```bash
npm install
npm run lint --workspaces --if-present
```

The R0 skeleton intentionally keeps runtime dependencies light. Later tasks
will add FastAPI, Next.js, dbt, model, solver, and infrastructure dependencies
inside their owned surfaces.

## Foundation Acceptance

This scaffold provides stable landing zones for:

- backend API, async worker, scheduler, and CLI entrypoints under `apps/`
- OpsBoard frontend workspace under `apps/web`
- shared Python platform primitives under `shared/`
- generated/frontend package surfaces under `packages/`
- data, ML, solver, infrastructure, and cross-cutting test surfaces

The initial tests verify the expected folders and importable Python skeletons
without requiring external services.
