---
doc_id: ODP-EMGI-SOURCE-MATRIX-003-1
title: EMGI v0.3 Source and OSS Decision Matrix — Part 1
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 1
part_count: 2
source_document: ODP-EMGI-SOURCE-MATRIX-003
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Research was checked on 2026-08-13. Each implementation task must re-verify upstream versions, terms, endpoints, and coverage in a dated feasibility receipt.

# ODay Plus EMGI Source and Open-Source Decision Matrix

## 1. Decision Standard

Every source is evaluated independently on:

```text
technical usability
data completeness
spatial coverage
temporal coverage
freshness
historical replay
identity stability
change semantics
cost and quota
current-repo fit
open-source leverage
policy warning
```

Policy warning is recorded but does not automatically reject technical implementation. Production publication remains controlled by `policy_state`, `publication_state`, allowed purposes, and kill switch.

Adoption labels:

```text
ADOPT_NOW
ADOPT_AS_BASELINE
PILOT_CHALLENGER
PILOT_PAID
DISCOVERY_ONLY
MANUAL_GROUND_TRUTH
DEFER
REJECT_FOR_PURPOSE
```

## 2. Geography, Address and Built Environment

| Source / project | Role | Decision | Notes |
|---|---|---|---|
| NLSC administrative and village geometry | AUTHORITY | ADOPT_NOW | Canonical versioned polygons and official codes |
| RIS ODRP014 population / households | AUTHORITY | ADOPT_NOW | Monthly village counts and age structure |
| SEGIS statistical areas | AUTHORITY / BASELINE | ADOPT_AS_BASELINE | Finer spatial support where available |
| TGOS QueryAddr | VERIFICATION / AUTHORITY-LIKE | ADOPT_NOW | Taiwan address geocoder; preserve exact/fuzzy result semantics |
| Overture Addresses / Buildings / Divisions | DISCOVERY / BASELINE | ADOPT_AS_BASELINE | Mirror every adopted release to internal GCS |
| Microsoft building footprints / density / height | ALLOCATION PRIOR | PILOT_CHALLENGER | Useful for dasymetric population allocation |
| GHSL / Kontur population | ALLOCATION PRIOR | PILOT_CHALLENGER | Never overwrite official RIS totals |
| `h3-pg` | SPATIAL INDEX | ADOPT_NOW | Native H3 v4 in Postgres |
| PySAL `tobler` | ALLOCATION ENGINE | ADOPT_NOW | Areal and dasymetric interpolation |
| TaiGeotrans | ENGINEERING REFERENCE | PILOT_CHALLENGER | TGOS client and Taiwan coordinate transformations |
| libpostal | PARSER CANDIDATE | DEFER | International parser; not Taiwan authority |

### Overture release rule

At review time the official current release page listed `2026-06-17.0`, schema `v1.17.0`. The Places `categories` field is deprecated and scheduled to be removed in the September 2026 release in favor of `basic_category` and `taxonomy`.

Implementation requirement:

```text
release watcher
→ mirror release / changelog / bridge files
→ checksum
→ schema compatibility
→ dual-read categories + basic_category/taxonomy
```

References:

- https://docs.overturemaps.org/release-calendar/
- https://docs.overturemaps.org/blog/2026/06/17/release-notes/

## 3. Demographics, Housing, Students and Employment

| Source | Role | Decision |
|---|---|---|
| RIS population / households / age | AUTHORITY | ADOPT_NOW |
| RIS migration and vital statistics | AUTHORITY | ADOPT_NOW |
| SEGIS socioeconomic and statistical-area data | AUTHORITY / BASELINE | ADOPT_AS_BASELINE |
| Ministry of Education student / campus / dorm data | ACTIVITY PRIOR | ADOPT_AS_BASELINE |
| Industrial park / science park tenant directories | WORKER PRIOR | ADOPT_AS_BASELINE |
| Company / factory registration | WORKER PRIOR | ADOPT_AS_BASELINE |
| Local occupancy permits / building completion | HOUSING PIPELINE | PILOT_CHALLENGER |
| Overture / Microsoft buildings | ALLOCATION PRIOR | ADOPT_AS_BASELINE |

