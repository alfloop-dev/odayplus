---
doc_id: ODP-EMGI-PRODUCT-001-C
title: EMGI v0.3 Product — Domains and Data Products
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 3
part_count: 4
source_document: ODP-EMGI-PRODUCT-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 3 of 4. Read together with all parts listed in the EMGI package index.

# 6. Data Domains

## D1 Geography and Built Environment

```text
AddressLocation
Building
Parcel
AdministrativeBoundary
RoadSegment
Intersection
Barrier
LandUseZone
HazardArea
```

## D2 Demographics and Housing

```text
population
households
age bands
migration
housing stock
building age
small-unit proxy
rental-household proxy
new housing pipeline
allocation method and uncertainty
```

## D3 POI and Business Activity

```text
PoiEntity
PoiObservation
PoiIdentityEdge
PoiCategoryMapping
PoiStatusDecision
SourceDependency
```

## D4 Competitors and Supply

```text
CompetitorEntity
CompetitorObservation
CompetitorCapacityObservation
CompetitorPriceObservation
CompetitorStatusDecision
```

供給以容量為主，不只計店數。

## D5 Mobility and Activity

```text
MobilityCellProfile
SyntheticODFlow
ObservedODAggregate
ResidentWorkerStudentVisitorProfile
TimeOfDayActivityProfile
```

Synthetic estimate 不可命名 actual telecom population。

## D6 Traffic, Road and Parking

```text
TrafficSegmentProfile
TrafficCoverage
BusProbeObservation
ParkingFacilityObservation
CurbsideObservation
CatchmentGeometry
```

## D7 Rent and Property

```text
RentalTransaction
RentBenchmark
PropertyEntity
SourceListingObservation
ListingRevision
ListingStatusObservation
PropertyIdentityEdge
```

## D8 Market Events

```text
MarketEvent
MarketEventObservation
MarketImpactArea
MarketEventReview
```

## D9 Survey

```text
SurveyAssignment
FieldSurveyObservation
SurveyEvidence
SurveyReview
```

## D10 Physical Site Feasibility

```text
legal_use
zoning
floor
frontage
power
water
drainage
gas
ventilation
exhaust
flood risk
loading
temporary stop
building restrictions
construction feasibility
```

## D11 Site Economics Context

```text
target format
machine mix
capex
deposit
fitout
utilities
maintenance
cleaning
payment fee
platform fee
financing
taxes
working capital
residual value
```

---

# 7. Data Product Catalog

## 7.1 Source Evidence Products

### DP-A01 `source_content_blob`

Content-addressed raw or redacted bytes.

### DP-A02 `source_observation`

A dated observation referencing a blob and dataset version.

### DP-A03 `source_search_execution`

Query geometry, filters, partition strategy, result limits, saturation, pagination, source health and completeness.

### DP-A04 `external_ingestion_run`

Run, scope principal, counts, watermark, DQ, snapshot and failure classification.

### DP-A05 `external_record_lineage`

Source field to canonical field lineage.

### DP-A06 `source_dependency_graph`

Upstream and independence groups.

### DP-A07 `source_retraction`

Retraction, correction or supersession evidence.

## 7.2 Canonical Market Products

```text
DP-B01 address_location_registry
DP-B02 geo_reference_snapshot
DP-B03 demographic_spatial_observation
DP-B04 poi_observation
DP-B05 competitor_store_observation
DP-B06 mobility_profile
DP-B07 traffic_access_profile
DP-B08 parking_access_profile
DP-B09 rent_market_benchmark
DP-B10 property_listing_observation
DP-B11 market_event
DP-B12 field_survey_observation
DP-B13 physical_site_feasibility_observation
DP-B14 store_operational_start_observation
DP-B15 entity_partition_coverage
```

## 7.3 Decision-Ready Products

### DP-C01 `coverage_surface_snapshot`

每個 domain 在 space × time 的可用性、完整性、freshness 與 uncertainty。

### DP-C02 `market_cell_profile_monthly`

H3 × month，保留 source-native allocation method 與 coverage。

### DP-C03 `catchment_profile_snapshot`

Origin × mode × duration × build version。

### DP-C04 `site_context_component_snapshot`

可獨立重建的 identity、demographics、POI、competitor、rent、listing、mobility、traffic、event、survey、feasibility 元件。

### DP-C05 `site_context_snapshot`

元件 manifest，而非每次複製一份巨大不可辨識 JSON。

至少引用：

