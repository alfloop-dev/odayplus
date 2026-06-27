# Local Developer Setup

ODay Plus uses `uv` for Python tooling. Node workspaces are scaffolded, but
runtime frontend dependencies are intentionally not installed until the owned
frontend tasks add a lockfile and package dependencies.

## Prerequisites

- Python 3.12
- `uv`
- Node.js 20, for future workspace checks
- `make`

## First Run

```bash
uv sync
make bootstrap
```

`make bootstrap` creates `.orchestrator/config.json` from
`.orchestrator/config.example.json` when the ignored local config file is
missing. Existing local config is left unchanged.

## Daily Commands

```bash
make lint
make test
make smoke
make ci
```

- `make lint` runs `uv run ruff check .`.
- `make test` runs `uv run pytest -m "not requires_live_env"`.
- `make smoke` runs the fast foundation smoke suite under `tests/smoke/`.
- `make ci` runs bootstrap, lint, CI-safe tests, smoke tests, and Node
  workspace checks when `package-lock.json` exists.

## Node Workspaces

The root `package.json` declares workspaces for `apps/web` and `packages/*`.
Until a committed `package-lock.json` exists, `make node-check` skips Node
workspace commands so the foundation CI does not fail on placeholder frontend
scripts. Once frontend dependencies are added, commit the lockfile and the same
Makefile target will run `npm ci`, workspace lint, typecheck, and tests.

## Live Environment Tests

Tests marked `requires_live_env` depend on machine-specific services, local
paths, or credentials. They are excluded from the default CI baseline:

```bash
uv run pytest -m "requires_live_env"
```
