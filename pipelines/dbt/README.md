# dbt

dbt project landing zone for canonical, mart, and model-ready views.

The first baseline under `models/model_ready/` defines view contracts for:

- `geo_grid_view`
- `candidate_site_view`
- `store_machine_timeseries_view`
- `forecast_training_view`
- `intervention_panel_view`
- `valuation_view`
- `network_plan_view`

These models are intentionally view-materialized and expose the shared
`ODP-DATA-06` fields required by Learning Hub dataset snapshots.
