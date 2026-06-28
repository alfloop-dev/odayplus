# Docker

Local and deployable container definitions.

## Local Stack

```bash
docker compose -f infra/docker/docker-compose.yml up --build
```

The stack starts:

| Service | Purpose |
|---|---|
| `postgres` | PostgreSQL 16 + PostGIS baseline for local migration testing. |
| `api` | FastAPI application container listening on `:8080`. |

The compose file uses only local credentials. Cloud environments should inject
`ODAY_DATABASE_URL` from the deployment platform secret manager.
