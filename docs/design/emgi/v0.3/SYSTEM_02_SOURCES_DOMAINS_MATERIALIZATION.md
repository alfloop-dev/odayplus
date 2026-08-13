---
doc_id: ODP-EMGI-IMPLEMENTATION-001-B
title: EMGI v0.3 System — Source Plugins, Domains and Materialization
version: 0.3.0
status: approved-for-parallel-implementation
language: zh-TW
part: 2
part_count: 3
source_document: ODP-EMGI-IMPLEMENTATION-001@0.3.0
reviewed_baseline_sha: 0d1603cf347e30c9cf2f25f0eecc10673ac55015
updated_at: 2026-08-13
---

> Binding part 2 of 3. Read together with all parts listed in the EMGI package index.

# 4. Source Plugin Contract

每個 plugin：

```text
modules/external_data/source_plugins/<source_id>/
  source.yaml
  contract.yaml
  transport.py
  adapter.py
  mapping.py
  quality.yaml
  policy-warning.yaml
  fixtures/
  tests/
```

`source.yaml` 至少：

```yaml
provider_id:
source_dataset_id:
source_dataset_version_strategy:
source_role:
upstream_source_ids:
technical_readiness:
policy_state:
publication_state:
owner_scope:
sharing_scope:
sensitivity_class:
allowed_purposes:
freshness_sla:
retention_class:
```

Generator 產生：

- registry entry；
- secret inventory；
- Dagster asset／job；
- dlt source wiring；
- health metadata；
- OpenLineage metadata；
- docs。

Source worker 不直接改 central registry。

---

# 5. Data Ingestion Semantics

## 5.1 Counts

必須對帳：

```text
source_count
parsed_count
accepted_count
quarantined_count
rejected_count
duplicate_content_count
new_observation_count
```

不可只驗 `accepted + quarantined + rejected <= source_count` 而留下無法解釋缺口。

## 5.2 Idempotency

分開：

```text
content idempotency
observation idempotency
run idempotency
publication idempotency
```

同 content 在不同 observed_at 可建立新 observation。

## 5.3 Retention

Retention 必須由 source／purpose 明確指定，無通用 730-day 默認。

## 5.4 Restart Readback

Source task 必須證明：

```text
fetch
→ persist
→ process restart
→ API readback
→ freshness readback
→ lineage readback
→ consumer readback
```

並覆蓋 GLOBAL、AUTHORIZED_SHARED、TENANT_PRIVATE。

---

# 6. Geography

## 6.1 Address

Address parser 處理：

- NFKC；
- 臺／台；
- 縣市升格；
- 路／街／大道；
- 段、巷、弄、號、之號；
- 樓層與地下室；
- 同建物多門牌；
- provider component match；
- admin polygon cross-check。

Unresolved geocode 不建立 `(0,0)`。

## 6.2 Geocode Fusion

```text
manual authority
official address provider
commercial geocoder
open geocoder
source coordinates
```

保存 candidate disagreement，不用一個不明 match score。

## 6.3 Demographic Allocation

Village／statistical area to H3：

```text
address weighted
building floor-area weighted
residential footprint weighted
land-use weighted
area weighted fallback
```

保存 method、source unit、auxiliary version、uncertainty。

## 6.4 Routing

Provider interface：

```text
route()
matrix()
isochrone()
map_match()
health()
graph_version()
```

Challengers：

- Valhalla；
- GraphHopper；
- openrouteservice；
- Google Routes comparator；
- FMM／STMatch for probe map matching。

正式 engine 由 Taiwan benchmark 決定。

---

# 7. POI and Competitors

## 7.1 Durable Discovery

```text
Overture Places
Foursquare OS Places
All the Places / brand locators
MOF tax registry
TGOS / TDX public facilities
```

## 7.2 Verification

```text
Google Places verifier
source-site watcher
Survey
imagery
```

## 7.3 Identity

Splink-compatible probabilistic linkage：

```text
name
address
phone
tax ID
coordinates
brand
source IDs
hours
image hash
source independence
```

## 7.4 Status Hysteresis

```text
ACTIVE
POSSIBLY_CLOSED
CLOSURE_LIKELY
VERIFIED_CLOSED
```

單次來源缺失不直接關閉。

## 7.5 Capacity