Published outputs must state whether values are direct observations, official totals spatially allocated, or model estimates.

## 4. POI and Competitors

| Source / project | Role | Decision | Key rule |
|---|---|---|---|
| Foursquare OS Places | DISCOVERY BASELINE | ADOPT_AS_BASELINE | Read Places, Categories and Deltas through Iceberg catalog |
| Overture Places | DISCOVERY BASELINE | ADOPT_AS_BASELINE | Use GERS / bridge files and release version |
| All the Places | BRAND DISCOVERY | PILOT_CHALLENGER | Reuse spider patterns for laundromat and equipment-brand locators |
| MOF business tax registration | OFFICIAL DISCOVERY | ADOPT_NOW | Registration address is candidate evidence, not physical-store truth |
| TGOS / TDX public POI | PUBLIC BASELINE | ADOPT_AS_BASELINE | Public facilities and coarse business categories |
| Google Places | VERIFICATION | PILOT_CHALLENGER | Verify shortlist / source identity; not unquestioned permanent authority |
| Brand websites | VERIFICATION / DISCOVERY | ADOPT_AS_BASELINE | Watch locator and store pages |
| Field Survey | GROUND_TRUTH | MANUAL_GROUND_TRUTH | Capacity, price, status, parking, storefront |
| Splink | IDENTITY RESOLUTION | ADOPT_NOW | Probabilistic linkage with review bands |

### Foursquare access rule

FSQ OS Places is currently delivered through the Foursquare Places Portal using an Iceberg-based catalog. Available datasets include Places, Categories, and Deltas. Monthly delta actions include add, update, remove, and merge.

References:

- https://docs.foursquare.com/data-products/docs/access-fsq-os-places
- https://docs.foursquare.com/data-products/docs/fsq-os-places-release-notes

### Source dependency rule

At minimum record:

```text
AllThePlaces → Overture
AllThePlaces → downstream POI products where applicable
Overture → Foursquare for regions/releases where documented
```

Three downstream copies of one upstream observation are one evidence group, not three independent votes.

### Google search completeness

Nearby Search New requires POST and FieldMask, returns at most 20 results for one request. A response of 20 is not proof of complete coverage. Partition the query geometry or mark `SATURATED`.

Reference:

- https://developers.google.com/maps/documentation/places/web-service/nearby-search

## 5. Rent and Property

| Source / project | Role | Decision |
|---|---|---|
| MOI `lvr_land_c` rental transactions | AUTHORITY / HISTORICAL BENCHMARK | ADOPT_NOW |
| MOI sales `lvr_land_a` | PROPERTY VALUE RESEARCH | KEEP SEPARATE |
| Public tenders / public leasing APIs | ACTIVE LISTING | ADOPT_NOW |
| Partner / broker feed | ACTIVE LISTING | ADOPT_WHEN_AVAILABLE |
| Browser capture / mobile share / email / LINE intake | USER DISCOVERY | ADOPT_NOW |
| Crawlee Python | CRAWLER RUNTIME | ADOPT_NOW |
| `tw-rent-radar` | ENGINEERING REFERENCE | PILOT_CHALLENGER |
| Taiwan Housing Rent Dashboard | ENGINEERING REFERENCE | PILOT_CHALLENGER |
| changedetection.io | CHANGE WATCHER | ADOPT_NOW |
| Browsertrix Crawler | EVIDENCE SNAPSHOT | PILOT_CHALLENGER |
| Scrapling | ADAPTIVE SELECTOR | PILOT_CHALLENGER |
| Crawl4AI | LLM EXTRACTION FALLBACK | PILOT_CHALLENGER |

Extraction order:

```text
API / feed
embedded JSON / JSON-LD / Next.js
network response
deterministic DOM
adaptive selector
LLM extraction fallback
browser evidence snapshot
```

All channels publish `SourceListingObservation`. Assisted Listing owns identity, revision, correction, review, merge, and candidate promotion.

Policy warning: source-specific automated retrieval remains an owner publication decision. Technical adapters may be implemented and shadow-tested under explicit policy state.

## 6. Roads, Routing and Catchments

