# Web → API Binding Matrix (ODP-PV-010)

Status: active · Phase: PV Product-Grade E2E Validation · Owner: Claude ·
Reviewer: Claude2

This matrix records how each web workspace (`apps/web/features/*`) sources its
data: from the live FastAPI backend (`apps/api/oday_api`) or from a bundled
fixture (`data.ts`) kept as **documented non-product fallback**. It is the
evidence artifact for acceptance criterion *"Remaining demo data is documented
as non-product fallback."*

## Binding architecture

- **`packages/openapi-client`** — hand-maintained typed client. Reads
  `ODP_API_BASE_URL` / `NEXT_PUBLIC_ODP_API_BASE_URL`; returns `null` when
  unconfigured so the product still renders. All reads use `cache: "no-store"`
  so a backend state change always reaches the UI.
- **`apps/web/src/lib/api/binding.ts`** — `loadApiBinding()` runs in server
  components and never throws. It classifies the result into one of four
  states and chooses the data source accordingly:

  | State | Meaning | Rendered source |
  | --- | --- | --- |
  | `ready` | API returned ≥1 row | **api** (live rows) |
  | `empty` | API reachable, store cold | fixture (documented fallback) |
  | `error` | API unreachable / non-2xx | fixture (documented fallback) |
  | `unconfigured` | no API base URL set | fixture (documented fallback) |

- **`apps/web/src/components/DataSourceBadge.tsx`** — renders the state with a
  test-addressable `data-source` (`api` | `fixture`) and `data-state`
  attribute, so the binding is observable in the UI and in E2E.

This covers the *loading / error / empty / stale / permission* contract:
`error`/`empty`/`unconfigured` are explicit states; permission masking and the
stale (`STALE`) data badge remain expressed by the existing per-feature
fixtures, which the bound regions sit alongside rather than replace.

## Feature matrix

| Web feature | Route(s) | Backend endpoint | Binding | Cold-start behavior | E2E |
| --- | --- | --- | --- | --- | --- |
| avm | `/avm`, `/w/dealroom/cases` | `GET`/`POST /avm/cases` | **BOUND** (cases list) | `empty` → fixture table | `e2e-api-bound-ui` proves create→render |
| audit | `/audit`, `/admin/audit` | `GET /audit/events` | **BOUND** (admin) | `empty` → fixture table | `e2e-api-bound-ui` proves event→render |
| intervention | `/interventions` | `GET /interventions` (+ workflow) | FIXTURE-FALLBACK | store cold; seed needs multi-step workflow | shell render only |
| adlift | `/adlift` | `GET /adlift/reports` | FIXTURE-FALLBACK | store cold; seed needs incrementality job | shell render only |
| expansion | `/expansion`, `/w/expansion/*` | `GET /listings/candidates`, `GET /sitescore/reports`, `GET /heatzones` | FIXTURE-FALLBACK | store cold; listing/geo pipeline + score job required | shell render only |
| operations | `/operations`, `/w/operations/*` | `GET /forecastops/forecasts`, `/alerts` | FIXTURE-FALLBACK | store cold; forecast job required | shell render only |
| netplan | `/netplan`, `/w/network/*` | `GET /heatzones/map` | FIXTURE-FALLBACK | heatzone store cold; scenario API not yet modeled | shell render only |
| priceops | `/pricing` | _none_ | NO-BACKEND | pricing endpoints not in API surface | shell render only |
| learninghub | `/learning`, `/w/ai/*` | _none_ | NO-BACKEND | model registry/release endpoints not in API surface | shell render only |

### Legend

- **BOUND** — a server component fetches live rows; when present they render
  with `data-source="api"`. Proven end-to-end.
- **FIXTURE-FALLBACK** — endpoint exists but its in-memory store starts cold
  and is seeded only through multi-step domain workflows. The workspace renders
  the bundled fixture as documented non-product fallback. Promoting these to
  BOUND is follow-up work: either seed the store on API startup or drive the
  workflow before read.
- **NO-BACKEND** — no backend endpoint exists yet; fixture is the only source.
  These are honest product gaps to be filled by future API tasks (pricing,
  learning hub / model registry).

## Why the two bound surfaces

- **AVM cases** is the cleanest create→list path: `POST /avm/cases` is a single
  request with a well-defined payload, and `GET /avm/cases` returns it. The
  E2E creates a uniquely-named case via the API and asserts that exact
  `store_id` / `case_id` appears in `/w/dealroom/cases` — proving a backend
  state change reaches the UI without touching `data.ts`.
- **Audit events** auto-populate from *every* backend write (`avm.case_created.v1`,
  `job.enqueue`, …). The admin audit workspace reads `GET /audit/events`,
  giving a second, write-driven proof of live backend state in the UI.

## Rich-view gap (intentional)

The fixture view types (e.g. `ValuationCase`, `AuditDecision`) are far richer
than the cold-start API shape — they encode the *completed* decision lifecycle
(three-lens valuation, finance approval, dataroom, 7-node timeline). A freshly
created backend row only carries its creation fields. The bound regions
therefore render the fields the API actually serves and keep the rich fixture
as the documented fallback, rather than fabricating lifecycle data the backend
has not produced. Closing this gap means running the full domain workflow (or
adding read models) so the API serves the lifecycle fields the rich views need.

## Reproduce

```bash
npm install
npm run test:e2e -- e2e-api-bound-ui.spec.ts   # boots API + web, proves binding
ODP_API_BASE_URL=http://127.0.0.1:8099 npm run build --workspace=@oday-plus/web
```
