# ODay Plus Provider Gateway — 歷史 wiring 紀錄

本文件記錄 `alfaloop-data-project` 在 2026-07-25 的歷史驗證結果，不代表目前
release 的啟用狀態。新的 runtime project 是 `odayplus-runtime-20260825`；舊專案的
gateway URL、credentials 與 allowlist 不得搬入或作為 fallback。

新 project 部署時所有第三方 provider 一律 disabled，啟用清單為空、Secret
Manager 不建立 provider credentials，runtime 也不得取得 provider egress。只有
逐來源完成授權、更新頻率、rate limit、kill switch 與 activation receipt 後，才可
由獨立 activation release 啟用。

- 歷史 Service URL: https://odp-provider-gateway-7sxbjoeozq-de.a.run.app
- Source: `services/provider-gateway/`

## Lane status

| provider_id | lane | upstream | status |
|---|---|---|---|
| `geocode.primary_api` | `/geocode` | Google Maps Geocoding API | HISTORICAL VERIFIED；現行 disabled |
| `poi.commercial_api` | `/poi` | Google Places Nearby Search | HISTORICAL VERIFIED；現行 disabled |
| `admin_boundary.official_dataset` | `/admin-boundary` | official TW 鄉鎮市區界 (NLSC/g0v) | HISTORICAL VERIFIED；現行 disabled |
| `listing.partner_feed` | `/listing` (planned) | proprietary partner feed | BLOCKED on real source (business input) |

## 歷史設定（不得套用至新 project）

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

歷史 live mode 曾使用以下 allowlist；新 project 不得設定：

    ODP_EXTERNAL_PROVIDER_MODE = live
    ODP_PRODUCTION_PROVIDER_IDS = listing.partner_feed,poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset

## 歷史 Secrets（僅存在舊 project，不得複製）

- `oday-plus-dev-google-geocode-key`   — Google Geocoding API key (restricted to geocoding-backend.googleapis.com)
- `oday-plus-dev-google-places-key`    — Google Places API key (restricted to places-backend/places.googleapis.com)
- `oday-plus-dev-geocode-gateway-key`  — gateway X-API-Key the oday-api clients present (shared across lanes)

## Verification (2026-07-25)

    POST /geocode  {"address":"台北市信義區信義路五段7號"}  (X-API-Key: <gateway key>)
    -> result: lat 25.033976, lng 121.5645389, confidence 0.98, precision rooftop,
       city 臺北市, district 信義區, request_id ChIJH56c2rarQjQRphD9gvC8BhI
    POST /geocode without X-API-Key -> 401