| Engine / source | Role | Decision |
|---|---|---|
| OpenStreetMap | ROAD BASELINE | ADOPT_NOW |
| QuackOSM | PBF → GeoParquet | ADOPT_NOW |
| osm2pgsql | POSTGIS REPLICATION | ADOPT_NOW |
| ohsome API | OSM HISTORY | PILOT_CHALLENGER |
| Valhalla | ROUTING CANDIDATE | PILOT_CHALLENGER |
| GraphHopper | ROUTING CANDIDATE | PILOT_CHALLENGER |
| openrouteservice | ROUTING CANDIDATE | PILOT_CHALLENGER |
| Google Routes | COMPARATOR / SELECTIVE LIVE | PILOT_CHALLENGER |
| OSMnx | GRAPH QA / FEATURES | ADOPT_AS_BASELINE |
| FMM / STMatch | GPS MAP MATCHING | PILOT_CHALLENGER |

Common provider interface:

```text
route
matrix
isochrone
map_match
health
graph_version
```

Google REST Compute Route Matrix supports DRIVE, TWO_WHEELER, TRANSIT, and WALK. BICYCLE is not supported for the REST route matrix. WALK and TWO_WHEELER are beta and require warning semantics.

References:

- https://developers.google.com/maps/documentation/routes/vehicles-rm
- https://developers.google.com/maps/documentation/routes/reference/rest/v2/RouteTravelMode

Production routing is selected by Taiwan benchmark, not by document preference.

## 7. Traffic, Parking and Camera

| Source / project | Role | Decision |
|---|---|---|
| TDX VD | OBSERVED SPEED / VOLUME | ADOPT_AS_BASELINE |
| TDX parking | OBSERVED FACILITY / AVAILABILITY | ADOPT_AS_BASELINE |
| TDX CCTV metadata | CAMERA DISCOVERY | PILOT_CHALLENGER |
| Bus GPS | ROAD PROBE | PILOT_CHALLENGER |
| TomTom selective sampling | COMMERCIAL COMPARATOR | PILOT_PAID / FREE-QUOTA EXPERIMENT |
| OTVision / OTAnalytics | CAMERA TRAJECTORY / COUNTS | PILOT_CHALLENGER |
| Mapillary | STREET OBJECT PRIOR | PILOT_CHALLENGER |
| Field Survey | CURB / LOADING GROUND TRUTH | MANUAL_GROUND_TRUTH |
| FMM | HIGH-THROUGHPUT MAP MATCHING | PILOT_CHALLENGER |

Bus probe output must be labeled proxy/calibrated probe, not general traffic truth. Stop dwell, terminal layover, GPS rewind, sparse samples, elevated roads, and road-class mismatch require explicit filters.

## 8. Mobility and OD

| Source / project | Role | Decision |
|---|---|---|
| Metro hourly station OD | OBSERVED PUBLIC-TRANSIT OD | ADOPT_AS_BASELINE |
| YouBike OD | OBSERVED SHORT-TRIP OD | ADOPT_AS_BASELINE |
| TDX transit / parking / road observations | OBSERVED CONSTRAINT | ADOPT_AS_BASELINE |
| RIS / housing / POI | SYNTHETIC PRIORS | ADOPT_NOW |
| ODay hourly transactions / machine cycles | DEMAND CALIBRATION | ADOPT_NOW WITH CROSS-FITTING |
| Field counts | GROUND-TRUTH CALIBRATION | MANUAL_GROUND_TRUTH |
| scikit-mobility | OD / GRAVITY / RADIATION | ADOPT_NOW |
| AequilibraE | IPF / SKIMS / ASSIGNMENT | PILOT_CHALLENGER |
| PopulationSim | SYNTHETIC POPULATION | DEFER UNTIL SEED DATA |
| ActivitySim | ACTIVITY MODEL | DEFER RESEARCH LANE |
| Commercial telecom mobility | CALIBRATION / OBSERVATION | PILOT_PAID |

The production baseline is aggregate synthetic OD, not a full activity-based simulation. Commercial mobility is evaluated as incremental uplift, not a prerequisite.
