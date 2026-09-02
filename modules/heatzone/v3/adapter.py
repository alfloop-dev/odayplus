from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from modules.heatzone.domain.scoring import HeatZoneFeatureInput
from modules.heatzone.v3.absorption import AbsorptionResult
from modules.heatzone.v3.contract import HeatZoneV3Input
from packages.oday_data_contracts_client.models.machine_capacity import MachineCapacityRecord
from packages.oday_data_contracts_client.models.operational_start_observation import (
    OperationalStartObservation,
)
from packages.oday_data_contracts_client.models.store_coverage import StoreDayCoverage
from packages.oday_data_contracts_client.models.store_daily_performance import (
    StoreDailyPerformance,
)
from packages.oday_data_product_contracts_client.models.catchment_profile import (
    CatchmentProfile,
    DomainStatus,
)
from packages.oday_data_product_contracts_client.models.market_cell_profile import (
    MarketCellProfile,
    ReadinessLevel,
)
from shared.governance import DecisionPolicy


def _match_store_capacities_and_coverage(
    target_h3_indices: set[str],
    target_entity_ids: set[str],
    own_store_capacities: Sequence[MachineCapacityRecord] | None,
    store_coverage_records: Sequence[StoreDayCoverage] | None,
) -> tuple[list[MachineCapacityRecord], list[StoreDayCoverage], int, float]:
    """Match capacity and coverage records to a spatial target (cell or catchment).

    Returns:
        (matched_caps, matched_store_covs, distinct_store_count, total_machine_capacity)
    """
    valid_h3s = {h for h in target_h3_indices if h}
    valid_ids = {i for i in target_entity_ids if i}

    if not valid_h3s and not valid_ids:
        return [], [], 0, 0.0

    matched_store_ids: set[str] = set()

    # 1. Match from store coverage records
    cov_list = list(store_coverage_records or ())
    for s in cov_list:
        store_id = getattr(s, "store_id", "")
        if not store_id:
            continue
        cov = getattr(s, "coverage", None)
        matched = False
        if cov is not None:
            if isinstance(cov, dict):
                qgeom = cov.get("query_geometry")
                if isinstance(qgeom, dict):
                    gh3 = qgeom.get("h3_index")
                    if gh3 and gh3 in valid_h3s:
                        matched = True
                elif qgeom is not None:
                    gh3 = getattr(qgeom, "h3_index", None)
                    if gh3 and gh3 in valid_h3s:
                        matched = True
                eid = cov.get("entity_id")
                if eid and (eid in valid_h3s or eid in valid_ids):
                    matched = True
                spid = cov.get("scope_principal_id")
                if spid and spid in valid_ids:
                    matched = True
                meta = cov.get("metadata")
                if isinstance(meta, dict):
                    if meta.get("h3_index") in valid_h3s or meta.get("cell_id") in valid_ids:
                        matched = True
                subparts = cov.get("sub_partitions") or ()
                for sub in subparts:
                    if isinstance(sub, dict):
                        sub_geom = sub.get("geometry")
                        if isinstance(sub_geom, dict) and sub_geom.get("h3_index") in valid_h3s:
                            matched = True
                        if sub.get("sub_partition_key") in valid_h3s:
                            matched = True
                    else:
                        sub_geom = getattr(sub, "geometry", None)
                        if sub_geom and getattr(sub_geom, "h3_index", None) in valid_h3s:
                            matched = True
                        if getattr(sub, "sub_partition_key", None) in valid_h3s:
                            matched = True
            else:
                qgeom = getattr(cov, "query_geometry", None)
                if qgeom is not None:
                    gh3 = getattr(qgeom, "h3_index", None)
                    if gh3 and gh3 in valid_h3s:
                        matched = True
                eid = getattr(cov, "entity_id", None)
                if eid and (eid in valid_h3s or eid in valid_ids):
                    matched = True
                spid = getattr(cov, "scope_principal_id", None)
                if spid and spid in valid_ids:
                    matched = True
                meta = getattr(cov, "metadata", None)
                if isinstance(meta, dict):
                    if meta.get("h3_index") in valid_h3s or meta.get("cell_id") in valid_ids:
                        matched = True
                subparts = getattr(cov, "sub_partitions", None) or ()
                for sub in subparts:
                    sub_geom = getattr(sub, "geometry", None)
                    if sub_geom and getattr(sub_geom, "h3_index", None) in valid_h3s:
                        matched = True
                    if getattr(sub, "sub_partition_key", None) in valid_h3s:
                        matched = True
        if store_id in valid_h3s or store_id in valid_ids:
            matched = True
        if matched:
            matched_store_ids.add(store_id)

    # 2. Match from machine capacity records
    cap_list = list(own_store_capacities or ())
    for c in cap_list:
        store_id = getattr(c, "store_id", "")
        if not store_id:
            continue
        ev_ref = getattr(c, "evidence_ref", None)
        if ev_ref:
            ev_str = str(ev_ref)
            if ev_str in valid_h3s or ev_str in valid_ids:
                matched_store_ids.add(store_id)
            elif any(h in ev_str for h in valid_h3s if len(h) >= 7):
                matched_store_ids.add(store_id)
            elif any(i in ev_str for i in valid_ids if len(i) >= 4):
                matched_store_ids.add(store_id)
        if store_id in valid_h3s or store_id in valid_ids:
            matched_store_ids.add(store_id)

    # 3. Filter matched records
    matched_caps = [
        c for c in cap_list
        if getattr(c, "store_id", "") in matched_store_ids
        or (getattr(c, "evidence_ref", None) and any(h in str(c.evidence_ref) for h in valid_h3s if len(h) >= 7))
    ]
    matched_store_covs = [
        s for s in cov_list
        if getattr(s, "store_id", "") in matched_store_ids
    ]

    # 4. Compute distinct store count (R2) and sum machine capacity
    distinct_store_ids = {
        getattr(c, "store_id", "") for c in matched_caps if getattr(c, "store_id", "")
    } | {
        getattr(s, "store_id", "") for s in matched_store_covs if getattr(s, "store_id", "")
    }
    distinct_store_count = len(distinct_store_ids)
    total_machine_capacity = sum(float(c.machine_count or 0) for c in matched_caps)

    return matched_caps, matched_store_covs, distinct_store_count, total_machine_capacity


