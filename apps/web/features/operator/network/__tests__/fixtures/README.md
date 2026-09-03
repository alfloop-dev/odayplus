# Shared disclosure contract fixture

`netplanDisclosureApiPayload.json` is not hand-written sample data. It is the
constraint-disclosure projection that a production CP-SAT solve produces when
it is driven through the shipped Operator routes, and it is asserted from both
sides:

- `tests/integration/test_netplan_disclosure_ui_e2e.py` solves with
  `NetPlanProductionExecutor`, posts through
  `create_network_rebalance_sub_router`, and refuses to pass if the response
  differs from this file.
- `PlanGanttChart.test.tsx` renders `RebalancePanel` from this file rather than
  from a literal written next to the assertions.

The point is that neither half can drift alone. A backend change that drops a
field the console reads fails the Python test; a console that stops rendering
what the backend sends fails the vitest one. Two literals maintained by hand on
either side of the HTTP boundary would have agreed with each other right up
until they did not.

Regenerate with, and do not edit by hand:

```
uv run --frozen python -m tests.integration.test_netplan_disclosure_ui_e2e
```

Regeneration is a script rather than a test-time side effect. A test that
repaired the file it checks would report agreement between the console and an
API contract that had just changed underneath it.