```text
identity_component_id
catchment_component_id
demographic_component_id
built_environment_component_id
poi_component_id
competitor_component_id
rent_component_id
listing_component_id
mobility_component_id
traffic_component_id
market_event_component_id
survey_component_id
feasibility_component_id
coverage_component_id
```

### DP-C06 `store_market_context_history`

既有店 × period 的外部市場歷史。

### DP-C07 `competitor_network_snapshot`

Market zone × snapshot。

### DP-C08 `listing_inventory_snapshot`

Market zone × snapshot。

### DP-C09 `physical_site_feasibility_snapshot`

輸出：

```text
FEASIBLE
FEASIBLE_WITH_CONDITIONS
UNKNOWN_REQUIRES_SURVEY
INFEASIBLE
```

### DP-C10 `site_economics_snapshot`

完整 assumptions、cash flow、NPV／IRR／payback inputs；不由 EMGI 自行決定投資。

### DP-C11 `market_change_signal`

需求、競爭、租金、access、listing、建設與 coverage 變化。

### DP-C12 `market_archetype_assignment`

多標籤、機率、版本化，不能取代原始特徵。

### DP-C13 `data_gap_task`

缺漏與決策影響。

### DP-C14 `data_acquisition_plan`

Value of Information、費用、quota、延遲與優先順序。

### DP-C15 `source_value_experiment`

Baseline vs added-source uplift evidence。

### DP-C16 `feature_source_manifest`

只含 prediction origin 前可知 feature evidence。

### DP-C17 `label_source_manifest`

只含 outcome／label evidence。

### DP-C18 `training_dataset_manifest`

引用 feature 與 label manifests、split、cross-fitting 與 cohort。

---

# 8. Decision Readiness

```text
DISCOVERY_READY
SCREENING_READY
COMPARISON_READY
DUE_DILIGENCE_READY
MODEL_TRAINING_READY
```

Readiness 不是單一 quality score。

例：

```text
SCREENING_READY = true
COMPARISON_READY = true
DUE_DILIGENCE_READY = false
reason = THREE_PHASE_POWER_NOT_VERIFIED
```

---

# 9. Product Capabilities

## P1 Market Explorer

- demand、competition、rent、listing、growth、feasibility、coverage layers；
- 區域排名與 evidence；
- 產生 data acquisition plan；
- 轉 Property Radar／Survey；
- 不因 missing 顯示假低分。

## P2 Site Intelligence Dossier

- address、catchments、market profile；
- physical feasibility；
- source and negative evidence；
- component manifests；
- readiness；
- build immutable site context。

## P3 Candidate Compare

- 同版本 policy；
- feature／coverage／uncertainty 差異；
- hard gaps；
- 可送 SiteScore demand model；
- economics 未完成時不輸出 binding recommendation。

## P4 Competition Monitor

- discovery、status hysteresis、capacity、price、overlap；
- source dependency；
- review queue。

## P5 Property Radar

技術 discovery 可包括：

```text
PUBLIC_API
PARTNER_FEED
CSV_UPLOAD
BROWSER_CAPTURE
MOBILE_SHARE
EMAIL_ALERT
LINE_INTAKE
CRAWLER
CHANGE_WATCHER
```

所有來源只建立 observation，identity、revision、correction、promotion 仍由 Assisted Listing binding lifecycle 管理。

## P6 Market Change Monitor

- source registry；
- document observation；
- LLM candidate extraction；
- human review；
- affected geometry；
- state milestones。

## P7 Survey Operations

ODK 為一般離線表單主流程，QField 用於 GIS geometry 修正；EMGI 管理 assignment、review、evidence、expiry、promotion。

## P8 Data Health and Governance

- provider／dataset／version；
- source technical vs policy readiness；
- freshness、coverage、DQ、quarantine；
- cost／quota；
- dependency；
- publication；
- lineage；
- retention／retraction。

---

# 10. Integration with Internal Data

`oday-data-platform` 發布：

```text
store_reference_v1
store_daily_performance_v1
machine_capacity_snapshot_v1
machine_utilization_daily_v1
store_data_coverage_v1
store_operational_start_observation_v1
```

EMGI 發布本文件資料產品。

Join key：

```text
canonical_store_id
place_id
address_id
coordinates
H3
business_date
valid_from / valid_to
```

禁止以店名模糊 join 取代受治理 mapping。

Cross-brand training 使用 `analytical_cohort_id`、brand archetype、store format、machine capacity 與 coverage masks。`tenant_id` 不作未經審查的 predictor。

---