def from_market_cell_profile(
    cell: MarketCellProfile | Mapping[str, Any],
    *,
    own_store_capacities: Sequence[MachineCapacityRecord] | None = None,
    store_coverage_records: Sequence[StoreDayCoverage] | None = None,
    tenant_id: str = "default",
    housing_units_override: float | None = None,
    active_listing_count_override: int | None = None,
    median_listing_rent_override: float | None = None,
    absorption: AbsorptionResult | None = None,
    store_performances: Sequence[StoreDailyPerformance | Mapping[str, Any]] | None = None,
    operational_starts: (
        Mapping[str, OperationalStartObservation | Mapping[str, Any]]
        | Sequence[OperationalStartObservation | Mapping[str, Any]]
        | None
    ) = None,
    decision_policy: DecisionPolicy | None = None,
    as_of: date | None = None,
    original_demand: float | None = None,
    observation_window_start: date | str | None = None,
    observation_window_end: date | str | None = None,
) -> HeatZoneV3Input:
    """Adapt a canonical emgi.market-cell-profile.v1 cell into a HeatZone v3 input."""
    if isinstance(cell, Mapping):
        cell_obj = MarketCellProfile.from_dict(cell)
    else:
        cell_obj = cell

    # Demographics / Population
    raw_pop = cell_obj.demographics.total_population
    pop_val = float(raw_pop) if raw_pop is not None else 0.0
    raw_hh = cell_obj.demographics.household_count
    hh_val = float(raw_hh) if raw_hh is not None else 0.0
    daytime_ratio = (
        float(cell_obj.demographics.daytime_population_ratio)
        if cell_obj.demographics.daytime_population_ratio is not None
        else 1.0
    )

    # Competitors
    comp_cap = (
        float(cell_obj.competitors.total_capacity)
        if cell_obj.competitors.total_capacity is not None
        else (
            float(cell_obj.competitors.average_capacity or 0.0)
            * float(cell_obj.competitors.active_competitors)
        )
    )
    active_comp_count = int(cell_obj.competitors.active_competitors)
    brands = list(cell_obj.competitors.brands_present)
    price_tiers = (
        dict(cell_obj.competitors.price_tier_distribution)
        if cell_obj.competitors.price_tier_distribution
        else {}
    )

    # POI count from stores_by_category or total competitors
    poi_count = sum(
        int(v) for v in cell_obj.competitors.stores_by_category.values() if isinstance(v, (int, float))
    )
    if poi_count == 0:
        poi_count = int(cell_obj.competitors.total_competitors)

    # Rent
    median_rent = (
        float(cell_obj.rent.median_rent_per_ping)
        if cell_obj.rent.median_rent_per_ping is not None
        else 0.0
    )
    mean_rent = (
        float(cell_obj.rent.mean_rent_per_ping)
        if cell_obj.rent.mean_rent_per_ping is not None
        else median_rent
    )
    rent_samples = int(cell_obj.rent.sample_count)

    # Housing & Listing
    housing_units = (
        housing_units_override
        if housing_units_override is not None
        else hh_val
    )
    active_listing = (
        active_listing_count_override
        if active_listing_count_override is not None
        else rent_samples
    )
    listing_rent = (
        median_listing_rent_override
        if median_listing_rent_override is not None
        else median_rent
    )

    # Own-store capacity and coverage proof (R1 & R2)
    target_h3s = {cell_obj.h3_index} if cell_obj.h3_index else set()
    target_ids = {cell_obj.cell_id, cell_obj.h3_index} if (cell_obj.cell_id or cell_obj.h3_index) else set()
    matched_caps, matched_store_covs, own_store_count, own_machine_cap = (
        _match_store_capacities_and_coverage(
            target_h3s,
            target_ids,
            own_store_capacities,
            store_coverage_records,
        )
    )

    # Coverage & Support
    overall_readiness = cell_obj.coverage.overall_readiness
    domain_cov = dict(cell_obj.coverage.domain_coverage)
    domain_fresh = dict(cell_obj.coverage.domain_freshness)
    has_gaps = bool(cell_obj.coverage.has_gaps)
    reasons = [r.code for r in cell_obj.coverage.readiness_reasons]
    is_quar = any("quarantined" in str(v).lower() for v in domain_cov.values()) or any("quarantine" in r.lower() for r in reasons)
    
    if domain_cov:
        valid_domains = sum(1 for v in domain_cov.values() if str(v).lower() in ("complete", "partial", "fresh", "available"))
        cov_ratio = valid_domains / len(domain_cov)
    else:
        cov_ratio = None
        
    readiness_str = overall_readiness.value if hasattr(overall_readiness, "value") else str(overall_readiness).lower()
    support_lvl = "supported" if readiness_str in ("ready", "usable_with_gaps") and not is_quar else "unsupported"

    # Confidence calculation
    conf = 1.0
    if cell_obj.rent.confidence_pct is not None:
        conf = min(conf, float(cell_obj.rent.confidence_pct) / 100.0)
    if cell_obj.demographics.uncertainty_pct is not None:
        conf = min(conf, max(0.0, 1.0 - float(cell_obj.demographics.uncertainty_pct) / 100.0))

    effective_absorption = absorption
    if (
        effective_absorption is None
        and store_performances is not None
        and operational_starts is not None
        and decision_policy is not None
        and original_demand is not None
        and observation_window_start is not None
        and observation_window_end is not None
    ):
        from modules.heatzone.application.absorption_inputs import assemble_zone_absorption

        distinct_store_ids = {
            getattr(c, "store_id", "") for c in matched_caps if getattr(c, "store_id", "")
        } | {
            getattr(s, "store_id", "") for s in matched_store_covs if getattr(s, "store_id", "")
        }

        effective_as_of = as_of
        if effective_as_of is None:
            raw_as_of = getattr(cell_obj, "as_of_date", None)
            if raw_as_of:
                if isinstance(raw_as_of, date) and not isinstance(raw_as_of, datetime):
                    effective_as_of = raw_as_of
                else:
                    try:
                        effective_as_of = date.fromisoformat(str(raw_as_of).split("T")[0])
                    except (ValueError, TypeError):
                        effective_as_of = datetime.now(UTC).date()
            else:
                effective_as_of = datetime.now(UTC).date()

        effective_absorption = assemble_zone_absorption(
            store_ids=distinct_store_ids,
            performances=store_performances,
            operational_starts=operational_starts,
            original_demand=original_demand,
            policy=decision_policy,
            as_of=effective_as_of,
            observation_window_start=observation_window_start,
            observation_window_end=observation_window_end,
        )

    return HeatZoneV3Input(
        absorption=effective_absorption,
        h3_index=cell_obj.h3_index,
        h3_resolution=cell_obj.h3_resolution,
        cell_id=cell_obj.cell_id,
        tenant_id=tenant_id,
        population=pop_val,
        daytime_population_ratio=daytime_ratio,
        household_count=hh_val,
        housing_units=housing_units,
        poi_count=poi_count,
        poi_categories=dict(cell_obj.competitors.stores_by_category),
        competitor_capacity=comp_cap,
        active_competitor_count=active_comp_count,
        competitor_brands=brands,
        competitor_price_tiers=price_tiers,
        median_rent_per_ping=median_rent,
        mean_rent_per_ping=mean_rent,
        rent_sample_count=rent_samples,
        active_listing_count=active_listing,
        median_listing_rent=listing_rent,
        own_store_count=own_store_count,
        own_store_machine_capacity=own_machine_cap,
        own_store_capacities=matched_caps,
        store_coverage_records=matched_store_covs,
        overall_readiness=overall_readiness,
        domain_coverage=domain_cov,
        domain_freshness=domain_fresh,
        has_coverage_gaps=has_gaps,
        readiness_reasons=reasons,
        coverage_ratio=cov_ratio,
        is_quarantined=is_quar,
        support_level=support_lvl,
        confidence=conf,
        centroid_lat=cell_obj.centroid_lat,
        centroid_lng=cell_obj.centroid_lng,
        county=cell_obj.county or "",
        district=cell_obj.district or "",
        admin_code=cell_obj.admin_code or "",
        period_grain=cell_obj.period_grain.value if hasattr(cell_obj.period_grain, "value") else str(cell_obj.period_grain),
        period_key=cell_obj.period_key,
        component_manifest_refs=list(cell_obj.component_manifest_refs),
        metadata=dict(cell_obj.metadata),
    )


