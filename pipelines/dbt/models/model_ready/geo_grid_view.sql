with poi_counts as (
    select
        h3_cells.geo_cell_id,
        count(*) filter (where pois.poi_category = 'school') as poi_school_count,
        count(*) filter (where pois.poi_category = 'residential') as poi_residential_count,
        count(*) filter (where pois.poi_category in ('market', 'retail')) as poi_market_count,
        avg(pois.confidence) as poi_confidence
    from geo.h3_cells
    left join geo.pois on pois.geo_cell_id = h3_cells.geo_cell_id
    group by h3_cells.geo_cell_id
),
competitor_counts as (
    select
        geo_cell_id,
        count(*) as competitor_count_500m,
        sum(estimated_capacity) as competitor_capacity_proxy_500m,
        avg(confidence) as competitor_confidence
    from geo.competitor_stores
    where status = 'active'
    group by geo_cell_id
),
listing_counts as (
    select
        address_locations.h3_res_9,
        count(*) filter (where listings.listing_status = 'active') as listing_count_active,
        percentile_cont(0.5) within group (order by listings.rent_amount / nullif(listings.area_ping, 0)) as rent_p50_per_ping
    from expansion.listings
    left join core.address_locations on address_locations.address_id = listings.address_id
    group by address_locations.h3_res_9
)
select
    'geo_grid_view' as view_name,
    'v1' as view_version,
    h3_cells.h3_index as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['geo.h3_cells', 'geo.pois', 'geo.competitor_stores', 'expansion.listings'] as source_snapshot_ids,
    case when h3_cells.h3_index is not null then 1.0 else 0.0 end as data_quality_score,
    least(coalesce(poi_counts.poi_confidence, 1.0), coalesce(competitor_counts.competitor_confidence, 1.0)) as confidence,
    true as is_training_eligible,
    true as is_scoring_eligible,
    '' as exclusion_reason,
    h3_cells.h3_index,
    h3_cells.h3_resolution,
    h3_cells.admin_city,
    h3_cells.admin_district,
    coalesce(poi_counts.poi_school_count, 0) as poi_school_count,
    coalesce(poi_counts.poi_residential_count, 0) as poi_residential_count,
    coalesce(poi_counts.poi_market_count, 0) as poi_market_count,
    coalesce(competitor_counts.competitor_count_500m, 0) as competitor_count_500m,
    coalesce(competitor_counts.competitor_capacity_proxy_500m, 0) as competitor_capacity_proxy_500m,
    coalesce(listing_counts.rent_p50_per_ping, 0) as rent_p50_per_ping,
    coalesce(listing_counts.listing_count_active, 0) as listing_count_active
from geo.h3_cells
left join poi_counts on poi_counts.geo_cell_id = h3_cells.geo_cell_id
left join competitor_counts on competitor_counts.geo_cell_id = h3_cells.geo_cell_id
left join listing_counts on listing_counts.h3_res_9 = h3_cells.h3_index
