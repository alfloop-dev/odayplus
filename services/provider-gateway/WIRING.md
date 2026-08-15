# ODay Plus Provider Gateway — live wiring record

`odp-provider-gateway` is a thin fail-closed adapter (Cloud Run, asia-east1,
project `alfaloop-data-project`) that translates real upstream providers into
the ODay Plus canonical provider contracts. The oday-api live provider client
calls it; the real upstream credentials stay inside the gateway.

- Service URL: https://odp-provider-gateway-7sxbjoeozq-de.a.run.app
- Source: `services/provider-gateway/`

## Lane status

| provider_id | lane | upstream | status |
|---|---|---|---|
| `geocode.primary_api` | `/geocode` | Google Maps Geocoding API | LIVE (verified 2026-07-25) |
| `poi.commercial_api` | `/poi` | Google Places Nearby Search | LIVE (119 POIs, 0 quarantined, verified 2026-07-25) |
| `admin_boundary.official_dataset` | `/admin-boundary` | official TW 鄉鎮市區界 (NLSC/g0v) | LIVE (399 records, 0 quarantined, verified 2026-07-25) |
| `listing.partner_feed` | `/listing` (planned) | proprietary partner feed | BLOCKED on real source (business input) |

## oday-api env to apply at final deploy (geocode lane)

    ODP_GEOCODE_PROVIDER_URL         = https://odp-provider-gateway-7sxbjoeozq-de.a.run.app/geocode
    ODP_GEOCODE_PROVIDER_API_KEY     = <secret: oday-plus-dev-geocode-gateway-key:latest>
    ODP_GEOCODE_PROVIDER_AUTH_STATUS = active

### poi lane

    ODP_POI_PROVIDER_URL         = https://odp-provider-gateway-7sxbjoeozq-de.a.run.app/poi
    ODP_POI_PROVIDER_API_KEY     = <secret: oday-plus-dev-geocode-gateway-key:latest>  (shared gateway key)
    ODP_POI_PROVIDER_AUTH_STATUS = active
    # snapshot scope is configured on the gateway: POI_AREAS (lat,lng,radius;...) + POI_TYPES
    # default scope: 3 Taipei areas x {convenience_store, supermarket}

### admin_boundary lane

    ODP_ADMIN_BOUNDARY_PROVIDER_URL         = https://odp-provider-gateway-7sxbjoeozq-de.a.run.app/admin-boundary
    ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN       = <secret: oday-plus-dev-geocode-gateway-key:latest>  (bearer, shared gateway key)
    ODP_ADMIN_BOUNDARY_PROVIDER_AUTH_STATUS = active
    # dataset embedded in the gateway image (services/provider-gateway/admin_boundary_data.json):
    # 22 縣市 + 377 鄉鎮市區, centroids computed from official NLSC/g0v twTown geometry

Live mode also requires the provider allowlist:

    ODP_EXTERNAL_PROVIDER_MODE = live
    ODP_PRODUCTION_PROVIDER_IDS = listing.partner_feed,poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset

## Secrets (Secret Manager, project alfaloop-data-project)

- `oday-plus-dev-google-geocode-key`   — Google Geocoding API key (restricted to geocoding-backend.googleapis.com)
- `oday-plus-dev-google-places-key`    — Google Places API key (restricted to places-backend/places.googleapis.com)
- `oday-plus-dev-geocode-gateway-key`  — gateway X-API-Key the oday-api clients present (shared across lanes)

## Verification (2026-07-25)

    POST /geocode  {"address":"台北市信義區信義路五段7號"}  (X-API-Key: <gateway key>)
    -> result: lat 25.033976, lng 121.5645389, confidence 0.98, precision rooftop,
       city 臺北市, district 信義區, request_id ChIJH56c2rarQjQRphD9gvC8BhI
    POST /geocode without X-API-Key -> 401
