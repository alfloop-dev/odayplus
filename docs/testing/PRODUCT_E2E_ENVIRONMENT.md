# Product E2E Environment

ODP-PV-004 adds a deterministic product E2E stack that can start from a clean checkout without production secrets.

## What Starts

- FastAPI product API on `ODP_E2E_API_PORT` with `ODP_PERSISTENCE=durable`.
- Next.js web app on `ODP_E2E_WEB_PORT`, bound to the API through `ODP_API_BASE_URL`.
- A file-backed SQLite database in the `e2e-db` Docker volume.
- A mock storage volume mounted at `/storage`.
- A static external source stub serving `tests/fixtures/source_data`.
- A lightweight worker/scheduler heartbeat writing JSONL diagnostics to mock storage.

## Run

```bash
cp .env.e2e.example .env.e2e
scripts/e2e/run_product_e2e.sh
```

The runner builds the stack, waits for API/source readiness, seeds deterministic data, runs the API-bound UI, map, and product environment E2E specs, then writes diagnostics under `.odp_data/e2e-diagnostics`.

Set `ODP_E2E_KEEP_STACK=1` to leave containers running for inspection.

## Seeded Data

`scripts/e2e/seed_product_e2e_data.py` creates:

- an AVM case for `e2e-store-taipei-001`;
- a HeatZone scoring job from deterministic H3 features;
- a scheduler job queue item;
- an audit evidence export for `product-e2e-subsidy`;
- a retained evidence bundle tied to correlation id `corr-product-e2e-seed-001`.

The seed script is idempotent for the core API writes and records a summary in `seed-summary.json`.

## Diagnostics

The runner captures:

- `seed-summary.json`;
- `compose-ps.txt`;
- `compose-tail.log`.

The API database is durable for the lifetime of the Docker volume, so restart checks can be done with:

```bash
docker compose -p oday-plus-e2e -f infra/docker/docker-compose.e2e.yml restart api web
curl http://127.0.0.1:8099/avm/cases
```
