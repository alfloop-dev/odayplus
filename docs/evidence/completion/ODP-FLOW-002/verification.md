# ODP-FLOW-002 — Verification

**Owner:** Claude2 · **Reviewer:** Codex

> Closeout note: durable reviewer is **Codex** (approved, no blocking findings);
> the original commit `58c5360` predates the reassignment and records
> `Reviewer: Claude`. This closeout commit syncs the evidence reviewer.

## Reviewer independent re-verification (Codex)

```
python3 -m pytest tests/integration/test_flow_002_expansion_persistence.py -q   # 1 passed
python3 -m pytest tests/integration/test_sitescore_decision.py \
                  tests/integration/test_heatzone_flow.py \
                  tests/integration/test_listing_pipeline.py \
                  tests/integration/test_durable_repository_wiring.py -q          # 24 passed
python3 -m pytest tests/integration/ tests/contract/test_platform_api.py \
                  --ignore=tests/integration/test_netplan_solver.py -q           # 206 passed
python3 -m ruff check <task python files>                                        # clean
git diff --check HEAD^..HEAD                                                     # clean
```

## Commands run (from the task worktree)

```
python3 -m pytest tests/integration/test_flow_002_expansion_persistence.py -v
# 1 passed

python3 -m pytest tests/integration/test_sitescore_decision.py \
                  tests/integration/test_heatzone_flow.py \
                  tests/integration/test_listing_pipeline.py \
                  tests/integration/test_durable_repository_wiring.py -q
# 24 passed (sitescore 7 / heatzone / listing 3 / durable-wiring)

python3 -m pytest tests/integration/ tests/contract/test_platform_api.py \
                  --ignore=tests/integration/test_netplan_solver.py
# 206 passed

python3 -m ruff check <all changed files + shared/infrastructure/persistence/>
# All checks passed!
```

## New test — survives a simulated restart

`tests/integration/test_flow_002_expansion_persistence.py` drives the full loop
against a durable SQLite bundle, closes the engine (simulated process restart),
rebuilds a fresh `create_app` on the same DB file, and asserts through the HTTP
API that:

- HeatZone `GET /heatzones`, `/heatzones/map`, `/heatzones/{h3}` still return
  the ranking; an idempotent replay resolves to the original `job_id`.
- Re-importing the same source listing is rejected as a duplicate
  (`duplicate_count == 1`, `accepted_count == 0`) — dedup keys persisted.
- `GET /listings/candidates` still lists the converted candidate site.
- `GET /sitescore/reports/{candidate}` reports `version_count == 2`.
- `GET /sitescore/decisions/{id}` returns `APPROVED`.
- `GET /sitescore/realized` still contains the realized site.
- `GET /audit/events?correlation_id=…` contains `heatzone.scored.v1`,
  `sitescore.scored.v1`, `sitescore.decision.v1` with actions
  `run_model` / `return` / `approve`.

## Environment caveats (pre-existing, not caused by this task)

- `tests/integration/test_netplan_solver.py` needs the `ortools` CP-SAT solver.
  `ortools>=9.15` is a declared dependency in `pyproject.toml`, but is not
  installed in this minimal worktree, so those 5 tests error on import. They are
  unrelated to the expansion flow and excluded from the 206-pass run above.
- Listing geocode needs `h3` (`h3>=4.5.0`, also declared in `pyproject.toml`);
  it was installed locally to run the listing + expansion-flow tests. The new
  test `pytest.importorskip("h3")`s so it degrades gracefully if absent.