def from_catchment_profile(
    profile: CatchmentProfile | Mapping[str, Any],
    *,
    own_store_capacities: Sequence[MachineCapacityRecord] | None = None,
    store_coverage_records: Sequence[StoreDayCoverage] | None = None,
    tenant_id: str = "default",
    poi_count_override: int | None = None,
    housing_units_override: float | None = None,
    active_listing_count_override: int | None = None,
    absorption: AbsorptionResult | None = None,
    store_performances: Sequence[StoreDailyPerformance | Mapping[str, Any]] | None = None,
    operational_starts: (
        Mapping[str, OperationalStartObservation | Mapping[str, Any]]
        | Sequence[OperationalStartObservation | Mapping[str, Any]]
        | None
    ) = None,
    decision_policy: DecisionPolicy | None = None,
    as_of: date | None = None,
    original_demand: float | None = None,
    observation_window_start: date | str | None = None,
    observation_window_end: date | str | None = None,
) -> HeatZoneV3Input:
    """Adapt a canonical emgi.catchment-profile.v1 profile into a HeatZone v3 input."""
    if isinstance(profile, Mapping):
        prof_obj = CatchmentProfile.from_dict(profile)
    else:
        prof_obj = profile

    h3_idx = prof_obj.origin.origin_h3
    h3_res = prof_obj.boundary.h3_resolution or 9

    # Demographics
    pop_val = (
        float(prof_obj.demographics.total_population)
        if prof_obj.demographics.total_population is not None
        else (
            float(prof_obj.mobility.resident_population or prof_obj.mobility.activity_population or 0.0)
            if prof_obj.mobility
            else 0.0
        )
    )
    hh_val = (
        float(prof_obj.demographics.household_count)
        if prof_obj.demographics.household_count is not None
        else (pop_val / 2.5 if pop_val > 0 else 0.0)
    )
    daytime_ratio = (
        float(prof_obj.demographics.daytime_population_ratio)
        if prof_obj.demographics.daytime_population_ratio is not None
        else 1.0
    )

    # Competitors
    comp_cap = (
        float(prof_obj.competitors.total_capacity)
        if prof_obj.competitors.total_capacity is not None
        else (
            float(prof_obj.competitors.average_capacity or 0.0)
            * float(prof_obj.competitors.active_competitors or 0)
        )
    )
    active_comp_count = int(prof_obj.competitors.active_competitors or 0)
    brands = list(prof_obj.competitors.brands_present)
    price_tiers = (
        dict(prof_obj.competitors.price_tier_distribution)
        if prof_obj.competitors.price_tier_distribution
        else {}
    )

    # POI
    poi_count = (
        poi_count_override
        if poi_count_override is not None
        else sum(int(v) for v in prof_obj.competitors.stores_by_category.values() if isinstance(v, (int, float)))
    )
    if poi_count == 0 and prof_obj.competitors.total_competitors:
        poi_count = int(prof_obj.competitors.total_competitors)

    # Rent
    median_rent = (
        float(prof_obj.rent.median_rent_per_ping)
        if prof_obj.rent.median_rent_per_ping is not None
        else 0.0
    )
    mean_rent = (
        float(prof_obj.rent.mean_rent_per_ping)
        if prof_obj.rent.mean_rent_per_ping is not None
        else median_rent
    )
    rent_samples = int(prof_obj.rent.sample_count)

    # Housing & Listing
    housing_units = (
        housing_units_override
        if housing_units_override is not None
        else hh_val
    )
    active_listing = (
        active_listing_count_override
        if active_listing_count_override is not None
        else rent_samples
    )

    # Own-store capacity and coverage proof (R1 & R2)
    target_h3s = set()
    if prof_obj.origin and prof_obj.origin.origin_h3:
        target_h3s.add(prof_obj.origin.origin_h3)
    if prof_obj.boundary and prof_obj.boundary.h3_cells:
        target_h3s.update(prof_obj.boundary.h3_cells)

    target_ids = set(target_h3s)
    if prof_obj.profile_id:
        target_ids.add(prof_obj.profile_id)
        target_ids.add(f"catchment:{prof_obj.profile_id}")
    if prof_obj.origin and prof_obj.origin.origin_id:
        target_ids.add(prof_obj.origin.origin_id)
    if prof_obj.boundary and prof_obj.boundary.catchment_id:
        target_ids.add(prof_obj.boundary.catchment_id)

    matched_caps, matched_store_covs, own_store_count, own_machine_cap = (
        _match_store_capacities_and_coverage(
            target_h3s,
            target_ids,
            own_store_capacities,
            store_coverage_records,
        )
    )

    # Coverage
    overall_readiness = prof_obj.coverage.overall_readiness
    domain_cov = dict(prof_obj.coverage.domain_coverage)
    has_gaps = bool(prof_obj.coverage.has_gaps)
    reasons = [r.code for r in prof_obj.coverage.readiness_reasons]
    is_quar = any("quarantined" in str(v).lower() for v in domain_cov.values()) or any("quarantine" in r.lower() for r in reasons)
    
    if domain_cov:
        valid_domains = sum(1 for v in domain_cov.values() if str(v).lower() in ("complete", "partial", "fresh", "available"))
        cov_ratio = valid_domains / len(domain_cov)
    else:
        cov_ratio = None
        
    readiness_str = overall_readiness.value if hasattr(overall_readiness, "value") else str(overall_readiness).lower()
    support_lvl = "supported" if readiness_str in ("ready", "usable_with_gaps") and not is_quar else "unsupported"

    conf = 1.0
    if prof_obj.demographics.status is not DomainStatus.available:
        conf *= 0.8
    if prof_obj.competitors.status is not DomainStatus.available:
        conf *= 0.8
    if prof_obj.rent.status is not DomainStatus.available:
        conf *= 0.8

    effective_absorption = absorption
    if (
        effective_absorption is None
        and store_performances is not None
        and operational_starts is not None
        and decision_policy is not None
        and original_demand is not None
        and observation_window_start is not None
        and observation_window_end is not None
    ):
        from modules.heatzone.application.absorption_inputs import assemble_zone_absorption

        distinct_store_ids = {
            getattr(c, "store_id", "") for c in matched_caps if getattr(c, "store_id", "")
        } | {
            getattr(s, "store_id", "") for s in matched_store_covs if getattr(s, "store_id", "")
        }

        effective_absorption = assemble_zone_absorption(
            store_ids=distinct_store_ids,
            performances=store_performances,
            operational_starts=operational_starts,
            original_demand=original_demand,
            policy=decision_policy,
            as_of=as_of or datetime.now(UTC).date(),
            observation_window_start=observation_window_start,
            observation_window_end=observation_window_end,
        )

    return HeatZoneV3Input(
        absorption=effective_absorption,
        h3_index=h3_idx,
        h3_resolution=h3_res,
        cell_id=f"catchment:{prof_obj.profile_id}",
        tenant_id=tenant_id,
        population=pop_val,
        daytime_population_ratio=daytime_ratio,
        household_count=hh_val,
        housing_units=housing_units,
        poi_count=poi_count,
        poi_categories=dict(prof_obj.competitors.stores_by_category),
        competitor_capacity=comp_cap,
        active_competitor_count=active_comp_count,
        competitor_brands=brands,
        competitor_price_tiers=price_tiers,
        median_rent_per_ping=median_rent,
        mean_rent_per_ping=mean_rent,
        rent_sample_count=rent_samples,
        active_listing_count=active_listing,
        median_listing_rent=median_rent,
        own_store_count=own_store_count,
        own_store_machine_capacity=own_machine_cap,
        own_store_capacities=matched_caps,
        store_coverage_records=matched_store_covs,
        overall_readiness=overall_readiness,
        domain_coverage=domain_cov,
        domain_freshness=dict(prof_obj.coverage.domain_freshness),
        has_coverage_gaps=has_gaps,
        readiness_reasons=reasons,
        coverage_ratio=cov_ratio,
        is_quarantined=is_quar,
        support_level=support_lvl,
        confidence=conf,
        centroid_lat=prof_obj.origin.latitude,
        centroid_lng=prof_obj.origin.longitude,
        county=prof_obj.origin.county or "",
        district=prof_obj.origin.district or "",
        admin_code=prof_obj.origin.admin_code or "",
        period_grain=prof_obj.period_grain.value if hasattr(prof_obj.period_grain, "value") else str(prof_obj.period_grain),
        period_key=prof_obj.period_key,
        component_manifest_refs=list(prof_obj.component_manifest_refs),
        metadata=dict(prof_obj.metadata),
    )


