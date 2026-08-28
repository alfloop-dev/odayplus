# Docker

Local and deployable container definitions.

## Local Stack

```bash
docker compose up --build
```

For E2E test stack:

```bash
docker compose -f infra/docker/docker-compose.e2e.yml up --build
```

## Container Definitions

| Dockerfile | Purpose |
|---|---|
| `api.Dockerfile` | FastAPI backend service (port 8000). |
| `web.Dockerfile` | Next.js frontend application (port 3000). |
| `worker.Dockerfile` | Asynchronous worker process. |
| `scheduler.Dockerfile` | Background scheduler process. |
| `data-platform.Dockerfile` | Data platform and EMGI runtime. |
| `docker-compose.e2e.yml` | Deterministic E2E testing compose stack. |

Environment configurations and secrets should be injected via environment variables or secret manager.
