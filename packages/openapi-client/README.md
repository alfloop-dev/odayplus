# @oday-plus/openapi-client

Hand-maintained typed client for the ODay Plus FastAPI backend
(`apps/api/oday_api`). No runtime dependencies beyond the platform `fetch`, so
it runs in Next.js **server components**, the Playwright test runner, or plain
Node. It must never be imported into a browser bundle — the web app calls it
only from server components under `apps/web/src/lib/api`.

## Usage

```ts
import { createOdpApiClient } from "@oday-plus/openapi-client";

// Reads ODP_API_BASE_URL / NEXT_PUBLIC_ODP_API_BASE_URL; returns null when
// unconfigured so callers can fall back to bundled fixtures.
const client = createOdpApiClient();
const cases = client ? await client.listAvmCases() : { items: [], count: 0 };
```

## Surface

| Method | Endpoint |
| --- | --- |
| `health()` | `GET /platform/health` |
| `listAvmCases()` | `GET /avm/cases` |
| `createAvmCase(input)` | `POST /avm/cases` |
| `listAuditEvents()` | `GET /audit/events` |
| `listInterventions()` | `GET /interventions` |
| `listAdliftReports()` | `GET /adlift/reports` |

`resolveApiBaseUrl(env)` exposes the env-resolution rule; `OdpApiError`
carries the HTTP status and correlation id of a failed call. All reads use
`cache: "no-store"` so a backend state change is always reflected in the UI.