def from_legacy_feature_input(
    legacy: HeatZoneFeatureInput | Mapping[str, Any],
    *,
    population_override: float | None = None,
    household_count_override: float | None = None,
    housing_units_override: float | None = None,
    overall_readiness: ReadinessLevel = ReadinessLevel.ready,
    coverage_ratio: float = 1.0,
    tenant_id: str = "default",
    absorption: AbsorptionResult | None = None,
    store_ids: Sequence[str] | set[str] | None = None,
    store_performances: Sequence[StoreDailyPerformance | Mapping[str, Any]] | None = None,
    operational_starts: (
        Mapping[str, OperationalStartObservation | Mapping[str, Any]]
        | Sequence[OperationalStartObservation | Mapping[str, Any]]
        | None
    ) = None,
    decision_policy: DecisionPolicy | None = None,
    as_of: date | None = None,
    original_demand: float | None = None,
    observation_window_start: date | str | None = None,
    observation_window_end: date | str | None = None,
) -> HeatZoneV3Input:
    """Bridge legacy v1 HeatZoneFeatureInput into HeatZoneV3Input."""
    if isinstance(legacy, Mapping):
        data = legacy
        h3_index = str(data["h3_index"])
        h3_resolution = int(data.get("h3_resolution", 9))
        poi_count = int(data.get("poi_count", 0))
        competitor_count = int(data.get("competitor_count", 0))
        competitor_capacity = float(data.get("competitor_capacity", 0.0))
        median_listing_rent = float(data.get("median_listing_rent", 0.0))
        active_listing_count = int(data.get("active_listing_count", 0))
        existing_store_count = int(data.get("existing_store_count", 0))
        confidence_raw = data.get("average_confidence", data.get("confidence"))
        confidence = float(confidence_raw) if confidence_raw is not None else None
        admin_city = str(data.get("admin_city", ""))
        admin_district = str(data.get("admin_district", ""))
        lat = float(data.get("cell_latitude", 0.0)) if data.get("cell_latitude") else None
        lng = float(data.get("cell_longitude", 0.0)) if data.get("cell_longitude") else None
    else:
        h3_index = legacy.h3_index
        h3_resolution = legacy.h3_resolution
        poi_count = legacy.poi_count
        competitor_count = legacy.competitor_count
        competitor_capacity = legacy.competitor_capacity
        median_listing_rent = legacy.median_listing_rent
        active_listing_count = legacy.active_listing_count
        existing_store_count = legacy.existing_store_count
        confidence = legacy.average_confidence
        admin_city = legacy.admin_city
        admin_district = legacy.admin_district
        lat = legacy.cell_latitude if legacy.cell_latitude != 0.0 else None
        lng = legacy.cell_longitude if legacy.cell_longitude != 0.0 else None

    pop = population_override if population_override is not None else float(poi_count * 200 + competitor_count * 500)
    hh = household_count_override if household_count_override is not None else float(pop / 2.6)
    housing = housing_units_override if housing_units_override is not None else hh

    effective_absorption = absorption
    if (
        effective_absorption is None
        and store_performances is not None
        and operational_starts is not None
        and decision_policy is not None
        and original_demand is not None
        and store_ids
        and observation_window_start is not None
        and observation_window_end is not None
    ):
        from modules.heatzone.application.absorption_inputs import assemble_zone_absorption

        effective_absorption = assemble_zone_absorption(
            store_ids=set(store_ids),
            performances=store_performances,
            operational_starts=operational_starts,
            original_demand=original_demand,
            policy=decision_policy,
            as_of=as_of or datetime.now(UTC).date(),
            observation_window_start=observation_window_start,
            observation_window_end=observation_window_end,
        )

    return HeatZoneV3Input(
        absorption=effective_absorption,
        h3_index=h3_index,
        h3_resolution=h3_resolution,
        cell_id=f"legacy:{h3_index}",
        tenant_id=tenant_id,
        population=pop,
        daytime_population_ratio=1.0,
        household_count=hh,
        housing_units=housing,
        poi_count=poi_count,
        competitor_capacity=competitor_capacity,
        active_competitor_count=competitor_count,
        median_rent_per_ping=median_listing_rent,
        mean_rent_per_ping=median_listing_rent,
        rent_sample_count=active_listing_count,
        active_listing_count=active_listing_count,
        median_listing_rent=median_listing_rent,
        own_store_count=existing_store_count,
        own_store_machine_capacity=float(existing_store_count * 10),
        overall_readiness=overall_readiness,
        coverage_ratio=coverage_ratio,
        confidence=confidence,
        centroid_lat=lat,
        centroid_lng=lng,
        county=admin_city,
        district=admin_district,
    )


__all__ = [
    "from_catchment_profile",
    "from_legacy_feature_input",
    "from_market_cell_profile",
]
