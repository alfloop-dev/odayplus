"""ODay Plus external-provider gateway.

Thin fail-closed adapter that translates real upstream providers into the
ODay Plus canonical provider contracts. The oday-api live provider clients call
this service; the real upstream credentials stay inside the gateway.

Lanes:
  POST /geocode          geocode.primary_api        <- Google Maps Geocoding API
  GET  /poi              poi.commercial_api snapshot <- Google Places Nearby Search
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="odp-provider-gateway", version="1.2.0")

_ADMIN_BOUNDARY_PATH = pathlib.Path(__file__).with_name("admin_boundary_data.json")
try:
    _ADMIN_BOUNDARY = json.loads(_ADMIN_BOUNDARY_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    _ADMIN_BOUNDARY = {"records": []}

GOOGLE_GEOCODE_KEY = os.environ.get("GOOGLE_GEOCODE_KEY", "").strip()
GOOGLE_PLACES_KEY = os.environ.get("GOOGLE_PLACES_KEY", "").strip()
GATEWAY_KEY = (
    os.environ.get("GATEWAY_API_KEY", "").strip()
    or os.environ.get("GEOCODE_GATEWAY_KEY", "").strip()
)

# --- POI snapshot scope (real, bounded, configurable) ----------------------
# Each area is "lat,lng,radius_m"; snapshot = union of (area x type) queries.
_DEFAULT_POI_AREAS = "25.0330,121.5654,1200;25.0478,121.5170,1200;25.0417,121.5436,1200"
_DEFAULT_POI_TYPES = "convenience_store,supermarket"
POI_AREAS = os.environ.get("POI_AREAS", _DEFAULT_POI_AREAS).strip()
POI_TYPES = os.environ.get("POI_TYPES", _DEFAULT_POI_TYPES).strip()

# Google location_type -> canonical geocode precision / confidence.
_PRECISION = {
    "ROOFTOP": "rooftop",
    "RANGE_INTERPOLATED": "interpolated",
    "GEOMETRIC_CENTER": "street",
    "APPROXIMATE": "approximate",
}
_CONFIDENCE = {
    "ROOFTOP": 0.98,
    "RANGE_INTERPOLATED": 0.85,
    "GEOMETRIC_CENTER": 0.6,
    "APPROXIMATE": 0.4,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def _require_gateway_key(request: Request) -> None:
    if not GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="gateway unconfigured")
    if request.headers.get("X-API-Key", "") != GATEWAY_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


def _require_gateway_bearer(request: Request) -> None:
    if not GATEWAY_KEY:
        raise HTTPException(status_code=503, detail="gateway unconfigured")
    if request.headers.get("Authorization", "") != f"Bearer {GATEWAY_KEY}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _checksummed_snapshot(payload: dict) -> Response:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Response(
        content=body,
        media_type="application/json",
        headers={"X-Content-SHA256": hashlib.sha256(body).hexdigest()},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "geocode": "ready" if (GOOGLE_GEOCODE_KEY and GATEWAY_KEY) else "unconfigured",
        "poi": "ready" if (GOOGLE_PLACES_KEY and GATEWAY_KEY) else "unconfigured",
        "admin_boundary": f"ready:{len(_ADMIN_BOUNDARY.get('records', []))}"
        if (_ADMIN_BOUNDARY.get("records") and GATEWAY_KEY)
        else "unconfigured",
    }


# --- geocode lane ----------------------------------------------------------
@app.post("/geocode")
async def geocode(request: Request) -> dict:
    if not GOOGLE_GEOCODE_KEY:
        raise HTTPException(status_code=503, detail="geocode gateway unconfigured")
    _require_gateway_key(request)
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    address = str((body or {}).get("address", "")).strip()
    if not address:
        raise HTTPException(status_code=400, detail="address required")

    params = urllib.parse.urlencode(
        {"address": address, "key": GOOGLE_GEOCODE_KEY, "language": "zh-TW", "region": "tw"}
    )
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            gd = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"google http {exc.code}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise HTTPException(status_code=504, detail="google timeout") from exc

    status = gd.get("status")
    if status == "ZERO_RESULTS" or not gd.get("results"):
        return {}
    if status != "OK":
        raise HTTPException(status_code=502, detail=f"google status {status}")

    r = gd["results"][0]
    loc = r["geometry"]["location"]
    loc_type = r["geometry"].get("location_type", "APPROXIMATE")
    comps = {
        c["types"][0]: c["long_name"]
        for c in r.get("address_components", [])
        if c.get("types")
    }
    return {
        "result": {
            "latitude": loc["lat"],
            "longitude": loc["lng"],
            "confidence": _CONFIDENCE.get(loc_type, 0.4),
            "precision": _PRECISION.get(loc_type, "approximate"),
            "provider_id": "geocode.primary_api",
            "city": comps.get("administrative_area_level_1", ""),
            "district": comps.get("administrative_area_level_2", ""),
        },
        "request_id": str(r.get("place_id") or correlation_id),
        "observed_at": _now_iso(),
        "upstream": "google-maps-geocoding",
    }


# --- poi lane (checksummed snapshot) ---------------------------------------
def _places_nearby(lat: str, lng: str, radius: str, place_type: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "location": f"{lat},{lng}",
            "radius": radius,
            "type": place_type,
            "language": "zh-TW",
            "key": GOOGLE_PLACES_KEY,
        }
    )
    url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=12) as response:
        gd = json.loads(response.read().decode("utf-8"))
    if gd.get("status") not in {"OK", "ZERO_RESULTS"}:
        raise HTTPException(status_code=502, detail=f"places status {gd.get('status')}")
    return gd.get("results", [])


@app.get("/poi")
def poi(request: Request) -> Response:
    if not GOOGLE_PLACES_KEY:
        raise HTTPException(status_code=503, detail="poi gateway unconfigured")
    _require_gateway_key(request)

    observed_at = _now_iso()
    records: list[dict] = []
    seen: set[str] = set()
    for area in [a.strip() for a in POI_AREAS.split(";") if a.strip()]:
        try:
            lat, lng, radius = [p.strip() for p in area.split(",")]
        except ValueError:
            continue
        for place_type in [t.strip() for t in POI_TYPES.split(",") if t.strip()]:
            for place in _places_nearby(lat, lng, radius, place_type):
                pid = str(place.get("place_id") or "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                loc = place.get("geometry", {}).get("location", {})
                lat_v = loc.get("lat")
                lng_v = loc.get("lng")
                if lat_v is None or lng_v is None:
                    continue
                operational = place.get("business_status", "OPERATIONAL") == "OPERATIONAL"
                types = place.get("types", [])
                records.append(
                    {
                        "source_poi_id": pid,
                        "poi_name": str(place.get("name", "")),
                        "poi_category": place_type,
                        "poi_subcategory": str(types[0]) if types else "",
                        "address_raw": str(place.get("vicinity", "")),
                        "latitude": float(lat_v),
                        "longitude": float(lng_v),
                        "status": "active" if operational else "inactive",
                        "confidence": 0.9,
                        "observed_at": observed_at,
                        "event_time": observed_at,
                    }
                )

    scope_digest = hashlib.sha256(f"{POI_AREAS}|{POI_TYPES}".encode()).hexdigest()[:12]
    snapshot_id = f"poi-{time.strftime('%Y%m%d', time.gmtime())}-{scope_digest}"
    for rec in records:
        rec["snapshot_id"] = snapshot_id

    payload = {
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
        "records": records,
        "next_page_token": "",
        "upstream": "google-places-nearbysearch",
    }
    return _checksummed_snapshot(payload)


# --- admin_boundary lane (checksummed snapshot, official TW dataset) --------
@app.get("/admin-boundary")
def admin_boundary(request: Request) -> Response:
    _require_gateway_bearer(request)
    observed_at = _now_iso()
    base = _ADMIN_BOUNDARY.get("records", [])
    scope_digest = hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    snapshot_id = f"adminboundary-{time.strftime('%Y%m%d', time.gmtime())}-{scope_digest}"
    records = []
    for rec in base:
        row = dict(rec)
        row["snapshot_id"] = snapshot_id
        row["observed_at"] = observed_at
        row["event_time"] = observed_at
        row["effective_date"] = time.strftime("%Y-%m-%d", time.gmtime())
        records.append(row)
    payload = {
        "snapshot_id": snapshot_id,
        "observed_at": observed_at,
        "records": records,
        "next_page_token": "",
        "upstream": _ADMIN_BOUNDARY.get("source", "official-tw-admin-boundary"),
    }
    return _checksummed_snapshot(payload)