```text
washer_count
dryer_count
capacity_kg_total
price band
hours
payments
surveyed_at
evidence
uncertainty interval
```

---

# 8. Rent and Listings

## 8.1 Rental Transactions

買賣 `lvr_land_a` 與租賃 `lvr_land_c` 分開 package。

## 8.2 Rent Benchmark

Hierarchical backoff：

```text
use + floor + area + catchment
→ H3
→ market zone
→ district
→ adjacent zone
→ city segment
```

輸出 sample count、effective sample size、age、fallback level、interval。

## 8.3 Listing Discovery

Extraction priority：

```text
official / partner API
embedded JSON / JSON-LD / Next.js
network response
deterministic DOM
adaptive selector
LLM extraction fallback
browser evidence snapshot
```

Crawler 只建立 `SourceListingObservation`，後續進 Assisted Listing identity、revision、review、correction、promotion。

---

# 9. Mobility, Traffic and Survey

## 9.1 Synthetic Mobility Baseline

```text
public OD
demographics
POI attraction
routing impedance
gravity / radiation
IPF / entropy balancing
ODay demand calibration
field-count calibration
```

輸出名稱明確包含 estimate／synthetic。

## 9.2 Leakage Control

使用內部營收校正外部活動模型時，必須 cross-fit，保存 training scope、window、model version、out-of-fold flag。

## 9.3 Traffic Fusion

Metric-specific source priority：

| Metric | Sources |
|---|---|
| Speed | VD / commercial probe / calibrated bus |
| Volume | VD / camera / field |
| Motorcycle | camera / field |
| Static access | OSM / official road |
| Temporary stop | Survey / camera |
| Loading friction | Survey |

不同來源不直接平均。

## 9.4 Survey

ODK：

- forms；
- counts；
- photos；
- common attributes。

QField：

- geometry；
- road／curb／entrance edits；
- professional GIS tasks。

EMGI 管理 assignment、review、expiry、evidence binding。

---

# 10. Physical Feasibility and Economics

## 10.1 Physical Gate

`physical_site_feasibility_snapshot` 產出 explicit state；unknown 建 Survey，不轉成低分。

## 10.2 Target Format Snapshot

```text
format code
machine mix
required area
power
water
drainage
gas
ventilation
loading
CAPEX assumptions
price assumptions
```

## 10.3 SiteScore v3 Boundary

拆成：

```text
MarketPotentialModel
TargetFormatCounterfactual
RampCurveModel
CannibalizationModel
UnitEconomicsSimulator
DecisionPolicy
```

Model score 與 decision readiness 分離。

## 10.4 Payback

以月度 cash flow、NPV／IRR／payback interval 計算。未回本是 censored outcome，不使用固定 999 當 ordinary label。

---

# 11. Feature and Data Product Materialization

## 11.1 YAML FeatureSpec

第一版只描述：

```text
source relation
grain
spatial support
temporal join
aggregation
missingness
coverage gates
output schema
```

由 generator 產生 dbt SQL、tests、docs。

## 11.2 Component Manifest

每個 component content-addressed：

```text
feature spec version
source manifests
entity scope
effective as-of
knowledge as-of
compiler version
policy version
```

## 11.3 Dirty Region

Source update 產生：

```text
affected_geometry
affected_time_window
affected_entities
changed_fields
```

只重建受影響 cell、catchment、site、store 與 component。

---

# 12. Model Training and Validation

## 12.1 Training Cohorts

```text
STRICT_OPENING_COHORT
PROXY_START_COHORT
MATURE_STORE_COHORT
```

Proxy 不寫回 authoritative opened_on。

## 12.2 Cross-brand

使用 governed analytical cohort；tenant ID 僅 scope／split／audit。

## 12.3 Split

至少：

```text
forward time
spatial block
leave region out
leave brand / tenant out
new-store cohort
```

## 12.4 Uncertainty

Prediction interval 必須由 validation residual 校正。可 pilot MAPIE／conformal，但需 region／brand／time grouped calibration 及 out-of-support abstention。

## 12.5 Metrics

```text
MAE / WAPE / RMSLE
interval coverage and width
NDCG@K / Top-K recall
pairwise rank accuracy
decision regret
accepted-bad / rejected-good rates
abstention rate
censoring-aware payback metrics
```

MAPE 只作既有補助目標之一，不作唯一 release gate。

---
