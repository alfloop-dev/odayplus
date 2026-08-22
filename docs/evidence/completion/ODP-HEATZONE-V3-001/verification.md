# Verification Evidence: ODP-HEATZONE-V3-001

## Verification Command
```bash
uv run pytest tests/models/test_heatzone_v3_contract.py -q
```

## Results
- **Status**: PASSED (22 / 22 tests)
- **Execution Time**: 0.61s
- **Linter Status**: PASSED (ruff check 0 errors)

### Test Cases Covered
1. `test_heatzone_v3_contract_identity_and_version`: Validated contract ID `odayplus.heatzone-v3.v1`, contract version `1.0.0`, model version `heatzone-v3-shadow`.
2. `test_heatzone_v3_scores_all_nine_required_dimensions`: Validated all 9 dimensions (population, households, housing, POI, competitor capacity, rent, listing, own-store capacity, coverage).
3. `test_heatzone_v3_sensitivity_to_own_store_cannibalization`: Verified own-store capacity increases cannibalization risk and reduces unmet demand.
4. `test_heatzone_v3_sensitivity_to_rent_and_listing_availability`: Verified rent affordability and active listing supply dynamics.
5. `test_heatzone_v3_abstains_when_readiness_is_blocked`: Fail-closed on `ReadinessLevel.blocked`.
6. `test_heatzone_v3_abstains_when_readiness_is_unknown`: Fail-closed on `ReadinessLevel.unknown`.
7. `test_heatzone_v3_abstains_when_source_is_quarantined`: Fail-closed on quarantined source data.
8. `test_heatzone_v3_abstains_when_coverage_ratio_below_threshold`: Fail-closed on coverage ratio < 0.50.
9. `test_heatzone_v3_abstains_when_support_level_outside_bounds`: Fail-closed on unsupported support levels.
10. `test_heatzone_v3_abstains_when_critical_domain_quarantined_or_missing`: Fail-closed on missing/quarantined critical domains.
11. `test_heatzone_v3_abstains_when_data_confidence_unacceptably_low`: Fail-closed on confidence floor breach.
12. `test_adapt_market_cell_profile_document_and_own_store_capacity`: Ingesting `emgi.market-cell-profile.v1` and `oday.machine-capacity.v1` / `oday.store-coverage.v1`.
13. `test_adapt_catchment_profile_document`: Ingesting `emgi.catchment-profile.v1`.
14. `test_adapt_legacy_feature_input_bridge`: Compatibility bridge from legacy v1 feature inputs.
15. `test_manifest_document_linkage`: Manifest linkages to `emgi.manifests.v4.1`.
16. `test_shadow_runner_generates_side_by_side_comparison_with_baseline`: Shadow execution and side-by-side comparison metrics.
17. `test_heatzone_v3_score_result_round_trips`: Wire dict and GeoJSON feature round-trip serialization.
18. `test_heatzone_v3_batch_result_round_trips`: Full batch document serialization.
19. `test_heatzone_v3_deterministic_ranking_order`: Deterministic ranking descending by score with abstained items at the end.
20. `test_heatzone_v3_abstains_when_critical_domain_empty_even_if_ready_and_no_gaps`: Acceptance B2 fail-closed verification.
21. `test_heatzone_v3_rent_feasibility_monotonic_without_rent_data`: C1 monotonic rent feasibility verification.
22. `test_heatzone_v3_saturated_state_when_competitor_saturated_and_zero_own_stores`: C3 competitor saturated state verification.

## Code Boundary Governance
```bash
python3 delivery_toolchain/governance/check_code_boundaries.py
```
- **Status**: PASSED (887 files checked cleanly).
