select
    'candidate_site_view' as view_name,
    'v1' as view_version,
    candidate_sites.candidate_site_id::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['expansion.candidate_sites', 'expansion.listings', 'core.address_locations'] as source_snapshot_ids,
    case
        when listings.rent_amount > 0 and address_locations.geocode_confidence >= 0.5 then 1.0
        when listings.rent_amount > 0 then 0.8
        else 0.0
    end as data_quality_score,
    least(coalesce(listings.confidence, 1.0), coalesce(address_locations.geocode_confidence, 1.0)) as confidence,
    listings.rent_amount > 0
      and candidate_sites.created_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as is_training_eligible,
    listings.rent_amount > 0 as is_scoring_eligible,
    case
        when listings.rent_amount <= 0 then 'missing_rent'
        when candidate_sites.created_at > {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz then 'created_after_snapshot'
        else ''
    end as exclusion_reason,
    candidate_sites.candidate_site_id,
    candidate_sites.listing_id,
    candidate_sites.target_format_code,
    listings.rent_amount,
    listings.area_ping,
    listings.frontage_m,
    listings.floor,
    listings.utility_electricity_flag,
    listings.utility_drainage_flag,
    listings.utility_gas_flag,
    address_locations.geocode_confidence,
    address_locations.h3_res_9 as h3_index,
    case when listings.area_ping > 0 then listings.rent_amount / listings.area_ping else null end as rent_per_ping,
    array_remove(array[
        case when listings.rent_amount <= 0 then 'rent_missing_or_zero' end,
        case when address_locations.geocode_confidence < 0.5 then 'low_geocode_confidence' end
    ], null) as hard_rule_fail_reasons
from expansion.candidate_sites
left join expansion.listings on listings.listing_id = candidate_sites.listing_id
left join core.address_locations on address_locations.address_id = candidate_sites.address_id
