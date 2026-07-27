# ODP-RUNTIME-PROVIDER-SELECTION-001 live provider probe

- Observed at: 2026-07-27T16:25:45Z
- GCP project / region: `alfaloop-data-project` / `asia-east1`
- Cloud Run service / revision: `oday-api` / `oday-api-probe8`
- Tagged URL: `https://probe8---oday-api-7sxbjoeozq-de.a.run.app`
- Traffic: 0% (acceptance-only candidate; production traffic was not changed)
- Container image: `asia-east1-docker.pkg.dev/alfaloop-data-project/oday/oday-api:dev-8ec12c02`
- Governed runtime binding: `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS=8`
- Health correlation ID: `072670ee-4d46-4a0c-aaf7-f08552a2c438`

## Selected-provider evidence

`GET /platform/health` reported the external-provider dependency as
`status=healthy`, `mode=live`, `configuration_valid=true`, and
`connectivity_healthy=true`.

| Provider | Connectivity | Auth | Response | Schema | HTTP | Latency |
| --- | --- | --- | --- | --- | --- | --- |
| `admin_boundary.official_dataset` | healthy | accepted | valid | valid | 200 | 129 ms |
| `geocode.primary_api` | healthy | accepted | valid | valid | 200 | 102 ms |
| `poi.commercial_api` | healthy | accepted | valid | valid | 200 | 948 ms |

The selected provider IDs were exactly
`admin_boundary.official_dataset`, `geocode.primary_api`, and
`poi.commercial_api`. `listing.partner_feed` was not selected and no listing
endpoint or credential was introduced.

The overall health endpoint returned HTTP 503 only because production model
aliases were unavailable. Its provider mode independently reported
`healthy=true` and `live=true`; the remaining model-registry blocker is outside
this task's provider-selection layer.

## Reproduction

```bash
gcloud run revisions describe oday-api-probe8 \
  --project alfaloop-data-project \
  --region asia-east1

curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://probe8---oday-api-7sxbjoeozq-de.a.run.app/platform/health
```
